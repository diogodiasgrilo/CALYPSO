#!/usr/bin/env python3
"""Per-VIX-regime spread-width analysis — does optimal width differ by VIX zone?

Same logic as buffer_per_vix_regime.py but for max_spread_width. If optima differ
clearly per zone, justifies the engine change to support per-zone widths. If
optima are similar across zones, drop the spread-width question entirely.

Approach: rerun the full 6-width backtest, then aggregate entries by VIX zone
(based on per-entry vix_at_entry, not day-open) and compute zone-wise stats.
"""
import sys
import statistics
from datetime import date
from pathlib import Path
from dataclasses import replace
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest


START = date(2025, 12, 1)
END = date(2026, 4, 10)
WIDTHS = [50, 60, 75, 90, 110, 150]
BREAKPOINTS = [18.0, 22.0, 28.0]
ZONE_LABELS = ["Z0 (VIX<18)", "Z1 (18-22)", "Z2 (22-28)", "Z3 (>=28)"]


def vix_zone(vix):
    if vix is None or vix <= 0:
        return None
    for i, bp in enumerate(BREAKPOINTS):
        if vix < bp:
            return i
    return len(BREAKPOINTS)


def run_width(w):
    cfg = replace(
        live_config(),
        start_date=START,
        end_date=END,
        data_resolution="1min",
        max_spread_width=w,
        conditional_upday_e6_enabled=True,
        conditional_downday_e6_enabled=True,
        conditional_downday_threshold_pct=0.0025,
        base_entry_downday_callonly_pct=None,
        fomc_t1_callonly_enabled=False,
        fomc_t1_skip_enabled=True,
        fomc_announcement_skip=False,
    )
    print(f"  [w={w}pt] running...", flush=True)
    return run_backtest(cfg, verbose=False)


def aggregate_zone(results, w, target_zone):
    """Aggregate stats for entries in target_zone only."""
    n_entries = 0
    n_stopped = 0
    total_credit = 0.0
    pnl_per_entry = []
    daily_zone_pnl = defaultdict(float)

    for r in results:
        for e in r.entries:
            if e.entry_type == "skipped":
                continue
            z = vix_zone(getattr(e, 'vix_at_entry', None))
            if z != target_zone:
                continue
            n_entries += 1
            total_credit += (e.call_credit or 0) + (e.put_credit or 0)
            if e.call_outcome == "stopped" or e.put_outcome == "stopped":
                n_stopped += 1
            entry_pnl = (e.net_pnl or 0) if hasattr(e, 'net_pnl') else 0
            pnl_per_entry.append(entry_pnl)
            daily_zone_pnl[r.date] += entry_pnl

    if n_entries == 0:
        return None
    daily_vals = list(daily_zone_pnl.values())
    mean = statistics.mean(daily_vals)
    stdev = statistics.stdev(daily_vals) if len(daily_vals) > 1 else 0
    sharpe = (mean / stdev * (252 ** 0.5)) if stdev > 0 else 0
    avg_cred = total_credit / n_entries
    avg_margin = (w * 100) - avg_cred
    return {
        'entries': n_entries,
        'days': len(daily_zone_pnl),
        'stop_rt': n_stopped / n_entries * 100,
        'avg_cred': avg_cred,
        'avg_margin': avg_margin,
        'net_pnl': sum(pnl_per_entry),
        'ev': sum(pnl_per_entry) / n_entries,
        'sharpe': sharpe,
    }


print("=" * 110)
print(f"PER-VIX-REGIME SPREAD-WIDTH ANALYSIS  |  {START} → {END}")
print("=" * 110)
print()

# Run each width
all_results = {}
for w in WIDTHS:
    all_results[w] = run_width(w)

# Build per-zone tables
per_zone_grids = {}
for z_idx in range(4):
    grid = {}
    for w in WIDTHS:
        grid[w] = aggregate_zone(all_results[w], w, z_idx)
    per_zone_grids[z_idx] = grid

