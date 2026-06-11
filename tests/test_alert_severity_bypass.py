"""L-C2 — alert severity bypass.

When alerts are DISABLED in config, CRITICAL/HIGH events must still publish
(defense-in-depth on the live-order variant: a naked short, a failed emergency
close, or a tripped breaker must never be silenced by alerts.enabled=false).
LOW/MEDIUM stay suppressed when disabled.

Uses ALERT_DRY_RUN=true so a "published" alert is observable as a True return
without a live Pub/Sub channel.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.alert_service import AlertService, AlertType, AlertPriority  # noqa: E402


@pytest.fixture(autouse=True)
def _dry_run_env(monkeypatch):
    monkeypatch.setenv("ALERT_DRY_RUN", "true")
    yield


def _disabled_service() -> AlertService:
    return AlertService({"alerts": {"enabled": False}}, "HYDRA_C")


def _enabled_service() -> AlertService:
    return AlertService({"alerts": {"enabled": True}}, "HYDRA_C")


def test_disabled_service_still_initializes_publisher_path():
    """The publisher path must be live even when disabled, else the bypass
    would have nothing to publish through."""
    svc = _disabled_service()
    # In ALERT_DRY_RUN mode, _initialize sets _initialized even when disabled.
    assert svc._initialized is True


def test_critical_bypasses_disabled_gate():
    svc = _disabled_service()
    assert svc.send_alert(
        AlertType.NAKED_POSITION,
        "Naked short detected",
        "test",
        priority=AlertPriority.CRITICAL,
    ) is True


def test_high_bypasses_disabled_gate():
    svc = _disabled_service()
    assert svc.send_alert(
        AlertType.STOP_LOSS,
        "Stop loss fired",
        "test",
        priority=AlertPriority.HIGH,
    ) is True


def test_low_is_suppressed_when_disabled():
    svc = _disabled_service()
    assert svc.send_alert(
        AlertType.BOT_STOPPED,
        "Bot stopped",
        "test",
        priority=AlertPriority.LOW,
    ) is False


def test_medium_is_suppressed_when_disabled():
    svc = _disabled_service()
    assert svc.send_alert(
        AlertType.POSITION_OPENED,
        "Position opened",
        "test",
        priority=AlertPriority.MEDIUM,
    ) is False


def test_enabled_service_publishes_all_severities():
    svc = _enabled_service()
    # Distinct TYPE per severity so neither the content-dedup gate (collapses
    # identical repeats) nor the per-type token bucket (caps a same-type burst
    # at 3) interferes — this test is about severity routing, not throttling.
    cases = [
        (AlertType.CIRCUIT_BREAKER, AlertPriority.CRITICAL),
        (AlertType.STOP_LOSS, AlertPriority.HIGH),
        (AlertType.POSITION_CLOSED, AlertPriority.MEDIUM),
        (AlertType.BOT_STOPPED, AlertPriority.LOW),
    ]
    for atype, prio in cases:
        assert svc.send_alert(atype, f"event {prio.value}", "t", priority=prio) is True


def test_default_priority_critical_type_bypasses_when_disabled():
    """A CRITICAL-by-default alert type (no explicit priority) must also bypass."""
    svc = _disabled_service()
    # CIRCUIT_BREAKER defaults to CRITICAL in DEFAULT_PRIORITIES.
    assert svc.send_alert(
        AlertType.CIRCUIT_BREAKER,
        "Breaker open",
        "test",
    ) is True
