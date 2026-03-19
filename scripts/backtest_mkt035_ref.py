#!/usr/bin/env python3
"""Backtest MKT-035 E6/E7: sweep thresholds 0.1% to 2.0% using SPX open as reference."""

import sqlite3

DB_PATH = "data/backtesting.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

days = [r["date"] for r in conn.execute(
    "SELECT DISTINCT date FROM daily_summaries ORDER BY date"
).fetchall()]

print(f"Analyzing {len(days)} trading days...")

# Build dataset: for each day + E6/E7 slot, get SPX open, price at entry time, and actual outcome
slots = []

for day in days:
    first_tick = conn.execute(
        "SELECT spx_price FROM market_ticks WHERE timestamp LIKE ? ORDER BY timestamp LIMIT 1",
        (f"{day}%",)
    ).fetchone()
    if not first_tick or not first_tick["spx_price"]:
        continue
    spx_open = first_tick["spx_price"]

    for entry_name, entry_time in [("E6", "12:45"), ("E7", "13:15")]:
        tick_at_entry = conn.execute(
            "SELECT spx_price FROM market_ticks WHERE timestamp LIKE ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
            (f"{day}%", f"{day} {entry_time}:59")
        ).fetchone()
        if not tick_at_entry or not tick_at_entry["spx_price"]:
            continue
        spx_at_entry = tick_at_entry["spx_price"]

        drop_vs_open = (spx_at_entry - spx_open) / spx_open

        entry_num = 6 if entry_name == "E6" else 7
        actual_entry = conn.execute(
            "SELECT entry_number, total_credit, entry_type FROM trade_entries WHERE date = ? AND entry_number = ?",
            (day, entry_num)
        ).fetchone()

        actual_stop = conn.execute(
            "SELECT net_pnl, actual_debit FROM trade_stops WHERE date = ? AND entry_number = ?",
            (day, entry_num)
        ).fetchone()

        was_placed = actual_entry is not None
        was_stopped = actual_stop is not None
        credit = actual_entry["total_credit"] if actual_entry else 0
        stop_pnl = actual_stop["net_pnl"] if actual_stop else 0

        if was_placed and not was_stopped:
            net_pnl = credit - 5.0
        elif was_placed and was_stopped:
            net_pnl = stop_pnl
        else:
            net_pnl = 0

        slots.append({
            "day": day, "entry": entry_name,
            "spx_open": spx_open, "spx_at_entry": spx_at_entry,
            "drop_vs_open": drop_vs_open,
            "was_placed": was_placed, "was_stopped": was_stopped,
            "credit": credit, "net_pnl": net_pnl,
        })

conn.close()

# Also compute average credit for entries that were placed (for estimating unknown outcomes)
placed_entries = [s for s in slots if s["was_placed"]]
if placed_entries:
    avg_credit = sum(s["credit"] for s in placed_entries) / len(placed_entries)
    avg_stop_loss = sum(s["net_pnl"] for s in placed_entries if s["was_stopped"]) / max(1, sum(1 for s in placed_entries if s["was_stopped"]))
    stop_rate = sum(1 for s in placed_entries if s["was_stopped"]) / len(placed_entries)
    avg_win = sum(s["net_pnl"] for s in placed_entries if not s["was_stopped"]) / max(1, sum(1 for s in placed_entries if not s["was_stopped"]))
else:
    avg_credit = 65.0
    avg_stop_loss = -300.0
    stop_rate = 0.5
    avg_win = 60.0

print(f"\nFrom {len(placed_entries)} actual E6/E7 entries:")
print(f"  Avg credit: ${avg_credit:.2f}")
print(f"  Avg win P&L: ${avg_win:.2f}")
print(f"  Avg stop loss: ${avg_stop_loss:.2f}")
print(f"  Stop rate: {stop_rate * 100:.1f}%")

print()
print("=" * 100)
print("MKT-035 E6/E7 THRESHOLD SWEEP: 0.1% to 2.0% (SPX vs OPEN)")
print("=" * 100)

# Sweep thresholds
thresholds = [x / 1000 for x in range(1, 21)]  # 0.001 to 0.020

fmt = "{:<8} {:>8} {:>8} {:>8} {:>10} {:>8} {:>10} {:>12} {:>10}"
print()
print(fmt.format("Thresh", "Trigger", "Known", "Stopped", "KnownP&L", "WinRate", "Unknown", "EstTotalP&L", "PerEntry"))
print("-" * 100)

best_threshold = None
best_pnl = -999999
best_est_pnl = -999999

for threshold in thresholds:
    triggered = [s for s in slots if s["drop_vs_open"] < -threshold]
    known = [s for s in triggered if s["was_placed"]]
    unknown = [s for s in triggered if not s["was_placed"]]

    if known:
        known_pnl = sum(s["net_pnl"] for s in known)
        known_stopped = sum(1 for s in known if s["was_stopped"])
        known_expired = len(known) - known_stopped
        wr = known_expired / len(known) * 100
    else:
        known_pnl = 0
        known_stopped = 0
        known_expired = 0
        wr = 0

    # Estimate unknown entries using actual avg outcomes
    est_unknown_pnl = len(unknown) * (avg_win * (1 - stop_rate) + avg_stop_loss * stop_rate)
    est_total_pnl = known_pnl + est_unknown_pnl
    entries_total = len(triggered)
    per_entry = est_total_pnl / entries_total if entries_total > 0 else 0

    label = f"{threshold * 100:.1f}%"
    print(fmt.format(
        label,
        str(len(triggered)),
        str(len(known)),
        str(known_stopped),
        f"${known_pnl:.0f}",
        f"{wr:.0f}%" if known else "n/a",
        str(len(unknown)),
        f"${est_total_pnl:.0f}",
        f"${per_entry:.0f}"
    ))

    if known_pnl > best_pnl and len(known) >= 2:
        best_pnl = known_pnl
        best_threshold = threshold

    if est_total_pnl > best_est_pnl:
        best_est_pnl = est_total_pnl
        best_est_threshold = threshold

print()
print(f"BEST by known P&L (>= 2 known entries): {best_threshold * 100:.1f}% → ${best_pnl:.0f}")
print(f"BEST by estimated total P&L:             {best_est_threshold * 100:.1f}% → ${best_est_pnl:.0f}")

# Detail the best threshold
print()
print(f"--- DETAIL: {best_threshold * 100:.1f}% threshold ---")
best_triggered = [s for s in slots if s["drop_vs_open"] < -best_threshold]
fmt2 = "{:<12} {:<4} {:>10} {:>8} {:>8} {:>8}"
print(fmt2.format("Date", "E#", "Drop/Open", "Placed", "Stopped", "P&L"))
print("-" * 55)
for s in best_triggered:
    pnl_str = f"${s['net_pnl']:.0f}" if s["was_placed"] else "est"
    stopped_str = str(s["was_stopped"]) if s["was_placed"] else "?"
    print(fmt2.format(
        s["day"], s["entry"],
        f"{s['drop_vs_open'] * 100:.2f}%",
        str(s["was_placed"]), stopped_str, pnl_str
    ))

# Also show: what if E6/E7 are DISABLED entirely?
print()
print("--- COMPARISON: E6/E7 DISABLED (no conditional entries) ---")
print(f"P&L: $0 (no entries placed)")
print(f"vs BEST threshold {best_threshold * 100:.1f}%: ${best_pnl:.0f} known P&L")
if best_pnl < 0:
    print(f"→ DISABLING E6/E7 would have saved ${abs(best_pnl):.0f}")
elif best_pnl > 0:
    print(f"→ DISABLING E6/E7 would have missed ${best_pnl:.0f} profit")
