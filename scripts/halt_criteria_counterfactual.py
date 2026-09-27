#!/usr/bin/env python3
"""Measure what a SAME-DAY halt criterion would actually have done.

WHY THIS EXISTS. `docs/migration/LIVE_HALT_CRITERIA.md` defines H1 (session
loss per contract) and H3 (stops in one session). Both were breached on
2026-09-21, nine days after being drafted. The obvious reaction is "automate
them" — the bot's `_is_daily_loss_limit_reached()` hook is wired and merely
stubbed to `return False`.

Measured first, on 2026-09-27, across B's whole live era (32 traded days):

    HALT at -400/contract OR >=3 stops -> 1 entry blocked, on 1 day
    HALT at -600/contract OR >=3 stops -> 0 entries blocked, ever

The single blocked entry was 2026-09-24 e#7, which collected $280 and never
stopped. So automating the halt would have **cost $280** over 32 days.

THE STRUCTURAL REASON. The only lever a daily-loss halt has is refusing to open
NEW entries. B's grid runs 09:45-12:45, so entries are finished before the
afternoon in which losses accumulate. On 2026-09-21 the threshold was crossed
at 13:19 — after the last entry (12:47). A same-day entry-blocking halt is
close to inert for this strategy, and that is a property of the schedule, not
of the thresholds.

So re-run this before automating anything, and before re-deriving a threshold.
If a later sample makes the blocked-entry count materially positive, the
conclusion changes and the hook can be un-stubbed.

Usage:
    .venv/bin/python -m scripts.halt_criteria_counterfactual [variant] [--since DATE]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

LIVE_ERA_DEFAULT = "2026-07-24"


def _cols(cur, table):
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]


def run(db_path: Path, since: str, thresholds):
    if not db_path.exists():
        print(f"no such database: {db_path}", file=sys.stderr)
        return 1
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    scols = _cols(cur, "trade_stops")
    tcol = next(c for c in scols if "time" in c.lower())
    pcol = next(c for c in scols if "pnl" in c.lower())

    days = [r[0] for r in cur.execute(
        "SELECT DISTINCT date FROM trade_entries WHERE date>=? ORDER BY date", (since,))]
    print(f"{db_path}  ·  {len(days)} traded days since {since}\n")

    for thresh_pc, nstops in thresholds:
        blocked, days_hit, realized_after, credit_forgone = 0, 0, 0.0, 0.0
        detail = []
        for d in days:
            ent = [(r[0], str(r[1]), r[2], r[3]) for r in cur.execute(
                "SELECT entry_number,entry_time,contracts,total_credit "
                "FROM trade_entries WHERE date=? ORDER BY entry_time", (d,))]
            stp = [(r[0], str(r[1]), r[2]) for r in cur.execute(
                f"SELECT entry_number,{tcol},{pcol} FROM trade_stops "
                f"WHERE date=? ORDER BY {tcol}", (d,))]
            if not ent:
                continue
            k = ent[0][2] or 7
            cum, trip = 0.0, None
            for i, (_en, t, p) in enumerate(stp, 1):
                cum += (p or 0.0)
                if cum / k <= thresh_pc or i >= nstops:
                    trip = t[-8:]
                    break
            if trip is None:
                continue
            after = [e for e in ent if e[1][-8:] > trip]
            if not after:
                continue
            days_hit += 1
            blocked += len(after)
            for en, t, _k, credit in after:
                row = cur.execute(
                    f"SELECT COUNT(*),SUM({pcol}) FROM trade_stops "
                    "WHERE date=? AND entry_number=?", (d, en)).fetchone()
                realized_after += (row[1] or 0.0)
                if not row[0]:
                    # never stopped -> it KEPT its credit, so blocking it costs
                    credit_forgone += (credit or 0.0)
                detail.append(
                    f"      {d} e#{en} @{t[-8:]} credit=${credit or 0:,.2f} "
                    f"stops={row[0]} stop_pnl={row[1]}")

        print(f"  HALT at ${thresh_pc:,.0f}/contract OR >={nstops} stops")
        print(f"     days where a later entry would be blocked : {days_hit}/{len(days)}")
        print(f"     entries blocked                           : {blocked}")
        print(f"     stop P&L avoided by blocking them         : ${realized_after:,.2f}")
        print(f"     credit FORGONE (they never stopped)       : ${credit_forgone:,.2f}")
        net = realized_after - credit_forgone
        verdict = "would have HELPED" if net > 0 else "would have COST money"
        print(f"     net effect of automating                  : ${net:,.2f}  <- {verdict}")
        for line in detail:
            print(line)
        print()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", nargs="?", default="b")
    ap.add_argument("--since", default=LIVE_ERA_DEFAULT)
    ap.add_argument("--root", default="/opt/calypso")
    a = ap.parse_args()
    db = Path(a.root) / "data" / f"variant_{a.variant}" / "backtesting.db"
    return run(db, a.since, [(-400.0, 3), (-600.0, 3), (-800.0, 4)])


if __name__ == "__main__":
    raise SystemExit(main())
