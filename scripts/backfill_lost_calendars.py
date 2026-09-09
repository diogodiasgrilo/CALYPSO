#!/usr/bin/env python3
"""
Back-fill calendars that were dropped from tracking and never booked
(Phase 0.6, 2026-09-09). DRY-RUN BY DEFAULT — pass --apply to write.

THE PROBLEM
-----------
A calendar lives in two places: `dc_open_trades.json` (authoritative for
monitoring) and `dc_calendar_entries` (the record). It is only *finished* when
it also gets a `dc_outcomes` row. Five positions fell out of the sidecar
WITHOUT one, so they stopped being monitored and were never booked as a win or
a loss — they simply vanished, and each strategy's lifetime P&L silently
excludes them:

    D  dctm_20260618_001  ($2,290 debit)
    D  dctm_20260803_001  ($950)
    D  dctm_20260813_001  ($1,590)
    E  spydc_20260722_001
    E  spydc_20260805_001

D's stated lifetime excludes three whole positions. E is missing two of its
five entries — 40% of its record.

The write-ordering race behind these was fixed on 2026-08-18
(`_reset_for_new_day`'s `re_save_needed`), and all five predate it.

WHAT THIS DOES
--------------
Books each lost entry with `terminal_state='LOST_FROM_TRACKING'` and a
realized P&L taken from its LAST OBSERVED MARK.

Read that carefully: it is a **last observed mark, not a realized close**. The
position was never actually closed; nobody knows what it would have settled
at. The distinct terminal_state exists precisely so these rows can be excluded
from any strict analysis — `dc_edge.py` and friends should filter them out, and
they must never be presented as trades that completed.

Booking them is still the honest choice: excluding a losing position because
the bookkeeping lost it makes the strategy look better than it was.

ATTRIBUTION. Historical `dc_calendar_snapshots` rows carry no `strategy_id`
(added 2026-09-09, schema v3), so a lost entry's marks are attributed by TIME
WINDOW: from its own entry_time up to the next entry's entry_time. That is
sound here only because `dc_max_concurrent = 1` — at most one calendar is open
at a time, so the window cannot contain another trade's marks. The script
refuses to run if it finds evidence of overlapping entries.

USAGE (on the VM, as calypso)
  # inspect first — this is the default
  sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m scripts.backfill_lost_calendars --variant d'
  # then, only if the numbers look right
  sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m scripts.backfill_lost_calendars --variant d --apply'
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TERMINAL_STATE = "LOST_FROM_TRACKING"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="d")
    ap.add_argument("--apply", action="store_true",
                    help="actually write; default is a dry run")
    a = ap.parse_args()

    db = f"data/variant_{a.variant}/dc_calendar.db"
    if not os.path.exists(db):
        print(f"ERROR: {db} not found", file=sys.stderr)
        return 2

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    entries = list(con.execute(
        "SELECT strategy_id, date, entry_time, net_debit, entry_number "
        "FROM dc_calendar_entries ORDER BY entry_time"))
    booked = {r[0] for r in con.execute(
        "SELECT strategy_id FROM dc_outcomes WHERE strategy_id IS NOT NULL")}

    # Open positions must NOT be back-filled — they are still live.
    sidecar = f"data/variant_{a.variant}/dc_open_trades.json"
    live = set()
    if os.path.exists(sidecar):
        import json
        try:
            live = {r.get("strategy_id") for r in json.load(open(sidecar))}
        except Exception as e:
            print(f"ERROR: cannot read the sidecar ({e}) — refusing to run, "
                  f"a live position could be booked as lost", file=sys.stderr)
            return 2

    lost = [r for r in entries if r["strategy_id"] not in booked
            and r["strategy_id"] not in live]
    if not lost:
        print(f"variant {a.variant.upper()}: nothing to back-fill "
              f"({len(entries)} entries, {len(booked)} booked, {len(live)} open)")
        return 0

    # Safety: time-window attribution is only valid at max_concurrent = 1.
    times = [r["entry_time"] for r in entries if r["entry_time"]]
    if len(times) != len(set(times)):
        print("ERROR: duplicate entry_time values — cannot attribute snapshots "
              "by time window. Refusing to run.", file=sys.stderr)
        return 2

    print(f"\nvariant {a.variant.upper()} — {len(lost)} calendar(s) never booked "
          f"(of {len(entries)} entries)\n")
    print(f"{'strategy_id':24} {'entry':11} {'debit':>9} {'last mark':>11} "
          f"{'last seen':20} {'ticks':>7}")
    print("-" * 92)

    plan = []
    for r in lost:
        sid = r["strategy_id"]
        start = r["entry_time"]
        later = [e["entry_time"] for e in entries
                 if e["entry_time"] and start and e["entry_time"] > start]
        end = min(later) if later else "9999"
        snaps = list(con.execute(
            "SELECT timestamp, unrealized_pnl FROM dc_calendar_snapshots "
            "WHERE timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp",
            (str(start)[:19].replace("T", " "), str(end)[:19].replace("T", " "))))
        if not snaps:
            print(f"{sid:24} {str(r['date']):11} {r['net_debit'] or 0:>9.2f} "
                  f"{'NO SNAPSHOTS':>11} {'-':20} {0:>7}")
            continue
        last = snaps[-1]
        plan.append({
            "strategy_id": sid, "entry_date": r["date"],
            "entry_number": r["entry_number"],
            "close_date": str(last["timestamp"])[:10],
            "realized_pnl": last["unrealized_pnl"],
            "net_debit": r["net_debit"],
        })
        print(f"{sid:24} {str(r['date']):11} {r['net_debit'] or 0:>9.2f} "
              f"{last['unrealized_pnl']:>11.2f} {str(last['timestamp']):20} "
              f"{len(snaps):>7}")

    print("-" * 92)
    total = sum(p["realized_pnl"] or 0 for p in plan)
    cur = con.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM dc_outcomes").fetchone()[0]
    print(f"\nP&L currently booked in dc_outcomes : {cur:>12,.2f}")
    print(f"P&L of the lost positions (last mark): {total:>12,.2f}")
    print(f"Corrected lifetime                   : {cur + total:>12,.2f}\n")
    print("These are LAST OBSERVED MARKS, not realized closes. The positions")
    print("were never actually closed. They are booked under terminal_state")
    print(f"'{TERMINAL_STATE}' so analysis can exclude them — do not present")
    print("them as completed trades.\n")

    if not a.apply:
        print("DRY RUN — nothing written. Re-run with --apply to write.\n")
        con.close()
        return 0

    backup = f"{db}.pre_backfill_{datetime.utcnow():%Y%m%dT%H%M%SZ}"
    shutil.copy2(db, backup)
    print(f"backup: {backup}")
    n = 0
    for p in plan:
        try:
            con.execute(
                "INSERT INTO dc_outcomes (entry_date, close_date, entry_number, "
                "strategy_id, terminal_state, realized_pnl, spx_at_close, "
                "close_commission, transform_credit, net_debit) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (p["entry_date"], p["close_date"], p["entry_number"],
                 p["strategy_id"], TERMINAL_STATE, p["realized_pnl"],
                 None, 0.0, 0.0, p["net_debit"]),
            )
            n += 1
        except Exception as e:
            print(f"  FAILED {p['strategy_id']}: {e}", file=sys.stderr)
    con.commit()
    con.close()
    print(f"wrote {n} row(s) with terminal_state='{TERMINAL_STATE}'\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
