"""Session-cookie auth for the dashboard REST/WS API.

Replaces the old single-shared DASHBOARD_API_KEY (every visitor was the same
undifferentiated "user" forever, no accounts, no expiry, no rate limiting).
Real per-person accounts (username + bcrypt password + TOTP 2FA) now live in
a dedicated DB (services/auth_db.py) and are issued short opaque session
tokens by the login flow in routers/auth.py. Sessions are DB-backed (not
JWT) specifically so they can be revoked instantly (disable a user, log out
everywhere) with a single UPDATE.

Posture mirrors the old key-based gate: when NO accounts exist yet, auth is
a no-op (local-dev convenience) — but this logs a loud warning, and on the
VM there is always >=1 account once bootstrapped via
scripts/manage_dashboard_users.py.
"""

import logging
import time
from typing import Optional

from fastapi import Cookie, HTTPException, status

from dashboard.backend.config import settings
from dashboard.backend.services import auth_crypto, auth_db

logger = logging.getLogger("dashboard.auth")

SESSION_COOKIE_NAME = "calypso_session"

_warned_no_users = False


def dev_mode_open() -> bool:
    """True (and logs once) when zero accounts exist — matches the old
    empty-DASHBOARD_API_KEY dev posture."""
    global _warned_no_users
    if auth_db.count_users(settings.dashboard_auth_db) == 0:
        if not _warned_no_users:
            logger.warning(
                "No dashboard accounts configured — the login gate is DISABLED. "
                "Run scripts/manage_dashboard_users.py add <username> to secure this dashboard."
            )
            _warned_no_users = True
        return True
    return False


def validate_and_refresh_session(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    token_hash = auth_crypto.hash_token(token)
    session = auth_db.get_session_with_user(settings.dashboard_auth_db, token_hash)
    if not session:
        return None
    now = time.time()
    idle_expiry = now + settings.session_idle_timeout_hours * 3600
    absolute_cap = session["session_created_at"] + settings.session_absolute_max_days * 86400
    new_expiry = min(idle_expiry, absolute_cap)
    if new_expiry > session["expires_at"]:
        auth_db.refresh_session_expiry(settings.dashboard_auth_db, token_hash, new_expiry)
    return session


async def require_session(
    calypso_session: Optional[str] = Cookie(default=None),
) -> None:
    """Reject the request unless a valid, non-expired session cookie is present.

    No-op when no accounts exist (see dev_mode_open).
    """
    if dev_mode_open():
        return
    session = validate_and_refresh_session(calypso_session)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )


async def get_current_username(
    calypso_session: Optional[str] = Cookie(default=None),
) -> str:
    """Like require_session, but also returns the username for display (/api/auth/me)."""
    if dev_mode_open():
        return "dev"
    session = validate_and_refresh_session(calypso_session)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return session["username"]
