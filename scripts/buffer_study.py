#!/usr/bin/env python3
"""Deep study: HYDRA stop buffer optimization using Saxo-only VM data.

Methodology (Option C): descriptive analysis of stop timing + spread dynamics
where the data is too small for direct optimization, plus a robust 2D sweep
on (call_stop_buffer, put_stop_buffer) where 28 days IS enough.

Period: 2026-03-16 -> 2026-04-24 (28 trading days)
"""
import sqlite3
from datetime import datetime
from collections import defaultdict
import statistics


def parse_dt(date_str, time_str):
    """Parse entry_time which can be 'HH:MM:SS AM ET' or 'YYYY-MM-DD HH:MM:SS'."""
    if not time_str:
        return None
    s = time_str.strip()
    # Format A: '10:16:59 AM ET'
    if 'AM' in s or 'PM' in s:
        clean = s.replace(' ET', '').strip()
        try:
            return datetime.strptime(f"{date_str} {clean}", "%Y-%m-%d %I:%M:%S %p")
        except ValueError:
            return None
    # Format B: 'YYYY-MM-DD HH:MM:SS'
    if '-' in s and ' ' in s:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    # Format C: 'HH:MM:SS' (24h)
    try:
        return datetime.strptime(f"{date_str} {s}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_snap_ts(s):
    """Parse spread_snapshot timestamp 'YYYY-MM-DD HH:MM:SS' or with microseconds."""
    if not s:
        return None
    try:
        if '.' in s:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

DB = "/opt/calypso/data/backtesting.db"
START_DATE = "2026-03-16"
END_DATE = "2026-04-24"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 90)
print(f"BUFFER STUDY  {START_DATE} -> {END_DATE}")
print("=" * 90)

# ── Load entries
cur.execute("""
    SELECT date, entry_number, entry_time, entry_type, total_credit,
           call_credit, put_credit, contracts
    FROM trade_entries
    WHERE date BETWEEN ? AND ? AND entry_type != 'skipped'
    ORDER BY date, entry_number
""", (START_DATE, END_DATE))
entries = [dict(r) for r in cur.fetchall()]

cur.execute("""
    SELECT date, entry_number, side, stop_time, trigger_level, actual_debit,
           net_pnl, slippage_on_close, minutes_held, spx_move_since_entry
    FROM trade_stops
    WHERE date BETWEEN ? AND ?
    ORDER BY date, entry_number, side
""", (START_DATE, END_DATE))
stops = [dict(r) for r in cur.fetchall()]

print(f"\nLoaded {len(entries)} entries, {len(stops)} stops")
type_counts = defaultdict(int)
for e in entries:
    type_counts[e['entry_type']] += 1
print(f"By type: {dict(type_counts)}")
n_call = sum(1 for s in stops if s['side'] == 'call')
n_put = sum(1 for s in stops if s['side'] == 'put')
print(f"Stops: call={n_call}, put={n_put}")

# ── PHASE 1 — DESCRIPTIVE
print()
print("=" * 90)
print("PHASE 1  STOP TIMING + SLIPPAGE BY SIDE")
print("=" * 90)

for side in ('call', 'put'):
    ss = [s for s in stops if s['side'] == side]
    if not ss:
        continue
    print(f"\n-- {side.upper()} STOPS (n={len(ss)}) --")
    holds = [s['minutes_held'] for s in ss if s['minutes_held'] is not None]
    if holds:
        q = statistics.quantiles(holds, n=4) if len(holds) >= 4 else [statistics.median(holds)]*3
        print(f"  Hold time (min): min={min(holds):.0f} p25={q[0]:.0f} med={statistics.median(holds):.0f} p75={q[2] if len(q)>=3 else q[0]:.0f} max={max(holds):.0f}")
    slips_d = [(s['actual_debit'] - s['trigger_level']) for s in ss
               if s['actual_debit'] and s['trigger_level']]
    if slips_d:
        print(f"  Slippage $ (debit-trigger): min=${min(slips_d):.0f} med=${statistics.median(slips_d):.0f} max=${max(slips_d):.0f} mean=${statistics.mean(slips_d):.0f}")
    slips_r = [s['actual_debit']/s['trigger_level'] for s in ss
               if s['actual_debit'] and s['trigger_level']]
    if slips_r:
        print(f"  Slippage ratio: min={min(slips_r):.2f}x med={statistics.median(slips_r):.2f}x max={max(slips_r):.2f}x")
    hours = defaultdict(int)
    for s in ss:
        if s['stop_time'] and ':' in s['stop_time'] and len(s['stop_time']) >= 5:
            hours[int(s['stop_time'][:2])] += 1
    print(f"  Stops by hour-of-day: {dict(sorted(hours.items()))}")

