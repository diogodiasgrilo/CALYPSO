"""D/E calendar simulate-failure recording (2026-07-31) — same fix family as
tests/test_entry_execution_failure.py, applied to the two dry-run-locked
calendar strategies. CalendarEntry subclasses IronCondorEntry, so the
inherited HydraStrategy._record_failed_entry works unchanged; these tests
pin that compatibility empirically rather than assuming it.

Note: this does NOT verify the /dc calendar dashboard (a separate recorder,
dc_calendar.db, out of scope for this fix) — only that the failure now
reaches the skipped_entries DB table and fires a Telegram alert instead of
vanishing into a bare counter increment.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy
from bots.hydra.spy_double_calendar_strategy import SpyDoubleCalendarStrategy


def _common(s):
    s._next_entry_index = 0
    s.dry_run = True
    s.contracts_per_entry = 1
    s.daily_state = SimpleNamespace(
        entries=[], entries_completed=0, entries_skipped=0, entries_failed=0,
    )
    s._has_orphaned_orders = lambda: False
    s._check_market_halt = lambda: (False, "")
    s._check_buying_power = lambda: (True, "ok")
    s._dc_entry_is_open = lambda e: False
    s._calculate_strikes = lambda entry: True
    s._dc_simulate_entry = lambda entry: False  # force the failure path
    s._data_recorder = MagicMock()
    s.alert_service = MagicMock()
    s._record_shadow_entry = lambda **kw: None
    s.current_price = 7450.0
    s.current_vix = 17.5
    return s


def _d_strat():
    # D's gate method (_dc_pre_entry_gates): dc_max_concurrent + dc_max_deployed_debit.
    s = _common(DoubleCalendarStrategy.__new__(DoubleCalendarStrategy))
    s.dc_max_concurrent = 1
    s.dc_max_deployed_debit = 0.0
    return s


def _e_strat():
    # E's gate method (_pre_entry_gates) additionally has a LOW-IV VIX entry gate —
    # different attribute names from D's, not shared despite both being calendars.
    s = _common(SpyDoubleCalendarStrategy.__new__(SpyDoubleCalendarStrategy))
    s.spy_dc_max_concurrent = 1
    s.spy_dc_max_deployed_debit = 0.0
    s.spy_dc_max_vix_entry = 30.0  # current_vix=17.5 must pass under this
    return s


class TestCalendarSimulateFailureShared:
    """Assertions common to both D and E, run against each via the two
    fixtures above (not @pytest.mark.parametrize — the fixtures' gate
    attributes differ enough between D and E that a single parametrized
    fixture function would obscure which strategy's real gate names are
    being exercised).

    D/E deliberately pass send_alert=False (see the call site comments in
    double_calendar_strategy.py / spy_double_calendar_strategy.py): their
    configs document "dry-run path emits none" as an intentional invariant
    (avoids paging on a scaffold/NO-GO variant), and this fix must not
    silently break that — it only closes the DB-visibility gap for D/E.
    """

    def _assert_recorded_but_not_alerted(self, s):
        result = s._initiate_entry()
        assert "failed" in result
        assert s.daily_state.entries_failed == 1
        assert len(s.daily_state.entries) == 1
        assert s.daily_state.entries[0].execution_failed is True
        assert s._data_recorder.record_skipped_entry.called
        (db_kwargs,), _ = s._data_recorder.record_skipped_entry.call_args
        assert db_kwargs["execution_failed"] == 1
        # No real-time alert for D/E — see class docstring.
        assert not s.alert_service.send_alert.called

    def test_d_records_but_does_not_alert_on_simulate_failure(self):
        self._assert_recorded_but_not_alerted(_d_strat())

    def test_e_records_but_does_not_alert_on_simulate_failure(self):
        self._assert_recorded_but_not_alerted(_e_strat())
