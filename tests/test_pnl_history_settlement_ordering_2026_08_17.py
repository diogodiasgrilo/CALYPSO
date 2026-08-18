"""2026-08-17: the dashboard's "today's P&L" showed +$78.40 all evening after
B's true settled total was -$76.60 — exactly the -$155.00 combined loss on
two Brandon defensive-overlay hedges that settled a few seconds AFTER the
"final" pnl_history point had already been written and saved.

Root cause: check_after_hours_settlement() (bots/hydra/strategy.py) wrote
that final point itself, but main.py calls log_daily_summary() right after
it returns True — and for a Brandon variant, THAT call is what settles the
hedges (BrandonHydraStrategy.log_daily_summary folds their P&L into
total_realized_pnl before calling super()). So the point recorded a
pre-hedge-settlement snapshot with nothing left to correct it.

Fix: the final-point write moved to base_strategy.py's log_daily_summary()
(MEICStrategy, the method every concrete strategy eventually calls via
super()), using the SAME net_pnl the real alert/Sheets/DB row already
report — which by construction cannot be written before a subclass's own
settlement work, since that work runs before the super() call reaches here.

These tests exercise the REAL log_daily_summary() and
check_after_hours_settlement() methods (not hand-derived expected numbers),
with just the side-effecting collaborators (Sheets logging, alerts, EUR
conversion, cumulative-metrics persistence) stubbed out.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.market_hours import get_us_market_time  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _make_strategy_for_summary(tmp_path: Path, *, realized_pnl: float, commission: float) -> HydraStrategy:
    """A HydraStrategy instance with just enough real state for
    log_daily_summary() (base_strategy.py) to run for real — collaborators
    with heavy external dependencies (Sheets, alerts, FX rates, cumulative
    persistence) are stubbed; the pnl_history logic under test is NOT."""
    s = HydraStrategy.__new__(HydraStrategy)

    ds = MagicMock()
    ds.date = "2026-08-17"
    ds.entries_completed = 2
    ds.entries_failed = 0
    ds.entries_skipped = 5
    ds.total_credit_received = 350.0
    ds.total_realized_pnl = realized_pnl
    ds.total_commission = commission
    ds.call_stops_triggered = 0
    ds.put_stops_triggered = 0
    ds.double_stops = 0
    ds.circuit_breaker_opens = 0
    ds.one_sided_entries = 0
    ds.trend_overrides = 0
    ds.credit_gate_skips = 0
    ds.stops_avoided_mkt036 = 0
    ds.entries = []
    s.daily_state = ds

    s._pnl_history = []
    s.cumulative_metrics = {
        "cumulative_pnl": 0.0,
        "total_entries": 0,
        "total_credit_collected": 0.0,
        "total_stops": 0,
        "double_stops": 0,
        "daily_returns": [],
    }
    s._pending_fill_corrections = []
    s.trade_logger = None  # skip the Google Sheets branch entirely
    s.alert_service = None  # _send_variant_comparison_summary no-ops on this
    s.market_data = SimpleNamespace(
        spx_open=None, spx_high=None, spx_low=None,
        vix_open=None, vix_high=None, vix_low=None,
    )
    s.current_price = 7745.36
    s.current_vix = 15.0
    s.contracts_per_entry = 7
    s._early_close_triggered = False
    s._early_close_time = None
    s._settlement_reconciliation_complete = True
    s.state_file = str(tmp_path / "hydra_state.json")

    # _save_state_to_disk() requirements (real call — proves the point is
    # actually PERSISTED, not just held in memory), matching the fixture
    # pattern already proven in test_pnl_history_calendar_fix.py.
    s.state = MagicMock()
    s.state.value = "MONITORING"
    s._next_entry_index = 0
    s.entry_times = []
    s._base_entry_count = 0
    s._conditional_entry_times = []
    s.strategy_config = {}
    s.short_only_stop = False

    # Collaborators stubbed to isolate the pnl_history logic under test.
    s._get_total_saxo_pnl = lambda: 0.0
    s._check_pnl_sanity = lambda *a, **kw: None
    s._calculate_capital_deployed = lambda: 500.0
    s._send_daily_summary = lambda: None
    s._read_fx_rate = lambda *a, **kw: None
    s._save_cumulative_metrics = lambda *a, **kw: None
    s._get_effective_stop_level = lambda *a, **kw: None
    return s


def _read_state_pnl_history(s: HydraStrategy) -> list:
    return json.loads(Path(s.state_file).read_text())["pnl_history"]


class TestLogDailySummaryWritesCorrectFinalPoint:
    def test_final_point_uses_post_settlement_net_pnl(self, tmp_path):
        # Simulates the REAL Brandon sequence: total_realized_pnl already
        # reflects hedge settlement by the time log_daily_summary() is
        # called (BrandonHydraStrategy.log_daily_summary settles hedges
        # BEFORE calling super().log_daily_summary(), which is what's under
        # test here) — the true 2026-08-17 numbers.
        s = _make_strategy_for_summary(tmp_path, realized_pnl=20.0, commission=96.60)
        s.log_daily_summary()

        assert len(s._pnl_history) == 1
        assert s._pnl_history[-1]["pnl"] == pytest.approx(-76.60, abs=0.01)

        # And it's actually persisted to disk, not just in-memory.
        history = _read_state_pnl_history(s)
        assert history[-1]["pnl"] == pytest.approx(-76.60, abs=0.01)

    def test_reproduces_the_real_incident_numbers_exactly(self, tmp_path):
        # The actual 2026-08-17 figures: gross $20.00 (IC legs) minus $96.60
        # commission = -$76.60 net. The stale dashboard figure was +$78.40 —
        # confirm the fix does NOT land on that number.
        s = _make_strategy_for_summary(tmp_path, realized_pnl=20.0, commission=96.60)
        s.log_daily_summary()
        pnl = s._pnl_history[-1]["pnl"]
        assert pnl == pytest.approx(-76.60, abs=0.01)
        assert pnl != pytest.approx(78.40, abs=0.01)

    def test_same_minute_overwrites_rather_than_duplicates(self, tmp_path):
        s = _make_strategy_for_summary(tmp_path, realized_pnl=20.0, commission=96.60)
        now = get_us_market_time()
        time_key = now.strftime("%H:%M")
        # A regular heartbeat-driven point already exists for this exact minute.
        s._pnl_history = [{"time": time_key, "pnl": 175.0}]
        s.log_daily_summary()
        assert len(s._pnl_history) == 1  # overwritten, not appended
        assert s._pnl_history[0]["pnl"] == pytest.approx(-76.60, abs=0.01)

    def test_different_minute_appends_a_new_point(self, tmp_path):
        s = _make_strategy_for_summary(tmp_path, realized_pnl=20.0, commission=96.60)
        s._pnl_history = [{"time": "20:55", "pnl": 175.0}]
        s.log_daily_summary()
        assert len(s._pnl_history) == 2
        assert s._pnl_history[0]["pnl"] == 175.0  # untouched
        assert s._pnl_history[-1]["pnl"] == pytest.approx(-76.60, abs=0.01)


class TestCheckAfterHoursSettlementNoLongerWritesPnlHistory:
    """The removed call sites (bots/hydra/strategy.py) must no longer touch
    _pnl_history at all — that responsibility moved entirely to
    log_daily_summary(). Exercises the REAL method, not a re-derivation."""

    def _make_settlement_strategy(self, tmp_path, *, expired_credit: float = 0.0):
        s = HydraStrategy.__new__(HydraStrategy)
        ds = MagicMock()
        ds.date = "2026-08-17"
        ds.total_realized_pnl = 175.0  # pre-hedge-settlement snapshot
        ds.total_commission = 96.60
        ds.entries = []
        s.daily_state = ds
        s._pnl_history = []
        s.state_file = str(tmp_path / "hydra_state.json")
        s._settlement_reconciliation_complete = False
        s._expected_position_quantities = lambda: {}  # "no tracked conids" branch
        s._process_expired_credits = lambda: expired_credit
        s._settlement_deferred = False
        s._save_state_to_disk = MagicMock()
        return s

    def test_no_tracked_conids_branch_does_not_touch_pnl_history(self, tmp_path):
        s = self._make_settlement_strategy(tmp_path, expired_credit=50.0)
        result = s.check_after_hours_settlement()
        assert result is True
        assert s._pnl_history == []  # untouched — no premature write
        s._save_state_to_disk.assert_called()  # other state changes still saved

    def test_no_tracked_conids_zero_expired_credit_also_untouched(self, tmp_path):
        s = self._make_settlement_strategy(tmp_path, expired_credit=0.0)
        result = s.check_after_hours_settlement()
        assert result is True
        assert s._pnl_history == []
