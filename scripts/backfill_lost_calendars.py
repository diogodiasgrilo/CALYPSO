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

METRICS SYNC (added 2026-09-10). Writing `dc_outcomes` alone is NOT enough, and
the first run of this script proved it: `hydra_metrics.json` is derived from
`daily_summaries` in a DIFFERENT database (`backtesting.db`), which the
back-fill never touched. That left D reading -$6,129.05 in metrics while
`dc_outcomes` summed to -$6,458.40 — two different "lifetime P&L" figures for
the same strategy.

So this script also adds each lost position's P&L to the `daily_summaries` row
for the day it was last seen — the day it would have been booked had it closed
properly. `_reconcile_cumulative_metrics_from_db` then re-derives
`cumulative_pnl` from `daily_summaries` at the next settlement, so the metrics
file self-heals with no new machinery.

Idempotency is explicit: every adjustment is recorded in `dc_metrics_adjustments`
keyed on strategy_id, and an id already present is never applied twice. That
matters because the `dc_outcomes` insert and the `daily_summaries` adjustment
can be applied in separate runs (they were, here).

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


def _ensure_adjustments_table(con) -> None:
    """Idempotency ledger for the daily_summaries side-effect.

    The `dc_outcomes` insert and the `daily_summaries` adjustment can be applied
    in SEPARATE runs (they were — outcomes on 2026-09-09, metrics on 09-10), so
    "already in dc_outcomes" cannot serve as the guard for the second half.
    """
    con.execute(
        "CREATE TABLE IF NOT EXISTS dc_metrics_adjustments ("
        "strategy_id TEXT PRIMARY KEY, close_date TEXT, amount REAL, applied_at TEXT)"
    )


def _phase1_book_outcomes(con, variant, entries, booked, live, apply):
    """Book lost entries into dc_outcomes from their LAST OBSERVED MARK."""
    lost = [r for r in entries
            if r["strategy_id"] not in booked and r["strategy_id"] not in live]
    if not lost:
        print(f"  phase 1 (dc_outcomes): nothing to book — {len(entries)} entries, "
              f"{len(booked)} booked, {len(live)} open")
        return []

    times = [r["entry_time"] for r in entries if r["entry_time"]]
    if len(times) != len(set(times)):
        raise SystemExit("ERROR: duplicate entry_time — time-window attribution "
                         "is unsafe. Refusing to run.")

    print(f"  phase 1 (dc_outcomes): {len(lost)} calendar(s) never booked")
    print(f"    {'strategy_id':24} {'entry':11} {'debit':>9} {'last mark':>11} {'ticks':>7}")
    plan = []
    for r in lost:
        start = r["entry_time"]
        later = [e["entry_time"] for e in entries
                 if e["entry_time"] and start and e["entry_time"] > start]
        end = min(later) if later else "9999"
        snaps = list(con.execute(
            "SELECT timestamp, unrealized_pnl FROM dc_calendar_snapshots "
            "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
            (str(start)[:19].replace("T", " "), str(end)[:19].replace("T", " "))))
        if not snaps:
            print(f"    {r['strategy_id']:24} {str(r['date']):11} "
                  f"{r['net_debit'] or 0:>9.2f} {'NO SNAPSHOTS':>11} {0:>7}")
            continue
        last = snaps[-1]
        plan.append({"strategy_id": r["strategy_id"], "entry_date": r["date"],
                     "entry_number": r["entry_number"],
                     "close_date": str(last["timestamp"])[:10],
                     "realized_pnl": last["unrealized_pnl"], "net_debit": r["net_debit"]})
        print(f"    {r['strategy_id']:24} {str(r['date']):11} "
              f"{r['net_debit'] or 0:>9.2f} {last['unrealized_pnl']:>11.2f} {len(snaps):>7}")

    if apply:
        for p_ in plan:
            con.execute(
                "INSERT INTO dc_outcomes (entry_date, close_date, entry_number, "
                "strategy_id, terminal_state, realized_pnl, spx_at_close, "
                "close_commission, transform_credit, net_debit) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (p_["entry_date"], p_["close_date"], p_["entry_number"], p_["strategy_id"],
                 TERMINAL_STATE, p_["realized_pnl"], None, 0.0, 0.0, p_["net_debit"]))
        con.commit()
        print(f"    -> wrote {len(plan)} dc_outcomes row(s)")
    return plan


