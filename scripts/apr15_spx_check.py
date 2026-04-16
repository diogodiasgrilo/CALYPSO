#!/usr/bin/env python3
"""Check if SPX touched the 7025 call strike on April 15."""
import sqlite3

DB_PATH = "data/backtesting.db"

def main():
    conn = sqlite3.connect(DB_PATH)

    print("=== APRIL 15 SPX HIGH ===")
    row = conn.execute(
        "SELECT MAX(high) FROM market_ohlc_1min "
        "WHERE timestamp >= '2026-04-15 09:30' AND timestamp < '2026-04-16'"
    ).fetchone()
    spx_high = row[0] or 0
    print("Daily high: %.2f" % spx_high)
    print("Short call strike: 7025")
    print("Distance: %.2f pts" % (7025 - spx_high))

    # Top 5 highest bars
    print("\nHighest 1-min bars:")
    rows = conn.execute(
        "SELECT timestamp, high, close FROM market_ohlc_1min "
        "WHERE timestamp >= '2026-04-15 09:30' AND timestamp < '2026-04-16' "
        "ORDER BY high DESC LIMIT 5"
    ).fetchall()
    for r in rows:
        print("  %s  high=%.2f  close=%.2f  (%.0fpt from 7025)" % (
            r[0], r[1], r[2], 7025 - r[1]))

    # SPX around stop time
    print("\nSPX around stop time (14:02):")
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close FROM market_ohlc_1min "
        "WHERE timestamp >= '2026-04-15 13:50' AND timestamp < '2026-04-15 14:10' "
        "ORDER BY timestamp"
    ).fetchall()
    for r in rows:
        print("  %s  H=%.2f L=%.2f C=%.2f (%.0fpt from 7025)" % (
            r[0], r[1], r[2], r[3], r[4], 7025 - r[1]))

    # Ticks around stop
    print("\nTicks around stop:")
    rows = conn.execute(
        "SELECT timestamp, spx_price FROM market_ticks "
        "WHERE timestamp >= '2026-04-15 13:55' AND timestamp < '2026-04-15 14:10' "
        "ORDER BY timestamp"
    ).fetchall()
    for r in rows:
        dist = 7025 - r[1]
        print("  %s  SPX=%.2f  (%.1fpt from 7025)" % (r[0], r[1], dist))

    # CRITICAL: spread snapshots just before and at stop time
    # The stop fired at 14:02:33 with trigger=$320
    # But snapshots show csv=$52 at 14:02. WHY?
    print("\n=== SPREAD SNAPSHOTS 13:58-14:03 (around stop) ===")
    rows = conn.execute(
        "SELECT timestamp, call_spread_value, put_spread_value, "
        "short_call_bid, short_call_ask, long_call_bid, long_call_ask "
        "FROM spread_snapshots "
        "WHERE entry_number=1 "
        "AND timestamp >= '2026-04-15 13:58' AND timestamp <= '2026-04-15 14:03' "
        "ORDER BY timestamp"
    ).fetchall()
    for r in rows:
        csv = r[1] if r[1] is not None else "None"
        psv = r[2] if r[2] is not None else "None"
        sc_bid = r[3] if r[3] is not None else "N/A"
        sc_ask = r[4] if r[4] is not None else "N/A"
        lc_bid = r[5] if r[5] is not None else "N/A"
        lc_ask = r[6] if r[6] is not None else "N/A"
        print("  %s  csv=%s psv=%s  SC bid/ask=%s/%s  LC bid/ask=%s/%s" % (
            r[0], csv, psv, sc_bid, sc_ask, lc_bid, lc_ask))

    # Check the STOP LOG for exact reason
    print("\n=== HYDRA STOP LOG (from trade_stops) ===")
    stop = conn.execute(
        "SELECT * FROM trade_stops WHERE date='2026-04-15' AND entry_number=1"
    ).fetchone()
    if stop:
        cols = [d[0] for d in conn.execute("SELECT * FROM trade_stops LIMIT 0").description]
        for c in cols:
            print("  %s: %s" % (c, stop[cols.index(c)]))

    # Also check: after the call stop fired, did the put side survive?
    print("\n=== POST-STOP: Put side snapshots (14:03 - 16:00) ===")
    rows = conn.execute(
        "SELECT timestamp, put_spread_value FROM spread_snapshots "
        "WHERE entry_number=1 "
        "AND timestamp >= '2026-04-15 14:03' AND timestamp <= '2026-04-15 16:00' "
        "ORDER BY timestamp"
    ).fetchall()
    if rows:
        print("Put snapshots after call stop: %d" % len(rows))
        print("First: %s psv=%s" % (rows[0][0], rows[0][1]))
        print("Last: %s psv=%s" % (rows[-1][0], rows[-1][1]))
        max_psv = max(r[1] for r in rows if r[1] is not None)
        min_psv = min(r[1] for r in rows if r[1] is not None)
        print("Put spread range: %.2f - %.2f" % (min_psv, max_psv))

if __name__ == "__main__":
    main()
