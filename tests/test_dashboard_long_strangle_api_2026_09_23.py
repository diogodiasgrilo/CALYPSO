"""Strategy H dashboard API — Playbook Step 8, the dashboard half.

`/api/long-strangle/status` + `/recent`. The endpoint exists as its own route
rather than as a row in `/api/variants` or `/api/dc` because **the P&L shapes
are not comparable**: the iron-condor renderers assume premium was COLLECTED
("expired worthless" is profit there and maximum loss here) and the calendar
renderers assume a net debit that is theta-POSITIVE. H is net debit AND
theta-negative — the only such strategy here.

The properties worth pinning:

* it is **read-only** against a database the trading loop writes;
* a **missing** database is `available: false`, never a 500 — H is not installed
  on the VM, so that is today's expected response and the page must render it;
* the payload is **debit-shaped**: no `total_credit`, no spread width, and a
  `max_possible_loss` that is a fact rather than an estimate;
* it surfaces the **peak alongside the exit**, which no other dashboard view
  needs and this one cannot do without.

The dashboard backend runs in its own venv. Skip cleanly where FastAPI isn't
installed; this runs in the dashboard env (and on the VM).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")

import pyotp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.config import settings  # noqa: E402
from dashboard.backend.services import auth_crypto, auth_db  # noqa: E402
from dashboard.backend.services.ls_reader import (  # noqa: E402
    read_ls_recent,
    read_ls_status,
)

DATE = "2026-09-23"


def _executable_strings(path: Path) -> list:
    """String literals a module actually EXECUTES, docstrings excluded.

    A plain grep over the source also hits the docstring, which names the very
    things it forbids in order to explain why they are absent. Forbidding the
    explanation would push the reasoning out of the file — the opposite of
    useful — so the check parses the AST instead. (Same fix as
    tests/test_ls_recorder_2026_09_23.py.)
    """
    import ast
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.add(d)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docs]


def _enroll_and_login(client, username="ls-tester", password="initial-temp-password-1"):
    from dashboard.backend.routers import auth as auth_router_module
    auth_router_module._rate_buckets.clear()
    auth_router_module._pending.clear()
    auth_db.init_db(settings.dashboard_auth_db)
    auth_db.create_user(settings.dashboard_auth_db, username,
                        auth_crypto.hash_password(password))
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    pending = r.json()["pending_token"]
    r = client.post("/api/auth/change-password",
                    json={"pending_token": pending,
                          "new_password": "a-brand-new-strong-password-2"})
    pending = r.json()["pending_token"]
    r = client.post("/api/auth/setup-totp", json={"pending_token": pending})
    secret = r.json()["secret"]
    r = client.post("/api/auth/verify-totp",
                    json={"pending_token": pending, "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text


def _seed(path: Path) -> None:
    """A day with one closed position that peaked above where it exited, one
    still open, and one declined entry."""
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE ls_entries (
            date TEXT, entry_number INTEGER, entry_time TEXT,
            spx_at_entry REAL, vix_at_entry REAL, expiry TEXT,
            call_strike REAL, put_strike REAL,
            call_debit REAL, put_debit REAL, total_debit REAL, contracts INTEGER,
            long_call_uic INTEGER, long_put_uic INTEGER,
            em_source TEXT, expected_move REAL,
            call_premium_mid REAL, put_premium_mid REAL, skew_gap_pct REAL,
            PRIMARY KEY (date, entry_number));
        CREATE TABLE ls_exits (
            date TEXT, entry_number INTEGER, exit_time TEXT, exit_reason TEXT,
            call_exit_value REAL, put_exit_value REAL, total_exit_value REAL,
            realized_pnl REAL, pnl_pct_of_debit REAL, commissions REAL,
            spx_at_exit REAL, minutes_held REAL,
            PRIMARY KEY (date, entry_number));
        CREATE TABLE ls_snapshots (
            date TEXT, entry_number INTEGER, timestamp TEXT, spx REAL,
            call_value REAL, put_value REAL, total_value REAL,
            unrealized_pnl REAL, pnl_pct_of_debit REAL);
        CREATE TABLE ls_skipped (
            date TEXT, entry_number INTEGER, skip_time TEXT, skip_reason TEXT,
            spx REAL, vix REAL, proposed_call_strike REAL, proposed_put_strike REAL,
            proposed_debit REAL, em_source TEXT, expected_move REAL,
            iv_percentile REAL, skew_gap_pct REAL);
        """
    )
    con.executemany(
        "INSERT INTO ls_entries (date, entry_number, entry_time, spx_at_entry, "
        "vix_at_entry, call_strike, put_strike, call_debit, put_debit, total_debit, "
        "contracts, em_source, expected_move, skew_gap_pct) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (DATE, 1, "2026-09-23T09:45:00", 7765.0, 14.5, 7785.0, 7745.0,
             200.0, 290.0, 490.0, 2, "straddle", 22.0, 16.7),
            (DATE, 2, "2026-09-23T10:15:00", 7770.0, 14.6, 7795.0, 7750.0,
             180.0, 220.0, 400.0, 2, "straddle", 24.0, 10.0),
        ],
    )
    con.execute(
        "INSERT INTO ls_exits (date, entry_number, exit_time, exit_reason, "
        "realized_pnl, pnl_pct_of_debit, commissions, spx_at_exit, minutes_held) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (DATE, 1, "10:27", "profit_target_50", 250.0, 51.02, 4.6, 7800.0, 42.0),
    )
    con.executemany(
        "INSERT INTO ls_snapshots (date, entry_number, timestamp, spx, total_value, "
        "unrealized_pnl, pnl_pct_of_debit) VALUES (?,?,?,?,?,?,?)",
        [
            (DATE, 1, "10:00", 7765.0, 490.0, 0.0, 0.0),
            (DATE, 1, "10:10", 7790.0, 890.0, 400.0, 81.63),   # the peak
            (DATE, 1, "10:27", 7800.0, 740.0, 250.0, 51.02),   # where it exited
            (DATE, 2, "10:30", 7772.0, 430.0, 30.0, 7.50),     # still open
        ],
    )
    con.execute(
        "INSERT INTO ls_skipped (date, entry_number, skip_time, skip_reason, spx, vix, "
        "proposed_call_strike, proposed_put_strike, proposed_debit, em_source, "
        "expected_move, skew_gap_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (DATE, 3, "10:45", "skew 61.0% > 35.0% tolerance", 7775.0, 14.7,
         7800.0, 7755.0, 300.0, "straddle", 20.0, 61.0),
    )
    con.commit()
    con.close()


