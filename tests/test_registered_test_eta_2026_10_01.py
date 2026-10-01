"""The projection must stay naive, and must not report "ready" when it isn't.

"Wait for the out-of-sample data" was repeated for weeks without anyone
computing when it arrives. It is not one answer: at B's measured rates the GEX
gate is ~12 trading days out and the e#4 prune ~70. A fortnight is worth
waiting for passively; a quarter is a decision.

The failure modes worth pinning are optimistic ones. A rate of zero must not
render as "soon", a threshold already met must not read as pending, and the
cut-off must keep excluding the in-sample history that formed the hypotheses —
28 GEX vetoes exist, and counting them would report the test as finished on
day one.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import registered_test_eta as eta  # noqa: E402


class TestTheCutoffIsRespected:
    def test_the_prereg_cutoff_matches_the_registered_documents(self):
        assert eta.PREREG_CUTOFF == "2026-09-29", (
            "the cut-off moved. PREREG_GEX_GATE fixes 2026-09-29 and counts only "
            "later decisions; loosening it readmits the data that formed the "
            "hypothesis and would report the test finished immediately.")

    def test_the_live_era_is_the_seat_swap(self):
        assert eta.LIVE_ERA == "2026-07-24"


def _db(tmp_path, rows_entries=0, rows_vetoes=0, summary_days=0, e4=0):
    from shared.data_recorder import DataRecorder
    d = tmp_path / "data" / "variant_t"
    d.mkdir(parents=True)
    p = str(d / "backtesting.db")
    DataRecorder(p).ensure_schema()
    con = sqlite3.connect(p)
    for i in range(summary_days):
        con.execute("INSERT INTO daily_summaries (date, entries_placed) VALUES (?,?)",
                    (f"2026-08-{i+1:02d}", 1))
    for i in range(rows_entries):
        con.execute("INSERT INTO trade_entries (date, entry_number) VALUES (?,?)",
                    (f"2026-08-{(i % 28)+1:02d}", 4 if i < e4 else 2))
    con.commit()
    con.close()
    return tmp_path


class TestItDoesNotFlatter:
    def test_a_zero_rate_is_not_reported_as_soon(self, tmp_path, capsys):
        """No entries at all -> the e#4 rate is 0. It must say so, not divide
        by zero or print a reassuring number."""
        root = _db(tmp_path, summary_days=10)
        assert eta.main(["--variant", "t", "--root", str(root)]) == 0
        out = capsys.readouterr().out
        assert "never@0" in out or "READY" in out, out[-500:]
        assert "inf" not in out.lower() and "nan" not in out.lower()

    def test_a_missing_database_exits_nonzero(self, tmp_path, capsys):
        assert eta.main(["--variant", "zz", "--root", str(tmp_path)]) == 2

    def test_no_live_era_days_does_not_divide_by_zero(self, tmp_path, capsys):
        root = _db(tmp_path, summary_days=0)
        assert eta.main(["--variant", "t", "--root", str(root)]) == 0
        assert "no live-era days" in capsys.readouterr().out


class TestItKeepsSayingTheProjectionIsNaive:
    def test_the_caveat_is_printed(self, tmp_path, capsys):
        root = _db(tmp_path, summary_days=10, rows_entries=5, e4=2)
        eta.main(["--variant", "t", "--root", str(root)])
        out = capsys.readouterr().out
        assert "models no trend" in out, (
            "the naive-projection caveat is gone — an extrapolated rate would "
            "then read as a forecast")
        assert "order of" in out

    def test_it_names_the_accrual_rate_as_the_binding_constraint(self, tmp_path, capsys):
        """The actionable half: the thresholds are fixed, the rate is not."""
        root = _db(tmp_path, summary_days=10, rows_entries=5, e4=2)
        eta.main(["--variant", "t", "--root", str(root)])
        out = capsys.readouterr().out
        assert "binding constraint" in out and "skipped entry" in out
