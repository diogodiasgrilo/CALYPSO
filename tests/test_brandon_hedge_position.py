"""Tests for bots.hydra.brandon.hedge_position."""

import math
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.hedge_position import (
    HedgeLeg,
    HedgeSettlement,
    black_scholes_call_price,
    black_scholes_put_price,
    estimate_fill_price,
    leg_intrinsic_at_expiry,
    leg_pnl_at_expiry,
    settle_hedge,
    total_hedge_pnl,
)

T0 = datetime(2026, 5, 5, 13, 0, tzinfo=timezone.utc)


def _leg(side="long", ctype="call", strike=6850, qty=1, fill=5.0, ent=1, struct="debit_spread", threatened="call"):
    return HedgeLeg(
        entry_number=ent,
        side=side,
        contract_type=ctype,
        strike=strike,
        quantity=qty,
        fill_price=fill,
        position_id=f"DRY_OVERLAY_{ent}_x",
        structure=struct,
        threatened_side=threatened,
        placed_at=T0,
    )


class TestBlackScholesPrices:
    def test_call_price_positive(self):
        p = black_scholes_call_price(6800, 6800, 0.18, 1 / 365.0)
        assert p > 0
        assert math.isfinite(p)

    def test_put_price_positive(self):
        p = black_scholes_put_price(6800, 6800, 0.18, 1 / 365.0)
        assert p > 0

    def test_deep_otm_call_near_zero(self):
        p = black_scholes_call_price(6800, 7500, 0.18, 1 / 365.0)
        assert p < 1.0  # essentially worthless

    def test_t_zero_returns_intrinsic(self):
        # At expiry, BS function returns intrinsic
        assert black_scholes_call_price(6800, 6700, 0.18, 0.0) == pytest.approx(100.0)
        assert black_scholes_call_price(6800, 6900, 0.18, 0.0) == 0.0
        assert black_scholes_put_price(6800, 6900, 0.18, 0.0) == pytest.approx(100.0)
        assert black_scholes_put_price(6800, 6700, 0.18, 0.0) == 0.0


class TestEstimateFillPrice:
    def test_call_routes_to_call_price(self):
        p = estimate_fill_price(contract_type="call", strike=6850, spot=6800, t_years=1 / 365.0)
        assert p > 0

    def test_put_routes_to_put_price(self):
        p = estimate_fill_price(contract_type="put", strike=6750, spot=6800, t_years=1 / 365.0)
        assert p > 0


class TestLegPnL:
    def test_long_call_in_money_at_expiry(self):
        # Long 6800 call, paid $10, SPX settles 6850 → intrinsic 50, P&L per contract = +40
        leg = _leg(side="long", ctype="call", strike=6800, fill=10.0, qty=1)
        assert leg_pnl_at_expiry(leg, 6850) == pytest.approx((50 - 10) * 100)

    def test_long_call_out_of_money_at_expiry(self):
        leg = _leg(side="long", ctype="call", strike=6800, fill=10.0, qty=1)
        # SPX settles 6700 → intrinsic 0, P&L = -fill = -1000
        assert leg_pnl_at_expiry(leg, 6700) == pytest.approx(-1000)

    def test_short_call_kept_premium(self):
        leg = _leg(side="short", ctype="call", strike=6900, fill=5.0, qty=1)
        # SPX settles 6700 → call OTM → keep premium → +500
        assert leg_pnl_at_expiry(leg, 6700) == pytest.approx(500)

    def test_short_call_assigned(self):
        leg = _leg(side="short", ctype="call", strike=6900, fill=5.0, qty=1)
        # SPX settles 7000 → intrinsic 100, paid back 100, kept 5 → -9500
        assert leg_pnl_at_expiry(leg, 7000) == pytest.approx((5 - 100) * 100)

    def test_long_put_in_money(self):
        leg = _leg(side="long", ctype="put", strike=6800, fill=8.0, qty=1)
        assert leg_pnl_at_expiry(leg, 6750) == pytest.approx((50 - 8) * 100)

    def test_quantity_scales_pnl(self):
        leg = _leg(side="long", ctype="call", strike=6800, fill=10.0, qty=3)
        assert leg_pnl_at_expiry(leg, 6850) == pytest.approx((50 - 10) * 100 * 3)

    def test_intrinsic_helper_matches_pnl_formula(self):
        leg = _leg(side="long", ctype="call", strike=6800, fill=10.0, qty=1)
        intrinsic = leg_intrinsic_at_expiry(leg, 6850)
        assert intrinsic == 50
        assert leg_pnl_at_expiry(leg, 6850) == (intrinsic - leg.fill_price) * 100


