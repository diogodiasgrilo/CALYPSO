"""2026-08-28: MKT-046's anti-spike breach-confirmation window made
configurable per-variant (was a hardcoded 10-second constant in
_check_stop_with_confirmation). Prompted by a full-history study of B (16
delayed stops over 3 months) and C (6 delayed stops) that found ZERO cases
where the wait ever avoided a stop that didn't happen anyway, and the wait
made the eventual exit price WORSE in 19 of 22 delayed stops (never better)
-- see bots/hydra/__init__.py version history for the full study. B's live
config now sets strategy.mkt046_confirm_seconds=0; every other variant
defaults to 10.0, unchanged from today's behavior.

Pins two things: (1) the config read defaults correctly and honors an
override, (2) the REAL (unmocked) _check_stop_with_confirmation actually
behaves differently at 0.0 vs 10.0 -- confirms on the very next tick at 0.0
(matching the method's own docstring, which describes "two consecutive
heartbeat cycles" as the original intent) instead of waiting out a full 10s.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import bots.hydra.base_strategy as base_mod
from bots.hydra.strategy import HydraStrategy


def _entry(**kw):
    e = base_mod.IronCondorEntry(entry_number=1)
    e.contracts = 7
    for k, v in kw.items():
        setattr(e, k, v)
    return e


class TestMkt046ConfirmSecondsConfigRead:
    def test_default_is_10_seconds_when_not_overridden(self):
        strategy_cfg = {}
        confirm = float(strategy_cfg.get("mkt046_confirm_seconds", 10.0))
        assert confirm == 10.0

    def test_explicit_zero_override_reads_as_zero_not_falsy_default(self):
        """0 must not be treated as 'unset' -- .get(key, 10.0) with an
        explicit 0 in the dict correctly returns 0, not the default."""
        strategy_cfg = {"mkt046_confirm_seconds": 0}
        confirm = float(strategy_cfg.get("mkt046_confirm_seconds", 10.0))
        assert confirm == 0.0


class TestMkt046ConfirmSecondsBehavior:
    """Drives the REAL _check_stop_with_confirmation (not mocked) to prove
    the configured window actually changes when a stop fires."""

    def _strat(self, confirm_seconds):
        s = HydraStrategy.__new__(HydraStrategy)
        s.stop_confirmation_enabled = False  # MKT-036 (75s) off on every variant
        s.mkt046_confirm_seconds = confirm_seconds
        s.settlement_hold_enabled = False
        s.narrow_spread_stop_enabled = False
        s._get_effective_stop_level = MagicMock(return_value=1400.0)
        s._log_stop_detail = MagicMock()
        s._execute_stop_loss = MagicMock(return_value="STOPPED")
        return s

    def test_default_10s_does_not_confirm_on_the_second_tick(self):
        s = self._strat(confirm_seconds=10.0)
        e = _entry()

        first = s._check_stop_with_confirmation(e, "put", 1500.0, 1400.0)
        second = s._check_stop_with_confirmation(e, "put", 1500.0, 1400.0)

        assert first is None
        assert second is None  # a real 10s hasn't elapsed between two fast test calls
        s._execute_stop_loss.assert_not_called()

    def test_zero_seconds_confirms_on_the_very_next_tick(self):
        s = self._strat(confirm_seconds=0.0)
        e = _entry()

        first = s._check_stop_with_confirmation(e, "put", 1500.0, 1400.0)
        second = s._check_stop_with_confirmation(e, "put", 1500.0, 1400.0)

        assert first is None  # still requires the breach to be seen twice
        assert second == "STOPPED"
        s._execute_stop_loss.assert_called_once_with(e, "put")

    def test_severity_bypass_still_fires_instantly_regardless_of_confirm_seconds(self):
        """Negative control: the >=2x-trigger severity bypass (unrelated to
        this change) must still fire on the FIRST tick even at the default
        10s setting -- confirms this fix didn't accidentally touch that path."""
        s = self._strat(confirm_seconds=10.0)
        e = _entry()

        result = s._check_stop_with_confirmation(e, "put", 2900.0, 1400.0)  # 2.07x

        assert result == "STOPPED"
        s._execute_stop_loss.assert_called_once_with(e, "put")

    def test_recovery_below_trigger_resets_regardless_of_confirm_seconds(self):
        s = self._strat(confirm_seconds=0.0)
        e = _entry()
        s.daily_state = base_mod.MEICDailyState()
        s._log_safety_event = MagicMock()

        s._check_stop_with_confirmation(e, "put", 1500.0, 1400.0)  # first breach
        result = s._check_stop_with_confirmation(e, "put", 1000.0, 1400.0)  # recovers below trigger

        assert result is None
        s._execute_stop_loss.assert_not_called()
        assert getattr(e, "put_breach_time", None) is None
