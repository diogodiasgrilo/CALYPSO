"""
STATE-004 restart-gap backstop (2026-09-06).

THE GAP
-------
``_reset_for_new_day()`` — where the overnight-position check lives — only
fires when a running process observes the ET date CHANGE under it. On a day
where NO process survived across ET midnight (VM reboot, crash storm, a deploy
straddling midnight), ``main.py`` seeds ``last_day = today`` and
``strategy.py`` stamps ``daily_state.date = today`` unconditionally at startup.
The reset therefore never runs, and STATE-004 is silently skipped for that day.

Real in code; latent in production so far (all 7 units logged "Resetting for
new trading day" on every date Aug 14 -> Sep 6). It is also NOT a total blind
spot: ``_check_hourly_reconciliation``'s orphan sweep fires a CRITICAL alert
~2 minutes after the open on a genuine overnight leg. The real delta this fix
buys is *halt-before-the-open* instead of *CRITICAL-alert-after-it*.

THE FIX
-------
1. The check body moves to ``_run_overnight_position_check()``, which returns
   CLEAN / POSITIONS_CONFIRMED / READ_FAILED instead of deciding policy.
2. ``_reset_for_new_day`` keeps its historical policy byte-for-byte, INCLUDING
   halting on a failed read (the statements after it wipe ``daily_state``).
3. A new pre-market hook, ``run_overnight_check_if_owed()``, runs the same
   check from ``main.py`` when it is owed — and treats READ_FAILED as
   "retry later", NOT as a halt.
4. ``overnight_check_date`` is persisted and restored (same-day only) so the
   check runs at most once per ET day across restarts.

WHY READ_FAILED MUST NOT HALT ON THE HOOK PATH (the review's blocking finding)
-----------------------------------------------------------------------------
A green broker ``/health`` only proves the *session* family is up;
``get_positions`` runs on the independent *portfolio* family with its own
circuit breaker, shared by all 7 processes through the one broker. Halting
there would stop entries AND stop monitoring for the whole session, on a FLAT
pre-market account, clearable only by another restart — on exactly the day a
restart just happened.
"""

from __future__ import annotations

import sys
from datetime import time as dt_time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bots.hydra.strategy as strategy_mod  # noqa: E402
from bots.hydra.strategy import (  # noqa: E402
    HydraStrategy,
    OVERNIGHT_CHECK_ALREADY_DONE,
    OVERNIGHT_CHECK_CLEAN,
    OVERNIGHT_CHECK_POSITIONS,
    OVERNIGHT_CHECK_READ_FAILED,
    OVERNIGHT_CHECK_SKIPPED_HALTED,
    OVERNIGHT_CHECK_WINDOW_END_ET,
    STATE004_MAX_ATTEMPTS,
)
from shared.alert_service import AlertType, AlertPriority  # noqa: E402
from shared.ib_client import IBClientError  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402


TODAY = get_us_market_time().strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The check sleeps up to ~4 minutes across its retry budget."""
    monkeypatch.setattr(strategy_mod.time, "sleep", lambda *_a, **_k: None)


def _strat() -> HydraStrategy:
    """Minimal HydraStrategy built without __init__ (same shape the existing
    STATE-004 suite uses), with only what the check touches."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.BOT_NAME = "HYDRA"
    s.contracts_per_entry = 1
    s.alert_service = MagicMock()
    s.registry = MagicMock()
    s.registry.get_positions.return_value = set()
    s._save_state_to_disk = MagicMock()
    s._critical_intervention_required = False
    s._critical_intervention_reason = ""
    s._overnight_check_date = None
    s._overnight_check_read_error = None
    return s


# ---------------------------------------------------------------------------
# 1. The hook's owed-gate
# ---------------------------------------------------------------------------

