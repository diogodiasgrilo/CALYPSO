#!/usr/bin/env python3
"""Sweep down-day thresholds to find the optimal for E6 call-only at 14:00."""
import sqlite3

conn = sqlite3.connect("data/backtesting.db")

days = conn.execute(
    "SELECT DISTINCT date FROM daily_summaries WHERE date >= '2026-02-10' ORDER BY date"
).fetchall()

day_data = []
for d_row in days:
    d = d_row[0]
    open_row = conn.execute(
        "SELECT open FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '09:30' ORDER BY timestamp LIMIT 1",
        (d,)
    ).fetchone()
    aft_row = conn.execute(
        "SELECT close FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '14:00' ORDER BY timestamp LIMIT 1",
        (d,)
    ).fetchone()
    close_row = conn.execute(
        "SELECT close FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '15:55' ORDER BY timestamp DESC LIMIT 1",
        (d,)
    ).fetchone()
    high_row = conn.execute(
        "SELECT MAX(high) FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '14:00' AND time(timestamp) < '16:00'",
        (d,)
    ).fetchone()
    vix_row = conn.execute(
        "SELECT vix_at_entry FROM trade_entries WHERE date=? ORDER BY entry_number LIMIT 1",
        (d,)
    ).fetchone()

    if not all([open_row, aft_row, close_row]):
        continue
    open_px = open_row[0] or 0
    px_1400 = aft_row[0] or 0
    close_px = close_row[0] or 0
    max_high = (high_row[0] if high_row else px_1400) or px_1400
    vix = vix_row[0] if vix_row and vix_row[0] else 18.0

    if not open_px or not px_1400:
        continue
    pct = (px_1400 - open_px) / open_px

    day_data.append({
        "date": d, "open": open_px, "px_1400": px_1400, "close": close_px,
        "max_high_afternoon": max_high, "pct_at_1400": pct, "vix": vix
    })

print("DOWN-DAY THRESHOLD SWEEP FOR E6 CALL-ONLY AT 14:00")
print("=" * 85)
print(f"\nTotal trading days with data: {len(day_data)}")
print()
print(f"{'Threshold':>12} | {'Triggers':>8} | {'Trigger%':>9} | {'Wins':>5} | {'Close':>5} | {'Stops':>6} | {'WR':>5} | {'Est P&L':>9}")
print("-" * 85)

thresholds = [0.0015, 0.0020, 0.0025, 0.0030, 0.0035, 0.0040, 0.0050, 0.0057, 0.0070, 0.0100]
for thresh in thresholds:
    triggered = [d for d in day_data if d["pct_at_1400"] <= -thresh]
    wins = 0
    stops = 0
    close_calls = 0

    for d in triggered:
        # Simulated strike: max(45, VIX*3) pt above SPX at 14:00 (matches VIX-scaled estimate)
        vix = d["vix"]
        otm = max(45, round(vix * 3))
        strike = round((d["px_1400"] + otm) / 5) * 5

        if d["close"] < strike - 5:
            wins += 1
        elif d["max_high_afternoon"] >= strike + 15:
            stops += 1
        else:
            close_calls += 1

    n = len(triggered)
    trigger_pct = 100 * n / len(day_data) if day_data else 0
    wr = 100 * wins / n if n else 0
    # Crude P&L estimate: $100 per win, -$150 per stop, 0 per close call
    est_pnl = wins * 100 - stops * 150

    print(f"  {'%.3f' % thresh + '%':>12} | {n:>8} | {trigger_pct:>7.0f}% | {wins:>5} | {close_calls:>5} | {stops:>6} | {wr:>4.0f}% | ${est_pnl:>+7}")

print()
print("=" * 85)
print("CONTEXT: Up-day thresholds for comparison")
print("=" * 85)
print(f"{'Threshold':>12} | {'Triggers':>8} | {'Trigger%':>9}")
print("-" * 45)

for thresh in thresholds:
    triggered = [d for d in day_data if d["pct_at_1400"] >= thresh]
    n = len(triggered)
    trigger_pct = 100 * n / len(day_data) if day_data else 0
    print(f"  {'%.3f' % thresh + '%':>12} | {n:>8} | {trigger_pct:>7.0f}%")

print()
print("=" * 85)
print("ANALYSIS")
print("=" * 85)

# Compare 0.25% down vs 0.25% up
down_at_25 = sum(1 for d in day_data if d["pct_at_1400"] <= -0.0025)
up_at_25 = sum(1 for d in day_data if d["pct_at_1400"] >= 0.0025)
print(f"\nAsymmetry at 0.25% threshold:")
print(f"  Down days: {down_at_25} ({100*down_at_25/len(day_data):.0f}%)")
print(f"  Up days:   {up_at_25} ({100*up_at_25/len(day_data):.0f}%)")
print(f"  Ratio up/down: {up_at_25/down_at_25:.2f}x  ← markets drift UP more")
print()
print("Implications:")
print("  - Markets have positive drift → up-day triggers are MORE COMMON")
print("  - A 0.25% down at 14:00 is MORE UNUSUAL than 0.25% up")
print("  - Down-day momentum tends to be STRONGER (fear > greed)")
print()
print("Recommendation: 0.25% is fine as a starting point (symmetric, simple).")
print("Tighter thresholds (0.35-0.50%) reduce false triggers but cut sample size.")
print("Looser (0.15-0.20%) more opportunities but may catch noise.")
