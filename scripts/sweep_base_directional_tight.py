#!/usr/bin/env python3
"""Sweep base-entry directional conversion at TIGHT thresholds.

The Apr 19 A/B sweep tested base_entry_downday_callonly_pct at 0.57-1.20%
and found negative EV at every value, so the feature was disabled. That
sweep was downday-only and used coarse thresholds.

This script opens a tighter search space:
  - Tests both DOWNDAY (call-only) and UPDAY (put-only) base conversion
  - Thresholds: 0.10%, 0.15%, 0.20%, 0.25%
  - 4 modes per threshold: baseline, downday-only, upday-only, both
  - Period: Feb 10 - Apr 24 2026 (the full current live era, ~50 trading days)

Output: matrix of net P&L delta vs baseline per (mode, threshold), plus
diagnostic counts (conversions, stops, false-positive rate).
"""
import sys
import time
from datetime import date
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest


START = date(2026, 2, 10)
END   = date(2026, 4, 24)
THRESHOLDS = [0.10, 0.15, 0.20, 0.25]   # percent


def aggregate(results):
    """Compute scenario summary including base-conversion attribution."""
    total_pnl = sum(r.net_pnl for r in results)
    total_entries = sum(
        len([e for e in r.entries if e.entry_type != "skipped"])
        for r in results
    )
    total_stops = sum(
        sum(1 for e in r.entries
            if (e.call_outcome == "stopped" or e.put_outcome == "stopped"))
        for r in results
    )

    base_converted = 0          # E1-E3 flipped (down OR up) by base rule
    base_converted_pnl = 0.0
    base_converted_wins = 0
    base_converted_stops = 0

    for day in results:
        for e in day.entries:
            if e.entry_num > 3:
                continue
            if e.entry_type == "skipped":
                continue
            reason = (e.skip_reason or "").lower()
            is_base_conv = (
                "base-downday" in reason or "base_downday" in reason
                or "base-upday" in reason or "base_upday" in reason
            )
            if is_base_conv:
                base_converted += 1
                base_converted_pnl += e.net_pnl
                if e.net_pnl > 0:
                    base_converted_wins += 1
                if (e.call_outcome == "stopped" or e.put_outcome == "stopped"):
                    base_converted_stops += 1

    return {
        "total_pnl": total_pnl,
        "total_entries": total_entries,
        "total_stops": total_stops,
        "base_converted": base_converted,
        "base_converted_pnl": base_converted_pnl,
        "base_converted_wins": base_converted_wins,
        "base_converted_stops": base_converted_stops,
        "num_days": len(results),
    }


def make_cfg(down_pct=None, up_pct=None):
    """Build config with optional base-entry directional conversion."""
    return replace(
        live_config(),
        start_date=START,
        end_date=END,
        base_entry_downday_callonly_pct=down_pct,
        base_entry_upday_putonly_pct=up_pct,
    )


print("=" * 100)
print(f"BASE-ENTRY DIRECTIONAL TIGHT-THRESHOLD SWEEP")
print(f"Period: {START} → {END}")
print(f"Thresholds tested: {[f'{t}%' for t in THRESHOLDS]}")
print("=" * 100)

# Baseline run first
print(f"\n[1/13] Running baseline (both off)...")
t0 = time.time()
baseline = aggregate(run_backtest(make_cfg(), verbose=False))
print(f"      done in {time.time()-t0:.1f}s — {baseline['num_days']} days, "
      f"${baseline['total_pnl']:+,.0f} total P&L")

# Sweep
results_table = []
run_idx = 1
for mode in ("downday-only", "upday-only", "both"):
    for thresh_pct in THRESHOLDS:
        run_idx += 1
        thresh_frac = thresh_pct / 100.0
        if mode == "downday-only":
            cfg = make_cfg(down_pct=thresh_pct, up_pct=None)
        elif mode == "upday-only":
            cfg = make_cfg(down_pct=None, up_pct=thresh_pct)
        else:
            cfg = make_cfg(down_pct=thresh_pct, up_pct=thresh_pct)

        print(f"\n[{run_idx}/13] mode={mode} threshold={thresh_pct}%...")
        t0 = time.time()
        agg = aggregate(run_backtest(cfg, verbose=False))
        elapsed = time.time() - t0
        delta = agg["total_pnl"] - baseline["total_pnl"]
        print(f"      done in {elapsed:.1f}s — ${agg['total_pnl']:+,.0f} "
              f"(Δ ${delta:+,.0f} vs baseline) — {agg['base_converted']} conversions")
        results_table.append({"mode": mode, "thresh": thresh_pct, **agg, "delta": delta})

# Final report
print()
print("=" * 100)
print("RESULTS MATRIX — Δ P&L vs baseline, per (mode, threshold)")
print("=" * 100)
print(f"\nBaseline P&L (both off):          ${baseline['total_pnl']:+,.0f}")
print(f"Baseline entries / stops:          {baseline['total_entries']} / {baseline['total_stops']}")
print()
print(f"{'Mode':<15} | {'Thresh':>7} | {'Net P&L':>10} | {'Δ vs base':>10} | "
      f"{'Conv':>5} | {'Conv WR%':>9} | {'Conv P&L':>10} | {'Stops':>6}")
print("-" * 100)
for row in results_table:
    wr = (100 * row["base_converted_wins"] / row["base_converted"]
          if row["base_converted"] > 0 else 0)
    avg_conv = (row["base_converted_pnl"] / row["base_converted"]
                if row["base_converted"] > 0 else 0)
    print(f"{row['mode']:<15} | {row['thresh']:>5.2f}% | "
          f"${row['total_pnl']:>+8,.0f} | ${row['delta']:>+8,.0f} | "
          f"{row['base_converted']:>5} | {wr:>8.1f}% | "
          f"${row['base_converted_pnl']:>+8,.0f} | {row['total_stops']:>6}")

# Best-of summary
print()
print("=" * 100)
print("BEST CONFIG PER MODE (highest Δ)")
print("=" * 100)
for mode in ("downday-only", "upday-only", "both"):
    rows = [r for r in results_table if r["mode"] == mode]
    if not rows:
        continue
    best = max(rows, key=lambda r: r["delta"])
    sign = "+" if best["delta"] >= 0 else ""
    verdict = "POSITIVE EV" if best["delta"] > 0 else "still negative"
    print(f"  {mode:<15}: best at {best['thresh']:.2f}% — Δ ${sign}{best['delta']:,.0f} ({verdict})")

print()
overall_best = max(results_table, key=lambda r: r["delta"])
sign = "+" if overall_best["delta"] >= 0 else ""
print(f"OVERALL BEST: {overall_best['mode']} @ {overall_best['thresh']:.2f}% → "
      f"Δ ${sign}{overall_best['delta']:,.0f}")
