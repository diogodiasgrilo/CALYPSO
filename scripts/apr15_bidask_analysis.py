#!/usr/bin/env python3
"""Check the bid/ask spread data around the April 15 stop to see if
the mid-price snapshots are hiding ask-price spikes."""
import sqlite3

DB_PATH = "data/backtesting.db"

def main():
    conn = sqlite3.connect(DB_PATH)

    # Get bid/ask data around the stop time
    print("=== APRIL 15 E#1: BID/ASK DATA AROUND STOP (14:02) ===")
    print("Short call at 7025, Long call at 7130")
    print()

    rows = conn.execute(
        "SELECT timestamp, call_spread_value, "
        "short_call_bid, short_call_ask, long_call_bid, long_call_ask, "
        "short_put_bid, short_put_ask "
        "FROM spread_snapshots "
        "WHERE entry_number=1 "
        "AND timestamp >= '2026-04-15 13:50' AND timestamp <= '2026-04-15 14:05' "
        "ORDER BY timestamp"
    ).fetchall()

    print("%8s | %6s | %10s | %10s | %10s | %10s | %6s | %6s" % (
        "Time", "CSV", "SC Bid/Ask", "LC Bid/Ask", "Ask Spread", "Mid Spread",
        "BA Wid", "AskSV"))
    print("-" * 95)

    for r in rows:
        ts = r[0][11:19] if r[0] else ""
        csv = r[1] if r[1] is not None else 0
        sc_bid = r[2] if r[2] is not None else 0
        sc_ask = r[3] if r[3] is not None else 0
        lc_bid = r[4] if r[4] is not None else 0
        lc_ask = r[5] if r[5] is not None else 0

        # Mid-based spread value (what snapshots show)
        sc_mid = (sc_bid + sc_ask) / 2 if sc_bid and sc_ask else 0
        lc_mid = (lc_bid + lc_ask) / 2 if lc_bid and lc_ask else 0
        mid_sv = (sc_mid - lc_mid) * 100 if sc_mid and lc_mid else 0

        # ASK-based spread value (worst case to close: buy back short at ASK, sell long at BID)
        ask_sv = (sc_ask - lc_bid) * 100 if sc_ask and lc_bid else 0

        # Bid-ask width on short call
        ba_width = sc_ask - sc_bid if sc_ask and sc_bid else 0

        print("%8s | %6.0f | %4.2f/%4.2f | %4.2f/%4.2f | %10.0f | %10.0f | %6.2f | %6.0f" % (
            ts, csv, sc_bid, sc_ask, lc_bid, lc_ask, ask_sv, mid_sv, ba_width, ask_sv))

    # Now show the full day's bid/ask width evolution
    print("\n=== SHORT CALL BID/ASK WIDTH EVOLUTION (full day) ===")
    rows2 = conn.execute(
        "SELECT timestamp, short_call_bid, short_call_ask, call_spread_value "
        "FROM spread_snapshots "
        "WHERE entry_number=1 "
        "AND timestamp >= '2026-04-15 10:15' AND timestamp <= '2026-04-15 14:05' "
        "AND short_call_bid IS NOT NULL AND short_call_ask IS NOT NULL "
        "ORDER BY timestamp"
    ).fetchall()

    if rows2:
        # Sample every ~5 minutes
        print("%8s | %6s | %10s | %6s | %6s" % (
            "Time", "CSV", "SC Bid/Ask", "Width", "AskSV"))
        print("-" * 55)
        prev_min = ""
        max_width = 0
        max_width_ts = ""
        for r in rows2:
            ts = r[0][11:19]
            minute = ts[:5]
            sc_bid = r[1] or 0
            sc_ask = r[2] or 0
            csv = r[3] or 0
            width = sc_ask - sc_bid

            if width > max_width:
                max_width = width
                max_width_ts = ts

            # Print every 5 minutes
            if minute != prev_min and int(minute[3:5]) % 5 == 0:
                # Compute ask-based spread value
                # Need long call data too
                r_full = conn.execute(
                    "SELECT long_call_bid FROM spread_snapshots "
                    "WHERE entry_number=1 AND timestamp=?", (r[0],)
                ).fetchone()
                lc_bid = r_full[0] if r_full and r_full[0] else 0
                ask_sv = (sc_ask - lc_bid) * 100 if lc_bid else 0

                print("%8s | %6.0f | %4.2f/%4.2f | %6.2f | %6.0f" % (
                    ts, csv, sc_bid, sc_ask, width, ask_sv))
                prev_min = minute

        print("\nMax bid/ask width: $%.2f at %s" % (max_width, max_width_ts))
        print("(For context: if the short call has $1.00 bid/ask spread,")
        print(" the ask-based spread value is $100 higher than mid-based)")

    # KEY QUESTION: What would the ASK-based spread value have been?
    print("\n=== CRITICAL: MID vs ASK spread value comparison ===")
    rows3 = conn.execute(
        "SELECT timestamp, call_spread_value, "
        "short_call_bid, short_call_ask, long_call_bid, long_call_ask "
        "FROM spread_snapshots "
        "WHERE entry_number=1 "
        "AND timestamp >= '2026-04-15 10:15' AND timestamp <= '2026-04-15 14:05' "
        "AND short_call_ask IS NOT NULL AND long_call_bid IS NOT NULL "
        "ORDER BY timestamp"
    ).fetchall()

    if rows3:
        max_mid_sv = 0
        max_ask_sv = 0
        max_ask_ts = ""
        for r in rows3:
            sc_bid = r[2] or 0
            sc_ask = r[3] or 0
            lc_bid = r[4] or 0
            lc_ask = r[5] or 0
            mid_sv = ((sc_bid + sc_ask)/2 - (lc_bid + lc_ask)/2) * 100 if sc_bid and lc_bid else 0
            ask_sv = (sc_ask - lc_bid) * 100 if sc_ask else 0
            if mid_sv > max_mid_sv:
                max_mid_sv = mid_sv
            if ask_sv > max_ask_sv:
                max_ask_sv = ask_sv
                max_ask_ts = r[0][11:19]

        print("Max MID-based spread value: $%.0f" % max_mid_sv)
        print("Max ASK-based spread value: $%.0f (at %s)" % (max_ask_sv, max_ask_ts))
        print("Stop trigger level: $320")
        print()
        if max_ask_sv >= 320:
            print(">>> ASK-BASED SPREAD REACHED THE TRIGGER! <<<")
            print("The stop CORRECTLY fired on the worst-case (ask) price,")
            print("while snapshots recorded the mid price which was lower.")
        elif max_ask_sv >= 250:
            print("Ask-based spread came close but didn't reach $320 in snapshots.")
            print("The actual trigger likely hit between snapshot intervals.")
        else:
            print("Neither mid nor ask reached $320 in snapshot data.")
            print("The spike happened BETWEEN 10-second snapshots.")

if __name__ == "__main__":
    main()
