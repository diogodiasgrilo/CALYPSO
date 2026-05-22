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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from ibind import IbkrClient, OrderRequest, QuestionType  # module-level so tests can patch cleanly

from shared.ib_oauth import (
    IBKRCredentials,
    assert_safe_crypto_backend,
    build_oauth1a_config,
)

logger = logging.getLogger(__name__)


# ─── Field code constants + spread template ─────────────────────────────────
# Sourced from shared/ib_constants.py so ib_streaming.py can import the same
# canonical set without circling back through ib_client.

from shared.ib_constants import (  # noqa: E402
    FIELD_LAST, FIELD_BID, FIELD_ASK, FIELD_BID_SIZE, FIELD_ASK_SIZE,
    FIELD_MARK, FIELD_DELTA, FIELD_GAMMA, FIELD_THETA, FIELD_VEGA,
    FIELD_IV, FIELD_OI, FIELD_AVAILABILITY,
    DEFAULT_QUOTE_FIELDS, DEFAULT_GREEKS_FIELDS, DEFAULT_OPTION_QUOTE_FIELDS,
    SPREAD_TEMPLATE_CONID,
)

# Phase A.8 — retry + per-family circuit breaker primitives. Wired into
# every ibind call via the _ib_call() helper below. Lifecycle methods
# (connect/disconnect) stay un-wrapped: they have specialized error
# translation and are one-shot per session.
from shared.ib_retry import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpen,
    RetryPolicy,
    retry_with_backoff,
)


# ─── Snapshot warmup tuning (Fix #2026-05-18) ──────────────────────────────
# IBKR's /iserver/marketdata/snapshot endpoint needs more than one warmup
# poll for a fresh-this-session conid before it starts returning populated
# price fields. Empirically (Monday 2026-05-18 paper smoke): the FIRST data
# call after preflight returned metadata-only; LATER session calls returned
# populated data immediately. We poll up to 8 × 250ms = 2s after the
# preflight before giving up. Tunable if HYDRA's tick budget needs adjusting.
_SNAPSHOT_MAX_WARMUP_POLLS = 8
_SNAPSHOT_POLL_INTERVAL_S = 0.25
# Keys IBKR returns in the snapshot row that are pure metadata (not market
# data). If a snapshot row contains ONLY these keys, the cache isn't warm.
_SNAPSHOT_METADATA_KEYS = frozenset({"conid", "conidEx", "_updated"})


def _snapshot_has_data(payload) -> bool:
    """True if the snapshot payload has at least one row with at least one
    non-metadata field. Used to decide whether to continue warmup polling.
    """
    if not payload:
        return False
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Any key beyond {conid, conidEx, _updated} means a price/Greek field
        # is populated. The IBKR field-code keys are short numeric strings
        # like "31", "84", "7308" — those count as data here.
        for key in row.keys():
            if key not in _SNAPSHOT_METADATA_KEYS:
                return True
    return False


# ─── place_and_wait_for_fill tuning ────────────────────────────────────────
# Building block used by HYDRA's strategy-level retry loops (progressive-
# chase entry placement, stop-loss escalation). Encapsulates the
# place → poll-status → return-when-done pattern so callers don't
# re-implement polling for every retry level.
_DEFAULT_FILL_TIMEOUT_S = 30.0
_DEFAULT_FILL_POLL_INTERVAL_S = 0.5

# Order status strings IBKR returns that mean the order is in a terminal
# state (won't work anymore). Compared lowercase. `pendingcancel` is
# intentionally NOT here — a cancel-in-progress order can still fill, so
# we keep polling until we see an actually-terminal status.
_TERMINAL_ORDER_STATUSES = frozenset({
    "filled",
    "cancelled",
    "api_cancelled",        # IBKR variant for cancels initiated by us
    "rejected",
    "inactive",             # broker decided the order won't work
    "expired",              # TIF expired
    "presubmitted_cancelled",  # rare race during cancel propagation
})


# ─── Position normalization ────────────────────────────────────────────────
# IBKR's `portfolio/{account}/positions/{page}` endpoint returns flat dicts
# per position with broker-specific field names (`conid`, `position`,
# `lastTradingDay`, `putOrCall`, `avgCost`, etc.). Strategy code wants a
# stable schema that doesn't lock in IBKR field naming + handles option
# metadata uniformly. `_normalize_position_dict` is the single seam.


def _normalize_position_dict(raw_position: dict) -> dict:
    """Convert IBKR's portfolio_positions response entry into a stable shape.

    HYDRA's strategy code expects flat keys (`instrument_id`, `quantity`,
    `side`, `expiry`, `strike`, `right`) that don't lock in IBKR's field
    naming. This helper does the translation in one place so the call
    sites stay clean. Saxo's nested `PositionBase.OptionsData.*` pattern
    is gone — IBKR's flat dict makes this a single pass.

    Args:
        raw_position: one entry from `IBClient.get_positions()` (which is
            the IBKR `portfolio/{accountId}/positions/{page}` shape after
            ibind unwrapping)

    Returns:
        dict with keys:
          instrument_id: int (IBKR conid)
          symbol: str (e.g. "SPX" / "SPXW" / "")
          asset_type: str ("OPT" / "STK" / "IND" / "BAG" / "FUT" / "")
          quantity: int (signed — negative means short)
          side: "LONG" | "SHORT" | "FLAT"
          avg_cost: Optional[float]
          market_price: Optional[float]
          market_value: Optional[float]
          unrealized_pnl: Optional[float]
          currency: str (default "USD" when broker omits)
          # Option-only (None for non-options or when broker omits):
          expiry: Optional[date]
          strike: Optional[float]
          right: Optional[str] ("C" or "P")
          raw: dict (preserved IBKR response for fields we didn't normalize)

    Raises:
        ValueError: `raw_position` is not a dict, or has no `conid`
            (conid is the only field we treat as required — without it
            there's no instrument identity)
    """
    if not isinstance(raw_position, dict):
        raise ValueError(
            f"raw_position must be a dict, got {type(raw_position).__name__}"
        )
    conid = raw_position.get("conid")
    if conid is None:
        raise ValueError(f"position missing conid: {raw_position!r}")

    # Signed quantity. IBKR uses float in the wire format but the
    # strategy always works in whole contracts; cast to int. Defensive
    # against string/bool/None — those become 0.
    quantity_raw = raw_position.get("position", 0)
    try:
        quantity = int(float(quantity_raw))
    except (TypeError, ValueError):
        quantity = 0

    side = "SHORT" if quantity < 0 else ("LONG" if quantity > 0 else "FLAT")

    def _to_float_or_none(key: str) -> Optional[float]:
        v = raw_position.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # Symbol: prefer the explicit `ticker` field; fall back to the first
    # whitespace-delimited token of `contractDesc` (e.g. "SPXW 20MAY26
    # 5500 P" → "SPXW"). Empty string when both are missing — caller
    # decides whether that's an error in their context.
    symbol = (raw_position.get("ticker") or "").strip()
    if not symbol:
        desc = (raw_position.get("contractDesc") or "").strip()
        symbol = desc.split()[0] if desc else ""

    # Right: IBKR uses "P"/"C" or "PUT"/"CALL" depending on endpoint
    # version. Normalize both. Bad/unknown values → None (defensive,
    # don't silently mis-label).
    right_raw = (raw_position.get("putOrCall") or "").strip().upper()
    if right_raw == "PUT":
        right = "P"
    elif right_raw == "CALL":
        right = "C"
    elif right_raw in ("P", "C"):
        right = right_raw
    else:
        right = None

    # Expiry: IBKR delivers as YYYYMMDD string in `lastTradingDay`.
    # contractDesc parsing is intentionally NOT attempted — too fragile
    # across IBKR's format variations across asset classes.
    expiry: Optional[date] = None
    ltd_str = str(raw_position.get("lastTradingDay") or "").strip()
    if len(ltd_str) == 8:
        try:
            expiry = date(int(ltd_str[:4]), int(ltd_str[4:6]), int(ltd_str[6:8]))
        except (ValueError, TypeError):
            expiry = None

    return {
        "instrument_id": int(conid),
        "symbol": symbol,
        "asset_type": (raw_position.get("assetClass") or "").upper(),
        "quantity": quantity,
        "side": side,
        "avg_cost": _to_float_or_none("avgCost"),
        "market_price": _to_float_or_none("mktPrice"),
        "market_value": _to_float_or_none("mktValue"),
        "unrealized_pnl": _to_float_or_none("unrealizedPnl"),
        "currency": raw_position.get("currency") or "USD",
        "expiry": expiry,
        "strike": _to_float_or_none("strike"),
        "right": right,
        "raw": raw_position,
    }


