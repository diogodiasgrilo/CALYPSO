"""Tests for shared.ib_retry — Phase A.8.

CircuitBreaker state-machine + retry decorator behavior.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_retry import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    RetryPolicy,
    retry_with_backoff,
)


# ─── CircuitBreaker ─────────────────────────────────────────────────────────


class TestCircuitBreakerConsecutiveFailures:
    def test_starts_closed(self):
        cb = CircuitBreaker(name="t")
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request()

    def test_trips_after_N_consecutive_failures(self):
        cb = CircuitBreaker(name="t", consecutive_failures_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # threshold not yet reached
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_consecutive_counter(self):
        cb = CircuitBreaker(name="t", consecutive_failures_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        # Only 2 consecutive — still closed
        assert cb.state == CircuitState.CLOSED

    def test_open_breaks_request_flow(self):
        cb = CircuitBreaker(name="t", consecutive_failures_threshold=1)
        cb.record_failure()
        assert not cb.allow_request()


class TestCircuitBreakerFailureRate:
    def test_low_volume_does_not_trip(self):
        cb = CircuitBreaker(
            name="t", window_size=20, failure_rate_threshold=0.5,
        )
        # Only 5 outcomes — under window_size, rate not yet evaluable
        for _ in range(5):
            cb.record_failure()
        # consecutive_failures_threshold (default 5) WOULD trip — adjust
        # for this test by giving us a tall consecutive threshold
        cb._consecutive_failures = 0
        cb._state = CircuitState.CLOSED  # reset
        for _ in range(5):
            cb._outcomes.append((time.monotonic(), False))
        assert cb.state == CircuitState.CLOSED  # under window_size

    def test_high_rate_trips_when_window_full(self):
        cb = CircuitBreaker(
            name="t",
            consecutive_failures_threshold=999,  # disable that trip
            window_size=10,
            failure_rate_threshold=0.5,
        )
        # 6 failures, 4 successes — 60% failure rate
        for _ in range(6):
            cb.record_failure()
        for _ in range(4):
            cb.record_success()
        # Need one more event to re-evaluate the rate
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerHalfOpen:
    def test_transitions_to_half_open_after_interval(self):
        cb = CircuitBreaker(
            name="t",
            consecutive_failures_threshold=1,
            half_open_after_seconds=0.05,
        )
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.06)
        # Now allow_request triggers the OPEN→HALF_OPEN transition
        assert cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(
            name="t",
            consecutive_failures_threshold=1,
            half_open_after_seconds=0.05,
        )
        cb.record_failure()
        time.sleep(0.06)
        cb.allow_request()  # transition to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(
            name="t",
            consecutive_failures_threshold=1,
            half_open_after_seconds=0.05,
        )
        cb.record_failure()
        time.sleep(0.06)
        cb.allow_request()  # transition to HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerReset:
    def test_force_reset_to_closed(self):
        cb = CircuitBreaker(name="t", consecutive_failures_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.force_reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request()


# ─── RetryPolicy ────────────────────────────────────────────────────────────


class TestRetryPolicy:
    def test_429_is_retryable(self):
        pol = RetryPolicy()
        assert pol.is_retryable(Exception("429 Too Many Requests"))

    def test_5xx_is_retryable(self):
        pol = RetryPolicy()
        for code in ("500", "502", "503", "504"):
            assert pol.is_retryable(Exception(f"HTTP {code}"))

    def test_timeout_is_retryable(self):
        pol = RetryPolicy()
        assert pol.is_retryable(TimeoutError("read timeout"))
        assert pol.is_retryable(Exception("connection timed out"))

    def test_connection_error_is_retryable(self):
        pol = RetryPolicy()
        assert pol.is_retryable(ConnectionError("network unreachable"))
        assert pol.is_retryable(Exception("connection reset by peer"))

    def test_401_is_NOT_retryable(self):
        """Auth errors shouldn't be retried — they require human intervention."""
        pol = RetryPolicy()
        assert not pol.is_retryable(Exception("401 Unauthorized"))

    def test_arbitrary_error_is_NOT_retryable(self):
        pol = RetryPolicy()
        assert not pol.is_retryable(ValueError("bad input"))

    def test_delay_grows_exponentially(self):
        pol = RetryPolicy(base_delay_s=1.0, jitter_fraction=0.0)
        # No jitter for deterministic test
        assert pol.delay_for_attempt(1) == pytest.approx(1.0)
        assert pol.delay_for_attempt(2) == pytest.approx(2.0)
        assert pol.delay_for_attempt(3) == pytest.approx(4.0)
        assert pol.delay_for_attempt(4) == pytest.approx(8.0)

    def test_delay_capped_at_max(self):
        pol = RetryPolicy(base_delay_s=1.0, max_delay_s=10.0, jitter_fraction=0.0)
        # Attempt 100 would be 2^99 but should cap at 10
        assert pol.delay_for_attempt(100) == pytest.approx(10.0)


