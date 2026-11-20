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

      NOT a break, and reported as a NOTE instead (2026-11-20): a day whose
      whole gross IS the day-level residue — every entry attempt failed and was
      unwound — legitimately has zero entry rows. It reconciles, so it must not
      gate. But it is also not nothing: five such days on the live seat lost up
      to $2,203 each while being recorded in the exact shape of a day the bot
      sat out, and this check printed nothing at all about them. Read
      `entries_failed` (schema v21) to tell the two apart.

  I4  metrics file lifetime total == sum(daily_summaries.net_pnl)
      The cumulative file drifts silently when it accumulates incrementally
      instead of re-deriving; that has happened before (metrics_db_drift).

SETTLEMENT-AWARE. A day with entries but no summary row is normal until
settlement completes (~21:45-22:37 ET); it is reported as PENDING, not as a
break, so an after-close run does not cry wolf every evening.

Read-only. Opens every database with mode=ro.

EXIT CODES (what systemd and the operator gate on):

    0  every variant was audited and every identity holds
    1  an identity BREAK was found — the data disagrees with itself
    2  at least one variant could NOT be audited, and no break was found
       elsewhere. The fleet's numbers are not proven, which is not a pass.

    1 and 2 were the same code until 2026-11-16, and a crash exited 1 too, so
    "the data is wrong" and "we never looked" were indistinguishable — the
    2026-11-13 run died on one variant's unreadable WAL sidecar, never audited
    the variants after it, and presented as an identity break for three days.

Usage:
    .venv/bin/python -m scripts.audit_fleet_correctness
