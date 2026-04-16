#!/usr/bin/env python3
"""For each historical call stop, use spread_snapshots to check if the
call spread value would have breached a wider ($175) buffer."""
import sqlite3

DB_PATH = "data/backtesting.db"

def main():
    conn = sqlite3.connect(DB_PATH)

    # April 15 specific analysis
    print("=" * 70)
    print("APRIL 15 E#1: CALL SPREAD TRAJECTORY BEFORE STOP (14:02 ET)")
    print("=" * 70)

    e = conn.execute(
        "SELECT total_credit, call_credit FROM trade_entries "
        "WHERE date='2026-04-15' AND entry_number=1"
    ).fetchone()

    if e:
        total_credit, call_credit = e[0], e[1]
        print("Total credit: $%.2f  Call credit: $%.2f" % (total_credit, call_credit))
        print("Current stop trigger (credit+$75): $%.2f" % (total_credit + 75))
        print("Hypothetical $175 trigger: $%.2f" % (total_credit + 175))

        snaps = conn.execute(
            "SELECT timestamp, call_spread_value, put_spread_value "
            "FROM spread_snapshots "
            "WHERE entry_number=1 AND timestamp >= '2026-04-15 10:00' "
            "AND timestamp <= '2026-04-15 14:10' "
            "AND call_spread_value IS NOT NULL "
            "ORDER BY timestamp"
        ).fetchall()

        print("\nSnapshots with call data: %d" % len(snaps))
        if snaps:
            peak = max(s[1] for s in snaps)
            peak_ts = [s[0] for s in snaps if s[1] == peak][0]
            print("Peak call spread: $%.2f at %s" % (peak, peak_ts))
            print("Would $175 buffer be breached: %s" % (peak >= total_credit + 175))

            print("\nTrajectory:")
            prev_min = ""
            for ts, csv, psv in snaps:
                minute = ts[11:16]
                if minute != prev_min:
                    marker = ""
                    if csv >= total_credit + 75:
                        marker = " ** STOP@$75"
                    if csv >= total_credit + 175:
                        marker += " ** STOP@$175"
                    print("  %s  call_sv=%6.1f  put_sv=%6.1f%s" % (
                        ts[11:19], csv, psv or 0, marker))
                    prev_min = minute

    # Historical analysis: all call stops
    print("\n" + "=" * 70)
    print("ALL CALL STOPS: Would $175 buffer have saved them?")
    print("Using spread_snapshot peak call_spread_value vs hypothetical trigger")
    print("=" * 70)

    all_stops = conn.execute(
        "SELECT ts.date, ts.entry_number, ts.trigger_level, ts.actual_debit, "
        "ts.stop_time, te.total_credit, te.call_credit "
        "FROM trade_stops ts "
        "JOIN trade_entries te ON ts.date = te.date AND ts.entry_number = te.entry_number "
        "WHERE ts.side = 'call' ORDER BY ts.date"
    ).fetchall()

    print("\n%10s | %3s | %7s | %7s | %7s | %7s | %s" % (
        "Date", "E#", "Credit", "Trig75", "Peak", "Trig175", "Verdict"))
    print("-" * 85)

    saved_175 = 0
    saved_125 = 0
    total_with_data = 0
    saved_entries_pnl = 0

    for stop in all_stops:
        d, en, trigger, debit, stop_time = stop[0], stop[1], stop[2], stop[3], stop[4]
        total_credit, call_credit = stop[5], stop[6]
        trig_125 = total_credit + 125
        trig_175 = total_credit + 175

        snaps = conn.execute(
            "SELECT call_spread_value FROM spread_snapshots "
            "WHERE entry_number = ? AND timestamp >= ? AND timestamp < ? "
            "AND call_spread_value IS NOT NULL",
            (en, d + " 09:00", d + " 23:59")
        ).fetchall()

        if not snaps:
            print("%10s | %3d | %7.0f | %7.0f | %7s | %7.0f | NO SNAPSHOT DATA" % (
                d, en, total_credit, total_credit + 75, "N/A", trig_175))
            continue

        total_with_data += 1
        peak = max(s[0] for s in snaps)
        last = snaps[-1][0]

        if peak < trig_175:
            saved_175 += 1
            saved_entries_pnl += call_credit - 5  # would expire, keep credit - comm
            verdict = "SAVED ($175) - would expire +$%.0f" % (call_credit - 5)
        else:
            verdict = "STILL STOPPED (peak $%.0f)" % peak

        if peak < trig_125:
            saved_125 += 1

        print("%10s | %3d | %7.0f | %7.0f | %7.0f | %7.0f | %s" % (
            d, en, total_credit, total_credit + 75, peak, trig_175, verdict))

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("Call stops with snapshot data: %d" % total_with_data)
    print()
    print("$125 buffer: would save %d stops (%.0f%%)" % (
        saved_125, 100 * saved_125 / total_with_data if total_with_data else 0))
    print("$175 buffer: would save %d stops (%.0f%%)" % (
        saved_175, 100 * saved_175 / total_with_data if total_with_data else 0))
    print()
    if saved_175:
        print("Saved entries would have expired, keeping their credit:")
        print("  Total credit recovered: $%.2f" % saved_entries_pnl)
        print("  (These entries breached the $75 trigger but NOT the $175 trigger,")
        print("   meaning the spread value peaked between credit+$75 and credit+$175)")

    # Also check: for stops NOT saved, how much WORSE would the wider buffer be?
    # (stop fires later = potentially worse fill)
    print("\nFor stops NOT saved by $175 buffer:")
    worse_count = 0
    for stop in all_stops:
        d, en, trigger, debit = stop[0], stop[1], stop[2], stop[3]
        total_credit = stop[5]
        trig_175 = total_credit + 175
        snaps = conn.execute(
            "SELECT call_spread_value FROM spread_snapshots "
            "WHERE entry_number = ? AND timestamp >= ? AND timestamp < ? "
            "AND call_spread_value IS NOT NULL",
            (en, d + " 09:00", d + " 23:59")
        ).fetchall()
        if not snaps:
            continue
        peak = max(s[0] for s in snaps)
        if peak >= trig_175:
            worse_count += 1

    print("  %d stops would still trigger (spread exceeded credit+$175)" % worse_count)
    print("  These would fire LATER (at a higher spread value),")
    print("  potentially with a WORSE fill than the current $75 trigger.")
    print("  This is the COST of a wider buffer on true trend days.")

if __name__ == "__main__":
    main()
