#!/usr/bin/env python3
"""Backtest replay: validate Downday-035 expected P&L against cached ThetaData.

Runs two scenarios over Feb 10 - Apr 10 2026:
  A. Baseline: Upday-035 only (current production before Downday-035 deploy)
  B. Both: Upday-035 + Downday-035 (what we just deployed)
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

print("=" * 80)
print("DOWNDAY-035 BACKTEST REPLAY (Feb 10 - Apr 10, 2026)")
print("=" * 80)


def aggregate(results, label):
    """Aggregate results into a summary dict."""
    total_pnl = sum(r.net_pnl for r in results)
    total_entries = sum(len([e for e in r.entries if e.entry_type != "skipped"]) for r in results)
    winning_days = sum(1 for r in results if r.net_pnl > 0)
    losing_days = sum(1 for r in results if r.net_pnl < 0)

    e6_up = 0
    e6_up_wins = 0
    e6_up_stops = 0
    e6_up_pnl = 0.0
    e6_down = 0
    e6_down_wins = 0
    e6_down_stops = 0
    e6_down_pnl = 0.0
    e6_skipped = 0

    for day in results:
        for e in day.entries:
            if e.entry_num < 6:
                continue
            if e.skip_reason == "upday-035":
                e6_up += 1
                e6_up_pnl += e.net_pnl
                if e.net_pnl > 0:
                    e6_up_wins += 1
                else:
                    e6_up_stops += 1
            elif e.skip_reason == "downday-035":
                e6_down += 1
                e6_down_pnl += e.net_pnl
                if e.net_pnl > 0:
                    e6_down_wins += 1
                else:
                    e6_down_stops += 1
            elif e.skip_reason and "no_trigger" in e.skip_reason:
                e6_skipped += 1

    return {
        "label": label,
        "total_pnl": total_pnl,
        "total_entries": total_entries,
        "winning_days": winning_days,
        "losing_days": losing_days,
        "num_days": len(results),
        "e6_up": e6_up,
        "e6_up_wins": e6_up_wins,
        "e6_up_stops": e6_up_stops,
        "e6_up_pnl": e6_up_pnl,
        "e6_down": e6_down,
        "e6_down_wins": e6_down_wins,
        "e6_down_stops": e6_down_stops,
        "e6_down_pnl": e6_down_pnl,
        "e6_skipped": e6_skipped,
    }


# ── Scenario A: baseline (no Downday-035) ─────────────────────────────
cfg_a = replace(
    live_config(),
    start_date=START,
    end_date=END,
    data_resolution="1min",
    conditional_e6_enabled=False,
    conditional_e7_enabled=False,
    conditional_downday_e6_enabled=False,
    conditional_downday_e7_enabled=False,
    conditional_upday_e6_enabled=True,
    conditional_upday_e7_enabled=False,
)
print("\n--- Scenario A: Upday-035 only (baseline) ---")
results_a = run_backtest(cfg_a, verbose=False)
agg_a = aggregate(results_a, "A: baseline")

# ── Scenario B: Upday-035 + Downday-035 ────────────────────────────────
cfg_b = replace(
    live_config(),
    start_date=START,
    end_date=END,
    data_resolution="1min",
    conditional_e6_enabled=False,
    conditional_e7_enabled=False,
    conditional_downday_e6_enabled=True,
    conditional_downday_e7_enabled=False,
    conditional_downday_threshold_pct=0.0025,  # 0.25% — fraction convention (live_config style), engine does * 100
    conditional_upday_e6_enabled=True,
    conditional_upday_e7_enabled=False,
)
print("\n--- Scenario B: Upday-035 + Downday-035 ---")
results_b = run_backtest(cfg_b, verbose=False)
agg_b = aggregate(results_b, "B: with Downday-035")


# ── Print comparison ──────────────────────────────────────────────────
print()
print("=" * 80)
print("COMPARISON")
print("=" * 80)
print(f"{'Metric':<40} | {'Baseline':>12} | {'With Downday':>14} | {'Delta':>12}")
print("-" * 85)

def row(label, a_val, b_val, fmt="{:.0f}"):
    delta = b_val - a_val
    sign = "+" if delta >= 0 else ""
    va = fmt.format(a_val)
    vb = fmt.format(b_val)
    vd = f"{sign}{fmt.format(delta)}"
    print(f"{label:<40} | {va:>12} | {vb:>14} | {vd:>12}")

row("Total P&L", agg_a["total_pnl"], agg_b["total_pnl"], "${:.2f}")
row("Number of trading days", agg_a["num_days"], agg_b["num_days"], "{:.0f}")
row("Winning days", agg_a["winning_days"], agg_b["winning_days"], "{:.0f}")
row("Losing days", agg_a["losing_days"], agg_b["losing_days"], "{:.0f}")
row("Total entries placed", agg_a["total_entries"], agg_b["total_entries"], "{:.0f}")
print()
print("E6 conditional slot breakdown:")
row("  E6 Upday-035 triggers", agg_a["e6_up"], agg_b["e6_up"], "{:.0f}")
row("  E6 Upday-035 wins", agg_a["e6_up_wins"], agg_b["e6_up_wins"], "{:.0f}")
row("  E6 Upday-035 stops", agg_a["e6_up_stops"], agg_b["e6_up_stops"], "{:.0f}")
row("  E6 Upday-035 P&L", agg_a["e6_up_pnl"], agg_b["e6_up_pnl"], "${:.2f}")
row("  E6 Downday-035 triggers", agg_a["e6_down"], agg_b["e6_down"], "{:.0f}")
row("  E6 Downday-035 wins", agg_a["e6_down_wins"], agg_b["e6_down_wins"], "{:.0f}")
row("  E6 Downday-035 stops", agg_a["e6_down_stops"], agg_b["e6_down_stops"], "{:.0f}")
row("  E6 Downday-035 P&L", agg_a["e6_down_pnl"], agg_b["e6_down_pnl"], "${:.2f}")
row("  E6 skipped (flat day)", agg_a["e6_skipped"], agg_b["e6_skipped"], "{:.0f}")

print()
delta_pnl = agg_b["total_pnl"] - agg_a["total_pnl"]
print(f">>> P&L delta from adding Downday-035: ${delta_pnl:+.2f} <<<")
print(f"    Roadmap estimate: ~+$1,100")


# ── Detailed list of Downday-035 triggers ────────────────────────────
print()
print("=" * 80)
print("DOWNDAY-035 TRIGGER DETAIL (Scenario B)")
print("=" * 80)
print(f"{'Date':>12} | {'SC Strike':>9} | {'Credit':>8} | {'Outcome':>10} | {'Net P&L':>8}")
print("-" * 60)
for day in results_b:
    for e in day.entries:
        if e.skip_reason == "downday-035":
            outcome = e.call_outcome if hasattr(e, 'call_outcome') else "?"
            print(f"  {day.date} | {e.short_call:>8.0f} | ${e.call_credit:>6.0f} | {str(outcome):>10} | ${e.net_pnl:>+6.0f}")
