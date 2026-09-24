"""Variant H trades SPY, because that is what the source trades.

Operator, twice: *"I wanna run it JUST like the video says NOTHING different"*
and *"why can't H be exactly like in the video."*

H was built on SPX "to match the rest of the fleet" — **our** preference, not a
constraint, and the spec's own §1 records that the source does not restrict the
underlying. The choice was never free:

* The identical strangle costs **~$115 on SPY and ~$775 on SPX**. That is
  precisely how ``sizing_for_zero_max_loss: 500`` came to permit **zero
  contracts**, which would have produced an empty dataset from a variant whose
  only purpose is to collect one. On SPY the source's own sizing rule behaves as
  he describes it — a $1,200 limit buys ~10 contracts rather than one or none.
* Verified live before switching (2026-09-24, pre-open): SPY quotes **real-time**
  (``6509='Rp'``) where SPX was frozen, the 0DTE chain resolves with 329 strikes
  at **$1** spacing near the money, and variant E already trades SPY.

THE LANDMINE THIS EXPOSED
--------------------------
``MAX_SNAP_DISTANCE = 25.0`` is half the widest far-OTM *SPXW* gap — entirely
correct on SPX, where near-the-money strikes are 5pt apart. On SPY's **$1**
chain the same constant authorises snapping **twenty-five strikes**. The entry
would still be placed, still be logged, and still look ordinary; it would simply
be a different position from the one the strategy chose. A hardcoded tolerance is
an instrument assumption in disguise, so it is now MEASURED from the chain.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.long_strangle_chain import (  # noqa: E402
    chain_snap_cap,
    select_strangle_strikes,
    size_for_zero,
)

CFG_PATH = ROOT / "bots" / "hydra" / "config" / "config_variant_h.json"
CFG = json.loads(CFG_PATH.read_text())["strategy"]
LS = CFG["long_strangle"]

#: Measured off the live chain, 2026-09-24 pre-open.
SPY_SPOT = 764.86
SPY_STRIKES = [float(k) for k in range(700, 830)]          # $1 spacing
SPX_STRIKES = [7000.0 + 5 * i for i in range(200)]         # 5pt spacing


class TestHTradesTheUnderlyingTheSourceTrades:

    def test_the_underlying_is_SPY(self):
        assert CFG["underlying_symbol"] == "SPY"

    def test_the_option_class_follows_the_underlying(self):
        """SPXW is the SPX 0DTE class; leaving it behind on a SPY config would
        fail contract qualification rather than trade the wrong thing."""
        assert CFG["trading_class"] == "SPY"

    def test_it_is_still_zero_DTE(self):
        assert CFG["target_dte"] == 0

    def test_the_switch_records_why_and_what_was_verified(self):
        raw = CFG_PATH.read_text()
        assert "_comment_underlying_symbol" in raw
        assert "AMERICAN" in raw and "PHYSICALLY settled" in raw, (
            "the assignment consequence must be written down where the switch "
            "was made, not only in a commit message")


class TestTheSnapToleranceIsMeasuredNotAssumed:
    """The bug the switch would have caused."""

    def test_the_old_constant_would_snap_25_strikes_on_SPY(self):
        """The defect, stated as arithmetic. With a 25.0 tolerance a target of
        764.9 resolves happily to 740 — a strike 25 dollars away — because the
        chain genuinely lists one there."""
        from bots.hydra.long_strangle_chain import snap_to_chain
        sparse = [740.0, 790.0]          # the only strikes the snapshot returned
        assert snap_to_chain(764.9, sparse, 25.0) == 740.0      # old behaviour
        assert snap_to_chain(764.9, sparse, chain_snap_cap(SPY_STRIKES, SPY_SPOT)) is None

    def test_the_cap_scales_to_the_instrument(self):
        assert chain_snap_cap(SPY_STRIKES, SPY_SPOT) == pytest.approx(2.5)
        assert chain_snap_cap(SPX_STRIKES, 7706.0) == pytest.approx(12.5)

    def test_the_SPY_cap_is_far_tighter_than_the_old_constant(self):
        assert chain_snap_cap(SPY_STRIKES, SPY_SPOT) < 25.0 / 5

    def test_an_unmeasurable_chain_falls_back_rather_than_crashing(self):
        assert chain_snap_cap([], 764.9) == 25.0
        assert chain_snap_cap([764.0], 764.9) == 25.0

    def test_a_normal_SPY_selection_still_resolves_both_legs(self):
        """The no-op control: tightening the cap must not make H unable to
        trade. A $6 expected move on a $1 chain lands exactly on listed strikes."""
        call_k, put_k = select_strangle_strikes(
            SPY_SPOT, 6.0, SPY_STRIKES, chain_snap_cap(SPY_STRIKES, SPY_SPOT))
        assert (call_k, put_k) == (771.0, 759.0)

    def test_the_config_leaves_it_derived(self):
        assert CFG.get("max_snap_distance") is None, (
            "pinning a number reintroduces the instrument assumption")


class TestTheSourcesOwnSizingRuleNowBehavesAsDescribed:
    """The measurable consequence of the switch, and the reason it matters."""

    SPY_DEBIT = 113.0      # 0.1483% of 765 -> $1.13/share -> $113/contract
    SPX_DEBIT = 775.0      # measured by the RTH probe at SPX 7724

    def test_on_SPX_his_rule_bought_one_contract(self):
        assert size_for_zero(LS["sizing_for_zero_max_loss"], self.SPX_DEBIT) == 1

    def test_on_SPX_the_ORIGINAL_500_limit_bought_NONE(self):
        """The empty-dataset bug, preserved as the argument for the switch."""
        assert size_for_zero(500, self.SPX_DEBIT) == 0

    def test_on_SPY_the_same_limit_buys_about_ten(self):
        """Which is how the source describes his own rule working."""
        assert size_for_zero(LS["sizing_for_zero_max_loss"], self.SPY_DEBIT) == 10

    def test_even_a_small_limit_buys_a_position_on_SPY(self):
        """No limit a real person would choose silently produces nothing."""
        assert size_for_zero(500, self.SPY_DEBIT) >= 4

    def test_the_cost_cap_ported_without_being_touched(self):
        """It is expressed relative to SPOT, so his ~$1.15/share SPY cap and the
        ~$1,145/contract SPX equivalent are the SAME number. Spot-relative was
        the right shape; this is the control proving the switch needed no edit."""
        pct = LS["max_debit_pct_of_spot"] / 100.0
        assert pct * SPY_SPOT == pytest.approx(1.13, abs=0.03)   # his SPY cap
        assert pct * 7706.0 == pytest.approx(11.43, abs=0.10)    # the SPX one
