"""POS-003 must work out WHICH leg vanished, not give up (2026-09-15).

THE INCIDENT. IBKR nets every position at a conid into one number. With 5pt-wide
spreads placed 30 minutes apart, one entry's protective LONG routinely lands on
another entry's SHORT — same contract, opposite direction. Measured on the live
seat 2026-09-11:

    strike 7710:  E#1 short_call  +  E#5 long_call
    strike 7715:  E#1 long_call   +  E#2 short_call

Those net to zero, so expected and actual agree — until the short is stopped.
Then the broker shows +7 against an expectation of 0, and the old code saw
"conid maps to 2 tracked legs", logged "ambiguous, leaving for manual review",
and stopped. A real 7-lot sat untracked for six hours firing CRITICAL alerts
nobody read. **An untracked position has no stop on it.**

IT WAS NEVER AMBIGUOUS. The contribution that disappeared is exactly
`expected - actual`, so identifying the vanished legs is subset-sum over a
handful of signed quantities. Net +7 against {short -7, long +7} has one answer.

WHAT THIS FILE PINS, in order of how much it would cost to get wrong:
  1. the real 2026-09-11 shape resolves, and resolves to the SHORT
  2. genuine ties are still refused — two same-sign legs are NOT
     interchangeable, because each entry's credit differs and booking the wrong
     one mis-attributes realized P&L
  3. partial fills still fall through to manual review
  4. the single-leg behaviour that already worked is untouched
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402

resolve = HydraStrategy._resolve_vanished_legs


def leg(entry_no, name, contracts=7):
    """(entry, leg_name, signed_contribution) — short negative, long positive."""
    sign = -1 if name.startswith("short") else 1
    return (SimpleNamespace(entry_number=entry_no), name, sign * contracts)


class TestTheRealIncident:
    """2026-09-11, strike 7710: E#1 short_call (-7) + E#5 long_call (+7)."""

    LEGS = [leg(1, "short_call"), leg(5, "long_call")]

    def test_the_short_being_stopped_resolves_uniquely(self):
        """expected 0, broker +7 -> only the short can have gone."""
        resolved, reason = resolve(0, 7, self.LEGS)
        assert resolved is not None, reason
        assert len(resolved) == 1
        assert resolved[0][1] == "short_call"
        assert resolved[0][0].entry_number == 1

    def test_the_long_vanishing_resolves_to_the_LONG(self):
        """The mirror image must not resolve to the same leg — proof the solver
        reads the sign rather than picking the first match."""
        resolved, _ = resolve(0, -7, self.LEGS)
        assert resolved is not None
        assert resolved[0][1] == "long_call"
        assert resolved[0][0].entry_number == 5

    def test_both_vanishing_resolves_to_both(self):
        resolved, _ = resolve(0, 0, self.LEGS)
        # expected == actual -> nothing vanished at all
        assert resolved is None

    def test_the_old_code_would_have_refused_this(self):
        """Regression anchor: two legs is exactly what used to bail out."""
        assert len(self.LEGS) != 1
        assert resolve(0, 7, self.LEGS)[0] is not None


class TestItRefusesToGuess:
    """A wrong attribution books realized P&L against the wrong entry."""

    def test_two_same_sign_legs_are_a_genuine_tie(self):
        """Two entries short the same strike: either could be the one that
        closed, and their credits differ, so they are NOT interchangeable."""
        legs = [leg(1, "short_call"), leg(2, "short_call")]
        resolved, reason = resolve(-14, -7, legs)
        assert resolved is None
        assert "refusing to guess" in reason

    def test_a_partial_fill_is_left_alone(self):
        """A half-closed leg produces a delta no subset can sum to."""
        legs = [leg(1, "short_call", 7)]
        resolved, reason = resolve(-7, -3, legs)
        assert resolved is None
        assert "partial" in reason

    def test_no_tracked_leg_is_not_resolved(self):
        resolved, reason = resolve(0, 7, [])
        assert resolved is None
        assert "no tracked leg" in reason

    def test_a_pathological_leg_count_is_capped(self):
        legs = [leg(i, "short_call") for i in range(13)]
        resolved, reason = resolve(-91, -84, legs)
        assert resolved is None
        assert "too many" in reason