# ── PHASE 2 — 2D BUFFER SWEEP via spread_snapshot replay
print()
print("=" * 90)
print("PHASE 2  2D SWEEP (call_buffer x put_buffer), decay shape FIXED at current")
print("=" * 90)

call_slip_d_list = [(s['actual_debit'] - s['trigger_level']) for s in stops
                    if s['side']=='call' and s['actual_debit'] and s['trigger_level']]
put_slip_d_list  = [(s['actual_debit'] - s['trigger_level']) for s in stops
                    if s['side']=='put' and s['actual_debit'] and s['trigger_level']]
call_slip_d = statistics.median(call_slip_d_list) if call_slip_d_list else 100
put_slip_d  = statistics.median(put_slip_d_list)  if put_slip_d_list  else 170
print(f"\nMedian observed slippage (used in counterfactual fills):")
print(f"  Call: +${call_slip_d:.0f}")
print(f"  Put:  +${put_slip_d:.0f}")

print(f"\nLoading spread_snapshots...")
cur.execute("""
    SELECT timestamp, entry_number, call_spread_value, put_spread_value
    FROM spread_snapshots
    WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
    ORDER BY timestamp
""", (START_DATE, END_DATE))
snaps_by_entry = defaultdict(list)
for r in cur.fetchall():
    key = (r['timestamp'][:10], r['entry_number'])
    snaps_by_entry[key].append({
        'ts': r['timestamp'],
        'call_sv': r['call_spread_value'],
        'put_sv': r['put_spread_value'],
    })
print(f"  Loaded snapshots for {len(snaps_by_entry)} (date, entry) keys")

DECAY_START_MULT = 2.5
DECAY_HOURS = 4.0

def replay(entry, call_buf_d, put_buf_d):
    key = (entry['date'], entry['entry_number'])
    snaps = snaps_by_entry.get(key, [])
    if not snaps:
        return None
    entry_dt = parse_dt(entry["date"], entry["entry_time"])
    if not entry_dt:
        return None
    cc = entry['call_credit'] or 0
    pc = entry['put_credit'] or 0
    tc = entry['total_credit'] or 0
    et = entry['entry_type']

    call_stopped = False
    put_stopped  = False
    call_stop_value = None
    put_stop_value  = None
    call_stop_ts = None
    put_stop_ts  = None

    for snap in snaps:
        ts = parse_snap_ts(snap["ts"])
        elapsed_h = (ts - entry_dt).total_seconds() / 3600.0
        decay_factor = 1.0 + (DECAY_START_MULT - 1.0) * max(0.0, 1.0 - elapsed_h/DECAY_HOURS)

        if et in ('full_ic', 'call_only') and not call_stopped and cc > 0:
            if et == 'full_ic':
                cstop = tc + call_buf_d * decay_factor
            else:
                cstop = cc + 260.0 + call_buf_d * decay_factor
            cv = snap['call_sv']
            if cv is not None and cv >= cstop:
                call_stopped = True
                call_stop_value = cv
                call_stop_ts = ts

        if et in ('full_ic', 'put_only') and not put_stopped and pc > 0:
            if et == 'full_ic':
                pstop = tc + put_buf_d * decay_factor
            else:
                pstop = pc + put_buf_d * decay_factor
            pv = snap['put_sv']
            if pv is not None and pv >= pstop:
                put_stopped = True
                put_stop_value = pv
                put_stop_ts = ts

        if call_stopped and put_stopped:
            break

    pnl = 0.0
    commission = 5.0
    if et == 'full_ic':
        if call_stopped or put_stopped:
            # Whichever fired first triggers close-both
            if call_stopped and put_stopped:
                if call_stop_ts <= put_stop_ts:
                    sv, slip = call_stop_value, call_slip_d
                else:
                    sv, slip = put_stop_value, put_slip_d
            elif call_stopped:
                sv, slip = call_stop_value, call_slip_d
            else:
                sv, slip = put_stop_value, put_slip_d
            pnl = tc - (sv + slip)
        else:
            pnl = tc
        pnl -= commission * 2
    elif et == 'call_only':
        pnl = (cc - (call_stop_value + call_slip_d)) if call_stopped else cc
        pnl -= commission
    elif et == 'put_only':
        pnl = (pc - (put_stop_value + put_slip_d)) if put_stopped else pc
        pnl -= commission

    return {
        'pnl': pnl,
        'call_stopped': call_stopped,
        'put_stopped': put_stopped,
    }

