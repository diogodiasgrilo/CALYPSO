#!/usr/bin/env python3
"""Show exactly which call stops are avoided at $0.30 buffer vs $0.10."""

import sqlite3

DB_PATH = "data/backtesting.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

call_stops = conn.execute("""
    SELECT s.date, s.entry_number, s.stop_time, s.spx_at_stop,
           s.trigger_level, s.actual_debit, s.net_pnl,
           e.total_credit, e.call_credit,
           e.short_call_strike, e.long_call_strike
    FROM trade_stops s
    JOIN trade_entries e ON s.date = e.date AND s.entry_number = e.entry_number
    WHERE s.side = 'call' AND s.entry_number < 6
    ORDER BY s.date, s.entry_number
""").fetchall()

print("E1-E5 call stops: which ones fire at $0.10 but NOT at $0.30?\n")

fmt = "{:<12} {:<4} {:>8} {:>8} {:>10} {:>10} {:>8} {:>10} {:>10} {:>8}"
print(fmt.format(
    "Date", "E#", "Credit", "CallCrd",
    "StopLv@10", "StopLv@30", "ActDeb",
    "StopP&L", "HoldP&L", "Avoided"
))
print("-" * 105)

for s in call_stops:
    total_credit = s["total_credit"] or 0
    call_credit = s["call_credit"] or 0
    actual_debit = s["actual_debit"] or 0
    net_pnl = s["net_pnl"] or 0
    short_call = s["short_call_strike"] or 0
    long_call = s["long_call_strike"] or 0
    spread_width = long_call - short_call if (long_call and short_call) else 50

    stop_at_10 = total_credit + 10
    stop_at_30 = total_credit + 30

    fires_at_10 = actual_debit >= stop_at_10
    fires_at_30 = actual_debit >= stop_at_30

    # Get SPX at close
    close_tick = conn.execute(
        "SELECT spx_price FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp DESC LIMIT 1",
        (f"{s['date']}%",)
    ).fetchone()
    spx_close = close_tick["spx_price"] if close_tick else 0
    close_itm = max(0, spx_close - short_call) if (spx_close and short_call) else 0
    expiry_value = min(close_itm, spread_width) * 100
    hold_pnl = call_credit - expiry_value - 5

    is_false = spx_close < short_call if (spx_close and short_call) else None

    avoided = ""
    if fires_at_10 and not fires_at_30:
        avoided = "YES → " + ("SAVE" if is_false else "RISK")

    print(fmt.format(
        s["date"], f"E{s['entry_number']}",
        f"${total_credit:.0f}", f"${call_credit:.0f}",
        f"${stop_at_10:.0f}", f"${stop_at_30:.0f}",
        f"${actual_debit:.0f}",
        f"${net_pnl:.0f}", f"${hold_pnl:.0f}",
        avoided
    ))

conn.close()
