"""Regression tests for the 2026-05-28 audit reliability fixes:

  M7  HALF_OPEN→OPEN re-trip fired no alert (re-trip went silent)
  M10 stuck-OPEN reminder could never fire; flapping breaker alerted once ever
  #3  streaming subscribe() (blocking ~10s confirmation) held the data lock,
      freezing get_snapshot/is_healthy/last_tick_age

See docs/migration/PREFLIGHT_AUDIT_FINDINGS.md.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.alert_hooks import IBKRAlertHooks
from shared.ib_streaming import StreamingManager


class _CapturingAlerts:
    def __init__(self):
        self.sent = []

    def send_alert(self, **kw):
        self.sent.append(kw)


class _Breaker:
    def __init__(self, state="closed"):
        self.state = state  # plain string; _is_* helpers handle str states


def _make_hooks():
    h = IBKRAlertHooks.__new__(IBKRAlertHooks)
    h._last_breaker_states = {"orders": "closed"}
    h._stuck_open_since = {}
    h._stuck_open_last_reminder = {}
    h._stuck_open_reminders_today = {}
    h._half_open_since = {}  # audit #49: stable-HALF_OPEN recovery tracking
    h._alerts = _CapturingAlerts()
    brk = _Breaker("closed")
    broker = type("Broker", (), {})()
    broker.circuit_breakers = {"orders": brk}
    h._broker = broker
    return h, brk, h._alerts.sent


def _titles(sent, needle):
    return [s for s in sent if needle in s["title"].lower()]


class TestBreakerOutageAlerting:
    def test_flapping_opens_once_then_keeps_reminding(self):
        """M7 + M10: opened-alert fires once on entry; an OPEN↔HALF_OPEN flap
        does NOT re-spam it; and the stuck/degraded reminder still fires on a
        time cadence (previously it never could once the state flapped)."""
        h, brk, sent = _make_hooks()

        brk.state = "open"
        h._poll_breakers()
        assert "orders" in h._stuck_open_since
        assert len(_titles(sent, "tripped")) == 1

        # Flap: OPEN → HALF_OPEN → OPEN. No additional opened alerts.
        brk.state = "half_open"
        h._poll_breakers()
        brk.state = "open"
        h._poll_breakers()
        assert len(_titles(sent, "tripped")) == 1, "flap must not re-spam opened alert"

        # Force the reminder interval; a steady poll while still degraded must
        # now emit a reminder (the bug: it never fired after flapping).
        h._stuck_open_last_reminder["orders"] = time.monotonic() - 10_000
        h._poll_breakers()
        assert len(_titles(sent, "still degraded")) >= 1

    def test_open_missed_between_polls_still_alerts(self):
        """If the poll interval spans CLOSED→OPEN→HALF_OPEN, the OPEN is never
        observed — entry must still register via the HALF_OPEN (degraded)
        state, not be lost."""
        h, brk, sent = _make_hooks()
        brk.state = "half_open"
        h._poll_breakers()
        assert "orders" in h._stuck_open_since
        assert len(_titles(sent, "tripped")) == 1

    def test_recovery_clears_outage_and_alerts_once(self):
        h, brk, sent = _make_hooks()
        brk.state = "open"
        h._poll_breakers()
        brk.state = "closed"
        h._poll_breakers()
        assert "orders" not in h._stuck_open_since
        assert len(_titles(sent, "recovered")) == 1


class TestStreamingReaderNotBlocked:
    def test_get_snapshot_not_blocked_by_blocking_subscribe(self):
        """#3: a reader must not wait on ibind's blocking subscribe
        confirmation (~10s) — the wire I/O is on _io_lock, not the data lock
        that get_snapshot/is_healthy take."""
        release = threading.Event()

        class SlowWs:
            connected = True

            def subscribe(self, **kw):
                release.wait(2.0)  # simulate ibind's blocking confirmation
                return True

            def unsubscribe(self, **kw):
                return True

        mgr = StreamingManager(SlowWs(), resubscribe_gap_s=0.0)
        t = threading.Thread(target=lambda: mgr.subscribe_quote(123), daemon=True)
        t.start()
        try:
            time.sleep(0.1)  # let subscribe() begin and block inside ws.subscribe
            start = time.monotonic()
            snap = mgr.get_snapshot(123)  # must return immediately
            elapsed = time.monotonic() - start
            assert elapsed < 0.5, f"reader blocked {elapsed:.2f}s on in-flight subscribe"
            assert snap is None  # no tick received yet
        finally:
            release.set()
            t.join(timeout=3)

    def test_active_conids_not_blocked_by_blocking_subscribe(self):
        release = threading.Event()

        class SlowWs:
            connected = True

            def subscribe(self, **kw):
                release.wait(2.0)
                return True

            def unsubscribe(self, **kw):
                return True

        mgr = StreamingManager(SlowWs(), resubscribe_gap_s=0.0)
        t = threading.Thread(target=lambda: mgr.subscribe_quote(456), daemon=True)
        t.start()
        try:
            time.sleep(0.1)
            start = time.monotonic()
            _ = mgr.active_conids()
            assert time.monotonic() - start < 0.5
        finally:
            release.set()
            t.join(timeout=3)
