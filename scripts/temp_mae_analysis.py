#!/usr/bin/env python3
"""
SPX Minute-of-Hour Volatility & MAE Analysis
Uses ALL trading days of heartbeat data (19 days, 28K+ data points)
"""
import sys
from collections import defaultdict
import statistics

def load_data(filepath):
    """Load SPX price data from CSV."""
    data = []  # list of (date, hour, minute, second, price)
    with open(filepath) as f:
        header = f.readline()
        for line in f:
            parts = line.strip().split()
            if len(parts) == 5:
                date, h, m, s, price = parts
                data.append((date, int(h), int(m), int(s), float(price)))
    return data

def build_minute_series(data):
    """Build per-day, per-minute price series (using last price in each minute)."""
    # Group by (date, hour, minute) -> last price
    minute_prices = {}
    for date, h, m, s, price in data:
        key = (date, h, m)
        if key not in minute_prices or s > minute_prices[key][0]:
            minute_prices[key] = (s, price)
    
    # Convert to (date, hour, minute) -> price
    result = {k: v[1] for k, v in minute_prices.items()}
    return result

def analyze_1min_moves(minute_prices):
    """Absolute 1-minute price moves by minute-of-hour."""
    moves_by_minute = defaultdict(list)
    
    dates = sorted(set(k[0] for k in minute_prices))
    for date in dates:
        for h in range(9, 17):
            for m in range(60):
                key_now = (date, h, m)
                # Next minute
                if m < 59:
                    key_next = (date, h, m+1)
                else:
                    key_next = (date, h+1, 0)
                
                if key_now in minute_prices and key_next in minute_prices:
                    move = abs(minute_prices[key_next] - minute_prices[key_now])
                    moves_by_minute[m].append(move)
    
    return moves_by_minute

def analyze_5min_moves(minute_prices):
    """Absolute 5-minute price moves starting at each minute-of-hour."""
    moves_by_minute = defaultdict(list)
    
    dates = sorted(set(k[0] for k in minute_prices))
    for date in dates:
        for h in range(9, 16):
            for m in range(60):
                key_now = (date, h, m)
                # 5 minutes later
                future_m = m + 5
                future_h = h
                if future_m >= 60:
                    future_m -= 60
                    future_h += 1
                key_future = (date, future_h, future_m)
                
                if key_now in minute_prices and key_future in minute_prices:
                    move = abs(minute_prices[key_future] - minute_prices[key_now])
                    moves_by_minute[m].append(move)
    
    return moves_by_minute

def analyze_mae_30min(minute_prices):
    """30-minute Max Adverse Excursion from each minute-of-hour.
    MAE = max absolute move within 30 minutes of entry."""
    mae_by_minute = defaultdict(list)
    
    dates = sorted(set(k[0] for k in minute_prices))
    for date in dates:
        # Only analyze during HYDRA trading hours (10:55 - 13:35 ET)
        for h in range(10, 14):
            for m in range(60):
                if h == 10 and m < 55:
                    continue
                if h == 13 and m > 35:
                    continue
                    
                key_now = (date, h, m)
                if key_now not in minute_prices:
                    continue
                
                entry_price = minute_prices[key_now]
                max_adverse = 0
                
                # Look at each minute for 30 minutes
                for offset in range(1, 31):
                    future_m = m + offset
                    future_h = h
                    while future_m >= 60:
                        future_m -= 60
                        future_h += 1
                    
                    key_future = (date, future_h, future_m)
                    if key_future in minute_prices:
                        move = abs(minute_prices[key_future] - entry_price)
                        max_adverse = max(max_adverse, move)
                
                if max_adverse > 0:
                    mae_by_minute[m].append(max_adverse)
    
    return mae_by_minute

