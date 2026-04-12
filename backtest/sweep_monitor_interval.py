"""
Sweep: Stop check monitoring interval

Tests how frequently checking stop losses affects P&L.
Slower checking = misses brief spikes = fewer false stops.
Faster checking = catches real danger = more true stops.

Current live: ~2-5 seconds (not testable with 1-min data)
Backtest default: every 1-minute data point (60000ms)

This sweep tests: 1-min, 2-min, 3-min, 5-min, 10-min, 15-min intervals.

Run: python -m backtest.sweep_monitor_interval --workers 8
"""
import argparse
import csv
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

try:
    from rich.console import Console
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                               SpinnerColumn, TextColumn, TimeElapsedColumn,
                               TimeRemainingColumn)
    _RICH = True
except ImportError:
    _RICH = False

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 4, 8)

# Intervals in milliseconds (1 min = 60000ms)
INTERVALS = [
    (60000,   "1min"),    # every data point (current backtest default)
    (120000,  "2min"),
    (180000,  "3min"),
    (300000,  "5min"),
    (600000,  "10min"),
    (900000,  "15min"),
]


def build_cfg(interval_ms: int) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.monitor_interval_ms = interval_ms
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl = sum(daily_pnls)
    total_days = len(results)
    win_days = sum(1 for p in daily_pnls if p > 0)

    placed = sum(r.entries_placed for r in results)
    total_stops = sum(r.stops_hit for r in results)

    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops = sum(sum(1 for e in r.entries if e.put_outcome == "stopped") for r in results)

    mean = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    # Average stop loss cost (when stopped)
    stop_costs = []
    for r in results:
        for e in r.entries:
            if e.call_outcome == "stopped" and e.call_close_cost > 0:
                stop_costs.append(e.call_close_cost - e.call_credit)
            if e.put_outcome == "stopped" and e.put_close_cost > 0:
                stop_costs.append(e.put_close_cost - e.put_credit)
    avg_stop_loss = statistics.mean(stop_costs) if stop_costs else 0

    return {
        "label": label, "days": total_days, "win": win_days,
        "win_rate": win_days / total_days * 100 if total_days else 0,
        "total_pnl": total_pnl, "mean_daily": mean, "sharpe": sharpe,
        "max_dd": max_dd, "calmar": calmar,
        "placed": placed, "total_stops": total_stops,
        "stop_rate": total_stops / placed * 100 if placed else 0,
        "call_stops": call_stops, "put_stops": put_stops,
        "avg_stop_loss": avg_stop_loss,
    }


def _run_one(args):
    label, cfg = args
    results = run_backtest(cfg, verbose=False)
    return summarise(results, label)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    n_workers = args.workers
    console = Console() if _RICH else None

    print(f"\nSweep: Stop Check Monitoring Interval")
    print(f"Base: live_config()  |  Period: {START_DATE} -> {END_DATE}")
    print(f"Workers: {n_workers}  |  1-min data  |  Real Greeks\n")

    configs = [
        (label, build_cfg(ms))
        for ms, label in INTERVALS
    ]

    all_stats = []
    total = len(configs)

    if _RICH:
        with Progress(
            SpinnerColumn(), TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40), MofNCompleteColumn(),
            TextColumn("[yellow]{task.percentage:.0f}%"),
            TextColumn("*"), TimeElapsedColumn(),
            TextColumn("*"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
            console=console, refresh_per_second=4,
        ) as progress:
            task = progress.add_task("Running", total=total)
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                futures = {pool.submit(_run_one, item): item[0] for item in configs}
                for future in as_completed(futures):
                    stats = future.result()
                    all_stats.append(stats)
                    progress.update(task, description=stats["label"])
                    progress.advance(task)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_run_one, item): item[0] for item in configs}
            for future in as_completed(futures):
                stats = future.result()
                all_stats.append(stats)
                print(f"  Done: {stats['label']} Sharpe={stats['sharpe']:.3f}")

    # Sort by interval (label)
    interval_order = {label: i for i, (_, label) in enumerate(INTERVALS)}
    all_stats.sort(key=lambda s: interval_order.get(s["label"], 99))

    print(f"\n{'='*100}")
    print(f"  Stop Check Interval Sweep Results")
    print(f"{'='*100}")
    print(f"  {'Interval':<10} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>8} {'WR%':>6} {'StopR':>6} {'Stops':>6} {'CStop':>6} {'PStop':>6} {'AvgLoss':>8} {'Placed':>7}")
    print(f"  {'-'*96}")
    for s in all_stats:
        live = " *" if s["label"] == "1min" else ""
        print(f"  {s['label']:<10} {s['sharpe']:>7.3f} {s['total_pnl']:>10,.0f} {s['max_dd']:>8,.0f} {s['win_rate']:>5.1f}% {s['stop_rate']:>5.1f}% {s['total_stops']:>6} {s['call_stops']:>6} {s['put_stops']:>6} {s['avg_stop_loss']:>7,.0f} {s['placed']:>7}{live}")

    best = max(all_stats, key=lambda s: s["sharpe"])
    print(f"\n  Best Sharpe: {best['label']} ({best['sharpe']:.3f})")

    # Show delta from 1-min baseline
    baseline = next((s for s in all_stats if s["label"] == "1min"), None)
    if baseline:
        print(f"\n  Delta from 1-min baseline (current backtest):")
        for s in all_stats:
            if s["label"] != "1min":
                dp = s["total_pnl"] - baseline["total_pnl"]
                ds = s["sharpe"] - baseline["sharpe"]
                dstops = s["total_stops"] - baseline["total_stops"]
                print(f"    {s['label']:<8} Sharpe: {ds:+.3f}  P&L: {dp:+,.0f}  Stops: {dstops:+}")

    # Save CSV
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"monitor_interval_sweep_{ts}.csv"
    csv_keys = list(all_stats[0].keys())
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_stats)
    print(f"\n  Results saved -> {csv_path}")
