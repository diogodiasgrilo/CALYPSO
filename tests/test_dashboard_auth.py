"""Dashboard multi-user login + TOTP 2FA (2026-07-31 real-accounts work).

Replaces the old single-shared DASHBOARD_API_KEY. Covers the full backend
surface: password hashing, TOTP verification, recovery codes, DB-backed
sessions (issue/validate/refresh/revoke), account lockout, and the dev-mode
no-op posture when zero accounts exist. Uses FastAPI's TestClient against
the real app (real SQLite auth DB in a temp file) rather than mocking —
this is the security boundary for a soon-to-be-public dashboard, so it's
tested end to end, not just unit-by-unit.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")

import pyotp  # noqa: E402

from dashboard.backend.services import auth_crypto, auth_db  # noqa: E402


# ── auth_crypto: pure functions ─────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        h = auth_crypto.hash_password("a-reasonably-long-passphrase")
        assert auth_crypto.verify_password("a-reasonably-long-passphrase", h)

    def test_wrong_password_rejected(self):
        h = auth_crypto.hash_password("correct-passphrase-here")
        assert not auth_crypto.verify_password("wrong-passphrase-here", h)

    def test_verify_never_raises_on_garbage_hash(self):
        assert not auth_crypto.verify_password("anything", "not-a-bcrypt-hash")

    def test_verify_never_raises_on_empty_hash(self):
        assert not auth_crypto.verify_password("anything", "")


class TestPasswordPolicy:
    def test_rejects_short_password(self):
        ok, msg = auth_crypto.password_meets_policy("short1")
        assert not ok and msg

    def test_rejects_all_digits(self):
        ok, _ = auth_crypto.password_meets_policy("123456789012")
        assert not ok

    def test_rejects_password_equal_to_username(self):
        ok, _ = auth_crypto.password_meets_policy("MyUserName123", "myusername123")
        assert not ok

    def test_accepts_reasonable_password(self):
        ok, msg = auth_crypto.password_meets_policy("correct-horse-battery-staple")
        assert ok and msg == ""


class TestTotp:
    def test_valid_code_verifies(self):
        secret = auth_crypto.generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert auth_crypto.verify_totp_code(secret, code)

    def test_wrong_code_rejected(self):
        secret = auth_crypto.generate_totp_secret()
        assert not auth_crypto.verify_totp_code(secret, "000000")

    def test_empty_code_rejected(self):
        secret = auth_crypto.generate_totp_secret()
        assert not auth_crypto.verify_totp_code(secret, "")

    def test_provisioning_qr_is_a_data_uri(self):
        secret = auth_crypto.generate_totp_secret()
        uri = auth_crypto.totp_provisioning_qr_data_uri(secret, "diogo")
        assert uri.startswith("data:image/png;base64,")


class TestRecoveryCodes:
    def test_generates_ten_unique_codes(self):
        codes = auth_crypto.generate_recovery_codes()
        assert len(codes) == 10
        assert len(set(codes)) == 10

    def test_consume_valid_code_removes_it(self):
        codes = auth_crypto.generate_recovery_codes(count=3)
        stored = auth_crypto.hash_recovery_codes(codes)
        matched, updated = auth_crypto.consume_recovery_code(stored, codes[0])
        assert matched
        # The consumed code no longer matches a second time (single-use).
        matched_again, _ = auth_crypto.consume_recovery_code(updated, codes[0])
        assert not matched_again
        # The other two still work.
        matched_other, _ = auth_crypto.consume_recovery_code(updated, codes[1])
        assert matched_other

    def test_consume_unknown_code_fails(self):
        codes = auth_crypto.generate_recovery_codes(count=2)
        stored = auth_crypto.hash_recovery_codes(codes)
        matched, _ = auth_crypto.consume_recovery_code(stored, "not-a-real-code")
        assert not matched

    def test_consume_against_empty_store_fails(self):
        matched, updated = auth_crypto.consume_recovery_code("", "anything")
        assert not matched
        assert updated == ""


class TestSessionTokens:
    def test_token_hash_is_deterministic(self):
        token = auth_crypto.generate_session_token()
        assert auth_crypto.hash_token(token) == auth_crypto.hash_token(token)

    def test_different_tokens_hash_differently(self):
        a = auth_crypto.generate_session_token()
        b = auth_crypto.generate_session_token()
        assert auth_crypto.hash_token(a) != auth_crypto.hash_token(b)


# ── auth_db: schema + CRUD against a real temp SQLite file ─────────────

@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "dashboard_auth.db"
    auth_db.init_db(p)
    return p


class TestAuthDb:
    def test_fresh_db_has_zero_users(self, db_path):
        assert auth_db.count_users(db_path) == 0

    def test_create_and_fetch_user(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", auth_crypto.hash_password("x" * 12))
        user = auth_db.get_user_by_id(db_path, uid)
        assert user["username"] == "diogo"
        assert user["must_change_password"] == 1
        assert user["totp_enabled"] == 0
        assert user["is_active"] == 1

    def test_username_unique(self, db_path):
        auth_db.create_user(db_path, "diogo", "h1")
        with pytest.raises(Exception):
            auth_db.create_user(db_path, "diogo", "h2")

    def test_get_missing_user_returns_none(self, db_path):
        assert auth_db.get_user_by_username(db_path, "nobody") is None

    def test_set_active_false_revokes_sessions(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "tokhash1", uid, time.time() + 3600)
        assert auth_db.get_session_with_user(db_path, "tokhash1") is not None
        auth_db.set_active(db_path, "diogo", False)
        assert auth_db.get_session_with_user(db_path, "tokhash1") is None

    def test_update_password_clears_must_change_flag(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.update_password(db_path, uid, "newhash")
        user = auth_db.get_user_by_id(db_path, uid)
        assert user["password_hash"] == "newhash"
        assert user["must_change_password"] == 0

    def test_totp_enroll_and_reset(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.set_totp_secret(db_path, uid, "SECRET123")
        auth_db.enable_totp(db_path, uid, "[]")
        user = auth_db.get_user_by_id(db_path, uid)
        assert user["totp_enabled"] == 1
        assert user["totp_secret"] == "SECRET123"

        auth_db.reset_totp(db_path, "diogo")
        user = auth_db.get_user_by_id(db_path, uid)
        assert user["totp_enabled"] == 0
        assert user["totp_secret"] is None

    def test_reset_totp_revokes_existing_sessions(self, db_path):
        # Security-review fix: a lost-phone reset shouldn't leave a stolen
        # session cookie still valid.
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "sesshash", uid, time.time() + 3600)
        assert auth_db.get_session_with_user(db_path, "sesshash") is not None

        auth_db.reset_totp(db_path, "diogo")
        assert auth_db.get_session_with_user(db_path, "sesshash") is None

    def test_admin_reset_password_forces_change_and_revokes_sessions(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", auth_crypto.hash_password("x" * 20))
        auth_db.update_password(db_path, uid, auth_crypto.hash_password("x" * 20))  # clear must_change
        auth_db.create_session(db_path, "sesshash", uid, time.time() + 3600)

        ok = auth_db.admin_reset_password(db_path, "diogo", auth_crypto.hash_password("temp-2"))
        assert ok
        user = auth_db.get_user_by_id(db_path, uid)
        assert user["must_change_password"] == 1
        assert auth_crypto.verify_password("temp-2", user["password_hash"])
        assert auth_db.get_session_with_user(db_path, "sesshash") is None

    def test_admin_reset_password_unknown_user_returns_false(self, db_path):
        assert not auth_db.admin_reset_password(db_path, "nobody", "hash")


class TestLockout:
    def test_locks_after_threshold(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        for _ in range(5):
            auth_db.record_login_failure(db_path, uid, lockout_threshold=5, lockout_seconds=900)
        user = auth_db.get_user_by_id(db_path, uid)
        assert auth_db.is_locked(user)

    def test_not_locked_below_threshold(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        for _ in range(4):
            auth_db.record_login_failure(db_path, uid, lockout_threshold=5, lockout_seconds=900)
        user = auth_db.get_user_by_id(db_path, uid)
        assert not auth_db.is_locked(user)

    def test_success_resets_counter_and_unlocks(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        for _ in range(5):
            auth_db.record_login_failure(db_path, uid, lockout_threshold=5, lockout_seconds=900)
        auth_db.record_login_success(db_path, uid)
        user = auth_db.get_user_by_id(db_path, uid)
        assert user["failed_login_count"] == 0
        assert not auth_db.is_locked(user)

    def test_lock_expires_after_window(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        for _ in range(5):
            # Negative lockout window = already expired by the time we check.
            auth_db.record_login_failure(db_path, uid, lockout_threshold=5, lockout_seconds=-1)
        user = auth_db.get_user_by_id(db_path, uid)
        assert not auth_db.is_locked(user)


class TestSessions:
    def test_create_validate_revoke(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "hash1", uid, time.time() + 3600)
        s = auth_db.get_session_with_user(db_path, "hash1")
        assert s is not None and s["username"] == "diogo"

        auth_db.revoke_session(db_path, "hash1")
        assert auth_db.get_session_with_user(db_path, "hash1") is None

    def test_expired_session_invalid(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "hash1", uid, time.time() - 10)
        assert auth_db.get_session_with_user(db_path, "hash1") is None

    def test_unknown_token_invalid(self, db_path):
        assert auth_db.get_session_with_user(db_path, "does-not-exist") is None

    def test_refresh_extends_expiry(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "hash1", uid, time.time() + 10)
        new_expiry = time.time() + 99999
        auth_db.refresh_session_expiry(db_path, "hash1", new_expiry)
        s = auth_db.get_session_with_user(db_path, "hash1")
        assert abs(s["expires_at"] - new_expiry) < 1

    def test_cleanup_removes_only_dead_sessions(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "alive", uid, time.time() + 3600)
        auth_db.create_session(db_path, "dead", uid, time.time() - 200000)
        removed = auth_db.cleanup_expired_sessions(db_path)
        assert removed == 1
        assert auth_db.get_session_with_user(db_path, "alive") is not None


class TestGetSessionRaw:
    """2026-08-17: get_session_raw() bypasses the expiry/revoked filter that
    get_session_with_user() applies — added to diagnose a live incident where
    a WS auth rejection gave zero visibility into WHY (unknown token vs a
    genuinely-known-but-expired/revoked one look identical from the outside
    otherwise)."""

    def test_unknown_token_is_none(self, db_path):
        assert auth_db.get_session_raw(db_path, "does-not-exist") is None

    def test_expired_token_still_returned(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "hash1", uid, time.time() - 10)
        # The filtered lookup correctly hides it...
        assert auth_db.get_session_with_user(db_path, "hash1") is None
        # ...but the raw lookup still finds it, with expired info intact.
        row = auth_db.get_session_raw(db_path, "hash1")
        assert row is not None
        assert row["expires_at"] < time.time()
        assert row["revoked"] == 0

    def test_revoked_token_still_returned(self, db_path):
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "hash1", uid, time.time() + 3600)
        auth_db.revoke_session(db_path, "hash1")
        assert auth_db.get_session_with_user(db_path, "hash1") is None
        row = auth_db.get_session_raw(db_path, "hash1")
        assert row is not None and row["revoked"] == 1

    def test_valid_token_returned_without_username(self, db_path):
        # No JOIN to users — confirms this is the "raw sessions row" contract,
        # not a drop-in replacement for get_session_with_user.
        uid = auth_db.create_user(db_path, "diogo", "h")
        auth_db.create_session(db_path, "hash1", uid, time.time() + 3600)
        row = auth_db.get_session_raw(db_path, "hash1")
        assert row is not None
        assert "username" not in row


class TestFailClosedStartup:
    def test_refuses_to_start_with_zero_accounts_when_required(self, tmp_path, monkeypatch):
        # Security-review fix: a cold-deploy race or a misconfigured DB path
        # must not silently serve the dashboard with the login gate open.
        from dashboard.backend import main as main_module
        from dashboard.backend.config import settings

        monkeypatch.setattr(settings, "dashboard_auth_db", tmp_path / "auth.db")
        monkeypatch.setattr(settings, "require_accounts_configured", True)

        from starlette.testclient import TestClient

        with pytest.raises(RuntimeError, match="Refusing to start"):
            with TestClient(main_module.app):
                pass

    def test_starts_fine_once_an_account_exists(self, tmp_path, monkeypatch):
        from dashboard.backend import main as main_module
        from dashboard.backend.config import settings

        db_path = tmp_path / "auth.db"
        monkeypatch.setattr(settings, "dashboard_auth_db", db_path)
        monkeypatch.setattr(settings, "require_accounts_configured", True)
        auth_db.init_db(db_path)
        auth_db.create_user(db_path, "diogo", auth_crypto.hash_password("x" * 20))

        from starlette.testclient import TestClient

        with TestClient(main_module.app) as client:
            assert client.get("/api/health").status_code == 200


# ── Full HTTP flow against the real FastAPI app ──────────────────────────

@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """A TestClient wired to a fresh temp auth DB, with lifespan triggered.

    `settings` is a true module-level singleton (every module does
    `from dashboard.backend.config import settings`, same object everywhere)
    — monkeypatching its attributes directly reaches every consumer without
    any import-reload gymnastics.
    """
    from dashboard.backend import main as main_module
    from dashboard.backend.config import settings
    from dashboard.backend.routers import auth as auth_router_module

    monkeypatch.setattr(settings, "dashboard_auth_db", tmp_path / "auth.db")
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    # Clear in-memory pending-login / rate-limit state so tests don't leak
    # into each other (module-level dicts, shared across the whole process).
    auth_router_module._pending.clear()
    auth_router_module._rate_buckets.clear()

    from starlette.testclient import TestClient

    with TestClient(main_module.app) as client:
        yield client, settings


class TestLoginFlowEndToEnd:
    def test_dev_mode_open_when_no_users(self, app_client):
        client, settings = app_client
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["username"] == "dev"
        r = client.get("/api/hydra/state")
        assert r.status_code == 200

    def test_gated_once_a_user_exists(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
        r = client.get("/api/auth/me")
        assert r.status_code == 401
        r = client.get("/api/hydra/state")
        assert r.status_code == 401

    def test_full_enrollment_and_login(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("temp-password-123"))

        r = client.post("/api/auth/login", json={"username": "diogo", "password": "temp-password-123"})
        assert r.status_code == 200
        assert r.json()["step"] == "change_password"
        pending = r.json()["pending_token"]

        r = client.post(
            "/api/auth/change-password",
            json={"pending_token": pending, "new_password": "a-brand-new-strong-password"},
        )
        assert r.status_code == 200
        assert r.json()["step"] == "totp"
        assert r.json()["totp_enabled"] is False

        r = client.post("/api/auth/setup-totp", json={"pending_token": pending})
        assert r.status_code == 200
        secret = r.json()["secret"]

        r = client.post(
            "/api/auth/verify-totp", json={"pending_token": pending, "code": pyotp.TOTP(secret).now()}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert len(body["recovery_codes"]) == 10
        assert "calypso_session" in client.cookies

        r = client.get("/api/auth/me")
        assert r.status_code == 200 and r.json()["username"] == "diogo"
        r = client.get("/api/hydra/state")
        assert r.status_code == 200

        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_wrong_password_rejected_generic_message(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("correct-password-here"))
        r = client.post("/api/auth/login", json={"username": "diogo", "password": "wrong"})
        assert r.status_code == 401

    def test_unknown_username_same_error_as_wrong_password(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("correct-password-here"))
        r1 = client.post("/api/auth/login", json={"username": "nobody", "password": "irrelevant"})
        r2 = client.post("/api/auth/login", json={"username": "diogo", "password": "wrong"})
        assert r1.status_code == r2.status_code == 401
        assert r1.json()["detail"] == r2.json()["detail"]

    def test_account_locks_after_repeated_failures(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("correct-password-here"))
        for _ in range(settings.login_lockout_threshold):
            client.post("/api/auth/login", json={"username": "diogo", "password": "wrong"})
        r = client.post("/api/auth/login", json={"username": "diogo", "password": "correct-password-here"})
        assert r.status_code == 423

    def test_invalid_totp_code_rejected(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        uid = adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
        adb.update_password(settings.dashboard_auth_db, uid, ac.hash_password("x" * 20))
        adb.set_totp_secret(settings.dashboard_auth_db, uid, "AAAAAAAAAAAAAAAA")
        adb.enable_totp(settings.dashboard_auth_db, uid, ac.hash_recovery_codes(["a", "b"]))

        r = client.post("/api/auth/login", json={"username": "diogo", "password": "x" * 20})
        pending = r.json()["pending_token"]
        r = client.post("/api/auth/verify-totp", json={"pending_token": pending, "code": "000000"})
        assert r.status_code == 401

    def test_recovery_code_consumes_and_logs_in(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        uid = adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
        adb.update_password(settings.dashboard_auth_db, uid, ac.hash_password("x" * 20))
        adb.set_totp_secret(settings.dashboard_auth_db, uid, "AAAAAAAAAAAAAAAA")
        codes = ["recov-code1", "recov-code2"]
        adb.enable_totp(settings.dashboard_auth_db, uid, ac.hash_recovery_codes(codes))

        r = client.post("/api/auth/login", json={"username": "diogo", "password": "x" * 20})
        pending = r.json()["pending_token"]
        r = client.post("/api/auth/verify-totp", json={"pending_token": pending, "code": codes[0]})
        assert r.status_code == 200
        assert r.json().get("recovery_code_used") is True

        # Same recovery code can't be reused.
        client.post("/api/auth/logout")
        r = client.post("/api/auth/login", json={"username": "diogo", "password": "x" * 20})
        pending2 = r.json()["pending_token"]
        r = client.post("/api/auth/verify-totp", json={"pending_token": pending2, "code": codes[0]})
        assert r.status_code == 401

    def test_disabled_account_cannot_login(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
        adb.set_active(settings.dashboard_auth_db, "diogo", False)
        r = client.post("/api/auth/login", json={"username": "diogo", "password": "x" * 20})
        assert r.status_code == 401

    def test_disabling_account_kills_existing_session(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        uid = adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
        adb.update_password(settings.dashboard_auth_db, uid, ac.hash_password("x" * 20))
        adb.set_totp_secret(settings.dashboard_auth_db, uid, "AAAAAAAAAAAAAAAA")
        adb.enable_totp(settings.dashboard_auth_db, uid, "[]")

        r = client.post("/api/auth/login", json={"username": "diogo", "password": "x" * 20})
        pending = r.json()["pending_token"]
        client.post(
            "/api/auth/verify-totp",
            json={"pending_token": pending, "code": pyotp.TOTP("AAAAAAAAAAAAAAAA").now()},
        )
        assert client.get("/api/auth/me").status_code == 200

        adb.set_active(settings.dashboard_auth_db, "diogo", False)
        assert client.get("/api/auth/me").status_code == 401

    def test_change_password_rejects_weak_password(self, app_client):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("temp-password-123"))
        r = client.post("/api/auth/login", json={"username": "diogo", "password": "temp-password-123"})
        pending = r.json()["pending_token"]
        r = client.post("/api/auth/change-password", json={"pending_token": pending, "new_password": "short"})
        assert r.status_code == 400

    def test_unknown_pending_token_rejected(self, app_client):
        client, settings = app_client
        r = client.post(
            "/api/auth/change-password", json={"pending_token": "bogus", "new_password": "x" * 20}
        )
        assert r.status_code == 401
        r = client.post("/api/auth/setup-totp", json={"pending_token": "bogus"})
        assert r.status_code == 401
        r = client.post("/api/auth/verify-totp", json={"pending_token": "bogus", "code": "000000"})
        assert r.status_code == 401

    def test_rate_limit_keys_on_x_forwarded_for_not_shared_loopback(self, app_client):
        # Security-review fix: uvicorn sits behind nginx on 127.0.0.1, so
        # request.client.host is ALWAYS the proxy's loopback address unless
        # X-Forwarded-For is trusted — otherwise every real visitor shares one
        # bucket and one attacker can lock out everyone else's login.
        client, settings = app_client
        for _ in range(settings.login_lockout_threshold + 15):
            client.post(
                "/api/auth/login",
                json={"username": "nobody", "password": "wrong"},
                headers={"X-Forwarded-For": "203.0.113.1"},
            )
        exhausted = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "wrong"},
            headers={"X-Forwarded-For": "203.0.113.1"},
        )
        assert exhausted.status_code == 429

        # A different forwarded IP is a separate bucket — not blocked.
        still_ok = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "wrong"},
            headers={"X-Forwarded-For": "203.0.113.2"},
        )
        assert still_ok.status_code == 401

    def test_rate_limit_uses_last_forwarded_hop_not_client_spoofable_first(self, app_client):
        # Round-2 review: nginx's default $proxy_add_x_forwarded_for APPENDS
        # to any client-supplied header rather than replacing it, so the
        # LAST segment is the one nginx itself appended (trustworthy); the
        # FIRST segment is attacker-controlled. Taking the first would let an
        # attacker forge a new fake leading IP on every request and bypass
        # the limiter entirely — this pins the last-segment behavior.
        client, settings = app_client
        for _ in range(settings.login_lockout_threshold + 15):
            client.post(
                "/api/auth/login",
                json={"username": "nobody", "password": "wrong"},
                # Attacker prepends a fresh fake IP each time; nginx would
                # append the real one last — simulated here as a fixed tail.
                headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.9"},
            )
        exhausted = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "wrong"},
            headers={"X-Forwarded-For": "9.9.9.9, 203.0.113.9"},
        )
        assert exhausted.status_code == 429


class TestWebSocketAuthRejection:
    """2026-08-17: a real incident looked like a live dashboard-deploy
    regression (every WS reconnect for a logged-in user rejected 403,
    continuously, not self-healing) but was actually a browser tab open for
    5 days, retrying forever with a session that had simply hit its idle
    timeout hours earlier — a genuinely dead session, correctly rejected.
    The real gap: ws/router.py gave no diagnostic signal for WHY, and the
    frontend retried an already-and-forever-doomed connection indefinitely
    instead of ever sending the user back to a login screen. This pins both
    the diagnostic (auth_db.get_session_raw + _diagnose_ws_auth_failure) and
    the close-code contract the frontend fix (useWebSocket.ts) depends on."""

    def test_expired_session_closes_with_4001_not_generic_reject(self, app_client, caplog):
        import time as time_mod

        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        from starlette.websockets import WebSocketDisconnect

        uid = adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
        token = ac.generate_session_token()
        # Expired 10 hours ago — exactly the shape of the real incident.
        adb.create_session(settings.dashboard_auth_db, ac.hash_token(token), uid, time_mod.time() - 36000)
        client.cookies.set("calypso_session", token)

        with caplog.at_level("WARNING", logger="dashboard.ws_router"):
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/dashboard"):
                    pass
        assert exc_info.value.code == 4001

        # The diagnostic log must actually distinguish "known but expired"
        # from "unknown token" — this is the exact signal that resolved the
        # incident (it wasn't a code regression, it was a 5-day-old cookie).
        rejected_logs = [r.message for r in caplog.records if "WS auth rejected" in r.message]
        assert rejected_logs, "expected a WS auth rejected log line"
        assert "known_token" in rejected_logs[0]
        assert "expired=True" in rejected_logs[0]
        # Never leak the raw bearer token into logs — only a hash prefix.
        assert token not in rejected_logs[0]

    def test_no_cookie_at_all_distinguished_from_unknown_token(self, app_client, caplog):
        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac
        from starlette.websockets import WebSocketDisconnect

        adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
        client.cookies.clear()

        with caplog.at_level("WARNING", logger="dashboard.ws_router"):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws/dashboard"):
                    pass

        rejected_logs = [r.message for r in caplog.records if "WS auth rejected" in r.message]
        assert rejected_logs and "no_cookie_sent" in rejected_logs[0]

    def test_valid_session_connects_and_logs_nothing(self, app_client, caplog):
        import time as time_mod

        client, settings = app_client
        from dashboard.backend.services import auth_db as adb, auth_crypto as ac

        uid = adb.create_user(settings.dashboard_auth_db, "diogo", ac.hash_password("x" * 20))
        token = ac.generate_session_token()
        adb.create_session(settings.dashboard_auth_db, ac.hash_token(token), uid, time_mod.time() + 3600)
        client.cookies.set("calypso_session", token)

        with caplog.at_level("WARNING", logger="dashboard.ws_router"):
            with client.websocket_connect("/ws/dashboard") as ws:
                snapshot = ws.receive_json()
                assert snapshot["type"] == "snapshot"

        assert not [r for r in caplog.records if "WS auth rejected" in r.message]
