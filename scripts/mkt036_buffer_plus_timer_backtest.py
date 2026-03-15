"""
MKT-036 Backtest: $5.00 Buffer + 75s Timer Combination

Tests whether adding the 75s confirmation timer ON TOP of the $5.00 put buffer
improves or hurts P&L across all trading days in backtesting.db.

Previous analysis (with $0.10 buffer): Timer alone was NET -$1,080 (rejected).
Previous analysis: $5.00 buffer alone was NET +$6,885 (implemented).
This script tests: $5.00 buffer + timer = ???

Methodology:
1. For each entry, calculate stop level with $5.00 buffer
2. Use tick data to track when spread value first breaches stop level
3. Simulate timer: breach must sustain 75s before stop executes
4. Compare P&L: buffer-only vs buffer+timer

Spread value estimation from SPX price:
- Use Black-Scholes delta approximation for put spread value
- Short put spread value ≈ max(0, strike - SPX) for deep ITM
- For near-ATM: use linear interpolation based on OTM distance
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "data/backtesting.db"
PUT_BUFFER = 500.0  # $5.00 × 100
CALL_BUFFER = 10.0  # $0.10 × 100
TIMER_SECONDS = 75

def estimate_put_spread_value(spx_price, short_put_strike, long_put_strike,
                               minutes_to_expiry):
    """
    Estimate put spread value from SPX price.

    For 0DTE puts:
    - Deep OTM (SPX >> strike): value ≈ 0
    - Near ATM: value increases roughly linearly as SPX approaches strike
    - ITM (SPX < strike): value ≈ (strike - SPX) × 100, capped at spread width

    We use a simplified model based on distance-to-strike and time remaining.
    """
    otm_distance = spx_price - short_put_strike  # positive = OTM
    spread_width = (short_put_strike - long_put_strike) * 100

    if otm_distance <= 0:
        # ITM - spread value ≈ intrinsic + small time value
        intrinsic = abs(otm_distance) * 100
        return min(intrinsic + 50, spread_width)  # cap at spread width

    # OTM - estimate using a decay curve
    # At 0 OTM distance, value ≈ 50% of spread width (ATM)
    # Decay based on distance and time

    # Time factor: more time = more value
    time_factor = max(0.1, min(1.0, minutes_to_expiry / 390))  # 390 min = full day

    # Distance factor: further OTM = less value
    # Typical 0DTE put: loses ~$15-25 per point of OTM distance
    # Adjusted for time remaining
    value_per_point = 20 * time_factor  # $20/point at open, less as day progresses

    estimated_value = max(0, (50 * time_factor + value_per_point * max(0, 30 - otm_distance)))

    # Scale based on how close to strike
    if otm_distance < 10:
        estimated_value = spread_width * 0.4 * time_factor
    elif otm_distance < 20:
        estimated_value = spread_width * 0.25 * time_factor
    elif otm_distance < 30:
        estimated_value = spread_width * 0.15 * time_factor
    elif otm_distance < 50:
        estimated_value = spread_width * 0.08 * time_factor
    elif otm_distance < 75:
        estimated_value = spread_width * 0.04 * time_factor
    else:
        estimated_value = spread_width * 0.01 * time_factor

    return max(0, estimated_value)


def estimate_call_spread_value(spx_price, short_call_strike, long_call_strike,
                                minutes_to_expiry):
    """Mirror of put spread estimation for calls."""
    otm_distance = short_call_strike - spx_price  # positive = OTM
    spread_width = (long_call_strike - short_call_strike) * 100

    if otm_distance <= 0:
        intrinsic = abs(otm_distance) * 100
        return min(intrinsic + 50, spread_width)

    time_factor = max(0.1, min(1.0, minutes_to_expiry / 390))

    if otm_distance < 10:
        estimated_value = spread_width * 0.4 * time_factor
    elif otm_distance < 20:
        estimated_value = spread_width * 0.25 * time_factor
    elif otm_distance < 30:
        estimated_value = spread_width * 0.15 * time_factor
    elif otm_distance < 50:
        estimated_value = spread_width * 0.08 * time_factor
    elif otm_distance < 75:
        estimated_value = spread_width * 0.04 * time_factor
    else:
        estimated_value = spread_width * 0.01 * time_factor

    return max(0, estimated_value)


def parse_entry_time(entry_time_str, date_str):
    """Parse entry_time which can be '10:05 AM ET' or '2026-03-13T10:15:25...'"""
    if not entry_time_str:
        return None
    if entry_time_str[:4].count('-') > 0 and len(entry_time_str) > 10:
        # ISO format: 2026-03-13T10:15:25.405610-04:00
        return datetime.strptime(entry_time_str[:19], "%Y-%m-%dT%H:%M:%S")
    # Format: "10:05 AM ET"
    time_part = entry_time_str.replace(" ET", "").strip()
    try:
        t = datetime.strptime(time_part, "%I:%M %p")
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.replace(hour=t.hour, minute=t.minute)
    except ValueError:
        return None


def minutes_until_close(timestamp_str):
    """Calculate minutes from timestamp to 4:00 PM market close."""
    ts = datetime.strptime(timestamp_str[:19], "%Y-%m-%d %H:%M:%S")
    close = ts.replace(hour=16, minute=0, second=0)
    return max(0, (close - ts).total_seconds() / 60)


def run_backtest():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get all entries
    entries = conn.execute(
        "SELECT * FROM trade_entries ORDER BY date, entry_number"
    ).fetchall()

    # Get all actual stops
    actual_stops = conn.execute(
        "SELECT * FROM trade_stops ORDER BY date, entry_number"
    ).fetchall()

    # Index actual stops by (date, entry_number, side)
    actual_stop_map = {}
    for s in actual_stops:
        key = (s['date'], s['entry_number'], s['side'])
        actual_stop_map[key] = dict(s)

    # Get all dates
    dates = [r['date'] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_summaries ORDER BY date"
    ).fetchall()]

    print(f"{'='*80}")
    print(f"MKT-036 BACKTEST: $5.00 Buffer + {TIMER_SECONDS}s Timer")
    print(f"{'='*80}")
    print(f"Days: {len(dates)} ({dates[0]} to {dates[-1]})")
    print(f"Entries: {len(entries)}, Actual stops: {len(actual_stops)}")
    print(f"Put buffer: ${PUT_BUFFER/100:.2f}, Call buffer: ${CALL_BUFFER/100:.2f}")
    print(f"Timer: {TIMER_SECONDS}s sustained breach required")
    print()

    # Track results per scenario
    results = {
        'actual': {'pnl': 0, 'stops': 0, 'put_stops': 0, 'call_stops': 0},
        'buffer_only': {'pnl': 0, 'stops': 0, 'put_stops': 0, 'call_stops': 0,
                        'false_avoided': 0, 'true_avoided': 0},
        'buffer_timer': {'pnl': 0, 'stops': 0, 'put_stops': 0, 'call_stops': 0,
                         'false_avoided': 0, 'true_avoided': 0, 'timer_saves': 0},
    }

    daily_details = []

    for date in dates:
        day_entries = [dict(e) for e in entries if e['date'] == date]

        # Get ticks for the day
        ticks = conn.execute(
            "SELECT * FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp",
            (f"{date}%",)
        ).fetchall()

        if not ticks:
            continue

        # Get SPX close (last tick or daily summary)
        spx_close_row = conn.execute(
            "SELECT spx_close FROM daily_summaries WHERE date = ?", (date,)
        ).fetchone()
        spx_close = spx_close_row['spx_close'] if spx_close_row else ticks[-1]['spx_price']

        day_actual_pnl = 0
        day_buffer_pnl = 0
        day_timer_pnl = 0
        day_details = {'date': date, 'entries': []}

        for entry in day_entries:
            en = entry['entry_number']
            total_credit = entry['total_credit'] or 0
            call_credit = entry['call_credit'] or 0
            put_credit = entry['put_credit'] or 0
            sc_strike = entry['short_call_strike'] or 0
            sp_strike = entry['short_put_strike'] or 0
            lc_strike = entry['long_call_strike'] or 0
            lp_strike = entry['long_put_strike'] or 0
            entry_time = entry['entry_time'] or ''

            if total_credit <= 0:
                continue

            # Determine entry type
            has_call = sc_strike > 0 and call_credit > 0
            has_put = sp_strike > 0 and put_credit > 0

            entry_detail = {
                'entry_number': en,
                'credit': total_credit,
                'call_credit': call_credit,
                'put_credit': put_credit,
            }

            # Process each side
            for side in ['call', 'put']:
                if side == 'call' and not has_call:
                    continue
                if side == 'put' and not has_put:
                    continue

                credit = call_credit if side == 'call' else put_credit
                buffer = CALL_BUFFER if side == 'call' else PUT_BUFFER

                # Current actual stop level (what was used in production)
                actual_stop_key = (date, en, side)
                actual_stop = actual_stop_map.get(actual_stop_key)

                # New stop level with buffer
                # Full IC: stop = total_credit + buffer
                # One-sided: stop = 2 × credit + buffer
                if has_call and has_put:
                    new_stop_level = total_credit + buffer
                else:
                    new_stop_level = 2 * credit + buffer

                # Determine if this side would have been stopped at settlement
                if side == 'put' and sp_strike > 0:
                    settled_itm = spx_close < sp_strike
                elif side == 'call' and sc_strike > 0:
                    settled_itm = spx_close > sc_strike
                else:
                    settled_itm = False

                is_false_stop = not settled_itm  # False stop = expired OTM at settlement

                # === ACTUAL P&L ===
                if actual_stop:
                    actual_debit = actual_stop['actual_debit'] or actual_stop['trigger_level']
                    actual_loss = -(actual_debit - credit)
                    day_actual_pnl += actual_loss
                    results['actual']['stops'] += 1
                    if side == 'put':
                        results['actual']['put_stops'] += 1
                    else:
                        results['actual']['call_stops'] += 1
                else:
                    # Survived — credit kept
                    day_actual_pnl += credit

                # === BUFFER-ONLY SCENARIO ===
                # Simulate: would this stop still trigger with new buffer?
                if actual_stop:
                    # Check if spread value at stop time exceeded new stop level
                    stop_time = actual_stop['stop_time']
                    spx_at_stop = actual_stop['spx_at_stop']

                    if side == 'put':
                        mins_left = minutes_until_close(stop_time) if stop_time else 200
                        sv_at_stop = estimate_put_spread_value(
                            spx_at_stop, sp_strike, lp_strike, mins_left)
                    else:
                        mins_left = minutes_until_close(stop_time) if stop_time else 200
                        sv_at_stop = estimate_call_spread_value(
                            spx_at_stop, sc_strike, lc_strike, mins_left)

                    # Also check: did SPX continue past the strike?
                    # Use ticks after stop time to find peak spread value
                    peak_sv = sv_at_stop
                    if stop_time:
                        stop_dt = datetime.strptime(stop_time[:19], "%Y-%m-%d %H:%M:%S")
                        for tick in ticks:
                            tick_dt = datetime.strptime(tick['timestamp'][:19], "%Y-%m-%d %H:%M:%S")
                            if tick_dt <= stop_dt:
                                continue
                            mins_left_t = minutes_until_close(tick['timestamp'])
                            if side == 'put':
                                sv = estimate_put_spread_value(
                                    tick['spx_price'], sp_strike, lp_strike, mins_left_t)
                            else:
                                sv = estimate_call_spread_value(
                                    tick['spx_price'], sc_strike, lc_strike, mins_left_t)
                            peak_sv = max(peak_sv, sv)

                    # Would buffer-only stop trigger?
                    buffer_triggers = peak_sv >= new_stop_level

                    if buffer_triggers:
                        # Stop still triggers with buffer — same loss
                        # But might trigger at different time/price (worse?)
                        # Use actual debit as approximation
                        day_buffer_pnl += -(actual_debit - credit)
                        results['buffer_only']['stops'] += 1
                        if side == 'put':
                            results['buffer_only']['put_stops'] += 1
                        else:
                            results['buffer_only']['call_stops'] += 1
                    else:
                        # Buffer prevented this stop!
                        if is_false_stop:
                            # Good: false stop avoided, credit kept
                            day_buffer_pnl += credit
                            results['buffer_only']['false_avoided'] += 1
                            entry_detail[f'{side}_buffer'] = f"FALSE STOP AVOIDED (+${credit:.0f})"
                        else:
                            # Bad: true stop avoided, settled ITM
                            # Loss = spread width (max loss)
                            if side == 'put':
                                max_loss = (sp_strike - lp_strike) * 100
                            else:
                                max_loss = (lc_strike - sc_strike) * 100
                            settlement_loss = -(max_loss - credit)
                            day_buffer_pnl += settlement_loss
                            results['buffer_only']['true_avoided'] += 1
                            entry_detail[f'{side}_buffer'] = f"TRUE STOP MISSED (${settlement_loss:.0f})"
                else:
                    # No actual stop — survived
                    day_buffer_pnl += credit

                # === BUFFER + TIMER SCENARIO ===
                if actual_stop and stop_time:
                    # Simulate timer: find first breach, check if sustained 75s
                    stop_dt = datetime.strptime(stop_time[:19], "%Y-%m-%d %H:%M:%S")
                    entry_dt = parse_entry_time(entry_time, date)

                    breach_start = None
                    timer_triggered = False
                    timer_trigger_time = None
                    timer_trigger_spx = None

                    for tick in ticks:
                        tick_dt = datetime.strptime(tick['timestamp'][:19], "%Y-%m-%d %H:%M:%S")

                        # Skip ticks before entry
                        if entry_dt and tick_dt < entry_dt:
                            continue

                        mins_left_t = minutes_until_close(tick['timestamp'])
                        if side == 'put':
                            sv = estimate_put_spread_value(
                                tick['spx_price'], sp_strike, lp_strike, mins_left_t)
                        else:
                            sv = estimate_call_spread_value(
                                tick['spx_price'], sc_strike, lc_strike, mins_left_t)

                        if sv >= new_stop_level:
                            if breach_start is None:
                                breach_start = tick_dt

                            elapsed = (tick_dt - breach_start).total_seconds()
                            if elapsed >= TIMER_SECONDS:
                                timer_triggered = True
                                timer_trigger_time = tick_dt
                                timer_trigger_spx = tick['spx_price']
                                break
                        else:
                            # Reset timer
                            breach_start = None

                    if timer_triggered:
                        # Timer confirmed stop — execute
                        # SPX may have moved further, potentially worse price
                        if side == 'put':
                            sv_at_trigger = estimate_put_spread_value(
                                timer_trigger_spx, sp_strike, lp_strike,
                                minutes_until_close(timer_trigger_time.strftime("%Y-%m-%d %H:%M:%S")))
                        else:
                            sv_at_trigger = estimate_call_spread_value(
                                timer_trigger_spx, sc_strike, lc_strike,
                                minutes_until_close(timer_trigger_time.strftime("%Y-%m-%d %H:%M:%S")))

                        # Use actual_debit as approximation (timer delay might make it slightly worse)
                        timer_debit = max(actual_debit, sv_at_trigger)
                        day_timer_pnl += -(timer_debit - credit)
                        results['buffer_timer']['stops'] += 1
                        if side == 'put':
                            results['buffer_timer']['put_stops'] += 1
                        else:
                            results['buffer_timer']['call_stops'] += 1
                        entry_detail[f'{side}_timer'] = f"TIMER CONFIRMED at {timer_trigger_time.strftime('%H:%M:%S')}"

                    elif peak_sv >= new_stop_level:
                        # Buffer would trigger but timer saved it
                        if is_false_stop:
                            day_timer_pnl += credit
                            results['buffer_timer']['false_avoided'] += 1
                            results['buffer_timer']['timer_saves'] += 1
                            entry_detail[f'{side}_timer'] = f"TIMER SAVED! (+${credit:.0f})"
                        else:
                            # True stop missed by timer
                            if side == 'put':
                                max_loss = (sp_strike - lp_strike) * 100
                            else:
                                max_loss = (lc_strike - sc_strike) * 100
                            settlement_loss = -(max_loss - credit)
                            day_timer_pnl += settlement_loss
                            results['buffer_timer']['true_avoided'] += 1
                            entry_detail[f'{side}_timer'] = f"TIMER MISSED TRUE STOP (${settlement_loss:.0f})"
                    else:
                        # Buffer prevented stop, timer irrelevant
                        if is_false_stop:
                            day_timer_pnl += credit
                            results['buffer_timer']['false_avoided'] += 1
                        else:
                            if side == 'put':
                                max_loss = (sp_strike - lp_strike) * 100
                            else:
                                max_loss = (lc_strike - sc_strike) * 100
                            settlement_loss = -(max_loss - credit)
                            day_timer_pnl += settlement_loss
                            results['buffer_timer']['true_avoided'] += 1
                elif not actual_stop:
                    # No stop — survived
                    day_timer_pnl += credit
                elif actual_stop:
                    # Stop exists but no stop_time — use same result as buffer-only
                    actual_debit = actual_stop['actual_debit'] or actual_stop['trigger_level']
                    if peak_sv >= new_stop_level:
                        day_timer_pnl += -(actual_debit - credit)
                        results['buffer_timer']['stops'] += 1
                    else:
                        if is_false_stop:
                            day_timer_pnl += credit
                        else:
                            if side == 'put':
                                max_loss = (sp_strike - lp_strike) * 100
                            else:
                                max_loss = (lc_strike - sc_strike) * 100
                            day_timer_pnl += -(max_loss - credit)

            day_details['entries'].append(entry_detail)

        results['actual']['pnl'] += day_actual_pnl
        results['buffer_only']['pnl'] += day_buffer_pnl
        results['buffer_timer']['pnl'] += day_timer_pnl

        daily_details.append({
            'date': date,
            'actual': day_actual_pnl,
            'buffer': day_buffer_pnl,
            'timer': day_timer_pnl,
            'diff_buffer': day_buffer_pnl - day_actual_pnl,
            'diff_timer': day_timer_pnl - day_actual_pnl,
        })

    conn.close()

    # === PRINT RESULTS ===
    print(f"\n{'='*80}")
    print("DAILY BREAKDOWN")
    print(f"{'='*80}")
    print(f"{'Date':<12} {'Actual':>10} {'Buf Only':>10} {'Buf+Timer':>10} {'Δ Buf':>10} {'Δ Timer':>10}")
    print(f"{'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    for d in daily_details:
        print(f"{d['date']:<12} ${d['actual']:>8.0f} ${d['buffer']:>8.0f} ${d['timer']:>8.0f} "
              f"${d['diff_buffer']:>+8.0f} ${d['diff_timer']:>+8.0f}")

    print(f"{'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    print(f"{'TOTAL':<12} ${results['actual']['pnl']:>8.0f} ${results['buffer_only']['pnl']:>8.0f} "
          f"${results['buffer_timer']['pnl']:>8.0f} "
          f"${results['buffer_only']['pnl'] - results['actual']['pnl']:>+8.0f} "
          f"${results['buffer_timer']['pnl'] - results['actual']['pnl']:>+8.0f}")

    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    for label, r in [('Actual (old stops)', results['actual']),
                      ('$5.00 Buffer Only', results['buffer_only']),
                      (f'$5.00 Buffer + {TIMER_SECONDS}s Timer', results['buffer_timer'])]:
        print(f"\n  {label}:")
        print(f"    Total P&L: ${r['pnl']:,.0f}")
        print(f"    Stops: {r['stops']} (put: {r['put_stops']}, call: {r['call_stops']})")
        if 'false_avoided' in r:
            print(f"    False stops avoided: {r['false_avoided']}")
            print(f"    True stops missed: {r['true_avoided']}")
        if 'timer_saves' in r:
            print(f"    Timer-specific saves: {r['timer_saves']}")

    print(f"\n{'='*80}")
    print("NET IMPACT vs ACTUAL")
    print(f"{'='*80}")
    print(f"  Buffer only:     ${results['buffer_only']['pnl'] - results['actual']['pnl']:>+,.0f}")
    print(f"  Buffer + Timer:  ${results['buffer_timer']['pnl'] - results['actual']['pnl']:>+,.0f}")
    print(f"  Timer increment: ${results['buffer_timer']['pnl'] - results['buffer_only']['pnl']:>+,.0f} "
          f"(value of adding timer to buffer)")


if __name__ == "__main__":
    run_backtest()
