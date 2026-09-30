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


class TestTheAuditACTUALLYRUNS:
    """Every other test in this file reads the SOURCE. None of them executes it.

    That gap shipped a crash: the calendar lookup was inserted BELOW the loop
    that consumes it, so `audit()` died with

        UnboundLocalError: cannot access local variable 'cal_adjusted'

    on the first real database — while all six source-grep tests passed. A test
    that never calls the function cannot tell working code from a NameError.
    """

    def _db(self, tmp, with_calendar: bool):
        import sqlite3
        from pathlib import Path
        data = Path(tmp) / "data" / "variant_d"
        data.mkdir(parents=True)
        con = sqlite3.connect(data / "backtesting.db")
        con.executescript("""
            CREATE TABLE daily_summaries (date TEXT, gross_pnl REAL, net_pnl REAL,
                commission REAL, entries_placed INT, unattributed_overlay_pnl REAL);
            CREATE TABLE trade_entries (date TEXT, entry_number INT, realized_pnl REAL);
            INSERT INTO daily_summaries VALUES ('2026-08-17',0.0,-323.75,0.0,0,0.0);
        """)
        con.commit(); con.close()
        if with_calendar:
            cal = sqlite3.connect(data / "dc_calendar.db")
            cal.executescript("""
                CREATE TABLE dc_metrics_adjustments (strategy_id TEXT, close_date TEXT,
                    amount REAL, applied_at TEXT);
                CREATE TABLE dc_outcomes (entry_date TEXT, close_date TEXT,
                    realized_pnl REAL);
                INSERT INTO dc_metrics_adjustments VALUES ('x','2026-08-17',-323.75,'z');
            """)
            cal.commit(); cal.close()
        return Path(tmp)

    def test_it_runs_without_raising(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = self._db(td, with_calendar=True)
            afc.audit(root, "d", "2026-09-30")   # must not raise

    def test_a_logged_adjustment_is_classified_not_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = self._db(td, with_calendar=True)
            tags = {t for _n, t, _m in afc.audit(root, "d", "2026-09-30")}
            assert "ADJUSTED" in tags, tags
            assert "I2" not in tags, f"a logged adjustment was called a break: {tags}"

    def test_the_SAME_row_IS_flagged_without_the_ledger(self):
        """The negative control, executed rather than grepped: with no
        dc_calendar.db the discrepancy is unexplained and must report I2."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = self._db(td, with_calendar=False)
            tags = {t for _n, t, _m in afc.audit(root, "d", "2026-09-30")}
            assert "I2" in tags, f"an unexplained discrepancy was not flagged: {tags}"


class TestThePreFixCutoff:
    """A check that is permanently red is not a control.

    F's accounting was wrong until 2026-09-18 — it booked every take-profit
    twice, counted no entries, and never charged the opening commission. Rows
    written before that date are history, not live defects, and they cannot be
    repaired. Left as gating breaks they would fail the nightly unit forever,
    which is how a signal becomes something people scroll past.

    They are still PRINTED — downgraded, never hidden.
    """

    def test_a_pre_fix_row_is_not_a_gating_break(self):
        from scripts.audit_fleet_correctness import _is_pre_fix
        assert _is_pre_fix("f", "2026-09-14: gross -12.50 but ZERO trade_entries rows")

    def test_a_row_ON_the_fix_date_still_gates(self):
        """The cutoff is exclusive: the fix landed that day, so that day counts."""
        from scripts.audit_fleet_correctness import _is_pre_fix
        assert not _is_pre_fix("f", "2026-09-18: gross -12.50 but ZERO rows")

    def test_a_row_AFTER_the_fix_still_gates(self):
        from scripts.audit_fleet_correctness import _is_pre_fix
        assert not _is_pre_fix("f", "2026-09-29: gross -12.50 but ZERO rows")

    def test_a_variant_with_no_cutoff_is_never_excused(self):
        """B is the live seat. Nothing about it is ever pre-fix."""
        from scripts.audit_fleet_correctness import _is_pre_fix
        assert not _is_pre_fix("b", "2020-01-01: gross -12.50 but ZERO rows")

    def test_a_message_with_no_date_is_never_excused(self):
        from scripts.audit_fleet_correctness import _is_pre_fix
        assert not _is_pre_fix("f", "no date in this message at all")

    def test_the_cutoff_table_is_small_and_explicit(self):
        """Every entry is an assertion that the data before it is understood
        and unrepairable. Growth here should be deliberate and reviewed."""
        from scripts.audit_fleet_correctness import ACCOUNTING_FIXED_ON
        assert set(ACCOUNTING_FIXED_ON) == {"f"}, (
            f"the pre-fix excuse list grew to {sorted(ACCOUNTING_FIXED_ON)} — "
            f"each entry silences a gating break, so adding one needs a dated, "
            f"committed explanation of what was fixed")
        assert "b" not in ACCOUNTING_FIXED_ON, "the live seat must never be excused"


class TestTheGateStillGates:
    """The control my unit tests missed.

    Everything above calls `_is_pre_fix` directly, so none of it touches the
    loop that APPLIES it. Mutating that loop to treat every break as pre-fix —
    silencing the gate completely — left the suite fully green. A classifier
    that is only tested in isolation cannot tell you whether the thing using it
    still works.

    So this builds a real fleet database with a POST-fix break and asserts
    `main()` still returns non-zero, which is what systemd gates the nightly
    unit on.
    """

    def _fleet(self, tmp_path, vid, break_date):
        """A real fleet DB built with the REAL schema.

        First attempt hand-wrote the tables and died on `no such column:
        realized_pnl` — approximating a schema in a test just encodes my guess
        about it. `ensure_schema()` is the same call production makes.
        """
        import sqlite3
        from shared.data_recorder import DataRecorder
        d = tmp_path / "data" / f"variant_{vid}"
        d.mkdir(parents=True)
        db = str(d / "backtesting.db")
        DataRecorder(db).ensure_schema()
        con = sqlite3.connect(db)
        # real gross, and deliberately NO trade_entries rows -> I5
        con.execute("INSERT INTO daily_summaries (date, gross_pnl, net_pnl, "
                    "commission, entries_placed, entries_stopped, entries_expired) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (break_date, -12.50, -12.50, 0.0, 0, 0, 0))
        con.commit()
        con.close()
        return tmp_path

    def test_a_POST_fix_break_still_fails_the_unit(self, tmp_path, capsys):
        from scripts import audit_fleet_correctness as afc
        root = self._fleet(tmp_path, "f", "2026-09-29")     # after the cutoff
        rc = afc.main(["--root", str(root)]) if _takes_argv(afc.main) else None
        if rc is None:
            pytest.skip("main() does not accept argv; covered by the CLI test")
        out = capsys.readouterr().out
        assert rc == 1, (
            f"main() returned {rc} on a post-fix identity break — systemd gates "
            f"the nightly unit on this exit code, so a 0 here silences the gate")
        assert "I5" in out

    def test_a_PRE_fix_break_does_NOT_fail_the_unit(self, tmp_path, capsys):
        from scripts import audit_fleet_correctness as afc
        root = self._fleet(tmp_path, "f", "2026-09-14")     # before the cutoff
        rc = afc.main(["--root", str(root)]) if _takes_argv(afc.main) else None
        if rc is None:
            pytest.skip("main() does not accept argv; covered by the CLI test")
        out = capsys.readouterr().out
        assert rc == 0, f"a pre-fix row gated the unit (rc={rc}) — it would stay red forever"
        assert "PRE-FIX" in out, "the row was hidden rather than downgraded"


def _takes_argv(fn):
    import inspect
    return len(inspect.signature(fn).parameters) >= 1
