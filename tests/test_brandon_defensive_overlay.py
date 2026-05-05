"""Tests for bots.hydra.brandon.defensive_overlay."""

import os
import sys
from datetime import date, datetime, time, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.defensive_overlay import (
    OverlayConfig,
    OverlayLeg,
    OverlayStructure,
    evaluate_overlay,
)
from bots.hydra.brandon.gex_provider import build_profile


def _contract(strike, ctype, oi, gamma=0.001):
    return {
        "details": {"strike_price": strike, "contract_type": ctype},
        "open_interest": oi,
        "greeks": {"gamma": gamma},
    }


def _profile_with_call_accel(spot=6800):
    """Profile with strong negative-GEX cluster ABOVE spot (call-side accel).

    Under SpotGamma convention, calls negate to negative GEX, so call OI
    above spot is exactly an accel zone on the call side.
    """
    return build_profile(
        [
            _contract(6840, "call", 50000),
            _contract(6850, "call", 50000),
            _contract(6860, "call", 50000),
        ],
        spot=spot,
        expiry=date(2026, 5, 4),
        time_to_expiry=1 / 365.0,
    )


def _profile_with_put_accel(spot=6800):
    """Profile with strong negative-GEX cluster BELOW spot (put-side accel).

    Call OI below spot creates negative GEX (rare in practice, but valid for
    the test) — exactly the accel pattern on the put side.
    """
    return build_profile(
        [
            _contract(6740, "call", 50000),
            _contract(6750, "call", 50000),
            _contract(6760, "call", 50000),
        ],
        spot=spot,
        expiry=date(2026, 5, 4),
        time_to_expiry=1 / 365.0,
    )


def _profile_quiet():
    """No significant clusters."""
    return build_profile(
        [_contract(6500, "put", 100)],
        spot=6800,
        expiry=date(2026, 5, 4),
        time_to_expiry=1 / 365.0,
    )


MORNING = datetime(2026, 5, 4, 11, 0, tzinfo=timezone.utc)  # 11:00
AFTERNOON = datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc)  # 14:00


class TestTriggerConditions:
    def test_no_proposal_when_far_from_short(self):
        # SPX 6800, short call at 6900 → 100pt away, beyond default 25pt trigger
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6800,
            short_strike=6900,
            long_strike=6910,
            now_et=MORNING,
            profile=_profile_with_call_accel(),
        )
        assert p is None

    def test_no_proposal_when_gex_required_but_quiet(self):
        # Within distance, but no accel zone
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=MORNING,
            profile=_profile_quiet(),
        )
        assert p is None

    def test_proposal_without_gex_when_confirmation_disabled(self):
        # Distance trigger only — profile=None, config.require_gex_confirmation=False
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=MORNING,
            config=OverlayConfig(require_gex_confirmation=False),
            profile=None,
        )
        assert p is not None
        assert p.structure == OverlayStructure.DEBIT_SPREAD

    def test_invalid_side_returns_none(self):
        p = evaluate_overlay(
            threatened_side="bogus",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=MORNING,
        )
        assert p is None

    def test_call_side_short_below_spot_returns_none(self):
        # Caller bug — call short below spot makes no sense
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6800,
            long_strike=6810,
            now_et=MORNING,
        )
        assert p is None

    def test_put_side_short_above_spot_returns_none(self):
        p = evaluate_overlay(
            threatened_side="put",
            spot_now=6750,
            short_strike=6800,
            long_strike=6790,
            now_et=MORNING,
        )
        assert p is None


class TestDebitSpreadProposal:
    def test_morning_call_threat_proposes_debit_spread(self):
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=MORNING,
            profile=_profile_with_call_accel(),
        )
        assert p is not None
        assert p.structure == OverlayStructure.DEBIT_SPREAD
        assert p.threatened_side == "call"
        assert len(p.legs) == 2

    def test_call_debit_spread_above_credit_spread(self):
        # Credit spread: short 6840, long 6850 (10-wide)
        # Debit spread should be: long 6850, short 6860 (one width above)
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=MORNING,
            profile=_profile_with_call_accel(),
        )
        long_leg = next(l for l in p.legs if l.side == "long")
        short_leg = next(l for l in p.legs if l.side == "short")
        assert long_leg.strike == 6850
        assert short_leg.strike == 6860
        assert long_leg.contract_type == "call"
        assert short_leg.contract_type == "call"

    def test_put_debit_spread_below_credit_spread(self):
        # Credit spread: short 6760, long 6750 (10-wide put)
        # Debit spread should be: long 6750, short 6740
        p = evaluate_overlay(
            threatened_side="put",
            spot_now=6780,
            short_strike=6760,
            long_strike=6750,
            now_et=MORNING,
            profile=_profile_with_put_accel(),
        )
        assert p is not None
        long_leg = next(l for l in p.legs if l.side == "long")
        short_leg = next(l for l in p.legs if l.side == "short")
        assert long_leg.strike == 6750
        assert short_leg.strike == 6740

    def test_debit_spread_quantity_matches_config(self):
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=MORNING,
            config=OverlayConfig(contracts=3),
            profile=_profile_with_call_accel(),
        )
        for leg in p.legs:
            assert leg.quantity == 3


