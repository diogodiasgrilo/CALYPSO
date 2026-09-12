"""Tests for the pure market-data kernels (modularity-audit item 3, first slice).

These functions are now importable + testable without constructing HydraStrategy.
HydraStrategy's _normalize_chart_bar / _snap_to_chain_strike / _snap_long_for_spread
delegate here, so these also pin the strategy's behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.market_data_adapter import (
    normalize_chart_bar,
    snap_long_for_spread,
    snap_to_chain_strike,
)


class TestNormalizeChartBar:
    def test_full_bar(self):
        out = normalize_chart_bar({"o": "1.0", "h": "2.0", "l": "0.5", "c": "1.5", "v": "100", "t": "1700000000000"})
        assert out == {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                       "volume": 100, "timestamp_ms": 1700000000000}

    def test_missing_and_malformed_degrade_to_zero(self):
        out = normalize_chart_bar({"o": None, "h": "", "l": "bad", "c": 1.25})
        assert out["open"] == 0.0 and out["high"] == 0.0 and out["low"] == 0.0
        assert out["close"] == 1.25
        assert out["volume"] == 0 and out["timestamp_ms"] == 0

    def test_empty_bar(self):
        assert normalize_chart_bar({}) == {
            "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0,
            "volume": 0, "timestamp_ms": 0,
        }


class TestSnapToChainStrike:
    def test_exact_hit(self):
        assert snap_to_chain_strike(7500.0, {7500.0: 1, 7505.0: 2}) == (7500.0, 1)

    def test_snaps_within_tolerance(self):
        # 7503 -> nearest listed 7505 (2pt, within default 15)
        assert snap_to_chain_strike(7503.0, {7500.0: 1, 7505.0: 2}) == (7505.0, 2)

    def test_returns_none_beyond_tolerance(self):
        # 7530 is 25pt from 7505 — beyond the 15pt default
        assert snap_to_chain_strike(7530.0, {7500.0: 1, 7505.0: 2}) == (None, None)

    def test_empty_map(self):
        assert snap_to_chain_strike(7500.0, {}) == (None, None)


class TestSnapLongForSpread:
    def test_call_long_above_short(self):
        uic = {7600.0: 10, 7610.0: 11}
        # short 7500, width 110 -> ideal long 7610
        assert snap_long_for_spread(7500.0, 110, uic, is_call=True) == (7610.0, 11)

    def test_put_long_below_short(self):
        uic = {7390.0: 20, 7400.0: 21}
        # short 7500, width 110 -> ideal long 7390 (put: short - width)
        assert snap_long_for_spread(7500.0, 110, uic, is_call=False) == (7390.0, 20)

    def test_rejects_too_narrow_spread(self):
        # only a strike 30pt away exists for a 110-wide target → narrower than
        # target_width-15 (95) → rejected
        assert snap_long_for_spread(7500.0, 110, {7530.0: 9}, is_call=True) == (None, None)

    def test_none_when_no_strike_near_ideal(self):
        assert snap_long_for_spread(7500.0, 110, {7000.0: 1}, is_call=True) == (None, None)
