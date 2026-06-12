"""CLOSE-LMT (2026-06-08 research): IBKR routes an OPTION market order as CAPPED
marketable-limit orders with a documented non-fill possibility, and
Market-with-Protection is unavailable for OPT via the Client Portal API. So the
close path must CROSS the spread with an aggressive marketable LIMIT (walked
wider each attempt, capped), using a plain MARKET only when no quote is
available."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import CLOSE_LIMIT_CROSS_CAP  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _leg_result(order_id="O", fill=2.5):
    return {"success": True, "filled": True, "filled_quantity": 7,
            "requested_quantity": 7, "order_id": order_id, "fill_price": fill,
            "position_id": None, "raw": {}}


def _strat(quote):
    s = HydraStrategy.__new__(HydraStrategy)
    s._read_option_quote = MagicMock(return_value=quote)
    s._place_leg_order = MagicMock(return_value=_leg_result("LMT1"))
    s._close_leg_order = MagicMock(return_value=_leg_result("MKT1"))
    return s


class TestMarketableClose:
    def test_buy_crosses_up_through_ask(self):
        s = _strat({"bid": 1.80, "ask": 2.00})
        s._place_marketable_close(uic=123, side="BUY", quantity=7, attempt_num=1)
        # 2026-06-12: cross is now 1 SPX tick/attempt (was $0.50). attempt 1 →
        # BUY limit = ask 2.00 + 0.05 = 2.05, LMT, marketable (>= ask), near touch.
        kw = s._place_leg_order.call_args.kwargs
        assert kw["order_type"] == "LMT"
        assert kw["side"] == "BUY"
        assert kw["limit_price"] == 2.05  # marketable (>= ask), 1 tick over
        s._close_leg_order.assert_not_called()

    def test_sell_crosses_down_through_bid(self):
        s = _strat({"bid": 4.00, "ask": 4.30})
        s._place_marketable_close(uic=123, side="SELL", quantity=7, attempt_num=1)
        kw = s._place_leg_order.call_args.kwargs
        assert kw["order_type"] == "LMT"
        assert kw["side"] == "SELL"
        # bid 4.00 - 0.05 = 3.95 → snaps to the $0.10 tick = 4.00 (= bid, still
        # marketable: a SELL at the bid crosses to the resting buyer).
        assert kw["limit_price"] == 4.00

    def test_walk_widens_with_attempt(self):
        s = _strat({"bid": 1.80, "ask": 2.00})
        s._place_marketable_close(uic=123, side="BUY", quantity=7, attempt_num=3)
        # cross = 0.05 * 3 = 0.15 → 2.00 + 0.15 = 2.15 (still inside the cap).
        assert s._place_leg_order.call_args.kwargs["limit_price"] == 2.15

    def test_cross_is_capped(self):
        s = _strat({"bid": 1.80, "ask": 2.00})
        s._place_marketable_close(uic=123, side="BUY", quantity=7, attempt_num=99)
        # cross capped at CLOSE_LIMIT_CROSS_CAP (3.00) → 2.00 + 3.00 = 5.00.
        assert s._place_leg_order.call_args.kwargs["limit_price"] == 2.00 + CLOSE_LIMIT_CROSS_CAP

    def test_no_quote_falls_back_to_market(self):
        s = _strat(None)
        res = s._place_marketable_close(uic=123, side="BUY", quantity=7, attempt_num=1)
        s._close_leg_order.assert_called_once()
        s._place_leg_order.assert_not_called()
        assert res["order_id"] == "MKT1"

    def test_buy_with_no_ask_falls_back_to_market(self):
        s = _strat({"bid": 1.80, "ask": None})
        s._place_marketable_close(uic=123, side="BUY", quantity=7, attempt_num=1)
        s._close_leg_order.assert_called_once()
        s._place_leg_order.assert_not_called()

    def test_sell_worthless_long_no_bid_falls_back_to_market(self):
        # A deep-OTM long with bid 0 can't be sold via a marketable limit → MARKET
        # fallback (which also won't fill on no bid — the long simply expires).
        s = _strat({"bid": 0.0, "ask": 0.05})
        s._place_marketable_close(uic=123, side="SELL", quantity=7, attempt_num=1)
        s._close_leg_order.assert_called_once()
        s._place_leg_order.assert_not_called()
