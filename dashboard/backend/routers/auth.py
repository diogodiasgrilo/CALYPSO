"""Login / 2FA-enrollment / logout endpoints.

Mounted at /api/auth and NOT behind require_session (it IS the mechanism
that issues sessions). See dashboard/backend/auth.py for the session-cookie
guard used by every other router, and services/auth_db.py + auth_crypto.py
for the underlying storage/crypto.

Flow: POST /login (password) -> pending_token
      -> [POST /change-password if must_change_password]
      -> POST /setup-totp (only if 2FA not yet enabled, returns a QR code)
      -> POST /verify-totp (6-digit code or a recovery code) -> session cookie set
      GET /me -> {username} (also the frontend's "am I logged in" probe)
      POST /logout -> revokes the session, clears the cookie
"""

import logging
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from dashboard.backend.auth import SESSION_COOKIE_NAME, get_current_username
from dashboard.backend.config import settings
from dashboard.backend.services import auth_crypto, auth_db

logger = logging.getLogger("dashboard.routers.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_GENERIC_LOGIN_ERROR = "Invalid username or password."
_SESSION_EXPIRED_ERROR = "Login session expired — please start again."
# A real (but useless) bcrypt hash to compare against on unknown-username
# logins, so a missing account doesn't short-circuit faster than a
# wrong-password one (no timing oracle revealing whether a username exists).
# Generated once at import time so it's a valid bcrypt hash, not hand-rolled.
_DUMMY_HASH = auth_crypto.hash_password("anti-timing-oracle-placeholder")

# ── In-memory pending-login state (single-process uvicorn; ephemeral by design) ──
_PENDING_TTL_SECONDS = 300
_pending: dict[str, dict] = {}

# ── In-memory per-IP rate limit on the two credential-checking endpoints ──
_RATE_WINDOW_SECONDS = 300
_RATE_MAX_REQUESTS = 20
_rate_buckets: dict[str, list[float]] = {}


def _rate_limit(ip: str) -> None:
    now = time.time()
    bucket = _rate_buckets.setdefault(ip, [])
    while bucket and bucket[0] < now - _RATE_WINDOW_SECONDS:
        bucket.pop(0)
    if not bucket:
        # Drop other IPs' now-empty buckets opportunistically so a spoofed
        # (or just high-cardinality) IP churn can't grow this dict without
        # bound — cheap since it only runs on the empty-bucket path.
        for key in [k for k, v in _rate_buckets.items() if not v]:
            _rate_buckets.pop(key, None)
        _rate_buckets[ip] = bucket
    if len(bucket) >= _RATE_MAX_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts from this address — try again in a few minutes.",
        )
    bucket.append(now)


def _purge_expired_pending() -> None:
    now = time.time()
    for token in [t for t, e in _pending.items() if e["expires"] < now]:
        _pending.pop(token, None)


def _new_pending(user_id: int) -> str:
    _purge_expired_pending()
    token = secrets.token_urlsafe(24)
    _pending[token] = {"user_id": user_id, "expires": time.time() + _PENDING_TTL_SECONDS}
    return token


def _get_pending(token: str) -> Optional[dict]:
    entry = _pending.get(token)
    if not entry or entry["expires"] < time.time():
        _pending.pop(token, None)
        return None
    return entry


def _touch_pending(token: str) -> None:
    if token in _pending:
        _pending[token]["expires"] = time.time() + _PENDING_TTL_SECONDS


def _pop_pending(token: str) -> None:
    _pending.pop(token, None)


