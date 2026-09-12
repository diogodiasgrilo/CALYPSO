"""Regression tests for the 2026-05-28 audit medium-severity fixes:

  M5 _handle_tick bumped received_at on dataless messages, masking staleness
  M6 retry_with_backoff rebuilt RetryPolicy, dropping a custom is_retryable

(M8 telegram /config buffer, M1 connect-timeout doc, M9/M14 docstrings, and
the M-? unit-file ProtectDevices comment are doc/config-only or
integration-heavy — verified by inspection, not unit-tested here.)

See docs/migration/PREFLIGHT_AUDIT_FINDINGS.md.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_retry import CircuitBreaker, RetryPolicy, retry_with_backoff
from shared.ib_streaming import StreamingManager


class _NoopWs:
    connected = True


class TestTickStaleness:
    """M5: a market-data message with no field codes must NOT count as a
    fresh tick (otherwise is_stale/is_healthy keep trusting stale data)."""

    def test_dataless_message_records_no_tick(self):
        mgr = StreamingManager(_NoopWs())
        mgr._handle_tick({"conid": 111, "_updated": 123, "topic": "smd+111"})
        assert mgr.get_snapshot(111) is None

    def test_message_with_field_records_tick(self):
        mgr = StreamingManager(_NoopWs())
        mgr._handle_tick({"conid": 222, "31": "5500.0"})
        snap = mgr.get_snapshot(222)
        assert snap is not None
        assert snap.received_at is not None
        assert snap.fields.get("31") == "5500.0"

    def test_dataless_after_data_does_not_refresh_received_at(self):
        mgr = StreamingManager(_NoopWs())
        mgr._handle_tick({"conid": 333, "31": "5500.0"})
        first = mgr.get_snapshot(333).received_at
        time.sleep(0.01)
        # A subsequent dataless heartbeat must not reset the freshness clock.
        mgr._handle_tick({"conid": 333, "_updated": 999, "topic": "smd+333"})
        assert mgr.get_snapshot(333).received_at == first


class TestRetryPreservesCustomPredicate:
    """M6: passing a breaker must not drop a subclass's is_retryable()."""

    def test_custom_is_retryable_survives_breaker(self):
        class AlwaysRetry(RetryPolicy):
            def is_retryable(self, exc):  # normally ValueError is NOT retryable
                return True

        pol = AlwaysRetry(max_attempts=3, base_delay_s=0.0,
                          max_delay_s=0.0, jitter_fraction=0.0)
        breaker = CircuitBreaker(name="test")
        calls = {"n": 0}

        @retry_with_backoff(policy=pol, breaker=breaker)
        def fn():
            calls["n"] += 1
            raise ValueError("would not retry under the base policy")

        with pytest.raises(ValueError):
            fn()
        # The override made ValueError retryable → all 3 attempts ran.
        # Before the fix, the rebuilt base RetryPolicy returned False here
        # and the call ran exactly once.
        assert calls["n"] == 3

    def test_base_policy_still_does_not_retry_valueerror(self):
        """Control: a plain RetryPolicy + breaker must NOT retry ValueError."""
        pol = RetryPolicy(max_attempts=3, base_delay_s=0.0,
                          max_delay_s=0.0, jitter_fraction=0.0)
        breaker = CircuitBreaker(name="test2")
        calls = {"n": 0}

        @retry_with_backoff(policy=pol, breaker=breaker)
        def fn():
            calls["n"] += 1
            raise ValueError("non-retryable")

        with pytest.raises(ValueError):
            fn()
        assert calls["n"] == 1
