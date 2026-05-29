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

from shared.broker_service import ALLOWED_METHODS

logger = logging.getLogger(__name__)


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
            raise BrokerError(
                f"broker {method}: {resp.get('type', 'Error')}: {resp['error']}"
            )
        return resp.get("result")

    def _http_transport(self, method: str, args: list, kwargs: dict) -> dict:
        import requests
        try:
            r = requests.post(
                f"{self._base_url}/rpc",
                json={"method": method, "args": args, "kwargs": kwargs},
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
        import requests
        r = requests.get(f"{self._base_url}/health", timeout=5)
        r.raise_for_status()
        return r.json()
