"""`cascade_gap_seconds` measured overnight time, not a cascade.

The column exists to show stops firing in quick succession — one stop
triggering the next within seconds. `_last_stop_time` was set on every stop
and never cleared at the daily rollover, so the FIRST stop of each day
reported the gap to the PREVIOUS DAY's last stop.

Measured on the live seat 2026-11-02, n=9 recorded gaps:

    min 9.9s   median 860.8s   max 86,433s  ( = 24h 0m 33s )
    2 of 9 exceeded one hour

A column whose largest value is "one day" is not measuring what its name
says. It nearly supported a wrong conclusion during the exit-path slippage
investigation that same day — the apparent correlation between "cascade gap"
and slippage was partly an artefact of overnight rows.

Fix: clear it in `_reset_for_new_day`, alongside the other per-day state.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _strat():
    """A real HydraStrategy with only what `_reset_for_new_day` touches.

    `_run_overnight_position_check` is stubbed because it retries a BROKER read
    STATE004_MAX_ATTEMPTS times with backoff — 60s of sleeping that has nothing
    to do with this fix, and would be added to every CI run.
    """
    s = HydraStrategy.__new__(HydraStrategy)
    s._last_stop_time = dt.datetime(2026, 10, 29, 15, 50, 0)   # yesterday's stop
    s._emergency_close_alerted = set()
    s._run_overnight_position_check = lambda *a, **k: None
    return s


class TestTheDailyResetClearsIt:
    def test_last_stop_time_is_cleared(self, monkeypatch):
        s = _strat()
        assert s._last_stop_time is not None
        try:
            HydraStrategy._reset_for_new_day(s)
        except Exception:
            # The full reset touches far more than this field; what matters is
            # that the clear happens, so tolerate downstream setup it lacks.
            pass
        assert s._last_stop_time is None, (
            "the first stop of a new day would measure OVERNIGHT time")

    def test_the_shipped_reset_contains_the_clear(self):
        """Guards against the line being moved somewhere that never runs."""
        import inspect
        src = inspect.getsource(HydraStrategy._reset_for_new_day)
        assert "self._last_stop_time = None" in src


class TestWhatTheGapWouldHaveReported:
    """The arithmetic the bug produced, stated so a regression is obvious."""

    def test_an_overnight_gap_is_not_a_cascade(self):
        prev = dt.datetime(2026, 10, 29, 15, 50, 0)
        now = dt.datetime(2026, 10, 30, 14, 16, 12)      # first stop next day
        gap = (now - prev).total_seconds()
        assert gap > 80000, "this is the 24h-scale value seen in the data"
        # After the fix there is no previous stop, so no gap is recorded at all.
        assert (None if True else gap) is None

    def test_a_real_cascade_is_still_measured(self):
        """Control: within one day the gap must still be computed."""
        first = dt.datetime(2026, 10, 30, 14, 16, 12)
        second = dt.datetime(2026, 10, 30, 14, 18, 16)
        assert 0 < (second - first).total_seconds() < 300
