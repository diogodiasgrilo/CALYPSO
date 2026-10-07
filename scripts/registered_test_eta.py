#!/usr/bin/env python3
"""When do the registered out-of-sample tests actually complete?

"Wait for the out-of-sample data" was said repeatedly without anyone working
out when that data arrives. The answer is not uniform, and the spread changes
what the right decision is: a test two weeks out is worth waiting for
passively, one three months out deserves to be decided on rather than drifted
into.

Projects each registered test's remaining out-of-sample requirement against
the rate it has actually accrued at over B's live era. Rates are measured, not
assumed, and the projection is deliberately naive — it extends the observed
rate and says so, rather than modelling a trend it has no data to fit.

The accrual rate is itself the finding. B places ~2.08 entries/day across 69%
of days, and e#4 fires on 0.39/day, so a test needing 27 e#4 entries is a
quarter away while one needing 25 GEX vetoes is a fortnight. Same phrase,
"wait for the data", two very different decisions.

    python -m scripts.registered_test_eta --variant b
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.measurement_clock import economic_paths  # noqa: E402

LIVE_ERA = "2026-07-24"      # the B/C live-seat swap
PREREG_CUTOFF = "2026-09-29"  # PREREG_GEX_GATE / PREREG_SLOT_PRUNE: later only

# ---------------------------------------------------------------------------
# Which code paths each registered test's CONCLUSION depends on.
#
# Counting out-of-sample observations says how MUCH data has accrued. It says
# nothing about whether the system generating it stayed still — so this file
# would happily report "25/25, go read the result" on a sample collected across
# a change to the very behaviour under test. The 2026-10-01 chain-truncation
# fix (`1ab92221`, max_pages 4 -> 20) is the live example: it altered the delta
# ladder the GEX adjuster picks strikes from, INSIDE the GEX test's
# out-of-sample window. It was caught by hand. The next one might not be.
#
# `direct`  — paths whose behaviour the test is literally measuring.
# `shared`  — paths that could matter but are touched by nearly every commit;
#             reported separately so a real signal is not buried in them.
#
# This flags commits for REVIEW. It deliberately does NOT rule on whether a
# flagged commit invalidates a test: that needs a data check (for 1ab92221,
# whether truncated-chain days contributed any vetoes — they contributed 2
# decisions and 0 vetoes, so it was clean). Automating detection is the value;
# automating the verdict would be false precision.
# ---------------------------------------------------------------------------
TEST_DEPS = {
    "GEX gate": {
        "direct": [re.compile(r"^bots/hydra/brandon/"), re.compile(r"gex")],
        "shared": [re.compile(r"^bots/hydra/(strategy|base_strategy)\.py$")],
        "measures": "veto decisions + delta-target strike selection",
    },
    "e#4 slot prune": {
        "direct": [re.compile(r"^bots/hydra/strategy\.py$"),
                   re.compile(r"^bots/hydra/config/config.*\.json$")],
        "shared": [re.compile(r"^bots/hydra/(base_strategy)\.py$"),
                   re.compile(r"^bots/hydra/brandon/")],
        "measures": "which slots fire, and the per-slot P&L",
    },
    "one-entry-a-day": {
        "direct": [re.compile(r"^bots/hydra/strategy\.py$"),
                   re.compile(r"^bots/hydra/config/config.*\.json$")],
        "shared": [re.compile(r"^bots/hydra/(base_strategy)\.py$"),
                   re.compile(r"^bots/hydra/brandon/")],
        "measures": "entry count per day",
    },
}


def commits_since(cutoff: str, root: str):
    """Economic commits strictly AFTER `cutoff` (YYYY-MM-DD), newest first.

    Own git call rather than `measurement_clock.commits()` because that one
    takes a rolling day-window and runs in the process CWD; a pre-registration
    needs an absolute cut-off DATE and an explicit repo root. The CLASSIFICATION
    is shared — `economic_paths` stays the single source of truth for what can
    change a trading decision, so the two tools cannot drift apart.
    """
    fmt = "%x00%H%x1f%ad%x1f%s"
    try:
        proc = subprocess.run(
            ["git", "log", f"--since={cutoff}", "--date=short", f"--format={fmt}",
             "--numstat"],
            capture_output=True, text=True, cwd=root, timeout=30)
    except Exception:
        return None                      # git binary missing / timeout
    if proc.returncode != 0:
        # git ran and REFUSED (not a repo, bad revision, ...). It does not raise,
        # so without this the empty stdout parsed to [] and the caller printed
        # "all tests clean" for a check that never ran. A check that cannot run
        # must never read as clean.
        return None
    raw = proc.stdout
    out = []
    for chunk in raw.split("\x00"):
        if not chunk.strip():
            continue
        head, _, files = chunk.partition("\n\n") if "\n\n" in chunk else (chunk, "", "")
        parts = head.split("\x1f")
        if len(parts) < 3:
            continue
        sha, ad, subj = parts[0][:7], parts[1], parts[2]
        if ad <= cutoff:                 # --since is inclusive-ish; be strict
            continue
        # numstat rows are "added<TAB>deleted<TAB>path"; binary files give "-".
        churn = {}
        for line in (files or "").splitlines():
            bits = line.split("\t")
            if len(bits) != 3:
                continue
            add, dele, path = bits
            try:
                churn[path.strip()] = int(add) + int(dele)
            except ValueError:
                churn[path.strip()] = 0          # binary
        econ = economic_paths(list(churn))
        if econ:
            out.append({"sha": sha, "date": ad, "subject": subj[:52],
                        "paths": econ, "churn": churn})
    return out


def integrity_report(tests, cutoff: str, root: str, show_all: bool = False):
    """Per test: did anything touch what it measures, since its cut-off?"""
    cs = commits_since(cutoff, root)
    print(f"PRE-REGISTRATION INTEGRITY — did the system move while the data accrued?")
    if cs is None:
        print(f"  git unavailable at {root} — integrity NOT checked (treat as UNKNOWN,")
        print("  not as clean).")
        return
    if not cs:
        print(f"  no economic commits since {cutoff} — all tests clean.")
        return
    SHOW = 10**6 if show_all else 5
    for name, *_rest in tests:
        dep = TEST_DEPS.get(name)
        if not dep:
            continue
        direct, shared = [], []
        for c in cs:
            hit = [p for p in c["paths"] if any(r.search(p) for r in dep["direct"])]
            if hit:
                # Rank by LINES CHANGED in the dependency files only. A 3-line
                # logging tweak and a strike-selection rewrite both "touch
                # strategy.py"; without this the real one is buried among the
                # trivial ones and the whole report gets ignored — the failure
                # mode this tool exists to prevent.
                direct.append((sum(c["churn"].get(p, 0) for p in hit), hit, c))
            elif any(r.search(p) for p in c["paths"] for r in dep["shared"]):
                shared.append(c)
        direct.sort(key=lambda t: -t[0])
        churn_total = sum(t[0] for t in direct)
        verdict = "REVIEW" if direct else ("look" if shared else "clean")
        print(f"  {name}  — measures {dep['measures']}")
        print(f"      direct: {len(direct):<3} commits / {churn_total:>5} lines in dep files"
              f"   shared-file: {len(shared):<3}   -> {verdict}")
        for lines, hit, c in direct[:SHOW]:
            where = ", ".join(os.path.basename(p) for p in hit[:2])
            print(f"        {lines:>5}L  {c['sha']}  {c['date']}  {c['subject'][:44]}  [{where}]")
        if len(direct) > SHOW:
            rest = sum(t[0] for t in direct[SHOW:])
            print(f"        ... +{len(direct)-SHOW} more, {rest} lines "
                  f"(smaller than the above; listed by --all)")
        if direct:
            print("        ^ ranked by churn, NOT by importance. A flag is not a")
            print("          verdict: assess against the DATA before reading the")
            print("          result, the way 1ab9222 was cleared (truncated-chain")
            print("          days contributed 2 decisions and 0 vetoes).")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="b")
    ap.add_argument("--root", default="/opt/calypso")
    ap.add_argument("--all", action="store_true",
                    help="list every flagged commit, not just the 5 largest")
    a = ap.parse_args(argv)

    db = (os.path.join(a.root, "data", "backtesting.db") if a.variant in ("", "a")
          else os.path.join(a.root, "data", f"variant_{a.variant}", "backtesting.db"))
    if not os.path.exists(db):
        print(f"no database at {db}")
        return 2
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    def one(q, args=()):
        try:
            return con.execute(q, args).fetchone()[0] or 0
        except Exception:
            return 0

    days = one("SELECT COUNT(*) FROM daily_summaries WHERE date>=?", (LIVE_ERA,))
    if not days:
        print("no live-era days recorded")
        return 0
    traded = one("SELECT COUNT(*) FROM daily_summaries WHERE date>=? AND entries_placed>0", (LIVE_ERA,))
    entries = one("SELECT COUNT(*) FROM trade_entries WHERE date>=?", (LIVE_ERA,))
    e4 = one("SELECT COUNT(*) FROM trade_entries WHERE date>=? AND entry_number=4", (LIVE_ERA,))
    vetoes = one("SELECT COUNT(*) FROM gex_decisions WHERE live_action='SKIP'")
    gex_days = one("SELECT COUNT(DISTINCT substr(timestamp,1,10)) FROM gex_decisions")
    gex_reach = one("SELECT COUNT(*) FROM (SELECT 1 FROM daily_summaries ds WHERE ds.date>=? "
                    "AND EXISTS (SELECT 1 FROM gex_decisions gx "
                    "WHERE substr(gx.timestamp,1,10)=ds.date))", (LIVE_ERA,))

    print(f"ACCRUAL RATES — variant {a.variant.upper()}, live era since {LIVE_ERA}")
    print("=" * 74)
    print(f"  days of record         : {days}")
    print(f"  traded days            : {traded}  ({100.0*traded/days:.0f}%)")
    print(f"  entries placed         : {entries:<5} -> {entries/days:.2f}/day")
    print(f"  e#4 entries            : {e4:<5} -> {e4/days:.2f}/day")
    print(f"  GEX vetoes (SKIP)      : {vetoes:<5} over {gex_days} recorded days"
          f" -> {vetoes/max(gex_days,1):.2f}/day")
    print(f"  days reaching the GEX adjuster at all : {gex_reach}/{days}"
          f"  ({100.0*gex_reach/days:.0f}%)")
    print()

    oos_v = one("SELECT COUNT(*) FROM gex_decisions WHERE live_action='SKIP' "
                "AND substr(timestamp,1,10) > ?", (PREREG_CUTOFF,))
    oos_e4 = one("SELECT COUNT(*) FROM trade_entries WHERE entry_number=4 AND date > ?",
                 (PREREG_CUTOFF,))
    oos_d = one("SELECT COUNT(*) FROM daily_summaries WHERE date > ? AND entries_placed>0",
                (PREREG_CUTOFF,))

    tests = [
        ("GEX gate", oos_v, 25, vetoes / max(gex_days, 1),
         "docs/PREREG_GEX_GATE_2026_09_29.md"),
        ("e#4 slot prune", oos_e4, 27, e4 / days,
         "docs/PREREG_SLOT_PRUNE_2026_09_29.md"),
        ("one-entry-a-day", oos_d, 40, traded / days,
         "docs/PREREG_SLOT_PRUNE_2026_09_29.md"),
    ]
    print(f"OUT-OF-SAMPLE PROGRESS — only data after {PREREG_CUTOFF} counts")
    print(f"  {'test':<18}{'have':>5}{'need':>6}{'rate/day':>10}{'days left':>11}{'~weeks':>8}")
    print("  " + "-" * 60)
    for name, have, need, rate, _doc in tests:
        rem = max(0, need - have)
        if rem == 0:
            print(f"  {name:<18}{have:>5}{need:>6}{rate:>10.2f}{'READY':>11}{'-':>8}")
        elif rate <= 0:
            print(f"  {name:<18}{have:>5}{need:>6}{rate:>10.2f}{'never@0':>11}{'-':>8}")
        else:
            d = rem / rate
            print(f"  {name:<18}{have:>5}{need:>6}{rate:>10.2f}{d:>11.0f}{d/5:>8.1f}")
    print()
    print("  Naive projection: it extends the OBSERVED rate and models no trend,")
    print("  because there is no basis for fitting one. Treat it as an order of")
    print("  magnitude — 'a fortnight' vs 'a quarter' is the decision-relevant")
    print("  distinction, not the exact day.")
    print()
    print("  The accrual rate is the binding constraint, not the thresholds: every")
    print("  skipped entry is a day added to all three. Anything that raises B's")
    print("  placement rate shortens all of them at once.")
    print()
    integrity_report(tests, PREREG_CUTOFF, a.root, show_all=a.all)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
