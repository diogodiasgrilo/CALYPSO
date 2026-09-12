"""POS-003 reconciliation: merge-aware diff + confirm-before-alarm decision.

These exercise the pure helpers that drive the hourly reconciliation, including
the exact shape of today's (2026-06-15) variant-C false alarm: two entries
sharing the same call strikes merge at the broker, and a reconciliation racing a
just-executed close reads a stale quantity.
"""
from bots.hydra.strategy import HydraStrategy as H


def test_merge_is_not_a_discrepancy():
    # Both entries short the same call conid -> expected SUMS to -14; broker
    # shows the merged -14. That is a correct merge, NOT a mismatch.
    assert H._recon_diff_quantities({886493705: -14}, {886493705: -14}) == {}


def test_stale_read_after_one_entry_closes_is_flagged():
    # After E#2 closes, expected drops to E#1-only (-7); a stale broker read
    # still shows the merged -14 -> flagged (this is what we now defer/confirm).
    assert H._recon_diff_quantities({886493705: -7}, {886493705: -14}) == {
        886493705: (-7, -14)
    }


def test_detect_orphans_ignores_zero_and_tracked():
    # E#2's put conid still showing -7 (stale) while untracked -> orphan;
    # a settled-to-0 conid is ignored; a tracked conid is not an orphan.
    assert H._recon_detect_orphans({886493705: -7}, {886493705: -7, 879096052: -7, 999: 0}) == {
        879096052: -7
    }


def test_confirm_before_alarm_defers_first_then_commits():
    # First detection -> defer (don't alert; it may be IBKR feed lag).
    assert H._recon_should_defer(has_findings=True, is_confirm_pass=False) is True
    # Confirmation re-check -> commit (alert/act on what persists).
    assert H._recon_should_defer(has_findings=True, is_confirm_pass=True) is False
    # Nothing wrong -> never defer.
    assert H._recon_should_defer(has_findings=False, is_confirm_pass=False) is False
    assert H._recon_should_defer(has_findings=False, is_confirm_pass=True) is False


# ── recent-close orphan suppression (2026-06-23) ──────────────────────────────
# A leg the bot ITSELF just closed can linger in IBKR's positions feed for >80s
# (a TP-closed leg lingered 83s, past the 30s confirm window → a false CRITICAL
# on live C). Suppress orphans at conids the bot closed within the settle grace;
# a close that PERSISTS past the grace (the close didn't take) is still surfaced.

from datetime import datetime, timedelta  # noqa: E402
from bots.hydra.strategy import RECON_CLOSE_SETTLE_GRACE_S  # noqa: E402


def _inst():
    return H.__new__(H)


def test_note_recent_close_records_and_ignores_none():
    s = _inst()
    s._recent_close_conids = None  # unset → lazy-init
    s._note_recent_close(885532730)
    assert 885532730 in s._recent_close_conids
    s._note_recent_close(None)     # no-op, no crash
    assert None not in s._recent_close_conids


def test_suppress_drops_recent_keeps_genuine_orphan():
    s = _inst()
    now = datetime(2026, 6, 23, 15, 0, 57)
    s._recent_close_conids = {
        885532730: now - timedelta(seconds=80),   # bot closed 80s ago (within grace)
        885089498: now - timedelta(seconds=10),
    }
    # 999999 was NEVER closed by the bot → a genuine untracked orphan
    orphans = {885532730: -7, 885089498: 7, 999999: -7}
    assert s._recon_suppress_recent_closes(orphans, now) == {999999: -7}


def test_suppress_keeps_close_persisting_past_grace():
    # A "close" still showing past the settle grace means it didn't actually take
    # → a real problem → must still surface.
    s = _inst()
    now = datetime(2026, 6, 23, 16, 0, 0)
    s._recent_close_conids = {885532730: now - timedelta(seconds=RECON_CLOSE_SETTLE_GRACE_S + 60)}
    assert s._recon_suppress_recent_closes({885532730: -7}, now) == {885532730: -7}


def test_suppress_is_noop_without_recent_closes():
    s = _inst()
    s._recent_close_conids = {}
    orphans = {999: -7}
    assert s._recon_suppress_recent_closes(orphans, datetime(2026, 6, 23, 15, 0, 0)) == orphans


def test_todays_false_alarm_fully_suppressed():
    # The exact 2026-06-23 15:00 alarm: 3 just-TP-closed legs lingering ~83s in
    # the feed → all within grace → all suppressed → no CRITICAL fired.
    s = _inst()
    now = datetime(2026, 6, 23, 15, 0, 57)
    closed_at = now - timedelta(seconds=83)
    s._recent_close_conids = {885532730: closed_at, 885089498: closed_at, 883825680: closed_at}
    orphans = {885532730: -7, 885089498: 7, 883825680: -7}
    assert s._recon_suppress_recent_closes(orphans, now) == {}
