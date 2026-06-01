"""Retry + per-endpoint-family circuit breakers for IBClient — Phase A.8.

Per research_scratch/12_ibind_errors_lifecycle.md: ibind 0.1.23 retries
network errors only (3× linear backoff). It does NOT retry on 429/5xx —
that's our responsibility.

Design:

  • CircuitBreaker class: classic 3-state (CLOSED / OPEN / HALF_OPEN)
    breaker keyed by an "endpoint family" string ('oauth', 'market',
    'orders', 'portfolio', 'session'). Opens after N consecutive
    failures OR ≥X% failure rate over a sliding window. Half-open
    probe interval is configurable. HALF_OPEN admits exactly ONE
    in-flight probe (single-probe gate); concurrent callers short-
    circuit until the probe's outcome is recorded.

  • retry_with_backoff decorator: exponential backoff + jitter on a
    configurable set of retryable exception types. Optional integration
    with a CircuitBreaker — if breaker is OPEN (or HALF_OPEN with a
    probe already in flight), the retry call short-circuits.

  • RetryPolicy preset: sensible defaults for our use case (5 retries,
    1s base, 30s max, jitter 0.5, retry 429/500/502/503/504).

  • Never retry order placement without a client_order_id (cOID) — that's
    a separate concern handled in the caller (IBClient.place_order
    enforces this). This module just provides the primitives.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import wraps
from typing import Callable, Optional

# HTTP status codes that signal a transient, retryable server condition.
#
# Retryability is decided primarily from the STRUCTURED status code carried
# by ibind's ExternalBrokerError (``exc.status_code``); see
# RetryPolicy.is_retryable. This regex is the *text fallback* for exceptions
# that don't carry a structured code, and it deliberately matches a code only
# where it appears as a real HTTP status token — NOT as a bare number anywhere
# in the message. The previous ``\b(?:429|...)\b`` form matched a digit run
# that happened to equal a code even when it was an embedded price/size/
# notional (e.g. "limit price 500.00", "quantity 503", "$1,500.00"), which
# could misclassify a permanent order reject as retryable and trip the orders
# breaker. We now match only:
#   • ibind's framing status slot ``:: <code> ::`` (the authoritative slot —
#     a body number after it is ignored), or
#   • an http/status[_code] keyword immediately preceding the code, or
#   • a leading status line ``^<code> <reason-phrase>``.
_HTTP_RETRYABLE_CODE_RE = re.compile(
    # 5xx only — 429 is intentionally excluded (IBKR-audit #9): a 429 must NOT
    # be retried (it triggers a ~10-min IP penalty box; retrying escalates
    # toward a permanent block). _ib_call handles 429 via a fail-fast cooldown.
    r"(?:"
    r"::\s*(?:500|502|503|504)\s*::"
    r"|(?:http|status(?:[_ ]?code)?)[\s:=\"']*\(?(?:500|502|503|504)\b"
    r"|^\s*(?:500|502|503|504)\s+[a-z]"
    r")"
)


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
    breaker moves to HALF_OPEN and allows exactly ONE request through as a
    "probe"; while that single probe is in flight, every other concurrent
    caller short-circuits (allow_request() → False) just as in OPEN. If the
    probe succeeds → CLOSED. If it fails → back to OPEN. This single-probe
    gate matters because one IBClient (and one breaker per family) is shared
    across strategies + the alert thread, and some read paths fan out many
    concurrent calls; without the gate an OPEN→HALF_OPEN transition would let
    all of them stampede the still-degraded broker at once and a single lucky
    success could prematurely re-CLOSE the breaker.

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
    # Single-probe gate: True once allow_request() has admitted the lone
    # HALF_OPEN probe and until its outcome is recorded. Concurrent callers
    # short-circuit while it is set.
    _probe_in_flight: bool = False
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
        """Returns True if a request may proceed; False if it must short-circuit.

        CLOSED  → always True.
        OPEN    → always False.
        HALF_OPEN → True for exactly ONE caller (the probe), which atomically
                    claims the in-flight slot here; every other concurrent
                    caller gets False until the probe's outcome is recorded
                    via record_success()/record_failure(). This is the
                    single-probe gate — it stops a concurrent stampede onto a
                    still-degraded broker on OPEN→HALF_OPEN.
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == CircuitState.OPEN:
                return False
            if self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    return False
                # Claim the lone probe slot for this caller.
                self._probe_in_flight = True
                return True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._outcomes.append((time.monotonic(), True))
            self._consecutive_failures = 0
            if self._state == CircuitState.HALF_OPEN:
                logger.info("CircuitBreaker[%s] HALF_OPEN → CLOSED (probe success)", self.name)
                self._state = CircuitState.CLOSED
                self._opened_at = None
            # Release the single-probe slot (no-op outside HALF_OPEN).
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._outcomes.append((time.monotonic(), False))
            self._consecutive_failures += 1
            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — back to OPEN with fresh timer. Release the
                # probe slot so the NEXT half-open window can issue a probe.
                logger.warning(
                    "CircuitBreaker[%s] HALF_OPEN → OPEN (probe failure)", self.name,
                )
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._probe_in_flight = False
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

    def release_probe(self) -> None:
        """Release the single-probe slot WITHOUT recording an outcome.

        Used when a HALF_OPEN probe call ends in a way that should not count
        for or against the breaker (e.g. a non-retryable exception, which by
        design never records a breaker failure). Leaving the slot claimed
        would wedge the breaker in HALF_OPEN with every caller short-
        circuiting forever. The breaker stays HALF_OPEN so the next caller
        re-probes. No-op when no probe is in flight.
        """
        with self._lock:
            self._probe_in_flight = False

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
            # Fresh half-open window — no probe claimed yet. The next
            # allow_request() caller claims the lone probe slot.
            self._probe_in_flight = False

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
            self._probe_in_flight = False
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
        network errors — EXCEPT for IBKR's known 5xx-misuse patterns where
        the status code is 5xx but the response body indicates a permanent
        error (effectively a 4xx semantically).

        Discovered 2026-05-17 via paper smoke diagnostic: IBKR's
        `/iserver/account/order/status/{orderId}` returns
        `503 Service Unavailable` with body
        `{"error":"Order X is not found","statusCode":503}` for orders that
        don't exist in IBKR's database (purged after a terminal state +
        short retention). Retrying these wastes ~20s of backoff and trips
        the orders breaker, blocking subsequent legitimate calls. Same
        misuse-of-503 pattern shows up for "already filled or canceled"
        responses to a cancel on a terminated order. Both must propagate
        immediately as non-retryable.

        Retryability is decided primarily from the STRUCTURED status code
        carried by ibind's ExternalBrokerError (``exc.status_code``), which
        is the authoritative signal. Only when no structured code is present
        do we fall back to matching the stringified message via
        `_HTTP_RETRYABLE_CODE_RE`, which matches a code only where it appears
        as a real HTTP status token (ibind's ``:: <code> ::`` slot, an
        http/status keyword prefix, or a leading status line) — NOT as a bare
        number embedded in a price/size/order-id/notional. The earlier
        bare-token form still matched a body number that happened to equal a
        code (e.g. "limit price 500.00", "quantity 503"), which could
        misclassify a permanent order reject as retryable and trip the orders
        breaker on the safety-critical path.
        """
        msg = str(exc).lower()

        # IBKR permanent-error patterns served via 5xx — short-circuit
        # BEFORE any status-code match so they don't get retried. This runs
        # ahead of the structured status_code check too: ibind tags the
        # "order is not found" 503 with status_code=503, but it is
        # semantically permanent and must NOT retry.
        for permanent_pattern in (
            "is not found",          # /order/status/{id} on purged order
            "no longer found",       # variant
            "already filled",        # cancel on filled order
            "already cancel",        # cancel on cancelled order ("canceled" too)
            "order is filled or canceled",  # exact ibind/IBKR phrasing
        ):
            if permanent_pattern in msg:
                return False

        # Authoritative signal: ibind's ExternalBrokerError carries the raw
        # HTTP status as a structured int (`status_code`). Prefer it over any
        # text heuristic — it cannot be confused by body numbers. Note ibind
        # also tags some non-HTTP failures (e.g. invalid-JSON) with
        # status_code=None, which simply falls through to the text/type
        # checks below.
        # IBKR-audit #9: 429 is deliberately NON-retryable. IBKR penalty-boxes
        # the IP for ~10 min on a 429 (repeat offenders permanently blocked), so
        # retrying is futile (still boxed) and escalates toward a permanent ban.
        # We let a 429 surface immediately; _ib_call catches it, enters a
        # fail-fast penalty-box cooldown, and alerts. Only true 5xx retry.
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code in (500, 502, 503, 504)

        # HTTP 5xx text fallback — matched only as a real status token (see
        # _HTTP_RETRYABLE_CODE_RE) so digit runs embedded in conids, strikes,
        # order-ids or notionals can't masquerade as a 5xx and cause retry
        # storms + false orders-breaker trips on the safety-critical order path.
        # NOTE: 429 / "rate limit" intentionally NOT matched here (see above).
        if _HTTP_RETRYABLE_CODE_RE.search(msg):
            return True
        if any(t in msg for t in (
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

    Breaker semantics — PINNED:
      • Only **retryable** exceptions (HTTP 429/5xx + transient network
        errors per `RetryPolicy.is_retryable`) record a breaker failure.
      • Non-retryable exceptions (auth, validation, programmer errors)
        propagate immediately and **do NOT** record a breaker failure —
        the breaker is for "broker is degraded", not "caller did
        something wrong". This means sustained 4xx errors will surface
        each call but won't trip the breaker; that is intentional.
    """
    pol = policy or RetryPolicy()
    if pol.max_attempts < 1:
        raise ValueError(
            f"retry_with_backoff: max_attempts must be >= 1, got {pol.max_attempts}"
        )
    if breaker is not None:
        # Only swap in the breaker — preserve the caller's RetryPolicy type
        # and any overridden is_retryable(). Rebuilding a base RetryPolicy
        # here silently dropped custom is_retryable predicates from a
        # subclass (audit M6); dataclasses.replace returns type(pol).
        pol = replace(pol, breaker=breaker)

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            br = pol.breaker
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
                    is_retryable = pol.is_retryable(exc)
                    if br is not None and is_retryable:
                        br.record_failure()
                    if not is_retryable:
                        # Non-retryable errors never record a breaker failure
                        # (caller-side fault, not broker degradation). But if
                        # this attempt was the lone HALF_OPEN probe, we must
                        # release the probe slot so the breaker doesn't wedge
                        # in HALF_OPEN with every future caller short-circuiting.
                        if br is not None:
                            br.release_probe()
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
            # Unreachable: the loop always exits via return or raise.
            raise RuntimeError(  # pragma: no cover
                "retry_with_backoff: invariant violated — loop exited without raise/return"
            )
        return wrapper
    return decorator


class CircuitBreakerOpen(Exception):
    """Raised when a wrapped call is short-circuited by an OPEN breaker."""