@pytest.fixture
def seeded(tmp_path):
    vd = tmp_path / "variant_h"
    vd.mkdir()
    _seed(vd / "long_strangle.db")
    return vd


@pytest.fixture
def client(tmp_path, seeded, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_auth_db", tmp_path / "dashboard_auth.db",
                        raising=False)
    monkeypatch.setattr(settings, "session_cookie_secure", False, raising=False)
    monkeypatch.setattr(settings, "variant_h_state_file", seeded / "hydra_state.json",
                        raising=False)
    from dashboard.backend.main import app
    with TestClient(app) as c:
        _enroll_and_login(c)
        yield c


# ======================================================================
# The reader
# ======================================================================

class TestTheReader:
    def test_a_missing_database_is_available_false_not_an_error(self, tmp_path):
        """H is dry-run-locked and NOT installed on the VM, so this is the
        expected production response today — the page must render it."""
        out = read_ls_status(str(tmp_path / "nope.db"))
        assert out["available"] is False
        assert "never recorded a row" in out["reason"]
        assert out["summary"]["open_count"] == 0

    def test_a_corrupt_database_degrades_rather_than_raising(self, tmp_path):
        p = tmp_path / "junk.db"
        p.write_bytes(b"not a database")
        assert read_ls_status(str(p))["open"] == []

    def test_it_defaults_to_the_most_recent_day_with_activity(self, seeded):
        """A dry-run variant may not have traded today. Showing the last day it
        did is more useful than showing nothing."""
        out = read_ls_status(str(seeded / "long_strangle.db"))
        assert out["date"] == DATE and out["available"] is True

    def test_open_and_closed_are_separated_by_the_presence_of_an_exit_row(self, seeded):
        out = read_ls_status(str(seeded / "long_strangle.db"), date=DATE)
        assert [r["entry_number"] for r in out["closed"]] == [1]
        assert [r["entry_number"] for r in out["open"]] == [2]

    def test_an_open_position_carries_its_live_mark(self, seeded):
        """No sidecar: ls_snapshots is written every tick, so the last row IS
        the live mark."""
        out = read_ls_status(str(seeded / "long_strangle.db"), date=DATE)
        assert out["open"][0]["last_mark"]["total_value"] == 430.0

    def test_the_peak_minus_exit_gap_is_computed(self, seeded):
        """Reached +81.6%, captured +51.0%. Invisible in realized P&L alone, and
        the number the source's 80%-win-rate claim actually turns on."""
        out = read_ls_status(str(seeded / "long_strangle.db"), date=DATE)
        assert out["closed"][0]["peak_minus_exit_pct"] == pytest.approx(30.61, abs=0.01)

    def test_max_possible_loss_is_the_debit_not_an_estimate(self, seeded):
        s = read_ls_status(str(seeded / "long_strangle.db"), date=DATE)["summary"]
        assert s["debit_deployed"] == pytest.approx(890.0)
        assert s["max_possible_loss"] == s["debit_deployed"]

    def test_declined_entries_come_through_with_their_counterfactual(self, seeded):
        out = read_ls_status(str(seeded / "long_strangle.db"), date=DATE)
        assert out["summary"]["skipped_count"] == 1
        assert out["skipped"][0]["proposed_debit"] == 300.0

    def test_recent_joins_each_exit_to_the_debit_it_risked(self, seeded):
        """A +$250 day means nothing without knowing whether $200 or $2,000 was
        at stake."""
        rows = read_ls_recent(str(seeded / "long_strangle.db"))
        assert rows[0]["realized_pnl"] == 250.0
        assert rows[0]["total_debit"] == 490.0
        assert rows[0]["em_source"] == "straddle"

    def test_the_reader_executes_no_write_statement(self):
        """Checked over EXECUTABLE string literals via the AST, not a grep: the
        module docstring names these verbs on purpose, to say they are absent."""
        live = [s.upper() for s in _executable_strings(
            Path(__file__).resolve().parents[1] / "dashboard" / "backend" /
            "services" / "ls_reader.py")]
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "CREATE TABLE"):
            assert not [x for x in live if verb in x], verb

    def test_the_reader_imports_no_bot_code(self):
        """CLAUDE.md: the dashboard must not import trading code. dc_reader keeps
        the same boundary by duplicating its SQL rather than importing
        dc_status."""
        src = (Path(__file__).resolve().parents[1] / "dashboard" / "backend" /
               "services" / "ls_reader.py").read_text()
        for banned in ("from bots", "import bots", "ib_client", "ibind"):
            assert banned not in src, banned


