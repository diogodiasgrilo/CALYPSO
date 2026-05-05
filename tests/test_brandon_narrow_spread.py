"""Tests for bots.hydra.brandon.narrow_spread."""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.narrow_spread import NarrowSpreadConfig, narrow_spread_width


class TestBreakpoint:
    @pytest.mark.parametrize("vix", [12.0, 15.0, 18.0, 20.0, 21.99])
    def test_below_breakpoint_returns_5(self, vix):
        assert narrow_spread_width(vix) == 5

    @pytest.mark.parametrize("vix", [22.0, 25.0, 28.0, 30.0, 50.0])
    def test_at_or_above_breakpoint_returns_10(self, vix):
        assert narrow_spread_width(vix) == 10

    def test_custom_breakpoint(self):
        cfg = NarrowSpreadConfig(breakpoint_vix=18.0)
        assert narrow_spread_width(17.9, cfg) == 5
        assert narrow_spread_width(18.0, cfg) == 10


class TestSafety:
    def test_negative_vix_returns_low_width(self):
        assert narrow_spread_width(-1.0) == 5

    def test_zero_vix_returns_low_width(self):
        assert narrow_spread_width(0.0) == 5

    def test_nan_vix_returns_low_width(self):
        assert narrow_spread_width(float("nan")) == 5

    def test_none_vix_returns_low_width(self):
        # Defensive: caller passed missing vix
        assert narrow_spread_width(None) == 5  # type: ignore[arg-type]

    def test_floor_clamps_low_config(self):
        # Bizarre config asks for 1pt — clamped to floor (5pt)
        cfg = NarrowSpreadConfig(width_low=1, width_high=2, floor_pts=5)
        assert narrow_spread_width(15.0, cfg) == 5
        assert narrow_spread_width(25.0, cfg) == 5

    def test_ceiling_clamps_high_config(self):
        # Bizarre config asks for 100pt — clamped to ceiling
        cfg = NarrowSpreadConfig(width_high=100, ceiling_pts=25)
        assert narrow_spread_width(25.0, cfg) == 25


class TestCustomWidths:
    def test_custom_low_and_high_widths(self):
        cfg = NarrowSpreadConfig(width_low=7, width_high=15, ceiling_pts=20)
        assert narrow_spread_width(15.0, cfg) == 7
        assert narrow_spread_width(25.0, cfg) == 15
