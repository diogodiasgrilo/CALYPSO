"""IB adapter for CALYPSO — Phase A standalone module.

Wraps Voyz/ibind 0.1.23 against IBKR's Client Portal Web API using OAuth 1.0a
(no IB Gateway, no IBC, no weekly phone tap — per the architecture pivot
documented in docs/migration/SAXO_TO_IB_MIGRATION_PLAN.md).

Phase A scope (this file's current state):
  ✓ Connection lifecycle: connect / disconnect / is_connected
  ✓ 3-stage auth: LST → ssodh/init → auth/status (verified per migration plan)
  ✓ Account discovery
  ✓ pyCrypto fast-fail safety assertion
  ✓ Saxo-compat properties (`client_key`)
  ✓ Contract qualification with conid cache (qualify_contract)
  ✓ Read methods (quotes, positions, chains, greeks, orders, history, fx)
  ─ Write methods (place_order, place_iron_condor) — Phase A.4
  ─ WebSocket streaming with smd refresh    — Phase A.5
  ─ Order-state reconcile on reconnect      — Phase A.7
  ─ Retry + circuit breaker                 — Phase A.8

Phase B will introduce shared/broker/{interface,saxo_adapter,ibkr_adapter}.py
to give HYDRA a single broker-agnostic interface. Until then this module is
standalone — NOT imported by HYDRA, NOT importable into the production bot.

Mapping from SaxoClient methods (kept in module-level docstring so Phase B
authors don't have to chase it down): see
docs/migration/SAXO_TO_IB_MIGRATION_PLAN.md §11.

CP API field codes (from research_scratch/10_cpapi_streaming.md, verified
against ibind/client/ibkr_definitions.py):

  Quote fields (live_marketdata_snapshot):
     31 = last
     84 = bid
     86 = ask
     88 = bid size
     85 = ask size
   7635 = mark price

  Greeks fields:
   7308 = delta
   7309 = gamma
   7310 = theta
   7311 = vega
   7633 = implied volatility (per strike, NOT 7283 which is something else)
   7638 = open interest

  Status fields:
   6509 = market data availability (R=real-time, D=delayed, Z=stale)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from ibind import IbkrClient, OrderRequest, QuestionType  # module-level so tests can patch cleanly

from shared.ib_oauth import (
    IBKRCredentials,
    assert_safe_crypto_backend,
    build_oauth1a_config,
)

logger = logging.getLogger(__name__)


# ─── Field code constants (CP API live_marketdata_snapshot) ─────────────────
# See module docstring for the full list.

FIELD_LAST = "31"
FIELD_BID = "84"
FIELD_ASK = "86"
FIELD_BID_SIZE = "88"
FIELD_ASK_SIZE = "85"
FIELD_MARK = "7635"
FIELD_DELTA = "7308"
FIELD_GAMMA = "7309"
FIELD_THETA = "7310"
FIELD_VEGA = "7311"
FIELD_IV = "7633"
FIELD_OI = "7638"
FIELD_AVAILABILITY = "6509"

DEFAULT_QUOTE_FIELDS = [
    FIELD_LAST, FIELD_BID, FIELD_ASK, FIELD_BID_SIZE, FIELD_ASK_SIZE,
    FIELD_MARK, FIELD_AVAILABILITY,
]
DEFAULT_GREEKS_FIELDS = [
    FIELD_DELTA, FIELD_GAMMA, FIELD_THETA, FIELD_VEGA, FIELD_IV, FIELD_OI,
]
DEFAULT_OPTION_QUOTE_FIELDS = DEFAULT_QUOTE_FIELDS + DEFAULT_GREEKS_FIELDS


# ─── Order placement constants ──────────────────────────────────────────────

# IBKR's published USD spread template conid — used as the prefix in the
# `conidex` field for USD multi-leg combos (iron condors, vertical spreads).
# Verified in research_scratch/09_cpapi_combo_orders.md against IBKR Campus
# combo-order docs + ibind/examples/rest_06_options_chain.py.
SPREAD_TEMPLATE_CONID = 28812380

# Default answers for IBKR's order-reply prompts. Caller can override per-call.
#
# Reply prompts fire on each place_order/modify_order call where IBKR wants
# us to confirm a warning. Our defaults match Brandon-style 0DTE flow:
#   • Confirm price-deviation prompts (0DTE wings often price 3%+ off mid)
#   • Confirm immediate-fill prompts (combos at mid often fill instantly)
#   • Refuse "no market data" prompts (we always have OPRA — refusing means
#     "abort the trade" which is safer than placing blind)
#   • Refuse stop-order prompts (we don't use native stop orders)
#
# Per research_scratch/12_ibind_errors_lifecycle.md: answers dict must use
# QuestionType enum members or matching string keys.
DEFAULT_ORDER_ANSWERS = {
    QuestionType.PRICE_PERCENTAGE_CONSTRAINT: True,  # 0DTE wings often price 3%+ off mid
    QuestionType.ORDER_VALUE_LIMIT: True,            # our 10c IC notional is small but flagged
    QuestionType.TICK_SIZE_LIMIT: True,              # CBOE combo $0.05 rounding edge cases
    QuestionType.TRIGGER_AND_FILL: True,             # combos at mid often fill instantly
    QuestionType.MANDATORY_CAP_PRICE: True,          # IBKR cap-price safety; we want it on
    QuestionType.CASH_QUANTITY: True,                # info disclosure
    QuestionType.CASH_QUANTITY_ORDER: True,          # info disclosure
    QuestionType.DISRUPTIVE_ORDERS: True,            # IBKR may reject; informational
    QuestionType.MISSING_MARKET_DATA: False,         # we always have OPRA — refuse = abort
    QuestionType.STOP_ORDER_RISKS: False,            # we don't use native stop orders
    QuestionType.ORDER_SIZE_LIMIT: False,            # safety: don't auto-confirm oversize
    QuestionType.SIZE_MODIFICATION_LIMIT: False,     # safety: don't auto-confirm large mods
    QuestionType.MULTIPLE_ACCOUNTS: False,           # we trade one account at a time
    QuestionType.CLOSE_POSITION: False,              # don't auto-close-all in response to anything
}


# ─── Public exceptions ──────────────────────────────────────────────────────


class IBClientError(Exception):
    """Base exception for IBClient. Catch this to handle any IB-side failure."""


class IBAuthError(IBClientError):
    """OAuth or brokerage session failure (LST handshake / ssodh / auth/status).

    Raised when:
      - LST handshake returns 401 with `invalid consumer` (pre-activation OR
        wrong consumer key)
      - ssodh/init returns authenticated=false / connected=false
      - auth/status returns competing=true (another session active on the
        account elsewhere)
    """


class IBConnectionError(IBClientError):
    """Network / protocol failure reaching api.ibkr.com.

    Raised on connection refused, DNS error, TLS handshake fail, etc. Wraps
    the underlying ibind/requests exception in `self.__cause__`.
    """


# ─── Config dataclass ───────────────────────────────────────────────────────


@dataclass
class IBConfig:
    """Loaded configuration for an IBClient instance.

    Phase A: constructed manually from env vars in dev/test.
    Phase B: built by shared/broker/__init__.py factory from JSON config +
             GCP Secret Manager values.

    Fields:
      credentials: IBKR OAuth credentials bundle (loaded by ib_oauth.py)
      account_id: optional pinned account ID; if None, discovered from
                  managedAccounts on connect (typical case for single-account
                  setups)
      tickle_interval_seconds: ibind's Tickler thread cadence to keep the
                  brokerage session warm (default 60s; IBKR idle timeout
                  is ~6 minutes)
      connection_timeout_seconds: hard cap on initial connect handshake;
                  beyond this we raise IBConnectionError
      debug_log_payloads: if True, log full IBKR responses at DEBUG level.
                  NEVER enable in production (responses may contain account
                  values, order IDs, etc.)
    """
    credentials: IBKRCredentials
    account_id: Optional[str] = None
    tickle_interval_seconds: int = 60
    connection_timeout_seconds: float = 30.0
    debug_log_payloads: bool = False

    # Phase A.7 will add: reconcile_state_path: Path  ─ where to load/save
    #   bot's view of open orders/positions for cross-check on reconnect


# ─── Main client ────────────────────────────────────────────────────────────


class IBClient:
    """Synchronous IBKR adapter wrapping ibind 0.1.23.

    Public API designed to match SaxoClient where reasonable so the Phase B
    `shared/broker/ibkr_adapter.py` shim is mechanical. HYDRA strategy code
    does NOT import this class directly; it goes through the broker
    abstraction.

    Lifecycle:
        client = IBClient(config)
        client.connect()                # 3-stage OAuth+session init
        try:
            # ... do work via client.get_quote / place_order / etc ...
            pass
        finally:
            client.disconnect()

    Thread safety: ibind's IbkrClient is single-threaded; we serialize calls
    via an internal lock so multiple callers from one process don't race.
    StreamingManager (Phase A.5) runs on its own thread but only writes to
    its own internal cache, so no cross-talk.

    Saxo→IB method mapping kept in migration plan §11.
    """

    def __init__(self, config: IBConfig):
        self.cfg = config
        self._client = None  # type: ignore[assignment]  # set by connect()
        self._connected = False
        self._account_id: Optional[str] = config.account_id
        # Lock for serializing ibind calls across threads.
        # ibind is documented as not thread-safe per github.com/Voyz/ibind/issues.
        self._call_lock = threading.RLock()
        # conid cache for qualify_contract — cleared on disconnect.
        # Key: (symbol, expiry_iso, strike, right, trading_class, sec_type)
        self._conid_cache: dict[tuple, int] = {}

    # ─── Connection lifecycle ──────────────────────────────────────────────

    def connect(self) -> bool:
        """3-stage connect: LST handshake → brokerage session → auth/status.

        Returns True on success; raises IBAuthError or IBConnectionError on
        failure. After return, client is ready to place orders and read data.

        Phase A version: synchronous, blocks until all 3 stages pass or fails.
        No automatic retry — caller decides whether to retry. Phase A.8 will
        add an outer retry+circuit-breaker layer.
        """
        assert_safe_crypto_backend()

        logger.info(
            "IBClient connecting — environment=%s consumer_key=%s",
            self.cfg.credentials.environment,
            self.cfg.credentials.consumer_key,
        )

        # Stage 1: LST handshake (happens inside IbkrClient.__init__ when
        # use_oauth=True). On failure: 401 with invalid consumer (pending
        # activation) or other error.
        try:
            oauth_cfg = build_oauth1a_config(
                self.cfg.credentials,
                init_brokerage_session=True,
            )
            with self._call_lock:
                self._client = IbkrClient(
                    use_oauth=True,
                    oauth_config=oauth_cfg,
                )
        except Exception as exc:
            err_str = str(exc).lower()
            if any(k in err_str for k in ("401", "unauthorized", "invalid consumer", "invalid_token")):
                raise IBAuthError(
                    f"LST handshake failed (pre-activation OR wrong consumer key): {exc}"
                ) from exc
            raise IBConnectionError(
                f"Connection failed at LST stage: {exc}"
            ) from exc

        logger.info("IBClient stage 1/3 ok: live session token issued")

        # Stage 2: brokerage session (init_brokerage_session=True triggers
        # ssodh/init inside ibind on construction; verify by checking
        # authentication status).
        # Stage 3: explicit auth/status check.
        try:
            with self._call_lock:
                status_result = self._client.authentication_status()
            status_data = getattr(status_result, "data", {}) or {}
            authenticated = bool(status_data.get("authenticated", False))
            connected = bool(status_data.get("connected", False))
            competing = bool(status_data.get("competing", False))
            logger.info(
                "IBClient stage 3/3 auth status: authenticated=%s connected=%s competing=%s",
                authenticated, connected, competing,
            )
            if not (authenticated and connected):
                raise IBAuthError(
                    f"Auth status check failed: data={status_data!r}"
                )
            if competing:
                raise IBAuthError(
                    "Auth status reports competing session — another client "
                    "is logged into this account. Sign out elsewhere, retry."
                )
        except IBAuthError:
            raise
        except Exception as exc:
            raise IBAuthError(
                f"auth/status check errored after LST success: {exc}"
            ) from exc

        # Resolve account ID if not pinned
        if not self._account_id:
            self._account_id = self._discover_account_id()

        self._connected = True
        logger.info(
            "IBClient connected successfully — account=%s",
            self._account_id,
        )
        return True

    def _discover_account_id(self) -> str:
        """Look up the IBKR account ID via portfolio_accounts.

        For most single-account retail users this returns one DU* (paper) or
        Uxxxx* (live) account. Multi-account setups need to pin via
        IBConfig.account_id.
        """
        with self._call_lock:
            result = self._client.portfolio_accounts()
        data = getattr(result, "data", []) or []
        if not data:
            raise IBAuthError(
                "No managed accounts returned by IBKR — likely an account "
                "permission issue or fresh activation propagating"
            )
        # Pick the first account; warn if multiple
        if len(data) > 1:
            logger.warning(
                "IBClient: multiple accounts visible (%d) — using first; "
                "pin via IBConfig.account_id to be explicit. Accounts: %s",
                len(data),
                [a.get("accountId") for a in data],
            )
        return data[0]["accountId"]

    def disconnect(self) -> None:
        """Tear down the brokerage session cleanly.

        Calls ibind's logout/shutdown if available. Idempotent — safe to call
        even if connect() failed partway through.
        """
        if not self._client:
            return
        try:
            with self._call_lock:
                # ibind's shutdown stops the Tickler thread + clears session
                if hasattr(self._client, "stop_tickler"):
                    self._client.stop_tickler()
                if hasattr(self._client, "close_session"):
                    self._client.close_session()
        except Exception as exc:
            logger.warning("IBClient disconnect cleanup non-fatal error: %s", exc)
        finally:
            self._connected = False
            self._conid_cache.clear()
            logger.info("IBClient disconnected")

    def is_connected(self) -> bool:
        """Cheap check — returns last-known state. Does NOT round-trip to IBKR.

        For an authoritative live check, call check_auth_status() (Phase A.3).
        """
        return self._connected

    # ─── Account properties (Saxo-compat) ─────────────────────────────────

    @property
    def account_id(self) -> str:
        """Resolved account ID. Raises if connect() hasn't been called yet."""
        if not self._account_id:
            raise IBClientError(
                "account_id not yet resolved — call connect() first"
            )
        return self._account_id

    @property
    def client_key(self) -> str:
        """Saxo-compat alias for account_id.

        SaxoClient exposed .client_key as the account identifier. Some HYDRA
        code reads this for use in order-status URLs. Keeping the name aligned
        so the Phase B broker abstraction can swap in IBClient without
        renaming call sites.
        """
        return self.account_id

    @property
    def is_paper(self) -> bool:
        """True if connected to paper trading.

        Inferred from the environment field of the loaded credentials. Always
        correct when credentials are loaded via ib_oauth.load_credentials()
        which keys files per-environment.
        """
        return self.cfg.credentials.environment == "paper"

    @property
    def is_live(self) -> bool:
        """True if connected to live trading. Inverse of is_paper."""
        return self.cfg.credentials.environment == "live"

    # ─── Context manager support ──────────────────────────────────────────

    def __enter__(self) -> "IBClient":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        env = self.cfg.credentials.environment
        connected = "connected" if self._connected else "disconnected"
        return f"<IBClient env={env} {connected} account={self._account_id or '?'}>"

    # ─── Internal helpers ─────────────────────────────────────────────────

    def _require_connected(self) -> None:
        if not self._connected or self._client is None:
            raise IBClientError(
                "IBClient not connected — call connect() first"
            )

    def _unwrap(self, result):
        """Unwrap ibind's Result object; raise IBClientError on error."""
        if result is None:
            raise IBClientError("ibind returned None — unexpected")
        # Result is a dataclass-like: .data, .request, .error
        err = getattr(result, "error", None)
        if err:
            raise IBClientError(f"ibind error: {err}")
        return getattr(result, "data", result)

    # ─── Contract qualification (conid cache) ─────────────────────────────

    def qualify_contract(
        self,
        symbol: str,
        expiry: Optional[date] = None,
        strike: Optional[float] = None,
        right: Optional[str] = None,
        trading_class: str = "SPXW",
        sec_type: Optional[str] = None,
    ) -> int:
        """Resolve a contract identifier to its IBKR conid.

        Cached by (symbol, expiry, strike, right, trading_class). Per the
        migration plan, SPX 0DTE uses trading_class='SPXW' (PM-settled
        weeklies). For the underlying SPX index quote, pass expiry/strike/
        right=None and sec_type='IND'.

        Flow (per research_scratch/09_cpapi_combo_orders.md):
          1. search_contract_by_symbol(symbol) → list of conid candidates
          2. For options: search_secdef_info_by_conid(underlying_conid,
             sec_type='OPT', month, exchange='CBOE', strike, right)

        Args:
            symbol: 'SPX', 'VIX', etc.
            expiry: option expiry (None for underlying)
            strike: option strike (None for underlying)
            right: 'C' or 'P' (None for underlying)
            trading_class: 'SPXW' for 0DTE; 'SPX' for monthly AM-settled
            sec_type: explicit override; 'IND' for index, 'OPT' for option,
                'STK' for stock. Inferred from args if None.

        Returns:
            conid (int)
        """
        self._require_connected()
        if sec_type is None:
            sec_type = "OPT" if strike is not None else "IND"

        cache_key = (
            symbol,
            expiry.isoformat() if expiry else None,
            strike,
            right,
            trading_class,
            sec_type,
        )
        with self._call_lock:
            if cache_key in self._conid_cache:
                return self._conid_cache[cache_key]

            # Step 1: resolve underlying conid
            search_result = self._client.search_contract_by_symbol(
                symbol=symbol,
                sec_type="IND" if sec_type == "OPT" else sec_type,
            )
            candidates = self._unwrap(search_result) or []
            if not candidates:
                raise IBClientError(f"No contract found for symbol={symbol}")
            underlying_conid = candidates[0].get("conid") if isinstance(candidates, list) else None
            if not underlying_conid:
                raise IBClientError(
                    f"Unexpected contract search response shape: {candidates!r}"
                )

            # Step 2: for options, walk the secdef chain to the specific strike
            if sec_type == "OPT":
                if expiry is None or strike is None or right is None:
                    raise IBClientError(
                        "Option qualification needs expiry, strike, right"
                    )
                month = expiry.strftime("%b%y").upper()  # e.g. 'MAY26'
                secdef_result = self._client.search_secdef_info_by_conid(
                    conid=str(underlying_conid),
                    sec_type="OPT",
                    month=month,
                    exchange="CBOE",
                    strike=str(strike),
                    right=right.upper(),
                )
                secdef_data = self._unwrap(secdef_result) or []
                # Filter by trading_class
                matches = [
                    d for d in (secdef_data if isinstance(secdef_data, list) else [secdef_data])
                    if (d.get("tradingClass") == trading_class
                        or trading_class.upper() == d.get("tradingClass", "").upper())
                ]
                if not matches:
                    raise IBClientError(
                        f"No {trading_class} option matched: symbol={symbol} "
                        f"expiry={expiry} strike={strike} right={right}"
                    )
                conid = matches[0]["conid"]
            else:
                conid = underlying_conid

            conid = int(conid)
            self._conid_cache[cache_key] = conid
            return conid

    # ─── Quotes (read methods) ────────────────────────────────────────────

    def get_quote(
        self,
        conid: int,
        fields: Optional[Iterable[str]] = None,
    ) -> dict:
        """Fetch a single snapshot quote for a conid.

        REST-based — does NOT subscribe to streaming. For ongoing monitoring,
        use the StreamingManager (Phase A.5).

        Returns dict with keys: bid, ask, last, mid, mark, bid_size, ask_size,
        availability ('R'=real-time, 'D'=delayed, 'Z'=stale), conid, raw.
        Values are None when IBKR returns nothing for the field.

        SaxoClient.get_quote() equivalent. Caller doesn't need to know the
        IBKR field codes — defaults cover what HYDRA reads today.
        """
        self._require_connected()
        fields = list(fields) if fields else DEFAULT_QUOTE_FIELDS
        with self._call_lock:
            result = self._client.live_marketdata_snapshot(
                conids=str(conid), fields=fields,
            )
        data = self._unwrap(result) or []
        # IBKR returns a list with one entry per conid
        if isinstance(data, list) and data:
            row = data[0]
        elif isinstance(data, dict):
            row = data
        else:
            return {"conid": conid, "raw": data}
        return self._parse_quote_row(row, conid)

    def get_quotes_batch(
        self,
        conids: list[int],
        fields: Optional[Iterable[str]] = None,
    ) -> list[dict]:
        """Fetch snapshot quotes for many conids in one CP API call.

        CP API caps batch size at 100 conids per request (since Dec 2025).
        Caller is responsible for chunking if more than 100 needed.

        SaxoClient.get_quotes_batch() equivalent.
        """
        self._require_connected()
        if len(conids) > 100:
            raise IBClientError(
                f"get_quotes_batch: max 100 conids per call, got {len(conids)}"
            )
        if not conids:
            return []
        fields = list(fields) if fields else DEFAULT_QUOTE_FIELDS
        with self._call_lock:
            result = self._client.live_marketdata_snapshot(
                conids=",".join(str(c) for c in conids),
                fields=fields,
            )
        data = self._unwrap(result) or []
        rows = data if isinstance(data, list) else [data]
        # IBKR doesn't guarantee response order matches request order;
        # parse each row, key by conid for caller's convenience.
        return [self._parse_quote_row(r, r.get("conid")) for r in rows]

    def get_vix_price(self) -> Optional[float]:
        """Latest VIX index price (mid of bid/ask, or last as fallback).

        SaxoClient.get_vix_price() equivalent — returns a single float, not
        the full quote dict.
        """
        conid = self.qualify_contract("VIX", sec_type="IND")
        q = self.get_quote(conid)
        if q.get("mid") is not None:
            return q["mid"]
        return q.get("last")

    def get_option_greeks(self, conid: int) -> dict:
        """Snapshot of delta/gamma/theta/vega/IV/OI for an option.

        Returns the same dict shape as get_quote, with greeks fields populated
        (delta, gamma, theta, vega, iv, open_interest).

        SaxoClient.get_option_greeks() equivalent.
        """
        self._require_connected()
        with self._call_lock:
            result = self._client.live_marketdata_snapshot(
                conids=str(conid),
                fields=DEFAULT_OPTION_QUOTE_FIELDS,
            )
        data = self._unwrap(result) or []
        row = data[0] if isinstance(data, list) and data else (data or {})
        return self._parse_quote_row(row, conid, include_greeks=True)

    @staticmethod
    def _parse_quote_row(row: dict, conid: Optional[int] = None,
                         *, include_greeks: bool = False) -> dict:
        """Normalize one row of ibind's snapshot response to our shape."""
        if not isinstance(row, dict):
            return {"conid": conid, "raw": row}

        def f(field_code: str) -> Optional[float]:
            v = row.get(field_code)
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        bid = f(FIELD_BID)
        ask = f(FIELD_ASK)
        mid = (bid + ask) / 2 if (bid is not None and ask is not None) else None

        out = {
            "conid": conid or row.get("conid"),
            "bid": bid,
            "ask": ask,
            "last": f(FIELD_LAST),
            "mid": mid,
            "mark": f(FIELD_MARK),
            "bid_size": f(FIELD_BID_SIZE),
            "ask_size": f(FIELD_ASK_SIZE),
            "availability": row.get(FIELD_AVAILABILITY),
            "raw": row,
        }
        if include_greeks:
            out.update({
                "delta": f(FIELD_DELTA),
                "gamma": f(FIELD_GAMMA),
                "theta": f(FIELD_THETA),
                "vega": f(FIELD_VEGA),
                "iv": f(FIELD_IV),
                "open_interest": f(FIELD_OI),
            })
        return out

    # ─── Account / portfolio (read) ───────────────────────────────────────

    def get_account_info(self) -> dict:
        """Account metadata (account type, capabilities, currency).

        SaxoClient.get_account_info() equivalent.
        """
        self._require_connected()
        with self._call_lock:
            result = self._client.portfolio_account_information(
                account_id=self.account_id,
            )
        return self._unwrap(result) or {}

    def get_balance(self, currency: str = "USD") -> dict:
        """Live tradable amount in `currency`, plus diagnostics.

        For EUR-base + USD-trade (CALYPSO's case):
          USD_tradable = EUR_availablefunds × ExchangeRate(USD per EUR)
                       + USD_CashBalance
        Per research_scratch/11_cpapi_margin_account.md:
          - portfolio_summary returns base-currency values (EUR)
          - get_ledger returns per-currency cash + exchangerate
          - NO 3-minute throttle on CP API (unlike TWS) — can poll at 1Hz,
            but the underlying risk engine still updates at ~3s so polling
            faster than that is pointless
          - exchangerate direction (base-per-quote vs quote-per-base) needs
            first-call verification on live data — see Phase A.10 smoke test

        Returns:
            dict with keys:
              tradable: float in `currency`
              currency: requested currency (echoed)
              base_currency: account base (e.g. 'EUR')
              base_available: AvailableFunds in base currency
              exchange_rate: rate from ledger
              cash_in_target: CashBalance in `currency` from ledger
              raw_summary: unmodified portfolio_summary
              raw_ledger: unmodified get_ledger
        """
        self._require_connected()
        with self._call_lock:
            summary_result = self._client.portfolio_summary(
                account_id=self.account_id,
            )
            ledger_result = self._client.get_ledger(
                account_id=self.account_id,
            )
        summary = self._unwrap(summary_result) or {}
        ledger = self._unwrap(ledger_result) or {}

        # Summary uses keys like {"availablefunds": {"amount": ..., "currency": ...}}
        avail = summary.get("availablefunds") or {}
        base_avail = float(avail.get("amount", 0)) if avail else 0.0
        base_currency = avail.get("currency") or self._guess_base_currency(ledger) or "USD"

        if currency == base_currency:
            tradable = base_avail
            exchange_rate = 1.0
            cash_in_target = base_avail
        else:
            # Per ledger schema: key by ISO currency code, value contains
            # cashbalance + exchangerate (rate from CURRENCY to base, or
            # base to CURRENCY — needs verification per agent 11)
            row = ledger.get(currency, {}) if isinstance(ledger, dict) else {}
            exchange_rate = float(row.get("exchangerate", 0)) or 1.0
            cash_in_target = float(row.get("cashbalance", 0) or 0)
            # Empirical default: assume `exchangerate` is base-per-target
            # (i.e., 1 USD = ER EUR). USD_tradable = EUR_avail / ER + USD_cash.
            # Will be confirmed/inverted in Phase A.10 smoke test.
            if exchange_rate > 0:
                tradable = base_avail / exchange_rate + cash_in_target
            else:
                tradable = cash_in_target

        return {
            "tradable": tradable,
            "currency": currency,
            "base_currency": base_currency,
            "base_available": base_avail,
            "exchange_rate": exchange_rate,
            "cash_in_target": cash_in_target,
            "raw_summary": summary,
            "raw_ledger": ledger,
        }

    @staticmethod
    def _guess_base_currency(ledger: dict) -> Optional[str]:
        """Find the row marked isbase=True in ledger response."""
        if not isinstance(ledger, dict):
            return None
        for code, row in ledger.items():
            if isinstance(row, dict) and row.get("isbase"):
                return code
        return None

    def get_positions(self) -> list[dict]:
        """All open positions on this account, across all pages.

        Iterates through paginated /portfolio/{accountId}/positions/{page}.
        Returns a flat list.

        SaxoClient.get_positions() equivalent. HYDRA filters this list by
        asset_type/symbol downstream.
        """
        self._require_connected()
        all_positions: list[dict] = []
        page = 0
        max_pages = 50  # safety cap
        while page < max_pages:
            with self._call_lock:
                result = self._client.positions(
                    account_id=self.account_id, page=page,
                )
            data = self._unwrap(result)
            if not data:
                break
            batch = data if isinstance(data, list) else [data]
            all_positions.extend(batch)
            # IBKR returns up to 30 per page; if we got fewer, we're done
            if len(batch) < 30:
                break
            page += 1
        return all_positions

    def get_fx_rate(self, source: str, target: str) -> Optional[float]:
        """Latest FX rate between two ISO currency codes.

        Uses CP API's currency_exchange_rate endpoint. Note: only one direction
        may be exposed — call site should handle inverse if needed.

        SaxoClient.get_fx_rate() equivalent.
        """
        self._require_connected()
        with self._call_lock:
            result = self._client.currency_exchange_rate(
                source=source, target=target,
            )
        data = self._unwrap(result)
        if isinstance(data, dict):
            rate = data.get("rate") or data.get(f"{source}_{target}")
            return float(rate) if rate else None
        if isinstance(data, (int, float, str)):
            try:
                return float(data)
            except (TypeError, ValueError):
                return None
        return None

    # ─── Options chain ────────────────────────────────────────────────────

    def get_option_chain(
        self,
        symbol: str,
        expiry: date,
        trading_class: str = "SPXW",
    ) -> list[float]:
        """List of strike prices available for a given expiry.

        Uses search_strikes_by_conid under the hood. Returns the union of
        call + put strikes (IBKR returns them separately; for SPX they
        match).

        SaxoClient.get_option_chain() equivalent.
        """
        self._require_connected()
        # Resolve underlying conid first
        underlying_conid = self.qualify_contract(symbol, sec_type="IND")
        month = expiry.strftime("%b%y").upper()
        with self._call_lock:
            result = self._client.search_strikes_by_conid(
                conid=str(underlying_conid),
                sec_type="OPT",
                month=month,
                exchange="CBOE",
            )
        data = self._unwrap(result) or {}
        # Response shape: {"call": [strikes], "put": [strikes]}
        if isinstance(data, dict):
            calls = data.get("call") or data.get("calls") or []
            puts = data.get("put") or data.get("puts") or []
            return sorted({float(s) for s in (calls + puts)})
        if isinstance(data, list):
            return sorted({float(s) for s in data})
        return []

    # ─── Orders (read) ────────────────────────────────────────────────────

    def get_open_orders(self) -> list[dict]:
        """All live orders on this account.

        SaxoClient.get_open_orders() equivalent. Returns raw ibind shape.
        """
        self._require_connected()
        with self._call_lock:
            result = self._client.live_orders(account_id=self.account_id)
        data = self._unwrap(result) or {}
        # ibind returns {"orders": [...]} wrapping
        if isinstance(data, dict):
            return data.get("orders") or []
        return data if isinstance(data, list) else []

    def get_order_status(self, order_id: str) -> dict:
        """Current status of a specific order_id.

        SaxoClient.get_order_status() equivalent.
        """
        self._require_connected()
        with self._call_lock:
            result = self._client.order_status(order_id=str(order_id))
        return self._unwrap(result) or {}

    # ─── Historical bars ─────────────────────────────────────────────────

    def get_chart_data(
        self,
        symbol: str,
        bar: str = "1min",
        period: str = "1d",
        outside_rth: bool = False,
    ) -> list[dict]:
        """Historical OHLC bars for a symbol.

        Args:
            symbol: e.g. 'SPX' (uses CP API's symbol-keyed history endpoint)
            bar: bar size — '1min', '5min', '15min', '1h', '1d', etc.
            period: lookback — '1d', '5d', '1m', '1y', etc.
            outside_rth: include extended hours

        Returns:
            list of dicts with keys: t (ms epoch), o, h, l, c, v

        SaxoClient.get_chart_data() equivalent.
        """
        self._require_connected()
        with self._call_lock:
            result = self._client.marketdata_history_by_symbol(
                symbol=symbol,
                bar=bar,
                period=period,
                outside_rth=outside_rth,
            )
        data = self._unwrap(result) or {}
        if isinstance(data, dict):
            return data.get("data") or []
        return data if isinstance(data, list) else []

    # ─── Order placement (write methods) ──────────────────────────────────

    @staticmethod
    def _round_to_increment(price: float, increment: float = 0.05) -> float:
        """Round price to nearest CBOE-allowed increment.

        SPX options combo orders must use $0.05 net-credit increments on the
        CBOE Complex Order Book. Non-conforming prices are rejected outright.
        """
        return round(price / increment) * increment

    @staticmethod
    def build_ic_conidex(
        short_call_conid: int, long_call_conid: int,
        short_put_conid: int,  long_put_conid: int,
    ) -> str:
        """Construct the CP API conidex string for a 4-leg iron condor.

        Format (per research_scratch/09_cpapi_combo_orders.md):
            "{template};;;{sc_conid}/-1,{lc_conid}/1,{sp_conid}/-1,{lp_conid}/1"

        Where template = SPREAD_TEMPLATE_CONID (28812380, IBKR's universal
        USD spread template). The three semicolons are a literal grammar
        requirement. Negative ratio = SELL leg; positive = BUY leg.

        For a SHORT iron condor we SELL the call spread (sc short, lc long)
        and SELL the put spread (sp short, lp long).

        Exposed as a static method so tests can verify the exact bytes
        without instantiating an IBClient.
        """
        return (
            f"{SPREAD_TEMPLATE_CONID};;;"
            f"{short_call_conid}/-1,{long_call_conid}/1,"
            f"{short_put_conid}/-1,{long_put_conid}/1"
        )

    @staticmethod
    def build_vertical_conidex(
        short_conid: int, long_conid: int,
    ) -> str:
        """Construct conidex for a 2-leg vertical spread (call or put).

        Used for one-sided entries (when Brandon GEX-ADJ skips one side) and
        for stop-out closes (closing one side of an open IC atomically).

        Negative ratio = SELL leg; positive = BUY leg. The credit side is
        the SHORT leg.
        """
        return (
            f"{SPREAD_TEMPLATE_CONID};;;"
            f"{short_conid}/-1,{long_conid}/1"
        )

    def place_iron_condor(
        self,
        expiry: date,
        short_call_strike: float, long_call_strike: float,
        short_put_strike: float,  long_put_strike: float,
        contracts: int,
        net_credit_limit: float,
        tif: str = "DAY",
        coid: Optional[str] = None,
        symbol: str = "SPX",
        trading_class: str = "SPXW",
        answers: Optional[dict] = None,
    ) -> dict:
        """Place a 4-leg SPX iron condor as a single net-credit combo limit.

        For a SHORT IC (selling premium):
          • side = "SELL"
          • price = POSITIVE — IBKR's counter-intuitive convention for
            "price you receive in credit" when SELLing a combo.
            (See https://www.ibkrguides.com/traderworkstation/notes-on-combination-orders.htm)

        Atomic-fill enforcement: CP API has NO direct NonGuaranteed flag
        equivalent to TWS API's. Caller monitors `sor` WebSocket for
        partial-fill detection — that's Phase A.5 / A.7 territory.

        Args:
            expiry: option expiry date (today for 0DTE)
            short_call_strike, long_call_strike, short_put_strike, long_put_strike:
                the 4 strike prices. Call spread is short<long; put spread is
                long<short (i.e., short closer to spot, long further OTM).
            contracts: number of spreads (not legs)
            net_credit_limit: minimum credit per spread we'll accept
            tif: 'DAY' (default — 0DTE doesn't survive past close anyway)
            coid: client order ID for dedup; if None, ibind generates one
            symbol: 'SPX' (or other underlying for non-SPX uses)
            trading_class: 'SPXW' for 0DTE; 'SPX' for monthly AM-settled
            answers: override DEFAULT_ORDER_ANSWERS reply-prompt dict

        Returns:
            dict with order_id, status (PreSubmitted/Submitted/Filled/etc.),
            local_order_id, conidex, raw

        SaxoClient: no exact equivalent; HYDRA composes 4 separate Saxo
        orders via place_multi_leg_order. The IB conidex approach is the
        single-order path.
        """
        self._require_connected()

        # Resolve conids for all 4 legs (cached after first call)
        sc = self.qualify_contract(symbol, expiry, short_call_strike, "C", trading_class)
        lc = self.qualify_contract(symbol, expiry, long_call_strike,  "C", trading_class)
        sp = self.qualify_contract(symbol, expiry, short_put_strike,  "P", trading_class)
        lp = self.qualify_contract(symbol, expiry, long_put_strike,   "P", trading_class)

        conidex = self.build_ic_conidex(sc, lc, sp, lp)
        price = self._round_to_increment(net_credit_limit, 0.05)

        order = OrderRequest(
            conid=None,
            conidex=conidex,
            sec_type="BAG",
            side="SELL",           # SHORT IC = SELL the combo
            order_type="LMT",
            price=price,           # POSITIVE = credit received (IBKR convention)
            quantity=contracts,
            tif=tif,
            acct_id=self.account_id,
            coid=coid,
        )
        logger.info(
            "IC place: %s %s expiry=%s C:%.0f/%.0f P:%.0f/%.0f x%d net_credit=%.2f tif=%s",
            symbol, trading_class, expiry,
            short_call_strike, long_call_strike,
            short_put_strike, long_put_strike,
            contracts, price, tif,
        )

        return self._submit_order(order, answers=answers)

    def place_vertical_spread(
        self,
        expiry: date,
        short_strike: float, long_strike: float,
        right: str,           # 'C' or 'P'
        contracts: int,
        net_credit_limit: float,
        action: str = "SELL", # 'SELL' to open short spread; 'BUY' to close
        tif: str = "DAY",
        coid: Optional[str] = None,
        symbol: str = "SPX",
        trading_class: str = "SPXW",
        answers: Optional[dict] = None,
    ) -> dict:
        """Place a 2-leg vertical spread as a single combo limit.

        Two main uses:
          1. One-sided entries: Brandon GEX-ADJ SKIP'd one side; place only
             the other side as a short vertical
          2. Stop-out close: closing one side of an open IC. Pass
             action='BUY' (we're buying back the spread we sold). price
             should be the maximum debit we'll pay to close.

        For SHORT vertical (selling): side="SELL", positive price = credit.
        For closing (buying): side="BUY", positive price = debit paid.
        """
        self._require_connected()
        if right not in ("C", "P"):
            raise IBClientError(f"right must be 'C' or 'P', got {right!r}")
        if action not in ("SELL", "BUY"):
            raise IBClientError(f"action must be 'SELL' or 'BUY', got {action!r}")

        s = self.qualify_contract(symbol, expiry, short_strike, right, trading_class)
        l = self.qualify_contract(symbol, expiry, long_strike,  right, trading_class)
        conidex = self.build_vertical_conidex(s, l)
        price = self._round_to_increment(net_credit_limit, 0.05)

        order = OrderRequest(
            conid=None,
            conidex=conidex,
            sec_type="BAG",
            side=action,
            order_type="LMT",
            price=price,
            quantity=contracts,
            tif=tif,
            acct_id=self.account_id,
            coid=coid,
        )
        logger.info(
            "Vertical place: %s %s expiry=%s %s short:%.0f long:%.0f x%d price=%.2f side=%s",
            symbol, trading_class, expiry, right,
            short_strike, long_strike, contracts, price, action,
        )

        return self._submit_order(order, answers=answers)

    def place_order(
        self,
        conid: int,
        side: str,
        quantity: int,
        order_type: str = "LMT",
        price: Optional[float] = None,
        tif: str = "DAY",
        coid: Optional[str] = None,
        answers: Optional[dict] = None,
    ) -> dict:
        """Place a single-leg order.

        Used for single options or stock orders. For multi-leg combos use
        place_iron_condor / place_vertical_spread.

        SaxoClient.place_order() equivalent.
        """
        self._require_connected()
        if order_type == "LMT" and price is None:
            raise IBClientError("LMT order requires price")
        if side not in ("BUY", "SELL"):
            raise IBClientError(f"side must be 'BUY' or 'SELL', got {side!r}")

        # Round price to $0.05 only for combo / SPX options
        rounded_price = self._round_to_increment(price, 0.05) if price is not None else None

        order = OrderRequest(
            conid=int(conid),
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=rounded_price,
            tif=tif,
            acct_id=self.account_id,
            coid=coid,
        )
        logger.info(
            "Order place: conid=%d %s %d %s @ %s tif=%s",
            conid, side, quantity, order_type,
            f"{rounded_price:.2f}" if rounded_price else "MKT",
            tif,
        )

        return self._submit_order(order, answers=answers)

    def place_market_order(
        self,
        conid: int,
        side: str,
        quantity: int,
        tif: str = "DAY",
        coid: Optional[str] = None,
        answers: Optional[dict] = None,
    ) -> dict:
        """Place a market order (no price). Used for emergency / stop-out fallback.

        SaxoClient.place_emergency_order() equivalent.
        """
        return self.place_order(
            conid=conid, side=side, quantity=quantity,
            order_type="MKT", price=None, tif=tif, coid=coid, answers=answers,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a working order.

        Returns True on successful cancellation request (the actual cancel
        completes asynchronously — caller should poll get_order_status if
        confirmation is needed).

        SaxoClient.cancel_order() equivalent.
        """
        self._require_connected()
        with self._call_lock:
            result = self._client.cancel_order(
                order_id=str(order_id),
                account_id=self.account_id,
            )
        try:
            self._unwrap(result)
            logger.info("Order cancel: order_id=%s", order_id)
            return True
        except IBClientError as exc:
            logger.warning("Order cancel failed for %s: %s", order_id, exc)
            return False

    def modify_order(
        self,
        order_id: str,
        price: Optional[float] = None,
        quantity: Optional[int] = None,
        answers: Optional[dict] = None,
    ) -> dict:
        """Modify a working order's price or quantity.

        IBKR's modify_order replaces the previous order instance (not amend);
        order_id stays the same but other fields can change.
        """
        self._require_connected()
        # Build a partial order request — ibind merges with existing state
        if price is None and quantity is None:
            raise IBClientError("modify_order needs at least price or quantity")

        rounded_price = self._round_to_increment(price, 0.05) if price is not None else None

        order = OrderRequest(
            conid=None,
            side="SELL",        # required by dataclass; ignored on modify
            quantity=quantity or 0,
            order_type="LMT",
            price=rounded_price,
            acct_id=self.account_id,
        )
        with self._call_lock:
            result = self._client.modify_order(
                order_id=str(order_id),
                order_request=order,
                answers=answers or DEFAULT_ORDER_ANSWERS,
                account_id=self.account_id,
            )
        data = self._unwrap(result) or {}
        logger.info("Order modify: order_id=%s price=%s qty=%s",
                    order_id, rounded_price, quantity)
        return data if isinstance(data, dict) else (data[0] if data else {})

    def what_if_order(
        self,
        order: OrderRequest,
    ) -> dict:
        """Pre-trade margin / cost check WITHOUT placing the order.

        Returns IBKR's 5 blocks: amount, equity, initial, maintenance,
        position — each with current/change/after keys. All values are in
        the account's base currency (EUR for us). Caller parses strings
        like "+4,500.00" and converts to USD via get_balance("USD") if
        needed.

        Used as our pre-trade BP gate (replaces SaxoClient's ORDER-004
        check with broker-authoritative numbers).

        Per research_scratch/11_cpapi_margin_account.md: whatif does NOT
        fire reply prompts (no `answers` param needed).
        """
        self._require_connected()
        with self._call_lock:
            result = self._client.whatif_order(
                order_request=order,
                account_id=self.account_id,
            )
        return self._unwrap(result) or {}

    def _submit_order(
        self,
        order: OrderRequest,
        answers: Optional[dict] = None,
    ) -> dict:
        """Internal: submit an OrderRequest via ibind, normalize the response.

        Reply-prompt handling: pass our DEFAULT_ORDER_ANSWERS unless caller
        overrides. ibind walks the reply loop until IBKR's prompt chain is
        cleared or rejects an unknown prompt (in which case it raises).
        """
        a = answers if answers is not None else DEFAULT_ORDER_ANSWERS
        with self._call_lock:
            result = self._client.place_order(
                order_request=order,
                answers=a,
                account_id=self.account_id,
            )
        data = self._unwrap(result) or {}
        # ibind returns a list (one entry per leg or one per submission)
        if isinstance(data, list):
            data = data[0] if data else {}
        return data
