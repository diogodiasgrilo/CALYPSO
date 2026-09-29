"""A restarted process must not bury a day that traded under a zero summary.

WHAT HAPPENED. The 2026-05-29 calypso-broker cutover restarted A, B and C
mid-session. Every one lost position monitoring within eight seconds of the
others — spread_snapshots stop at 12:23:11, 12:23:17 and 12:23:19 — and each
then wrote a settlement summary of 0 entries / $0 gross / $0 net over a day
with real fills (time_to_fill_ms 2750-7494ms across nine entries, zero skip
rows, market ticks running to 15:59).

Reconstructed from surviving credits, strikes and the settlement price — all
four positions closed comfortably inside their shorts — those days were worth
**+$3,510.20**. The books read zero. B 2026-05-05 is a fourth instance.

WHY IT SURVIVED FOUR MONTHS. The zero was plausible. HOMER read it, found no
entries, and wrote a confident narrative into the trading journal about
MKT-011 credit-gate failures at VIX 15.24 — an explanation NO row in the
database supports, ticked as reconciled. A MISSING row would have been
noticed; a zero row reconciles to itself.

Hence the shape of the fix: REFUSE, do not write. A gap is visibly a gap.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.data_recorder import DataRecorder  # noqa: E402


def _rec(tmp: Path) -> DataRecorder:
    r = DataRecorder(db_path=str(tmp / "t.db"))
    # Tables are created lazily; the guard queries trade_entries, so the
    # schema has to exist before a test can seed it.
    r.ensure_schema()
    return r


def _rows(r, date):
    con = sqlite3.connect(r.db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM daily_summaries WHERE date=?", (date,)).fetchone()[0]
    finally:
        con.close()


def _add_entry(r, date, n=1):
    con = sqlite3.connect(r.db_path)
    try:
        con.execute(
            "INSERT INTO trade_entries (date, entry_number, entry_time) VALUES (?,?,?)",
            (date, n, f"{date} 10:45:00"))
        con.commit()
    finally:
        con.close()


ZERO = {"date": "2026-05-29", "entries_placed": 0, "gross_pnl": 0.0,
        "net_pnl": 0.0, "spx_close": 7581.95}


class TestTheGuardFiresOnTheContradiction:
    def test_zero_summary_is_REFUSED_when_entries_exist(self):
        with tempfile.TemporaryDirectory() as td:
            r = _rec(Path(td))
            _add_entry(r, "2026-05-29")
            r.record_daily_summary(dict(ZERO))
            assert _rows(r, "2026-05-29") == 0, (
                "a zero summary was written over a day that has entry rows — "
                "this is the 2026-05-29 bug")

    def test_two_entries_also_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r = _rec(Path(td))
            _add_entry(r, "2026-05-29", 1)
            _add_entry(r, "2026-05-29", 2)
            r.record_daily_summary(dict(ZERO))
            assert _rows(r, "2026-05-29") == 0


class TestItDoesNotBreakTheNORMALCases:
    """A guard that blocks legitimate writes is worse than the bug."""

    def test_a_genuinely_flat_day_still_writes(self):
        """No entries recorded and nothing traded — the zero is TRUE."""
        with tempfile.TemporaryDirectory() as td:
            r = _rec(Path(td))
            r.record_daily_summary(dict(ZERO))
            assert _rows(r, "2026-05-29") == 1, (
                "a real flat day was refused — the guard is too broad")

    def test_a_day_with_entries_AND_pnl_writes(self):
        with tempfile.TemporaryDirectory() as td:
            r = _rec(Path(td))
            _add_entry(r, "2026-05-29")
            ok = dict(ZERO, entries_placed=2, gross_pnl=495.0, net_pnl=485.8)
            r.record_daily_summary(ok)
            assert _rows(r, "2026-05-29") == 1

    def test_a_losing_day_with_entries_writes(self):
        """gross and net are negative, not zero — nothing contradictory."""
        with tempfile.TemporaryDirectory() as td:
            r = _rec(Path(td))
            _add_entry(r, "2026-05-29")
            r.record_daily_summary(dict(ZERO, entries_placed=1,
                                        gross_pnl=-300.0, net_pnl=-310.0))
            assert _rows(r, "2026-05-29") == 1

    def test_entries_on_a_DIFFERENT_date_do_not_block(self):
        """The check must be per-date or every zero day after the first trade
        would be refused forever."""
        with tempfile.TemporaryDirectory() as td:
            r = _rec(Path(td))
            _add_entry(r, "2026-05-28")
            r.record_daily_summary(dict(ZERO))
            assert _rows(r, "2026-05-29") == 1


class TestTheGuardCannotBlockSettlement:
    def test_it_writes_anyway_if_the_check_itself_fails(self):
        """A guard that throws would stop settlement for every variant. It
        must fail OPEN — the bug it prevents is rare, settlement is nightly."""
        import inspect
        src = inspect.getsource(DataRecorder.record_daily_summary)
        i = src.index("AMNESIA GUARD")
        # Anchor on the except clause itself rather than a character count —
        # the first version used src[i:i+3000] and cut off twenty characters
        # before the line it was checking for.
        j = src.index("except Exception", i)
        handler = src[j:j + 400]
        assert "writing anyway" in handler, (
            "the guard's exception handler does not fall through to the write")

    def test_it_logs_critically_rather_than_silently(self):
        """A silent refusal is a different invisible failure."""
        import inspect
        src = inspect.getsource(DataRecorder.record_daily_summary)
        i = src.index("AMNESIA GUARD")
        assert "logger.critical" in src[i:i + 3000]
