#!/usr/bin/env python3
"""Temporary script to analyze max adverse excursion by minute-of-hour.
Run on VM: .venv/bin/python scripts/temp_mae_analysis.py < /tmp/spx_data.csv
"""
import sys
from collections import defaultdict

prices = []
with open('/tmp/spx_data.csv') as f:
    for line in f:
        parts = line.strip().split()
        if len(parts) == 5:
            date, hour, minute, second, price = parts
            h, m = int(hour), int(minute)
            if 10 <= h <= 13:
                prices.append((date, h, m, int(second), float(price)))

minute_prices = {}
for date, h, m, s, p in prices:
    key = (date, h, m)
    if key not in minute_prices:
        minute_prices[key] = p

dates = sorted(set(d for d, h, m in minute_prices.keys()))

# MAX ADVERSE EXCURSION in 30 min after entry
mae_by_minute = defaultdict(list)
for date in dates:
    for h in range(10, 14):
        for m in range(60):
            k_entry = (date, h, m)
            if k_entry not in minute_prices:
                continue
            entry_price = minute_prices[k_entry]
            max_move = 0
            for delta in range(1, 31):
                nm = m + delta
                nh = h
                while nm >= 60:
                    nh += 1
                    nm -= 60
                k_future = (date, nh, nm)
                if k_future in minute_prices:
                    move = abs(minute_prices[k_future] - entry_price)
                    max_move = max(max_move, move)
            if max_move > 0:
                mae_by_minute[m].append(max_move)

print("=== MAX ADVERSE EXCURSION IN 30 MIN AFTER ENTRY ===")
print("(Higher = more likely to hit a stop)")
print()

strategies = [
    ("A) :00/:30 (on the hour)", [0, 30]),
    ("B) :05/:35 (current HYDRA)", [5, 35]),
    ("C) :15/:45 (quarter offsets)", [15, 45]),
    ("D) :10/:40", [10, 40]),
    ("E) :20/:50", [20, 50]),
    ("F) :08/:38", [8, 38]),
    ("G) :12/:42", [12, 42]),
]

header = f"{'Strategy':<30} {'AvgMAE':>8} {'MedMAE':>8} {'P90MAE':>8} {'MaxMAE':>8} {'N':>4}"
print(header)
for name, minutes in strategies:
    all_mae = []
    for m in minutes:
        all_mae.extend(mae_by_minute[m])
    if all_mae:
        vals = sorted(all_mae)
        avg = sum(vals) / len(vals)
        med = vals[len(vals)//2]
        p90 = vals[int(len(vals)*0.9)]
        mx = max(vals)
        print(f"{name:<30} {avg:>8.2f} {med:>8.2f} {p90:>8.2f} {mx:>8.2f} {len(vals):>4}")

# Top 10 calmest and most volatile
print()
print("=== TOP 10 CALMEST MINUTES (lowest avg 30-min MAE) ===")
results = []
for m in range(60):
    if mae_by_minute[m] and len(mae_by_minute[m]) >= 5:
        avg = sum(mae_by_minute[m]) / len(mae_by_minute[m])
        results.append((avg, m))
results.sort()
for rank, (avg, m) in enumerate(results[:10], 1):
    label = ""
    if m in [0, 30]: label = "ROUND"
    elif m in [5, 35]: label = "CURRENT"
    elif m in [15, 45]: label = "QUARTER"
    elif m in [10, 40]: label = ":10/:40"
    print(f"  #{rank}: :{m:02d} avg_mae={avg:.2f}pts  {label}")

print()
print("=== TOP 10 MOST VOLATILE MINUTES (highest avg 30-min MAE) ===")
for rank, (avg, m) in enumerate(reversed(results[-10:]), 1):
    label = ""
    if m in [0, 30]: label = "ROUND"
    elif m in [5, 35]: label = "CURRENT"
    elif m in [15, 45]: label = "QUARTER"
    elif m in [10, 40]: label = ":10/:40"
    print(f"  #{rank}: :{m:02d} avg_mae={avg:.2f}pts  {label}")

# Also: directional bias check - does SPX tend to trend or mean-revert
# at different minutes?
print()
print("=== DIRECTIONAL MOVES (not absolute) AT ROUND TIMES ===")
print("Positive = SPX tends to go UP in the 5min after this minute")
print("Negative = SPX tends to go DOWN")
for m_check in [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]:
    moves = []
    for date in dates:
        for h in range(10, 14):
            k1 = (date, h, m_check)
            nm = m_check + 5
            nh = h
            if nm >= 60:
                nh += 1
                nm -= 60
            k2 = (date, nh, nm)
            if k1 in minute_prices and k2 in minute_prices:
                moves.append(minute_prices[k2] - minute_prices[k1])
    if moves:
        avg = sum(moves) / len(moves)
        up_pct = sum(1 for x in moves if x > 0) / len(moves) * 100
        print(f"  :{m_check:02d}  avg_dir={avg:+.2f}pts  up_pct={up_pct:.0f}%  n={len(moves)}")