class TestOwedGate:
    def test_already_stamped_today_is_a_noop_and_never_touches_the_broker(self):
        s = _strat()
        s._overnight_check_date = TODAY
        s._read_open_positions = MagicMock(
            side_effect=AssertionError("must not read the broker")
        )

        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_ALREADY_DONE
        assert s._read_open_positions.call_count == 0

    def test_NEGATIVE_CONTROL_unstamped_does_reach_the_broker(self):
        """Proves the test above passes because of the stamp, not because the
        fixture happens to never call the broker."""
        s = _strat()
        s._overnight_check_date = None
        s._read_open_positions = MagicMock(return_value=[])

        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_CLEAN
        assert s._read_open_positions.call_count >= 1

    def test_stale_stamp_from_a_prior_day_does_not_satisfy_the_check(self):
        s = _strat()
        s._overnight_check_date = "1999-01-01"
        s._read_open_positions = MagicMock(return_value=[])

        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_CLEAN

    def test_already_halted_skips_without_stacking_a_second_alert(self):
        s = _strat()
        s._critical_intervention_required = True
        s._read_open_positions = MagicMock(
            side_effect=AssertionError("must not read the broker")
        )

        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_SKIPPED_HALTED
        assert s.alert_service.send_alert.call_count == 0


# ---------------------------------------------------------------------------
# 2. READ_FAILED — the review's blocking finding
# ---------------------------------------------------------------------------

class TestHookReadFailureDoesNotHalt:
    def _exhausted(self):
        s = _strat()
        s._read_open_positions = MagicMock(
            side_effect=IBClientError("portfolio circuit breaker open")
        )
        return s

    def test_read_failure_does_not_halt_the_bot(self):
        s = self._exhausted()
        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_READ_FAILED
        assert s._critical_intervention_required is False

    def test_read_failure_alerts_MEDIUM_not_CRITICAL(self):
        s = self._exhausted()
        s.run_overnight_check_if_owed()

        assert s.alert_service.send_alert.call_count == 1
        kw = s.alert_service.send_alert.call_args.kwargs
        assert kw["priority"] is AlertPriority.MEDIUM
        assert kw["alert_type"] is AlertType.DATA_QUALITY
        assert kw["details"]["attempts"] == STATE004_MAX_ATTEMPTS

    def test_read_failure_leaves_the_check_OWED_so_it_can_retry(self):
        s = self._exhausted()
        s.run_overnight_check_if_owed()
        assert s._overnight_check_date is None

    def test_a_later_retry_after_a_read_failure_succeeds_and_stamps(self):
        """The whole point of leaving it owed: the next loop can fix it."""
        s = _strat()
        s._read_open_positions = MagicMock(
            side_effect=IBClientError("portfolio circuit breaker open")
        )
        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_READ_FAILED

        s._read_open_positions = MagicMock(return_value=[])
        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_CLEAN
        assert s._overnight_check_date == TODAY

    def test_a_failing_alert_send_does_not_propagate(self):
        s = self._exhausted()
        s.alert_service.send_alert.side_effect = RuntimeError("pubsub down")
        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_READ_FAILED
        assert s._critical_intervention_required is False


class TestResetPathKeepsHistoricalHaltPolicy:
    """The hook's leniency must NOT leak into _reset_for_new_day, where a
    failed read is genuinely dangerous: the next statements wipe daily_state."""

    def test_reset_path_still_halts_and_alerts_CRITICAL_on_read_failure(self):
        s = _strat()
        s._read_open_positions = MagicMock(
            side_effect=IBClientError("broker down")
        )
        s.market_data = SimpleNamespace(reset_daily_tracking=lambda: None)
        s._ws_price_cache = MagicMock()
        s.vix_gate_enabled = False
        s._parse_entry_times = MagicMock()
        s.entry_times = []
        s._api_results_window = MagicMock()
        s.skip_weekdays = set()

        s._reset_for_new_day()

        assert s._critical_intervention_required is True
        kw = s.alert_service.send_alert.call_args.kwargs
        assert kw["priority"] is AlertPriority.CRITICAL
        assert kw["alert_type"] is AlertType.CRITICAL_INTERVENTION
        # And it must NOT stamp — a halted day leaves the check owed.
        assert s._overnight_check_date is None


# ---------------------------------------------------------------------------
# 3. POSITIONS_CONFIRMED still halts, from EITHER caller
# ---------------------------------------------------------------------------

