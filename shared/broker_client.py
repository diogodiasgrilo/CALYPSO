"""BrokerClient — a drop-in replacement for IBClient on the strategy side.

bots/hydra/base_strategy.py only ever calls ``self.broker.<one of the 16
allowlisted methods>(...)`` (see BROKER_SESSION_SERVICE_DESIGN.md §3). BrokerClient
proxies each call over loopback HTTP to calypso-broker and returns the IDENTICAL
shape IBClient would, so no strategy code changes — main.py just constructs a
BrokerClient instead of an IBClient.

The transport is injectable so the contract test can exercise the full
client↔dispatcher round-trip in-process, with no HTTP / FastAPI.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from shared.broker_service import ALLOWED_METHODS, _json_default

logger = logging.getLogger(__name__)


def _decode_wire(value: Any) -> Any:
    """Reverse the JSON-safe transforms broker_service.to_jsonable applies to
    types JSON can't natively carry, so the strategy sees the SAME object an
    in-process IBClient would have returned.

    Currently the only non-JSON-native shape is ``qualify_option_strikes``'s
    ``dict[(strike, right) -> conid]``: tuple keys are illegal in JSON, so the
    broker (broker_service._encode_strike_conid_map) encodes it as
    ``{"__kind__": "strike_conid_map", "items": [{"strike":.., "right":..,
    "conid":..}, ...]}``. Rebuild the tuple-keyed dict here so the consumer's
    ``for (strike, right), conid in conid_map.items()`` is unchanged. Recurse
    through plain dicts/lists so a marker nested anywhere is still decoded; any
    value without the marker is returned untouched."""
    if isinstance(value, dict):
        if value.get("__kind__") == "strike_conid_map":
            return {
                (item["strike"], item["right"]): item["conid"]
                for item in value.get("items", [])
            }
        return {k: _decode_wire(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_wire(v) for v in value]
    return value


class BrokerError(RuntimeError):
    """The broker returned an error, or is unreachable. Strategies should treat
    this like a transient broker/data outage (skip the tick) — never crash-loop;
    the broker has Restart=always and the units depend on it softly (Wants=)."""


class BrokerClient:
    """Loopback RPC stub. Exposes exactly the allowlisted broker surface via
    ``__getattr__``; any other attribute access raises AttributeError as normal."""

    # IBKRAlertHooks (when constructed against the broker) polls
    # ``broker.circuit_breakers``. The real per-family breakers live in the
    # broker PROCESS now (it owns the IBClient), so expose an empty mapping
    # here → the strategy-side breaker poll is a harmless no-op and never
    # AttributeErrors. The breakers still protect the order/market/session
    # paths inside the broker; breaker-transition ALERTING is owned by the
    # broker (see BROKER_SESSION_SERVICE_DESIGN.md — breaker/warmup alert hooks
    # run in calypso-broker, not in each strategy).
    circuit_breakers: dict = {}

    def __init__(self, base_url: str = "http://127.0.0.1:8788", *,
                 timeout: float = 35.0,
                 transport: Optional[Callable[[str, list, dict], dict]] = None):
        # timeout must exceed place_and_wait_for_fill's server-side poll (~30s).
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport or self._http_transport

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name not in ALLOWED_METHODS:
            raise AttributeError(name)

        def _call(*args, **kwargs):
            return self._invoke(name, list(args), dict(kwargs))

        _call.__name__ = name
        return _call

    def _invoke(self, method: str, args: list, kwargs: dict) -> Any:
        try:
            resp = self._transport(method, args, kwargs)
        except BrokerError:
            raise
        except Exception as e:  # any transport failure → BrokerError (never crash)
            raise BrokerError(
                f"broker {method} transport failed: {type(e).__name__}: {e}"
            ) from e
        if not isinstance(resp, dict):
            raise BrokerError(f"broker {method}: malformed response {resp!r}")
        if "error" in resp:
            etype = resp.get("type", "Error")
            msg = f"broker {method}: {etype}: {resp['error']}"
            # Preserve the ONE exception type whose CLASS the caller branches on:
            # AmbiguousOrderError signals "order may be live but unconfirmed —
            # ABORT, do not resubmit". If it collapsed to a generic BrokerError
            # over the wire, bots/hydra/strategy._place_leg_order's
            # `except AmbiguousOrderError` would miss it and the progressive
            # retry loop would re-place under a new cOID → double-fill. Re-raise
            # the same class the bot imports so the abort path fires in broker
            # mode exactly as it does on the direct IBClient path.
            if etype == "AmbiguousOrderError":
                from shared.ib_client import AmbiguousOrderError
                raise AmbiguousOrderError(msg)
            raise BrokerError(msg)
        # Reverse any JSON-safe transforms the broker applied to non-JSON-native
        # return shapes (e.g. qualify_option_strikes' tuple-keyed dict) so the
        # strategy sees exactly what a direct IBClient would have returned.
        return _decode_wire(resp.get("result"))

    def _http_transport(self, method: str, args: list, kwargs: dict) -> dict:
        import json

        import requests
        try:
            # Serialize the request body with the SAME _json_default the broker's
            # return path (broker_service.to_jsonable) uses, so JSON-incompatible
            # args — notably the datetime.date passed to get_option_chain /
            # qualify_option_strikes — serialize via isoformat() instead of
            # raising TypeError. requests' json= path uses the stdlib encoder
            # with no default=, which is why we build the body explicitly here.
            body = json.dumps(
                {"method": method, "args": args, "kwargs": kwargs},
                default=_json_default,
            )
            r = requests.post(
                f"{self._base_url}/rpc",
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            raise BrokerError(
                f"broker unreachable for {method}: {type(e).__name__}: {e}"
            ) from e

    # ── session-lifecycle drop-ins (main.py calls these on the broker object;
    #    they are NOT part of the 16-method data surface — the broker owns the
    #    real IBKR session, so here they just reflect/await the broker's health)
    def connect(self) -> bool:
        """Drop-in for IBClient.connect(): the strategy owns NO IBKR session —
        calypso-broker does. Verify the broker is up and holding a session;
        raise BrokerError (so the strategy fails to start, exactly like
        IBClient.connect raising) if it is not."""
        h = self.health()
        if not h.get("connected"):
            raise BrokerError(f"broker is not holding an IBKR session: {h}")
        logger.info("BrokerClient connected to calypso-broker at %s (%s)",
                    self._base_url, h)
        return True

    def ensure_connected(self) -> bool:
        """Drop-in for IBClient.ensure_connected(): report the broker's session
        health (the broker maintains the session — daily re-auth + 15-min
        re-check live there). Returns False, never raises, so the strategy's
        session gate handles a broker/session problem gracefully."""
        try:
            return bool(self.health().get("connected"))
        except Exception:
            return False

    def health(self) -> dict:
        # Convert any transport failure (broker down/unreachable at startup,
        # bad status, bad JSON) into BrokerError so connect()'s documented
        # contract holds — it always fails-to-start as BrokerError, which is
        # what main.py's connect-failure handler catches (so the logger is shut
        # down cleanly). ensure_connected() swallows this into False itself.
        import requests
        try:
            r = requests.get(f"{self._base_url}/health", timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            raise BrokerError(
                f"broker health check failed: {type(e).__name__}: {e}"
            ) from e
