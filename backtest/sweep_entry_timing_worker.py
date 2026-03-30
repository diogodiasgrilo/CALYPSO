"""
Worker for parallel entry timing sweep. Run with chunk_id argument.
Usage: python -m backtest.sweep_entry_timing_worker 0  (chunk 0 of 4)
"""
import csv
import json
import statistics
import sys
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)
NUM_CHUNKS = 4

START_TIMES_MIN = [600, 615, 630, 645, 660]
INTERVALS = [15, 20, 30, 45, 60]
NUM_ENTRIES = [3, 4, 5, 6, 7]
LAST_ENTRY_CUTOFF = 13 * 60 + 30


def min_to_hhmm(m: int) -> str:
    return f"{m // 60}:{m % 60:02d}"


def generate_all():
    schedules = []
    for start in START_TIMES_MIN:
        for interval in INTERVALS:
            for n in NUM_ENTRIES:
                last = start + (n - 1) * interval
                if last <= LAST_ENTRY_CUTOFF:
                    times = [min_to_hhmm(start + i * interval) for i in range(n)]
                    label = f"s{min_to_hhmm(start)}_i{interval}m_n{n}"
                    schedules.append((label, times, start, interval, n))
    return schedules


def build_cfg(entry_times: List[str]) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    cfg.entry_times = entry_times
    cfg.conditional_e7_enabled = False
    cfg.conditional_upday_e6_enabled = False
    return cfg


def summarise(results: List[DayResult], label: str, start: int, interval: int, n: int, times: List[str]) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl = sum(daily_pnls)
    total_days = len(results)
    win_days = sum(1 for p in daily_pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    skipped = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)
    mean = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum += p; peak = max(peak, cum); max_dd = max(max_dd, peak - cum)
    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    return {
        "label": label, "schedule": " ".join(times),
        "start": min_to_hhmm(start), "interval": interval, "num_entries": n,
        "days": total_days, "win_rate": round(win_days / total_days * 100, 1) if total_days else 0,
        "total_pnl": round(total_pnl, 2), "mean_daily": round(mean, 2),
        "stdev_daily": round(stdev, 2), "sharpe": round(sharpe, 4),
        "max_dd": round(max_dd, 2), "calmar": round(calmar, 4),
        "placed": placed, "skipped": skipped,
        "total_stops": total_stops,
        "stop_rate": round(total_stops / placed * 100, 1) if placed else 0,
    }


if __name__ == "__main__":
    chunk_id = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    all_schedules = generate_all()
    total = len(all_schedules)

    # Split into chunks
    chunk_size = (total + NUM_CHUNKS - 1) // NUM_CHUNKS
    start_idx = chunk_id * chunk_size
    end_idx = min(start_idx + chunk_size, total)
    my_schedules = all_schedules[start_idx:end_idx]

    print(f"Worker {chunk_id}: configs {start_idx+1}–{end_idx} of {total} ({len(my_schedules)} combos)")

    results_list = []
    for i, (label, times, start, interval, n) in enumerate(my_schedules, 1):
        print(f"  [{start_idx+i}/{total}] {label}  →  {times}")
        results = run_backtest(build_cfg(times), verbose=False)
        stat = summarise(results, label, start, interval, n, times)
        results_list.append(stat)

    # Save chunk results as JSON
    out_dir = Path("backtest/results/sweep_logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"entry_timing_chunk_{chunk_id}.json"
    with open(out_path, "w") as f:
        json.dump(results_list, f, indent=2)
    print(f"\nWorker {chunk_id} done: {len(results_list)} results → {out_path}")