CALL_BUFFERS = [25, 50, 75, 100, 125, 150]
PUT_BUFFERS  = [100, 125, 150, 175, 200, 250, 300]
print(f"\nRunning {len(CALL_BUFFERS)}x{len(PUT_BUFFERS)}={len(CALL_BUFFERS)*len(PUT_BUFFERS)} configs over {len(entries)} entries...")

grid = {}
for cb in CALL_BUFFERS:
    for pb in PUT_BUFFERS:
        total_pnl = 0.0
        n_replayed = 0
        n_call_st = 0
        n_put_st  = 0
        for e in entries:
            r = replay(e, cb, pb)
            if r is None:
                continue
            total_pnl += r['pnl']
            n_replayed += 1
            if r['call_stopped']: n_call_st += 1
            if r['put_stopped']:  n_put_st += 1
        grid[(cb, pb)] = {'pnl': total_pnl, 'n': n_replayed,
                          'cs': n_call_st, 'ps': n_put_st}

# ── P&L matrix
print(f"\n{'NET PNL ($)':>12} | " + " | ".join(f"PB={pb:>3}" for pb in PUT_BUFFERS))
print("-" * (15 + 9 * len(PUT_BUFFERS)))
for cb in CALL_BUFFERS:
    row = f"{'CB=' + str(cb):>12} | " + " | ".join(f"{grid[(cb,pb)]['pnl']:>+6.0f}" for pb in PUT_BUFFERS)
    print(row)

# ── Stops fired matrix
print(f"\n{'STOPS FIRED':>12} | " + " | ".join(f"PB={pb:>3}" for pb in PUT_BUFFERS))
print("-" * (15 + 9 * len(PUT_BUFFERS)))
for cb in CALL_BUFFERS:
    row = f"{'CB=' + str(cb):>12} | " + " | ".join(f"{grid[(cb,pb)]['cs']+grid[(cb,pb)]['ps']:>6}" for pb in PUT_BUFFERS)
    print(row)

best = max(grid.items(), key=lambda kv: kv[1]['pnl'])
current_key = (75, 175)
print(f"\nBest: call_buf=${best[0][0]}, put_buf=${best[0][1]}, P&L=${best[1]['pnl']:+.0f}")
if current_key in grid:
    cur_pnl = grid[current_key]['pnl']
    print(f"Current ($75/$175): P&L=${cur_pnl:+.0f}, stops={grid[current_key]['cs']+grid[current_key]['ps']}")
    print(f"Delta vs current: ${best[1]['pnl'] - cur_pnl:+.0f}")
    print(f"Best config stops: {best[1]['cs']+best[1]['ps']}")

# ── PHASE 3 — DECAY SHAPE DESCRIPTIVE
print()
print("=" * 90)
print("PHASE 3  SPREAD VALUE AS % OF STOP-LEVEL, BY HOUR-OF-DAY")
print("=" * 90)

hourly_call = defaultdict(list)
hourly_put  = defaultdict(list)
for e in entries:
    key = (e['date'], e['entry_number'])
    snaps = snaps_by_entry.get(key, [])
    if not snaps:
        continue
    entry_dt = parse_dt(e["date"], e["entry_time"])
    if not entry_dt:
        continue
    tc = e['total_credit'] or 0
    cc = e['call_credit'] or 0
    pc = e['put_credit'] or 0
    et = e['entry_type']
    for snap in snaps:
        ts = parse_snap_ts(snap["ts"])
        h = ts.hour
        elapsed_h = (ts - entry_dt).total_seconds() / 3600.0
        df = 1.0 + (DECAY_START_MULT - 1.0) * max(0.0, 1.0 - elapsed_h/DECAY_HOURS)
        if et == 'full_ic':
            cs = tc + 75 * df
            ps = tc + 175 * df
        elif et == 'call_only':
            cs = cc + 260 + 75 * df
            ps = None
        else:
            ps = pc + 175 * df
            cs = None
        if cs and snap['call_sv']:
            hourly_call[h].append(snap['call_sv'] / cs * 100)
        if ps and snap['put_sv']:
            hourly_put[h].append(snap['put_sv'] / ps * 100)

