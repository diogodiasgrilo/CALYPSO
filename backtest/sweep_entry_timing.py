"""
Sweep: Entry timing — start time, interval, and number of entries.

Generates all valid combinations where the last entry is before 13:30 ET.
Tests each schedule against the full real-Greeks dataset.

Current live: start=10:15, interval=30min, entries=5 → [10:15, 10:45, 11:15, 11:45, 12:15]

Run: python -m backtest.sweep_entry_timing
"""
import csv
import statistics
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)

# ── Grid parameters ──────────────────────────────────────────────────────────
# Start times (minutes from midnight ET)
START_TIMES_MIN = [
    600,   # 10:00
    615,   # 10:15
    630,   # 10:30
    645,   # 10:45
    660,   # 11:00
]

# Interval between entries (minutes)
INTERVALS = [15, 20, 30, 45, 60]

# Number of entries
NUM_ENTRIES = [3, 4, 5, 6, 7]

# Last entry must be at or before this time (minutes from midnight)
LAST_ENTRY_CUTOFF = 13 * 60 + 30  # 13:30

# ── Generate valid schedules ─────────────────────────────────────────────────

def min_to_hhmm(m: int) -> str:
    return f"{m // 60}:{m % 60:02d}"

def generate_schedules():
    """Generate all valid (label, entry_times) tuples."""
    schedules = []
    for start in START_TIMES_MIN:
        for interval in INTERVALS:
            for n in NUM_ENTRIES:
                last_entry = start + (n - 1) * interval
                if last_entry > LAST_ENTRY_CUTOFF:
                    continue
                times = [min_to_hhmm(start + i * interval) for i in range(n)]
                label = f"s{min_to_hhmm(start)}_i{interval}m_n{n}"
                schedules.append((label, times, start, interval, n))
    return schedules


def build_cfg(entry_times: List[str]) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date      = START_DATE
    cfg.end_date        = END_DATE
    cfg.use_real_greeks = True
    cfg.entry_times     = entry_times
    # Disable conditional entries (already confirmed OFF)
    cfg.conditional_e7_enabled = False
    cfg.conditional_upday_e6_enabled = False
    return cfg


def summarise(results: List[DayResult], label: str, start: int, interval: int, n: int, times: List[str]) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl  = sum(daily_pnls)
    total_days = len(results)
    win_days   = sum(1 for p in daily_pnls if p > 0)
    placed     = sum(r.entries_placed for r in results)
    skipped    = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)

    mean  = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum += p; peak = max(peak, cum); max_dd = max(max_dd, peak - cum)
    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    return {
        "label": label,
        "schedule": " ".join(times),
        "start": min_to_hhmm(start),
        "interval": interval,
        "num_entries": n,
        "days": total_days,
        "win_rate": win_days / total_days * 100 if total_days else 0,
        "total_pnl": total_pnl,
        "mean_daily": mean,
        "stdev_daily": stdev,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "calmar": calmar,
        "placed": placed,
        "skipped": skipped,
        "total_stops": total_stops,
        "stop_rate": total_stops / placed * 100 if placed else 0,
    }


