"""Retry + per-endpoint-family circuit breakers for IBClient — Phase A.8.

Per research_scratch/12_ibind_errors_lifecycle.md: ibind 0.1.23 retries
network errors only (3× linear backoff). It does NOT retry on 429/5xx —
that's our responsibility.

Design:

  • CircuitBreaker class: classic 3-state (CLOSED / OPEN / HALF_OPEN)
    breaker keyed by an "endpoint family" string ('oauth', 'market',
    'orders', 'portfolio', 'session'). Opens after N consecutive
    failures OR ≥X% failure rate over a sliding window. Half-open
    probe interval is configurable.

  • retry_with_backoff decorator: exponential backoff + jitter on a
    configurable set of retryable exception types. Optional integration
    with a CircuitBreaker — if breaker is OPEN, retry call short-circuits.

  • RetryPolicy preset: sensible defaults for our use case (5 retries,
    1s base, 30s max, jitter 0.5, retry 429/500/502/503/504).

  • Never retry order placement without a client_order_id (cOID) — that's
    a separate concern handled in the caller (IBClient.place_order
    enforces this). This module just provides the primitives.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Callable, Optional, Type


logger = logging.getLogger(__name__)


# ─── Circuit breaker ────────────────────────────────────────────────────────


class CircuitState(str, Enum):
    CLOSED = "closed"      # normal — requests pass through
    OPEN = "open"          # tripped — requests short-circuit
    HALF_OPEN = "half_open"  # probing — one request allowed through


@dataclass
class CircuitBreaker:
    """Per-endpoint-family circuit breaker.

    Default thresholds match research_scratch/12 guidance:
      • 5 consecutive failures OR
      • ≥50% failure rate over a 20-request / 60-second window

    OPEN state lasts at least half_open_after_seconds. After that, the
    NEXT request is allowed through as a "probe" (HALF_OPEN state).
    If the probe succeeds → CLOSED. If it fails → back to OPEN.

    Thread-safe via internal lock.
    """
    name: str = "default"
    consecutive_failures_threshold: int = 5
    failure_rate_threshold: float = 0.5
    window_size: int = 20
    window_seconds: float = 60.0
    half_open_after_seconds: float = 30.0

    # Mutable state — only touched under _lock
    _state: CircuitState = CircuitState.CLOSED
    _consecutive_failures: int = 0
    _opened_at: Optional[float] = None
    _outcomes: deque = field(default_factory=lambda: deque(maxlen=20))
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self):
        # Re-create deque with the configured window size
        self._outcomes = deque(maxlen=self.window_size)

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def allow_request(self) -> bool:
        """Returns True if a request may proceed; False if the breaker is OPEN."""
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state != CircuitState.OPEN

    def record_success(self) -> None:
        with self._lock:
            self._outcomes.append((time.monotonic(), True))
            self._consecutive_failures = 0
            if self._state == CircuitState.HALF_OPEN:
                logger.info("CircuitBreaker[%s] HALF_OPEN → CLOSED (probe success)", self.name)
                self._state = CircuitState.CLOSED
                self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._outcomes.append((time.monotonic(), False))
            self._consecutive_failures += 1
            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — back to OPEN with fresh timer
                logger.warning(
                    "CircuitBreaker[%s] HALF_OPEN → OPEN (probe failure)", self.name,
                )
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                return
            # Check trip conditions
            if self._consecutive_failures >= self.consecutive_failures_threshold:
                self._trip("consecutive failures threshold")
                return
            if self._failure_rate_exceeded():
                self._trip(
                    f"failure rate ≥{self.failure_rate_threshold:.0%} "
                    f"over {self.window_seconds}s window"
                )

    def _trip(self, reason: str) -> None:
        if self._state != CircuitState.OPEN:
            logger.warning(
                "CircuitBreaker[%s] %s → OPEN — %s",
                self.name, self._state.value, reason,
            )
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()

    def _maybe_transition_to_half_open(self) -> None:
        if self._state != CircuitState.OPEN:
            return
        if self._opened_at is None:
            return
        if time.monotonic() - self._opened_at >= self.half_open_after_seconds:
            logger.info(
                "CircuitBreaker[%s] OPEN → HALF_OPEN (probe interval elapsed)",
                self.name,
            )
            self._state = CircuitState.HALF_OPEN

    def _failure_rate_exceeded(self) -> bool:
        """True if the recent-window failure rate ≥ threshold."""
        cutoff = time.monotonic() - self.window_seconds
        recent = [outcome for ts, outcome in self._outcomes if ts >= cutoff]
        if len(recent) < self.window_size:
            return False  # need a full window
        failures = sum(1 for ok in recent if not ok)
        rate = failures / len(recent)
        return rate >= self.failure_rate_threshold

    def force_reset(self) -> None:
        """Manually reset to CLOSED. Used by tests + manual operator action."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._outcomes.clear()


