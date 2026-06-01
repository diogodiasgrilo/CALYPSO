"""calypso-broker — the single shared IBKR session service.

One process owns the ONLY IBClient (one Live Session Token, one ssodh/init, one
Tickler, one morning re-auth gate). Strategies A/B/C reach it through
shared.broker_client.BrokerClient over loopback HTTP. This solves IBKR's
one-brokerage-session-per-username limit (see
docs/migration/BROKER_SESSION_SERVICE_DESIGN.md and IBKR_MULTI_SESSION.md).

Split on purpose so the RPC core is framework-agnostic and unit-testable without
FastAPI (which only runs on the VM):
  • BrokerDispatcher — pure Python: allowlist → call the IBClient → JSON-safe
    {"result": ...} / {"error": ...}. Tested in-process.
  • create_app()     — a thin FastAPI shell, imported lazily at runtime.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The EXACT broker surface the HYDRA strategies call (self.broker.<method>).
# Allowlisted so the RPC can never invoke anything else on the IBClient. Keep in
# sync with the seam audited in BROKER_SESSION_SERVICE_DESIGN.md §3.
ALLOWED_METHODS = frozenset({
    # market data
    "get_quote", "get_quotes_batch", "get_vix_price", "get_option_chain",
    "get_option_greeks", "get_chart_data",
    # contracts
    "qualify_contract", "qualify_option_strikes",
    # account / positions
    "get_positions", "get_balance", "get_fx_rate", "get_open_orders",
    "get_order_status", "get_closed_position_price",
    # orders (writes)
    "place_and_wait_for_fill", "cancel_order",
    # operator override — clear the rate-limit penalty box (no restart needed)
    "clear_rate_penalty",
})


def _json_default(o: Any) -> Any:
    """Fallback encoder for the few non-JSON types IBClient might return."""
    import datetime
    import decimal
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return list(o)
    return str(o)


# Wire-contract sentinel for the one method whose native return shape cannot
# survive a JSON round-trip: qualify_option_strikes returns
# dict[(strike:float, right:str) -> conid:int], and json.dumps rejects tuple
# dict keys (its default= hook is NEVER consulted for keys). We re-encode it as
# an explicit record list so BrokerClient can rebuild the exact tuple-keyed
# dict the strategy's `for (strike, right), conid in conid_map.items()`
# unpacking depends on. Keep this string in sync with BrokerClient's decoder.
STRIKE_CONID_MAP_KIND = "strike_conid_map"


def _is_strike_conid_map(value: Any) -> bool:
    """True iff `value` is the tuple-keyed (strike, right) -> conid dict that
    qualify_option_strikes returns. Used as a method-name-independent fallback
    so an empty {} (which carries no method hint) is still detected correctly
    only via the method name, never misfired on ordinary str/int-keyed dicts."""
    if not isinstance(value, dict) or not value:
        return False
    return all(
        isinstance(k, tuple) and len(k) == 2
        and isinstance(k[0], (int, float)) and isinstance(k[1], str)
        for k in value
    )


def _encode_strike_conid_map(value: dict) -> dict:
    """(strike, right) -> conid  →  {"__kind__":..., "items":[{strike,right,conid}]}."""
    return {
        "__kind__": STRIKE_CONID_MAP_KIND,
        "items": [
            {"strike": float(strike), "right": str(right), "conid": int(conid)}
            for (strike, right), conid in value.items()
        ],
    }


def to_jsonable(value: Any, method: Optional[str] = None) -> Any:
    """Round-trip through JSON so the returned object is byte-for-byte what
    crosses the wire to BrokerClient (guarantees shape parity).

    `method` lets us apply per-method wire contracts for return shapes that a
    plain JSON round-trip cannot represent. qualify_option_strikes returns a
    tuple-keyed dict (unserializable JSON keys), so we re-encode it to an
    explicit record list that BrokerClient decodes back into the tuple-keyed
    dict — preserving the strategy-side contract end to end."""
    if method == "qualify_option_strikes" or _is_strike_conid_map(value):
        value = _encode_strike_conid_map(value if isinstance(value, dict) else {})
    return json.loads(json.dumps(value, default=_json_default))


# How long an authoritative auth/status result is trusted before health() does
# another live round-trip. Short enough that a server-side-dead session is
# caught within seconds (so the bot's intraday session gate fails closed), long
# enough that a burst of /health polls can't hammer IBKR's auth/status.
HEALTH_CACHE_S = 5.0


class BrokerDispatcher:
    """Framework-agnostic RPC core wrapping the single IBClient."""

    def __init__(self, ib: Any):
        self._ib = ib
        # Short-lived cache of the last authoritative health() result.
        self._health_cache: Optional[dict] = None
        self._health_cache_at: float = 0.0

    def dispatch(self, method: str, args: Optional[list] = None,
                 kwargs: Optional[dict] = None) -> dict:
        """Validate + invoke one broker method. Never raises — returns
        {"result": <jsonable>} or {"error": str, "type": str}."""
        # A malformed body can make `method` a non-string (e.g. {"method": []}).
        # The frozenset membership test would hash an unhashable list/dict and
        # raise TypeError OUTSIDE the try/except below, breaking the documented
        # "never raises" contract and 500-ing the loopback endpoint. Guard the
        # type first so any non-str method fails closed as MethodNotAllowed.
        if not isinstance(method, str) or method not in ALLOWED_METHODS:
            return {"error": f"method not allowed: {method!r}", "type": "MethodNotAllowed"}
        fn = getattr(self._ib, method, None)
        if not callable(fn):
            return {"error": f"broker has no method {method!r}", "type": "MethodNotAllowed"}
        try:
            result = fn(*(args or []), **(kwargs or {}))
            return {"result": to_jsonable(result, method)}
        except Exception as e:  # noqa: BLE001 — surface any IBClient error to caller
            logger.warning("broker rpc %s failed: %s: %s", method, type(e).__name__, e)
            return {"error": str(e), "type": type(e).__name__}

    def health(self) -> dict:
        """Authoritative session health; never raises, fails CLOSED.

        The bot's morning + intraday session gates resolve to this endpoint's
        `connected` flag (via BrokerClient.ensure_connected). A cheap cached
        is_connected() is fail-OPEN: IBKR can kill the brokerage session
        server-side (24h LST TTL, ~01:00 ET reset, a competing login) without
        flipping the cached flag, so the bot would keep placing entries/stops
        against a dead session until the broker's next ~15-min re-check.

        So we do a LIVE round-trip via check_auth_status() and only report
        connected when the session is truly usable: authenticated AND connected
        AND not competing. Results are cached for HEALTH_CACHE_S so a burst of
        polls can't hammer IBKR. ANY error, a non-dict status, or a missing
        field degrades (connected=False) — never report a session we cannot
        positively confirm is live."""
        now = time.monotonic()
        if (self._health_cache is not None
                and now - self._health_cache_at < HEALTH_CACHE_S):
            return self._health_cache

        result = self._probe_health()
        self._health_cache = result
        self._health_cache_at = now
        return result

    def _probe_health(self) -> dict:
        """One authoritative, fail-closed auth/status probe. Never raises."""
        check = getattr(self._ib, "check_auth_status", None)
        try:
            status = check() if callable(check) else None
            if not isinstance(status, dict):
                # No authoritative signal available → fail closed.
                return {"status": "degraded", "connected": False}
            authenticated = bool(status.get("authenticated"))
            connected = bool(status.get("connected"))
            competing = bool(status.get("competing"))
            live = authenticated and connected and not competing
            return {
                "status": "ok" if live else "degraded",
                "connected": live,
                "authenticated": authenticated,
                "competing": competing,
            }
        except Exception as e:  # noqa: BLE001 — health must never raise
            logger.warning("broker health check failed: %s: %s",
                           type(e).__name__, e)
            return {"status": "degraded", "connected": False}


def create_app(dispatcher: BrokerDispatcher):
    """Thin FastAPI shell exposing the dispatcher on loopback. Imported lazily so
    this module stays importable (for ALLOWED_METHODS / the dispatcher) without
    FastAPI installed. The /rpc endpoint is sync `def` → FastAPI runs it in a
    threadpool, so a slow place_and_wait_for_fill doesn't head-of-line-block
    other strategies' quote reads (IBClient's own locks serialize IBKR access)."""
    from fastapi import Body, FastAPI

    app = FastAPI(title="calypso-broker", version="1.0.0")

    # Access control is the loopback bind (CALYPSO_BROKER_HOST=127.0.0.1) on a
    # single-tenant VM — that is the intended, accepted boundary for this RPC
    # (no per-request auth token by design; see BROKER_SESSION_SERVICE_DESIGN.md).
    @app.post("/rpc")
    def rpc(body: dict = Body(...)):  # sync → threadpool; explicit JSON body
        return dispatcher.dispatch(
            body.get("method", ""),
            body.get("args") or [],
            body.get("kwargs") or {},
        )

    @app.get("/health")
    def health():
        return dispatcher.health()

    return app
