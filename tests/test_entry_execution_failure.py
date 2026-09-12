"""_record_failed_entry (2026-07-31) — the fix for a real incident on live
variant B: entry #3 exhausted all order-placement retries (the broker accepted
a market order but it never filled — an IBKR paper-engine matching anomaly,
confirmed via a direct broker query, not a HYDRA logic bug) and the failure
produced ZERO operator-visible signal: no DB row, no dashboard detail (the
entry-slot card rendered as a blank "window passed"), and no Telegram alert —
only a raw log line and an incremented in-memory counter.

These tests pin the fix: a genuine execution failure now flows through the
same append/DB/alert pipeline `_record_skipped_entry` already used for
deliberate strategic skips, but marked `execution_failed=True` and alerted at
HIGH priority (never LOW/MEDIUM — those are silently dropped when a variant's
alerts.enabled=false; HIGH/CRITICAL are the only priorities guaranteed to
publish regardless, per the severity bypass in shared/alert_service.py).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import ENTRY_MAX_RETRIES
from bots.hydra.strategy import HydraStrategy
from shared.alert_service import AlertType, AlertPriority


def _strat(*, data_recorder=None, alert_service=None):
    s = HydraStrategy.__new__(HydraStrategy)
    s.daily_state = SimpleNamespace(entries=[])
    s._data_recorder = data_recorder if data_recorder is not None else MagicMock()
    s.alert_service = alert_service if alert_service is not None else MagicMock()
    s._record_shadow_entry = lambda **kw: None  # unrelated observation-only path
    s.current_price = 7439.73
    s.current_vix = 17.5
    s.contracts_per_entry = 7
    return s


class TestRecordFailedEntry:
    def test_appends_entry_marked_execution_failed(self):
        s = _strat()
        s._record_failed_entry(3, "Entry execution failed")
        assert len(s.daily_state.entries) == 1
        entry = s.daily_state.entries[0]
        assert entry.entry_number == 3
        assert entry.is_complete is True
        assert entry.execution_failed is True
        # Both sides marked skipped too — preserves every existing "no real
        # position was opened" check elsewhere in the codebase.
        assert entry.call_side_skipped is True
        assert entry.put_side_skipped is True
        assert f"after {ENTRY_MAX_RETRIES} attempts" in entry.skip_reason
        assert "Entry execution failed" in entry.skip_reason

    def test_normal_entry_defaults_execution_failed_false(self):
        """A real, successfully-filled entry must never be mistaken for a
        failure — pins the dataclass default so a future refactor can't
        accidentally flip this."""
        from bots.hydra.strategy import HydraIronCondorEntry
        real_entry = HydraIronCondorEntry(entry_number=2)
        assert real_entry.execution_failed is False

    def test_writes_db_row_with_execution_failed_flag(self):
        recorder = MagicMock()
        s = _strat(data_recorder=recorder)
        s._record_failed_entry(3, "Entry execution failed")
        assert recorder.record_skipped_entry.called
        (call_kwargs,), _ = recorder.record_skipped_entry.call_args
        assert call_kwargs["execution_failed"] == 1
        assert call_kwargs["entry_number"] == 3
        assert call_kwargs["contracts"] == 0  # v8 convention: no entry placed
        assert "Entry execution failed" in call_kwargs["skip_reason"]

    def test_db_write_failure_does_not_raise(self):
        """Matches _record_skipped_entry's own guarantee — a DB hiccup must
        never block the strategy loop or swallow the alert."""
        recorder = MagicMock()
        recorder.record_skipped_entry.side_effect = Exception("disk full")
        alert_service = MagicMock()
        s = _strat(data_recorder=recorder, alert_service=alert_service)
        s._record_failed_entry(3, "Entry execution failed")  # must not raise
        assert alert_service.send_alert.called  # alert still fires

    def test_sends_high_priority_alert_explicitly(self):
        """The one non-negotiable detail from the design review: priority
        must be explicit HIGH, never inherited from a default, because
        LOW/MEDIUM alerts are silently dropped when alerts.enabled=false."""
        alert_service = MagicMock()
        s = _strat(alert_service=alert_service)
        s._record_failed_entry(3, "Entry execution failed")
        assert alert_service.send_alert.called
        _, call_kwargs = alert_service.send_alert.call_args
        assert call_kwargs["alert_type"] == AlertType.ENTRY_EXECUTION_FAILED
        assert call_kwargs["priority"] == AlertPriority.HIGH
        assert "3" in call_kwargs["title"]
        assert call_kwargs["details"]["entry_number"] == 3

    def test_send_alert_false_skips_alert_but_still_persists(self):
        """Mirrors _record_skipped_entry's send_alert=False contract (for a
        caller that wants to send its own bespoke alert instead)."""
        recorder = MagicMock()
        alert_service = MagicMock()
        s = _strat(data_recorder=recorder, alert_service=alert_service)
        s._record_failed_entry(3, "Entry execution failed", send_alert=False)
        assert recorder.record_skipped_entry.called  # DB write still happens
        assert not alert_service.send_alert.called    # but no alert

    def test_alert_send_exception_does_not_raise(self):
        """Matches _record_skipped_entry's try/except around the Telegram
        call — a Pub/Sub hiccup must never crash the strategy loop."""
        alert_service = MagicMock()
        alert_service.send_alert.side_effect = Exception("pubsub down")
        s = _strat(alert_service=alert_service)
        s._record_failed_entry(3, "Entry execution failed")  # must not raise

    def test_entry_type_is_hydra_iron_condor_entry(self):
        """The appended object must be a real HydraIronCondorEntry (not a bare
        IronCondorEntry) so dashboard/state serialization code that expects
        Hydra-specific fields (call_only, put_only, etc.) keeps working."""
        from bots.hydra.strategy import HydraIronCondorEntry
        s = _strat()
        s._record_failed_entry(3, "Entry execution failed")
        assert isinstance(s.daily_state.entries[0], HydraIronCondorEntry)
