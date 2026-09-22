#!/usr/bin/env python3
"""Cross-variant scoreboard: total, per-contract, and how STEADY each curve is.

THE COMPARABILITY WARNING COMES FIRST, because the headline number is the most
misleading one in this repo:

  1. **Only variant B places real orders.** Every other variant is dry-run, so its
     P&L is SIMULATED and never pays entry slippage, partial fills or unwinds. B's
     own measured entry fill leak was ~38% of its net. A dry-run variant beating B
     by less than that margin is not beating it.
  2. **Different structures do not share a P&L axis.** `strategy_taxonomy` splits
     these into comparability groups on purpose -- a credit iron condor, a
     net-debit calendar and an undefined-risk naked strangle have different
     capital at risk per unit of P&L. Ranking across groups is presentation, not
     analysis, and is printed here only with the caveat attached.
  3. **Different timeframes.** B has traded since the 2026-07-24 seat swap; F and
     G only since 2026-08-27. A longer record is a harder test, so a short record
     with a good number is a weaker claim, not a stronger one.

NORMALISATION. Everything is per contract, from each variant's own
daily_summaries (`net_pnl / contracts_per_entry`), so B's 7c does not flatter it.
Figures are settled net, commissions included.

STEADINESS is what the question usually means, and it is not total P&L:
  * daily_sharpe -- mean/stdev of per-contract daily P&L, annualised at sqrt(252).
    Scale-free, so it survives the per-contract normalisation.
  * max_drawdown -- deepest peak-to-trough on the cumulative curve.
  * win_rate and worst_streak -- how often, and how long a bad run gets.
A high total with a deep drawdown is a worse instrument than a lower total that
never scares you, and the table is ordered to make that visible.

USAGE (on the VM, as calypso)
    .venv/bin/python -m scripts.compare_all_strategies
    .venv/bin/python -m scripts.compare_all_strategies --since 2026-07-24
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import statistics as st

DBS = {
    "A": "/opt/calypso/data/backtesting.db",
    "B": "/opt/calypso/data/variant_b/backtesting.db",
    "C": "/opt/calypso/data/variant_c/backtesting.db",
    "D": "/opt/calypso/data/variant_d/backtesting.db",
    "E": "/opt/calypso/data/variant_e/backtesting.db",
    "F": "/opt/calypso/data/variant_f/backtesting.db",
    "G": "/opt/calypso/data/variant_g/backtesting.db",
}
GROUP = {"A": "ic_0dte", "B": "ic_0dte", "C": "ic_0dte", "F": "ic_0dte",
         "D": "calendar_multiday", "E": "calendar_multiday", "G": "undefined_risk_0dte"}
STATUS = {"A": "dry-run shadow", "B": "** LIVE (real orders) **", "C": "dry-run shadow",
          "D": "dry-run locked", "E": "dry-run locked", "F": "dry-run locked",
          "G": "dry-run locked"}


def series(path: str, since: str):
    """[(date, per-contract net)] for sessions that actually placed an entry."""
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT date, net_pnl, contracts_per_entry FROM daily_summaries "
            "WHERE date >= ? AND entries_placed > 0 ORDER BY date", (since,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(d, n / (c or 1)) for d, n, c in rows if n is not None]


def metrics(xs):
    """Steadiness, not just total."""
    vals = [v for _, v in xs]
    if not vals:
        return None
    total = sum(vals)
    mean = st.mean(vals)
    sd = st.pstdev(vals) if len(vals) > 1 else 0.0
    sharpe = (mean / sd * (252 ** 0.5)) if sd > 0 else 0.0

    cum = peak = dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = min(dd, cum - peak)

    streak = worst = 0
    for v in vals:
        streak = streak + 1 if v < 0 else 0
        worst = max(worst, streak)

    return dict(n=len(vals), total=total, mean=mean, sd=sd, sharpe=sharpe,
                dd=dd, win=sum(1 for v in vals if v > 0) / len(vals) * 100,
                streak=worst, first=xs[0][0], last=xs[-1][0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-24")
    a = ap.parse_args(argv)

    print("=" * 108)
    print("CROSS-VARIANT SCOREBOARD — per contract, settled net".center(108))
    print("=" * 108)
    print("\n⚠  ONLY B PLACES REAL ORDERS. The rest are simulated and pay no slippage;")
    print("   B's own measured entry fill leak was ~38% of its net. Read every dry-run")
    print("   number as an upper bound, and never rank across comparability groups.\n")

    data = {k: metrics(series(p, a.since)) for k, p in DBS.items()}

    hdr = (f"{'':<3}{'group':<20}{'status':<26}{'n':>4}{'total':>10}{'mean':>9}"
           f"{'sd':>9}{'sharpe':>8}{'maxDD':>10}{'win%':>7}{'streak':>7}")
    for grp in ("ic_0dte", "calendar_multiday", "undefined_risk_0dte"):
        print("-" * 108)
        print(f"GROUP: {grp}")
        print("-" * 108)
        print(hdr)
        for k in sorted(DBS):
            if GROUP[k] != grp:
                continue
            m = data[k]
            if not m:
                print(f"{k:<3}{grp:<20}{STATUS[k]:<26}   (no traded sessions in window)")
                continue
            print(f"{k:<3}{grp:<20}{STATUS[k]:<26}{m['n']:>4}{m['total']:>10.0f}"
                  f"{m['mean']:>9.2f}{m['sd']:>9.2f}{m['sharpe']:>8.2f}"
                  f"{m['dd']:>10.0f}{m['win']:>7.0f}{m['streak']:>7}")
        print()

    print("=" * 108)
    print("STEADIEST (by daily Sharpe, within the credit-0DTE group only — the one")
    print("group with more than two members and a shared P&L axis)")
    print("=" * 108)
    ranked = sorted(((data[k]['sharpe'], k) for k in ("A", "B", "C", "F") if data.get(k)),
                    reverse=True)
    for sh, k in ranked:
        m = data[k]
        print(f"  {k}  sharpe {sh:>6.2f}   total {m['total']:>8.0f}/ctr   "
              f"maxDD {m['dd']:>7.0f}   n={m['n']:<3} {m['first']}..{m['last']}   {STATUS[k]}")
    print("\n  A short record is a weaker claim, not a stronger one — check n and the")
    print("  date range before preferring a high Sharpe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
