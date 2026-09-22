#!/usr/bin/env python3
"""Does B's narrow/tight configuration lose more than A's wide one on TREND days?

THE QUESTION. 2026-09-21 was a clean up-trend day (SPX +0.84% open->close) on
which B lost $441.17/contract while A lost $94.20/contract on the same tape.
That is a 4.7x gap, and it is the strongest live argument for changing B's strike
selection. But it is one day, so this pairs the two variants across the whole
live era and asks whether the gap is a property of trend days or of that day.

WHAT THIS CAN AND CANNOT ISOLATE -- read before quoting it
-----------------------------------------------------------
A and B differ in FIVE ways at once, not one:

  | | A | B |
  |---|---|---|
  | width          | ~75pt, MKT-027 dynamic  | 5pt narrow |
  | strike picker  | OTM multiplier          | 8-delta target (Polygon) |
  | stop           | credit + buffer         | A2 %-of-width (0.40) |
  | schedule       | 2 slots                 | 7-slot grid |
  | overlays       | none                    | Brandon TP / GEX adjuster |

So a gap between them measures the WHOLE configuration difference. It cannot
attribute the gap to width alone, and this script does not claim to. What it CAN
do is establish whether the difference is trend-conditional -- i.e. whether B
underperforms A specifically when the market moves directionally -- which is the
precondition for the width hypothesis being worth pursuing at all. If the gap is
flat across trend buckets, the 09-21 observation was about that day, not a
structural weakness, and no parameter change is warranted.

PAIRING. Only days where BOTH variants placed at least one entry are compared.
B skips days A trades (different credit thresholds and schedule), and an
unpaired mean would be a selection effect rather than a performance difference.
The unpaired figures are printed alongside, so the selection is visible.

NORMALISATION. Every figure is net P&L per contract, from each variant's own
daily_summaries (A runs 1c, B runs 7c). Commissions are included -- these are
settled net figures, not gross.

TREND DEFINITION. Fixed before looking at outcomes: open is the first SPX tick at
or after 09:30 ET, close the last at or before 16:00, and the day's trend is
|close - open| / open. Buckets are stated as constants below rather than tuned.

RESULT, FIRST RUN 2026-09-22 (27 paired live-era sessions)
----------------------------------------------------------
**The trend hypothesis is NOT supported.** The gap is directionally there but
nowhere near significant, and the trend bucket contains a sign flip:

    bucket              n    B - A mean    B - A median
    chop  <0.30%       15       -49.89          -49.80
    mild  0.30-0.60%    8        -7.19          -13.55
    TREND >=0.60%       4      -257.95         -328.86   <- 3 of 4 negative
                                                            sign test p = 0.625

n=4 with one day (2026-08-04, +1.35%) favouring B. Nothing can be concluded from
that, and 2026-09-21 is one of the four -- so the observation that prompted this
is inside the sample it would be used to justify.

What IS there is broader and different: across ALL 27 paired days A beats B on
19, two-sided sign test **p = 0.052**, paired mean **-$68.07/contract**. That is
a whole-configuration difference, not a trend-conditional one, and it cannot be
attributed to width for the five-way-confound reason above.

And it is not one-directional. B's stop bounds its tail far more tightly:

    worst three sessions, per contract
    A (credit+buffer, 75pt) : -1023.60, -961.30, -673.80
    B (A2 %-of-width, 5pt)  :  -441.17,  -256.19,  -250.86

A wins on central tendency; B's tail is ~2.3x smaller. That is a different risk
profile rather than a worse one, which is much of the point of running both.

**Conclusion: no parameter change is justified by trend-day evidence.** Revisit
when the >=0.60% bucket reaches n>=15, which at ~4 per 27 sessions is roughly
100 sessions away.

USAGE (on the VM, as calypso)
    .venv/bin/python -m scripts.analyze_trend_day_width
    .venv/bin/python -m scripts.analyze_trend_day_width --since 2026-07-24
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics as st
from collections import defaultdict

# Pre-registered, not tuned to the outcome.
CHOP, MILD = 0.30, 0.60          # percent of open
LIVE_ERA = "2026-07-24"          # the B<->C seat swap; earlier is a different regime

A_DB = "/opt/calypso/data/backtesting.db"
B_DB = "/opt/calypso/data/variant_b/backtesting.db"


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def day_trend(con: sqlite3.Connection, date: str):
    """(open, close, signed pct) from the tick series, or None if not covered."""
    row = con.execute(
        "SELECT spx_price FROM market_ticks WHERE timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC LIMIT 1", (f"{date} 09:30:00", f"{date} 16:00:00")).fetchone()
    if not row or not row[0]:
        return None
    op = row[0]
    row = con.execute(
        "SELECT spx_price FROM market_ticks WHERE timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1", (f"{date} 09:30:00", f"{date} 16:00:00")).fetchone()
    if not row or not row[0]:
        return None
    cl = row[0]
    return op, cl, (cl - op) / op * 100.0


def per_contract(con: sqlite3.Connection, since: str) -> dict:
    """date -> net P&L per contract, for sessions that actually placed an entry."""
    out = {}
    for d, net, cpe in con.execute(
            "SELECT date, net_pnl, contracts_per_entry FROM daily_summaries "
            "WHERE date >= ? AND entries_placed > 0", (since,)):
        if net is None:
            continue
        out[d] = net / (cpe or 1)
    return out


def _stat(label: str, xs):
    if not xs:
        print(f"  {label:<26} (none)")
        return
    print(f"  {label:<26} n={len(xs):<3} mean={st.mean(xs):>9.2f}  "
          f"median={st.median(xs):>9.2f}  worst={min(xs):>9.2f}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=LIVE_ERA)
    p.add_argument("--a-db", default=A_DB)
    p.add_argument("--b-db", default=B_DB)
    a = p.parse_args(argv)
    a_con, b_con = _ro(a.a_db), _ro(a.b_db)

    A, B = per_contract(a_con, a.since), per_contract(b_con, a.since)
    paired = sorted(set(A) & set(B))

    print("=" * 74)
    print("TREND-DAY COMPARISON — variant A (wide) vs variant B (narrow), per contract")
    print("=" * 74)
    print(f"\nlive era >= {a.since}")
    print(f"  A traded {len(A)} sessions · B traded {len(B)} sessions · "
          f"BOTH traded {len(paired)} (the paired set)")
    print(f"  A-only {len(set(A)-set(B))}  ·  B-only {len(set(B)-set(A))}   "
          f"(unpaired days: a selection effect, not a result)")

    buckets = defaultdict(list)
    rows = []
    for d in paired:
        t = day_trend(b_con, d)
        if t is None:
            continue
        _, _, pct = t
        mag = abs(pct)
        key = f"chop  |move| < {CHOP}%" if mag < CHOP else (
              f"mild  {CHOP}-{MILD}%" if mag < MILD else f"TREND |move| >= {MILD}%")
        buckets[key].append((A[d], B[d], pct, d))
        rows.append((d, pct, A[d], B[d]))

    print(f"\n{'date':<12}{'move%':>8}{'A /ctr':>11}{'B /ctr':>11}{'B - A':>11}")
    for d, pct, av, bv in rows:
        print(f"{d:<12}{pct:>8.2f}{av:>11.2f}{bv:>11.2f}{bv-av:>11.2f}")

    print("\n" + "-" * 74)
    print("BY TREND BUCKET (paired days only)")
    print("-" * 74)
    for key in sorted(buckets, key=lambda k: ("chop" not in k, "mild" not in k)):
        vals = buckets[key]
        print(f"\n{key}   [{len(vals)} paired session(s)]")
        _stat("A (wide) per contract", [v[0] for v in vals])
        _stat("B (narrow) per contract", [v[1] for v in vals])
        _stat("B - A (paired diff)", [v[1] - v[0] for v in vals])

    print("\n" + "=" * 74)
    print("READ THIS WITH THE NUMBERS")
    print("=" * 74)
    print("  A and B differ in width, strike picker, stop formula, schedule AND overlays.")
    print("  A gap here is the WHOLE configuration difference, never width alone.")
    print("  The question it answers is narrower: is the gap TREND-CONDITIONAL?")
    print("  If B - A is similar across buckets, 2026-09-21 was a day, not a structure,")
    print("  and no parameter change is justified by it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
