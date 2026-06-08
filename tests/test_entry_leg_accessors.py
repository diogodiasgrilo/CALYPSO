"""G3 — the property-bridge guarantee (item 1, commit 3).

After IronCondorEntry's 24 flat leg fields became @property bridges over a
`legs` dict of Leg objects, these tests pin that the bridge is transparent:
read, write-through, and dynamic getattr/setattr all resolve to the leg, and
every derived property that reads leg fields still computes correctly. If any
of these break, ~1,011 flat leg-name references across the strategy are at risk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import IronCondorEntry
from bots.hydra.leg import LEG_NAMES, LEG_PROPS, Leg, _new_legset
from bots.hydra.strategy import HydraIronCondorEntry

# distinct, type-correct probe value per leg-scoped prop
_PROBE = {
    "strike": 7000.0,
    "position_id": "PID-x",
    "uic": 999,
    "price": 1.5,
    "fill_price": 1.25,
    "mid_at_fill": 1.30,
}
_ALL_NAMES = [f"{leg}_{prop}" for leg in LEG_NAMES for prop in LEG_PROPS]


class TestBridgeEquivalence:
    def test_legs_is_a_dataclass_field_and_flat_fields_are_not(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(IronCondorEntry)}
        assert "legs" in fields
        for name in _ALL_NAMES:
            assert name not in fields, f"{name} should be a property, not a dataclass field"

    @pytest.mark.parametrize("leg,prop", [(l, p) for l in LEG_NAMES for p in LEG_PROPS])
    def test_read_reflects_leg(self, leg, prop):
        e = IronCondorEntry(entry_number=1)
        setattr(e.legs[leg], prop, _PROBE[prop])
        assert getattr(e, f"{leg}_{prop}") == _PROBE[prop]

    @pytest.mark.parametrize("leg,prop", [(l, p) for l in LEG_NAMES for p in LEG_PROPS])
    def test_write_through_mutates_leg(self, leg, prop):
        e = IronCondorEntry(entry_number=1)
        setattr(e, f"{leg}_{prop}", _PROBE[prop])
        assert getattr(e.legs[leg], prop) == _PROBE[prop]

    def test_dynamic_getattr_setattr_routes_to_leg(self):
        # The live position-clearing path: setattr(entry, f"{leg}_uic", None).
        e = IronCondorEntry(entry_number=1)
        e.legs["short_call"].uic = 416904
        assert getattr(e, "short_call_uic") == 416904
        setattr(e, "short_call_uic", None)
        assert e.legs["short_call"].uic is None
        assert getattr(e, "short_call_uic") is None

    def test_defaults_match_leg_defaults(self):
        e = IronCondorEntry(entry_number=1)
        assert e.short_call_strike == 0.0
        assert e.short_call_uic is None
        assert e.short_call_position_id is None

    def test_entries_do_not_share_leg_state(self):
        a = IronCondorEntry(entry_number=1)
        b = IronCondorEntry(entry_number=2)
        a.short_call_strike = 7520.0
        assert b.short_call_strike == 0.0
        assert a.legs is not b.legs


class TestDerivedPropertiesThroughBridge:
    def test_spread_width_from_strikes(self):
        e = IronCondorEntry(entry_number=1)
        e.short_call_strike, e.long_call_strike = 7520.0, 7595.0  # 75 wide
        e.short_put_strike, e.long_put_strike = 7350.0, 7250.0    # 100 wide
        assert e.spread_width == 100.0  # max of the two

    def test_spread_value_scales_with_contracts(self):
        e = IronCondorEntry(entry_number=1)
        e.short_call_price, e.long_call_price = 2.0, 0.5
        e.contracts = 3
        assert e.call_spread_value == (2.0 - 0.5) * 100 * 3

    def test_all_position_ids_filters_none(self):
        e = IronCondorEntry(entry_number=1)
        e.short_call_position_id = "A"
        e.long_put_position_id = "B"
        # long_call / short_put left None
        assert e.all_position_ids == ["A", "B"]

    def test_unrealized_pnl_both_open(self):
        e = IronCondorEntry(entry_number=1)
        e.call_spread_credit, e.put_spread_credit = 85.0, 105.0
        e.short_call_price, e.long_call_price = 0.30, 0.10  # call_spread_value = 20
        e.short_put_price, e.long_put_price = 0.40, 0.15    # put_spread_value = 25
        assert e.unrealized_pnl == e.total_credit - e.call_spread_value - e.put_spread_value


class TestHydraSubclassThroughBridge:
    """The bridge lives on the base; HydraIronCondorEntry must inherit it intact."""

    def test_hydra_entry_read_write(self):
        e = HydraIronCondorEntry(entry_number=1)
        e.short_call_strike = 7520.0
        assert e.legs["short_call"].strike == 7520.0
        assert e.short_call_strike == 7520.0

    def test_hydra_total_credit_override_call_only(self):
        e = HydraIronCondorEntry(entry_number=1)
        e.call_spread_credit, e.put_spread_credit = 85.0, 105.0
        e.call_only = True
        assert e.is_one_sided is True
        assert e.total_credit == 85.0  # one-sided: only the placed side

    def test_hydra_total_credit_full_ic(self):
        e = HydraIronCondorEntry(entry_number=1)
        e.call_spread_credit, e.put_spread_credit = 85.0, 105.0
        assert e.is_one_sided is False
        assert e.total_credit == 190.0


class TestStrikeAdjusterStyleMutation:
    """Brandon's GEX adjuster mutates *_strike in place; pin that it lands."""

    def test_in_place_strike_mutation_visible_both_ways(self):
        e = HydraIronCondorEntry(entry_number=1)
        e.short_call_strike = 7500.0
        e.short_call_strike += 25  # adjuster-style in-place shift
        assert e.short_call_strike == 7525.0
        assert e.legs["short_call"].strike == 7525.0