def _phase2_sync_metrics(con, variant, apply):
    """Add each lost position's P&L to the daily_summaries row for the day it
    was last seen, so hydra_metrics.json self-heals at the next settlement."""
    _ensure_adjustments_table(con)
    done = {r[0] for r in con.execute("SELECT strategy_id FROM dc_metrics_adjustments")}
    rows = [r for r in con.execute(
        "SELECT strategy_id, close_date, realized_pnl FROM dc_outcomes "
        "WHERE terminal_state = ?", (TERMINAL_STATE,))
        if r["strategy_id"] not in done]
    if not rows:
        print(f"  phase 2 (daily_summaries): nothing to sync "
              f"({len(done)} adjustment(s) already applied)")
        return 0.0

    bt = f"data/variant_{variant}/backtesting.db"
    if not os.path.exists(bt):
        print(f"  phase 2: {bt} not found — SKIPPED", file=sys.stderr)
        return 0.0
    b = sqlite3.connect(bt)
    b.row_factory = sqlite3.Row

    print(f"  phase 2 (daily_summaries): {len(rows)} adjustment(s) pending")
    total = 0.0
    applied = []
    for r in rows:
        cur = b.execute("SELECT net_pnl FROM daily_summaries WHERE date=?",
                        (r["close_date"],)).fetchone()
        if cur is None:
            print(f"    {r['strategy_id']:24} {r['close_date']}  "
                  f"NO daily_summaries ROW — skipped (will not fabricate one)")
            continue
        amt = r["realized_pnl"] or 0.0
        print(f"    {r['strategy_id']:24} {r['close_date']}  "
              f"net_pnl {cur['net_pnl']:+.2f} -> {cur['net_pnl'] + amt:+.2f}  ({amt:+.2f})")
        total += amt
        applied.append((r["strategy_id"], r["close_date"], amt))

    if apply and applied:
        stamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        for sid, cd, amt in applied:
            b.execute("UPDATE daily_summaries SET net_pnl = net_pnl + ? WHERE date = ?",
                      (amt, cd))
            con.execute("INSERT OR REPLACE INTO dc_metrics_adjustments "
                        "(strategy_id, close_date, amount, applied_at) VALUES (?,?,?,?)",
                        (sid, cd, amt, stamp))
        b.commit(); con.commit()
        print(f"    -> adjusted {len(applied)} daily_summaries row(s) by {total:+,.2f}")
        print(f"    -> hydra_metrics.json self-heals at the next settlement via "
              f"_reconcile_cumulative_metrics_from_db")
    b.close()
    return total


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

    if a.apply:
        backup = f"{db}.pre_backfill_{datetime.utcnow():%Y%m%dT%H%M%SZ}"
        shutil.copy2(db, backup)
        bt = f"data/variant_{a.variant}/backtesting.db"
        if os.path.exists(bt):
            shutil.copy2(bt, f"{bt}.pre_backfill_{datetime.utcnow():%Y%m%dT%H%M%SZ}")
        print(f"backup: {backup} (+ backtesting.db)")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    print(f"\n=== variant {a.variant.upper()} ===")

    entries = list(con.execute(
        "SELECT strategy_id, date, entry_time, net_debit, entry_number "
        "FROM dc_calendar_entries ORDER BY entry_time"))
    booked = {r[0] for r in con.execute(
        "SELECT strategy_id FROM dc_outcomes WHERE strategy_id IS NOT NULL")}

    sidecar = f"data/variant_{a.variant}/dc_open_trades.json"
    live = set()
    if os.path.exists(sidecar):
        import json
        try:
            live = {r.get("strategy_id") for r in json.load(open(sidecar))}
        except Exception as e:
            print(f"ERROR: cannot read the sidecar ({e}) — refusing to run, a live "
                  f"position could be booked as lost", file=sys.stderr)
            return 2

    _phase1_book_outcomes(con, a.variant, entries, booked, live, a.apply)
    _phase2_sync_metrics(con, a.variant, a.apply)
    con.close()

    print("\n  NOTE: these are LAST OBSERVED MARKS, not realized closes. The")
    print("  positions were never actually closed. terminal_state="
          f"'{TERMINAL_STATE}'")
    print("  so dc_edge excludes them from the edge verdict.")
    if not a.apply:
        print("\n  DRY RUN — nothing written. Re-run with --apply.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
