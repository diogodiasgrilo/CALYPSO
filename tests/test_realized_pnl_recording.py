"""Per-entry realized-P&L recording (schema v12) — the slot_edge unlock.

Covers the two correctness pillars:
  1. _book_realized_pnl mirrors the EXACT amount onto the entry while booking the
     identical amount to the day aggregate — so per-entry sums equal the day total
     by construction (the reconciliation guard's invariant).
  2. The DataRecorder v12 column + update_entry_realized_pnl round-trip, and the
     additive migration on a pre-v12 DB.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from datetime import datetime

from bots.hydra.base_strategy import MEICStrategy
from bots.hydra.brandon.strategy import BrandonHydraStrategy
from bots.hydra.brandon import hedge_position
from bots.hydra.brandon.hedge_position import HedgeLeg
from shared.data_recorder import DataRecorder, SCHEMA_VERSION


class _BookStub:
    """Borrows the real MEICStrategy._book_realized_pnl onto a minimal object so we
    can test the mirroring invariant without constructing a full strategy."""

    _book_realized_pnl = MEICStrategy._book_realized_pnl

    def __init__(self):
        self.daily_state = SimpleNamespace(total_realized_pnl=0.0)


def _entry():
    return SimpleNamespace(realized_pnl=0.0)


class TestBookRealizedPnl:
    def test_mirrors_amount_to_both_aggregate_and_entry(self):
        s = _BookStub()
        e = _entry()
        s._book_realized_pnl(137.5, e)
        assert s.daily_state.total_realized_pnl == 137.5
        assert e.realized_pnl == 137.5

    def test_negative_amount_mirrors(self):
        s = _BookStub()
        e = _entry()
        s._book_realized_pnl(-1875.0, e)
        assert s.daily_state.total_realized_pnl == -1875.0
        assert e.realized_pnl == -1875.0

    def test_entry_none_touches_only_aggregate(self):
        s = _BookStub()
        s._book_realized_pnl(500.0, None)
        assert s.daily_state.total_realized_pnl == 500.0  # aggregate-only (e.g. legacy)

    def test_entry_without_field_starts_from_zero(self):
        s = _BookStub()
        e = SimpleNamespace()  # no realized_pnl attr yet
        s._book_realized_pnl(42.0, e)
        assert e.realized_pnl == 42.0

    def test_reconciliation_invariant_holds_across_a_mixed_sequence(self):
        """The core guarantee: after any sequence of per-entry bookings, the sum of
        entry.realized_pnl equals daily_state.total_realized_pnl exactly."""
        s = _BookStub()
        e1, e2, e3 = _entry(), _entry(), _entry()
        # e1: two winning sides kept; e2: one side stopped at a loss; e3: salvage +
        # a later external correction.
        s._book_realized_pnl(300.0, e1)
        s._book_realized_pnl(250.0, e1)
        s._book_realized_pnl(200.0, e2)
        s._book_realized_pnl(-1875.0, e2)
        s._book_realized_pnl(120.0, e3)
        s._book_realized_pnl(-40.0, e3)
        per_entry_sum = e1.realized_pnl + e2.realized_pnl + e3.realized_pnl
        assert per_entry_sum == pytest.approx(s.daily_state.total_realized_pnl)
        assert e1.realized_pnl == 550.0
        assert e2.realized_pnl == pytest.approx(-1675.0)
        assert e3.realized_pnl == 80.0


class _HedgeStub:
    """Borrows the real _brandon_settle_hedges + _book_realized_pnl onto a minimal
    object to test the overlay-P&L booking without a full BrandonHydraStrategy."""

    _brandon_settle_hedges = BrandonHydraStrategy._brandon_settle_hedges
    _book_realized_pnl = MEICStrategy._book_realized_pnl

    def __init__(self, entries, hedge_legs):
        self.daily_state = SimpleNamespace(entries=entries, total_realized_pnl=0.0)
        self._brandon_hedge_legs = hedge_legs
        self._brandon_hedge_settlements = []
        self._brandon_overlay_booked = set()
        self.dry_run = True

    def _brandon_send_telegram(self, *a, **k):
        pass

    def _brandon_save_hedge_state(self):
        pass  # persistence is exercised in the load/save round-trip tests


def _hedge_legs_for_entry(n):
    """A long-call debit spread hedge for entry n."""
    now = datetime(2026, 7, 2, 10, 0, 0)
    return [
        HedgeLeg(n, "long", "call", 7500.0, 1, 5.0, f"DRY_OVERLAY_{n}_0", "debit_spread", "call", now),
        HedgeLeg(n, "short", "call", 7520.0, 1, 3.0, f"DRY_OVERLAY_{n}_1", "debit_spread", "call", now),
    ]


class TestOverlayPnlBooking:
    def test_overlay_pnl_booked_to_aggregate_and_entry_and_reconciles(self):
        e1 = SimpleNamespace(entry_number=1, realized_pnl=0.0, overlay_pnl_booked=False)
        e2 = SimpleNamespace(entry_number=2, realized_pnl=0.0, overlay_pnl_booked=False)  # no hedge
        legs = _hedge_legs_for_entry(1)
        expected = hedge_position.settle_hedge(legs, spx_settle=7550.0).total_pnl
        assert expected != 0.0  # sanity: the hedge has a nonzero P&L

        s = _HedgeStub([e1, e2], {1: legs})
        s._brandon_settle_hedges(spx_settle=7550.0)

        assert e1.realized_pnl == pytest.approx(expected)
        assert e1.overlay_pnl_booked is True
        assert e2.realized_pnl == 0.0  # unhedged entry untouched
        assert s.daily_state.total_realized_pnl == pytest.approx(expected)
        # Reconciliation invariant: per-entry sum == day aggregate (both incl overlay).
        assert (e1.realized_pnl + e2.realized_pnl) == pytest.approx(s.daily_state.total_realized_pnl)

    def test_restart_rerun_does_not_double_book(self):
        e1 = SimpleNamespace(entry_number=1, realized_pnl=0.0, overlay_pnl_booked=False)
        legs = _hedge_legs_for_entry(1)
        expected = hedge_position.settle_hedge(legs, spx_settle=7550.0).total_pnl

        s = _HedgeStub([e1], {1: legs})
        s._brandon_settle_hedges(spx_settle=7550.0)
        # Simulate a post-close restart: the settlements list is NOT persisted, so
        # the sweep re-runs — but overlay_pnl_booked (which IS persisted) guards it.
        s._brandon_hedge_settlements = []
        s._brandon_settle_hedges(spx_settle=7550.0)

        assert e1.realized_pnl == pytest.approx(expected)  # booked ONCE, not 2x
        assert s.daily_state.total_realized_pnl == pytest.approx(expected)

    def test_aggregate_only_booked_once_and_guarded_on_rerun(self):
        # Entry ABSENT from daily_state -> aggregate-only booking (2026-07-18 fix).
        # Must book once into the day total and NOT double-book on a sweep re-run.
        legs = _hedge_legs_for_entry(1)
        expected = hedge_position.settle_hedge(legs, spx_settle=7550.0).total_pnl
        s = _HedgeStub([], {1: legs})  # entry 1 NOT in daily_state
        s._brandon_settle_hedges(spx_settle=7550.0)
        assert s.daily_state.total_realized_pnl == pytest.approx(expected)  # aggregate, once
        assert 1 in s._brandon_overlay_booked
        # restart: settlements list not persisted -> sweep re-runs; guard prevents re-book
        s._brandon_hedge_settlements = []
        s._brandon_settle_hedges(spx_settle=7550.0)
        assert s.daily_state.total_realized_pnl == pytest.approx(expected)  # STILL once

    def test_flip_aggregate_then_attributed_books_once(self):
        # run1: entry absent (aggregate-only); run2 (restart): entry now PRESENT.
        # The unified guard must stop the entry-attributed path re-booking it.
        legs = _hedge_legs_for_entry(1)
        expected = hedge_position.settle_hedge(legs, spx_settle=7550.0).total_pnl
        s = _HedgeStub([], {1: legs})
        s._brandon_settle_hedges(spx_settle=7550.0)
        e1 = SimpleNamespace(entry_number=1, realized_pnl=0.0, overlay_pnl_booked=False)
        s.daily_state.entries = [e1]
        s._brandon_hedge_settlements = []
        s._brandon_settle_hedges(spx_settle=7550.0)
        assert s.daily_state.total_realized_pnl == pytest.approx(expected)  # booked ONCE
        assert e1.realized_pnl == 0.0  # not re-attributed on the flip

    def test_flip_attributed_then_aggregate_books_once(self):
        # run1: entry present (attributed); run2 (restart): entry now ABSENT.
        # The unified guard must stop the aggregate-only path re-booking it.
        e1 = SimpleNamespace(entry_number=1, realized_pnl=0.0, overlay_pnl_booked=False)
        legs = _hedge_legs_for_entry(1)
        expected = hedge_position.settle_hedge(legs, spx_settle=7550.0).total_pnl
        s = _HedgeStub([e1], {1: legs})
        s._brandon_settle_hedges(spx_settle=7550.0)
        assert e1.realized_pnl == pytest.approx(expected)
        s.daily_state.entries = []
        s._brandon_hedge_settlements = []
        s._brandon_settle_hedges(spx_settle=7550.0)
        assert s.daily_state.total_realized_pnl == pytest.approx(expected)  # booked ONCE


class TestDataRecorderV12:
    def test_fresh_db_has_realized_pnl_column(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "backtesting.db"))
        assert rec.ensure_schema() is True
        con = sqlite3.connect(rec.db_path)
        cols = [r[1] for r in con.execute("PRAGMA table_info(trade_entries)")]
        con.close()
        assert "realized_pnl" in cols
        assert SCHEMA_VERSION >= 12

    def test_update_and_read_back_realized_pnl(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "backtesting.db"))
        rec.ensure_schema()
        assert rec.record_entry({
            "date": "2026-06-30", "entry_number": 1,
            "call_credit": 100, "put_credit": 100, "total_credit": 200, "contracts": 10,
        }) is True
        # realized_pnl starts NULL, then gets set at settlement.
        con = sqlite3.connect(rec.db_path)
        assert con.execute("SELECT realized_pnl FROM trade_entries "
                           "WHERE date=? AND entry_number=?", ("2026-06-30", 1)).fetchone()[0] is None
        con.close()
        assert rec.update_entry_realized_pnl("2026-06-30", 1, -337.5) is True
        con = sqlite3.connect(rec.db_path)
        got = con.execute("SELECT realized_pnl FROM trade_entries "
                          "WHERE date=? AND entry_number=?", ("2026-06-30", 1)).fetchone()[0]
        con.close()
        assert got == pytest.approx(-337.5)

    def test_migration_adds_column_to_preexisting_db(self, tmp_path):
        # Build a minimal pre-v12 trade_entries (no realized_pnl) + schema_info=11.
        db = tmp_path / "old.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE trade_entries (date TEXT NOT NULL, entry_number INTEGER "
                    "NOT NULL, total_credit REAL, contracts INTEGER NOT NULL DEFAULT 1, "
                    "PRIMARY KEY (date, entry_number))")
        con.execute("CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO schema_info VALUES ('version', '11')")
        con.execute("INSERT INTO trade_entries (date, entry_number, total_credit, contracts) "
                    "VALUES ('2026-06-01', 1, 500, 10)")
        con.commit()
        con.close()

        rec = DataRecorder(str(db))
        assert rec.ensure_schema() is True
        con = sqlite3.connect(db)
        cols = [r[1] for r in con.execute("PRAGMA table_info(trade_entries)")]
        # existing row preserved, new column present + NULL for it.
        val = con.execute("SELECT realized_pnl FROM trade_entries "
                          "WHERE date='2026-06-01' AND entry_number=1").fetchone()[0]
        con.close()
        assert "realized_pnl" in cols
        assert val is None
