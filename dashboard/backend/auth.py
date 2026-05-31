"""Optional API-key auth for the dashboard REST API (audit M12).

The REST API previously had NO authentication while nginx exposes it on
``0.0.0.0:8080`` — anyone who could reach the host could read full bot
state, P&L and config. This dependency requires a matching key on every
``/api/*`` REST route via the ``X-API-Key`` header (or ``?api_key=`` query
param, to match the WebSocket endpoint in ws/router.py).

Posture mirrors the WebSocket auth: when ``settings.api_key`` is empty,
auth is DISABLED (local/dev) — so this is backward-compatible until a key
is configured. Set ``DASHBOARD_API_KEY`` on the VM to turn it on (and bind
nginx to localhost / a VPN for defense in depth).
"""

import hmac
from typing import Optional

from fastapi import Header, HTTPException, Query, status

from dashboard.backend.config import settings


async def require_api_key(
    x_api_key: Optional[str] = Header(default=None),
    api_key: Optional[str] = Query(default=None),
) -> None:
    """Reject the request unless a configured API key matches.

    No-op when no key is configured (``settings.api_key`` empty).
    """
    if not settings.api_key:
        return
    provided = x_api_key or api_key
    if not provided or not hmac.compare_digest(provided, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
