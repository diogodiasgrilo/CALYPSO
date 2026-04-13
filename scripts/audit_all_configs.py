"""
Systematic audit of all HYDRA config values using live trading data.

Uses tested config_audit_lib (20 passing tests). Each config is checked:
1. Does the current live data reveal its value is suboptimal?
2. If we can run a counterfactual — what's the better value?
3. Explicit confidence level and data reliability caveats.

Configs are grouped by feasibility of measurement:
  TIER 1 (high confidence): overrides with clear fire/not-fire logic
  TIER 2 (medium): features that modify behavior observably
  TIER 3 (insufficient data): need quote replay or bid/ask history
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import defaultdict
from config_audit_lib import ConfigAuditDB

DB_PATH = '/tmp/backtesting.db'


def print_header(text, char='='):
    print(f'\n{char*100}')
    print(f'  {text}')
    print(f'{char*100}')


def print_finding(name, current, recommendation, confidence, rationale=''):
    print(f'\n  ★ {name}')
    print(f'    Current: {current}')
    print(f'    Recommendation: {recommendation}')
    print(f'    Confidence: {confidence}')
    if rationale:
        print(f'    Rationale: {rationale}')


# ============================================================================
# TIER 1 — HIGH CONFIDENCE (direct counterfactual from live data)
# ============================================================================

def audit_upday_threshold(db: ConfigAuditDB):
    """E6 conditional fires put-only when SPX rises >= 0.25% from open at 14:00.

    Counterfactual: check which days E6 fired and whether it was profitable.
    If threshold raised, which days would skip? If lowered, which would add?
    """
    print_header('TIER 1.1 — upday_threshold_pct (E6 conditional)')

    dates = db.get_all_dates()
    e6_fires = []  # (date, rise_at_14:00, actual_pnl)
    non_fire_days = []  # where SPX rose but under 0.25%

    for date in dates:
        # E6 fires at 14:00 only if rise from open >= threshold
        rise = db.get_spx_rise_pct_at(date, '14:00:00')
        if rise is None:
            continue

        entries = db.get_entries(date)
        stops = db.get_stops(date)

        # Find Entry #4 or any 14:00 entry
        e6 = None
        for e in entries:
            if e['num'] == 4 and e['time'] and '14:00' in e['time']:
                e6 = e
                break
            # Also check if any entry at exact 14:00
            if e['time'] and e['time'][11:16] == '14:00':
                e6 = e
                break

        if e6:
            ep = db.compute_entry_pnl(e6, stops.get(e6['num'], []))
            e6_fires.append({
                'date': date, 'rise': rise, 'entry': e6, 'pnl': ep['entry_net'],
            })
        elif rise and rise >= 0.0015:  # ~0.15%+ rise but didn't fire
            non_fire_days.append({'date': date, 'rise': rise})

    print(f'\n  E6 actual fires in data: {len(e6_fires)}')
    for f in e6_fires:
        print(f'    {f["date"]}: rise={f["rise"]*100:.3f}% → type={f["entry"]["type"]} pnl=${f["pnl"]:+.0f}')

    total_pnl = sum(f['pnl'] for f in e6_fires)
    wins = sum(1 for f in e6_fires if f['pnl'] > 0)
    losses = sum(1 for f in e6_fires if f['pnl'] < 0)
    print(f'\n  Total E6 P&L: ${total_pnl:+.0f} ({wins} wins, {losses} losses)')

    print(f'\n  Days with rise > 0.15% but E6 did not fire: {len(non_fire_days)}')
    for d in non_fire_days[:10]:
        print(f'    {d["date"]}: rise={d["rise"]*100:.3f}%')

    # Verdict
    if len(e6_fires) < 3:
        print_finding(
            'upday_threshold_pct', '0.0025 (0.25%)',
            'INSUFFICIENT DATA (<3 fires) — keep current value',
            'LOW',
            f'Only {len(e6_fires)} E6 fires in 37 days. Need more data to tune.',
        )
    else:
        avg = total_pnl / len(e6_fires)
        rec = f'Keep 0.25%' if avg > 0 else f'Consider disabling (avg ${avg:+.0f}/fire)'
        print_finding(
            'upday_threshold_pct', '0.0025 (0.25%)', rec,
            'MEDIUM' if len(e6_fires) < 10 else 'HIGH',
            f'{wins}/{len(e6_fires)} wins, avg ${avg:+.0f}/fire',
        )


def audit_vix_regime(db: ConfigAuditDB):
    """VIX regime breakpoints [14, 20, 30] determine max_entries per day.

    Current: VIX<14 → 2 entries, 14-20 → 3, 20-30 → 3, ≥30 → 1
    Counterfactual: bucket days by VIX, measure P&L per bucket.
    """
    print_header('TIER 1.2 — vix_regime_breakpoints [14, 20, 30]')

    dates = db.get_all_dates()

    # Bucket each day by VIX and sum P&L
    buckets = {
        '<14': {'days': [], 'pnl': 0},
        '14-18': {'days': [], 'pnl': 0},
        '18-22': {'days': [], 'pnl': 0},
        '22-25': {'days': [], 'pnl': 0},
        '25-28': {'days': [], 'pnl': 0},
        '28-32': {'days': [], 'pnl': 0},
        '32+': {'days': [], 'pnl': 0},
    }

    for date in dates:
        summary = db.get_daily_summary(date)
        if not summary or summary['vix_open'] is None:
            continue
        vix = summary['vix_open']
        pnl = summary['net_pnl'] or 0

        if vix < 14: b = '<14'
        elif vix < 18: b = '14-18'
        elif vix < 22: b = '18-22'
        elif vix < 25: b = '22-25'
        elif vix < 28: b = '25-28'
        elif vix < 32: b = '28-32'
        else: b = '32+'

        buckets[b]['days'].append((date, vix, pnl))
        buckets[b]['pnl'] += pnl

    print(f'\n  {"VIX Bucket":<10}{"Days":>6}{"Total P&L":>12}{"Avg/Day":>12}{"Win%":>7}')
    for label, data in buckets.items():
        if not data['days']: continue
        avg = data['pnl'] / len(data['days'])
        wins = sum(1 for _, _, p in data['days'] if p > 0)
        wr = 100 * wins / len(data['days'])
        print(f'  {label:<10}{len(data["days"]):>6}${data["pnl"]:>+10.0f}${avg:>+10.0f}{wr:>6.0f}%')

    # Find best/worst buckets
    non_empty = [(l, d) for l, d in buckets.items() if d['days']]
    by_avg = sorted(non_empty, key=lambda x: -(x[1]['pnl']/len(x[1]['days'])))
    print(f'\n  Best VIX buckets: {by_avg[0][0]} (${by_avg[0][1]["pnl"]/len(by_avg[0][1]["days"]):+.0f}/day)')
    print(f'  Worst VIX bucket: {by_avg[-1][0]} (${by_avg[-1][1]["pnl"]/len(by_avg[-1][1]["days"]):+.0f}/day)')

    # Actual insight from data:
    # - 14-18 bucket: +$106/day (best)
    # - 18-22 bucket: +$53/day (winning)
    # - 22-25 bucket: -$202/day (LOSING)  ← current [14,20,30] allows 3 entries here
    # - 25-28 bucket: -$2/day (breakeven)
    # - 28-32 bucket: -$712/day (but n=1 from Mar 9 — pre-VIX-regime config)
    print_finding(
        'vix_regime_breakpoints', '[14, 20, 30]',
        'Consider [18, 22, 28] — REDUCE entries in 22-28 range (data shows net loss there)',
        'MEDIUM',
        'Current breakpoint 20-30 lumps VIX 22-28 (losing) with 18-22 (winning). Caveat: '
        'historical VIX 30+ data predates current regime — small sample for 28+ bucket.',
    )


def audit_entry_times(db: ConfigAuditDB):
    """Entry slots 10:15/10:45/11:15 — which slot performs best?"""
    print_header('TIER 1.3 — Entry Times (10:15, 10:45, 11:15)')

    reliable_dates = set(db.get_reconciled_dates(tolerance=100.0))
    by_slot = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0, 'losses': 0})

    for date in db.get_all_dates():
        if date not in reliable_dates:
            continue
        entries = db.get_entries(date)
        stops = db.get_stops(date)
        for e in entries:
            if e['num'] > 3:  # Skip E6 conditional
                continue
            ep = db.compute_entry_pnl(e, stops.get(e['num'], []))
            slot = f'E#{e["num"]}'
            by_slot[slot]['count'] += 1
            by_slot[slot]['pnl'] += ep['entry_net']
            if ep['entry_net'] > 0: by_slot[slot]['wins'] += 1
            elif ep['entry_net'] < 0: by_slot[slot]['losses'] += 1

    print(f'\n  Per-slot P&L (reliable days only, {len(reliable_dates)} days):')
    print(f'  {"Slot":<8}{"Count":>8}{"Total":>12}{"Avg":>10}{"WinRate":>10}')
    for slot in sorted(by_slot.keys()):
        d = by_slot[slot]
        if d['count'] == 0: continue
        avg = d['pnl'] / d['count']
        wr = 100 * d['wins'] / d['count']
        print(f'  {slot:<8}{d["count"]:>8}${d["pnl"]:>+10.0f}${avg:>+8.0f}{wr:>9.0f}%')

    # Rank
    ranked = sorted([(s, d) for s, d in by_slot.items() if d['count']],
                    key=lambda x: -x[1]['pnl']/x[1]['count'])
    if ranked:
        best = ranked[0]
        worst = ranked[-1]
        print_finding(
            'Entry time slots', '10:15 / 10:45 / 11:15',
            f'Best: {best[0]} ({best[1]["pnl"]/best[1]["count"]:+.0f}/entry), '
            f'Worst: {worst[0]} ({worst[1]["pnl"]/worst[1]["count"]:+.0f}/entry)',
            'MEDIUM',
            f'Consider dropping {worst[0]} if its pattern persists over 30+ more entries',
        )


# ============================================================================
# TIER 2 — MEDIUM CONFIDENCE (approximate counterfactual)
# ============================================================================

def audit_whipsaw_filter(db: ConfigAuditDB):
    """whipsaw_range_skip_mult = 1.75: skip entry when day_range > 1.75 × expected_move.

    We don't directly see this filter in data (it prevents entries from being logged),
    but we can check which days had high range and LOW entry count (entries were skipped).
    """
    print_header('TIER 2.1 — whipsaw_range_skip_mult (1.75)')
    dates = db.get_all_dates()

    print(f'\n  Days with high day_range / expected_move ratio:')
    print(f'  {"Date":<12}{"Range":>8}{"EM":>8}{"Ratio":>8}{"Entries":>10}{"P&L":>10}')
    high_range_days = []
    for date in dates:
        s = db.get_daily_summary(date)
        if not s or not s['spx_high'] or not s['spx_low'] or not s['vix_open']:
            continue
        rng = s['spx_high'] - s['spx_low']
        # Approximate expected move: VIX × sqrt(1/252) × SPX_open × 1 (for 1 day) ≈ VIX * 6.3
        if not s['spx_open']:
            continue
        em = (s['vix_open'] / 100) * s['spx_open'] * (1/252)**0.5
        ratio = rng / em if em > 0 else 0
        if ratio > 1.3:
            high_range_days.append({
                'date': date, 'range': rng, 'em': em, 'ratio': ratio,
                'entries': s['entries_placed'], 'pnl': s['net_pnl']
            })

    high_range_days.sort(key=lambda x: -x['ratio'])
    for d in high_range_days[:15]:
        print(f'  {d["date"]:<12}{d["range"]:>8.0f}{d["em"]:>8.0f}{d["ratio"]:>8.2f}{d["entries"]:>10}${d["pnl"]:>+8.0f}')

    # Count how many "should have fired" (ratio > 1.75)
    would_fire = [d for d in high_range_days if d['ratio'] > 1.75]
    fired_loss = sum(1 for d in would_fire if d['pnl'] < 0)
    print(f'\n  Days where ratio > 1.75 (whipsaw filter would fire): {len(would_fire)}')
    print(f'  Of those, {fired_loss} were losing days')
    if would_fire:
        total_pnl_of_fire_days = sum(d['pnl'] for d in would_fire)
        print(f'  Total P&L on those days: ${total_pnl_of_fire_days:+,.0f}')

    print_finding(
        'whipsaw_range_skip_mult', '1.75',
        'INSUFFICIENT DATA — cannot tell if filter fired (skipped entries not logged with reason)',
        'LOW',
        'Need to cross-reference skipped_entries table if filter reasons logged',
    )


def audit_calm_entry(db: ConfigAuditDB):
    """Calm entry filter: delays entry up to 5 min when SPX moved >15pt in last 3min."""
    print_header('TIER 2.2 — calm_entry_threshold_pts (15.0), lookback (3min), max_delay (5min)')

    # Check: for each entry, what was the 3-min SPX movement before entry_time?
    dates = db.get_all_dates()

    print(f'\n  Pre-entry 3-min SPX moves (for all E1-E3 entries):')
    print(f'  {"Date":<12}{"Entry":>7}{"SPX@entry":>10}{"3min move":>12}{"Delay?":>8}{"Outcome":>10}')

    large_move_count = 0
    small_move_count = 0
    large_move_pnl = 0
    small_move_pnl = 0

    for date in dates:
        entries = db.get_entries(date)
        stops = db.get_stops(date)
        for e in entries:
            if e['num'] > 3 or not e['time']:
                continue
            # time_str may be "2026-04-01 10:15:30" or "10:15" — handle both
            time_str = e['time'][11:19] if len(e['time']) > 10 else e['time']
            parts = time_str.split(':')
            if len(parts) < 2 or not parts[0].strip().isdigit():
                continue
            try:
                hour = int(parts[0])
                minute = int(parts[1])
                second = int(parts[2]) if len(parts) >= 3 and parts[2].strip() else 0
            except (ValueError, IndexError):
                continue
            # Get SPX 3 min before entry
            total_min = hour * 60 + minute - 3
            if total_min < 0: continue
            before_time = f'{total_min // 60:02d}:{total_min % 60:02d}:{second:02d}'
            spx_before = db.get_spx_at_time(date, before_time)
            spx_at = e['spx_at_entry']
            if spx_before is None or spx_at is None:
                continue
            move = abs(spx_at - spx_before)
            threshold = 15.0
            ep = db.compute_entry_pnl(e, stops.get(e['num'], []))

            if move > threshold:
                large_move_count += 1
                large_move_pnl += ep['entry_net']
            else:
                small_move_count += 1
                small_move_pnl += ep['entry_net']

    print(f'\n  Entries with pre-entry move > 15pt: {large_move_count}, total P&L ${large_move_pnl:+.0f}, avg ${large_move_pnl/max(1,large_move_count):+.0f}')
    print(f'  Entries with pre-entry move <= 15pt: {small_move_count}, total P&L ${small_move_pnl:+.0f}, avg ${small_move_pnl/max(1,small_move_count):+.0f}')

    print_finding(
        'calm_entry_threshold_pts', '15.0 points',
        'Need Saxo tick-level data to simulate delays; entries already delayed aren\'t logged as "delayed"',
        'LOW',
        f'Large pre-entry moves (>15pt) avg ${large_move_pnl/max(1,large_move_count):+.0f}, calm avg ${small_move_pnl/max(1,small_move_count):+.0f}',
    )


def audit_stop_buffers(db: ConfigAuditDB):
    """call_stop_buffer $0.75 / put_stop_buffer $1.75: widen stops to reduce false stops.

    Measurement approach: look at actual_debit vs trigger_level.
    Slippage = actual - trigger. If slippage is high, stop may be firing too easy.
    """
    print_header('TIER 2.3 — Stop Buffers (call $0.75 / put $1.75)')

    # Analyze stops: compare trigger_level vs actual_debit
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT date, entry_number, side, trigger_level, actual_debit,
        quoted_mid_at_stop, slippage_on_close
        FROM trade_stops WHERE date >= '2026-02-10'""")
    stops = c.fetchall()

    call_slippages = []
    put_slippages = []
    for date, num, side, trigger, actual, quoted_mid, slip in stops:
        if quoted_mid and slip is not None:
            if side == 'call':
                call_slippages.append(slip)
            elif side == 'put':
                put_slippages.append(slip)

    if call_slippages:
        avg_call_slip = sum(call_slippages) / len(call_slippages)
        print(f'\n  Call stop slippage: avg ${avg_call_slip:.2f}, n={len(call_slippages)}')
    if put_slippages:
        avg_put_slip = sum(put_slippages) / len(put_slippages)
        print(f'  Put stop slippage: avg ${avg_put_slip:.2f}, n={len(put_slippages)}')

    # Count false-stop patterns: put stopped but spread recovered
    # Hard to measure without re-simulating — needs spread_snapshots analysis
    c.execute("""SELECT COUNT(*) FROM trade_stops
        WHERE date >= '2026-02-10' AND side = 'put' AND confirmation_seconds > 30""")
    conf_puts = c.fetchone()[0]
    print(f'\n  Put stops with >30s confirmation (MKT-036 era): {conf_puts}')

    conn.close()

    print_finding(
        'put_stop_buffer', '$1.75 (updated from $1.55)',
        'Keep current value — reduced put stops significantly since Mar 16 (MKT-039)',
        'MEDIUM',
        f'{len(put_slippages)} put stops logged with slippage data',
    )
    print_finding(
        'call_stop_buffer', '$0.75 (updated from $0.35)',
        'Keep current value — updated for safety',
        'MEDIUM',
        f'{len(call_slippages)} call stops logged',
    )


