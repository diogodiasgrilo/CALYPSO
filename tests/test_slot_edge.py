"""Tests for bots.hydra.slot_edge — per-slot edge analyzer for the IC variants."""

from __future__ import annotations

import sqlite3

import pytest

from bots.hydra.slot_edge import (
    analyze_slots,
    reconstruct_entry_pnl,
    slot_for,
)


class TestSlotFor:
    def test_maps_near_times_to_canonical_slots(self):
        assert slot_for("2026-06-30 09:46:35") == "09:45"
        assert slot_for("2026-06-30 10:45:54") == "10:45"
        assert slot_for("2026-06-30 11:16:09") == "11:15"
        assert slot_for("2026-06-30 11:45:30") == "11:45"
        assert slot_for("2026-06-30 14:01:10") == "E6 14:00"

    def test_off_schedule_buckets_to_other(self):
        assert slot_for("2026-06-30 13:05:00") == "other"  # >12min from any slot
        assert slot_for(None) == "other"
        assert slot_for("garbage") == "other"

    def test_accepts_time_only_string(self):
        assert slot_for("10:45:00") == "10:45"


class TestReconstructEntryPnl:
    def test_both_sides_expire_keeps_full_credit(self):
        pnl, scored = reconstruct_entry_pnl(300.0, 250.0, stops={})
        assert scored is True
        assert pnl == 550.0  # both sides kept

    def test_one_side_stopped_uses_net_pnl_plus_other_credit(self):
        # put stopped (net_pnl recorded), call expired worthless
        pnl, scored = reconstruct_entry_pnl(
            300.0, 250.0, stops={"put": {"net_pnl": -1875.0}})
        assert scored is True
        assert pnl == 300.0 + (-1875.0)  # call credit kept + put realized loss

    def test_both_sides_stopped_sum_net_pnl(self):
        pnl, scored = reconstruct_entry_pnl(
            300.0, 250.0,
            stops={"call": {"net_pnl": -1000.0}, "put": {"net_pnl": -1500.0}})
        assert scored is True
        assert pnl == -2500.0  # credits already baked into each side's net_pnl

    def test_stopped_side_without_net_pnl_is_unscored(self):
        pnl, scored = reconstruct_entry_pnl(
            300.0, 250.0, stops={"put": {"net_pnl": None}})
        assert scored is False
        assert pnl is None  # excluded from P&L means (data gap, not silently mis-scaled)


def _seed_db(con: sqlite3.Connection):
    # realized_pnl mirrors the real v12 schema; left NULL here so this fixture
    # exercises the trade_stops RECONSTRUCTION fallback (pre-v12 rows).
    con.execute("CREATE TABLE trade_entries (date TEXT, entry_number INT, entry_time TEXT,"
                " call_credit REAL, put_credit REAL, total_credit REAL, contracts INT,"
                " realized_pnl REAL)")
    con.execute("CREATE TABLE trade_stops (date TEXT, entry_number INT, side TEXT,"
                " net_pnl REAL, stop_time TEXT)")
    # gross_pnl is the reconciliation baseline (per-entry realized_pnl is gross of
    # commission); net_pnl kept for realism.
    con.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL, gross_pnl REAL)")
    # Two slots: 10:45 (a clean winner) and 11:45 (a loser via a put stop).
    entries = [
        ("2026-06-01", 1, "2026-06-01 10:45:30", 300, 250, 550, 10),  # win, kept 550
        ("2026-06-02", 1, "2026-06-02 10:45:10", 300, 250, 550, 10),  # win, kept 550
        ("2026-06-01", 2, "2026-06-01 11:45:20", 300, 250, 550, 10),  # put stop -1875
        ("2026-06-02", 2, "2026-06-02 11:45:40", 300, 250, 550, 10),  # put stop, net_pnl NULL → unscored
    ]
    con.executemany("INSERT INTO trade_entries VALUES (?,?,?,?,?,?,?,NULL)", entries)
    con.executemany("INSERT INTO trade_stops VALUES (?,?,?,?,?)", [
        ("2026-06-01", 2, "put", -1875.0, "2026-06-01 13:00:00"),
        ("2026-06-02", 2, "put", None, "2026-06-02 13:00:00"),
    ])
    con.executemany("INSERT INTO daily_summaries VALUES (?,?,?)", [
        # date, net_pnl, gross_pnl (gross = net + commission; ~$30/day token here)
        ("2026-06-01", 300.0 + 550.0 - 1875.0, 300.0 + 550.0 - 1875.0 + 30.0),
        ("2026-06-02", 550.0, 550.0 + 30.0),
    ])
    con.commit()


