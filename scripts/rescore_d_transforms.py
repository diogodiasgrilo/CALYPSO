#!/usr/bin/env python3
"""
Re-score strategy D's historical transforms under the commission-inclusive
risk-free threshold (Phase 0.2, 2026-09-09). READ-ONLY.

WHY
---
D's "risk-free transform" is the only mechanism that has ever produced a win:
the calendar leg itself is 0-for-23. So D's entire thesis rests on the
transform gate being real.

Two things were wrong with that gate:

1. It charged NO commission. Worst-case IC value at expiry is exactly
   wing*100*n and the threshold was exactly net_debit + wing*100*n, so
   worst-case realized P&L was exactly $0 BEFORE fees — the invariant had zero
   margin by construction. The transformer's own 4 legs, moreover, booked zero
   commission anywhere in the codebase.

2. It priced from a modelled fill (`dry_run_fill_model`, aggressiveness `agg`)
   that has never been validated against a real order. `agg=0` is mid,
   `agg=1` is full touch. D ran at 1.0 until 2026-07-20 (a silently-ignored
   config scalar) and 0.5 since.

This script answers: do D's transforms still clear the gate once fees are
charged, and at what fill aggressiveness do they stop clearing?

USAGE (on the VM, as calypso)
  sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m scripts.rescore_d_transforms'
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TRANSFORM_LEGS = 4
OPEN_LEGS = 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="d")
    ap.add_argument("--commission-per-leg", type=float, default=None,
                    help="default: read from the variant config")
    a = ap.parse_args()

    db = f"data/variant_{a.variant}/dc_calendar.db"
    if not os.path.exists(db):
        print(f"ERROR: {db} not found", file=sys.stderr)
        return 2

    cpl = a.commission_per_leg
    if cpl is None:
        cfg_path = f"bots/hydra/config/config_variant_{a.variant}.json"
        try:
            cfg = json.load(open(cfg_path))

            def find(o, k):
                if isinstance(o, dict):
                    for kk, v in o.items():
                        if kk == k:
                            return v
                        r = find(v, k)
                        if r is not None:
                            return r
                return None
            cpl = float(find(cfg, "commission_per_leg") or 0.0)
        except Exception as e:
            print(f"ERROR: could not read commission_per_leg ({e})", file=sys.stderr)
            return 2

    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = list(c.execute(
        "SELECT date, strategy_id, transform_credit, net_debit, wing_width, "
        "is_risk_free FROM dc_transformations ORDER BY date"))
    outcomes = {r["strategy_id"]: r for r in c.execute(
        "SELECT strategy_id, terminal_state, realized_pnl FROM dc_outcomes")}
    c.close()

    if not rows:
        print("No transformations recorded.")
        return 0

    n = 1  # D runs 1 contract; dc_transformations does not store it
    fees = (OPEN_LEGS + TRANSFORM_LEGS) * cpl * n

    print(f"\nStrategy {a.variant.upper()} — transform re-score at "
          f"commission_per_leg=${cpl:.2f} ({OPEN_LEGS + TRANSFORM_LEGS} legs = ${fees:.2f})\n")
    print(f"{'strategy_id':22} {'credit':>9} {'old thr':>9} {'old mgn':>8} "
          f"{'new thr':>9} {'new mgn':>8}  {'verdict':<10} outcome")
    print("-" * 104)

    still_clears = 0
    for r in rows:
        credit = r["transform_credit"]
        structural = r["net_debit"] + r["wing_width"] * 100 * n
        old_margin = credit - structural
        new_thr = structural + fees
        new_margin = credit - new_thr
        ok = new_margin >= 0
        still_clears += ok
        o = outcomes.get(r["strategy_id"])
        otxt = (f"{o['terminal_state']} {o['realized_pnl']:+.2f}" if o else "OPEN (unsettled)")
        print(f"{r['strategy_id']:22} {credit:>9.2f} {structural:>9.2f} {old_margin:>8.2f} "
              f"{new_thr:>9.2f} {new_margin:>8.2f}  {'CLEARS' if ok else 'FAILS':<10} {otxt}")

    print("-" * 104)
    print(f"\n{still_clears} of {len(rows)} transforms still clear a "
          f"commission-inclusive gate.\n")

    # ---- Sensitivity to the unvalidated fill assumption -------------------
    # transform_credit = (sell longs) - (buy wings). Crossing further (higher
    # agg) LOWERS the credit. The recorded credit was produced at the agg in
    # force at the time; we cannot recover the raw spread for historical rows
    # (schema v2 only records mid/touch from 2026-09-07). So this reports how
    # much credit erosion each transform can absorb before it fails — a
    # spread-independent way to express fragility.
    print("Fragility — how much credit erosion each transform can absorb")
    print("before it fails the commission-inclusive gate:\n")
    print(f"{'strategy_id':22} {'margin $':>9} {'% of credit':>12}")
    for r in rows:
        structural = r["net_debit"] + r["wing_width"] * 100 * n
        m = r["transform_credit"] - (structural + fees)
        pct = m / r["transform_credit"] * 100 if r["transform_credit"] else 0.0
        print(f"{r['strategy_id']:22} {m:>9.2f} {pct:>11.2f}%")

    print("\nContext: the modelled round-trip crossing cost on these 4 legs runs")
    print("~7-9% of debit at agg=0.5 and ~14-16% at agg=1.0. A transform whose")
    print("margin is a fraction of one percent of its credit is not meaningfully")
    print("distinguishable from zero under an unvalidated fill model.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
