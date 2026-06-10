"""2026-06-10: two fixes.

(1) base_strategy._book_daily_cumulative used to blind-increment winning_days for
EVERY day — including 0-capital NO-TRADE days (net_pnl 0 >= 0 counted as a win) —
and, because the idempotency guard keys on daily_returns (which a 0-capital day
never appends to), re-counted that phantom win on every restart. Observed on
variant C: winning_days=18 vs 14 real trading days. Fix: derive winning/losing
days from daily_returns (self-healing, idempotent, no-trade day = neither).

(2) HERMES compute_cheat_sheet now rebases its cumulative to LIVE_BASELINE_DATE
(2026-06-09) computed from daily_returns, so it AGREES with the dashboard (same
baseline) instead of reading the drifted lifetime counters.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from services.hermes.data_collector import compute_cheat_sheet, LIVE_BASELINE_DATE  # noqa: E402


def _summary(date, net_pnl, entries=1, credit=100.0):
    return {
        "date": date, "net_pnl": net_pnl, "total_pnl": net_pnl,
        "entries_completed": entries, "total_credit": credit,
        "call_stops": 0, "put_stops": 0, "double_stops": 0,
    }


def _strat(cumulative_metrics):
    s = HydraStrategy.__new__(HydraStrategy)
    s.cumulative_metrics = cumulative_metrics
    s.contracts_per_entry = 7
    s._save_cumulative_metrics = MagicMock()
    return s


class TestBookDailyCumulativeDrift:
    def _fresh(self):
        return {"cumulative_pnl": 0.0, "total_entries": 0, "total_credit_collected": 0.0,
                "total_stops": 0, "double_stops": 0, "winning_days": 0, "losing_days": 0,
                "daily_returns": []}

    def test_winning_day_counted_once(self):
        s = _strat(self._fresh())
        s._book_daily_cumulative(_summary("2026-06-09", 781.2), 781.2, 5000.0)
        assert s.cumulative_metrics["winning_days"] == 1
        assert s.cumulative_metrics["losing_days"] == 0
        assert len(s.cumulative_metrics["daily_returns"]) == 1

    def test_zero_capital_no_trade_day_is_not_a_win(self):
        # The exact bug: a 0-capital no-trade day must NOT count as a winning day.
        s = _strat(self._fresh())
        s._book_daily_cumulative(_summary("2026-06-10", 0.0, entries=0, credit=0.0), 0.0, 0.0)
        assert s.cumulative_metrics["winning_days"] == 0
        assert s.cumulative_metrics["losing_days"] == 0
        assert len(s.cumulative_metrics["daily_returns"]) == 0

    def test_zero_capital_reruns_do_not_inflate(self):
        s = _strat(self._fresh())
        for _ in range(5):  # simulate 5 restarts re-running the same no-trade day
            s._book_daily_cumulative(_summary("2026-06-10", 0.0, entries=0, credit=0.0), 0.0, 0.0)
        assert s.cumulative_metrics["winning_days"] == 0

    def test_counters_self_heal_from_drifted_state(self):
        # Pre-drifted file: winning_days=18 but daily_returns has 14W/5L (19 rows).
        dr = [{"date": f"2026-05-{d:02d}", "net_pnl": 100.0, "capital_deployed": 1000.0,
               "return_pct": 0.1, "contracts_per_entry": 7} for d in range(1, 15)]  # 14 wins
        dr += [{"date": f"2026-05-2{d}", "net_pnl": -50.0, "capital_deployed": 1000.0,
                "return_pct": -0.05, "contracts_per_entry": 7} for d in range(0, 5)]  # 5 losses
        cm = {"cumulative_pnl": 1150.0, "winning_days": 18, "losing_days": 5,  # 18 is DRIFTED
              "total_entries": 0, "total_credit_collected": 0.0, "total_stops": 0,
              "double_stops": 0, "daily_returns": dr}
        s = _strat(cm)
        s._book_daily_cumulative(_summary("2026-06-09", 781.2), 781.2, 5000.0)  # +1 win
        # 14 prior wins + 1 new = 15 (NOT 18+1=19). The phantom +4 is healed.
        assert s.cumulative_metrics["winning_days"] == 15
        assert s.cumulative_metrics["losing_days"] == 5


class TestHermesRebase:
    def _data(self, daily_returns):
        return {
            "state": {
                "entries": [],
                "total_realized_pnl": 781.2,
                "total_commission": 32.2,
                "contracts_per_entry": 7,
            },
            "metrics": {"daily_returns": daily_returns},
            "ohlc": {},
            "apollo_report": None,
        }

    def test_cumulative_rebased_to_baseline(self):
        # Lifetime spans pre + post baseline; HERMES must count ONLY >= baseline.
        dr = [
            {"date": "2026-05-05", "net_pnl": 500.0, "capital_deployed": 5000.0, "return_pct": 0.1, "contracts_per_entry": 15},
            {"date": "2026-06-02", "net_pnl": 558.0, "capital_deployed": 10000.0, "return_pct": 0.056, "contracts_per_entry": 10},
            {"date": "2026-06-09", "net_pnl": 781.2, "capital_deployed": 7000.0, "return_pct": 0.111, "contracts_per_entry": 7},
        ]
        cs = compute_cheat_sheet(self._data(dr))
        cum = cs["cumulative"]
        assert cum["baseline_date"] == LIVE_BASELINE_DATE
        assert cum["day_number"] == 1          # only 2026-06-09 qualifies
        assert cum["cumulative_pnl"] == 781.2  # NOT the 1839.2 lifetime
        assert cum["winning_days"] == 1
        assert cum["losing_days"] == 0

    def test_two_post_baseline_days(self):
        dr = [
            {"date": "2026-06-09", "net_pnl": 781.2, "capital_deployed": 7000.0, "return_pct": 0.111, "contracts_per_entry": 7},
            {"date": "2026-06-10", "net_pnl": -100.0, "capital_deployed": 7000.0, "return_pct": -0.014, "contracts_per_entry": 7},
        ]
        cum = compute_cheat_sheet(self._data(dr))["cumulative"]
        assert cum["day_number"] == 2
        assert round(cum["cumulative_pnl"], 2) == 681.2
        assert cum["winning_days"] == 1
        assert cum["losing_days"] == 1

    def test_no_post_baseline_days_falls_back_to_today(self):
        dr = [{"date": "2026-05-05", "net_pnl": 500.0, "capital_deployed": 5000.0, "return_pct": 0.1, "contracts_per_entry": 15}]
        cum = compute_cheat_sheet(self._data(dr))["cumulative"]
        # net_pnl from state = 781.2 - 32.2 = 749.0 → 1-day fallback
        assert cum["day_number"] == 1
        assert round(cum["cumulative_pnl"], 2) == 749.0
        assert cum["winning_days"] == 1
