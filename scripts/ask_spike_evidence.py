#!/usr/bin/env python3
"""For every call stop, compare the last bid/ask snapshot before the stop
to the actual fill price. If fill >> snapshot ask, the ask spiked between
snapshots — confirming the mid-price inflation theory."""
import sqlite3

DB_PATH = "data/backtesting.db"

def main():
    conn = sqlite3.connect(DB_PATH)

    stops = conn.execute(
        "SELECT ts.date, ts.entry_number, ts.stop_time, ts.actual_debit, "
        "ts.trigger_level, ts.spx_at_stop, te.total_credit, te.short_call_strike "
        "FROM trade_stops ts "
        "JOIN trade_entries te ON ts.date=te.date AND ts.entry_number=te.entry_number "
        "WHERE ts.side='call' ORDER BY ts.date"
    ).fetchall()

    print("ALL CALL STOPS: Comparing last snapshot bid/ask to actual fill")
    print("If fill price >> last snapshot ask, the ask spiked between snapshots")
    print()
    print("%10s|E#|Trigger| SC Bid| SC Ask|BA Wid| MidSV| AskSV| Fill$| Fill/Ask| Verdict" )
    print("-" * 105)

    found_spike = 0
    total_checked = 0

    for s in stops:
        d, en, stop_time, debit, trigger = s[0], s[1], s[2], s[3], s[4]
        total_credit, strike = s[6], s[7]

        if not stop_time:
            continue

        # Get last snapshot with bid/ask before the stop
        snap = conn.execute(
            "SELECT timestamp, call_spread_value, short_call_bid, short_call_ask, "
            "long_call_bid, long_call_ask "
            "FROM spread_snapshots "
            "WHERE entry_number=? AND timestamp < ? "
            "AND short_call_ask IS NOT NULL AND short_call_ask > 0 "
            "ORDER BY timestamp DESC LIMIT 1",
            (en, d + " " + stop_time)
        ).fetchone()

        if not snap:
            continue

        total_checked += 1
        ts, csv, sc_bid, sc_ask = snap[0], snap[1] or 0, snap[2] or 0, snap[3] or 0
        lc_bid, lc_ask = snap[4] or 0, snap[5] or 0

        ba_width = sc_ask - sc_bid if sc_ask and sc_bid else 0
        mid_sv = ((sc_bid + sc_ask)/2 - (lc_bid + lc_ask)/2) * 100 if sc_bid else 0
        ask_sv = (sc_ask - lc_bid) * 100 if sc_ask else 0

        fill_price = (debit or 0) / 100  # debit is in dollars, /100 for per-share
        ratio = fill_price / sc_ask if sc_ask > 0 else 0

        if ratio >= 3:
            verdict = "MASSIVE SPIKE (%.0fx)" % ratio
            found_spike += 1
        elif ratio >= 2:
            verdict = "LARGE SPIKE (%.1fx)" % ratio
            found_spike += 1
        elif ratio >= 1.5:
            verdict = "MODERATE SPIKE (%.1fx)" % ratio
            found_spike += 1
        elif csv < (trigger or 999) * 0.4:
            verdict = "CSV<<trigger (no bid/ask evidence)"
        else:
            verdict = "Normal"

        snap_time = ts[11:19] if ts else "?"
        print("%10s|%2d| %5.0f | %5.2f| %5.2f| %4.2f| %5.0f| %5.0f| %5.2f| %7.1fx| %s" % (
            d, en, trigger or 0, sc_bid, sc_ask, ba_width, mid_sv, ask_sv,
            fill_price, ratio, verdict))

    print()
    print("Stops with bid/ask data: %d" % total_checked)
    print("Evidence of ask spikes (fill >= 1.5x last ask): %d (%.0f%%)" % (
        found_spike, 100 * found_spike / total_checked if total_checked else 0))
    print()
    print("INTERPRETATION:")
    print("  fill/ask = 1.0x: fill matched the last known ask (normal)")
    print("  fill/ask = 2-5x: ask spiked between snapshot and fill (market maker pulled liquidity)")
    print("  fill/ask = 5x+: massive ask spike (possible API glitch or extreme illiquidity)")

if __name__ == "__main__":
    main()
