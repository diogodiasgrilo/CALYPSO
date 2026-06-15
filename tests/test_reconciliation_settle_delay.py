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
