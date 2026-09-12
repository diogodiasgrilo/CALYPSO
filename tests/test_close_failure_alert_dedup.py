"""Emergency-close alert dedup (2026-06-12).

The close-with-retry path is re-invoked every monitoring tick while a position
stays unclosed, so a stuck close fired the EMERGENCY/STOP-CLOSE alert cluster
~84×/hr (variant-C E#2's take-profit couldn't execute and re-alerted all
afternoon). _emergency_close_alert_once collapses that to one alert per
(conid, title) per day, so the IMMEDIATE → STRUGGLING → FAILED escalation still
comes through once, then stays quiet.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from shared.alert_service import AlertType, AlertPriority  # noqa: E402


def _strat(alert_service=None):
    s = HydraStrategy.__new__(HydraStrategy)
    s.alert_service = alert_service if alert_service is not None else MagicMock()
    return s


def test_fires_once_per_conid_title():
    s = _strat()
    for _ in range(40):  # 40 ticks of a stuck close
        s._emergency_close_alert_once(
            877415786, alert_type=AlertType.CIRCUIT_BREAKER,
            title="EMERGENCY CLOSE FAILED", message="m", priority=AlertPriority.CRITICAL)
    assert s.alert_service.send_alert.call_count == 1


def test_escalation_titles_each_fire_once():
    # The full story (IMMEDIATE → STRUGGLING → FAILED) comes through once each.
    s = _strat()
    for _ in range(5):
        s._emergency_close_alert_once(877, alert_type=AlertType.EMERGENCY_CLOSE,
                                      title="STOP CLOSE FAILED - IMMEDIATE", message="m",
                                      priority=AlertPriority.HIGH)
        s._emergency_close_alert_once(877, alert_type=AlertType.EMERGENCY_CLOSE,
                                      title="EMERGENCY CLOSE STRUGGLING", message="m",
                                      priority=AlertPriority.CRITICAL)
        s._emergency_close_alert_once(877, alert_type=AlertType.CIRCUIT_BREAKER,
                                      title="EMERGENCY CLOSE FAILED", message="m",
                                      priority=AlertPriority.CRITICAL)
    assert s.alert_service.send_alert.call_count == 3


def test_different_conid_fires_separately():
    s = _strat()
    s._emergency_close_alert_once(877, alert_type=AlertType.CIRCUIT_BREAKER,
                                  title="EMERGENCY CLOSE FAILED", message="m",
                                  priority=AlertPriority.CRITICAL)
    s._emergency_close_alert_once(999, alert_type=AlertType.CIRCUIT_BREAKER,
                                  title="EMERGENCY CLOSE FAILED", message="m",
                                  priority=AlertPriority.CRITICAL)
    assert s.alert_service.send_alert.call_count == 2  # two distinct stuck legs


def test_no_alert_service_is_noop():
    s = _strat(alert_service=None)
    # Must not raise even with no alert service (e.g. dry-run shadow).
    s._emergency_close_alert_once(877, alert_type=AlertType.CIRCUIT_BREAKER,
                                  title="EMERGENCY CLOSE FAILED", message="m",
                                  priority=AlertPriority.CRITICAL)
    assert (877, "EMERGENCY CLOSE FAILED") in s._emergency_close_alerted


def test_passes_conid_in_details():
    s = _strat()
    s._emergency_close_alert_once(877, alert_type=AlertType.CIRCUIT_BREAKER,
                                  title="EMERGENCY CLOSE FAILED", message="m",
                                  priority=AlertPriority.CRITICAL)
    kw = s.alert_service.send_alert.call_args.kwargs
    assert kw["details"]["conid"] == 877