def analyze_10min_moves(minute_prices):
    """10-minute absolute moves from each minute-of-hour (during HYDRA hours)."""
    moves_by_minute = defaultdict(list)
    
    dates = sorted(set(k[0] for k in minute_prices))
    for date in dates:
        for h in range(10, 14):
            for m in range(60):
                if h == 10 and m < 55:
                    continue
                if h == 13 and m > 35:
                    continue
                    
                key_now = (date, h, m)
                future_m = m + 10
                future_h = h
                if future_m >= 60:
                    future_m -= 60
                    future_h += 1
                key_future = (date, future_h, future_m)
                
                if key_now in minute_prices and key_future in minute_prices:
                    move = abs(minute_prices[key_future] - minute_prices[key_now])
                    moves_by_minute[m].append(move)
    
    return moves_by_minute

def analyze_directional_bias(minute_prices):
    """Check if certain minutes have directional bias (more up or down moves)."""
    bias_by_minute = defaultdict(lambda: {"up": 0, "down": 0, "flat": 0})
    
    dates = sorted(set(k[0] for k in minute_prices))
    for date in dates:
        for h in range(10, 14):
            for m in range(60):
                key_now = (date, h, m)
                if m < 59:
                    key_next = (date, h, m+1)
                else:
                    key_next = (date, h+1, 0)
                
                if key_now in minute_prices and key_next in minute_prices:
                    diff = minute_prices[key_next] - minute_prices[key_now]
                    if diff > 0.5:
                        bias_by_minute[m]["up"] += 1
                    elif diff < -0.5:
                        bias_by_minute[m]["down"] += 1
                    else:
                        bias_by_minute[m]["flat"] += 1
    
    return bias_by_minute

