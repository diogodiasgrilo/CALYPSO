#!/usr/bin/env python3
"""Simulate P&L at different call/put buffer levels using actual stop data."""
import sqlite3

DB_PATH = "data/backtesting.db"
COMM = 5.0

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    entries = conn.execute("""
        SELECT date, entry_number, call_credit, put_credit, total_credit
        FROM trade_entries WHERE date >= '2026-02-10'
    """).fetchall()

    all_stops = conn.execute("""
        SELECT date, entry_number, side, actual_debit, trigger_level
        FROM trade_stops
    """).fetchall()

    stop_map = {}
    for s in all_stops:
        stop_map[(s["date"], s["entry_number"], s["side"])] = {
            "debit": s["actual_debit"] or 0,
            "trigger": s["trigger_level"] or 0
        }

    entry_map = {}
    for e in entries:
        entry_map[(e["date"], e["entry_number"])] = e

    # Build per-side records
    for side_label, credit_key, stop_side in [("CALL", "call_credit", "call"), ("PUT", "put_credit", "put")]:
        sides = []
        for e in entries:
            credit = e[credit_key]
            if not credit or credit <= 0:
                continue
            d, en = e["date"], e["entry_number"]
            total_credit = e["total_credit"] or 0
            si = stop_map.get((d, en, stop_side))
            was_stopped = si is not None
            sides.append({
                "credit": credit, "total_credit": total_credit,
                "stopped": was_stopped,
                "debit": si["debit"] if si else 0,
                "trigger": si["trigger"] if si else 0,
            })

        print("=" * 75)
        print("%s BUFFER SIMULATION (%d entries, %d stopped)" % (
            side_label, len(sides), sum(1 for s in sides if s["stopped"])))
        print("Stop formula: total_credit + buffer")
        print("If debit < total_credit + NEW buffer, we assume the stop would NOT have triggered.")
        print("Saved stops are assumed to expire and keep their side credit.")
        print("=" * 75)

        # Current buffer
        current_buffer = 75 if side_label == "CALL" else 175

        print("\n%10s | %5s | %5s | %6s | %6s | %9s | %9s" % (
            "Buffer", "Stops", "Saved", "StopR", "WR", "Avg P/L", "Total"))
        print("-" * 65)

        best_pnl = -999999
        best_buffer = 0

        for buffer_dollars in [0, 25, 50, 75, 100, 125, 150, 175, 200, 250, 300, 400, 500]:
            saved = 0
            new_stops = 0
            total_pnl = 0

            for s in sides:
                if not s["stopped"]:
                    # Expired: keep credit minus commission
                    total_pnl += s["credit"] - COMM
                else:
                    new_trigger = s["total_credit"] + buffer_dollars
                    if s["debit"] < new_trigger:
                        # Wider buffer would have saved this stop
                        saved += 1
                        total_pnl += s["credit"] - COMM  # expires instead
                    else:
                        # Still stopped even with wider buffer
                        # BUT: wider buffer means we tolerate more loss before stopping
                        # The debit is the same (market moved past both triggers)
                        # Actually if the buffer is wider, the stop fires LATER,
                        # and the debit would be HIGHER (more adverse). We approximate
                        # by using: new_debit = max(debit, new_trigger) since the
                        # stop fires at a higher level.
                        # Conservative: use actual debit (underestimates wider-buffer cost)
                        new_stops += 1
                        total_pnl += s["total_credit"] - s["debit"] - COMM

            total_stops_now = sum(1 for s in sides if s["stopped"]) - saved
            sr = 100.0 * total_stops_now / len(sides)
            wr = 100.0 - sr
            avg_pnl = total_pnl / len(sides)
            tag = "+++" if total_pnl > 0 else "---"
            marker = " <-- CURRENT" if buffer_dollars == current_buffer else ""
            if total_pnl > best_pnl:
                best_pnl = total_pnl
                best_buffer = buffer_dollars

            print("%10s | %5d | %5d | %4.0f%%  | %4.0f%%  | %8.2f | %8.2f %s%s" % (
                "$%d" % buffer_dollars, total_stops_now, saved, sr, wr,
                avg_pnl, total_pnl, tag, marker))

        print("\n>>> OPTIMAL %s BUFFER: $%d (total P&L: $%.2f) <<<" % (
            side_label, best_buffer, best_pnl))

        # Show the delta from current
        current_pnl = 0
        for s in sides:
            if not s["stopped"]:
                current_pnl += s["credit"] - COMM
            else:
                current_pnl += s["total_credit"] - s["debit"] - COMM
        print("Current buffer $%d P&L: $%.2f" % (current_buffer, current_pnl))
        print("Improvement: $%.2f" % (best_pnl - current_pnl))
        print()

    # CAVEAT section
    print("=" * 75)
    print("IMPORTANT CAVEAT")
    print("=" * 75)
    print("""
This simulation is OPTIMISTIC about wider buffers because it assumes:
1. If debit < new_trigger, the stop would NOT have triggered (true)
2. The saved entry then expires worthless and keeps full credit (optimistic -
   the price might have continued moving and stopped at the wider level too)
3. Entries that still stop have the SAME debit (conservative - wider buffer
   means the stop fires later, possibly at a worse price)

The real answer requires spread_snapshot tick data to see if prices that
breached the narrow buffer also breached the wider one. This simulation
gives an UPPER BOUND on the benefit of wider buffers.
""")

if __name__ == "__main__":
    main()
