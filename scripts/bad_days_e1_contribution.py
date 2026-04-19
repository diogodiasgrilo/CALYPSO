#!/usr/bin/env python3
"""On the worst days for E#2+E#3, how much did E#1 contribute?
This tells us whether dropping E#1 reduces overall drawdown enough
to justify doubling E#2+E#3 contracts."""
import sqlite3
from collections import defaultdict

conn = sqlite3.connect("data/backtesting.db")
entries = conn.execute(
    "SELECT date, entry_number, call_credit, put_credit FROM trade_entries "
    "WHERE date >= '2026-02-10'"
).fetchall()
stops = conn.execute("SELECT date, entry_number, side, actual_debit FROM trade_stops").fetchall()
stop_map = {}
for s in stops:
    stop_map[(s[0], s[1], s[2])] = s[3] or 0

def side_pnl(e, side):
    credit = e[2] if side == "call" else e[3]
    if not credit or credit <= 0:
        return 0
    debit = stop_map.get((e[0], e[1], side))
    if debit is not None:
        return credit - debit - 5.0 - 2.5
    return credit - 5.0

# Per-day, per-entry-number P&L
daily = defaultdict(lambda: {1: 0.0, 2: 0.0, 3: 0.0, 6: 0.0})
for e in entries:
    d, en = e[0], e[1]
    if en in daily[d]:
        for side in ("call", "put"):
            daily[d][en] += side_pnl(e, side)

# Find worst days sorted by E#2+E#3
worst = sorted(daily.items(), key=lambda x: x[1][2] + x[1][3])[:12]
print("Worst E#2+E#3 days — breakdown of ALL entries that day:")
print("%12s | %8s | %8s | %8s | %8s | %9s | %12s" % (
    "Date", "E#1", "E#2", "E#3", "E#6", "E23 sum", "Total old"))
for d, ep in worst:
    e1 = ep.get(1, 0)
    e2 = ep.get(2, 0)
    e3 = ep.get(3, 0)
    e6 = ep.get(6, 0)
    e23 = e2 + e3
    total = e1 + e2 + e3 + e6
    print("%12s | %8.2f | %8.2f | %8.2f | %8.2f | %9.2f | %11.2f" % (
        d, e1, e2, e3, e6, e23, total))

# Now compare: new config (no E#1, E#2+E#3 1x) vs doubled
print()
print("=" * 75)
print("NEW CONFIG DRAWDOWN COMPARISON (E#1 dropped)")
print("=" * 75)
print("Worst days — new config at 1 contract vs doubled:")
print("%12s | %11s | %11s | %11s" % ("Date", "E#2+E#3 1x", "E#2+E#3 2x", "Old 3-entry"))
for d, ep in worst:
    e1 = ep.get(1, 0)
    e23 = ep.get(2, 0) + ep.get(3, 0)
    e23_2x = e23 * 2
    e6 = ep.get(6, 0)
    old_total = e1 + ep.get(2, 0) + ep.get(3, 0) + e6
    print("%12s | %10.2f | %10.2f | %10.2f" % (d, e23, e23_2x, old_total))
