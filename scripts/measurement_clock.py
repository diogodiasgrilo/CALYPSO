#!/usr/bin/env python3
"""How many consecutive clean trading days has the money path had?

`docs/GO_LIVE_MASTER.md` §2-quater calls *15 consecutive trading days with no
economics-changing commit* the real go-live gate — "better than any P&L number
because it is not noisy". Until 2026-09-30 nothing computed it. It lived as
prose, so the gate could only be asserted, never stated, and a claim that some
change did or did not reset it was unfalsifiable.

It was not hypothetical: on 2026-09-30 I twice reported changes as "purely
additive, so it does not reset the clock" with no means to check, and one of the
three touched `_check_whipsaw_filter` — a live entry-skip decision.

WHY TWO NUMBERS. An exemption an author grants themselves is not a control. So
this reports the streak BOTH ways:

  * **strict**  — every commit touching an economic path breaks it, no appeals.
  * **claimed** — honours `Telemetry-Only:` / `Equivalence-Proven:` trailers.

A single number would let a judgement call hide inside it. Two make the gap
visible: when they disagree, someone exempted themselves and the report says so,
by commit. The strict number is the one to quote unless the exemptions have been
reviewed by someone other than their author.

Usage:
    python -m scripts.measurement_clock              # report
    python -m scripts.measurement_clock --json       # machine-readable
    python -m scripts.measurement_clock --days 60    # widen the window
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TARGET_DAYS = 15

# Paths that CAN change a trading decision. Default for anything under
# bots/hydra/ is economic — the safe direction, because a miss here silently
# inflates the streak, which is the number the go-live gate reads.
ECONOMIC = [
    re.compile(r"^bots/hydra/.*\.py$"),
    re.compile(r"^bots/hydra/config/config.*\.json$"),
    re.compile(r"^shared/(ib_client|ib_retry|ib_oauth|ib_reconcile|broker_client|"
               r"broker_service|strategy_taxonomy|market_hours|event_calendar|"
               r"technical_indicators|external_price_feed|position_registry)\.py$"),
    re.compile(r"^deploy/hydra.*\.service$"),
    re.compile(r"^deploy/calypso-broker.*\.service$"),
]

# Explicit exceptions inside those trees: they record or render, never decide.
NOT_ECONOMIC = [
    re.compile(r"^bots/hydra/(dc_recorder|dc_status|ls_status|ls_recorder)\.py$"),
    re.compile(r"^bots/hydra/__init__\.py$"),          # version-history prose
    re.compile(r"^shared/(data_recorder|logger_service|alert_service|db_backup|"
               r"secret_manager|sheets_reader|sheets_db_shim|claude_client)\.py$"),
]

TRAILERS = ("Telemetry-Only:", "Equivalence-Proven:")


def economic_paths(paths):
    out = []
    for p in paths:
        if any(r.match(p) for r in NOT_ECONOMIC):
            continue
        if any(r.match(p) for r in ECONOMIC):
            out.append(p)
    return out


def trading_days_back(n_days: int):
    """Most recent trading days, newest first — weekends AND market holidays out."""
    try:
        from shared.market_hours import get_us_market_holidays
    except Exception:
        get_us_market_holidays = None
    hol = {}
    today = date.today()
    for yr in {today.year, today.year - 1}:
        if get_us_market_holidays:
            try:
                hol.update({d: True for d in get_us_market_holidays(yr)})
            except Exception:
                pass
    days, d = [], today
    while len(days) < n_days:
        if d.weekday() < 5 and d not in hol and d.isoformat() not in hol:
            days.append(d)
        d -= timedelta(days=1)
    return days


def commits(window_days: int):
    fmt = "%x00%H%x1f%ad%x1f%s%x1f%b"
    raw = subprocess.run(
        ["git", "log", f"--since={window_days} days ago", "--date=short", f"--format={fmt}",
         "--name-only"],
        capture_output=True, text=True).stdout
    out = []
    for chunk in raw.split("\x00"):
        if not chunk.strip():
            continue
        head, _, files = chunk.partition("\n\n") if "\n\n" in chunk else (chunk, "", "")
        parts = head.split("\x1f")
        if len(parts) < 3:
            continue
        sha, ad, subj = parts[0], parts[1], parts[2]
        body = parts[3] if len(parts) > 3 else ""
        paths = [l.strip() for l in (files or "").splitlines() if l.strip()]
        if not paths:
            lines = chunk.splitlines()
            paths = [l.strip() for l in lines[1:] if l.strip() and "\x1f" not in l]
        econ = economic_paths(paths)
        if not econ:
            continue
        exempt = [t for t in TRAILERS if t.lower() in (subj + "\n" + body).lower()]
        out.append({"sha": sha[:7], "date": ad, "subject": subj[:60],
                    "paths": econ, "exempt": exempt})
    return out


def streak(days, by_date, honour_exemptions: bool):
    n = 0
    for d in days:
        hits = by_date.get(d.isoformat(), [])
        if honour_exemptions:
            hits = [c for c in hits if not c["exempt"]]
        if hits:
            return n, d, hits
        n += 1
    return n, None, []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    cs = commits(a.days)
    by_date = {}
    for c in cs:
        by_date.setdefault(c["date"], []).append(c)
    days = trading_days_back(a.days)

    s_strict, d_strict, h_strict = streak(days, by_date, False)
    s_claim, d_claim, h_claim = streak(days, by_date, True)

    if a.json:
        print(json.dumps({
            "target": TARGET_DAYS, "strict_streak": s_strict,
            "claimed_streak": s_claim,
            "broken_on_strict": d_strict.isoformat() if d_strict else None,
            "exemptions_in_play": s_claim != s_strict,
            "met": s_strict >= TARGET_DAYS,
        }, indent=2))
        return 0

    print("MEASUREMENT CLOCK — consecutive clean trading days on the money path")
    print("=" * 70)
    print(f"  target                 : {TARGET_DAYS}")
    print(f"  STRICT streak          : {s_strict}"
          f"   {'MET' if s_strict >= TARGET_DAYS else f'({TARGET_DAYS - s_strict} more needed)'}")
    print(f"  claimed streak         : {s_claim}"
          + ("   <-- exemptions are doing work here" if s_claim != s_strict else ""))
    if d_strict:
        print(f"  strict streak broken on: {d_strict} ({d_strict.strftime('%A')})")
        for c in h_strict:
            tag = ("  [exempt: " + ",".join(t.rstrip(':') for t in c["exempt"]) + "]") if c["exempt"] else ""
            print(f"      {c['sha']}  {c['subject']}{tag}")
            for p in c["paths"][:4]:
                print(f"           {p}")
    if s_claim != s_strict:
        print()
        print("  ⚠️  The two numbers disagree, so a self-granted exemption is holding")
        print("      the claimed streak up. Quote the STRICT number unless those")
        print("      exemptions have been reviewed by someone other than their author.")
    print()
    print("  recent money-path commits by trading day:")
    for d in days[:12]:
        hits = by_date.get(d.isoformat(), [])
        if hits:
            ex = sum(1 for c in hits if c["exempt"])
            print(f"    {d} {d.strftime('%a')}  {len(hits):2d} commit(s)"
                  + (f"  ({ex} exempt)" if ex else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