# ======================================================================
# The endpoints
# ======================================================================

class TestTheEndpoints:
    def test_status_serves_the_day(self, client):
        r = client.get("/api/long-strangle/status")
        assert r.status_code == 200
        body = r.json()
        assert body["strategy"] == "long_strangle"
        assert body["date"] == DATE
        assert body["summary"]["closed_count"] == 1

    def test_the_label_states_dry_run_up_front(self, client):
        """Per the playbook. A P&L figure that looks real must not be mistaken
        for one."""
        label = client.get("/api/long-strangle/status").json()["label"]
        assert "dry-run" in label and "places no real orders" in label

    def test_the_payload_is_debit_shaped_with_no_credit_keys(self, client):
        """The whole reason this is its own endpoint. A credit key here would
        invite an IC renderer to read a long strangle as a premium sale."""
        body = client.get("/api/long-strangle/status").json()
        flat = str(body).lower()
        for banned in ("total_credit", "call_spread_credit", "spread_width"):
            assert banned not in flat, banned
        assert "total_debit" in flat and "max_possible_loss" in flat

    def test_an_explicit_date_is_honoured(self, client):
        r = client.get("/api/long-strangle/status", params={"date": "2026-09-22"})
        assert r.status_code == 200 and r.json()["summary"]["open_count"] == 0

    def test_recent_returns_rows(self, client):
        rows = client.get("/api/long-strangle/recent").json()["rows"]
        assert len(rows) == 1 and rows[0]["exit_reason"] == "profit_target_50"

    def test_recent_bounds_its_limit(self, client):
        assert client.get("/api/long-strangle/recent", params={"limit": 0}).status_code == 422
        assert client.get("/api/long-strangle/recent", params={"limit": 500}).status_code == 422

    def test_the_endpoints_require_a_session(self, tmp_path, seeded, monkeypatch):
        """Behind the same _api_guard as every other REST router."""
        monkeypatch.setattr(settings, "dashboard_auth_db",
                            tmp_path / "guard_auth.db", raising=False)
        monkeypatch.setattr(settings, "session_cookie_secure", False, raising=False)
        monkeypatch.setattr(settings, "variant_h_state_file",
                            seeded / "hydra_state.json", raising=False)
        auth_db.init_db(settings.dashboard_auth_db)
        auth_db.create_user(settings.dashboard_auth_db, "someone",
                            auth_crypto.hash_password("initial-temp-password-1"))
        from dashboard.backend.main import app
        with TestClient(app) as anon:
            assert anon.get("/api/long-strangle/status").status_code in (401, 403)

    def test_a_missing_database_still_returns_200(self, tmp_path, monkeypatch):
        """The production case today. An error page for "not installed yet"
        would be worse than an empty one."""
        monkeypatch.setattr(settings, "dashboard_auth_db",
                            tmp_path / "a.db", raising=False)
        monkeypatch.setattr(settings, "session_cookie_secure", False, raising=False)
        empty = tmp_path / "empty_variant_h"
        empty.mkdir()
        monkeypatch.setattr(settings, "variant_h_state_file",
                            empty / "hydra_state.json", raising=False)
        from dashboard.backend.main import app
        with TestClient(app) as c:
            _enroll_and_login(c, username="ls-empty")
            r = c.get("/api/long-strangle/status")
            assert r.status_code == 200 and r.json()["available"] is False