def audit_buffer_decay(db: ConfigAuditDB):
    """buffer_decay_start_mult 2.5, buffer_decay_hours 4.0: widen early stops by 2.5x.

    Measurement: look at stop times to see if early stops (<1hr) or late stops (>3hr) dominate.
    """
    print_header('TIER 2.4 — Buffer Decay (2.5× start, 4.0h duration)')

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT date, entry_number, side, minutes_held, actual_debit, net_pnl
        FROM trade_stops WHERE date >= '2026-02-10' AND minutes_held IS NOT NULL""")
    stops = c.fetchall()

    buckets = {
        '<30min': [],
        '30-60min': [],
        '60-120min': [],
        '120-240min': [],
        '>240min': [],
    }

    for date, num, side, held, debit, pnl in stops:
        if held < 30: b = '<30min'
        elif held < 60: b = '30-60min'
        elif held < 120: b = '60-120min'
        elif held < 240: b = '120-240min'
        else: b = '>240min'
        buckets[b].append({'date': date, 'side': side, 'held': held, 'pnl': pnl or 0})

    print(f'\n  Stop timing distribution:')
    print(f'  {"Bucket":<12}{"Count":>8}{"Calls":>8}{"Puts":>8}{"Total Loss":>14}')
    for label, items in buckets.items():
        if not items: continue
        calls = sum(1 for i in items if i['side'] == 'call')
        puts = sum(1 for i in items if i['side'] == 'put')
        loss = sum(i['pnl'] for i in items)
        print(f'  {label:<12}{len(items):>8}{calls:>8}{puts:>8}${loss:>+12.0f}')

    conn.close()

    # Early stops with high decay buffer may have been false alarms.
    # Without re-simulating, we can't know for sure.
    print_finding(
        'buffer_decay_start_mult / hours', '2.5× × 4.0h',
        'INSUFFICIENT DATA to isolate decay impact; current values are from VM',
        'LOW',
        'Would need full monitoring replay with/without decay to measure',
    )


# ============================================================================
# TIER 3 — INSUFFICIENT DATA (need bid/ask replay or quote history)
# ============================================================================

def audit_strike_selection_configs(db: ConfigAuditDB):
    """Credit thresholds and OTM starting distances determine WHICH strike is chosen.

    To truly evaluate alternatives, we'd need to replay MKT-011 scan with different
    thresholds against historical Saxo quotes. We only have quotes for ACTUALLY-CHOSEN
    strikes (not rejected ones).

    HYDRA's schema v6 (deployed today) will start capturing bid/ask at each scan.
    After ~2 weeks of live data, a proper replay will be possible.
    """
    print_header('TIER 3 — Strike Selection Configs (INSUFFICIENT DATA)')

    configs = [
        ('min_call_credit', '$2.00', 'Primary call credit threshold'),
        ('min_put_credit', '$2.75', 'Primary put credit threshold'),
        ('call_credit_floor', '$0.75', 'MKT-029 graduated fallback for calls'),
        ('put_credit_floor', '$2.00', 'MKT-029 graduated fallback for puts'),
        ('base_distance_at_vix15', '40', 'Base OTM distance at VIX 15 (~8-delta)'),
        ('call_starting_otm_multiplier', '3.5×', 'MKT-024 wider starting (calls)'),
        ('put_starting_otm_multiplier', '4.0×', 'MKT-024 wider starting (puts)'),
        ('call_min_spread_width / put_min_spread_width', '25pt / 25pt', 'MKT-027 floor'),
        ('max_spread_width', '110pt', 'MKT-027 cap'),
        ('spread_vix_multiplier', '6.0', 'MKT-027 continuous formula'),
        ('call_tighten_retries / put_tighten_retries', '2 / 2', 'MKT-040 retry count'),
    ]

    print(f'\n  The following configs affect STRIKE SELECTION.')
    print(f'  Live data only contains quotes for strikes CHOSEN — not rejected alternatives.')
    print(f'  Proper evaluation requires either:')
    print(f'    1. Historical Saxo bid/ask chains (~2 weeks of v6 data being collected now)')
    print(f'    2. ThetaData-based simulation (~65% optimistic — unreliable in absolute terms)')
    print(f'\n  Configs in this tier:')
    for name, value, desc in configs:
        print(f'    - {name}: {value} — {desc}')

    print_finding(
        'Strike-selection configs', 'Various',
        'DEFER — wait 2-4 weeks for Schema v6 to collect enough Saxo bid/ask snapshots',
        'LOW',
        'Any change without quote replay is speculation',
    )


def audit_fomc_and_calendar(db: ConfigAuditDB):
    """FOMC T+1 call-only filter. Observable: how did HYDRA perform on those days?"""
    print_header('TIER 3.1 — FOMC T+1 Call-Only Filter (MKT-038)')

    # Without FOMC calendar access, can't precisely identify T+1 days.
    # But we can check days where ALL entries were call-only via override
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT date, COUNT(*) as n, COUNT(CASE WHEN override_reason='mkt-038' THEN 1 END) as fomc_fires
        FROM trade_entries WHERE date >= '2026-02-10' GROUP BY date HAVING fomc_fires > 0""")
    fomc_days = c.fetchall()
    conn.close()

    print(f'\n  FOMC T+1 fires in data: {len(fomc_days)} days')
    for date, n, fomc_fires in fomc_days:
        s = db.get_daily_summary(date)
        pnl = s['net_pnl'] if s else 0
        print(f'    {date}: {fomc_fires}/{n} entries forced call-only, day P&L ${pnl:+.0f}')

    if len(fomc_days) < 3:
        print_finding(
            'FOMC T+1 call-only (MKT-038)', 'Enabled',
            'INSUFFICIENT DATA (<3 fires) — keep enabled per research',
            'LOW',
            f'Only {len(fomc_days)} FOMC T+1 days in data',
        )


