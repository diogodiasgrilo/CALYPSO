"""The halt hook stays stubbed BY DECISION, not by oversight.

`_is_daily_loss_limit_reached()` returns False. Read cold that looks like an
unfinished feature, and the obvious "fix" is to wire the threshold in. It was
measured before being left that way (LIVE_HALT_CRITERIA §2-bis, operator
decision 2026-09-30):

  * halt at −$600/contract or ≥3 stops → fires on **0 of B's 32 live days**
  * halt at −$400 → blocks exactly one entry, 2026-09-24 e#7, which collected
    **+$280 and never stopped**

The binding constraint is the schedule, not the threshold: B places every entry
by 12:45, so a same-day halt has almost nothing left in the day to block.
Automating it would have cost $280 and bought nothing.

So these criteria are an operator tripwire, not an automated control. This test
exists so that turning them into one is a deliberate act with a failing test to
answer, rather than a tidy-up.
"""
from __future__ import annotations

import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


class TestTheHookStaysStubbedDeliberately:
    def test_it_returns_false(self):
        s = HydraStrategy.__new__(HydraStrategy)
        assert HydraStrategy._is_daily_loss_limit_reached(s) is False, (
            "the halt hook now blocks entries. That is a money-path change and "
            "it reverses a measured decision (0/32 days at −$600; −$280 at "
            "−$400). If it is intended, update LIVE_HALT_CRITERIA §2-bis and "
            "this test together.")

    def test_it_records_why(self):
        """A bare `return False` invites a well-meaning fix. The measurement
        has to travel with the code, not only live in a doc."""
        src = inspect.getsource(HydraStrategy._is_daily_loss_limit_reached)
        assert "MEASURED" in src, "the stub lost the measurement that justifies it"
        assert re.search(r"halt_criteria_counterfactual", src), (
            "the stub no longer names the script that produced the numbers, so "
            "the next reader cannot re-run them")


class TestTheChosenThresholdIsRecorded:
    def _doc(self):
        import io
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, "docs/migration/LIVE_HALT_CRITERIA.md"),
                       encoding="utf-8").read()

    def _h1_row(self):
        """The H1 table row itself.

        A bare `"≤ −$600" in doc` assertion is VACUOUS here: H2 (cumulative
        week-1 loss) is also ≤ −$600, so the substring matches whatever H1
        says. A mutation control proved it — changing H1 to −$700 left the
        test green. Anchor on the row.
        """
        for line in self._doc().splitlines():
            if line.startswith("| H1 |"):
                return line
        raise AssertionError("no H1 row found in LIVE_HALT_CRITERIA.md")

    def test_h1_is_the_structural_minus_600(self):
        row = self._h1_row()
        assert "−$600" in row, f"H1 is no longer −$600/contract: {row[:120]}"
        assert "max_sides_tolerated" in row, (
            "H1 lost its STRUCTURAL derivation and may have drifted back to a "
            "'worst observed' rule — the definition that broke on 2026-09-21")

    def test_the_h1_assertion_is_not_matching_H2(self):
        """H2 is also ≤ −$600. Pin that the two rows are distinguishable, so
        this suite cannot go vacuous the way it did on first writing."""
        rows = [l for l in self._doc().splitlines() if l.startswith("| H2 |")]
        assert rows, "no H2 row"
        assert "max_sides_tolerated" not in rows[0], (
            "H2 now carries H1's derivation text — the H1 test may be reading "
            "the wrong row again")

    def test_the_rejected_definition_is_still_named_as_rejected(self):
        """Option A was chosen partly BECAUSE '1.6x the worst observed' is
        guaranteed to re-break. If that reasoning disappears, someone will
        re-derive it from a bigger sample and call it an improvement."""
        d = self._doc()
        assert "1.6×" in d and "guaranteed" in d

    def test_h1_and_h3_are_consistent(self):
        d = self._doc()
        assert "consistent with H1 by construction" in d, (
            "the H1/H3 consistency note is gone — a 3-stop session must trip "
            "both together, not sit inside H3 while past H1")
