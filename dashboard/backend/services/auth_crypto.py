"""Password hashing, TOTP, recovery codes, and session-token helpers.

Pure functions, no FastAPI/DB imports — kept usable from both the backend
routers and the operator CLI (scripts/manage_dashboard_users.py).
"""

import base64
import hashlib
import io
import json
import secrets

import bcrypt
import pyotp
import qrcode

TOTP_ISSUER = "CALYPSO HYDRA Dashboard"


# ── Passwords ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_temp_password() -> str:
    """Strong random temp password for admin-created accounts (must be changed on first login)."""
    return secrets.token_urlsafe(18)


def password_meets_policy(password: str, username: str = "") -> tuple[bool, str]:
    """Minimal, deliberate policy: length + not-trivial. No external wordlist checks."""
    if len(password) < 12:
        return False, "Password must be at least 12 characters."
    if password.isdigit():
        return False, "Password cannot be numbers only."
    if username and password.lower() == username.lower():
        return False, "Password cannot be the same as the username."
    return True, ""


# ── Session tokens ─────────────────────────────────────────────────────

def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Session tokens are high-entropy (256 bits) — SHA-256 is sufficient
    (unlike passwords, there's no feasible offline brute force to slow down)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── TOTP (2FA) ─────────────────────────────────────────────────────────

def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_qr_data_uri(secret: str, username: str) -> str:
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def verify_totp_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


# ── Recovery codes ─────────────────────────────────────────────────────

def generate_recovery_codes(count: int = 10) -> list[str]:
    return [f"{secrets.token_hex(5)[:5]}-{secrets.token_hex(5)[5:]}" for _ in range(count)]


def hash_recovery_codes(codes: list[str]) -> str:
    return json.dumps([hashlib.sha256(c.encode("utf-8")).hexdigest() for c in codes])


def consume_recovery_code(recovery_codes_hash_json: str, code: str) -> tuple[bool, str]:
    """Returns (matched, updated_json_with_code_removed)."""
    if not recovery_codes_hash_json:
        return False, recovery_codes_hash_json
    hashes = json.loads(recovery_codes_hash_json)
    target = hashlib.sha256(code.strip().encode("utf-8")).hexdigest()
    if target in hashes:
        hashes.remove(target)
        return True, json.dumps(hashes)
    return False, recovery_codes_hash_json
