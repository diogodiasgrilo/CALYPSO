"""Fix #6 (2026-06-08 forensic): the after-hours settlement loop must escalate
to a CRITICAL operator alert after a streak of strict broker-read failures —
NOT silently retry 'settlement pending' forever — while NEVER auto-clearing
tracked legs (which would falsely book unconfirmed P&L)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.alert_service import AlertPriority, AlertType  # noqa: E402
from bots.hydra.strategy import (  # noqa: E402
    SETTLEMENT_MAX_STRICT_READ_FAILURES,
    HydraStrategy,
)


def _strategy(read_side_effect):
    s = HydraStrategy.__new__(HydraStrategy)
    s._settlement_reconciliation_complete = False
    s._settlement_strict_read_failures = 0
    s._settlement_halt_alerted = False
    s.daily_state = SimpleNamespace(entries=[])
    s.alert_service = MagicMock()
    # One tracked conid → reaches the strict-read try block.
    s._expected_position_quantities = MagicMock(return_value={123: -1})
    s._read_open_positions = MagicMock(side_effect=read_side_effect)
    return s


class TestSettlementStrictHalt:
    def test_failures_below_threshold_do_not_alert(self):
        s = _strategy(RuntimeError("broker unreachable"))
        for _ in range(SETTLEMENT_MAX_STRICT_READ_FAILURES - 1):
            assert s.check_after_hours_settlement() is False
        s.alert_service.send_alert.assert_not_called()
        assert s._settlement_strict_read_failures == SETTLEMENT_MAX_STRICT_READ_FAILURES - 1

    def test_threshold_pages_operator_critical_once(self):
        s = _strategy(RuntimeError("broker unreachable"))
        for _ in range(SETTLEMENT_MAX_STRICT_READ_FAILURES):
            assert s.check_after_hours_settlement() is False
        # Paged exactly once, CRITICAL.
        s.alert_service.send_alert.assert_called_once()
        kwargs = s.alert_service.send_alert.call_args.kwargs
        assert kwargs["priority"] == AlertPriority.CRITICAL
        assert kwargs["alert_type"] == AlertType.CRITICAL_INTERVENTION

        # Further failures keep returning False but do NOT re-page (no spam).
        s.check_after_hours_settlement()
        s.check_after_hours_settlement()
        s.alert_service.send_alert.assert_called_once()

    def test_never_auto_clears_tracked_legs(self):
        # The unsafe naive fix would clear UICs to force completion. Prove we
        # never touch tracked state on a failed read: the leg's uic survives.
        leg = SimpleNamespace(entry_number=1, short_call_uic=123)
        s = _strategy(RuntimeError("broker unreachable"))
        s.daily_state.entries = [leg]
        for _ in range(SETTLEMENT_MAX_STRICT_READ_FAILURES + 2):
            assert s.check_after_hours_settlement() is False
        assert leg.short_call_uic == 123  # NOT cleared

    def test_recovery_resets_streak_and_rearms_alert(self):
        # Fail to the threshold (pages once), then a successful read resets the
        # streak + re-arms the alert so a later outage pages again.
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] <= SETTLEMENT_MAX_STRICT_READ_FAILURES:
                raise RuntimeError("broker unreachable")
            return []  # recovered: no open positions

        s = _strategy(flaky)
        # Minimal surface for the success path (no tracked legs settle cleanly).
        s._actual_position_quantities = MagicMock(return_value={})
        s._classify_settlement_conids = MagicMock(return_value=(set(), set(), set()))
        s._process_expired_credits = MagicMock(return_value=0.0)
        s._save_state_to_disk = MagicMock()
        s._pnl_history = []
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        s._log_safety_event = MagicMock()

        for _ in range(SETTLEMENT_MAX_STRICT_READ_FAILURES):
            assert s.check_after_hours_settlement() is False
        assert s.alert_service.send_alert.call_count == 1

        # Next call: read succeeds → streak resets, alert re-armed.
        s.check_after_hours_settlement()
        assert s._settlement_strict_read_failures == 0
        assert s._settlement_halt_alerted is False
