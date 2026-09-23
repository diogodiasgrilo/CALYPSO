"""Strategy H strike/sizing selection — pure helpers, no broker, no clock.

Step 3's exit criterion is "selection helpers are pure + unit-tested; new data
assumptions are confirmed by a real VM probe (or explicitly flagged as unverified
in the docstring)." This file is the first half. The probe is the second, and
until it runs every assumption here is marked unverified in the module.

Most of these tests pin REFUSALS rather than happy paths, because for this
strategy the dangerous failures are all silent-but-plausible: a missing quote
becoming a tiny expected move and therefore near-the-money strikes; a sparse
chain snapping a 7825 target onto 7700; a sizing helper rounding up to one
contract past a loss limit it exists to enforce.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.long_strangle_chain import (  # noqa: E402
    TRADING_DAYS_PER_YEAR,
    expected_move_from_straddle,
    expected_move_from_vix,
    iv_percentile,
    premiums_are_balanced,
    select_strangle_strikes,
    size_for_zero,
    snap_to_chain,
)

CHAIN = [7700.0 + 5 * i for i in range(41)]  # 7700 … 7900, 5pt grid


class TestExpectedMove:
    def test_straddle_move_is_the_sum_of_both_legs(self):
        assert expected_move_from_straddle(12.0, 10.0) == pytest.approx(22.0)

    def test_a_missing_leg_price_yields_zero_not_a_tiny_move(self):
        """THE trap. A zero/absent quote must not become a ~0 expected move,
        which would place the 'OTM strangle' essentially at the money."""
        assert expected_move_from_straddle(0.0, 10.0) == 0.0
        assert expected_move_from_straddle(12.0, -1.0) == 0.0

    def test_vix_move_matches_variant_F_exactly(self):
        """H and F must never disagree about the arithmetic — only about config."""
        spot, vix = 7765.0, 14.5
        expected = spot * (vix / 100.0) / (TRADING_DAYS_PER_YEAR ** 0.5)
        assert expected_move_from_vix(spot, vix) == pytest.approx(expected)

    def test_vix_multiplier_scales_the_boundary(self):
        base = expected_move_from_vix(7765.0, 14.5)
        assert expected_move_from_vix(7765.0, 14.5, multiplier=2.0) == pytest.approx(2 * base)

    def test_vix_move_refuses_bad_inputs(self):
        assert expected_move_from_vix(0.0, 14.5) == 0.0
        assert expected_move_from_vix(7765.0, 0.0) == 0.0

    def test_the_two_definitions_genuinely_differ(self):
        """Not a tautology — it is the reason the choice is config-driven and the
        reason the spec calls it an open question. On 2026-09-22's numbers the
        straddle-implied move is far wider than the VIX-implied one."""
        vix_move = expected_move_from_vix(7765.0, 14.5)       # ≈ 70.9
        straddle_move = expected_move_from_straddle(12.0, 10.0)  # 22.0
        assert straddle_move != pytest.approx(vix_move, rel=0.1)


class TestSnapToChain:
    def test_snaps_to_the_nearest_listed_strike(self):
        assert snap_to_chain(7823.0, CHAIN) == 7825.0
        assert snap_to_chain(7822.0, CHAIN) == 7820.0

    def test_refuses_when_the_nearest_strike_is_too_far(self):
        """A sparse or partially-loaded chain must not silently resolve 7825 to
        7700. None means 'skip the entry', which is the right answer to a chain
        you cannot trust."""
        sparse = [7700.0, 7900.0]
        assert snap_to_chain(7825.0, sparse, max_distance=25.0) is None

    def test_without_a_cap_it_will_snap_anywhere(self):
        sparse = [7700.0, 7900.0]
        assert snap_to_chain(7825.0, sparse, max_distance=None) == 7900.0

    def test_empty_chain_is_none_not_a_crash(self):
        assert snap_to_chain(7825.0, []) is None

    def test_zero_strikes_are_ignored(self):
        assert snap_to_chain(7825.0, [0.0, 7825.0]) == 7825.0


class TestStrikeSelection:
    def test_places_both_legs_one_expected_move_out(self):
        call, put = select_strangle_strikes(7765.0, 60.0, CHAIN)
        assert (call, put) == (7825.0, 7705.0)

    def test_a_zero_expected_move_is_refused(self):
        """spot ± 0 is an ATM straddle — a different and far costlier position."""
        assert select_strangle_strikes(7765.0, 0.0, CHAIN) == (None, None)

    def test_a_chain_that_cannot_supply_one_side_returns_none_for_it(self):
        """The caller must treat a None on EITHER side as no-entry: a one-legged
        long strangle is a directional bet, which is not this strategy."""
        one_sided = [s for s in CHAIN if s <= 7790.0]
        call, put = select_strangle_strikes(7765.0, 60.0, one_sided)
        assert call is None
        assert put == 7705.0

    def test_bad_spot_is_refused(self):
        assert select_strangle_strikes(0.0, 60.0, CHAIN) == (None, None)


class TestSkewBalance:
    def test_similar_premiums_pass(self):
        assert premiums_are_balanced(10.0, 12.0) is True

    def test_a_lopsided_pair_fails(self):
        """A 'strangle' whose put costs 5× the call is a directional position
        wearing two legs."""
        assert premiums_are_balanced(2.0, 10.0) is False

    def test_the_check_is_symmetric(self):
        """Measured against the larger premium, so argument order cannot change
        the verdict."""
        assert premiums_are_balanced(4.0, 10.0) == premiums_are_balanced(10.0, 4.0)

    def test_a_missing_premium_fails_closed(self):
        assert premiums_are_balanced(0.0, 10.0) is False

    def test_tolerance_is_configurable(self):
        """35% is an explicit guess — the source says only 'reasonably similar'."""
        assert premiums_are_balanced(6.0, 10.0, tolerance_pct=50.0) is True
        assert premiums_are_balanced(6.0, 10.0, tolerance_pct=10.0) is False


class TestSizingForZero:
    def test_sizes_by_what_can_be_lost_outright(self):
        assert size_for_zero(max_acceptable_loss=500.0, debit_per_contract=220.0) == 2

    def test_it_floors_at_zero_rather_than_rounding_up_to_one(self):
        """THE assertion that matters. Rounding up to one contract would breach
        the very loss limit this function exists to enforce."""
        assert size_for_zero(max_acceptable_loss=100.0, debit_per_contract=220.0) == 0

    def test_exact_division_is_not_off_by_one(self):
        assert size_for_zero(440.0, 220.0) == 2

    def test_bad_inputs_size_nothing(self):
        assert size_for_zero(0.0, 220.0) == 0
        assert size_for_zero(500.0, 0.0) == 0


class TestIvPercentile:
    def test_ranks_within_the_history(self):
        assert iv_percentile(15.0, [10.0, 12.0, 14.0, 16.0, 18.0]) == pytest.approx(60.0)

    def test_the_lowest_value_is_a_low_percentile(self):
        assert iv_percentile(5.0, [10.0, 12.0, 14.0]) == pytest.approx(0.0)

    def test_the_highest_value_is_one_hundred(self):
        assert iv_percentile(99.0, [10.0, 12.0, 14.0]) == pytest.approx(100.0)

    def test_empty_history_is_none_not_zero(self):
        """None means 'unknown'. A caller must NOT read it as 'passes the <35%
        filter' — which a 0.0 would invite."""
        assert iv_percentile(15.0, []) is None

    def test_junk_values_are_dropped(self):
        assert iv_percentile(15.0, [None, 0.0, -1.0, 10.0, 20.0]) == pytest.approx(50.0)


class TestNothingHereNeedsABrokerOrAClock:
    def test_the_module_imports_no_broker_and_no_datetime(self):
        """Purity is the point of Step 3's split: these helpers must be testable
        without a broker or a live clock, so the probe tests DATA assumptions and
        nothing else."""
        src = (Path(__file__).resolve().parents[1]
               / "bots" / "hydra" / "long_strangle_chain.py").read_text()
        for banned in ("ib_client", "BrokerClient", "import requests", "datetime.now"):
            assert banned not in src, f"{banned} leaked into a pure module"

    def test_unverified_assumptions_are_flagged_in_the_source(self):
        """The playbook allows unverified assumptions only if the docstring says
        so. Two are marked: the straddle-as-expected-move proxy, and IV
        percentile having no honest series in the repo today."""
        src = (Path(__file__).resolve().parents[1]
               / "bots" / "hydra" / "long_strangle_chain.py").read_text()
        assert src.count("Unverified assumption") + src.count("unverified") >= 2
