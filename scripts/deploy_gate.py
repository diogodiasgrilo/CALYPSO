#!/usr/bin/env python3
"""Which deploy path does this change have to take?

Answers one question before a deploy: *how must this reach the live seat?*

It exists because a single uniform rule gets one of two opposite cost profiles
wrong. Measured over 120 days and 330 money-path commits (2026-09-30):

  * **59%** of money-path commits are fixes, and **44%** were followed by a
    same-file fix within three days. Shipping new behaviour straight to the
    live seat demonstrably produces rework.
  * But delay is not free either. MKT-011B cost B **$137.20** while it sat
    unfixed; the Brandon overlay put **$6,230** of hedge debit against
    **$315** of real damage before being disabled. A fix to a bug that is
    actively costing money gets more expensive every session it waits.

So the gate is asymmetric, and it answers TWO questions, not one:

  WHEN     immediate (a fix to an active loss) vs after a canary session
           (new behaviour, where delay costs ~nothing)
  VERIFIED how you will know it worked — and this is where the canary has a
           hard limit: `_place_option_order` returns at SAFETY-DRY-01 in dry
           mode, so a dry-run variant NEVER places an order. The order path
           cannot be canaried at all. Changes to it have to be verified
           against the next session's real fills, which is what rung pricing
           actually got.

Advisory, not enforcement: it prints the required path and the reason. A gate
that blocks a genuine hotfix at 15:58 ET would be worse than the problem.

    python -m scripts.deploy_gate                 # classify unpushed commits
    python -m scripts.deploy_gate --ref HEAD~3..  # an explicit range
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Touching any of these means the change can alter how an order is placed,
# priced, cancelled or reconciled — the class a dry-run canary cannot see.
ORDER_PATH_SYMBOLS = (
    "_place_option_order", "place_and_wait_for_fill", "place_order",
    "cancel_order", "modify_order", "_close_leg_order", "_correct_over_fill",
    "_unwind_partial_entry", "_ensure_coid", "rung", "_execute_stop_loss",
)
ORDER_PATH_FILES = re.compile(
    r"^(shared/ib_client\.py|shared/broker_client\.py|bots/hydra/base_strategy\.py)$")

FIXWORD = re.compile(r"^(fix|hotfix|revert)\b", re.I)

MONEY_PATH = re.compile(
    r"^(bots/hydra/.*\.py|bots/hydra/config/config.*\.json|"
    r"shared/(ib_client|ib_retry|ib_oauth|broker_client|broker_service|"
    r"strategy_taxonomy|market_hours|event_calendar)\.py)$")
NOT_MONEY = re.compile(
    r"^(bots/hydra/(dc_recorder|dc_status|ls_status|ls_recorder|__init__)\.py)$")


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def classify(ref: str):
    rng = ref or "@{u}..HEAD"
    raw = _git("log", rng, "--format=%x00%H%x1f%s", "--name-only")
    out = []
    for chunk in raw.split("\x00"):
        if not chunk.strip():
            continue
        lines = chunk.splitlines()
        parts = lines[0].split("\x1f")
        if len(parts) < 2:
            continue
        sha, subj = parts[0][:7], parts[1]
        files = [l.strip() for l in lines[1:] if l.strip()]
        money = [f for f in files
                 if MONEY_PATH.match(f) and not NOT_MONEY.match(f)]
        if not money:
            continue
        diff = _git("show", sha, "--unified=0")
        touched = [s for s in ORDER_PATH_SYMBOLS
                   if re.search(r"^[+-].*\b" + re.escape(s), diff, re.M)]
        order_path = bool(touched) or any(ORDER_PATH_FILES.match(f) and touched
                                          for f in money)
        out.append({"sha": sha, "subject": subj, "files": money,
                    "is_fix": bool(FIXWORD.match(subj)),
                    "order_path": order_path, "symbols": touched[:4]})
    return out


def verdict(commits):
    if not commits:
        return ("NO MONEY-PATH CHANGE",
                "Nothing here reaches a trading decision — deploy normally.",
                "n/a")
    any_order = any(c["order_path"] for c in commits)
    all_fix = all(c["is_fix"] for c in commits)

    when = ("IMMEDIATE" if all_fix else "AFTER ONE CANARY SESSION")
    why_when = ("every commit is a fix — a bug that is actively costing money "
                "gets more expensive each session it waits (MKT-011B: $137.20)"
                if all_fix else
                "this batch introduces new behaviour, where delay costs ~nothing "
                "and 44% of money-path commits needed a same-file fix within 3 days")
    how = ("NEXT SESSION'S REAL FILLS — a canary cannot verify this"
           if any_order else "A CANARY VARIANT for one full session")
    return when, why_when, how


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=None,
                    help="git range (default: unpushed commits)")
    a = ap.parse_args(argv)

    cs = classify(a.ref)
    when, why, how = verdict(cs)

    print("DEPLOY GATE")
    print("=" * 66)
    if not cs:
        print(f"  {when}\n  {why}")
        return 0
    print(f"  money-path commits : {len(cs)}")
    for c in cs:
        tags = []
        if c["is_fix"]:
            tags.append("fix")
        if c["order_path"]:
            tags.append("ORDER-PATH:" + ",".join(c["symbols"]))
        print(f"    {c['sha']}  {c['subject'][:52]}"
              + (f"   [{' | '.join(tags)}]" if tags else "   [new behaviour]"))
    print()
    print(f"  WHEN     : {when}")
    print(f"             {why}")
    print(f"  VERIFIED : {how}")
    if any(c["order_path"] for c in cs):
        print()
        print("  ⚠️  The order path is in this batch. `_place_option_order` returns")
        print("      at SAFETY-DRY-01 in dry mode, so NO dry-run variant places an")
        print("      order — a canary would tell you nothing about it. Deploy after")
        print("      the close and check the next session's fills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