def _build_fill_result_dict(
    *,
    order_id: str,
    raw: dict,
    status: str,
) -> dict:
    """Build the normalized result dict returned by place_and_wait_for_fill.

    Extracted as a module-level helper so the field-name defensive lookup
    logic is unit-testable independent of the place+poll loop. IBKR's
    order status response uses inconsistent field names across endpoints
    (snake_case vs camelCase, `filled` vs `filledQuantity`, etc.); we
    accept several variants.
    """
    filled_qty_raw = (
        raw.get("filled_quantity")
        or raw.get("filledQuantity")
        or raw.get("filled")
        or 0
    )
    try:
        filled_quantity = int(float(filled_qty_raw))
    except (TypeError, ValueError):
        filled_quantity = 0

    avg_price_raw = (
        raw.get("avg_fill_price")
        or raw.get("avgPrice")
        or raw.get("average_price")
        or raw.get("avgFillPrice")
    )
    try:
        avg_fill_price: Optional[float] = (
            float(avg_price_raw) if avg_price_raw is not None and avg_price_raw != "" else None
        )
    except (TypeError, ValueError):
        avg_fill_price = None

    return {
        "order_id": order_id,
        "status": status,
        "filled_quantity": filled_quantity,
        "avg_fill_price": avg_fill_price,
        "raw": raw,
    }


# ─── Order placement constants ──────────────────────────────────────────────

# Locale-independent uppercase 3-letter month names for IBKR's secdef month
# format (e.g. 'MAY26'). datetime.strftime('%b') is locale-dependent and
# emits 'MAI'/'MAGGIO'/'MAI' on non-English VMs — that would be rejected
# by IBKR's secdef endpoint at runtime.
_IB_MONTHS = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)


