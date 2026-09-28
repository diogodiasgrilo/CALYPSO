#!/usr/bin/env python3
"""Is variant H (long gamma) a usable hedge for variant B (short gamma)?

THE QUESTION THIS ANSWERS, AND THE ONE IT REFUSES TO.

B sells premium: it is short gamma, long theta, and it loses on large
directional moves. H buys premium: long gamma, short theta, and it gains on
exactly those moves. So there is a real structural reason to think H could
offset B's worst sessions. On 2026-09-24 the relationship showed up in the
timing — H exited `profit_target_100` at 12:41:01, the same minute B's second
call spread stopped out for -$1,470.

What this script will NOT do is tell you to run H at some particular size
because it "feels" matched to B. P&L is LINEAR in contracts, so running H
bigger multiplies its daily series by a constant and changes the correlation
by exactly nothing. The hedge ratio is an OUTPUT of a regression on
per-contract P&L, not an input you assume. That is the whole point of doing
this arithmetic instead of picking 7.

THREE THINGS IT CHECKS THAT SIZING CANNOT FIX

1. COVERAGE. A hedge absent on the day you need it is not a hedge. H is
   event-gated (range expansion) and skips days — 2 of its first 4 eligible
   ones. This reports how often B traded and H did not.

2. TIMING. H exits on profit targets tuned for H's own P&L, not for covering
   B. On 2026-09-25 H closed at 10:04 for +$42; B's loss landed at 12:06. A
   hedge that profit-targets out in the morning does not hedge the afternoon.
   This reports H's exit time against B's stop times, per day.

3. SAMPLE SIZE. It refuses to print a hedge ratio below MIN_N paired days and
   says why. H currently has 2 observations, both winners — for a strategy
   that should lose small often and win big rarely, two straight wins is the
   least informative sample possible.

A CAVEAT NO ARITHMETIC HERE CAN REMOVE: H is dry-run, so its fills are
simulated, and H PAYS the spread on entry. Simulated mid-fills flatter a debit
strategy specifically (the sim-vs-live gap measured on B's credits was ~31%).
Treat every H number below as an optimistic bound.

Usage:
    .venv/bin/python -m scripts.hedge_analysis_h_vs_b [--since 2026-07-24]
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics as st
import sys
from pathlib import Path

MIN_N = 20          # paired days below which no ratio is reported
LIVE_ERA = "2026-07-24"


def _q(db: Path, sql, args=()):
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.cursor().execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def b_daily(root: Path, since: str):
    """B's NET daily P&L, per contract."""
    db = root / "data" / "variant_b" / "backtesting.db"
    rows = _q(db, "SELECT date, net_pnl, contracts_per_entry FROM daily_summaries "
                  "WHERE date>=? AND net_pnl IS NOT NULL ORDER BY date", (since,)) or []
    out = {}
    for d, pnl, k in rows:
        k = k or 7
        out[d] = pnl / k
    return out


def b_stop_times(root: Path, since: str):
    db = root / "data" / "variant_b" / "backtesting.db"
    cols = _q(db, "PRAGMA table_info(trade_stops)") or []
    names = [c[1] for c in cols]
    if not names:
        return {}
    tcol = next((c for c in names if "time" in c.lower()), None)
    pcol = next((c for c in names if "pnl" in c.lower()), None)
    if not tcol or not pcol:
        return {}
    rows = _q(db, f"SELECT date,{tcol},{pcol} FROM trade_stops WHERE date>=? "
                  f"ORDER BY date,{tcol}", (since,)) or []
    out = {}
    for d, t, p in rows:
        out.setdefault(d, []).append((str(t)[-8:], p))
    return out


