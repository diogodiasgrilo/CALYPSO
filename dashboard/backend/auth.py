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

from fastapi import Cookie, Header, HTTPException, status

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


# ── widget token (scoped, read-only, revocable) ──────────────────────────────

WIDGET_TOKEN_HEADER = "X-Widget-Token"


async def require_widget_token(
    calypso_session: Optional[str] = Cookie(default=None),
    x_widget_token: Optional[str] = Header(default=None),
) -> None:
    """Gate for /api/widget ONLY: a valid widget token OR a normal session.

    Why a second credential at all. The iOS Scriptable widget and Siri
    shortcuts cannot do a username + password + TOTP login, so when the
    2026-08-03 account migration retired the shared DASHBOARD_API_KEY the
    widget simply died — it has been sending a dead `?api_key=` and receiving
    {"detail":"Not authenticated"} ever since.

    Why a HEADER and not a query parameter. The widget's old shape was
    `?api_key=...`, and nginx's combined log format writes the full request
    line — so every poll would persist the credential to disk in plaintext, on
    a public host, forever. Scriptable sets request headers in one line
    (`req.headers = {...}`), so there is no convenience being traded away.

    Why a session is ALSO accepted. The browser dashboard and the e2e harness
    read this endpoint too; refusing a logged-in operator from their own
    widget JSON would be a regression with no security benefit.

    Scope is structural, not conditional: this dependency is attached to the
    widget router alone, and the token lives in its own table, so a widget
    token cannot reach /api/hydra, /api/variants or anything else even if this
    function were later copied carelessly. The endpoint itself is read-only —
    there is no non-GET route on the widget router.
    """
    if dev_mode_open():
        return

    if x_widget_token:
        token_hash = auth_crypto.hash_token(x_widget_token)
        row = auth_db.get_widget_token_with_user(
            settings.dashboard_auth_db, token_hash)
        if row:
            try:
                auth_db.touch_widget_token(settings.dashboard_auth_db, token_hash)
            except Exception:  # noqa: BLE001
                pass  # last-used telemetry must never fail a valid request
            return

    if validate_and_refresh_session(calypso_session):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
