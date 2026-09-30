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

    # ── I2 ON A MULTI-DAY STRATEGY ──────────────────────────────────────────
    # `gross - commission == net` is an IRON-CONDOR identity. It assumes a
    # position opens and closes the same day, so the day that pays the
    # commission is the day that realises the P&L. A calendar breaks that
    # assumption honestly:
    #
    #   OPENING DAY   commission is paid, nothing is realised. The cost belongs
    #                 to the position and is booked at close, so net = 0 while
    #                 commission > 0 is CORRECT, not a break.
    #   ADJUSTED DAY  scripts/backfill_lost_calendars.py (2026-09-09) recovered
    #                 five calendars that fell out of the sidecar and were never
    #                 booked, via `UPDATE daily_summaries SET net_pnl = net_pnl
    #                 + ?` — net ONLY. Deliberately: gross and commission in an
    #                 IC-shaped row are meaningless for a calendar (E's own code
    #                 calls that table "vestigial"; dc_calendar.db is
    #                 authoritative), and fabricating a gross to satisfy an
    #                 identity that does not apply would have been worse. Every
    #                 adjustment is recorded in dc_metrics_adjustments.
    #
    # So rather than skipping I2 for calendars — which would throw away a real
    # check — each discrepancy is looked up and CLASSIFIED. Only a discrepancy
    # that is neither an opening day nor a logged adjustment is a break.
    cal_adjusted, cal_opened = set(), set()
    if vid in ("d", "e"):
        cal = _ro(data / "dc_calendar.db")
        if cal is not None:
            try:
                cal_adjusted = {r[0] for r in cal.execute(
                    "SELECT close_date FROM dc_metrics_adjustments")}
            except sqlite3.Error:
                pass
            try:
                cal_opened = {r[0] for r in cal.execute(
                    "SELECT DISTINCT entry_date FROM dc_outcomes")}
            except sqlite3.Error:
                pass
            cal.close()

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
            if d in cal_adjusted:
                out.append((name, "ADJUSTED", f"{d}: net carries a recovered calendar "
                                              "(dc_metrics_adjustments) — gross/comm "
                                              "untouched by design"))
            elif d in cal_opened:
                out.append((name, "MULTIDAY", f"{d}: calendar OPENED, nothing closed — "
                                              f"commission {cm:,.2f} belongs to the "
                                              "position, realised at close"))
            else:
                i2 += 1
                out.append((name, "I2", f"{d}: gross {g:,.2f} - comm {cm:,.2f} != net {n:,.2f}"))
        if d in cnt_by_day and ep != cnt_by_day[d]:
            i3 += 1
            out.append((name, "I3", f"{d}: summary says {ep} entries, table has {cnt_by_day[d]}"))

    # I5 DOES NOT APPLY TO VARIANTS THAT DO NOT USE trade_entries. D and E
    # are calendars and keep their entries in dc_calendar.db; H is the long
    # strangle and keeps its in long_strangle.db. For them "gross with no
    # trade_entries rows" is CORRECT, not missing — the first run of this
    # audit flagged 16 such days and every one was a false positive of an
    # IC-shaped assumption.
    uses_trade_entries = vid not in ("d", "e", "h")


    # A day with real gross and NO trade_entries rows at all is a different
    # animal from a pre-backfill NULL: the detail is genuinely missing, so any
    # per-entry analysis of that day is silently incomplete.
    for d, g in (con.execute(
            "SELECT date, gross_pnl FROM daily_summaries ds WHERE gross_pnl IS NOT NULL "
            "AND gross_pnl != 0 AND NOT EXISTS "
            "(SELECT 1 FROM trade_entries te WHERE te.date = ds.date)")
            if uses_trade_entries else []):
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


def _alert_breaks(rows, today):
    """Push the breaks somewhere a person will see them.

    A correctness audit nobody reads is not a control. This script existed
    since 2026-09-29 and was scheduled by no timer, which is why three columns
    sat empty for weeks and were found by hand. Scheduling it fixes half of
    that; the other half is that a failed systemd unit is invisible unless
    someone runs `systemctl --failed`.

    Never raises: an alerting failure must not mask the audit's own exit code,
    which is what systemd and the operator actually gate on.
    """
    try:
        from shared.alert_service import AlertService, AlertType
        body = "\n".join(f"{n} {tag}: {msg}" for n, tag, msg in rows[:12])
        if len(rows) > 12:
            body += f"\n… and {len(rows) - 12} more"
        AlertService({"alerts": {"enabled": True}}, "AUDIT").send_alert(
            alert_type=AlertType.DATA_QUALITY,
            title=f"Fleet correctness: {len(rows)} break(s)",
            message=f"{today} (ET)\n{body}",
            details={"break_count": len(rows), "date": today})
    except Exception as exc:  # noqa: BLE001 — never mask the exit code
        print(f"  (alert failed: {exc})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/opt/calypso")
    ap.add_argument("--alert", action="store_true",
                    help="publish a DATA_QUALITY alert when breaks are found")
    a = ap.parse_args()
    root = Path(a.root)
    today = os.popen("TZ=America/New_York date +%Y-%m-%d").read().strip()
    print(f"FLEET CORRECTNESS AUDIT — {today} (ET)\n")
    worst = 0
    breaks = []
    for vid in VARIANTS:
        for name, tag, msg in audit(root, vid, today):
            marker = {"OK": "  ok ", "PENDING": "  .. ", "SKIP": "  -- ",
                      "NOTE": "  -- ", "ADJUSTED": "  -- ",
                      "MULTIDAY": "  -- "}.get(tag, "  !! ")
            print(f"{marker}{name:<3} {tag:<8} {msg}")
            if tag in ("I1", "I2", "I3", "I4", "I5"):
                worst = 1
                breaks.append((name, tag, msg))
    print("\nVERDICT:", "BREAKS FOUND — see !! rows" if worst else "all identities hold")
    if worst and a.alert:
        _alert_breaks(breaks, today)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
