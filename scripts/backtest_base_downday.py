#!/usr/bin/env python3
"""A/B backtest: base-entry down-day call-only (0.57%) under CURRENT live config.

Scenarios over Feb 10 - Apr 10 2026, both with Downday-035 + Upday-035 enabled
(matches production as of 2026-04-19). Only difference is base-downday conversion
on E1-E3.

  A. base_entry_downday_callonly_pct = None   (feature OFF)
  B. base_entry_downday_callonly_pct = 0.57   (current live value)

Question: given everything else we deploy today (incl. Downday-035), does
base-downday still add P&L? Or has Downday-035 absorbed its edge?
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

print("=" * 85)
print("BASE-DOWNDAY A/B BACKTEST — under CURRENT config incl. Downday-035")
print(f"Period: {START} → {END}")
print("=" * 85)


def aggregate(results, label):
    """Summarize a scenario; track base-downday E1-E3 conversions."""
    total_pnl = sum(r.net_pnl for r in results)
    total_entries = sum(len([e for e in r.entries if e.entry_type != "skipped"]) for r in results)
    winning_days = sum(1 for r in results if r.net_pnl > 0)
    losing_days = sum(1 for r in results if r.net_pnl < 0)

    base_converted = 0   # E1-E3 flipped to call-only by base-downday
    base_converted_wins = 0
    base_converted_stops = 0
    base_converted_pnl = 0.0

    full_ic_base = 0      # E1-E3 left as full IC
    full_ic_base_pnl = 0.0

    for day in results:
        for e in day.entries:
            if e.entry_num > 3:
                continue  # only base entries
            if e.entry_type == "skipped":
                continue
            reason = (e.skip_reason or "").lower()
            # base-downday conversion is logged with skip_reason="base-downday" in engine
            if "base-downday" in reason or "base_downday" in reason:
                base_converted += 1
                base_converted_pnl += e.net_pnl
                if e.net_pnl > 0:
                    base_converted_wins += 1
                else:
                    base_converted_stops += 1
            elif e.entry_type == "full_ic":
                full_ic_base += 1
                full_ic_base_pnl += e.net_pnl

    return {
        "label": label,
        "total_pnl": total_pnl,
        "total_entries": total_entries,
        "winning_days": winning_days,
        "losing_days": losing_days,
        "num_days": len(results),
        "base_converted": base_converted,
        "base_converted_wins": base_converted_wins,
        "base_converted_stops": base_converted_stops,
        "base_converted_pnl": base_converted_pnl,
        "full_ic_base": full_ic_base,
        "full_ic_base_pnl": full_ic_base_pnl,
    }


# Shared config: current live (Downday-035 + Upday-035 enabled, E#1 dropped by regime, etc.)
def shared_cfg():
    return replace(
        live_config(),
        start_date=START,
        end_date=END,
        data_resolution="1min",
        # Upday-035 E6 put-only
        conditional_upday_e6_enabled=True,
        conditional_upday_e7_enabled=False,
        # Downday-035 E6 call-only (deployed 2026-04-19)
        conditional_downday_e6_enabled=True,
        conditional_downday_e7_enabled=False,
        conditional_downday_threshold_pct=0.0025,   # fraction convention (engine does * 100)
        # Legacy E6/E7 off
        conditional_e6_enabled=False,
        conditional_e7_enabled=False,
    )


# ── Scenario A: base-downday OFF ─────────────────────────────────────────
cfg_a = replace(shared_cfg(), base_entry_downday_callonly_pct=None)
print("\n--- Scenario A: base_entry_downday_callonly_pct = None (OFF) ---")
results_a = run_backtest(cfg_a, verbose=False)
agg_a = aggregate(results_a, "A: base-downday OFF")

# ── Scenario B: base-downday ON at 0.57% ─────────────────────────────────
cfg_b = replace(shared_cfg(), base_entry_downday_callonly_pct=0.57)
print("\n--- Scenario B: base_entry_downday_callonly_pct = 0.57 (live) ---")
results_b = run_backtest(cfg_b, verbose=False)
agg_b = aggregate(results_b, "B: base-downday ON (0.57%)")


# ── Print comparison ─────────────────────────────────────────────────────
print()
print("=" * 85)
print("COMPARISON")
print("=" * 85)
print(f"{'Metric':<40} | {'A: OFF':>12} | {'B: ON (0.57%)':>14} | {'Delta':>12}")
print("-" * 85)


def row(label, a_val, b_val, fmt="{:.0f}"):
    delta = b_val - a_val
    sign = "+" if delta >= 0 else ""
    va = fmt.format(a_val)
    vb = fmt.format(b_val)
    vd = f"{sign}{fmt.format(delta)}"
    print(f"{label:<40} | {va:>12} | {vb:>14} | {vd:>12}")


row("Total P&L",             agg_a["total_pnl"],    agg_b["total_pnl"],    "${:.2f}")
row("Number of trading days", agg_a["num_days"],     agg_b["num_days"],     "{:.0f}")
row("Winning days",           agg_a["winning_days"], agg_b["winning_days"], "{:.0f}")
row("Losing days",            agg_a["losing_days"],  agg_b["losing_days"],  "{:.0f}")
row("Total entries placed",   agg_a["total_entries"], agg_b["total_entries"], "{:.0f}")
print()
print("Base entries (E1-E3) breakdown:")
row("  Full-IC base entries",  agg_a["full_ic_base"],      agg_b["full_ic_base"],      "{:.0f}")
row("  Full-IC base P&L",      agg_a["full_ic_base_pnl"],  agg_b["full_ic_base_pnl"],  "${:.2f}")
row("  Base-downday conversions", agg_a["base_converted"],    agg_b["base_converted"],    "{:.0f}")
row("  Base-downday wins",     agg_a["base_converted_wins"],  agg_b["base_converted_wins"],  "{:.0f}")
row("  Base-downday stops",    agg_a["base_converted_stops"], agg_b["base_converted_stops"], "{:.0f}")
row("  Base-downday P&L",      agg_a["base_converted_pnl"],   agg_b["base_converted_pnl"],   "${:.2f}")

print()
delta_pnl = agg_b["total_pnl"] - agg_a["total_pnl"]
print(f">>> P&L delta from base-downday (0.57%) under current config: ${delta_pnl:+.2f} <<<")
print()

if agg_b["base_converted"] > 0:
    wr = 100 * agg_b["base_converted_wins"] / agg_b["base_converted"]
    avg = agg_b["base_converted_pnl"] / agg_b["base_converted"]
    print(f"Base-downday fire details (Scenario B):")
    print(f"  Triggers: {agg_b['base_converted']}")
    print(f"  Win rate: {wr:.1f}%")
    print(f"  Avg P&L per conversion: ${avg:+.2f}")

# ── Detailed list of base-downday conversions in Scenario B ──────────────
print()
print("=" * 85)
print("BASE-DOWNDAY CONVERSION DETAIL (Scenario B)")
print("=" * 85)
print(f"{'Date':>12} | {'Entry':>5} | {'SC Strike':>9} | {'Credit':>8} | {'Outcome':>10} | {'Net P&L':>8}")
print("-" * 75)
for day in results_b:
    for e in day.entries:
        if e.entry_num > 3:
            continue
        reason = (e.skip_reason or "").lower()
        if "base-downday" in reason or "base_downday" in reason:
            outcome = getattr(e, "call_outcome", "?")
            print(f"  {day.date} | E#{e.entry_num:<2} | {e.short_call:>8.0f} | "
                  f"${e.call_credit:>6.0f} | {str(outcome):>10} | ${e.net_pnl:>+6.0f}")
