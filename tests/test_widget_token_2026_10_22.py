"""The iOS widget has been unauthenticated — and therefore dead — since 2026-08-03.

The 2026-08-03 security migration replaced the single shared DASHBOARD_API_KEY
with per-account sessions (username + bcrypt + TOTP). The Scriptable widget
cannot perform that login, and nothing replaced its credential, so it kept
sending `?api_key=...` to an endpoint that had stopped reading it:

    GET /api/widget?api_key=...  ->  {"detail":"Not authenticated"}

Fix: a scoped widget token. Three properties, each pinned below.

**Scoped structurally, not conditionally.** The token lives in its own table
and `require_widget_token` is attached to the widget router alone, so a widget
token cannot reach /api/hydra, /api/variants or anything else. A boolean on
`sessions` plus an `if` at each call site would have been one forgotten check
away from a full-access credential.

**Read-only.** The widget router exposes no non-GET route, asserted here so
adding one is a test failure rather than a silent privilege escalation.

**Revocable instantly.** DB-backed, like sessions — revoke the token or disable
the user and the next request fails. No expiry: a home-screen widget must keep
working for months, which is exactly why its lifetime rules live apart from a
browser session's hours-long idle timeout.

Sent as a header, not a query parameter: nginx's combined log format writes the
full request line, so `?api_key=` persists the credential to disk in plaintext
on a public host on every poll. Scriptable sets headers in one line, so nothing
is traded away.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from dashboard.backend.services import auth_crypto, auth_db  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    from pathlib import Path
    p = Path(tmp_path) / "auth.db"
    auth_db.init_db(p)
    auth_db.create_user(p, "ddias", auth_crypto.hash_password("Xk9!qr2Lmz7v"))
    return p


def _issue(db, user="ddias", label="iphone"):
    token = auth_crypto.generate_session_token()
    uid = auth_db.get_user_by_username(db, user)["id"]
    auth_db.create_widget_token(db, auth_crypto.hash_token(token), uid, label)
    return token


class TestTheTokenStore:
    def test_a_valid_token_resolves_to_its_user(self, db):
        t = _issue(db)
        row = auth_db.get_widget_token_with_user(db, auth_crypto.hash_token(t))
        assert row and row["username"] == "ddias"

    def test_only_the_HASH_is_stored(self, db):
        """A stolen database must yield no usable token."""
        t = _issue(db)
        assert auth_db.list_widget_tokens(db)[0]["token_hash"] != t
        raw = open(db, "rb").read()
        assert t.encode() not in raw, "the plaintext token is on disk"

    def test_an_unknown_token_is_rejected(self, db):
        _issue(db)
        other = auth_crypto.hash_token(auth_crypto.generate_session_token())
        assert auth_db.get_widget_token_with_user(db, other) is None

    def test_revoking_takes_effect(self, db):
        t = _issue(db)
        h = auth_crypto.hash_token(t)
        assert auth_db.revoke_widget_token(db, h[:12]) == 1
        assert auth_db.get_widget_token_with_user(db, h) is None

    def test_revoking_an_unknown_prefix_reports_zero(self, db):
        """So the CLI can say 'no such token' instead of claiming success."""
        assert auth_db.revoke_widget_token(db, "deadbeefdead") == 0

    def test_disabling_the_USER_kills_their_token(self, db):
        """Revocation must not be the only lever — disabling an account has to
        cut every credential it owns, not just browser sessions."""
        t = _issue(db)
        auth_db.set_active(db, "ddias", False)
        assert auth_db.get_widget_token_with_user(db, auth_crypto.hash_token(t)) is None

    def test_a_widget_token_is_NOT_a_session(self, db):
        """The scoping, at the store level: the two tables are disjoint, so a
        widget token cannot be presented as a session cookie."""
        t = _issue(db)
        assert auth_db.get_session_with_user(db, auth_crypto.hash_token(t)) is None

    def test_a_session_is_NOT_a_widget_token(self, db):
        """And the converse, so the scoping is not one-directional."""
        import time
        st = auth_crypto.generate_session_token()
        uid = auth_db.get_user_by_username(db, "ddias")["id"]
        auth_db.create_session(db, auth_crypto.hash_token(st), uid, time.time() + 3600)
        assert auth_db.get_widget_token_with_user(db, auth_crypto.hash_token(st)) is None

    def test_last_used_is_recorded(self, db):
        t = _issue(db)
        h = auth_crypto.hash_token(t)
        assert auth_db.list_widget_tokens(db)[0]["last_used_at"] is None
        auth_db.touch_widget_token(db, h)
        assert auth_db.list_widget_tokens(db)[0]["last_used_at"] is not None

    def test_the_schema_bootstrap_is_idempotent(self, db):
        """init_db runs on every process start, so the new table has to be an
        auto-migration for the DB already live on the VM."""
        t = _issue(db)
        auth_db.init_db(db)
        auth_db.init_db(db)
        assert auth_db.get_widget_token_with_user(db, auth_crypto.hash_token(t))


class TestTheScopeIsStructural:
    def test_only_the_widget_router_uses_the_widget_dependency(self):
        src = open(os.path.join(ROOT, "dashboard/backend/main.py")).read()
        assert src.count("require_widget_token") == 2, (
            "expected exactly the import and ONE router wiring — a second "
            "router accepting widget tokens would widen the credential")
        i = src.index("Depends(require_widget_token)")
        line_start = src.rindex("app.include_router", 0, i)
        assert "widget.router" in src[line_start:i]

    def test_every_other_router_still_requires_a_full_session(self):
        src = open(os.path.join(ROOT, "dashboard/backend/main.py")).read()
        for r in ("hydra", "metrics", "market", "variants", "dc",
                  "strategies", "long_strangle"):
            assert f"app.include_router({r}.router, dependencies=_api_guard)" in src, r

    def test_the_widget_router_is_READ_ONLY(self):
        """No non-GET route, so the token cannot mutate anything."""
        src = open(os.path.join(ROOT, "dashboard/backend/routers/widget.py")).read()
        for verb in ("post", "put", "patch", "delete"):
            assert f"@router.{verb}" not in src, f"widget router gained a {verb} route"


class TestTheCredentialIsNotLoggedByNginx:
    def test_the_dependency_reads_a_HEADER_not_a_query_param(self):
        src = open(os.path.join(ROOT, "dashboard/backend/auth.py")).read()
        i = src.index("async def require_widget_token")
        body = src[i:]
        assert "Header(default=None)" in body
        assert "Query(" not in body, "a query param lands in the nginx access log"

    @staticmethod
    def _widget_code():
        """The widget's EXECUTABLE lines only. The file's comments discuss the
        old `?api_key=` shape on purpose, and a naive substring search over the
        whole file would match the explanation of the fix rather than the code —
        the same false-positive class as grepping source for a token and calling
        it proof the code runs."""
        js = open(os.path.join(ROOT, "dashboard/scriptable/HYDRA_Widget.js")).read()
        return "\n".join(l for l in js.splitlines()
                          if not l.lstrip().startswith("//"))

    def test_the_widget_sends_the_header_and_no_longer_a_query_param(self):
        code = self._widget_code()
        assert '"X-Widget-Token": WIDGET_TOKEN' in code
        assert "?api_key=" not in code, "the dead credential shape is still being sent"
        assert "const API_KEY" not in code, "the retired constant is still declared"

    def test_negative_control_the_old_widget_DID_send_a_query_param(self):
        """Proves the assertion above is not vacuous by reproducing the shape
        that was live until today."""
        old = 'const url = `${DASHBOARD_URL}/api/widget${API_KEY ? `?api_key=${API_KEY}` : ""}`;'
        assert "?api_key=" in old
        assert old not in self._widget_code()


class TestEndToEndOverHTTP:
    """The store tests prove the lookup. They do NOT prove the endpoint accepts
    the token, which is the thing that was broken — so drive it over real HTTP
    against a live app, with accounts present so the gate is actually armed.
    """

    @pytest.fixture()
    def client(self, db, monkeypatch):
        from fastapi.testclient import TestClient
        from dashboard.backend.config import settings
        monkeypatch.setattr(settings, "dashboard_auth_db", db, raising=False)
        import dashboard.backend.auth as auth_mod
        monkeypatch.setattr(auth_mod.settings, "dashboard_auth_db", db, raising=False)
        from dashboard.backend.main import app
        return TestClient(app)

    def test_no_credential_is_rejected(self, client):
        assert client.get("/api/widget").status_code == 401

    def test_the_dead_query_param_is_still_rejected(self, client, db):
        """The old shape must not start working by accident — that would put the
        credential back in the nginx access log."""
        t = _issue(db)
        assert client.get(f"/api/widget?api_key={t}").status_code == 401
        assert client.get(f"/api/widget?widget_token={t}").status_code == 401

    def test_a_valid_widget_token_in_the_HEADER_is_accepted(self, client, db):
        t = _issue(db)
        r = client.get("/api/widget", headers={"X-Widget-Token": t})
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), dict)

    def test_a_revoked_token_stops_working_immediately(self, client, db):
        t = _issue(db)
        assert client.get("/api/widget", headers={"X-Widget-Token": t}).status_code == 200
        auth_db.revoke_widget_token(db, auth_crypto.hash_token(t)[:12])
        assert client.get("/api/widget", headers={"X-Widget-Token": t}).status_code == 401

    def test_disabling_the_user_stops_it_working_immediately(self, client, db):
        t = _issue(db)
        assert client.get("/api/widget", headers={"X-Widget-Token": t}).status_code == 200
        auth_db.set_active(db, "ddias", False)
        assert client.get("/api/widget", headers={"X-Widget-Token": t}).status_code == 401

    @pytest.mark.parametrize("path", [
        "/api/hydra/state", "/api/metrics/cumulative", "/api/variants/health",
        "/api/strategies/meta", "/api/dc/status", "/api/long-strangle/status",
        "/api/agents/status",
    ])
    def test_the_token_opens_NOTHING_else(self, client, db, path):
        """The property that matters most. A credential that works everywhere
        is not a widget token, it is a password."""
        t = _issue(db)
        r = client.get(path, headers={"X-Widget-Token": t})
        assert r.status_code == 401, f"{path} accepted a widget token"
