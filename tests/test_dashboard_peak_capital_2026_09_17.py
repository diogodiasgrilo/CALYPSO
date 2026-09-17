"""Deployed capital is PEAK CONCURRENT margin, not a running sum (defect D20).

Two definitions of "capital" coexisted:

    dashboard        SUM of every entry's margin, ever
    base_strategy    PEAK CONCURRENT margin, summed per day

The sum counts the same dollars again every time a position closes and another
opens. ``base_strategy._calculate_capital_deployed`` had already made this
correction inside the bot — its docstring says the prior version "SUMMED every
entry's max-loss ... the sum overstated capital" — and the dashboard simply
never followed.

Measured on real data:

    var   SUM          PEAK         overstated   roi before -> after
     a    2,561,500    1,784,500      43.5%      -0.126%  ->  -0.180%
     b    1,404,000    1,372,000       2.3%       1.731%  ->   1.770%
     c      422,500      422,500       0.0%      unchanged
     f        1,000        1,000       0.0%      unchanged
     g      780,000      660,000      18.2%       0.107%  ->   0.130%

Because ``roi_pct = pnl / capital``, the sum UNDERSTATED return for a profitable
strategy — and overstated the loss rate for a losing one, which is why A's ROI
gets WORSE under the correction. It is not uniformly flattering.

It also made the card contradict the ``daily_returns`` rows in the same metrics
file, which already use peak-concurrent. G's card now reads $50,769/day, exactly
matching its backfilled rows.

Operator decision, 2026-09-17: switch to peak-concurrent, and record the change
in the trading journal so the step in reported ROI has a written cause.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.backend.services.db_reader import BacktestingDBReader  # noqa: E402


def _db(path: Path, entries: list[tuple], stops: list[tuple] = ()) -> None:
    """entries: (date, n, entry_time, width, contracts); stops: (date, n, side, time)."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE daily_summaries (date TEXT PRIMARY KEY, net_pnl REAL, "
                "entries_placed INT, entries_stopped INT)")
    con.execute("CREATE TABLE trade_entries (date TEXT, entry_number INT, entry_time TEXT, "
                "call_spread_width REAL, put_spread_width REAL, contracts INT, total_credit REAL)")
    con.execute("CREATE TABLE trade_stops (date TEXT, entry_number INT, side TEXT, "
                "stop_time TEXT, net_pnl REAL)")
    days = {e[0] for e in entries}
    for d in days:
        con.execute("INSERT INTO daily_summaries VALUES (?,100.0,1,0)", (d,))
    for d, n, t, w, c in entries:
        con.execute("INSERT INTO trade_entries VALUES (?,?,?,?,?,?,0)", (d, n, t, w, w, c))
    for s in stops:
        con.execute("INSERT INTO trade_stops VALUES (?,?,?,?,-5)", s)
    con.commit()
    con.close()


def _cap(path: Path, **kw) -> float:
    return asyncio.run(BacktestingDBReader(path, **kw).get_cumulative_overrides())["capital_deployed"]


def test_non_overlapping_entries_count_once(tmp_path):
    """THE defect. Entry #1 stops at 10:54, entry #2 opens at 11:15 — the same
    dollars were freed and reused, so peak is ONE position, not two."""
    p = tmp_path / "a.db"
    _db(p,
        [("2026-09-16", 1, "2026-09-16 10:45:00", 0, 1),
         ("2026-09-16", 2, "2026-09-16 11:15:00", 0, 1)],
        [("2026-09-16", 1, "call", "10:54:00")])
    cap = _cap(p, capital_basis="broker_margin", broker_margin_per_contract=30_000.0)
    assert cap == pytest.approx(30_000.0), "the old SUM would report $60,000"


def test_overlapping_entries_count_twice(tmp_path):
    """The control. When both positions ARE open at once, capital really is
    both — a fix that always halved the number would be just as wrong."""
    p = tmp_path / "b.db"
    _db(p,
        [("2026-09-16", 1, "2026-09-16 10:45:00", 0, 1),
         ("2026-09-16", 2, "2026-09-16 11:15:00", 0, 1)],
        [("2026-09-16", 1, "call", "15:30:00")])   # #1 still open when #2 opens
    cap = _cap(p, capital_basis="broker_margin", broker_margin_per_contract=30_000.0)
    assert cap == pytest.approx(60_000.0)