if __name__ == "__main__":
    schedules = generate_schedules()
    total = len(schedules)

    print(f"Sweep: Entry Timing  |  {total} valid combinations")
    print(f"Start times: {[min_to_hhmm(s) for s in START_TIMES_MIN]}")
    print(f"Intervals:   {INTERVALS} min")
    print(f"Num entries: {NUM_ENTRIES}")
    print(f"Cutoff:      {min_to_hhmm(LAST_ENTRY_CUTOFF)}")
    print(f"Period:      {START_DATE} → {END_DATE}  |  Real Greeks")
    print(f"Current:     10:15, 30min, 5 entries → [10:15, 10:45, 11:15, 11:45, 12:15]")
    print()

    all_stats = []
    for i, (label, times, start, interval, n) in enumerate(schedules, 1):
        print(f"  [{i}/{total}] {label}  →  {times}")
        results = run_backtest(build_cfg(times), verbose=False)
        stat = summarise(results, label, start, interval, n, times)
        all_stats.append(stat)

        # Print running best
        if i % 10 == 0 or i == total:
            best_sh = max(all_stats, key=lambda s: s["sharpe"])
            print(f"    ... best so far: {best_sh['label']}  Sharpe={best_sh['sharpe']:.3f}  P&L=${best_sh['total_pnl']:,.0f}")

    # ── Sort and display top results ─────────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"  TOP 15 BY SHARPE")
    print(f"{'='*90}")
    by_sharpe = sorted(all_stats, key=lambda s: s["sharpe"], reverse=True)
    print(f"  {'#':<3} {'Label':<25} {'Schedule':<35} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>10} {'Win%':>6} {'Stops':>6}")
    print(f"  {'─'*105}")
    for i, s in enumerate(by_sharpe[:15], 1):
        live = " ◀ LIVE" if s["schedule"] == "10:15 10:45 11:15 11:45 12:15" else ""
        print(f"  {i:<3} {s['label']:<25} {s['schedule']:<35} {s['sharpe']:>7.3f} ${s['total_pnl']:>9,.0f} ${s['max_dd']:>9,.0f} {s['win_rate']:>5.1f}% {s['total_stops']:>6}{live}")

    print(f"\n{'='*90}")
    print(f"  TOP 15 BY P&L")
    print(f"{'='*90}")
    by_pnl = sorted(all_stats, key=lambda s: s["total_pnl"], reverse=True)
    print(f"  {'#':<3} {'Label':<25} {'Schedule':<35} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>10} {'Win%':>6}")
    print(f"  {'─'*100}")
    for i, s in enumerate(by_pnl[:15], 1):
        live = " ◀ LIVE" if s["schedule"] == "10:15 10:45 11:15 11:45 12:15" else ""
        print(f"  {i:<3} {s['label']:<25} {s['schedule']:<35} {s['sharpe']:>7.3f} ${s['total_pnl']:>9,.0f} ${s['max_dd']:>9,.0f} {s['win_rate']:>5.1f}%{live}")

    # ── Dimension analysis ───────────────────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"  BEST BY DIMENSION (marginalised)")
    print(f"{'='*90}")

    # Best start time (averaged across intervals and num_entries)
    from collections import defaultdict
    by_start = defaultdict(list)
    for s in all_stats:
        by_start[s["start"]].append(s)
    print(f"\n  By start time (avg Sharpe across all intervals/counts):")
    for start in sorted(by_start.keys()):
        stats = by_start[start]
        avg_sh = statistics.mean([s["sharpe"] for s in stats])
        avg_pnl = statistics.mean([s["total_pnl"] for s in stats])
        best = max(stats, key=lambda s: s["sharpe"])
        print(f"    {start:>5}: avg Sharpe={avg_sh:.3f}  avg P&L=${avg_pnl:,.0f}  (best: {best['label']} Sharpe={best['sharpe']:.3f})")

    # Best interval
    by_interval = defaultdict(list)
    for s in all_stats:
        by_interval[s["interval"]].append(s)
    print(f"\n  By interval (avg Sharpe across all starts/counts):")
    for interval in sorted(by_interval.keys()):
        stats = by_interval[interval]
        avg_sh = statistics.mean([s["sharpe"] for s in stats])
        avg_pnl = statistics.mean([s["total_pnl"] for s in stats])
        print(f"    {interval:>3}min: avg Sharpe={avg_sh:.3f}  avg P&L=${avg_pnl:,.0f}  (n={len(stats)})")

    # Best num entries
    by_count = defaultdict(list)
    for s in all_stats:
        by_count[s["num_entries"]].append(s)
    print(f"\n  By number of entries (avg Sharpe across all starts/intervals):")
    for n in sorted(by_count.keys()):
        stats = by_count[n]
        avg_sh = statistics.mean([s["sharpe"] for s in stats])
        avg_pnl = statistics.mean([s["total_pnl"] for s in stats])
        print(f"    {n} entries: avg Sharpe={avg_sh:.3f}  avg P&L=${avg_pnl:,.0f}  (n={len(stats)})")

    # ── Live config comparison ───────────────────────────────────────────────
    live = next((s for s in all_stats if s["schedule"] == "10:15 10:45 11:15 11:45 12:15"), None)
    if live:
        rank_sh = sorted(all_stats, key=lambda s: s["sharpe"], reverse=True)
        rank_pnl = sorted(all_stats, key=lambda s: s["total_pnl"], reverse=True)
        live_rank_sh = next(i for i, s in enumerate(rank_sh, 1) if s["label"] == live["label"])
        live_rank_pnl = next(i for i, s in enumerate(rank_pnl, 1) if s["label"] == live["label"])
        print(f"\n  LIVE config rank: #{live_rank_sh}/{total} by Sharpe, #{live_rank_pnl}/{total} by P&L")
        print(f"  LIVE: Sharpe={live['sharpe']:.3f}  P&L=${live['total_pnl']:,.0f}  MaxDD=${live['max_dd']:,.0f}")

    best = by_sharpe[0]
    print(f"  BEST: Sharpe={best['sharpe']:.3f}  P&L=${best['total_pnl']:,.0f}  MaxDD=${best['max_dd']:,.0f}")
    print(f"         Schedule: {best['schedule']}")

    # ── CSV ──────────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"entry_timing_sweep_{ts}.csv"
    csv_keys = ["label", "schedule", "start", "interval", "num_entries",
                "days", "win_rate", "total_pnl", "mean_daily", "stdev_daily",
                "sharpe", "max_dd", "calmar", "placed", "skipped",
                "total_stops", "stop_rate"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader(); writer.writerows(all_stats)
    print(f"\n  Results saved → {csv_path}")