class TestDebitSpreadPayoff:
    """Net hedge value across a 2-leg debit spread at expiry."""

    def _call_debit(self, long_strike, short_strike, long_fill=8.0, short_fill=3.0):
        # Buy long call (lower strike), sell short call (higher strike)
        return [
            _leg(side="long", ctype="call", strike=long_strike, fill=long_fill, qty=1),
            _leg(side="short", ctype="call", strike=short_strike, fill=short_fill, qty=1),
        ]

    def test_call_debit_spread_max_loss_below_long(self):
        legs = self._call_debit(long_strike=6850, short_strike=6860, long_fill=8.0, short_fill=3.0)
        # SPX 6800: both expire worthless. Net = -fill_long + fill_short = -800 + 300 = -500.
        assert total_hedge_pnl(legs, 6800) == pytest.approx(-500)

    def test_call_debit_spread_max_profit_above_short(self):
        legs = self._call_debit(long_strike=6850, short_strike=6860, long_fill=8.0, short_fill=3.0)
        # SPX 6900: long ITM 50, short ITM 40 → gross spread value 10, net = 10 × 100 - debit (5) × 100 = 500
        assert total_hedge_pnl(legs, 6900) == pytest.approx(500)

    def test_call_debit_spread_partial_payoff(self):
        legs = self._call_debit(long_strike=6850, short_strike=6860, long_fill=8.0, short_fill=3.0)
        # SPX 6855: long ITM 5, short OTM. Net = (5-8)*100 + (3-0)*100 = -300 + 300 = 0
        assert total_hedge_pnl(legs, 6855) == pytest.approx(0)


class TestButterflyPayoff:
    """Net hedge value across a 4-contract butterfly at expiry."""

    def _call_butterfly(self, lower=6890, pin=6900, upper=6910, lower_fill=15, pin_fill=8, upper_fill=3):
        # 1 long lower / 2 short pin / 1 long upper
        return [
            _leg(side="long", ctype="call", strike=lower, fill=lower_fill, qty=1),
            _leg(side="short", ctype="call", strike=pin, fill=pin_fill, qty=2),
            _leg(side="long", ctype="call", strike=upper, fill=upper_fill, qty=1),
        ]

    def test_butterfly_max_at_pin(self):
        legs = self._call_butterfly()
        # Net debit at placement: paid lower + upper, received 2× pin = 15 + 3 - 16 = 2
        # At pin 6900: long lower ITM 10, short pin 0, long upper 0
        # Gross intrinsic = 10×1 + 0 + 0 = 10
        # Per-contract P&L: long_lower (10-15)=-5, short_pin (8-0)*2=16, long_upper (0-3)=-3
        # Sum: -500 + 1600 - 300 = +800
        assert total_hedge_pnl(legs, 6900) == pytest.approx(800)

    def test_butterfly_max_loss_below_lower(self):
        legs = self._call_butterfly()
        # SPX 6800: all OTM. Lose long fills, keep short fills.
        # -lower_fill + 2×short_fill - upper_fill = -15 + 16 - 3 = -2
        # × 100 = -200
        assert total_hedge_pnl(legs, 6800) == pytest.approx(-200)

    def test_butterfly_max_loss_above_upper(self):
        legs = self._call_butterfly()
        # SPX 7000: all ITM
        # long_lower (110-15)=95, short_pin (8-100)*2=-184, long_upper (90-3)=87
        # Sum = 95 - 184 + 87 = -2 → × 100 = -200
        assert total_hedge_pnl(legs, 7000) == pytest.approx(-200)

    def test_butterfly_payoff_symmetry(self):
        # Butterfly payoff should be the same at equal distances from pin
        legs = self._call_butterfly()
        below = total_hedge_pnl(legs, 6800)  # 100pt below pin
        above = total_hedge_pnl(legs, 7000)  # 100pt above pin
        assert below == pytest.approx(above)


class TestSettleHedge:
    def test_settle_returns_none_for_empty(self):
        assert settle_hedge([], 6800) is None

    def test_settle_aggregates_correctly(self):
        legs = [
            _leg(side="long", ctype="call", strike=6800, fill=10.0, qty=1),
            _leg(side="short", ctype="call", strike=6810, fill=5.0, qty=1),
        ]
        s = settle_hedge(legs, 6850)
        assert s is not None
        assert s.entry_number == 1
        assert s.threatened_side == "call"
        assert s.structure == "debit_spread"
        assert s.spx_settle == 6850
        # debit_paid = +1×fill_long − 1×fill_short = 1000 − 500 = 500
        assert s.total_debit_paid == pytest.approx(500)
        # P&L: long (50-10)*100 = 4000, short (5-40)*100 = -3500, net = 500
        assert s.total_pnl == pytest.approx(500)

    def test_dataclass_is_frozen(self):
        legs = [_leg()]
        s = settle_hedge(legs, 6800)
        with pytest.raises(Exception):
            s.total_pnl = 0.0  # type: ignore
