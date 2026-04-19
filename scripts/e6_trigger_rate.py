#!/usr/bin/env python3
"""How often does E6 actually fire? Is the reserved margin being wasted?"""
import sqlite3
from collections import defaultdict

conn = sqlite3.connect("data/backtesting.db")

# Find the date Upday-035 was introduced — first entry with override_reason = upday-035
first_e6 = conn.execute(
    "SELECT MIN(date) FROM trade_entries WHERE override_reason LIKE '%upday%'"
).fetchone()[0]
print(f"First E6 (Upday-035) entry: {first_e6}")

# Count trading days since first E6 vs E6 fires
all_trading_days = conn.execute(
    "SELECT DISTINCT date FROM daily_summaries WHERE date >= ? ORDER BY date",
    (first_e6,)
).fetchall()
trading_days = [d[0] for d in all_trading_days]

e6_fires = conn.execute(
    "SELECT DISTINCT date FROM trade_entries WHERE override_reason LIKE '%upday%'"
).fetchall()
e6_dates = set(d[0] for d in e6_fires)

# Also check skipped E6 (conditional trigger not met)
e6_skipped = conn.execute(
    "SELECT DISTINCT date FROM skipped_entries WHERE skip_reason LIKE '%up-day%' OR skip_reason LIKE '%upday%' OR skip_reason LIKE '%no up-day%'"
).fetchall()
e6_skip_dates = set(d[0] for d in e6_skipped)

fires = len(e6_dates)
skips = len(e6_skip_dates)
total = len(trading_days)

print(f"\nSince {first_e6}:")
print(f"  Trading days: {total}")
print(f"  E6 fired: {fires} days ({100*fires/total:.0f}%)")
print(f"  E6 not triggered (skipped): {skips} days ({100*skips/total:.0f}%)")
print(f"  Unaccounted: {total - fires - skips} days")

# Capital usage estimate
MARGIN_PER_CONTRACT = 11000  # ~$11k per IC contract
print(f"\nCapital efficiency at ${MARGIN_PER_CONTRACT}/contract margin:")
print(f"  Current config: ~$33k peak margin when all 3 entries + E6 placed")
print(f"  With E#1 dropped: ~$22k margin (E#2+E#3 @1x) on non-E6 days")
print(f"  With E#1 dropped + E6: ~$33k margin when E6 fires")
print(f"  Alternative: ~$44k margin (E#2+E#3 @2x) every day, no E6")

# Lifetime P&L comparison
entries = conn.execute(
    "SELECT date, entry_number, call_credit, put_credit, override_reason FROM trade_entries WHERE date >= '2026-02-10'"
).fetchall()
stops = conn.execute("SELECT date, entry_number, side, actual_debit FROM trade_stops").fetchall()
stop_map = {}
for s in stops:
    stop_map[(s[0], s[1], s[2])] = s[3] or 0

def entry_pnl(e):
    d, en = e[0], e[1]
    pnl = 0.0
    for side, credit in [("call", e[2] or 0), ("put", e[3] or 0)]:
        if credit <= 0:
            continue
        debit = stop_map.get((d, en, side))
        if debit is not None:
            pnl += (credit - debit - 5.0 - 2.5)
        else:
            pnl += (credit - 5.0)
    return pnl

e23 = [e for e in entries if e[1] in (2, 3)]
e6_entries = [e for e in entries if e[4] and "upday" in str(e[4]).lower()]

e23_pnl = sum(entry_pnl(e) for e in e23)
e6_pnl = sum(entry_pnl(e) for e in e6_entries)

print(f"\n=== LIFETIME P&L COMPARISON ===")
print(f"Current (E#2+E#3 @1x + E6 @1x): ${e23_pnl + e6_pnl:.2f}")
print(f"Alternative (E#2+E#3 @2x, no E6): ${e23_pnl * 2:.2f}")
print(f"Delta: ${e23_pnl * 2 - (e23_pnl + e6_pnl):.2f}")

# Over ~252 trading days/year extrapolation
days_in_data = len(trading_days) if trading_days else 48
annualized_current = (e23_pnl + e6_pnl) * (252 / days_in_data)
annualized_doubled = (e23_pnl * 2) * (252 / days_in_data)
print(f"\nAnnualized (x252 days):")
print(f"  Current: ~${annualized_current:.0f}/year")
print(f"  Doubled: ~${annualized_doubled:.0f}/year")
print(f"  Extra profit: ~${annualized_doubled - annualized_current:.0f}/year")