def _client_ip(request: Request) -> str:
    """Real client IP for rate limiting.

    uvicorn binds 127.0.0.1 only (dashboard.service) and is reachable
    exclusively through the nginx reverse proxy — so request.client.host is
    ALWAYS nginx's loopback peer address, not the visitor's. Without reading
    X-Forwarded-For, every real-world visitor shares one rate-limit bucket
    (found in review: trivially DoSable — 20 requests from anyone exhausts
    login for everyone).
    nginx MUST be configured to SET (overwrite), not append to,
    X-Forwarded-For for this to be safe — e.g. `proxy_set_header
    X-Forwarded-For $remote_addr;`, NOT nginx's default
    `$proxy_add_x_forwarded_for` (which appends to any client-supplied
    value, letting an attacker forge a fresh IP on every request and defeat
    the limiter entirely — this exact mistake was caught in round-2 review
    against the OLD tracked nginx-dashboard.conf). Taking the LAST CSV
    segment here is defense-in-depth for that same reason: even if a chain
    of trusted proxies each APPENDS correctly, the last hop's contribution
    is the one closest to being trustworthy, never the client-supplied first
    segment.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _lockout_seconds() -> float:
    return settings.login_lockout_minutes * 60


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    pending_token: str
    new_password: str


class PendingTokenRequest(BaseModel):
    pending_token: str


class VerifyTotpRequest(BaseModel):
    pending_token: str
    code: str


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    _rate_limit(_client_ip(request))
    username = body.username.strip()
    user = auth_db.get_user_by_username(settings.dashboard_auth_db, username)

    if not user or not user["is_active"]:
        # Dummy hash compare so an unknown/disabled username takes the same
        # time as a wrong password — no username-enumeration timing oracle.
        auth_crypto.verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)

    if auth_db.is_locked(user):
        # Same bcrypt cost as the real-password-check branch below, so a
        # locked account doesn't respond measurably faster than an unlocked
        # one — a minor timing oracle found in security review (low value
        # since an attacker only sees this after having locked the account
        # themselves, but cheap to close).
        auth_crypto.verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account temporarily locked after repeated failed attempts. Try again later.",
        )

    if not auth_crypto.verify_password(body.password, user["password_hash"]):
        auth_db.record_login_failure(
            settings.dashboard_auth_db, user["id"], settings.login_lockout_threshold, _lockout_seconds()
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)

    pending_token = _new_pending(user["id"])
    if user["must_change_password"]:
        return {"step": "change_password", "pending_token": pending_token}
    return {"step": "totp", "pending_token": pending_token, "totp_enabled": bool(user["totp_enabled"])}


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest):
    entry = _get_pending(body.pending_token)
    if not entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_SESSION_EXPIRED_ERROR)
    user = auth_db.get_user_by_id(settings.dashboard_auth_db, entry["user_id"])
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_SESSION_EXPIRED_ERROR)

    ok, message = auth_crypto.password_meets_policy(body.new_password, user["username"])
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    auth_db.update_password(
        settings.dashboard_auth_db, user["id"], auth_crypto.hash_password(body.new_password)
    )
    _touch_pending(body.pending_token)
    return {
        "step": "totp",
        "pending_token": body.pending_token,
        "totp_enabled": bool(user["totp_enabled"]),
    }


@router.post("/setup-totp")
async def setup_totp(body: PendingTokenRequest):
    entry = _get_pending(body.pending_token)
    if not entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_SESSION_EXPIRED_ERROR)
    user = auth_db.get_user_by_id(settings.dashboard_auth_db, entry["user_id"])
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_SESSION_EXPIRED_ERROR)
    if user["must_change_password"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Change your password first.")
    if user["totp_enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Two-factor auth is already enabled."
        )

    secret = auth_crypto.generate_totp_secret()
    auth_db.set_totp_secret(settings.dashboard_auth_db, user["id"], secret)
    _touch_pending(body.pending_token)
    return {
        "pending_token": body.pending_token,
        "secret": secret,
        "qr_data_uri": auth_crypto.totp_provisioning_qr_data_uri(secret, user["username"]),
    }


@router.post("/verify-totp")
async def verify_totp(body: VerifyTotpRequest, request: Request, response: Response):
    _rate_limit(_client_ip(request))
    entry = _get_pending(body.pending_token)
    if not entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_SESSION_EXPIRED_ERROR)
    user = auth_db.get_user_by_id(settings.dashboard_auth_db, entry["user_id"])
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_SESSION_EXPIRED_ERROR)
    if auth_db.is_locked(user):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account temporarily locked after repeated failed attempts. Try again later.",
        )
    if user["must_change_password"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Change your password first.")

    first_enrollment = not user["totp_enabled"]
    if first_enrollment and not user["totp_secret"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Call /setup-totp first.")

    ok = auth_crypto.verify_totp_code(user["totp_secret"], body.code)
    recovery_used = False
    if not ok and not first_enrollment and user["recovery_codes_hash"]:
        matched, updated_hashes = auth_crypto.consume_recovery_code(
            user["recovery_codes_hash"], body.code
        )
        if matched:
            ok = True
            recovery_used = True
            auth_db.set_recovery_codes(settings.dashboard_auth_db, user["id"], updated_hashes)

    if not ok:
        auth_db.record_login_failure(
            settings.dashboard_auth_db, user["id"], settings.login_lockout_threshold, _lockout_seconds()
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid code.")

    recovery_codes_out = None
    if first_enrollment:
        codes = auth_crypto.generate_recovery_codes()
        auth_db.enable_totp(settings.dashboard_auth_db, user["id"], auth_crypto.hash_recovery_codes(codes))
        recovery_codes_out = codes

    auth_db.record_login_success(settings.dashboard_auth_db, user["id"])
    _pop_pending(body.pending_token)

    token = auth_crypto.generate_session_token()
    now = time.time()
    idle_expiry = now + settings.session_idle_timeout_hours * 3600
    auth_db.create_session(settings.dashboard_auth_db, auth_crypto.hash_token(token), user["id"], idle_expiry)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=int(settings.session_absolute_max_days * 86400),
        path="/",
    )

    result = {"ok": True, "username": user["username"]}
    if recovery_codes_out:
        result["recovery_codes"] = recovery_codes_out
    if recovery_used:
        result["recovery_code_used"] = True
    return result


@router.post("/logout")
async def logout(response: Response, calypso_session: Optional[str] = Cookie(default=None)):
    if calypso_session:
        auth_db.revoke_session(settings.dashboard_auth_db, auth_crypto.hash_token(calypso_session))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(get_current_username)):
    return {"username": username}
