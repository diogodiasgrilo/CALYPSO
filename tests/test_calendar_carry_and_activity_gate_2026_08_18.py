"""2026-08-18: two real bugs found investigating why variant D's open calendar
(dctm_20260813_001, entered 2026-08-13) vanished from dc_open_trades.json
sometime on 2026-08-17 with no stop/transform/settlement log evidence anywhere.

Root cause, confirmed directly against source (not just log archaeology):

BUG A (calendar_strategy_base.py:_reset_for_new_day) — the base reset
(HydraStrategy._reset_for_new_day) ends with its own _save_state_to_disk()
call, made while daily_state.entries is still EMPTY (the fresh
MEICDailyState() it just built). CalendarStrategyBase's own reset re-attaches
the carried position ONLY AFTER calling that base reset, and never re-saved —
so the sidecar was left holding an empty list for the rest of the day, with
the correct (carried) state only living in memory. Harmless as long as some
LATER save that day happens to re-persist it first; genuinely dangerous if a
restart lands in that window, since _dc_load_sidecar() on boot would read the
empty file and permanently drop the position from tracking.

BUG B (base_strategy.py:_had_trading_activity_today, called from main.py) —
the gate deciding whether to send an EOD daily summary included
`len(daily_state.entries) > 0`, which is always true for as long as ANY
multi-day position is carried (re-attached every reset) — even on a day
nothing new happened at all. Confirmed in the logs: at 00:00:36 ET on
2026-08-18 (14 seconds after the midnight reset, before market data for that
day could exist), this falsely triggered a "daily summary" send that folded
the carried position's stale unrealized mark from the reset moment into
hydra_metrics.json as if it were a realized result for 2026-08-18 -- a day
with zero actual trades.

Both fixed here. Fix A: re-save after the carry re-attach. Fix B: a
strategy-level _had_trading_activity_today() hook (base logic unchanged for
A/B/C) that CalendarStrategyBase overrides to require either a real
today-dated entry or nonzero realized P&L, not just "entries list non-empty".
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy as D  # noqa: E402
from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402
from bots.hydra.leg import LEG_NAMES, Leg  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402


def _entry(net_debit=1000.0, num=1, phase=DCPhase.CALENDAR, entry_time=None):
    e = MagicMock(spec=CalendarEntry)
    e.entry_number = num
    e.net_debit = net_debit
    e.contracts = 1
    e.unrealized_pnl = -250.0
    e.dc_phase = phase
    e.entry_time = entry_time
    e.opening_pnl = 0.0
    for flag in (
        "call_side_stopped", "put_side_stopped",
        "call_side_pivot_closed", "put_side_pivot_closed",
        "call_side_expired", "put_side_expired",
    ):
        setattr(e, flag, False)
    return e


def _strat():
    s = D.__new__(D)
    s._dc_stop_breach = {}
    s.daily_state = MagicMock()
    s.daily_state.entries = []
    s.daily_state.active_entries = []
    return s


# ─────────────── Bug A: sidecar re-save after carry re-attach ───────────────
class TestResetResavesAfterCarry:
    def _fake_base_reset_matching_real_behavior(self, s, save_calls):
        """Simulate HydraStrategy._reset_for_new_day's REAL, relevant shape:
        it rebuilds daily_state to empty, THEN calls self._save_state_to_disk()
        at its own tail — exactly the sequence that (before the fix) left the
        sidecar holding an empty list."""
        def fake(self_inner):
            s.daily_state.entries = []
            s._save_state_to_disk()
        return fake

    def test_resaves_with_carried_entry_present_not_empty(self):
        s = _strat()
        e = _entry(num=1)
        s.daily_state.entries = [e]
        s._dc_entry_is_open = lambda x: True
        save_calls = []
        s._save_state_to_disk = lambda: save_calls.append(list(s.daily_state.entries))

        with patch(
            "bots.hydra.strategy.HydraStrategy._reset_for_new_day",
            self._fake_base_reset_matching_real_behavior(s, save_calls),
        ):
            s._reset_for_new_day()

        # The base reset's own save (pre-existing, unavoidable) happens first,
        # while empty -- this is the bug's signature, still present as an
        # intermediate step.
        assert save_calls[0] == []
        # But the fix means the method does NOT end there: a further save
        # happens once the carried entry is back in daily_state.entries, so
        # by the time _reset_for_new_day() RETURNS, the last thing written
        # reflects reality.
        assert save_calls[-1] == [e]
        assert len(save_calls) == 2

    def test_no_extra_resave_when_nothing_was_carried(self):
        # Efficiency/no-op check: a day with nothing open shouldn't pay for a
        # pointless second save.
        s = _strat()
        s.daily_state.entries = []
        s._dc_entry_is_open = lambda x: True
        save_calls = []
        s._save_state_to_disk = lambda: save_calls.append(list(s.daily_state.entries))

        with patch(
            "bots.hydra.strategy.HydraStrategy._reset_for_new_day",
            self._fake_base_reset_matching_real_behavior(s, save_calls),
        ):
            s._reset_for_new_day()

        assert len(save_calls) == 1  # only the base's own save

    def test_end_to_end_sidecar_file_is_never_left_empty_on_disk(self, tmp_path):
        """Real _dc_save_sidecar() writes, real file reads -- proves the ACTUAL
        on-disk dc_open_trades.json ends up correct, not just an in-memory
        assertion. This is the exact file a restart's _dc_load_sidecar()
        would read."""
        s = _strat()
        s.state_file = str(tmp_path / "hydra_state.json")
        s._dc_loaded = True  # sidecar already loaded earlier today (not a startup race)
        e = _entry(num=1, net_debit=1590.0)
        e.strategy_id = "dctm_20260813_001"
        e.structure = "double_calendar"
        e.entry_time = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
        e.transform_credit = 0.0
        e.wing_width = 25.0
        e.is_risk_free = False
        e.transformed_at = ""
        e.is_complete = False
        e.call_spread_credit = 0.0
        e.put_spread_credit = 0.0
        e.legs = {
            name: Leg(side="short" if name.startswith("short") else "long",
                      right="call" if name.endswith("call") else "put",
                      strike=100.0)
            for name in LEG_NAMES
        }
        s.daily_state.entries = [e]
        s._dc_entry_is_open = lambda x: True

        # HydraStrategy._save_state_to_disk (the hydra_state.json half) is
        # unrelated to this bug and heavy to stand up for real -- stub it,
        # but let the REAL _dc_save_sidecar (the actual method with the bug)
        # run unmocked via the real _save_state_to_disk override chain.
        with patch("bots.hydra.strategy.HydraStrategy._save_state_to_disk", lambda self: None), \
             patch(
                 "bots.hydra.strategy.HydraStrategy._reset_for_new_day",
                 lambda self: setattr(s.daily_state, "entries", []),
             ):
            s._reset_for_new_day()

        import json
        sidecar_path = Path(tmp_path) / "dc_open_trades.json"
        assert sidecar_path.exists(), "sidecar was never written at all"
        records = json.loads(sidecar_path.read_text())
        assert len(records) == 1, (
            "sidecar left empty on disk after the reset -- a restart right now "
            "would permanently lose this position, reproducing the 2026-08-17 incident"
        )
        assert records[0]["strategy_id"] == "dctm_20260813_001"


# ─────────────── Bug B: _had_trading_activity_today ───────────────
class TestHadTradingActivityTodayBase:
    """MEICStrategy's default -- must stay byte-identical to the logic that
    used to be inlined in main.py (A/B/C behavior unchanged by this fix)."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.daily_state = MagicMock()
        s.daily_state.entries_completed = 0
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.entries = []
        return s

    def test_false_when_nothing_happened(self):
        assert self._strat()._had_trading_activity_today() is False

    def test_true_when_entries_completed(self):
        s = self._strat()
        s.daily_state.entries_completed = 1
        assert s._had_trading_activity_today() is True

    def test_true_when_realized_pnl_nonzero(self):
        s = self._strat()
        s.daily_state.total_realized_pnl = -50.0
        assert s._had_trading_activity_today() is True

    def test_true_when_any_entry_present_even_if_not_completed(self):
        # A FAILED entry (never incremented entries_completed) still counts --
        # this is the base's own pre-existing, intentional behavior.
        s = self._strat()
        s.daily_state.entries = [MagicMock()]
        assert s._had_trading_activity_today() is True


