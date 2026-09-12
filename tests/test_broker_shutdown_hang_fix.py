"""calypso-broker graceful-shutdown fix (found in the 2026-08-03 full-day audit).

Incident: services/broker/main.py's shutdown hook called stop.set() then
maintain_thread.join(timeout=30) — but stop.set() only short-circuits the
NEXT loop iteration, not an in-flight ensure_connected() retry backoff
(shared/ib_retry.py's retry_with_backoff used a plain, non-cancellable
time.sleep()). The first time SIGTERM landed while the broker was mid
OAuth-rehandshake (a 2026-08-03 deploy restart), the join blocked the full
30s, systemd's TimeoutStopSec elapsed, and 84 processes were SIGKILLed.

Fix: retry_with_backoff is now cooperatively interruptible via a module-wide
SHUTDOWN_EVENT (shared/ib_retry.py), and _shutdown_broker (extracted from the
FastAPI shutdown hook so it's directly testable) sets that event BEFORE
joining — see tests/test_ib_retry.py::TestShutdownAbort for the retry-layer
coverage. These tests exercise the broker-shutdown sequence itself: does it
actually set the event, in the right order, and still behave safely if the
maintenance thread doesn't cooperate.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_retry import SHUTDOWN_EVENT  # noqa: E402
from services.broker.main import _shutdown_broker  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_shutdown_event():
    SHUTDOWN_EVENT.clear()
    yield
    SHUTDOWN_EVENT.clear()


class TestShutdownBroker:
    def test_sets_shutdown_event_before_joining(self):
        # The maintenance thread's target checks SHUTDOWN_EVENT itself
        # (mirroring what an in-flight ensure_connected() retry would see) —
        # if the event isn't set BEFORE join() is called, this thread would
        # spin past its own check and the test would time out / hang.
        stop = threading.Event()
        observed_event_state_at_start = {}

        def _target():
            stop.wait(0.01)
            observed_event_state_at_start["set"] = SHUTDOWN_EVENT.is_set()

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        ib = MagicMock()

        _shutdown_broker(ib, stop, thread, join_timeout=5)

        assert observed_event_state_at_start["set"] is True
        assert SHUTDOWN_EVENT.is_set()  # left set — this process IS shutting down

    def test_stop_event_also_set(self):
        stop = threading.Event()
        thread = threading.Thread(target=lambda: stop.wait(0.01), daemon=True)
        thread.start()
        ib = MagicMock()

        _shutdown_broker(ib, stop, thread, join_timeout=5)
        assert stop.is_set()

    def test_joins_the_maintenance_thread_before_disconnecting(self):
        stop = threading.Event()
        order = []

        def _target():
            stop.wait(5)
            order.append("thread-exited")

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        ib = MagicMock()
        ib.disconnect.side_effect = lambda: order.append("disconnected")

        _shutdown_broker(ib, stop, thread, join_timeout=5)

        assert order == ["thread-exited", "disconnected"], (
            "disconnect() must happen AFTER the maintain thread has fully "
            "stopped (findings #36/#67) — otherwise a reconnect could still "
            "be in flight when we release the session"
        )

    def test_resolves_quickly_when_thread_is_blocked_in_an_abortable_retry(self):
        # Simulates the ACTUAL 2026-08-03 incident: the maintenance thread is
        # "stuck" inside what would be ensure_connected()'s retry backoff.
        # Model it directly with SHUTDOWN_EVENT.wait() (the real mechanism
        # retry_with_backoff uses) rather than re-implementing retry logic.
        stop = threading.Event()

        def _target():
            # Blocks here until SHUTDOWN_EVENT fires — exactly what an
            # abortable retry_with_backoff attempt does mid-delay.
            SHUTDOWN_EVENT.wait(30)

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        time.sleep(0.05)  # let the thread actually enter the wait
        ib = MagicMock()

        start = time.monotonic()
        _shutdown_broker(ib, stop, thread, join_timeout=30)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, (
            f"took {elapsed:.2f}s — this is the exact incident being fixed: "
            f"a thread blocked in an abortable retry must unblock within "
            f"~1 tick of SHUTDOWN_EVENT being set, not the full 30s join timeout"
        )
        ib.disconnect.assert_called_once()

    def test_still_disconnects_and_logs_if_thread_never_stops(self):
        # Defense-in-depth: even a thread that ignores SHUTDOWN_EVENT entirely
        # (e.g. genuinely stuck in a non-abortable 'orders' family retry, or a
        # bug) must not prevent disconnect() from eventually running — the
        # existing "log + disconnect anyway" fallback must survive this change.
        stop = threading.Event()
        thread = threading.Thread(target=lambda: time.sleep(999), daemon=True)
        thread.start()
        ib = MagicMock()

        _shutdown_broker(ib, stop, thread, join_timeout=0.05)

        ib.disconnect.assert_called_once()

    def test_disconnect_exception_is_swallowed(self):
        stop = threading.Event()
        thread = threading.Thread(target=lambda: None, daemon=True)
        thread.start()
        thread.join()
        ib = MagicMock()
        ib.disconnect.side_effect = RuntimeError("already gone")

        # Must not raise.
        _shutdown_broker(ib, stop, thread, join_timeout=5)
