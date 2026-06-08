"""Unit tests for the first-class Leg model (modularity-audit item 1, commit 1).

These pin the substrate the property bridge will sit on. Pure-stdlib import —
no broker/strategy stack — so they run fast and in isolation.
"""

from bots.hydra.leg import LEG_NAMES, LEG_PROPS, Leg, _new_legset


class TestLegDefaults:
    def test_required_fields(self):
        leg = Leg("short", "call")
        assert leg.side == "short"
        assert leg.right == "call"

    def test_numeric_defaults_are_zero(self):
        leg = Leg("short", "call")
        assert leg.strike == 0.0
        assert leg.price == 0.0
        assert leg.fill_price == 0.0
        assert leg.mid_at_fill == 0.0

    def test_id_defaults_are_none(self):
        leg = Leg("short", "call")
        assert leg.position_id is None
        assert leg.uic is None

    def test_fields_are_mutable(self):
        # IC legs are re-struck / filled / re-priced in place over their lifetime.
        leg = Leg("short", "call")
        leg.strike = 7520.0
        leg.uic = 416904
        leg.fill_price = 1.25
        assert leg.strike == 7520.0
        assert leg.uic == 416904
        assert leg.fill_price == 1.25


class TestLegName:
    def test_name_composition(self):
        assert Leg("short", "call").name == "short_call"
        assert Leg("long", "call").name == "long_call"
        assert Leg("short", "put").name == "short_put"
        assert Leg("long", "put").name == "long_put"

    def test_name_matches_every_canonical_leg(self):
        # name = f"{side}_{right}" must reproduce exactly the canonical LEG_NAMES.
        produced = {
            Leg(name.split("_")[0], name.split("_")[1]).name for name in LEG_NAMES
        }
        assert produced == set(LEG_NAMES)


class TestLegConstants:
    def test_leg_names_content_and_order(self):
        assert LEG_NAMES == ("short_call", "long_call", "short_put", "long_put")

    def test_leg_props_match_leg_dataclass_fields(self):
        # The bridge maps {leg}_{prop} -> legs[leg].{prop} for exactly these props;
        # every prop must be a real, settable attribute on a Leg.
        leg = Leg("short", "call")
        for prop in LEG_PROPS:
            assert hasattr(leg, prop), prop
            setattr(leg, prop, 1)  # settable
        assert set(LEG_PROPS) == {
            "strike", "position_id", "uic", "price", "fill_price", "mid_at_fill"
        }


class TestNewLegSet:
    def test_has_all_four_canonical_legs(self):
        legs = _new_legset()
        assert set(legs.keys()) == set(LEG_NAMES)
        assert len(legs) == 4

    def test_key_matches_leg_identity(self):
        # Each dict key must equal the leg's own canonical name.
        for key, leg in _new_legset().items():
            assert leg.name == key

    def test_legs_are_independent_instances(self):
        # Two entries must never share leg state: mutating one set's leg must not
        # leak into a freshly built set, and the four legs must be distinct objects.
        a = _new_legset()
        b = _new_legset()
        a["short_call"].strike = 7520.0
        assert b["short_call"].strike == 0.0
        assert len({id(leg) for leg in a.values()}) == 4

    def test_fresh_legs_carry_defaults(self):
        for leg in _new_legset().values():
            assert leg.strike == 0.0
            assert leg.uic is None
            assert leg.position_id is None


class TestLegCountAgnostic:
    """The substrate must describe arbitrary leg counts — the whole point of item 1."""

    def test_two_leg_strangle_shape(self):
        strangle = {
            "short_call": Leg("short", "call", strike=7600.0),
            "short_put": Leg("short", "put", strike=7300.0),
        }
        assert len(strangle) == 2
        assert all(leg.name == key for key, leg in strangle.items())

    def test_three_leg_butterfly_shape(self):
        butterfly = [
            Leg("long", "put", strike=7300.0),
            Leg("short", "put", strike=7400.0),
            Leg("short", "put", strike=7400.0),
        ]
        assert len(butterfly) == 3
        assert {leg.right for leg in butterfly} == {"put"}
