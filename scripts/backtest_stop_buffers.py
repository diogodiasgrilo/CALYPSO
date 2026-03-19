#!/usr/bin/env python3
"""Backtest stop buffer analysis for HYDRA.

Analyzes two questions:
1. Is the $5.00 put buffer optimal for full IC entries (E1-E5)?
2. Is the $2.50 theoretical put + $0.10 buffer optimal for E6/E7 call-only entries?

Uses actual market tick data to simulate what would have happened with different
buffer values. NO confirmation bias — reports all results including cases where
current settings are already optimal.

Methodology:
- For each stopped entry, check the ACTUAL spread value trajectory from entry
  to expiry using tick-level data
- A "false stop" = spread value exceeded stop level during the day but would
  have expired below stop level (profitable if held)
- A "true stop" = spread value exceeded stop level AND would have expired
  above stop level (correct to stop)
- Tests buffer values from $0.00 to $10.00 in $0.50 increments
"""

import sqlite3
from collections import defaultdict

DB_PATH = "data/backtesting.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row


def get_entry_spread_trajectory(date, entry_num, side):
    """Get the spread value over time for an entry's side.

    Returns list of (timestamp, spread_value) tuples.
    For puts: spread_value = cost to close the put spread
    For calls: spread_value = cost to close the call spread
    """
    # Get entry details
    entry = conn.execute(
        "SELECT * FROM trade_entries WHERE date = ? AND entry_number = ?",
        (date, entry_num)
    ).fetchone()
    if not entry:
        return None, None, None

    credit = entry["total_credit"] or 0
    call_credit = entry.get("call_credit", 0) or 0
    put_credit = entry.get("put_credit", 0) or 0
    entry_type = entry.get("entry_type", "")

    return entry, credit, entry_type


# =============================================================================
# PART 1: PUT BUFFER ANALYSIS (Full IC entries E1-E5)
# =============================================================================

print("=" * 90)
print("PART 1: PUT STOP BUFFER ANALYSIS (Full IC entries E1-E5)")
print("=" * 90)
print()
print("Current setting: put_stop_buffer = $5.00")
print("Current formula: put_stop = total_credit + $500 (5.00 × 100)")
print()

# Get all put stops
put_stops = conn.execute("""
    SELECT s.date, s.entry_number, s.trigger_level, s.actual_debit, s.net_pnl,
           e.total_credit, e.put_credit, e.call_credit, e.entry_type,
           e.short_put_strike, e.long_put_strike, e.otm_distance_put
    FROM trade_stops s
    JOIN trade_entries e ON s.date = e.date AND s.entry_number = e.entry_number
    WHERE s.side = 'put'
    ORDER BY s.date, s.entry_number
""").fetchall()

print(f"Total put stops in history: {len(put_stops)}")
print()

# Show each put stop with details
fmt = "{:<12} {:<4} {:>8} {:>8} {:>10} {:>10} {:>8} {:>10}"
print(fmt.format("Date", "E#", "Credit", "PutCred", "Trigger", "ActDebit", "NetP&L", "OTM"))
print("-" * 80)
for s in put_stops:
    print(fmt.format(
        s["date"], f"E{s['entry_number']}",
        f"${(s['total_credit'] or 0):.0f}", f"${(s['put_credit'] or 0):.0f}",
        f"${(s['trigger_level'] or 0):.0f}", f"${(s['actual_debit'] or 0):.0f}",
        f"${(s['net_pnl'] or 0):.0f}",
        f"{s['otm_distance_put']:.0f}pt" if s['otm_distance_put'] else "?"
    ))

# For each put stop, check: what was the MAX put spread value between entry and expiry?
# If max spread value < stop_level, the stop was "false" (would have recovered)
print()
print("--- False Stop Analysis ---")
print("A false stop = stopped out but would have been profitable at expiry")
print()

# We can't perfectly reconstruct spread values from ticks (we'd need option prices),
# but we CAN check: did SPX recover above the short put strike by close?
# If SPX at close > short_put_strike, the put spread would have expired worthless = false stop

