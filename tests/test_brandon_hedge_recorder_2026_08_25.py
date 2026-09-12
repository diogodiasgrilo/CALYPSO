"""2026-08-25: durable hedge-history tracking (BrandonHedgeRecorder).

Mirrors dc_recorder.py's isolation pattern (own physically-separate DB file,
not the shared backtesting.db) and its fire-and-forget resilience convention
— a recording failure must never affect trading logic. These tests cover the
recorder module directly (schema creation, per-leg placement rows, settlement
rows, failure isolation) and the strategy-level wrapper methods that call it.
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.hedge_recorder import BrandonHedgeRecorder  # noqa: E402
from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.brandon.hedge_position import HedgeLeg  # noqa: E402


def _leg(strike=6915.0, side="long", contract_type="call", quantity=1,
         fill_price=1.5, conid=12345):
    return HedgeLeg(
        entry_number=1, side=side, contract_type=contract_type, strike=strike,
        quantity=quantity, fill_price=fill_price, position_id="DRY_OVERLAY_1_call_0",
        structure="debit_spread", threatened_side="call",
        placed_at=datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc), conid=conid,
    )


class TestSchema:
    def test_tables_created(self, tmp_path):
        rec = BrandonHedgeRecorder(str(tmp_path / "hedges.db"))
        assert rec._conn is not None
        tables = {
            r[0] for r in rec._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"hedge_placements", "hedge_settlements", "hedge_schema_info"} <= tables

    def test_reopening_the_same_file_is_idempotent(self, tmp_path):
        path = str(tmp_path / "hedges.db")
        rec1 = BrandonHedgeRecorder(path)
        rec1.record_hedge_placement(
            date="2026-08-25", entry_number=1, threatened_side="call",
            structure="debit_spread", legs=[_leg()], gex_confirmed=True,
            trigger_distance_at_arm_pts=4.0, confirm_seconds=10.0, severity_bypassed=False,
        )
        rec2 = BrandonHedgeRecorder(path)  # reopen, e.g. across a restart
        rows = rec2._conn.execute("SELECT COUNT(*) FROM hedge_placements").fetchone()[0]
        assert rows == 1  # the earlier write survived, schema wasn't clobbered


class TestRecordHedgePlacement:
    def test_writes_one_row_per_leg(self, tmp_path):
        rec = BrandonHedgeRecorder(str(tmp_path / "hedges.db"))
        legs = [
            _leg(strike=6915.0, side="long", conid=111),
            _leg(strike=6990.0, side="short", conid=222),
        ]
        rec.record_hedge_placement(
            date="2026-08-25", entry_number=7, threatened_side="call",
            structure="debit_spread", legs=legs, gex_confirmed=True,
            trigger_distance_at_arm_pts=4.0, confirm_seconds=10.0, severity_bypassed=True,
        )
        rows = rec._conn.execute(
            "SELECT entry_number, threatened_side, structure, leg_side, contract_type, "
            "strike, quantity, fill_price, conid, gex_confirmed, trigger_distance_at_arm_pts, "
            "confirm_seconds, severity_bypassed FROM hedge_placements ORDER BY strike"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][:9] == (7, "call", "debit_spread", "long", "call", 6915.0, 1, 1.5, 111)
        assert rows[0][9] == 1  # gex_confirmed stored as int
        assert rows[0][10] == 4.0
        assert rows[0][11] == 10.0
        assert rows[0][12] == 1  # severity_bypassed stored as int
        assert rows[1][3] == "short"
        assert rows[1][8] == 222

    def test_gex_confirmed_none_stores_null(self, tmp_path):
        rec = BrandonHedgeRecorder(str(tmp_path / "hedges.db"))
        rec.record_hedge_placement(
            date="2026-08-25", entry_number=1, threatened_side="call",
            structure="debit_spread", legs=[_leg()], gex_confirmed=None,
            trigger_distance_at_arm_pts=None, confirm_seconds=None, severity_bypassed=False,
        )
        row = rec._conn.execute(
            "SELECT gex_confirmed, trigger_distance_at_arm_pts, confirm_seconds FROM hedge_placements"
        ).fetchone()
        assert row == (None, None, None)


class TestRecordHedgeSettlement:
    def test_writes_a_settlement_row(self, tmp_path):
        rec = BrandonHedgeRecorder(str(tmp_path / "hedges.db"))
        rec.record_hedge_settlement(
            date="2026-08-25", entry_number=3, threatened_side="put",
            structure="butterfly", spx_close=6800.5, debit_paid=525.0,
            hedge_pnl=-140.0, settled_at="2026-08-25T16:00:00",
        )
        row = rec._conn.execute(
            "SELECT entry_number, threatened_side, structure, spx_close, debit_paid, "
            "hedge_pnl, settled_at FROM hedge_settlements"
        ).fetchone()
        assert row == (3, "put", "butterfly", 6800.5, 525.0, -140.0, "2026-08-25T16:00:00")


class TestFailureIsolation:
    def test_bad_db_path_does_not_raise_and_leaves_conn_none(self, tmp_path):
        # A directory where a file is expected — sqlite3.connect will fail.
        bad_path = str(tmp_path)  # tmp_path itself is a directory, not a file
        rec = BrandonHedgeRecorder(bad_path)
        assert rec._conn is None

    def test_writes_after_a_broken_connection_do_not_raise(self, tmp_path):
        rec = BrandonHedgeRecorder(str(tmp_path / "hedges.db"))
        rec._conn.close()  # simulate a broken/closed connection mid-session
        # None of these may raise — fire-and-forget.
        rec.record_hedge_placement(
            date="2026-08-25", entry_number=1, threatened_side="call",
            structure="debit_spread", legs=[_leg()], gex_confirmed=True,
            trigger_distance_at_arm_pts=1.0, confirm_seconds=1.0, severity_bypassed=False,
        )
        rec.record_hedge_settlement(
            date="2026-08-25", entry_number=1, threatened_side="call",
            structure="debit_spread", spx_close=6800.0, debit_paid=1.0,
            hedge_pnl=0.0, settled_at="2026-08-25T16:00:00",
        )

    def test_no_conn_at_all_is_a_silent_noop(self):
        rec = BrandonHedgeRecorder.__new__(BrandonHedgeRecorder)
        rec._conn = None
        rec.record_hedge_placement(
            date="2026-08-25", entry_number=1, threatened_side="call",
            structure="debit_spread", legs=[_leg()], gex_confirmed=True,
            trigger_distance_at_arm_pts=1.0, confirm_seconds=1.0, severity_bypassed=False,
        )
        rec.record_hedge_settlement(
            date="2026-08-25", entry_number=1, threatened_side="call",
            structure="debit_spread", spx_close=6800.0, debit_paid=1.0,
            hedge_pnl=0.0, settled_at="2026-08-25T16:00:00",
        )


def _strategy_stub(recorder):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s._brandon_hedge_recorder = recorder
    s._brandon_today_date = lambda: __import__("datetime").date(2026, 8, 25)
    s._brandon_now_et = lambda: datetime(2026, 8, 25, 16, 0)
    return s


class TestStrategyWrapperMethods:
    def test_record_hedge_placement_noop_when_recorder_is_none(self):
        s = _strategy_stub(None)
        entry = SimpleNamespace(entry_number=1)
        proposal = SimpleNamespace(threatened_side="call", structure=SimpleNamespace(value="debit_spread"))
        s._brandon_record_hedge_placement(entry, proposal, [_leg()])  # must not raise

    def test_record_hedge_placement_noop_for_empty_legs(self):
        recorder = MagicMock()
        s = _strategy_stub(recorder)
        entry = SimpleNamespace(entry_number=1)
        proposal = SimpleNamespace(threatened_side="call", structure=SimpleNamespace(value="debit_spread"))
        s._brandon_record_hedge_placement(entry, proposal, [])
        recorder.record_hedge_placement.assert_not_called()

    def test_record_hedge_placement_calls_through_with_correct_args(self):
        recorder = MagicMock()
        s = _strategy_stub(recorder)
        entry = SimpleNamespace(entry_number=7)
        proposal = SimpleNamespace(threatened_side="call", structure=SimpleNamespace(value="debit_spread"))
        legs = [_leg()]
        s._brandon_record_hedge_placement(
            entry, proposal, legs,
            gex_confirmed=True, trigger_distance_pts=4.0,
            confirm_seconds=10.0, severity_bypassed=True,
        )
        recorder.record_hedge_placement.assert_called_once_with(
            date="2026-08-25", entry_number=7, threatened_side="call",
            structure="debit_spread", legs=legs, gex_confirmed=True,
            trigger_distance_at_arm_pts=4.0, confirm_seconds=10.0, severity_bypassed=True,
        )

    def test_record_hedge_placement_swallows_recorder_exceptions(self):
        recorder = MagicMock()
        recorder.record_hedge_placement.side_effect = RuntimeError("boom")
        s = _strategy_stub(recorder)
        entry = SimpleNamespace(entry_number=1)
        proposal = SimpleNamespace(threatened_side="call", structure=SimpleNamespace(value="debit_spread"))
        s._brandon_record_hedge_placement(entry, proposal, [_leg()])  # must not raise

    def test_record_hedge_settlement_calls_through(self):
        recorder = MagicMock()
        s = _strategy_stub(recorder)
        settlement = SimpleNamespace(
            entry_number=3, threatened_side="put", structure="butterfly",
            spx_settle=6800.5, total_debit_paid=525.0, total_pnl=-140.0,
        )
        s._brandon_record_hedge_settlement(settlement)
        recorder.record_hedge_settlement.assert_called_once_with(
            date="2026-08-25", entry_number=3, threatened_side="put",
            structure="butterfly", spx_close=6800.5, debit_paid=525.0,
            hedge_pnl=-140.0, settled_at="2026-08-25T16:00:00",
        )

    def test_record_hedge_settlement_swallows_recorder_exceptions(self):
        recorder = MagicMock()
        recorder.record_hedge_settlement.side_effect = RuntimeError("boom")
        s = _strategy_stub(recorder)
        settlement = SimpleNamespace(
            entry_number=1, threatened_side="call", structure="debit_spread",
            spx_settle=6800.0, total_debit_paid=1.0, total_pnl=0.0,
        )
        s._brandon_record_hedge_settlement(settlement)  # must not raise


class TestOpenHedgeRecorderCreatesDataDir:
    """2026-08-25 convergence-audit fix: _brandon_open_hedge_recorder must
    create DATA_DIR before opening the DB, mirroring the identical
    os.makedirs call HydraStrategy.__init__ already does for the shared
    DataRecorder — without it, a genuinely fresh variant install (data/
    variant_<id>/ not yet created) would hit sqlite3.connect()'s
    "unable to open database file" error, which BrandonHedgeRecorder
    catches and turns into a PERMANENT self._conn = None for the process
    lifetime (no retry until the next restart)."""

    def test_creates_missing_data_dir_and_opens_successfully(self, tmp_path, monkeypatch):
        fresh_dir = tmp_path / "variant_b_fresh_install"
        assert not fresh_dir.exists()
        monkeypatch.setattr("bots.hydra.strategy.DATA_DIR", str(fresh_dir))

        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        recorder = s._brandon_open_hedge_recorder()

        assert fresh_dir.exists()
        assert recorder is not None
        assert recorder._conn is not None
        assert (fresh_dir / "brandon_hedges.db").exists()

    def test_negative_control_fails_without_makedirs(self, tmp_path, monkeypatch):
        # Proves the test above actually exercises the fix: reproduce the
        # pre-fix behavior directly (no makedirs) and confirm it degrades to
        # a permanently-None connection, exactly the bug this fix closes.
        from bots.hydra.brandon.hedge_recorder import BrandonHedgeRecorder
        fresh_dir = tmp_path / "variant_b_fresh_install_no_makedirs"
        assert not fresh_dir.exists()
        recorder = BrandonHedgeRecorder(str(fresh_dir / "brandon_hedges.db"))
        assert recorder._conn is None  # exactly the silent-failure this fix prevents
