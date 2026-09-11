"""
Counterfactual outcomes for skipped entries (2026-09-11).

Answers the long-open GEX question — *the adjuster vetoed more entries than it
placed; was that worth it?* — from data now being recorded (commit ec71967).

THE DISCIPLINE THIS FILE ENFORCES is the separation of MEASURED from MODELLED,
because collapsing them is how a plausible number becomes a decision:

  MEASURED: did SPX breach a proposed short strike AFTER the skip time.
            Call breached if max(SPX after skip) >= short_call.
            Put  breached if min(SPX after skip) <= short_put.
  MODELLED: the dollars. Unbreached -> keep the estimated credit (near-measured).
            Breached -> loss modelled as B's acting A2 stop,
            pct_of_width x width x 100 x contracts. The REAL loss depends on when
            the stop fired and what the spread cost to close, neither of which
            exists for a trade never taken.

Two traps specifically tested:

  * **The window must start at the SKIP TIME.** What SPX did EARLIER in the
    session cannot breach a position that would have been opened later. Using
    the whole day would inflate the breach rate and make the veto look better
    than it was — i.e. bias the answer toward the conclusion we already lean to.
  * **A missing strike is UNMEASURABLE, not "no breach".** Scoring it as
    unbreached would silently count all 95 historical vetoes as wins.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_skipped_entry_outcomes import evaluate  # noqa: E402

K = dict(pct_of_width=0.40, contracts=7)


def _row(sc=7650, lc=7655, sp=7550, lp=7545, cc=1.0, pc=1.0):
    return {
        "date": "2026-09-11", "entry_number": 1, "skip_time": "2026-09-11 10:45:00",
        "theoretical_short_call": sc, "theoretical_long_call": lc,
        "theoretical_short_put": sp, "theoretical_long_put": lp,
        "estimated_call_credit": cc, "estimated_put_credit": pc,
    }


class TestBreachDetectionIsMeasured:
    def test_no_breach_when_spx_stayed_inside(self):
        r = evaluate(_row(), lo=7580, hi=7620, **K)
        assert r["breached"] is False
        assert r["call_breach"] is False and r["put_breach"] is False

    def test_call_breach_when_spx_reached_the_short_call(self):
        r = evaluate(_row(sc=7650), lo=7600, hi=7650, **K)
        assert r["call_breach"] is True and r["breached"] is True

    def test_put_breach_when_spx_reached_the_short_put(self):
        r = evaluate(_row(sp=7550), lo=7550, hi=7600, **K)
        assert r["put_breach"] is True and r["breached"] is True

    def test_touching_the_strike_counts_as_a_breach(self):
        """>= and <=, not > and <. A short AT the money is in trouble."""
        assert evaluate(_row(sc=7650), lo=7600, hi=7650.0, **K)["call_breach"]
        assert evaluate(_row(sp=7550), lo=7550.0, hi=7600, **K)["put_breach"]

    def test_one_tick_short_of_the_strike_is_not_a_breach(self):
        assert not evaluate(_row(sc=7650), lo=7600, hi=7649.99, **K)["call_breach"]

    def test_both_sides_can_breach(self):
        r = evaluate(_row(), lo=7540, hi=7660, **K)
        assert r["call_breach"] and r["put_breach"]


class TestAMissingStrikeIsUnmeasurableNotAWin:
    def test_no_strikes_at_all_returns_None(self):
        """The 95 historical vetoes. Scoring them as 'no breach' would count
        every one as a win and invert the conclusion."""
        assert evaluate(_row(sc=None, sp=None), lo=7500, hi=7700, **K) is None

    def test_a_one_sided_row_is_still_measurable(self):
        """The GEX adjuster zeroes the side it drops, so most rows carry only the
        SURVIVING side — which is the side that would have been placed."""
        r = evaluate(_row(sc=None, lc=None), lo=7540, hi=7600, **K)
        assert r is not None
        assert r["put_breach"] is True
        assert r["call_breach"] is False   # absent, not breached

    def test_missing_tick_coverage_returns_None(self):
        """No ticks after the skip means no evidence — not 'no breach'."""
        assert evaluate(_row(), lo=None, hi=None, **K) is None


class TestTheModelledDollarsAreLabelledAndSane:
    def test_unbreached_keeps_the_estimated_credit(self):
        r = evaluate(_row(cc=1.0, pc=1.0), lo=7580, hi=7620, **K)
        assert r["modelled_pnl"] == pytest.approx((1.0 + 1.0) * 100 * 7)

    def test_breached_models_the_loss_as_the_A2_stop(self):
        """5pt wings, 40%, 7c -> -(0.40 x 5 x 100 x 7) = -1400."""
        r = evaluate(_row(sc=7650, lc=7655), lo=7600, hi=7660, **K)
        assert r["modelled_pnl"] == pytest.approx(-(0.40 * 5 * 100 * 7))

    def test_the_loss_scales_with_the_stop_fraction(self):
        a = evaluate(_row(), lo=7600, hi=7660, pct_of_width=0.40, contracts=7)
        b = evaluate(_row(), lo=7600, hi=7660, pct_of_width=0.25, contracts=7)
        assert b["modelled_pnl"] > a["modelled_pnl"]   # smaller stop, smaller loss

    def test_no_width_means_no_modelled_loss_rather_than_zero(self):
        """A zero loss would read as a breakeven breach — strictly better than
        reality. None says 'not modellable'."""
        r = evaluate(_row(sc=7650, lc=None, sp=None, lp=None), lo=7600, hi=7660, **K)
        assert r["breached"] is True
        assert r["modelled_pnl"] is None

    def test_the_measured_and_modelled_fields_stay_separate(self):
        """Both are returned so a caller can report the breach rate without
        inheriting the model's assumptions."""
        r = evaluate(_row(), lo=7580, hi=7620, **K)
        assert set(("breached", "modelled_pnl", "credit")) <= set(r)


class TestTheWindowStartsAtTheSkip:
    """Regression guard for the subtlest bias available here."""

    def test_the_query_is_bounded_below_by_skip_time(self):
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "analyze_skipped_entry_outcomes.py").read_text()
        assert "timestamp >= ?" in src, (
            "the SPX extremes must be taken from the SKIP TIME forward — using "
            "the whole day would let a pre-skip move count as a breach and "
            "inflate the veto's apparent value"
        )

    def test_it_states_the_historical_set_is_unrecoverable(self):
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "analyze_skipped_entry_outcomes.py").read_text()
        assert "NOT RECOVERABLE" in src or "never will" in src

    def test_it_warns_on_a_small_sample(self):
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "analyze_skipped_entry_outcomes.py").read_text()
        assert "Directional at best" in src
