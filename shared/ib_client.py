"""IB adapter for CALYPSO — the production IBKR broker for HYDRA.

Wraps Voyz/ibind 0.1.23 against IBKR's Client Portal Web API using OAuth 1.0a
(no IB Gateway, no IBC, no weekly phone tap — per the architecture pivot
documented in docs/migration/SAXO_TO_IB_MIGRATION_PLAN.md).

Status — F1-F7 + P1-P7 complete (see docs/migration/PROJECT_STATUS.md):
  ✓ Connection lifecycle: connect / disconnect / is_connected, Tickler,
    re-auth gate (morning + ~15-min intraday re-check, idempotent when healthy)
  ✓ 3-stage auth: LST → ssodh/init → auth/status
  ✓ Account discovery + pyCrypto fast-fail safety assertion
  ✓ Contract qualification with conid cache (qualify_contract)
  ✓ Read methods (quotes, positions, chains, greeks, orders, history, fx)
  ✓ Write methods (place_order, place_and_wait_for_fill, place_iron_condor)
    with cOID dedup safety
  ✓ WebSocket streaming with smd refresh (shared/ib_streaming.py)
  ✓ Order-state / position reconcile (shared/ib_reconcile.py)
  ✓ Retry + per-family circuit breakers (shared/ib_retry.py)

This is the IBKR adapter that owns the live session. In the deployed
multi-strategy setup (Option 1 — shared broker) it runs INSIDE the
calypso-broker process as ONE shared IBKR session for all of A/B/C;
HYDRA strategies reach it via shared.broker_client.BrokerClient over
loopback HTTP (self.broker is a BrokerClient whenever CALYPSO_BROKER_URL
is set — committed inline as http://127.0.0.1:8788 in all three hydra
units). IBClient is only `self.broker` directly in the legacy single-bot
mode (CALYPSO_BROKER_URL unset). The Saxo adapter is retired on this
branch — the broker-abstraction reparent landed in P1–P4.

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
   6509 = market data availability; FIRST char: R=RealTime, D=Delayed,
          Z=Frozen, Y=Frozen-Delayed, N=Not-Subscribed (+ secondary chars)
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime
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
# P7-audit H10: 12×0.5s = 6s warmup. A cold conid (and especially
# calculated greek fields) can take "a few moments" to populate after
# priming — 2s was too thin. 0.5s spacing also keeps the snapshot call
# rate (~2/s, even batched) well under IBKR's 10 req/s global limit.
_SNAPSHOT_MAX_WARMUP_POLLS = 12
_SNAPSHOT_POLL_INTERVAL_S = 0.5
# Keys IBKR returns in the snapshot row that are routing/availability
# metadata, NOT price/Greek data. If a snapshot row contains ONLY these
# keys, the cache isn't warm. IBKR populates these alongside the conid
# BEFORE any quote arrives, so counting them as "data" made warmup exit
# early and return None VIX/SPX (silently degrading VIX-regime + stop
# logic). "6509" is the availability flag (R/D/Z); "server_id"/"6119" are
# server-routing fields. Erring toward more keys here is safe — worst case
# warmup polls a little longer; the bug was exiting too soon.
_SNAPSHOT_METADATA_KEYS = frozenset(
    {"conid", "conidEx", "_updated", "server_id", "6119", "6509"}
)


def _snapshot_row_has_data(row) -> bool:
    """True if a single snapshot row has at least one non-metadata field.

    Any key beyond the metadata set ({conid, conidEx, _updated, ...}) means
    a price/Greek field is populated. The IBKR field-code keys are short
    numeric strings like "31", "84", "7308" — those count as data here.
    """
    if not isinstance(row, dict):
        return False
    for key in row.keys():
        if key not in _SNAPSHOT_METADATA_KEYS:
            return True
    return False


def _snapshot_has_data(payload) -> bool:
    """True if the snapshot payload has at least one row with at least one
    non-metadata field. Used to decide whether to continue warmup polling.
    """
    if not payload:
        return False
    rows = payload if isinstance(payload, list) else [payload]
    return any(_snapshot_row_has_data(row) for row in rows)


def _snapshot_ready_conids(payload) -> set:
    """Set of conids (as str) in `payload` whose row has non-metadata data.

    Used by batch warmup to decide whether ALL requested conids are warm,
    not just the first one (audit #19). A row with no recoverable conid
    key is ignored for the purpose of per-conid readiness.
    """
    ready: set = set()
    if not payload:
        return ready
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not _snapshot_row_has_data(row):
            continue
        conid = row.get("conid")
        if conid is None:
            conidex = row.get("conidEx")
            if conidex is not None:
                # conidEx is "<conid>;..." for combos — take the leading id.
                conid = str(conidex).split(";", 1)[0]
        if conid is not None:
            ready.add(str(conid))
    return ready


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
    requested_quantity: int = 0,
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
        # /iserver/account/order/status/{id} reports the cumulative fill
        # under snake_case `cum_fill` (alongside `average_price` /
        # `total_size`), NOT `filledQuantity` (which belongs to the
        # live_orders/sor envelope — see research_scratch/09_cpapi...md
        # :437-441). Mirror the snake_case `average_price` consumed just
        # below; without this a fully-filled order reports
        # filled_quantity=0 and drives a cancel+retry double-fill.
        or raw.get("cum_fill")
        or raw.get("filled")
        or 0
    )
    try:
        filled_quantity = int(float(filled_qty_raw))
    except (TypeError, ValueError):
        filled_quantity = 0

    # Field-name-independent safety net (audit #3 hardening). The exact fill-
    # quantity key on /iserver/account/order/status/{id} is NOT confirmed
    # against a captured payload (`cum_fill` is inferred from IBKR docs). If we
    # could not parse ANY quantity but the order reached the TERMINAL "filled"
    # state, that status is authoritative — the order is fully filled — so
    # report the full requested quantity rather than 0 (which would otherwise
    # drive a cancel+retry double-fill). Log the raw keys so the first real
    # fill reveals the actual field name and we can pin it down.
    if (
        filled_quantity == 0
        and requested_quantity
        and str(status).strip().lower() == "filled"
    ):
        logger.warning(
            "order %s reported status=filled but no parseable fill-quantity "
            "field — treating as fully filled (qty=%d) via authoritative "
            "status; raw keys=%s",
            order_id, requested_quantity,
            sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
        )
        filled_quantity = int(requested_quantity)

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


def _coerce_expiry(expiry):
    """Accept a ``datetime.date`` OR an ISO/compact date string.

    Audit #4: over the calypso-broker RPC wire a ``datetime.date`` argument is
    JSON-serialized to its isoformat string (e.g. ``"2026-05-29"``), so the
    broker process receives a ``str`` where these methods require a ``date``
    (``_ib_month_str``/``strftime``/``isoformat`` would all raise on a str).
    Coercing here makes get_option_chain/qualify_option_strikes/qualify_contract
    work identically on the direct AND the RPC path. Returns the value unchanged
    for a ``date`` (or ``None``); raises ValueError on an unparseable string
    (which fails CLOSED upstream — no trade).
    """
    if isinstance(expiry, str):
        s = expiry.strip()
        return date.fromisoformat(s) if "-" in s else datetime.strptime(s, "%Y%m%d").date()
    return expiry


class _RateGate:
    """Process-global request-rate gate — caps the START rate of IBKR calls to
    ``max_rps`` across ALL threads (the broker's single session serving A/B/C +
    health + smoke), keeping the combined rate under IBKR's ~10 req/s/session
    limit.

    Design: each caller atomically RESERVES the next time-slot under a short
    lock, then sleeps until that slot OUTSIDE the lock. Successive reservations
    are spaced ``1/max_rps`` apart, so starts are rate-capped while the lock is
    only ever held for a few microseconds — it cannot deadlock and the wait is
    bounded. At low rates (slot already in the past) the wait is zero, so it
    only throttles genuine bursts.
    """

    def __init__(self, max_rps: float) -> None:
        self._min_interval = 1.0 / max_rps
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = self._next_allowed if self._next_allowed > now else now
            self._next_allowed = slot + self._min_interval
            wait = slot - now
        if wait > 0:
            time.sleep(wait)

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

# IBKR-audit #6/#7: precautionary ORDER warnings to PRE-SUPPRESS at connect via
# /iserver/questions/suppress (ibind suppress_messages). These are benign
# "are-you-sure" prompts that, if they appear in the reply loop and are NOT in
# DEFAULT_ORDER_ANSWERS, raise an unmapped-prompt error and can leave a stop-out
# market order un-submitted. We suppress ONLY "proceed" warnings (market data /
# market-order risk / order-routing / price-cap) — the size-limit and
# close-position SAFETY prompts are deliberately NOT suppressed (we answer those
# False so they still block). IDs are TWS error codes with an 'o' prefix.
_SUPPRESSED_ORDER_MESSAGE_IDS = (
    "o354",    # market-data warning (we always intend to proceed on OPRA)
    "o451",    # order will be routed / price-cap acknowledgement
    "o10151",  # market-order risk ("no live market data") — the stop-out path
    "o10153",  # market-order / price-cap variant
)

# IBKR-audit #8: refresh the LST this many seconds BEFORE its 24h expiry, so the
# expiry boundary never lands mid-entry/stop as a surprise 401.
_LST_REFRESH_MARGIN_S = 15 * 60

# IBKR-audit #9: a 429 puts the IP in a ~10-minute penalty box (repeat offenders
# permanently blocked). Fail-fast for this long after a 429 rather than retrying.
_RATE_PENALTY_COOLDOWN_S = 10 * 60

# IBKR-audit #14: pin the underlying INDEX conids we actually trade. The
# free-text `search_contract_by_symbol` lookup returns several "SPX-flavored"
# rows and we pick the first survivor of the strict/loose filter; on a thin or
# reordered response that could silently land on the wrong instrument. These two
# index conids are stable IBKR identifiers, confirmed live against the paper
# session (2026-06-01): SPX=416904, VIX=13455763. Pinning them removes the
# ambiguity AND saves one API call per qualify. Keyed by (symbol, sec_type);
# only the INDEX underlyings are pinned — option strikes still walk the secdef
# chain (their conids rotate per expiry and cannot be hard-coded).
_PINNED_UNDERLYING_CONIDS = {
    ("SPX", "IND"): 416904,
    ("VIX", "IND"): 13455763,
}

# IBKR-audit #16: sentinel for place_order(price_increment=...) meaning "use the
# SPX/SPXW tiered single-leg tick derived from the price" ($0.05 <$3, $0.10 ≥$3),
# rather than a fixed grid. Default for the single-leg order path; equity callers
# still pass an explicit numeric increment (e.g. 0.01).
_SPX_TIERED_TICK = "spx_tiered"


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


class AmbiguousOrderError(IBClientError):
    """A place_order POST may have landed but it could NOT be confirmed.

    Raised by ``_submit_order`` when a *retryable* failure occurred during/
    after the order POST AND the subsequent cOID lookup was inconclusive — so
    the order might be working at IBKR or might not be. The caller MUST NOT
    resubmit (a fresh order under a new cOID could double-fill); it must ABORT
    the placement and let reconciliation/alerting handle the unknown order.
    Audit: place_order retry double-execution.
    """


class RatePenaltyError(IBClientError):
    """A NON-risk-critical IBKR call was refused because the rate-limit penalty
    box is active (a 429 was recently seen; IBKR boxes the IP for ~10 min and
    permanently blocks repeat offenders).

    A distinct subclass (not a bare IBClientError) so it survives the broker
    RPC boundary as ``type="RatePenaltyError"`` and the strategy can tell "the
    session is rate-penalty-degraded" apart from "this strike just isn't
    listed" / a transient empty tick — which matters when the bot holds live
    positions and must alert that risk reads may be degraded. Risk-critical
    calls (stop-loss reads, order status, stop-close placement) are NOT refused
    with this — they re-route through the slow penalty gate instead.
    """


def _looks_like_410_gone(exc: Exception) -> bool:
    """True if `exc` represents an IBKR HTTP 410 Gone.

    IBKR-audit #18: a 410 means the brokerage session has been REVOKED (its
    LST/ssodh state is gone — e.g. competed away, expired, or torn down by the
    ~01:00 ET reset). Unlike a 401 (which can mean a wrong/pre-activation
    consumer key — a human-fix-it condition), a 410 is purely a session-state
    loss that a fresh reconnect re-establishes. It must therefore be classified
    RETRYABLE (IBConnectionError), never as the fatal IBAuthError.

    Detected by the structured `status_code` attribute first (ibind sets it on
    its HTTP errors), falling back to a word-boundary match on the message so a
    "4101" / order-id "...410" substring can't false-positive.
    """
    if getattr(exc, "status_code", None) == 410:
        return True
    return bool(re.search(r"\b410\b", str(exc)))


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
      tickle_interval_seconds: INTENDED Tickler cadence to keep the
                  brokerage session warm (default 60s; IBKR idle timeout
                  is ~6 minutes). NOT YET wired (audit #43): connect()
                  constructs IbkrClient(use_oauth=True, oauth_config=...)
                  and ibind starts its own Tickler from maintain_oauth=True
                  at the fixed `var.IBIND_TICKLER_INTERVAL` (60s) default —
                  this field is not passed through, so setting it has no
                  effect on the real cadence. It only matches behavior at
                  the 60s default. Wiring it (set var.IBIND_TICKLER_INTERVAL
                  before construction, or start_tickler(interval=...)) is a
                  tracked follow-up; until then treat the cadence as fixed
                  at ibind's 60s default.
      connection_timeout_seconds: advisory connect-handshake budget (default
                  30s). NOT YET enforced as a hard cap in connect() (audit M1):
                  a hung handshake is bounded instead by ibind's own
                  per-request socket timeouts, and a crash-loop on connect is
                  bounded by systemd StartLimitIntervalSec/Burst
                  (deploy/hydra.service). Wiring a true watchdog cap that
                  raises IBConnectionError on expiry is a tracked follow-up;
                  until then do not rely on this as a guaranteed ceiling.
      debug_log_payloads: if True, allow the diagnostic log sites that
                  would otherwise dump a full IBKR response (the snapshot
                  warmup-exhausted WARNING and the place_order
                  no-order-id WARNING) to include the raw payload. When
                  False (the default), those sites log only a
                  non-sensitive summary (conids / entry count / keys).
                  NEVER enable in production (raw responses may contain
                  account values, order IDs, etc.)
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
        # Symbols whose /iserver/secdef/search has been issued this session.
        # IBKR requires that search before /secdef/strikes + /secdef/info will
        # resolve a symbol's OPTION contracts (else they 500 "No Contracts
        # retrieved"). The conid PIN (#14) skips the fuzzy search, so we re-issue
        # it ONCE per (symbol, sec_type) purely to prime IBKR's session cache.
        # Cleared on disconnect IN LOCKSTEP with _conid_cache so a cached conid
        # never outlives its priming.
        self._secdef_search_primed: set[tuple] = set()
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
        # Global request-rate gate (opt-in via CALYPSO_IBKR_MAX_RPS). IBKR's
        # Client Portal Web API caps each authenticated SESSION at ~10 req/s
        # (verified: IBKR Campus docs; 429 on exceed). The calypso-broker holds
        # the ONE session for A/B/C+health+smoke, so their COMBINED rate must
        # stay under 10/s — otherwise a sustained overlapping-entry-window burst
        # could 429 repeatedly and (after 5 consecutive failures) trip the
        # orders/market breaker. This caps the combined start-rate at the single
        # chokepoint. Default OFF (0.0) so tests/dev/direct-IBClient are
        # unaffected; the broker unit sets CALYPSO_IBKR_MAX_RPS=8 (20% headroom).
        try:
            _rps = float(os.environ.get("CALYPSO_IBKR_MAX_RPS", "0") or "0")
        except ValueError:
            _rps = 0.0
        self._rate_gate = _RateGate(_rps) if _rps > 0 else None
        # IBKR-audit (429-burst): cap the TOTAL concurrent option-chain secdef
        # fan-out across ALL bots sharing this ONE session. qualify_option_strikes
        # uses a per-call thread pool (max_workers up to 8); three bots resolving
        # chains at the SAME entry window would otherwise put ~16-24 secdef calls
        # in flight at once and saturate the rate gate's queue — which is what
        # produced the 10:45 entry-window 429. This process-global bounded
        # semaphore is the single cross-bot concurrency cap (default 4, env
        # CALYPSO_IBKR_CHAIN_CONCURRENCY). It wraps ONLY the per-strike secdef
        # call, so warm-cache strikes never hold a permit.
        try:
            _chain_cc = int(os.environ.get("CALYPSO_IBKR_CHAIN_CONCURRENCY", "4") or "4")
        except ValueError:
            _chain_cc = 4
        self._chain_resolve_sem = threading.BoundedSemaphore(max(1, _chain_cc))
        # IBKR-audit #8: LST expiry (epoch ms) captured at connect; used by
        # ensure_connected to refresh proactively before the 24h TTL.
        self._lst_expires_ms: Optional[int] = None
        # IBKR-audit #9: when a 429 is seen, IBKR penalty-boxes the IP for ~10
        # min (repeat offenders permanently blocked). We set this to a monotonic
        # deadline and fail-fast (no further IBKR calls) until it passes.
        self._rate_penalty_until: float = 0.0
        # IBKR-audit #9 (live-safety): while the penalty box is active, RISK-
        # CRITICAL calls (stop-loss quote reads, order status, open-orders/coid
        # lookup, position reads, stop-CLOSE placement) are NOT refused — they
        # re-route through this dedicated slow gate so stop-loss management of
        # held 0DTE positions never goes dark for the full 10 min, yet stays far
        # under IBKR's ~10/s so it cannot itself re-trigger a 429. ~3 rps (≈0.33s
        # spacing) covers the 0.5s order-status poll loop without stretching it.
        # Only NEW-entry / bulk-chain work is hard-refused during the box.
        self._penalty_gate = _RateGate(3.0)
        # Polish Item 1: monotonically-increasing counter incremented in
        # _snapshot_with_preflight whenever the warmup-poll loop exits
        # without populated data (i.e., the snapshot stays metadata-only
        # for the full warmup budget). Exposed via the
        # `snapshot_warmup_exhausted_count` read-only property. main.py
        # polls this between iterations to fire a DATA_QUALITY Telegram
        # alert on the first occurrence of the day (one alert per day
        # per process; reset on day boundary in main.py, not here).
        # NEVER reset within a session — the day-rollover dedup logic
        # lives in main.py to keep IBClient broker-agnostic.
        self._snapshot_warmup_exhausted_count = 0
        # FAIL-CLOSED expiry guard (audit #20). The maturityDate filter in
        # qualify_contract / qualify_option_strikes is the SOLE thing
        # preventing landing on a wrong-dated (e.g. already-expired or
        # later-weekly) option conid — a month=MAY26 secdef query returns
        # ALL May expiries mixed together. A secdef row that lacks a
        # parseable expiry field must therefore be REJECTED, not accepted
        # for today's 0DTE date. This test-only escape hatch lets unit
        # mocks that omit maturityDate keep resolving; it is NEVER set in
        # production code paths. Default False = fail-closed.
        self._allow_missing_expiry = False

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

        # AUD2-H1: do NOT log the consumer_key value. The Polish #10
        # IBKRCredentials `field(repr=False)` hardening means
        # repr(creds) doesn't leak the secrets — but this %s-format
        # line was bypassing that. Logging just the length is enough
        # for the operator to confirm "the right credential file got
        # loaded" without putting the key into journalctl / Cloud
        # Logging / operator screenshots.
        logger.info(
            "IBClient connecting — environment=%s consumer_key_length=%d",
            self.cfg.credentials.environment,
            len(self.cfg.credentials.consumer_key or ""),
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
            # P7-audit L7: `"401" in err_str` was too loose — would match
            # "error 4017" or a URL containing the substring. Use a word-
            # boundary regex for the HTTP code; keep the longer keyword
            # phrases as substring matches since collision is implausible.
            # IBKR-audit #18: a 410 Gone is a revoked-session condition, NOT a
            # credential problem — keep it retryable (IBConnectionError) and
            # never let it fall into the auth branch below.
            if _looks_like_410_gone(exc):
                raise IBConnectionError(
                    f"Session gone (410) at LST stage — reconnect will re-init: {exc}"
                ) from exc
            looks_like_auth = (
                bool(re.search(r"\b401\b", err_str))
                or any(k in err_str for k in (
                    "unauthorized",
                    "invalid consumer",
                    "invalid_token",
                    "invalid token",
                ))
            )
            if looks_like_auth:
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
            # IBKR-audit #18: a 410 Gone during the status read is a revoked
            # session (self-healing on reconnect), not a credential failure —
            # keep it retryable instead of wrapping it as the fatal IBAuthError.
            if _looks_like_410_gone(exc):
                raise IBConnectionError(
                    f"Session gone (410) during auth/status — reconnect will "
                    f"re-init: {exc}"
                ) from exc
            raise IBAuthError(
                f"auth/status check errored after LST success: {exc}"
            ) from exc

        # Resolve account ID if not pinned
        if not self._account_id:
            self._account_id = self._discover_account_id()

        # Audit #12: publish _connected under the lock so a concurrent
        # _require_connected/_ib_call sees a consistent (client-built +
        # connected) state, never the True flag ahead of a usable client.
        with self._call_lock:
            self._connected = True
        # IBKR-audit #8: capture the LST expiry so ensure_connected can refresh
        # PROACTIVELY (before the 24h TTL / nightly reset lands inside an entry
        # or stop window), instead of only reacting to a 401 up to 15 min late.
        try:
            self._lst_expires_ms = getattr(
                self._client, "live_session_token_expires_ms", None
            )
        except Exception:
            self._lst_expires_ms = None
        # IBKR-audit #6/#7: pre-suppress the benign precautionary order warnings
        # so the stop/entry reply loop can't hit an UNMAPPED prompt and fail to
        # submit (a breached short must always be able to close). Best-effort —
        # a suppression failure must NOT block connect.
        try:
            self._client.suppress_messages(list(_SUPPRESSED_ORDER_MESSAGE_IDS))
            logger.info("suppressed benign order warnings: %s",
                        list(_SUPPRESSED_ORDER_MESSAGE_IDS))
        except Exception as e:  # noqa: BLE001
            logger.warning("suppress_messages failed (non-fatal): %s: %s",
                           type(e).__name__, e)
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

        Call this as a session gate before the first entry of the day and
        as a periodic intraday re-check (HYDRA and calypso-broker both call
        it every ~15 min), plus after any 401/410. It is cheap/idempotent
        when the session is healthy and round-trips to auth/status:

        - healthy (``authenticated`` + ``connected``, not ``competing``)
          → returns True, touches nothing;
        - stale → runs a clean ``disconnect()`` + ``connect()`` to obtain
          a fresh live session token (which also restarts the Tickler
          and re-runs ssodh/init), and returns whether that succeeded.

        Returns False if the session is down and could not be
        re-established — the caller should then exit so systemd restarts
        the process with a fresh ``connect()`` (the most reliable reset).

        Concurrency (audit #11/#12): the status-read + ``disconnect()`` +
        ``connect()`` are performed as ONE atomic transaction under
        ``self._call_lock``. Uvicorn dispatches each /rpc on a threadpool
        worker, so other threads can be inside ``_ib_call`` while the
        broker-maintenance thread reconnects. Holding ``_call_lock`` for
        the whole transaction means an in-flight RPC either completes
        before the swap or blocks until the new ``self._client`` is in
        place — it can never observe a half-torn-down session (``_client``
        set but ``_connected`` False, or a closed client). ``_call_lock``
        is an ``RLock``, so the inner re-acquisitions in
        ``_read_auth_status`` / ``disconnect`` / ``connect`` / ``_ib_call``
        on THIS thread are free and cannot deadlock; cross-thread callers
        serialize behind it as intended.
        """
        with self._call_lock:
            try:
                status = self._read_auth_status() if self._connected else {}
                if (status.get("authenticated")
                        and status.get("connected")
                        and not status.get("competing")):
                    # IBKR-audit #8: even when the session reads healthy, refresh
                    # PROACTIVELY if the LST is within the margin of its 24h
                    # expiry — so the boundary never lands mid-entry/stop as a
                    # surprise 401 caught up to 15 min late.
                    exp_ms = self._lst_expires_ms
                    if (isinstance(exp_ms, (int, float))
                            and (exp_ms / 1000.0) - time.time() < _LST_REFRESH_MARGIN_S):
                        logger.info(
                            "ensure_connected: LST within %ds of expiry "
                            "(expires_ms=%s) — proactively reconnecting",
                            int(_LST_REFRESH_MARGIN_S), exp_ms,
                        )
                        # fall through to disconnect()+connect() below
                    else:
                        return True
                else:
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
        # P7-audit M13: surface a missing accountId as a typed
        # IBAuthError, not a raw KeyError — operators reading the log
        # need a clear "broker returned a malformed account row" signal,
        # not a generic Python traceback. (Should never happen in
        # practice: IBKR's portfolio_accounts always returns rows shaped
        # `{accountId, accountVan, accountTitle, ...}`. Defense in depth.)
        try:
            return data[0]["accountId"]
        except KeyError as exc:
            raise IBAuthError(
                f"IBKR portfolio_accounts returned a row without 'accountId': "
                f"{data[0]!r}"
            ) from exc

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

        # Audit #11/#12: close the ibind client AND flip the session-state
        # flags as a single locked unit, so a concurrent _ib_call/
        # _require_connected never observes a torn state (e.g. _connected
        # still True against an already-closed _client). _call_lock is an
        # RLock so this composes with ensure_connected holding the same
        # lock across the whole disconnect→connect transaction.
        with self._call_lock:
            if self._client:
                try:
                    # ibind 0.1.23: IbkrClient.close() runs oauth_shutdown()
                    # (which calls stop_tickler + logout) and then the parent
                    # RestClient.close(). No separate close_session() exists.
                    self._client.close()
                except Exception as exc:
                    unclean += 1
                    logger.error("IBClient session close failed: %s", exc)

            self._connected = False
            self._conid_cache.clear()
            self._secdef_search_primed.clear()  # lockstep with _conid_cache
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
        # Audit #12: read _connected + _client as one snapshot under
        # _call_lock so this can't observe a torn state mid-reconnect
        # (e.g. _connected flipped but _client not yet rebuilt). _call_lock
        # is an RLock, so a caller already holding it (e.g. inside a public
        # method that wraps a locked region) re-acquires for free.
        with self._call_lock:
            connected = self._connected
            client = self._client
        if not connected or client is None:
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

    def _ib_call(
        self, family: str, fn, *args,
        _serialize: bool = True,
        _policy: Optional["RetryPolicy"] = None,
        _risk_critical: bool = False,
        **kwargs,
    ):
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
            # Global rate gate (opt-in): cap the combined IBKR start-rate under
            # the per-session 10 req/s limit. Applied on BOTH paths so the
            # concurrent qualify_option_strikes burst is paced too. The wait
            # happens before acquiring _call_lock so it never holds the
            # serialization lock while throttling.
            #
            # While the penalty box is active, risk-critical calls (which the
            # guard below let THROUGH instead of refusing) use the dedicated
            # SLOW penalty gate (~3 rps) instead of the normal gate — keeping
            # stop-loss reads/closes alive but far enough under IBKR's limit
            # that they can't re-trigger a 429. Gate selection lives HERE (not
            # in the guard) because this is where the gate actually runs.
            boxed = bool(self._rate_penalty_until
                         and time.monotonic() < self._rate_penalty_until)
            if _risk_critical and boxed:
                self._penalty_gate.acquire()
            elif self._rate_gate is not None:
                self._rate_gate.acquire()
            if _serialize:
                with self._call_lock:
                    return fn(*args, **kwargs)
            # Concurrent path — no lock. Caller is responsible for having
            # verified the endpoint is concurrency-safe.
            return fn(*args, **kwargs)

        # `_policy` (keyword-only, default None): per-call RetryPolicy
        # override. Used by the order-WRITE path (_submit_order) to run
        # with max_attempts=1 so a transient failure does NOT blindly
        # re-POST the order — see _submit_order (audit: place_order retry
        # double-execution). Defaults to the shared self._retry_policy.
        # IBKR-audit #9: a 429 puts the IP in a ~10-min penalty box (repeat
        # offenders permanently blocked). If we're in it, fail fast — issuing
        # MORE requests is futile (still boxed) and escalates toward a permanent
        # ban. The rate gate should keep us out of the box entirely; this is the
        # don't-make-a-slip-catastrophic backstop.
        #
        # EXEMPTION (live-safety): risk-critical calls are NOT refused. Blinding
        # stop-loss DETECTION (quote reads) and ACTION (order status + stop-close
        # placement) for the full 10 min on held 0DTE positions is WORSE than the
        # box itself — an undefended breached short can run to max loss in well
        # under 10 min. Exempt calls fall through and _invoke routes them through
        # the slow ~3 rps penalty gate so they stay alive without re-triggering a
        # 429. Only NEW-entry / bulk-chain work (the burst source) is refused.
        if (not _risk_critical
                and self._rate_penalty_until
                and time.monotonic() < self._rate_penalty_until):
            raise RatePenaltyError(
                f"IBKR rate-limit penalty box active "
                f"(~{int(self._rate_penalty_until - time.monotonic())}s left) — "
                f"refusing non-risk-critical {family} call to avoid escalation "
                f"toward a permanent block"
            )
        wrapped = retry_with_backoff(
            policy=_policy or self._retry_policy, breaker=breaker,
        )(_invoke)
        try:
            return self._unwrap(wrapped())
        except Exception as exc:
            # 429 is NON-retryable (see ib_retry.is_retryable) so it surfaces
            # here on the first hit. Enter the penalty box + alert loudly.
            if getattr(exc, "status_code", None) == 429:
                self._rate_penalty_until = time.monotonic() + _RATE_PENALTY_COOLDOWN_S
                logger.critical(
                    "IBKR 429 Too Many Requests on %s — entering %ds rate-limit "
                    "penalty box; all IBKR calls fail fast until it clears (a 429 "
                    "means the global rate gate was exceeded — investigate).",
                    family, int(_RATE_PENALTY_COOLDOWN_S),
                )
            raise

    def clear_rate_penalty(self) -> float:
        """Operator override: clear the rate-limit penalty box immediately.

        The box otherwise only clears by waiting out the full monotonic
        ``_RATE_PENALTY_COOLDOWN_S`` (~10 min). Exposed via the broker RPC so
        an operator who has confirmed IBKR lifted the IP penalty (or who knows
        the 429 was a one-off, e.g. caused by manual probing) can resume NEW
        entries without a broker restart. Returns the number of seconds that
        remained on the box (0.0 if it was not active). Use with care — only
        clear it when you're confident the rate pressure is gone, or you risk
        re-tripping IBKR toward a permanent block.
        """
        remaining = max(0.0, self._rate_penalty_until - time.monotonic())
        self._rate_penalty_until = 0.0
        if remaining > 0:
            logger.warning(
                "clear_rate_penalty: penalty box cleared by operator with "
                "~%ds remaining", int(remaining),
            )
        return remaining

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

    @property
    def snapshot_warmup_exhausted_count(self) -> int:
        """Polish Item 1: number of times ``_snapshot_with_preflight`` exited
        its warmup-poll loop without populated data, since this IBClient
        instance was constructed.

        Each "exhaustion" means one snapshot endpoint call where the
        ~6-second warmup budget elapsed and the response was still
        metadata-only or empty. That's almost always one of: no
        real-time entitlement for the conid, a stale conid (e.g.
        expired option), or IBKR snapshot service degradation.

        ``main.py`` polls this between iterations to fire:

        - ONE MEDIUM ``DATA_QUALITY`` Telegram on the first
          exhaustion of the trading day (so the operator notices
          early degradation without flooding on a single illiquid
          chain read).
        - ONE additional HIGH ``DATA_QUALITY`` alert once the
          per-day count crosses 25 (severe data-flow degradation
          warranting investigation).

        The day-boundary reset of the "alerted today" flag lives in
        main.py, not here, so IBClient stays broker-agnostic. The
        counter itself is monotonic for the life of the process —
        a session reconnect does NOT reset it.
        """
        return self._snapshot_warmup_exhausted_count

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
        expiry = _coerce_expiry(expiry)  # accept ISO string over the RPC wire (#4)
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

            # IBKR-audit #14: short-circuit to the pinned conid for the index
            # underlyings we trade — deterministic, and skips the fuzzy search
            # (and its API call) entirely. Falls through to the search below for
            # any non-pinned symbol/sec_type.
            pinned = _PINNED_UNDERLYING_CONIDS.get((symbol, underlying_sec_type))
            if pinned is not None:
                # #14-regression fix (2026-06-01): the pin skips the fuzzy
                # search, but /iserver/secdef/search ALSO primes IBKR's session
                # contract cache — without it /secdef/strikes + /secdef/info 500
                # with "No Contracts retrieved" and option-chain resolution
                # breaks. Re-issue the search ONCE per session for its priming
                # side-effect; we still return the deterministic pinned conid.
                # Runs on BOTH the IND-quote and OPT paths because both option
                # readers (get_option_chain / qualify_option_strikes) obtain the
                # underlying via qualify_contract(symbol, sec_type="IND"), and
                # this is the first call of the session before any secdef query.
                self._ensure_secdef_search_primed(symbol, underlying_sec_type)
                if sec_type != "OPT":
                    # Underlying quote: the pinned conid IS the answer.
                    conid = int(pinned)
                    self._conid_cache[cache_key] = conid
                    return conid
                # Option: feed the pinned underlying through as the sole search
                # candidate so the filter/extract logic below resolves
                # underlying_conid = pinned with no fuzzy lookup, then Step 2
                # still walks the secdef chain to the exact strike.
                candidates = [{
                    "conid": pinned,
                    "sections": [{"secType": underlying_sec_type, "exchange": "CBOE"}],
                }]
            else:
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
            # P7-audit L6: only accept the canonical numeric `conid`.
            # The prior `chosen[0].get("conid") or chosen[0].get("conidEx")`
            # had two problems: (a) the `if isinstance(...)` ternary was
            # mis-bound with `or`, so `chosen[0].get(...)` was called
            # unconditionally — would raise AttributeError if `chosen[0]`
            # wasn't a dict; (b) `conidEx` is the *compound* identifier
            # form (e.g. "12345;0;;" for combos) — callers downstream do
            # `int(underlying_conid)` or pass it to single-leg APIs, both
            # of which would fail on a compound form. Fall back to nothing
            # — if conid is missing, fail loudly.
            chosen0 = chosen[0] if isinstance(chosen[0], dict) else {}
            underlying_conid = chosen0.get("conid")
            if not underlying_conid:
                raise IBClientError(
                    f"Unexpected contract search response shape "
                    f"(no 'conid' key): {candidates!r}"
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
                    """True only if d's expiry parses AND equals the requested
                    date. FAIL-CLOSED (audit #20): a row with no parseable
                    expiry field is REJECTED, not accepted — accepting it
                    could land on a wrong-dated (e.g. already-expired) conid,
                    and this filter is the only expiry guard before
                    place_iron_condor. The permissive "field absent → accept"
                    behavior is gated behind the test-only
                    `_allow_missing_expiry` flag (never set in production).
                    """
                    for key in ("maturityDate", "expirationDate", "expiry"):
                        val = d.get(key)
                        if val:
                            return str(val) == want_yyyymmdd
                    # No expiry field present.
                    return self._allow_missing_expiry

                def _matches_trading_class(d: dict) -> bool:
                    # `d.get(key, "")` returns the default only when the key
                    # is ABSENT — a present `tradingClass: null` yields None,
                    # whose .upper() would raise AttributeError (propagating a
                    # raw error to the caller instead of the typed
                    # IBClientError this method otherwise raises). Coerce so a
                    # None value is treated as a non-match.
                    tc = d.get("tradingClass") or ""
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
        expiry = _coerce_expiry(expiry)  # accept ISO string over the RPC wire (#4)
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
                # Cross-bot concurrency cap (429-burst fix): only the actual
                # secdef call holds a permit — cache hits already returned
                # above, so warm strikes never serialize behind this.
                with self._chain_resolve_sem:
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
                    # Trading-class filter (SPXW vs SPX monthly). Coerce so a
                    # present `tradingClass: null` (None) is a non-match
                    # rather than an AttributeError on .upper().
                    tc = d.get("tradingClass") or ""
                    if not (tc == trading_class
                            or trading_class.upper() == tc.upper()):
                        continue
                    # Exact-expiry filter — a strike can be listed across
                    # many May expiries (probe 7: strike 6750 had 7). FAIL-
                    # CLOSED (audit #20): a row with no parseable expiry is
                    # skipped, not accepted — accepting an undated row could
                    # land on a wrong-dated (e.g. already-expired) conid, and
                    # this is the only expiry guard before place_iron_condor.
                    # The permissive "missing expiry → accept" path is gated
                    # behind the test-only `_allow_missing_expiry` flag.
                    mat = str(
                        d.get("maturityDate")
                        or d.get("expirationDate")
                        or d.get("expiry")
                        or ""
                    )
                    if mat:
                        if mat != want_yyyymmdd:
                            continue
                    elif not self._allow_missing_expiry:
                        continue
                    right = (d.get("right") or "").strip().upper()
                    if right not in ("C", "P"):
                        continue
                    raw_conid = d.get("conid") or d.get("conidEx")
                    if raw_conid:
                        resolved[(strike, right)] = int(raw_conid)
                return resolved
            except (CircuitBreakerOpen, RatePenaltyError):
                # Broker-down (breaker) or rate-limit penalty box — must NOT be
                # swallowed as "strike absent". Propagate so the WHOLE batch
                # aborts and the caller (_read_option_chain) can tell a degraded
                # broker / boxed session from a genuinely empty chain and alert.
                # RatePenaltyError especially: without this re-raise the generic
                # handler below returns {} per strike, qualify_option_strikes
                # yields an empty map, and the strategy's `except RatePenaltyError`
                # would be DEAD CODE (silent skipped entry, no alert).
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

    def _ensure_iserver_primed(self, _risk_critical: bool = False) -> None:
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
        # Audit #12: check-and-set under _call_lock so a disconnect()
        # racing in between the read and the prime call can't leave
        # _iserver_primed=True against a torn-down session. _call_lock is
        # an RLock, so the nested _ib_call acquisition on this thread is
        # free. The actual /iserver/accounts call is cheap + idempotent.
        with self._call_lock:
            if self._iserver_primed:
                return
            # Inherit risk-criticality from the caller: a risk-critical snapshot
            # (stop-loss read) reaching here after a reconnect-during-box must
            # NOT be blocked by this mandatory preflight, or the snapshot — and
            # thus stop-loss price reads — go dark for the whole 10-min box.
            self._ib_call("session", self._client.receive_brokerage_accounts,
                          _risk_critical=_risk_critical)
            self._iserver_primed = True

    def _ensure_secdef_search_primed(self, symbol: str, sec_type: str) -> None:
        """Issue /iserver/secdef/search for `symbol` once per session.

        IBKR requires a secdef/search for a symbol before /secdef/strikes and
        /secdef/info will resolve that symbol's OPTION contracts in the session;
        without it they 500 with "No Contracts retrieved". qualify_contract's
        conid PIN (#14) bypasses the fuzzy search, which inadvertently removed
        this priming on 2026-06-01 and broke all option-chain resolution. We
        re-issue the search here purely for its server-side priming effect (the
        result is discarded — the pinned conid is authoritative).

        Idempotent per (symbol, sec_type); the set is cleared in lockstep with
        _conid_cache on disconnect so a cached conid never outlives its priming.
        Best-effort: a priming failure is logged, not raised — the subsequent
        strikes/info call will surface any genuine error.
        """
        key = (symbol, sec_type)
        # _call_lock is an RLock; qualify_contract already holds it, and the
        # nested _ib_call re-acquisition on this thread is free.
        with self._call_lock:
            if key in self._secdef_search_primed:
                return
            try:
                self._ib_call(
                    "market", self._client.search_contract_by_symbol,
                    symbol=symbol, sec_type=sec_type,
                )
                self._secdef_search_primed.add(key)
            except Exception as exc:
                logger.warning(
                    "secdef search-prime for %s/%s failed (%s) — option-chain "
                    "secdef calls may 500 'No Contracts retrieved' until it "
                    "succeeds", symbol, sec_type, exc,
                )

    def _snapshot_with_preflight(self, conids: str, fields: list[str],
                                 _risk_critical: bool = False):
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
        the preflight, sleeping `_SNAPSHOT_POLL_INTERVAL_S` between each.

        For a multi-conid batch we wait until EVERY requested conid has a
        non-metadata field (audit #19) — exiting on the first populated
        conid would leave the still-cold conids reading all-None with no
        signal. If the budget is exhausted with only SOME conids warm, that
        partial warmup is counted in `snapshot_warmup_exhausted_count` and
        logged with the specific missing conids. Returns whatever the LAST
        call produced (rows may still be metadata-only if a conid genuinely
        has no entitlement / is stale — caller checks via _parse_quote_row).

        ibind's own `live_marketdata_snapshot_by_symbol` also calls the
        endpoint twice but doesn't warmup-poll; we needed this in
        production because HYDRA reads quotes on demand and a silent
        all-None response would break credit estimation + stop monitoring.
        """
        # Pre-flight 1: /iserver/accounts (required, once per session). Inherit
        # risk-criticality so a stop-loss read's mandatory preflight survives a
        # reconnect-during-box (else the snapshot fails and stop reads go dark).
        self._ensure_iserver_primed(_risk_critical=_risk_critical)

        # Pre-flight 2: ibind's snapshot priming — first call arms the
        # conid's snapshot cache, it returns no data itself.
        self._ib_call(
            "market", self._client.live_marketdata_snapshot,
            conids=conids, fields=fields,
            _risk_critical=_risk_critical,
        )

        # Requested conids — `conids` is the comma-separated string the
        # endpoint expects. Audit #19: for multi-conid batches we must NOT
        # stop the instant ANY one conid populates (the old `_snapshot_has_data`
        # exit), or the still-cold conids silently return all-None — and
        # nothing counts/alerts that partial warmup. Track per-conid
        # readiness and keep polling until EVERY requested conid is warm or
        # the budget is exhausted.
        requested = {c.strip() for c in str(conids).split(",") if c.strip()}

        # Data call + warmup polling — return only when ALL requested conids
        # have data (or budget exhausted, handled below).
        data = None
        for attempt in range(_SNAPSHOT_MAX_WARMUP_POLLS):
            data = self._ib_call(
                "market", self._client.live_marketdata_snapshot,
                conids=conids, fields=fields,
                _risk_critical=_risk_critical,
            )
            ready = _snapshot_ready_conids(data)
            if requested:
                if requested.issubset(ready):
                    return data
            elif _snapshot_has_data(data):
                # No parseable requested set (shouldn't happen) — fall back
                # to the legacy "any data" exit so we don't loop forever.
                return data
            if attempt < _SNAPSHOT_MAX_WARMUP_POLLS - 1:
                time.sleep(_SNAPSHOT_POLL_INTERVAL_S)

        # Budget exhausted. Distinguish full vs partial warmup so the
        # operator sees that N of M conids never warmed (audit #19): a
        # partial warmup still degrades stop monitoring / credit estimation
        # for the cold conids even though some rows are populated.
        ready_final = _snapshot_ready_conids(data)
        missing = sorted(requested - ready_final) if requested else []
        if missing and ready_final:
            # PARTIAL: some conids warmed, some didn't. Count it the same as
            # a full exhaustion (it is a data-quality degradation event) and
            # alert with the specific missing conids.
            self._snapshot_warmup_exhausted_count += 1
            logger.warning(
                "snapshot warmup PARTIAL for conids=%s — %d of %d conid(s) "
                "never returned price fields within the warmup budget "
                "(missing=%s). Likely no entitlement / stale conid(s) for "
                "those; the rest returned data. Cold conids will read as "
                "all-None.",
                conids, len(missing), len(requested), missing,
            )
            return data if isinstance(data, list) else [data] if data else []
        # P7-audit M17: warmup budget exhausted. A non-empty `data` here
        # is metadata-only (`{conid, _updated}` with no price fields) —
        # truthy but useless to the caller; an empty `data` is "broker
        # returned nothing". Log diagnostically so the operator can tell
        # "no entitlement" from "preflight bug" from "no quote", and
        # always return a list (never None) so callers can iterate
        # without a type check.
        # Polish Item 1: increment the exhaustion counter exactly once
        # per exhausted call (regardless of metadata-only vs empty-list).
        # main.py polls this to fire a per-day Telegram alert on first
        # exhaustion + a follow-up alert at 25+ exhaustions/day.
        self._snapshot_warmup_exhausted_count += 1
        if data:
            # Audit #41: the raw response is gated behind
            # debug_log_payloads so the documented "never log full IBKR
            # responses in production" contract actually holds. Without the
            # flag we log only the conids + row count (non-sensitive).
            if self.cfg.debug_log_payloads:
                logger.warning(
                    "snapshot warmup exhausted for conids=%s — returning "
                    "metadata-only rows (no price fields). "
                    "Likely causes: no real-time entitlement for this conid, "
                    "stale/invalid conid, or IBKR snapshot service degraded. "
                    "Last response: %r",
                    conids, data,
                )
            else:
                logger.warning(
                    "snapshot warmup exhausted for conids=%s — returning "
                    "metadata-only rows (no price fields). "
                    "Likely causes: no real-time entitlement for this conid, "
                    "stale/invalid conid, or IBKR snapshot service degraded. "
                    "(set debug_log_payloads to log the raw response)",
                    conids,
                )
        else:
            logger.warning(
                "snapshot warmup exhausted for conids=%s — broker "
                "returned empty list. Likely IBKR snapshot service "
                "outage or preflight degradation.",
                conids,
            )
        return data if isinstance(data, list) else []

    def get_quote(
        self,
        conid: int,
        fields: Optional[Iterable[str]] = None,
    ) -> dict:
        """Fetch a single snapshot quote for a conid.

        REST-based — does NOT subscribe to streaming. For ongoing monitoring,
        use the StreamingManager (Phase A.5).

        Returns dict with keys: bid, ask, last, mid, mark, bid_size, ask_size,
        availability (6509 raw string; first char R=RealTime/D=Delayed/
        Z=Frozen/Y=Frozen-Delayed/N=Not-Subscribed), conid, raw.
        Values are None when IBKR returns nothing for the field.

        SaxoClient.get_quote() equivalent. Caller doesn't need to know the
        IBKR field codes — defaults cover what HYDRA reads today.
        """
        self._require_connected()
        fields = list(fields) if fields else DEFAULT_QUOTE_FIELDS
        # Risk-critical: this is the stop-loss monitor's price source. It must
        # stay alive (via the slow penalty gate) if the rate-penalty box is
        # active, so a held 0DTE position is never left unmonitored. New entries
        # are still blocked during a box because CHAIN resolution is refused.
        data = self._snapshot_with_preflight(str(conid), fields, _risk_critical=True)
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
        # Risk-critical: stop-loss monitoring reads spread leg values via this
        # batch path — keep it alive on the slow penalty gate during a box.
        data = self._snapshot_with_preflight(
            ",".join(str(c) for c in conids), fields, _risk_critical=True,
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
        # P7-audit L9: do NOT compute mid on a crossed market (bid > ask).
        # Crossed quotes are a transient IBKR state during fast moves /
        # quote-staleness and (bid+ask)/2 of a crossed quote is a
        # nonsense price that downstream callers (credit estimation,
        # stop-monitoring) would treat as authoritative. Setting mid=None
        # forces callers to fall back to last/mark or skip this tick.
        if bid is not None and ask is not None and bid <= ask:
            mid = (bid + ask) / 2
        else:
            mid = None

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
            # NaN check: NaN != NaN, so this catches it cleanly.
            # P7-audit H8: do NOT hard-fail on a bad/missing rate — a
            # transient missing ledger field would block ALL trading.
            # Degrade: report base-currency available funds as `tradable`.
            # For CALYPSO (EUR base, USD trade) EUR-avail < USD-equivalent,
            # so the buying-power gate stays CONSERVATIVE — never falsely
            # permissive — and trading continues.
            if exchange_rate != exchange_rate or exchange_rate <= 0:
                logger.warning(
                    "get_balance(%s): ledger exchangerate missing/invalid "
                    "(%r) — falling back to base-currency available funds "
                    "(%.2f %s) as a conservative tradable estimate.",
                    currency, raw_rate, base_avail, base_currency,
                )
                return {
                    "tradable": base_avail,
                    "currency": currency,
                    "base_currency": base_currency,
                    "base_available": base_avail,
                    "exchange_rate": None,
                    "cash_in_target": 0.0,
                    "fx_rate_unavailable": True,
                    "raw_summary": summary,
                    "raw_ledger": ledger,
                }
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
                _risk_critical=True,  # stop-mgmt: must read held positions during a box
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

        P7-audit M14: the IBKR ``currency_exchange_rate`` response shape
        is ``{"USD.EUR": 0.92}`` (or ``{"rate": 0.92}`` on some variants).
        The lookup now explicitly tries known shapes — ``"rate"``,
        ``"{source}.{target}"``, ``"{source}_{target}"``,
        ``"{target}.{source}"`` (inverse) — and logs a warning when none
        match so a real broker shape change surfaces in logs instead of
        silently returning None. (Inverse is logged but not auto-flipped
        — callers know the directional convention they asked for.)

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
            # Try known IBKR response shapes in order of likelihood.
            for key in (
                "rate",
                f"{source}.{target}",
                f"{source}_{target}",
            ):
                v = data.get(key)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
            # Inverse shape — log it so a future maintainer notices the
            # convention mismatch instead of silently mis-converting.
            inverse_key = f"{target}.{source}"
            inv = data.get(inverse_key)
            if inv is not None:
                try:
                    rate = float(inv)
                    if rate:
                        logger.warning(
                            "get_fx_rate: broker returned inverse key %r "
                            "(=%.6f) for requested %s/%s; returning the "
                            "inverse-flipped value (1/rate). Verify "
                            "direction matches caller expectation.",
                            inverse_key, rate, source, target,
                        )
                        return 1.0 / rate
                except (TypeError, ValueError):
                    pass
            logger.warning(
                "get_fx_rate: no known rate key in response for %s/%s; "
                "keys=%s",
                source, target, list(data.keys()),
            )
            return None
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
        expiry = _coerce_expiry(expiry)  # accept ISO string over the RPC wire (#4)
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
        # P7-audit L8: coerce each side to a list explicitly — `calls + puts`
        # would raise TypeError if either side came back as a single
        # scalar (we've seen IBKR return one strike as a bare number on
        # very thin chains) or a dict instead of a list.
        if isinstance(data, dict):
            def _as_list(v):
                if v is None:
                    return []
                if isinstance(v, list):
                    return v
                return [v]  # scalar or unexpected shape — treat as one entry
            calls = _as_list(data.get("call") or data.get("calls"))
            puts = _as_list(data.get("put") or data.get("puts"))
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
        # Risk-critical: the double-execution guard (_find_order_by_coid) and
        # stop-close reconciliation depend on reading live orders during a box.
        self._ib_call(
            "orders", self._client.live_orders,
            account_id=self.account_id, force=True,
            _risk_critical=True,
        )
        data = self._ib_call(
            "orders", self._client.live_orders,
            account_id=self.account_id,
            _risk_critical=True,
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
        # Risk-critical: place_and_wait_for_fill polls this to confirm a
        # stop-close fill; it must keep polling during a box (on the slow
        # penalty gate) or a 0DTE stop-close could hang unconfirmed.
        return self._ib_call(
            "orders", self._client.order_status,
            order_id=str(order_id),
            _risk_critical=True,
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
    def _spx_option_tick(price: float) -> float:
        """SPX/SPXW single-leg minimum price increment for a given premium.

        IBKR-audit #16: SPX index options (incl. SPXW 0DTE) are NOT a flat
        $0.05 grid — CBOE's tick regime is $0.05 for series quoted UNDER
        $3.00 and $0.10 AT/ABOVE $3.00. Rounding a $3+ single leg to the
        $0.05 grid can land off the legal $0.10 grid (e.g. $3.05) and the
        exchange rejects the order. Combo *net-credit* prices stay $0.05 on
        the Complex Order Book — this tiered rule is the SINGLE-LEG path
        only (legging in/out, stop-out closes). abs() so it's sign-safe.
        """
        return 0.05 if abs(price) < 3.0 else 0.10

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

        Atomic-fill enforcement: a combo on the CP API fills atomically on the
        complex (BAG) book, and the order ticket DOES expose `allOrNone`
        (boolean) per the IBKR REST OpenAPI spec — so if this combo path is ever
        promoted to the live entry, pass allOrNone=True via OrderRequest rather
        than building a WebSocket partial-fill watcher.

        Entry path (IBKR-audit #1): the live/paper entry does NOT use this combo
        method — it LEGS IN via 4 single-leg place_and_wait_for_fill calls (this
        combo path is not yet RPC-exposed; see the broker ALLOWLIST). Legging is
        the accepted approach for both paper and live because the strategy buys
        the PROTECTION (long wings) BEFORE selling the shorts (see
        _execute_entry / _execute_put_spread_only — "buy protection first"), so
        there is never a naked-short window even if a later leg fails; a failed
        short after a filled long leaves a fully-hedged long, not naked risk.

        Conid preflight (IBKR-audit #21): every leg conid here is resolved by
        qualify_contract, which walks the secdef chain (search_secdef_info_by_
        conid) and fail-closes on an expiry mismatch — i.e. qualify_contract IS
        the secdef preflight, so a stale/expired/wrong-dated strike is rejected
        BEFORE any order POST rather than 500'ing at whatif/submit time.

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
        price_increment: "float | str" = _SPX_TIERED_TICK,
    ) -> dict:
        """Place a single-leg order.

        Used for single options or stock orders. For multi-leg combos use
        place_iron_condor / place_vertical_spread.

        Args:
            price_increment: tick size for price rounding. Defaults to the
                SPX/SPXW tiered single-leg tick (``_SPX_TIERED_TICK``):
                $0.05 under $3.00, $0.10 at/above $3.00 (IBKR-audit #16) —
                correct for HYDRA's universe today. For equities or sub-$1
                stocks, pass an explicit numeric increment (e.g. 0.01). Pass
                0 to skip rounding entirely (caller has already chosen a
                tick-conforming price).

        SaxoClient.place_order() equivalent.
        """
        self._require_connected()
        if order_type == "LMT" and price is None:
            raise IBClientError("LMT order requires price")
        if side not in ("BUY", "SELL"):
            raise IBClientError(f"side must be 'BUY' or 'SELL', got {side!r}")

        if price is None:
            rounded_price = price
        else:
            inc = price_increment
            if inc == _SPX_TIERED_TICK:
                inc = self._spx_option_tick(price)  # tiered SPX/SPXW tick
            if inc and inc > 0:
                rounded_price = self._round_to_increment(price, inc)
            else:
                rounded_price = price  # inc == 0 → caller-chosen, skip
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
        price_increment: "float | str" = _SPX_TIERED_TICK,
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
                SPX/SPXW tiered tick — $0.05 <$3, $0.10 ≥$3, IBKR-audit #16;
                pass an explicit numeric increment, e.g. 0.01, for equities)

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
        # (instant fills on MKT orders; reject responses for invalid
        # orders). P7-audit C3: IBKR's place response reports the state
        # under `order_status` (camelCase `status` is the *live-order*
        # field) — check both, order_status first.
        initial_status = (
            place_resp.get("order_status")
            or place_resp.get("status")
            or ""
        ).lower()
        if initial_status in _TERMINAL_ORDER_STATUSES:
            # P7-audit C4: the place response carries the status but NOT
            # the fill detail (filledQuantity / avgPrice). For an instant
            # fill, fetch the authoritative numbers from the order-status
            # endpoint first — otherwise a genuinely-filled order is
            # reported filled_quantity=0, the caller treats the entry as
            # failed and retries → a double position.
            fill_raw = place_resp
            if initial_status == "filled":
                try:
                    status_resp = self.get_order_status(str(order_id))
                    if status_resp:
                        fill_raw = status_resp
                except IBClientError:
                    pass  # order purged post-fill — fall back to place_resp
            return _build_fill_result_dict(
                order_id=str(order_id), raw=fill_raw, status=initial_status,
                requested_quantity=quantity,
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
            except CircuitBreakerOpen as exc:
                # P7-audit M12: the orders breaker opened DURING polling
                # (e.g. five consecutive retryable 5xx from get_order_status).
                # The order is unknown — it may still be working, may have
                # filled, may have been purged. We must NOT propagate
                # CircuitBreakerOpen — the documented `Raises:` clause says
                # only ValueError / IBClientError. Surface as `timed_out`
                # (the existing "may still be working — caller escalates"
                # path: cancel the order id + retry at the next chase level).
                logger.error(
                    "place_and_wait_for_fill: orders breaker OPEN during "
                    "status poll for %s (%s) — surfacing as timed_out; "
                    "caller should cancel + escalate",
                    order_id, exc,
                )
                return _build_fill_result_dict(
                    order_id=str(order_id),
                    raw=last_status_resp,
                    status="timed_out",
                )

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
                    requested_quantity=quantity,
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
                # Risk-critical: a cancel on the stop-CLOSE path flattens a
                # still-working close before the residual is re-placed. If the
                # penalty box refused it, the un-cancelled remainder could fill
                # late → over-close / new naked exposure. Low-volume write, never
                # a 429 burst source — must stay alive on the penalty gate.
                _risk_critical=True,
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
        price_increment: float = 0.05,
    ) -> dict:
        """Modify a working order's price (and/or quantity).

        IBKR's modify_order replaces the previous order instance (not amend);
        order_id stays the same but other fields are re-submitted. Per ibind
        docs (OrderMixin.modify_order): *"The content should mirror the
        content of the original order."* So the caller must pass the
        original side + quantity (and optionally the conid for single-leg
        orders) — we do not invent placeholders.

        ``price_increment`` mirrors ``place_order`` (P7-audit H11):
        default 0.05 (SPX/SPXW combo tick); pass 0.01 for equities or
        single-leg sub-$3 options, 0 to skip rounding.

        **Single-leg only.** Combo (BAG/conidex) modify requires
        ``sec_type="BAG"`` + the original ``conidex`` to be carried
        through (per IBKR's "content should mirror the original"
        requirement); the current implementation does not do that yet
        and would likely be rejected on a combo. HYDRA's progressive-
        chase entry placement uses cancel+place, not modify, so this
        is not blocking. P7-audit H11 follow-up: implement combo modify
        before any caller uses it on a combo.
        """
        self._require_connected()
        if not side:
            raise IBClientError("modify_order requires side ('BUY' or 'SELL')")
        if quantity is None or quantity <= 0:
            raise IBClientError("modify_order requires a positive quantity")

        rounded_price = (
            self._round_to_increment(price, price_increment)
            if price is not None and price_increment > 0 else price
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

        P7-audit M11: ``/iserver/account/{accountId}/order/whatif`` is on
        the same ``/iserver`` namespace as the snapshot endpoint and
        requires the brokerage session to be primed via
        ``/iserver/accounts``. Without the preflight, whatif silently
        returns an empty / metadata-only block, defeating the BP gate.
        ``_ensure_iserver_primed`` is idempotent so the extra call is
        free on subsequent invocations.
        """
        self._require_connected()
        self._ensure_iserver_primed()
        return self._ib_call(
            "orders", self._client.whatif_order,
            order_request=order,
            account_id=self.account_id,
        ) or {}

    def _find_order_by_coid(self, coid: str) -> Optional[dict]:
        """Return the live_orders envelope whose cOID matches `coid`, or None.

        Used by the order-WRITE failure path to decide whether a transient
        place_order failure (e.g. a 503/timeout AFTER the order POST already
        landed) left a working order behind. IBKR surfaces the customer
        order id under a few key spellings depending on endpoint/version
        (`cOID`, `coid`, `order_ref`, `orderRef`), so match all of them.

        Returns None on ANY uncertainty — including a failed lookup — so the
        caller fails CLOSED (does NOT resubmit unless absence is positively
        confirmed).
        """
        if not coid:
            return None
        try:
            orders = self.get_open_orders()
        except Exception as exc:
            # Lookup itself failed — we cannot prove absence. Treat as
            # "unknown", caller must not resubmit.
            logger.warning(
                "_find_order_by_coid: live_orders lookup failed (%s) — "
                "cannot confirm order %s state", exc, coid,
            )
            return None
        for o in orders or []:
            if not isinstance(o, dict):
                continue
            for key in ("cOID", "coid", "order_ref", "orderRef"):
                if str(o.get(key) or "") == coid:
                    return o
        return None

    def _normalize_place_response(self, data) -> dict:
        """Normalize ibind's place_order response into a single dict.

        ibind returns a list when the order resolves to multiple entries
        (one per leg for combos, one per child for OCA brackets). We promote
        the first entry to the top-level dict for caller convenience and
        stash the remaining entries under "_legs" so nothing is silently
        dropped — callers that need per-leg fill tracking read order["_legs"].

        P7-audit M15: prefer entries that look like real order responses
        (carry `order_id` or `id`) over reply-prompt entries (which carry
        `message`/`messageIds` but not `order_id`).
        """
        data = data or {}
        if isinstance(data, list):
            if not data:
                return {}
            if len(data) == 1:
                return data[0] if isinstance(data[0], dict) else {"_first": data[0]}

            def _looks_like_order(d):
                return isinstance(d, dict) and (
                    d.get("order_id") or d.get("id")
                )
            ordered = [d for d in data if _looks_like_order(d)]
            if ordered:
                head = ordered[0]
                rest = [d for d in data if d is not head]
            else:
                # Audit #41: the raw order response carries order IDs /
                # account context — gate the dump behind debug_log_payloads
                # so the documented "never log full IBKR responses in
                # production" contract holds. Without the flag, log only the
                # entry count + keys.
                if self.cfg.debug_log_payloads:
                    logger.warning(
                        "place_order: list response had no entry with "
                        "order_id/id (possible unresolved reply prompt); "
                        "promoting first entry. raw=%r", data,
                    )
                else:
                    logger.warning(
                        "place_order: list response had no entry with "
                        "order_id/id (possible unresolved reply prompt); "
                        "promoting first entry (%d entries; first keys=%s; "
                        "set debug_log_payloads to log the raw response)",
                        len(data),
                        sorted(data[0].keys())
                        if isinstance(data[0], dict) else type(data[0]).__name__,
                    )
                head = data[0]
                rest = list(data[1:])
            out = dict(head) if isinstance(head, dict) else {"_first": head}
            out["_legs"] = rest
            return out
        return data

    def _submit_order(
        self,
        order: OrderRequest,
        answers: Optional[dict] = None,
    ) -> dict:
        """Internal: submit an OrderRequest via ibind, normalize the response.

        Reply-prompt handling: pass our DEFAULT_ORDER_ANSWERS unless caller
        overrides. ibind walks the reply loop until IBKR's prompt chain is
        cleared or rejects an unknown prompt (in which case it raises).

        Double-execution safety (audit: place_order retry double-execution):
        ibind's place_order FIRST POSTs the order and THEN runs the reply
        loop (more POSTs to /iserver/reply/{id}). If the order POST SUCCEEDS
        but a later reply POST hits a transient 503/timeout, blindly
        retrying the whole place_order (the old behavior, via the shared
        retry policy) could create a SECOND working order: the cOID server-
        side dedup is only demonstrated for an order that is already
        *registered*, not one still mid-reply. So we run the place call with
        max_attempts=1 (NO blind re-POST) and, on a *retryable* failure,
        look up the order by cOID before deciding:
          • found  → the order is live; return it, do NOT resubmit;
          • lookup inconclusive → raise AmbiguousOrderError so the caller
            ABORTS the placement (it must NOT resubmit under a fresh cOID —
            the production caller uses a per-attempt cOID, so a "retry" would
            be a genuinely new order and could double-fill). The maybe-live
            order is left for reconciliation/alerting.
        Non-retryable errors (auth/validation) propagate immediately — the
        order did not land, so the caller may safely try the next rung.
        """
        a = answers if answers is not None else DEFAULT_ORDER_ANSWERS
        coid = getattr(order, "coid", None)
        # No-blind-retry policy for the WRITE POST.
        no_retry = replace(self._retry_policy, max_attempts=1)

        def _place():
            return self._ib_call(
                "orders", self._client.place_order,
                _policy=no_retry,
                _risk_critical=True,  # a stop-CLOSE must place during a box;
                # new ENTRIES can't reach here in a box anyway (chain refused)
                order_request=order,
                answers=a,
                account_id=self.account_id,
            )

        try:
            data = _place()
        except CircuitBreakerOpen:
            # Broker degraded — surface, never resubmit.
            raise
        except Exception as exc:
            # Only the place POST can have side effects we must not repeat.
            # If the failure is non-retryable (validation/auth), the order
            # almost certainly never landed — but we STILL fail closed:
            # only resubmit when we can positively confirm absence via the
            # cOID lookup.
            if not self._retry_policy.is_retryable(exc):
                raise
            logger.warning(
                "_submit_order: place_order raised retryable error (%s); "
                "checking live_orders for coid=%s before any resubmit "
                "(double-execution guard)", exc, coid,
            )
            existing = self._find_order_by_coid(coid)
            if existing is not None:
                logger.info(
                    "_submit_order: order coid=%s already present after "
                    "transient place failure — returning existing, NOT "
                    "resubmitting", coid,
                )
                return self._normalize_place_response(existing)
            # Could not confirm the order is present. _find_order_by_coid
            # returns None both for "confirmed absent" and "lookup failed",
            # so we cannot prove the POST did not land. Fail CLOSED: raise a
            # DISTINCT AmbiguousOrderError so the caller ABORTS rather than
            # re-placing under a fresh per-attempt cOID (which would be a new
            # order → double-fill). The maybe-live order is surfaced to
            # reconciliation/alerting instead.
            raise AmbiguousOrderError(
                f"place_order coid={coid} failed transiently and the order's "
                f"live state is unconfirmed; aborting to avoid a double-fill"
            ) from exc

        return self._normalize_place_response(data)

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
        #
        # P7-audit M16: the prior code passed `start=True` (which kicks
        # ibind's connect/listen threads asynchronously) and then
        # immediately called `StreamingManager.start()` — our consume
        # thread could race the WS handshake and try to read from a
        # not-yet-ready queue accessor. Sequence is now explicit:
        # construct with `start=False`, start the WS, briefly wait for
        # `ws.ready`, THEN start our StreamingManager. Best-effort wait
        # (up to 5s) — if the WS isn't ready, StreamingManager.start()
        # still proceeds and ibind's reconnect-on-close handles a later
        # connection drop.
        ws = IbkrWsClient(
            ibkr_client=self._client,
            account_id=self.account_id,
            use_oauth=True,
            access_token=self.cfg.credentials.access_token,
            unwrap_market_data=False,
            start=False,
        )
        ws.start()
        # Wait briefly for `ready` so StreamingManager doesn't race the
        # handshake. We don't gate on it — log + continue if it's slow.
        ws_ready_deadline = time.monotonic() + 5.0
        while time.monotonic() < ws_ready_deadline:
            if getattr(ws, "ready", False):
                break
            time.sleep(0.1)
        else:
            logger.warning(
                "IBClient: IbkrWsClient did not report ready within 5s "
                "— starting StreamingManager anyway; ibind reconnect "
                "will recover if the WS is slow."
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
