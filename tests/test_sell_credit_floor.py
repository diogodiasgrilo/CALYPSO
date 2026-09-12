"""2026-06-10: SELL-leg net-credit prevention floor. A short vertical leg must
never sell BELOW (paired long fill + min net credit), so the spread cannot leg
into a net DEBIT (the Entry #2 inversion). Prevention-at-submission on the legged
path — the world-class fix that IS validatable on the paper account (combos are
not; their paper order lifecycle is broken). Layers under GUARD-INVERT.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import round_to_spx_tick  # noqa: E402
from bots.hydra.order_types import BuySell  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _strat(min_credit=0.05, contracts=7):
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.contracts_per_entry = contracts
    s.min_net_credit_per_contract = min_credit
    s._max_absolute_slippage = 5.0
    s._validate_order_size = MagicMock(return_value=(True, None))
    s._register_position = MagicMock()
    s._cancel_order = MagicMock(return_value=True)
    # call_map has both strikes; put_map empty
    s._read_option_chain = lambda expiry, strikes: ({7500.0: 111, 7505.0: 112}, {})
    return s


class TestFloorPriceMath:
    def test_sell_floor_long_960(self):
        s = _strat()
        # 9.60 + 0.05 = 9.65, rounded UP to the SPX tick
        assert s._sell_credit_floor_price(BuySell.SELL, 9.60) == round_to_spx_tick(9.65, round_up=True)
        assert s._sell_credit_floor_price(BuySell.SELL, 9.60) >= 9.65

    def test_sell_floor_cheap_leg(self):
        s = _strat()
        assert s._sell_credit_floor_price(BuySell.SELL, 0.50) == round_to_spx_tick(0.55, round_up=True)
        assert s._sell_credit_floor_price(BuySell.SELL, 0.0) == round_to_spx_tick(0.05, round_up=True)

    def test_buy_leg_no_floor(self):
        s = _strat()
        assert s._sell_credit_floor_price(BuySell.BUY, 9.60) is None

    def test_no_paired_long_no_floor(self):
        s = _strat()
        assert s._sell_credit_floor_price(BuySell.SELL, None) is None

    def test_custom_min_credit(self):
        s = _strat(min_credit=0.20)
        assert s._sell_credit_floor_price(BuySell.SELL, 1.00) == round_to_spx_tick(1.20, round_up=True)


class TestFloorApplication:
    def test_sell_limit_is_floored_not_the_low_mid(self):
        # Mid 8.80 would sell the short BELOW the 9.60 long → net debit. The floor
        # must raise the LIMIT to >= 9.65 instead of resting at 8.80.
        s = _strat()
        s._read_option_quote = MagicMock(return_value={"bid": 8.70, "ask": 8.90})  # mid 8.80
        calls = []

        def fake_place(**kw):
            calls.append(kw)
            return {"filled": True, "fill_price": kw.get("limit_price"), "order_id": "o1"}

        s._place_leg_order = fake_place
        res = s._place_option_order_ib(7500.0, "Call", BuySell.SELL, "2026-06-10", "ref",
                                       paired_long_fill_per_share=9.60)
        assert res is not None
        assert calls[0]["order_type"] == "LMT"
        assert calls[0]["limit_price"] >= 9.65   # floored, NOT the 8.80 mid

    def test_market_rung_aborted_for_floored_sell(self):
        # All LIMIT rungs floored above the (8.80) market → none fills; the MARKET
        # rung must be SKIPPED (can't honour the floor) → leg fails, no MKT order.
        s = _strat()
        s._read_option_quote = MagicMock(return_value={"bid": 8.70, "ask": 8.90})
        calls = []

        def fake_place(**kw):
            calls.append(kw)
            return {"filled": False, "filled_quantity": 0, "order_id": None}

        s._place_leg_order = fake_place
        res = s._place_option_order_ib(7500.0, "Call", BuySell.SELL, "2026-06-10", "ref",
                                       paired_long_fill_per_share=9.60)
        assert res is None
        assert all(c.get("order_type") != "MKT" for c in calls), "MARKET rung must be skipped for a floored SELL"

    def test_buy_leg_unfloored(self):
        s = _strat()
        s._read_option_quote = MagicMock(return_value={"bid": 9.50, "ask": 9.70})  # mid 9.60
        calls = []

        def fake_place(**kw):
            calls.append(kw)
            return {"filled": True, "fill_price": kw.get("limit_price"), "order_id": "o"}

        s._place_leg_order = fake_place
        res = s._place_option_order_ib(7505.0, "Call", BuySell.BUY, "2026-06-10", "ref",
                                       paired_long_fill_per_share=None)
        assert res is not None
        assert calls[0]["limit_price"] <= 9.75   # ~mid, NOT floored upward

    def test_sell_with_no_paired_long_unfloored(self):
        # Naked short (no paired long) — floor must NOT apply, and MARKET rung allowed.
        s = _strat()
        s._read_option_quote = MagicMock(return_value={"bid": 8.70, "ask": 8.90})
        calls = []

        def fake_place(**kw):
            calls.append(kw)
            return {"filled": True, "fill_price": kw.get("limit_price"), "order_id": "o"}

        s._place_leg_order = fake_place
        res = s._place_option_order_ib(7500.0, "Call", BuySell.SELL, "2026-06-10", "ref",
                                       paired_long_fill_per_share=None)
        assert res is not None
        assert calls[0]["limit_price"] <= 8.85   # rests near the 8.80 mid, no floor
