"""calendar_chain (Strategy D Phase 2) — pure two-expiry selection tests.

Deterministic: candidate lists / today are passed in, holidays are stubbed, so
no live clock or real calendar is needed.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.calendar_chain import (
    generate_candidate_expiries,
    pick_calendar_expiries,
)

# Anchor: 2026-06-15 is a Monday (2026-06-14 is Sunday). 06-19 / 06-26 are Fridays.
TODAY = "2026-06-15"
NO_HOLIDAYS = lambda d: False  # noqa: E731


class TestGenerateCandidates:
    def test_excludes_today_and_weekends(self):
        got = generate_candidate_expiries(TODAY, 7, is_holiday=NO_HOLIDAYS)
        # +1..+7 = Jun16..Jun22; Jun20(Sat)/Jun21(Sun) dropped; today excluded.
        assert got == ["2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19", "2026-06-22"]

    def test_excludes_holiday(self):
        got = generate_candidate_expiries(
            TODAY, 7, is_holiday=lambda d: d.isoformat() == "2026-06-19"
        )
        assert "2026-06-19" not in got
        assert "2026-06-18" in got


class TestPickExpiries:
    # Trading-day candidates Jun16..Jun30 (weekends removed).
    CANDIDATES = [
        "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19",  # Tue-Fri (DTE 1-4)
        "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26",  # Mon-Fri (DTE 7-11)
        "2026-06-29", "2026-06-30",  # Mon/Tue (DTE 14-15)
    ]

    def test_prefer_friday_picks_in_window_friday_and_nearest_long(self):
        got = pick_calendar_expiries(
            self.CANDIDATES, TODAY,
            short_dte_min=6, short_dte_max=15,
            long_extra_min=1, long_extra_max=4,
            prefer_friday=True,
        )
        # Friday in [6,15] DTE = Jun26 (DTE 11). Long: smallest gap 1-4 after it =
        # Jun29 (DTE 14, gap 3 — Jun27/28 are a weekend).
        assert got == ("2026-06-26", "2026-06-29")

    def test_no_friday_pref_picks_earliest_in_window(self):
        got = pick_calendar_expiries(
            self.CANDIDATES, TODAY,
            short_dte_min=6, short_dte_max=15,
            long_extra_min=1, long_extra_max=4,
            prefer_friday=False,
        )
        # Earliest in [6,15] = Jun22 (DTE 7). Long gap 1-4 = Jun23 (DTE 8, gap 1).
        assert got == ("2026-06-22", "2026-06-23")

    def test_short_is_a_friday_when_preferred(self):
        short, _ = pick_calendar_expiries(
            self.CANDIDATES, TODAY, 6, 15, 1, 4, prefer_friday=True
        )
        assert date.fromisoformat(short).weekday() == 4  # Friday

    def test_long_gap_within_bounds(self):
        short, long = pick_calendar_expiries(
            self.CANDIDATES, TODAY, 6, 15, 1, 4, prefer_friday=True
        )
        gap = (date.fromisoformat(long) - date.fromisoformat(short)).days
        assert 1 <= gap <= 4

    def test_none_when_no_short_in_window(self):
        # Window 6-15 DTE but only DTE 1-4 candidates available.
        got = pick_calendar_expiries(
            ["2026-06-16", "2026-06-17", "2026-06-18"], TODAY,
            short_dte_min=6, short_dte_max=15, long_extra_min=1, long_extra_max=4,
        )
        assert got is None

    def test_none_when_no_long_in_gap(self):
        # A valid short (Jun26 DTE11) but no candidate within +1..+4 of it.
        got = pick_calendar_expiries(
            ["2026-06-26"], TODAY,
            short_dte_min=6, short_dte_max=15, long_extra_min=1, long_extra_max=4,
        )
        assert got is None

    def test_backtracks_when_preferred_friday_has_no_long(self):
        # Friday Jun26 (DTE 11) is preferred but has NO long in +1..+4 here.
        # The earlier non-Friday short Jun22 (DTE 7) does (Jun23, gap 1) — so a
        # valid pair must still be returned rather than None.
        cands = ["2026-06-22", "2026-06-23", "2026-06-26"]  # DTE 7, 8, 11
        got = pick_calendar_expiries(
            cands, TODAY, short_dte_min=6, short_dte_max=15,
            long_extra_min=1, long_extra_max=4, prefer_friday=True,
        )
        assert got == ("2026-06-22", "2026-06-23")