class TestHadTradingActivityTodayCalendarOverride:
    """The 2026-08-18 fix: a carried-only entry must NOT count as 'today'."""

    def _strat(self):
        s = D.__new__(D)
        s.daily_state = MagicMock()
        s.daily_state.entries_completed = 0
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.entries = []
        return s

    def test_false_for_carried_only_entry_from_days_ago(self):
        # Reproduces the exact 2026-08-18 incident: a position entered
        # 2026-08-13, carried forward, nothing new today.
        s = self._strat()
        old_entry = _entry(entry_time=datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc))
        s.daily_state.entries = [old_entry]
        assert s._had_trading_activity_today() is False

    def test_true_when_an_entry_was_actually_opened_today(self):
        s = self._strat()
        today_entry = _entry(entry_time=get_us_market_time())
        s.daily_state.entries = [today_entry]
        assert s._had_trading_activity_today() is True

    def test_true_when_mix_of_carried_and_todays_entry(self):
        s = self._strat()
        old_entry = _entry(num=1, entry_time=datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc))
        new_entry = _entry(num=2, entry_time=get_us_market_time())
        s.daily_state.entries = [old_entry, new_entry]
        assert s._had_trading_activity_today() is True

    def test_true_when_realized_pnl_nonzero_regardless_of_entries(self):
        # A real settlement/stop/transform booked today -- must still count
        # even with no "new" entry (e.g. a carried position that settled).
        s = self._strat()
        s.daily_state.total_realized_pnl = 250.0
        s.daily_state.entries = []
        assert s._had_trading_activity_today() is True

    def test_true_when_entries_completed_nonzero(self):
        s = self._strat()
        s.daily_state.entries_completed = 1
        s.daily_state.entries = []
        assert s._had_trading_activity_today() is True

    def test_false_when_entry_time_is_none(self):
        # Defensive: a malformed/legacy entry with no entry_time must not
        # crash the check or be miscounted as "today".
        s = self._strat()
        broken_entry = _entry(entry_time=None)
        s.daily_state.entries = [broken_entry]
        assert s._had_trading_activity_today() is False


class TestMainPyUsesTheHook:
    """Confirms main.py actually calls the new hook instead of re-inlining
    the check (the literal regression this refactor could introduce)."""

    def test_had_trading_activity_source_calls_the_hook(self):
        import inspect
        import bots.hydra.main as main_mod
        source = inspect.getsource(main_mod)
        assert "strategy._had_trading_activity_today()" in source
        # The old inline three-way OR must be gone from this call site (a
        # leftover duplicate would silently un-fix the calendar override).
        assert "strategy.daily_state.entries_completed > 0 or" not in source
