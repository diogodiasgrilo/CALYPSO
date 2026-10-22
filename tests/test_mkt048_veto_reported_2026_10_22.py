"""A skip caused by UNFILLABILITY was reported as a thin credit.

Live on the seat `bl`, entry #2, 2026-10-22 12:45:37:

    MKT-048: Entry #2 call side unfillable — fillable $0.03/sh < net-credit
             floor $0.05/sh (mid est $0.05 passed, but short_bid−long_ask is
             a debit) → vetoing call (would fail at leg 3)
    MKT-011: Entry #2 call credit non-viable ($0.05 < $0.05) - SKIPPING

The DECISION was right. The stated REASON was not: the credit had passed
($0.05 >= $0.05 — the inequality printed is not even true), and MKT-048 had
flipped `call_viable` for a different reason entirely. Downstream the only
signal was the flipped flag, so every consumer reported a credit failure.

Operator-facing, that became: "Call spread $0.05 is below our $0.05 minimum"
— false, self-contradictory, and pointing at the wrong fix. Thin credit says
"the market is not paying enough"; unfillable says "the book is crossed or the
spread is too wide to leg in". Different problems.

Second occurrence of the pattern recorded on 2026-10-07 (`8d4102c`): a log line
ASSERTING a reason rather than deriving it from the decision site. The veto now
records why, and both the log line and the operator message read it.

No behaviour change — the entry is still skipped, and should be.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.strategy import HydraStrategy  # noqa: E402

BUILD = HydraStrategy._build_credit_gate_skip_message


class _Stub:
    min_viable_credit_per_side = 5.0      # $0.05/sh
    min_viable_credit_put_side = 10.0
    one_sided_entries_enabled = False
    put_only_max_vix = 15.0
    current_vix = 14.0


VETO = {"side": "call", "fillable_ps": 0.03, "floor_ps": 0.05}


class TestTheOperatorIsToldWhatActuallyHappened:
    def test_it_names_FILLABILITY_not_price(self):
        reason, details = BUILD(_Stub(), "call", 5.0, 12.0, VETO)
        low = (reason + details).lower()
        assert "fill" in low, reason
        assert "0.03" in reason, "the fillable figure is the whole point"

    def test_it_does_NOT_claim_the_credit_was_too_low(self):
        reason, _d = BUILD(_Stub(), "call", 5.0, 12.0, VETO)
        assert "below our" not in reason, reason
        assert "$0.05 is below" not in reason

    def test_it_states_the_mid_credit_PASSED(self):
        reason, details = BUILD(_Stub(), "call", 5.0, 12.0, VETO)
        assert "passed" in (reason + details).lower()

    def test_the_contradictory_x_below_x_message_cannot_recur(self):
        """The literal string from the incident: '$0.05 is below our $0.05'."""
        reason, details = BUILD(_Stub(), "call", 5.0, 12.0, VETO)
        text = reason + details
        assert "$0.05 is below our $0.05" not in text

    def test_a_put_side_veto_is_reported_too(self):
        v = {"side": "put", "fillable_ps": 0.02, "floor_ps": 0.05}
        reason, _d = BUILD(_Stub(), "put", 12.0, 5.0, v)
        assert "put" in reason.lower() and "0.02" in reason

    def test_a_GENUINE_thin_credit_still_reads_as_one(self):
        """The control. With no veto the original message must be unchanged —
        the fix must not relabel every credit-gate skip as a fill problem."""
        reason, details = BUILD(_Stub(), "call", 2.0, 12.0, None)
        assert "below our" in reason, reason
        assert "fill" not in (reason + details).lower()

    def test_a_veto_for_the_OTHER_side_does_not_hijack_the_message(self):
        """A put veto must not explain a call-side skip."""
        v = {"side": "put", "fillable_ps": 0.02, "floor_ps": 0.05}
        reason, _d = BUILD(_Stub(), "call", 2.0, 12.0, v)
        assert "below our" in reason, reason


class TestTheDecisionSiteRecordsWhy:
    def test_both_veto_branches_stash_the_reason(self):
        import inspect
        src = inspect.getsource(HydraStrategy._check_credit_gate)
        assert src.count("entry._mkt048_veto = {") == 2, (
            "both the call-side and put-side vetoes must record why")

    def test_the_log_line_branches_on_the_stash(self):
        import inspect
        src = inspect.getsource(HydraStrategy._check_credit_gate)
        i = src.index("SKIPPING (one-sided entries disabled)")
        window = src[max(0, i - 1200):i + 200]
        assert "_mkt048_veto" in window, (
            "the skip log still asserts a credit failure unconditionally")
        assert "UNFILLABLE" in window

    def test_the_builder_receives_the_stash_at_the_call_site(self):
        import inspect
        src = inspect.getsource(HydraStrategy)
        # the DEFINITION also matches the name, so find the CALL — the
        # occurrence preceded by "self."
        i = src.index("self._build_credit_gate_skip_message(")
        assert "_mkt048_veto" in src[i:i + 400], "call site drops the veto"
