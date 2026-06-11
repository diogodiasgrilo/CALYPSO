"""Anti-spam gate in AlertService (added 2026-06-11).

A retry-loop flooded the inbox with dozens of identical CRITICAL/HIGH emails.
The fix is one gate at the send_alert chokepoint that EVERY call site passes
through, so no loop can ever spam email again:

  Layer 1 — content dedup: identical alerts within a per-priority window
            collapse (the first always sends; distinct entry/side/wording does
            not collapse). Applies to ALL alerts including _NEVER_SUPPRESS.
  Layer 2 — per-type token bucket: a burst of same-TYPE alerts whose titles
            differ enough to dodge dedup is rate-limited. Skipped for
            _NEVER_SUPPRESS.
  Layer 3 — global email ceiling: over N emails / window, non-critical alerts
            downgrade to Telegram-only. _NEVER_SUPPRESS always emails.

Plus the _should_send_email reorder so a _TELEGRAM_ONLY type can be demoted
even at HIGH (previously the CRITICAL/HIGH bypass ran first and made the
Telegram-only set unreachable for HIGH alerts).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.alert_service import AlertService, AlertType, AlertPriority  # noqa: E402


@pytest.fixture(autouse=True)
def _dry_run_env(monkeypatch):
    # DRY_RUN makes a "published" alert observable as a True return without a
    # live Pub/Sub channel; the gate still runs before the dry-run short-circuit.
    monkeypatch.setenv("ALERT_DRY_RUN", "true")
    yield


def _svc() -> AlertService:
    return AlertService({"alerts": {"enabled": True}}, "HYDRA_C")


# ─── Layer 1: content dedup ───────────────────────────────────────────────

def test_identical_alerts_collapse_to_one():
    """The decisive guarantee: fire the SAME alert 50× rapidly → only the first
    is delivered, the rest are suppressed. This is what stops a tick-loop."""
    svc = _svc()
    results = [
        svc.send_alert(AlertType.EMERGENCY_CLOSE, "ORPHANED CLOSE — TP closed 0 legs",
                       "still open", priority=AlertPriority.HIGH)
        for _ in range(50)
    ]
    assert results[0] is True
    assert all(r is False for r in results[1:])
    assert sum(results) == 1


def test_distinct_entries_are_not_collapsed():
    """A real, distinct event (different entry_number) must still get through —
    dedup keys on entry/side from details, not just the title."""
    svc = _svc()
    r1 = svc.send_alert(AlertType.STOP_LOSS, "Stop loss fired", "x",
                        priority=AlertPriority.HIGH, details={"entry_number": 1})
    r2 = svc.send_alert(AlertType.STOP_LOSS, "Stop loss fired", "x",
                        priority=AlertPriority.HIGH, details={"entry_number": 2})
    r3 = svc.send_alert(AlertType.STOP_LOSS, "Stop loss fired", "x",
                        priority=AlertPriority.HIGH, details={"entry_number": 1, "side": "call"})
    assert r1 is True and r2 is True and r3 is True


def test_volatile_dollar_amounts_collapse():
    """Two repeats of a looping alert whose only difference is the $ amount /
    price collapse — the volatile tokens are stripped before fingerprinting."""
    svc = _svc()
    r1 = svc.send_alert(AlertType.EMERGENCY_CLOSE, "Close failed at $3,380.50", "x",
                        priority=AlertPriority.HIGH)
    r2 = svc.send_alert(AlertType.EMERGENCY_CLOSE, "Close failed at $1,200.00", "x",
                        priority=AlertPriority.HIGH)
    assert r1 is True
    assert r2 is False  # same event, different $ → collapsed


def test_window_expiry_allows_resend():
    """After the per-priority window elapses, the same alert sends again
    (a persistent condition re-pings, it isn't silenced forever)."""
    svc = _svc()
    assert svc.send_alert(AlertType.EMERGENCY_CLOSE, "loop", "x",
                          priority=AlertPriority.HIGH) is True
    assert svc.send_alert(AlertType.EMERGENCY_CLOSE, "loop", "x",
                          priority=AlertPriority.HIGH) is False
    # Backdate the recorded send beyond the HIGH window (600s).
    for k in list(svc._dedup_last):
        svc._dedup_last[k] -= 601
    assert svc.send_alert(AlertType.EMERGENCY_CLOSE, "loop", "x",
                          priority=AlertPriority.HIGH) is True


# ─── Layer 2: per-type token bucket ───────────────────────────────────────

def test_token_bucket_limits_same_type_burst():
    """Distinct titles dodge dedup, but the per-type bucket (capacity 3) still
    caps a same-TYPE burst."""
    svc = _svc()
    results = [
        svc.send_alert(AlertType.SLIPPAGE_ALERT, f"slippage {i}", "x",
                       priority=AlertPriority.HIGH)
        for i in range(8)
    ]
    # capacity 3 → first 3 pass, refill is 1/10min so nothing refills mid-test.
    assert sum(results) == 3
    assert results[:3] == [True, True, True]


def test_take_type_token_capacity_then_empty():
    svc = _svc()
    now = time.monotonic()
    assert svc._take_type_token(AlertType.GAP_WARNING, now) is True
    assert svc._take_type_token(AlertType.GAP_WARNING, now) is True
    assert svc._take_type_token(AlertType.GAP_WARNING, now) is True
    assert svc._take_type_token(AlertType.GAP_WARNING, now) is False
    # One refill interval later → exactly one more token.
    assert svc._take_type_token(AlertType.GAP_WARNING, now + svc._BUCKET_REFILL_S) is True
    assert svc._take_type_token(AlertType.GAP_WARNING, now + svc._BUCKET_REFILL_S) is False


# ─── Layer 3: global email ceiling ────────────────────────────────────────

def test_email_ceiling_downgrades_non_critical():
    """At/over the ceiling a non-critical alert is allowed but its email is
    suppressed (Telegram still delivers)."""
    svc = _svc()
    svc._email_times = [time.monotonic()] * svc._EMAIL_CEILING  # pre-fill the window
    allow, send_email, _ = svc._apply_alert_gate(
        AlertType.GAP_WARNING, AlertPriority.HIGH, "fresh gap", None, send_email=True)
    assert allow is True
    assert send_email is False


def test_email_ceiling_never_blocks_critical():
    """A NEVER_SUPPRESS type emails even over the ceiling."""
    svc = _svc()
    svc._email_times = [time.monotonic()] * (svc._EMAIL_CEILING + 5)
    allow, send_email, _ = svc._apply_alert_gate(
        AlertType.NAKED_POSITION, AlertPriority.CRITICAL, "naked short!", None, send_email=True)
    assert allow is True
    assert send_email is True


# ─── _NEVER_SUPPRESS semantics ────────────────────────────────────────────

def test_never_suppress_still_dedups_identical():
    """Even a critical, identical, re-fired-every-tick alert collapses (the
    first sends, repeats within the window don't) — protects against a tight
    pre-halt loop of CRITICAL_INTERVENTION / NAKED_POSITION."""
    svc = _svc()
    results = [
        svc.send_alert(AlertType.NAKED_POSITION, "Naked short on the call side",
                       "x", priority=AlertPriority.CRITICAL)
        for _ in range(20)
    ]
    assert sum(results) == 1


def test_never_suppress_exempt_from_bucket():
    """Distinct critical events (different titles) are NOT bucket-limited —
    every real naked short / breaker must land."""
    svc = _svc()
    results = [
        svc.send_alert(AlertType.CIRCUIT_BREAKER, f"breaker {i} open", "x",
                       priority=AlertPriority.CRITICAL)
        for i in range(8)
    ]
    assert sum(results) == 8  # no bucket cap for never-suppress


# ─── _should_send_email reorder ───────────────────────────────────────────

def test_telegram_only_type_not_emailed_even_at_high():
    """The reorder: a _TELEGRAM_ONLY type must be demotable even at HIGH.
    Before the fix the CRITICAL/HIGH bypass returned email=True first."""
    svc = _svc()
    assert svc._should_send_email(AlertType.POSITION_OPENED, AlertPriority.HIGH) is False
    assert svc._should_send_email(AlertType.CONNECTION_RESTORED, AlertPriority.HIGH) is False


def test_email_always_type_emails_even_at_low():
    svc = _svc()
    assert svc._should_send_email(AlertType.DAILY_SUMMARY, AlertPriority.LOW) is True
    assert svc._should_send_email(AlertType.STOP_LOSS, AlertPriority.LOW) is True


def test_low_default_is_telegram_only():
    """An un-classified LOW alert is Telegram-only (matches DEFAULT_PRIORITIES'
    stated intent); MEDIUM still emails."""
    svc = _svc()
    assert svc._should_send_email(AlertType.PROFIT_TARGET, AlertPriority.LOW) is False
    assert svc._should_send_email(AlertType.PROFIT_TARGET, AlertPriority.MEDIUM) is True


def test_critical_unlisted_type_still_emails():
    svc = _svc()
    assert svc._should_send_email(AlertType.MAX_LOSS, AlertPriority.CRITICAL) is True


# ─── fingerprint ──────────────────────────────────────────────────────────

def test_fingerprint_strips_volatile_keeps_entry_side():
    svc = _svc()
    fp_a = svc._alert_fingerprint(AlertType.STOP_LOSS, "Stop at $3,380.50 (12.5%)",
                                  {"entry_number": 1, "side": "call"})
    fp_b = svc._alert_fingerprint(AlertType.STOP_LOSS, "Stop at $999.00 (4.0%)",
                                  {"entry_number": 1, "side": "call"})
    fp_c = svc._alert_fingerprint(AlertType.STOP_LOSS, "Stop at $3,380.50 (12.5%)",
                                  {"entry_number": 2, "side": "call"})
    assert fp_a == fp_b           # volatile $/% stripped → same identity
    assert fp_a != fp_c           # different entry → different identity


# ─── roll-up note ─────────────────────────────────────────────────────────

def test_rollup_note_appended_after_suppression(monkeypatch):
    """When suppressed repeats precede a fresh send (window expired), the
    surviving alert tells the user how many it stands in for."""
    captured = {}

    svc = _svc()
    # Send once, then suppress two, then expire the window and send again —
    # the resend should carry a '+2 similar suppressed' note.
    svc.send_alert(AlertType.EMERGENCY_CLOSE, "loop", "body", priority=AlertPriority.HIGH)
    svc.send_alert(AlertType.EMERGENCY_CLOSE, "loop", "body", priority=AlertPriority.HIGH)
    svc.send_alert(AlertType.EMERGENCY_CLOSE, "loop", "body", priority=AlertPriority.HIGH)
    for k in list(svc._dedup_last):
        svc._dedup_last[k] -= 601

    allow, _, note = svc._apply_alert_gate(
        AlertType.EMERGENCY_CLOSE, AlertPriority.HIGH, "loop",
        {"entry_number": None}, send_email=True)
    assert allow is True
    assert "2 similar suppressed" in note
