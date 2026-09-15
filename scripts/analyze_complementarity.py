#!/usr/bin/env python3
"""Run the PRE-REGISTERED complementarity analysis.

Executes docs/STRATEGY_COMPLEMENTARITY_ANALYSIS.md. Read-only.
    sudo -u calypso /opt/calypso/.venv/bin/python scripts/analyze_complementarity.py
"""
from __future__ import annotations

import os
import sys
from statistics import mean

sys.path.insert(0, "/opt/calypso")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.analysis_eras import LIVE_ERA_SINCE  # noqa: E402
from bots.hydra.complementarity import (  # noqa: E402
    BUCKET_LABELS, analyse_variant, load_days, tercile_edges,
)

VARIANTS = [("A", ""), ("B", "variant_b"), ("C", "variant_c"), ("D", "variant_d"),
            ("E", "variant_e"), ("F", "variant_f"), ("G", "variant_g")]
DATA = "/opt/calypso/data"


def main() -> int:
    print(f"\nPRE-REGISTERED COMPLEMENTARITY ANALYSIS — era floor {LIVE_ERA_SINCE}")
    print("Spec: docs/STRATEGY_COMPLEMENTARITY_ANALYSIS.md (written before any result existed)\n")

    all_days, per_variant = [], {}
    for vid, d in VARIANTS:
        db = f"{DATA}/{d}/backtesting.db" if d else f"{DATA}/backtesting.db"
        if not os.path.exists(db):
            continue
        rows = load_days(db, vid, LIVE_ERA_SINCE)
        per_variant[vid] = rows
        all_days.extend(rows)

    if len(all_days) < 9:
        print("  Not enough traded days pooled to form terciles. Stop.")
        return 1

    for feature in ("move_pct", "range_pct"):
        pooled = [getattr(x, feature) for x in all_days]
        edges = tercile_edges(pooled)
        print("=" * 78)
        print(f"FEATURE: {feature}   pooled tercile edges: "
              f"<{edges[0]:.2f}%  |  {edges[0]:.2f}–{edges[1]:.2f}%  |  >{edges[1]:.2f}%")
        print(f"  (pooled over {len(pooled)} variant-days; P&L is PER CONTRACT)\n")
        print(f"  {'var':<5}{'n':<5}" + "".join(f"{b:>26}" for b in BUCKET_LABELS)
              + f"{'perm p':>10}")
        print("  " + "-" * 96)
        for vid, _ in VARIANTS:
            rows = per_variant.get(vid) or []
            if not rows:
                continue
            res = analyse_variant(rows, edges, feature)
            cells = {b.label: b for b in res.buckets}
            line = f"  {vid:<5}{res.n_days:<5}"
            for lab in BUCKET_LABELS:
                b = cells.get(lab)
                if not b:
                    line += f"{'—':>26}"
                else:
                    flag = "~" if b.spans_zero else " "
                    line += f"{f'{b.mean:+,.0f}(n={b.n}){flag}':>26}"
            line += f"{(f'{res.permutation_p:.3f}' if res.permutation_p is not None else '—'):>10}"
            print(line)
            if res.note:
                print(f"        note: {res.note}")
        print("\n  ~ = bootstrap 95% CI spans zero -> NO SIGNAL, do not read a direction.")
        print("  perm p = chance of this quiet-vs-trending spread arising from shuffled labels.")
        print("  42 cells are examined overall, so ~2 will look significant by chance:")
        print("  a lone significant cell is NOT a finding (pre-registration §5).\n")

    # H3 input: what would a complement have to earn?
    b = per_variant.get("B") or []
    if b:
        edges = tercile_edges([x.move_pct for x in all_days])
        trend = [x.pnl_per_contract for x in b if x.move_pct >= edges[1]]
        quiet = [x.pnl_per_contract for x in b if x.move_pct < edges[0]]
        print("=" * 78)
        print("H3 — SPECIFICATION FOR A COMPLEMENT (B, per contract)")
        if trend:
            print(f"  B on trending days : n={len(trend)}  mean ${mean(trend):+,.2f}  "
                  f"total ${sum(trend):+,.2f}")
        if quiet:
            print(f"  B on quiet days    : n={len(quiet)}  mean ${mean(quiet):+,.2f}  "
                  f"total ${sum(quiet):+,.2f}")
        if trend and mean(trend) < 0:
            need = -mean(trend)
            print(f"\n  A complement must earn > ${need:,.2f}/contract on a trending day")
            print(f"  WITHOUT losing more than ${need * len(trend) / max(len(quiet), 1):,.2f}"
                  f"/contract on each quiet day, or it is net negative.")
            print("  ⚠️  The Brandon overlay hedges FAILED this exact bar (2026-09-04):")
            print("      hedge debits $1,925–$2,240 vs an IC-side loss of ~$1,400.")
        else:
            print("\n  B is not measurably negative on trending days at this n —")
            print("  the complement cannot be specified yet. Collect more sessions.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
