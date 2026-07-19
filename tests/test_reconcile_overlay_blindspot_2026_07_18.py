"""2026-07-18: reconciliation blind-spot fix — overlay-aware + pre-feature floor.

Two documented blind spots in the identity
``sum(trade_entries.realized_pnl) + unattributed_overlay == gross_pnl`` are removed:
  1. Brandon defensive-overlay hedge P&L booked AGGREGATE-ONLY (hedged entry absent
     from daily_state at settle) lands in gross_pnl but on no entry.
  2. Pre-feature days (< 2026-07-02) carry unbooked 0.0 realized_pnl.
Derived from THIS process's settlement sweep so a genuine double-book still surfaces.
"""
import datetime as _dt
import logging
import os
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.slot_edge import analyze_slots  # noqa: E402
from shared.data_recorder import DataRecorder, SCHEMA_VERSION  # noqa: E402


# ============================================================ _unattributed_overlay_pnl
class TestUnattributedOverlayPnl:
    def test_base_strategy_is_always_zero(self):
        s = HydraStrategy.__new__(HydraStrategy)
        assert s._unattributed_overlay_pnl() == 0.0

    def test_brandon_sums_only_settlements_whose_entry_is_absent(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.daily_state = SimpleNamespace(entries=[
            SimpleNamespace(entry_number=1), SimpleNamespace(entry_number=2)])
        s._brandon_hedge_settlements = [
            SimpleNamespace(entry_number=1, total_pnl=-661.0),   # present -> attributed (excluded)
            SimpleNamespace(entry_number=5, total_pnl=-1200.0),  # absent  -> unattributed
            SimpleNamespace(entry_number=7, total_pnl=-500.0),   # absent  -> unattributed
        ]
        assert s._unattributed_overlay_pnl() == -1700.0

    def test_brandon_zero_when_all_present(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.daily_state = SimpleNamespace(entries=[SimpleNamespace(entry_number=3)])
        s._brandon_hedge_settlements = [SimpleNamespace(entry_number=3, total_pnl=-661.0)]
        assert s._unattributed_overlay_pnl() == 0.0

    def test_brandon_zero_when_no_settlements_or_attr_missing(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.daily_state = SimpleNamespace(entries=[])
        s._brandon_hedge_settlements = []
        assert s._unattributed_overlay_pnl() == 0.0
        s2 = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s2.daily_state = SimpleNamespace(entries=[])
        assert s2._unattributed_overlay_pnl() == 0.0  # attr missing -> getattr default


# ============================================================ RECONCILE guard identity
def _recon_strategy(entries_realized, total, unattributed):
    s = HydraStrategy.__new__(HydraStrategy)
    s.daily_state = SimpleNamespace(
        entries=[SimpleNamespace(entry_number=i + 1, realized_pnl=r,
                                 call_side_skipped=False, put_side_skipped=False)
                 for i, r in enumerate(entries_realized)],
        total_realized_pnl=total)
    s._data_recorder = MagicMock()
    s._unattributed_overlay_pnl = lambda: unattributed
    return s


class TestReconcileGuard:
    def test_overlay_day_reconciles_no_drift(self, caplog):
        # per-entry sum 2800 + unattributed -2408 == total 392 -> OK (the 07-07 case)
        s = _recon_strategy([1400.0, 1400.0], total=392.0, unattributed=-2408.0)
        with caplog.at_level(logging.INFO):
            s._record_entry_realized_pnl("2026-07-07")
        msg = " ".join(r.message for r in caplog.records)
        assert "RECONCILE realized_pnl OK" in msg
        assert "DRIFT" not in msg
        assert "aggregate-only overlay" in msg

    def test_clean_day_reconciles_plain(self, caplog):
        s = _recon_strategy([500.0, 300.0], total=800.0, unattributed=0.0)
        with caplog.at_level(logging.INFO):
            s._record_entry_realized_pnl("2026-07-14")
        msg = " ".join(r.message for r in caplog.records)
        assert "RECONCILE realized_pnl OK" in msg
        assert "aggregate-only overlay" not in msg  # plain path when no overlay

    def test_genuine_drift_still_flagged(self, caplog):
        # per-entry 1000 + unattributed 0 != total 1500 -> a real booking miss
        s = _recon_strategy([1000.0], total=1500.0, unattributed=0.0)
        with caplog.at_level(logging.WARNING):
            s._record_entry_realized_pnl("2026-07-08")
        assert "RECONCILE realized_pnl DRIFT" in " ".join(r.message for r in caplog.records)

    def test_double_book_not_masked(self, caplog):
        # A restart re-ran the sweep and DOUBLE-booked the overlay into the aggregate
        # (total inflated by 2x the overlay), but _unattributed_overlay_pnl() reflects
        # only THIS process's single sweep (1x). So the identity does NOT reconcile ->
        # drift still surfaces rather than being masked.
        overlay = -2408.0
        s = _recon_strategy([2800.0], total=2800.0 + 2 * overlay, unattributed=overlay)
        with caplog.at_level(logging.WARNING):
            s._record_entry_realized_pnl("2026-07-07")
        assert "RECONCILE realized_pnl DRIFT" in " ".join(r.message for r in caplog.records)


# ============================================================ v13 migration + column
class TestV13Migration:
    def test_fresh_db_has_unattributed_overlay_column(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "backtesting.db"))
        assert rec.ensure_schema() is True
        con = sqlite3.connect(rec.db_path)
        cols = [r[1] for r in con.execute("PRAGMA table_info(daily_summaries)")]
        con.close()
        assert "unattributed_overlay_pnl" in cols
        assert SCHEMA_VERSION >= 13

    def test_migration_adds_column_to_v12_db(self, tmp_path):
        db = tmp_path / "old.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE daily_summaries (date TEXT PRIMARY KEY, gross_pnl REAL, net_pnl REAL)")
        con.execute("CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO schema_info VALUES ('version', '12')")
        con.execute("INSERT INTO daily_summaries (date, gross_pnl, net_pnl) VALUES ('2026-07-01', 925, 787)")
        con.commit(); con.close()
        assert DataRecorder(str(db)).ensure_schema() is True
        con = sqlite3.connect(db)
        cols = [r[1] for r in con.execute("PRAGMA table_info(daily_summaries)")]
        val = con.execute("SELECT unattributed_overlay_pnl FROM daily_summaries WHERE date='2026-07-01'").fetchone()[0]
        con.close()
        assert "unattributed_overlay_pnl" in cols
        assert val is None  # historical row stays NULL

    def test_record_and_read_back_unattributed(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "bt.db"))
        rec.ensure_schema()
        assert rec.record_daily_summary({
            "date": "2026-07-07", "gross_pnl": 392.0, "net_pnl": 93.0,
            "unattributed_overlay_pnl": -2408.0,
        }) is True
        con = sqlite3.connect(rec.db_path)
        got = con.execute("SELECT unattributed_overlay_pnl FROM daily_summaries WHERE date='2026-07-07'").fetchone()[0]
        con.close()
        assert got == pytest.approx(-2408.0)


