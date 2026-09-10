"""
Phase-0 remainder: concurrency source, entry guards, and the edge era filter
(2026-09-10).

1. CONCURRENCY SOURCE. Losing a calendar from the sidecar silently FREED the
   concurrency slot — D opened a fresh calendar the next session, all three
   times it happened. The obvious fix ("count real broker positions instead")
   would be a SERIOUS REGRESSION: D and E are dry-run-locked, so
   `get_positions()` returns nothing for them, the count would be 0 forever and
   the cap would stop binding entirely. The DB is the only source that knows
   about a dry-run calendar, so the cap now takes max(in-memory, DB-unfinished).

2. ENTRY GUARDS. `grep -n assert double_calendar_strategy.py` returned NOTHING
   before today. Two conditions now fail loudly: inverted strikes (call at or
   below put is not a double calendar) and a sub-1 short DTE (a 0DTE near leg
   is not a calendar, and it would put D on the SAME CONID as the 0DTE variants
   on the shared account — IBKR merges at matching conid).

3. ERA FILTER. Entries before 2026-07-20 ran under `eod_close_if_no_transform:
   true`, which force-closed a 6-15 DTE structure the same session: 19 trades,
   0 wins, -$4,877.40. That era measures a config, not a strategy.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import ConfigError  # noqa: E402
from bots.hydra.calendar_strategy_base import CalendarStrategyBase  # noqa: E402
from bots.hydra.dc_edge import (  # noqa: E402
    DEFAULT_MULTIDAY_ERA_START, LOST_FROM_TRACKING, analyze_calendar_edge,
)
from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Concurrency source
# ---------------------------------------------------------------------------

def _conn(entries, outcomes):
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE dc_calendar_entries (strategy_id TEXT);
        CREATE TABLE dc_outcomes (strategy_id TEXT);
    """)
    for sid in entries:
        c.execute("INSERT INTO dc_calendar_entries VALUES (?)", (sid,))
    for sid in outcomes:
        c.execute("INSERT INTO dc_outcomes VALUES (?)", (sid,))
    c.commit()
    return c


def _strat(conn, in_memory_open):
    s = CalendarStrategyBase.__new__(CalendarStrategyBase)
    s._dc_recorder = SimpleNamespace(_conn=conn) if conn is not None else None
    s.daily_state = SimpleNamespace(entries=[object() for _ in range(in_memory_open)])
    s._dc_entry_is_open = lambda e: True
    return s


class TestConcurrencySource:
    def test_a_lost_position_keeps_occupying_its_slot(self):
        """THE bug. dctm_B is in the DB with no outcome but gone from memory —
        previously the slot read as free and D opened another calendar."""
        c = _conn(["dctm_A", "dctm_B"], ["dctm_A"])
        assert _strat(c, in_memory_open=0)._dc_open_calendar_count() == 1

    def test_normal_case_agrees_with_in_memory(self):
        c = _conn(["dctm_A", "dctm_B"], ["dctm_A"])
        assert _strat(c, in_memory_open=1)._dc_open_calendar_count() == 1

    def test_fully_booked_db_frees_the_slot(self):
        """NEGATIVE CONTROL — without this the cap would wedge permanently
        once any trade completed."""
        c = _conn(["dctm_A", "dctm_B"], ["dctm_A", "dctm_B"])
        assert _strat(c, in_memory_open=0)._dc_open_calendar_count() == 0

    def test_in_memory_wins_when_it_is_higher(self):
        c = _conn(["dctm_A"], ["dctm_A"])
        assert _strat(c, in_memory_open=2)._dc_open_calendar_count() == 2

    def test_no_recorder_degrades_to_in_memory(self):
        assert _strat(None, in_memory_open=1)._dc_open_calendar_count() == 1

    def test_an_unreadable_db_degrades_rather_than_blocking(self):
        """A recorder hiccup must not stop D entering; degrade to today's
        behaviour instead."""
        broken = sqlite3.connect(":memory:")   # no tables at all
        assert _strat(broken, in_memory_open=1)._dc_open_calendar_count() == 1

    def test_the_broker_count_is_NOT_the_source(self):
        """Regression guard for the tempting-but-wrong fix: a dry-run strategy
        has no broker positions, so sourcing the cap there would read 0 forever
        and the cap would stop binding."""
        c = _conn(["dctm_A"], [])
        s = _strat(c, in_memory_open=0)
        s.broker = MagicMock()
        s.broker.get_positions.return_value = []      # dry-run reality
        assert s._dc_open_calendar_count() == 1
        assert s.broker.get_positions.call_count == 0


