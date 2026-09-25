"""Abandon a SELL leg once the credit floor rises above the offer.

THE FAILURE THIS ADDRESSES. Variant B has failed 5 entries in 8 retained days,
**every one of them on a SHORT leg** (3x short call, 2x short put), never a
long. Cost, excluding one lucky windfall: **-$739.70 over 8 days, ~-$92/day**,
against a mean day of ~$229.

Mechanism, from 2026-09-25 11:15-11:23:

    11:15:46  credit gate PASSES — put spread validated at $0.20 credit
    11:15:46  place long call
    11:17:35  place long put   (filled $0.95)
    11:19:30  place short call
    11:20:46  place SHORT PUT  <- FIVE MINUTES after the decision
        rung 1  bid 1.00 / ask 1.05   floor 1.00   -> at the ask, plausible
        rung 3  bid 0.80 / ask 0.85   floor 1.00   -> $0.15 ABOVE the offer
    11:23:33  failed all rungs; 3 legs unwound at market

Viability is decided ONCE and the legs take minutes to place. The paired long
is bought at its own price, so the floor (long + min credit) is FIXED while the
market keeps moving. When the market falls, the floor ends up above the offer.

WHAT THIS DOES AND DOES NOT DO. It does not rescue the entry — that entry is
uneconomic and the floor refusing to cross is CORRECT, since crossing would leg
into a net debit. It saves the DRIFT: on 09-25 the long put fell ~$0.20 (~$140
on 7 contracts) during the rungs spent after the floor became unreachable.

THE THRESHOLD IS MEASURED, not chosen. Across the retained logs, on legs where
a floor applied:
    floor >  ask : n=2, filled 0  — both failed entries
    floor == ask : n=3, filled 2 (67%)
    floor <  ask : n=1, filled 1
Hence strictly `>`. At the ask we are merely passive and usually still fill, so
`>=` would abort good entries — which `test_at_the_ask_still_tries` pins.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.base_strategy import (  # noqa: E402
    MEICStrategy, MEICDailyState, BuySell, PROGRESSIVE_RETRY_SEQUENCE,
)


class _S(MEICStrategy):
    def _calculate_strikes(self, *a, **k): raise NotImplementedError
    def _check_stop_losses(self, *a, **k): raise NotImplementedError
    def _initiate_entry(self, *a, **k): raise NotImplementedError


def _rig(monkeypatch, bid, ask, floor):
    monkeypatch.setattr("bots.hydra.base_strategy.time.sleep", lambda *_a: None)
    s = _S.__new__(_S)
    s.strategy_config = {"entry_leg_budget_s": 0}     # isolate from B4
    s.dry_run = False
    s._entry_in_progress = True
    s.contracts_per_entry = 7
    s.max_contracts_per_order = 15
    s.max_contracts_per_underlying = 600
    s.commission_per_leg = 1.15
    s._max_absolute_slippage = 0.50
    s.min_net_credit_per_contract = 0.05
    s.daily_state = MEICDailyState(date="2026-09-25")
    s.attempts = []
    s._read_option_chain = lambda expiry, strikes: ({}, {7665.0: 111})
    s._read_option_quote = lambda conid: {"bid": bid, "ask": ask}
    # side-aware, like the real helper: a BUY leg carries no sell floor
    s._sell_credit_floor_price = (
        lambda bs, paired: floor if bs == BuySell.SELL else None)
    s._monitor_fill_slippage = lambda **k: None
    s._flatten_accumulated_partial = lambda *a, **k: None
    s._cancel_order = lambda *a, **k: True
    s._get_order_status = lambda *a, **k: {}
    s._extract_filled_quantity = staticmethod(lambda *a, **k: 0)
    s._add_orphaned_order = lambda *a, **k: None

    def _place(**kw):
        s.attempts.append(kw)
        return {"filled": False, "order_id": None, "filled_quantity": 0}
    s._place_leg_order = _place
    return s


def _run(s):
    return s._place_option_order_ib(
        7665.0, "Put", BuySell.SELL, "2026-09-25", "ref",
        paired_long_fill_per_share=0.95,
    )


class TestUnfillableFloor:
    def test_floor_above_the_offer_abandons_after_one_try(self, monkeypatch):
        """2026-09-25's rung 3 shape: floor $1.00 vs ask $0.85.

        ONE rung is still placed at the floor — the market may come back, and
        `test_sell_limit_is_floored_not_the_low_mid` (2026-06-10) deliberately
        pins that a floored SELL is placed rather than skipped. What this drops
        is the three rungs after it, once the market is visibly moving away.
        """
        s = _rig(monkeypatch, bid=0.80, ask=0.85, floor=1.00)
        assert _run(s) is None
        assert len(s.attempts) == 1, (
            f"expected exactly one resting attempt, got {len(s.attempts)}")

    def test_at_the_ask_still_tries(self, monkeypatch):
        """CONTROL. `>=` would abort here — but at the ask we are the best
        offer and filled 2 of 3 historically. Aborting would kill good entries.

        A floored SELL walks every rung EXCEPT the final MARKET one, which a
        pre-existing GUARD-FLOOR rule skips (a market order cannot honour a
        floor). Hence len-1, not len.
        """
        s = _rig(monkeypatch, bid=0.80, ask=1.00, floor=1.00)
        _run(s)
        assert len(s.attempts) == len(PROGRESSIVE_RETRY_SEQUENCE) - 1

    def test_floor_below_the_offer_is_untouched(self, monkeypatch):
        s = _rig(monkeypatch, bid=0.95, ask=1.05, floor=1.00)
        _run(s)
        assert len(s.attempts) == len(PROGRESSIVE_RETRY_SEQUENCE) - 1

    def test_no_floor_is_untouched(self, monkeypatch):
        """Legs without a paired long carry no floor and must be unaffected."""
        s = _rig(monkeypatch, bid=0.80, ask=0.85, floor=None)
        _run(s)
        assert len(s.attempts) == len(PROGRESSIVE_RETRY_SEQUENCE)

    def test_BUY_legs_are_untouched(self, monkeypatch):
        """The floor is a SELL concept; a buy must never be gated by it.

        HONEST SCOPE: not a control for the `buy_sell == SELL` clause. That
        clause is structurally redundant — `_sell_credit_floor_price` returns
        None unless the side is SELL, so a BUY leg can never carry a floor and
        removing the clause leaves every test green (verified). It is kept as
        defence in depth against a future floor that is not side-scoped. This
        test pins the PROPERTY, not the mechanism.
        """
        s = _rig(monkeypatch, bid=0.80, ask=0.85, floor=1.00)
        s._read_option_chain = lambda expiry, strikes: ({7665.0: 111}, {})
        s._place_option_order_ib(
            7665.0, "Call", BuySell.BUY, "2026-09-25", "ref",
            paired_long_fill_per_share=None,
        )
        assert len(s.attempts) == len(PROGRESSIVE_RETRY_SEQUENCE)

    def test_it_aborts_MID_leg_when_the_market_moves(self, monkeypatch):
        """The real shape: fillable at rung 1, unreachable by rung 3."""
        quotes = iter([{"bid": 1.00, "ask": 1.05},
                       {"bid": 0.95, "ask": 1.00},
                       {"bid": 0.80, "ask": 0.85}])
        s = _rig(monkeypatch, bid=1.00, ask=1.05, floor=1.00)
        s._read_option_quote = lambda conid: next(quotes, {"bid": 0.80, "ask": 0.85})
        _run(s)
        assert len(s.attempts) == 2, (
            f"expected 2 rungs before the floor went unreachable, got {len(s.attempts)}")
