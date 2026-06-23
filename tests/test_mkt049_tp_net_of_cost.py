"""MKT-049 (2026-06-22): net-of-cost take-profit gate.

The Brandon take-profit fires on the MID mark (entry.*_spread_value), but a
thin 0DTE credit spread CLOSES at short_ask − long_bid, which can be many times
the mid. On 2026-06-22 variant-C E#2 the mid said "SV $17.50 → 87.5% captured",
but the real close cost was $105 (25% captured); after commission the $140
credit netted only +$2.80 — the TP gave back ~75% to slippage it never saw.

The gate recomputes the REAL net capture from live bid/ask (buy the short at
ask, sell the long at bid) minus close commission, and DEFERS the close when
it's below `brandon_tp_min_net_capture`. Mirrors the entry-side MKT-048.

2026-06-23 hardening (C E#1/E#2 fired TPs at ~50% real despite the 80% bar):
  • A worthless / unquoted / crossed LONG leg is priced as $0 recovery (compute
    the side from the short) instead of aborting the whole entry — one dead long
    leg must not blind the gate to a still-wide short side.
  • FAIL CLOSED: when a SHORT leg is unquoted/crossed the close is genuinely
    unpriceable → HOLD (the old fail-OPEN closed on the optimistic mid, the exact
    bug). A held winner is still protected by the stop + GEX exit + expiry.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402


def _strat(*, contracts=7, comm=1.15, gate=True, min_net=0.80, threshold=0.80):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.commission_per_leg = comm
    s.contracts_per_entry = contracts
    s.brandon_tp_net_of_cost_gate_enabled = gate
    s.brandon_tp_min_net_capture = min_net
    s.brandon_take_profit_threshold = threshold
    return s


def _entry(*, call_credit=0.0, put_credit=140.0, contracts=7):
    return types.SimpleNamespace(
        entry_number=2, contracts=contracts,
        call_spread_credit=call_credit, put_spread_credit=put_credit,
        short_call_uic=1, long_call_uic=2, short_put_uic=3, long_put_uic=4,
    )


def _wire(s, quotes):
    s._read_option_quotes_batch = lambda conids: {c: quotes.get(c) for c in conids}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: _brandon_real_close_capture (the math + fail-open)
# ─────────────────────────────────────────────────────────────────────────────

class TestRealCloseCapture:
    def test_today_C_E2_real_capture_is_far_below_mid(self):
        # Put-only $140 credit, 7c. Close: buy short@0.50, sell long@0.35 →
        # $0.15×700 = $105 cost; commission 2×1×1.15×7 = $16.10.
        # net = (140 − 105 − 16.10)/140 = 0.135 — vs the mid's "87.5%".
        s = _strat()
        _wire(s, {
            3: {"bid": 0.40, "ask": 0.50},   # short put
            4: {"bid": 0.35, "ask": 0.45},   # long put
        })
        e = _entry()
        cap = s._brandon_real_close_capture(e, call_alive=False, put_alive=True)
        assert cap == pytest.approx((140 - 105 - 16.10) / 140, abs=1e-4)
        assert cap < 0.80   # would be DEFERRED

    def test_genuinely_cheap_close_clears_the_bar(self):
        # short@0.02 / long@0.01 → $7 cost, +$16.10 comm → 0.835 ≥ 0.80.
        s = _strat()
        _wire(s, {
            3: {"bid": 0.01, "ask": 0.02},
            4: {"bid": 0.01, "ask": 0.02},
        })
        e = _entry()
        cap = s._brandon_real_close_capture(e, call_alive=False, put_alive=True)
        assert cap == pytest.approx((140 - 7 - 16.10) / 140, abs=1e-4)
        assert cap >= 0.80

    def test_full_ic_sums_both_sides(self):
        s = _strat()
        _wire(s, {
            1: {"bid": 0.05, "ask": 0.10},   # short call
            2: {"bid": 0.05, "ask": 0.10},   # long call
            3: {"bid": 0.07, "ask": 0.12},   # short put
            4: {"bid": 0.06, "ask": 0.11},   # long put
        })
        e = _entry(call_credit=120.0, put_credit=130.0)
        cap = s._brandon_real_close_capture(e, call_alive=True, put_alive=True)
        # call close: (0.10 − 0.05)*700 = 35 ; put close: (0.12 − 0.06)*700 = 42
        # comm: 2*2*1.15*7 = 32.20 ; net = (250 − 77 − 32.20)/250
        assert cap == pytest.approx((250 - 77 - 32.20) / 250, abs=1e-4)

    def test_missing_long_quote_prices_from_short_not_none(self):
        # 2026-06-23: a missing/worthless LONG is $0 recovery, NOT a None. The
        # side is still priced from the short — this is the fix for the worthless
        # long_call that used to fail-open the whole entry.
        s = _strat()
        _wire(s, {3: {"bid": 0.40, "ask": 0.50}})  # long put (4) absent → $0 recovery
        e = _entry()
        cap = s._brandon_real_close_capture(e, call_alive=False, put_alive=True)
        # close cost = (0.50 − 0)×700 = 350 ; comm 16.10 ; net = (140−350−16.10)/140
        assert cap is not None
        assert cap == pytest.approx((140 - 350 - 16.10) / 140, abs=1e-4)
        assert cap < 0.80   # → would HOLD (correct: closing here gives it all back)

    def test_missing_short_ask_returns_none(self):
        # The SHORT drives the cost; without its ask the close is unpriceable → None.
        s = _strat()
        _wire(s, {3: {"bid": 0.40}, 4: {"bid": 0.35, "ask": 0.45}})  # short has no ask
        e = _entry()
        assert s._brandon_real_close_capture(e, call_alive=False, put_alive=True) is None

    def test_missing_long_bid_field_prices_from_short(self):
        # long quote present but no bid field → $0 recovery, still priced.
        s = _strat()
        _wire(s, {3: {"bid": 0.40, "ask": 0.50}, 4: {"ask": 0.45}})  # long has no bid
        e = _entry()
        cap = s._brandon_real_close_capture(e, call_alive=False, put_alive=True)
        assert cap == pytest.approx((140 - 350 - 16.10) / 140, abs=1e-4)

    def test_crossed_short_book_returns_none(self):
        s = _strat()
        _wire(s, {
            3: {"bid": 0.80, "ask": 0.20},   # crossed short → unpriceable
            4: {"bid": 0.35, "ask": 0.45},
        })
        e = _entry()
        assert s._brandon_real_close_capture(e, call_alive=False, put_alive=True) is None

    def test_crossed_long_book_treated_as_zero_recovery(self):
        # crossed LONG book → distrust it → $0 recovery, priced from the short.
        s = _strat()
        _wire(s, {
            3: {"bid": 0.40, "ask": 0.50},
            4: {"bid": 0.55, "ask": 0.45},   # crossed long (bid > ask)
        })
        e = _entry()
        cap = s._brandon_real_close_capture(e, call_alive=False, put_alive=True)
        assert cap == pytest.approx((140 - 350 - 16.10) / 140, abs=1e-4)

    def test_long_bid_above_short_ask_treated_as_zero_recovery(self):
        # long bid illogically above short ask → distrust → $0 recovery (NOT None).
        s = _strat()
        _wire(s, {
            3: {"bid": 0.10, "ask": 0.15},
            4: {"bid": 0.50, "ask": 0.55},   # long bid 0.50 > short ask 0.15
        })
        e = _entry()
        cap = s._brandon_real_close_capture(e, call_alive=False, put_alive=True)
        # priced from the short only: (0.15 − 0)×700 = 105 ; net = (140−105−16.10)/140
        assert cap == pytest.approx((140 - 105 - 16.10) / 140, abs=1e-4)

    def test_missing_long_uic_prices_from_short(self):
        # a LONG leg whose conid is None (e.g. independently salvaged) → $0
        # recovery, side priced from the short — NOT a None (it IS priceable).
        s = _strat()
        _wire(s, {3: {"bid": 0.40, "ask": 0.50}})
        e = _entry()
        e.long_put_uic = None
        cap = s._brandon_real_close_capture(e, call_alive=False, put_alive=True)
        assert cap == pytest.approx((140 - 350 - 16.10) / 140, abs=1e-4)

    def test_missing_short_uic_returns_none(self):
        # a SHORT leg with no conid → the close is unpriceable → None (hold).
        s = _strat()
        _wire(s, {4: {"bid": 0.35, "ask": 0.45}})
        e = _entry()
        e.short_put_uic = None
        assert s._brandon_real_close_capture(e, call_alive=False, put_alive=True) is None

    def test_no_alive_side_returns_none(self):
        s = _strat()
        _wire(s, {})
        e = _entry()
        assert s._brandon_real_close_capture(e, call_alive=False, put_alive=False) is None

    def test_zero_contracts_returns_none(self):
        s = _strat(contracts=7)
        _wire(s, {3: {"bid": 0.40, "ask": 0.50}, 4: {"bid": 0.35, "ask": 0.45}})
        e = _entry(contracts=0)
        e.contracts = 0
        s.contracts_per_entry = 0
        assert s._brandon_real_close_capture(e, call_alive=False, put_alive=True) is None


# ─────────────────────────────────────────────────────────────────────────────
# Integration: the veto inside _brandon_check_take_profit
# ─────────────────────────────────────────────────────────────────────────────

def _tp_entry(*, put_value=17.50):
    return types.SimpleNamespace(
        entry_number=2, contracts=7,
        call_spread_credit=0.0, put_spread_credit=140.0,
        call_spread_value=0.0, put_spread_value=put_value,
        short_call_strike=0.0, long_call_strike=0.0,
        short_put_strike=7400.0, long_put_strike=7395.0,
        short_call_uic=1, long_call_uic=2, short_put_uic=3, long_put_uic=4,
    )


def _tp_strat(*, gate=True, quotes=None):
    s = _strat(gate=gate)
    s.dry_run = False  # live path: post-close realized subtraction is skipped
    # Put-only: call dead, put alive.
    s._brandon_side_alive = lambda e, side: side == "put"
    s.current_price = 7463.0
    s.brandon_tp_worthless_otm_pts = 20.0
    s.brandon_tp_hold_to_expiry_minutes = 60.0
    s.brandon_tp_hold_safe_cushion_pts = 25.0
    s._minutes_to_market_close = lambda: 200.0   # far from close → no hold-if-safe
    s._brandon_close_in_cooldown = lambda e, side: False
    s._close_entry_early = MagicMock(return_value=(2, 0, None))
    s._brandon_clear_close_failed = MagicMock()
    _wire(s, quotes or {})
    return s


class TestVetoIntegration:
    def test_mid_says_close_but_real_cost_defers(self):
        # Mid SV $17.50 / $140 = 87.5% captured → evaluate_iron_condor fires.
        # Real close (0.50/0.35) = 13.5% net < 80% → DEFER, no close.
        s = _tp_strat(quotes={
            3: {"bid": 0.40, "ask": 0.50},
            4: {"bid": 0.35, "ask": 0.45},
        })
        e = _tp_entry()
        assert s._brandon_check_take_profit(e) is None
        assert s._close_entry_early.call_count == 0
        assert getattr(e, "_mkt049_deferred", False) is True

    def test_mid_and_real_both_good_proceeds_to_close(self):
        # Real close (0.02/0.01) = 83.5% net ≥ 80% → proceed; close is attempted.
        s = _tp_strat(quotes={
            3: {"bid": 0.01, "ask": 0.02},
            4: {"bid": 0.01, "ask": 0.02},
        })
        e = _tp_entry()
        e.call_side_expired = False
        e.put_side_expired = True
        e.actual_call_stop_debit = 0.0
        e.actual_put_stop_debit = 0.0
        s._brandon_check_take_profit(e)
        assert s._close_entry_early.call_count == 1

    def test_gate_disabled_skips_the_veto(self):
        # Same bad real cost, but gate off → mid decision stands → close attempted.
        s = _tp_strat(gate=False, quotes={
            3: {"bid": 0.40, "ask": 0.50},
            4: {"bid": 0.35, "ask": 0.45},
        })
        e = _tp_entry()
        e.call_side_expired = False
        e.put_side_expired = True
        e.actual_call_stop_debit = 0.0
        e.actual_put_stop_debit = 0.0
        s._brandon_check_take_profit(e)
        assert s._close_entry_early.call_count == 1

    def test_unpriceable_short_fails_closed_holds(self):
        # 2026-06-23: the SHORT is unquoted (no batch) → real cost unpriceable →
        # FAIL CLOSED. The old code fell open to the optimistic mid and CLOSED;
        # now it HOLDS (protected by stop + GEX + expiry).
        s = _tp_strat(quotes={})  # batch returns empty → short put unquoted
        e = _tp_entry()
        e.call_side_expired = False
        e.put_side_expired = True
        e.actual_call_stop_debit = 0.0
        e.actual_put_stop_debit = 0.0
        assert s._brandon_check_take_profit(e) is None
        assert s._close_entry_early.call_count == 0   # HELD, not closed
        assert getattr(e, "_mkt049_deferred", False) is True


def _full_ic_strat(quotes):
    """Full-IC variant: both sides alive, comfortably OTM, far from close."""
    s = _strat(gate=True)
    s.dry_run = False
    # alive until that side is marked expired (a close marks it so)
    s._brandon_side_alive = lambda e, side: not getattr(e, f"{side}_side_expired", False)
    s.current_price = 7400.0
    s.brandon_tp_worthless_otm_pts = 20.0
    s.brandon_tp_hold_to_expiry_minutes = 60.0
    s.brandon_tp_hold_safe_cushion_pts = 25.0
    s._minutes_to_market_close = lambda: 200.0            # no hold-if-safe
    s._brandon_close_in_cooldown = lambda e, side: False
    s._close_entry_early = MagicMock(return_value=(3, 0, None))
    _wire(s, quotes)
    return s


def _full_ic_entry():
    # E#1-like: call $105 + put $315 = $420 credit; mids small → mid says ~93%.
    return types.SimpleNamespace(
        entry_number=1, contracts=7,
        call_spread_credit=105.0, put_spread_credit=315.0,
        call_spread_value=10.0, put_spread_value=20.0,
        short_call_strike=7475.0, long_call_strike=7480.0,
        short_put_strike=7320.0, long_put_strike=7315.0,
        short_call_uic=1, long_call_uic=2, short_put_uic=3, long_put_uic=4,
        call_side_expired=False, put_side_expired=False,
        actual_call_stop_debit=0.0, actual_put_stop_debit=0.0,
    )


class TestWorthlessLongRepro:
    """The exact 2026-06-23 C E#1/E#2 failure: a worthless long_call goes
    unquoted near expiry while the put side is still wide. Old code → fail-open →
    the optimistic mid TP fired at ~50% real. New code → the worthless long is
    priced $0, the put side's real cost is seen, and the TP is DEFERRED."""

    def test_worthless_unquoted_long_call_defers_not_closes(self):
        s = _full_ic_strat(quotes={
            1: {"bid": 0.05, "ask": 0.10},   # short call (cheap, near-worthless)
            # 2: long call UNQUOTED (worthless near expiry) → $0 recovery
            3: {"bid": 0.40, "ask": 0.45},   # short put — still wide
            4: {"bid": 0.25, "ask": 0.30},   # long put
        })
        e = _full_ic_entry()
        # real: call (0.10−0)×700=70 ; put (0.45−0.25)×700=140 ; total 210
        # comm 2×2×1.15×7=32.20 ; net = (420−210−32.20)/420 ≈ 0.423 < 0.80
        assert s._brandon_check_take_profit(e) is None
        assert s._close_entry_early.call_count == 0       # DEFERRED, not closed
        assert getattr(e, "_mkt049_deferred", False) is True

    def test_both_sides_genuinely_cheap_still_closes(self):
        # Sanity: when BOTH sides are genuinely cheap to close (incl. a real
        # worthless long), the gate still lets a good TP through.
        def _mark_closed(entry):
            entry.call_side_expired = True
            entry.put_side_expired = True
            return (4, 0, None)
        s = _full_ic_strat(quotes={
            1: {"bid": 0.01, "ask": 0.02},
            2: {"bid": 0.00, "ask": 0.01},   # worthless long, $0 recovery
            3: {"bid": 0.01, "ask": 0.02},
            4: {"bid": 0.00, "ask": 0.01},
        })
        s._close_entry_early = MagicMock(side_effect=_mark_closed)
        e = _full_ic_entry()
        # real close ≈ (0.02×700)+(0.02×700)=28 ; comm 32.20 ;
        # net = (420−28−32.20)/420 ≈ 0.857 ≥ 0.80 → close
        s._brandon_check_take_profit(e)
        assert s._close_entry_early.call_count == 1
