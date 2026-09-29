#!/usr/bin/env python3
"""Cross-check every variant's database against its own accounting identities.

WHAT THIS IS FOR. Each variant writes its own isolated SQLite, and the
dashboard, the agents and every analysis in `docs/` read those numbers as
truth. Nothing had ever verified that they are internally consistent — and
this session found three places where they were not: a headline that was
gross being labelled net, a day whose P&L was dominated by a failed-entry
unwind, and telemetry columns that were never populated.

The identities below are the ones that must hold by construction. A break is
not a rounding artifact; it means one of the writers disagrees with another.

  I1  sum(trade_entries.realized_pnl) + unattributed_overlay_pnl == gross_pnl
      Per-entry results plus day-level residue must reconstruct the day.

  I2  gross_pnl - commission == net_pnl
      The definition of net. A break means a writer computed it differently.

  I3  daily_summaries.entries_placed == count(trade_entries)
      The summary's own count must match the rows it summarises.

  I4  metrics file lifetime total == sum(daily_summaries.net_pnl)
      The cumulative file drifts silently when it accumulates incrementally
      instead of re-deriving; that has happened before (metrics_db_drift).

SETTLEMENT-AWARE. A day with entries but no summary row is normal until
settlement completes (~21:45-22:37 ET); it is reported as PENDING, not as a
break, so an after-close run does not cry wolf every evening.

Read-only. Opens every database with mode=ro.

Usage:
    .venv/bin/python -m scripts.audit_fleet_correctness
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

TOL = 0.01
VARIANTS = ["", "b", "c", "d", "e", "f", "g", "h"]


def _ro(p: Path):
    if not p.exists():
        return None
    try:
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _cols(con, table):
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []


def audit(root: Path, vid: str, today: str):
    name = (vid or "a").upper()
    data = root / "data" if not vid else root / "data" / f"variant_{vid}"
    db = data / "backtesting.db"
    con = _ro(db)
    if con is None:
        return [(name, "SKIP", f"no backtesting.db at {db}")]

    out = []
    have_unattr = "unattributed_overlay_pnl" in _cols(con, "daily_summaries")

    days = con.execute(
        "SELECT date, gross_pnl, net_pnl, commission, entries_placed"
        + (", unattributed_overlay_pnl" if have_unattr else "")
        + " FROM daily_summaries ORDER BY date").fetchall()
    # `realized_pnl` was only populated from 2026-07-01; before that it is
    # NULL on every row. SUM() would read those as 0.00 and make I1 fail on
    # every historical day — ~131 rows across A/B/C on the first run, which is
    # ONE schema fact wearing a hundred hats, not a hundred breaks. Days whose
    # entries carry no per-entry P&L are reported as UNSCORABLE instead.
    ent_by_day, cnt_by_day, null_days = {}, {}, set()
    for d, s, c, nulls in con.execute(
            "SELECT date, SUM(realized_pnl), COUNT(*), "
            "SUM(CASE WHEN realized_pnl IS NULL THEN 1 ELSE 0 END) "
            "FROM trade_entries GROUP BY date"):
        ent_by_day[d] = s or 0.0
        cnt_by_day[d] = c
        if nulls:
            null_days.add(d)

    i1 = i2 = i3 = 0
    for row in days:
        d, g, n, cm, ep = row[0], row[1] or 0, row[2] or 0, row[3] or 0, row[4] or 0
        unattr = (row[5] or 0) if have_unattr else 0.0
        if d in null_days:
            pass  # pre-backfill: no per-entry P&L exists, so I1 cannot be tested
        elif d in ent_by_day and abs((ent_by_day[d] + unattr) - g) > TOL:
            i1 += 1
            out.append((name, "I1", f"{d}: entries {ent_by_day[d]:,.2f} + unattr "
                                    f"{unattr:,.2f} != gross {g:,.2f}"))
        if abs((g - cm) - n) > TOL:
            i2 += 1
            out.append((name, "I2", f"{d}: gross {g:,.2f} - comm {cm:,.2f} != net {n:,.2f}"))
        if d in cnt_by_day and ep != cnt_by_day[d]:
            i3 += 1
            out.append((name, "I3", f"{d}: summary says {ep} entries, table has {cnt_by_day[d]}"))

    # A day with real gross and NO trade_entries rows at all is a different
    # animal from a pre-backfill NULL: the detail is genuinely missing, so any
    # per-entry analysis of that day is silently incomplete.
    for d, g in con.execute(
            "SELECT date, gross_pnl FROM daily_summaries ds WHERE gross_pnl IS NOT NULL "
            "AND gross_pnl != 0 AND NOT EXISTS "
            "(SELECT 1 FROM trade_entries te WHERE te.date = ds.date)"):
        unattr_d = 0.0
        if have_unattr:
            row = con.execute("SELECT unattributed_overlay_pnl FROM daily_summaries "
                              "WHERE date=?", (d,)).fetchone()
            unattr_d = (row[0] or 0.0) if row else 0.0
        if abs(g - unattr_d) <= TOL:
            continue  # gross IS the day-level residue — correct, not missing
        out.append((name, "I5", f"{d}: gross {g:,.2f} but ZERO trade_entries rows "
                                "— per-entry detail lost"))

    # days with entries but no summary row
    summary_dates = {r[0] for r in days}
    orphan = sorted(set(ent_by_day) - summary_dates)
    for d in orphan:
        kind = "PENDING" if d >= today else "I3"
        out.append((name, kind, f"{d}: {cnt_by_day[d]} entries, no daily_summaries row"
                    + (" (settlement not finished — expected)" if kind == "PENDING" else "")))

    # I4 — metrics file vs DB
    mf = data / "hydra_metrics.json"
    if mf.exists():
        try:
            m = json.load(open(mf))
            lifetime = m.get("total_pnl", m.get("lifetime_pnl"))
            dbsum = sum((r[2] or 0) for r in days)
            if isinstance(lifetime, (int, float)) and abs(lifetime - dbsum) > 1.0:
                out.append((name, "I4", f"metrics {lifetime:,.2f} vs DB {dbsum:,.2f} "
                                        f"(drift {lifetime - dbsum:+,.2f})"))
        except Exception as e:  # noqa: BLE001
            out.append((name, "I4", f"metrics unreadable: {e}"))

    if null_days:
        out.append((name, "NOTE", f"{len(null_days)} day(s) pre-2026-07-01 carry no "
                                  "per-entry realized_pnl — I1 not testable there"))

    if not any(t in ("I1", "I2", "I3", "I4") for _n, t, _m in out):
        out.append((name, "OK", f"{len(days)} summary days, {sum(cnt_by_day.values())} entries "
                                f"— all identities hold"))
    con.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/opt/calypso")
    a = ap.parse_args()
    root = Path(a.root)
    today = os.popen("TZ=America/New_York date +%Y-%m-%d").read().strip()
    print(f"FLEET CORRECTNESS AUDIT — {today} (ET)\n")
    worst = 0
    for vid in VARIANTS:
        for name, tag, msg in audit(root, vid, today):
            marker = {"OK": "  ok ", "PENDING": "  .. ", "SKIP": "  -- ",
                      "NOTE": "  -- "}.get(tag, "  !! ")
            print(f"{marker}{name:<3} {tag:<8} {msg}")
            if tag in ("I1", "I2", "I3", "I4", "I5"):
                worst = 1
    print("\nVERDICT:", "BREAKS FOUND — see !! rows" if worst else "all identities hold")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
