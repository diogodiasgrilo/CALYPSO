#!/usr/bin/env python3
"""EMA 20/40 trend signal backtest on Saxo-only data, same window as buffer study.

Tests the hypothesis: should the EMA 20/40 trend signal — which is currently
'informational only' — drive entry-type selection? On BULLISH days convert
to put-only (skip call side); on BEARISH days convert to call-only.

Uses the NEW per-VIX-regime buffer values (Option B, just deployed) as baseline,
since that's what the bot will actually run going forward.

Period: 2026-03-16 -> 2026-04-24 (28 trading days, 74 entries)
Methodology: Saxo replay using spread_snapshots (same as buffer study)
"""
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
import statistics

DB = "/opt/calypso/data/backtesting.db"
START = "2026-03-16"
END = "2026-04-24"

# Option B buffer values (just deployed)
def buffer_for_vix(vix, side):
    """Return buffer in dollars for given VIX and side, matching deployed config."""
    if vix is None:
        # Default to global
        return 75.0 if side == "call" else 175.0
    if vix < 18:    # Zone 0 — null, fall back to global
        return 75.0 if side == "call" else 175.0
    if vix < 22:    # Zone 1
        return 150.0 if side == "call" else 250.0
    if vix < 28:    # Zone 2
        return 100.0 if side == "call" else 150.0
    return 75.0 if side == "call" else 175.0  # Zone 3 — null, global

DECAY_START_MULT = 2.5
DECAY_HOURS = 4.0

# EMA thresholds to test (% divergence between EMA20 and EMA40)
THRESHOLDS = [0.10, 0.15, 0.20, 0.30, 0.40, 0.50]


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


def compute_ema(values, period):
    """Compute EMA for a sequence of values; returns final EMA."""
    if not values or len(values) < period:
        return None
    alpha = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# ── Load entries
cur.execute("""
    SELECT date, entry_number, entry_time, entry_type, total_credit,
           call_credit, put_credit, vix_at_entry, trend_signal
    FROM trade_entries
    WHERE date BETWEEN ? AND ? AND entry_type != 'skipped'
    ORDER BY date, entry_number
""", (START, END))
entries = [dict(r) for r in cur.fetchall()]

# ── Load stops for slippage estimation
cur.execute("""
    SELECT side, actual_debit, trigger_level
    FROM trade_stops
    WHERE date BETWEEN ? AND ?
""", (START, END))
stops = cur.fetchall()
call_slip_d = statistics.median([(s['actual_debit'] - s['trigger_level']) for s in stops
                                  if s['side'] == 'call' and s['actual_debit'] and s['trigger_level']])
put_slip_d = statistics.median([(s['actual_debit'] - s['trigger_level']) for s in stops
                                 if s['side'] == 'put' and s['actual_debit'] and s['trigger_level']])

# ── Load spread snapshots
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

# ── Load market_ohlc_1min for EMA computation
cur.execute("""
    SELECT timestamp, close FROM market_ohlc_1min
    WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
    ORDER BY timestamp
""", (START, END))
all_bars = cur.fetchall()
# Index by date for fast lookup
bars_by_date = defaultdict(list)
for r in all_bars:
    bars_by_date[r['timestamp'][:10]].append({'ts': r['timestamp'], 'close': r['close']})

print("=" * 90)
print(f"EMA 20/40 TREND SIGNAL BACKTEST — Saxo data, {START} -> {END}")
print(f"Baseline: Option B per-VIX-regime buffers (just deployed)")
print(f"Median slippage: call=+${call_slip_d:.0f}, put=+${put_slip_d:.0f}")
print(f"Total entries: {len(entries)}, snapshot keys: {len(snaps_by_entry)}, "
      f"market bar dates: {len(bars_by_date)}")
print("=" * 90)


