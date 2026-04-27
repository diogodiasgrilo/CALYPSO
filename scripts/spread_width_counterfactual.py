#!/usr/bin/env python3
"""Counterfactual: what would last week's stops have cost at 50pt and 75pt?

Mechanics:
  1. For each real stop, we know: SPX at stop, short strike, original long strike,
     stop_time, VIX, actual debit.
  2. The actual SHORT leg's value is preserved (HYDRA's adaptive tightening keeps
     short the same regardless of width — that's the empirical finding).
  3. The LONG leg's value differs because it sits closer/further OTM.
  4. We model long leg values with Black-Scholes using IV ≈ VIX/100 and the time
     remaining to 4pm ET expiry.
  5. Counterfactual spread mid = (actual_short_value) - (modeled_long_at_new_strike)
  6. Validate the model by comparing modeled-vs-actual long at the original strike.
  7. Counterfactual fill = counterfactual_mid + observed median slippage.

This is a Saxo-grounded empirical test: "given last week's exact SPX moves and
times, would Tammy's 50pt have resulted in smaller stop debits?"
"""
import sqlite3
from datetime import datetime, time
from math import log, sqrt, exp

DB = "/opt/calypso/data/backtesting.db"
START = "2026-04-17"  # Apr 17 stop included for context
END = "2026-04-24"

# Median slippage observed in live data (additive constants)
CALL_SLIP_DOLLARS = 60.0
PUT_SLIP_DOLLARS = 40.0

# Risk-free rate (low-rate env, near-zero impact for short-dated)
R = 0.04


def normcdf(x):
    """Abramowitz-Stegun normal CDF approximation, no scipy dependency."""
    a1, a2, a3 = 0.254829592, -0.284496736, 1.421413741
    a4, a5, p = -1.453152027, 1.061405429, 0.3275911
    sign = 1 if x >= 0 else -1
    z = abs(x) / sqrt(2)
    t = 1.0 / (1.0 + p * z)
    y = 1 - ((a1*t + a2*t**2 + a3*t**3 + a4*t**4 + a5*t**5) * exp(-z*z))
    return 0.5 * (1 + sign * y)


def bs_price(S, K, T, iv, opt_type, r=R):
    """Black-Scholes option mid price."""
    if T <= 0:
        return max(0.0, S - K) if opt_type == 'C' else max(0.0, K - S)
    if iv <= 0 or S <= 0 or K <= 0:
        return max(0.0, S - K) if opt_type == 'C' else max(0.0, K - S)
    d1 = (log(S / K) + (r + iv ** 2 / 2) * T) / (iv * sqrt(T))
    d2 = d1 - iv * sqrt(T)
    if opt_type == 'C':
        return S * normcdf(d1) - K * exp(-r * T) * normcdf(d2)
    else:
        return K * exp(-r * T) * normcdf(-d2) - S * normcdf(-d1)