def h_daily(root: Path, since: str):
    """H's NET daily P&L, per contract. An entry with no exit lost its debit."""
    db = root / "data" / "variant_h" / "long_strangle.db"
    ent = _q(db, "SELECT date,entry_number,total_debit,contracts FROM ls_entries "
                 "WHERE date>=? ORDER BY date", (since,))
    if ent is None:
        return {}, {}, []
    ex = _q(db, "SELECT date,entry_number,exit_time,exit_reason,realized_pnl,commissions "
                "FROM ls_exits WHERE date>=?", (since,)) or []
    exits = {(d, n): (t, r, pnl, com) for d, n, t, r, pnl, com in ex}
    daily, times = {}, {}
    for d, n, debit, k in ent:
        k = k or 1
        hit = exits.get((d, n))
        if hit:
            t, reason, pnl, com = hit
            net = (pnl or 0.0) - (com or 0.0)
            times.setdefault(d, []).append((str(t)[-8:], reason, net))
        else:
            net = -(debit or 0.0)          # expired worthless: lost the debit
            times.setdefault(d, []).append(("(no exit)", "expired?", net))
        daily[d] = daily.get(d, 0.0) + net / k
    skips = _q(db, "SELECT date,skip_time,skip_reason FROM ls_skipped WHERE date>=? "
                   "ORDER BY date", (since,)) or []
    return daily, times, skips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=LIVE_ERA)
    ap.add_argument("--root", default="/opt/calypso")
    a = ap.parse_args()
    root = Path(a.root)

    B = b_daily(root, a.since)
    H, htimes, hskips = h_daily(root, a.since)
    stops = b_stop_times(root, a.since)

    print(f"HEDGE ANALYSIS — H (long gamma, SPY) vs B (short gamma, SPX)")
    print(f"since {a.since}    B days={len(B)}    H days={len(H)}\n")
    if not H:
        print("H has no recorded trades yet — nothing to analyse. Let it run.")
        return 0

    # ---- 1. COVERAGE -------------------------------------------------------
    # H STARTED LATER THAN B, so "days B traded that H did not" over B's whole
    # history mostly measures H's age, not H's selectivity. Quoting that number
    # raw would be a loose inference dressed as a finding. Coverage is computed
    # inside H's OWN era — the first date H recorded anything, entry or skip —
    # and the pre-era days are reported separately as what they are.
    h_era = min(list(H) + [str(r[0]) for r in hskips]) if (H or hskips) else None
    both = sorted(set(B) & set(H))
    in_era = sorted(d for d in B if h_era and d >= h_era)
    unhedged_era = sorted(d for d in in_era if d not in H)
    pre_era = sorted(d for d in B if h_era and d < h_era)
    print("1. COVERAGE — a hedge absent on the day you need it is not a hedge")
    print(f"   H's data begins          : {h_era}")
    print(f"   B days BEFORE H existed  : {len(pre_era)}  (not H's fault — excluded below)")
    print(f"   B days INSIDE H's era    : {len(in_era)}")
    print(f"   ...of which BOTH traded  : {len(both)}")
    if in_era:
        print(f"   ...of which H was ABSENT : {len(unhedged_era)}"
              f"   ({100*len(unhedged_era)/len(in_era):.0f}% of eligible days unhedged)")
    for d in unhedged_era:
        print(f"       {d}  B {B[d]:+8.2f}/contract  — NO HEDGE ON")
    if pre_era:
        worst = sorted(((B[d], d) for d in pre_era))[:3]
        print("   B's worst days predating H (context only, NOT a coverage failure):")
        for pnl, d in worst:
            print(f"       {d}  B {pnl:+8.2f}/contract")
    if hskips:
        print(f"   H skip reasons ({len(hskips)}):")
        for d, t, why in hskips[-4:]:
            print(f"       {d} {str(t)[-8:]}  {str(why)[:78]}")

    # ---- 2. TIMING ---------------------------------------------------------
    print("\n2. TIMING — does H's payoff land inside B's risk window?")
    if not both:
        print("   no overlapping days yet")
    for d in both:
        st_list = stops.get(d, [])
        first_stop = st_list[0][0] if st_list else None
        for t, reason, net in htimes.get(d, []):
            verdict = "n/a (B had no stop)"
            if first_stop:
                verdict = ("COVERS  (H still open at B's first stop)"
                           if t >= first_stop else
                           "TOO EARLY (H closed before B's first stop)")
            print(f"   {d}  H exit {t} {reason:<18} {net:+8.2f}   "
                  f"B first stop {first_stop or '--':<8}  -> {verdict}")

    # ---- 3. THE RATIO ------------------------------------------------------
    print("\n3. HEDGE RATIO")
    n = len(both)
    if n < MIN_N:
        print(f"   REFUSING to report a ratio: {n} paired day(s), need >= {MIN_N}.")
        print("   With a sample this small any number would be noise dressed as")
        print("   an answer. H should lose small often and win big rarely, so a")
        print("   short run of winners is the LEAST informative sample possible.")
        print("   Re-run this as data accumulates — nothing else needs changing.")
        return 0

    xb = [B[d] for d in both]
    xh = [H[d] for d in both]
    mb, mh = st.mean(xb), st.mean(xh)
    cov = sum((p - mb) * (q - mh) for p, q in zip(xb, xh)) / (n - 1)
    vh, sb, sh = st.variance(xh), st.stdev(xb), st.stdev(xh)
    corr = cov / (sb * sh) if sb and sh else float("nan")
    print(f"   n={n}   corr(B,H) = {corr:+.3f}")
    if corr > -0.1:
        print("   -> NOT negatively correlated. H is not hedging B on this sample;")
        print("      sizing it up would just add variance. Stop here.")
    k_mv = -cov / vh if vh else float("nan")
    print(f"   minimum-variance ratio   : {k_mv:.2f} H-contracts per B-contract")
    print("   (this is the OUTPUT — it is what 'how big should H be' actually means)\n")
    print("   combined series at several ratios:")
    print(f"   {'ratio':>6} {'mean/day':>10} {'sd':>10} {'ann.Sharpe':>11} {'worst day':>11}")
    for k in (0.0, max(0.0, k_mv * 0.5), max(0.0, k_mv), max(0.0, k_mv * 1.5), 5.0, 10.0):
        comb = [p + k * q for p, q in zip(xb, xh)]
        m, s = st.mean(comb), (st.stdev(comb) if len(comb) > 1 else 0.0)
        sr = (m / s) * (252 ** 0.5) if s else float("nan")
        print(f"   {k:6.2f} {m:10.2f} {s:10.2f} {sr:11.2f} {min(comb):11.2f}")
    print("\n   Reminder: H is DRY-RUN and PAYS the spread on entry. Simulated")
    print("   mid-fills flatter a debit strategy, so every H figure above is an")
    print("   optimistic bound, not a forecast.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
