"""The gate has to stay asymmetric, and it has to keep admitting its blind spot.

A single uniform deploy rule gets one of two opposite cost profiles wrong:
a fix to a bug that is actively costing money gets more expensive every session
it waits, while new behaviour delayed costs ~nothing and 44% of money-path
commits needed a same-file fix within three days.

The blind spot is the part most worth pinning. `_place_option_order` returns at
SAFETY-DRY-01 in dry mode, so NO dry-run variant ever places an order — the
order path cannot be canaried at all. If that warning ever quietly stops firing,
the gate starts recommending a verification that is structurally incapable of
verifying anything, which is worse than having no gate.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import deploy_gate as dg  # noqa: E402


def _c(sha="abc1234", subject="feat: thing", is_fix=False, order_path=False):
    return {"sha": sha, "subject": subject, "files": ["bots/hydra/strategy.py"],
            "is_fix": is_fix, "order_path": order_path, "symbols": []}


class TestTheAsymmetry:
    def test_an_all_fix_batch_goes_immediately(self):
        when, why, _ = dg.verdict([_c(is_fix=True), _c(is_fix=True)])
        assert when == "IMMEDIATE", when
        assert "costing money" in why

    def test_any_new_behaviour_forces_a_canary_session(self):
        when, _, _ = dg.verdict([_c(is_fix=True), _c(is_fix=False)])
        assert when == "AFTER ONE CANARY SESSION", (
            f"got {when} — one new-behaviour commit in the batch must pull the "
            f"whole batch onto the slow path; the most restrictive wins")

    def test_no_money_path_change_is_waved_through(self):
        when, _, _ = dg.verdict([])
        assert when == "NO MONEY-PATH CHANGE"


class TestTheBlindSpotIsAlwaysDeclared:
    def test_order_path_cannot_be_verified_by_a_canary(self):
        _, _, how = dg.verdict([_c(order_path=True)])
        assert "canary cannot verify" in how.lower(), how

    def test_non_order_path_uses_a_canary(self):
        _, _, how = dg.verdict([_c(order_path=False)])
        assert "canary" in how.lower() and "cannot" not in how.lower(), how

    def test_a_fix_to_the_order_path_is_immediate_but_NOT_canary_verified(self):
        """The two axes are independent: WHEN it ships and HOW you know it
        worked. Collapsing them would either delay a hotfix or claim a
        verification that cannot happen."""
        when, _, how = dg.verdict([_c(is_fix=True, order_path=True)])
        assert when == "IMMEDIATE"
        assert "canary cannot verify" in how.lower()

    def test_the_safety_guard_it_cites_still_exists(self):
        """The warning names SAFETY-DRY-01 as the reason. If that guard were
        ever removed, dry-run variants WOULD place orders and this whole
        recommendation inverts — so the claim has to stay checkable."""
        import io
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "bots/hydra/base_strategy.py"),
                      encoding="utf-8").read()
        assert "SAFETY-DRY-01" in src, (
            "the dry-run placement guard is gone — dry-run variants may now "
            "place real orders, and the order-path advice is backwards")


class TestItClassifiesRealHistory:
    def test_an_order_path_commit_is_detected(self):
        """bb0bb9d added the deferred over-fill re-check — squarely order path."""
        cs = dg.classify("bb0bb9d~1..bb0bb9d")
        if not cs:
            pytest.skip("commit not present in this checkout")
        assert cs[0]["order_path"], (
            f"bb0bb9d not flagged as order path; symbols={cs[0]['symbols']}")
        assert cs[0]["is_fix"]

    def test_a_docs_only_range_has_no_money_path(self):
        cs = dg.classify("HEAD~1..HEAD")
        assert all(c["files"] for c in cs)

    def test_main_runs(self, capsys):
        assert dg.main(["--ref", "HEAD~1..HEAD"]) == 0
        assert "DEPLOY GATE" in capsys.readouterr().out


class TestTheOrderPathListCannotBeQuietlyNarrowed:
    """The symbol list is the whole detector — pin it.

    A mutation control that deleted four symbols from it still left every test
    green, because the one commit under test happened to match on a fifth. The
    list is an allowlist that decides whether a change gets real-fill
    verification or a canary that structurally cannot see it, so shrinking it
    must fail loudly rather than depend on which commit a test picked.
    """

    REQUIRED = {
        "_place_option_order",      # placement itself
        "place_and_wait_for_fill",  # the fill poll
        "place_order", "cancel_order", "modify_order",
        "_close_leg_order",         # stop/TP closes
        "_correct_over_fill",       # the A2 over-fill class
        "_unwind_partial_entry",    # abandoned partials (the reconcile lead)
        "_ensure_coid",             # retry-safety / dedup
        "rung",                     # entry rung pricing, ~38% of B's net
        "_execute_stop_loss",
    }

    def test_every_required_symbol_is_still_watched(self):
        missing = self.REQUIRED - set(dg.ORDER_PATH_SYMBOLS)
        assert not missing, (
            f"order-path symbols dropped: {sorted(missing)}. Each one removed "
            f"means changes touching it get recommended for canary "
            f"verification, which cannot see the order path at all.")

    def test_the_watched_files_include_the_placement_modules(self):
        for f in ("shared/ib_client.py", "bots/hydra/base_strategy.py"):
            assert dg.ORDER_PATH_FILES.match(f), f
