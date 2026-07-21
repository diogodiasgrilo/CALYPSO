"""2026-07-21: realistic dry-run ENTRY fill for Brandon overlays.

estimate_fill_price returns a Black-Scholes MID (no bid/ask spread), so the modeled
overlay debit was too cheap and the overlay P&L inflated (the settlement side is
already realistic — SPXW is PM-settled at the close, cash-settled, held to expiry).
The fix crosses the spread on entry: long legs toward the ask (+half_spread), short
legs toward the bid (−half_spread). This test pins the exact effect on B's real
07-21 butterfly (LC 7500x10 / SC 7510x20 / LC 7520x10).
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.brandon import hedge_position  # noqa: E402
from bots.hydra.brandon.defensive_overlay import (  # noqa: E402
    OverlayProposal, OverlayLeg, OverlayStructure,
)


def _stub(spread):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.dry_run = True
    s.brandon_overlay_fill_spread = spread
    s.current_price = 7510.0
    s._brandon_hedge_legs = {}
    s._brandon_estimate_t_years_to_close = lambda: 0.01
    s._brandon_now_et = lambda: datetime(2026, 7, 21, 13, 0)
    s._brandon_save_hedge_state = lambda: None
    s._brandon_send_telegram = lambda *a, **k: None
    return s


def _butterfly():
    return OverlayProposal(
        structure=OverlayStructure.BUTTERFLY,
        threatened_side="call",
        legs=(
            OverlayLeg("long", "call", 7500.0, 10),
            OverlayLeg("short", "call", 7510.0, 20),
            OverlayLeg("long", "call", 7520.0, 10),
        ),
        pin_strike=7510.0,
        reason="test butterfly",
    )


class TestOverlayFillSpread:
    def test_spread_raises_debit_and_lowers_pnl_by_expected(self):
        s0 = _stub(0.0)
        s0._brandon_place_overlay(SimpleNamespace(entry_number=5), _butterfly())
        s1 = _stub(0.25)
        s1._brandon_place_overlay(SimpleNamespace(entry_number=5), _butterfly())
        legs0, legs1 = s0._brandon_hedge_legs[5], s1._brandon_hedge_legs[5]

        # Long legs cost more (toward ask); short legs receive less (toward bid).
        for l0, l1 in zip(legs0, legs1):
            if l0.side == "long":
                assert l1.fill_price == pytest.approx(l0.fill_price + 0.25)
            else:
                assert l1.fill_price == pytest.approx(l0.fill_price - 0.25)

        pnl0 = hedge_position.settle_hedge(legs0, spx_settle=7510.0).total_pnl
        pnl1 = hedge_position.settle_hedge(legs1, spx_settle=7510.0).total_pnl
        # De-inflated: same intrinsic, higher debit. Butterfly debit rises
        # 0.25(LC7500) + 0.25(LC7520) + 0.50(2xSC7510) = $1.00/sh x100x10 = $1000.
        assert pnl1 < pnl0
        assert (pnl0 - pnl1) == pytest.approx(1000.0)

    def test_zero_spread_is_the_old_mid_pricing(self):
        s = _stub(0.0)
        s._brandon_place_overlay(SimpleNamespace(entry_number=1), _butterfly())
        for leg in s._brandon_hedge_legs[1]:
            expected = hedge_position.estimate_fill_price(
                contract_type="call", strike=leg.strike, spot=7510.0, t_years=0.01)
            assert leg.fill_price == pytest.approx(expected)
