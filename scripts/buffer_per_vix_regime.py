#!/usr/bin/env python3
"""Per-VIX-regime buffer analysis — does optimal buffer differ by VIX zone?

Buckets the 28-day window's entries by VIX zone (breakpoints [18, 22, 28])
and runs the buffer sweep separately per zone. If the optimal call/put buffer
differs systematically by zone, populating vix_regime.call_stop_buffer /
vix_regime.put_stop_buffer (which already exists in the strategy) is justified.

If the optima are similar across zones, just bump the global value.
"""
import sqlite3
from datetime import datetime
from collections import defaultdict
import statistics

DB = "/opt/calypso/data/backtesting.db"
START = "2026-03-16"
END = "2026-04-24"
BREAKPOINTS = [18.0, 22.0, 28.0]
ZONE_LABELS = ["Zone 0 (VIX<18)", "Zone 1 (18-22)", "Zone 2 (22-28)", "Zone 3 (VIX>=28)"]


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


def vix_zone(vix):
    if vix is None:
        return None
    for i, bp in enumerate(BREAKPOINTS):
        if vix < bp:
            return i
    return len(BREAKPOINTS)


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT date, entry_number, entry_time, entry_type, total_credit,
           call_credit, put_credit, vix_at_entry
    FROM trade_entries
    WHERE date BETWEEN ? AND ? AND entry_type != 'skipped'
    ORDER BY date, entry_number
""", (START, END))
all_entries = [dict(r) for r in cur.fetchall()]

cur.execute("""
    SELECT date, entry_number, side, actual_debit, trigger_level
    FROM trade_stops
    WHERE date BETWEEN ? AND ?
""", (START, END))
all_stops = [dict(r) for r in cur.fetchall()]

call_slip_d = statistics.median([(s['actual_debit'] - s['trigger_level']) for s in all_stops
                                  if s['side'] == 'call' and s['actual_debit'] and s['trigger_level']])
put_slip_d = statistics.median([(s['actual_debit'] - s['trigger_level']) for s in all_stops
                                 if s['side'] == 'put' and s['actual_debit'] and s['trigger_level']])

cur.execute("""
    SELECT timestamp, entry_number, call_spread_value, put_spread_value
    FROM spread_snapshots
    WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
    ORDER BY timestamp
