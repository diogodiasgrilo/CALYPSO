#!/usr/bin/env python3
"""What would fixed-OTM strike selection have done, against what we actually did?

`shadow_entries` has recorded, on every entry since 2026-05-05, the strikes a
fixed-OTM-per-regime rule WOULD have chosen beside the strikes live selection
actually chose. **569 rows across 96 days — and until 2026-09-30 nothing read
it.** `grep -rl shadow_entries scripts/` returned two migration tests. The
collection was built; the looking never was.

WHAT IS COMPARABLE, AND WHAT IS NOT. The table stores the shadow's *strikes*
but never the credit it would have collected — `actual_call_credit` is the
ACTUAL entry's credit, not the shadow's. So a shadow P&L cannot be computed
without inventing the premium, and this script does not try. It scores
**breach** instead: did the day's SPX range reach the short strike?

That is the same statistic `PREREG_GEX_GATE` chose, for the same reason. A
strike-selection rule's claim is *"this strike is far enough away"*, which is
directly checkable against the day's high and low and cannot be rescued by a
lucky premium. A P&L comparison here would be a reconstruction resting on an
assumed credit, and would authorise almost anything.

TWO POPULATIONS, and they answer different questions:

  A. **Live PLACED** (318 rows) — head to head. Both rules picked strikes;
     which sat further out and which got breached more?
  B. **Live SKIPPED** (251 rows) — counterfactual. Live declined, the shadow
     would have entered. Were those skips protecting us or costing us?

🔴 **(B) IS NOT A SUBSTITUTE FOR THE REGISTERED GEX TEST.** 133 of the skips
are GEX accel-zone / require-both-sides suppressions, and this data is entirely
**in-sample** — it is the history that formed the hypothesis.
`PREREG_GEX_GATE_2026_09_29.md` fixes a 2026-09-29 cut-off and forbids
"counting credit-gate skips as GEX vetoes". Section B is descriptive context,
never a verdict, and the prereg's out-of-sample count is the only thing that
may disable the gate.

    python -m scripts.analyze_shadow_entries --variant b
    python -m scripts.analyze_shadow_entries --variant b --since 2026-05-05
"""
from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys

LIVE_ERA = "2026-07-24"   # the B/C live-seat swap


def _rows(db: str, since: str):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("""
            SELECT s.*, d.spx_high, d.spx_low
            FROM shadow_entries s
            JOIN daily_summaries d ON d.date = s.date
            WHERE s.date >= ? AND d.spx_high IS NOT NULL AND d.spx_low IS NOT NULL
            ORDER BY s.date, s.entry_number
        """, (since,)).fetchall()
    finally:
        con.close()


def _breached(short_call, short_put, hi, lo):
    """(call_breached, put_breached) — None where that side has no strike."""
    c = (hi >= short_call) if (short_call and hi) else None
    p = (lo <= short_put) if (short_put and lo) else None
    return c, p


def _rate(hits, n):
    return f"{hits}/{n} = {100.0 * hits / n:5.1f}%" if n else "   n/a"


def _binom_p(k, n, p0):
    """P(X <= k) under Binomial(n, p0) — one-sided, for 'fewer breaches'."""
    if n == 0:
        return 1.0
    return sum(math.comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(k + 1))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="b")
    ap.add_argument("--root", default="/opt/calypso")
    ap.add_argument("--since", default=LIVE_ERA)
    a = ap.parse_args(argv)

    db = (os.path.join(a.root, "data", "backtesting.db") if a.variant in ("", "a")
          else os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db"))
    if not os.path.exists(db):
        print(f"no database at {db}")
        return 2

    rows = _rows(db, a.since)
    print(f"SHADOW STRIKE SELECTION — variant {a.variant.upper()}, since {a.since}")
    print("=" * 72)
    print(f"  rows with a scoreable day range: {len(rows)}")
    if not rows:
        return 0

    placed = [r for r in rows if r["actual_short_call_strike"] or r["actual_short_put_strike"]]
    skipped = [r for r in rows if not (r["actual_short_call_strike"] or r["actual_short_put_strike"])]

    # ---- A. head to head on entries both rules would have taken -------------
    print()
    print(f"A. LIVE PLACED — head to head  (n={len(placed)})")
    print("   Breach = the day's range reached the short strike.")
    sb = ab = sn = an = 0
    sd, ad = [], []
    for r in placed:
        hi, lo = r["spx_high"], r["spx_low"]
        for sk, ak, is_call in (
                (r["shadow_short_call_strike"], r["actual_short_call_strike"], True),
                (r["shadow_short_put_strike"], r["actual_short_put_strike"], False)):
            if not sk or not ak:
                continue
            spx = r["spx_at_entry"]
            s_hit = (hi >= sk) if is_call else (lo <= sk)
            a_hit = (hi >= ak) if is_call else (lo <= ak)
            sb += bool(s_hit); sn += 1
            ab += bool(a_hit); an += 1
            if spx:
                sd.append(abs(sk - spx)); ad.append(abs(ak - spx))
    print(f"     shadow (fixed-OTM)  breached {_rate(sb, sn)}"
          + (f"   median distance {sorted(sd)[len(sd)//2]:5.1f}pt" if sd else ""))
    print(f"     actual (live rule)  breached {_rate(ab, an)}"
          + (f"   median distance {sorted(ad)[len(ad)//2]:5.1f}pt" if ad else ""))
    if sn and an:
        if sb == ab:
            print("     -> identical breach counts; no separation on this statistic.")
        else:
            better = "shadow" if sb < ab else "actual"
            p = _binom_p(min(sb, ab), sn, max(sb, ab) / an) if an else 1.0
            print(f"     -> {better} breached less; one-sided binomial p = {p:.3f}")
            if p >= 0.05:
                print("        NOT significant — this is a difference, not a finding.")

    # ---- B. what the skips avoided, or cost --------------------------------
    print()
    print(f"B. LIVE SKIPPED — counterfactual  (n={len(skipped)})")
    print("   The shadow would have entered. Would those strikes have been breached?")
    kb = kn = 0
    by_reason = {}
    for r in skipped:
        hi, lo = r["spx_high"], r["spx_low"]
        c, p = _breached(r["shadow_short_call_strike"], r["shadow_short_put_strike"], hi, lo)
        hit = bool(c) or bool(p)
        kb += hit; kn += 1
        key = (r["skip_reason"] or "(none)")[:46]
        d = by_reason.setdefault(key, [0, 0])
        d[0] += hit; d[1] += 1
    print(f"     shadow strikes breached on skipped entries: {_rate(kb, kn)}")
    print()
    print("     by skip reason:")
    for reason, (h, n) in sorted(by_reason.items(), key=lambda kv: -kv[1][1])[:6]:
        print(f"       {_rate(h, n):>16}   {reason}")
    print()
    print("   🔴 IN-SAMPLE and NOT a verdict on the GEX gate. The registered test")
    print("      (PREREG_GEX_GATE_2026_09_29) counts only vetoes from 2026-09-30")
    print("      onward and forbids scoring credit-gate skips as GEX vetoes.")
    print()
    print("   No P&L is reported: the credit the shadow would have collected was")
    print("   never recorded, so any profit figure here would be invented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
