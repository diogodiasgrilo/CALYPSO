"""SaxoBrokerAdapter — wraps the existing SaxoClient behind BrokerInterface.

Phase B.2 of the migration. Pure delegation layer — NO behavior change
to the live Saxo dry-run on the VM. HYDRA can be wired through this
adapter today and produce identical decisions / orders / state.

What's implemented (easy 1:1 + shape normalization):
  • Lifecycle: connect/is_connected/disconnect
  • Reads: get_quote, get_quotes_batch, get_option_greeks,
    get_option_chain, get_chart_data, get_fx_rate, get_vix_price
  • Account: get_account_info, get_balance, get_positions
  • Order management: get_open_orders, get_order_status, cancel_order

What raises NotImplementedError (deferred to Phase B.2.b, after Phase
A.10 validates the IBClient combo-order surface — that's the point of
parity where the Saxo side needs to compose the same flow):
  • place_iron_condor — Saxo's existing flow uses
    `find_iron_fly_options + place_multi_leg_order`; the composition
    lives in `bots/iron_fly_0dte/strategy.py` today and needs to be
    extracted before the adapter can host it cleanly.
  • place_vertical_spread — same composition story.
  • what_if_iron_condor — Saxo has no native what_if; CALYPSO uses a
    client-side ORDER-004 BP gate via `external_price_feed`. To wire
    this through the interface we need to either (a) call the existing
    HYDRA-side gate, or (b) accept that Saxo's what_if is a no-op that
    returns an empty dict. Decision deferred to Phase B.4.

These stubs are explicit so a caller never silently does the wrong
thing. The errors carry actionable next-step text.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from shared.broker.interface import (
    BrokerAuthError,
    BrokerError,
    BrokerInterface,
    IronCondorRequest,
    OrderResult,
    Position,
    QuoteSnapshot,
    VerticalSpreadRequest,
)


logger = logging.getLogger(__name__)


# ─── Status-string normalization ────────────────────────────────────────────
#
# Saxo returns a small vocabulary that we collapse onto the
# BrokerInterface canonical set so callers can switch on order status
# without knowing which broker produced the response.

_SAXO_STATUS_NORMALIZE = {
    # Working
    "working": "Submitted",
    "ordered": "Submitted",
    "submitted": "Submitted",
    "presubmitted": "PreSubmitted",
    # Terminal
    "filled": "Filled",
    "partiallyfilled": "PartiallyFilled",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
    "expired": "Expired",
    "rejected": "Rejected",
}


def _normalize_status(saxo_status: Optional[str]) -> str:
    if not saxo_status:
        return "Unknown"
    return _SAXO_STATUS_NORMALIZE.get(saxo_status.lower(), saxo_status)


# ─── Adapter ────────────────────────────────────────────────────────────────


class SaxoBrokerAdapter(BrokerInterface):
    """Wraps a `shared.saxo_client.SaxoClient` instance.

    Caller is responsible for instantiating the SaxoClient with proper
    config (token coordinator, account keys, etc.) and passing it in.
    Constructor stays cheap so the adapter can be wired into HYDRA's
    boot path without changing how SaxoClient gets built.

    Phase B.2 invariant: this adapter MUST NOT change SaxoClient's
    behavior, only translate its surface.
    """

    def __init__(self, saxo_client):
        self._saxo = saxo_client
        # VIX UIC must be wired by the caller — Saxo doesn't expose a
        # symbol-keyed VIX lookup the way IBKR does. If unset,
        # get_vix_price returns None rather than raising.
        self._vix_uic: Optional[int] = None
        # symbol → (underlying_uic, option_root_uic, asset_type) registry.
        # Saxo's option-chain / chart-data / find_iron_fly_options APIs
        # require UIC integers, not symbol strings — callers register
        # mappings here at boot so the BrokerInterface contract (symbol+
        # expiry+strike) translates cleanly. Keyed by uppercase symbol.
        self._symbol_registry: dict[str, dict] = {}
        # Streaming proxy — lazy. SaxoClient's WS isn't started until
        # caller subscribes; the proxy owns the start/stop lifecycle.
        self._streaming_proxy = None

    @property
    def streaming(self):
        """StreamingInterface proxy over SaxoClient's bulk WS streaming.

        Saxo's `start_price_streaming` is bulk; the proxy maintains a
        per-instrument set internally and restarts streaming when the
        set changes. Greeks come via REST get_option_greeks — the proxy
        falls back to subscribe_quote for subscribe_option.
        """
        if self._streaming_proxy is None:
            from shared.broker.streaming_proxies import SaxoStreamingProxy
            self._streaming_proxy = SaxoStreamingProxy(self._saxo)
        return self._streaming_proxy

    @property
    def saxo(self):
        """Escape hatch for callers needing Saxo-specific functionality
        not yet covered by the BrokerInterface (e.g., streaming subscribe,
        position close, per-leg sequential order placement with retry).
        Use sparingly — every direct-Saxo call site is a Phase B.4 cleanup
        target."""
        return self._saxo

    def set_vix_uic(self, uic: int) -> None:
        """Configure the Saxo UIC for the VIX index. Must be called once
        at boot time before `get_vix_price()` is usable."""
        self._vix_uic = int(uic)

    def register_underlying(
        self,
        symbol: str,
        underlying_uic: int,
        option_root_uic: int,
        asset_type: str = "StockIndexOption",
    ) -> None:
        """Register a symbol→UIC mapping for option / chart lookups.

        Args:
            symbol: e.g. "SPX" — uppercased for storage
            underlying_uic: Saxo UIC for the underlying instrument
                (e.g. SPX index ~6469910 in some Saxo setups)
            option_root_uic: Saxo's option-root UIC (e.g. SPXW = 128)
                — used by find_iron_fly_options / get_option_chain.
            asset_type: Saxo asset type string. Default
                "StockIndexOption" matches SPX 0DTE; equity options use
                "StockOption", FX options use "FxOption", etc.
        """
        self._symbol_registry[symbol.upper()] = {
            "underlying_uic": int(underlying_uic),
            "option_root_uic": int(option_root_uic),
            "asset_type": asset_type,
        }

    def _registry_entry(self, symbol: str) -> dict:
        """Look up a registered symbol; raise BrokerError if not registered."""
        entry = self._symbol_registry.get(symbol.upper())
        if entry is None:
            raise BrokerError(
                f"SaxoBrokerAdapter: symbol {symbol!r} not registered. "
                f"Call register_underlying({symbol!r}, underlying_uic, "
                f"option_root_uic) at boot time before option/chart lookups."
            )
        return entry

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            ok = self._saxo.authenticate()
        except Exception as exc:
            raise BrokerAuthError(f"Saxo authenticate failed: {exc}") from exc
        if not ok:
            raise BrokerAuthError("Saxo authenticate returned False")
        return True

    def is_connected(self) -> bool:
        # SaxoClient uses _is_token_valid as its cheap last-known check.
        try:
            return bool(self._saxo._is_token_valid())
        except Exception:
            return False

    def disconnect(self) -> None:
        # Saxo has no explicit logout endpoint; tokens expire naturally
        # and the next authenticate() refreshes. Mirror IB's idempotent
        # contract — no-op when already disconnected.
        logger.debug("SaxoBrokerAdapter.disconnect: no-op (Saxo has no logout)")

    # ─── Quote / market data normalization ────────────────────────────────

    @staticmethod
    def _to_quote_snapshot(uic: str, raw: Optional[dict]) -> Optional[QuoteSnapshot]:
        """Map Saxo's `{"Quote": {"Bid": ..., "Ask": ..., "Mid": ...}}`
        envelope into a normalized QuoteSnapshot. None passes through."""
        if not raw:
            return None
        quote = raw.get("Quote") or {}
        bid = quote.get("Bid")
        ask = quote.get("Ask")
        mid = quote.get("Mid")
        # Compute mid if Saxo didn't include it but we have both sides
        if mid is None and bid is not None and ask is not None:
            try:
                mid = (float(bid) + float(ask)) / 2
            except (TypeError, ValueError):
                mid = None
        return QuoteSnapshot(
            instrument_id=str(uic),
            bid=bid,
            ask=ask,
            last=quote.get("LastTraded"),
            mid=mid,
            mark=mid,  # Saxo doesn't expose a distinct mark; use mid
            bid_size=quote.get("BidSize"),
            ask_size=quote.get("AskSize"),
            delta=raw.get("Greeks", {}).get("Delta") if "Greeks" in raw else None,
            gamma=raw.get("Greeks", {}).get("Gamma") if "Greeks" in raw else None,
            theta=raw.get("Greeks", {}).get("Theta") if "Greeks" in raw else None,
            vega=raw.get("Greeks", {}).get("Vega") if "Greeks" in raw else None,
            iv=raw.get("Greeks", {}).get("ImpliedVolatility") if "Greeks" in raw else None,
            open_interest=raw.get("InstrumentSummary", {}).get("OpenInterest"),
            timestamp=raw.get("LastUpdated"),
            raw=raw,
        )

    def get_quote(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        uic = int(instrument_id)
        raw = self._saxo.get_quote(uic, asset_type="StockIndexOption")
        return self._to_quote_snapshot(instrument_id, raw)

    def get_quotes_batch(self, instrument_ids: list[str]) -> list[QuoteSnapshot]:
        uics = [int(i) for i in instrument_ids]
        raw_by_uic = self._saxo.get_quotes_batch(uics, asset_type="StockIndexOption") or {}
        out: list[QuoteSnapshot] = []
        for uic in uics:
            row = raw_by_uic.get(uic) or raw_by_uic.get(str(uic))
            snap = self._to_quote_snapshot(str(uic), row)
            if snap is not None:
                out.append(snap)
        return out

    def get_vix_price(self) -> Optional[float]:
        if self._vix_uic is None:
            logger.warning(
                "SaxoBrokerAdapter.get_vix_price: VIX UIC not configured. "
                "Call set_vix_uic() at boot."
            )
            return None
        return self._saxo.get_vix_price(self._vix_uic)

    def get_option_greeks(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        uic = int(instrument_id)
        raw = self._saxo.get_option_greeks(uic, asset_type="StockIndexOption")
        return self._to_quote_snapshot(instrument_id, raw)

    def get_option_chain(self, underlying_symbol: str, expiry: date) -> list[float]:
        """Returns available strikes for `underlying_symbol` on `expiry`.

        Requires the symbol to be registered via `register_underlying()`
        because Saxo's API needs UICs, not symbols.
        """
        entry = self._registry_entry(underlying_symbol)
        raw = self._saxo.get_option_chain(
            underlying_uic=entry["underlying_uic"],
            option_root_uic=entry["option_root_uic"],
            expiry_date=expiry,
        )
        if not raw:
            return []
        # Saxo returns a list of strike-keyed dicts; normalize to floats.
        strikes: list[float] = []
        for entry_dict in raw:
            sk = entry_dict.get("Strike") or entry_dict.get("StrikePrice")
            if sk is not None:
                try:
                    strikes.append(float(sk))
                except (TypeError, ValueError):
                    continue
        return sorted(set(strikes))

    def get_chart_data(
        self,
        symbol: str,
        bar: str = "1min",
        period: str = "1d",
        outside_rth: bool = False,
    ) -> list[dict]:
        """Historical OHLCV bars. Requires registered symbol→UIC mapping."""
        entry = self._registry_entry(symbol)
        raw = self._saxo.get_chart_data(
            uic=entry["underlying_uic"],
            asset_type=entry["asset_type"],
            horizon=bar,
            count=1440 if period == "1d" else 5000,  # crude mapping
        )
        return raw if isinstance(raw, list) else []

    def get_fx_rate(self, source: str, target: str) -> Optional[float]:
        try:
            return self._saxo.get_fx_rate(source, target)
        except Exception as exc:
            raise BrokerError(f"Saxo get_fx_rate({source}, {target}) failed: {exc}") from exc

    # ─── Account state ────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        info = self._saxo.get_account_info()
        return info or {}

    def get_balance(self, currency: str = "USD") -> dict:
        raw = self._saxo.get_balance() or {}
        # Saxo's balance is in the account's base currency; we don't have
        # a multi-currency composition here today. Return a shape that
        # matches the BrokerInterface contract.
        cash = raw.get("CashBalance")
        if cash is None:
            cash = raw.get("TotalValue")
        base_currency = raw.get("Currency", currency)
        return {
            "currency": currency,
            "base_currency": base_currency,
            "tradable": float(cash) if cash is not None else 0.0,
            "raw": raw,
        }

    @staticmethod
    def _to_position(raw: dict) -> Position:
        """Map a Saxo /port/v1/positions entry to a Position dataclass."""
        pb = raw.get("PositionBase") or {}
        pv = raw.get("PositionView") or {}
        amount = pb.get("Amount", 0)
        side = "LONG" if amount > 0 else ("SHORT" if amount < 0 else "FLAT")
        return Position(
            instrument_id=str(pb.get("Uic", "")),
            symbol=pb.get("Symbol") or raw.get("DisplayAndFormat", {}).get("Symbol", ""),
            quantity=int(abs(amount)) if amount is not None else 0,
            side=side,
            avg_price=pb.get("OpenPrice"),
            unrealized_pnl=pv.get("ProfitLossOnTrade"),
            raw=raw,
        )

    def get_positions(self) -> list[Position]:
        raw_list = self._saxo.get_positions() or []
        return [self._to_position(p) for p in raw_list]

    # ─── Order management ─────────────────────────────────────────────────

    @staticmethod
    def _to_order_result(raw: Optional[dict]) -> OrderResult:
        """Map a Saxo order-shaped dict to OrderResult."""
        raw = raw or {}
        # Saxo's order_id appears under different keys depending on which
        # endpoint produced the dict — be lenient.
        order_id = str(
            raw.get("OrderId")
            or raw.get("orderId")
            or raw.get("ExternalReference")
            or ""
        )
        return OrderResult(
            order_id=order_id,
            status=_normalize_status(raw.get("Status") or raw.get("OpenOrderStatus")),
            filled_qty=int(raw.get("FilledAmount", 0) or 0),
            avg_fill_price=raw.get("AveragePrice"),
            reject_reason=raw.get("ErrorMessage"),
            raw=raw,
        )

    def get_open_orders(self) -> list[OrderResult]:
        raw_list = self._saxo.get_open_orders() or []
        return [self._to_order_result(o) for o in raw_list]

    def get_order_status(self, order_id: str) -> OrderResult:
        raw = self._saxo.get_order_status(str(order_id))
        return self._to_order_result(raw)

    def cancel_order(self, order_id: str) -> bool:
        try:
            result = self._saxo.cancel_order(str(order_id))
        except Exception as exc:
            logger.error("Saxo cancel_order(%s) failed: %s", order_id, exc)
            return False
        # SaxoClient returns the raw response dict; treat a truthy
        # response as success (None / empty dict / exception = failure).
        return bool(result)

    # ─── Pre-trade (still deferred — see docstring) ───────────────────────

    def what_if_iron_condor(self, request: IronCondorRequest) -> dict:
        """Saxo has no native what_if endpoint. Returns an empty dict so
        callers don't break the contract; strategies that need a
        broker-authoritative pre-trade gate must use IBBrokerAdapter or
        keep their existing client-side ORDER-004 BP check.

        Returns: `{"_status": "not_supported_on_saxo", "_reason": ...}`
        so the empty-dict case is self-describing.
        """
        logger.debug(
            "SaxoBrokerAdapter.what_if_iron_condor: Saxo has no native "
            "what_if; returning sentinel. Use IBBrokerAdapter for "
            "broker-authoritative margin checks."
        )
        return {
            "_status": "not_supported_on_saxo",
            "_reason": "Saxo Bank's /trade/v2/orders/precheck does not "
                       "compose 4-leg combo margin in a single call; "
                       "strategies must continue using client-side "
                       "ORDER-004 BP gates.",
        }

    # ─── Writes — atomic combo placement via place_multi_leg_order ────────

    def place_iron_condor(self, request: IronCondorRequest) -> OrderResult:
        """Place a 4-leg short iron condor as a single multi-leg order.

        This is the ATOMIC COMBO path: legs go to Saxo in one request,
        broker-side atomic-fill semantics. Strategies that want per-leg
        sequential placement (e.g., iron_fly_0dte's longs-first safety
        ordering with verification) should continue using the `.saxo`
        escape hatch to call `place_order_with_retry` directly — that's
        a strategy choice, not a broker abstraction concern.

        Requires the underlying symbol to be registered via
        `register_underlying()` so UICs can be resolved.
        """
        entry = self._registry_entry(request.underlying_symbol)
        # Step 1: resolve all 4 leg UICs via find_iron_fly_options
        iron_fly = self._saxo.find_iron_fly_options(
            underlying_uic=entry["underlying_uic"],
            atm_strike=request.short_call_strike,  # for IC, "ATM" is the short call
            upper_wing_strike=request.long_call_strike,
            lower_wing_strike=request.long_put_strike,
            target_dte_min=0,
            target_dte_max=(request.expiry - date.today()).days + 1,
            option_root_uic=entry["option_root_uic"],
        )
        if not iron_fly:
            raise BrokerError(
                f"SaxoBrokerAdapter.place_iron_condor: find_iron_fly_options "
                f"returned no UICs for {request.underlying_symbol} expiry="
                f"{request.expiry} strikes "
                f"SC={request.short_call_strike}/LC={request.long_call_strike}/"
                f"SP={request.short_put_strike}/LP={request.long_put_strike}"
            )
        sc_uic = iron_fly["short_call"]["uic"]
        sp_uic = iron_fly["short_put"]["uic"]
        lc_uic = iron_fly["long_call"]["uic"]
        lp_uic = iron_fly["long_put"]["uic"]

        # Step 2: build legs — Saxo wants {"uic", "asset_type", "buy_sell", "amount"}
        legs = [
            {"uic": lc_uic, "asset_type": entry["asset_type"], "buy_sell": "Buy",  "amount": request.contracts},
            {"uic": lp_uic, "asset_type": entry["asset_type"], "buy_sell": "Buy",  "amount": request.contracts},
            {"uic": sc_uic, "asset_type": entry["asset_type"], "buy_sell": "Sell", "amount": request.contracts},
            {"uic": sp_uic, "asset_type": entry["asset_type"], "buy_sell": "Sell", "amount": request.contracts},
        ]
        try:
            response = self._saxo.place_multi_leg_order(
                legs=legs,
                duration_type="DayOrder" if request.tif == "DAY" else request.tif,
            )
        except Exception as exc:
            raise BrokerError(
                f"SaxoBrokerAdapter.place_iron_condor: place_multi_leg_order "
                f"raised: {exc}"
            ) from exc
        if not response:
            raise BrokerError(
                "SaxoBrokerAdapter.place_iron_condor: place_multi_leg_order "
                "returned None — order rejected by broker"
            )
        result = self._to_order_result(response)
        result.is_combo = True
        # If Saxo returned per-leg order ids, capture them
        per_leg = response.get("Orders") if isinstance(response, dict) else None
        if per_leg:
            result.legs = [self._to_order_result(o) for o in per_leg]
        return result

    def place_vertical_spread(self, request: VerticalSpreadRequest) -> OrderResult:
        """Place a 2-leg vertical spread atomically.

        Like place_iron_condor, this is the combo path. action='SELL'
        opens a short spread (collect credit); 'BUY' closes one.
        """
        entry = self._registry_entry(request.underlying_symbol)
        # Saxo doesn't have a 2-leg helper — resolve UICs via the same
        # find_iron_fly_options + the strikes-on-one-side, OR via
        # find_strangle_options. For simplicity here, callers are
        # expected to know UICs already and reach via .saxo for the
        # underlying UIC resolution. To keep the BrokerInterface
        # contract honest, raise BrokerError with explicit next step.
        raise BrokerError(
            "SaxoBrokerAdapter.place_vertical_spread: Saxo's symbol→UIC "
            "lookup for a 2-leg spread needs a strike-pair-specific call "
            "(find_strangle_options or similar). Wire that translation "
            "in Phase B.4 when HYDRA's vertical-spread paths are "
            "actually exercised on Saxo. Until then, strategies place "
            "verticals via .saxo + place_multi_leg_order directly."
        )