""", (START, END))
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

    cs = ps = False
    csv_v = psv_v = None
    cst = pst = None

    for snap in snaps:
        ts = parse_snap_ts(snap['ts'])
        if ts is None:
            continue
        eh = (ts - entry_dt).total_seconds() / 3600.0
        df = 1.0 + (DECAY_START_MULT - 1.0) * max(0.0, 1.0 - eh/DECAY_HOURS)

        if et in ('full_ic', 'call_only') and not cs and cc > 0:
            cstop = (tc + call_buf_d * df) if et == 'full_ic' else (cc + 260.0 + call_buf_d * df)
            cv = snap['call_sv']
            if cv is not None and cv >= cstop:
                cs = True
                csv_v = cv
                cst = ts
        if et in ('full_ic', 'put_only') and not ps and pc > 0:
            pstop = (tc + put_buf_d * df) if et == 'full_ic' else (pc + put_buf_d * df)
            pv = snap['put_sv']
            if pv is not None and pv >= pstop:
                ps = True
                psv_v = pv
                pst = ts
        if cs and ps:
            break

    pnl = 0.0
    com = 5.0
    if et == 'full_ic':
        if cs or ps:
            if cs and ps:
                if cst <= pst:
                    sv, sl = csv_v, call_slip_d
                else:
                    sv, sl = psv_v, put_slip_d
            elif cs:
                sv, sl = csv_v, call_slip_d
            else:
                sv, sl = psv_v, put_slip_d
            pnl = tc - (sv + sl)
        else:
            pnl = tc
        pnl -= com * 2
    elif et == 'call_only':
        pnl = (cc - (csv_v + call_slip_d)) if cs else cc
        pnl -= com
    elif et == 'put_only':
        pnl = (pc - (psv_v + put_slip_d)) if ps else pc
        pnl -= com
    return {'pnl': pnl, 'cs': cs, 'ps': ps}


# ── Bucket entries by VIX zone
by_zone = defaultdict(list)
for e in all_entries:
    z = vix_zone(e['vix_at_entry'])
    if z is None:
        continue
    by_zone[z].append(e)

print("=" * 90)
print("PER-VIX-REGIME BUFFER ANALYSIS")
print("=" * 90)
print(f"\nVIX breakpoints: {BREAKPOINTS}")
print(f"\nEntries by zone:")
for z in sorted(by_zone):
    ent = by_zone[z]
    vixes = [e['vix_at_entry'] for e in ent if e['vix_at_entry']]
    vmin = min(vixes) if vixes else 0
    vmax = max(vixes) if vixes else 0
    n_dates = len(set(e['date'] for e in ent))
    # Count stops in this zone
    zone_dates_entries = set((e['date'], e['entry_number']) for e in ent)
    zone_stops = [s for s in all_stops if (s['date'], s['entry_number']) in zone_dates_entries]
    print(f"  {ZONE_LABELS[z]}: {len(ent)} entries on {n_dates} days, "
          f"VIX range {vmin:.1f}-{vmax:.1f}, {len(zone_stops)} stops")

# ── Sweep per zone (only zones with sufficient data)
CALL_BUFFERS = [50, 75, 100, 125, 150]
PUT_BUFFERS  = [125, 150, 175, 200, 250]

for z in sorted(by_zone):
    ent = by_zone[z]
    if len(ent) < 10:
        print(f"\n--- {ZONE_LABELS[z]}: SKIPPING (n={len(ent)}, too small) ---")
        continue
    print(f"\n{'='*90}")
    print(f"{ZONE_LABELS[z]}: n={len(ent)} entries")
    print(f"{'='*90}")

    grid = {}
    for cb in CALL_BUFFERS:
        for pb in PUT_BUFFERS:
            total = 0
            n = 0
            cs_n = ps_n = 0
            for e in ent:
                r = replay(e, cb, pb)
                if r is None:
                    continue
                total += r['pnl']
                n += 1
                if r['cs']:
                    cs_n += 1
                if r['ps']:
                    ps_n += 1
            grid[(cb, pb)] = {'pnl': total, 'cs': cs_n, 'ps': ps_n}

    print(f"\n  P&L matrix:")
    print(f"  {'CB / PB':>8} | " + " | ".join(f"{pb:>5}" for pb in PUT_BUFFERS))
    print("  " + "-" * 58)
    for cb in CALL_BUFFERS:
        row = f"  {cb:>8} | " + " | ".join(f"{grid[(cb,pb)]['pnl']:>+5.0f}" for pb in PUT_BUFFERS)
        print(row)

    cur_pnl = grid.get((75, 175), {}).get('pnl', None)
    best = max(grid.items(), key=lambda kv: kv[1]['pnl'])
    print(f"\n  Current ($75/$175): ${cur_pnl:+.0f}" if cur_pnl is not None else "")
    print(f"  Best: CB=${best[0][0]}, PB=${best[0][1]}, P&L=${best[1]['pnl']:+.0f}")
    if cur_pnl is not None:
        print(f"  Δ vs current: ${best[1]['pnl'] - cur_pnl:+.0f}")

    # What about 100/175 (call-only widening)?
    if (100, 175) in grid:
        delta_100_175 = grid[(100,175)]['pnl'] - (cur_pnl or 0)
        delta_125_175 = grid[(125,175)]['pnl'] - (cur_pnl or 0)
        print(f"  CB=$1.00 only: ${grid[(100,175)]['pnl']:+.0f} (Δ ${delta_100_175:+.0f})")
        print(f"  CB=$1.25 only: ${grid[(125,175)]['pnl']:+.0f} (Δ ${delta_125_175:+.0f})")

conn.close()
print("\n" + "=" * 90)
print("DONE")
print("=" * 90)
