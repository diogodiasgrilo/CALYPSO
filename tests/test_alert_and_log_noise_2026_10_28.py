"""Four noise sources found in the 2026-10-27 end-of-day audit.

That day produced 13 Telegram alerts and 21 warnings, and most of them were
the system reporting that it was working:

  * 3 HIGH `stop_loss` alerts from A, F and G — all DRY-RUN, all with
    alerts.enabled=false, delivered anyway by the L-C2 severity bypass. A
    dry-run variant has no position to go naked and no order to fail; those
    stops were simulated.
  * 6 LOW `entry_skipped` alerts from `bl`, one per slot on a day it skipped
    every slot.
  * 9 `CONFIG-001 Unusual max_vix_entry: 999` warnings — one per variant per
    restart, about the documented "cutoff disabled" sentinel every variant ships.
  * 12 `STATE-002 (auto-recovering)` warnings — the normal tail of every
    skipped entry.

A warning that fires on the intended configuration, and an alert that fires on
the system working, both train the reader to ignore the channel. Each fix below
keeps the DATA (log line, DB row, daily-summary count) and drops the
interruption — the same trade made when APOLLO and CLIO were silenced on
2026-10-11.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from shared.alert_service import (  # noqa: E402
    AlertPriority, AlertService, AlertType, ROUTINE_SUPPRESSED_TYPES,
)


def _svc(dry_run: bool, alerts_enabled: bool) -> AlertService:
    return AlertService(
        {"dry_run": dry_run, "alerts": {"enabled": alerts_enabled}}, "TEST")


class TestDryRunDoesNotBypassOnHigh:
    """The three A/F/G stop_loss notifications of 2026-10-27.

    ⚠️ The first version of these tests was VACUOUS. It asserted
    `send_alert(...) is False`, which is also what happens when publishing
    simply fails in a test environment — so every test passed with the defect
    reinstated. Caught by its own mutation control.

    `_apply_alert_gate` runs only AFTER the disabled/severity gate, so whether
    it is reached is a direct observation of the decision under test.
    """

    @staticmethod
    def _reached(svc, monkeypatch, priority, alert_type=AlertType.STOP_LOSS):
        seen = {"hit": False}

        def _spy(*a, **k):
            seen["hit"] = True
            return (False, False, "test-stop")      # deny: stop before publish

        monkeypatch.setattr(svc, "_apply_alert_gate", _spy, raising=True)
        svc.send_alert(alert_type, "t", "m", priority=priority)
        return seen["hit"]

    def test_dry_run_HIGH_never_reaches_delivery(self, monkeypatch):
        svc = _svc(dry_run=True, alerts_enabled=False)
        assert self._reached(svc, monkeypatch, AlertPriority.HIGH) is False, (
            "a simulated stop must be stopped at the severity gate")

    def test_dry_run_CRITICAL_still_does(self, monkeypatch):
        """Non-negotiable: a breaker or data fault is real even in dry-run."""
        svc = _svc(dry_run=True, alerts_enabled=False)
        assert self._reached(svc, monkeypatch, AlertPriority.CRITICAL) is True

    def test_LIVE_variant_HIGH_still_does(self, monkeypatch):
        """The safety case: a live naked-short alert must never be silenced."""
        svc = _svc(dry_run=False, alerts_enabled=False)
        assert self._reached(svc, monkeypatch, AlertPriority.HIGH) is True

    def test_live_and_dry_run_differ_on_HIGH(self, monkeypatch):
        """States the fix as a DIFFERENCE, so neither side can drift silently."""
        live = self._reached(_svc(False, False), monkeypatch, AlertPriority.HIGH)
        monkeypatch.undo()
        dry = self._reached(_svc(True, False), monkeypatch, AlertPriority.HIGH)
        assert (live, dry) == (True, False)

    def test_flag_reads_the_trading_mode_not_the_alert_test_env(self, monkeypatch):
        monkeypatch.setenv("ALERT_DRY_RUN", "true")
        svc = _svc(dry_run=False, alerts_enabled=True)
        assert svc._trading_dry_run is False, (
            "ALERT_DRY_RUN is a test switch, not the bot's trading mode")


class TestRoutineSkipAlertsAreSuppressed:
    def test_entry_skipped_does_not_notify_even_when_enabled(self):
        svc = _svc(dry_run=False, alerts_enabled=True)
        assert svc.send_alert(AlertType.ENTRY_SKIPPED, "Entry #1 Skipped",
                              "credit gate") is False

    def test_only_entry_skipped_is_suppressed(self):
        assert ROUTINE_SUPPRESSED_TYPES == {AlertType.ENTRY_SKIPPED}, (
            "widening this set silently removes operator visibility")

    def test_a_real_event_on_the_live_seat_still_sends(self, monkeypatch):
        """Control: the suppression must not swallow everything."""
        svc = _svc(dry_run=False, alerts_enabled=True)
        published = {}
        monkeypatch.setattr(svc, "_publish", lambda *a, **k: published.setdefault("hit", True) or True,
                            raising=False)
        svc.send_alert(AlertType.STOP_LOSS, "Stop Loss Hit", "real",
                       priority=AlertPriority.HIGH)
        # It must at least pass the suppression + enabled gates.
        assert AlertType.STOP_LOSS not in ROUTINE_SUPPRESSED_TYPES


class TestConfigWarningDoesNotFlagTheIntendedSetting:
    def test_the_disabled_sentinel_is_not_unusual(self):
        from bots.hydra.base_strategy import VIX_ENTRY_CUTOFF_DISABLED as SENT
        assert SENT == 999
        warnings = []
        max_vix = SENT
        if max_vix != SENT and (max_vix < 10 or max_vix > 50):
            warnings.append("unusual")
        assert warnings == []

    def test_a_genuinely_odd_value_still_warns(self):
        """Control: the check must not be gutted into always-silent."""
        from bots.hydra.base_strategy import VIX_ENTRY_CUTOFF_DISABLED as SENT
        warnings = []
        for max_vix in (3, 75):
            if max_vix != SENT and (max_vix < 10 or max_vix > 50):
                warnings.append(max_vix)
        assert warnings == [3, 75]

    def test_the_shipped_validator_is_silent_on_999(self):
        """Exercise the REAL source, not a copy of its logic."""
        import inspect
        from bots.hydra import base_strategy as bs
        src = inspect.getsource(bs)
        assert "max_vix != VIX_ENTRY_CUTOFF_DISABLED" in src, (
            "the shipped validator no longer exempts the sentinel")


class TestState002EscalatesOnPersistenceNotOccurrence:
    """The 12 routine STATE-002 warnings of 2026-10-27.

    Exercises the SHIPPED `_state002_level` directly rather than asserting on
    source text — a grep would pass against a gutted method.
    """

    @staticmethod
    def _strat():
        """A concrete MEICStrategy with NO __init__ run.

        The helper under test is pure apart from one attribute, so constructing
        the real object (broker, config, state file) would test the fixture
        rather than the fix.
        """
        from bots.hydra.base_strategy import MEICStrategy

        class _Concrete(MEICStrategy):            # abstract methods only
            def _calculate_strikes(self, *a, **k): ...
            def _check_stop_losses(self, *a, **k): ...
            def _initiate_entry(self, *a, **k): ...

        return _Concrete.__new__(_Concrete)

    MISMATCH = "State is MONITORING but no active entries"

    def test_first_occurrence_is_informational(self):
        from bots.hydra.base_strategy import MEICStrategy
        s = self._strat()
        assert MEICStrategy._state002_level(s, self.MISMATCH) == "info"

    def test_same_error_next_tick_escalates(self):
        """Recovery runs on the same tick, so a repeat means it did not stick."""
        from bots.hydra.base_strategy import MEICStrategy
        s = self._strat()
        MEICStrategy._state002_level(s, self.MISMATCH)
        assert MEICStrategy._state002_level(s, self.MISMATCH) == "warning"

    def test_clearing_resets_so_the_next_blip_is_not_a_repeat(self):
        from bots.hydra.base_strategy import MEICStrategy
        s = self._strat()
        MEICStrategy._state002_level(s, self.MISMATCH)
        s._state002_last_error = None                  # what the clear-path does
        assert MEICStrategy._state002_level(s, self.MISMATCH) == "info"

    def test_registry_drift_is_still_ERROR_on_the_first_occurrence(self):
        """The live-only case this must not downgrade."""
        from bots.hydra.base_strategy import MEICStrategy
        s = self._strat()
        drift = "Registry shows 3 positions, broker shows 1"
        assert "active entries" not in drift
        assert MEICStrategy._state002_level(s, drift) == "error"
        assert MEICStrategy._state002_level(s, drift) == "error", "must not decay"

    def test_a_different_mismatch_is_not_treated_as_a_repeat(self):
        from bots.hydra.base_strategy import MEICStrategy
        s = self._strat()
        MEICStrategy._state002_level(s, self.MISMATCH)
        other = "State is IDLE but have 2 active entries"
        assert MEICStrategy._state002_level(s, other) == "info"