false_stop_analysis = []
for s in put_stops:
    # Get SPX at close (last tick of day)
    last_tick = conn.execute(
        "SELECT spx_price FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp DESC LIMIT 1",
        (f"{s['date']}%",)
    ).fetchone()

    # Get SPX at stop time
    stop_event = conn.execute(
        "SELECT stop_time, spx_at_stop FROM trade_stops WHERE date = ? AND entry_number = ? AND side = 'put'",
        (s["date"], s["entry_number"])
    ).fetchone()

    spx_close = last_tick["spx_price"] if last_tick else 0
    short_put = s["short_put_strike"] or 0
    spx_at_stop = stop_event["spx_at_stop"] if stop_event else 0

    # Put spread expires worthless if SPX > short_put at settlement
    would_expire_worthless = spx_close > short_put if (spx_close and short_put) else None

    # How far was SPX from the short put at stop time vs close?
    cushion_at_stop = spx_at_stop - short_put if (spx_at_stop and short_put) else None
    cushion_at_close = spx_close - short_put if (spx_close and short_put) else None

    false_stop_analysis.append({
        "date": s["date"],
        "entry": s["entry_number"],
        "credit": s["total_credit"] or 0,
        "put_credit": s["put_credit"] or 0,
        "trigger": s["trigger_level"] or 0,
        "actual_debit": s["actual_debit"] or 0,
        "net_pnl": s["net_pnl"] or 0,
        "short_put": short_put,
        "spx_at_stop": spx_at_stop,
        "spx_close": spx_close,
        "cushion_at_stop": cushion_at_stop,
        "cushion_at_close": cushion_at_close,
        "would_expire_worthless": would_expire_worthless,
        "is_false_stop": would_expire_worthless == True,
    })

false_stops = [f for f in false_stop_analysis if f["is_false_stop"]]
true_stops = [f for f in false_stop_analysis if f["is_false_stop"] == False]
unknown = [f for f in false_stop_analysis if f["is_false_stop"] is None]

print(f"False stops (would have recovered): {len(false_stops)}")
print(f"True stops (correctly stopped):     {len(true_stops)}")
print(f"Unknown (missing data):             {len(unknown)}")
print()

if false_stops:
    false_cost = sum(f["net_pnl"] for f in false_stops)
    false_credits = sum((f["put_credit"] or 0) for f in false_stops)
    print(f"False stop total P&L: ${false_cost:.0f} (would have been +${false_credits:.0f} if held)")
    print(f"Cost of false stops: ${abs(false_cost) + false_credits:.0f}")
    print()

    fmt2 = "{:<12} {:<4} {:>8} {:>8} {:>10} {:>10} {:>10} {:>6}"
    print(fmt2.format("Date", "E#", "PutCred", "NetP&L", "SPX@Stop", "SPX@Close", "Cushion@Cl", "False"))
    print("-" * 75)
    for f in false_stop_analysis:
        print(fmt2.format(
            f["date"], f"E{f['entry']}",
            f"${f['put_credit']:.0f}", f"${f['net_pnl']:.0f}",
            f"{f['spx_at_stop']:.0f}" if f["spx_at_stop"] else "?",
            f"{f['spx_close']:.0f}" if f["spx_close"] else "?",
            f"{f['cushion_at_close']:.0f}pt" if f["cushion_at_close"] is not None else "?",
            "YES" if f["is_false_stop"] else "no"
        ))

# Buffer sweep for puts
print()
print("--- Put Buffer Sweep ---")
print("For each buffer value, how many false stops would have been AVOIDED?")
print("(A wider buffer = fewer false stops but more risk if truly breached)")
print()

# For false stops, check: what buffer would have prevented the stop?
# Stop triggers when spread_value >= stop_level
# stop_level = total_credit + buffer
# If we increase buffer, stop_level increases, fewer stops trigger
# But we can't know the EXACT spread value at stop time from our data
# We CAN approximate: the actual_debit tells us what it cost to close
# If actual_debit < new_stop_level, the stop would NOT have triggered

buffers = [x * 50 for x in range(0, 21)]  # $0 to $1000 in $50 increments

fmt3 = "{:>8} {:>12} {:>12} {:>12} {:>14} {:>14}"
print(fmt3.format("Buffer", "PutStops", "FalseAvoid", "TrueAvoid", "SavedP&L", "AddedRisk"))
print("-" * 80)

for buffer in buffers:
    # For each historical put stop, would it have triggered with this buffer?
    would_stop = 0
    would_avoid_false = 0
    would_avoid_true = 0
    saved_pnl = 0
    added_risk = 0

    for f in false_stop_analysis:
        credit = f["credit"]
        actual_debit = f["actual_debit"]
        new_stop_level = credit + buffer

        # Would the stop still trigger?
        # We approximate: if actual_debit >= new_stop_level, it would still trigger
        # This is imperfect (actual_debit is what we PAID, not what the spread was worth at trigger)
        if actual_debit >= new_stop_level:
            would_stop += 1
        else:
            # This stop would have been avoided
            if f["is_false_stop"]:
                would_avoid_false += 1
                saved_pnl += abs(f["net_pnl"]) + (f["put_credit"] or 0)  # avoided loss + kept credit
            elif f["is_false_stop"] == False:
                would_avoid_true += 1
                # Added risk = the entry would have stayed open and potentially lost more
                # Worst case: spread goes to max width (e.g., 50pt = $5000)
                # But we know it expired with some value, so actual risk = close value - credit
                added_risk += abs(f["net_pnl"])  # would have lost at least this much more

    label = f"${buffer / 100:.2f}"
    print(fmt3.format(
        label,
        str(len(false_stop_analysis) - would_avoid_false - would_avoid_true),
        str(would_avoid_false),
        str(would_avoid_true),
        f"${saved_pnl:.0f}" if saved_pnl else "$0",
        f"${added_risk:.0f}" if added_risk else "$0"
    ))

