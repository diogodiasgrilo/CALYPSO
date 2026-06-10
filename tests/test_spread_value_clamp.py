"""2026-06-10 (L-C2b): put/call spread_value clamped to the spread's structural
[0, width] range.

A short vertical's cost-to-close can never exceed its width (the intrinsic cap)
nor fall below 0, yet spread_value is built from two independently-mid'd legs. On
2026-06-10 a wide close-auction quote on one leg marked variant C's 7290/7285 put
spread at $4270 — impossible on a 5pt/$3500-max structure — which inflated the
displayed -36% cushion + the -$3,384 P&L mark and fed a noisy value into the
MKT-046 stop confirmation (oscillation across the trigger keeps resetting the 10s
timer). The clamp pins the mark to economic reality.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.base_strategy import IronCondorEntry  # noqa: E402


def _put_entry(short_strike, long_strike, short_price, long_price, contracts):
    e = IronCondorEntry(entry_number=1)
    e.short_put_strike = short_strike
    e.long_put_strike = long_strike
    e.short_put_price = short_price
    e.long_put_price = long_price
    e.contracts = contracts
    return e


def _call_entry(short_strike, long_strike, short_price, long_price, contracts):
    e = IronCondorEntry(entry_number=1)
    e.short_call_strike = short_strike
    e.long_call_strike = long_strike
    e.short_call_price = short_price
    e.long_call_price = long_price
    e.contracts = contracts
    return e


class TestPutSpreadValueClamp:
    def test_within_width_unchanged(self):
        # 5pt spread, cost-to-close 3.20/contract -> within width -> raw value
        e = _put_entry(7290, 7285, 5.20, 2.00, 7)
        assert e.put_spread_value == 3.20 * 100 * 7

    def test_live_wide_quote_clamped_to_width(self):
        # The 2026-06-10 live case: 7290/7285 (5pt), short 23.80 / long 17.70 ->
        # raw 6.10/contract = $4270 on 7c, impossible on a $3500-max spread.
        e = _put_entry(7290, 7285, 23.80, 17.70, 7)
        assert e.put_spread_value == 5 * 100 * 7  # $3500, not $4270

    def test_negative_raw_clamped_to_zero(self):
        # crossed/bad quote (short < long) -> the spread can't be worth < 0
        e = _put_entry(7290, 7285, 1.00, 1.50, 7)
        assert e.put_spread_value == 0.0

    def test_missing_strikes_returns_raw_unclamped(self):
        # one-sided / unset put side (width 0) -> no clamp, raw passthrough
        e = _put_entry(0, 0, 0.0, 0.0, 7)
        assert e.put_spread_value == 0.0

    def test_exactly_at_width_kept(self):
        # fully ITM: worth exactly the width -> kept (boundary, not over-clamped)
        e = _put_entry(7290, 7285, 30.0, 25.0, 3)  # (30-25)=5.0 == width
        assert e.put_spread_value == 5 * 100 * 3


class TestCallSpreadValueClamp:
    def test_within_width_unchanged(self):
        e = _call_entry(7425, 7430, 4.00, 1.50, 7)  # 2.50 < width 5
        assert e.call_spread_value == 2.50 * 100 * 7

    def test_wide_quote_clamped_to_width(self):
        e = _call_entry(7425, 7430, 12.0, 2.0, 7)  # raw 10/contract on a 5pt spread
        assert e.call_spread_value == 5 * 100 * 7

    def test_negative_raw_clamped_to_zero(self):
        e = _call_entry(7425, 7430, 1.0, 2.0, 7)
        assert e.call_spread_value == 0.0

    def test_wide_spread_width_respected(self):
        # a genuinely wider spread (75pt) is clamped to ITS width, not 5pt
        e = _call_entry(7425, 7500, 50.0, 1.0, 1)  # raw 49/contract; width 75
        assert e.call_spread_value == 49.0 * 100 * 1  # under 75 width -> unchanged
