#!/usr/bin/env python3
"""Analyze E6 (Upday-035) performance and compare to hypothetical 2-contract
allocation across E#2 and E#3 instead."""
import sqlite3

DB_PATH = "data/backtesting.db"
COMM = 5.0  # per side

def main():
    conn = sqlite3.connect(DB_PATH)

    entries = conn.execute(
        "SELECT date, entry_number, entry_type, call_credit, put_credit, total_credit, "
        "vix_at_entry, override_reason "
        "FROM trade_entries WHERE date >= '2026-02-10'"
    ).fetchall()

    stops = conn.execute(
        "SELECT date, entry_number, side, actual_debit FROM trade_stops"
    ).fetchall()
    stop_map = {}
    for s in stops:
        stop_map[(s[0], s[1], s[2])] = s[3] or 0

    # ==========================================================
    # 1. E6 Performance — entries with "upday-035" override reason
    # ==========================================================
    print("=" * 65)
    print("E6 (UPDAY-035) PERFORMANCE")
    print("=" * 65)

    e6_entries = [e for e in entries
                   if e[7] and "upday" in str(e[7]).lower()]

    # Also catch legacy entry_number=6 or put_only at 14:00
    import re
    e6_extra = [e for e in entries
                if e[2] == "put_only" and e not in e6_entries]
    # Filter to ones placed around 14:00 ET — we don't have entry_time in this query
    # Just go with override_reason match
    print(f"E6 entries found (upday-035 trigger): {len(e6_entries)}")

    if not e6_entries:
        print("No E6 entries — Upday-035 has not triggered historically.")
        # Check put-only entries as proxy
        put_only = [e for e in entries if e[2] == "put_only"]
        print(f"\nAll put_only entries in history: {len(put_only)}")
        for e in put_only[:10]:
            print(f"  {e[0]} E#{e[1]} | credit={e[5]} | override={e[7]}")

    total_pnl = 0.0
    wins = losses = 0
    for e in e6_entries:
        d, en = e[0], e[1]
        put_credit = e[4] or 0
        debit = stop_map.get((d, en, "put"))
        if debit is not None:
            total_pnl += (put_credit - debit - COMM - 2.5)
            losses += 1
        else:
            total_pnl += (put_credit - COMM)
            wins += 1

    if e6_entries:
        print(f"\nE6 results:")
        print(f"  N = {len(e6_entries)}, wins = {wins}, stops = {losses}")
        print(f"  Win rate: {100*wins/len(e6_entries):.0f}%")
        print(f"  Total P&L: ${total_pnl:.2f}")
        print(f"  Avg P&L per entry: ${total_pnl/len(e6_entries):.2f}")

    # ==========================================================
    # 2. E#2 and E#3 as-is (what would doubling contracts do?)
    # ==========================================================
    print("\n" + "=" * 65)
    print("E#2 + E#3 HISTORICAL PERFORMANCE (with 1 contract)")
    print("=" * 65)

    for target in [2, 3]:
        group = [e for e in entries if e[1] == target]
        if not group:
            continue
        total_pnl = 0.0
        wins = losses = 0
        for e in group:
            d, en = e[0], e[1]
            for side, credit in [("call", e[3] or 0), ("put", e[4] or 0)]:
                if credit <= 0:
                    continue
                debit = stop_map.get((d, en, side))
                if debit is not None:
                    total_pnl += (credit - debit - COMM - 2.5)
                    losses += 1
                else:
                    total_pnl += (credit - COMM)
                    wins += 1

        total_sides = wins + losses
        wr = 100 * wins / total_sides if total_sides else 0
        print(f"\nE#{target}: {len(group)} entries, {wins} wins / {losses} stops ({wr:.0f}% WR)")
        print(f"  Total P&L: ${total_pnl:.2f} (${total_pnl/len(group):.2f}/entry)")
        print(f"  If 2 contracts: ~${2*total_pnl:.2f} (${2*total_pnl/len(group):.2f}/entry)")

    # ==========================================================
    # 3. E6 vs doubling E#2/E#3 — direct comparison
    # ==========================================================
    print("\n" + "=" * 65)
    print("TRADEOFF: Keep E6 vs Double E#2/E#3 margin")
    print("=" * 65)

    # Total E6 P&L
    e6_total = sum(
        ((e[4] or 0) - (stop_map.get((e[0], e[1], "put")) or 0) - COMM - 2.5)
        if stop_map.get((e[0], e[1], "put")) is not None
        else ((e[4] or 0) - COMM)
        for e in e6_entries
    )

    # Total E#2 P&L (as if doubled)
    e2_single = 0.0
    for e in [x for x in entries if x[1] == 2]:
        d, en = e[0], e[1]
        for side, credit in [("call", e[3] or 0), ("put", e[4] or 0)]:
            if credit <= 0:
                continue
            debit = stop_map.get((d, en, side))
            if debit is not None:
                e2_single += (credit - debit - COMM - 2.5)
            else:
                e2_single += (credit - COMM)

    e3_single = 0.0
    for e in [x for x in entries if x[1] == 3]:
        d, en = e[0], e[1]
        for side, credit in [("call", e[3] or 0), ("put", e[4] or 0)]:
            if credit <= 0:
                continue
            debit = stop_map.get((d, en, side))
            if debit is not None:
                e3_single += (credit - debit - COMM - 2.5)
            else:
                e3_single += (credit - COMM)

    print(f"\nE6 lifetime P&L (existing): ${e6_total:.2f}")
    print(f"E#2 lifetime P&L (1 contract): ${e2_single:.2f}")
    print(f"E#3 lifetime P&L (1 contract): ${e3_single:.2f}")
    print()
    print(f"If E#2 were 2 contracts: incremental gain = ${e2_single:.2f}")
    print(f"If E#3 were 2 contracts: incremental gain = ${e3_single:.2f}")
    print(f"Combined incremental (E#2 + E#3 doubled): ${e2_single + e3_single:.2f}")
    print()

    if e6_total + (e2_single + e3_single) > 0:
        diff = (e2_single + e3_single) - e6_total
        if diff > 0:
            print(f">>> Doubling E#2/E#3 would gain ${diff:.2f} vs keeping E6")
        else:
            print(f">>> Keeping E6 would gain ${abs(diff):.2f} vs doubling E#2/E#3")

if __name__ == "__main__":
    main()