# =============================================================================
# PART 2: CALL BUFFER ANALYSIS (E6/E7 call-only entries)
# =============================================================================

print()
print("=" * 90)
print("PART 2: CALL STOP BUFFER ANALYSIS (E6/E7 call-only + ALL call stops)")
print("=" * 90)
print()
print("Current E6/E7 call-only formula: call_credit + theo_put ($250) + call_buffer ($10)")
print("Current full IC call formula:     total_credit + call_buffer ($10)")
print()

# Get all call stops
call_stops = conn.execute("""
    SELECT s.date, s.entry_number, s.trigger_level, s.actual_debit, s.net_pnl,
           s.stop_time, s.spx_at_stop,
           e.total_credit, e.call_credit, e.put_credit, e.entry_type,
           e.short_call_strike, e.long_call_strike, e.otm_distance_call
    FROM trade_stops s
    JOIN trade_entries e ON s.date = e.date AND s.entry_number = e.entry_number
    WHERE s.side = 'call'
    ORDER BY s.date, s.entry_number
""").fetchall()

print(f"Total call stops in history: {len(call_stops)}")
print()

# Separate E6/E7 call-only from full IC call stops
e67_call_stops = [s for s in call_stops if s["entry_number"] >= 6]
base_call_stops = [s for s in call_stops if s["entry_number"] < 6]

print(f"  Base entry (E1-E5) call stops: {len(base_call_stops)}")
print(f"  Conditional (E6/E7) call stops: {len(e67_call_stops)}")
print()

# Show all call stops
fmt4 = "{:<12} {:<4} {:>8} {:>10} {:>10} {:>8} {:>10} {:>8}"
print(fmt4.format("Date", "E#", "Credit", "CallCred", "Trigger", "ActDeb", "NetP&L", "OTM"))
print("-" * 80)
for s in call_stops:
    entry_label = f"E{s['entry_number']}"
    if s["entry_number"] >= 6:
        entry_label += "*"  # mark conditional
    print(fmt4.format(
        s["date"], entry_label,
        f"${(s['total_credit'] or 0):.0f}",
        f"${(s['call_credit'] or 0):.0f}",
        f"${(s['trigger_level'] or 0):.0f}",
        f"${(s['actual_debit'] or 0):.0f}",
        f"${(s['net_pnl'] or 0):.0f}",
        f"{s['otm_distance_call']:.0f}pt" if s['otm_distance_call'] else "?"
    ))

# False stop analysis for calls
print()
print("--- Call False Stop Analysis ---")

call_false_analysis = []
for s in call_stops:
    last_tick = conn.execute(
        "SELECT spx_price FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp DESC LIMIT 1",
        (f"{s['date']}%",)
    ).fetchone()

    spx_close = last_tick["spx_price"] if last_tick else 0
    short_call = s["short_call_strike"] or 0
    spx_at_stop = s["spx_at_stop"] or 0

    # Call spread expires worthless if SPX < short_call at settlement
    would_expire_worthless = spx_close < short_call if (spx_close and short_call) else None

    cushion_at_close = short_call - spx_close if (spx_close and short_call) else None

    call_false_analysis.append({
        "date": s["date"],
        "entry": s["entry_number"],
        "is_conditional": s["entry_number"] >= 6,
        "credit": s["total_credit"] or 0,
        "call_credit": s["call_credit"] or 0,
        "trigger": s["trigger_level"] or 0,
        "actual_debit": s["actual_debit"] or 0,
        "net_pnl": s["net_pnl"] or 0,
        "short_call": short_call,
        "spx_at_stop": spx_at_stop,
        "spx_close": spx_close,
        "cushion_at_close": cushion_at_close,
        "would_expire_worthless": would_expire_worthless,
        "is_false_stop": would_expire_worthless == True,
    })

call_false = [f for f in call_false_analysis if f["is_false_stop"]]
call_true = [f for f in call_false_analysis if f["is_false_stop"] == False]

