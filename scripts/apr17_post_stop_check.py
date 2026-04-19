#!/usr/bin/env python3
"""Check what happened to the call spread AFTER the April 17 E#1 stop.
Did SPX reach the 7160 short call strike? Would a longer confirmation timer have helped or hurt?"""
import sqlite3

DB_PATH = "data/backtesting.db"

def main():
    conn = sqlite3.connect(DB_PATH)

    print("=== SPX AFTER 10:49 STOP (through 11:30) ===")
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close FROM market_ohlc_1min "
        "WHERE timestamp >= '2026-04-17 10:40' AND timestamp <= '2026-04-17 11:30' "
        "ORDER BY timestamp"
    ).fetchall()

    for r in rows:
        ts = r[0][11:16]
        dist = 7160 - r[2]
        marker = ""
        if r[2] >= 7160:
            marker = " *** SPX BREACHED THE STRIKE"
        elif dist < 5:
            marker = " *** within 5pt of strike"
        elif dist < 10:
            marker = " (within 10pt)"
        print("  %s  H=%.2f L=%.2f C=%.2f | %.1fpt from 7160%s" % (
            ts, r[2], r[3], r[4], dist, marker))

    print("\n=== DAY HIGH ===")
    hi = conn.execute(
        "SELECT MAX(high) FROM market_ohlc_1min "
        "WHERE timestamp >= '2026-04-17 09:30' AND timestamp < '2026-04-18'"
    ).fetchone()
    hi_val = hi[0] or 0
    print("  SPX day high: %.2f" % hi_val)
    print("  Distance from 7160 strike: %.2f pts" % (7160 - hi_val))

    # Find time of day high
    hi_time = conn.execute(
        "SELECT timestamp, high FROM market_ohlc_1min "
        "WHERE timestamp >= '2026-04-17 09:30' AND timestamp < '2026-04-18' "
        "ORDER BY high DESC LIMIT 1"
    ).fetchone()
    if hi_time:
        print("  At: %s" % hi_time[0])

    # What if the stop had been delayed? Check spread snapshots after stop
    print("\n=== SPREAD SNAPSHOT VALUES AFTER STOP (E#1 call was closed, so csv=0) ===")
    # But we can look at similar entries (E#2, E#3) to see if calls spiked further
    print("\nE#2 call spread trajectory (from stop time onward):")
    rows = conn.execute(
        "SELECT timestamp, call_spread_value, short_call_bid, short_call_ask "
        "FROM spread_snapshots "
        "WHERE entry_number=2 "
        "AND timestamp >= '2026-04-17 10:50' AND timestamp <= '2026-04-17 11:30' "
        "AND call_spread_value IS NOT NULL "
        "ORDER BY timestamp"
    ).fetchall()
    if rows:
        print("  %d snapshots. Sample every ~2 min:" % len(rows))
        prev_min = ""
        for r in rows:
            ts = r[0][11:19]
            minute = ts[:5]
            if minute[3:5] and int(minute[3:5]) % 2 == 0 and minute != prev_min:
                csv = r[1] or 0
                bid = r[2] if r[2] else 0
                ask = r[3] if r[3] else 0
                print("    %s  csv=%.0f  SC bid/ask=%.2f/%.2f" % (ts, csv, bid, ask))
                prev_min = minute

    print("\n=== E#1 POST-STOP: what would we be paying if we held? ===")
    # Check E#1 put spread (put side still alive)
    rows = conn.execute(
        "SELECT timestamp, call_spread_value, put_spread_value "
        "FROM spread_snapshots "
        "WHERE entry_number=1 "
        "AND timestamp >= '2026-04-17 10:50' AND timestamp <= '2026-04-17 16:00' "
        "ORDER BY timestamp"
    ).fetchall()
    print("  E#1 snapshots after stop: %d" % len(rows))
    if rows:
        # Show put side trajectory
        print("\n  Put side value over time (put wasn't stopped, still alive):")
        prev_min = ""
        for r in rows:
            ts = r[0][11:19]
            minute = ts[:5]
            if minute[3:5] and int(minute[3:5]) % 15 == 0 and minute != prev_min:
                psv = r[2] if r[2] is not None else "closed"
                csv = r[1] if r[1] is not None else "stopped"
                print("    %s  csv=%s  psv=%s" % (ts, csv, psv))
                prev_min = minute

if __name__ == "__main__":
    main()
