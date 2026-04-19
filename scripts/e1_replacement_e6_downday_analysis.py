#!/usr/bin/env python3
"""Two analyses:
1. Replacing E#1 with a new late entry (11:45) — do we have data on later entries?
2. E6 call-only on down days (currently only put-only on up days) — what would it have caught?
"""
import sqlite3
from collections import defaultdict

DB_PATH = "data/backtesting.db"
COMM = 5.0

def entry_pnl(e, stop_map):
    d, en = e[0], e[1]
    pnl = 0.0
    for side, credit in [("call", e[2] or 0), ("put", e[3] or 0)]:
        if credit <= 0:
            continue
        debit = stop_map.get((d, en, side))
        if debit is not None:
            pnl += (credit - debit - COMM - 2.5)
        else:
            pnl += (credit - COMM)
    return pnl

def main():
    conn = sqlite3.connect(DB_PATH)

    entries = conn.execute(
        "SELECT date, entry_number, call_credit, put_credit, entry_time, entry_type, "
        "override_reason, vix_at_entry FROM trade_entries WHERE date >= '2026-02-10'"
    ).fetchall()
    stops = conn.execute("SELECT date, entry_number, side, actual_debit FROM trade_stops").fetchall()
    stop_map = {(s[0], s[1], s[2]): s[3] or 0 for s in stops}

    # ======================================================
    # Q1: What do LATER entries historically look like?
    # Look at E#4, E#5 (historical 11:45, 12:15 slots)
    # ======================================================
    print("=" * 70)
    print("Q1: REPLACE E#1 WITH LATE ENTRY — what does historical data show?")
    print("=" * 70)

    # Group entries by hour-of-day to see what "11:45" historical data looks like
    late_entries = []  # entries that fired 11:30-12:30
    for e in entries:
        t = e[4]  # entry_time
        if not t:
            continue
        hhmm = t[11:16] if len(t) > 15 else ""
        if "11:30" <= hhmm <= "12:30":
            late_entries.append(e)

    print(f"\nHistorical entries placed between 11:30-12:30:")
    print(f"  Count: {len(late_entries)}")
    if late_entries:
        times = defaultdict(int)
        for e in late_entries:
            t = e[4][11:16]
            times[t] += 1
        for t in sorted(times.keys())[:10]:
            print(f"  {t}: {times[t]} entries")

    # P&L for late entries
    if late_entries:
        total = sum(entry_pnl(e, stop_map) for e in late_entries)
        stops_count = sum(1 for e in late_entries if
                           stop_map.get((e[0], e[1], "call")) is not None or
                           stop_map.get((e[0], e[1], "put")) is not None)
        print(f"\n  Total P&L: ${total:.2f}")
        print(f"  Avg P&L/entry: ${total/len(late_entries):.2f}")
        print(f"  Stops: {stops_count}/{len(late_entries)} ({100*stops_count/len(late_entries):.0f}%)")

    # Compare to current E#2 and E#3
    e2 = [e for e in entries if e[1] == 2]
    e3 = [e for e in entries if e[1] == 3]
    e2_pnl = sum(entry_pnl(e, stop_map) for e in e2)
    e3_pnl = sum(entry_pnl(e, stop_map) for e in e3)
    print(f"\nFor comparison:")
    print(f"  E#2 (10:45): ${e2_pnl:.2f} total over {len(e2)} entries (${e2_pnl/len(e2):.2f}/entry)")
    print(f"  E#3 (11:15): ${e3_pnl:.2f} total over {len(e3)} entries (${e3_pnl/len(e3):.2f}/entry)")

    # ======================================================
    # Q2: E6 down-day call-only — what would it have caught?
    # ======================================================
    print("\n" + "=" * 70)
    print("Q2: E6 CALL-ONLY ON DOWN DAYS — would it be profitable?")
    print("=" * 70)

    # Get SPX data at 14:00 for each day and check down-day vs up-day
    # SPX at 14:00 vs SPX at market open
    # Threshold: -0.25% (mirror of up-day)

    days = defaultdict(dict)
    for e in entries:
        days[e[0]]["has_entry"] = True

    # For each day, check SPX at 9:30 (open) and 14:00
    # and see if it would have been a down day
    trading_days = list(days.keys())
    print(f"\nChecking SPX direction at 14:00 across {len(trading_days)} trading days...")

    down_days = []
    up_days = []
    flat_days = []
    for d in trading_days:
        # Get SPX open
        open_row = conn.execute(
            "SELECT open FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '09:30' ORDER BY timestamp LIMIT 1",
            (d,)
        ).fetchone()
        # Get SPX at 14:00
        afternoon_row = conn.execute(
            "SELECT close FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '14:00' ORDER BY timestamp LIMIT 1",
            (d,)
        ).fetchone()
        if not open_row or not afternoon_row:
            continue
        open_px = open_row[0]
        px_1400 = afternoon_row[0]
        if not open_px or not px_1400:
            continue
        pct = (px_1400 - open_px) / open_px
        if pct <= -0.0025:
            down_days.append((d, open_px, px_1400, pct))
        elif pct >= 0.0025:
            up_days.append((d, open_px, px_1400, pct))
        else:
            flat_days.append((d, open_px, px_1400, pct))

    print(f"\n  Up days at 14:00 (SPX >= +0.25%): {len(up_days)} ({100*len(up_days)/(len(up_days)+len(down_days)+len(flat_days)):.0f}%)")
    print(f"  Down days at 14:00 (SPX <= -0.25%): {len(down_days)} ({100*len(down_days)/(len(up_days)+len(down_days)+len(flat_days)):.0f}%)")
    print(f"  Flat days: {len(flat_days)}")

    # For each down day, what would a call-only E6 at 14:00 have looked like?
    # Simulation: assume we would have placed a call spread at ~55pt OTM (typical)
    # Then check if SPX rallied back up past the short call strike by close

    print(f"\n  DOWN DAYS AT 14:00 — would E6 call-only have worked?")
    print(f"  {'Date':>12} | {'Open':>8} | {'14:00':>8} | {'14:00%':>6} | {'Close':>8} | {'Strike*':>8} | {'Result':>10}")

    wins = 0
    losses = 0
    ambiguous = 0
    for d, open_px, px_1400, pct in down_days[:20]:  # show first 20
        # Get close
        close_row = conn.execute(
            "SELECT close FROM market_ohlc_1min WHERE date(timestamp)=? AND time(timestamp) >= '16:00' ORDER BY timestamp DESC LIMIT 1",
            (d,)
        ).fetchone()
        if not close_row:
            close_row = conn.execute(
                "SELECT close FROM market_ohlc_1min WHERE date(timestamp)=? ORDER BY timestamp DESC LIMIT 1",
                (d,)
            ).fetchone()
        close_px = close_row[0] if close_row else px_1400

        # Simulated short call strike: ~55pt above SPX at 14:00 (typical OTM for 2h-to-expiry)
        sim_strike = round((px_1400 + 55) / 5) * 5

        # Did SPX end below the strike? That's a win for a call-only entry
        if close_px < sim_strike - 5:
            result = "WIN"
            wins += 1
        elif close_px >= sim_strike:
            result = "STOP"
            losses += 1
        else:
            result = "CLOSE"
            ambiguous += 1

        print(f"  {d} | {open_px:>7.2f} | {px_1400:>7.2f} | {pct*100:>+5.2f}% | {close_px:>7.2f} | {sim_strike:>7.0f} | {result:>10}")

    print(f"\n  Simulation summary (55pt OTM call strike hypothesis):")
    print(f"  Wins: {wins}, Stops: {losses}, Close calls: {ambiguous}")
    if wins + losses + ambiguous > 0:
        print(f"  Win rate: {100*wins/(wins+losses+ambiguous):.0f}%")

    # Also check the current MKT-035 (base-entry downday call-only) performance
    # These are E#1/E#2/E#3 that converted to call-only on down days
    downday_calls = [e for e in entries if e[6] and "downday" in str(e[6]).lower() and "base" in str(e[6]).lower()]
    print(f"\n  For comparison: MKT-035 Base-Downday call-only entries (E#1-E#3 on down days):")
    if downday_calls:
        total = sum(entry_pnl(e, stop_map) for e in downday_calls)
        print(f"  Count: {len(downday_calls)}, Total P&L: ${total:.2f}")
        print(f"  Avg: ${total/len(downday_calls):.2f}/entry")

if __name__ == "__main__":
    main()
