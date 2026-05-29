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


def to_jsonable(value: Any) -> Any:
    """Round-trip through JSON so the returned object is byte-for-byte what
    crosses the wire to BrokerClient (guarantees shape parity)."""
    return json.loads(json.dumps(value, default=_json_default))


class BrokerDispatcher:
    """Framework-agnostic RPC core wrapping the single IBClient."""

    def __init__(self, ib: Any):
        self._ib = ib

    def dispatch(self, method: str, args: Optional[list] = None,
                 kwargs: Optional[dict] = None) -> dict:
        """Validate + invoke one broker method. Never raises — returns
        {"result": <jsonable>} or {"error": str, "type": str}."""
        if method not in ALLOWED_METHODS:
            return {"error": f"method not allowed: {method!r}", "type": "MethodNotAllowed"}
        fn = getattr(self._ib, method, None)
        if not callable(fn):
            return {"error": f"broker has no method {method!r}", "type": "MethodNotAllowed"}
        try:
            result = fn(*(args or []), **(kwargs or {}))
            return {"result": to_jsonable(result)}
        except Exception as e:  # noqa: BLE001 — surface any IBClient error to caller
            logger.warning("broker rpc %s failed: %s: %s", method, type(e).__name__, e)
            return {"error": str(e), "type": type(e).__name__}

    def health(self) -> dict:
        """Best-effort session health; never raises."""
        try:
            connected = bool(getattr(self._ib, "is_connected", lambda: False)())
        except Exception:
            connected = False
        return {"status": "ok" if connected else "degraded", "connected": connected}


def create_app(dispatcher: BrokerDispatcher):
    """Thin FastAPI shell exposing the dispatcher on loopback. Imported lazily so
    this module stays importable (for ALLOWED_METHODS / the dispatcher) without
    FastAPI installed. The /rpc endpoint is sync `def` → FastAPI runs it in a
    threadpool, so a slow place_and_wait_for_fill doesn't head-of-line-block
    other strategies' quote reads (IBClient's own locks serialize IBKR access)."""
    from fastapi import FastAPI
    from pydantic import BaseModel

    class RpcRequest(BaseModel):
        method: str
        args: list = []
        kwargs: dict = {}

    app = FastAPI(title="calypso-broker", version="1.0.0")

    @app.post("/rpc")
    def rpc(req: RpcRequest):  # sync → threadpool
        return dispatcher.dispatch(req.method, req.args, req.kwargs)

    @app.get("/health")
    def health():
        return dispatcher.health()

    return app
