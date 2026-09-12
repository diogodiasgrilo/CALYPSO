#!/usr/bin/env python3
"""Measure LIVE_READINESS_CHECKLIST Gate 4's "N consecutive clean sessions".

WHY THIS EXISTS. Gate 4 asks for "five consecutive full trading sessions without
manual intervention required" and names the trading journal as the source of
truth. That is a judgement call, made by the person who wants the gate to pass,
about days they remember — which is exactly the shape of a check that gets ticked
optimistically. This measures it instead.

THE DISTINCTION THAT MATTERS: an AUTOMATIC restart is not manual intervention.
Every unit is `Restart=always`, so a crash-loop through an IBKR outage produces
dozens of restarts while nobody touches anything — that is the system working.
systemd logs the two differently, and this script keys on that:

    MANUAL     "Stopping <unit>..."            <- a human ran systemctl stop/restart
    AUTOMATIC  "Scheduled restart job, restart counter is at N"

Counting "Started" lines alone (the obvious approach) conflates them and would
have reported ~70 interventions in 10 days when the true number is far smaller.

A day is CLEAN when it has, per Gate 4's own definition:
  * no manual restart (code/config deploy, hand-holding)
  * no CRITICAL / intervention-required alert
  * no ARGUS incident file dated that day

Run on the VM, as root (journalctl needs it):
    sudo /opt/calypso/.venv/bin/python scripts/check_clean_sessions.py
    sudo ... --unit hydra_variant_b --days 21
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, "/opt/calypso")

ARGUS_INCIDENTS = "/opt/calypso/intel/argus/incidents"

#: Logged by systemd when a HUMAN stops/restarts the unit.
RE_MANUAL = re.compile(r"Stopping .*\.\.\.")
#: Logged by systemd when Restart=always brings it back on its own.
RE_AUTO = re.compile(r"Scheduled restart job")
#: The alert classes Gate 4 calls "manual intervention required".
RE_CRITICAL = re.compile(
    r"CRITICAL_INTERVENTION|INTERVENTION REQUIRED|NAKED_POSITION|EMERGENCY_EXIT"
    r"|DAILY_HALT|manual intervention required",
    re.I,
)


def _journal(unit: str, days: int) -> list[str]:
    r = subprocess.run(
        ["journalctl", "-u", unit, "--since", f"{days} days ago",
         "--no-pager", "-o", "short-iso"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"journalctl failed (run with sudo?): {r.stderr.strip()[:200]}")
    return r.stdout.splitlines()


def _day_of(line: str) -> str | None:
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T", line)
    return m.group(1) if m else None


def _traded_days(db: str) -> dict[str, dict]:
    """date -> {'entries': n, 'pnl': x} from the variant's own DB."""
    out: dict[str, dict] = {}
    if not os.path.exists(db):
        return out
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for d, n, p in con.execute(
            "SELECT date, entries_placed, net_pnl FROM daily_summaries"
        ):
            out[d] = {"entries": n or 0, "pnl": p or 0.0}
    finally:
        con.close()
    return out


def _argus_incident_days() -> set[str]:
    days: set[str] = set()
    if not os.path.isdir(ARGUS_INCIDENTS):
        return days
    for fn in os.listdir(ARGUS_INCIDENTS):
        m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", fn)
        if m:
            days.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return days


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", default="hydra_variant_b", help="the LIVE seat's unit")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--need", type=int, default=5, help="Gate 4 requires 5")
    a = ap.parse_args(argv)

    variant = a.unit.replace("hydra_variant_", "") if "variant" in a.unit else ""
    db = f"/opt/calypso/data{'/variant_' + variant if variant else ''}/backtesting.db"

    lines = _journal(a.unit, a.days)
    manual: dict[str, int] = defaultdict(int)
    auto: dict[str, int] = defaultdict(int)
    crit: dict[str, int] = defaultdict(int)
    for ln in lines:
        d = _day_of(ln)
        if not d:
            continue
        if RE_MANUAL.search(ln):
            manual[d] += 1
        elif RE_AUTO.search(ln):
            auto[d] += 1
        if RE_CRITICAL.search(ln):
            crit[d] += 1

    traded = _traded_days(db)
    incidents = _argus_incident_days()
    days = sorted(set(list(manual) + list(auto) + list(crit)
                      + [d for d in traded if d in {_day_of(l) or "" for l in lines}]))
    days = [d for d in days if datetime.strptime(d, "%Y-%m-%d").weekday() < 5]

    print(f"\nGATE 4 — consecutive clean sessions   unit={a.unit}  window={a.days}d")
    print(f"  journal covers {days[0] if days else '—'} … {days[-1] if days else '—'}\n")
    print(f"  {'date':<12}{'traded':<9}{'manual':<9}{'auto':<7}{'crit':<7}{'argus':<8}verdict")
    print("  " + "-" * 66)

    streak, best, verdicts = 0, 0, []
    for d in days:
        t = traded.get(d)
        tr = f"{t['entries']}e" if t else "—"
        inc = "YES" if d in incidents else "-"
        clean = manual[d] == 0 and crit[d] == 0 and d not in incidents
        verdicts.append((d, clean))
        streak = streak + 1 if clean else 0
        best = max(best, streak)
        print(f"  {d:<12}{tr:<9}{manual[d]:<9}{auto[d]:<7}{crit[d]:<7}{inc:<8}"
              f"{'CLEAN' if clean else 'not clean'}")

    # trailing streak = the one that counts
    cur = 0
    for _, clean in reversed(verdicts):
        if clean:
            cur += 1
        else:
            break

    print("\n  " + "-" * 66)
    print(f"  current streak: {cur}    longest in window: {best}    Gate 4 needs: {a.need}")
    if cur >= a.need:
        print(f"  ✅ GATE 4 SATISFIED on this measure ({cur} >= {a.need}).")
    else:
        print(f"  ❌ NOT satisfied — {a.need - cur} more clean session(s) needed.")
    print("\n  'manual' = a human ran systemctl stop/restart (deploys count).")
    print("  'auto'   = Restart=always doing its job — NOT intervention.")
    print("  A deploy breaks the streak by design: that is what a change freeze means.\n")
    return 0 if cur >= a.need else 1


if __name__ == "__main__":
    raise SystemExit(main())