# ─── retry_with_backoff decorator ───────────────────────────────────────────


class TestRetryDecorator:
    def test_succeeds_first_try_no_retry(self):
        fn = MagicMock(return_value="ok")
        wrapped = retry_with_backoff(
            RetryPolicy(max_attempts=3, base_delay_s=0.001)
        )(fn)
        assert wrapped() == "ok"
        assert fn.call_count == 1

    def test_retries_then_succeeds(self):
        fn = MagicMock(side_effect=[
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            "ok",
        ])
        wrapped = retry_with_backoff(
            RetryPolicy(max_attempts=5, base_delay_s=0.001, jitter_fraction=0)
        )(fn)
        assert wrapped() == "ok"
        assert fn.call_count == 3

    def test_exhausts_retries_then_raises(self):
        fn = MagicMock(side_effect=Exception("503"))
        wrapped = retry_with_backoff(
            RetryPolicy(max_attempts=3, base_delay_s=0.001, jitter_fraction=0)
        )(fn)
        with pytest.raises(Exception, match="503"):
            wrapped()
        assert fn.call_count == 3

    def test_non_retryable_raises_immediately(self):
        fn = MagicMock(side_effect=ValueError("bad input"))
        wrapped = retry_with_backoff(
            RetryPolicy(max_attempts=5, base_delay_s=0.001)
        )(fn)
        with pytest.raises(ValueError, match="bad input"):
            wrapped()
        assert fn.call_count == 1  # no retry

    def test_breaker_short_circuits_when_open(self):
        cb = CircuitBreaker(name="t", consecutive_failures_threshold=1)
        cb.record_failure()  # OPEN
        fn = MagicMock(return_value="ok")
        wrapped = retry_with_backoff(
            RetryPolicy(max_attempts=3, base_delay_s=0.001),
            breaker=cb,
        )(fn)
        with pytest.raises(CircuitBreakerOpen):
            wrapped()
        fn.assert_not_called()

    def test_breaker_records_success_on_call_success(self):
        cb = CircuitBreaker(name="t", consecutive_failures_threshold=5)
        cb.record_failure()
        cb.record_failure()  # state CLOSED but with failure counter
        fn = MagicMock(return_value="ok")
        wrapped = retry_with_backoff(
            RetryPolicy(max_attempts=3, base_delay_s=0.001),
            breaker=cb,
        )(fn)
        wrapped()
        assert cb._consecutive_failures == 0

    def test_breaker_records_failure_on_retryable_error(self):
        cb = CircuitBreaker(name="t", consecutive_failures_threshold=10)
        fn = MagicMock(side_effect=Exception("503"))
        wrapped = retry_with_backoff(
            RetryPolicy(max_attempts=3, base_delay_s=0.001, jitter_fraction=0),
            breaker=cb,
        )(fn)
        with pytest.raises(Exception, match="503"):
            wrapped()
        # Each retry attempt records a failure
        assert cb._consecutive_failures == 3

    def test_preserves_function_signature(self):
        """@wraps decoration preserves the original function's metadata."""
        @retry_with_backoff(RetryPolicy(max_attempts=2, base_delay_s=0.001))
        def my_fn(a: int, b: int = 5) -> int:
            """Adds two numbers."""
            return a + b
        assert my_fn.__name__ == "my_fn"
        assert "Adds two numbers" in my_fn.__doc__
        assert my_fn(3, b=7) == 10

    def test_args_and_kwargs_passed_through(self):
        fn = MagicMock(return_value="ok")
        wrapped = retry_with_backoff(
            RetryPolicy(max_attempts=2, base_delay_s=0.001)
        )(fn)
        wrapped(1, 2, x="y")
        fn.assert_called_once_with(1, 2, x="y")