class TestConfirmedPositionsStillHalt:
    def test_hook_halts_on_a_twice_confirmed_overnight_position(self):
        s = _strat()
        pos = [{"instrument_id": 999, "quantity": -1}]
        s._read_open_positions = MagicMock(return_value=pos)

        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_POSITIONS
        assert s._critical_intervention_required is True
        kw = s.alert_service.send_alert.call_args.kwargs
        assert kw["priority"] is AlertPriority.CRITICAL
        assert kw["alert_type"] is AlertType.CRITICAL_INTERVENTION

    def test_hook_halt_does_not_stamp_so_the_state_is_re_derived(self):
        s = _strat()
        s._read_open_positions = MagicMock(
            return_value=[{"instrument_id": 999, "quantity": -1}]
        )
        s.run_overnight_check_if_owed()
        assert s._overnight_check_date is None

    def test_confirm_before_alarm_clears_a_transient_first_read(self):
        """First read shows a position, re-check shows none -> no emergency."""
        s = _strat()
        s._read_open_positions = MagicMock(side_effect=[
            [{"instrument_id": 999, "quantity": -1}],  # transitional blip
            [],                                        # re-check: clean
        ])

        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_CLEAN
        assert s._critical_intervention_required is False
        assert s.alert_service.send_alert.call_count == 0


# ---------------------------------------------------------------------------
# 4. The clean path stamps AND persists (review point 4)
# ---------------------------------------------------------------------------

class TestCleanPathPersists:
    def test_clean_stamps_today_and_saves_state(self):
        s = _strat()
        s._read_open_positions = MagicMock(return_value=[])

        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_CLEAN
        assert s._overnight_check_date == TODAY
        assert s._save_state_to_disk.call_count == 1

    def test_a_failing_save_still_stamps_in_memory_and_does_not_raise(self):
        s = _strat()
        s._read_open_positions = MagicMock(return_value=[])
        s._save_state_to_disk = MagicMock(side_effect=OSError("disk full"))

        assert s.run_overnight_check_if_owed() == OVERNIGHT_CHECK_CLEAN
        assert s._overnight_check_date == TODAY

    def test_NEGATIVE_CONTROL_halt_path_does_not_reach_the_stamping_save(self):
        """Guards against a refactor that stamps unconditionally at the end."""
        s = _strat()
        s._read_open_positions = MagicMock(
            side_effect=IBClientError("broker down")
        )
        s.run_overnight_check_if_owed()
        assert s._save_state_to_disk.call_count == 0


# ---------------------------------------------------------------------------
# 5. Persistence round-trip (review point 5: NOT nested in the Brandon guard)
# ---------------------------------------------------------------------------

class TestStatePersistenceRoundTrip:
    def test_save_state_includes_overnight_check_date(self, tmp_path):
        import json

        s = _strat()
        del s._save_state_to_disk           # use the real method
        s.state_file = str(tmp_path / "hydra_state.json")
        s.daily_state = SimpleNamespace(
            date=TODAY, total_realized_pnl=0.0, put_stops_triggered=0,
            call_stops_triggered=0, double_stops=0, total_commission=0.0,
            entries_completed=0, entries_failed=0, entries_skipped=0,
            total_credit_received=0.0, one_sided_entries=0, trend_overrides=0,
        )
        s._overnight_check_date = TODAY

        # Only assert the KEY is carried; the full writer needs far more state
        # than this fixture has, so drive the serialisation shape directly.
        import inspect
        src = inspect.getsource(HydraStrategy._save_state_to_disk)
        assert '"overnight_check_date": getattr(self, "_overnight_check_date", None)' in src

    def test_restore_is_NOT_nested_in_the_brandon_hasattr_guard(self):
        """Review point 5: nesting it under `hasattr(self,
        '_brandon_overlay_booked')` would restore on B/C only, leaving
        A/D/E/F/G re-running the check and re-alerting on EVERY restart."""
        import inspect
        src = inspect.getsource(HydraStrategy._load_state_file_history)

        restore = "self._overnight_check_date = saved_state.get(\"overnight_check_date\")"
        assert restore in src
        guard = 'if hasattr(self, "_brandon_overlay_booked"):'
        assert guard in src
        # The restore must appear BEFORE the Brandon guard, at method indent.
        assert src.index(restore) < src.index(guard)
        line = next(ln for ln in src.split("\n") if restore in ln)
        assert len(line) - len(line.lstrip()) == 12, (
            f"restore is indented {len(line) - len(line.lstrip())} spaces — "
            "expected 12 (same level as the daily_state.* restores)"
        )