print(f"\n{'Hour':>5} | {'Call n':>7} | {'Call med%':>9} | {'Call p90%':>9} | {'Put n':>7} | {'Put med%':>9} | {'Put p90%':>9}")
print("-" * 75)
for h in range(10, 17):
    cs = hourly_call.get(h, [])
    ps = hourly_put.get(h, [])
    cmed = statistics.median(cs) if cs else 0
    cp90 = statistics.quantiles(cs, n=10)[8] if len(cs) >= 10 else 0
    pmed = statistics.median(ps) if ps else 0
    pp90 = statistics.quantiles(ps, n=10)[8] if len(ps) >= 10 else 0
    print(f"{h:>5} | {len(cs):>7} | {cmed:>8.0f}% | {cp90:>8.0f}% | "
          f"{len(ps):>7} | {pmed:>8.0f}% | {pp90:>8.0f}%")

print()
print("Stop time-from-entry distribution (informs decay duration):")
for side in ('call', 'put'):
    sh = [s['minutes_held'] for s in stops if s['side'] == side and s['minutes_held'] is not None]
    if not sh:
        continue
    n = len(sh)
    n0_60 = sum(1 for x in sh if x <= 60)
    n60_120 = sum(1 for x in sh if 60 < x <= 120)
    n120_180 = sum(1 for x in sh if 120 < x <= 180)
    n180p = sum(1 for x in sh if x > 180)
    print(f"  {side}: 0-60min={n0_60}/{n} ({100*n0_60/n:.0f}%)  60-120={n60_120} ({100*n60_120/n:.0f}%)  120-180={n120_180} ({100*n120_180/n:.0f}%)  180+={n180p} ({100*n180p/n:.0f}%)")

# Spread vol by elapsed_hour bucket (per side)
print()
print("Spread value volatility (std dev of value/stop_level%) by ELAPSED hour from entry:")
elapsed_call = defaultdict(list)
elapsed_put = defaultdict(list)
for e in entries:
    key = (e['date'], e['entry_number'])
    snaps = snaps_by_entry.get(key, [])
    if not snaps: continue
    entry_dt = parse_dt(e["date"], e["entry_time"])
    if not entry_dt: continue
    tc = e['total_credit'] or 0
    cc = e['call_credit'] or 0
    pc = e['put_credit'] or 0
    et = e['entry_type']
    for snap in snaps:
        ts = parse_snap_ts(snap["ts"])
        eh = (ts - entry_dt).total_seconds() / 3600.0
        bucket = int(eh)  # 0,1,2,3,4,5
        df = 1.0 + (DECAY_START_MULT - 1.0) * max(0.0, 1.0 - eh/DECAY_HOURS)
        if et == 'full_ic':
            cs = tc + 75 * df
            ps = tc + 175 * df
        elif et == 'call_only':
            cs = cc + 260 + 75 * df
            ps = None
        else:
            ps = pc + 175 * df
            cs = None
        if cs and snap['call_sv']:
            elapsed_call[bucket].append(snap['call_sv'] / cs * 100)
        if ps and snap['put_sv']:
            elapsed_put[bucket].append(snap['put_sv'] / ps * 100)

print(f"\n{'Elapsed h':>10} | {'Call n':>7} | {'Call med%':>9} | {'Call p90%':>9} | {'Call std':>9} | {'Put n':>7} | {'Put med%':>9} | {'Put p90%':>9} | {'Put std':>9}")
print("-" * 100)
for h in range(0, 6):
    cs = elapsed_call.get(h, [])
    ps = elapsed_put.get(h, [])
    cmed = statistics.median(cs) if cs else 0
    cp90 = statistics.quantiles(cs, n=10)[8] if len(cs) >= 10 else 0
    cstd = statistics.stdev(cs) if len(cs) >= 2 else 0
    pmed = statistics.median(ps) if ps else 0
    pp90 = statistics.quantiles(ps, n=10)[8] if len(ps) >= 10 else 0
    pstd = statistics.stdev(ps) if len(ps) >= 2 else 0
    print(f"{h:>10} | {len(cs):>7} | {cmed:>8.0f}% | {cp90:>8.0f}% | {cstd:>8.1f} | {len(ps):>7} | {pmed:>8.0f}% | {pp90:>8.0f}% | {pstd:>8.1f}")

conn.close()
print()
print("=" * 90)
print("DONE")
print("=" * 90)
