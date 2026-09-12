"""
A day the bot DECLINED to trade is not a loss (2026-09-11).

THE INCIDENT THIS FIXES. An ad-hoc query reported variant B at a "49% win rate"
over 35 live-paper days. Eleven of those days placed no trades at all, and
counting them as non-wins dragged 17-of-24 (71%) down to 17-of-35 (49%). It was
caught only by disbelieving the arithmetic — not a process anyone should rely
on. No report should be ABLE to imply a declined day is a loss, so the
classification is structural rather than a convention.

FOUR DAY TYPES, and the distinction between the middle two carries the
information:

  TRADED      entries_placed > 0 — the only days a win rate means anything on.
  GATED       0 entries but skip rows exist: the bot WANTED to trade and its own
              filters stopped it. Auditable, possibly wrong, and on B it is ~26%
              of sessions.
  NO-ATTEMPT  0 entries, 0 skips, market open — policy. FOMC T+0 is the known
              case: the entry loop never runs, so there is nothing to skip.
              Absence of skip rows here is CORRECT. It looked like a telemetry
              bug until `economic_events` explained it, which is exactly why
              this is a named type rather than an anomaly.
  CLOSED      no market ticks — a holiday. Not a bot decision at all.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.variant_performance import (  # noqa: E402
    CLOSED,
    GATED,
    NO_ATTEMPT,
    TRADED,
    analyze,
    render,
)


def _db(tmp_path, days):
    """days: list of (date, entries, net, n_ticks, [skip_reasons], events)"""
    p = tmp_path / "t.db"
    con = sqlite3.connect(str(p))
    con.executescript("""
        CREATE TABLE daily_summaries (date TEXT, spx_open REAL, spx_close REAL,
          spx_high REAL, spx_low REAL, day_range REAL, vix_open REAL, vix_close REAL,
          entries_placed INT, entries_stopped INT, entries_expired INT,
          gross_pnl REAL, net_pnl REAL, commission REAL, long_salvage_revenue REAL,
          day_type TEXT, day_of_week TEXT, contracts_per_entry INT,
          overnight_gap REAL, realized_volatility REAL, economic_events TEXT,
          config_version TEXT, opex_week INT, unattributed_overlay_pnl REAL);
        CREATE TABLE market_ticks (timestamp TEXT, spx_price REAL);
        CREATE TABLE skipped_entries (date TEXT, entry_number INT, skip_reason TEXT);
    """)
    for d, entries, net, ticks, reasons, events in days:
        con.execute("INSERT INTO daily_summaries (date, entries_placed, entries_stopped,"
                    " gross_pnl, net_pnl, economic_events) VALUES (?,?,?,?,?,?)",
                    (d, entries, 0, net, net, events))
        for i in range(ticks):
            con.execute("INSERT INTO market_ticks VALUES (?,?)", (f"{d} 10:{i:02d}:00", 7600))
        for i, r in enumerate(reasons):
            con.execute("INSERT INTO skipped_entries VALUES (?,?,?)", (d, i + 1, r))
    con.commit(); con.close()
    return str(p)


GEX = "…GEX accel-zone skip…"


class TestTheFourDayTypes:
    def test_a_traded_day_is_TRADED(self, tmp_path):
        db = _db(tmp_path, [("2026-08-01", 4, 500.0, 10, [], None)])
        assert analyze(db, None)["days"][0]["type"] == TRADED

    def test_zero_entries_with_skips_is_GATED(self, tmp_path):
        """The bot wanted to trade and stopped itself."""
        db = _db(tmp_path, [("2026-08-01", 0, 0.0, 10, [GEX, GEX], None)])
        d = analyze(db, None)["days"][0]
        assert d["type"] == GATED
        assert "GEX" in d["why"]

    def test_zero_entries_zero_skips_with_ticks_is_NO_ATTEMPT(self, tmp_path):
        """FOMC T+0: the entry loop never runs, so there is nothing to skip.
        This is the case that looked like a telemetry bug on 2026-07-29."""
        db = _db(tmp_path, [("2026-08-01", 0, 0.0, 10, [], '["FOMC_ANNOUNCEMENT"]')])
        d = analyze(db, None)["days"][0]
        assert d["type"] == NO_ATTEMPT
        assert "FOMC" in d["why"]

    def test_no_ticks_at_all_is_CLOSED(self, tmp_path):
        db = _db(tmp_path, [("2026-09-07", 0, 0.0, 0, [], None)])
        d = analyze(db, None)["days"][0]
        assert d["type"] == CLOSED
        assert "holiday" in d["why"]

    def test_gated_and_no_attempt_are_NOT_merged(self, tmp_path):
        """One is the bot exercising judgement; the other is policy. Collapsing
        them hides the only auditable half."""
        db = _db(tmp_path, [
            ("2026-08-01", 0, 0.0, 10, [GEX], None),
            ("2026-08-02", 0, 0.0, 10, [], '["FOMC_ANNOUNCEMENT"]'),
        ])
        bt = analyze(db, None)["by_type"]
        assert len(bt[GATED]) == 1 and len(bt[NO_ATTEMPT]) == 1


class TestTheWinRateExcludesNonTradedDays:
    """The actual bug: 17/35 = 49% instead of 17/24 = 71%."""

    def _b_shaped(self, tmp_path):
        days = ([(f"2026-08-{i:02d}", 4, 800.0, 10, [], None) for i in range(1, 18)]
                + [(f"2026-08-{i:02d}", 4, -1000.0, 10, [], None) for i in range(18, 25)]
                + [(f"2026-09-{i:02d}", 0, 0.0, 10, [GEX], None) for i in range(1, 12)])
        return _db(tmp_path, days)

    def test_win_rate_is_over_traded_days_only(self, tmp_path):
        out = render(analyze(self._b_shaped(tmp_path), None), "t")
        assert "17/24 = 71%" in out
        assert "49%" not in out

    def test_both_per_session_and_per_traded_day_are_shown(self, tmp_path):
        """Per-session is the honest portfolio number; per-traded-day describes
        the strategy. Showing only one invites the wrong conclusion."""
        out = render(analyze(self._b_shaped(tmp_path), None), "t")
        assert "per SESSION" in out and "per TRADED day" in out

    def test_the_session_count_is_broken_out(self, tmp_path):
        out = render(analyze(self._b_shaped(tmp_path), None), "t")
        assert "24 traded" in out and "11 gated" in out


class TestTheGatedSectionIsHonest:
    def test_it_quantifies_the_stake(self, tmp_path):
        db = _db(tmp_path, [("2026-08-01", 4, 1000.0, 10, [], None),
                            ("2026-08-02", 0, 0.0, 10, [GEX], None)])
        out = render(analyze(db, None), "t")
        assert "GATED DAYS" in out

    def test_it_refuses_to_call_the_stake_a_loss(self, tmp_path):
        """The gates may have avoided the bad days — that is their purpose. The
        report must not imply the money was forgone."""
        db = _db(tmp_path, [("2026-08-01", 4, 1000.0, 10, [], None),
                            ("2026-08-02", 0, 0.0, 10, [GEX], None)])
        out = render(analyze(db, None), "t")
        assert "IF they resembled an average day" in out
        assert "may have been the bad" in out

    def test_it_points_at_the_tool_that_would_settle_it(self, tmp_path):
        db = _db(tmp_path, [("2026-08-01", 4, 1000.0, 10, [], None),
                            ("2026-08-02", 0, 0.0, 10, [GEX], None)])
        assert "analyze_skipped_entry_outcomes" in render(analyze(db, None), "t")


class TestItDoesNotOverstateTheEdge:
    def test_the_execution_drag_caveat_is_always_printed(self):
        src = (Path(__file__).resolve().parents[1]
               / "scripts" / "variant_performance.py").read_text()
        assert "Execution drag is NOT deducted" in src

    def test_the_sharpe_is_labelled_provisional(self, tmp_path):
        db = _db(tmp_path, [(f"2026-08-{i:02d}", 4, 100.0 * (1 if i % 2 else -1), 10, [], None)
                            for i in range(1, 11)])
        assert "provisional" in render(analyze(db, None), "t")

    def test_a_losses_bigger_than_wins_shape_is_called_out(self, tmp_path):
        db = _db(tmp_path, [("2026-08-01", 4, 800.0, 10, [], None),
                            ("2026-08-02", 4, 800.0, 10, [], None),
                            ("2026-08-03", 4, -2000.0, 10, [], None)])
        assert "losses BIGGER than wins" in render(analyze(db, None), "t")


class TestDegenerateInputs:
    def test_an_empty_window_says_so(self, tmp_path):
        assert "no days in window" in render(analyze(_db(tmp_path, []), None), "t")

    def test_all_days_gated_does_not_divide_by_zero(self, tmp_path):
        db = _db(tmp_path, [("2026-08-01", 0, 0.0, 10, [GEX], None)])
        out = render(analyze(db, None), "t")
        assert "no traded days" in out
