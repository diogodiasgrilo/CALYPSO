#!/usr/bin/env python3
"""Sweep call buffer from $0.00 to $5.00 and find optimal value.

For each buffer value, simulates which stops would/wouldn't fire,
then calculates the TOTAL P&L impact (saved false stops vs missed true stops).

Uses actual tick data to determine if each stop was false or true,
and what the expiry P&L would have been if held.
"""

import sqlite3

DB_PATH = "data/backtesting.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Get all call stops with full details
call_stops = conn.execute("""
    SELECT s.date, s.entry_number, s.stop_time, s.spx_at_stop,
           s.trigger_level, s.actual_debit, s.net_pnl,
           e.total_credit, e.call_credit, e.entry_type,
           e.short_call_strike, e.long_call_strike,
           e.otm_distance_call
    FROM trade_stops s
    JOIN trade_entries e ON s.date = e.date AND s.entry_number = e.entry_number
    WHERE s.side = 'call'
    ORDER BY s.date, s.entry_number
""").fetchall()

# Build enriched stop data
stops = []
for s in call_stops:
    date = s["date"]
    short_call = s["short_call_strike"] or 0
    long_call = s["long_call_strike"] or 0
    call_credit = s["call_credit"] or 0
    total_credit = s["total_credit"] or 0
    actual_debit = s["actual_debit"] or 0
    net_pnl = s["net_pnl"] or 0
    spread_width = long_call - short_call if (long_call and short_call) else 50
    stop_time = s["stop_time"] or ""
    is_conditional = s["entry_number"] >= 6

    if len(stop_time) <= 8:
        stop_ts = f"{date} {stop_time}"
    else:
        stop_ts = stop_time

    # SPX at close
    close_tick = conn.execute(
        "SELECT spx_price FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp DESC LIMIT 1",
        (f"{date}%",)
    ).fetchone()
    spx_close = close_tick["spx_price"] if close_tick else 0

    # Would expire worthless?
    close_itm = max(0, spx_close - short_call) if (spx_close and short_call) else 0
    expiry_value = min(close_itm, spread_width) * 100
    hold_pnl = call_credit - expiry_value - 5  # credit - spread value - open commission
    is_false = spx_close < short_call if (spx_close and short_call) else None

    stops.append({
        "date": date,
        "entry": s["entry_number"],
        "is_conditional": is_conditional,
        "total_credit": total_credit,
        "call_credit": call_credit,
        "actual_debit": actual_debit,
        "stop_pnl": net_pnl,
        "hold_pnl": hold_pnl,
        "expiry_value": expiry_value,
        "is_false": is_false,
        "short_call": short_call,
        "spread_width": spread_width,
        "spx_close": spx_close,
        "close_itm": close_itm,
    })

conn.close()

# Also get total expired credit (entries that were never stopped on call side)
# to calculate overall strategy P&L at each buffer level
conn2 = sqlite3.connect(DB_PATH)
conn2.row_factory = sqlite3.Row
all_entries = conn2.execute("SELECT * FROM trade_entries ORDER BY date, entry_number").fetchall()
all_call_stops = conn2.execute("SELECT date, entry_number FROM trade_stops WHERE side = 'call'").fetchall()
conn2.close()

call_stop_set = set((s["date"], s["entry_number"]) for s in all_call_stops)

# Baseline: credit from entries whose call side was NOT stopped
baseline_expired_call_credit = 0
for e in all_entries:
    if (e["date"], e["entry_number"]) not in call_stop_set:
        baseline_expired_call_credit += (e["call_credit"] or 0)

print(f"Total call stops: {len(stops)}")
print(f"  False (would recover): {sum(1 for s in stops if s['is_false'])}")
print(f"  True (correctly stopped): {sum(1 for s in stops if s['is_false'] == False)}")
print(f"Baseline expired call credit (never stopped): ${baseline_expired_call_credit:.0f}")
print()

# =============================================================================
# SWEEP: E1-E5 Full IC call buffer
# =============================================================================
print("=" * 100)
print("E1-E5 FULL IC: Call Buffer Sweep ($0.00 to $5.00)")
print("=" * 100)
print()
print("Current formula: stop = total_credit + buffer")
print("We sweep the BUFFER portion only (total_credit stays the same)")
print()

base_stops = [s for s in stops if not s["is_conditional"]]

# Current stop formula: total_credit + buffer
# At each buffer, a stop fires if actual_debit >= total_credit + buffer
# If stop doesn't fire, the entry either expires worthless (hold_pnl) or expires ITM (hold_pnl < 0)

fmt = "{:>8} {:>8} {:>10} {:>10} {:>10} {:>12} {:>12} {:>12}"
print(fmt.format("Buffer", "Stops", "FalseStop", "TrueStop", "Avoided", "StopP&L", "HoldP&L", "TotalP&L"))
print("-" * 100)