def parse_stop_time(date_str, time_str):
    """Parse 'HH:MM:SS' as datetime on date_str."""
    if not time_str:
        return None
    s = time_str.strip()
    try:
        return datetime.strptime(f"{date_str} {s}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def time_to_expiry_years(stop_dt):
    """Years remaining to 4pm ET expiry (assumes stop_dt is in ET)."""
    if stop_dt is None:
        return 0
    expiry = datetime.combine(stop_dt.date(), time(16, 0, 0))
    secs = (expiry - stop_dt).total_seconds()
    if secs <= 0:
        return 0
    return secs / (365.25 * 24 * 3600)


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT s.date, s.entry_number, s.side, s.stop_time, s.spx_at_stop,
           s.trigger_level, s.actual_debit, s.net_pnl,
           e.short_call_strike, e.long_call_strike, e.short_put_strike, e.long_put_strike,
           e.call_credit, e.put_credit, e.total_credit,
           e.vix_at_entry, e.entry_time, e.entry_type
    FROM trade_stops s
    JOIN trade_entries e ON s.date = e.date AND s.entry_number = e.entry_number
    WHERE s.date BETWEEN ? AND ?
    ORDER BY s.date, s.entry_number, s.side
""", (START, END))
stops = [dict(r) for r in cur.fetchall()]
conn.close()

print("=" * 110)
print(f"SPREAD-WIDTH COUNTERFACTUAL — last week's stops at 50pt / 75pt vs actual 110pt")
print(f"Period: {START} -> {END}")
print(f"Method: Black-Scholes long-leg estimate (IV = VIX/100), preserved actual short")
print("=" * 110)
print()
print(f"{'Date':>10} {'#':>2} {'Side':>5} {'SPX':>7} {'Time':>9} {'IV':>5} "
      f"{'Trig':>5} {'Actual':>7} {'Model':>7} {'@75pt':>7} {'@50pt':>7} "
      f"{'Δ@75':>6} {'Δ@50':>6}")
print("-" * 110)

total_actual = 0.0
total_75 = 0.0
total_50 = 0.0

for s in stops:
    stop_dt = parse_stop_time(s['date'], s['stop_time'])
    T = time_to_expiry_years(stop_dt)
    iv = (s['vix_at_entry'] or 19.0) / 100.0  # rough approximation; ignores skew
    spx = s['spx_at_stop']
    side = s['side']
    actual_debit = s['actual_debit'] or 0
    if not actual_debit or actual_debit <= 0 or T <= 0 or spx is None:
        continue

    if side == 'call':
        short_K = s['short_call_strike']
        long_K = s['long_call_strike']
        opt = 'C'
        if not short_K or not long_K:
            continue
        actual_width = long_K - short_K  # positive (long above short)
        # Hypothetical long strikes for narrower widths
        long_K_75 = short_K + 75
        long_K_50 = short_K + 50
        slip = CALL_SLIP_DOLLARS
    else:  # put
        short_K = s['short_put_strike']
        long_K = s['long_put_strike']
        opt = 'P'
        if not short_K or not long_K:
            continue
        actual_width = short_K - long_K  # positive (long below short)
        long_K_75 = short_K - 75
        long_K_50 = short_K - 50
        slip = PUT_SLIP_DOLLARS

    # BS prices at stop (in $/share — multiply by 100 for $/contract)
    long_bs_actual = bs_price(spx, long_K, T, iv, opt) * 100
    long_bs_75 = bs_price(spx, long_K_75, T, iv, opt) * 100
    long_bs_50 = bs_price(spx, long_K_50, T, iv, opt) * 100

    # Anchor on ACTUAL fill, not BS-modeled mid. This eliminates BS model error
    # at the strike level (skew, etc) — both actual long and hypothetical long
    # are off by similar amounts, so the DIFFERENCE between BS-long values
    # approximates the real-market difference.
    actual_mid_at_fill = actual_debit - slip
    delta_long_75 = long_bs_75 - long_bs_actual  # positive: narrower long worth more
    delta_long_50 = long_bs_50 - long_bs_actual

    # Counterfactual mid = actual mid minus the additional long value
    counter_mid_75 = actual_mid_at_fill - delta_long_75
    counter_mid_50 = actual_mid_at_fill - delta_long_50

    counter_fill_75 = counter_mid_75 + slip
    counter_fill_50 = counter_mid_50 + slip

    # Sanity-check field for output: BS-modeled mid (for spotting bad model fits)
    short_bs = bs_price(spx, short_K, T, iv, opt) * 100
    model_mid = short_bs - long_bs_actual

    # P&L delta vs actual (only on the stopped side)
    delta_75 = actual_debit - counter_fill_75
    delta_50 = actual_debit - counter_fill_50

    total_actual += actual_debit
    total_75 += counter_fill_75
    total_50 += counter_fill_50

    print(f"{s['date']:>10} {s['entry_number']:>2} {side:>5} "
          f"{spx:>7.0f} {s['stop_time']:>9} {iv:>5.2f} "
          f"{s['trigger_level']:>5.0f} {actual_debit:>7.0f} "
          f"{model_mid:>7.0f} {counter_fill_75:>7.0f} {counter_fill_50:>7.0f} "
          f"{delta_75:>+6.0f} {delta_50:>+6.0f}")

print("-" * 110)
print(f"{'TOTAL DEBITS':>53}  {total_actual:>7.0f} "
      f"  ?     {total_75:>7.0f} {total_50:>7.0f} "
      f"{total_actual - total_75:>+6.0f} {total_actual - total_50:>+6.0f}")

print()
print(f"Total actual debits (110pt):       ${total_actual:>7.0f}")
print(f"Counterfactual @75pt:              ${total_75:>7.0f}  -- saves ${total_actual - total_75:.0f}")
print(f"Counterfactual @50pt:              ${total_50:>7.0f}  -- saves ${total_actual - total_50:.0f}")
print()
print("Notes:")
print("- BS uses IV = VIX/100 (no skew adjustment). Real put IV is HIGHER than this,")
print("  so put-side numbers slightly understate the long leg's actual protective value.")
print("- 'Model' column shows BS estimate of actual spread mid; useful sanity check vs Actual fill.")
print("- Slippage held constant per side (call $60, put $40) — assumes same execution dynamics.")
print("- Δ@75 = actual_debit - counter_fill_75 (positive = narrower would have saved $).")