def main():
    filepath = "/tmp/spx_all_data.csv"
    print("=" * 80)
    print("SPX MINUTE-OF-HOUR ANALYSIS — ALL TRADING DAYS")
    print("=" * 80)
    
    data = load_data(filepath)
    print(f"\nLoaded {len(data)} data points")
    
    dates = sorted(set(d[0] for d in data))
    print(f"Trading days: {len(dates)} ({dates[0]} to {dates[-1]})")
    
    minute_prices = build_minute_series(data)
    print(f"Unique minutes: {len(minute_prices)}")
    
    # =========================================================================
    # 1. ONE-MINUTE ABSOLUTE MOVES
    # =========================================================================
    print("\n" + "=" * 80)
    print("1. AVERAGE 1-MINUTE ABSOLUTE MOVE BY MINUTE-OF-HOUR")
    print("   (All hours 9:30-16:00, all trading days)")
    print("=" * 80)
    
    moves_1m = analyze_1min_moves(minute_prices)
    results_1m = []
    for m in range(60):
        if m in moves_1m and len(moves_1m[m]) >= 5:
            avg = statistics.mean(moves_1m[m])
            med = statistics.median(moves_1m[m])
            results_1m.append((m, avg, med, len(moves_1m[m])))
    
    results_1m.sort(key=lambda x: x[1], reverse=True)
    print(f"\n{'Min':>3} {'Avg Move':>10} {'Median':>10} {'N':>5}  Bar")
    print("-" * 65)
    max_avg = max(r[1] for r in results_1m)
    for m, avg, med, n in results_1m:
        bar = "█" * int(avg / max_avg * 30)
        marker = " ◄ CURRENT" if m in (5, 35) else ""
        marker = " ◄ ON-HOUR" if m in (0, 30) else marker
        marker = " ◄ QUARTER" if m in (15, 45) else marker
        print(f":{m:02d}  {avg:8.3f}pt  {med:8.3f}pt  {n:4d}  {bar}{marker}")
    
    # =========================================================================
    # 2. FIVE-MINUTE ABSOLUTE MOVES
    # =========================================================================
    print("\n" + "=" * 80)
    print("2. AVERAGE 5-MINUTE ABSOLUTE MOVE BY MINUTE-OF-HOUR")
    print("=" * 80)
    
    moves_5m = analyze_5min_moves(minute_prices)
    results_5m = []
    for m in range(60):
        if m in moves_5m and len(moves_5m[m]) >= 5:
            avg = statistics.mean(moves_5m[m])
            med = statistics.median(moves_5m[m])
            results_5m.append((m, avg, med, len(moves_5m[m])))
    
    results_5m.sort(key=lambda x: x[1], reverse=True)
    print(f"\n{'Min':>3} {'Avg Move':>10} {'Median':>10} {'N':>5}  Bar")
    print("-" * 65)
    max_avg = max(r[1] for r in results_5m)
    for m, avg, med, n in results_5m:
        bar = "█" * int(avg / max_avg * 30)
        marker = " ◄ CURRENT" if m in (5, 35) else ""
        marker = " ◄ ON-HOUR" if m in (0, 30) else marker
        marker = " ◄ QUARTER" if m in (15, 45) else marker
        print(f":{m:02d}  {avg:8.3f}pt  {med:8.3f}pt  {n:4d}  {bar}{marker}")
    
    # =========================================================================
    # 3. 30-MIN MAX ADVERSE EXCURSION (MAE) - HYDRA HOURS ONLY
    # =========================================================================
    print("\n" + "=" * 80)
    print("3. 30-MIN MAX ADVERSE EXCURSION (MAE) BY MINUTE-OF-HOUR")
    print("   (HYDRA hours 10:55-13:35 ET only)")
    print("=" * 80)
    
    mae_30m = analyze_mae_30min(minute_prices)
    results_mae = []
    for m in range(60):
        if m in mae_30m and len(mae_30m[m]) >= 3:
            avg = statistics.mean(mae_30m[m])
            med = statistics.median(mae_30m[m])
            p75 = sorted(mae_30m[m])[int(len(mae_30m[m]) * 0.75)]
            results_mae.append((m, avg, med, p75, len(mae_30m[m])))
    
    results_mae.sort(key=lambda x: x[1])  # Sort ascending (lower MAE = better)
    print(f"\n{'Rank':>4} {'Min':>3} {'Avg MAE':>10} {'Median':>10} {'P75':>10} {'N':>5}  Rating")
    print("-" * 70)
    for rank, (m, avg, med, p75, n) in enumerate(results_mae, 1):
        if avg < 12:
            rating = "★★★ EXCELLENT"
        elif avg < 15:
            rating = "★★ GOOD"
        elif avg < 18:
            rating = "★ OK"
        else:
            rating = "⚠ HIGH"
        marker = ""
        if m in (5, 35): marker = " [CURRENT]"
        elif m in (0, 30): marker = " [ON-HOUR]"
        elif m in (15, 45): marker = " [QUARTER]"
        print(f"  {rank:2d}  :{m:02d}  {avg:8.2f}pt  {med:8.2f}pt  {p75:8.2f}pt  {n:4d}  {rating}{marker}")
    
    # =========================================================================
    # 4. STRATEGY COMPARISON
    # =========================================================================
    print("\n" + "=" * 80)
    print("4. STRATEGY COMPARISON (30-MIN MAE)")
    print("=" * 80)
    
    strategies = {
        ":00/:30 (on-hour)":     [0, 30],
        ":05/:35 (CURRENT)":     [5, 35],
        ":10/:40":               [10, 40],
        ":12/:42":               [12, 42],
        ":15/:45 (quarter)":     [15, 45],
        ":20/:50":               [20, 50],
        ":25/:55":               [25, 55],
    }
    
    strategy_results = []
    for name, minutes in strategies.items():
        all_mae = []
        for m in minutes:
            if m in mae_30m:
                all_mae.extend(mae_30m[m])
        if all_mae:
            avg = statistics.mean(all_mae)
            med = statistics.median(all_mae)
            p75 = sorted(all_mae)[int(len(all_mae) * 0.75)]
            p90 = sorted(all_mae)[int(len(all_mae) * 0.90)]
            strategy_results.append((name, avg, med, p75, p90, len(all_mae)))
    
    strategy_results.sort(key=lambda x: x[1])
    print(f"\n{'Strategy':<25} {'Avg MAE':>10} {'Median':>10} {'P75':>10} {'P90':>10} {'N':>5}")
    print("-" * 75)
    baseline = None
    for name, avg, med, p75, p90, n in strategy_results:
        if "CURRENT" in name:
            baseline = avg
    for name, avg, med, p75, p90, n in strategy_results:
        delta = f"({avg - baseline:+.2f})" if baseline else ""
        print(f"  {name:<23} {avg:8.2f}pt  {med:8.2f}pt  {p75:8.2f}pt  {p90:8.2f}pt  {n:4d}  {delta}")
    
    # =========================================================================
    # 5. 10-MINUTE MOVES (immediate post-entry risk)
    # =========================================================================
    print("\n" + "=" * 80)
    print("5. 10-MINUTE ABSOLUTE MOVE COMPARISON (HYDRA hours)")
    print("=" * 80)
    
    moves_10m = analyze_10min_moves(minute_prices)
    
    strategy_10m = []
    for name, minutes in strategies.items():
        all_moves = []
        for m in minutes:
            if m in moves_10m:
                all_moves.extend(moves_10m[m])
        if all_moves:
            avg = statistics.mean(all_moves)
            med = statistics.median(all_moves)
            strategy_10m.append((name, avg, med, len(all_moves)))
    
    strategy_10m.sort(key=lambda x: x[1])
    print(f"\n{'Strategy':<25} {'Avg 10m':>10} {'Median':>10} {'N':>5}")
    print("-" * 55)
    for name, avg, med, n in strategy_10m:
        print(f"  {name:<23} {avg:8.2f}pt  {med:8.2f}pt  {n:4d}")
    
    # =========================================================================
    # 6. ROUND vs OFF-ROUND COMPARISON
    # =========================================================================
    print("\n" + "=" * 80)
    print("6. ROUND vs OFF-ROUND MINUTE VOLATILITY (1-min moves)")
    print("=" * 80)
    
    categories = {
        "On-hour (:00)": [0],
        "Half-hour (:30)": [30],
        "Quarters (:15,:45)": [15, 45],
        "Fives (:05,:10,:20,:25,:35,:40,:50,:55)": [5,10,20,25,35,40,50,55],
        "Off-round (all others)": [m for m in range(60) if m not in [0,5,10,15,20,25,30,35,40,45,50,55]],
    }
    
    for cat_name, minutes in categories.items():
        all_moves = []
        for m in minutes:
            if m in moves_1m:
                all_moves.extend(moves_1m[m])
        if all_moves:
            avg = statistics.mean(all_moves)
            med = statistics.median(all_moves)
            print(f"  {cat_name:<50} avg={avg:.3f}pt  med={med:.3f}pt  N={len(all_moves)}")
    
    # =========================================================================
    # 7. BEST ENTRY WINDOWS (consecutive calm minutes)
    # =========================================================================
    print("\n" + "=" * 80)
    print("7. CALMEST 5-MINUTE WINDOWS (lowest avg MAE)")
    print("=" * 80)
    
    # Find 5-minute windows with lowest average MAE
    windows = []
    for start in range(56):  # 0 to 55
        window_mins = list(range(start, start + 5))
        window_maes = []
        for m in window_mins:
            if m in mae_30m:
                window_maes.extend(mae_30m[m])
        if len(window_maes) >= 10:
            avg = statistics.mean(window_maes)
            windows.append((start, avg, len(window_maes)))
    
    windows.sort(key=lambda x: x[1])
    print(f"\n{'Window':>15} {'Avg MAE':>10} {'N':>5}")
    print("-" * 35)
    for start, avg, n in windows[:10]:
        end = start + 4
        print(f"  :{start:02d}-:{end:02d}       {avg:8.2f}pt  {n:4d}")
    print("  ...")
    for start, avg, n in windows[-5:]:
        end = start + 4
        print(f"  :{start:02d}-:{end:02d}       {avg:8.2f}pt  {n:4d}")
    
    # =========================================================================
    # 8. DIRECTIONAL BIAS
    # =========================================================================
    print("\n" + "=" * 80)
    print("8. DIRECTIONAL BIAS BY MINUTE (HYDRA hours, 1-min moves)")
    print("=" * 80)
    
    bias = analyze_directional_bias(minute_prices)
    print(f"\n{'Min':>3} {'Up%':>6} {'Down%':>6} {'Flat%':>6} {'Bias':>8}  Direction")
    print("-" * 55)
    bias_results = []
    for m in range(60):
        if m in bias:
            total = bias[m]["up"] + bias[m]["down"] + bias[m]["flat"]
            if total > 0:
                up_pct = bias[m]["up"] / total * 100
                down_pct = bias[m]["down"] / total * 100
                flat_pct = bias[m]["flat"] / total * 100
                net_bias = up_pct - down_pct
                bias_results.append((m, up_pct, down_pct, flat_pct, net_bias, total))
    
    # Sort by absolute bias (most biased first)
    bias_results.sort(key=lambda x: abs(x[4]), reverse=True)
    for m, up_pct, down_pct, flat_pct, net_bias, total in bias_results[:15]:
        direction = "↑ BULLISH" if net_bias > 5 else ("↓ BEARISH" if net_bias < -5 else "→ NEUTRAL")
        print(f":{m:02d}  {up_pct:5.1f}% {down_pct:5.1f}% {flat_pct:5.1f}% {net_bias:+6.1f}%  {direction}  (N={total})")
    
    # =========================================================================
    # 9. OPTIMAL SINGLE PAIR RECOMMENDATION
    # =========================================================================
    print("\n" + "=" * 80)
    print("9. OPTIMAL ENTRY MINUTE PAIR (every combination)")
    print("=" * 80)
    
    # Test every possible pair of minutes (spaced 25-35 apart, like :05/:35)
    pair_results = []
    for m1 in range(60):
        for offset in range(25, 36):  # 25-35 minutes apart
            m2 = (m1 + offset) % 60
            all_mae_pair = []
            for m in [m1, m2]:
                if m in mae_30m:
                    all_mae_pair.extend(mae_30m[m])
            if len(all_mae_pair) >= 10:
                avg = statistics.mean(all_mae_pair)
                pair_results.append((m1, m2, avg, len(all_mae_pair)))
    
    pair_results.sort(key=lambda x: x[2])
    print(f"\n{'Pair':>12} {'Avg MAE':>10} {'N':>5}  {'vs Current':>12}")
    print("-" * 50)
    current_mae = None
    for m1, m2, avg, n in pair_results:
        if m1 == 5 and m2 == 35:
            current_mae = avg
            break
    for m1, m2, avg, n in pair_results[:15]:
        delta = f"({avg - current_mae:+.2f})" if current_mae else ""
        marker = " ◄◄◄ CURRENT" if (m1 == 5 and m2 == 35) else ""
        print(f"  :{m1:02d}/:{m2:02d}   {avg:8.2f}pt  {n:4d}  {delta:>12}{marker}")
    
    # Find current rank
    for i, (m1, m2, avg, n) in enumerate(pair_results):
        if m1 == 5 and m2 == 35:
            print(f"\n  Current :05/:35 ranks #{i+1} out of {len(pair_results)} pairs")
            break
    
    # =========================================================================
    # 10. SUMMARY
    # =========================================================================
    print("\n" + "=" * 80)
    print("10. EXECUTIVE SUMMARY")
    print("=" * 80)
    
    print(f"""
DATA: {len(data)} price points across {len(dates)} trading days ({dates[0]} to {dates[-1]})

KEY FINDINGS:
""")
    
    # Get rankings for key strategies
    for name, avg, med, p75, p90, n in strategy_results:
        rank = [i for i, (n2, _, _, _, _, _) in enumerate(strategy_results) if n2 == name][0] + 1
        print(f"  {rank}. {name}: avg MAE = {avg:.2f}pt (N={n})")


if __name__ == "__main__":
    main()