# ─── Retry decorator ────────────────────────────────────────────────────────


@dataclass
class RetryPolicy:
    """Configurable retry policy for IBClient calls.

    Default: 5 retries, exponential backoff base 1s, max 30s, jitter 0.5x.
    Retryable exceptions: anything with HTTP-style 429/5xx semantics. Caller
    can extend the predicate.

    Total worst-case time: ~63 seconds (1+2+4+8+16+30+30+30 with cap+jitter
    in degenerate case, but typical retry chain terminates within ~30s).
    """
    max_attempts: int = 6  # initial call + 5 retries
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter_fraction: float = 0.5
    # When integrated with a CircuitBreaker: short-circuit if breaker is OPEN
    breaker: Optional[CircuitBreaker] = None

    def is_retryable(self, exc: Exception) -> bool:
        """Override-able predicate. Default: retry HTTP 429/5xx + transient
        network errors.
        """
        msg = str(exc).lower()
        if any(t in msg for t in (
            "429", "rate limit",
            "500", "502", "503", "504",
            "timeout", "timed out",
            "connection reset", "connection refused", "connection aborted",
            "broken pipe", "remote end closed",
        )):
            return True
        # ConnectionError / TimeoutError subclasses
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True
        return False

    def delay_for_attempt(self, attempt: int) -> float:
        """Exponential backoff with jitter.

        attempt = 1 → base_delay
        attempt = 2 → base_delay × 2
        ...
        Then jitter: result × (1 ± jitter_fraction)
        Capped at max_delay_s.
        """
        raw = self.base_delay_s * (2 ** (attempt - 1))
        raw = min(raw, self.max_delay_s)
        jitter = raw * self.jitter_fraction * (random.random() * 2 - 1)
        return max(0.0, raw + jitter)


def retry_with_backoff(
    policy: Optional[RetryPolicy] = None,
    breaker: Optional[CircuitBreaker] = None,
) -> Callable:
    """Decorator factory: applies retry + circuit-breaker logic to a callable.

    Usage:
        @retry_with_backoff(policy=my_policy, breaker=market_breaker)
        def get_quote(...): ...

    Or, as a direct call wrapper:
        result = retry_with_backoff(policy)(fn)(*args, **kwargs)

    Args:
        policy: RetryPolicy instance (uses defaults if None)
        breaker: CircuitBreaker instance; if OPEN at call time, the wrapped
                 function raises CircuitBreakerOpen WITHOUT calling.
    """
    pol = policy or RetryPolicy()
    if breaker is not None:
        pol = RetryPolicy(
            max_attempts=pol.max_attempts,
            base_delay_s=pol.base_delay_s,
            max_delay_s=pol.max_delay_s,
            jitter_fraction=pol.jitter_fraction,
            breaker=breaker,
        )

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            br = pol.breaker
            last_exc: Optional[Exception] = None
            for attempt in range(1, pol.max_attempts + 1):
                if br is not None and not br.allow_request():
                    raise CircuitBreakerOpen(
                        f"Circuit breaker '{br.name}' is OPEN — refusing call"
                    )
                try:
                    result = fn(*args, **kwargs)
                    if br is not None:
                        br.record_success()
                    return result
                except Exception as exc:
                    last_exc = exc
                    is_retryable = pol.is_retryable(exc)
                    if br is not None and is_retryable:
                        br.record_failure()
                    if not is_retryable:
                        raise
                    name = getattr(fn, "__name__", repr(fn))
                    if attempt >= pol.max_attempts:
                        logger.error(
                            "%s exhausted %d retries; last error: %s",
                            name, pol.max_attempts, exc,
                        )
                        raise
                    delay = pol.delay_for_attempt(attempt)
                    logger.warning(
                        "%s attempt %d/%d failed (%s); retrying in %.2fs",
                        name, attempt, pol.max_attempts, exc, delay,
                    )
                    time.sleep(delay)
            # Shouldn't reach here; guard for type checker
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


class CircuitBreakerOpen(Exception):
    """Raised when a wrapped call is short-circuited by an OPEN breaker."""
