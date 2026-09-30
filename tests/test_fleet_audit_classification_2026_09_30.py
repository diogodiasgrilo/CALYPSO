"""The audit must distinguish a BREAK from a strategy it does not understand.

Three times in one session this audit reported correct data as broken, each
time from an iron-condor-shaped assumption:

  1. `SUM(realized_pnl)` read NULL as 0.00 on every pre-2026-07-01 day —
     ~131 "I1 breaks" that were one schema fact.
  2. I5 flagged 16 days on D/E/H for having no `trade_entries` rows. They keep
     entries in `dc_calendar.db` and `long_strangle.db`.
  3. I2 (`gross - commission == net`) flagged 10 days on D/E. That identity
     assumes a position opens and closes the SAME DAY.

An audit that cries wolf gets ignored, and an ignored audit is how a real
break hides — which is exactly how a $3,510 phantom-zero survived four months.

So the calendar cases are CLASSIFIED rather than skipped: skipping would throw
away a real check, and only a discrepancy that is neither an opening day nor a
logged adjustment is a genuine break.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import audit_fleet_correctness as afc  # noqa: E402


class TestCalendarDiscrepanciesAreClassifiedNotSkipped:
    def test_it_does_not_simply_skip_I2_for_calendars(self):
        """Skipping would discard a check that still catches real breaks on
        the days a calendar actually closes."""
        src = inspect.getsource(afc.audit)
        i = src.index("if abs((g - cm) - n) > TOL:")
        block = src[i:i + 1200]
        assert "cal_adjusted" in block and "cal_opened" in block, (
            "I2 is not classifying calendar discrepancies")
        assert '"I2"' in block, (
            "the I2 break path was removed entirely — an unexplained "
            "discrepancy on a calendar must still be reported")

    def test_a_logged_adjustment_is_reported_as_adjusted(self):
        """Anchored on the QUERY, not on the word appearing anywhere.

        The first version asserted `"dc_metrics_adjustments" in src` and passed
        against a control that gutted the SQL — because the comment block
        directly above it explains dc_metrics_adjustments by name. A source
        file that documents what it does will always contain the words for
        what it does.
        """
        src = inspect.getsource(afc.audit)
        assert '"ADJUSTED"' in src
        assert "SELECT close_date FROM dc_metrics_adjustments" in src, (
            "the audit does not QUERY the adjustment ledger, so a recovered "
            "calendar still reads as a break")

    def test_an_opening_day_is_reported_as_multiday(self):
        src = inspect.getsource(afc.audit)
        assert '"MULTIDAY"' in src
        assert "SELECT DISTINCT entry_date FROM dc_outcomes" in src, (
            "the audit does not QUERY dc_outcomes, so it cannot tell an "
            "opening day from a break")

    def test_classified_rows_do_not_set_the_failure_verdict(self):
        """ADJUSTED and MULTIDAY are explanations, not failures — they must
        not colour the run red or the audit is no more useful than before."""
        src = inspect.getsource(afc.main)
        i = src.index("if tag in (")
        cond = src[i:i + 160]
        for benign in ("ADJUSTED", "MULTIDAY", "NOTE", "PENDING"):
            assert benign not in cond, f"{benign} counts as a failure"
        for real in ("I1", "I2", "I3"):
            assert real in cond, f"{real} no longer counts as a failure"


class TestTheICAssumptionsAreScoped:
    def test_I5_excludes_variants_without_trade_entries(self):
        src = inspect.getsource(afc.audit)
        assert "uses_trade_entries" in src
        i = src.index("uses_trade_entries = ")
        assert all(v in src[i:i + 80] for v in ('"d"', '"e"', '"h"')), (
            "D/E/H are not excluded from the trade_entries assumption")

    def test_pre_backfill_days_are_not_counted_as_I1_breaks(self):
        src = inspect.getsource(afc.audit)
        assert "null_days" in src
        i = src.index("if d in null_days:")
        assert "pass" in src[i:i + 200], (
            "days with no per-entry realized_pnl are being tested for I1 "
            "anyway, which reads NULL as zero")
