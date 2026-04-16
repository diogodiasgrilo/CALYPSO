#!/usr/bin/env python3
"""Analyze breakeven credit thresholds from actual HYDRA trading data.

Accounts for the tradeoff: higher credit = closer to ATM = more stops.
Uses actual outcomes to compute realized expected value at each credit floor.
"""
import sqlite3
import sys

DB_PATH = "data/backtesting.db"
COMM = 5.0  # dollars per side (2 legs x $2.50)

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    entries = conn.execute("""
        SELECT date, entry_number, entry_type, call_credit, put_credit, total_credit,
               otm_distance_call, otm_distance_put, vix_at_entry
        FROM trade_entries WHERE date >= '2026-02-10'
    """).fetchall()

    stops = conn.execute("SELECT date, entry_number, side, actual_debit FROM trade_stops").fetchall()
    stop_map = {}
    for s in stops:
        stop_map[(s["date"], s["entry_number"], s["side"])] = s["actual_debit"] or 0

    # Build per-side records
    sides = []
    for e in entries:
        for side_label, cred_key, otm_key, stop_side in [
            ("call", "call_credit", "otm_distance_call", "call"),
            ("put", "put_credit", "otm_distance_put", "put")
        ]:
            credit = e[cred_key]
            if not credit or credit <= 0:
                continue
            d, en = e["date"], e["entry_number"]
            debit = stop_map.get((d, en, stop_side))
            was_stopped = debit is not None
            if was_stopped:
                net = credit - debit - COMM
            else:
                net = credit - COMM
            sides.append({
                "date": d, "entry": en, "side": side_label,
                "credit": credit, "otm": e[otm_key] or 0,
                "stopped": was_stopped, "net": net,
                "vix": e["vix_at_entry"] or 0,
                "debit": debit if was_stopped else 0,
                "recent": d >= "2026-03-16"
            })

    # ANALYSIS 1: Sweep credit floors (all data)
    print("=" * 75)
    print("ANALYSIS 1: Realized EV at each credit floor (all data)")
    print("If we had BLOCKED all entries with credit < X, what would total P&L be?")
    print("=" * 75)

    for side_filter in ["call", "put", "both"]:
        pool = sides if side_filter == "both" else [s for s in sides if s["side"] == side_filter]
        print("\n%s (N=%d)" % (side_filter.upper(), len(pool)))
        print("%12s | %5s | %5s | %6s | %6s | %9s | %9s | %8s" % (
            "Min Credit", "N", "Kept%", "WR", "StopR", "Avg P/L", "Total", "AvgOTM"))
        print("-" * 80)

        for min_credit in [0, 25, 50, 75, 100, 125, 150, 175, 200, 250, 300]:
            kept = [s for s in pool if s["credit"] >= min_credit]
            if len(kept) < 5:
                continue
            expired = sum(1 for s in kept if not s["stopped"])
            stopped = sum(1 for s in kept if s["stopped"])
            total_net = sum(s["net"] for s in kept)
            avg_net = total_net / len(kept)
            wr = 100.0 * expired / len(kept)
            sr = 100.0 * stopped / len(kept)
            avg_otm = sum(s["otm"] for s in kept) / len(kept)
            kept_pct = 100.0 * len(kept) / len(pool)
            tag = "+++" if total_net > 0 else "---"
            print("%12s | %5d | %4.0f%% | %4.0f%%  | %4.0f%%  | %8.2f | %8.2f %s | %6.0fpt" % (
                ">=%d" % min_credit, len(kept), kept_pct, wr, sr, avg_net, total_net, tag, avg_otm))

    # ANALYSIS 2: Recent period only (Mar 16+)
    print("\n" + "=" * 75)
    print("ANALYSIS 2: RECENT PERIOD ONLY (Mar 16+ with current stop buffers)")
    print("=" * 75)

    for side_filter in ["call", "put", "both"]:
        pool = [s for s in sides if s["recent"]] if side_filter == "both" else [
            s for s in sides if s["side"] == side_filter and s["recent"]]
        print("\n%s (N=%d, Mar 16 - Apr 15)" % (side_filter.upper(), len(pool)))
        print("%12s | %5s | %5s | %6s | %6s | %9s | %9s | %8s" % (
            "Min Credit", "N", "Kept%", "WR", "StopR", "Avg P/L", "Total", "AvgOTM"))
        print("-" * 80)

        for min_credit in [0, 25, 50, 75, 100, 125, 150, 175, 200, 250, 300]:
            kept = [s for s in pool if s["credit"] >= min_credit]
            if len(kept) < 5:
                continue
            expired = sum(1 for s in kept if not s["stopped"])
            stopped = sum(1 for s in kept if s["stopped"])
            total_net = sum(s["net"] for s in kept)
            avg_net = total_net / len(kept)
            wr = 100.0 * expired / len(kept)
            sr = 100.0 * stopped / len(kept)
            avg_otm = sum(s["otm"] for s in kept) / len(kept)
            kept_pct = 100.0 * len(kept) / len(pool)
            tag = "+++" if total_net > 0 else "---"
            print("%12s | %5d | %4.0f%% | %4.0f%%  | %4.0f%%  | %8.2f | %8.2f %s | %6.0fpt" % (
                ">=%d" % min_credit, len(kept), kept_pct, wr, sr, avg_net, total_net, tag, avg_otm))

    # ANALYSIS 3: Credit vs OTM vs Stop Rate correlation
    print("\n" + "=" * 75)
    print("ANALYSIS 3: THE TRADEOFF — higher credit = closer OTM = more stops")
    print("=" * 75)

    for side_filter in ["call", "put"]:
        pool = [s for s in sides if s["side"] == side_filter and s["otm"] > 0]
        print("\n%s side:" % side_filter.upper())
        print("%12s | %5s | %8s | %6s | %9s" % ("Credit", "N", "AvgOTM", "StopR", "Avg P/L"))
        print("-" * 55)
        buckets = [(0, 50), (50, 100), (100, 150), (150, 200), (200, 300), (300, 600)]
        for lo, hi in buckets:
            bk = [s for s in pool if lo <= s["credit"] < hi]
            if len(bk) < 2:
                continue
            avg_otm = sum(s["otm"] for s in bk) / len(bk)
            sr = 100.0 * sum(1 for s in bk if s["stopped"]) / len(bk)
            avg_net = sum(s["net"] for s in bk) / len(bk)
            tag = "+++" if avg_net > 0 else "---"
            print("%12s | %5d | %6.0fpt  | %4.0f%%  | %8.2f %s" % (
                "%d-%d" % (lo, hi), len(bk), avg_otm, sr, avg_net, tag))

    # ANALYSIS 4: Marginal value — what does each credit bucket ADD
    print("\n" + "=" * 75)
    print("ANALYSIS 4: MARGINAL VALUE — P&L contributed by each credit band")
    print("(If this band is negative, blocking it improves total P&L)")
    print("=" * 75)

    for side_filter in ["call", "put"]:
        pool = [s for s in sides if s["side"] == side_filter]
        print("\n%s side:" % side_filter.upper())
        print("%12s | %5s | %6s | %9s | %9s | %s" % (
            "Credit Band", "N", "StopR", "Avg P/L", "Band Tot", "Verdict"))
        print("-" * 70)
        buckets = [(0, 25), (25, 50), (50, 75), (75, 100), (100, 125),
                   (125, 150), (150, 175), (175, 200), (200, 250), (250, 300), (300, 600)]
        for lo, hi in buckets:
            bk = [s for s in pool if lo <= s["credit"] < hi]
            if not bk:
                continue
            sr = 100.0 * sum(1 for s in bk if s["stopped"]) / len(bk)
            tot = sum(s["net"] for s in bk)
            avg = tot / len(bk)
            verdict = "KEEP" if tot > 0 else "BLOCK (saves $%.0f)" % abs(tot)
            print("%12s | %5d | %4.0f%%  | %8.2f | %8.2f | %s" % (
                "$%d-$%d" % (lo, hi), len(bk), sr, avg, tot, verdict))

    conn.close()

if __name__ == "__main__":
    main()
