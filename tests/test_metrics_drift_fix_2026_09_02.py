"""2026-09-02: fleet-wide metrics/DB drift audit (follow-up to the 2026-07-20
self-heal) — two real, confirmed bugs closed here.

BUG 1 (calendar daily-P&L double-counting, D/E): a carried multi-day calendar's
``total_pnl`` re-includes the still-OPEN position's live unrealized mark on
EVERY day it's held (``_get_total_saxo_pnl`` sums ``active_entries``, and a
carried entry is re-attached to ``daily_state.entries`` on every reset — see
``CalendarStrategyBase._reset_for_new_day``). ``_book_daily_cumulative`` and
``_record_daily_summary_to_db`` both booked that inflated value as if it were
a fresh day's result, so the SAME open trade's running mark got counted again
and again for every day it stayed open — confirmed on live variant E, whose
``hydra_metrics.json`` showed a -$2,599.20 lifetime loss when the real,
``dc_outcomes``-verified result was only about -$151 (a ~17x overstatement).
Fixed via an overridable ``_cumulative_tracking_pnl(summary, net_pnl)`` hook:
a no-op for A/B/C/F/G (same-day strategies, where ``total_pnl`` already IS
today's own result), overridden by ``CalendarStrategyBase`` to book $0 on a
pure hold day and the real close P&L only on the day a position actually
closes.

BUG 2 (total_stops never covered by the 2026-07-20 self-heal, all variants):
``_reconcile_cumulative_metrics_from_db`` (added 2026-07-20) only ever
re-derived ``cumulative_pnl``/``daily_returns``/win-loss from the DB —
``total_stops``/``double_stops`` kept the exact same "increment once,
idempotent-by-date, never revisited" fragility that caused the original
drift. Root cause of the SIZE of the gap: Brandon's take-profit/GEX-breach
exits and the shared MKT-018/047 early-close/flatten path all close a
position through ``_close_entry_early`` (writing a real ``trade_stops`` row),
never through ``_execute_stop_loss`` (the only place that increments the
in-memory counters) — confirmed live on variant C: metrics file said 7 stops,
``trade_stops`` actually held 29 rows. Fixed by extending the same self-heal
to also re-derive ``total_stops``/``double_stops`` from ``trade_stops``, using
the "genuine stop" definition already established by
``shared/sheets_db_shim.py`` (``exit_reason IN ('stop_loss','gex_breach')``,
excluding take-profit wins and early-close/EOD flattens; a legacy NULL
``exit_reason`` — pre-v11 rows, written before Brandon's TP/breach paths
existed — falls back to ``net_pnl < 0``).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy as D  # noqa: E402
from bots.hydra.spy_double_calendar_strategy import SpyDoubleCalendarStrategy as E  # noqa: E402
from shared.data_recorder import DataRecorder  # noqa: E402


# ═══════════════════════════ BUG 1: _cumulative_tracking_pnl hook ═══════════════════════════
class TestCumulativeTrackingPnlHook:
    def test_base_hook_is_identity(self):
        """A/B/C/F/G: the default hook is a pure pass-through — no behavior change."""
        s = HydraStrategy.__new__(HydraStrategy)
        assert s._cumulative_tracking_pnl({}, 456.78) == 456.78
        assert s._cumulative_tracking_pnl({}, -1234.56) == -1234.56
        assert s._cumulative_tracking_pnl({}, 0.0) == 0.0

    def test_calendar_hook_ignores_the_inflated_net_pnl_on_a_hold_day(self):
        """The whole point of the fix: a carried position's net_pnl argument
        (today's live unrealized re-snapshot) must be IGNORED — the hook derives
        from daily_state's own realized-only fields instead."""
        s = D.__new__(D)
        s.daily_state = SimpleNamespace(total_realized_pnl=0.0, total_commission=0.0)
        # Even a wildly inflated net_pnl (what total_pnl would show mid-hold) must
        # not leak through — nothing closed today, so nothing should be booked.
        assert s._cumulative_tracking_pnl({}, 9999.99) == 0.0
        assert s._cumulative_tracking_pnl({}, -9999.99) == 0.0

    def test_calendar_hook_returns_real_close_pnl_net_of_commission(self):
        s = D.__new__(D)
        s.daily_state = SimpleNamespace(total_realized_pnl=-130.0, total_commission=5.0)
        assert s._cumulative_tracking_pnl({}, 42.0) == -135.0  # arg ignored, daily_state wins

    def test_spy_calendar_e_inherits_the_same_override(self):
        """E subclasses CalendarStrategyBase too (via SpyDoubleCalendarStrategy) and
        must get the identical fix — no separate E-specific override exists."""
        s = E.__new__(E)
        s.daily_state = SimpleNamespace(total_realized_pnl=21.0, total_commission=1.0)
        assert s._cumulative_tracking_pnl({}, 500.0) == 20.0

    def test_negative_control_calendar_override_is_not_a_dead_no_op(self):
        """Proves the calendar override actually DIFFERS from the base's identity
        hook for the exact inputs that matter — if a future edit accidentally made
        it `return net_pnl` again (silently un-fixing the bug), this fails."""
        s = D.__new__(D)
        s.daily_state = SimpleNamespace(total_realized_pnl=0.0, total_commission=0.0)
        tracked = s._cumulative_tracking_pnl({}, 9999.99)
        base = HydraStrategy._cumulative_tracking_pnl(s, {}, 9999.99)
        assert tracked != base
        assert tracked == 0.0 and base == 9999.99


class TestLogDailySummaryWiresTheHook:
    """log_daily_summary is heavy (Sheets/alerts/state-save side effects) to invoke
    directly — pin the wiring at the source level instead, matching this codebase's
    own established pattern for exactly this kind of hook-wiring regression (see
    TestMainPyUsesTheHook in test_calendar_carry_and_activity_gate_2026_08_18.py)."""

    def test_book_daily_cumulative_call_routes_through_the_hook(self):
        import inspect
        import bots.hydra.base_strategy as base_mod
        source = inspect.getsource(base_mod.MEICStrategy.log_daily_summary)
        assert "self._cumulative_tracking_pnl(summary, net_pnl)" in source
        assert "self._book_daily_cumulative(summary, net_pnl, capital_deployed)" not in source


# ═══════════════════════ BUG 1: _record_daily_summary_to_db DB-write routing ═══════════════════════
class TestRecordDailySummaryToDbRouting:
    """Mirrors TestRecordDailySummaryForwardsOverlay's established pattern
    (test_reconcile_overlay_blindspot_2026_07_18.py) — mock the strategy's
    dependencies, call the REAL _record_daily_summary_to_db, inspect the payload
    handed to DataRecorder.record_daily_summary."""

    def _base_mocks(self, s, total_pnl, total_commission):
        s._data_recorder = MagicMock()
        s._data_recorder.get_yesterday_spx_close.return_value = None
        s._resolve_spx_close = MagicMock(return_value=7500.0)
        s._daily_summary_is_stale = MagicMock(return_value=False)
        s.get_daily_summary = MagicMock(return_value={
            "total_pnl": total_pnl, "total_commission": total_commission,
            "entries_completed": 0, "long_salvage_revenue": 0.0,
        })
        s.daily_state = SimpleNamespace(date="2026-09-01", entries=[])
        s.market_data = SimpleNamespace(spx_open=7480.0, spx_high=7520.0, spx_low=7460.0, vix_open=18.0)
        s.current_vix = 18.5
        s.contracts_per_entry = 1
        return s

    def _run(self, s):
        now = _dt.datetime(2026, 9, 1, 16, 0, 0)
        with patch("bots.hydra.strategy.get_us_market_time", return_value=now), \
             patch("shared.event_calendar.get_economic_events_for_date", return_value=[]), \
             patch("shared.event_calendar.is_opex_week", return_value=False):
            s._record_daily_summary_to_db()
        return s._data_recorder.record_daily_summary.call_args[0][0]

    def test_noncalendar_payload_unchanged_by_the_fix(self):
        """A/B/C/F/G negative control: gross/net must equal the exact pre-fix formula."""
        s = HydraStrategy.__new__(HydraStrategy)
        self._base_mocks(s, total_pnl=392.0, total_commission=299.0)
        payload = self._run(s)
        assert payload["gross_pnl"] == 392.0
        assert payload["net_pnl"] == 392.0 - 299.0

    def test_calendar_hold_day_writes_zero_despite_inflated_total_pnl(self):
        """The exact E-shaped bug: total_pnl carries a live unrealized mark from a
        still-open position (nothing closed today) — the DB row must show $0, not
        that mark, so it can't be double/triple/dozens-counted across the hold."""
        s = D.__new__(D)
        self._base_mocks(s, total_pnl=-620.0, total_commission=0.0)  # inflated, nothing realized
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        payload = self._run(s)
        assert payload["gross_pnl"] == 0.0
        assert payload["net_pnl"] == 0.0

    def test_calendar_close_day_writes_the_real_settled_pnl(self):
        s = D.__new__(D)
        self._base_mocks(s, total_pnl=-130.0, total_commission=5.0)
        s.daily_state.total_realized_pnl = -130.0
        s.daily_state.total_commission = 5.0
        payload = self._run(s)
        assert payload["gross_pnl"] == -130.0
        assert payload["net_pnl"] == -135.0


# ═══════════════════════════ BUG 2: total_stops/double_stops self-heal ═══════════════════════════
def _strategy_with_db(tmp_path, daily_rows, stop_rows, metrics):
    rec = DataRecorder(str(tmp_path / "bt.db"))
    rec.ensure_schema()
    for r in daily_rows:
        rec.record_daily_summary(r)
    for r in stop_rows:
        rec.record_stop(r)
    s = HydraStrategy.__new__(HydraStrategy)
    s._data_recorder = rec
    s.metrics_file = str(tmp_path / "hydra_metrics.json")
    s.cumulative_metrics = metrics
    return s


def _stop(date, entry_number, side, net_pnl, exit_reason):
    return {"date": date, "entry_number": entry_number, "side": side,
            "net_pnl": net_pnl, "exit_reason": exit_reason}


class TestReconcileTotalStopsFromDb:
    def test_corrects_stale_total_stops_reproducing_the_c_incident(self, tmp_path):
        # Shape of the real C incident: a handful of true stop_loss/gex_breach
        # exits, plus a much larger pile of take_profit wins that must NOT count.
        stops = (
            [_stop("2026-08-01", i, "put", -50.0, "stop_loss") for i in range(1, 4)]     # 3
            + [_stop("2026-08-01", i, "call", -40.0, "gex_breach") for i in range(4, 6)]  # 2
            + [_stop("2026-08-02", i, "put", 30.0, "take_profit") for i in range(1, 8)]   # 7 wins, excluded
        )
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-08-01", "gross_pnl": -270, "net_pnl": -270.0, "commission": 0},
             {"date": "2026-08-02", "gross_pnl": 210, "net_pnl": 210.0, "commission": 0}],
            stops,
            {"cumulative_pnl": -60.0, "total_stops": 7, "double_stops": 0,
             "winning_days": 1, "losing_days": 1,
             "daily_returns": [{"date": "2026-08-01", "net_pnl": -270.0, "capital_deployed": 7000},
                                {"date": "2026-08-02", "net_pnl": 210.0, "capital_deployed": 7000}]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-08-02")
        assert s.cumulative_metrics["total_stops"] == 5  # 3 stop_loss + 2 gex_breach, NOT the 7 take_profits
        saved = json.load(open(s.metrics_file))
        assert saved["total_stops"] == 5

    def test_double_stops_counts_entries_with_both_sides_genuinely_stopped(self, tmp_path):
        stops = [
            _stop("2026-08-05", 1, "call", -50.0, "stop_loss"),
            _stop("2026-08-05", 1, "put", -60.0, "stop_loss"),   # entry 1: BOTH sides -> double
            _stop("2026-08-05", 2, "call", -40.0, "stop_loss"),  # entry 2: one side only
            _stop("2026-08-05", 3, "call", 25.0, "take_profit"),
            _stop("2026-08-05", 3, "put", -30.0, "gex_breach"),  # entry 3: TP + breach -> only 1 genuine side
        ]
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-08-05", "gross_pnl": -155, "net_pnl": -155.0, "commission": 0}],
            stops,
            {"cumulative_pnl": -155.0, "total_stops": 0, "double_stops": 0,
             "winning_days": 0, "losing_days": 1,
             "daily_returns": [{"date": "2026-08-05", "net_pnl": -155.0, "capital_deployed": 7000}]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-08-05")
        assert s.cumulative_metrics["total_stops"] == 4   # 2(entry1) + 1(entry2) + 1(entry3 breach)
        assert s.cumulative_metrics["double_stops"] == 1  # only entry 1

    def test_legacy_null_exit_reason_falls_back_to_net_pnl_sign(self, tmp_path):
        stops = [
            _stop("2026-05-01", 1, "put", -75.0, None),  # pre-v11 loss -> counts
            _stop("2026-05-01", 2, "call", 15.0, None),  # pre-v11 gain (shouldn't happen, but must not count)
        ]
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-05-01", "gross_pnl": -60, "net_pnl": -60.0, "commission": 0}],
            stops,
            {"cumulative_pnl": -60.0, "total_stops": 0, "double_stops": 0,
             "winning_days": 0, "losing_days": 1,
             "daily_returns": [{"date": "2026-05-01", "net_pnl": -60.0, "capital_deployed": 7000}]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-05-01")
        assert s.cumulative_metrics["total_stops"] == 1

    def test_early_close_never_counts_as_a_stop(self, tmp_path):
        stops = [_stop("2026-08-10", 1, "put", -300.0, "early_close")]  # MKT-018/047 flatten, not a stop
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-08-10", "gross_pnl": -300, "net_pnl": -300.0, "commission": 0}],
            stops,
            {"cumulative_pnl": -300.0, "total_stops": 0, "double_stops": 0,
             "winning_days": 0, "losing_days": 1,
             "daily_returns": [{"date": "2026-08-10", "net_pnl": -300.0, "capital_deployed": 7000}]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-08-10")
        assert s.cumulative_metrics["total_stops"] == 0

    def test_noop_when_stops_already_consistent(self, tmp_path):
        stops = [_stop("2026-08-12", 1, "put", -50.0, "stop_loss")]
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-08-12", "gross_pnl": -50, "net_pnl": -50.0, "commission": 0}],
            stops,
            {"cumulative_pnl": -50.0, "total_stops": 1, "double_stops": 0,
             "winning_days": 0, "losing_days": 1,
             "daily_returns": [{"date": "2026-08-12", "net_pnl": -50.0, "capital_deployed": 7000}]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-08-12")
        assert not os.path.exists(s.metrics_file)  # already consistent -> no write at all

    def test_negative_control_stale_stops_alone_still_triggers_a_save(self, tmp_path):
        """Even when cumulative_pnl/daily_returns are ALREADY correct (the pre-fix
        no-op condition), a total_stops mismatch alone must still trigger the save
        — this is what the 2026-07-20 version would have missed entirely."""
        stops = [_stop("2026-08-14", 1, "put", -50.0, "gex_breach")]
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-08-14", "gross_pnl": -50, "net_pnl": -50.0, "commission": 0}],
            stops,
            {"cumulative_pnl": -50.0, "total_stops": 0, "double_stops": 0,  # WRONG: DB has 1 genuine stop
             "winning_days": 0, "losing_days": 1,
             "daily_returns": [{"date": "2026-08-14", "net_pnl": -50.0, "capital_deployed": 7000}]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-08-14")
        assert s.cumulative_metrics["total_stops"] == 1
        assert os.path.exists(s.metrics_file)

    def test_still_safe_with_no_data_recorder(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s._data_recorder = None
        s._reconcile_cumulative_metrics_from_db("2026-09-02")  # must not raise
