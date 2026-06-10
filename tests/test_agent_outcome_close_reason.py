"""2026-06-10: HOMER + HERMES must label a Brandon take-profit (or any early
close) as a take-profit / early-close, NOT a "Double Stop".

The Brandon TP path closes the IC early and — because the early-close path reuses
the stop-logging plumbing — sets BOTH per-side *_stopped flags AND writes "Stop #N"
Trades rows + per-side stop times to Google Sheets. The agents used to infer
"Double Stop" from those, so the 2026-06-09 journal/HERMES narrative mislabeled a
clean +$781 take-profit day as a double-stop loss day.

Authoritative disposition signals:
  - HERMES reads the state file → entry["close_reason"] ("TP"/"BREACH"/...) +
    entry["early_closed"].
  - HOMER reads Google Sheets → Positions "Status" == "EARLY_CLOSED" + P&L sign.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.hermes.data_collector import _classify_outcome  # noqa: E402
from services.homer.data_collector import _build_entries_for_day  # noqa: E402


# --------------------------------------------------------------------------- #
# HERMES — state-file close_reason classification
# --------------------------------------------------------------------------- #
class TestHermesClassifyOutcome:
    def test_tp_with_both_stop_flags_is_take_profit(self):
        # Brandon TP sets BOTH *_side_stopped as a generic "closed" marker.
        e = {"call_side_stopped": True, "put_side_stopped": True,
             "close_reason": "TP", "early_closed": True}
        assert _classify_outcome(e) == "take_profit"

    def test_breach_is_breach_exit(self):
        e = {"call_side_stopped": True, "put_side_stopped": True,
             "close_reason": "BREACH", "early_closed": True}
        assert _classify_outcome(e) == "breach_exit"

    def test_real_double_stop_still_double_stopped(self):
        e = {"call_side_stopped": True, "put_side_stopped": True,
             "close_reason": "STOP"}
        assert _classify_outcome(e) == "double_stopped"

    def test_real_one_sided_stop_preserved(self):
        e = {"call_side_stopped": True, "put_side_stopped": False,
             "close_reason": "STOP"}
        assert _classify_outcome(e) == "call_stopped"

    def test_clean_expiry(self):
        e = {"call_side_stopped": False, "put_side_stopped": False,
             "close_reason": "EXPIRED"}
        assert _classify_outcome(e) == "clean"

    def test_mkt018_early_close_without_reason(self):
        # Early close with no explicit TP/BREACH reason → early_closed, not a stop.
        e = {"call_side_stopped": True, "put_side_stopped": True,
             "close_reason": "", "early_closed": True}
        assert _classify_outcome(e) == "early_closed"


# --------------------------------------------------------------------------- #
# HOMER — Sheets EARLY_CLOSED + P&L-sign classification
# --------------------------------------------------------------------------- #
def _entry_row(entry_num: str, date_str: str):
    """The 'HYDRA Entry #N [TAG]' Trades row that creates the entry. Short
    strikes 6900C / 6700P so the stop rows attach by strike."""
    return {
        "Action": f"HYDRA Entry #{entry_num} [NEUTRAL]",
        "Timestamp": f"{date_str} 10:45:00",
        "Expiry": date_str,
        "Type": "Iron Condor",
        "Strike": "C:6900/6925 P:6700/6675",
        "P&L ($)": "0",
    }


def _positions_rows_early_closed(entry_num: str, date_str: str, pnl_per_leg: float):
    """Two per-side Positions rows for one early-closed entry."""
    common = {"Entry #": entry_num, "Expiry": date_str, "Status": "EARLY_CLOSED",
              "Stop Triggered": "Yes", "P&L ($)": str(pnl_per_leg)}
    return [
        {**common, "Side": "call", "Strike": "6900"},
        {**common, "Side": "put", "Strike": "6700"},
    ]


def _trades_stop_rows(entry_num: str, date_str: str, pnl_per_side: float):
    """The 'Stop #N' Trades rows the early-close path also emits."""
    ts = f"{date_str} 15:11:00"
    return [
        {"Action": f"HYDRA Stop #{entry_num} (CALL)", "Timestamp": ts,
         "Expiry": date_str, "Strike": "6900", "P&L ($)": str(pnl_per_side)},
        {"Action": f"HYDRA Stop #{entry_num} (PUT)", "Timestamp": ts,
         "Expiry": date_str, "Strike": "6700", "P&L ($)": str(pnl_per_side)},
    ]


class TestHomerOutcome:
    DATE = "2026-06-09"

    def test_profitable_early_close_is_take_profit(self):
        # EARLY_CLOSED status + positive net → Take Profit, NOT Double Stop.
        rows = _build_entries_for_day(
            [_entry_row("1", self.DATE), *_trades_stop_rows("1", self.DATE, +200.0)],
            _positions_rows_early_closed("1", self.DATE, +200.0),
            self.DATE,
        )
        assert rows and rows[0]["Outcome"] == "Take Profit"

    def test_defensive_early_close_is_not_double_stop(self):
        rows = _build_entries_for_day(
            [_entry_row("2", self.DATE), *_trades_stop_rows("2", self.DATE, -150.0)],
            _positions_rows_early_closed("2", self.DATE, -150.0),
            self.DATE,
        )
        assert rows and rows[0]["Outcome"] == "Early Closed (Defensive)"

    def test_positive_double_stop_relabeled_take_profit_when_status_lost(self):
        # Positions tab overwritten (no EARLY_CLOSED status), but both stop times
        # exist and net P&L is positive → logically a take-profit, not a stop.
        rows = _build_entries_for_day(
            [_entry_row("3", self.DATE), *_trades_stop_rows("3", self.DATE, +175.0)],
            None,
            self.DATE,
        )
        assert rows and rows[0]["Outcome"] == "Take Profit"

    def test_real_double_stop_still_double_stop(self):
        # Both stop times, negative net, no EARLY_CLOSED status → real Double Stop.
        rows = _build_entries_for_day(
            [_entry_row("4", self.DATE), *_trades_stop_rows("4", self.DATE, -300.0)],
            None,
            self.DATE,
        )
        assert rows and rows[0]["Outcome"] == "Double Stop"
