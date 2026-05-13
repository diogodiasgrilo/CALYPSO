"""Tests for bots.hydra.brandon.gex_strike_adjuster."""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.gex_provider import build_profile
from bots.hydra.brandon.gex_strike_adjuster import (
    AdjustAction,
    AdjusterConfig,
    adjust_call_strike,
    adjust_put_strike,
)


def _contract(strike, ctype, oi, gamma=0.001):
    return {
        "details": {"strike_price": strike, "contract_type": ctype},
        "open_interest": oi,
        "greeks": {"gamma": gamma},
    }


def _profile(contracts, spot=6800):
    return build_profile(
        contracts, spot=spot, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0
    )


class TestAdjustCallStrike:
    def test_keep_when_no_signals(self):
        # Empty/quiet GEX profile around proposed
        prof = _profile([_contract(6500, "put", 50)])
        r = adjust_call_strike(spot=6800, proposed_short=6850, profile=prof)
        assert r.action == AdjustAction.KEEP

    def test_skip_when_inside_accel_zone(self):
        # Big call OI just above spot → negative cluster (accel zone) under
        # SpotGamma convention — overlaps proposed call short.
        prof = _profile(
            [
                _contract(6840, "call", 50000),
                _contract(6850, "call", 50000),
                _contract(6860, "call", 50000),
            ],
            spot=6800,
        )
        r = adjust_call_strike(spot=6800, proposed_short=6850, profile=prof)
        assert r.action == AdjustAction.SKIP
        assert "accel-zone peak" in r.reason

    def test_shift_to_capture_decel_wall_above(self):
        # Big put OI cluster at 6870-6880 → positive cluster (decel wall) above
        # proposed 6850. Adjuster should shift OUT to ~6885.
        prof = _profile(
            [
                _contract(6870, "put", 80000),
                _contract(6875, "put", 80000),
                _contract(6880, "put", 80000),
            ],
            spot=6800,
        )
        r = adjust_call_strike(
            spot=6800,
            proposed_short=6850,
            profile=prof,
            config=AdjusterConfig(decel_min_pct=0.01, max_shift_pts=50),
        )
        assert r.action == AdjustAction.SHIFT
        assert r.new_strike == 6885

    def test_shift_capped_by_max_shift(self):
        # Wall (put OI) far away (60pt past proposed) — exceeds default 25pt cap → KEEP
        prof = _profile(
            [_contract(6920, "put", 80000), _contract(6925, "put", 80000)],
            spot=6800,
        )
        r = adjust_call_strike(
            spot=6800, proposed_short=6850, profile=prof,
            config=AdjusterConfig(decel_min_pct=0.01, max_shift_pts=25),
        )
        assert r.action == AdjustAction.KEEP

    def test_call_short_below_spot_caller_bug_returns_keep(self):
        # Defensive: caller passed a put-side strike to the call adjuster.
        prof = _profile([_contract(6850, "call", 100)], spot=6800)
        r = adjust_call_strike(spot=6800, proposed_short=6750, profile=prof)
        assert r.action == AdjustAction.KEEP
        assert "below spot" in r.reason

    def test_skip_takes_precedence_over_shift(self):
        # An accel zone (call OI) overlapping proposed AND a decel wall (put OI)
        # further OTM → skip wins (don't shift past an accel zone).
        prof = _profile(
            [
                _contract(6850, "call", 100000),  # accel cluster around proposed
                _contract(6900, "put", 100000),   # decel wall further OTM
                _contract(6905, "put", 100000),
            ],
            spot=6800,
        )
        r = adjust_call_strike(
            spot=6800, proposed_short=6850, profile=prof,
            config=AdjusterConfig(accel_min_pct=0.05, decel_min_pct=0.05, max_shift_pts=100),
        )
        assert r.action == AdjustAction.SKIP

    def test_keep_when_proposed_far_from_accel_peak(self):
        # Reproduces 2026-05-04..05-12 B/C pathology: the SpotGamma sign
        # convention makes the entire call wing one giant negative cluster.
        # Pre-peak-locality, ANY call short inside that broad band was SKIP'd
        # — driving B to 77% put-only across 5/4-5/12. Peak-locality fixes
        # this: a strike 40pt away from the cluster's |GEX| peak is no longer
        # SKIP'd, even though it lies inside the broad contiguous run.
        # Setup: huge call OI at the ATM-adjacent peak (6810), thin tail to
        # 6900 — one contiguous negative cluster but its peak is at 6810.
        contracts = [_contract(6810, "call", 200000)]
        for k in range(6820, 6905, 10):
            contracts.append(_contract(k, "call", 5000))
        prof = _profile(contracts, spot=6800)
        # Proposed call short 6850 is 40pt off-peak — should KEEP under the
        # 25pt default locality, despite still being inside the broad cluster.
        r = adjust_call_strike(
            spot=6800, proposed_short=6850, profile=prof,
            config=AdjusterConfig(accel_min_pct=0.01),
        )
        assert r.action == AdjustAction.KEEP

    def test_skip_still_fires_when_proposed_near_accel_peak(self):
        # Inverse of the above: a short at the cluster's peak (within 25pt)
        # SHOULD still be SKIP'd. This proves the new gate doesn't disable
        # accel-zone protection — it just localizes it.
        contracts = [_contract(6810, "call", 200000)]
        for k in range(6820, 6905, 10):
            contracts.append(_contract(k, "call", 5000))
        prof = _profile(contracts, spot=6800)
        r = adjust_call_strike(
            spot=6800, proposed_short=6815, profile=prof,
            config=AdjusterConfig(accel_min_pct=0.01),
        )
        assert r.action == AdjustAction.SKIP
        assert "peak" in r.reason


