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

    def health(self) -> dict:
        import requests
        r = requests.get(f"{self._base_url}/health", timeout=5)
        r.raise_for_status()
        return r.json()
