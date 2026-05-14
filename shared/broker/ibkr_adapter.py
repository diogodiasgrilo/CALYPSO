"""IBBrokerAdapter — wraps shared.ib_client.IBClient behind BrokerInterface.

Phase B.3 of the migration. Symmetric to SaxoBrokerAdapter, but the IB
side benefits from a much cleaner one-to-one mapping because IBClient
was designed with the abstraction in mind (Phase A.2-A.8).

Compared to Saxo:
  • IB uses conid (int) as the instrument identifier. The
    BrokerInterface contract says instrument_id is `str`; this adapter
    converts at the boundary so callers never see int conids.
  • IB resolves symbols → conids via IBClient.qualify_contract(); no
    pre-registration needed (unlike SaxoBrokerAdapter's symbol registry).
    The cache lives on IBClient itself.
  • IB has native what_if (broker-authoritative pre-trade margin). Saxo
    doesn't — the IB adapter is the one that makes what_if_iron_condor
    meaningful for the migration.
  • IB has native combo-order support via conidex+BAG sec_type. The
    adapter calls IBClient.place_iron_condor / place_vertical_spread
    directly — those methods already handle conidex construction, $0.05
    rounding, coid generation, etc.

Phase A.10 paper smoke verifies IBClient's surface end-to-end against a
live paper account. Any response-shape surprises caught there are fixed
inside IBClient, not in this adapter — the adapter's job is only
boundary translation.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from shared.broker.interface import (
    BrokerAuthError,
    BrokerConnectionError,
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
# IBKR's order_status enum is a fixed set; we normalize to the
# BrokerInterface canonical vocabulary so callers don't switch on
# broker-specific strings. See research_scratch/12_ibind_errors_lifecycle.md
# for the full IBKR vocabulary.

_IBKR_STATUS_NORMALIZE = {
    "presubmitted":  "PreSubmitted",
    "submitted":     "Submitted",
    "filled":        "Filled",
    "partiallyfilled": "PartiallyFilled",
    "cancelled":     "Cancelled",
    "apicancelled":  "Cancelled",
    "api_cancelled": "Cancelled",
    "rejected":      "Rejected",
    "expired":       "Expired",
    "inactive":      "Cancelled",  # IBKR's catch-all terminal
}


def _normalize_status(ibkr_status: Optional[str]) -> str:
    if not ibkr_status:
        return "Unknown"
    return _IBKR_STATUS_NORMALIZE.get(ibkr_status.lower(), ibkr_status)


# ─── Adapter ────────────────────────────────────────────────────────────────


class IBBrokerAdapter(BrokerInterface):
    """Wraps a `shared.ib_client.IBClient` instance.

    Caller is responsible for constructing the IBClient with proper
    IBConfig (paper or live credentials, account_id pinning if multi-
    account, etc.) and passing it in. This adapter only translates the
    surface; it does NOT own the OAuth session lifecycle.
    """

    def __init__(self, ib_client):
        self._ib = ib_client

    @property
    def ib(self):
        """Escape hatch for callers needing IB-specific functionality
        not covered by BrokerInterface (e.g., direct streaming access
        via .streaming, reconcile_orders, what_if on arbitrary
        OrderRequests). Phase B.4 should eliminate every direct-IB
        call site."""
        return self._ib

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            return self._ib.connect()
        except Exception as exc:
            # Distinguish auth from connection errors when possible
            from shared.ib_client import IBAuthError, IBConnectionError
            if isinstance(exc, IBAuthError):
                raise BrokerAuthError(f"IB auth failed: {exc}") from exc
            if isinstance(exc, IBConnectionError):
                raise BrokerConnectionError(f"IB connection failed: {exc}") from exc
            raise BrokerError(f"IB connect failed: {exc}") from exc

    def is_connected(self) -> bool:
        try:
            return bool(self._ib.is_connected())
        except Exception:
            return False

    def disconnect(self) -> None:
        try:
            self._ib.disconnect()
        except Exception as exc:
            # disconnect is best-effort; log + swallow per IB semantics
            logger.warning("IBBrokerAdapter.disconnect swallowed: %s", exc)

    # ─── Quote normalization ──────────────────────────────────────────────

    @staticmethod
    def _to_quote_snapshot(conid: str, raw: Optional[dict]) -> Optional[QuoteSnapshot]:
        """Map IBClient's normalized quote dict to QuoteSnapshot.

        IBClient.get_quote already returns a dict with normalized keys
        (bid/ask/last/mid/mark/bid_size/ask_size/availability) — see
        ib_client._parse_quote_row. So this is mostly a re-key + extract
        Greeks if present.
        """
        if not raw:
            return None
        return QuoteSnapshot(
            instrument_id=str(conid),
            bid=raw.get("bid"),
            ask=raw.get("ask"),
            last=raw.get("last"),
            mid=raw.get("mid"),
            mark=raw.get("mark"),
            bid_size=raw.get("bid_size"),
            ask_size=raw.get("ask_size"),
            delta=raw.get("delta"),
            gamma=raw.get("gamma"),
            theta=raw.get("theta"),
            vega=raw.get("vega"),
            iv=raw.get("iv"),
            open_interest=raw.get("open_interest"),
            availability=raw.get("availability"),
            timestamp=raw.get("timestamp"),
            raw=raw,
        )

    def get_quote(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        try:
            raw = self._ib.get_quote(int(instrument_id))
        except Exception as exc:
            raise BrokerError(f"IB get_quote({instrument_id}) failed: {exc}") from exc
        return self._to_quote_snapshot(instrument_id, raw)

    def get_quotes_batch(self, instrument_ids: list[str]) -> list[QuoteSnapshot]:
        conids = [int(i) for i in instrument_ids]
        try:
            raw_list = self._ib.get_quotes_batch(conids)
        except Exception as exc:
            raise BrokerError(f"IB get_quotes_batch failed: {exc}") from exc
        out: list[QuoteSnapshot] = []
        for raw in raw_list or []:
            # IBClient.get_quotes_batch puts 'conid' in each row
            cid = raw.get("conid")
            snap = self._to_quote_snapshot(str(cid), raw)
            if snap is not None:
                out.append(snap)
        return out

    def get_vix_price(self) -> Optional[float]:
        try:
            return self._ib.get_vix_price()
        except Exception as exc:
            logger.warning("IBBrokerAdapter.get_vix_price: %s", exc)
            return None

    def get_option_greeks(self, instrument_id: str) -> Optional[QuoteSnapshot]:
        try:
            raw = self._ib.get_option_greeks(int(instrument_id))
        except Exception as exc:
            raise BrokerError(f"IB get_option_greeks failed: {exc}") from exc
        return self._to_quote_snapshot(instrument_id, raw)

    def get_option_chain(self, underlying_symbol: str, expiry: date) -> list[float]:
        try:
            return self._ib.get_option_chain(underlying_symbol, expiry)
        except Exception as exc:
            raise BrokerError(
                f"IB get_option_chain({underlying_symbol}, {expiry}) failed: {exc}"
            ) from exc

    def get_chart_data(
        self,
        symbol: str,
        bar: str = "1min",
        period: str = "1d",
        outside_rth: bool = False,
    ) -> list[dict]:
        try:
            return self._ib.get_chart_data(
                symbol=symbol, bar=bar, period=period, outside_rth=outside_rth,
            )
        except Exception as exc:
            raise BrokerError(f"IB get_chart_data({symbol}) failed: {exc}") from exc

    def get_fx_rate(self, source: str, target: str) -> Optional[float]:
        try:
            return self._ib.get_fx_rate(source, target)
        except Exception as exc:
            raise BrokerError(
                f"IB get_fx_rate({source}, {target}) failed: {exc}"
            ) from exc

    # ─── Account state ────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        try:
            return self._ib.get_account_info() or {}
        except Exception as exc:
            raise BrokerError(f"IB get_account_info failed: {exc}") from exc

    def get_balance(self, currency: str = "USD") -> dict:
        try:
            return self._ib.get_balance(currency)
        except Exception as exc:
            raise BrokerError(f"IB get_balance({currency}) failed: {exc}") from exc

    @staticmethod
    def _to_position(raw: dict) -> Position:
        """Map IBClient's get_positions dict → Position dataclass.

        IBKR's portfolio_summary positions schema uses keys like:
            conid, contractDesc, position, mktPrice, mktValue, avgPrice,
            avgCost, unrealizedPnl, ...
        """
        conid = str(raw.get("conid") or raw.get("conidEx") or "")
        symbol = (
            raw.get("ticker")
            or raw.get("contractDesc", "").split()[0]
            or "?"
        )
        # IBKR uses signed `position` field (long positive, short negative)
        position = raw.get("position", 0) or 0
        side = "LONG" if position > 0 else ("SHORT" if position < 0 else "FLAT")
        return Position(
            instrument_id=conid,
            symbol=symbol,
            quantity=int(abs(position)),
            side=side,
            avg_price=raw.get("avgPrice") or raw.get("avgCost"),
            unrealized_pnl=raw.get("unrealizedPnl"),
            raw=raw,
        )

    def get_positions(self) -> list[Position]:
        try:
            raw_list = self._ib.get_positions() or []
        except Exception as exc:
            raise BrokerError(f"IB get_positions failed: {exc}") from exc
        return [self._to_position(p) for p in raw_list]

    # ─── Order management ─────────────────────────────────────────────────

    @staticmethod
    def _to_order_result(raw: Optional[dict]) -> OrderResult:
        """Map IBClient response dict → OrderResult."""
        raw = raw or {}
        oid = str(raw.get("order_id") or raw.get("orderId") or raw.get("id") or "")
        status_raw = raw.get("status") or raw.get("order_status")
        legs_raw = raw.get("_legs") or []
        legs = [IBBrokerAdapter._to_order_result(l) for l in legs_raw if isinstance(l, dict)]
        return OrderResult(
            order_id=oid,
            status=_normalize_status(status_raw),
            filled_qty=int(raw.get("filled") or raw.get("filled_qty") or 0),
            avg_fill_price=raw.get("avg_fill_price") or raw.get("avgPrice"),
            reject_reason=raw.get("reject_reason") or raw.get("error"),
            is_combo=bool(legs) or raw.get("sec_type") == "BAG",
            legs=legs,
            raw=raw,
        )

    def get_open_orders(self) -> list[OrderResult]:
        try:
            raw_list = self._ib.get_open_orders() or []
        except Exception as exc:
            raise BrokerError(f"IB get_open_orders failed: {exc}") from exc
        return [self._to_order_result(o) for o in raw_list]

    def get_order_status(self, order_id: str) -> OrderResult:
        try:
            raw = self._ib.get_order_status(str(order_id))
        except Exception as exc:
            raise BrokerError(
                f"IB get_order_status({order_id}) failed: {exc}"
            ) from exc
        return self._to_order_result(raw)

    def cancel_order(self, order_id: str) -> bool:
        try:
            return bool(self._ib.cancel_order(str(order_id)))
        except Exception as exc:
            logger.error("IBBrokerAdapter.cancel_order(%s): %s", order_id, exc)
            return False

    # ─── Pre-trade ────────────────────────────────────────────────────────

    def what_if_iron_condor(self, request: IronCondorRequest) -> dict:
        """Broker-authoritative pre-trade margin via IBKR's whatif endpoint.

        Builds the OrderRequest at the adapter boundary (using the same
        conidex format IBClient.place_iron_condor uses) and routes to
        IBClient.what_if_order. Returns IBKR's 5-block dict
        (amount/equity/initial/maintenance/position) — caller parses
        currency-coded strings like "+4,500.00" itself.
        """
        from ibind import OrderRequest
        from shared.ib_client import SPREAD_TEMPLATE_CONID

        try:
            sc = self._ib.qualify_contract(
                request.underlying_symbol, request.expiry,
                request.short_call_strike, "C", "SPXW",
            )
            lc = self._ib.qualify_contract(
                request.underlying_symbol, request.expiry,
                request.long_call_strike, "C", "SPXW",
            )
            sp = self._ib.qualify_contract(
                request.underlying_symbol, request.expiry,
                request.short_put_strike, "P", "SPXW",
            )
            lp = self._ib.qualify_contract(
                request.underlying_symbol, request.expiry,
                request.long_put_strike, "P", "SPXW",
            )
            conidex = (
                f"{SPREAD_TEMPLATE_CONID};;;"
                f"{sc}/-1,{lc}/1,{sp}/-1,{lp}/1"
            )
            order = OrderRequest(
                conid=None,
                conidex=conidex,
                sec_type="BAG",
                side="SELL",
                order_type="LMT",
                price=request.net_credit_limit,
                quantity=float(request.contracts),
                tif=request.tif,
                acct_id=self._ib.account_id,
            )
            return self._ib.what_if_order(order)
        except Exception as exc:
            raise BrokerError(
                f"IB what_if_iron_condor failed: {exc}"
            ) from exc

    # ─── Writes ───────────────────────────────────────────────────────────

    def place_iron_condor(self, request: IronCondorRequest) -> OrderResult:
        """Place a 4-leg combo via IBClient.place_iron_condor.

        IBClient handles conidex construction, $0.05 rounding, auto-coid
        generation, and DEFAULT_ORDER_ANSWERS reply-prompt handling. The
        adapter just translates the IronCondorRequest into IBClient kwargs.
        """
        try:
            raw = self._ib.place_iron_condor(
                expiry=request.expiry,
                short_call_strike=request.short_call_strike,
                long_call_strike=request.long_call_strike,
                short_put_strike=request.short_put_strike,
                long_put_strike=request.long_put_strike,
                contracts=request.contracts,
                net_credit_limit=request.net_credit_limit,
                tif=request.tif,
                coid=request.coid,
                symbol=request.underlying_symbol,
            )
        except Exception as exc:
            raise BrokerError(f"IB place_iron_condor failed: {exc}") from exc
        result = self._to_order_result(raw)
        result.is_combo = True
        return result

    def place_vertical_spread(self, request: VerticalSpreadRequest) -> OrderResult:
        try:
            raw = self._ib.place_vertical_spread(
                expiry=request.expiry,
                short_strike=request.short_strike,
                long_strike=request.long_strike,
                right=request.right,
                contracts=request.contracts,
                net_credit_limit=request.net_credit_limit,
                action=request.action,
                tif=request.tif,
                coid=request.coid,
                symbol=request.underlying_symbol,
            )
        except Exception as exc:
            raise BrokerError(f"IB place_vertical_spread failed: {exc}") from exc
        result = self._to_order_result(raw)
        result.is_combo = True
        return result
