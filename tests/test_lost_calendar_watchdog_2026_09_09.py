"""
Vanished-position watchdog + LOST_FROM_TRACKING exclusion (Phase 0.6, 2026-09-09).

THE INCIDENT
------------
A calendar lives in two places: `dc_open_trades.json` (authoritative for
monitoring) and `dc_calendar_entries` (the record). It is only *finished* when
it also gets a `dc_outcomes` row. Five positions fell out of the sidecar
without one — they stopped being monitored and were never booked as a win or a
loss:

    D  dctm_20260618_001 ($2,290 debit)   D  dctm_20260803_001 ($950)
    D  dctm_20260813_001 ($1,590)         E  spydc_20260722_001
    E  spydc_20260805_001

D's lifetime P&L excluded three whole positions; E was missing two of its five
entries. Every one was found by manual audit, never by an alarm. The
write-ordering race behind them was fixed on 2026-08-18 and all five predate
that fix — so this is a DETECTOR, not the fix.

TWO INVARIANTS PINNED HERE
--------------------------
1. The watchdog fires when the DB holds an un-closed entry the sidecar does not.
2. Back-filled LOST_FROM_TRACKING rows are EXCLUDED from the edge verdict.
   This one is load-bearing: those rows carry a LAST OBSERVED MARK, not a
   realized close. `dc_edge` previously segmented only on "did it transform",
   so a lost row would have landed in `calendar_mvl` — the trustworthy segment
   — and contaminated the single number that answers "does D's calendar leg
   have an edge". Booking them without this filter would have been worse than
   leaving them out.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.calendar_strategy_base import CalendarStrategyBase  # noqa: E402
from bots.hydra.dc_edge import LOST_FROM_TRACKING, analyze_calendar_edge  # noqa: E402
from shared.alert_service import AlertPriority, AlertType  # noqa: E402


def _db(tmp_path, entries, outcomes):
    """A minimal dc_calendar.db with the two tables the watchdog joins."""
    p = str(tmp_path / "dc_calendar.db")
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE dc_calendar_entries (strategy_id TEXT, date TEXT,
            entry_time TEXT, net_debit REAL, entry_number INTEGER,
            contracts INTEGER);
        CREATE TABLE dc_outcomes (entry_date TEXT, close_date TEXT,
            entry_number INTEGER, strategy_id TEXT, terminal_state TEXT,
            realized_pnl REAL, spx_at_close REAL, close_commission REAL,
            transform_credit REAL, net_debit REAL);
    """)
    # DISTINCT dates per entry. dc_edge joins outcomes to entries on
    # (date, entry_number), NOT strategy_id, and entry_number is always 1 —
    # so reusing one date here would produce a cartesian product. That is the
    # same fragility the v3 unique index on dc_outcomes.strategy_id guards.
    dates = {sid: f"2026-08-{10 + i:02d}" for i, sid in enumerate(entries)}
    for sid in entries:
        d = dates[sid]
        c.execute("INSERT INTO dc_calendar_entries VALUES (?,?,?,?,?,?)",
                  (sid, d, f"{d}T10:00:00", 1590.0, 1, 1))
    for sid, state, pnl in outcomes:
        d = dates.get(sid, "2026-08-13")
        c.execute("INSERT INTO dc_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (d, "2026-08-17", 1, sid, state, pnl, 0.0, 0.0, 0.0, 1590.0))
    c.commit()
    return p, c


def _strat(conn, tracked_ids):
    s = CalendarStrategyBase.__new__(CalendarStrategyBase)
    s.BOT_NAME = "DCTM"
    s._dc_recorder = SimpleNamespace(_conn=conn)
    s.daily_state = SimpleNamespace(
        entries=[SimpleNamespace(strategy_id=i) for i in tracked_ids])
    s.alert_service = MagicMock()
    return s


class TestWatchdogDetection:
    def test_detects_a_db_entry_the_sidecar_lost(self, tmp_path):
        p, c = _db(tmp_path, ["dctm_A", "dctm_B"], [("dctm_A", "stop", -100.0)])
        s = _strat(c, tracked_ids=[])          # sidecar holds nothing
        lost = s._dc_detect_lost_positions()
        assert lost == ["dctm_B"]

    def test_alerts_HIGH_on_detection(self, tmp_path):
        p, c = _db(tmp_path, ["dctm_A", "dctm_B"], [("dctm_A", "stop", -100.0)])
        s = _strat(c, tracked_ids=[])
        s._dc_detect_lost_positions()
        assert s.alert_service.send_alert.call_count == 1
        kw = s.alert_service.send_alert.call_args.kwargs
        assert kw["priority"] is AlertPriority.HIGH
        assert kw["alert_type"] is AlertType.DATA_QUALITY
        assert kw["details"]["lost"] == ["dctm_B"]

    def test_NEGATIVE_CONTROL_a_tracked_open_position_is_not_lost(self, tmp_path):
        """The open position is legitimately un-closed. Flagging it would make
        the watchdog cry wolf on every single startup."""
        p, c = _db(tmp_path, ["dctm_A", "dctm_B"], [("dctm_A", "stop", -100.0)])
        s = _strat(c, tracked_ids=["dctm_B"])   # sidecar HAS it
        assert s._dc_detect_lost_positions() == []
        assert s.alert_service.send_alert.call_count == 0

    def test_a_fully_booked_db_is_silent(self, tmp_path):
        p, c = _db(tmp_path, ["dctm_A"], [("dctm_A", "stop", -100.0)])
        s = _strat(c, tracked_ids=[])
        assert s._dc_detect_lost_positions() == []

    def test_no_recorder_is_a_silent_noop(self):
        s = CalendarStrategyBase.__new__(CalendarStrategyBase)
        s.BOT_NAME = "DCTM"
        s._dc_recorder = None
        s.daily_state = SimpleNamespace(entries=[])
        s.alert_service = MagicMock()
        assert s._dc_detect_lost_positions() == []

    def test_a_failing_alert_does_not_propagate(self, tmp_path):
        p, c = _db(tmp_path, ["dctm_A", "dctm_B"], [("dctm_A", "stop", -100.0)])
        s = _strat(c, tracked_ids=[])
        s.alert_service.send_alert.side_effect = RuntimeError("pubsub down")
        assert s._dc_detect_lost_positions() == ["dctm_B"]


class TestLostRowsExcludedFromTheEdgeVerdict:
    """The load-bearing half. A LOST_FROM_TRACKING row carries a last observed
    MARK, not a realized close — it must never reach the trustworthy segment."""

    def test_lost_rows_are_segregated_not_counted_as_calendar_outcomes(self, tmp_path):
        p, c = _db(
            tmp_path,
            ["a", "b", "c"],
            [("a", "stop", -300.0), ("b", "eod_close", -200.0),
             ("c", LOST_FROM_TRACKING, -323.75)],
        )
        c.close()
        r = analyze_calendar_edge(p)
        assert r["total_outcomes"] == 3
        assert r["segments"]["calendar_mvl"]["n"] == 2      # NOT 3
        assert r["segments"]["lost_untracked"]["n"] == 1

    def test_a_lost_row_cannot_move_the_verdict(self, tmp_path):
        """A large fabricated 'win' in a lost row must not improve the edge."""
        base_rows = [("a", "stop", -300.0), ("b", "eod_close", -200.0)]
        d1 = tmp_path / "one"; d1.mkdir()
        p1, c1 = _db(d1, ["a", "b"], base_rows); c1.close()
        d2 = tmp_path / "two"; d2.mkdir()
        p2, c2 = _db(d2, ["a", "b", "c"],
                     base_rows + [("c", LOST_FROM_TRACKING, +5000.0)]); c2.close()

        r1, r2 = analyze_calendar_edge(p1), analyze_calendar_edge(p2)
        assert (r1["segments"]["calendar_mvl"]["ev_dollars_net"]
                == r2["segments"]["calendar_mvl"]["ev_dollars_net"])
        assert r1["verdict"]["code"] == r2["verdict"]["code"]

    def test_the_lost_segment_carries_an_explicit_caveat(self, tmp_path):
        p, c = _db(tmp_path, ["a"], [("a", LOST_FROM_TRACKING, -323.75)])
        c.close()
        seg = analyze_calendar_edge(p)["segments"]["lost_untracked"]
        assert "NEVER CLOSED" in seg["caveat"]
