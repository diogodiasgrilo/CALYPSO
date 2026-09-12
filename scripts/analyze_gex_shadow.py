#!/usr/bin/env python3
"""Analyze the GEX shadow-gate telemetry (schema v16, added 2026-09-05).

Answers the questions the 2026-09-04 gate audit could not, because the data
did not exist at the time:

  1. How often would each corrected gate variant have decided differently
     from the live gate, split by side and by consumer?
  2. Does the corrected gate finally SEE the put side? (The live gate
     confirmed 0 times in 843 watch ticks inside 25pt of a short, while every
     real stop-loss in the window was put-side.)
  3. How often do the adjuster and overlay predicates disagree with each
     other on the same profile (audit BUG 4)?
  4. What do the qualifying clusters actually look like — width, strength,
     peak offset — i.e. are we vetoing on 345pt tail artifacts or on real
     localized walls?

This is READ-ONLY reporting. It changes nothing and decides nothing; it
exists so the deferred calibration questions can eventually be settled on
recorded evidence instead of argument.

Usage:
    python -m scripts.analyze_gex_shadow                      # default variant_b DB
    python -m scripts.analyze_gex_shadow --db data/variant_c/backtesting.db
    python -m scripts.analyze_gex_shadow --since 2026-09-08
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict


def _rows(con, sql, params=()):
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  (query failed: {e})")
        return []


def analyze(db_path: str, since: str = "") -> int:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        print(f"cannot open {db_path}: {e}")
        return 1

    where, params = ("WHERE date >= ?", (since,)) if since else ("", ())

    decisions = _rows(con, f"SELECT * FROM gex_decisions {where} ORDER BY timestamp", params)
    snaps = _rows(con, f"SELECT * FROM gex_profile_snapshots {where} ORDER BY timestamp", params)

    print("=" * 72)
    print(f"GEX SHADOW ANALYSIS — {db_path}" + (f"  (since {since})" if since else ""))
    print("=" * 72)

    if not decisions:
        print("\nNo gex_decisions rows yet.")
        print("This is EXPECTED until the instrumentation has run through a live")
        print("session — it records only when the Brandon GEX subsystem actually")
        print("evaluates a strike or watches a threatened side. Re-run after a")
        print("trading day. Snapshot rows so far:", len(snaps))
        return 0

    dates = sorted({d["date"] for d in decisions})
    print(f"\n{len(decisions)} decisions over {len(dates)} day(s): {dates[0]} .. {dates[-1]}")
    print(f"{len(snaps)} profile snapshots recorded")

    # ---- 1. live behavior baseline -------------------------------------
    print("\n" + "-" * 72)
    print("1. LIVE GATE BEHAVIOR")
    print("-" * 72)
    by_consumer = Counter((d["consumer"], d["side"]) for d in decisions)
    for (consumer, side), n in sorted(by_consumer.items()):
        sub = [d for d in decisions if d["consumer"] == consumer and d["side"] == side]
        fired = sum(1 for d in sub if d["live_adjuster_predicate"] or d["live_overlay_predicate"])
        print(f"  {consumer:9s} {side:4s}: {n:5d} decisions, {fired:4d} accel-zone confirmations "
              f"({100.0*fired/n:.1f}%)")
    actions = Counter(d["live_action"] for d in decisions if d["consumer"] == "adjuster")
    if actions:
        print(f"  adjuster actions: {dict(actions)}")

    # ---- 2. shadow disagreement ----------------------------------------
    print("\n" + "-" * 72)
    print("2. SHADOW DISAGREEMENT (would a corrected gate have decided differently?)")
    print("-" * 72)
    # variant -> side -> [n_total, n_differ, n_shadow_true_live_false, n_live_true_shadow_false]
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    for d in decisions:
        try:
            shadow = json.loads(d["shadow_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        live = next((s for s in shadow if s["variant"] == "live"), None)
        if live is None:
            continue
        key = "adjuster_predicate" if d["consumer"] == "adjuster" else "overlay_predicate"
        live_v = bool(live[key])
        for s in shadow:
            if s["variant"] == "live":
                continue
            cell = agg[s["variant"]][d["side"]]
            cell[0] += 1
            sv = bool(s[key])
            if sv != live_v:
                cell[1] += 1
                if sv and not live_v:
                    cell[2] += 1
                elif live_v and not sv:
                    cell[3] += 1

    if not agg:
        print("  (no parseable shadow payloads)")
    for variant in sorted(agg):
        print(f"\n  {variant}:")
        for side in sorted(agg[variant]):
            n, differ, gained, lost = agg[variant][side]
            print(f"    {side:4s}: {differ:4d}/{n:<5d} differ ({100.0*differ/n if n else 0:.1f}%)"
                  f"   +{gained} would-confirm-where-live-did-not"
                  f"   -{lost} would-stand-down-where-live-fired")

    # ---- 3. the put-blindness question ---------------------------------
    print("\n" + "-" * 72)
    print("3. PUT-SIDE BLINDNESS (the audit's central structural finding)")
    print("-" * 72)
    puts = [d for d in decisions if d["side"] == "put"]
    if not puts:
        print("  no put-side decisions recorded yet")
    else:
        live_conf = sum(1 for d in puts if d["live_adjuster_predicate"] or d["live_overlay_predicate"])
        print(f"  live gate confirmed on {live_conf}/{len(puts)} put decisions "
              f"({100.0*live_conf/len(puts):.1f}%)")
        for variant in ("flipped_sign", "all_fixes"):
            n = agg.get(variant, {}).get("put", [0, 0, 0, 0])
            if n[0]:
                print(f"  {variant}: would have confirmed on {n[2]} put decisions the live gate missed")

    # ---- 4. predicate divergence (BUG 4) -------------------------------
    print("\n" + "-" * 72)
    print("4. ADJUSTER vs OVERLAY PREDICATE DIVERGENCE (audit BUG 4)")
    print("-" * 72)
    div = 0
    for d in decisions:
        try:
            shadow = json.loads(d["shadow_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        live = next((s for s in shadow if s["variant"] == "live"), None)
        if live and bool(live["adjuster_predicate"]) != bool(live["overlay_predicate"]):
            div += 1
    print(f"  the two predicates disagreed on {div}/{len(decisions)} decisions "
          f"({100.0*div/len(decisions):.1f}%)")
    print("  (a spot-straddling cluster aborts an entry via the adjuster while the")
    print("   overlay reports no accel zone on either side — see defensive_overlay)")

    # ---- 5. what the vetoing clusters look like ------------------------
    print("\n" + "-" * 72)
    print("5. QUALIFYING CLUSTER SHAPE (real localized wall, or tail artifact?)")
    print("-" * 72)
    widths, strengths, npts, offsets = [], [], [], []
    for d in decisions:
        if d["cluster_low"] is None or d["cluster_high"] is None:
            continue
        widths.append(d["cluster_high"] - d["cluster_low"])
        if d["cluster_strength_pct"] is not None:
            strengths.append(d["cluster_strength_pct"])
        if d["cluster_n_strikes"] is not None:
            npts.append(d["cluster_n_strikes"])
        if d["cluster_peak"] is not None and d["reference_strike"] is not None:
            offsets.append(abs(d["cluster_peak"] - d["reference_strike"]))

    def _stat(name, xs, fmt="{:.1f}"):
        if not xs:
            print(f"  {name}: (none)")
            return
        xs = sorted(xs)
        med = xs[len(xs) // 2]
        print(f"  {name}: n={len(xs)} min={fmt.format(xs[0])} "
              f"median={fmt.format(med)} max={fmt.format(xs[-1])}")

    _stat("cluster width (pts)   ", widths)
    _stat("cluster n_strikes     ", npts, "{:.0f}")
    _stat("cluster strength      ", strengths, "{:.2%}")
    _stat("peak offset from short", offsets)
    if npts:
        singles = sum(1 for n in npts if n <= 1)
        print(f"  single-strike clusters (audit BUG 1): {singles}/{len(npts)} "
              f"({100.0*singles/len(npts):.1f}%)")

    print("\n" + "=" * 72)
    print("Reminder: nothing here acts on trading. These numbers are the input to")
    print("the DEFERRED decisions (sign convention, windowed normalization, width")
    print("floor, the 43-vs-39 abort rate) — see bots/hydra/__init__.py 2026-09-05.")
    print("=" * 72)
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/variant_b/backtesting.db",
                    help="backtesting.db to read (default: variant_b, the live seat)")
    ap.add_argument("--since", default="", help="only rows with date >= this (YYYY-MM-DD)")
    args = ap.parse_args()
    return analyze(args.db, args.since)


if __name__ == "__main__":
    sys.exit(main())
