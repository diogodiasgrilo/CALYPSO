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

# Grace period after which a breaker that has settled into HALF_OPEN (and
# stayed there, never re-tripping to OPEN) is treated as recovered.
#
# Reading `breaker.state` is side-effecting: it drives OPEN→HALF_OPEN once
# `half_open_after_seconds` elapses, but HALF_OPEN→CLOSED only happens on a
# real successful probe (record_success). A recovered-but-idle family (e.g.
# `orders` outside the brief entry windows) therefore never sees a closing
# probe and would otherwise stay pinned in HALF_OPEN forever — firing false
# HIGH "still degraded" reminders every day and never emitting a recovery
# alert. After this many seconds of *continuous* HALF_OPEN with no re-trip to
# OPEN, we end the outage and send the recovery alert. A genuinely-unhealthy
# breaker keeps flipping back to OPEN on failing probes, which resets this
# timer, so it keeps reminding correctly.
HALF_OPEN_RECOVERY_GRACE_S = 5 * 60        # 5 min stable HALF_OPEN ⇒ recovered

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
        # When (monotonic) a family first read HALF_OPEN within the current
        # outage. Reset whenever it re-trips to OPEN. Used to treat a family
        # that has settled into a stable HALF_OPEN (no failing probes) as
        # recovered after HALF_OPEN_RECOVERY_GRACE_S, so reminders don't fire
        # forever on a recovered-but-idle family.
        self._half_open_since: dict[str, float] = {}
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

    def on_ensure_connected_failed(self, reason: str = "", *, will_restart: bool = True) -> None:
        """Fire the highest-value alert in the polish pass (A1.3): an intraday
        ``ensure_connected()`` returned False.

        Two callers, two framings:

        * **Strategy bot** (``will_restart=True``, default): main.py is about to
          ``break`` out of its loop for a systemd restart — without this
          Telegram the operator only sees the bot vanish.
        * **Shared broker** (``will_restart=False``): calypso-broker does NOT
          exit — it stays up and keeps retrying re-auth — so the wording and
          expectation differ. The broker only calls this after **≥2 consecutive
          failed re-auth cycles**; the routine ~01:00 ET IBKR reset clears in a
          single cycle and must NOT alert (that was a nightly false HIGH).

        Args:
            reason: short human-readable reason (e.g. ``"competing session"``,
                ``"LST handshake failed"``, ``"connect() exception: ..."``).
                Appended to the alert body if provided.
            will_restart: see above — selects bot-exit vs broker-stays-up wording.
        """
        try:
            if will_restart:
                # SELF-HEALING in the deployed (broker-mode) topology: the
                # strategy's ensure_connected() is only a GET /health probe; on
                # failure main.py break()s and systemd restarts it in 30s, then
                # it re-probes. The REAL session is owned by calypso-broker, which
                # recovers independently. So this is informational, NOT actionable
                # for the operator — demote to LOW (Telegram-only via routing) so
                # it never emails. A crash-loop is collapsed to ~1/hr by the
                # send_alert dedup gate. (Before 2026-06-11 this was a HIGH email
                # and spammed the inbox on every restart cycle.)
                title = "HYDRA — IBKR session re-probe failed (auto-restarting)"
                body = (
                    "Strategy ensure_connected() health-probe failed — exiting "
                    "for an automatic systemd restart (back in ~30s). In broker "
                    "mode the shared session is owned by calypso-broker and "
                    "recovers on its own; this is informational. If the bot "
                    "keeps cycling, check calypso-broker."
                )
                priority = _alert_priority("LOW")
            else:
                # ACTIONABLE: calypso-broker's own re-auth has failed 2+ cycles —
                # A/B/C cannot trade until the session is restored. Stays HIGH +
                # emails (and is exempt from the LOW Telegram-only routing).
                title = "calypso-broker — IBKR re-auth failing"
                body = (
                    "Shared broker IBKR re-auth has FAILED for 2+ consecutive "
                    "cycles (~30+ min). The broker stays UP and keeps retrying "
                    "(it does NOT exit), but A/B/C cannot trade until the "
                    "session is restored. The routine ~01:00 ET IBKR reset "
                    "normally clears in one cycle — if this fired during market "
                    "hours, investigate LST/consumer-key or a competing session "
                    "on the same IBKR username."
                )
                priority = _alert_priority("HIGH")
            if reason:
                body = f"{body}\n\nReason: {reason}"
            self._alerts.send_alert(
                alert_type=_alert_type("API_ERROR"),
                title=title,
                message=body,
                priority=priority,
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
            # If the family has settled into a stable HALF_OPEN (reading
            # breaker.state drove OPEN→HALF_OPEN but no real probe has run to
            # close it), treat it as recovered after a bounded grace period so
            # we don't remind forever on a recovered-but-idle family. This must
            # run before the reminder so a just-resolved outage doesn't also
            # fire a "still degraded" reminder in the same cycle.
            if family in self._stuck_open_since:
                self._maybe_resolve_stable_half_open(family, current)
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
            self._half_open_since.pop(family, None)
            self._fire_breaker_recovered(family)
        # else: OPEN↔HALF_OPEN flap inside an outage, or CLOSED→CLOSED — no
        # per-transition alert; the cadence reminder covers "still degraded".
        # The stable-HALF_OPEN-since timer is maintained in
        # _maybe_resolve_stable_half_open (re-armed on any read of OPEN).

    def _maybe_resolve_stable_half_open(self, family: str, current: Any) -> None:
        """End an outage whose breaker has settled into a stable HALF_OPEN.

        Reading ``breaker.state`` drives OPEN→HALF_OPEN once the probe interval
        elapses, but HALF_OPEN→CLOSED only happens on a *real* successful probe
        (``record_success``). A recovered-but-idle family (e.g. ``orders``
        outside the brief entry windows) therefore never sees a closing probe
        and would stay pinned in HALF_OPEN — firing false HIGH "still degraded"
        reminders forever and never emitting a recovery alert.

        We track when the family first read HALF_OPEN within this outage. Any
        read of OPEN (a failing probe re-tripped it) re-arms the timer, so a
        genuinely-unhealthy breaker is never presumed recovered. Once the
        family has stayed continuously HALF_OPEN for HALF_OPEN_RECOVERY_GRACE_S,
        we end the outage and fire a (presumed) recovery alert.
        """
        if self._is_open(current):
            # A failing probe re-tripped the breaker — restart the HALF_OPEN
            # stability timer; this is a real ongoing outage.
            self._half_open_since.pop(family, None)
            return
        if not self._is_half_open(current):
            # CLOSED is handled by the transition path; anything else: ignore.
            return
        now = time.monotonic()
        since = self._half_open_since.get(family)
        if since is None:
            self._half_open_since[family] = now
            return
        if now - since < HALF_OPEN_RECOVERY_GRACE_S:
            return
        # Stable HALF_OPEN past the grace period — presume recovered.
        self._stuck_open_since.pop(family, None)
        self._stuck_open_last_reminder.pop(family, None)
        self._half_open_since.pop(family, None)
        self._fire_breaker_recovered(family, presumed=True)

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

    def _fire_breaker_recovered(self, family: str, *, presumed: bool = False) -> None:
        """Fire when a family's outage ends — LOW priority.

        ``presumed=False`` (default): an actual probe closed the breaker
        (OPEN→CLOSED or HALF_OPEN→CLOSED). ``presumed=True``: the breaker
        settled into a stable HALF_OPEN with no failing probes for the grace
        period and is presumed recovered without a closing probe having run
        (a quiet family with no traffic to drive the closing probe)."""
        if presumed:
            message = (
                f"The IBKR `{family}` circuit breaker has been in HALF_OPEN "
                f"with no further failures for "
                f"{int(HALF_OPEN_RECOVERY_GRACE_S / 60)}m and is presumed "
                f"recovered (no request traffic ran a closing probe). It will "
                f"fully CLOSE on the next successful call. Clearing the outage "
                f"so reminders stop."
            )
        else:
            message = (
                f"The IBKR `{family}` circuit breaker is CLOSED "
                f"again. Normal operation resumed."
            )
        try:
            self._alerts.send_alert(
                alert_type=_alert_type("CONNECTION_RESTORED"),
                title=f"HYDRA — {family} breaker recovered",
                message=message,
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
    def _is_half_open(state: Any) -> bool:
        return getattr(state, "value", str(state)).lower() == "half_open"

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
