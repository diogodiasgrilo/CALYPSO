#!/usr/bin/env python3
"""Would the CORRECTED sign convention have made B more money, or less?

THE QUESTION. This codebase assumes dealers are short calls / long puts — the
inverse of the published SpotGamma convention. The 2026-09-04 gate audit called
it a defect and shadowed it rather than flipping it, because it changes which
entries get placed on a live seat.

It has never been scored. The aggregate looks harmless — across 166 recorded
decisions the live gate would block 36 and the corrected one 37 — but they
DISAGREE on 44% of individual decisions. It does not block fewer trades; it
blocks a different set. So "is it a bug?" is the wrong question. The right one
is "is its veto set better than the one we run?", and that is answerable today
because we already know what happened to every entry involved.

HOW EACH SIDE IS SCORED, and why the two are not symmetric:

  live ALLOWS, corrected BLOCKS   The entry WAS placed, so its outcome is a
                                  FACT — `trade_entries.realized_pnl`. Flipping
                                  would have forgone exactly that.

  live BLOCKS, corrected ALLOWS   The entry was NOT placed, so its outcome is a
                                  COUNTERFACTUAL: strikes and credits from
                                  `skipped_entries`, settled against the day's
                                  close, with each side capped at B's A2 stop
                                  (pct_of_width x width) because B does not hold
                                  a breached side to expiry. Held-to-expiry is
                                  also printed, because the difference between
                                  the two is large enough to flip a sign.

ONLY GEX-DRIVEN SKIPS COUNT on the second branch. An entry skipped by the
credit gate would not have been placed by any sign convention, and counting it
would credit the corrected gate with trades it never enables.

THE UNIT IS THE ENTRY, NOT THE SIDE. Under require-both-sides an entry is
placed only if NEITHER side is blocked, so a per-side tally would double-count
the days both sides fired and miss that one veto kills the whole entry.

Usage:
    .venv/bin/python -m scripts.gex_sign_convention_verdict [--arm flipped_sign]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

A2_PCT_OF_WIDTH = 0.40
COMMISSION_PER_CONTRACT_PER_LEG = 0.65


def _spread_value(settle, short_strike, long_strike, is_call):
    if short_strike is None or long_strike is None:
        return None
    width = abs(long_strike - short_strike)
    intrinsic = (settle - short_strike) if is_call else (short_strike - settle)
    return max(0.0, min(intrinsic, width)), width


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="flipped_sign",
                    help="shadow arm to score (flipped_sign | all_fixes | windowed)")
    ap.add_argument("--variant", default="b")
    ap.add_argument("--root", default="/opt/calypso")
    a = ap.parse_args()
    db = Path(a.root) / "data" / f"variant_{a.variant}" / "backtesting.db"
    if not db.exists():
        print(f"no such database: {db}")
        return 1
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    # --- the corrected gate's verdict, per entry -------------------------
    # Last decision per (date, entry, side) wins: a retry is what actually
    # determined the outcome. Then ANY blocked side blocks the entry, which is
    # how require-both-sides behaves.
    per_side = {}
    for date, en, side, ts, sj in con.execute(
            "SELECT date, entry_number, side, timestamp, shadow_json "
            "FROM gex_decisions WHERE consumer='adjuster' ORDER BY timestamp"):
        try:
            arms = {v["variant"]: v for v in json.loads(sj or "[]")}
        except Exception:  # noqa: BLE001
            continue
        if a.arm not in arms:
            continue
        per_side[(date, en, side)] = bool(arms[a.arm].get("adjuster_predicate"))

    corrected_blocks = defaultdict(bool)
    seen_entries = set()
    for (date, en, _side), blocked in per_side.items():
        seen_entries.add((date, en))
        corrected_blocks[(date, en)] |= blocked

    placed = {(d, e): (p or 0.0) for d, e, p in con.execute(
        "SELECT date, entry_number, realized_pnl FROM trade_entries")}
    skipped = {(d, e): r for d, e, *r in con.execute(
        "SELECT date, entry_number, skip_reason, theoretical_short_call, "
        "theoretical_long_call, theoretical_short_put, theoretical_long_put, "
        "estimated_call_credit, estimated_put_credit FROM skipped_entries")}
    closes = {d: c for d, c in con.execute(
        "SELECT date, spx_close FROM daily_summaries")}
    contracts = {d: (k or 7) for d, k in con.execute(
        "SELECT date, contracts_per_entry FROM daily_summaries")}

    forgone, taken, unscorable = [], [], 0
    for key in sorted(seen_entries):
        date, en = key
        blocked = corrected_blocks[key]
        was_placed = key in placed

        if was_placed and blocked:
            # FACT: this entry happened; the corrected gate would have killed it.
            forgone.append((date, en, placed[key]))

        elif (not was_placed) and (not blocked):
            row = skipped.get(key)
            if not row:
                unscorable += 1
                continue
            reason, sc, lc, sp, lp, ccred, pcred = row
            if "GEX" not in str(reason):
                continue          # a credit-gate skip; no convention enables it
            settle = closes.get(date)
            if settle is None or sc is None or sp is None:
                unscorable += 1
                continue
            cvw = _spread_value(settle, sc, lc, True)
            pvw = _spread_value(settle, sp, lp, False)
            if cvw is None or pvw is None:
                unscorable += 1
                continue
            (cv, cw), (pv, pw) = cvw, pvw
            k = contracts.get(date, 7)
            credit_ps = (ccred or 0.0) + (pcred or 0.0)
            comm = COMMISSION_PER_CONTRACT_PER_LEG * k * 4 * 2
            expiry = (credit_ps - cv - pv) * 100.0 * k - comm
            stopped = (credit_ps
                       - min(cv, A2_PCT_OF_WIDTH * cw)
                       - min(pv, A2_PCT_OF_WIDTH * pw)) * 100.0 * k - comm
            taken.append((date, en, stopped, expiry))

    print(f"SIGN-CONVENTION VERDICT — arm '{a.arm}' vs the live gate")
    print(f"variant {a.variant.upper()}  ·  {len(seen_entries)} entries carrying a GEX decision\n")

    f_sum = sum(x[2] for x in forgone)
    print(f"1. ENTRIES THE CORRECTED GATE WOULD HAVE BLOCKED (live placed them)")
    print(f"   these outcomes are FACTS, not estimates")
    print(f"   n = {len(forgone)}    their actual P&L = {f_sum:>12,.2f}")
    if forgone:
        w = sum(1 for x in forgone if x[2] > 0)
        print(f"   of which winners: {w}/{len(forgone)}   "
              f"mean {st.mean(x[2] for x in forgone):+,.2f}")
        for d, e, p in sorted(forgone, key=lambda x: x[2])[:3]:
            print(f"      worst kept: {d} e#{e}  {p:+,.2f}")
        for d, e, p in sorted(forgone, key=lambda x: -x[2])[:3]:
            print(f"      best  lost: {d} e#{e}  {p:+,.2f}")

    t_sum = sum(x[2] for x in taken)
    t_exp = sum(x[3] for x in taken)
    print(f"\n2. ENTRIES THE CORRECTED GATE WOULD HAVE TAKEN (live skipped them on GEX)")
    print(f"   these are COUNTERFACTUALS")
    print(f"   n = {len(taken)}    with the A2 stop = {t_sum:>12,.2f}"
          f"    (held to expiry {t_exp:,.2f})")
    if taken:
        w = sum(1 for x in taken if x[2] > 0)
        print(f"   of which winners: {w}/{len(taken)}   "
              f"mean {st.mean(x[2] for x in taken):+,.2f}")

    net = t_sum - f_sum
    print(f"\n3. NET EFFECT OF FLIPPING")
    print(f"   gained from entries it would take   {t_sum:>12,.2f}")
    print(f"   lost from entries it would block    {-f_sum:>12,.2f}")
    print(f"   {'-' * 48}")
    print(f"   NET                                 {net:>12,.2f}")
    if unscorable:
        print(f"\n   ({unscorable} entries unscorable — missing strikes or close)")
    n = len(forgone) + len(taken)
    if n < 20:
        print(f"\n   ⚠️  n = {n} changed entries. Too few to act on: this is the")
        print("       magnitude and the direction, not a decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
