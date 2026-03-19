#!/usr/bin/env python3
"""Deep analysis of call stop buffer: track MAX adverse excursion after each stop.

For each call stop in history:
1. What was SPX at stop time?
2. What was the MAX SPX price between stop time and 4:00 PM close?
3. How deep ITM did the short call go?
4. What would the spread have been worth at the worst point?
5. What would holding have cost vs stopping?

This tells us: if we had a wider buffer and DIDN'T stop, what's the worst
the position would have been worth before it (maybe) recovered?
"""

import sqlite3

DB_PATH = "data/backtesting.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Get all call stops with entry details
call_stops = conn.execute("""
    SELECT s.date, s.entry_number, s.stop_time, s.spx_at_stop,
           s.trigger_level, s.actual_debit, s.net_pnl,
           e.total_credit, e.call_credit, e.entry_type,
           e.short_call_strike, e.long_call_strike,
           e.otm_distance_call, e.short_put_strike
    FROM trade_stops s
    JOIN trade_entries e ON s.date = e.date AND s.entry_number = e.entry_number
    WHERE s.side = 'call'
    ORDER BY s.date, s.entry_number
""").fetchall()

print(f"Analyzing {len(call_stops)} call stops...")
print()

results = []

for s in call_stops:
    date = s["date"]
    entry_num = s["entry_number"]
    stop_time = s["stop_time"] or ""
    spx_at_stop = s["spx_at_stop"] or 0
    short_call = s["short_call_strike"] or 0
    long_call = s["long_call_strike"] or 0
    call_credit = s["call_credit"] or 0
    total_credit = s["total_credit"] or 0
    actual_debit = s["actual_debit"] or 0
    net_pnl = s["net_pnl"] or 0
    spread_width = long_call - short_call if (long_call and short_call) else 50

    # Find stop timestamp for querying ticks after stop
    # stop_time format is "HH:MM:SS" or "YYYY-MM-DD HH:MM:SS"
    if len(stop_time) <= 8:
        stop_ts = f"{date} {stop_time}"
    else:
        stop_ts = stop_time

    # Get MAX SPX price from stop time to close (worst case for calls)
    max_after = conn.execute(
        "SELECT MAX(spx_price) as max_spx FROM market_ticks WHERE timestamp LIKE ? AND timestamp >= ?",
        (f"{date}%", stop_ts)
    ).fetchone()

    # Get SPX at close (last tick)
    close_tick = conn.execute(
        "SELECT spx_price FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp DESC LIMIT 1",
        (f"{date}%",)
    ).fetchone()

    # Get MIN SPX after stop (best case - maybe it dropped back)
    min_after = conn.execute(
        "SELECT MIN(spx_price) as min_spx FROM market_ticks WHERE timestamp LIKE ? AND timestamp >= ?",
        (f"{date}%", stop_ts)
    ).fetchone()

    max_spx_after = max_after["max_spx"] if max_after else 0
    min_spx_after = min_after["min_spx"] if min_after else 0
    spx_close = close_tick["spx_price"] if close_tick else 0

    # How deep ITM at worst point?
    max_itm = max_spx_after - short_call if (max_spx_after and short_call) else 0
    max_itm = max(0, max_itm)  # Only count if actually ITM

    # What would the spread have been worth at worst point?
    # Call spread value when ITM = min(ITM_amount, spread_width) * 100
    worst_spread_value = min(max_itm, spread_width) * 100 if max_itm > 0 else 0

    # What would holding to expiry have cost?
    close_itm = spx_close - short_call if (spx_close and short_call) else 0
    close_itm = max(0, close_itm)
    expiry_spread_value = min(close_itm, spread_width) * 100 if close_itm > 0 else 0

    # Net P&L if we held to expiry instead of stopping
    # Credit collected - spread value at expiry - open commission
    hold_pnl = call_credit - expiry_spread_value - 5  # $5 open commission, no close commission if expired

    # Net P&L from the actual stop
    stop_pnl = net_pnl  # already net

    # Difference: positive = holding was better, negative = stopping was better
    hold_vs_stop = hold_pnl - stop_pnl

    is_false_stop = spx_close < short_call if (spx_close and short_call) else None
    is_conditional = entry_num >= 6

    results.append({
        "date": date,
        "entry": entry_num,
        "is_conditional": is_conditional,
        "short_call": short_call,
        "long_call": long_call,
        "spread_width": spread_width,
        "call_credit": call_credit,
        "total_credit": total_credit,
        "spx_at_stop": spx_at_stop,
        "spx_close": spx_close,
        "max_spx_after": max_spx_after,
        "min_spx_after": min_spx_after,
        "max_itm": max_itm,
        "worst_spread_value": worst_spread_value,
        "close_itm": close_itm,
        "expiry_spread_value": expiry_spread_value,
        "actual_debit": actual_debit,
        "stop_pnl": stop_pnl,
        "hold_pnl": hold_pnl,
        "hold_vs_stop": hold_vs_stop,
        "is_false_stop": is_false_stop,
    })

conn.close()

# =============================================================================
# DETAILED TABLE
# =============================================================================
print("=" * 120)
print("CALL STOP DEEP ANALYSIS: What if we held instead of stopping?")
print("=" * 120)
print()

fmt = "{:<11} {:<5} {:>6} {:>6} {:>7} {:>7} {:>7} {:>7} {:>8} {:>8} {:>9} {:>6}"
print(fmt.format(
    "Date", "E#", "SC", "Width", "Credit",
    "SPX@St", "MaxSPX", "SPX@Cl",
    "StopP&L", "HoldP&L", "Hold-Stop", "False"
))
print("-" * 120)

