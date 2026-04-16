#!/usr/bin/env python3
"""
Investigation 1: Why did the Apr 15 stop fire when snapshots show csv=$52?
Investigation 2: For each of the 13 "saved" stops, did SPX later reach the strike?
"""
import sqlite3

DB_PATH = "data/backtesting.db"

def main():
    conn = sqlite3.connect(DB_PATH)

    # ================================================================
    # INVESTIGATION 1: Stop trigger vs snapshot discrepancy
    # ================================================================
    print("=" * 75)
    print("INVESTIGATION 1: STOP TRIGGER vs SNAPSHOT DISCREPANCY")
    print("For ALL call stops, compare the trigger level to the peak")
    print("call_spread_value in snapshots. Flag any where peak < trigger.")
    print("=" * 75)

    stops = conn.execute(
        "SELECT ts.date, ts.entry_number, ts.trigger_level, ts.actual_debit, "
        "ts.stop_time, ts.spx_at_stop, ts.quoted_mid_at_stop, "
        "te.total_credit, te.short_call_strike "
        "FROM trade_stops ts "
        "JOIN trade_entries te ON ts.date=te.date AND ts.entry_number=te.entry_number "
        "WHERE ts.side='call' ORDER BY ts.date"
    ).fetchall()

    print("\n%10s|E#|Trigger|PeakCSV|Debit |SPX@Stop|Strike|Gap |Verdict" )
    print("-" * 85)

    discrepancies = 0
    for s in stops:
        d, en, trigger, debit, stop_time = s[0], s[1], s[2] or 0, s[3] or 0, s[4]
        spx_stop = s[5] or 0
        strike = s[8] or 0
        total_credit = s[7] or 0

        snaps = conn.execute(
            "SELECT call_spread_value FROM spread_snapshots "
            "WHERE entry_number=? AND timestamp >= ? AND timestamp < ? "
            "AND call_spread_value IS NOT NULL",
            (en, d + " 09:00", d + " 23:59")
        ).fetchall()

        if not snaps:
            print("%10s|%2d|%7.0f|  N/A  |%6.0f|%8.1f|%6.0f|     | NO DATA" % (
                d, en, trigger, debit, spx_stop, strike))
            continue

        peak_csv = max(r[0] for r in snaps)
        gap = peak_csv - trigger

        if peak_csv < trigger:
            verdict = "DISCREPANCY (peak < trigger by $%.0f)" % abs(gap)
            discrepancies += 1
        else:
            verdict = "OK (peak >= trigger)"

        print("%10s|%2d|%7.0f|%7.0f|%6.0f|%8.1f|%6.0f|%5.0f| %s" % (
            d, en, trigger, peak_csv, debit, spx_stop, strike, gap, verdict))

    print("\nTotal discrepancies (peak < trigger): %d / %d stops with data" % (
        discrepancies, sum(1 for s in stops if True)))

    # Check what the stop monitor ACTUALLY compares
    # Look at the stop code path in strategy.py
    print("\nPossible explanations for discrepancies:")
    print("  1. WebSocket real-time data spikes between ~10s snapshot intervals")
    print("  2. Stop uses ASK price (worst case), snapshots use MID price")
    print("  3. Stop monitors individual leg prices, not the spread composite")
    print("  4. Buffer decay (MKT-042) makes early stops trigger at HIGHER level")
    print("     (2.10x at entry, decaying to 1x over 2h)")

    # KEY CHECK: does MKT-042 buffer decay explain the discrepancy?
    # At entry time (10:15), buffer = $75 * 2.10 = $157.50
    # Trigger at entry = $245 + $157.50 = $402.50
    # After 2 hours (12:15), buffer decays to $75 * 1.0 = $75
    # Trigger at 12:15 = $245 + $75 = $320
    # After 3.8h (14:02), buffer = $75 * 1.0 = $75 (fully decayed)
    # Trigger at 14:02 = $245 + $75 = $320
    print("\n--- MKT-042 BUFFER DECAY CHECK (Apr 15 E#1) ---")
    print("  Entry: 10:15, Credit: $245, Call buffer: $0.75 ($75)")
    print("  Decay: 2.10x start, 2h linear to 1x")
    print("  At 10:15 (0h): buffer = $75 * 2.10 = $157.50, trigger = $402.50")
    print("  At 11:15 (1h): buffer = $75 * 1.55 = $116.25, trigger = $361.25")
    print("  At 12:15 (2h): buffer = $75 * 1.00 = $75.00,  trigger = $320.00")
    print("  At 14:02 (3.8h): buffer = $75 * 1.00 = $75.00, trigger = $320.00")
    print("  Peak CSV in snapshots: $158 (at 10:24)")
    print("  At 10:24, decay trigger was ~$398 -- peak $158 is way below")
    print("  BUFFER DECAY DOES NOT EXPLAIN THE DISCREPANCY")

    # Check if the stop value in the DB is actually the TOTAL spread (call+put)
    print("\n--- CHECK: Is trigger_level compared to TOTAL spread or just call? ---")
    for s in stops:
        d, en, trigger = s[0], s[1], s[2] or 0
        total_credit = s[7] or 0
        if d != "2026-04-15":
            continue
        # Get snapshot at stop time
        snaps_near = conn.execute(
            "SELECT timestamp, call_spread_value, put_spread_value "
            "FROM spread_snapshots "
            "WHERE entry_number=? AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp",
            (en, d + " 14:00", d + " 14:05")
        ).fetchall()
        print("  Apr 15 E#1 snapshots 14:00-14:05:")
        for sn in snaps_near:
            csv = sn[1] if sn[1] is not None else 0
            psv = sn[2] if sn[2] is not None else 0
            total = csv + psv
            print("    %s  csv=%.0f  psv=%.0f  total=%.0f  (trigger=%.0f)" % (
                sn[0], csv, psv, total, trigger))

    # ================================================================
    # INVESTIGATION 2: For each "saved" stop, did SPX later reach the strike?
    # ================================================================
    print("\n" + "=" * 75)
    print("INVESTIGATION 2: FOR EACH 'SAVED' STOP, DID SPX LATER REACH THE STRIKE?")
    print("If SPX touched the strike after the stop time, a wider buffer")
    print("would have meant holding INTO the breach = BIGGER loss.")
    print("=" * 75)

    for s in stops:
        d, en, trigger, debit, stop_time = s[0], s[1], s[2] or 0, s[3] or 0, s[4]
        total_credit = s[7] or 0
        strike = s[8] or 0

        # Check if this was a "saved" stop (peak < credit + 175)
        snaps = conn.execute(
            "SELECT call_spread_value FROM spread_snapshots "
            "WHERE entry_number=? AND timestamp >= ? AND timestamp < ? "
            "AND call_spread_value IS NOT NULL",
            (en, d + " 09:00", d + " 23:59")
        ).fetchall()
        if not snaps:
            continue
        peak_csv = max(r[0] for r in snaps)
        if peak_csv >= total_credit + 175:
            continue  # Not a "saved" stop

        # This stop WOULD have been saved by $175 buffer
        # Did SPX reach the strike AFTER the stop time?
        spx_high_after = conn.execute(
            "SELECT MAX(high) FROM market_ohlc_1min "
            "WHERE timestamp >= ? AND timestamp < ?",
            (d + " " + (stop_time or "14:00"), d + " 23:59")
        ).fetchone()
        spx_high_day = conn.execute(
            "SELECT MAX(high) FROM market_ohlc_1min "
            "WHERE timestamp >= ? AND timestamp < ?",
            (d + " 09:30", d + " 23:59")
        ).fetchone()

        high_after = spx_high_after[0] if spx_high_after and spx_high_after[0] else 0
        high_day = spx_high_day[0] if spx_high_day and spx_high_day[0] else 0
        dist_after = strike - high_after
        dist_day = strike - high_day

        if high_after >= strike:
            verdict = "DANGER - SPX reached strike AFTER stop (%.1fpt ITM)" % abs(dist_after)
        elif dist_after < 10:
            verdict = "CLOSE - SPX came within %.1fpt of strike after stop" % dist_after
        else:
            verdict = "SAFE - SPX stayed %.0fpt below strike" % dist_after

        print("\n  %s E#%d: strike=%.0f, stop_time=%s" % (d, en, strike, stop_time))
        print("    Peak CSV: $%.0f (trigger $175: $%.0f)" % (peak_csv, total_credit + 175))
        print("    SPX high AFTER stop: %.2f (%.1fpt from strike)" % (high_after, dist_after))
        print("    SPX day high: %.2f (%.1fpt from strike)" % (high_day, dist_day))
        print("    --> %s" % verdict)

    # Summary
    print("\n" + "=" * 75)
    print("SUMMARY: WOULD WIDER BUFFER HAVE HELPED OR HURT?")
    print("=" * 75)

    saved_safe = 0
    saved_danger = 0
    saved_close = 0

    for s in stops:
        d, en, trigger, debit = s[0], s[1], s[2] or 0, s[3] or 0
        total_credit, strike = s[7] or 0, s[8] or 0
        stop_time = s[4] or "14:00"

        snaps = conn.execute(
            "SELECT call_spread_value FROM spread_snapshots "
            "WHERE entry_number=? AND timestamp >= ? AND timestamp < ? "
            "AND call_spread_value IS NOT NULL",
            (en, d + " 09:00", d + " 23:59")
        ).fetchall()
        if not snaps:
            continue
        peak = max(r[0] for r in snaps)
        if peak >= total_credit + 175:
            continue

        high_after = conn.execute(
            "SELECT MAX(high) FROM market_ohlc_1min "
            "WHERE timestamp >= ? AND timestamp < ?",
            (d + " " + stop_time, d + " 23:59")
        ).fetchone()
        ha = high_after[0] if high_after and high_after[0] else 0

        if ha >= strike:
            saved_danger += 1
        elif strike - ha < 10:
            saved_close += 1
        else:
            saved_safe += 1

    print("\nOf the 13 stops that $175 buffer would 'save':")
    print("  SAFE (SPX stayed >10pt from strike): %d" % saved_safe)
    print("  CLOSE (SPX came within 10pt): %d" % saved_close)
    print("  DANGER (SPX actually reached the strike): %d" % saved_danger)
    print("\n  SAFE = wider buffer genuinely helps")
    print("  CLOSE = risky, could go either way")
    print("  DANGER = wider buffer would have INCREASED the loss")

if __name__ == "__main__":
    main()
