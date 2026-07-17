"""Tests for HOMER's DB-structured Section-3 entries builder (Sheets->DB migration).

Verifies _build_entries_for_day_from_db reproduces the _build_entries_for_day dict
shape from structured trade_entries/trade_stops rows, with Outcome derived from
exit_reason (cleaner than the Sheets P&L-sign heuristic).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.homer.data_collector import _build_entries_for_day_from_db


def test_put_only_stopped():
    entries = [dict(entry_number=1, entry_time="2026-07-16 10:46:02", trend_signal="neutral",
                    entry_type="put_only", short_call_strike=0, long_call_strike=0,
                    short_put_strike=7515, long_put_strike=7510, call_credit=0, put_credit=105,
                    total_credit=105, realized_pnl=-1785, override_reason=None)]
    stops = [dict(entry_number=1, side="put", exit_reason="stop_loss", net_pnl=-1785,
                  stop_time="2026-07-16 15:31:48", salvage_revenue=0)]
    e = _build_entries_for_day_from_db(entries, stops, "2026-07-16")[0]
    assert e["Entry #"] == 1
    assert e["Entry Type"] == "Put Only"
    assert e["Trend Signal"] == "NEUTRAL"
    assert e["Short Put Strike"] == 7515 and e["Short Call Strike"] == ""
    assert e["Outcome"] == "Put Stopped"
    assert e["P&L Impact"] == -1785.0
    assert e["Total Credit"] == 105
    assert e["Stop Time"] == "3:31:48 PM ET"
    assert e["Put Stop Time"] == "3:31:48 PM ET" and e["Call Stop Time"] == ""


def test_full_ic_expired():
    entries = [dict(entry_number=1, entry_time="2026-07-14 10:47:00", trend_signal="neutral",
                    entry_type="full_ic", short_call_strike=7580, long_call_strike=7605,
                    short_put_strike=7470, long_put_strike=7445, call_credit=245, put_credit=140,
                    total_credit=385, realized_pnl=385, override_reason=None)]
    e = _build_entries_for_day_from_db(entries, [], "2026-07-14")[0]
    assert e["Entry Type"] == "Full IC"
    assert e["Outcome"] == "Expired"          # no stop rows
    assert e["P&L Impact"] == 385.0
    assert e["Short Call Strike"] == 7580 and e["Short Put Strike"] == 7470
    assert e["Stop Time"] == ""


def test_outcome_double_stop_tp_early():
    base = dict(entry_number=1, entry_type="full_ic", trend_signal="neutral",
                short_call_strike=100, short_put_strike=90, total_credit=50)
    # both sides stop_loss, net<0 -> Double Stop
    dbl = [dict(entry_number=1, side="call", exit_reason="stop_loss", net_pnl=-250, stop_time="14:00:00", salvage_revenue=0),
           dict(entry_number=1, side="put", exit_reason="stop_loss", net_pnl=-250, stop_time="14:05:00", salvage_revenue=0)]
    assert _build_entries_for_day_from_db([dict(base, realized_pnl=-500)], dbl, "d")[0]["Outcome"] == "Double Stop"
    # take_profit -> Take Profit
    tp = [dict(entry_number=1, side="call", exit_reason="take_profit", net_pnl=40, stop_time="13:00:00", salvage_revenue=0)]
    assert _build_entries_for_day_from_db([dict(base, realized_pnl=40)], tp, "d")[0]["Outcome"] == "Take Profit"
    # early_close net>0 -> Take Profit; net<0 -> Early Closed (Defensive)
    ec = [dict(entry_number=1, side="call", exit_reason="early_close", net_pnl=35, stop_time="15:50:00", salvage_revenue=0)]
    assert _build_entries_for_day_from_db([dict(base, realized_pnl=105)], ec, "d")[0]["Outcome"] == "Take Profit"
    assert _build_entries_for_day_from_db([dict(base, realized_pnl=-70)], ec, "d")[0]["Outcome"] == "Early Closed (Defensive)"


def test_call_stopped_and_salvage():
    entries = [dict(entry_number=2, entry_type="full_ic", trend_signal="bullish",
                    short_call_strike=100, short_put_strike=90, total_credit=60, realized_pnl=-120)]
    stops = [dict(entry_number=2, side="call", exit_reason="stop_loss", net_pnl=-120,
                  stop_time="13:30:00", salvage_revenue=15.0)]
    e = _build_entries_for_day_from_db(entries, stops, "d")[0]
    assert e["Outcome"] == "Call Stopped"
    assert e["Trend Signal"] == "BULLISH"
    assert e["Call Long Salvage Proceeds"] == 15.0 and e["Put Long Salvage Proceeds"] == 0