# Print per-zone tables
for z_idx in range(4):
    grid = per_zone_grids[z_idx]
    valid = [(w, g) for w, g in grid.items() if g is not None]
    if not valid:
        print(f"\n── {ZONE_LABELS[z_idx]}: NO ENTRIES ──")
        continue
    n_total = sum(g['entries'] for _, g in valid)
    if n_total < 10:
        print(f"\n── {ZONE_LABELS[z_idx]}: only {n_total} entries — SKIPPING ──")
        continue
    print(f"\n── {ZONE_LABELS[z_idx]} (across all widths: {n_total} entries summed) ──")
    hdr = f"  {'Width':<8}{'Entries':<10}{'Days':<7}{'StopRt%':<10}{'NetPnL':<12}{'EV/entry':<12}{'Sharpe':<10}{'AvgMargin':<12}"
    print(hdr)
    print("  " + "-" * (len(hdr)-2))
    for w in WIDTHS:
        g = grid[w]
        if g is None: continue
        print(f"  {w:<8}{g['entries']:<10}{g['days']:<7}{g['stop_rt']:<10.1f}${g['net_pnl']:<11,.0f}${g['ev']:<11.1f}{g['sharpe']:<10.2f}${g['avg_margin']:<11,.0f}")

    # Best width per zone
    best_ev = max(valid, key=lambda kv: kv[1]['ev'])
    best_sh = max(valid, key=lambda kv: kv[1]['sharpe'])
    print(f"  → Best EV: {best_ev[0]}pt (${best_ev[1]['ev']:.1f}/entry)")
    print(f"  → Best Sharpe: {best_sh[0]}pt ({best_sh[1]['sharpe']:.2f})")

# Stability across zones
print()
print("=" * 110)
print("CROSS-ZONE STABILITY")
print("=" * 110)

best_per_zone_ev = {}
best_per_zone_sh = {}
for z_idx in range(4):
    grid = per_zone_grids[z_idx]
    valid = [(w, g) for w, g in grid.items() if g is not None]
    if not valid or sum(g['entries'] for _, g in valid) < 10:
        continue
    best_per_zone_ev[z_idx] = max(valid, key=lambda kv: kv[1]['ev'])[0]
    best_per_zone_sh[z_idx] = max(valid, key=lambda kv: kv[1]['sharpe'])[0]

print(f"\nBest width per zone (by EV):")
for z, w in best_per_zone_ev.items():
    print(f"  {ZONE_LABELS[z]}: {w}pt")
print(f"\nBest width per zone (by Sharpe):")
for z, w in best_per_zone_sh.items():
    print(f"  {ZONE_LABELS[z]}: {w}pt")

if len(set(best_per_zone_ev.values())) <= 1:
    print(f"\n→ Optima AGREE across zones — no benefit to per-VIX-regime widths.")
elif len(set(best_per_zone_ev.values())) >= 3:
    print(f"\n→ Optima DIVERGE strongly — per-VIX-regime widths potentially valuable.")
else:
    print(f"\n→ Optima differ but only modestly — gain may not justify engine change.")

# Top-3 overlap across zones
print(f"\nTop-3 widths by Sharpe per zone:")
zone_top3 = {}
for z_idx in range(4):
    grid = per_zone_grids[z_idx]
    valid = [(w, g) for w, g in grid.items() if g is not None]
    if not valid or sum(g['entries'] for _, g in valid) < 10:
        continue
    top3 = [w for w, g in sorted(valid, key=lambda kv: -kv[1]['sharpe'])[:3]]
    zone_top3[z_idx] = top3
    print(f"  {ZONE_LABELS[z_idx]}: {top3}")

if len(zone_top3) >= 2:
    common = set.intersection(*[set(t) for t in zone_top3.values()])
    print(f"\nCommon top-3 across all zones: {sorted(common)}")
    if len(common) >= 2:
        print(f"  → Multiple widths perform consistently — robust single-width pick possible.")
    elif len(common) == 1:
        print(f"  → One width is universally good — pick it as global default.")
    else:
        print(f"  → Zero overlap — strong case for per-zone widths.")

print("\n" + "=" * 110)
print("DONE")
print("=" * 110)
