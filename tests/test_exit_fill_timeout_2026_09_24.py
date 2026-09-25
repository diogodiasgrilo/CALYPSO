"""An EXIT must not inherit the ENTRY fill budget.

THE $560 (variant B, live seat, 2026-09-24). E#5's call side stopped. The short
call was bought back in 4 seconds. The long call — riskless by then, the short
already covered — was offered at a correctly-marketable price, sat the FULL
45-second budget without filling, and attempt 2 filled $1.20 lower:

    12:28:56  SELL long call @ $8.30   (bid - cross: marketable)
    12:29:46  ...did not fill.  45s gone.
    12:29:58  attempt 2 fills @ $7.10

That is the single largest exit slippage in B's history (25 stops since
2026-07-24: mean $43.10, total $1,077.50, worst $595).

The 45s came from ``min(30 + 3*(qty-1), 45)``, which the 2026-06-08 partial-fill
forensic calibrated **for entries** — a 7-lot entry legging in needs poll time,
and ORDER-010 fills any remainder afterwards. An exit has the opposite
economics: the position is moving against us, or (post-cover) is a decaying
long, and waiting costs money at the rate the market moves.

Re-placing sooner is safe because ``_close_position_with_retry_ib`` re-checks
the broker position with ``strict=True`` before every attempt after the first,
so a cancel that raced a fill ends the close rather than double-closing.

CONTROLS: ``test_entry_budget_is_UNCHANGED`` (a blanket shortening would break
entry fills — the thing the 2026-06-08 forensic fixed) and the three
``is_exit=True`` propagation tests, which go red if any exit path is missed.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _strategy(cfg=None):
    s = HydraStrategy.__new__(HydraStrategy)
    s.strategy_config = cfg if cfg is not None else {}
    s._positions_cache = None
    s.broker = MagicMock()
    s.broker.place_and_wait_for_fill = MagicMock(
        return_value={"status": "filled", "filled_quantity": 7, "avg_price": 1.0})
    return s


def _timeout_used(s):
    return s.broker.place_and_wait_for_fill.call_args.kwargs["timeout_seconds"]


class TestTheBudgets:
    def test_entry_budget_is_UNCHANGED(self):
        """CONTROL. 7 contracts -> 30 + 3*6 = 48, capped at 45."""
        s = _strategy()
        HydraStrategy._place_leg_order(
            s, instrument_id=1, side="BUY", quantity=7, order_type="LMT",
            limit_price=1.0)
        assert _timeout_used(s) == 45.0

    def test_entry_budget_still_scales_with_size(self):
        s = _strategy()
        HydraStrategy._place_leg_order(
            s, instrument_id=1, side="BUY", quantity=1, order_type="LMT",
            limit_price=1.0)
        assert _timeout_used(s) == 30.0

    def test_exit_uses_the_short_budget(self):
        """15s, not the 10s first shipped. Measured 2026-09-25 over 15 close
        fills: median 2s, p90 5s, MAX 12s — so 10s cut 1 in 15 short for no
        gain, while 15s cuts zero and still beats the old 45s by 30s."""
        s = _strategy()
        HydraStrategy._place_leg_order(
            s, instrument_id=1, side="SELL", quantity=7, order_type="LMT",
            limit_price=1.0, is_exit=True)
        assert _timeout_used(s) == 15.0

    def test_exit_budget_is_configurable(self):
        s = _strategy({"exit_fill_timeout_s": 6.0})
        HydraStrategy._place_leg_order(
            s, instrument_id=1, side="SELL", quantity=7, order_type="MKT",
            is_exit=True)
        assert _timeout_used(s) == 6.0

    def test_a_garbage_config_value_falls_back_not_crashes(self):
        s = _strategy({"exit_fill_timeout_s": "soon"})
        HydraStrategy._place_leg_order(
            s, instrument_id=1, side="SELL", quantity=7, order_type="MKT",
            is_exit=True)
        assert _timeout_used(s) == 15.0

    def test_exit_budget_never_drops_below_one_second(self):
        """A 0 in config must not mean 'time out instantly'."""
        s = _strategy({"exit_fill_timeout_s": 0})
        HydraStrategy._place_leg_order(
            s, instrument_id=1, side="SELL", quantity=7, order_type="MKT",
            is_exit=True)
        assert _timeout_used(s) == 1.0


class TestEveryExitPathIsTagged:
    """CONTROLS: each goes red if that call site loses is_exit=True."""

    def test_close_leg_order(self):
        s = _strategy()
        s._place_leg_order = MagicMock(return_value={})
        HydraStrategy._close_leg_order(s, instrument_id=1, side="BUY", quantity=7)
        assert s._place_leg_order.call_args.kwargs["is_exit"] is True

    def test_marketable_close(self):
        from bots.hydra.base_strategy import MEICStrategy
        s = _strategy()
        s.eod_flatten_market_minutes = 0
        s._read_option_quote = MagicMock(return_value={"bid": 8.0, "ask": 8.1})
        s._place_leg_order = MagicMock(return_value={})
        MEICStrategy._place_marketable_close(
            s, uic=1, side="SELL", quantity=7, attempt_num=1)
        assert s._place_leg_order.call_args.kwargs["is_exit"] is True

    def test_flatten_accumulated_partial(self):
        from bots.hydra.base_strategy import MEICStrategy
        s = _strategy()
        s.commission_per_leg = 1.15
        s.daily_state = MagicMock(total_commission=0.0)
        s._place_leg_order = MagicMock(return_value={"filled": True})
        MEICStrategy._flatten_accumulated_partial(
            s, 1, "BUY", 3, "ref", "sfx", "Put 7625.0")
        assert s._place_leg_order.call_args.kwargs["is_exit"] is True
