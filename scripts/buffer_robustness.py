#!/usr/bin/env python3
"""Robustness check: split 28-day window in half, re-run sweep on each.

If the best config is stable across the two halves, the recommendation is
robust. If best diverges across halves, the result is fragile and we should
be more conservative.
"""
import sqlite3
from datetime import datetime
from collections import defaultdict
import statistics

DB = "/opt/calypso/data/backtesting.db"

# Two halves of the 28-day window
HALF1 = ("2026-03-16", "2026-04-03")  # ~14 days
HALF2 = ("2026-04-06", "2026-04-24")  # ~14 days  (skip Apr 04 weekend hole)
FULL  = ("2026-03-16", "2026-04-24")


def parse_dt(date_str, time_str):
    if not time_str:
        return None
    s = time_str.strip()
    if 'AM' in s or 'PM' in s:
        clean = s.replace(' ET', '').strip()
        try:
            return datetime.strptime(f"{date_str} {clean}", "%Y-%m-%d %I:%M:%S %p")
        except ValueError:
            return None
    if '-' in s and ' ' in s:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    try:
        return datetime.strptime(f"{date_str} {s}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_snap_ts(s):
    if not s:
        return None
    try:
        if '.' in s:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Load all data once
cur.execute("""
    SELECT date, entry_number, entry_time, entry_type, total_credit,
           call_credit, put_credit
    FROM trade_entries
    WHERE date BETWEEN ? AND ? AND entry_type != 'skipped'
    ORDER BY date, entry_number
""", (FULL[0], FULL[1]))
all_entries = [dict(r) for r in cur.fetchall()]

cur.execute("""
    SELECT date, entry_number, side, actual_debit, trigger_level
    FROM trade_stops
    WHERE date BETWEEN ? AND ?
""", (FULL[0], FULL[1]))
all_stops = [dict(r) for r in cur.fetchall()]

# Compute median slippage from FULL window (more samples = stable estimate)
call_slips = [(s['actual_debit'] - s['trigger_level']) for s in all_stops
              if s['side'] == 'call' and s['actual_debit'] and s['trigger_level']]
put_slips = [(s['actual_debit'] - s['trigger_level']) for s in all_stops
             if s['side'] == 'put' and s['actual_debit'] and s['trigger_level']]
call_slip_d = statistics.median(call_slips) if call_slips else 60
put_slip_d = statistics.median(put_slips) if put_slips else 40

cur.execute("""
    SELECT timestamp, entry_number, call_spread_value, put_spread_value
    FROM spread_snapshots
    WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
    ORDER BY timestamp
""", (FULL[0], FULL[1]))
snaps_by_entry = defaultdict(list)
for r in cur.fetchall():
    key = (r['timestamp'][:10], r['entry_number'])
    snaps_by_entry[key].append({
        'ts': r['timestamp'],
        'call_sv': r['call_spread_value'],
        'put_sv': r['put_spread_value'],
    })

DECAY_START_MULT = 2.5
DECAY_HOURS = 4.0


def replay(entry, call_buf_d, put_buf_d):
    key = (entry['date'], entry['entry_number'])
    snaps = snaps_by_entry.get(key, [])
    if not snaps:
        return None
    entry_dt = parse_dt(entry['date'], entry['entry_time'])
    if not entry_dt:
        return None
    cc = entry['call_credit'] or 0
    pc = entry['put_credit'] or 0
    tc = entry['total_credit'] or 0
    et = entry['entry_type']

    call_stopped = False
    put_stopped = False
    csv_at_stop = None
    psv_at_stop = None
    cst, pst = None, None

    for snap in snaps:
        ts = parse_snap_ts(snap['ts'])
        if ts is None:
            continue
        elapsed_h = (ts - entry_dt).total_seconds() / 3600.0
        df = 1.0 + (DECAY_START_MULT - 1.0) * max(0.0, 1.0 - elapsed_h/DECAY_HOURS)

        if et in ('full_ic', 'call_only') and not call_stopped and cc > 0:
            cstop = (tc + call_buf_d * df) if et == 'full_ic' else (cc + 260.0 + call_buf_d * df)
            cv = snap['call_sv']
            if cv is not None and cv >= cstop:
                call_stopped = True
                csv_at_stop = cv
                cst = ts
        if et in ('full_ic', 'put_only') and not put_stopped and pc > 0:
            pstop = (tc + put_buf_d * df) if et == 'full_ic' else (pc + put_buf_d * df)
            pv = snap['put_sv']
            if pv is not None and pv >= pstop:
                put_stopped = True
                psv_at_stop = pv
                pst = ts
        if call_stopped and put_stopped:
            break

    pnl = 0.0
    commission = 5.0
    if et == 'full_ic':
        if call_stopped or put_stopped:
            if call_stopped and put_stopped:
                if cst <= pst:
                    sv, slip = csv_at_stop, call_slip_d
                else:
                    sv, slip = psv_at_stop, put_slip_d
            elif call_stopped:
                sv, slip = csv_at_stop, call_slip_d
            else:
                sv, slip = psv_at_stop, put_slip_d
            pnl = tc - (sv + slip)
        else:
            pnl = tc
        pnl -= commission * 2
    elif et == 'call_only':
        pnl = (cc - (csv_at_stop + call_slip_d)) if call_stopped else cc
        pnl -= commission
    elif et == 'put_only':
        pnl = (pc - (psv_at_stop + put_slip_d)) if put_stopped else pc
        pnl -= commission
    return {'pnl': pnl, 'cs': call_stopped, 'ps': put_stopped}


CALL_BUFFERS = [25, 50, 75, 100, 125, 150]
PUT_BUFFERS = [100, 125, 150, 175, 200, 250, 300]


def sweep_window(start_d, end_d, label):
    win_entries = [e for e in all_entries if start_d <= e['date'] <= end_d]
    win_dates = sorted(set(e['date'] for e in win_entries))
    print(f"\n{'='*90}")
    print(f"{label}: {start_d} → {end_d}")
    print(f"  Trading days: {len(win_dates)}, entries: {len(win_entries)}")
    win_stops = [s for s in all_stops if start_d <= s['date'] <= end_d]
    print(f"  Stops in window: {len(win_stops)} (call={sum(1 for s in win_stops if s['side']=='call')}, put={sum(1 for s in win_stops if s['side']=='put')})")

    grid = {}
    for cb in CALL_BUFFERS:
        for pb in PUT_BUFFERS:
            total = 0.0
            n = 0
            cs_n = 0
            ps_n = 0
            for e in win_entries:
                r = replay(e, cb, pb)
                if r is None:
                    continue
                total += r['pnl']
                n += 1
                if r['cs']:
                    cs_n += 1
                if r['ps']:
                    ps_n += 1
            grid[(cb, pb)] = {'pnl': total, 'n': n, 'cs': cs_n, 'ps': ps_n}

    print(f"\n  P&L matrix:")
    header = "CB / PB"
    print(f"  {header:>8} | " + " | ".join(f"{pb:>5}" for pb in PUT_BUFFERS))
    print("  " + "-" * (10 + 8 * len(PUT_BUFFERS)))
    for cb in CALL_BUFFERS:
        row = f"  {cb:>8} | " + " | ".join(f"{grid[(cb,pb)]['pnl']:>+5.0f}" for pb in PUT_BUFFERS)
        print(row)

    # Find current and best
    cur_key = (75, 175)
    cur_pnl = grid[cur_key]['pnl']
    best = max(grid.items(), key=lambda kv: kv[1]['pnl'])
    print(f"\n  Current ($75/$175): ${cur_pnl:+.0f}")
    print(f"  Best ({best[0][0]}/{best[0][1]}): ${best[1]['pnl']:+.0f} (Δ ${best[1]['pnl']-cur_pnl:+.0f})")

    # Top-5 configs
    top5 = sorted(grid.items(), key=lambda kv: -kv[1]['pnl'])[:5]
    print(f"  Top-5 configs:")
    for (cb, pb), v in top5:
        print(f"    CB={cb}, PB={pb}: ${v['pnl']:+.0f} ({v['cs']+v['ps']} stops)")

    return grid, best, cur_pnl


print("=" * 90)
print("ROBUSTNESS CHECK — split 28-day window in halves, re-sweep each")
print("=" * 90)
print(f"\nMedian slippage (computed on FULL window): call=+${call_slip_d:.0f}, put=+${put_slip_d:.0f}")

g_full, b_full, c_full = sweep_window(*FULL,  "FULL WINDOW")
g_h1,   b_h1,   c_h1   = sweep_window(*HALF1, "HALF 1")
g_h2,   b_h2,   c_h2   = sweep_window(*HALF2, "HALF 2")

# ── Compare halves
print()
print("=" * 90)
print("STABILITY ANALYSIS")
print("=" * 90)
print(f"\nBest config per window:")
print(f"  Full (28d): CB={b_full[0][0]}, PB={b_full[0][1]}, Δ ${b_full[1]['pnl']-c_full:+.0f}")
print(f"  Half 1 (~14d): CB={b_h1[0][0]}, PB={b_h1[0][1]}, Δ ${b_h1[1]['pnl']-c_h1:+.0f}")
print(f"  Half 2 (~14d): CB={b_h2[0][0]}, PB={b_h2[0][1]}, Δ ${b_h2[1]['pnl']-c_h2:+.0f}")

# Overlap: top-5 of each half
top5_h1 = set(k for k, v in sorted(g_h1.items(), key=lambda kv: -kv[1]['pnl'])[:5])
top5_h2 = set(k for k, v in sorted(g_h2.items(), key=lambda kv: -kv[1]['pnl'])[:5])
overlap = top5_h1 & top5_h2
print(f"\nTop-5 overlap (configs in BOTH halves' top 5): {len(overlap)} of 5")
if overlap:
    for cb, pb in sorted(overlap):
        print(f"  CB={cb}, PB={pb}: H1=${g_h1[(cb,pb)]['pnl']:+.0f}  H2=${g_h2[(cb,pb)]['pnl']:+.0f}")

# Direction agreement: does each half agree current is suboptimal?
def direction(grid):
    cur = grid[(75, 175)]['pnl']
    best = max(grid.values(), key=lambda v: v['pnl'])['pnl']
    return best - cur

print(f"\nImprovement signal (best - current):")
print(f"  Full: ${direction(g_full):+.0f}")
print(f"  Half 1: ${direction(g_h1):+.0f}")
print(f"  Half 2: ${direction(g_h2):+.0f}")

# Show sweep result at conservative pick (100, 200) across windows
conservative = (100, 200)
moderate = (125, 225)
aggressive = (150, 250)
print(f"\nCandidate configs across windows:")
for name, key in [("Current", (75,175)), ("Conservative", conservative),
                  ("Moderate", moderate), ("Aggressive", aggressive)]:
    if key not in g_full:
        continue
    print(f"  {name} (CB={key[0]}, PB={key[1]}): "
          f"Full=${g_full[key]['pnl']:+.0f}, H1=${g_h1[key]['pnl']:+.0f}, H2=${g_h2[key]['pnl']:+.0f}")

conn.close()
print("\n" + "=" * 90)
print("DONE")
print("=" * 90)