# ── Compute EMA divergence per entry
def ema_at_entry(entry):
    """Compute EMA20 and EMA40 of SPX 1-min closes BEFORE entry_time."""
    ts = parse_dt(entry['date'], entry['entry_time'])
    if not ts:
        return None
    bars = bars_by_date.get(entry['date'], [])
    if not bars:
        return None
    closes = []
    for b in bars:
        bts = parse_snap_ts(b['ts'])
        if bts and bts < ts:
            closes.append(b['close'])
    if len(closes) < 40:
        return None
    e20 = compute_ema(closes, 20)
    e40 = compute_ema(closes, 40)
    if e20 is None or e40 is None or e40 == 0:
        return None
    div_pct = (e20 - e40) / e40 * 100
    return {'ema20': e20, 'ema40': e40, 'divergence_pct': div_pct}


print("\nComputing EMA at each entry...")
n_with_ema = 0
ema_data = {}
for e in entries:
    res = ema_at_entry(e)
    if res:
        ema_data[(e['date'], e['entry_number'])] = res
        n_with_ema += 1
print(f"  Computed EMA for {n_with_ema}/{len(entries)} entries")

# Distribution of divergences
divergences = [d['divergence_pct'] for d in ema_data.values()]
divergences.sort()
print(f"\nEMA divergence distribution (% from EMA40):")
print(f"  min={min(divergences):+.3f}%, p25={divergences[len(divergences)//4]:+.3f}%, "
      f"median={statistics.median(divergences):+.3f}%, "
      f"p75={divergences[3*len(divergences)//4]:+.3f}%, max={max(divergences):+.3f}%")
print(f"  abs median: {statistics.median([abs(d) for d in divergences]):.3f}%, "
      f"abs p90: {sorted([abs(d) for d in divergences])[int(0.9*len(divergences))]:.3f}%")


# ── Replay engine: compute P&L of one entry under arbitrary entry-type
def replay_entry(entry, forced_type):
    """Replay one entry as forced_type ('full_ic', 'call_only', 'put_only').
    Uses Option B per-VIX buffers. Returns net P&L."""
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
    vix = entry['vix_at_entry']
    cb = buffer_for_vix(vix, 'call')
    pb = buffer_for_vix(vix, 'put')

    # Original entry might be call_only or put_only — can't flip if base credit is missing
    if forced_type == 'full_ic' and (cc <= 0 or pc <= 0):
        return None  # can't synthesize a full IC from a one-sided entry
    if forced_type == 'call_only' and cc <= 0:
        return None  # no call side to use
    if forced_type == 'put_only' and pc <= 0:
        return None

    cs = ps = False
    csv_v = psv_v = None

    for snap in snaps:
        ts = parse_snap_ts(snap['ts'])
        if ts is None:
            continue
        eh = (ts - entry_dt).total_seconds() / 3600.0
        df = 1.0 + (DECAY_START_MULT - 1.0) * max(0.0, 1.0 - eh / DECAY_HOURS)

        if forced_type in ('full_ic', 'call_only') and not cs and cc > 0:
            cstop = (tc + cb * df) if forced_type == 'full_ic' else (cc + 260.0 + cb * df)
            cv = snap['call_sv']
            if cv is not None and cv >= cstop:
                cs = True
                csv_v = cv
        if forced_type in ('full_ic', 'put_only') and not ps and pc > 0:
            pstop = (tc + pb * df) if forced_type == 'full_ic' else (pc + pb * df)
            pv = snap['put_sv']
            if pv is not None and pv >= pstop:
                ps = True
                psv_v = pv
        if cs and ps:
            break

    com = 5.0
    if forced_type == 'full_ic':
        if cs or ps:
            sv, slip = (csv_v, call_slip_d) if cs else (psv_v, put_slip_d)
            return tc - (sv + slip) - com * 2
        return tc - com * 2
    elif forced_type == 'call_only':
        if cs:
            return cc - (csv_v + call_slip_d) - com
        return cc - com
    else:  # put_only
        if ps:
            return pc - (psv_v + put_slip_d) - com
        return pc - com


# ── Baseline: use original entry types (no flipping)
baseline_pnl = 0
n_baseline = 0
for e in entries:
    pnl = replay_entry(e, e['entry_type'])
    if pnl is None:
        continue
    baseline_pnl += pnl
    n_baseline += 1