"""
from __future__ import annotations

import argparse
import re
import json
import os
import sqlite3
from pathlib import Path

TOL = 0.01

# "" is variant A. `bl` MUST be here: it has held the live paper seat since
# 2026-10-09, so it is the only variant whose numbers are the money path this
# audit exists to gate. It was absent until 2026-11-16 — the gating check that
# decides when real money may start was auditing eight variants and skipping
# the one that trades. That is the third time a fleet list in this project has
# been written without the live seat (the emergency stop, 2026-09-19; the GCS
# DB backup, ab63407), so: when a variant is added, grep for every list that
# names the others and add it to ALL of them.
VARIANTS = ["", "bl", "b", "c", "d", "e", "f", "g", "h"]


def _ro(p: Path):
    """A read-only connection, or None if the database cannot be READ.

    The probe query is load-bearing, not a sanity check. `sqlite3.connect` is
    lazy: it returns a handle without touching the file, so a database that
    cannot actually be read fails later, on the first execute. For a WAL
    database that is the normal case rather than a corner one — a read-only
    connection still needs the `-shm` sidecar, and when the last writer closes
    cleanly SQLite DELETES `-wal`/`-shm`, so a reader arriving in that window
    must create them. Under the nightly unit's `ProtectSystem=strict` sandbox
    /opt/calypso is read-only, so it cannot, and SQLite raises

        sqlite3.OperationalError: unable to open database file

    which is what killed the 2026-11-13 run outright. Probing HERE moves that
    failure to the one place already designed to handle it, so the caller gets
    None and reports a row instead of taking the whole fleet down with it.
    """
    if not p.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return con
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
        if not db.exists():
            return [(name, "SKIP", f"no backtesting.db at {db}")]
        # Present but unreadable is NOT the same as absent, and conflating them
        # is how a real failure wore a benign label: SKIP is a "nothing to do
        # here" row that does not gate, so an unreadable live-seat database
        # would have reported as routine.
        return [(name, "UNAUDITABLE", f"{db} exists but could not be read "
                                      "(WAL sidecar unavailable under a "
                                      "read-only mount?) — NOT audited")]

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
    have_failed = "entries_failed" in _cols(con, "daily_summaries")
    residue_days = []
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
            # The day reconciles: its whole gross IS the day-level residue
            # (a failed entry attempt's unwind, or an aggregate-only overlay),
            # so no per-entry detail is missing and this is NOT a break.
            #
            # But it is not nothing either, and skipping it silently is how the
            # Nov 2-11 OPRA option-feed lapse stayed invisible for five
            # sessions: on five live-seat days EVERY slot failed, the books
            # recorded `entries_placed=0` with real money lost, and the audit
            # printed nothing at all. Collected into one non-gating NOTE below.
            residue_days.append((d, g))
            continue
        out.append((name, "I5", f"{d}: gross {g:,.2f} but ZERO trade_entries rows "
                                "— per-entry detail lost"))
    if residue_days:
        tot = sum(g for _, g in residue_days)
        nf = 0
        if have_failed:
            nf = con.execute(
                "SELECT COALESCE(SUM(entries_failed), 0) FROM daily_summaries "
                f"WHERE date IN ({','.join('?' * len(residue_days))})",
                [d for d, _ in residue_days]).fetchone()[0] or 0
        out.append((name, "NOTE", (
            f"{len(residue_days)} day(s) with P&L but no entry rows, GROSS "
            f"{tot:,.2f} (net is worse — commission was paid) — reconciled as "
            f"day-level residue (failed-entry unwind "
            f"/ aggregate-only overlay), NOT missing detail"
            + (f"; {nf} recorded failed attempt(s)" if have_failed and nf
               else "; attempt count not recorded (pre-v21 rows)" if have_failed
               else "")
            + f". Days: {', '.join(d for d, _ in residue_days[:8])}"
            + (" …" if len(residue_days) > 8 else ""))))

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
        # "finding(s)", not "break(s)": this now also carries UNAUDITABLE rows,
        # and a variant nobody could audit is not a break in the data.
        AlertService({"alerts": {"enabled": True}}, "AUDIT").send_alert(
            alert_type=AlertType.DATA_QUALITY,
            title=f"Fleet correctness: {len(rows)} finding(s)",
            message=f"{today} (ET)\n{body}",
            details={"break_count": len(rows), "date": today})
    except Exception as exc:  # noqa: BLE001 — never mask the exit code
        print(f"  (alert failed: {exc})")


# An identity break dated BEFORE a variant's accounting was fixed is history,
# not a live defect — the rows were written by code that no longer exists. They
# still deserve to be visible, so they are reported as PRE-FIX rather than
# hidden, but they must not gate: a check that is permanently red is not a
# control, it is a thing people learn to scroll past.
#
# f: 2026-09-18 — F booked every take-profit TWICE, counted no entries at all,
#    and never charged the opening commission; lifetime read $177.90 against a
#    true $45.80. Fixed and documented in bots/hydra/__init__.py (2026-09-18),
#    and the residue re-confirmed as pre-fix history in cbb3e60 / e6bb3bc.
#
# Add an entry ONLY with a dated, committed explanation of what was fixed.
# Anything here is an assertion that the data before the date cannot be
# repaired and is understood — not that it is unimportant.
ACCOUNTING_FIXED_ON = {
    "f": "2026-09-18",
}


# Individual rows that are understood and CANNOT be repaired. A per-row
# register rather than a date cutoff, deliberately: a cutoff silences
# everything before it, including breaks nobody has looked at yet. Each entry
# below is a specific dated incident that was diagnosed on 2026-09-30 and found
# unrepairable — not unimportant, unrepairable.
#
# Repairing these would mean INVENTING trade detail: strikes and credits for
# entries whose rows are gone, from days whose logs rotated away (retention is
# 7 days). That is the same fabrication this project corrected HOMER for.
#
# EVERY entry here is pre-2026-07-24, i.e. before B took the live seat. None of
# them touches the live-seat record, and a test pins that.
KNOWN_IRREPARABLE = {
    ("a", "2026-03-20"): "2 entry rows lost; gross -1,590 reconciles to 3 stops "
                         "+ 2 expired worth +165 by identity, but their strikes "
                         "and credits are unrecoverable (logs rotated)",
    ("a", "2026-04-09"): "e#1 row lost; its credit was ~435 by identity, strikes "
                         "unrecoverable (logs rotated)",
    ("a", "2026-07-01"): "MKT-047 pre-fix: two early_close flattens (-300, -405) "
                         "were never booked into gross. Fixed for good in August "
                         "(B's early_close went -37,500 in July to +255 in August)",
    ("b", "2026-06-05"): "the entire session is absent — 0 trade_entries, 0 stops, "
                         "0 market_ticks, 0 spread_snapshots. Only a summary row "
                         "exists, so there is nothing to reconcile it against",
    ("b", "2026-07-01"): "MKT-047 pre-fix: an early_close flatten (-1,850) was "
                         "never booked into gross, which equals the 4 entry "
                         "credits exactly. B was still DRY-RUN here (live from "
                         "2026-07-24), so no real paper money is affected",
}


def _is_known_irreparable(vid: str, msg: str):
    """The register entry for this row, or None.

    `vid` is "" for variant A (see VARIANTS), not "a" — keying the register on
    "a" made every A entry silently miss, and the gate stayed red on three rows
    that were supposed to be registered. Normalised here rather than in the
    register, so the register reads the way a person would write it.
    """
    vid = vid or "a"
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", msg)
    return KNOWN_IRREPARABLE.get((vid, m.group(1))) if m else None


def _is_pre_fix(vid: str, msg: str) -> bool:
    """True when this row predates the variant's accounting fix."""
    cutoff = ACCOUNTING_FIXED_ON.get(vid or "a")
    if not cutoff:
        return False
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", msg)
    return bool(m and m.group(1) < cutoff)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/opt/calypso")
    ap.add_argument("--alert", action="store_true",
                    help="publish a DATA_QUALITY alert when breaks are found")
    a = ap.parse_args(argv)
    root = Path(a.root)
    today = os.popen("TZ=America/New_York date +%Y-%m-%d").read().strip()
    print(f"FLEET CORRECTNESS AUDIT — {today} (ET)\n")
    worst = 0
    unauditable = 0
    findings = []
    for vid in VARIANTS:
        try:
            rows = audit(root, vid, today)
        except Exception as exc:  # noqa: BLE001
            # Defence in depth behind _ro()'s probe, and the actual lesson of
            # the 2026-11-13 failure: audit() raised, which aborted THIS loop,
            # so every variant after the failing one was never audited and the
            # only trace was an exit code identical to a real identity break.
            # One variant's bad day must not void the fleet's audit, and must
            # never be able to impersonate a finding about the data.
            rows = [((vid or "a").upper(), "UNAUDITABLE",
                     f"audit raised {type(exc).__name__}: {exc} — NOT audited")]
        for name, tag, msg in rows:
            if tag.startswith("I") and _is_pre_fix(vid, msg):
                tag = "PRE-FIX"
            elif tag.startswith("I") and _is_known_irreparable(vid, msg):
                tag = "KNOWN"
                msg = f"{msg}  [{_is_known_irreparable(vid, msg)}]"
            marker = {"OK": "  ok ", "PENDING": "  .. ", "SKIP": "  -- ",
                      "NOTE": "  -- ", "ADJUSTED": "  -- ",
                      "MULTIDAY": "  -- ", "PRE-FIX": "  -- ",
                      "KNOWN": "  -- "}.get(tag, "  !! ")
            print(f"{marker}{name:<3} {tag:<8} {msg}")
            if tag in ("I1", "I2", "I3", "I4", "I5"):
                worst = 1
                findings.append((name, tag, msg))
            elif tag == "UNAUDITABLE":
                unauditable += 1
                findings.append((name, tag, msg))

    if worst:
        verdict = "BREAKS FOUND — see !! rows"
    elif unauditable:
        verdict = (f"NOT PROVEN — {unauditable} variant(s) could not be audited; "
                   "every variant that WAS audited holds")
    else:
        verdict = "all identities hold"
    print("\nVERDICT:", verdict)
    if (worst or unauditable) and a.alert:
        _alert_breaks(findings, today)
    # 0 clean · 1 identity break · 2 could not audit (and found no break).
    #
    # A break keeps 1: systemd gates the nightly unit on it and two tests pin
    # it. An unauditable variant must ALSO fail the unit — silence about the
    # live seat is not a pass — but with its own code, so that "the data is
    # wrong" and "we did not look" can never again arrive as the same signal.
    # A break outranks a gap: it is the actionable one.
    return 1 if worst else (2 if unauditable else 0)


if __name__ == "__main__":
    raise SystemExit(main())
