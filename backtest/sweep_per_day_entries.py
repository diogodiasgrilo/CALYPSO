"""
Sweep per-day entry caps at 1 contract.
Test: every combination of capping individual days to 1e or 2e.

Run: python -m backtest.sweep_per_day_entries
"""
import multiprocessing as mp
import os
import statistics
import itertools
from copy import deepcopy
from datetime import date
from typing import Dict, List, Tuple

from backtest.config import live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
N_WORKERS = min(8, os.cpu_count() or 4)
DAY_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}


def _metrics(results):
    daily = [r.net_pnl for r in results]
    n = len(daily)
    if n == 0:
        return {"days": 0, "net_pnl": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0,
                "total_pnl": 0, "avg_daily": 0}
    mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = sum(1 for p in daily if p > 0)
    return {"days": n, "net_pnl": mean * 252, "sharpe": sharpe, "max_dd": max_dd,
            "win_rate": wins / n * 100, "total_pnl": sum(daily), "avg_daily": mean}


def _run_one(args):
    idx, label, cfg = args
    r = run_backtest(cfg, verbose=False)
    m = _metrics(r)
    return (idx, label, m)


def _base():
    cfg = live_config()
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"; cfg.contracts = 1
    return cfg


def main():
    configs = []

    # Baseline: 3 entries every day + VIX regime
    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    configs.append(("Baseline (VIX regime, 3e all days)", cfg))

    # Single day caps
    for dow in range(5):
        for cap in [1, 2]:
            cfg = deepcopy(configs[0][1])
            cfg.dow_max_entries = {dow: cap}
            configs.append((f"{DAY_NAMES[dow]}={cap}e", cfg))

    # Multi-day caps (most promising combos)
    for combo in [
        {2: 2, 3: 2},           # Wed+Thu=2e
        {2: 1, 3: 1},           # Wed+Thu=1e
        {2: 2, 3: 2, 4: 2},     # Wed+Thu+Fri=2e
        {2: 1, 3: 1, 4: 2},     # Wed=1e+Thu=1e+Fri=2e
        {2: 2, 3: 1},           # Wed=2e+Thu=1e
        {2: 1, 3: 2},           # Wed=1e+Thu=2e
        {0: 2, 2: 2, 3: 2},     # Mon+Wed+Thu=2e
        {3: 2, 4: 2},           # Thu+Fri=2e
        {2: 2, 4: 2},           # Wed+Fri=2e
    ]:
        cfg = deepcopy(configs[0][1])
        cfg.dow_max_entries = combo
        label = "+".join(f"{DAY_NAMES[d]}={v}e" for d, v in sorted(combo.items()))
        configs.append((label, cfg))

    # No VIX regime baseline for comparison
    cfg = _base()
    configs.append(("No VIX regime, no caps", cfg))

    n = len(configs)
    print(f"Sweeping {n} per-day entry configs at 1 contract, {N_WORKERS} workers\n", flush=True)

    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            print(f"  [{len(results)}/{n}] {label:40s} Sharpe {m['sharpe']:.3f}  "
                  f"P&L ${m['total_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}", flush=True)

    results.sort(key=lambda x: x[0])
    baseline_pnl = results[0][2]["total_pnl"]

    # Sort by total P&L (since user cares about maximizing money)
    by_pnl = sorted(results, key=lambda x: x[2]["total_pnl"], reverse=True)
    print(f"\n{'Config':<40s}  {'Total P&L':>10s}  {'vs Base':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}")
    print(f"{'─'*85}")
    for idx, label, m in by_pnl:
        delta_pnl = m["total_pnl"] - baseline_pnl
        print(f"{label:<40s}  ${m['total_pnl']:>+9,.0f}  ${delta_pnl:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%")


if __name__ == "__main__":
    main()
