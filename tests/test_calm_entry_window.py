"""MKT-043 forensic fix (2026-06-08): the calm-entry wait must not consume the
whole entry window.

These pin the placement-window-remaining helper that the calm-wait cap relies
on, plus the cap arithmetic itself (max_delay = min(calm_budget, remaining −
headroom)). The forensic found the calm wait (max calm_entry_max_delay_min,
default 5min) equalled the 5-min entry window, so an entry that needed a retry
hit "window expired" and never placed.
"""
from __future__ import annotations

import sys
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import ENTRY_WINDOW_MINUTES  # noqa: E402
from bots.hydra.strategy import (  # noqa: E402
    CALM_PLACEMENT_HEADROOM_SEC,
    HydraStrategy,
)


def _strategy(entry_times, idx):
    s = HydraStrategy.__new__(HydraStrategy)
    s.entry_times = entry_times
    s._next_entry_index = idx
    return s


def _at(h, m, s=0):
    return datetime(2026, 6, 9, h, m, s)


class TestEntryWindowRemaining:
    def test_full_window_at_scheduled_time(self):
        s = _strategy([dt_time(10, 45)], 0)
        with patch("bots.hydra.strategy.get_us_market_time", return_value=_at(10, 45)):
            assert s._entry_window_remaining_sec() == ENTRY_WINDOW_MINUTES * 60

    def test_partway_through_window(self):
        s = _strategy([dt_time(10, 45)], 0)
        # 2 min into a (say) 5-min window → 3 min left.
        with patch("bots.hydra.strategy.get_us_market_time", return_value=_at(10, 47)):
            assert s._entry_window_remaining_sec() == (ENTRY_WINDOW_MINUTES - 2) * 60

    def test_negative_after_window_closed(self):
        s = _strategy([dt_time(10, 45)], 0)
        closed = _at(10, 45) + timedelta(minutes=ENTRY_WINDOW_MINUTES + 1)
        with patch("bots.hydra.strategy.get_us_market_time", return_value=closed):
            assert s._entry_window_remaining_sec() < 0

    def test_none_when_no_current_entry(self):
        s = _strategy([dt_time(10, 45)], 1)  # idx past the end
        with patch("bots.hydra.strategy.get_us_market_time", return_value=_at(10, 45)):
            assert s._entry_window_remaining_sec() is None


class TestCalmWaitCap:
    """The cap arithmetic the forensic prescribed: never wait past
    (window_remaining − placement headroom), and never beyond the configured
    calm budget."""

    @staticmethod
    def _capped(calm_min, window_remaining):
        max_delay = calm_min * 60
        if window_remaining is not None:
            max_delay = min(max_delay, max(0.0, window_remaining - CALM_PLACEMENT_HEADROOM_SEC))
        return max_delay

    def test_early_in_long_window_allows_full_calm_budget(self):
        # 8-min window, 5-min calm budget, fresh entry → full 300s allowed
        # (480 − 60 headroom = 420 > 300).
        assert self._capped(5, 480) == 300

    def test_late_in_window_caps_below_budget(self):
        # Only 120s of window left → calm wait capped to 60s (120 − 60 headroom).
        assert self._capped(5, 120) == 60

    def test_no_headroom_yields_zero_wait(self):
        # Window almost closed → no calm wait at all (placement gets priority).
        assert self._capped(5, 40) == 0
        assert self._capped(5, 0) == 0

    def test_none_remaining_uses_full_budget(self):
        assert self._capped(5, None) == 300