print(f"False call stops (would have recovered): {len(call_false)}")
print(f"True call stops (correctly stopped):     {len(call_true)}")
print()

fmt5 = "{:<12} {:<5} {:>8} {:>10} {:>10} {:>10} {:>10} {:>6}"
print(fmt5.format("Date", "E#", "CallCrd", "NetP&L", "SPX@Stop", "SPX@Close", "Cushion", "False"))
print("-" * 75)
for f in call_false_analysis:
    entry_label = f"E{f['entry']}" + ("*" if f["is_conditional"] else "")
    print(fmt5.format(
        f["date"], entry_label,
        f"${f['call_credit']:.0f}", f"${f['net_pnl']:.0f}",
        f"{f['spx_at_stop']:.0f}" if f["spx_at_stop"] else "?",
        f"{f['spx_close']:.0f}" if f["spx_close"] else "?",
        f"{f['cushion_at_close']:.0f}pt" if f["cushion_at_close"] is not None else "?",
        "YES" if f["is_false_stop"] else "no"
    ))

# E6/E7 specific: What theo_put value + buffer combo works best?
print()
print("--- E6/E7 Call-Only: Theo Put + Buffer Sweep ---")
print("Current: call_credit + $250 (theo put) + $10 (buffer)")
print("Testing theo_put values from $0 to $500 in $50 increments")
print()

if e67_call_stops:
    e67_analysis = [f for f in call_false_analysis if f["is_conditional"]]

    fmt6 = "{:>10} {:>8} {:>12} {:>12} {:>14}"
    print(fmt6.format("TheoPut", "Stops", "FalseAvoid", "TrueAvoid", "NetEffect"))
    print("-" * 60)

    for theo_put in range(0, 550, 50):
        buffer_val = 10  # keep call_buffer fixed at $10
        would_stop = 0
        would_avoid_false = 0
        would_avoid_true = 0

        for f in e67_analysis:
            new_stop_level = (f["call_credit"] or 0) + theo_put + buffer_val
            if f["actual_debit"] >= new_stop_level:
                would_stop += 1
            else:
                if f["is_false_stop"]:
                    would_avoid_false += 1
                else:
                    would_avoid_true += 1

        # Net effect: avoided false stops save money, avoided true stops add risk
        net = 0
        for f in e67_analysis:
            new_stop_level = (f["call_credit"] or 0) + theo_put + buffer_val
            if f["actual_debit"] < new_stop_level:
                if f["is_false_stop"]:
                    net += abs(f["net_pnl"]) + (f["call_credit"] or 0)
                else:
                    net -= abs(f["net_pnl"])

        print(fmt6.format(
            f"${theo_put:.0f}",
            str(len(e67_analysis) - would_avoid_false - would_avoid_true),
            str(would_avoid_false),
            str(would_avoid_true),
            f"${net:.0f}"
        ))

# =============================================================================
# PART 3: SUMMARY
# =============================================================================

print()
print("=" * 90)
print("SUMMARY")
print("=" * 90)

# All entries that expired (not stopped) — how much did they make?
all_entries = conn.execute(
    "SELECT date, entry_number, total_credit, entry_type FROM trade_entries ORDER BY date"
).fetchall()
all_stops = conn.execute(
    "SELECT date, entry_number, side FROM trade_stops"
).fetchall()

stop_set = set()
for s in all_stops:
    stop_set.add((s["date"], s["entry_number"], s["side"]))

expired_credits = 0
expired_count = 0
stopped_count = 0
for e in all_entries:
    has_call_stop = (e["date"], e["entry_number"], "call") in stop_set
    has_put_stop = (e["date"], e["entry_number"], "put") in stop_set
    if not has_call_stop and not has_put_stop:
        expired_credits += e["total_credit"]
        expired_count += 1
    else:
        stopped_count += 1

print(f"\nTotal entries: {len(all_entries)}")
print(f"  Expired (no stops): {expired_count} → +${expired_credits:.0f} gross credit")
print(f"  Had at least one stop: {stopped_count}")
print(f"\nPut stops: {len(put_stops)}")
print(f"  False (would have recovered): {len(false_stops)} → cost ${sum(abs(f['net_pnl']) + f['put_credit'] for f in false_stops):.0f}")
print(f"  True (correctly stopped): {len(true_stops)}")
print(f"\nCall stops: {len(call_stops)}")
print(f"  False (would have recovered): {len(call_false)} → cost ${sum(abs(f['net_pnl']) + f['call_credit'] for f in call_false):.0f}")
print(f"  True (correctly stopped): {len(call_true)}")

conn.close()