# ---------------------------------------------------------------------------
# 2. Entry guards
# ---------------------------------------------------------------------------

#: Module-level alias. Binding this as a CLASS attribute would make Python
#: re-bind `self` as its first positional argument on access.
_v = DoubleCalendarStrategy._validate_dc_dte_window


class TestDteGuard:
    def test_zero_short_dte_is_refused(self):
        """A 0DTE near leg is not a calendar, and it would share a conid with
        the 0DTE variants on this account — IBKR merges at matching conid."""
        with pytest.raises(ConfigError, match="short_dte_min must be >= 1"):
            _v(0, 15)

    def test_negative_short_dte_is_refused(self):
        with pytest.raises(ConfigError, match="short_dte_min must be >= 1"):
            _v(-3, 15)

    def test_inverted_window_is_refused(self):
        """Unsatisfiable window: D would silently never enter, which looks
        exactly like 'no signal today', forever."""
        with pytest.raises(ConfigError, match="below short_dte_min"):
            _v(10, 5)

    def test_NEGATIVE_CONTROL_the_real_production_window_passes(self):
        """D's actual config is 6-15. If this ever raises, the guard is wrong,
        not the config."""
        _v(6, 15)

    def test_the_minimum_legal_window_passes(self):
        _v(1, 1)


# ---------------------------------------------------------------------------
# 3. Era filter
# ---------------------------------------------------------------------------

def _edge_db(tmp_path, rows):
    p = str(tmp_path / "dc_calendar.db")
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE dc_calendar_entries (strategy_id TEXT, date TEXT,
            entry_number INTEGER, contracts INTEGER, net_debit REAL);
        CREATE TABLE dc_outcomes (entry_date TEXT, close_date TEXT,
            entry_number INTEGER, strategy_id TEXT, terminal_state TEXT,
            realized_pnl REAL, spx_at_close REAL, close_commission REAL,
            transform_credit REAL, net_debit REAL);
    """)
    for i, (sid, edate, state, pnl) in enumerate(rows):
        c.execute("INSERT INTO dc_calendar_entries VALUES (?,?,?,?,?)",
                  (sid, edate, i, 1, 1000.0))
        c.execute("INSERT INTO dc_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (edate, edate, i, sid, state, pnl, 0.0, 0.0, 0.0, 1000.0))
    c.commit(); c.close()
    return p


ROWS = [
    ("old_a", "2026-06-25", "eod_close", -300.0),   # broken same-day era
    ("old_b", "2026-07-02", "stop", -480.0),        # broken same-day era
    ("new_a", "2026-07-20", "stop", -428.75),       # boundary — INCLUDED
    ("new_b", "2026-08-04", "stop", -331.25),
]


class TestEraFilter:
    def test_no_filter_counts_everything(self, tmp_path):
        r = analyze_calendar_edge(_edge_db(tmp_path, ROWS))
        assert r["total_outcomes"] == 4
        assert r["era_filter"] is None

    def test_since_excludes_the_broken_era(self, tmp_path):
        r = analyze_calendar_edge(_edge_db(tmp_path, ROWS),
                                  since=DEFAULT_MULTIDAY_ERA_START)
        assert r["total_outcomes"] == 2
        assert r["era_filter"] == {"since": "2026-07-20", "excluded": 2}

    def test_the_boundary_date_is_INCLUDED(self, tmp_path):
        """2026-07-20 is the first multi-day-hold entry — off-by-one here would
        silently drop the era's own first trade."""
        r = analyze_calendar_edge(_edge_db(tmp_path, ROWS), since="2026-07-20")
        assert r["segments"]["calendar_mvl"]["n"] == 2

    def test_the_filter_changes_the_measured_edge(self, tmp_path):
        """Otherwise the flag would be decorative."""
        db = _edge_db(tmp_path, ROWS)
        allr = analyze_calendar_edge(db)["segments"]["calendar_mvl"]
        filt = analyze_calendar_edge(db, since="2026-07-20")["segments"]["calendar_mvl"]
        assert allr["ev_dollars_net"] != filt["ev_dollars_net"]

    def test_era_filter_composes_with_the_lost_exclusion(self, tmp_path):
        rows = ROWS + [("lost_x", "2026-08-13", LOST_FROM_TRACKING, -323.75)]
        r = analyze_calendar_edge(_edge_db(tmp_path, rows), since="2026-07-20")
        assert r["segments"]["calendar_mvl"]["n"] == 2       # not 3
        assert r["segments"]["lost_untracked"]["n"] == 1