def _ib_month_str(d: date) -> str:
    """Format a date as IBKR's 3-letter-month + 2-digit year, e.g. 'MAY26'."""
    return f"{_IB_MONTHS[d.month - 1]}{d.year % 100:02d}"

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
        self._ws_client = None  # lazy: created on first streaming use
        self._streaming = None  # lazy: created on first streaming use
        self._connected = False
        self._account_id: Optional[str] = config.account_id
        # Lock for serializing ibind calls across threads.
        # ibind is documented as not thread-safe per github.com/Voyz/ibind/issues.
        self._call_lock = threading.RLock()
        # conid cache for qualify_contract — cleared on disconnect.
        # Key: (symbol, expiry_iso, strike, right, trading_class, sec_type)
        self._conid_cache: dict[tuple, int] = {}
        # Whether /iserver/accounts has been primed this session — IBKR
        # requires it before /iserver/marketdata/snapshot and
        # /iserver/account/trades. Reset on disconnect.
        self._iserver_primed = False
        # Phase A.8 — retry + per-family circuit breakers. RetryPolicy
        # defaults: 6 attempts (initial + 5 retries), 1s base, 30s cap,
        # 0.5 jitter, retryable on HTTP 429/5xx + transient network errors.
        # Per-family breakers default to 5 consecutive failures OR ≥50%
        # rate over 20-request / 60s window, 30s half-open probe.
        # Exposed via the retry_policy / circuit_breakers properties so
        # tests + operators can introspect or force_reset.
        self._retry_policy = RetryPolicy()
        self._breakers: dict[str, CircuitBreaker] = {
            family: CircuitBreaker(name=f"ib.{family}")
            for family in ("oauth", "session", "portfolio", "market", "orders")
        }

    # ─── Connection lifecycle ──────────────────────────────────────────────

    def connect(self) -> bool:
        """3-stage connect: LST handshake → brokerage session → auth/status.

        Returns True on success; raises IBAuthError or IBConnectionError on
        failure. After return, client is ready to place orders and read data.

        Lifecycle methods (this connect() + disconnect()) are intentionally
        NOT wrapped by the Phase A.8 retry+breaker layer: they translate
        ibind exceptions into IBAuthError vs IBConnectionError with bespoke
        logic that doesn't compose cleanly with generic retry, and they
        only run once per session. Post-connect API calls DO route through
        retry+breaker via _ib_call(). Caller decides whether to retry
        connect() itself on failure.
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
        #
        # We pass compete=True (ibind's default) at init time, which means
        # the new session should *steal* ownership from any prior session.
        # Race: ssodh/init can return before IBKR has finished swapping
        # ownership, so the first auth/status read sometimes still shows
        # competing=true. Per research_scratch/12_ibind_errors_lifecycle.md
        # §5, retry once after a 5s pause before declaring failure.
        try:
            status_data = self._read_auth_status()
            if status_data.get("competing"):
                logger.warning(
                    "Auth status: competing=true post-init; sleeping 5s "
                    "then retrying once (race with ssodh/init handoff)"
                )
                time.sleep(5.0)
                status_data = self._read_auth_status()

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

    def _read_auth_status(self) -> dict:
        """One-shot auth/status read. Returns the `data` dict (empty if missing).

        NOT routed through _ib_call: this helper is deliberately tolerant
        of ibind responses that lack the standard Result shape (`.error`
        may be missing entirely on a half-initialized session during the
        ssodh/init handoff). _unwrap would raise on that; we want to
        return {} so connect()'s own competing-session retry loop can
        decide what to do. connect() already retries this race once after
        5s, and `authentication_status` is a low-cost session endpoint —
        the Phase A.8 retry+breaker layer would not meaningfully improve
        reliability here.
        """
        with self._call_lock:
            status_result = self._client.authentication_status()
        return getattr(status_result, "data", {}) or {}

    def check_auth_status(self) -> dict:
        """Authoritative live check — round-trips to IBKR's auth/status.

        Use when `is_connected()` (cheap, last-known) isn't strong enough,
        e.g. before placing a real-money order after a long idle period.
        Returns the raw status dict (`authenticated`, `connected`,
        `competing`, etc.). Does NOT auto-reconnect; caller decides.
        """
        self._require_connected()
        return self._read_auth_status()

    def ensure_connected(self) -> bool:
        """Verify the brokerage session is live; re-establish it if not.

        The IBKR brokerage session does NOT survive IBKR's ~01:00 ET
        daily server reset or the 24h live-session-token TTL. ibind's
        Tickler holds off the 6-minute *idle* timeout but cannot survive
        a server-side reset — so a process that has been up overnight
        will usually find its session dead the next morning.

        Call this as a once-per-trading-day gate before the first entry
        (and after any 401/410). It round-trips to auth/status:

        - healthy (``authenticated`` + ``connected``, not ``competing``)
          → returns True, touches nothing;
        - stale → runs a clean ``disconnect()`` + ``connect()`` to obtain
          a fresh live session token (which also restarts the Tickler
          and re-runs ssodh/init), and returns whether that succeeded.

        Returns False if the session is down and could not be
        re-established — the caller should then exit so systemd restarts
        the process with a fresh ``connect()`` (the most reliable reset).
        """
        try:
            status = self._read_auth_status() if self._connected else {}
            if (status.get("authenticated")
                    and status.get("connected")
                    and not status.get("competing")):
                return True
            logger.info(
                "ensure_connected: session stale (status=%r) — reconnecting",
                status,
            )
        except Exception as exc:
            logger.warning(
                "ensure_connected: auth status read failed (%s) — reconnecting",
                exc,
            )

        try:
            self.disconnect()
        except Exception as exc:
            logger.warning(
                "ensure_connected: disconnect during reconnect failed (%s)",
                exc,
            )
        try:
            return self.connect()
        except Exception as exc:
            logger.error("ensure_connected: reconnect failed (%s)", exc)
            return False

    def _discover_account_id(self) -> str:
        """Look up the IBKR account ID via portfolio_accounts.

        For most single-account retail users this returns one DU* (paper) or
        Uxxxx* (live) account. Multi-account setups need to pin via
        IBConfig.account_id.
        """
        data = self._ib_call(
            "portfolio", self._client.portfolio_accounts,
        ) or []
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

        Calls ibind's logout/shutdown if available. Stops the StreamingManager
        and WS client if they were spun up. Idempotent — safe to call
        even if connect() failed partway through.
        """
        # Track partial-teardown failures so operators (and monitoring) see
        # when a disconnect didn't fully clean up. Anything > 0 means the
        # session may still be live on IBKR's side or a thread is still
        # running locally.
        unclean = 0

        # Tear down streaming first (cleanly unsubscribes all conids)
        if self._streaming is not None:
            try:
                self._streaming.stop()
            except Exception as exc:
                unclean += 1
                logger.error("StreamingManager stop failed: %s", exc)
            self._streaming = None
        if self._ws_client is not None:
            try:
                if hasattr(self._ws_client, "shutdown"):
                    self._ws_client.shutdown()
            except Exception as exc:
                unclean += 1
                logger.error("WS client shutdown failed: %s", exc)
            self._ws_client = None

        if self._client:
            try:
                with self._call_lock:
                    # ibind 0.1.23: IbkrClient.close() runs oauth_shutdown()
                    # (which calls stop_tickler + logout) and then the parent
                    # RestClient.close(). No separate close_session() exists.
                    self._client.close()
            except Exception as exc:
                unclean += 1
                logger.error("IBClient session close failed: %s", exc)

        self._connected = False
        self._conid_cache.clear()
        self._iserver_primed = False
        if unclean:
            logger.error(
                "IBClient disconnect completed with %d unclean step(s); "
                "session may still be live on IBKR's side", unclean,
            )
        else:
            logger.info("IBClient disconnected cleanly")

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

    def _ib_call(self, family: str, fn, *args, _serialize: bool = True, **kwargs):
        """Run an ibind call through retry + per-family circuit breaker + unwrap.

        Every API call from public IBClient methods should route through here.
        `family` selects which CircuitBreaker tracks the call:

            'oauth'     — token refresh / handshake (rare; lifecycle is
                          NOT wrapped — see connect() docstring)
            'session'   — auth/status, tickle (high-frequency keep-alive)
            'portfolio' — accounts, summary, positions, ledger, FX
            'market'    — quotes, snapshots, chains, greeks, history,
                          contract search (secdef)
            'orders'    — place, cancel, modify, status, live_orders,
                          whatif

        Concurrency: by default each retry attempt re-acquires
        `self._call_lock` for the duration of the ibind call only;
        backoff sleeps happen OUTSIDE the lock so other threads can use
        the client between attempts. Tests can introspect / reset
        breakers via `circuit_breakers`.

        `_serialize` (keyword-only, default True): when True, the ibind
        call is wrapped in `self._call_lock` — ibind's IbkrClient is
        documented as not thread-safe so calls are serialized across
        threads. Set False ONLY for read-only endpoints that have been
        EMPIRICALLY verified safe for concurrent use. Currently the only
        such caller is `qualify_option_strikes`, whose concurrent
        `search_secdef_info_by_conid` calls were proven safe by
        scripts/probe_ibkr_chain.py PROBE 7 (6 concurrent calls, 6.0×
        concurrency, zero cross-thread data corruption). Do NOT set
        False for write paths or for endpoints that mutate ibind's
        internal state — the retry + breaker still apply, but the
        serialization guard is gone.

        Non-retryable exceptions (auth, validation, bad request) propagate
        immediately and do NOT record a breaker failure — the breaker is
        for "broker is degraded", not "caller did something wrong".
        """
        try:
            breaker = self._breakers[family]
        except KeyError as exc:
            raise IBClientError(
                f"unknown circuit-breaker family {family!r}; "
                f"expected one of {sorted(self._breakers)}"
            ) from exc

        def _invoke():
            if _serialize:
                with self._call_lock:
                    return fn(*args, **kwargs)
            # Concurrent path — no lock. Caller is responsible for having
            # verified the endpoint is concurrency-safe.
            return fn(*args, **kwargs)

        wrapped = retry_with_backoff(
            policy=self._retry_policy, breaker=breaker,
        )(_invoke)
        return self._unwrap(wrapped())

    @property
    def retry_policy(self) -> RetryPolicy:
        """The shared RetryPolicy applied to every _ib_call.

        Mutate in place to tune for a specific deployment (e.g. tests can
        set max_attempts=1 to disable retries; production may shorten
        backoff in latency-sensitive paths).
        """
        return self._retry_policy

    @property
    def circuit_breakers(self) -> dict[str, CircuitBreaker]:
        """Defensive copy of the per-family CircuitBreaker registry.

        Use `client.circuit_breakers['market'].force_reset()` to clear an
        OPEN breaker manually (operator action / test setup). The returned
        dict is a shallow copy but the CircuitBreaker instances are the
        live ones — mutating their state via record_*/force_reset affects
        future calls.
        """
        return dict(self._breakers)

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

            # Step 1: resolve underlying conid. Outer lock is RLock so
            # _ib_call's re-acquisition is free; backoff sleeps inside
            # _ib_call do hold the outer lock here (blocks duplicate
            # qualify_contract for the same key — desired) but only the
            # _call_lock, which serializes ibind anyway.
            underlying_sec_type = "IND" if sec_type == "OPT" else sec_type
            candidates = self._ib_call(
                "market", self._client.search_contract_by_symbol,
                symbol=symbol,
                sec_type=underlying_sec_type,
            ) or []
            if not isinstance(candidates, list):
                candidates = [candidates]
            if not candidates:
                raise IBClientError(f"No contract found for symbol={symbol}")
            # iserver/secdef/search is a fuzzy free-text lookup — for SPX it
            # returns the SPX conid plus several other "SPX-flavored" rows
            # (US/foreign variants, ETF wrappers, etc.). Two-pass filter:
            #
            #   1. STRICT: inspect each candidate's `sections` array
            #      (IBKR's authoritative secType/exchange mapping for a
            #      conid) — pick rows that explicitly publish the
            #      requested secType on CBOE.
            #   2. LOOSE (fallback): match top-level secType/exchange if
            #      present. Tolerant of partial responses + test mocks
            #      that only stub `conid`.
            #
            # 2026-05-16 paper-smoke observation: live IBKR responses lack
            # top-level `secType`/`exchange` on SPX, so the old loose
            # filter passed 5 candidates and we picked the first by luck.
            # The strict pass nails the right conid deterministically.
            def _strict_match(d: dict) -> bool:
                sections = d.get("sections") or []
                for s in sections:
                    if not isinstance(s, dict):
                        continue
                    if (s.get("secType") or "").upper() != underlying_sec_type.upper():
                        continue
                    if "CBOE" in (s.get("exchange") or "").upper():
                        return True
                return False

            def _loose_match(d: dict) -> bool:
                if not isinstance(d, dict):
                    return False
                sec = (d.get("secType") or "").upper()
                if sec and sec != underlying_sec_type.upper():
                    return False
                exch = (d.get("exchange") or "").upper()
                if exch and "CBOE" not in exch:
                    return False
                return True

            strict = [d for d in candidates if isinstance(d, dict) and _strict_match(d)]
            loose = [d for d in candidates if _loose_match(d)]
            chosen = strict or loose or candidates
            if len(chosen) > 1:
                # Still ambiguous — log but pick the first; better to surface
                # than fail. Production should pin a known conid.
                logger.warning(
                    "qualify_contract(%s, %s): %d underlying candidates after "
                    "filtering, picking first (%r). Pin a known conid via "
                    "IBConfig if this is the wrong contract.",
                    symbol, underlying_sec_type, len(chosen), chosen[0],
                )
            underlying_conid = (
                chosen[0].get("conid")
                or chosen[0].get("conidEx")
                if isinstance(chosen[0], dict) else None
            )
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
                month = _ib_month_str(expiry)  # e.g. 'MAY26'
                secdef_data = self._ib_call(
                    "market", self._client.search_secdef_info_by_conid,
                    conid=str(underlying_conid),
                    sec_type="OPT",
                    month=month,
                    exchange="CBOE",
                    strike=str(strike),
                    right=right.upper(),
                ) or []
                # Two-axis filter: trading_class (SPXW vs SPX) AND exact
                # expiry date. SPXW publishes Mon/Wed/Fri (and now daily)
                # weeklies within a single month — matching on month
                # alone (the IBKR query parameter) returns a list with
                # multiple expiries. We must pick the one matching the
                # caller-requested `expiry` exactly, else we can land on
                # an already-expired conid (cause of the 2026-05-16
                # paper-smoke `whatif → "Order is already expired"` 500).
                #
                # IBKR's secdef response shape for an option uses
                # `maturityDate` formatted as YYYYMMDD. Defensive: also
                # accept `expirationDate` / `expiry` since field naming
                # varies across IBKR endpoints + ibind versions.
                want_yyyymmdd = expiry.strftime("%Y%m%d")
                rows = secdef_data if isinstance(secdef_data, list) else [secdef_data]

                def _matches_expiry(d: dict) -> bool:
                    """True if d's expiry matches the requested date, OR if
                    no expiry field is present (back-compat with mocks +
                    partial responses).
                    """
                    for key in ("maturityDate", "expirationDate", "expiry"):
                        val = d.get(key)
                        if val:
                            return str(val) == want_yyyymmdd
                    return True  # field absent — preserve legacy behavior

                def _matches_trading_class(d: dict) -> bool:
                    tc = d.get("tradingClass", "")
                    return tc == trading_class or trading_class.upper() == tc.upper()

                matches = [
                    d for d in rows
                    if isinstance(d, dict)
                    and _matches_trading_class(d)
                    and _matches_expiry(d)
                ]
                if not matches:
                    # Surface the exact expiry mismatch reason so production
                    # debugging doesn't have to introspect raw responses.
                    available_expiries = sorted({
                        str(d.get("maturityDate")
                            or d.get("expirationDate")
                            or d.get("expiry") or "?")
                        for d in rows if isinstance(d, dict)
                    })
                    raise IBClientError(
                        f"No {trading_class} option matched: symbol={symbol} "
                        f"expiry={expiry} (want maturityDate={want_yyyymmdd}) "
                        f"strike={strike} right={right}. "
                        f"Available expiries in chain: {available_expiries}"
                    )
                # Defensive: response shape varies — prefer 'conid', fall
                # back to 'conidEx' (some endpoints return one or the other).
                first = matches[0]
                conid = first.get("conid") or first.get("conidEx")
                if not conid:
                    raise IBClientError(
                        f"Option secdef response missing conid/conidEx: {first!r}"
                    )
            else:
                conid = underlying_conid

            conid = int(conid)
            self._conid_cache[cache_key] = conid
            return conid

    def qualify_option_strikes(
        self,
        *,
        symbol: str,
        expiry: date,
        strikes: Iterable[float],
        trading_class: str = "SPXW",
        max_workers: int = 8,
    ) -> dict[tuple[float, str], int]:
        """Batch-resolve conids for many (strike, right) pairs at one expiry.

        F3 of the IB-only HYDRA rewrite. HYDRA's MKT-020/022 strike-
        tightening scan needs conids for a range of candidate strikes.
        IBKR has no full-chain endpoint (probe: `secdef_info` requires a
        strike) and rejects CSV strike lists, so resolution is
        per-strike. This method parallelizes that across a bounded
        thread pool.

        Args:
            symbol: underlying, e.g. "SPX"
            expiry: the exact option expiry to resolve for
            strikes: candidate strike prices (deduplicated internally)
            trading_class: "SPXW" for 0DTE PM-settled weeklies
            max_workers: thread-pool size. Default 8 — keeps the burst
                under IBKR's verified 10 req/s global rate limit with
                20% headroom (see docs/migration/F3_OPTION_CHAIN_DESIGN.md
                §8). Capped internally at len(strikes).

        Returns:
            dict mapping (strike, right) -> conid for every (strike,
            "C") and (strike, "P") that has a listed option at the exact
            `expiry`. Strikes with no option listed at that expiry are
            simply absent — callers detect "not tradable today" via
            absence rather than an exception.

        Concurrency: each per-strike `secdef_info` call runs through
        `_ib_call(..., _serialize=False)` — the lock-free concurrent
        path proven safe by scripts/probe_ibkr_chain.py PROBE 7. Retry
        + circuit breaker still apply per call. A single strike's
        failure is logged and that strike omitted; it does NOT abort
        the batch.

        Side effect: every resolved conid is written into the shared
        `_conid_cache` using the SAME key format as `qualify_contract`,
        so a later `qualify_contract(symbol, expiry, strike, right,
        trading_class)` for any resolved strike is a cache hit (zero
        API calls). This makes HYDRA's entries #2 and #3 near-instant
        after entry #1 warms the cache.

        Raises:
            IBClientError: not connected, or the underlying-symbol
                resolution failed (a hard prerequisite). Per-strike
                option-resolution failures do NOT raise.
        """
        self._require_connected()
        unique_strikes = sorted({float(s) for s in strikes})
        if not unique_strikes:
            return {}

        # Resolve the underlying conid ONCE (cached after first call).
        # This is a normal serialized _ib_call — happens before the
        # parallel section.
        underlying_conid = self.qualify_contract(symbol, sec_type="IND")
        month = _ib_month_str(expiry)
        want_yyyymmdd = expiry.strftime("%Y%m%d")
        expiry_iso = expiry.isoformat()

        def _cache_key(strike: float, right: str) -> tuple:
            # Mirror qualify_contract's key exactly so cross-method
            # cache hits work. sec_type is always "OPT" here.
            return (symbol, expiry_iso, strike, right, trading_class, "OPT")

        def _resolve_one(strike: float) -> dict[tuple[float, str], int]:
            """Resolve both rights for one strike. Returns the matched
            (strike, right) -> conid entries. Never raises — failures
            are logged and yield an empty dict for that strike."""
            try:
                # Cache short-circuit: if BOTH rights are already
                # cached, skip the API call entirely. (One right cached
                # isn't enough — the secdef call returns both anyway.)
                cached: dict[tuple[float, str], int] = {}
                with self._call_lock:
                    for right in ("C", "P"):
                        ck = _cache_key(strike, right)
                        if ck in self._conid_cache:
                            cached[(strike, right)] = self._conid_cache[ck]
                if len(cached) == 2:
                    return cached

                # One secdef_info call resolves BOTH rights for the
                # strike (probe 4). _serialize=False → concurrent path.
                secdef_data = self._ib_call(
                    "market", self._client.search_secdef_info_by_conid,
                    _serialize=False,
                    conid=str(underlying_conid), sec_type="OPT",
                    month=month, exchange="CBOE", strike=str(strike),
                ) or []
                rows = secdef_data if isinstance(secdef_data, list) else [secdef_data]

                resolved: dict[tuple[float, str], int] = {}
                for d in rows:
                    if not isinstance(d, dict):
                        continue
                    # Trading-class filter (SPXW vs SPX monthly)
                    tc = d.get("tradingClass", "")
                    if not (tc == trading_class
                            or trading_class.upper() == tc.upper()):
                        continue
                    # Exact-expiry filter — a strike can be listed across
                    # many May expiries (probe 7: strike 6750 had 7).
                    mat = str(
                        d.get("maturityDate")
                        or d.get("expirationDate")
                        or d.get("expiry")
                        or ""
                    )
                    if mat and mat != want_yyyymmdd:
                        continue
                    right = (d.get("right") or "").strip().upper()
                    if right not in ("C", "P"):
                        continue
                    raw_conid = d.get("conid") or d.get("conidEx")
                    if raw_conid:
                        resolved[(strike, right)] = int(raw_conid)
                return resolved
            except CircuitBreakerOpen:
                # Broker-down — must NOT be swallowed as "strike absent".
                # Propagate so the whole batch aborts and the caller can
                # tell a degraded broker from a genuinely empty chain.
                raise
            except Exception as exc:
                # A genuine per-strike failure (e.g. 400 "strike not
                # listed at this expiry") — omit that strike, keep the
                # batch. Debug, not warning: _read_option_chain pre-snaps
                # candidates to real strikes so this is normal noise.
                logger.debug(
                    "qualify_option_strikes: strike %s failed (%s: %s) — "
                    "omitted from result", strike,
                    type(exc).__name__, exc,
                )
                return {}

        # Parallel resolution. ThreadPoolExecutor.map preserves input
        # order, but order doesn't matter — we merge into one dict.
        workers = min(max_workers, len(unique_strikes))
        results: dict[tuple[float, str], int] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for partial in pool.map(_resolve_one, unique_strikes):
                results.update(partial)

        # Populate the shared conid cache so later qualify_contract
        # calls for these strikes are cache hits.
        with self._call_lock:
            for (strike, right), conid in results.items():
                self._conid_cache[_cache_key(strike, right)] = conid

        return results

    # ─── Quotes (read methods) ────────────────────────────────────────────

    def _ensure_iserver_primed(self) -> None:
        """Prime the /iserver brokerage session, once per session.

        IBKR requires `/iserver/accounts` to be called before
        `/iserver/marketdata/snapshot` (and `/iserver/account/trades`);
        without it the snapshot endpoint returns metadata-only rows
        (`{conid, conidEx, _updated}`) with no price fields, indefinitely.

        `portfolio_accounts()` (which `connect()` calls for account-id
        discovery) hits `/portfolio/accounts` — a DIFFERENT endpoint
        namespace — and does NOT satisfy this. `receive_brokerage_accounts()`
        is the `/iserver/accounts` call. Idempotent: primed once per
        session, reset on `disconnect()`.
        """
        if self._iserver_primed:
            return
        self._ib_call("session", self._client.receive_brokerage_accounts)
        self._iserver_primed = True

    def _snapshot_with_preflight(self, conids: str, fields: list[str]):
        """Call live_marketdata_snapshot with the required pre-flights.

        Two pre-flights are required:
        1. `/iserver/accounts` (via `_ensure_iserver_primed`) — without it
           the snapshot endpoint returns metadata-only forever.
        2. ibind's documented snapshot priming — the first
           `live_marketdata_snapshot` call for a conid primes IBKR's
           snapshot cache; the next call returns real data. ibind's
           `live_marketdata_snapshot_by_symbol` does the same.

        **Warmup polling (fix for 2026-05-18 Monday smoke failure):**
        IBKR's snapshot endpoint for a freshly-queried conid (first time
        this session) often returns ONLY metadata (`conid`, `conidEx`,
        `_updated`) with all price fields absent on the second call, even
        in active market hours. Subsequent calls populate. We poll the
        snapshot endpoint up to `_SNAPSHOT_MAX_WARMUP_POLLS` times after
        the preflight, sleeping `_SNAPSHOT_POLL_INTERVAL_S` between each,
        until any non-metadata field appears. Returns whatever the LAST
        call produced (may still be metadata-only if the conid genuinely
        has no entitlement / is stale — caller checks via _parse_quote_row).

        ibind's own `live_marketdata_snapshot_by_symbol` also calls the
        endpoint twice but doesn't warmup-poll; we needed this in
        production because HYDRA reads quotes on demand and a silent
        all-None response would break credit estimation + stop monitoring.
        """
        # Pre-flight 1: /iserver/accounts (required, once per session).
        self._ensure_iserver_primed()

        # Pre-flight 2: ibind's snapshot priming — first call arms the
        # conid's snapshot cache, it returns no data itself.
        self._ib_call(
            "market", self._client.live_marketdata_snapshot,
            conids=conids, fields=fields,
        )

        # Data call + warmup polling — return on first populated response
        data = None
        for attempt in range(_SNAPSHOT_MAX_WARMUP_POLLS):
            data = self._ib_call(
                "market", self._client.live_marketdata_snapshot,
                conids=conids, fields=fields,
            )
            if _snapshot_has_data(data):
                return data
            if attempt < _SNAPSHOT_MAX_WARMUP_POLLS - 1:
                time.sleep(_SNAPSHOT_POLL_INTERVAL_S)
        return data or []

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
        data = self._snapshot_with_preflight(str(conid), fields)
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
        # CP API caps fields at 50 per query (per IBKR's late-2024 Web API
        # changelog quoted in research_scratch/10_cpapi_streaming.md §4.3).
        if len(fields) > 50:
            raise IBClientError(
                f"get_quotes_batch: max 50 fields per call, got {len(fields)}"
            )
        data = self._snapshot_with_preflight(
            ",".join(str(c) for c in conids), fields,
        )
        rows = data if isinstance(data, list) else [data]
        # IBKR doesn't guarantee response order matches request order;
        # parse each row, key by conid for caller's convenience. Guard
        # the conid lookup — a non-dict row (e.g. a string error element)
        # must degrade through _parse_quote_row, not AttributeError here.
        return [
            self._parse_quote_row(r, r.get("conid") if isinstance(r, dict) else None)
            for r in rows
        ]

    def get_vix_price(self) -> Optional[float]:
        """Latest VIX index price. Tries mid (bid/ask average) → last → mark.

        Fix 2026-05-18 (Monday smoke diagnostic): VIX over IBKR's snapshot
        endpoint delivers ONLY `mark` (field 7635) for the cash index —
        no bid/ask (so no mid), no last (because VIX is a calculated index
        with no trade prints, not a tradable instrument). Without the
        mark-fallback, `get_vix_price()` returned None even with subs
        fully active, which would silently break HYDRA's VIX-regime
        decisions + stop monitoring on the IB cutover.

        SaxoClient.get_vix_price() equivalent — returns a single float.
        """
        conid = self.qualify_contract("VIX", sec_type="IND")
        q = self.get_quote(conid)
        if q.get("mid") is not None:
            return q["mid"]
        if q.get("last") is not None:
            return q["last"]
        return q.get("mark")

    def get_option_greeks(self, conid: int) -> dict:
        """Snapshot of delta/gamma/theta/vega/IV/OI for an option.

        Returns the same dict shape as get_quote, with greeks fields populated
        (delta, gamma, theta, vega, iv, open_interest).

        SaxoClient.get_option_greeks() equivalent.
        """
        self._require_connected()
        data = self._snapshot_with_preflight(str(conid), DEFAULT_OPTION_QUOTE_FIELDS)
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
        return self._ib_call(
            "portfolio", self._client.portfolio_account_information,
            account_id=self.account_id,
        ) or {}

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
        summary = self._ib_call(
            "portfolio", self._client.portfolio_summary,
            account_id=self.account_id,
        ) or {}
        ledger = self._ib_call(
            "portfolio", self._client.get_ledger,
            account_id=self.account_id,
        ) or {}

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
            raw_rate = row.get("exchangerate", 0)
            try:
                exchange_rate = float(raw_rate)
            except (TypeError, ValueError):
                exchange_rate = 0.0
            # NaN check: NaN != NaN, so this catches it cleanly
            if exchange_rate != exchange_rate or exchange_rate <= 0:
                raise IBClientError(
                    f"get_balance({currency!r}): ledger row missing or has "
                    f"invalid exchangerate (got {raw_rate!r}). Cannot compose "
                    f"{base_currency}-base to {currency}-tradable. Re-check "
                    "ledger response shape against IBKR docs (Phase A.10 "
                    "verifies direction on live paper account)."
                )
            cash_in_target = float(row.get("cashbalance", 0) or 0)
            # Empirical default: assume `exchangerate` is base-per-target
            # (i.e., 1 USD = ER EUR). USD_tradable = EUR_avail / ER + USD_cash.
            # Will be confirmed/inverted in Phase A.10 smoke test.
            tradable = base_avail / exchange_rate + cash_in_target

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
            data = self._ib_call(
                "portfolio", self._client.positions,
                account_id=self.account_id, page=page,
            )
            if not data:
                break
            batch = data if isinstance(data, list) else [data]
            all_positions.extend(batch)
            # ibind/IBKR's CP API positions endpoint returns up to 100 per
            # page (per ibind portfolio_mixin docstring). If we get fewer,
            # we've reached the last page.
            if len(batch) < 100:
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
        # FX rate quote — portfolio breaker (currency math is a portfolio
        # concern, not a market-data one — keeps the market-data breaker
        # focused on snapshot/chain volume).
        data = self._ib_call(
            "portfolio", self._client.currency_exchange_rate,
            source=source, target=target,
        )
        if isinstance(data, dict):
            rate = data.get("rate") or data.get(f"{source}_{target}")
            return float(rate) if rate else None
        if isinstance(data, (int, float, str)):
            try:
                return float(data)
            except (TypeError, ValueError):
                return None
        return None

    def get_closed_position_price(
        self,
        conid: int,
        *,
        buy_or_sell: str,
        days: int = 1,
    ) -> Optional[dict]:
        """Closing execution price for a position at ``conid``.

        Scans recent trade executions (`/iserver/account/trades`) and
        returns the MOST RECENT execution at ``conid`` matching the
        requested side.

        SaxoClient.get_closed_position_price() equivalent — returns the
        same ``{"closing_price": ...}`` shape the HYDRA call sites read.

        Args:
            conid: the instrument's IBKR conid.
            buy_or_sell: ``"Buy"`` or ``"Sell"`` — the direction of the
                CLOSING trade (closing a short is a Buy; closing a long
                is a Sell). Saxo-terminology kwarg; mapped to IBKR's
                ``"B"``/``"S"``.
            days: lookback window in days; IBKR caps this at 7.

        Returns:
            ``{"closing_price": float, "amount": int|None,
            "buy_or_sell": "Buy"|"Sell", "execution_time": str|None,
            "conid": int, "execution_id": str|None, "raw": dict}`` for
            the most recent matching execution, or None when there is no
            match / on a fetch failure.

        Field shape note: the per-record fields (`conid`, `side`,
        `price`, `size`, `trade_time`, `trade_time_r`) are taken from
        IBKR's documented `/iserver/account/trades` schema. The F5.1
        probe confirmed the endpoint + the `/iserver/accounts` priming
        requirement but the paper account had no execution history, so
        the exact field names are doc-sourced — hence the defensive
        multi-variant lookups below. Verify against a real execution
        once HYDRA places its first live order.
        """
        self._require_connected()

        want = (buy_or_sell or "").strip().upper()
        if want in ("BUY", "B"):
            ibkr_side = "B"
        elif want in ("SELL", "S"):
            ibkr_side = "S"
        else:
            raise IBClientError(
                f"get_closed_position_price: buy_or_sell must be "
                f"'Buy' or 'Sell', got {buy_or_sell!r}"
            )

        # /iserver/account/trades returns 500 'Please query /accounts
        # first' unless the brokerage session was primed via
        # /iserver/accounts (F5.1 probe finding). connect() only primes
        # /portfolio/accounts — a different endpoint — so prime here.
        self._ib_call("portfolio", self._client.receive_brokerage_accounts)

        days = max(1, min(int(days), 7))  # IBKR caps the lookback at 7
        data = self._ib_call(
            "portfolio", self._client.trades, days=str(days),
        ) or []
        rows = data if isinstance(data, list) else []

        target = str(conid)
        matches: list[dict] = []
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            rec_conid = rec.get("conid") or rec.get("conidEx")
            if str(rec_conid) != target:
                continue
            rec_side = str(rec.get("side") or "").strip().upper()
            norm = (
                "B" if rec_side in ("B", "BUY")
                else ("S" if rec_side in ("S", "SELL") else "")
            )
            if norm != ibkr_side:
                continue
            matches.append(rec)

        if not matches:
            return None

        # Most recent execution wins — trade_time_r is an epoch (ms).
        def _recency(r: dict) -> float:
            try:
                return float(r.get("trade_time_r"))
            except (TypeError, ValueError):
                return 0.0

        matches.sort(key=_recency, reverse=True)
        best = matches[0]

        try:
            closing_price = float(best.get("price"))
        except (TypeError, ValueError):
            return None
        if closing_price <= 0:
            return None

        try:
            amount: Optional[int] = int(float(best.get("size")))
        except (TypeError, ValueError):
            amount = None

        return {
            "closing_price": closing_price,
            "amount": amount,
            "buy_or_sell": "Buy" if ibkr_side == "B" else "Sell",
            "execution_time": best.get("trade_time"),
            "conid": conid,
            "execution_id": best.get("execution_id"),
            "raw": best,
        }

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
        month = _ib_month_str(expiry)
        data = self._ib_call(
            "market", self._client.search_strikes_by_conid,
            conid=str(underlying_conid),
            sec_type="OPT",
            month=month,
            exchange="CBOE",
        ) or {}
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

        Per ibind OrderMixin.live_orders docstring: *"set 'force=true' in
        a follow-up call to clear any cached behavior."* The first call
        primes IBKR (and per the doc returns a blank array as part of the
        cache-clear); the second call is the authoritative response.
        Critical for reconcile_orders — without it, the broker side can
        come back empty on a fresh session and cause state-only orders to
        be mis-classified as LOOKUP_FILL.
        """
        self._require_connected()
        # Cache-clear preflight + real read, each independently
        # retry+breaker wrapped (see _snapshot_with_preflight rationale).
        self._ib_call(
            "orders", self._client.live_orders,
            account_id=self.account_id, force=True,
        )
        data = self._ib_call(
            "orders", self._client.live_orders,
            account_id=self.account_id,
        ) or {}
        # ibind returns {"orders": [...]} wrapping
        if isinstance(data, dict):
            return data.get("orders") or []
        return data if isinstance(data, list) else []

    def get_order_status(self, order_id: str) -> dict:
        """Current status of a specific order_id.

        SaxoClient.get_order_status() equivalent.
        """
        self._require_connected()
        return self._ib_call(
            "orders", self._client.order_status,
            order_id=str(order_id),
        ) or {}

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
        # ibind's marketdata_history_by_symbol routes through
        # stock_conid_by_symbol(), which only resolves equities — index
        # symbols like 'SPX' / 'VIX' return no match. Resolve the conid
        # ourselves first (sec_type='IND') and call the by-conid variant.
        conid = self.qualify_contract(symbol, sec_type="IND")
        data = self._ib_call(
            "market", self._client.marketdata_history_by_conid,
            conid=str(conid),
            bar=bar,
            period=period,
            outside_rth=outside_rth,
        ) or {}
        if isinstance(data, dict):
            return data.get("data") or []
        return data if isinstance(data, list) else []

    # ─── Order placement (write methods) ──────────────────────────────────

    @staticmethod
    def _round_to_increment(price: float, increment: float) -> float:
        """Round price to the caller-specified tick increment.

        SPX options combo orders must use $0.05 net-credit increments on the
        CBOE Complex Order Book (Cboe U.S. Options Complex Book Process,
        Jan 2026). Non-conforming prices are rejected outright. Equities
        and many single-leg options use $0.01 — callers must pick the
        right increment per instrument (no safe default for mixed-asset
        order paths).
        """
        return round(price / increment) * increment

    @staticmethod
    def _ensure_coid(coid: Optional[str]) -> str:
        """Return the caller's client_order_id, or a fresh one if None.

        A client_order_id (cOID) is the only safe way to make order
        placement retryable — if the request times out before IBKR's ACK,
        a retry with the SAME cOID is deduplicated server-side; a retry
        WITHOUT a cOID would double-fill. Generate one if the caller
        didn't pass one so retry_with_backoff is safe by default.
        """
        return coid or f"CAL_{uuid.uuid4().hex[:12]}"

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
        coid = self._ensure_coid(coid)

        order = OrderRequest(
            conid=None,
            conidex=conidex,
            sec_type="BAG",
            side="SELL",           # SHORT IC = SELL the combo
            order_type="LMT",
            price=price,           # POSITIVE = credit received (IBKR convention)
            quantity=float(contracts),
            tif=tif,
            acct_id=self.account_id,
            coid=coid,
        )
        logger.info(
            "IC place: %s %s expiry=%s C:%.0f/%.0f P:%.0f/%.0f x%d net_credit=%.2f tif=%s coid=%s",
            symbol, trading_class, expiry,
            short_call_strike, long_call_strike,
            short_put_strike, long_put_strike,
            contracts, price, tif, coid,
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
        coid = self._ensure_coid(coid)

        order = OrderRequest(
            conid=None,
            conidex=conidex,
            sec_type="BAG",
            side=action,
            order_type="LMT",
            price=price,
            quantity=float(contracts),
            tif=tif,
            acct_id=self.account_id,
            coid=coid,
        )
        logger.info(
            "Vertical place: %s %s expiry=%s %s short:%.0f long:%.0f x%d price=%.2f side=%s coid=%s",
            symbol, trading_class, expiry, right,
            short_strike, long_strike, contracts, price, action, coid,
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
        price_increment: float = 0.05,
    ) -> dict:
        """Place a single-leg order.

        Used for single options or stock orders. For multi-leg combos use
        place_iron_condor / place_vertical_spread.

        Args:
            price_increment: tick size for price rounding. Defaults to 0.05
                (correct for SPX/SPXW single-leg option orders, which is
                HYDRA's universe today). For equities or sub-$1 stocks,
                pass 0.01. Pass 0 to skip rounding entirely (caller has
                already chosen a tick-conforming price).

        SaxoClient.place_order() equivalent.
        """
        self._require_connected()
        if order_type == "LMT" and price is None:
            raise IBClientError("LMT order requires price")
        if side not in ("BUY", "SELL"):
            raise IBClientError(f"side must be 'BUY' or 'SELL', got {side!r}")

        if price is None or price_increment <= 0:
            rounded_price = price
        else:
            rounded_price = self._round_to_increment(price, price_increment)
        coid = self._ensure_coid(coid)

        order = OrderRequest(
            conid=int(conid),
            side=side,
            quantity=float(quantity),
            order_type=order_type,
            price=rounded_price,
            tif=tif,
            acct_id=self.account_id,
            coid=coid,
        )
        logger.info(
            "Order place: conid=%d %s %d %s @ %s tif=%s coid=%s",
            conid, side, quantity, order_type,
            f"{rounded_price:.2f}" if rounded_price is not None else "MKT",
            tif, coid,
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

    def place_and_wait_for_fill(
        self,
        *,
        conid: int,
        side: str,
        quantity: int,
        order_type: str = "LMT",
        limit_price: Optional[float] = None,
        timeout_seconds: float = _DEFAULT_FILL_TIMEOUT_S,
        poll_interval_s: float = _DEFAULT_FILL_POLL_INTERVAL_S,
        tif: str = "DAY",
        coid: Optional[str] = None,
        answers: Optional[dict] = None,
        price_increment: float = 0.05,
    ) -> dict:
        """Place an order and poll until it reaches a terminal state or timeout.

        Building block for strategy-level retry loops. The strategy (e.g.
        HYDRA's progressive-chase entry placement, stop-loss escalation)
        owns the "what retry level to try next" decision; this method owns
        the place → poll-until-done mechanics inside one attempt.

        On timeout the order remains WORKING in IBKR's book — caller
        decides whether to cancel it, wait longer, or escalate (e.g.
        switch to a market order at a higher retry level).

        Args:
            conid: IBKR contract ID for the instrument
            side: "BUY" or "SELL"
            quantity: number of contracts (positive integer)
            order_type: "LMT" or "MKT"
            limit_price: required when order_type=="LMT"; ignored for MKT
            timeout_seconds: max wall-clock time to wait for terminal
                status (default 30s — calibrated to typical SPXW combo
                fill behavior on paper + live)
            poll_interval_s: seconds between status polls (default 0.5s
                — fast enough to catch fills promptly, slow enough to
                stay under IBKR's polling rate limits even when multiple
                orders are being placed)
            tif: time-in-force ("DAY" default; 0DTE trades don't survive
                past close anyway)
            coid: client_order_id for retry-safety; auto-generated if None
                (see _ensure_coid)
            answers: override DEFAULT_ORDER_ANSWERS reply-prompt map
            price_increment: tick size for limit price rounding (default
                0.05 — SPX/SPXW single-leg tick; pass 0.01 for equities)

        Returns:
            dict with keys:
              order_id: str
              status: "filled" | "cancelled" | "rejected" | "inactive" |
                      "expired" | "timed_out"
              filled_quantity: int (best-effort from status response)
              avg_fill_price: Optional[float]
              raw: dict (last status response, or place response if no
                         polling was needed)

        Raises:
            ValueError: invalid args (LMT without price, non-positive
                quantity, invalid side)
            IBClientError: place_order failed, response missing order_id,
                or status polling raised a non-retryable error (e.g.
                auth failure mid-polling)
        """
        # Validate args before any I/O so misuse fails fast.
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if order_type == "LMT" and limit_price is None:
            raise ValueError("LMT order_type requires limit_price")

        # Place the order. place_order handles _ensure_coid + tick
        # rounding + retry/breaker via _ib_call.
        place_resp = self.place_order(
            conid=conid,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=limit_price,
            tif=tif,
            coid=coid,
            answers=answers,
            price_increment=price_increment,
        )
        order_id = place_resp.get("order_id") or place_resp.get("id")
        if not order_id:
            raise IBClientError(
                f"place_order returned no order_id: {place_resp!r}"
            )

        # Sometimes the place response itself carries a terminal status
        # (instant fills on MKT orders; reject responses for invalid orders).
        # Short-circuit before polling to save an extra HTTP round-trip.
        initial_status = (place_resp.get("status") or "").lower()
        if initial_status in _TERMINAL_ORDER_STATUSES:
            return _build_fill_result_dict(
                order_id=str(order_id), raw=place_resp, status=initial_status,
            )

        # Poll for terminal state. Each poll is its own _ib_call with
        # retry+breaker; transient 429/5xx are absorbed automatically.
        deadline = time.monotonic() + timeout_seconds
        last_status_resp: dict = place_resp
        while time.monotonic() < deadline:
            try:
                status_resp = self.get_order_status(str(order_id))
            except IBClientError as exc:
                # IBKR's misuse of 503 for "not found" means the order was
                # purged after reaching a terminal state. Sunday's diagnostic
                # taught us this pattern. is_retryable doesn't retry these,
                # so the exception surfaces here — treat as cancelled-and-
                # purged from the caller's perspective (order is no longer
                # working). Other IBClientErrors propagate as real failures.
                msg = str(exc).lower()
                if any(p in msg for p in ("is not found", "no longer found")):
                    return _build_fill_result_dict(
                        order_id=str(order_id),
                        raw=last_status_resp,
                        status="cancelled",
                    )
                raise

            last_status_resp = status_resp or {}
            status = (
                last_status_resp.get("status")
                or last_status_resp.get("order_status")
                or ""
            ).lower()
            if status in _TERMINAL_ORDER_STATUSES:
                return _build_fill_result_dict(
                    order_id=str(order_id),
                    raw=last_status_resp,
                    status=status,
                )
            time.sleep(poll_interval_s)

        # Timed out — order still working. Caller decides next step.
        # Surface the last observed status response in `raw` for debugging.
        return _build_fill_result_dict(
            order_id=str(order_id),
            raw=last_status_resp,
            status="timed_out",
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a working order.

        Returns:
            True if (a) IBKR accepted the cancel request, OR (b) the order
                  is already in a terminal state (filled / cancelled /
                  purged from records) — both satisfy the caller's intent
                  "this order is no longer working".
            False on transient errors, breaker-open, or other unexpected
                  failures where the order MAY still be working — caller
                  must escalate (P0 trading-safety event).

        SaxoClient.cancel_order() equivalent.

        **Already-terminal handling (Fix 2026-05-18 paper smoke):**
        If the order was filled / cancelled / purged between when the
        caller decided to cancel and when our DELETE arrived, IBKR returns
        HTTP 400 (or 503) with `{"error":"...Order is filled or canceled"}`.
        The Sunday `is_retryable` fix already prevents wasteful retries on
        this pattern, but the exception still propagated past this method's
        catch (only caught IBClientError + CircuitBreakerOpen, not ibind's
        ExternalBrokerError). We now also catch the broader case + inspect
        the message: if it indicates "already terminal", return True
        because the caller's goal ("order no longer working") is achieved.
        Genuine 4xx/5xx without these markers still returns False.
        """
        self._require_connected()
        # Routed through _ib_call for retry+breaker on transient 429/5xx —
        # a transient cancel failure here would otherwise leave a working
        # order in the market (P0 trading-safety event).
        try:
            self._ib_call(
                "orders", self._client.cancel_order,
                order_id=str(order_id),
                account_id=self.account_id,
            )
            logger.info("Order cancel: order_id=%s", order_id)
            return True
        except CircuitBreakerOpen as exc:
            # Breaker tripped — order may still be working; caller must
            # escalate. Don't pretend success.
            logger.error("Order cancel failed for %s: breaker OPEN — %s", order_id, exc)
            return False
        except Exception as exc:
            # Inspect the exception message for already-terminal patterns.
            # Includes IBClientError, ibind's ExternalBrokerError, requests
            # HTTPError, and anything else that surfaces from the cancel
            # call below the retry layer.
            msg = str(exc).lower()
            for terminal_pattern in (
                "is filled or cancel",   # exact ibind/IBKR phrasing (covers ed and 'ed)
                "already filled",
                "already cancel",        # covers "cancelled" + "canceled"
                "is not found",          # purged after terminal state
                "no longer found",
            ):
                if terminal_pattern in msg:
                    logger.info(
                        "Order cancel: order_id=%s already in terminal state "
                        "(%s) — treating as cancel-success since the order "
                        "is no longer working", order_id, terminal_pattern,
                    )
                    return True
            # Genuine failure — order may still be working, escalate.
            logger.error("Order cancel failed for %s: %s", order_id, exc)
            return False

    def modify_order(
        self,
        order_id: str,
        *,
        side: str,
        quantity: int,
        price: Optional[float] = None,
        order_type: str = "LMT",
        conid: Optional[int] = None,
        answers: Optional[dict] = None,
    ) -> dict:
        """Modify a working order's price (and/or quantity).

        IBKR's modify_order replaces the previous order instance (not amend);
        order_id stays the same but other fields are re-submitted. Per ibind
        docs (OrderMixin.modify_order): *"The content should mirror the
        content of the original order."* So the caller must pass the
        original side + quantity (and optionally the conid for single-leg
        orders) — we do not invent placeholders.

        For combo modifications (BAG/conidex orders), pass the original
        `conid=None` and the IBKR `order_id` already identifies the combo.
        """
        self._require_connected()
        if not side:
            raise IBClientError("modify_order requires side ('BUY' or 'SELL')")
        if quantity is None or quantity <= 0:
            raise IBClientError("modify_order requires a positive quantity")

        rounded_price = (
            self._round_to_increment(price, 0.05) if price is not None else None
        )

        order = OrderRequest(
            conid=conid,
            side=str(side).upper(),
            quantity=float(quantity),
            order_type=order_type,
            price=rounded_price,
            acct_id=self.account_id,
        )
        data = self._ib_call(
            "orders", self._client.modify_order,
            order_id=str(order_id),
            order_request=order,
            answers=answers or DEFAULT_ORDER_ANSWERS,
            account_id=self.account_id,
        ) or {}
        logger.info(
            "Order modify: order_id=%s side=%s qty=%s price=%s",
            order_id, side, quantity, rounded_price,
        )
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
        return self._ib_call(
            "orders", self._client.whatif_order,
            order_request=order,
            account_id=self.account_id,
        ) or {}

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
        # Retry is safe here because _ensure_coid guarantees every order
        # carries a client_order_id (cOID) — IBKR dedupes server-side on
        # cOID, so a timed-out place that secretly succeeded won't double
        # fill on retry. See _ensure_coid docstring.
        data = self._ib_call(
            "orders", self._client.place_order,
            order_request=order,
            answers=a,
            account_id=self.account_id,
        ) or {}
        # ibind returns a list when the order resolves to multiple entries
        # (one per leg for combos, one per child for OCA brackets). We
        # promote the first entry to the top-level dict for caller
        # convenience and stash the remaining entries under "_legs" so
        # nothing is silently dropped — callers that need per-leg fill
        # tracking can read order["_legs"].
        if isinstance(data, list):
            if not data:
                data = {}
            elif len(data) == 1:
                data = data[0]
            else:
                head, *rest = data
                data = dict(head) if isinstance(head, dict) else {"_first": head}
                data["_legs"] = rest
        return data

    # ─── Streaming (Phase A.5 integration) ────────────────────────────────

    @property
    def streaming(self):
        """Lazy: create + start StreamingManager on first access.

        First access spins up an IbkrWsClient pointed at the live OAuth
        session, plus a StreamingManager around it. Subsequent reads return
        the cached instance.

        Returns None until connect() has been called.
        """
        if not self._connected:
            return None
        if self._streaming is not None:
            return self._streaming
        from ibind import IbkrWsClient
        from shared.ib_streaming import StreamingManager

        # Build the WS client tied to our IbkrClient's auth state.
        # use_oauth=True + access_token tells the WS to authenticate from
        # the same OAuth context. base_route default `/v1/api/ws` matches
        # the cloud endpoint (no local gateway).
        #
        # unwrap_market_data=False keeps the inner payload's numeric CP-API
        # field codes ("31", "84", "7308", …) instead of ibind's remapped
        # human names — StreamingManager._handle_tick filters on numeric
        # keys, and the rest of IBClient reads field codes directly.
        ws = IbkrWsClient(
            ibkr_client=self._client,
            account_id=self.account_id,
            use_oauth=True,
            access_token=self.cfg.credentials.access_token,
            unwrap_market_data=False,
            start=True,
        )
        self._ws_client = ws
        self._streaming = StreamingManager(ws)
        self._streaming.start()
        logger.info("IBClient: streaming subsystem started")
        return self._streaming

    # ─── Reconcile (Phase A.7 integration) ────────────────────────────────

    def reconcile_orders(
        self,
        state_orders: list,
        *,
        dry_run: bool = False,
        reattach_fn=None,
        lookup_fn=None,
    ) -> dict:
        """Cross-check broker open orders vs our state file; apply decisions.

        Per migration plan §4.4: IB orders are broker-side persistent. If
        bot crashed mid-order, the order is still live on IBKR's side when
        we reconnect. This function handles the three reconcile cases:

          • only_broker (orphan)  → CANCEL for safety
          • only_state (gone)     → LOOKUP_FILL via lookup_fn (if provided)
          • both                  → REATTACH via reattach_fn (if provided)

        Args:
            state_orders: list of dicts from caller's state file. Each must
                have at least 'order_id' (or 'orderId').
            dry_run: if True, log + return decisions but no side effects.
            reattach_fn: optional callable(order_id, broker_record) → None
                invoked for each REATTACH decision. If None, REATTACH items
                are logged only.
            lookup_fn: optional callable(order_id) → None invoked for each
                LOOKUP_FILL decision. If None, LOOKUP items are logged only.

        Returns:
            dict mapping action → list of order_ids:
                {"cancel": [...], "lookup": [...], "reattach": [...], "skip": [...]}
        """
        self._require_connected()
        from shared.ib_reconcile import classify_orders, apply_decisions

        broker_orders = self.get_open_orders()
        result = classify_orders(broker_orders, state_orders)
        logger.info("IBClient reconcile: %s", result.summary())

        return apply_decisions(
            result,
            cancel_fn=self.cancel_order,
            lookup_fn=lookup_fn,
            reattach_fn=reattach_fn,
            dry_run=dry_run,
        )