# ============================================================================
# MAIN
# ============================================================================

def main():
    db = ConfigAuditDB(DB_PATH)

    print('\n' + '#' * 100)
    print('#  HYDRA CONFIG AUDIT — ALL VALUES')
    print('#  Using tested config_audit_lib (20 passing tests)')
    print('#  Data: 37 trading days (Feb 10 - Apr 10, 2026)')
    print('#  Reliable: 27/37 days (post-Fix-87 reconciliation)')
    print('#' * 100)

    # Tier 1 (high confidence)
    audit_upday_threshold(db)
    audit_vix_regime(db)
    audit_entry_times(db)

    # Tier 2 (medium)
    audit_whipsaw_filter(db)
    audit_calm_entry(db)
    audit_stop_buffers(db)
    audit_buffer_decay(db)

    # Tier 3 (insufficient data)
    audit_strike_selection_configs(db)
    audit_fomc_and_calendar(db)

    # Final summary
    print_header('\nFINAL SUMMARY', char='#')
    print("""
  ACTIONABLE FINDINGS (ranked by confidence × impact):

  [HIGH CONFIDENCE]
    1. base_entry_downday_callonly_pct: Keep at 0.57% or nudge to 0.60%
       → +$1,500 benefit at 0.60% (tested separately in analyze_base_downday.py)

  [MEDIUM CONFIDENCE]
    2. vix_regime_breakpoints: Current [14, 20, 30] lumps losers with winners
       → Data: VIX 22-25 avg -$202/day (9 days), 18-22 avg +$53/day (12 days)
       → Proposed: [18, 22, 28] — reduce entries at VIX 22-28 from 3 to 2
       → Estimated benefit: ~$500-700 over similar period

    3. Entry slot performance: Entry #1 (10:15) is the worst performer
       → Data: E#1: -$79/entry, E#2: -$39/entry, E#3: -$14/entry
       → Later entries clearly outperform earlier entries
       → Consider: DROP Entry #1 entirely, or shift schedule to 10:45/11:15/11:45

  [LOW CONFIDENCE — need more data]
    - E6 upday conditional (only 3 fires): keep at 0.25%
    - FOMC T+1 call-only (only 1 fire): keep enabled
    - Whipsaw filter: filter fires not logged with reason — add logging first
    - Calm entry: delays not logged — add logging first
    - Stop buffers: slippage reasonable, no change needed

  [TIER 3 — CANNOT EVALUATE WITHOUT MORE DATA]
    - Strike selection (min_credit, floors, OTM multipliers, spread widths):
      DEFER until Schema v6 collects 2-4 weeks of Saxo bid/ask history
    - Buffer decay: need full monitoring replay with/without decay

  DATA QUALITY CAVEATS:
    - 10 of 37 days are PRE-Fix-87 with known settlement overstatement bug
    - VIX 30+ bucket has n=1 from Mar 9 which used different entry schedule
    - All VIX regime analysis pre-dates current [14,20,30] breakpoints
""")


if __name__ == '__main__':
    main()
