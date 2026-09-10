#!/usr/bin/env python3
"""
Back-fill `daily_summaries.unattributed_overlay_pnl` for days where the Brandon
overlay's P&L reached the day aggregate but no entry (2026-09-10).
DRY-RUN BY DEFAULT — pass --apply to write.

WHY THIS EXISTS
---------------
Overlay (butterfly / debit-spread hedge) P&L is normally attributed to the
single entry it defended — that is correct by design and must be preserved.
It falls back to aggregate-only when the hedged entry object is absent from
`daily_state.entries` at settlement time (the settler looks the entry up by
OBJECT IDENTITY, so a restart between placing the hedge and settling it loses
the link).

When that happens, `daily_summaries.gross_pnl` includes the hedge result but
`sum(trade_entries.realized_pnl)` does not, and the day appears to "drift".
The v13 column `unattributed_overlay_pnl` exists precisely to record the gap so
downstream tools can adjust — but it has **never carried a non-zero value in
variant B's entire history**, so `slot_edge`'s overlay adjustment has been a
no-op and the residual has been reported as an unexplained attribution defect.

MEASURED ON B: the whole residual is ONE DAY.
    2026-07-07  gross_pnl 392.42  vs  sum(entry realized_pnl) 2,925.00
                drift -2,532.58
Every other day in the reliable window (>= 2026-07-02) reconciles to $0.00.

WHAT THIS DOES NOT DO
---------------------
It back-fills the DAY-level scalar only. Which entry the hedge belonged to is
permanently lost — the attribution was never recorded, and guessing would be
worse than leaving it unattributed. So the day's per-SLOT numbers stay
incomplete by design; only the day-level reconciliation is repaired.

This is a symptom fix. The root cause — attributing by object identity rather
than by entry_number — is a separate strategy-side change.

USAGE (on the VM, as calypso)
  sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m scripts.backfill_overlay_residual --variant b'
  sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m scripts.backfill_overlay_residual --variant b --apply'
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Below this, per-entry realized_pnl was not booked at all, so a "drift" there
#: is expected and must NOT be back-filled as overlay residual.
RELIABLE_SINCE = "2026-07-02"
#: Ignore sub-dollar rounding noise.
EPSILON = 0.50


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="b")
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    a = ap.parse_args()

    db = ("data/backtesting.db" if a.variant == "a"
          else f"data/variant_{a.variant}/backtesting.db")
    if not os.path.exists(db):
        print(f"ERROR: {db} not found", file=sys.stderr)
        return 2

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(daily_summaries)")}
    if "unattributed_overlay_pnl" not in cols:
        print("ERROR: daily_summaries has no unattributed_overlay_pnl column "
              "(pre-v13 DB) — nothing to back-fill.", file=sys.stderr)
        return 2

    plan = []
    for r in con.execute(
        "SELECT date, gross_pnl, unattributed_overlay_pnl FROM daily_summaries "
        "WHERE date >= ? ORDER BY date", (RELIABLE_SINCE,)
    ):
        booked = con.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM trade_entries WHERE date = ?",
            (r["date"],)).fetchone()[0]
        drift = (r["gross_pnl"] or 0.0) - booked
        if abs(drift) <= EPSILON:
            continue
        existing = r["unattributed_overlay_pnl"]
        plan.append({"date": r["date"], "gross": r["gross_pnl"], "booked": booked,
                     "drift": drift, "existing": existing})

    print(f"\nvariant {a.variant.upper()} — days drifting more than ${EPSILON:.2f} "
          f"since {RELIABLE_SINCE}\n")
    if not plan:
        print("  none — every day reconciles.\n")
        con.close()
        return 0

    print(f"  {'date':12} {'gross':>12} {'booked':>12} {'drift':>12} {'existing':>10}")
    for p in plan:
        print(f"  {p['date']:12} {p['gross']:>12,.2f} {p['booked']:>12,.2f} "
              f"{p['drift']:>12,.2f} {str(p['existing']):>10}")

    todo = [p for p in plan if not p["existing"]]
    print(f"\n  {len(todo)} of {len(plan)} need writing "
          f"({len(plan) - len(todo)} already recorded).")
    print("\n  Sets unattributed_overlay_pnl = drift, so")
    print("  SUM(gross_pnl) - SUM(unattributed_overlay_pnl) == SUM(realized_pnl).")
    print("  DAY-level only — which entry the hedge belonged to is permanently lost.\n")

    if not a.apply:
        print("  DRY RUN — nothing written. Re-run with --apply.\n")
        con.close()
        return 0

    backup = f"{db}.pre_overlay_backfill_{datetime.utcnow():%Y%m%dT%H%M%SZ}"
    shutil.copy2(db, backup)
    print(f"  backup: {backup}")
    for p in todo:
        con.execute(
            "UPDATE daily_summaries SET unattributed_overlay_pnl = ? WHERE date = ?",
            (round(p["drift"], 2), p["date"]))
    con.commit()

    # Prove the identity now holds.
    resid = 0.0
    for r in con.execute(
        "SELECT date, gross_pnl, COALESCE(unattributed_overlay_pnl, 0) u "
        "FROM daily_summaries WHERE date >= ?", (RELIABLE_SINCE,)
    ):
        booked = con.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM trade_entries WHERE date = ?",
            (r["date"],)).fetchone()[0]
        resid += (r["gross_pnl"] or 0.0) - r["u"] - booked
    con.close()
    print(f"  wrote {len(todo)} row(s). Residual after adjustment: ${resid:,.2f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
