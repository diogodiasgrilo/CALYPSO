"""Polish Item 1 — Telegram alert-hook tests.

Pins the IBKRAlertHooks bridge that main.py uses to translate IBClient
breaker + warmup-exhaustion state into Telegram alerts. Specifically
tests the amendments folded into the plan (A1.1–A1.6).

Test strategy: a stub IBClient that exposes only the surface the hook
reads (`circuit_breakers` dict, `snapshot_warmup_exhausted_count`
property) + a recording stub for AlertService. No live IBKR.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_retry import CircuitBreaker, CircuitState
from bots.hydra.alert_hooks import (
    IBKRAlertHooks,
    SNAPSHOT_EXHAUSTION_SEVERE_THRESHOLD,
    STUCK_OPEN_REMINDER_INTERVAL_S,
    STUCK_OPEN_REMINDERS_PER_DAY,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────


class MockBrokerWithState:
    """Stub IBClient — exposes only the surface IBKRAlertHooks reads.

    All breakers start CLOSED. Tests mutate them via `set_breaker_open(...)`
    / `set_breaker_closed(...)` between poll() calls to exercise transitions.

    `snapshot_warmup_exhausted_count` is a plain int the test bumps directly.
    """

    def __init__(self):
        self.circuit_breakers: dict[str, CircuitBreaker] = {
            family: CircuitBreaker(name=f"ib.{family}")
            for family in ("oauth", "session", "portfolio", "market", "orders")
        }
        self.snapshot_warmup_exhausted_count: int = 0

    def set_breaker_state(self, family: str, state: CircuitState):
        """Force a breaker to a given state without going through
        record_* (which has its own threshold rules)."""
        cb = self.circuit_breakers[family]
        cb._state = state  # type: ignore[attr-defined]


@pytest.fixture
def broker():
    return MockBrokerWithState()


@pytest.fixture
def alert_svc():
    """A MagicMock whose `send_alert(**kwargs)` records every call."""
    svc = MagicMock()
    return svc


# ─── A1.5 — zero spurious alerts on stable state ──────────────────────────


class TestNoSpuriousAlertsOnStableState:
    """A1.5 contract: a poll loop with no state changes fires ZERO alerts.

    This is the regression-prevention test for state-bleed bugs where
    a future refactor accidentally fires on initial state."""

    def test_initial_construction_does_not_alert(self, broker, alert_svc):
        IBKRAlertHooks(broker, alert_svc)
        alert_svc.send_alert.assert_not_called()

    def test_N_polls_with_stable_state_fire_zero_alerts(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        for _ in range(50):
            hook.poll()
        alert_svc.send_alert.assert_not_called()


# ─── Breaker transition alerts (CLOSED → OPEN per family) ─────────────────


class TestBreakerOpenedAlerts:
    """Per A1 + A1.3: CLOSED → OPEN transition fires ONE alert.
    `orders` family is CRITICAL; other families are HIGH."""

    def test_orders_family_fires_critical_circuit_breaker_alert(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        broker.set_breaker_state("orders", CircuitState.OPEN)
        hook.poll()

        alert_svc.send_alert.assert_called_once()
        kwargs = alert_svc.send_alert.call_args.kwargs
        # AlertType + Priority comparisons via string fallback so the test
        # works regardless of whether the enum import succeeded.
        assert "CIRCUIT_BREAKER" in str(kwargs["alert_type"]) or \
               str(kwargs["alert_type"]) == "CIRCUIT_BREAKER"
        assert "CRITICAL" in str(kwargs["priority"]) or \
               str(kwargs["priority"]) == "CRITICAL"
        assert "orders" in kwargs["title"].lower() or "orders" in kwargs["message"].lower()

    def test_market_family_fires_high_api_error(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        broker.set_breaker_state("market", CircuitState.OPEN)
        hook.poll()

        alert_svc.send_alert.assert_called_once()
        kwargs = alert_svc.send_alert.call_args.kwargs
        assert "API_ERROR" in str(kwargs["alert_type"]) or \
               str(kwargs["alert_type"]) == "API_ERROR"
        assert "HIGH" in str(kwargs["priority"]) or \
               str(kwargs["priority"]) == "HIGH"
        assert "market" in kwargs["title"].lower() or "market" in kwargs["message"].lower()

    def test_transition_fires_once_not_per_poll(self, broker, alert_svc):
        """Idempotence (A1): polling N times with the breaker stuck
        OPEN fires the transition alert exactly once."""
        hook = IBKRAlertHooks(broker, alert_svc)
        broker.set_breaker_state("portfolio", CircuitState.OPEN)
        for _ in range(10):
            hook.poll()
        # 1 transition alert + 0 stuck-OPEN reminders yet (we haven't
        # waited 15 min — controlled by time.monotonic).
        assert alert_svc.send_alert.call_count == 1

    def test_distinct_families_alert_independently(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        broker.set_breaker_state("orders", CircuitState.OPEN)
        broker.set_breaker_state("market", CircuitState.OPEN)
        hook.poll()
        # 2 distinct alerts — one per family.
        assert alert_svc.send_alert.call_count == 2


# ─── Breaker recovery alert (OPEN → CLOSED) ───────────────────────────────


class TestBreakerRecoveredAlert:
    def test_recovery_fires_low_connection_restored(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        # Trip
        broker.set_breaker_state("orders", CircuitState.OPEN)
        hook.poll()
        alert_svc.send_alert.reset_mock()
        # Recover
        broker.set_breaker_state("orders", CircuitState.CLOSED)
        hook.poll()

        alert_svc.send_alert.assert_called_once()
        kwargs = alert_svc.send_alert.call_args.kwargs
        assert "CONNECTION_RESTORED" in str(kwargs["alert_type"]) or \
               str(kwargs["alert_type"]) == "CONNECTION_RESTORED"
        assert "LOW" in str(kwargs["priority"]) or \
               str(kwargs["priority"]) == "LOW"


# ─── Stuck-OPEN reminder cadence (A1.3) ───────────────────────────────────


class TestStuckOpenReminders:
    """A1.3: a breaker stuck OPEN > 15 min fires reminder alerts,
    capped at N reminders per family per day."""

    def test_no_reminder_within_first_15_min(self, broker, alert_svc, monkeypatch):
        """No reminder until STUCK_OPEN_REMINDER_INTERVAL_S has elapsed."""
        # Freeze time.monotonic
        clock = [1000.0]
        monkeypatch.setattr(
            "bots.hydra.alert_hooks.time.monotonic", lambda: clock[0]
        )
        hook = IBKRAlertHooks(broker, alert_svc)
        broker.set_breaker_state("orders", CircuitState.OPEN)
        hook.poll()  # initial transition alert
        alert_svc.send_alert.reset_mock()
        # Advance time by 14 min — still under the interval.
        clock[0] += 14 * 60
        hook.poll()
        alert_svc.send_alert.assert_not_called()

    def test_reminder_after_interval(self, broker, alert_svc, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(
            "bots.hydra.alert_hooks.time.monotonic", lambda: clock[0]
        )
        hook = IBKRAlertHooks(broker, alert_svc)
        broker.set_breaker_state("orders", CircuitState.OPEN)
        hook.poll()  # initial CRITICAL transition
        alert_svc.send_alert.reset_mock()
        # Advance past the interval
        clock[0] += STUCK_OPEN_REMINDER_INTERVAL_S + 1
        hook.poll()
        # One reminder fired
        assert alert_svc.send_alert.call_count == 1
        kwargs = alert_svc.send_alert.call_args.kwargs
        # Wording covers a breaker that is OPEN or flapping OPEN↔HALF_OPEN
        # on failing probes (audit M7/M10).
        assert "still degraded" in kwargs["title"]

    def test_reminders_capped_per_day(self, broker, alert_svc, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(
            "bots.hydra.alert_hooks.time.monotonic", lambda: clock[0]
        )
        hook = IBKRAlertHooks(broker, alert_svc)
        broker.set_breaker_state("orders", CircuitState.OPEN)
        hook.poll()  # transition
        alert_svc.send_alert.reset_mock()
        # Step through N+1 reminder intervals
        for _ in range(STUCK_OPEN_REMINDERS_PER_DAY + 5):
            clock[0] += STUCK_OPEN_REMINDER_INTERVAL_S + 1
            hook.poll()
        # Cap respected
        assert alert_svc.send_alert.call_count == STUCK_OPEN_REMINDERS_PER_DAY


# ─── Snapshot warmup exhaustion alerts (A1.2) ─────────────────────────────


class TestSnapshotExhaustionAlerts:
    """A1.2: ONE MEDIUM alert on first exhaustion of the day + ONE HIGH
    follow-up at 25+/day. No floods."""

    def test_no_alert_when_counter_unchanged(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        # Counter starts at 0; no exhaustion this iteration.
        hook.poll()
        alert_svc.send_alert.assert_not_called()

    def test_first_exhaustion_fires_medium_alert(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        broker.snapshot_warmup_exhausted_count = 1
        hook.poll()
        alert_svc.send_alert.assert_called_once()
        kwargs = alert_svc.send_alert.call_args.kwargs
        assert "DATA_QUALITY" in str(kwargs["alert_type"]) or \
               str(kwargs["alert_type"]) == "DATA_QUALITY"
        assert "MEDIUM" in str(kwargs["priority"]) or \
               str(kwargs["priority"]) == "MEDIUM"
        assert "First snapshot warmup exhaustion" in kwargs["title"]

    def test_subsequent_exhaustions_below_threshold_do_not_re_alert(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        # 5 exhaustions, no follow-up alert (below severe threshold)
        for n in (1, 2, 3, 4, 5):
            broker.snapshot_warmup_exhausted_count = n
            hook.poll()
        # ONLY the first-of-day MEDIUM fired
        assert alert_svc.send_alert.call_count == 1

    def test_severe_threshold_fires_additional_high_alert(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        # Walk up to severe threshold
        broker.snapshot_warmup_exhausted_count = 1
        hook.poll()  # first-of-day MEDIUM
        alert_svc.send_alert.reset_mock()
        broker.snapshot_warmup_exhausted_count = SNAPSHOT_EXHAUSTION_SEVERE_THRESHOLD
        hook.poll()
        # ONE HIGH follow-up
        alert_svc.send_alert.assert_called_once()
        kwargs = alert_svc.send_alert.call_args.kwargs
        assert "HIGH" in str(kwargs["priority"]) or \
               str(kwargs["priority"]) == "HIGH"
        assert "severely degraded" in kwargs["message"]

    def test_mark_new_day_resets_per_day_flags(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        # Day 1: trigger both alerts
        broker.snapshot_warmup_exhausted_count = 1
        hook.poll()
        broker.snapshot_warmup_exhausted_count = SNAPSHOT_EXHAUSTION_SEVERE_THRESHOLD
        hook.poll()
        assert alert_svc.send_alert.call_count == 2
        alert_svc.send_alert.reset_mock()

        # Day rollover
        hook.mark_new_day()

        # Day 2: counter is monotonic so it stays at the same lifetime
        # value, but the per-day baseline resets. Bumping by 1 (one new
        # exhaustion today) should fire the first-of-day MEDIUM again.
        broker.snapshot_warmup_exhausted_count += 1
        hook.poll()
        assert alert_svc.send_alert.call_count == 1


# ─── on_ensure_connected_failed (A1.3 — the highest-value alert) ─────────


class TestEnsureConnectedFailed:
    def test_strategy_bot_path_is_low_api_error_with_reason(self, broker, alert_svc):
        # will_restart=True (default, strategy bot) is SELF-HEALING in broker
        # mode — main.py just break()s for a systemd restart while the real
        # session (owned by calypso-broker) recovers on its own. So it is
        # demoted to LOW (Telegram-only via routing) — informational, not an
        # actionable email (changed 2026-06-11 after it spammed the inbox on
        # every restart cycle). Still API_ERROR, still carries the reason.
        hook = IBKRAlertHooks(broker, alert_svc)
        hook.on_ensure_connected_failed(reason="competing session detected")
        alert_svc.send_alert.assert_called_once()
        kwargs = alert_svc.send_alert.call_args.kwargs
        assert "API_ERROR" in str(kwargs["alert_type"]) or \
               str(kwargs["alert_type"]) == "API_ERROR"
        assert "LOW" in str(kwargs["priority"]) or \
               str(kwargs["priority"]) == "LOW"
        assert "competing session detected" in kwargs["message"]

    def test_no_reason_still_sends(self, broker, alert_svc):
        hook = IBKRAlertHooks(broker, alert_svc)
        hook.on_ensure_connected_failed()
        alert_svc.send_alert.assert_called_once()

    def test_bot_variant_says_auto_restarting(self, broker, alert_svc):
        # Default (strategy bot) framing: self-healing auto-restart, not an
        # operator-actionable "session lost" emergency.
        hook = IBKRAlertHooks(broker, alert_svc)
        hook.on_ensure_connected_failed("competing session")
        kwargs = alert_svc.send_alert.call_args.kwargs
        assert "HYDRA" in kwargs["title"] and "auto-restarting" in kwargs["title"]
        assert "automatic systemd restart" in kwargs["message"]
        assert "LOW" in str(kwargs["priority"])

    def test_broker_variant_says_broker_stays_up(self, broker, alert_svc):
        # Broker framing (will_restart=False): broker does NOT exit; must not
        # claim a bot is restarting, and must name calypso-broker.
        hook = IBKRAlertHooks(broker, alert_svc)
        hook.on_ensure_connected_failed("broker session re-auth gate", will_restart=False)
        kwargs = alert_svc.send_alert.call_args.kwargs
        assert "calypso-broker" in kwargs["title"]
        assert "does NOT exit" in kwargs["message"]
        assert "bot exiting" not in kwargs["message"]
        assert "broker session re-auth gate" in kwargs["message"]
        assert "HIGH" in str(kwargs["priority"])


# ─── Defensive: alert_service raising never crashes the loop ──────────────


class TestPollNeverPropagatesAlertException:
    def test_alert_send_failure_swallowed(self, broker, alert_svc):
        """If alert_service.send_alert raises (e.g. Pub/Sub down),
        the polling loop must NOT propagate the exception. A monitoring
        bug must never take down the trading bot."""
        hook = IBKRAlertHooks(broker, alert_svc)
        alert_svc.send_alert.side_effect = RuntimeError("Pub/Sub down")
        broker.set_breaker_state("orders", CircuitState.OPEN)
        # Should not raise
        hook.poll()
        # And it tried to send
        alert_svc.send_alert.assert_called()