print(f"\nBaseline (no EMA flipping, Option B buffers): ${baseline_pnl:+,.0f} over {n_baseline} entries")


# ── Sweep thresholds
print()
print("=" * 90)
print("SWEEP — convert full_ic to one-sided when EMA |divergence| >= threshold")
print("=" * 90)
print(f"\n{'Thresh':>7} | {'Bullish':>7} | {'Bearish':>7} | {'PnL bull-flip':>14} | {'PnL bear-flip':>14} | {'PnL both':>10}")
print(f"{'(%)':>7} | {'count':>7} | {'count':>7} | {'(skip call)':>14} | {'(skip put)':>14} | {'flip':>10}")
print("-" * 80)

best_results = {'bull': None, 'bear': None, 'both': None}

for thresh in THRESHOLDS:
    n_bull = 0
    n_bear = 0
    pnl_bull_flip = 0  # only flip on BULLISH classifications
    pnl_bear_flip = 0  # only flip on BEARISH
    pnl_both_flip = 0  # flip on both
    n_replayed = 0

    for e in entries:
        key = (e['date'], e['entry_number'])
        ema = ema_data.get(key)
        if ema is None:
            # Use baseline — no signal
            base = replay_entry(e, e['entry_type'])
            if base is not None:
                pnl_bull_flip += base
                pnl_bear_flip += base
                pnl_both_flip += base
                n_replayed += 1
            continue
        div = ema['divergence_pct']
        is_bull = div > thresh
        is_bear = div < -thresh
        if is_bull:
            n_bull += 1
        if is_bear:
            n_bear += 1

        # Only consider full_ic entries for flipping (one-sided entries have no other side to skip)
        if e['entry_type'] != 'full_ic':
            base = replay_entry(e, e['entry_type'])
            if base is None:
                continue
            pnl_bull_flip += base
            pnl_bear_flip += base
            pnl_both_flip += base
            n_replayed += 1
            continue

        # Bull-flip scenario: BULLISH → put-only, else keep full_ic
        if is_bull:
            r = replay_entry(e, 'put_only')
            pnl_bull_flip += r if r is not None else 0
        else:
            r = replay_entry(e, 'full_ic')
            pnl_bull_flip += r if r is not None else 0

        # Bear-flip scenario: BEARISH → call-only, else keep full_ic
        if is_bear:
            r = replay_entry(e, 'call_only')
            pnl_bear_flip += r if r is not None else 0
        else:
            r = replay_entry(e, 'full_ic')
            pnl_bear_flip += r if r is not None else 0

        # Both-flip scenario
        if is_bull:
            r = replay_entry(e, 'put_only')
        elif is_bear:
            r = replay_entry(e, 'call_only')
        else:
            r = replay_entry(e, 'full_ic')
        pnl_both_flip += r if r is not None else 0
        n_replayed += 1

    print(f"  {thresh:>5.2f}% | {n_bull:>7} | {n_bear:>7} | "
          f"${pnl_bull_flip:>+10,.0f} | ${pnl_bear_flip:>+10,.0f} | ${pnl_both_flip:>+8,.0f}")

    # Track best
    for label, pnl in [('bull', pnl_bull_flip), ('bear', pnl_bear_flip), ('both', pnl_both_flip)]:
        if best_results[label] is None or pnl > best_results[label]['pnl']:
            best_results[label] = {'thresh': thresh, 'pnl': pnl, 'n_bull': n_bull, 'n_bear': n_bear}

print()
print(f"Baseline (no flipping):            ${baseline_pnl:+,.0f}")
for label, r in best_results.items():
    delta = r['pnl'] - baseline_pnl
    print(f"Best {label}-flip:  thresh={r['thresh']:.2f}%, P&L=${r['pnl']:+,.0f}, "
          f"Δ vs baseline=${delta:+,.0f}")

conn.close()
print()
print("=" * 90)
print("DONE")
print("=" * 90)
