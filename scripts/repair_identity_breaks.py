#!/usr/bin/env python3
"""Repair the two identity breaks that are genuinely recoverable.

The nightly audit (2026-09-30, its first scheduled run) surfaced seven identity
breaks. Five cannot be repaired without inventing trade detail and are recorded
in `audit_fleet_correctness.KNOWN_IRREPARABLE` instead. Two can:

**A 2026-04-01 — `net_pnl` is wrong.** `net = gross - commission` holds on
**155 of 156** variant-A days; this is the single exception. gross (1,235) is
independently confirmed by the entries — three expired keeping 490+495+445 and
e#4 stopped at -195 — and commission is 40, so net must be **1,195**. The
recorded 330 is an $865 recording error in one field, on one day. Nothing is
inferred except by an identity that holds everywhere else.

**C 2026-06-11 — the `daily_summaries` row is missing entirely.** One entry
(credit 140, 7 contracts) and one stop (+70) exist. A day with recorded trades
and no summary is invisible to every roll-up, so the row is reconstructed from
the rows that ARE there — not from a guess about what the day should have been.

Neither database belongs to the live seat: A and C are dry-run variants, and
both dates are long before B took the live seat on 2026-07-24.

Dry-run by default. `--write` takes a real sqlite `.backup()` first and logs
every change to `data_corrections` with its before value and evidence.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _a_0401(con):
    """A 2026-04-01: net_pnl 330 -> gross - commission."""
    r = con.execute("SELECT gross_pnl, net_pnl, commission FROM daily_summaries "
                    "WHERE date='2026-04-01'").fetchone()
    if not r:
        return None
    gross, net, comm = r
    if gross is None or comm is None:
        return None
    want = round(gross - comm, 2)
    if net is not None and abs(net - want) <= 0.01:
        return None                      # already correct
    ents = con.execute("SELECT COALESCE(SUM(total_credit),0) FROM trade_entries "
                       "WHERE date='2026-04-01'").fetchone()[0]
    stops = con.execute("SELECT COALESCE(SUM(net_pnl),0) FROM trade_stops "
                        "WHERE date='2026-04-01'").fetchone()[0]
    return {
        "date": "2026-04-01", "field": "net_pnl", "old": net, "new": want,
        "sql": ("UPDATE daily_summaries SET net_pnl=? WHERE date='2026-04-01'", (want,)),
        "evidence": (f"net = gross - commission holds on 155/156 variant-A days; "
                     f"gross {gross} is confirmed by entries (credits {ents}, "
                     f"stops {stops}); commission {comm}"),
    }


def _c_0611(con):
    """C 2026-06-11: the daily_summaries row is missing."""
    if con.execute("SELECT 1 FROM daily_summaries WHERE date='2026-06-11'").fetchone():
        return None
    ent = con.execute("SELECT COUNT(*), COALESCE(SUM(total_credit),0), "
                      "COALESCE(MAX(contracts),1) FROM trade_entries "
                      "WHERE date='2026-06-11'").fetchone()
    st = con.execute("SELECT COUNT(*), COALESCE(SUM(net_pnl),0) FROM trade_stops "
                     "WHERE date='2026-06-11'").fetchone()
    n_ent, credit, contracts = ent
    n_stop, stop_pnl = st
    if not n_ent:
        return None
    gross = round(stop_pnl, 2)           # the only realised money on record
    return {
        "date": "2026-06-11", "field": "daily_summaries(row)", "old": None,
        "new": f"gross={gross} placed={n_ent} stopped={n_stop}",
        "sql": ("INSERT INTO daily_summaries (date, gross_pnl, net_pnl, commission, "
                "entries_placed, entries_stopped, entries_expired, contracts_per_entry) "
                "VALUES ('2026-06-11', ?, ?, 0.0, ?, ?, 0, ?)",
                (gross, gross, n_ent, n_stop, contracts)),
        "evidence": (f"reconstructed from the rows that exist: {n_ent} trade_entries "
                     f"(credit {credit}, {contracts}c) and {n_stop} trade_stops "
                     f"(net {stop_pnl}). commission unrecorded -> 0.0, so net==gross"),
    }


REPAIRS = {"a": [_a_0401], "c": [_c_0611]}

REASON = ("repair_identity_breaks.py — surfaced by the first scheduled run of "
          "audit_fleet_correctness (2026-09-30). Recoverable by identity from "
          "rows already present; no trade detail invented.")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/opt/calypso")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)

    total = 0
    for vid, fns in REPAIRS.items():
        db = (os.path.join(a.root, "data", "backtesting.db") if vid == "a"
              else os.path.join(a.root, "data", f"variant_{vid}", "backtesting.db"))
        if not os.path.exists(db):
            print(f"  {vid.upper()}: no database at {db}")
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            plan = [p for p in (f(con) for f in fns) if p]
        finally:
            con.close()
        print(f"\n=== variant {vid.upper()} ===")
        if not plan:
            print("  nothing to repair (already correct)")
            continue
        for p in plan:
            print(f"  {p['date']}  {p['field']}:  {p['old']}  ->  {p['new']}")
            print(f"      evidence: {p['evidence']}")
        total += len(plan)
        if not a.write:
            continue

        from shared.db_backup import safe_db_backup
        print(f"  backup -> {safe_db_backup(db, 'pre_identity_repair')}")
        from scripts.backfill_phantom_settlements import CORRECTIONS_DDL
        w = sqlite3.connect(db)
        try:
            w.execute(CORRECTIONS_DDL)
            now = datetime.utcnow().isoformat(timespec="seconds")
            for p in plan:
                sql, params = p["sql"]
                w.execute(sql, params)
                w.execute("INSERT INTO data_corrections (applied_at,date,field,"
                          "old_value,new_value,reason,evidence) VALUES (?,?,?,?,?,?,?)",
                          (now, p["date"], p["field"], str(p["old"]), str(p["new"]),
                           REASON, p["evidence"]))
            w.commit()
            print(f"  WROTE {len(plan)} repair(s) + {len(plan)} audit row(s)")
        finally:
            w.close()

    print(f"\n{'REPAIRED' if a.write else 'WOULD REPAIR'} {total} break(s)")
    if not a.write:
        print("(dry run — pass --write to apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
