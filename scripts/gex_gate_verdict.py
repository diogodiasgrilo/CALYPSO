#!/usr/bin/env python3
"""The registered verdict for PREREG_GEX_GATE_2026_09_29 — does the GEX gate earn its keep?

Written 2026-10-09 while the answer was still UNKNOWABLE (n = 24 of 25). That
timing is the point: an analysis authored after seeing the numbers can be
tuned, however honestly, until it agrees with whatever one already believes.
This file exists so the arithmetic is fixed before the data arrives.

THE REGISTERED TEST (docs/PREREG_GEX_GATE_2026_09_29.md — that file governs; if
this script and it ever disagree, the document wins and this is the bug)

    population   gex_decisions, consumer='adjuster', live_action in SKIP/KEEP
    window       date >= 2026-09-30  (the 2026-09-29 cutoff is EXCLUSIVE —
                 everything on or before it formed the hypothesis)
    statistic    breach rate of GEX-VETOED strikes
    benchmark    breach rate of KEPT strikes in the same window, restricted to
                 vetoed-distance +/- 10pt
    required n   25 vetoes
    DISABLE SKIP if   vetoed breach rate <= benchmark AND binomial p < 0.01
    KEEP the gate if  vetoed breach rate > benchmark, OR p >= 0.01 at n = 25
    on KEEP      the hypothesis is recorded FAILED. Not retried with a longer
                 window, a different arm, or a P&L statistic.

A breach is a property of the MARKET, not of our order flow: a strike either
was or was not touched that session. So decisions from a dry-run variant are
fully valid evidence here, which matters because variant `b` went dry-run on
2026-10-09 while still generating the decisions this test counts.

THREE GUARDS, because the prereg names exactly these failure modes

 1. NO VERDICT BELOW n = 25, and below it the script prints ONLY the accrual —
    never the breach rates. "Not permitted: stopping early on a favourable
    count" cannot be obeyed by someone who has already seen the count's
    direction. The blindness is enforced here rather than left to discipline.
 2. ONE VARIANT. The test was registered on `b`. Variant `bl` did not exist
    then, runs the same code, and logged 8 vetoes on its first day — pooling
    them would reach n = 25 immediately and would be a protocol change made
    after seeing data. `--pool` exists only so the deviation must be typed out,
    and it brands the output.
 3. NO P&L. The prereg chose breach rate deliberately: a veto's claim is "this
    strike is in danger", which is directly checkable, where a P&L test on ~29
    changed entries has a 95% CI over $400 wide and would authorise anything.
    This script cannot score dollars.

Usage:
    python -m scripts.gex_gate_verdict                 # variant b, the registered test
    python -m scripts.gex_gate_verdict --variant bl    # a separate, NON-registered look
    python -m scripts.gex_gate_verdict --pool b,bl     # protocol deviation, branded
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── the registered constants. Changing one invalidates the test. ─────────────
CUTOFF_EXCLUSIVE = "2026-09-29"   # decisions ON this date and earlier are in-sample
WINDOW_START = "2026-09-30"       # first evaluable date
REQUIRED_N = 25
ALPHA = 0.01
BENCHMARK_BAND_PT = 10.0          # "vetoed-distance +/- 10pt"
REGISTERED_VARIANT = "b"


def _db_for(root: str, variant: str) -> str:
    return (os.path.join(root, "data", "backtesting.db") if variant == "a"
            else os.path.join(root, "data", f"variant_{variant}", "backtesting.db"))


def _load_ohlc(con: sqlite3.Connection) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """date -> (high, low). A day with no row cannot be scored, and is dropped
    LOUDLY rather than silently treated as 'no breach' — which would bias the
    vetoed arm toward looking correct."""
    out = {}
    for d, hi, lo in con.execute(
            "SELECT date, spx_high, spx_low FROM daily_summaries WHERE date >= ?",
            (WINDOW_START,)):
        out[d] = (hi, lo)
    return out


def _decisions(con: sqlite3.Connection, variant: str) -> List[dict]:
    rows = []
    for (date, side, spot, strike, action, entry_no) in con.execute(
            "SELECT date, side, spot, reference_strike, UPPER(COALESCE(live_action,'')), entry_number "
            "FROM gex_decisions "
            "WHERE consumer = 'adjuster' AND UPPER(COALESCE(live_action,'')) IN ('SKIP','KEEP') "
            "  AND date >= ? "
            "  AND (variant IS NULL OR LOWER(variant) = ?) "
            "ORDER BY date, entry_number, side",
            (WINDOW_START, variant.lower())):
        s = (side or "").strip().lower()
        if s not in ("call", "put") or strike is None or spot is None:
            continue
        rows.append(dict(date=date, side=s, spot=float(spot), strike=float(strike),
                         action=action, entry_number=entry_no, variant=variant))
    return rows


def _breached(side: str, strike: float, hi: Optional[float], lo: Optional[float]) -> Optional[bool]:
    """Did the session touch the strike? None when the day cannot be scored."""
    if side == "call":
        return None if hi is None else bool(hi >= strike)
    return None if lo is None else bool(lo <= strike)


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p). Exact; no scipy dependency."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    return sum(math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i)) for i in range(0, k + 1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="/opt/calypso")
    ap.add_argument("--variant", default=REGISTERED_VARIANT,
                    help="the variant whose decisions to score (registered: b)")
    ap.add_argument("--pool", default="",
                    help="comma-separated variants to POOL. PROTOCOL DEVIATION — "
                         "the test was registered on one variant.")
    args = ap.parse_args()

    variants = [v.strip() for v in args.pool.split(",") if v.strip()] or [args.variant]
    pooled = len(variants) > 1
    off_protocol = pooled or (not pooled and variants[0] != REGISTERED_VARIANT)

    decisions: List[dict] = []
    ohlc: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for v in variants:
        db = _db_for(args.root, v)
        if not os.path.exists(db):
            print(f"  !! no database for variant {v}: {db}")
            return 2
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            decisions += _decisions(con, v)
            ohlc.update(_load_ohlc(con))
        finally:
            con.close()

    vetoed = [d for d in decisions if d["action"] == "SKIP"]
    kept = [d for d in decisions if d["action"] == "KEEP"]

    print("=" * 78)
    print("  REGISTERED VERDICT — PREREG_GEX_GATE_2026_09_29")
    print("=" * 78)
    print(f"  variant(s)        : {', '.join(variants)}")
    print(f"  window            : {WINDOW_START} .. (cutoff {CUTOFF_EXCLUSIVE} exclusive)")
    print(f"  vetoes (n)        : {len(vetoed)}   required: {REQUIRED_N}")
    print(f"  kept (pool)       : {len(kept)}")
    if off_protocol:
        print()
        print("  " + "!" * 70)
        if pooled:
            print("  !! POOLED ACROSS VARIANTS — this is a PROTOCOL DEVIATION.")
            print(f"  !! The test was registered on variant '{REGISTERED_VARIANT}' alone.")
        else:
            print(f"  !! variant '{variants[0]}' is NOT the registered variant "
                  f"('{REGISTERED_VARIANT}').")
        print("  !! Any verdict below is EXPLORATORY and does not settle the "
              "registered test.")
        print("  " + "!" * 70)

    # ── GUARD 1: blind below n. Print the accrual, never the direction. ──────
    if len(vetoed) < REQUIRED_N:
        print()
        print(f"  NO VERDICT. {len(vetoed)} of {REQUIRED_N} out-of-sample vetoes.")
        print("  Breach rates are deliberately NOT shown below the registered n:")
        print("  the prereg forbids stopping early on a favourable count, and that")
        print("  cannot be obeyed once the direction is known. Re-run when n >= "
              f"{REQUIRED_N}.")
        by_date: Dict[str, int] = {}
        for d in vetoed:
            by_date[d["date"]] = by_date.get(d["date"], 0) + 1
        print()
        print("  accrual by date:")
        for d in sorted(by_date):
            print(f"    {d}  {'#' * by_date[d]} ({by_date[d]})")
        return 0

    # ── scoring ─────────────────────────────────────────────────────────────
    def score(rows: List[dict]) -> Tuple[int, int, List[dict]]:
        n = b = 0
        unscorable = []
        for r in rows:
            hi, lo = ohlc.get(r["date"], (None, None))
            res = _breached(r["side"], r["strike"], hi, lo)
            if res is None:
                unscorable.append(r)
                continue
            n += 1
            b += 1 if res else 0
        return n, b, unscorable

    v_n, v_b, v_un = score(vetoed)

    # benchmark: KEPT strikes whose distance-from-spot is within +/-10pt of ANY
    # vetoed strike's distance. Distance-matching is the whole point — a filter
    # that only ever vetoes CLOSE strikes would otherwise be compared against
    # far ones, which breach less for reasons that have nothing to do with it.
    v_dists = sorted({abs(r["strike"] - r["spot"]) for r in vetoed})
    def matched(r: dict) -> bool:
        d = abs(r["strike"] - r["spot"])
        return any(abs(d - vd) <= BENCHMARK_BAND_PT for vd in v_dists)
    bench_rows = [r for r in kept if matched(r)]
    k_n, k_b, k_un = score(bench_rows)

    print()
    print(f"  vetoed    : {v_b}/{v_n} breached = {100.0 * v_b / v_n if v_n else 0:.1f}%")
    print(f"  benchmark : {k_b}/{k_n} breached = {100.0 * k_b / k_n if k_n else 0:.1f}%"
          f"   (KEPT, distance-matched +/-{BENCHMARK_BAND_PT:.0f}pt)")
    if v_un or k_un:
        print(f"  !! unscorable (no SPX OHLC for that date): "
              f"{len(v_un)} vetoed, {len(k_un)} benchmark — EXCLUDED, not assumed safe")

    if v_n < REQUIRED_N:
        print()
        print(f"  NO VERDICT: only {v_n} of {REQUIRED_N} vetoes could be scored "
              "(missing SPX OHLC).")
        return 0
    if k_n == 0:
        print()
        print("  NO VERDICT: the distance-matched benchmark is empty — nothing to "
              "compare against.")
        return 0

    p_bench = k_b / k_n
    v_rate = v_b / v_n
    p_value = _binom_cdf(v_b, v_n, p_bench)

    print()
    print(f"  H0        : vetoed strikes breach at the benchmark rate "
          f"({100.0 * p_bench:.1f}%)")
    print(f"  one-sided p (P[X <= {v_b}] | n={v_n}) = {p_value:.4f}   alpha = {ALPHA}")
    print()

    safer_or_equal = v_rate <= p_bench
    if safer_or_equal and p_value < ALPHA:
        print("  VERDICT: **DISABLE the SKIP action.**")
        print("  The vetoed strikes were not more dangerous than the ones the gate")
        print("  approved, at the registered significance. Per the prereg this")
        print("  changes ONLY the SKIP action — KEEP and SHIFT, the GEX breach exit,")
        print("  and one_sided_entries_enabled are all untouched.")
    else:
        print("  VERDICT: **KEEP the gate.** The hypothesis is recorded FAILED.")
        if not safer_or_equal:
            print(f"  Vetoed strikes breached MORE than the benchmark "
                  f"({100.0 * v_rate:.1f}% vs {100.0 * p_bench:.1f}%) — the gate's "
                  "claim held.")
        else:
            print(f"  Vetoed breach rate is lower but p = {p_value:.4f} >= {ALPHA}: "
                  "not separable from chance at the registered n.")
        print("  Per the prereg this is NOT retried with a longer window, a")
        print("  different arm, or a P&L statistic.")

    if off_protocol:
        print()
        print("  (Reminder: this run was off-protocol — see the banner above. It does")
        print("   not settle the registered test.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