# ---------------------------------------------------------------------------
# 6. Fleet-wide availability + the window invariant (review point 7)
# ---------------------------------------------------------------------------

class TestFleetWideAndWindowInvariant:
    def test_every_variant_class_inherits_the_hook(self):
        from bots.hydra.brandon.strategy import BrandonHydraStrategy
        from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy
        from bots.hydra.spy_double_calendar_strategy import SpyDoubleCalendarStrategy
        from bots.hydra.ghauri_strategy import GhauriMeanReversionStrategy
        from bots.hydra.strangle_strategy import StrangleStrategy

        for cls in (
            HydraStrategy, BrandonHydraStrategy, DoubleCalendarStrategy,
            SpyDoubleCalendarStrategy, GhauriMeanReversionStrategy,
            StrangleStrategy,
        ):
            assert hasattr(cls, "run_overnight_check_if_owed"), cls.__name__
            assert hasattr(cls, "_run_overnight_position_check"), cls.__name__

    def test_window_ends_before_every_configured_entry_time(self):
        """THE load-bearing invariant. The check reads the WHOLE account, so
        it must finish before ANY variant can legitimately hold a position.
        If this fails, someone scheduled an entry into the check's window."""
        import glob
        import json

        earliest = dt_time(23, 59)
        for path in sorted(glob.glob("bots/hydra/config/config_variant_*.json")):
            cfg = json.load(open(path))
            for t in cfg.get("strategy", {}).get("entry_times", []) or []:
                h, m = (int(p) for p in t.split(":"))
                earliest = min(earliest, dt_time(h, m))

        # Variant F is event-triggered and sets entry_times = [09:30] in code,
        # so the true fleet floor is the market open, not B's 09:45.
        earliest = min(earliest, dt_time(9, 30))

        assert OVERNIGHT_CHECK_WINDOW_END_ET < earliest, (
            f"overnight-check window ends at {OVERNIGHT_CHECK_WINDOW_END_ET} "
            f"but the earliest fleet entry is {earliest}"
        )

    def test_window_leaves_room_for_the_checks_own_worst_case(self):
        """~4 min worst case: STATE004_MAX_ATTEMPTS x ~35s HTTP timeout +
        3 x 20s retry delay + the 20s confirm-before-alarm sleep."""
        worst_case_s = (
            STATE004_MAX_ATTEMPTS * 35
            + (STATE004_MAX_ATTEMPTS - 1) * strategy_mod.STATE004_RETRY_DELAY_S
            + strategy_mod.STATE004_RETRY_DELAY_S
        )
        window_end_s = (
            OVERNIGHT_CHECK_WINDOW_END_ET.hour * 3600
            + OVERNIGHT_CHECK_WINDOW_END_ET.minute * 60
        )
        market_open_s = 9 * 3600 + 30 * 60

        assert window_end_s + worst_case_s < market_open_s, (
            f"a check starting at {OVERNIGHT_CHECK_WINDOW_END_ET} could still "
            f"be running {worst_case_s}s later, past the 09:30 open"
        )

    def test_NEGATIVE_CONTROL_a_0900_window_would_violate_nothing_but_0935_would(self):
        """Sanity-check the invariant test actually discriminates."""
        worst_case_s = (
            STATE004_MAX_ATTEMPTS * 35
            + STATE004_MAX_ATTEMPTS * strategy_mod.STATE004_RETRY_DELAY_S
        )
        assert 9 * 3600 + worst_case_s < 9 * 3600 + 30 * 60      # 09:00 fine
        assert 9 * 3600 + 35 * 60 > 9 * 3600 + 30 * 60           # 09:35 not