best_buffer = None
best_total = -999999

for buffer_cents in range(0, 510, 10):  # $0.00 to $5.00 in $0.10 steps
    total_stop_pnl = 0
    total_hold_pnl = 0
    stops_fired = 0
    false_fired = 0
    true_fired = 0
    avoided = 0

    for s in base_stops:
        new_stop_level = s["total_credit"] + buffer_cents

        # Would this stop still fire?
        # The stop fires when spread_value >= stop_level
        # We use actual_debit as proxy (what it cost to close at stop time)
        if s["actual_debit"] >= new_stop_level:
            # Stop fires
            stops_fired += 1
            total_stop_pnl += s["stop_pnl"]
            if s["is_false"]:
                false_fired += 1
            else:
                true_fired += 1
        else:
            # Stop avoided — entry held to expiry
            avoided += 1
            total_hold_pnl += s["hold_pnl"]

    total_pnl = total_stop_pnl + total_hold_pnl
    label = f"${buffer_cents / 100:.2f}"

    if total_pnl > best_total:
        best_total = total_pnl
        best_buffer = buffer_cents

    # Highlight current ($0.10 = 10 cents)
    marker = " ◄ current" if buffer_cents == 10 else ""
    marker = " ◄ BEST" if buffer_cents == best_buffer and buffer_cents != 10 else marker

    print(fmt.format(
        label, str(stops_fired),
        str(false_fired), str(true_fired), str(avoided),
        f"${total_stop_pnl:.0f}", f"${total_hold_pnl:.0f}",
        f"${total_pnl:.0f}"
    ) + marker)

print()
print(f"BEST E1-E5 buffer: ${best_buffer / 100:.2f} → total P&L ${best_total:.0f}")
current_at_10 = None
for buffer_cents in [10]:
    total = 0
    for s in base_stops:
        if s["actual_debit"] >= s["total_credit"] + buffer_cents:
            total += s["stop_pnl"]
        else:
            total += s["hold_pnl"]
    current_at_10 = total
print(f"Current ($0.10):   total P&L ${current_at_10:.0f}")
print(f"Improvement:       ${best_total - current_at_10:+.0f}")

# =============================================================================
# SWEEP: E6/E7 Call-only buffer
# =============================================================================
print()
print("=" * 100)
print("E6/E7 CALL-ONLY: Theo Put + Buffer Sweep")
print("=" * 100)
print()
print("Current formula: stop = call_credit + theo_put ($250) + call_buffer ($10)")
print("Sweeping total buffer (theo_put + call_buffer combined) from $0 to $500")
print()

cond_stops = [s for s in stops if s["is_conditional"]]

if not cond_stops:
    print("No E6/E7 call stops in history — nothing to analyze")
else:
    fmt2 = "{:>10} {:>8} {:>10} {:>10} {:>10} {:>12} {:>12} {:>12}"
    print(fmt2.format("TotalBuf", "Stops", "FalseStop", "TrueStop", "Avoided", "StopP&L", "HoldP&L", "TotalP&L"))
    print("-" * 100)

    best_cond_buf = None
    best_cond_total = -999999

    for total_buffer in range(0, 510, 10):
        total_stop_pnl = 0
        total_hold_pnl = 0
        stops_fired = 0
        false_fired = 0
        true_fired = 0
        avoided = 0

        for s in cond_stops:
            new_stop_level = s["call_credit"] + total_buffer

            if s["actual_debit"] >= new_stop_level:
                stops_fired += 1
                total_stop_pnl += s["stop_pnl"]
                if s["is_false"]:
                    false_fired += 1
                else:
                    true_fired += 1
            else:
                avoided += 1
                total_hold_pnl += s["hold_pnl"]

        total_pnl = total_stop_pnl + total_hold_pnl
        label = f"${total_buffer / 100:.2f}"

        if total_pnl > best_cond_total:
            best_cond_total = total_pnl
            best_cond_buf = total_buffer

        marker = " ◄ current" if total_buffer == 260 else ""

        print(fmt2.format(
            label, str(stops_fired),
            str(false_fired), str(true_fired), str(avoided),
            f"${total_stop_pnl:.0f}", f"${total_hold_pnl:.0f}",
            f"${total_pnl:.0f}"
        ) + marker)

    print()
    print(f"BEST E6/E7 total buffer: ${best_cond_buf / 100:.2f} → total P&L ${best_cond_total:.0f}")
    current_260 = None
    for tb in [260]:
        total = 0
        for s in cond_stops:
            if s["actual_debit"] >= s["call_credit"] + tb:
                total += s["stop_pnl"]
            else:
                total += s["hold_pnl"]
        current_260 = total
    print(f"Current ($2.60):         total P&L ${current_260:.0f}")
    print(f"Improvement:             ${best_cond_total - current_260:+.0f}")
