"""2026-06-08: the lifetime cumulative-metrics booking must be IDEMPOTENT by
date. A re-trigger of log_daily_summary for an already-booked day (e.g. a
restart that reset main.py's daily_summary_sent_date local — the Fix #82
scenario) must NOT double-count net P&L / entries or append a duplicate
daily_returns row. Observed live: 2026-06-02 was booked twice on variants A+C,
inflating cumulative_pnl by that day's net P&L."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _strategy():
    s = HydraStrategy.__new__(HydraStrategy)
    s.contracts_per_entry = 7
    s.cumulative_metrics = {
        "cumulative_pnl": 0.0, "total_entries": 0, "total_credit_collected": 0.0,
        "total_stops": 0, "double_stops": 0, "winning_days": 0, "losing_days": 0,
        "daily_returns": [],
    }
    s._save_cumulative_metrics = MagicMock()
    return s


def _summary(date, entries, credit, call_stops=0, put_stops=0, double_stops=0):
    return {"date": date, "entries_completed": entries, "total_credit": credit,
            "call_stops": call_stops, "put_stops": put_stops,
            "double_stops": double_stops}


class TestCumulativeIdempotent:
    def test_first_book_applies_increments_and_row(self):
        s = _strategy()
        s._book_daily_cumulative(_summary("2026-06-02", 10, 5000.0, call_stops=1),
                                 net_pnl=558.0, capital_deployed=10000.0)
        m = s.cumulative_metrics
        assert m["cumulative_pnl"] == 558.0
        assert m["total_entries"] == 10
        assert m["total_credit_collected"] == 5000.0
        assert m["total_stops"] == 1
        assert m["winning_days"] == 1 and m["losing_days"] == 0
        assert len(m["daily_returns"]) == 1
        assert m["daily_returns"][0]["date"] == "2026-06-02"
        s._save_cumulative_metrics.assert_called_once()

    def test_second_book_same_date_is_a_noop(self):
        # The exact 2026-06-02 double-book scenario.
        s = _strategy()
        summ = _summary("2026-06-02", 10, 5000.0, call_stops=1)
        s._book_daily_cumulative(summ, net_pnl=558.0, capital_deployed=10000.0)
        s._save_cumulative_metrics.reset_mock()

        s._book_daily_cumulative(summ, net_pnl=558.0, capital_deployed=10000.0)
        m = s.cumulative_metrics
        # NOT doubled.
        assert m["cumulative_pnl"] == 558.0, "double-counted P&L!"
        assert m["total_entries"] == 10, "double-counted entries!"
        assert m["total_credit_collected"] == 5000.0
        assert m["total_stops"] == 1
        assert m["winning_days"] == 1
        assert len(m["daily_returns"]) == 1, "duplicate daily_returns row!"
        # No re-save on the skip path (nothing changed).
        s._save_cumulative_metrics.assert_not_called()

    def test_different_date_books_normally(self):
        s = _strategy()
        s._book_daily_cumulative(_summary("2026-06-02", 10, 5000.0),
                                 net_pnl=558.0, capital_deployed=10000.0)
        s._book_daily_cumulative(_summary("2026-06-03", 5, 2500.0, put_stops=1),
                                 net_pnl=-100.0, capital_deployed=8000.0)
        m = s.cumulative_metrics
        assert m["cumulative_pnl"] == 458.0
        assert m["total_entries"] == 15
        assert m["total_credit_collected"] == 7500.0
        assert m["total_stops"] == 1
        assert m["winning_days"] == 1 and m["losing_days"] == 1
        assert len(m["daily_returns"]) == 2
        assert {r["date"] for r in m["daily_returns"]} == {"2026-06-02", "2026-06-03"}
