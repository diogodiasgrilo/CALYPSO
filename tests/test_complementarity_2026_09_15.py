"""Statistics for the pre-registered complementarity analysis (2026-09-15).

THE POINT OF THIS FILE. The analysis exists to avoid repeating a specific error:
on 2026-09-02 the 11:15 slot was cut for ranking worst of seven, and a later
permutation test put the ENTIRE per-slot effect at p=0.569. The permutation test
is therefore the load-bearing component, and a permutation test that is subtly
wrong is worse than none — it would launder noise as significance.

So these test the pure functions against answers known in advance:
  * a permutation test on data with NO real effect must return a high p
  * the same test on an overwhelming effect must return a low p
  * a bootstrap CI on a sample straddling zero must span zero
  * bucket edges must come from the POOLED distribution, not per-variant
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.complementarity import (  # noqa: E402
    BUCKET_LABELS,
    BucketStat,
    bootstrap_ci,
    bucket_by_terciles,
    permutation_p,
    spread_statistic,
    tercile_edges,
)


class TestTheBuckets:
    def test_terciles_split_into_thirds(self):
        assert tercile_edges([1, 2, 3, 4, 5, 6, 7, 8, 9]) == (4, 7)

    def test_assignment_is_monotonic(self):
        edges = (4, 7)
        assert bucket_by_terciles(1, edges) == "quiet"
        assert bucket_by_terciles(5, edges) == "moderate"
        assert bucket_by_terciles(9, edges) == "trending"

    def test_boundaries_are_half_open_and_consistent(self):
        """A value exactly on an edge must land in exactly one bucket."""
        edges = (4, 7)
        assert bucket_by_terciles(4, edges) == "moderate"
        assert bucket_by_terciles(7, edges) == "trending"

    def test_too_few_values_refuses(self):
        with pytest.raises(ValueError):
            tercile_edges([1, 2])


class TestThePermutationTestIsHONEST:
    """The whole analysis rests on this returning a HIGH p for noise."""

    def test_pure_noise_is_not_significant(self):
        """Random values with random labels: p must be high. If this test ever
        fails, every conclusion drawn with this function is suspect."""
        r = random.Random(42)
        labelled = [(r.choice(BUCKET_LABELS), r.gauss(0, 100)) for _ in range(30)]
        p = permutation_p(labelled, spread_statistic, n_permutations=2000)
        assert p > 0.10, f"noise scored p={p} — the test is laundering randomness"

    def test_an_overwhelming_effect_is_detected(self):
        """Quiet days all +100, trending days all -100: unmissable."""
        labelled = ([("quiet", 100.0)] * 12 + [("trending", -100.0)] * 12
                    + [("moderate", 0.0)] * 6)
        p = permutation_p(labelled, spread_statistic, n_permutations=2000)
        assert p < 0.01, f"a perfect separation scored p={p}"

    def test_it_is_two_sided(self):
        """A strategy that EARNS on trending days must be as detectable as one
        that loses — the complement we are hunting has the opposite sign."""
        flipped = ([("quiet", -100.0)] * 12 + [("trending", 100.0)] * 12
                   + [("moderate", 0.0)] * 6)
        assert permutation_p(flipped, spread_statistic, n_permutations=2000) < 0.01

    def test_a_negative_and_positive_effect_score_THE_SAME(self):
        """Symmetry is the real two-sidedness test, and the one that matters.

        A mutation survived the test above: comparing `statistic >= observed`
        instead of `abs(statistic) >= observed`. A POSITIVE effect is still
        detected, so that test passed — but a NEGATIVE effect's p is roughly
        HALVED, making it look twice as significant as it is.

        H1 predicts a NEGATIVE spread (a short-premium book losing on trend
        days). So that bug would inflate the significance of precisely the
        result this analysis exists to test honestly. Mirroring the data must
        not change the answer.
        """
        base = [("quiet", 40.0), ("quiet", 55.0), ("quiet", 30.0), ("quiet", 48.0),
                ("moderate", 5.0), ("moderate", -8.0), ("moderate", 2.0),
                ("trending", -35.0), ("trending", -50.0), ("trending", -28.0),
                ("trending", -44.0)]
        mirrored = [(lab, -val) for lab, val in base]
        p_neg = permutation_p(base, spread_statistic, n_permutations=4000)
        p_pos = permutation_p(mirrored, spread_statistic, n_permutations=4000)
        assert abs(p_neg - p_pos) < 0.02, (
            f"negative effect p={p_neg} vs mirrored p={p_pos} — the test is "
            f"one-sided, and it favours the sign we expect to find"
        )

    def test_it_matches_a_hand_computed_p(self):
        """Known-answer calibration — the only test that caught the real bug.

        Two mutants slipped past everything else: comparing
        `statistic >= observed` instead of `abs(statistic) >= observed`. It still
        detects a positive effect, and it halves BOTH signs symmetrically, so
        neither the two-sided test nor a mirror-symmetry test can see it. What it
        actually does is halve every p-value — turning p=0.10 into p=0.05.

        So: a case small enough to enumerate by hand. Values [10,10,-10,-10] with
        2 quiet and 2 trending labels admits 4!/(2!2!) = 6 distinct arrangements:

            (q=10,10 | t=-10,-10) -> spread -20   |spread| >= 20  ✓
            (q=-10,-10 | t=10,10) -> spread +20   |spread| >= 20  ✓
            the other four are mixed -> spread 0

        Exactly 2 of 6 reach the observed magnitude, so the true TWO-SIDED
        p = 2/6 = 0.333. A one-sided test counts only the +20 case and returns
        0.167 — a result that would read as "twice as significant".
        """
        labelled = [("quiet", 10.0), ("quiet", 10.0),
                    ("trending", -10.0), ("trending", -10.0)]
        p = permutation_p(labelled, spread_statistic, n_permutations=20000)
        assert 0.30 < p < 0.37, (
            f"expected the hand-computed two-sided p=0.333, got {p:.3f}. "
            f"~0.167 means the test has become one-sided and every p-value in "
            f"the report is half what it should be."
        )

    def test_it_is_deterministic(self):
        """A report that changes between runs cannot be reviewed."""
        labelled = [("quiet", 1.0), ("quiet", 2.0), ("trending", 9.0), ("trending", 8.0)]
        a = permutation_p(labelled, spread_statistic, n_permutations=500)
        b = permutation_p(labelled, spread_statistic, n_permutations=500)
        assert a == b

    def test_a_degenerate_sample_does_not_crash_or_claim_significance(self):
        assert permutation_p([("quiet", 1.0)], spread_statistic) == 1.0

    def test_a_missing_bucket_in_a_shuffle_contributes_zero(self):
        """Some shuffles leave a bucket empty; that must not raise."""
        assert spread_statistic({"quiet": [1.0]}) == 0.0
        assert spread_statistic({}) == 0.0


class TestTheBootstrap:
    def test_a_sample_straddling_zero_spans_zero(self):
        """`spans_zero` is what stops a mean being read as a direction."""
        lo, hi = bootstrap_ci([-50.0, 50.0, -40.0, 45.0, -5.0, 10.0], n_resamples=2000)
        assert lo < 0 < hi
        assert BucketStat("x", 6, 1.0, 1.0, lo, hi).spans_zero

    def test_a_clearly_positive_sample_excludes_zero(self):
        lo, hi = bootstrap_ci([100.0, 110.0, 95.0, 105.0, 98.0, 102.0], n_resamples=2000)
        assert lo > 0
        assert not BucketStat("x", 6, 101.0, 101.0, lo, hi).spans_zero

    def test_single_observation_is_degenerate_not_fabricated(self):
        assert bootstrap_ci([7.0]) == (7.0, 7.0)

    def test_it_is_deterministic(self):
        a = bootstrap_ci([1.0, 2.0, 3.0, 4.0], n_resamples=500)
        b = bootstrap_ci([1.0, 2.0, 3.0, 4.0], n_resamples=500)
        assert a == b

    def test_empty_refuses(self):
        with pytest.raises(ValueError):
            bootstrap_ci([])


class TestTheStatisticMeasuresWhatWeClaim:
    def test_trending_minus_quiet(self):
        assert spread_statistic({"quiet": [10.0], "trending": [-30.0]}) == -40.0

    def test_sign_convention_matches_the_hypothesis(self):
        """H1 predicts a NEGATIVE spread for a short-premium strategy."""
        assert spread_statistic({"quiet": [100.0], "trending": [-100.0]}) < 0
