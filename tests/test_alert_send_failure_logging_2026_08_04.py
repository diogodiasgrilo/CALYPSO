"""Alert-send-failure log level fix (2026-08-04).

Found during the same sweep that produced test_alert_publish_reliability.py:
four call sites wrapped a CRITICAL/HIGH alert_service.send_alert() call in a
try/except that logged the failure at logger.debug — filtered out of
production logging entirely — and labeled it "(non-fatal)", even though the
alert being lost is exactly the one telling an operator about an untracked
naked leg, a deferred settlement booking, a failed short buy-back, or a
STUCK EMERGENCY CLOSE (the 4th site — found by round-1 adversarial review of
the first 3, not the original sweep). Five other "alert send failed" sites in
the same two files already used logger.warning/logger.error — these four
were regressions from that established convention, not intentional design.

Each test drives the REAL method (not a mock of it) with a real exception
raised from send_alert, and asserts the failure is logged at ERROR with a
non-blank, type-identified message — not swallowed at DEBUG.
"""
from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from bots.hydra.base_strategy import IronCondorEntry  # noqa: E402


def _raising_alert_service(msg="Pub/Sub publish timed out"):
    svc = MagicMock()
    svc.send_alert.side_effect = RuntimeError(msg)
    return svc


class TestSettlementDeferredAlertFailureLogging:
    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.BOT_NAME = "HYDRA_B"
        s._settlement_deferred_alert_date = None
        s.alert_service = _raising_alert_service("boom-settlement")
        return s

    def test_send_failure_logged_at_error_not_debug(self, caplog):
        s = self._strat()
        with caplog.at_level(logging.DEBUG, logger="bots.hydra.base_strategy"):
            s._alert_settlement_deferred()

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG
                  and "settlement-deferred" in r.message]
        assert any("settlement-deferred" in r.message for r in errors), caplog.text
        assert debugs == [], "must not still be logged at DEBUG"
        assert any("RuntimeError" in r.message and "boom-settlement" in r.message
                   for r in errors), caplog.text


class TestShortCloseFailedAlertFailureLogging:
    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.BOT_NAME = "HYDRA_B"
        s._b2_short_fail_alerted = set()
        s.alert_service = _raising_alert_service("boom-short-close")
        return s

    def test_send_failure_logged_at_error_not_debug(self, caplog):
        s = self._strat()
        entry = IronCondorEntry(entry_number=1)
        with caplog.at_level(logging.DEBUG, logger="bots.hydra.base_strategy"):
            s._alert_short_close_failed(entry, "put")

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG
                  and "short-close-failed" in r.message]
        assert any("short-close-failed" in r.message for r in errors), caplog.text
        assert debugs == []
        assert any("RuntimeError" in r.message and "boom-short-close" in r.message
                   for r in errors), caplog.text


class TestOrphanSweepAlertFailureLogging:
    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.BOT_NAME = "HYDRA_B"
        s.contracts_per_entry = 7
        s.alert_service = _raising_alert_service("boom-orphan")
        # Bypass the real conid-diffing internals — force a single orphan so
        # the method reaches the send_alert() call under test.
        s._recon_detect_orphans = staticmethod(lambda expected, actual: {999: -1})
        s._recon_suppress_recent_closes = lambda orphans, now: orphans
        return s

    def test_send_failure_logged_at_error_not_debug(self, caplog):
        s = self._strat()
        with caplog.at_level(logging.DEBUG, logger="bots.hydra.strategy"):
            count = s._reconcile_orphan_sweep({}, {999: -1})

        assert count == 1
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG
                  and "orphan-sweep" in r.message]
        assert any("orphan-sweep" in r.message for r in errors), caplog.text
        assert debugs == []
        assert any("RuntimeError" in r.message and "boom-orphan" in r.message
                   for r in errors), caplog.text


class TestEmergencyCloseAlertFailureLogging:
    """4th site, found by round-1 review of the first 3: EMERGENCY-001's
    _emergency_close_alert_once had the identical logger.debug swallow,
    guarding the alert for a STUCK emergency close (a live unhedged/stuck
    position after the full close-retry budget is exhausted) — arguably the
    most severe alert in the escalation chain."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.BOT_NAME = "HYDRA_B"
        s._emergency_close_alerted = set()
        s.alert_service = _raising_alert_service("boom-emergency-close")
        return s

    def test_send_failure_logged_at_error_not_debug(self, caplog):
        from shared.alert_service import AlertType, AlertPriority

        s = self._strat()
        with caplog.at_level(logging.DEBUG, logger="bots.hydra.base_strategy"):
            s._emergency_close_alert_once(
                12345,
                alert_type=AlertType.CIRCUIT_BREAKER,
                title="EMERGENCY CLOSE FAILED",
                message="stuck leg",
                priority=AlertPriority.CRITICAL,
            )

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG
                  and "emergency-close" in r.message]
        assert any("emergency-close" in r.message for r in errors), caplog.text
        assert debugs == [], "must not still be logged at DEBUG"
        assert any("RuntimeError" in r.message and "boom-emergency-close" in r.message
                   for r in errors), caplog.text

    def test_dedup_still_fires_once_per_conid_title_per_day(self, caplog):
        """Non-regression: the fix must not disturb the existing per-(conid,
        title) dedup that prevents ~84x/hr re-alert spam on a stuck close."""
        from shared.alert_service import AlertType, AlertPriority

        s = self._strat()
        kwargs = dict(
            alert_type=AlertType.CIRCUIT_BREAKER, title="EMERGENCY CLOSE FAILED",
            message="stuck leg", priority=AlertPriority.CRITICAL,
        )
        s._emergency_close_alert_once(12345, **kwargs)
        s._emergency_close_alert_once(12345, **kwargs)
        assert s.alert_service.send_alert.call_count == 1
