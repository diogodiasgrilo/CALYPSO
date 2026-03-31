"""
HYDRA Combined Features Sweep — 1-Minute Resolution

Tests combinations of the winning features:
  - Skip Wednesday
  - Skip Thursday
  - VIX Regime (Conservative extremes, Tight low-VIX gates)
  - Fri=2e cap
  - All promising combos

Run: python -m backtest.sweep_combined
"""
import csv
import multiprocessing as mp
import os
import statistics
import time
from copy import copy
from datetime import date, datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Tuple

from backtest.config import BacktestConfig, live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)


def compute_metrics(results: List[DayResult]) -> Dict[str, Any]:
    if not results:
        return {"days": 0, "net_pnl": 0, "sharpe": 0, "sortino": 0,
                "max_dd": 0, "win_rate": 0, "calmar": 0, "avg_daily": 0,
                "entries_placed": 0, "total_stops": 0}
    daily = [r.net_pnl for r in results]
    total = sum(daily)
    n = len(daily)
    win = sum(1 for p in daily if p > 0)
    mean = statistics.mean(daily) if daily else 0
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * (252 ** 0.5) if std > 0 else 0
    neg = [p for p in daily if p < 0]
    down_dev = (sum(p ** 2 for p in neg) / n) ** 0.5 if neg else 0
    sortino = mean / down_dev * (252 ** 0.5) if down_dev > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    calmar = (mean * 252) / abs(max_dd) if max_dd != 0 else 0
    all_entries = [e for r in results for e in r.entries]
    placed = [e for e in all_entries if e.entry_type != "skipped"]
    stops = sum(
        (1 if e.call_outcome == "stopped" else 0) +
        (1 if e.put_outcome == "stopped" else 0)
        for e in placed
    )
    return {
        "days": n, "net_pnl": total, "sharpe": sharpe, "sortino": sortino,
        "max_dd": max_dd, "win_rate": win / n * 100 if n > 0 else 0,
        "calmar": calmar, "avg_daily": mean, "entries_placed": len(placed),
        "total_stops": stops,
    }


def _base() -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    return cfg


def build_configs() -> List[Tuple[str, BacktestConfig]]:
    configs: List[Tuple[str, BacktestConfig]] = []

    # ── Baseline ──────────────────────────────────────────────────────
    configs.append(("Baseline", _base()))

    # ── Individual winners (for reference) ────────────────────────────
    cfg = _base(); cfg.skip_weekdays = [2]
    configs.append(("Skip Wed", cfg))

    cfg = _base(); cfg.skip_weekdays = [3]
    configs.append(("Skip Thu", cfg))

    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("VIX Conservative", cfg))

    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_min_call_credit = [1.75, None, None, None]
    cfg.vix_regime_min_put_credit = [2.50, None, None, None]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 250.0]
    configs.append(("VIX Tight LowVIX", cfg))

    cfg = _base(); cfg.dow_max_entries = {4: 2}
    configs.append(("Fri=2e", cfg))

    # ── 2-way combos ─────────────────────────────────────────────────
    # Skip Wed + VIX Conservative
    cfg = _base(); cfg.skip_weekdays = [2]
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("Skip Wed + VIX Conserv", cfg))

    # Skip Wed + VIX Tight LowVIX
    cfg = _base(); cfg.skip_weekdays = [2]
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_min_call_credit = [1.75, None, None, None]
    cfg.vix_regime_min_put_credit = [2.50, None, None, None]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 250.0]
    configs.append(("Skip Wed + VIX TightLow", cfg))

    # Skip Thu + VIX Conservative
    cfg = _base(); cfg.skip_weekdays = [3]
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("Skip Thu + VIX Conserv", cfg))

    # Skip Wed + Fri=2e
    cfg = _base(); cfg.skip_weekdays = [2]; cfg.dow_max_entries = {4: 2}
    configs.append(("Skip Wed + Fri=2e", cfg))

    # Skip Thu + Fri=2e
    cfg = _base(); cfg.skip_weekdays = [3]; cfg.dow_max_entries = {4: 2}
    configs.append(("Skip Thu + Fri=2e", cfg))

    # VIX Conservative + Fri=2e
    cfg = _base(); cfg.dow_max_entries = {4: 2}
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("VIX Conserv + Fri=2e", cfg))

    # Skip Wed + Thu (both worst days)
    cfg = _base(); cfg.skip_weekdays = [2, 3]
    configs.append(("Skip Wed+Thu", cfg))

    # ── 3-way combos ─────────────────────────────────────────────────
    # Skip Wed + VIX Conservative + Fri=2e
    cfg = _base(); cfg.skip_weekdays = [2]; cfg.dow_max_entries = {4: 2}
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("Skip Wed + VIX + Fri=2e", cfg))

    # Skip Thu + VIX Conservative + Fri=2e
    cfg = _base(); cfg.skip_weekdays = [3]; cfg.dow_max_entries = {4: 2}
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("Skip Thu + VIX + Fri=2e", cfg))

    # Skip Wed+Thu + VIX Conservative
    cfg = _base(); cfg.skip_weekdays = [2, 3]
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("Skip Wed+Thu + VIX Conserv", cfg))

    # Skip Wed+Thu + VIX Conservative + Fri=2e
    cfg = _base(); cfg.skip_weekdays = [2, 3]; cfg.dow_max_entries = {4: 2}
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("Skip Wed+Thu + VIX + Fri=2e", cfg))

    return configs


