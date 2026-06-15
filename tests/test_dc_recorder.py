"""DCDataRecorder (Strategy D Phase 6) — isolated calendar DB tables.

Verifies D records a debit calendar / transformation / outcome / snapshot
correctly in its OWN tables (the shared DataRecorder is untouched).
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.calendar_entry import CalendarEntry, DCPhase
from bots.hydra.dc_recorder import DCDataRecorder


def _entry():
    e = CalendarEntry(entry_number=2)
    e.strategy_id = "dctm_20260615_002"
    e.contracts = 1
    e.entry_time = datetime(2026, 6, 15, 14, 5, tzinfo=timezone.utc)
    e.short_call_strike = e.long_call_strike = 5075.0
    e.short_put_strike = e.long_put_strike = 4925.0
    e.legs["short_call"].uic = 11
    e.legs["long_call"].uic = 12
    e.legs["short_put"].uic = 21
    e.legs["long_put"].uic = 22
    e.legs["short_call"].expiry = "2026-06-19"
    e.legs["long_call"].expiry = "2026-06-22"
    e.legs["short_put"].expiry = "2026-06-19"
    e.legs["long_put"].expiry = "2026-06-22"
    e.net_debit = 200.0
    return e


def _rows(db, table):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f"SELECT * FROM {table}")]
    finally:
        con.close()


def test_schema_created(tmp_path):
    db = str(tmp_path / "backtesting.db")
    DCDataRecorder(db)
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"dc_calendar_entries", "dc_transformations", "dc_outcomes",
            "dc_calendar_snapshots", "dc_schema_info"} <= tables


def test_record_calendar_entry(tmp_path):
    db = str(tmp_path / "backtesting.db")
    rec = DCDataRecorder(db)
    rec.record_calendar_entry(_entry(), spx_at_entry=5001.0, date="2026-06-15")
    rows = _rows(db, "dc_calendar_entries")
    assert len(rows) == 1
    r = rows[0]
    assert r["strategy_id"] == "dctm_20260615_002"
    assert r["net_debit"] == 200.0 and r["call_strike"] == 5075.0
    assert r["short_expiry"] == "2026-06-19" and r["long_expiry"] == "2026-06-22"
    assert r["short_dte"] == 4 and r["long_dte"] == 7  # calendar DTE from 06-15
    assert r["short_call_uic"] == 11 and r["long_put_uic"] == 22


def test_record_transformation(tmp_path):
    db = str(tmp_path / "backtesting.db")
    rec = DCDataRecorder(db)
    e = _entry()
    e.dc_phase = DCPhase.TRANSFORMED
    e.transform_credit = 700.0
    e.wing_width = 5.0
    e.is_risk_free = True
    e.long_call_strike = 5080.0  # wing
    e.long_put_strike = 4920.0
    e.call_spread_credit = 150.0
    e.put_spread_credit = 150.0
    e.transformed_at = "2026-06-15T14:40:00+00:00"
    rec.record_transformation(e, date="2026-06-15")
    r = _rows(db, "dc_transformations")[0]
    assert r["transform_credit"] == 700.0 and r["is_risk_free"] == 1
    assert r["wing_call_strike"] == 5080.0 and r["wing_put_strike"] == 4920.0


def test_record_outcome_carries_entry_and_close_date(tmp_path):
    db = str(tmp_path / "backtesting.db")
    rec = DCDataRecorder(db)
    e = _entry()
    e.transform_credit = 700.0
    rec.record_outcome(e, "transformed_settled", realized_pnl=500.0, spx_at_close=5000.0,
                       entry_date="2026-06-15", close_date="2026-06-19")
    r = _rows(db, "dc_outcomes")[0]
    assert r["terminal_state"] == "transformed_settled"
    assert r["realized_pnl"] == 500.0
    assert r["entry_date"] == "2026-06-15" and r["close_date"] == "2026-06-19"


def test_record_snapshot(tmp_path):
    db = str(tmp_path / "backtesting.db")
    rec = DCDataRecorder(db)
    e = _entry()
    e.short_call_price, e.long_call_price = 2.0, 3.0
    e.short_put_price, e.long_put_price = 2.5, 3.5
    rec.record_snapshot(e, timestamp="2026-06-15 14:10:00")
    r = _rows(db, "dc_calendar_snapshots")[0]
    assert r["dc_phase"] == "calendar"
    assert r["calendar_value"] == 200.0  # (3-2 + 3.5-2.5)*100
    assert r["unrealized_pnl"] == 0.0    # 200 - 200 debit


def test_init_failure_is_non_fatal():
    # A bad path must not raise — recorder degrades to a no-op.
    rec = DCDataRecorder("/nonexistent_dir_xyz/should/not/exist.db")
    assert rec._conn is None
    rec.record_calendar_entry(_entry(), 5000.0, "2026-06-15")  # no exception
