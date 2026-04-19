#!/usr/bin/env python3
"""Threshold sweep for base-entry down-day call-only under CURRENT live config.

Runs the full backtest engine (Feb 10 - Apr 10 2026) for several thresholds
with Downday-035 + Upday-035 enabled (matches production 2026-04-19).

Scenarios:
  OFF, 0.57 (live), 0.70, 0.80, 1.00, 1.20

For each scenario, reports:
  - Total P&L
  - Full-IC base count (higher = fewer conversions)
  - Implied conversions (= baseline_full_ic - this_full_ic)
  - Winning / losing days
"""
import sys
from datetime import date
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import live_config
from backtest.engine import run_backtest


START = date(2026, 2, 10)
END = date(2026, 4, 10)

THRESHOLDS = [None, 0.57, 0.70, 0.80, 1.00, 1.20]

print("=" * 95)
print("BASE-DOWNDAY THRESHOLD SWEEP — Feb 10 → Apr 10 2026, under current config")
print("(Downday-035 + Upday-035 enabled, E#1 dropped by VIX regime)")
print("=" * 95)


def shared_cfg():
    return replace(
        live_config(),
        start_date=START,
        end_date=END,
        data_resolution="1min",
        conditional_upday_e6_enabled=True,
        conditional_upday_e7_enabled=False,
        conditional_downday_e6_enabled=True,
        conditional_downday_e7_enabled=False,
        conditional_downday_threshold_pct=0.0025,
        conditional_e6_enabled=False,
        conditional_e7_enabled=False,
    )


def summarize(results):
    total_pnl = sum(r.net_pnl for r in results)
    winning_days = sum(1 for r in results if r.net_pnl > 0)
    losing_days = sum(1 for r in results if r.net_pnl < 0)

    # Count E1-E3 full-ICs vs call-only (the engine sets entry_type="call_only"
    # when base-downday converts).
    full_ic_base = 0
    call_only_base = 0
    put_only_base = 0
    for day in results:
        for e in day.entries:
            if e.entry_num > 3 or e.entry_type == "skipped":
                continue
            if e.entry_type == "full_ic":
                full_ic_base += 1
            elif e.entry_type == "call_only":
                call_only_base += 1
            elif e.entry_type == "put_only":
                put_only_base += 1
    return {
        "pnl": total_pnl,
        "win_days": winning_days,
        "loss_days": losing_days,
        "full_ic_base": full_ic_base,
        "call_only_base": call_only_base,
        "put_only_base": put_only_base,
    }


rows = []
baseline_full_ic = None
for thr in THRESHOLDS:
    label = "OFF" if thr is None else f"{thr:.2f}%"
    print(f"\n--- Running scenario: base-downday = {label} ---")
    cfg = replace(shared_cfg(), base_entry_downday_callonly_pct=thr)
    results = run_backtest(cfg, verbose=False)
    s = summarize(results)
    if baseline_full_ic is None:
        baseline_full_ic = s["full_ic_base"]
    s["implied_conversions"] = baseline_full_ic - s["full_ic_base"]
    s["label"] = label
    rows.append(s)


# ── Results table ────────────────────────────────────────────────────────
print()
print("=" * 95)
print("RESULTS")
print("=" * 95)
header = f"{'Threshold':<10} | {'Total P&L':>10} | {'Δ vs OFF':>10} | {'Win d':>5} | {'Loss d':>6} | {'Full-IC':>7} | {'Call-only':>9} | {'Conv':>4}"
print(header)
print("-" * len(header))

baseline_pnl = rows[0]["pnl"]
for s in rows:
    delta = s["pnl"] - baseline_pnl
    sign = "+" if delta >= 0 else ""
    print(
        f"{s['label']:<10} | "
        f"${s['pnl']:>8.0f} | "
        f"{sign}${delta:>7.0f} | "
        f"{s['win_days']:>5} | "
        f"{s['loss_days']:>6} | "
        f"{s['full_ic_base']:>7} | "
        f"{s['call_only_base']:>9} | "
        f"{s['implied_conversions']:>4}"
    )

print()
# Best by total P&L
best = max(rows, key=lambda r: r["pnl"])
print(f">>> Best threshold by raw P&L: {best['label']} (${best['pnl']:.0f})")

# Compare each to OFF
print()
print("Interpretation guide:")
print("  - Higher threshold = fires less often = more full-IC base entries preserved")
print("  - 'Conv' = E1-E3 entries converted from IC → call-only by this threshold")
print("  - Δ vs OFF < 0 means feature LOST money at that threshold in this period")
print("  - Live is ~34% of backtest (rough rule) — a $500 backtest delta ≈ $170 live")