class TestASkipOnlyDayIsStillData:
    """H's very first live session (2026-09-23) recorded ONE SKIP and no entry —
    and the page reported "no data".

    `_latest_date` read only `ls_entries`. For a MEASUREMENT strategy the
    declined entries ARE the data, and early on they are most of it: that skip
    carried the proposed strikes (7760/7720), the expected move (20.9pt from the
    straddle) and the reason — exactly the counterfactual the variant exists to
    collect, and exactly what the GEX work on variant B could never recover for
    its first 95 vetoes.
    """

    def _skip_only(self, tmp_path):
        import sqlite3
        p = tmp_path / "skiponly.db"
        con = sqlite3.connect(str(p))
        con.executescript(
            "CREATE TABLE ls_entries (date TEXT, entry_number INTEGER, "
            "  call_strike REAL, put_strike REAL, total_debit REAL);"
            "CREATE TABLE ls_exits (date TEXT, entry_number INTEGER);"
            "CREATE TABLE ls_snapshots (date TEXT, entry_number INTEGER, "
            "  timestamp TEXT, pnl_pct_of_debit REAL);"
            "CREATE TABLE ls_skipped (date TEXT, entry_number INTEGER, "
            "  skip_time TEXT, skip_reason TEXT, spx REAL, "
            "  proposed_call_strike REAL, proposed_put_strike REAL, "
            "  proposed_debit REAL, em_source TEXT, expected_move REAL);"
        )
        con.execute(
            "INSERT INTO ls_skipped VALUES ('2026-09-23',1,'09:49:57',"
            "'VIX-percentile proxy unavailable (empty history)',7741.16,"
            "7760.0,7720.0,0.0,'straddle',20.9)")
        con.commit()
        con.close()
        return str(p)

    def test_the_day_is_available(self, tmp_path):
        out = read_ls_status(self._skip_only(tmp_path))
        assert out["available"] is True
        assert out["date"] == "2026-09-23"

    def test_the_skip_and_its_counterfactual_are_served(self, tmp_path):
        out = read_ls_status(self._skip_only(tmp_path))
        assert out["summary"]["skipped_count"] == 1
        k = out["skipped"][0]
        assert k["proposed_call_strike"] == 7760.0
        assert k["proposed_put_strike"] == 7720.0
        assert k["expected_move"] == 20.9
        assert k["em_source"] == "straddle"

    def test_a_truly_empty_database_is_still_unavailable(self, tmp_path):
        """The fix must not make "nothing at all" look like data."""
        import sqlite3
        p = tmp_path / "empty.db"
        con = sqlite3.connect(str(p))
        con.executescript(
            "CREATE TABLE ls_entries (date TEXT, entry_number INTEGER);"
            "CREATE TABLE ls_skipped (date TEXT, entry_number INTEGER);")
        con.commit()
        con.close()
        out = read_ls_status(str(p))
        assert out["available"] is False
        assert "declined entries" in out["reason"]