def test_analyze_slots_aggregates_and_scores(tmp_path):
    db = tmp_path / "backtesting.db"
    con = sqlite3.connect(db)
    _seed_db(con)
    con.close()

    res = analyze_slots(str(db), min_preliminary=2, min_confident=10)
    assert res["ok"] is True
    by_slot = {r["slot"]: r for r in res["slots"]}

    # 10:45 — two clean wins, no stops, both scored.
    s1045 = by_slot["10:45"]
    assert s1045["n"] == 2 and s1045["n_scored"] == 2 and s1045["unscored"] == 0
    assert s1045["stop_rate"] == 0.0
    assert s1045["avg_pnl"] == 550.0
    assert s1045["win_rate"] == 1.0

    # 11:45 — both stopped; one scored (-1875+300), one unscored (NULL net_pnl).
    s1145 = by_slot["11:45"]
    assert s1145["n"] == 2 and s1145["n_scored"] == 1 and s1145["unscored"] == 1
    assert s1145["stop_rate"] == 1.0
    assert s1145["avg_pnl"] == pytest.approx(300.0 - 1875.0)

    # 10:45 ranks above 11:45 (higher mean P&L).
    assert res["slots"][0]["slot"] == "10:45"
    # scored_total = 550 + 550 + (300-1875) = -475
    assert res["scored_total"] == pytest.approx(-475.0)


def test_recorded_realized_pnl_is_preferred_over_reconstruction(tmp_path):
    """v12: when trade_entries.realized_pnl is populated, the analyzer trusts it
    directly (NOT the trade_stops reconstruction). Prove it by recording a value
    that DISAGREES with what reconstruction would compute."""
    db = tmp_path / "backtesting.db"
    con = sqlite3.connect(db)
    _seed_db(con)
    # Entry #2 on 2026-06-01 (11:45) reconstructs to 300-1875 = -1575 from stops,
    # but record a DIFFERENT reconciled value (+222) — the analyzer must use +222.
    con.execute("UPDATE trade_entries SET realized_pnl = 222.0 "
                "WHERE date='2026-06-01' AND entry_number=2")
    # The unscored 11:45 entry (NULL net_pnl stop) gets a recorded value too, so it
    # is now SCORED (no longer excluded).
    con.execute("UPDATE trade_entries SET realized_pnl = -99.0 "
                "WHERE date='2026-06-02' AND entry_number=2")
    con.commit()
    con.close()

    res = analyze_slots(str(db), min_preliminary=2, min_confident=10)
    by_slot = {r["slot"]: r for r in res["slots"]}
    s1145 = by_slot["11:45"]
    # Both 11:45 entries now scored via the recorded column; none unscored.
    assert s1145["n_scored"] == 2 and s1145["unscored"] == 0 and s1145["recorded"] == 2
    # avg uses the RECORDED values (+222, -99), not reconstruction (-1575).
    assert s1145["avg_pnl"] == pytest.approx((222.0 + -99.0) / 2)


def test_missing_realized_pnl_column_falls_back(tmp_path):
    """A pre-v12 DB with NO realized_pnl column still works (reconstruction)."""
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trade_entries (date TEXT, entry_number INT, entry_time TEXT,"
                " call_credit REAL, put_credit REAL, total_credit REAL, contracts INT)")
    con.execute("CREATE TABLE trade_stops (date TEXT, entry_number INT, side TEXT,"
                " net_pnl REAL, stop_time TEXT)")
    con.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL, gross_pnl REAL)")
    con.execute("INSERT INTO trade_entries VALUES "
                "('2026-06-01',1,'2026-06-01 10:45:00',300,250,550,10)")
    con.execute("INSERT INTO daily_summaries VALUES ('2026-06-01', 520, 550)")
    con.commit()
    con.close()
    res = analyze_slots(str(db), min_preliminary=1, min_confident=5)
    assert res["ok"] is True
    s = {r["slot"]: r for r in res["slots"]}["10:45"]
    assert s["recorded"] == 0 and s["avg_pnl"] == 550.0  # reconstruction path


def test_missing_db_returns_error():
    res = analyze_slots("/nonexistent/backtesting.db")
    assert res["ok"] is False and "no db" in res["error"]