# ===================================== production writer forwards the key (integration)
class TestRecordDailySummaryForwardsOverlay:
    """The ONLY production writer of daily_summaries is
    HydraStrategy._record_daily_summary_to_db, which builds its OWN payload dict.
    Regression for the 2026-07-18 review: it must FORWARD unattributed_overlay_pnl
    from get_daily_summary(), else the v13 column writes NULL on every real day and
    the slot_edge/audit half of the fix is inoperative."""

    def test_payload_forwards_unattributed_overlay(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s._data_recorder = MagicMock()
        s._data_recorder.get_yesterday_spx_close.return_value = None
        s._resolve_spx_close = MagicMock(return_value=7500.0)
        s._daily_summary_is_stale = MagicMock(return_value=False)
        s.get_daily_summary = MagicMock(return_value={
            "total_pnl": 392.0, "total_commission": 299.0, "entries_completed": 2,
            "long_salvage_revenue": 0.0, "unattributed_overlay_pnl": -2408.0,
        })
        s.daily_state = SimpleNamespace(date="2026-07-07", entries=[])
        s.market_data = SimpleNamespace(spx_open=7480.0, spx_high=7520.0, spx_low=7460.0, vix_open=18.0)
        s.current_vix = 18.5
        s.contracts_per_entry = 10
        now = _dt.datetime(2026, 7, 7, 16, 0, 0)
        with patch("bots.hydra.strategy.get_us_market_time", return_value=now), \
             patch("shared.event_calendar.get_economic_events_for_date", return_value=[]), \
             patch("shared.event_calendar.is_opex_week", return_value=False):
            s._record_daily_summary_to_db()
        s._data_recorder.record_daily_summary.assert_called_once()
        payload = s._data_recorder.record_daily_summary.call_args[0][0]
        assert payload["unattributed_overlay_pnl"] == -2408.0  # forwarded, not dropped
        assert payload["gross_pnl"] == 392.0                    # == total_realized_pnl at settle


# ============================================================ slot_edge cross-check
def _seed(con):
    con.execute("CREATE TABLE trade_entries (date TEXT, entry_number INT, entry_time TEXT,"
                " call_credit REAL, put_credit REAL, total_credit REAL, contracts INT, realized_pnl REAL)")
    con.execute("CREATE TABLE trade_stops (date TEXT, entry_number INT, side TEXT, net_pnl REAL, stop_time TEXT)")
    con.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL, gross_pnl REAL, unattributed_overlay_pnl REAL)")
    # Reliable overlay day (>= 07-02): per-entry 2800, overlay -2408 unattributed, gross 392.
    con.execute("INSERT INTO trade_entries VALUES ('2026-07-07',1,'2026-07-07 10:45:00',0,0,500,10,1400.0)")
    con.execute("INSERT INTO trade_entries VALUES ('2026-07-07',2,'2026-07-07 11:15:00',0,0,500,10,1400.0)")
    con.execute("INSERT INTO daily_summaries VALUES ('2026-07-07', 93.0, 392.0, -2408.0)")
    # Pre-feature day (< 07-02): per-entry 0 (unbooked), gross 925 -> must be EXCLUDED from xcheck.
    con.execute("INSERT INTO trade_entries VALUES ('2026-07-01',1,'2026-07-01 10:45:00',0,0,200,10,0.0)")
    con.execute("INSERT INTO daily_summaries VALUES ('2026-07-01', 900.0, 925.0, NULL)")
    con.commit()


