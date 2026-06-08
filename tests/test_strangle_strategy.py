"""StrangleStrategy tests (modularity driver, S-series).

Pins the strangle's 2-leg strike selection and that it's wired as an
undefined-risk strategy (requires_protective_wings=False) that's still a
concrete, instantiable strategy via the item-4b contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unittest.mock import MagicMock

from bots.hydra.order_types import BuySell
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


class TestSimulateEntry:
    def _strat(self):
        s = StrangleStrategy.__new__(StrangleStrategy)
        s.contracts_per_entry = 1
        s._get_todays_expiry = lambda: "2026-06-08"
        s._get_option_uic = lambda strike, right, expiry: 111 if right == "Call" else 222
        s._read_option_quote = lambda uic: {"mid": 1.5}
        return s

    def test_books_two_naked_shorts(self):
        s = self._strat()
        e = HydraIronCondorEntry(entry_number=1)
        e.short_call_strike, e.short_put_strike = 7600.0, 7300.0
        assert s._simulate_entry(e) is True
        assert e.short_call_uic == 111 and e.short_put_uic == 222
        # premium 1.5 × 100 × 1 contract per side
        assert e.call_spread_credit == 150.0 and e.put_spread_credit == 150.0
        assert e.total_credit == 300.0
        assert e.is_complete is True

    def test_dry_ids_assigned_to_shorts_only(self):
        s = self._strat()
        e = HydraIronCondorEntry(entry_number=1)
        e.short_call_strike, e.short_put_strike = 7600.0, 7300.0
        s._simulate_entry(e)
        assert e.short_call_position_id.startswith("DRY_") and e.short_call_position_id.endswith("_SC")
        assert e.short_put_position_id.startswith("DRY_") and e.short_put_position_id.endswith("_SP")
        # no long legs
        assert e.long_call_position_id is None and e.long_put_position_id is None

    def test_unrealized_pnl_is_naked_short_shaped(self):
        # With longs priced 0, the IC entry model collapses to the strangle's
        # economics: pnl = credit - cost_to_buy_back_the_two_shorts.
        s = self._strat()
        e = HydraIronCondorEntry(entry_number=1)
        e.short_call_strike, e.short_put_strike = 7600.0, 7300.0
        s._simulate_entry(e)
        # shorts now cost 0.80 each to close (price dropped from 1.5)
        e.short_call_price, e.short_put_price = 0.80, 0.80
        # credit 300 - (0.80+0.80)*100 = 300 - 160 = 140
        assert e.unrealized_pnl == 140.0

    def test_no_expiry_returns_false(self):
        s = self._strat()
        s._get_todays_expiry = lambda: None
        assert s._simulate_entry(HydraIronCondorEntry(entry_number=1)) is False


class TestStopLevels:
    def _strat(self):
        s = StrangleStrategy.__new__(StrangleStrategy)
        s.contracts_per_entry = 1
        s.call_stop_buffer = 75.0
        s.put_stop_buffer = 175.0
        return s

    def test_per_side_credit_basis(self):
        # Each naked leg stops on ITS OWN credit + buffer — NOT total credit.
        s = self._strat()
        e = HydraIronCondorEntry(entry_number=1)
        e.call_spread_credit = 150.0
        e.put_spread_credit = 200.0
        s._calculate_stop_levels_hydra(e)
        assert e.call_side_stop == 150.0 + 75.0   # per-side call
        assert e.put_side_stop == 200.0 + 175.0   # per-side put
        # Contrast: the IC base would stop BOTH sides on total credit (350 + buf).
        # The strangle deliberately does not — proving the per-strategy exit policy.

    def test_min_stop_floor_applies_per_side(self):
        s = self._strat()
        e = HydraIronCondorEntry(entry_number=1)
        e.call_spread_credit = 10.0   # below the 50 floor
        e.put_spread_credit = 200.0
        s._calculate_stop_levels_hydra(e)
        assert e.call_side_stop == 50.0 + 75.0    # floored to 50
        assert e.put_side_stop == 200.0 + 175.0

    def test_contracts_scale_buffer_and_floor(self):
        s = self._strat()
        s.contracts_per_entry = 2
        e = HydraIronCondorEntry(entry_number=1)
        e.call_spread_credit = 300.0
        e.put_spread_credit = 400.0
        s._calculate_stop_levels_hydra(e)
        assert e.call_side_stop == 300.0 + 75.0 * 2
        assert e.put_side_stop == 400.0 + 175.0 * 2


class TestExecuteEntry:
    def _strat(self):
        s = StrangleStrategy.__new__(StrangleStrategy)
        s._get_todays_expiry = lambda: "2026-06-08"
        s._register_position = lambda entry, leg: None
        s._unwind_partial_entry = MagicMock()
        return s

    def _entry(self):
        e = HydraIronCondorEntry(entry_number=1)
        e.short_call_strike, e.short_put_strike = 7600.0, 7300.0
        e.strategy_id = "strangle_test"
        return e

    def test_sells_two_shorts_no_longs(self):
        s = self._strat()
        placed = []

        def fake_place(*, strike, put_call, buy_sell, expiry, external_ref):
            placed.append((put_call, buy_sell))
            return {"position_id": f"P_{put_call}", "uic": 1,
                    "fill_price": 1.5, "mid_at_fill": 1.5, "credit": 150.0}

        s._place_option_order = fake_place
        e = self._entry()
        assert s._execute_entry(e) is True
        assert ("Call", BuySell.SELL) in placed and ("Put", BuySell.SELL) in placed
        assert all(bs == BuySell.SELL for _, bs in placed)  # no protective BUYs
        assert e.call_spread_credit == 150.0 and e.put_spread_credit == 150.0
        assert e.is_complete is True
        assert e.long_call_position_id is None and e.long_put_position_id is None

    def test_partial_fill_unwinds_and_aborts(self):
        s = self._strat()

        def fake_place(*, strike, put_call, buy_sell, expiry, external_ref):
            if put_call == "Put":  # second leg fails
                return None
            return {"position_id": "P_C", "uic": 1, "fill_price": 1.5,
                    "mid_at_fill": 1.5, "credit": 150.0}

        s._place_option_order = fake_place
        e = self._entry()
        assert s._execute_entry(e) is False
        s._unwind_partial_entry.assert_called_once()
        filled = s._unwind_partial_entry.call_args[0][0]
        assert any(leg[0] == "short_call" for leg in filled)  # the filled short was unwound

    def test_missing_strikes_returns_false(self):
        s = self._strat()
        e = HydraIronCondorEntry(entry_number=1)
        e.strategy_id = "x"  # strikes default 0
        assert s._execute_entry(e) is False
