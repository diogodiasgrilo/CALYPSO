#!/usr/bin/env python3
"""What B's record looks like stripped down to ONE entry a day, Brandon-style.

FOUR QUESTIONS, ANSWERED IN ORDER — each one has to be right before the next
means anything.

  1. DECOMPOSITION. What is B's live-era P&L actually made of? The headline
     net mixes three very different things: per-entry trading results,
     commissions, and `unattributed_overlay_pnl` — a badly-named column that
     is NOT hedge P&L (every non-zero day postdates the 2026-09-04 hedge
     disable) but the failed-entry unwind residual, dominated by a single
     +$5,995 windfall on 2026-09-24.

  2. HEDGES. What the defensive overlay actually cost, from its own recorder.

  3. ONE-ENTRY-A-DAY. Keep only the earliest entry each day — the closest
     thing B has to Brandon's single 9:31 open — and sum its realized P&L.
     Then rescale to the contract counts he implies.

  4. WHICH SLOT IS BEST. Per-slot realized P&L across the grid. He has no
     data on this at all: he trades one slot, so the question cannot arise
     for him. It is the one place our record knows something his does not.

HOW P&L IS ATTRIBUTED. `trade_entries.realized_pnl` is populated for 101 of
101 live-era entries and sums exactly to `daily_summaries.gross_pnl` minus
`unattributed_overlay_pnl`, so it is the per-entry GROSS result. Commission is
only recorded per DAY, so it is apportioned across that day's entries by LEG
COUNT — 4 legs to open, plus 2 more for each side that stopped — which is what
commission actually scales with. That is an apportionment, not a measurement,
and the report prints gross alongside so its effect is visible.

⚠️ THE SCALING CAVEAT, which no arithmetic here can remove. Rescaling a
7-contract result to 11-19 contracts assumes P&L is linear in size. It is not:
larger orders fill worse, and B's own fill-leak work measured a ~$60/entry
swing from pricing policy alone at 7 contracts. Treat every scaled figure as
an OPTIMISTIC upper bound, not a forecast.

Usage:
    .venv/bin/python -m scripts.one_entry_counterfactual
    .venv/bin/python -m scripts.one_entry_counterfactual --variant c
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

LIVE_ERA = "2026-07-24"


def load(db: Path, since: str):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    days = con.execute(
        "SELECT date,gross_pnl,net_pnl,commission,unattributed_overlay_pnl "
        "FROM daily_summaries WHERE date>=? ORDER BY date", (since,)).fetchall()
    entries = con.execute(
        "SELECT date,entry_number,entry_time,realized_pnl,contracts,total_credit "
        "FROM trade_entries WHERE date>=? ORDER BY date,entry_time", (since,)).fetchall()
    stops = con.execute(
        "SELECT date,entry_number,COUNT(*) FROM trade_stops WHERE date>=? "
        "GROUP BY date,entry_number", (since,)).fetchall()
    con.close()
    return days, entries, {(d, e): n for d, e, n in stops}


def apportion_commission(days, entries, stops):
    """Split each day's commission across its entries by LEG COUNT."""
    by_day = defaultdict(list)
    for e in entries:
        by_day[e[0]].append(e)
    daily_comm = {d: (c or 0.0) for d, _g, _n, c, _o in days}
    out = {}
    for d, ents in by_day.items():
        legs = {}
        for (dt, en, _t, _p, _k, _cr) in ents:
            legs[(dt, en)] = 4 + 2 * stops.get((dt, en), 0)
        total = sum(legs.values()) or 1
        comm = daily_comm.get(d, 0.0)
        for key, L in legs.items():
            out[key] = comm * L / total
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="b")
    ap.add_argument("--since", default=LIVE_ERA)
    ap.add_argument("--root", default="/opt/calypso")
    a = ap.parse_args()
    db = Path(a.root) / "data" / f"variant_{a.variant}" / "backtesting.db"
    if not db.exists():
        print(f"no such database: {db}")
        return 1

    days, entries, stops = load(db, a.since)
    comm = apportion_commission(days, entries, stops)

    g = sum((r[1] or 0) for r in days)
    n = sum((r[2] or 0) for r in days)
    c = sum((r[3] or 0) for r in days)
    ov = sum((r[4] or 0) for r in days)
    ent_gross = sum((e[3] or 0) for e in entries)

    print(f"variant {a.variant.upper()} — live era from {a.since}   "
          f"{len(days)} days, {len(entries)} entries\n")
    print("1. WHAT THE HEADLINE NET IS MADE OF")
    print(f"   per-entry realized (gross)        {ent_gross:>12,.2f}")
    print(f"   unattributed_overlay_pnl          {ov:>12,.2f}   <- NOT hedges")
    print(f"   ------------------------------------------------")
    print(f"   daily gross_pnl                   {g:>12,.2f}")
    print(f"   commission                        {-c:>12,.2f}")
    print(f"   ================================================")
    print(f"   REPORTED NET                      {n:>12,.2f}")
    print()
    print(f"   TRADING net, windfall removed     {ent_gross - c:>12,.2f}")
    print( "   (per-entry gross minus all commission — what the ENTRIES earned)")
    if c and g:
        print(f"   commission as a share of gross    {100*c/g:>11.1f}%")

    # ---- 2. hedges -------------------------------------------------------
    hdb = db.parent / "brandon_hedges.db"
    print("\n2. THE DEFENSIVE OVERLAY (hedges)")
    if hdb.exists():
        hc = sqlite3.connect(f"file:{hdb}?mode=ro", uri=True)
        try:
            rows = hc.execute(
                "SELECT date,structure,debit_paid,hedge_pnl FROM hedge_settlements "
                "ORDER BY date").fetchall()
        except sqlite3.OperationalError:
            rows = []
        hc.close()
        if rows:
            for d, s, deb, p in rows:
                print(f"   {d}  {s:<10} debit {deb:>9,.2f}   P&L {p:>+10,.2f}")
            print(f"   TOTAL hedge P&L {sum(r[3] for r in rows):>+27,.2f}")
            print(f"   (disabled 2026-09-04 — {sum(1 for d,_g,_n,_c,_o in days if d > '2026-09-04')}"
                  " of the days above already had NO hedge)")
        else:
            print("   no hedge settlements recorded")

    # ---- 3. one entry a day ---------------------------------------------
    by_day = defaultdict(list)
    for e in entries:
        by_day[e[0]].append(e)
    first, firstnums = [], defaultdict(int)
    for d, ents in sorted(by_day.items()):
        e = sorted(ents, key=lambda x: str(x[2]))[0]
        net = (e[3] or 0) - comm.get((e[0], e[1]), 0.0)
        first.append((d, e[1], e[4] or 7, e[3] or 0, net))
        firstnums[e[1]] += 1

    all_net = ent_gross - c
    one_net = sum(r[4] for r in first)
    print("\n3. ONE ENTRY A DAY — keep only the EARLIEST entry each day")
    print(f"   days with an entry                {len(first):>12}")
    print(f"   which slot that earliest entry was: "
          + ", ".join(f"e#{k}x{v}" for k, v in sorted(firstnums.items())))
    print(f"   ALL entries, net of commission    {all_net:>12,.2f}")
    print(f"   FIRST entry only, net             {one_net:>12,.2f}")
    print(f"   difference                        {one_net - all_net:>+12,.2f}")
    if first:
        w = sum(1 for r in first if r[4] > 0)
        print(f"   win rate                          {w}/{len(first)} "
              f"({100*w/len(first):.0f}%)   mean/day {st.mean(r[4] for r in first):>+8,.2f}")

    print("\n   scaled to Brandon's implied size (LINEAR — optimistic, see header):")
    base_k = st.median([r[2] for r in first]) or 7
    print(f"   {'contracts':>10} {'net':>14} {'vs all-entries':>16}")
    for k in (7, 11, 15, 19):
        scaled = one_net * k / base_k
        print(f"   {k:>10} {scaled:>14,.2f} {scaled - all_net:>+16,.2f}")

    # ---- 4. per-slot ranking --------------------------------------------
    print("\n4. WHICH SLOT ACTUALLY EARNS — the question his data cannot ask")
    slot = defaultdict(list)
    for e in entries:
        net = (e[3] or 0) - comm.get((e[0], e[1]), 0.0)
        slot[e[1]].append(net)
    print(f"   {'slot':>5} {'n':>4} {'total net':>13} {'mean':>10} {'win%':>7}")
    for s in sorted(slot):
        v = slot[s]
        wr = 100 * sum(1 for x in v if x > 0) / len(v)
        print(f"   e#{s:<3} {len(v):>4} {sum(v):>13,.2f} {st.mean(v):>10,.2f} {wr:>6.0f}%")
    best = max(slot.items(), key=lambda kv: sum(kv[1]))
    print(f"\n   best by total: e#{best[0]}  ({sum(best[1]):+,.2f} over {len(best[1])} entries)")
    print("   ⚠️ small n per slot — read the ranking as a hypothesis, not a result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
