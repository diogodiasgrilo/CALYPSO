"""Strategy-grouping redesign — dashboard BACKEND API (Phase 5).

Pins the new taxonomy-driven /api/strategies/* router:
  * /meta returns the 2 comparability groups + every registered strategy
    (incl. the pre-staged calendar variant E), sourced from
    shared.strategy_taxonomy — no hardcoded letters, NO file paths leaked.
  * group comparison is SHAPE-DISTINCT by pnl_shape: the calendar group's
    payload carries NO IC keys (total_credit / buffer / spread-width), the IC
    group's is credit-shaped.
  * a per-strategy snapshot for a not-yet-running variant returns
    available:false (never a 500).
  * the router is behind the _api_guard (a configured API key is required).

The dashboard backend runs in its own venv (FastAPI). Skip cleanly in the main
bot suite where it isn't installed; this runs in the dashboard env (and the VM).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import dashboard.backend.config as cfg_mod  # noqa: E402
from dashboard.backend.config import settings  # noqa: E402

API_KEY = "test-strategies-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_calendar_db(path: Path) -> None:
    """Seed a minimal dc_calendar.db (the dc_recorder schema) with two closed
    calendar outcomes so the debit-native rollup has data to compute on."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE dc_outcomes (
            entry_date TEXT, close_date TEXT, entry_number INTEGER, strategy_id TEXT,
            terminal_state TEXT, realized_pnl REAL, spx_at_close REAL,
            close_commission REAL, transform_credit REAL, net_debit REAL,
            PRIMARY KEY (entry_date, entry_number)
        );
        """
    )
    conn.executemany(
        "INSERT INTO dc_outcomes (entry_date, close_date, entry_number, strategy_id, "
        "terminal_state, realized_pnl, spx_at_close, close_commission, transform_credit, net_debit) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("2026-06-10", "2026-06-13", 1, "dctm_x_001", "settled", 320.0, 5000.0, 2.0, 0.0, 200.0),
            ("2026-06-11", "2026-06-14", 1, "dctm_x_002", "stopped", -150.0, 4990.0, 2.0, 0.0, 250.0),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient with the API key configured (so the _api_guard is ACTIVE)
    and variant D pointed at a seeded calendar DB under tmp_path. The FastAPI
    app's lifespan starts the broadcaster; TestClient(...) as a context manager
    runs it, but we don't depend on it for these REST endpoints."""
    # Turn the guard ON for this test (default is empty = disabled).
    monkeypatch.setattr(settings, "api_key", API_KEY, raising=False)

    # Point variant D's data dir at a seeded calendar DB (the calendar group's
    # comparison reads data/variant_<id>/dc_calendar.db). E stays missing →
    # available:false, which the payload must tolerate.
    vd = tmp_path / "variant_d"
    vd.mkdir()
    _seed_calendar_db(vd / "dc_calendar.db")
    monkeypatch.setattr(settings, "variant_d_state_file", vd / "hydra_state.json", raising=False)

    ve = tmp_path / "variant_e"  # intentionally NOT created → E unavailable
    monkeypatch.setattr(settings, "variant_e_state_file", ve / "hydra_state.json", raising=False)

    from dashboard.backend.main import app
    with TestClient(app) as c:
        yield c


def _h():
    return {"X-API-Key": API_KEY}


# ---------------------------------------------------------------------------
# _api_guard
# ---------------------------------------------------------------------------


class TestApiGuard:
    def test_requires_api_key(self, client):
        # No key → 401 (the guard is active because settings.api_key is set).
        assert client.get("/api/strategies/meta").status_code == 401

    def test_accepts_valid_key(self, client):
        assert client.get("/api/strategies/meta", headers=_h()).status_code == 200


# ---------------------------------------------------------------------------
# /meta
# ---------------------------------------------------------------------------


