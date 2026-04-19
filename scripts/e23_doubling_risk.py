#!/usr/bin/env python3
"""Worst-day drawdown if we doubled contracts on E#2+E#3."""
import sqlite3
from collections import defaultdict

conn = sqlite3.connect("data/backtesting.db")
entries = conn.execute(
    "SELECT date, entry_number, call_credit, put_credit FROM trade_entries "
    "WHERE date >= '2026-02-10' AND entry_number IN (2, 3)"
).fetchall()
stops = conn.execute(
    "SELECT date, entry_number, side, actual_debit FROM trade_stops "
    "WHERE entry_number IN (2, 3)"
).fetchall()
stop_map = {}
for s in stops:
    stop_map[(s[0], s[1], s[2])] = s[3] or 0

daily = defaultdict(float)
for e in entries:
    d, en = e[0], e[1]
    for side, credit in [("call", e[2] or 0), ("put", e[3] or 0)]:
        if credit <= 0:
            continue
        debit = stop_map.get((d, en, side))
        if debit is not None:
            daily[d] += (credit - debit - 5.0 - 2.5)
        else:
            daily[d] += (credit - 5.0)

sorted_days = sorted(daily.items(), key=lambda x: x[1])
print("Worst 10 days for E#2+E#3 (1 contract) vs doubled:")
print("%12s | %11s | %11s" % ("Date", "1 contract", "2 contracts"))
for d, pnl in sorted_days[:10]:
    print("%12s | %10.2f | %10.2f" % (d, pnl, pnl*2))

print("\nBest 10 days:")
for d, pnl in sorted_days[-10:]:
    print("%12s | %10.2f | %10.2f" % (d, pnl, pnl*2))

# Max single-day loss at 1x vs 2x
worst = sorted_days[0]
print(f"\n>>> Max single-day loss at 1 contract: ${worst[1]:.2f}")
print(f">>> Max single-day loss at 2 contracts: ${worst[1]*2:.2f}")
print(f">>> Max single-day gain at 2 contracts: ${sorted_days[-1][1]*2:.2f}")
