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


def _accel_profile(peak_strike, spot=6800, expiry=date(2026, 5, 4)):
    """A single dominant-OI call strike creates a deterministic accel-zone
    peak at `peak_strike`, with a thin contiguous tail (mirrors the shape of
    test_keep_when_proposed_far_from_accel_peak) so the cluster spans a
    realistic ~60pt range for persistence-gate tests to place `proposed_short`
    and a drifted/matching prior peak within.
    """
    contracts = [_contract(peak_strike, "call", 200000)]
    for offset in range(-30, 35, 5):
        if offset != 0:
            contracts.append(_contract(peak_strike + offset, "call", 2000))
    return build_profile(contracts, spot=spot, expiry=expiry, time_to_expiry=1 / 365.0)


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
        #
        # 2026-09-06: the accel side was ONE contract at 6850, which formed a
        # single-strike cluster. gex_provider.MIN_CLUSTER_STRIKES=2 shipped that
        # day and correctly rejects those (a wall is not one point), so this
        # fixture stopped producing an accel zone at all and the assertion fell
        # through to SHIFT. Widened to 3 contiguous strikes, matching this
        # class's own test_skip_when_inside_accel_zone. The behaviour under
        # test — SKIP taking precedence over SHIFT — is unchanged; only the
        # fixture's realism was at fault. (Real production accel zones are far
        # wider still: the narrowest cited by any of B's 44 live SKIPs was
        # 30pt = 7 strikes.)
        prof = _profile(
            [
                _contract(6845, "call", 100000),  # accel cluster around proposed
                _contract(6850, "call", 100000),
                _contract(6855, "call", 100000),
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


class TestAccelPeakPersistenceCall:
    """2026-08-12: a single noisy GEX read shouldn't be enough to veto an
    entry. These tests exercise the accel_peak_persistence_enabled gate on
    the call side — see AdjusterConfig's docstring for the forensic
    motivation (B/C's Aug 10-12 3-day zero-entry streak)."""

    def test_skip_fires_when_confirmed_by_prior_read_within_tolerance(self):
        prior = _accel_profile(6815, spot=6800)
        current = _accel_profile(6820, spot=6800)  # 5pt drift, within default 10pt tolerance
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.SKIP

    def test_unconfirmed_no_prior_cluster_falls_through_to_keep(self):
        # Prior read had no accel signal anywhere near proposed_short at all.
        prior = _profile([_contract(6500, "put", 50)], spot=6800)
        current = _accel_profile(6820, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.KEEP
        assert "UNCONFIRMED" in r.reason

    def test_unconfirmed_peak_drifted_beyond_tolerance_falls_through_to_keep(self):
        # Prior DOES have a covering cluster, but its peak is 15pt away —
        # beyond the default 10pt tolerance.
        prior = _accel_profile(6805, spot=6800)
        current = _accel_profile(6820, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.KEEP
        assert "UNCONFIRMED" in r.reason
        assert "drift" in r.reason

    def test_unconfirmed_but_decel_wall_falls_through_to_shift_not_keep(self):
        # Proves the design point: an unconfirmed accel veto doesn't force
        # KEEP — it falls through to the existing SHIFT check like any other
        # accel zone that was never in locality range.
        prior = _profile([_contract(6500, "put", 50)], spot=6800)  # no coverage
        contracts = [_contract(6820, "call", 200000)]
        for offset in range(-30, 35, 5):
            if offset != 0:
                contracts.append(_contract(6820 + offset, "call", 2000))
        contracts += [_contract(6870, "put", 100000), _contract(6875, "put", 100000)]
        current = _profile(contracts, spot=6800)
        cfg = AdjusterConfig(
            accel_min_pct=0.01, decel_min_pct=0.01, max_shift_pts=70,
            accel_peak_persistence_enabled=True,
        )
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.SHIFT
        assert "UNCONFIRMED" in r.reason

    def test_skip_fires_unconditionally_when_persistence_disabled_kill_switch(self):
        # Even with a drifted prior passed in, persistence OFF (the default)
        # reproduces today's single-read behavior exactly.
        prior = _accel_profile(6600, spot=6800)  # wildly different peak
        current = _accel_profile(6820, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=False)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.SKIP

    def test_skip_fires_when_no_prior_profile_even_with_persistence_enabled(self):
        # First entry slot of the day / post-restart: no prior read yet.
        # Falls back to today's single-read behavior for this one slot.
        current = _accel_profile(6820, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=None,
        )
        assert r.action == AdjustAction.SKIP

    def test_unconfirmed_when_prior_profile_expiry_differs(self):
        # Matching peak, but a different expiry — must not be trusted as
        # "the same wall", even though the strikes happen to line up.
        prior = _accel_profile(6820, spot=6800, expiry=date(2026, 5, 5))
        current = _accel_profile(6820, spot=6800, expiry=date(2026, 5, 4))
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.KEEP
        assert "UNCONFIRMED" in r.reason

    def test_existing_call_tests_unaffected_by_default_config(self):
        # Regression proof: every existing call-side test constructs
        # AdjusterConfig without touching the new fields and never passes
        # prior_profile — confirm the class-level default is inert.
        assert AdjusterConfig().accel_peak_persistence_enabled is False


class TestAccelPeakPersistencePut:
    """Put-side mirror of TestAccelPeakPersistenceCall — acceleration is
    sign-based (negative_clusters), not side-based, so the same helper
    profiles work; only spot/peak placement flips below spot."""

    def test_skip_fires_when_confirmed_by_prior_read_within_tolerance(self):
        prior = _accel_profile(6775, spot=6800)
        current = _accel_profile(6780, spot=6800)  # 5pt drift, within tolerance
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_put_strike(
            spot=6800, proposed_short=6780, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.SKIP

    def test_unconfirmed_no_prior_cluster_falls_through_to_keep(self):
        prior = _profile([_contract(6900, "call", 50)], spot=6800)
        current = _accel_profile(6780, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_put_strike(
            spot=6800, proposed_short=6780, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.KEEP
        assert "UNCONFIRMED" in r.reason

    def test_unconfirmed_peak_drifted_beyond_tolerance_falls_through_to_keep(self):
        prior = _accel_profile(6795, spot=6800)
        current = _accel_profile(6780, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_put_strike(
            spot=6800, proposed_short=6780, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.KEEP
        assert "UNCONFIRMED" in r.reason

    def test_skip_fires_unconditionally_when_persistence_disabled_kill_switch(self):
        prior = _accel_profile(6600, spot=6800)
        current = _accel_profile(6780, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=False)
        r = adjust_put_strike(
            spot=6800, proposed_short=6780, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.SKIP

    def test_skip_fires_when_no_prior_profile_even_with_persistence_enabled(self):
        current = _accel_profile(6780, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_put_strike(
            spot=6800, proposed_short=6780, profile=current, config=cfg, prior_profile=None,
        )
        assert r.action == AdjustAction.SKIP

    def test_unconfirmed_but_decel_wall_falls_through_to_shift_not_keep(self):
        # Put-side mirror of the call-side test with the same name.
        prior = _profile([_contract(6900, "call", 50)], spot=6800)  # no coverage
        contracts = [_contract(6780, "call", 200000)]
        for offset in range(-30, 35, 5):
            if offset != 0:
                contracts.append(_contract(6780 + offset, "call", 2000))
        contracts += [_contract(6725, "put", 100000), _contract(6730, "put", 100000)]
        current = _profile(contracts, spot=6800)
        cfg = AdjusterConfig(
            accel_min_pct=0.01, decel_min_pct=0.01, max_shift_pts=70,
            accel_peak_persistence_enabled=True,
        )
        r = adjust_put_strike(
            spot=6800, proposed_short=6780, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.SHIFT
        assert "UNCONFIRMED" in r.reason

    def test_unconfirmed_when_prior_profile_expiry_differs(self):
        # Put-side mirror of the call-side test with the same name.
        prior = _accel_profile(6780, spot=6800, expiry=date(2026, 5, 5))
        current = _accel_profile(6780, spot=6800, expiry=date(2026, 5, 4))
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_put_strike(
            spot=6800, proposed_short=6780, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.KEEP
        assert "UNCONFIRMED" in r.reason


class TestAccelPeakForceUnconfirmed:
    """2026-08-12 round-2 review fix: `force_unconfirmed` is a distinct
    signal from `prior_profile=None` — the caller uses it when the only
    "prior" available IS the current read itself (a same-profile entry
    retry or stale-fallback reuse), so no NEW independent confirmation has
    happened. This must fall through to KEEP/SHIFT like an unconfirmed
    accel zone, NOT collapse to the "no prior ever" unconditional-SKIP path
    — otherwise a retried entry would silently revert to pre-persistence-gate
    behavior while looking identical in the logs to a genuine decision."""

    def test_force_unconfirmed_falls_through_to_keep_with_no_prior_profile(self):
        current = _accel_profile(6820, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg,
            prior_profile=None, force_unconfirmed=True,
        )
        assert r.action == AdjustAction.KEEP
        assert "UNCONFIRMED" in r.reason
        assert "no new independent confirmation" in r.reason

    def test_force_unconfirmed_falls_through_to_shift_when_decel_wall_present(self):
        contracts = [_contract(6820, "call", 200000)]
        for offset in range(-30, 35, 5):
            if offset != 0:
                contracts.append(_contract(6820 + offset, "call", 2000))
        contracts += [_contract(6870, "put", 100000), _contract(6875, "put", 100000)]
        current = _profile(contracts, spot=6800)
        cfg = AdjusterConfig(
            accel_min_pct=0.01, decel_min_pct=0.01, max_shift_pts=70,
            accel_peak_persistence_enabled=True,
        )
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg,
            prior_profile=None, force_unconfirmed=True,
        )
        assert r.action == AdjustAction.SHIFT
        assert "UNCONFIRMED" in r.reason

    def test_force_unconfirmed_ignored_when_persistence_disabled(self):
        # Kill switch still wins — force_unconfirmed must not matter if the
        # feature itself is off.
        current = _accel_profile(6820, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=False)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg,
            prior_profile=None, force_unconfirmed=True,
        )
        assert r.action == AdjustAction.SKIP

    def test_put_side_force_unconfirmed_falls_through_to_keep(self):
        current = _accel_profile(6780, spot=6800)
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_put_strike(
            spot=6800, proposed_short=6780, profile=current, config=cfg,
            prior_profile=None, force_unconfirmed=True,
        )
        assert r.action == AdjustAction.KEEP
        assert "UNCONFIRMED" in r.reason


class TestAccelPeakPersistenceToleranceBoundary:
    """<= semantics on accel_peak_persistence_tolerance_pts: a drift exactly
    equal to the tolerance must still count as confirmed (not just strictly
    less than)."""

    def test_skip_fires_when_drift_exactly_equals_tolerance(self):
        prior = _accel_profile(6810, spot=6800)
        current = _accel_profile(6820, spot=6800)  # drift = 10 = default tolerance
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.SKIP

    def test_keep_when_drift_one_point_past_tolerance(self):
        prior = _accel_profile(6809, spot=6800)
        current = _accel_profile(6820, spot=6800)  # drift = 11 > default 10pt tolerance
        cfg = AdjusterConfig(accel_min_pct=0.01, accel_peak_persistence_enabled=True)
        r = adjust_call_strike(
            spot=6800, proposed_short=6820, profile=current, config=cfg, prior_profile=prior,
        )
        assert r.action == AdjustAction.KEEP
