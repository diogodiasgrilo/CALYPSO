"""A money-path commit made at a weekend must still cost a trading day.

THE BUG. `streak()` only ever looks up TRADING-day keys, and commits were keyed
on their raw calendar date. So a commit made on a Saturday or Sunday matched no
key and was **never counted**. The gate is "15 consecutive trading days with no
economics-changing commit", and its point is that the measured days ran
UNCHANGED code — a weekend commit breaks that intent while passing the check.

Found 2026-10-04 while checking whether a Sunday deploy was "free on the clock".
It was, and that was the bug.

The last test is the control: it reproduces the OLD keying and shows the commit
vanishing, so if attribution is ever reverted this file explains what broke.
"""
from datetime import date, timedelta

import scripts.measurement_clock as mc


def _commit(d, sha="abc1234", subject="fix: touch strategy"):
    return {"sha": sha, "date": d.isoformat(), "subject": subject,
            "paths": ["bots/hydra/strategy.py"], "exempt": []}


# A known Sat/Sun/Mon triple with no US market holiday in it.
SAT = date(2026, 10, 3)
SUN = date(2026, 10, 4)
MON = date(2026, 10, 5)
FRI = date(2026, 10, 2)


class TestWeekendCommitsAttributeForward:
    def test_saturday_lands_on_monday(self):
        by, deferred = mc.attribute_to_trading_days([_commit(SAT)])
        assert MON.isoformat() in by, by
        assert len(deferred) == 1
        assert deferred[0]["effective_date"] == MON.isoformat()

    def test_sunday_lands_on_monday(self):
        by, deferred = mc.attribute_to_trading_days([_commit(SUN)])
        assert MON.isoformat() in by, by
        assert deferred[0]["deferred"] is True

    def test_a_weekday_commit_is_not_moved(self):
        by, deferred = mc.attribute_to_trading_days([_commit(FRI)])
        assert FRI.isoformat() in by, by
        assert deferred == [], "a trading-day commit must not be shifted"

    def test_next_trading_day_skips_a_market_holiday(self):
        """Christmas 2026-12-25 is a Friday; the next trading day is Monday 28th."""
        got = mc.next_trading_day_on_or_after(date(2026, 12, 25))
        assert got > date(2026, 12, 25), got
        assert got.weekday() < 5

    def test_helper_is_idempotent_on_a_trading_day(self):
        assert mc.next_trading_day_on_or_after(FRI) == FRI

    def test_it_terminates_on_a_long_holiday_run(self):
        """The guard must stop rather than loop if holidays ever misbehave."""
        got = mc.next_trading_day_on_or_after(SAT, hol={})
        assert got == MON


class TestTheStreakActuallyBreaks:
    def test_a_weekend_commit_breaks_the_following_trading_day(self):
        by, _ = mc.attribute_to_trading_days([_commit(SUN)])
        # days newest-first, as trading_days_back yields them
        days = [MON, FRI, FRI - timedelta(days=1)]
        n, broke, hits = mc.streak(days, by, honour_exemptions=False)
        assert n == 0, "Monday should be broken by the Sunday commit"
        assert broke == MON
        assert hits and hits[0]["sha"] == "abc1234"

    def test_CONTROL_the_old_raw_date_keying_loses_it_entirely(self):
        """NEGATIVE CONTROL — the bug, reproduced.

        Keying on the raw calendar date (what main() did until 2026-10-04) makes
        a Sunday money-path commit invisible: the streak runs clean through
        Monday as though nothing had shipped.
        """
        c = _commit(SUN)
        old_style = {c["date"]: [c]}          # the pre-fix keying
        days = [MON, FRI, FRI - timedelta(days=1)]
        n, broke, _ = mc.streak(days, old_style, honour_exemptions=False)
        assert n == len(days) and broke is None, (
            "the control did not reproduce the bug — raw-date keying should "
            "make the weekend commit vanish")
        # ...and the fix must disagree with it on the same input.
        by, _ = mc.attribute_to_trading_days([_commit(SUN)])
        assert mc.streak(days, by, honour_exemptions=False)[0] == 0, (
            "attribution did not fix what the control just demonstrated")


class TestHolidaysAreActuallyExcluded:
    """The holiday exclusion was a no-op from the day it was written.

    `get_us_market_holidays(year)` returns {holiday NAME: datetime}, and the
    original built its set by iterating the dict — i.e. over the NAMES. So the
    set held strings like "Christmas Day" and `d not in hol` never matched.
    Market holidays counted as clean trading days and inflated the streak.
    """

    def test_the_set_contains_dates_not_names(self):
        hol = mc._market_holidays()
        assert hol, "no holidays loaded at all"
        assert all(isinstance(d, date) for d in hol), (
            "holiday set contains non-dates — this is the name-keyed bug: %r"
            % [d for d in hol if not isinstance(d, date)][:3])

    def test_labor_day_is_not_a_trading_day(self):
        assert mc._is_trading_day(date(2026, 9, 7)) is False

    def test_a_holiday_is_excluded_from_the_window(self):
        assert date(2026, 9, 7) not in mc.trading_days_back(40)

    def test_christmas_friday_pushes_to_the_following_monday(self):
        assert mc.next_trading_day_on_or_after(date(2026, 12, 25)) == date(2026, 12, 28)

    def test_thanksgiving_pushes_to_the_friday(self):
        assert mc.next_trading_day_on_or_after(date(2026, 11, 26)) == date(2026, 11, 27)

    def test_CONTROL_name_keyed_set_matches_nothing(self):
        """NEGATIVE CONTROL — the bug, reproduced.

        Build the set the old way (iterating the dict, so over its keys) and
        show a known holiday is NOT in it, which is why the check never fired.
        """
        from shared.market_hours import get_us_market_holidays
        old_style = {d: True for d in get_us_market_holidays(2026)}   # KEYS = names
        assert date(2026, 9, 7) not in old_style, "control failed to reproduce"
        assert "Labor Day" in old_style, (
            "control is not actually reproducing the name-keyed shape")
        # and the fixed set does contain it
        assert date(2026, 9, 7) in mc._market_holidays()
