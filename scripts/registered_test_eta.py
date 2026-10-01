#!/usr/bin/env python3
"""When do the registered out-of-sample tests actually complete?

"Wait for the out-of-sample data" was said repeatedly without anyone working
out when that data arrives. The answer is not uniform, and the spread changes
what the right decision is: a test two weeks out is worth waiting for
passively, one three months out deserves to be decided on rather than drifted
into.

Projects each registered test's remaining out-of-sample requirement against
the rate it has actually accrued at over B's live era. Rates are measured, not
assumed, and the projection is deliberately naive — it extends the observed
rate and says so, rather than modelling a trend it has no data to fit.

The accrual rate is itself the finding. B places ~2.08 entries/day across 69%
of days, and e#4 fires on 0.39/day, so a test needing 27 e#4 entries is a
quarter away while one needing 25 GEX vetoes is a fortnight. Same phrase,
"wait for the data", two very different decisions.

    python -m scripts.registered_test_eta --variant b
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

LIVE_ERA = "2026-07-24"      # the B/C live-seat swap
PREREG_CUTOFF = "2026-09-29"  # PREREG_GEX_GATE / PREREG_SLOT_PRUNE: later only


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="b")
    ap.add_argument("--root", default="/opt/calypso")
    a = ap.parse_args(argv)

    db = (os.path.join(a.root, "data", "backtesting.db") if a.variant in ("", "a")
          else os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db"))
    if not os.path.exists(db):
        print(f"no database at {db}")
        return 2
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    def one(q, args=()):
        try:
            return con.execute(q, args).fetchone()[0] or 0
        except Exception:
            return 0

    days = one("SELECT COUNT(*) FROM daily_summaries WHERE date>=?", (LIVE_ERA,))
    if not days:
        print("no live-era days recorded")
        return 0
    traded = one("SELECT COUNT(*) FROM daily_summaries WHERE date>=? AND entries_placed>0", (LIVE_ERA,))
    entries = one("SELECT COUNT(*) FROM trade_entries WHERE date>=?", (LIVE_ERA,))
    e4 = one("SELECT COUNT(*) FROM trade_entries WHERE date>=? AND entry_number=4", (LIVE_ERA,))
    vetoes = one("SELECT COUNT(*) FROM gex_decisions WHERE live_action='SKIP'")
    gex_days = one("SELECT COUNT(DISTINCT substr(timestamp,1,10)) FROM gex_decisions")
    gex_reach = one("SELECT COUNT(*) FROM (SELECT 1 FROM daily_summaries ds WHERE ds.date>=? "
                    "AND EXISTS (SELECT 1 FROM gex_decisions gx "
                    "WHERE substr(gx.timestamp,1,10)=ds.date))", (LIVE_ERA,))

    print(f"ACCRUAL RATES — variant {a.variant.upper()}, live era since {LIVE_ERA}")
    print("=" * 74)
    print(f"  days of record         : {days}")
    print(f"  traded days            : {traded}  ({100.0*traded/days:.0f}%)")
    print(f"  entries placed         : {entries:<5} -> {entries/days:.2f}/day")
    print(f"  e#4 entries            : {e4:<5} -> {e4/days:.2f}/day")
    print(f"  GEX vetoes (SKIP)      : {vetoes:<5} over {gex_days} recorded days"
          f" -> {vetoes/max(gex_days,1):.2f}/day")
    print(f"  days reaching the GEX adjuster at all : {gex_reach}/{days}"
          f"  ({100.0*gex_reach/days:.0f}%)")
    print()

    oos_v = one("SELECT COUNT(*) FROM gex_decisions WHERE live_action='SKIP' "
                "AND substr(timestamp,1,10) > ?", (PREREG_CUTOFF,))
    oos_e4 = one("SELECT COUNT(*) FROM trade_entries WHERE entry_number=4 AND date > ?",
                 (PREREG_CUTOFF,))
    oos_d = one("SELECT COUNT(*) FROM daily_summaries WHERE date > ? AND entries_placed>0",
                (PREREG_CUTOFF,))

    tests = [
        ("GEX gate", oos_v, 25, vetoes / max(gex_days, 1),
         "docs/PREREG_GEX_GATE_2026_09_29.md"),
        ("e#4 slot prune", oos_e4, 27, e4 / days,
         "docs/PREREG_SLOT_PRUNE_2026_09_29.md"),
        ("one-entry-a-day", oos_d, 40, traded / days,
         "docs/PREREG_SLOT_PRUNE_2026_09_29.md"),
    ]
    print(f"OUT-OF-SAMPLE PROGRESS — only data after {PREREG_CUTOFF} counts")
    print(f"  {'test':<18}{'have':>5}{'need':>6}{'rate/day':>10}{'days left':>11}{'~weeks':>8}")
    print("  " + "-" * 60)
    for name, have, need, rate, _doc in tests:
        rem = max(0, need - have)
        if rem == 0:
            print(f"  {name:<18}{have:>5}{need:>6}{rate:>10.2f}{'READY':>11}{'-':>8}")
        elif rate <= 0:
            print(f"  {name:<18}{have:>5}{need:>6}{rate:>10.2f}{'never@0':>11}{'-':>8}")
        else:
            d = rem / rate
            print(f"  {name:<18}{have:>5}{need:>6}{rate:>10.2f}{d:>11.0f}{d/5:>8.1f}")
    print()
    print("  Naive projection: it extends the OBSERVED rate and models no trend,")
    print("  because there is no basis for fitting one. Treat it as an order of")
    print("  magnitude — 'a fortnight' vs 'a quarter' is the decision-relevant")
    print("  distinction, not the exact day.")
    print()
    print("  The accrual rate is the binding constraint, not the thresholds: every")
    print("  skipped entry is a day added to all three. Anything that raises B's")
    print("  placement rate shortens all of them at once.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
