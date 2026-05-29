"""IBKR-specific Telegram alert hooks (Polish Item 1).

Polled from `bots/hydra/main.py`'s monitoring loop once per iteration.
Watches three IBClient state signals:

  1. Circuit-breaker state transitions per family (CLOSED → OPEN, OPEN → CLOSED).
  2. Snapshot warmup exhaustion counter — for first-of-day + 25+/day alerts.
  3. ``ensure_connected()`` return value (passed in by main.py, not polled here).

Design constraints (per Polish Plan v2 amendments A1.1–A1.6):

* **No IBClient → AlertService dependency.** IBClient is broker-agnostic.
  This module accepts an `AlertService` instance + an `IBClient` instance
  and bridges them.
* **Idempotent state transitions.** Each transition fires at most ONE
  alert. A breaker stuck OPEN for 10 min fires once at minute 0, then
  one HIGH reminder every 15 min (capped at 4 reminders/day per family).
* **Day-boundary reset.** The `mark_new_day()` method resets per-day
  flags (first-warmup-exhaustion-of-day, 25+ alert, stuck-OPEN reminder
  count). main.py calls it at the same point it resets daily state.

Public API:

  hook = IBKRAlertHooks(broker, alert_service)
  ...
  # Once per iteration in main.py:
  hook.poll()
  # When main.py's ensure_connected() returns False:
  hook.on_ensure_connected_failed(reason="competing session")
  # Day boundary:
  hook.mark_new_day()

Tests: `tests/test_main_alert_hooks.py`. Required to maintain
zero-spurious-alert-on-stable-state contract (A1.5).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional


logger = logging.getLogger(__name__)

# Reminder cadence for stuck-OPEN breakers (A1.3).
STUCK_OPEN_REMINDER_INTERVAL_S = 15 * 60   # 15 min between reminders
STUCK_OPEN_REMINDERS_PER_DAY = 4            # cap reminders so we don't spam

# DATA_QUALITY 25+-exhaustions-today threshold (A1.2).
SNAPSHOT_EXHAUSTION_SEVERE_THRESHOLD = 25


class IBKRAlertHooks:
    """Per-iteration poll → Telegram alert bridge for IBKR-specific signals.

    Constructed once per main.py process. Caller invokes :meth:`poll`
    every iteration of the monitoring loop; :meth:`on_ensure_connected_failed`
    on the rare event of an auth-gate failure; :meth:`mark_new_day` at
    day-rollover (alongside the strategy's `_reset_for_new_day`).
    """

    def __init__(self, broker: Any, alert_service: Any) -> None:
        """
        Args:
            broker: an ``IBClient`` instance. We read its
                ``circuit_breakers`` property + ``snapshot_warmup_exhausted_count``.
            alert_service: a configured ``shared.alert_service.AlertService``
                (the same one HYDRA's strategy uses). May be a stub in dry-run.
        """
        self._broker = broker
        self._alerts = alert_service
        # Last-seen breaker state per family — used to detect transitions.
        # Initialized from the current state so the first poll() doesn't
        # spuriously alert on initial state.
        self._last_breaker_states: dict[str, Any] = {}
        # Stuck-OPEN tracking: when did this family go OPEN, when did we
        # last remind, how many reminders today.
        self._stuck_open_since: dict[str, float] = {}
        self._stuck_open_last_reminder: dict[str, float] = {}
        self._stuck_open_reminders_today: dict[str, int] = {}
        # Day-keyed alert flags (reset by mark_new_day).
        self._warmup_first_alert_sent_today = False
        self._warmup_severe_alert_sent_today = False
        # Snapshot of the warmup counter at the boundary of "today" — so
        # we count today's exhaustions vs the lifetime total.
        self._warmup_count_at_day_start = self._read_warmup_count()
        # Seed last-seen breaker states.
        self._seed_breaker_states()

    # ─── Public API ────────────────────────────────────────────────────────

    def poll(self) -> None:
        """Run one polling cycle. Called from main.py's monitoring loop.

        Cheap when nothing has changed (the common case): one dict lookup
        per family + one int comparison. The hot loop is not a concern.
        """
        try:
            self._poll_breakers()
            self._poll_snapshot_exhaustion()
        except Exception as e:
            # Defensive: a bug in this module must NEVER take down main.py.
            logger.exception("IBKRAlertHooks.poll() error (non-fatal): %s", e)

    def on_ensure_connected_failed(self, reason: str = "") -> None:
        """Fire the highest-value alert in the polish pass (A1.3): main.py's
        intraday ensure_connected() returned False. The bot is about to
        ``break`` out of the monitoring loop for a systemd restart, but
        without this Telegram the operator only sees the bot vanish.

        Args:
            reason: short human-readable reason (e.g. ``"competing session"``,
                ``"LST handshake failed"``, ``"connect() exception: ..."``).
                Appended to the alert body if provided.
        """
        try:
            body = (
                "IBKR session lost mid-day — bot exiting for systemd "
                "restart — likely competing session or LST expiry."
            )
            if reason:
                body = f"{body}\n\nReason: {reason}"
            self._alerts.send_alert(
                alert_type=_alert_type("API_ERROR"),
                title="HYDRA — IBKR session lost",
                message=body,
                priority=_alert_priority("HIGH"),
            )
        except Exception as e:
            logger.exception("on_ensure_connected_failed alert failed: %s", e)

    def mark_new_day(self) -> None:
        """Reset day-keyed alert flags. Called by main.py at day-rollover.

        Note: the snapshot-warmup-exhausted counter on IBClient is NOT
        reset (it's monotonic for the process lifetime). We only reset
        our "first/severe alerted today" flags + capture the new
        baseline count.
        """
        self._warmup_first_alert_sent_today = False
        self._warmup_severe_alert_sent_today = False
        self._warmup_count_at_day_start = self._read_warmup_count()
        # Reset stuck-OPEN per-day reminder counts.
        self._stuck_open_reminders_today.clear()

    # ─── Internal: breaker state polling ──────────────────────────────────

    def _seed_breaker_states(self) -> None:
        """At construction, capture initial breaker state per family so
        the first poll() doesn't spuriously fire on initial state."""
        for family, breaker in self._broker.circuit_breakers.items():
            self._last_breaker_states[family] = breaker.state

    def _poll_breakers(self) -> None:
        breakers = self._broker.circuit_breakers
        for family, breaker in breakers.items():
            current = breaker.state
            previous = self._last_breaker_states.get(family)
            if current != previous:
                self._handle_breaker_transition(family, previous, current)
                self._last_breaker_states[family] = current
            # Outage reminder fires on a TIME cadence whenever the family is
            # in an active outage — independent of transitions. This covers a
            # breaker stuck OPEN *and* one flapping OPEN↔HALF_OPEN on failing
            # probes. The old code only reminded when current==previous AND
            # only fired the opened-alert on CLOSED→OPEN, so a re-trip after a
            # failed probe (HALF_OPEN→OPEN) and a flapping breaker went silent
            # after the first alert (audit M7 + M10).
            if family in self._stuck_open_since:
                self._maybe_fire_stuck_open_reminder(family)

    def _handle_breaker_transition(self, family: str, prev: Any, curr: Any) -> None:
        """Translate a breaker state change into an alert.

        Outage is tracked by membership in ``_stuck_open_since`` rather than
        by an exact edge: entering OPEN *or* HALF_OPEN (the latter catches an
        OPEN missed between polls, e.g. CLOSED→HALF_OPEN) starts the outage
        once; an OPEN↔HALF_OPEN flap inside an outage does NOT re-fire the
        opened-alert (the cadence reminder reports "still degraded"); and the
        first return to CLOSED ends it (audit M7 + M10).
        """
        in_outage = family in self._stuck_open_since
        if self._is_degraded(curr) and not in_outage:
            now = time.monotonic()
            self._stuck_open_since[family] = now
            self._stuck_open_last_reminder[family] = now
            self._stuck_open_reminders_today[family] = 0
            self._fire_breaker_opened(family)
        elif self._is_closed(curr) and in_outage:
            # Recovered to CLOSED (covers OPEN→CLOSED and HALF_OPEN→CLOSED).
            self._stuck_open_since.pop(family, None)
            self._stuck_open_last_reminder.pop(family, None)
            self._fire_breaker_recovered(family)
        # else: OPEN↔HALF_OPEN flap inside an outage, or CLOSED→CLOSED — no
        # per-transition alert; the cadence reminder covers "still degraded".

    def _maybe_fire_stuck_open_reminder(self, family: str) -> None:
        """If this family has been OPEN long enough and we haven't sent
        too many reminders today, send one (A1.3)."""
        since = self._stuck_open_since.get(family)
        if since is None:
            return
        now = time.monotonic()
        last = self._stuck_open_last_reminder.get(family, since)
        if now - last < STUCK_OPEN_REMINDER_INTERVAL_S:
            return
        count = self._stuck_open_reminders_today.get(family, 0)
        if count >= STUCK_OPEN_REMINDERS_PER_DAY:
            return
        # Fire reminder.
        try:
            elapsed_min = int((now - since) / 60)
            self._alerts.send_alert(
                alert_type=_alert_type("API_ERROR"),
                title=f"HYDRA — {family} breaker still degraded ({elapsed_min}m)",
                message=(
                    f"The IBKR {family} circuit breaker has been OPEN (or "
                    f"flapping OPEN↔HALF_OPEN on failing probes) for "
                    f"~{elapsed_min} minutes. This is reminder "
                    f"{count + 1}/{STUCK_OPEN_REMINDERS_PER_DAY} today. "
                    f"See RUNBOOKS.md RB-2."
                ),
                priority=_alert_priority("HIGH"),
            )
        except Exception as e:
            logger.exception("stuck-OPEN reminder alert failed: %s", e)
        self._stuck_open_last_reminder[family] = now
        self._stuck_open_reminders_today[family] = count + 1

    def _fire_breaker_opened(self, family: str) -> None:
        """Fire on CLOSED → OPEN transition. Orders family = CRITICAL,
        market/portfolio = HIGH, session/oauth = HIGH."""
        # `orders` is the highest-priority — we can't place trades.
        if family == "orders":
            priority = _alert_priority("CRITICAL")
            title = "HYDRA — Cannot place orders — orders breaker tripped"
            message = (
                "The IBKR `orders` circuit breaker is OPEN. HYDRA cannot "
                "place orders or cancel them until it recovers. Manual "
                "intervention may be required — see RUNBOOKS.md RB-2."
            )
            alert_type = _alert_type("CIRCUIT_BREAKER")
        else:
            priority = _alert_priority("HIGH")
            title = f"HYDRA — Broker degraded ({family} breaker OPEN)"
            message = (
                f"The IBKR `{family}` circuit breaker is OPEN. Data "
                f"flow may be stale. See RUNBOOKS.md RB-2 for "
                f"triage steps."
            )
            alert_type = _alert_type("API_ERROR")
        try:
            self._alerts.send_alert(
                alert_type=alert_type,
                title=title,
                message=message,
                priority=priority,
            )
        except Exception as e:
            logger.exception("breaker-opened alert failed for %s: %s", family, e)

    def _fire_breaker_recovered(self, family: str) -> None:
        """Fire on OPEN → CLOSED (or HALF_OPEN → CLOSED) — LOW priority."""
        try:
            self._alerts.send_alert(
                alert_type=_alert_type("CONNECTION_RESTORED"),
                title=f"HYDRA — {family} breaker recovered",
                message=(
                    f"The IBKR `{family}` circuit breaker is CLOSED "
                    f"again. Normal operation resumed."
                ),
                priority=_alert_priority("LOW"),
            )
        except Exception as e:
            logger.exception("breaker-recovered alert failed for %s: %s", family, e)

    # ─── Internal: snapshot exhaustion polling ────────────────────────────

    def _poll_snapshot_exhaustion(self) -> None:
        """Day-level dedup for snapshot warmup exhaustion (A1.2)."""
        today_count = self._read_warmup_count() - self._warmup_count_at_day_start
        if today_count > 0 and not self._warmup_first_alert_sent_today:
            try:
                self._alerts.send_alert(
                    alert_type=_alert_type("DATA_QUALITY"),
                    title="HYDRA — First snapshot warmup exhaustion of the day",
                    message=(
                        "IBKR's snapshot endpoint returned metadata-only "
                        "for at least one conid today. Likely: a thin "
                        "0DTE chain leg or transient IBKR degradation. "
                        "Watch for the 25+/day follow-up alert. See "
                        "RUNBOOKS.md RB-3."
                    ),
                    priority=_alert_priority("MEDIUM"),
                )
            except Exception as e:
                logger.exception("first-warmup-exhaustion alert failed: %s", e)
            self._warmup_first_alert_sent_today = True

        if (
            today_count >= SNAPSHOT_EXHAUSTION_SEVERE_THRESHOLD
            and not self._warmup_severe_alert_sent_today
        ):
            try:
                self._alerts.send_alert(
                    alert_type=_alert_type("DATA_QUALITY"),
                    title=(
                        f"HYDRA — {SNAPSHOT_EXHAUSTION_SEVERE_THRESHOLD}+ "
                        f"snapshot exhaustions today"
                    ),
                    message=(
                        f"IBKR's snapshot endpoint has returned "
                        f"metadata-only {today_count} times today. Data "
                        f"flow severely degraded — strategy decisions "
                        f"may be running on stale or no data. Consider "
                        f"stopping the bot until IBKR recovers. See "
                        f"RUNBOOKS.md RB-3."
                    ),
                    priority=_alert_priority("HIGH"),
                )
            except Exception as e:
                logger.exception("severe-warmup-exhaustion alert failed: %s", e)
            self._warmup_severe_alert_sent_today = True

    def _read_warmup_count(self) -> int:
        """Safe accessor — falls back to 0 if the property is missing."""
        return int(getattr(self._broker, "snapshot_warmup_exhausted_count", 0))

    # ─── Internal: state-machine helpers ──────────────────────────────────

    @staticmethod
    def _is_open(state: Any) -> bool:
        return getattr(state, "value", str(state)).lower() == "open"

    @staticmethod
    def _is_degraded(state: Any) -> bool:
        """OPEN or HALF_OPEN — the family is not fully healthy. Used to
        detect outage entry by state rather than by an exact edge, so an
        OPEN missed between polls (CLOSED→HALF_OPEN) still registers."""
        return getattr(state, "value", str(state)).lower() in ("open", "half_open")

    @staticmethod
    def _is_closed(state: Any) -> bool:
        return getattr(state, "value", str(state)).lower() == "closed"


# ─── Lazy AlertType / AlertPriority lookups ────────────────────────────────

def _alert_type(name: str) -> Any:
    """Look up AlertType.<NAME> lazily so this module can be imported
    even if shared.alert_service is unavailable (e.g. unit tests with a
    stub AlertService)."""
    try:
        from shared.alert_service import AlertType
        return getattr(AlertType, name)
    except Exception:
        return name


def _alert_priority(name: str) -> Any:
    try:
        from shared.alert_service import AlertPriority
        return getattr(AlertPriority, name)
    except Exception:
        return name