for r in results:
    entry_label = f"E{r['entry']}" + ("*" if r["is_conditional"] else "")
    false_label = "YES" if r["is_false_stop"] else "no"
    print(fmt.format(
        r["date"], entry_label,
        f"{r['short_call']:.0f}", f"{r['spread_width']:.0f}pt",
        f"${r['call_credit']:.0f}",
        f"{r['spx_at_stop']:.0f}", f"{r['max_spx_after']:.0f}", f"{r['spx_close']:.0f}",
        f"${r['stop_pnl']:.0f}", f"${r['hold_pnl']:.0f}",
        f"${r['hold_vs_stop']:+.0f}",
        false_label
    ))

# =============================================================================
# SUMMARY BY OUTCOME
# =============================================================================
print()
print("=" * 120)
print("SUMMARY")
print("=" * 120)

false_stops = [r for r in results if r["is_false_stop"]]
true_stops = [r for r in results if r["is_false_stop"] == False]

print(f"\nFalse stops (SPX below short call at close): {len(false_stops)}")
if false_stops:
    total_stop_pnl = sum(r["stop_pnl"] for r in false_stops)
    total_hold_pnl = sum(r["hold_pnl"] for r in false_stops)
    total_diff = sum(r["hold_vs_stop"] for r in false_stops)
    print(f"  Total P&L from stopping:  ${total_stop_pnl:.0f}")
    print(f"  Total P&L if held:        ${total_hold_pnl:.0f}")
    print(f"  Difference (hold - stop): ${total_diff:+.0f}")
    print(f"  Avg per false stop:       ${total_diff / len(false_stops):+.0f}")

    # Worst adverse excursion during hold
    print(f"\n  Worst adverse excursion (MAX SPX above short call after stop):")
    for r in sorted(false_stops, key=lambda x: -x["max_itm"]):
        if r["max_itm"] > 0:
            print(f"    {r['date']} E{r['entry']}: {r['max_itm']:.0f}pt ITM (spread worth ${r['worst_spread_value']:.0f} at worst)")

print(f"\nTrue stops (SPX above short call at close): {len(true_stops)}")
if true_stops:
    total_stop_pnl = sum(r["stop_pnl"] for r in true_stops)
    total_hold_pnl = sum(r["hold_pnl"] for r in true_stops)
    total_diff = sum(r["hold_vs_stop"] for r in true_stops)
    print(f"  Total P&L from stopping:  ${total_stop_pnl:.0f}")
    print(f"  Total P&L if held:        ${total_hold_pnl:.0f}")
    print(f"  Difference (hold - stop): ${total_diff:+.0f}")
    print(f"  Avg per true stop:        ${total_diff / len(true_stops):+.0f}")

    print(f"\n  True stop details (holding would have been WORSE):")
    for r in true_stops:
        print(f"    {r['date']} E{r['entry']}: closed {r['close_itm']:.0f}pt ITM, "
              f"spread worth ${r['expiry_spread_value']:.0f} at expiry, "
              f"max {r['max_itm']:.0f}pt ITM during day")

# =============================================================================
# OVERALL: Should we have wider call buffer?
# =============================================================================
print()
print("=" * 120)
print("VERDICT: WIDER CALL BUFFER?")
print("=" * 120)

all_stop_pnl = sum(r["stop_pnl"] for r in results)
all_hold_pnl = sum(r["hold_pnl"] for r in results)
all_diff = sum(r["hold_vs_stop"] for r in results)

print(f"\nAcross ALL {len(results)} call stops:")
print(f"  Total P&L from stopping:  ${all_stop_pnl:.0f}")
print(f"  Total P&L if ALL held:    ${all_hold_pnl:.0f}")
print(f"  Net difference:           ${all_diff:+.0f}")
print()

if all_diff > 0:
    print(f"HOLDING would have saved ${all_diff:.0f} across all call stops.")
    print("This suggests the call buffer is TOO TIGHT — many false stops are costing money.")
else:
    print(f"STOPPING saved ${abs(all_diff):.0f} across all call stops.")
    print("The current buffer is correct — stopping prevents larger losses.")

# Buffer that would capture the savings without the true stop risk
print(f"\n--- Optimal buffer search ---")
print("For each buffer, calculate net P&L if we had used that buffer")
print("(false stops avoided = profit, true stops avoided = bigger loss)")
print()

# Group by E1-E5 vs E6/E7
base_results = [r for r in results if not r["is_conditional"]]
cond_results = [r for r in results if r["is_conditional"]]

for label, group in [("E1-E5 (Full IC call side)", base_results), ("E6/E7 (Call-only)", cond_results)]:
    if not group:
        print(f"\n{label}: No data")
        continue

    print(f"\n{label}:")
    group_stop_pnl = sum(r["stop_pnl"] for r in group)
    group_hold_pnl = sum(r["hold_pnl"] for r in group)
    group_diff = sum(r["hold_vs_stop"] for r in group)
    false_in_group = sum(1 for r in group if r["is_false_stop"])
    true_in_group = sum(1 for r in group if r["is_false_stop"] == False)
    print(f"  Stops: {len(group)} (false: {false_in_group}, true: {true_in_group})")
    print(f"  Stop P&L: ${group_stop_pnl:.0f}, Hold P&L: ${group_hold_pnl:.0f}, Diff: ${group_diff:+.0f}")

    # What's the worst-case adverse excursion we'd need to survive?
    max_adverse = max(r["worst_spread_value"] for r in group)
    avg_adverse = sum(r["worst_spread_value"] for r in group) / len(group) if group else 0
    print(f"  Max adverse excursion: ${max_adverse:.0f}")
    print(f"  Avg adverse excursion: ${avg_adverse:.0f}")
