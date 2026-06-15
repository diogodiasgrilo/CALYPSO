"""Strategy D status reader + Telegram/dashboard rendering (Phase 7)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.calendar_entry import CalendarEntry
from bots.hydra.dc_recorder import DCDataRecorder
from bots.hydra.dc_status import (
    dc_status,
    format_calendars_telegram,
    read_open_calendars,
    read_recent_outcomes,
)
from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy
from dashboard.backend.services.dc_reader import read_dc_status as dashboard_read_dc_status

OPEN_RECORD = {
    "entry_number": 1, "strategy_id": "dctm_x_001", "dc_phase": "transformed",
    "is_risk_free": True, "contracts": 1, "net_debit": 200.0, "transform_credit": 700.0,
    "legs": {
        "short_call": {"strike": 5075.0, "expiry": "2026-06-19"},
        "long_call": {"expiry": "2026-06-19"},
        "short_put": {"strike": 4925.0, "expiry": "2026-06-19"},
        "long_put": {"expiry": "2026-06-19"},
    },
}


def _sidecar(tmp_path, records):
    p = tmp_path / "dc_open_trades.json"
    p.write_text(json.dumps(records))
    return str(p)


def _db_with_outcome(tmp_path):
    db = str(tmp_path / "backtesting.db")
    rec = DCDataRecorder(db)
    e = CalendarEntry(entry_number=1)
    e.strategy_id = "dctm_x_001"
    e.transform_credit = 700.0
    e.net_debit = 200.0
    rec.record_outcome(e, "transformed_settled", 500.0, 5000.0, "2026-06-15", "2026-06-19")
    return db


class TestReadOpenCalendars:
    def test_reads_record(self, tmp_path):
        rows = read_open_calendars(_sidecar(tmp_path, [OPEN_RECORD]))
        assert len(rows) == 1
        r = rows[0]
        assert r["dc_phase"] == "transformed" and r["is_risk_free"] is True
        assert r["call_strike"] == 5075.0 and r["put_strike"] == 4925.0
        assert r["net_debit"] == 200.0 and r["transform_credit"] == 700.0

    def test_missing_file(self):
        assert read_open_calendars("/nonexistent/dc_open_trades.json") == []


class TestReadOutcomes:
    def test_reads(self, tmp_path):
        outs = read_recent_outcomes(_db_with_outcome(tmp_path))
        assert len(outs) == 1
        assert outs[0]["realized_pnl"] == 500.0
        assert outs[0]["terminal_state"] == "transformed_settled"
        assert outs[0]["entry_date"] == "2026-06-15"

    def test_missing_db(self):
        assert read_recent_outcomes("/nonexistent/backtesting.db") == []


class TestStatusAndFormat:
    def test_summary(self, tmp_path):
        st = dc_status(_sidecar(tmp_path, [OPEN_RECORD]), _db_with_outcome(tmp_path))
        s = st["summary"]
        assert s["open_count"] == 1 and s["transformed_count"] == 1 and s["risk_free_count"] == 1
        assert s["realized_pnl_recent"] == 500.0

    def test_format_contains_key_info(self, tmp_path):
        msg = format_calendars_telegram(dc_status(_sidecar(tmp_path, [OPEN_RECORD]), str(tmp_path / "none.db")))
        assert "Strategy D" in msg and "RISK-FREE" in msg and "5075" in msg

    def test_format_empty(self, tmp_path):
        msg = format_calendars_telegram(dc_status(str(tmp_path / "none.json"), str(tmp_path / "none.db")))
        assert "No open calendars" in msg


class TestDashboardReader:
    def test_matches_native_reader(self, tmp_path):
        st = dashboard_read_dc_status(_sidecar(tmp_path, [OPEN_RECORD]), _db_with_outcome(tmp_path))
        assert st["strategy"] == "double_calendar"
        assert st["summary"]["open_count"] == 1
        assert st["open_calendars"][0]["call_strike"] == 5075.0


class TestBuildTelegramCalendars:
    def test_renders_from_variant_d_files(self, tmp_path):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        data = tmp_path / "data"
        (data / "variant_d").mkdir(parents=True)
        inst.state_file = str(data / "hydra_state.json")  # variant-A-style root
        (data / "variant_d" / "dc_open_trades.json").write_text(json.dumps([OPEN_RECORD]))
        msg = inst.build_telegram_calendars()
        assert "Strategy D" in msg and "5075" in msg

    def test_graceful_when_no_files(self, tmp_path):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.state_file = str(tmp_path / "data" / "hydra_state.json")
        msg = inst.build_telegram_calendars()
        assert "Strategy D" in msg  # empty status, not an error