class TestAdjustPutStrike:
    def test_keep_when_no_signals(self):
        prof = _profile([_contract(6900, "call", 50)])
        r = adjust_put_strike(spot=6800, proposed_short=6750, profile=prof)
        assert r.action == AdjustAction.KEEP

    def test_skip_when_inside_accel_zone(self):
        # Big CALL OI below spot → negative cluster (accel zone) under SpotGamma
        # convention — overlaps proposed put short. (Call OI below spot is rare
        # in real flow but valid for testing the math.)
        prof = _profile(
            [
                _contract(6740, "call", 50000),
                _contract(6750, "call", 50000),
                _contract(6760, "call", 50000),
            ],
            spot=6800,
        )
        r = adjust_put_strike(spot=6800, proposed_short=6750, profile=prof)
        assert r.action == AdjustAction.SKIP
        assert "accel-zone peak" in r.reason

    def test_shift_to_capture_decel_wall_below(self):
        # Big PUT OI cluster below proposed put short → positive cluster
        # (decel wall) under SpotGamma convention. Adjuster shifts wing out.
        prof = _profile(
            [
                _contract(6720, "put", 80000),
                _contract(6725, "put", 80000),
                _contract(6730, "put", 80000),
            ],
            spot=6800,
        )
        r = adjust_put_strike(
            spot=6800, proposed_short=6750, profile=prof,
            config=AdjusterConfig(decel_min_pct=0.01, max_shift_pts=50),
        )
        assert r.action == AdjustAction.SHIFT
        # Wall low = 6720, buffer = 5, shift target = 6720 - 5 = 6715
        assert r.new_strike == 6715
        assert r.new_strike < 6750

    def test_shift_capped_by_max_shift(self):
        prof = _profile(
            [_contract(6680, "put", 80000), _contract(6685, "put", 80000)],
            spot=6800,
        )
        r = adjust_put_strike(
            spot=6800, proposed_short=6750, profile=prof,
            config=AdjusterConfig(decel_min_pct=0.01, max_shift_pts=25),
        )
        assert r.action == AdjustAction.KEEP

    def test_put_short_above_spot_caller_bug_returns_keep(self):
        prof = _profile([_contract(6750, "put", 100)], spot=6800)
        r = adjust_put_strike(spot=6800, proposed_short=6850, profile=prof)
        assert r.action == AdjustAction.KEEP
        assert "above spot" in r.reason


class TestSnapping:
    def test_shift_target_snapped_to_5pt_grid(self):
        # Decel wall (puts above spot) high = 6878 → buffer 5 → 6883 → snap to 6885
        prof = _profile(
            [_contract(6875, "put", 100000), _contract(6878, "put", 100000)],
            spot=6800,
        )
        r = adjust_call_strike(
            spot=6800, proposed_short=6850, profile=prof,
            config=AdjusterConfig(decel_min_pct=0.01, max_shift_pts=50),
        )
        assert r.action == AdjustAction.SHIFT
        # 6883 rounds to 6885
        assert r.new_strike == 6885
        assert r.new_strike % 5 == 0


class TestSymmetry:
    def test_call_and_put_mirror_on_keep(self):
        # Profile with no actionable signal — both sides should KEEP
        prof = _profile([_contract(6800, "call", 10)])
        r_call = adjust_call_strike(spot=6800, proposed_short=6850, profile=prof)
        r_put = adjust_put_strike(spot=6800, proposed_short=6750, profile=prof)
        assert r_call.action == AdjustAction.KEEP
        assert r_put.action == AdjustAction.KEEP
