"""StrangleStrategy tests (modularity driver, S-series).

Pins the strangle's 2-leg strike selection and that it's wired as an
undefined-risk strategy (requires_protective_wings=False) that's still a
concrete, instantiable strategy via the item-4b contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strangle_strategy import StrangleStrategy
from bots.hydra.strategy import HydraIronCondorEntry


def _strat(spx, vix, *, target_delta=8, increment=5):
    s = StrangleStrategy.__new__(StrangleStrategy)
    s.current_price = spx
    s.current_vix = vix
    s.target_delta = target_delta
    s.strike_increment = increment
    return s


class TestContract:
    def test_is_undefined_risk(self):
        assert StrangleStrategy.requires_protective_wings is False

    def test_is_concrete_strategy(self):
        # Inherits HydraStrategy's hook implementations → no abstract methods left.
        assert StrangleStrategy.__abstractmethods__ == frozenset()


class TestStrikeSelection:
    def test_two_naked_shorts_no_wings(self):
        s = _strat(7465.0, 18.0)
        e = HydraIronCondorEntry(entry_number=1)
        assert s._calculate_strikes(e) is True
        assert e.short_call_strike > 7465.0
        assert e.short_put_strike < 7465.0
        assert e.long_call_strike == 0.0
        assert e.long_put_strike == 0.0

    def test_symmetric_otm(self):
        s = _strat(7465.0, 18.0)
        e = HydraIronCondorEntry(entry_number=1)
        s._calculate_strikes(e)
        # 7465 is on the 5-grid → symmetric distance on both sides.
        assert (e.short_call_strike - 7465.0) == (7465.0 - e.short_put_strike)

    def test_strikes_on_configured_grid(self):
        s = _strat(7463.0, 18.0, increment=10)
        e = HydraIronCondorEntry(entry_number=1)
        s._calculate_strikes(e)
        assert e.short_call_strike % 10 == 0
        assert e.short_put_strike % 10 == 0

    def test_higher_vix_pushes_strikes_further_otm(self):
        e_low = HydraIronCondorEntry(entry_number=1)
        e_high = HydraIronCondorEntry(entry_number=2)
        _strat(7465.0, 14.0)._calculate_strikes(e_low)
        _strat(7465.0, 28.0)._calculate_strikes(e_high)
        # Higher VIX → wider OTM → short call further above spot.
        assert e_high.short_call_strike >= e_low.short_call_strike

    def test_no_price_returns_false(self):
        s = _strat(0.0, 18.0)
        assert s._calculate_strikes(HydraIronCondorEntry(entry_number=1)) is False
