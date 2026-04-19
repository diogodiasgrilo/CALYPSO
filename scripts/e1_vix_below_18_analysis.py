#!/usr/bin/env python3
"""Analyze E#1 vs E#2/E#3 performance specifically at VIX < 18."""
import sqlite3

DB_PATH = "data/backtesting.db"
COMM = 5.0  # per side (2 legs)

def main():
    conn = sqlite3.connect(DB_PATH)

    entries = conn.execute(
        "SELECT date, entry_number, entry_type, call_credit, put_credit, total_credit, "
        "vix_at_entry, otm_distance_call, otm_distance_put "
        "FROM trade_entries WHERE date >= '2026-02-10'"
    ).fetchall()

    stops = conn.execute(
        "SELECT date, entry_number, side, actual_debit FROM trade_stops"
    ).fetchall()
    stop_map = {}
    for s in stops:
        stop_map[(s[0], s[1], s[2])] = s[3] or 0

    # Analyze by VIX regime and entry number
    # Use slot_number (canonical entry position 1/2/3) — entry_number field can drift
    # because dropped entries aren't placed. But since pre-2026-04-13 the schedule
    # was different, we approximate by entry_number.

    def analyze(filter_fn, label):
        filtered = [e for e in entries if filter_fn(e)]
        if not filtered:
            return
        print(f"\n=== {label} ({len(filtered)} entries) ===")
        print(f"{'Entry':>6} | {'N':>4} | {'Exp':>4} | {'Stop':>4} | {'WR':>5} | {'AvgCrd':>8} | {'AvgP/L':>9} | {'TotalP/L':>10}")
        print("-" * 75)

        for en in [1, 2, 3]:
            group = [e for e in filtered if e[1] == en]
            if not group:
                continue

            wins = losses = 0
            total_pnl = 0.0
            total_credit = 0.0
            for e in group:
                d = e[0]
                call_credit = e[3] or 0
                put_credit = e[4] or 0
                total_credit += (call_credit + put_credit)

                # Per-side outcome
                for side_label, credit, stop_side in [
                    ("call", call_credit, "call"),
                    ("put", put_credit, "put")
                ]:
                    if credit <= 0:
                        continue
                    debit = stop_map.get((d, en, stop_side))
                    if debit is not None:
                        losses += 1
                        total_pnl += (credit - debit - COMM - 2.5)  # stop = extra leg close
                    else:
                        wins += 1
                        total_pnl += (credit - COMM)

            total_sides = wins + losses
            if total_sides == 0:
                continue
            wr = 100 * wins / total_sides
            avg_cred = total_credit / len(group) if group else 0
            avg_pnl = total_pnl / len(group)
            tag = "+++" if total_pnl > 0 else "---"
            print(f"  E#{en} | {len(group):>4} | {wins:>4} | {losses:>4} | {wr:>4.0f}% | {avg_cred:>7.0f} | {avg_pnl:>8.2f} | {total_pnl:>9.2f} {tag}")

    # All data
    analyze(lambda e: True, "ALL ENTRIES")

    # VIX buckets
    analyze(lambda e: e[6] and e[6] < 18, "VIX < 18 (Regime 0)")
    analyze(lambda e: e[6] and 18 <= e[6] < 22, "VIX 18-22 (Regime 1)")
    analyze(lambda e: e[6] and 22 <= e[6] < 28, "VIX 22-28 (Regime 2)")
    analyze(lambda e: e[6] and e[6] >= 28, "VIX >= 28 (Regime 3)")

    # Recent period only (new config)
    analyze(lambda e: e[0] >= "2026-03-30" and e[6] and e[6] < 18, "VIX < 18 RECENT ONLY (Mar 30+)")

    # Summary question: at VIX<18, does dropping E#1 help?
    print("\n=== WOULD DROPPING E#1 AT VIX<18 HELP? ===")
    low_vix_entries = [e for e in entries if e[6] and e[6] < 18]
    e1 = [e for e in low_vix_entries if e[1] == 1]
    e23 = [e for e in low_vix_entries if e[1] in (2, 3)]

    def compute_pnl(group):
        total = 0.0
        for e in group:
            d, en = e[0], e[1]
            for side, credit in [("call", e[3] or 0), ("put", e[4] or 0)]:
                if credit <= 0:
                    continue
                debit = stop_map.get((d, en, side))
                if debit is not None:
                    total += (credit - debit - COMM - 2.5)
                else:
                    total += (credit - COMM)
        return total

    e1_pnl = compute_pnl(e1)
    e23_pnl = compute_pnl(e23)
    print(f"  E#1 at VIX<18: {len(e1)} entries, total P&L = ${e1_pnl:.2f} (${e1_pnl/len(e1):.2f}/entry)" if e1 else "  No E#1 data")
    print(f"  E#2+E#3 at VIX<18: {len(e23)} entries, total P&L = ${e23_pnl:.2f} (${e23_pnl/len(e23):.2f}/entry)" if e23 else "  No E#2/3 data")

    if e1 and e23:
        if e1_pnl < 0:
            print(f"  >>> Dropping E#1 at VIX<18 would have SAVED ${abs(e1_pnl):.2f} <<<")
        else:
            print(f"  >>> Dropping E#1 at VIX<18 would have LOST ${e1_pnl:.2f} of profit <<<")

if __name__ == "__main__":
    main()