class TestTheSingleLegPathIsUnchanged:
    """The case that already worked must keep working, identically."""

    def test_a_lone_short_that_vanished_resolves(self):
        legs = [leg(3, "short_put")]
        resolved, _ = resolve(-7, 0, legs)
        assert resolved == [(legs[0][0], "short_put")]

    def test_a_lone_long_that_vanished_resolves(self):
        legs = [leg(3, "long_put")]
        resolved, _ = resolve(7, 0, legs)
        assert resolved == [(legs[0][0], "long_put")]

    def test_agreement_is_never_treated_as_a_vanish(self):
        legs = [leg(3, "short_put")]
        assert resolve(-7, -7, legs)[0] is None


class TestItIsWiredIn:
    def test_the_handler_calls_the_solver(self):
        import inspect
        src = inspect.getsource(HydraStrategy._handle_position_discrepancies)
        assert "_resolve_vanished_legs" in src
        assert "maps to" in src  # still logs the unresolved case

    def test_the_old_bail_out_is_gone(self):
        import inspect
        src = inspect.getsource(HydraStrategy._handle_position_discrepancies)
        assert "if len(legs) != 1:" not in src

    def test_disposal_was_extracted_not_duplicated(self):
        """One disposal implementation, so the L-M3 double-book guard cannot
        drift between the single-leg and multi-leg paths."""
        import inspect
        assert hasattr(HydraStrategy, "_dispose_vanished_leg")
        src = inspect.getsource(HydraStrategy._dispose_vanished_leg)
        assert "L-M3" in src
        assert "_book_realized_pnl" in src


class TestTheHANDLERAssignsSignsCorrectly:
    """The solver is only as right as the signs handed to it.

    A mutation test caught this: inverting the handler's own
    `-1 if leg.startswith("short") else 1` left every solver test passing,
    because those tests build their legs with their own sign logic. With the
    signs flipped the bot resolves to the WRONG leg — clearing a live leg's
    tracking and booking realized P&L against the wrong entry.

    These drive `_handle_position_discrepancies` itself, with real entry
    objects, so the handler's sign construction is on the hook. Disposal is
    stubbed: what is under test is WHICH leg is chosen, not the booking path.
    """

    CONID = 911460104

    @staticmethod
    def _entry(entry_no, **uics):
        e = SimpleNamespace(entry_number=entry_no, contracts=7)
        for name in ("short_call", "long_call", "short_put", "long_put"):
            setattr(e, f"{name}_uic", uics.get(name))
        return e

    def _run(self, entries, exp_qty, act_qty):
        s = HydraStrategy.__new__(HydraStrategy)
        s.daily_state = SimpleNamespace(entries=entries)
        chosen = []
        s._dispose_vanished_leg = (
            lambda entry, leg, conid, act: chosen.append((entry.entry_number, leg))
        )
        HydraStrategy._handle_position_discrepancies(s, {self.CONID: (exp_qty, act_qty)})
        return chosen

    def test_the_real_incident_picks_the_SHORT(self):
        """E#1 short + E#5 long at one strike; broker shows +7 -> the SHORT went.
        If the handler's signs were inverted this would pick E#5's long."""
        e1 = self._entry(1, short_call=self.CONID)
        e5 = self._entry(5, long_call=self.CONID)
        assert self._run([e1, e5], 0, 7) == [(1, "short_call")]

    def test_the_mirror_case_picks_the_LONG(self):
        e1 = self._entry(1, short_call=self.CONID)
        e5 = self._entry(5, long_call=self.CONID)
        assert self._run([e1, e5], 0, -7) == [(5, "long_call")]

    def test_a_put_side_overlap_resolves_the_same_way(self):
        """Puts carry the same sign rule; a short put is still negative."""
        e2 = self._entry(2, short_put=self.CONID)
        e3 = self._entry(3, long_put=self.CONID)
        assert self._run([e2, e3], 0, 7) == [(2, "short_put")]

    def test_a_genuine_tie_disposes_NOTHING(self):
        e1 = self._entry(1, short_call=self.CONID)
        e2 = self._entry(2, short_call=self.CONID)
        assert self._run([e1, e2], -14, -7) == []

    def test_contracts_are_read_per_entry_not_assumed(self):
        """Entries can differ in size; the contribution must use each entry's
        own contract count or the subset-sum is solving the wrong equation."""
        e1 = self._entry(1, short_call=self.CONID)
        e1.contracts = 10
        e5 = self._entry(5, long_call=self.CONID)
        e5.contracts = 3
        # expected -10 + 3 = -7 ; broker +3 -> the 10-lot short vanished
        assert self._run([e1, e5], -7, 3) == [(1, "short_call")]
