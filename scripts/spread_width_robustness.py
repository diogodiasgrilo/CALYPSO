#!/usr/bin/env python3
"""Spread-width robustness check — split 90-day window in halves, re-aggregate.

If best width is stable across halves, the recommendation is robust. If best
diverges across halves, the result is fragile and we should be more cautious.

Reuses run_backtest output (one backtest per width over the full window) and
post-processes by date to compute per-half stats — no need to re-run per half.
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
ACCOUNT_MARGIN_2C = 51_816


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


def aggregate(results, w):
    """Compute aggregate stats from a list of DayResult."""
    if not results:
        return None
    n_days = len(results)
    total_entries = 0
    stopped = 0
    total_credit = 0.0
    for r in results:
        for e in r.entries:
            if e.entry_type == "skipped":
                continue
            total_entries += 1
            total_credit += (e.call_credit or 0) + (e.put_credit or 0)
            if e.call_outcome == "stopped" or e.put_outcome == "stopped":
                stopped += 1
    if total_entries == 0:
        return None
    avg_cred = total_credit / total_entries
    avg_margin = (w * 100) - avg_cred
    stop_rt = stopped / total_entries * 100
    net_pnl = sum(r.net_pnl for r in results)
    ev = net_pnl / total_entries
    daily = [r.net_pnl for r in results]
    mean = statistics.mean(daily)
    stdev = statistics.stdev(daily) if len(daily) > 1 else 0
    sharpe = (mean / stdev * (252 ** 0.5)) if stdev > 0 else 0
    margin_2c3 = 3 * 2 * avg_margin
    return {
        "n_days": n_days,
        "entries": total_entries,
        "stop_rt": stop_rt,
        "avg_cred": avg_cred,
        "avg_margin": avg_margin,
        "net_pnl": net_pnl,
        "ev": ev,
        "sharpe": sharpe,
        "margin_2c3": margin_2c3,
        "fits_2c": margin_2c3 < ACCOUNT_MARGIN_2C,
    }


print("=" * 110)
print(f"SPREAD-WIDTH ROBUSTNESS  |  {START} → {END}  |  splitting in halves")
print("=" * 110)
print()

# ── Run each width once over full window
all_results = {}
for w in WIDTHS:
    all_results[w] = run_width(w)

# ── Split each width's results into halves by date
sample_dates = sorted({r.date for r in all_results[WIDTHS[0]]})
mid_idx = len(sample_dates) // 2
split_date = sample_dates[mid_idx]
print(f"\nSplit date: {split_date} (idx {mid_idx} of {len(sample_dates)})")
print(f"  H1: {sample_dates[0]} → {sample_dates[mid_idx-1]} ({mid_idx} days)")
print(f"  H2: {sample_dates[mid_idx]} → {sample_dates[-1]} ({len(sample_dates)-mid_idx} days)")

results_h1 = {w: [r for r in all_results[w] if r.date < split_date] for w in WIDTHS}
results_h2 = {w: [r for r in all_results[w] if r.date >= split_date] for w in WIDTHS}

# ── Aggregate per (window, width)
agg_full = {w: aggregate(all_results[w], w) for w in WIDTHS}
agg_h1   = {w: aggregate(results_h1[w], w) for w in WIDTHS}
agg_h2   = {w: aggregate(results_h2[w], w) for w in WIDTHS}


def print_table(title, agg):
    print()
    print(f"── {title} ──")
    hdr = f"{'Width':<8}{'Entries':<10}{'StopRt%':<10}{'NetPnL':<12}{'EV/entry':<12}{'Sharpe':<10}{'2c×3':<8}"
    print(hdr)
    print("-" * len(hdr))
    for w in WIDTHS:
        a = agg[w]
        if a is None:
            continue
        fits = "✓" if a["fits_2c"] else "✗"
        print(f"{w:<8}{a['entries']:<10}{a['stop_rt']:<10.1f}${a['net_pnl']:<11,.0f}${a['ev']:<11.1f}{a['sharpe']:<10.2f}{fits:<8}")


print_table(f"FULL WINDOW ({sample_dates[0]} → {sample_dates[-1]}, {len(sample_dates)} days)", agg_full)
print_table(f"HALF 1 ({sample_dates[0]} → {sample_dates[mid_idx-1]}, {mid_idx} days)", agg_h1)
print_table(f"HALF 2 ({sample_dates[mid_idx]} → {sample_dates[-1]}, {len(sample_dates)-mid_idx} days)", agg_h2)


# ── Stability comparison
print()
print("=" * 110)
print("STABILITY ANALYSIS")
print("=" * 110)


def best_by_metric(agg, metric):
    valid = [(w, a) for w, a in agg.items() if a is not None]
    if not valid:
        return None
    return max(valid, key=lambda kv: kv[1][metric])


for metric, label in [("ev", "EV/entry"), ("sharpe", "Sharpe"), ("net_pnl", "NetPnL")]:
    print(f"\nBest by {label}:")
    bf = best_by_metric(agg_full, metric)
    b1 = best_by_metric(agg_h1, metric)
    b2 = best_by_metric(agg_h2, metric)
    print(f"  Full:   width={bf[0]}pt  ({label}={bf[1][metric]:.2f})")
    print(f"  Half 1: width={b1[0]}pt  ({label}={b1[1][metric]:.2f})")
    print(f"  Half 2: width={b2[0]}pt  ({label}={b2[1][metric]:.2f})")
    if b1[0] == b2[0]:
        print(f"  ✓ STABLE — both halves agree on {b1[0]}pt")
    else:
        print(f"  ⚠ DIVERGENT — halves prefer different widths")


# ── Direction consistency: does each half prefer narrowing vs current 110pt?
print()
print("Direction consistency (does each half prefer narrower than current 110pt?):")
for label, agg in [("Full", agg_full), ("Half 1", agg_h1), ("Half 2", agg_h2)]:
    cur_ev = agg[110]["ev"] if agg.get(110) else None
    # Look at the 50pt EV
    n50_ev = agg[50]["ev"] if agg.get(50) else None
    if cur_ev is not None and n50_ev is not None:
        delta = n50_ev - cur_ev
        print(f"  {label}: 50pt EV ${n50_ev:.1f} vs 110pt EV ${cur_ev:.1f}, Δ ${delta:+.1f}/entry "
              f"({'narrower wins' if delta > 0 else 'wider wins'})")


# ── Top-3 overlap
print()
print("Top-3 by Sharpe — overlap:")
top3_h1 = sorted(WIDTHS, key=lambda w: -(agg_h1[w]["sharpe"] if agg_h1[w] else -999))[:3]
top3_h2 = sorted(WIDTHS, key=lambda w: -(agg_h2[w]["sharpe"] if agg_h2[w] else -999))[:3]
print(f"  Half 1 top-3: {top3_h1}")
print(f"  Half 2 top-3: {top3_h2}")
overlap = set(top3_h1) & set(top3_h2)
print(f"  Overlap: {sorted(overlap)} ({len(overlap)} of 3)")


# ── Width comparison side-by-side
print()
print("Width-by-width side-by-side EV/entry across halves:")
print(f"  {'Width':<8}{'Full':<12}{'Half 1':<12}{'Half 2':<12}{'H1-H2 spread':<15}")
for w in WIDTHS:
    a_full = agg_full[w]["ev"] if agg_full[w] else 0
    a_h1 = agg_h1[w]["ev"] if agg_h1[w] else 0
    a_h2 = agg_h2[w]["ev"] if agg_h2[w] else 0
    spread = abs(a_h1 - a_h2)
    print(f"  {w}pt    ${a_full:<11.1f}${a_h1:<11.1f}${a_h2:<11.1f}${spread:<14.1f}")

print("\n" + "=" * 110)
print("DONE")
print("=" * 110)