class TestMeta:
    def test_two_groups_and_strategies_incl_e(self, client):
        r = client.get("/api/strategies/meta", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["primary_id"] == "c"

        group_ids = {g["id"] for g in body["groups"]}
        assert group_ids == {"ic_0dte", "calendar_multiday"}

        # ic_0dte = credit, calendar_multiday = debit; members derived from taxonomy.
        by_id = {g["id"]: g for g in body["groups"]}
        assert by_id["ic_0dte"]["pnl_shape"] == "credit"
        assert by_id["ic_0dte"]["member_ids"] == ["a", "b", "c"]
        assert by_id["calendar_multiday"]["pnl_shape"] == "debit"
        assert by_id["calendar_multiday"]["member_ids"] == ["d", "e"]
        # baseline is the first member, not hardcoded "a".
        assert by_id["calendar_multiday"]["baseline_id"] == "d"

        strat_ids = {s["id"] for s in body["strategies"]}
        assert {"a", "b", "c", "d", "e"} <= strat_ids
        e = next(s for s in body["strategies"] if s["id"] == "e")
        assert e["data_kind"] == "dc_calendar"
        assert e["family"] == "double_calendar"
        assert e["pnl_shape"] == "debit"
        assert e["is_primary"] is False
        # E hasn't run → available:false; capabilities expose calendar_cards, not history.
        assert e["available"] is False
        assert e["capabilities"]["calendar_cards"] is True
        assert e["capabilities"]["history"] is False

        c = next(s for s in body["strategies"] if s["id"] == "c")
        assert c["data_kind"] == "ic_state"
        assert c["is_primary"] is True
        assert c["capabilities"]["history"] is True

    def test_no_filesystem_paths_leaked(self, client):
        # The whole /meta payload must NOT contain any path-ish string.
        import json
        raw = json.dumps(client.get("/api/strategies/meta", headers=_h()).json())
        assert "/opt/calypso" not in raw
        assert "/" not in raw  # no path separators anywhere
        assert ".json" not in raw and ".db" not in raw


# ---------------------------------------------------------------------------
# group comparison — shape distinction
# ---------------------------------------------------------------------------

# The full set of IC-only keys that MUST NOT appear anywhere in a calendar payload.
_IC_FORBIDDEN_KEYS = {
    "total_credit", "total_credit_received", "total_credit_collected",
    "call_credit", "put_credit", "min_buffer_margin", "buffer",
    "call_stop_buffer", "put_stop_buffer", "call_spread_width", "put_spread_width",
    "capital_deployed", "call_spread_credit", "put_spread_credit",
}


def _collect_keys(obj, acc: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _collect_keys(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_keys(v, acc)


class TestGroupComparisonShape:
    def test_calendar_group_is_debit_and_has_no_ic_keys(self, client):
        r = client.get("/api/strategies/groups/calendar_multiday/comparison", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["pnl_shape"] == "debit"
        assert body["family"] == "double_calendar"
        assert body["baseline_id"] == "D"
        # It carries a calendar payload, NOT an IC variants/leaderboard-credit one.
        assert "calendars" in body
        assert "variants" not in body  # IC-only key

        # D (seeded) is available with debit-native lifetime totals.
        d = body["calendars"]["members"]["D"]
        assert d["available"] is True
        assert d["lifetime"]["cumulative_pnl"] == pytest.approx(170.0)  # 320 - 150
        assert d["lifetime"]["capital_at_risk"] == pytest.approx(450.0)  # 200 + 250
        # E unavailable, tolerated (no crash, available:false).
        assert body["calendars"]["members"]["E"]["available"] is False

        # The HARD assertion: NO IC-only key anywhere in the calendar payload.
        keys: set = set()
        _collect_keys(body, keys)
        leaked = keys & _IC_FORBIDDEN_KEYS
        assert not leaked, f"calendar payload leaked IC keys: {leaked}"

    def test_ic_group_is_credit_shaped(self, client):
        r = client.get("/api/strategies/groups/ic_0dte/comparison", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["pnl_shape"] == "credit"
        assert body["family"] == "iron_condor"
        # IC payload shape: a leaderboard + per-variant variants block.
        assert "leaderboard" in body
        assert "variants" in body
        assert body["leaderboard"]["baseline_id"] == "A"

    def test_unknown_group_404(self, client):
        assert client.get("/api/strategies/groups/nope/comparison", headers=_h()).status_code == 404


# ---------------------------------------------------------------------------
# /{id}/snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_missing_variant_is_available_false_not_500(self, client):
        # E has no state file / artifacts → available:false in the body, 200 OK.
        r = client.get("/api/strategies/e/snapshot", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["data_kind"] == "dc_calendar"
        assert body["body"]["available"] is False
        # Header chrome present even when unavailable (re-bind needs it).
        assert "display_name" in body and "underlying_symbol" in body

    def test_missing_ic_variant_is_available_false(self, client, tmp_path, monkeypatch):
        # Point an IC variant at a missing state file → available:false, not 500.
        monkeypatch.setattr(settings, "variant_a_state_file",
                            tmp_path / "nope" / "hydra_state.json", raising=False)
        r = client.get("/api/strategies/a/snapshot", headers=_h())
        assert r.status_code == 200
        assert r.json()["data_kind"] == "ic_state"
        assert r.json()["body"]["available"] is False

    def test_unknown_letter_degrades_gracefully(self, client):
        # An unregistered letter → safe sentinel (ic_state default), no crash.
        r = client.get("/api/strategies/z/snapshot", headers=_h())
        assert r.status_code == 200
        assert r.json()["body"]["available"] is False


# ---------------------------------------------------------------------------
# group aggregate
# ---------------------------------------------------------------------------


class TestGroupAggregate:
    def test_calendar_aggregate_debit_no_ic_keys(self, client):
        r = client.get("/api/strategies/groups/calendar_multiday/aggregate", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["pnl_shape"] == "debit"
        assert "calendars" in body
        d = body["calendars"]["members"]["D"]
        assert d["available"] is True
        # close_date attribution → curve dates are the CLOSE dates, in order.
        assert [p["date"] for p in d["cumulative_curve"]] == ["2026-06-13", "2026-06-14"]
        assert d["cumulative_curve"][-1]["cumulative"] == pytest.approx(170.0)
        keys: set = set()
        _collect_keys(body, keys)
        assert not (keys & _IC_FORBIDDEN_KEYS)

    def test_ic_aggregate_credit(self, client):
        r = client.get("/api/strategies/groups/ic_0dte/aggregate", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["pnl_shape"] == "credit"
        assert "variants" in body and "head_to_head" in body


def test_entry_realized_pnl_early_close_nets_the_close_cost():
    """2026-06-25: an EOD-flatten/TP early-close records its close cost in
    actual_*_stop_debit but reuses *_side_expired — realized must be
    credit - debit, NOT the full credit (which over-counted C E#2 as $210 vs $175)."""
    from dashboard.backend.routers.variants import _entry_realized_pnl
    flat = {"call_spread_credit": 0, "put_spread_credit": 210, "actual_put_stop_debit": 35,
            "actual_call_stop_debit": 0, "put_side_expired": True, "put_side_stopped": False,
            "call_side_expired": False, "call_side_stopped": False, "early_closed": True}
    assert _entry_realized_pnl(flat) == 175  # 210 - 35, not 210
    worthless = {"call_spread_credit": 0, "put_spread_credit": 210, "actual_put_stop_debit": 0,
                 "actual_call_stop_debit": 0, "put_side_expired": True, "put_side_stopped": False,
                 "call_side_expired": False, "call_side_stopped": False, "early_closed": False}
    assert _entry_realized_pnl(worthless) == 210  # true worthless expiry keeps full credit