class TestSlotEdgeCrossCheck:
    def _analyze(self, tmp_path):
        p = str(tmp_path / "bt.db")
        con = sqlite3.connect(p); _seed(con); con.close()
        return analyze_slots(p)

    def test_overlay_and_prefeature_reconcile_in_xcheck(self, tmp_path):
        r = self._analyze(tmp_path)
        assert r["ok"]
        # reliable-window, overlay-adjusted: per-entry 2800 == gross(392) - overlay(-2408) = 2800
        assert r["scored_total_xcheck"] == pytest.approx(2800.0)
        assert r["daily_gross_xcheck"] == pytest.approx(2800.0)
        assert abs(r["scored_total_xcheck"] - r["daily_gross_xcheck"]) < 0.01  # reconciles

    def test_display_totals_still_include_all_days(self, tmp_path):
        r = self._analyze(tmp_path)
        # display scored includes pre-feature 0 -> 2800; display gross includes 925 -> 1317
        assert r["scored_total"] == pytest.approx(2800.0)
        assert r["daily_gross_total"] == pytest.approx(1317.0)

    def test_old_db_without_overlay_column_still_works(self, tmp_path):
        # column guard: a pre-v13 DB (no unattributed_overlay_pnl) must not crash.
        p = str(tmp_path / "old.db")
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE trade_entries (date TEXT, entry_number INT, entry_time TEXT,"
                    " call_credit REAL, put_credit REAL, total_credit REAL, contracts INT, realized_pnl REAL)")
        con.execute("CREATE TABLE trade_stops (date TEXT, entry_number INT, side TEXT, net_pnl REAL, stop_time TEXT)")
        con.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL, gross_pnl REAL)")
        con.execute("INSERT INTO trade_entries VALUES ('2026-07-14',1,'2026-07-14 10:45:00',0,0,500,10,500.0)")
        con.execute("INSERT INTO daily_summaries VALUES ('2026-07-14', 480.0, 500.0)")
        con.commit(); con.close()
        r = analyze_slots(p)
        assert r["ok"]
        assert r["daily_gross_xcheck"] == pytest.approx(500.0)  # no overlay col -> gross only