class TestButterflyProposal:
    def test_afternoon_call_threat_proposes_butterfly(self):
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=AFTERNOON,
            profile=_profile_with_call_accel(),
        )
        assert p is not None
        assert p.structure == OverlayStructure.BUTTERFLY
        assert len(p.legs) == 3

    def test_butterfly_legs_form_long_short_short_long_pattern(self):
        # 1× lower, 2× pin, 1× upper
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=AFTERNOON,
            profile=_profile_with_call_accel(),
            config=OverlayConfig(butterfly_width_pts=10),
        )
        long_legs = [l for l in p.legs if l.side == "long"]
        short_legs = [l for l in p.legs if l.side == "short"]
        assert len(long_legs) == 2
        assert len(short_legs) == 1
        # Quantities: longs=1 each, short=2× total
        assert all(l.quantity == 1 for l in long_legs)
        assert short_legs[0].quantity == 2

    def test_butterfly_pin_targets_decel_wall_when_present(self):
        # Accel zone (call OI → negative GEX) overlapping the call short triggers
        # the overlay; decel wall (put OI → positive GEX) at 6870-6880 sets the pin.
        contracts = [
            _contract(6840, "call", 50000),
            _contract(6850, "call", 50000),
            _contract(6870, "put", 80000),
            _contract(6875, "put", 80000),
            _contract(6880, "put", 80000),
        ]
        prof = build_profile(
            contracts, spot=6820, expiry=date(2026, 5, 4), time_to_expiry=1 / 365.0
        )
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=AFTERNOON,
            profile=prof,
        )
        assert p is not None
        assert p.pin_strike == 6875  # midpoint of cluster

    def test_butterfly_falls_back_to_spot_when_no_wall(self):
        # GEX required for trigger via accel zone, but no decel wall on threatened side
        # → pin = spot
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=AFTERNOON,
            profile=_profile_with_call_accel(),  # accel only, no decel wall above
        )
        assert p.pin_strike == 6820  # snapped to grid (already there)

    def test_butterfly_width_from_config(self):
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=AFTERNOON,
            profile=_profile_with_call_accel(),
            config=OverlayConfig(butterfly_width_pts=15),
        )
        strikes = sorted([l.strike for l in p.legs])
        # spread: lower=pin-15, pin, upper=pin+15
        assert strikes[2] - strikes[1] == 15
        assert strikes[1] - strikes[0] == 15


class TestTimeBoundary:
    def test_exactly_at_cutoff_is_butterfly(self):
        # 12:30:00 ET → butterfly (cutoff is inclusive of >=)
        at_cutoff = datetime(2026, 5, 4, 12, 30, tzinfo=timezone.utc)
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=at_cutoff,
            profile=_profile_with_call_accel(),
        )
        assert p.structure == OverlayStructure.BUTTERFLY

    def test_just_before_cutoff_is_debit(self):
        before = datetime(2026, 5, 4, 12, 29, 59, tzinfo=timezone.utc)
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=before,
            profile=_profile_with_call_accel(),
        )
        assert p.structure == OverlayStructure.DEBIT_SPREAD

    def test_custom_cutoff_time_respected(self):
        # 11:00 with cutoff=10:30 → butterfly
        p = evaluate_overlay(
            threatened_side="call",
            spot_now=6820,
            short_strike=6840,
            long_strike=6850,
            now_et=MORNING,
            config=OverlayConfig(butterfly_cutoff=time(10, 30)),
            profile=_profile_with_call_accel(),
        )
        assert p.structure == OverlayStructure.BUTTERFLY
