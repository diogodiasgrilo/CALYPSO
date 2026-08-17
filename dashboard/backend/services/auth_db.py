"""Read-write store for dashboard user accounts + sessions.

This is the ONE dashboard-owned SQLite database that is opened read-write —
everything else in dashboard/backend/services/*.py opens HYDRA's own data
(backtesting.db, dc_calendar.db, hydra_state.json) strictly read-only
(PRAGMA query_only=TRUE). This file is intentionally separate and never
touches bot data: it only stores dashboard login accounts and sessions.

Used by both the FastAPI backend (routers/auth.py) and the operator CLI
(scripts/manage_dashboard_users.py), so it must not depend on any FastAPI
types. Kept as plain sqlite3 (no ORM), matching the rest of the codebase.
"""

import sqlite3
import time
from pathlib import Path
from typing import Optional

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    totp_secret TEXT,
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    recovery_codes_hash TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until REAL,
    created_at REAL NOT NULL,
    last_login_at REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    """Idempotent schema bootstrap. Safe to call on every process start."""
    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


# ── Users ──────────────────────────────────────────────────────────────

def count_users(db_path: Path) -> int:
    conn = _connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def create_user(db_path: Path, username: str, password_hash: str) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, must_change_password, "
            "is_active, created_at) VALUES (?, ?, 1, 1, ?)",
            (username, password_hash, time.time()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_user_by_username(db_path: Path, username: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(db_path: Path, user_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_users(db_path: Path) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT username, is_active, totp_enabled, failed_login_count, "
            "locked_until, created_at, last_login_at FROM users ORDER BY username"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_active(db_path: Path, username: str, active: bool) -> bool:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE users SET is_active = ? WHERE username = ?",
            (1 if active else 0, username),
        )
        conn.commit()
        if cur.rowcount and not active:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE sessions SET revoked = 1 WHERE user_id = ?", (row["id"],)
                )
                conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def update_password(db_path: Path, user_id: int, password_hash: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (password_hash, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def admin_reset_password(db_path: Path, username: str, temp_password_hash: str) -> bool:
    """Operator-forced password reset (lost/compromised password) — sets a
    new temp password, forces a change on next login, and revokes existing
    sessions (same conservative default as set_active(False)/reset_totp).
    """
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE username = ?",
            (temp_password_hash, username),
        )
        if cur.rowcount:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE sessions SET revoked = 1 WHERE user_id = ?", (row["id"],)
                )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def reset_totp(db_path: Path, username: str) -> bool:
    """Clears 2FA enrollment AND revokes existing sessions.

    Session revocation matters because the documented trigger for this
    (lost/stolen phone, suspected compromise) means any live session cookie
    should not be trusted to keep working either — found in security review
    that leaving sessions alive here (unlike set_active(False)) let an
    attacker holding a stolen session cookie keep full read access for up to
    the idle/absolute session lifetime even after 2FA was reset.
    """
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE users SET totp_secret = NULL, totp_enabled = 0, "
            "recovery_codes_hash = NULL WHERE username = ?",
            (username,),
        )
        if cur.rowcount:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE sessions SET revoked = 1 WHERE user_id = ?", (row["id"],)
                )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def set_totp_secret(db_path: Path, user_id: int, secret: str) -> None:
    """Stage a (not-yet-enabled) TOTP secret. Overwrites any prior staged secret."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET totp_secret = ? WHERE id = ?", (secret, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def enable_totp(db_path: Path, user_id: int, recovery_codes_hash_json: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET totp_enabled = 1, recovery_codes_hash = ? WHERE id = ?",
            (recovery_codes_hash_json, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_recovery_codes(db_path: Path, user_id: int, recovery_codes_hash_json: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET recovery_codes_hash = ? WHERE id = ?",
            (recovery_codes_hash_json, user_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Login attempt bookkeeping ─────────────────────────────────────────

def record_login_failure(db_path: Path, user_id: int, lockout_threshold: int,
                          lockout_seconds: float) -> None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT failed_login_count FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        count = (row["failed_login_count"] if row else 0) + 1
        locked_until = time.time() + lockout_seconds if count >= lockout_threshold else None
        conn.execute(
            "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
            (count, locked_until, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_login_success(db_path: Path, user_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET failed_login_count = 0, locked_until = NULL, "
            "last_login_at = ? WHERE id = ?",
            (time.time(), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def is_locked(user: dict) -> bool:
    locked_until = user.get("locked_until")
    return bool(locked_until and locked_until > time.time())


# ── Sessions ───────────────────────────────────────────────────────────

def create_session(db_path: Path, token_hash: str, user_id: int, expires_at: float) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, revoked) "
            "VALUES (?, ?, ?, ?, 0)",
            (token_hash, user_id, time.time(), expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_session_raw(db_path: Path, token_hash: str) -> Optional[dict]:
    """Like get_session_with_user, but returns a KNOWN token's row even if
    expired/revoked (no user join, no filtering) — for diagnosing an auth
    rejection (ws/router.py) without duplicating this query's WHERE clause
    elsewhere. None means the token is unknown to this DB at all."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT expires_at, revoked FROM sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_session_with_user(db_path: Path, token_hash: str) -> Optional[dict]:
    """Returns the joined session+user row, or None if missing/revoked/expired."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT s.token_hash, s.user_id, s.created_at AS session_created_at, "
            "s.expires_at, s.revoked, u.username, u.is_active "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d["revoked"] or d["expires_at"] < time.time() or not d["is_active"]:
            return None
        return d
    finally:
        conn.close()


def refresh_session_expiry(db_path: Path, token_hash: str, new_expires_at: float) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token_hash = ? AND revoked = 0",
            (new_expires_at, token_hash),
        )
        conn.commit()
    finally:
        conn.close()


def revoke_session(db_path: Path, token_hash: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (token_hash,)
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_expired_sessions(db_path: Path) -> int:
    """Best-effort housekeeping; call opportunistically (e.g. on startup)."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM sessions WHERE expires_at < ? OR revoked = 1",
            (time.time() - 86400,),  # keep 1 day of revoked rows for forensics
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
