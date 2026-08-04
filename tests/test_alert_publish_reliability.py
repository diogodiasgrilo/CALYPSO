"""Alert publish reliability (added 2026-08-04).

Found live: shared/alert_service.py's Pub/Sub publish failure handler logged
f"Failed to publish alert to Pub/Sub: {e}" — but `future.result(timeout=5)`
raises a bare `concurrent.futures.TimeoutError()` on timeout, which
stringifies to "". Confirmed in production logs: every strategy restart
logged "Failed to publish alert to Pub/Sub: " with nothing after the colon.
Confirmed via 30-day history this recurs (not a one-off).

This file exercises the REAL (non-dry-run) publish path — every existing
alert test uses ALERT_DRY_RUN=true, which short-circuits before send_alert
ever reaches the publish/retry/dead-letter code below. These tests construct
AlertService with dry-run OFF and drop in a mocked `_publisher`/`_topic_path`
to reach that code directly.

Covers:
  - describe_exception never returns a blank string
  - CRITICAL/HIGH get one retry (2 attempts); MEDIUM/LOW get one attempt only
  - a publish that exhausts its retry budget writes a dead-letter record
  - a publisher that failed to initialize lazily retries on a 60s cooldown
  - a broken dead-letter write never crashes send_alert or masks the real error
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import alert_service as alert_service_module  # noqa: E402
from shared.alert_service import (  # noqa: E402
    AlertPriority,
    AlertService,
    AlertType,
    describe_exception,
)


@pytest.fixture(autouse=True)
def _isolate_dead_letter_file(tmp_path, monkeypatch):
    """Every test gets its own dead-letter file — never touch the real repo's
    data/failed_alerts.jsonl, and never leak state between tests."""
    monkeypatch.setattr(alert_service_module, "FAILED_ALERTS_PATH", str(tmp_path / "failed_alerts.jsonl"))
    yield


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The retry gap uses time.sleep(1) — don't actually slow the suite down."""
    monkeypatch.setattr(alert_service_module.time, "sleep", lambda _s: None)


def _live_svc(monkeypatch, on_gcp: bool = True) -> AlertService:
    """AlertService with dry-run OFF, publisher already 'initialized' with a
    MagicMock so we can control publish()/future.result() per test."""
    # Note: __init__ still runs the REAL _initialize() once (is_running_on_gcp
    # mocked True, so it attempts `from google.cloud import pubsub_v1`), before
    # we overwrite _initialized/_publisher/_topic_path below — this no-ops
    # locally (the import or PublisherClient() construction fails and is
    # caught), but would NOT be a no-op if this suite were ever run somewhere
    # google-cloud-pubsub is actually installed with real ADC credentials
    # (e.g. accidentally on the VM) — don't run this file there.
    monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
    monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: on_gcp)
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    svc._initialized = True
    svc._publisher = MagicMock()
    svc._topic_path = "projects/test/topics/calypso-alerts"
    return svc


def _mock_future(result=None, exc: Exception = None) -> MagicMock:
    future = MagicMock()
    if exc is not None:
        future.result.side_effect = exc
    else:
        future.result.return_value = result or "mid-123"
    return future


def _read_dead_letters(monkeypatch=None) -> list:
    path = alert_service_module.FAILED_ALERTS_PATH
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ─── describe_exception: never blank ──────────────────────────────────────

def test_describe_exception_bare_timeout_error_is_not_blank():
    # concurrent.futures.TimeoutError() has an empty str() — the exact bug.
    e = TimeoutError()
    assert str(e) == ""
    result = describe_exception(e)
    assert result != ""
    assert "TimeoutError" in result


def test_describe_exception_with_message_includes_type_and_text():
    result = describe_exception(ValueError("bad payload"))
    assert result == "ValueError: bad payload"


# ─── Retry: CRITICAL/HIGH get 2 attempts, MEDIUM/LOW get 1 ───────────────

def test_critical_alert_retries_once_and_succeeds_on_second_attempt(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()),
        _mock_future(result="mid-ok"),
    ]
    result = svc.send_alert(
        AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
        priority=AlertPriority.CRITICAL,
    )
    assert result is True
    assert svc._publisher.publish.call_count == 2
    assert _read_dead_letters() == []


