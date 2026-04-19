#!/usr/bin/env python3
"""Check if E6 call-only on down days is economically viable.
Use spread_snapshots to estimate call credit available at 14:00 on historical down days."""
import sqlite3

conn = sqlite3.connect("data/backtesting.db")

# Down days we identified
down_days = [
    ("2026-02-11", 6947.81),
    ("2026-02-12", 6869.44),
    ("2026-02-23", 6837.98),
    ("2026-02-26", 6889.11),
    ("2026-03-05", 6780.94),
    ("2026-03-11", 6760.68),
    ("2026-03-12", 6696.26),
    ("2026-03-13", 6638.63),
    ("2026-03-20", 6541.21),
    ("2026-03-23", 6605.45),
    ("2026-04-10", 6818.19),
]

print("E6 CALL-ONLY DOWN-DAY CREDIT VIABILITY CHECK")
print("=" * 90)
print("For each down day, find CALL spread snapshots at ~14:00 where the short")
print("call strike is ~50-70pt above SPX. Extract actual bid/ask to estimate")
print("what credit an E6 call-only entry would have collected.")
print()
print(f"{'Date':>12} | {'SPX 14:00':>8} | {'Entry':>5} | {'SC Strike':>9} | {'OTM':>5} | {'SC Ask':>7} | {'LC Bid':>7} | {'Est Credit':>10}")
print("-" * 90)

results = []
for d, spx in down_days:
    # Get call spread snapshots around 14:00 for any active entry
    # Filter: short call strike should be 40-80pt above SPX
    rows = conn.execute(
        "SELECT ss.timestamp, ss.entry_number, ss.short_call_ask, ss.long_call_bid, "
        "ss.call_spread_value, te.short_call_strike, te.long_call_strike "
        "FROM spread_snapshots ss "
        "JOIN trade_entries te ON ss.entry_number = te.entry_number AND DATE(ss.timestamp) = te.date "
        "WHERE ss.timestamp >= ? AND ss.timestamp <= ? "
        "AND ss.short_call_ask IS NOT NULL AND ss.short_call_ask > 0 "
        "AND te.short_call_strike > 0 "
        "ORDER BY ss.timestamp LIMIT 3",
        (d + " 13:55:00", d + " 14:10:00")
    ).fetchall()

    found = False
    for r in rows:
        ts, en, sc_ask, lc_bid, csv, sc_strike, lc_strike = r
        if not sc_strike:
            continue
        otm = sc_strike - spx
        if 40 <= otm <= 80:
            # This is a reasonable proxy for what E6 call-only would face
            est_credit = (sc_ask - (lc_bid or 0)) * 100 if sc_ask else 0
            # We'd SELL the short, so we'd want mid or better
            print(f"  {d} | {spx:>7.2f} | E#{en:>3} | {sc_strike:>8.0f} | {otm:>4.0f}pt | "
                  f"{sc_ask:>6.2f} | {lc_bid or 0:>6.2f} | ${est_credit:>8.0f}")
            results.append({"date": d, "otm": otm, "credit": est_credit, "sc_ask": sc_ask})
            found = True
            break  # one proxy per day

    if not found:
        # No active entries with suitable strike — look for ANY call spread to estimate
        rows = conn.execute(
            "SELECT te.short_call_strike, te.long_call_strike, te.call_credit "
            "FROM trade_entries te WHERE te.date=? AND te.call_credit > 0 "
            "ORDER BY te.entry_number LIMIT 1",
            (d,)
        ).fetchone()
        if rows:
            sc_strike, lc_strike, call_credit = rows
            otm_am = sc_strike - spx  # OTM vs 14:00 SPX (not entry time)
            print(f"  {d} | {spx:>7.2f} | (AM)  | {sc_strike:>8.0f} | {otm_am:>4.0f}pt | "
                  f"    -- | -- | entry_credit=${call_credit:.0f} (AM)")
        else:
            print(f"  {d} | {spx:>7.2f} | -- | no call data available")

print()
print("=" * 90)
print("ANALYSIS")
print("=" * 90)
if results:
    avg_credit = sum(r["credit"] for r in results) / len(results)
    min_credit = min(r["credit"] for r in results)
    max_credit = max(r["credit"] for r in results)
    print(f"\nData points found: {len(results)}")
    print(f"Estimated credit range at 14:00 on down days (for ~50pt OTM calls):")
    print(f"  Min: ${min_credit:.0f}")
    print(f"  Max: ${max_credit:.0f}")
    print(f"  Avg: ${avg_credit:.0f}")

    print()
    print("Economic viability:")
    print(f"  Commission per side: $5")
    print(f"  Min credit needed to be worth placing (break-even + buffer): $30-40")
    print(f"  Minimum viable per active regime:")
    print(f"    VIX<18 regime: $100+ required")
    print(f"    VIX 18-22: $50+")
    print(f"    VIX 22-28: $30+")
    print(f"    VIX>=28: $30+")

    viable_strict = sum(1 for r in results if r["credit"] >= 50)
    viable_low = sum(1 for r in results if r["credit"] >= 30)
    print()
    print(f"  Days meeting strict threshold ($50+): {viable_strict}/{len(results)}")
    print(f"  Days meeting low threshold ($30+): {viable_low}/{len(results)}")

print()
print("CAVEATS:")
print("  1. These are snapshot mid/ask prices from ALREADY-PLACED entries")
print("     whose short call strikes we happened to have. A dedicated E6")
print("     call-only entry might choose a slightly different strike.")
print("  2. Small sample of 11 down days.")
print("  3. Snapshot bid/ask is a mid-price approximation of what we'd actually fill.")
print("  4. E6 call-only would face MKT-011 credit gate — if credit below")
print("     regime minimum, entry would be skipped.")
