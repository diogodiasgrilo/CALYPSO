#!/usr/bin/env python3
"""MKT-035 Corrected Analysis: Only analyze FULL IC entries (both sides have credit > 0).

The original analysis included one-sided entries (cc=0 or pc=0) which made
call-only conversion look terrible — converting a put-only entry (cc=0) to
call-only gives $0 credit, a massive false penalty.
"""

import sqlite3

def main():
    db = sqlite3.connect("data/backtesting.db")
    db.row_factory = sqlite3.Row

    entries = db.execute("""
        SELECT e.date, e.entry_number, e.entry_time, e.spx_at_entry,
               e.total_credit, e.short_call_strike, e.short_put_strike,
               e.call_credit, e.put_credit
        FROM trade_entries e ORDER BY e.date, e.entry_number
    """).fetchall()

    stops = db.execute("""
        SELECT date, entry_number, side, actual_debit
        FROM trade_stops ORDER BY date, entry_number
    """).fetchall()

    days = db.execute("""
        SELECT date, spx_open, spx_close, net_pnl
        FROM daily_summaries ORDER BY date
    """).fetchall()

    open_prices = {r["date"]: r["spx_open"] for r in days}

    stop_lookup = {}
    for s in stops:
        key = (s["date"], s["entry_number"])
        if key not in stop_lookup:
            stop_lookup[key] = set()
        stop_lookup[key].add(s["side"].lower() if s["side"] else "")

    # Build entry data
    all_entries = []
    for e in entries:
        date = e["date"]
        spx_open = open_prices.get(date, 0)
        spx_at = e["spx_at_entry"] or 0
        cc = e["call_credit"] or 0
        pc = e["put_credit"] or 0
        tc = e["total_credit"] or 0

        if spx_open > 0 and spx_at > 0:
            change_pct = (spx_at - spx_open) / spx_open * 100
        else:
            change_pct = None

        key = (date, e["entry_number"])
        sides = stop_lookup.get(key, set())

        all_entries.append({
            "date": date,
            "entry_num": e["entry_number"],
            "spx": spx_at,
            "open": spx_open,
            "change_pct": change_pct,
            "put_stopped": "put" in sides,
            "call_stopped": "call" in sides,
            "call_credit": cc,
            "put_credit": pc,
            "total_credit": tc,
            "is_full_ic": cc > 0 and pc > 0,
        })

    valid = [e for e in all_entries if e["change_pct"] is not None]
    full_ic = [e for e in valid if e["is_full_ic"]]
    one_sided = [e for e in valid if not e["is_full_ic"]]

    print(f"=== {len(valid)} total entries, {len(full_ic)} full IC, {len(one_sided)} one-sided (excluded) ===")
    print()

    # Show excluded entries
    print(f"EXCLUDED (one-sided, cc=0 or pc=0):")
    for e in one_sided:
        print(f"  {e['date']} E#{e['entry_num']}: cc=${e['call_credit']:.0f} pc=${e['put_credit']:.0f} "
              f"{'PUT_ONLY' if e['call_credit']==0 else 'CALL_ONLY'}")
    print()

    # Week breakdown for full ICs only
    print("=" * 90)
    print("FULL IC ENTRIES — STOP RATES BY WEEK")
    print("=" * 90)
    from collections import defaultdict
    weeks = defaultdict(lambda: {"entries": 0, "ps": 0, "cs": 0, "pnl_ic": 0})
    for e in full_ic:
        d = e["date"]
        if d <= "2026-02-14": w = "Wk1 Feb10-14"
        elif d <= "2026-02-21": w = "Wk2 Feb17-21"
        elif d <= "2026-02-28": w = "Wk3 Feb24-28"
        elif d <= "2026-03-07": w = "Wk4 Mar03-07"
        else: w = "Wk5 Mar10+"
        weeks[w]["entries"] += 1
        if e["put_stopped"]: weeks[w]["ps"] += 1
        if e["call_stopped"]: weeks[w]["cs"] += 1

    for w in sorted(weeks.keys()):
        v = weeks[w]
        n = v["entries"]
        print(f"  {w}: {n} entries, {v['ps']} put stops ({v['ps']/n*100:.0f}%), "
              f"{v['cs']} call stops ({v['cs']/n*100:.0f}%)")

    # Per-entry detail for full ICs
    print()
    print("=" * 110)
    print("FULL IC ENTRIES DETAIL (cc>0 AND pc>0 only)")
    print("=" * 110)
    print(f"{'Date':<12} {'E#':<4} {'SPX':<9} {'Chg%':<8} {'CC':<6} {'PC':<6} {'TC':<6} {'PS':<5} {'CS':<5} {'IC_PnL':<8} {'CO_PnL':<8} {'Diff':<8}")
    print("-" * 110)

    stop_buffer = 10  # $0.10 * 100 = $10
    theo_put = 250    # $2.50 * 100 = $250

    for e in full_ic:
        cc = e["call_credit"]
        pc = e["put_credit"]
        tc = e["total_credit"]

        # Full IC P&L
        ic_pnl = tc
        if e["put_stopped"]:
            ic_pnl -= (tc + stop_buffer)
        if e["call_stopped"]:
            ic_pnl -= (tc + stop_buffer)

        # Call-only P&L (MKT-035 conversion)
        co_pnl = cc
        if e["call_stopped"]:
            co_pnl -= (cc + theo_put + stop_buffer)  # stop = cc + theo_put + buffer

        diff = co_pnl - ic_pnl
        ps = "YES" if e["put_stopped"] else ""
        cs = "YES" if e["call_stopped"] else ""

        print(f"{e['date']:<12} {e['entry_num']:<4} {e['spx']:<9.1f} {e['change_pct']:<+8.2f} "
              f"${cc:<5.0f} ${pc:<5.0f} ${tc:<5.0f} {ps:<5} {cs:<5} "
              f"${ic_pnl:<+7.0f} ${co_pnl:<+7.0f} ${diff:<+7.0f}")

    # Threshold analysis on FULL IC entries only
    print()
    print("=" * 120)
    print("THRESHOLD ANALYSIS (full IC entries ONLY — one-sided excluded)")
    print("=" * 120)

    baseline = 0
    for e in full_ic:
        tc = e["total_credit"]
        p = tc
        if e["put_stopped"]: p -= tc + stop_buffer
        if e["call_stopped"]: p -= tc + stop_buffer
        baseline += p

    print(f"\nBaseline (all full IC): ${baseline:.0f} across {len(full_ic)} entries\n")

    thresholds = [0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]

    print(f"{'Thresh':<8} {'Trig':<6} {'%':<6} {'PStps':<6} {'PStp%':<7} {'CStps':<6} {'CStp%':<7} "
          f"{'FP':<5} {'Prec%':<7} {'Total$':<10} {'vsBas':<10} {'$/Trig':<8}")
    print("-" * 105)

    for thresh in thresholds:
        total = 0
        n_trig = 0
        false_pos = 0
        put_stops_trig = 0
        call_stops_trig = 0

        for e in full_ic:
            cc = e["call_credit"]
            tc = e["total_credit"]

            if e["change_pct"] < -thresh:
                # Call-only
                n_trig += 1
                p = cc
                if e["call_stopped"]:
                    p -= (cc + theo_put + stop_buffer)
                    call_stops_trig += 1
                if e["put_stopped"]:
                    put_stops_trig += 1
                if not e["put_stopped"]:
                    false_pos += 1
                total += p
            else:
                # Full IC
                p = tc
                if e["put_stopped"]: p -= tc + stop_buffer
                if e["call_stopped"]: p -= tc + stop_buffer
                total += p

        vs_base = total - baseline
        precision = (n_trig - false_pos) / n_trig * 100 if n_trig > 0 else 0
        ps_pct = put_stops_trig / n_trig * 100 if n_trig > 0 else 0
        cs_pct = call_stops_trig / n_trig * 100 if n_trig > 0 else 0
        per_trig = vs_base / n_trig if n_trig > 0 else 0

        print(f"{thresh:<8.2f} {n_trig:<6} {n_trig/len(full_ic)*100:<6.1f} "
              f"{put_stops_trig:<6} {ps_pct:<7.1f} {call_stops_trig:<6} {cs_pct:<7.1f} "
              f"{false_pos:<5} {precision:<7.1f} ${total:<9.0f} ${vs_base:<+9.0f} ${per_trig:<+7.0f}")

    # What-if: different theoretical put values
    print()
    print("=" * 100)
    print("SENSITIVITY: WHAT IF THEORETICAL PUT CREDIT WAS DIFFERENT? (at 0.30% threshold)")
    print("=" * 100)
    print(f"\n{'TheoPut':<10} {'Total$':<10} {'vsBas':<10} {'Note'}")
    print("-" * 50)

    for tp in [0, 50, 100, 150, 200, 250, 300, 350, 400]:
        total = 0
        for e in full_ic:
            cc = e["call_credit"]
            tc = e["total_credit"]
            if e["change_pct"] < -0.30:
                p = cc
                if e["call_stopped"]:
                    p -= (cc + tp + stop_buffer)
                total += p
            else:
                p = tc
                if e["put_stopped"]: p -= tc + stop_buffer
                if e["call_stopped"]: p -= tc + stop_buffer
                total += p

        vs = total - baseline
        note = ""
        if tp == 0: note = "stop = cc + buffer only"
        elif tp == 250: note = "current ($2.50)"
        print(f"${tp/100:<9.2f} ${total:<9.0f} ${vs:<+9.0f} {note}")

    # What if we used 2x credit instead of theoretical put?
    print()
    print("=" * 100)
    print("ALTERNATIVE: 2x CREDIT STOP (Fix #40 pattern) instead of theoretical put")
    print("=" * 100)
    print(f"\n{'Thresh':<8} {'Total$(2x)':<12} {'vsBase(2x)':<12} {'Total$(theo)':<12} {'vsBase(theo)':<12}")
    print("-" * 60)

    for thresh in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]:
        total_2x = 0
        total_theo = 0
        for e in full_ic:
            cc = e["call_credit"]
            tc = e["total_credit"]
            if e["change_pct"] < -thresh:
                # 2x credit stop
                p2 = cc
                if e["call_stopped"]:
                    p2 -= (cc * 2 + stop_buffer)
                total_2x += p2

                # Theoretical put stop
                pt = cc
                if e["call_stopped"]:
                    pt -= (cc + theo_put + stop_buffer)
                total_theo += pt
            else:
                p = tc
                if e["put_stopped"]: p -= tc + stop_buffer
                if e["call_stopped"]: p -= tc + stop_buffer
                total_2x += p
                total_theo += p

        print(f"{thresh:<8.2f} ${total_2x:<11.0f} ${total_2x - baseline:<+11.0f} "
              f"${total_theo:<11.0f} ${total_theo - baseline:<+11.0f}")

    db.close()

if __name__ == "__main__":
    main()
