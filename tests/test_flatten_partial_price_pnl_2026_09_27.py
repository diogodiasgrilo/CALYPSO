"""A flattened partial leg's price P&L must reach the books.

`_flatten_accumulated_partial` booked COMMISSION ONLY, and said so in its own
log line: *"market-order slippage on this round trip is NOT separately
tracked"*. That slippage is real money — the partial was a genuine broker fill
on the way in and a MARKET order on the way out, so the round trip almost
always loses the spread — and none of it reached the books.

It is booked AGGREGATE-ONLY and recorded as unattributed, exactly like
`_unwind_partial_entry`: a leg flattened because its entry aborted never became
a position, so no entry can own its result. Keeping the two consistent is what
lets the settlement RECONCILE identity hold on a failed-entry day.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.base_strategy import MEICStrategy, MEICDailyState  # noqa: E402


class _S(MEICStrategy):
    def _calculate_strikes(self, *a, **k): raise NotImplementedError
    def _check_stop_losses(self, *a, **k): raise NotImplementedError
    def _initiate_entry(self, *a, **k): raise NotImplementedError


def _rig(close_px, filled=True):
    s = _S.__new__(_S)
    s.commission_per_leg = 1.15
    s.daily_state = MEICDailyState(date="2026-09-27")
    s._place_leg_order = MagicMock(return_value={
        "filled": filled, "fill_price": close_px, "order_id": "f1"})
    s._add_orphaned_order = MagicMock()
    s.alert_service = MagicMock()
    return s


class TestPricePnLIsBooked:
    def test_a_long_partial_that_lost_is_booked(self):
        """Bought 5 @ $0.30, flattened @ $0.25 -> -$25."""
        s = _rig(close_px=0.25)
        s._flatten_accumulated_partial(111, "BUY", 5, "ref", "sfx", "Put 7625.0",
                                       open_price_per_share=0.30)
        assert round(s.daily_state.total_realized_pnl, 2) == -25.00
        assert round(s.daily_state.failed_entry_unattributed_pnl, 2) == -25.00

    def test_a_SHORT_partial_is_signed_the_other_way(self):
        """Sold 5 @ $0.30, bought back @ $0.25 -> +$25."""
        s = _rig(close_px=0.25)
        s._flatten_accumulated_partial(111, "SELL", 5, "ref", "sfx", "Call 7700.0",
                                       open_price_per_share=0.30)
        assert round(s.daily_state.total_realized_pnl, 2) == 25.00

    def test_it_lands_in_the_SAME_bucket_as_an_unwind(self):
        """CONTROL for the reconcile identity: aggregate-only AND recorded as
        unattributed. Booking it per-entry, or forgetting the accumulator,
        re-opens the drift A1 closed."""
        s = _rig(close_px=0.25)
        s._flatten_accumulated_partial(111, "BUY", 5, "ref", "sfx", "Put 7625.0",
                                       open_price_per_share=0.30)
        assert (round(s.daily_state.failed_entry_unattributed_pnl, 2)
                == round(s.daily_state.total_realized_pnl, 2))

    def test_commission_is_STILL_booked(self):
        """Regression guard for the 2026-08-20 fix this builds on."""
        s = _rig(close_px=0.25)
        s._flatten_accumulated_partial(111, "BUY", 5, "ref", "sfx", "Put 7625.0",
                                       open_price_per_share=0.30)
        assert round(s.daily_state.total_commission, 2) == round(2 * 1.15 * 5, 2)


class TestItNeverBreaksTheFlatten:
    def test_no_open_price_books_commission_only(self):
        """Older callers pass nothing — must not guess a P&L from thin air."""
        s = _rig(close_px=0.25)
        s._flatten_accumulated_partial(111, "BUY", 5, "ref", "sfx", "Put 7625.0")
        assert s.daily_state.total_realized_pnl == 0.0
        assert round(s.daily_state.total_commission, 2) == round(2 * 1.15 * 5, 2)

    def test_no_close_price_books_commission_only(self):
        s = _rig(close_px=None)
        s._flatten_accumulated_partial(111, "BUY", 5, "ref", "sfx", "Put 7625.0",
                                       open_price_per_share=0.30)
        assert s.daily_state.total_realized_pnl == 0.0

    def test_a_FAILED_flatten_books_nothing(self):
        """Nothing traded, so nothing to book — and the orphan path still runs."""
        s = _rig(close_px=0.25, filled=False)
        s._flatten_accumulated_partial(111, "BUY", 5, "ref", "sfx", "Put 7625.0",
                                       open_price_per_share=0.30)
        assert s.daily_state.total_realized_pnl == 0.0
        assert s.daily_state.total_commission == 0.0
        s._add_orphaned_order.assert_called()


class TestEveryCallSitePassesTheOpenPrice:
    def test_all_three_rung_loop_sites(self):
        """The booking is worthless if the callers do not supply the price."""
        import inspect
        src = inspect.getsource(MEICStrategy._place_option_order_ib)
        assert src.count("_flatten_accumulated_partial(") == 3
        assert src.count("open_price_per_share=(") == 3