def test_unstopped_entries_are_held_to_the_session_close(tmp_path):
    """A 0DTE entry with no stop row expired — it was open all afternoon, so it
    overlaps anything opened after it."""
    p = tmp_path / "c.db"
    _db(p, [("2026-09-16", 1, "2026-09-16 10:45:00", 0, 1),
            ("2026-09-16", 2, "2026-09-16 11:15:00", 0, 1)])
    cap = _cap(p, capital_basis="broker_margin", broker_margin_per_contract=30_000.0)
    assert cap == pytest.approx(60_000.0)


def test_peaks_sum_across_days(tmp_path):
    """Capital is a per-DAY peak, summed — not one global maximum."""
    p = tmp_path / "d.db"
    _db(p, [("2026-09-15", 1, "2026-09-15 10:45:00", 0, 1),
            ("2026-09-16", 1, "2026-09-16 10:45:00", 0, 1)])
    cap = _cap(p, capital_basis="broker_margin", broker_margin_per_contract=30_000.0)
    assert cap == pytest.approx(60_000.0), "two days at $30k each"


def test_time_only_stop_times_are_parsed(tmp_path):
    """trade_stops.stop_time is TIME-ONLY while entry_time is a full datetime.
    A parser that handles only the latter makes every stop vanish and every day
    look fully overlapped — the bug that first produced a suspiciously constant
    $60,000/day for G."""
    p = tmp_path / "e.db"
    _db(p,
        [("2026-09-16", 1, "2026-09-16 10:45:00", 0, 1),
         ("2026-09-16", 2, "2026-09-16 11:15:00", 0, 1)],
        [("2026-09-16", 1, "call", "10:54:00")])     # time-only, as stored
    assert _cap(p, capital_basis="broker_margin",
                broker_margin_per_contract=30_000.0) == pytest.approx(30_000.0)


def test_defined_risk_uses_width_not_margin(tmp_path):
    p = tmp_path / "f.db"
    _db(p, [("2026-09-16", 1, "2026-09-16 10:45:00", 5.0, 7)])
    assert _cap(p) == pytest.approx(5.0 * 100 * 7)


def test_roi_rises_when_capital_falls(tmp_path):
    """The consequence that made this an operator decision: correcting capital
    DOWN raises reported return."""
    p = tmp_path / "g.db"
    _db(p,
        [("2026-09-16", 1, "2026-09-16 10:45:00", 0, 1),
         ("2026-09-16", 2, "2026-09-16 11:15:00", 0, 1)],
        [("2026-09-16", 1, "call", "10:54:00")])
    o = asyncio.run(BacktestingDBReader(
        p, capital_basis="broker_margin", broker_margin_per_contract=30_000.0
    ).get_cumulative_overrides())
    # 100.0 P&L on 30k peak = 0.33%; on the old 60k sum it would read 0.17%.
    assert o["roi_pct"] == pytest.approx(0.33, abs=0.01)


def test_missing_entry_table_falls_back_rather_than_zeroing(tmp_path):
    """A card must never publish a zero because the sweep could not run."""
    p = tmp_path / "h.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE daily_summaries (date TEXT PRIMARY KEY, net_pnl REAL, "
                "entries_placed INT, entries_stopped INT)")
    con.commit(); con.close()
    r = BacktestingDBReader(p)
    assert r._peak_concurrent_capital("") is None, (
        "with no entries the sweep must return None so the caller keeps the "
        "SQL value, rather than overwriting it with 0."
    )


def test_entries_with_no_timestamp_are_skipped_not_guessed(tmp_path):
    """An entry with no entry_time cannot be placed on the timeline; counting
    it at an invented time would corrupt every overlap on that day."""
    p = tmp_path / "i.db"
    _db(p, [("2026-09-16", 1, "", 0, 1), ("2026-09-16", 2, "2026-09-16 11:15:00", 0, 1)])
    assert _cap(p, capital_basis="broker_margin",
                broker_margin_per_contract=30_000.0) == pytest.approx(30_000.0)