def _run_one(args: Tuple[int, str, BacktestConfig]) -> Tuple[int, str, Dict]:
    idx, label, cfg = args
    results = run_backtest(cfg, verbose=False)
    metrics = compute_metrics(results)
    return (idx, label, metrics)


def main():
    configs = build_configs()
    n_total = len(configs)
    n_workers = min(8, os.cpu_count() or 4)
    print(f"Combined features sweep: {n_total} configs, {n_workers} workers", flush=True)
    print(f"Period: {START_DATE} -> {END_DATE} | 1-min | Real Greeks\n", flush=True)

    worker_args = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    all_results: List[Tuple[int, str, Dict]] = []
    start = time.time()

    with mp.Pool(n_workers) as pool:
        for result in pool.imap_unordered(_run_one, worker_args):
            all_results.append(result)
            idx, label, m = result
            print(f"  [{len(all_results)}/{n_total}] {label:30s} Sharpe {m['sharpe']:.3f}  "
                  f"P&L ${m['net_pnl']:+,.0f}  MaxDD ${m['max_dd']:,.0f}", flush=True)

    elapsed = time.time() - start
    print(f"\nCompleted in {elapsed:.0f}s\n", flush=True)

    # Sort by original order and print
    all_results.sort(key=lambda x: x[0])
    baseline_sharpe = all_results[0][2]["sharpe"]

    print(f"{'='*95}")
    print(f"{'Config':<30s}  {'Days':>4s}  {'P&L':>10s}  {'Sharpe':>7s}  {'Delta':>7s}  "
          f"{'Sortino':>8s}  {'MaxDD':>8s}  {'Win%':>5s}")
    print(f"{'='*95}")
    for idx, label, m in all_results:
        delta = m["sharpe"] - baseline_sharpe
        print(f"{label:<30s}  {m['days']:>4d}  ${m['net_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
              f"{delta:>+7.3f}  {m['sortino']:>8.3f}  ${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%")

    print(f"\n--- TOP 5 by Sharpe ---")
    by_sharpe = sorted(all_results, key=lambda x: x[2]["sharpe"], reverse=True)
    for idx, label, m in by_sharpe[:5]:
        delta = m["sharpe"] - baseline_sharpe
        print(f"  {label:<30s} Sharpe {m['sharpe']:.3f} ({delta:+.3f})  "
              f"P&L ${m['net_pnl']:+,.0f}  MaxDD ${m['max_dd']:,.0f}  Sortino {m['sortino']:.3f}")

    # Save CSV
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path("backtest/results") / f"combined_sweep_1min_{ts}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["label", "days", "net_pnl", "sharpe", "sortino",
                         "max_dd", "win_rate", "calmar", "avg_daily",
                         "entries_placed", "total_stops"])
        for idx, label, m in all_results:
            writer.writerow([label, m["days"], f"{m['net_pnl']:.2f}",
                             f"{m['sharpe']:.4f}", f"{m['sortino']:.4f}",
                             f"{m['max_dd']:.2f}", f"{m['win_rate']:.2f}",
                             f"{m['calmar']:.4f}", f"{m['avg_daily']:.2f}",
                             m["entries_placed"], m["total_stops"]])
    print(f"\nSaved to {csv_path}")


if __name__ == "__main__":
    main()