def test_critical_alert_exhausts_retry_and_writes_dead_letter(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()),
        _mock_future(exc=TimeoutError()),
    ]
    result = svc.send_alert(
        AlertType.NAKED_POSITION, "Naked Position", "untracked short",
        priority=AlertPriority.CRITICAL,
    )
    assert result is False
    assert svc._publisher.publish.call_count == 2

    records = _read_dead_letters()
    assert len(records) == 1
    rec = records[0]
    assert rec["bot_name"] == "HYDRA_B"
    assert rec["priority"] == "critical"
    assert rec["alert_type"] == "naked_position"
    assert rec["error"] != ""
    assert "TimeoutError" in rec["error"]
    assert rec["payload"]["title"] == "Naked Position"


def test_medium_alert_does_not_retry(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [_mock_future(exc=TimeoutError())]
    result = svc.send_alert(
        AlertType.POSITION_OPENED, "Position Opened", "entry #1",
        priority=AlertPriority.MEDIUM,
    )
    assert result is False
    assert svc._publisher.publish.call_count == 1
    # Dead-letter still recorded even though no retry was attempted — the
    # dead-letter record is priority-independent, only the retry count isn't.
    assert len(_read_dead_letters()) == 1


def test_low_alert_does_not_retry_but_still_dead_letters(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [_mock_future(exc=TimeoutError())]
    result = svc.send_alert(
        AlertType.BOT_STOPPED, "Bot Stopped", "shutdown",
        priority=AlertPriority.LOW,
    )
    assert result is False
    assert svc._publisher.publish.call_count == 1
    assert len(_read_dead_letters()) == 1


def test_successful_publish_never_touches_dead_letter(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [_mock_future(result="mid-1")]
    result = svc.send_alert(
        AlertType.POSITION_CLOSED, "Closed", "flat",
        priority=AlertPriority.MEDIUM,
    )
    assert result is True
    assert _read_dead_letters() == []


# ─── Lazy re-init ──────────────────────────────────────────────────────────

def test_uninitialized_publisher_does_not_reinit_before_cooldown(monkeypatch):
    monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
    monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: True)
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    assert svc._initialized is False  # local test env — _initialize() no-ops

    reinit_calls = []
    monkeypatch.setattr(svc, "_initialize", lambda: reinit_calls.append(1))
    svc._last_init_attempt = alert_service_module.time.monotonic()  # "just tried"

    result = svc.send_alert(
        AlertType.BOT_STARTED, "Started", "up", priority=AlertPriority.LOW,
    )
    assert result is False  # still not initialized -> local-log-only
    assert reinit_calls == []  # cooldown not elapsed, no reinit attempt


def test_uninitialized_publisher_reinits_after_cooldown_and_publishes(monkeypatch):
    monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
    monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: True)
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    svc._last_init_attempt = 0.0  # far in the past -> cooldown elapsed

    def _fake_reinit():
        svc._initialized = True
        svc._publisher = MagicMock()
        svc._publisher.publish.return_value = _mock_future(result="mid-reinit")
        svc._topic_path = "projects/test/topics/calypso-alerts"

    monkeypatch.setattr(svc, "_initialize", _fake_reinit)

    result = svc.send_alert(
        AlertType.BOT_STARTED, "Started", "up", priority=AlertPriority.LOW,
    )
    assert result is True
    assert svc._initialized is True


def test_dry_run_never_attempts_reinit(monkeypatch):
    """Dry-run mode must stay a pure no-op path — it returns True before the
    reinit/publish code is ever reached."""
    monkeypatch.setenv("ALERT_DRY_RUN", "true")
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    reinit_calls = []
    monkeypatch.setattr(svc, "_initialize", lambda: reinit_calls.append(1))

    result = svc.send_alert(
        AlertType.BOT_STARTED, "Started", "up", priority=AlertPriority.LOW,
    )
    assert result is True
    assert reinit_calls == []


# ─── Dead-letter write failure must not crash or mask the original error ──

def test_dead_letter_write_failure_does_not_crash_send_alert(monkeypatch, caplog):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [_mock_future(exc=RuntimeError("boom"))]

    def _raise_makedirs(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(alert_service_module.os, "makedirs", _raise_makedirs)

    import logging
    with caplog.at_level(logging.WARNING, logger="shared.alert_service"):
        result = svc.send_alert(
            AlertType.EMERGENCY_EXIT, "Emergency Exit", "flattened",
            priority=AlertPriority.CRITICAL,
        )
    # The original publish failure is still reported — not masked by the
    # dead-letter write's own failure.
    assert result is False
    assert any("Failed to publish alert to Pub/Sub" in r.message for r in caplog.records)
    assert any("dead-letter" in r.message.lower() for r in caplog.records)


# ─── Regression: existing dry-run alert tests are unaffected ─────────────

def test_dry_run_path_still_returns_true_immediately(monkeypatch):
    monkeypatch.setenv("ALERT_DRY_RUN", "true")
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_C")
    assert svc.send_alert(
        AlertType.POSITION_OPENED, "Opened", "entry", priority=AlertPriority.MEDIUM
    ) is True
    assert _read_dead_letters() == []


# ─── Round-1 review findings: lock separation + concurrent dead-letter I/O ─
# Round 1 found the lazy-reinit block originally reused _gate_lock (shared
# with _apply_alert_gate, acquired on EVERY send_alert call) while holding it
# across _initialize() — an unbounded call (GCP metadata-server discovery has
# no timeout). A stuck reinit on one thread would then block every OTHER
# thread's send_alert, including an unrelated CRITICAL alert from the
# Telegram poller thread on variant A. Fixed with a dedicated _reinit_lock,
# acquired NON-BLOCKING so no thread ever waits on another's possibly-hung
# _initialize() call.

def test_reinit_lock_is_not_the_gate_lock():
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    assert svc._reinit_lock is not svc._gate_lock


def test_stuck_reinit_does_not_block_a_concurrent_thread_on_the_same_instance(monkeypatch):
    """The scenario round 1 flagged: two threads share ONE AlertService
    instance (main loop + Telegram poller, per the code's own comment).
    Thread A is mid-reinit inside a hung _initialize(); thread B must NOT
    block waiting for thread A — it should fall straight through to the
    not-initialized/local-log-only path instead."""
    import threading

    monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
    monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: True)
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    assert svc._initialized is False

    thread_a_entered_initialize = threading.Event()
    release_thread_a = threading.Event()

    def _hanging_initialize():
        thread_a_entered_initialize.set()
        release_thread_a.wait(timeout=5)  # simulates a stuck GCP call

    monkeypatch.setattr(svc, "_initialize", _hanging_initialize)

    thread_a = threading.Thread(
        target=lambda: svc.send_alert(
            AlertType.BOT_STARTED, "Started", "up", priority=AlertPriority.LOW
        )
    )
    thread_a.start()
    assert thread_a_entered_initialize.wait(timeout=2), "thread A never entered _initialize()"

    # Thread B (this thread) must return promptly — not block on thread A's
    # hung _initialize(). Bound the wait tightly: the old (buggy) blocking
    # acquire would hang here for the full 5s of thread A's fake hang.
    start = time.monotonic()
    result_b = svc.send_alert(
        AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
        priority=AlertPriority.CRITICAL,
    )
    elapsed = time.monotonic() - start

    release_thread_a.set()
    thread_a.join(timeout=5)

    assert result_b is False  # still not initialized -> local-log-only
    assert elapsed < 1.0, f"thread B blocked for {elapsed:.2f}s on thread A's hung reinit"


# ─── Round-1 review finding: concurrent dead-letter writers ───────────────
# FAILED_ALERTS_PATH is shared across every process that constructs an
# AlertService (variants A-E and calypso-broker), and a real Pub/Sub outage
# tends to hit them all near-simultaneously — making concurrent writers more
# likely than usual. _write_dead_letter now flock()s the append; this test
# proves the file stays parseable under genuine concurrent writes (real
# threads, not mocked), not just that flock() was called.

def test_concurrent_dead_letter_writes_produce_valid_jsonl(monkeypatch):
    import threading

    svc = _live_svc(monkeypatch)
    n_threads = 12

    def _write_one(i):
        svc._write_dead_letter(
            {"alert_type": "test", "title": f"concurrent-{i}", "big": "x" * 500},
            AlertPriority.HIGH,
            f"error-{i}",
        )

    threads = [threading.Thread(target=_write_one, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    records = _read_dead_letters()  # raises/fails the test if any line is malformed JSON
    assert len(records) == n_threads
    titles = {r["title"] for r in records}
    assert titles == {f"concurrent-{i}" for i in range(n_threads)}
