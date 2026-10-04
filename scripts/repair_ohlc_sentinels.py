#!/usr/bin/env python3
"""Turn 0.0 OHLC sentinels in daily_summaries into NULLs.

`spx_open` / `spx_high` / `vix_open` were written RAW by
`_record_daily_summary_to_db`, so a day that captured nothing recorded its 0.0
SENTINEL as though it were a price. `spx_low` already had the discipline
(inf -> None) and `day_range` already guarded on it; these three were missed.

The dates say what happened: 2026-04-03, 05-25, 06-19, 07-03, 09-07 are Good
Friday, Memorial Day, Juneteenth, July-4th-observed and Labor Day. Market
holidays — no session, so nothing to capture. D and E carry many more because
they are multi-day and write a summary on days they do not trade.

A price of 0.0 is not a fact about any market. It makes `day_range` and every
"% from open" derived from it nonsense, and the dashboard and history charts
read these columns. NULL is the honest value.

SAFETY
  * Only ever rewrites a value that is EXACTLY 0 -> NULL. Never touches a
    non-zero number, never invents one, never deletes a row.
  * Every change is written to `data_corrections` with its evidence, using the
    ONE shared CORRECTIONS_DDL (restating it is how an earlier backfill failed:
    CREATE TABLE IF NOT EXISTS does not reconcile schemas, it declines).
  * --dry-run by default. Pass --apply to write.

Usage:
    python -m scripts.repair_ohlc_sentinels            # report only
    python -m scripts.repair_ohlc_sentinels --apply
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIELDS = ("spx_open", "spx_high", "vix_open")

VARIANTS = [("A", "data/backtesting.db")] + [
    (v.upper(), "data/variant_%s/backtesting.db" % v) for v in "bcdefgh"]

REASON = ("spx_open/spx_high/vix_open were written raw by "
          "_record_daily_summary_to_db, so a session that captured nothing "
          "recorded its 0.0 sentinel as a price. Mostly market holidays. "
          "Fixed forward 2026-10-04; a price of 0.0 is not a fact.")


def plan_for(db_path):
    """Rows needing repair, as (date, {field: old}). Read-only."""
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        cols = ", ".join(FIELDS)
        where = " OR ".join("%s = 0" % f for f in FIELDS)
        rows = con.execute(
            "SELECT date, %s FROM daily_summaries WHERE %s ORDER BY date"
            % (cols, where)).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        date, vals = r[0], r[1:]
        bad = {f: v for f, v in zip(FIELDS, vals) if v == 0}
        if bad:
            out.append((date, bad))
    return out


def apply_to(db_path, plan):
    from scripts.backfill_phantom_settlements import CORRECTIONS_DDL
    con = sqlite3.connect(db_path, timeout=10)
    changed = 0
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(CORRECTIONS_DDL)
        now = datetime.utcnow().isoformat(timespec="seconds")
        for date, bad in plan:
            for field, old in bad.items():
                # `= 0` in the predicate, so this cannot touch a real value
                # even if the row changed between planning and applying.
                cur = con.execute(
                    "UPDATE daily_summaries SET %s = NULL "
                    "WHERE date = ? AND %s = 0" % (field, field), (date,))
                if cur.rowcount:
                    changed += cur.rowcount
                    con.execute(
                        "INSERT INTO data_corrections "
                        "(applied_at,date,field,old_value,new_value,reason,evidence) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (now, date, field, str(old), None, REASON,
                         "value was exactly 0 in daily_summaries; no session "
                         "data existed for this date"))
        con.commit()
    finally:
        con.close()
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    ap.add_argument("--root", default="/opt/calypso")
    a = ap.parse_args(argv)

    total_rows = total_fields = total_changed = 0
    for vid, rel in VARIANTS:
        db = os.path.join(a.root, rel)
        if not os.path.exists(db):
            continue
        try:
            plan = plan_for(db)
        except sqlite3.Error as e:
            print("  %-2s SKIP (%s)" % (vid, e))
            continue
        if not plan:
            print("  %-2s clean" % vid)
            continue
        nf = sum(len(b) for _, b in plan)
        total_rows += len(plan)
        total_fields += nf
        print("  %-2s %d row(s), %d field(s): %s%s" % (
            vid, len(plan), nf,
            ", ".join(d for d, _ in plan[:6]),
            " ..." if len(plan) > 6 else ""))
        if a.apply:
            n = apply_to(db, plan)
            total_changed += n
            print("       -> %d field(s) set to NULL, audited" % n)

    print()
    print("  rows: %d   fields: %d" % (total_rows, total_fields))
    if a.apply:
        print("  APPLIED: %d field(s) nulled" % total_changed)
    else:
        print("  DRY RUN — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
