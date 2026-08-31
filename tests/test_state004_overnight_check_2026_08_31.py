"""STATE-004 permanent-halt-on-transient-broker-failure fix (2026-08-31).

On 2026-08-31 a routine ~20-27s calypso-broker reconnect blip at market open
caused `_reset_for_new_day()`'s overnight-position check to catch a
transient `IBClientError` from `_read_open_positions(strict=True)` and
PERMANENTLY latch `self._critical_intervention_required = True` with zero
retry. That flag is checked FIRST in the main trading loop
(`_run_strategy_check_internal`), so it froze market-data updates, entry
logic, and everything else for 5 of 7 live variants for ~6 hours until a
manual restart. The broker itself recovered within seconds; only the
strategy layer's zero-retry overreaction was the actual bug.

Fix (in bots/hydra/strategy.py):
  1. `_read_open_positions_for_new_day_reset` — a bounded retry (up to
     STATE004_MAX_ATTEMPTS, STATE004_RETRY_DELAY_S apart) around the same
     `_read_open_positions(strict=True)` call, before the except block's
     existing (unchanged) CRITICAL-log + alert + permanent-latch behavior.
  2. Confirm-before-alarm on the "genuine overnight positions found" branch
     — a single re-check before latching, so a transitional/misleading
     non-empty read during a reconnect blip doesn't false-alarm.
  3. The halt flag is now persisted to the state file
     (`critical_intervention` / `critical_intervention_reason`) so ARGUS
     and the dashboard can see it — previously invisible outside the
     in-process flag, which is why the incident went undetected for 6
     hours despite ARGUS running every 15 minutes the whole time.

This file also carries the two-layer alert-delivery reproduction test
(TestAlertDeliveryReproduction) built to investigate a separate, still-
unexplained anomaly from the same incident: today's CRITICAL alert produced
zero trace anywhere in the journal despite the code provably executing all
the way through. Layer 1 mirrors
tests/test_alert_send_failure_logging_2026_08_04.py's pattern (mocked
alert_service, assert it's called correctly). Layer 2 mirrors
tests/test_alert_publish_reliability.py's `_live_svc()` pattern (a REAL,
non-mocked AlertService with only the outbound transport mocked) — the
layer that actually stresses the real mystery. If both pass, the Python
code is proven correct in isolation, meaning the incident's silence was
environment/infra-specific to that host that morning, not an application
bug — see bots/hydra/__init__.py version history for the follow-up plan
(a one-time staged fire drill on a dry-run-locked variant).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bots.hydra.strategy as strategy_mod  # noqa: E402
from bots.hydra.strategy import (  # noqa: E402
    HydraStrategy,
    STATE004_MAX_ATTEMPTS,
    STATE004_RETRY_DELAY_S,
)
from shared.alert_service import AlertType, AlertPriority  # noqa: E402
from shared.ib_client import IBClientError  # noqa: E402


def _minimal_strat(tmp_path: Path = None) -> HydraStrategy:
    """Bare HydraStrategy with just enough state to run the STATE-004 block
    of _reset_for_new_day() (the failure paths return before the full reset
    logic, so most of the method's other dependencies are never reached)."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.BOT_NAME = "HYDRA_TEST"
    s.contracts_per_entry = 1
    s.alert_service = MagicMock()
    s._critical_intervention_required = False
    s._critical_intervention_reason = ""
    s._save_state_to_disk = MagicMock()  # real disk I/O not under test here
    # hasattr-guarded in the real method — fine to omit, but set explicitly
    # so the STATE-004 block's surrounding lines (which DO run before the
    # broker call) behave identically to production.
    s._emergency_close_alerted = set()
    s._recent_close_conids = {}
    s.registry = MagicMock()
    s.registry.get_positions.return_value = set()
    return s


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """STATE004_RETRY_DELAY_S is 20s real — don't slow the suite down.
    Records sleep calls so tests can assert the retry cadence happened."""
    calls = []
    monkeypatch.setattr(strategy_mod.time, "sleep", lambda s: calls.append(s))
    return calls


# ─── Layer 0: the retry helper in isolation ───────────────────────────────

class TestReadOpenPositionsForNewDayResetRetry:
    def test_succeeds_first_attempt_no_retry(self, _no_real_sleep):
        s = _minimal_strat()
        s._read_open_positions = MagicMock(return_value=[{"instrument_id": 1}])

        result = s._read_open_positions_for_new_day_reset()

        assert result == [{"instrument_id": 1}]
        assert s._read_open_positions.call_count == 1
        assert _no_real_sleep == []

    def test_succeeds_on_third_attempt_after_two_transient_failures(self, _no_real_sleep):
        s = _minimal_strat()
        s._read_open_positions = MagicMock(side_effect=[
            IBClientError("IBClient not connected — call connect() first"),
            IBClientError("IBClient not connected — call connect() first"),
            [],
        ])

        result = s._read_open_positions_for_new_day_reset()

        assert result == []
        assert s._read_open_positions.call_count == 3
        assert _no_real_sleep == [STATE004_RETRY_DELAY_S] * 2

    def test_exhausts_all_attempts_and_raises_the_final_exception(self, _no_real_sleep):
        s = _minimal_strat()
        final_exc = IBClientError("IBClient not connected — call connect() first")
        s._read_open_positions = MagicMock(side_effect=[
            IBClientError("blip 1"),
            IBClientError("blip 2"),
            IBClientError("blip 3"),
            final_exc,
        ])

        with pytest.raises(IBClientError) as exc_info:
            s._read_open_positions_for_new_day_reset()

        assert exc_info.value is final_exc
        assert s._read_open_positions.call_count == STATE004_MAX_ATTEMPTS
        assert _no_real_sleep == [STATE004_RETRY_DELAY_S] * (STATE004_MAX_ATTEMPTS - 1)


# ─── Layer 1: through _reset_for_new_day()'s STATE-004 block ─────────────

class TestState004RetryIntegration:
    """Drives the REAL (unmocked) _reset_for_new_day, only mocking the
    broker-facing _read_open_positions. This is the negative-control-bearing
    layer: reverting _read_open_positions_for_new_day_reset back to a bare
    `self._read_open_positions(strict=True)` call makes
    test_transient_failure_that_recovers_does_not_halt fail (the single
    first failure would latch immediately, exactly like the real incident)."""

    def test_transient_failure_that_recovers_does_not_halt(self, _no_real_sleep):
        s = _minimal_strat()
        s._read_open_positions = MagicMock(side_effect=[
            IBClientError("IBClient not connected — call connect() first"),
            [],  # recovers on the 2nd attempt, well within budget
        ])
        # Reached only on the SUCCESS path (falls through STATE-004) —
        # would raise if the fix incorrectly still latched and returned early.
        s.registry.get_positions.return_value = set()
        s.market_data = SimpleNamespace(reset_daily_tracking=lambda: None)
        s._ws_price_cache = MagicMock()
        s.vix_gate_enabled = False
        s._parse_entry_times = MagicMock()
        s.entry_times = []
        s._api_results_window = MagicMock()
        s.skip_weekdays = set()

        s._reset_for_new_day()

        assert s._critical_intervention_required is False
        assert s.alert_service.send_alert.call_count == 0
        # _reset_for_new_day() always ends with ONE _save_state_to_disk() call
        # on the normal success path (unrelated to Fix 2's halt-persistence
        # calls) — one call here, not zero, confirms we reached the clean
        # end of the function rather than an early-return latch path.
        assert s._save_state_to_disk.call_count == 1

    def test_failure_exhausting_the_full_retry_budget_still_halts_and_alerts(self, _no_real_sleep):
        """The permanent latch on final exhaustion is CORRECT and must stay —
        this is not a regression to guard against, it's confirming the
        terminal safety behavior survives the fix."""
        s = _minimal_strat()
        s._read_open_positions = MagicMock(
            side_effect=IBClientError("IBClient not connected — call connect() first")
        )

        s._reset_for_new_day()

        assert s._critical_intervention_required is True
        assert "Overnight position verification failed" in s._critical_intervention_reason
        s.alert_service.send_alert.assert_called_once()
        _, kwargs = s.alert_service.send_alert.call_args
        assert kwargs["alert_type"] == AlertType.CRITICAL_INTERVENTION
        assert kwargs["priority"] == AlertPriority.CRITICAL
        # Fix 2: the halt is persisted immediately, not just on the next
        # ~10s periodic save.
        s._save_state_to_disk.assert_called_once()
        assert s._read_open_positions.call_count == STATE004_MAX_ATTEMPTS


# ─── Layer 2: confirm-before-alarm on the non-empty-positions branch ─────

class TestConfirmBeforeAlarm:
    def test_genuine_overnight_position_confirmed_on_recheck_halts(self, _no_real_sleep):
        s = _minimal_strat()
        pos = [{"instrument_id": 999, "quantity": -1}]
        # First read (via the retry helper) returns non-empty; the
        # dedicated re-check call also returns non-empty -> confirmed.
        s._read_open_positions = MagicMock(side_effect=[pos, pos])

        s._reset_for_new_day()

        assert s._critical_intervention_required is True
        assert "Overnight 0DTE positions detected" in s._critical_intervention_reason
        s.alert_service.send_alert.assert_called_once()
        _, kwargs = s.alert_service.send_alert.call_args
        assert kwargs["title"] == "HYDRA_TEST Overnight Position Detected!"
        assert _no_real_sleep == [STATE004_RETRY_DELAY_S]  # one re-check wait

    def test_transitional_misleading_read_recovers_on_recheck_does_not_halt(self, _no_real_sleep):
        """The exact false-alarm case the design review flagged: a
        non-empty first read during a reconnect blip that wasn't a real
        overnight position — the re-check comes back empty."""
        s = _minimal_strat()
        pos = [{"instrument_id": 999, "quantity": -1}]
        s._read_open_positions = MagicMock(side_effect=[pos, []])
        s.registry.get_positions.return_value = set()
        s.market_data = SimpleNamespace(reset_daily_tracking=lambda: None)
        s._ws_price_cache = MagicMock()
        s.vix_gate_enabled = False
        s._parse_entry_times = MagicMock()
        s.entry_times = []
        s._api_results_window = MagicMock()
        s.skip_weekdays = set()

        s._reset_for_new_day()

        assert s._critical_intervention_required is False
        assert s.alert_service.send_alert.call_count == 0

    def test_recheck_read_failure_falls_through_to_confirmed_not_silently_dropped(self, _no_real_sleep, caplog):
        """If the re-check ITSELF fails, don't silently drop a possibly-real
        emergency — fail closed by treating the original non-empty read as
        confirmed (matches strict=True's own house philosophy elsewhere in
        this file: a fetch failure must not be mistaken for 'all clear').

        Audit finding (2026-08-31): this fall-through used to be a bare
        `except Exception: pass` with zero log trace — an operator seeing
        the eventual CRITICAL alert had no way to tell "confirmed twice"
        from "confirmed once, then the re-check itself broke." Assert the
        WARNING line naming that ambiguity actually fires."""
        s = _minimal_strat()
        pos = [{"instrument_id": 999, "quantity": -1}]
        s._read_open_positions = MagicMock(side_effect=[
            pos,
            IBClientError("blip during re-check"),
        ])

        with caplog.at_level(logging.WARNING, logger="bots.hydra.strategy"):
            s._reset_for_new_day()

        assert s._critical_intervention_required is True
        assert "Overnight 0DTE positions detected" in s._critical_intervention_reason
        recheck_warnings = [
            r for r in caplog.records
            if "re-check itself failed" in r.message and "not re-confirmed" in r.message
        ]
        assert recheck_warnings, caplog.text


# ─── Fix 2: state-file persistence of the halt flag ───────────────────────

class TestCriticalInterventionPersistedToStateFile:
    def _make_full_strat(self, tmp_path: Path) -> HydraStrategy:
        """Mirrors tests/test_hydra_state_heartbeat.py's _make_strategy —
        the established pattern for driving the REAL _save_state_to_disk."""
        s = HydraStrategy.__new__(HydraStrategy)
        ds = MagicMock()
        ds.date = "2026-08-31"
        ds.entries_completed = 0
        ds.entries_failed = 0
        ds.entries_skipped = 0
        ds.total_credit_received = 0.0
        ds.total_realized_pnl = 0.0
        ds.total_commission = 0.0
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
        s.state = MagicMock()
        s.state.value = "MONITORING"
        s._next_entry_index = 0
        s.contracts_per_entry = 1
        s.entry_times = []
        s._base_entry_count = 0
        s._conditional_entry_times = []
        s.strategy_config = {}
        s._pnl_history = []
        s.short_only_stop = False
        s._early_close_time = None
        s.market_data = SimpleNamespace(
            spx_open=None, spx_high=None, spx_low=None,
            vix_open=None, vix_high=None, vix_low=None,
        )
        s.state_file = str(tmp_path / "hydra_state.json")
        s._get_effective_stop_level = lambda *a, **kw: None
        return s

    def test_halted_flag_and_reason_present_in_state_file(self, tmp_path):
        s = self._make_full_strat(tmp_path)
        s._critical_intervention_required = True
        s._critical_intervention_reason = "Overnight position verification failed: boom"

        s._save_state_to_disk()

        data = json.loads(Path(s.state_file).read_text())
        assert data["critical_intervention"] is True
        assert data["critical_intervention_reason"] == (
            "Overnight position verification failed: boom"
        )

    def test_negative_control_healthy_bot_persists_false(self, tmp_path):
        """Not just 'the field is truthy when halted' — a healthy bot must
        persist an explicit False, or ARGUS reading a missing/None field
        could misclassify silence as either state."""
        s = self._make_full_strat(tmp_path)
        s._critical_intervention_required = False
        s._critical_intervention_reason = ""

        s._save_state_to_disk()

        data = json.loads(Path(s.state_file).read_text())
        assert data["critical_intervention"] is False


# ─── Alert-delivery reproduction (the unexplained 2026-08-31 mystery) ─────

class TestAlertDeliveryReproduction:
    """Layer 1: strategy.py's side is correct — logger.critical fires and
    send_alert is called with the right kwargs. Mirrors
    test_alert_send_failure_logging_2026_08_04.py's pattern."""

    def test_state004_exhaustion_logs_critical_and_calls_send_alert(self, caplog, _no_real_sleep):
        s = _minimal_strat()
        s._read_open_positions = MagicMock(
            side_effect=IBClientError("IBClient not connected — call connect() first")
        )

        with caplog.at_level(logging.INFO, logger="bots.hydra.strategy"):
            s._reset_for_new_day()

        criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert any("broker overnight-position check failed" in r.message for r in criticals), caplog.text
        s.alert_service.send_alert.assert_called_once()
        _, kwargs = s.alert_service.send_alert.call_args
        assert kwargs["alert_type"] == AlertType.CRITICAL_INTERVENTION
        assert kwargs["priority"] == AlertPriority.CRITICAL
        assert "Overnight Position Check Failed" in kwargs["title"]


@pytest.fixture(autouse=True)
def _isolate_dead_letter_and_sleep_for_live_alert_tests(tmp_path, monkeypatch):
    """Layer 2 constructs a REAL AlertService — isolate its dead-letter file
    and skip its own internal retry sleep, mirroring
    test_alert_publish_reliability.py's fixtures exactly."""
    import shared.alert_service as alert_service_module
    monkeypatch.setattr(alert_service_module, "FAILED_ALERTS_PATH", str(tmp_path / "failed_alerts.jsonl"))
    monkeypatch.setattr(alert_service_module.time, "sleep", lambda _s: None)
    yield


class TestAlertDeliveryReproductionLive:
    """Layer 2: the layer that actually stresses the real mystery. A REAL
    (non-mocked) AlertService — only the outbound transport is mocked —
    fed the exact CRITICAL_INTERVENTION alert _reset_for_new_day() sends.
    If this passes, the missing 2026-08-31 log line was not caused by
    anything in this Python code, and the follow-up is an infra-level
    fire drill on the real VM, not a further code fix here."""

    def _live_svc(self, monkeypatch, on_gcp: bool = True):
        from shared.alert_service import AlertService
        import shared.alert_service as alert_service_module
        monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
        monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: on_gcp)
        svc = AlertService({"alerts": {"enabled": False}}, "HYDRA_TEST")
        svc._initialized = True
        svc._publisher = MagicMock()
        svc._topic_path = "projects/test/topics/calypso-alerts"
        return svc

    def test_critical_intervention_alert_produces_the_warning_log_line(self, monkeypatch, caplog):
        """AlertPriority.CRITICAL's "ALERT [...]" line is emitted via
        logger.warning, not logger.critical (shared/alert_service.py) —
        assert on the correct level, not the one the incident's own
        follow-up investigation initially assumed."""
        svc = self._live_svc(monkeypatch)
        svc._publisher.publish.return_value = MagicMock(result=MagicMock(return_value="mid-1"))

        with caplog.at_level(logging.WARNING, logger="shared.alert_service"):
            result = svc.send_alert(
                alert_type=AlertType.CRITICAL_INTERVENTION,
                title="HYDRA_TEST Overnight Position Check Failed!",
                message="CRITICAL: broker overnight-position check failed at new-day reset",
                priority=AlertPriority.CRITICAL,
                details={"error": "IBClient not connected"},
                contracts=1,
            )

        assert result is True
        alert_lines = [r for r in caplog.records if "ALERT [" in r.message and "CRITICAL" in r.message]
        assert alert_lines, (
            "the '\U0001f6a8 ALERT [...]' line was not emitted for a CRITICAL "
            f"alert with alerts.enabled=false — this reproduces the exact "
            f"2026-08-31 anomaly in code, not just in production logs. "
            f"caplog: {caplog.text}"
        )

    def test_negative_control_disabled_alerts_low_priority_is_silent(self, monkeypatch, caplog):
        """Confirms the test harness actually distinguishes 'fires' from
        'silent' — a LOW-priority alert with alerts.enabled=false must NOT
        produce the ALERT line (the severity-bypass gate is priority-
        specific), so the CRITICAL case above isn't trivially always-true."""
        svc = self._live_svc(monkeypatch)

        with caplog.at_level(logging.WARNING, logger="shared.alert_service"):
            result = svc.send_alert(
                alert_type=AlertType.BOT_STARTED,
                title="Bot Started",
                message="routine",
                priority=AlertPriority.LOW,
            )

        assert result is False
        alert_lines = [r for r in caplog.records if "ALERT [" in r.message]
        assert alert_lines == []
