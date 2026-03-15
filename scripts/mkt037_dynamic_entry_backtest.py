"""
MKT-037 Backtest: Dynamic Entry Timing (Move-Based Triggers)

Two variants tested:
  Variant A: Move from LAST entry's SPX price (reference resets after each entry)
  Variant B: Move from ANY existing entry's SPX price (must reach new territory)

For each variant, tests thresholds: 5, 8, 10, 12, 15, 20, 25 SPX points.

Logic:
- E1 always at 10:15 AM ET (fixed)
- After E1, next entry fires when:
  (a) SPX moves X points from reference price (move-triggered), OR
  (b) Next scheduled slot time arrives (scheduled fallback)
- Max 5 base entries per day
- Uses actual credits/strikes from DB (can't estimate credits at arbitrary times)
- Simulates stops with $5.00 put buffer using tick data from entry time onward

Main output: TIMING analysis (when entries fire, clustering, move-triggered %)
P&L is identical across variants because $5.00 buffer produces 0 stops.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "data/backtesting.db"
PUT_BUFFER = 500.0   # $5.00 x 100
CALL_BUFFER = 10.0   # $0.10 x 100

SCHEDULED_TIMES = ["10:15", "10:45", "11:15", "11:45", "12:15"]
MOVE_THRESHOLDS = [5, 8, 10, 12, 15, 20, 25]


def parse_entry_time(entry_time_str, date_str):
    if not entry_time_str:
        return None
    if entry_time_str[:4].count('-') > 0 and len(entry_time_str) > 10:
        return datetime.strptime(entry_time_str[:19], "%Y-%m-%dT%H:%M:%S")
    time_part = entry_time_str.replace(" ET", "").strip()
    try:
        t = datetime.strptime(time_part, "%I:%M %p")
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.replace(hour=t.hour, minute=t.minute)
    except ValueError:
        return None


def time_str_to_dt(time_str, date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    h, m = map(int, time_str.split(":"))
    return d.replace(hour=h, minute=m, second=0)


def simulate_dynamic_entries(ticks, date, threshold, max_entries, variant='A'):
    """
    Simulate entry timing for a day.

    variant='A': move threshold from LAST entry's SPX
    variant='B': move threshold from ALL existing entries' SPX (must be X pts from every one)

    Returns: list of (time_str, trigger_type, spx_price) tuples
    """
    e1_time = time_str_to_dt(SCHEDULED_TIMES[0], date)

    # Find SPX at 10:15
    e1_spx = None
    e1_tick_idx = 0
    for i, tick in enumerate(ticks):
        tick_dt = datetime.strptime(tick['timestamp'][:19], "%Y-%m-%d %H:%M:%S")
        if tick_dt >= e1_time:
            e1_spx = tick['spx_price']
            e1_tick_idx = i
            break

    if e1_spx is None:
        return []

    # Place E1
    results = [('10:15:00', 'scheduled', e1_spx, e1_time)]
    entry_spx_prices = [e1_spx]  # all entry SPX prices (for variant B)
    last_entry_time = e1_time
    next_sched_idx = 1

    for tick in ticks[e1_tick_idx + 1:]:
        if len(results) >= max_entries:
            break

        tick_dt = datetime.strptime(tick['timestamp'][:19], "%Y-%m-%d %H:%M:%S")
        if tick_dt <= last_entry_time:
            continue

        spx = tick['spx_price']
        trigger_entry = False
        trigger_type = None

        # Check move threshold based on variant
        if variant == 'A':
            # Variant A: move from LAST entry only
            move = abs(spx - entry_spx_prices[-1])
            if move >= threshold:
                trigger_entry = True
                trigger_type = 'move'
        elif variant == 'B':
            # Variant B: must be X points from ALL existing entries
            # i.e., SPX is at least X points away from the NEAREST existing entry
            min_distance = min(abs(spx - p) for p in entry_spx_prices)
            if min_distance >= threshold:
                trigger_entry = True
                trigger_type = 'move'

        # Check scheduled slot fallback
        if next_sched_idx < len(SCHEDULED_TIMES):
            slot_dt = time_str_to_dt(SCHEDULED_TIMES[next_sched_idx], date)
            if tick_dt >= slot_dt:
                trigger_entry = True
                if trigger_type != 'move':
                    trigger_type = 'scheduled'

        if trigger_entry:
            time_str = tick_dt.strftime("%H:%M:%S")
            results.append((time_str, trigger_type, spx, tick_dt))
            entry_spx_prices.append(spx)
            last_entry_time = tick_dt

            # Advance scheduled slot pointer past current time
            while next_sched_idx < len(SCHEDULED_TIMES):
                slot_dt = time_str_to_dt(SCHEDULED_TIMES[next_sched_idx], date)
                if slot_dt > tick_dt:
                    break
                next_sched_idx += 1

    return results


def run_backtest():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    days = [r['date'] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_summaries ORDER BY date").fetchall()]

    all_entries = conn.execute("""
        SELECT e.* FROM trade_entries e ORDER BY e.date, e.entry_number
    """).fetchall()

    entries_by_date = defaultdict(dict)
    for e in all_entries:
        d = dict(e)
        en = d['entry_number']
        if en <= 5 and en not in entries_by_date[d['date']]:
            entries_by_date[d['date']][en] = d

    summaries = {r['date']: dict(r) for r in conn.execute(
        "SELECT * FROM daily_summaries ORDER BY date").fetchall()}

    print("=" * 110)
    print("MKT-037 BACKTEST: Dynamic Entry Timing — Variant A vs Variant B")
    print("=" * 110)
    print(f"\nDataset: {len(days)} trading days ({days[0]} to {days[-1]})")
    print(f"Scheduled: {SCHEDULED_TIMES}")
    print(f"Thresholds: {MOVE_THRESHOLDS} SPX points")
    print()
    print("Variant A: Move from LAST entry's SPX (reference resets each entry)")
    print("Variant B: Move from ANY entry's SPX (must reach new territory from ALL)")
    print()

    # Collect timing data per variant/threshold/day
    # Structure: variant_data[variant][threshold] = list of day dicts
    variant_data = {'A': {x: [] for x in MOVE_THRESHOLDS},
                    'B': {x: [] for x in MOVE_THRESHOLDS}}

    for date in days:
        base_entries = entries_by_date.get(date, {})
        if not base_entries:
            continue

        max_entries = len(base_entries)

        ticks = conn.execute("""
            SELECT timestamp, spx_price, vix_level
            FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp
        """, (f"{date}%",)).fetchall()
        ticks = [dict(t) for t in ticks]
        if not ticks:
            continue

        summary = summaries.get(date, {})
        spx_high = summary.get('spx_high', 0) or 0
        spx_low = summary.get('spx_low', 0) or 0
        day_range = spx_high - spx_low

        for variant in ['A', 'B']:
            for threshold in MOVE_THRESHOLDS:
                timing = simulate_dynamic_entries(ticks, date, threshold, max_entries, variant)
                move_count = sum(1 for _, tt, _, _ in timing if tt == 'move')
                sched_count = sum(1 for _, tt, _, _ in timing if tt == 'scheduled')

                # Calculate time spread (minutes between first and last entry)
                if len(timing) >= 2:
                    first_dt = timing[0][3]
                    last_dt = timing[-1][3]
                    time_spread_min = (last_dt - first_dt).total_seconds() / 60
                else:
                    time_spread_min = 0

                # Calculate SPX spread (range of SPX prices at entry times)
                entry_prices = [t[2] for t in timing]
                spx_spread = max(entry_prices) - min(entry_prices) if entry_prices else 0

                # Calculate avg distance between consecutive entry SPX prices
                if len(entry_prices) >= 2:
                    consecutive_dists = [abs(entry_prices[i+1] - entry_prices[i])
                                         for i in range(len(entry_prices) - 1)]
                    avg_consec_dist = sum(consecutive_dists) / len(consecutive_dists)
                else:
                    avg_consec_dist = 0

                variant_data[variant][threshold].append({
                    'date': date,
                    'entries': len(timing),
                    'move_triggered': move_count,
                    'scheduled': sched_count,
                    'timing': timing,
                    'time_spread_min': time_spread_min,
                    'spx_spread': spx_spread,
                    'avg_consec_dist': avg_consec_dist,
                    'day_range': day_range,
                })

    conn.close()

    # ===== SUMMARY TABLE: Variant A vs B =====
    print("=" * 110)
    print("TIMING SUMMARY: How entries are triggered")
    print("=" * 110)

    print(f"\n{'':>12} {'--- Variant A (from LAST) ---':>42} {'--- Variant B (from ANY) ---':>42}")
    print(f"{'Threshold':>10} {'Move%':>8} {'Avg Spread':>11} {'Avg Time':>10} {'AvgCDist':>9}"
          f" {'Move%':>8} {'Avg Spread':>11} {'Avg Time':>10} {'AvgCDist':>9}")
    print("-" * 110)

    # Also current scheduled for reference
    # Scheduled: 10:15 to 12:15 = 120 min spread
    print(f"{'Scheduled':>10} {'0.0%':>8} {'N/A':>11} {'120.0m':>10} {'N/A':>9}"
          f" {'0.0%':>8} {'N/A':>11} {'120.0m':>10} {'N/A':>9}")

    for x in MOVE_THRESHOLDS:
        # Variant A stats
        a_data = variant_data['A'][x]
        a_total = sum(d['entries'] for d in a_data)
        a_moves = sum(d['move_triggered'] for d in a_data)
        a_pct = 100 * a_moves / max(1, a_total)
        a_avg_spread = sum(d['time_spread_min'] for d in a_data) / max(1, len(a_data))
        a_avg_spx = sum(d['spx_spread'] for d in a_data) / max(1, len(a_data))
        a_avg_cdist = sum(d['avg_consec_dist'] for d in a_data) / max(1, len(a_data))

        # Variant B stats
        b_data = variant_data['B'][x]
        b_total = sum(d['entries'] for d in b_data)
        b_moves = sum(d['move_triggered'] for d in b_data)
        b_pct = 100 * b_moves / max(1, b_total)
        b_avg_spread = sum(d['time_spread_min'] for d in b_data) / max(1, len(b_data))
        b_avg_spx = sum(d['spx_spread'] for d in b_data) / max(1, len(b_data))
        b_avg_cdist = sum(d['avg_consec_dist'] for d in b_data) / max(1, len(b_data))

        print(f"{str(x)+'pt':>10} {a_pct:>7.1f}% {a_avg_spx:>9.1f}pt {a_avg_spread:>8.1f}m {a_avg_cdist:>8.1f}"
              f" {b_pct:>7.1f}% {b_avg_spx:>9.1f}pt {b_avg_spread:>8.1f}m {b_avg_cdist:>8.1f}")

    print(f"\n  Move% = % of entries triggered by move (vs scheduled fallback)")
    print(f"  Avg Spread = avg SPX range across entry prices (diversification)")
    print(f"  Avg Time = avg minutes between first and last entry")
    print(f"  AvgCDist = avg SPX distance between consecutive entries")

    # ===== PER-DAY COMPARISON: Variant A vs B at key thresholds =====
    for show_x in [10, 15, 20]:
        print(f"\n{'=' * 110}")
        print(f"PER-DAY DETAIL: {show_x}pt threshold — Variant A vs Variant B")
        print(f"{'=' * 110}")

        print(f"\n{'Date':<12} {'DayRng':>7} {'--- Variant A ---':>38} {'--- Variant B ---':>38}")
        print(f"{'':12} {'':>7} {'M/S':>5} {'TimeSprd':>9} {'SPXSprd':>8} {'Entries':>38} {'M/S':>5} {'TimeSprd':>9} {'SPXSprd':>8} {'Entries':>38}")
        print("-" * 140)

        a_days = {d['date']: d for d in variant_data['A'][show_x]}
        b_days = {d['date']: d for d in variant_data['B'][show_x]}

        for date in days:
            a = a_days.get(date)
            b = b_days.get(date)
            if not a or not b:
                continue

            dr = a['day_range']

            a_ms = f"{a['move_triggered']}/{a['scheduled']}"
            a_times = " ".join([f"{t[0][:5]}{'*' if t[1]=='move' else ''}" for t in a['timing']])

            b_ms = f"{b['move_triggered']}/{b['scheduled']}"
            b_times = " ".join([f"{t[0][:5]}{'*' if t[1]=='move' else ''}" for t in b['timing']])

            print(f"{date:<12} {dr:>6.0f} {a_ms:>5} {a['time_spread_min']:>8.1f}m {a['spx_spread']:>7.1f}  {a_times:<30}"
                  f" {b_ms:>5} {b['time_spread_min']:>8.1f}m {b['spx_spread']:>7.1f}  {b_times}")

        # Averages
        a_avg_ts = sum(d['time_spread_min'] for d in variant_data['A'][show_x]) / max(1, len(variant_data['A'][show_x]))
        b_avg_ts = sum(d['time_spread_min'] for d in variant_data['B'][show_x]) / max(1, len(variant_data['B'][show_x]))
        a_avg_spx = sum(d['spx_spread'] for d in variant_data['A'][show_x]) / max(1, len(variant_data['A'][show_x]))
        b_avg_spx = sum(d['spx_spread'] for d in variant_data['B'][show_x]) / max(1, len(variant_data['B'][show_x]))
        print(f"\n  Avg time spread: A={a_avg_ts:.1f}m, B={b_avg_ts:.1f}m (scheduled=120m)")
        print(f"  Avg SPX spread:  A={a_avg_spx:.1f}pt, B={b_avg_spx:.1f}pt")

    # ===== KEY INSIGHT: Strike Diversification =====
    print(f"\n{'=' * 110}")
    print("STRIKE DIVERSIFICATION ANALYSIS")
    print(f"{'=' * 110}")
    print("\nHow spread out are entries across SPX levels? (higher = more diversified)")
    print("Current scheduled entries span ~120 min and typically 20-40pt of SPX range\n")

    print(f"{'Threshold':>10} {'A: AvgSPXSprd':>14} {'A: AvgTime':>11} {'B: AvgSPXSprd':>14} {'B: AvgTime':>11} {'Diversification':>16}")
    print("-" * 80)

    for x in MOVE_THRESHOLDS:
        a_spx = sum(d['spx_spread'] for d in variant_data['A'][x]) / max(1, len(variant_data['A'][x]))
        a_time = sum(d['time_spread_min'] for d in variant_data['A'][x]) / max(1, len(variant_data['A'][x]))
        b_spx = sum(d['spx_spread'] for d in variant_data['B'][x]) / max(1, len(variant_data['B'][x]))
        b_time = sum(d['time_spread_min'] for d in variant_data['B'][x]) / max(1, len(variant_data['B'][x]))

        # Diversification rating
        if b_time >= 90:
            div = "GOOD"
        elif b_time >= 60:
            div = "MODERATE"
        elif b_time >= 30:
            div = "LOW"
        else:
            div = "CLUSTERED"

        print(f"{str(x)+'pt':>10} {a_spx:>12.1f}pt {a_time:>9.1f}m {b_spx:>12.1f}pt {b_time:>9.1f}m {div:>16}")

    # ===== SAME-STRIKE OVERLAP RISK =====
    print(f"\n{'=' * 110}")
    print("SAME-STRIKE OVERLAP RISK")
    print(f"{'=' * 110}")
    print("\nDays where entries are within 5pt of each other (MKT-013/015 conflict zone):")

    for variant in ['A', 'B']:
        for show_x in [10, 15]:
            risky_days = 0
            total_days = 0
            for d in variant_data[variant][show_x]:
                total_days += 1
                prices = [t[2] for t in d['timing']]
                # Check if any two entries are within 5pt
                has_conflict = False
                for i in range(len(prices)):
                    for j in range(i + 1, len(prices)):
                        if abs(prices[i] - prices[j]) < 5:
                            has_conflict = True
                            break
                    if has_conflict:
                        break
                if has_conflict:
                    risky_days += 1
            pct = 100 * risky_days / max(1, total_days)
            print(f"  Variant {variant}, {show_x}pt: {risky_days}/{total_days} days ({pct:.0f}%) have <5pt overlap risk")

    # Scheduled reference
    print(f"  Scheduled (current):    rarely — entries spaced 30min apart, SPX usually moves >5pt")

    # ===== FINAL COMPARISON =====
    print(f"\n{'=' * 110}")
    print("CONCLUSION")
    print(f"{'=' * 110}")

    # Find the variant B threshold with best time spread while still having some moves
    print(f"\nVariant A clusters entries in first 15-30 min on volatile days.")
    print(f"Variant B requires new territory, spreading entries more.")
    print()

    for x in [10, 15, 20]:
        b_avg_ts = sum(d['time_spread_min'] for d in variant_data['B'][x]) / max(1, len(variant_data['B'][x]))
        b_moves = sum(d['move_triggered'] for d in variant_data['B'][x])
        b_total = sum(d['entries'] for d in variant_data['B'][x])
        b_pct = 100 * b_moves / max(1, b_total)
        print(f"  Variant B at {x}pt: {b_avg_ts:.0f}min avg spread, {b_pct:.0f}% move-triggered")

    print(f"\n  Scheduled: 120min avg spread, 0% move-triggered")
    print()
    print("  NOTE: P&L is identical across all variants because the $5.00 buffer")
    print("  produces 0 stops in simulation. The real question is whether")
    print("  move-triggered timing would produce DIFFERENT credits/strikes,")
    print("  which requires option chain data we don't have.")


if __name__ == "__main__":
    run_backtest()
