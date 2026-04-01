"""
Exploit the 11 AM stop cluster: avoid damage instead of closing early.

Run: python -m backtest.sweep_stop_avoidance
"""
import multiprocessing as mp
import os
import statistics
from copy import deepcopy
from datetime import date
from typing import Tuple

from backtest.config import live_config
from backtest.engine import run_backtest

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
N_WORKERS = min(8, os.cpu_count() or 4)


def _metrics(results):
    daily = [r.net_pnl for r in results]
    n = len(daily)
    if n == 0:
        return {"total_pnl": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0, "days": 0}
    mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = sum(1 for p in daily if p > 0)
    return {"total_pnl": sum(daily), "sharpe": sharpe, "max_dd": max_dd,
            "win_rate": wins / n * 100, "days": n}


def _run_one(args):
    idx, label, cfg = args
    r = run_backtest(cfg, verbose=False)
    m = _metrics(r)
    return (idx, label, m)


def _base():
    cfg = live_config()
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    return cfg


def main():
    configs = []

    # Baseline
    configs.append(("Baseline: 10:15/10:45/11:15", _base()))

    # ── 1. Shift E3 later (avoid 11 AM stop cluster) ─────────────
    for e3_time in ["11:30", "11:45", "12:00", "12:15", "12:30"]:
        cfg = _base()
        cfg.entry_times = ["10:15", "10:45", e3_time]
        configs.append((f"E3 shifted to {e3_time}", cfg))

    # ── 2. Shift E1 later (avoid early volatility) ────────────────
    for e1_time in ["10:30", "10:45"]:
        for gap in [30, 45]:
            h1, m1 = int(e1_time.split(":")[0]), int(e1_time.split(":")[1])
            m2 = m1 + gap
            h2 = h1 + m2 // 60; m2 = m2 % 60
            m3 = m2 + gap
            h3 = h2 + m3 // 60; m3 = m3 % 60
            e2 = f"{h2}:{m2:02d}"
            e3 = f"{h3}:{m3:02d}"
            cfg = _base()
            cfg.entry_times = [e1_time, e2, e3]
            configs.append((f"{e1_time}/{e2}/{e3} ({gap}min gap)", cfg))

    # ── 3. Skip E1 entirely (2 entries starting 10:45) ────────────
    configs_2e = [
        ("Skip E1: 10:45/11:15 only", ["10:45", "11:15"]),
        ("Skip E1: 10:45/11:30", ["10:45", "11:30"]),
        ("Skip E1: 10:45/11:45", ["10:45", "11:45"]),
        ("Skip E1: 11:00/11:30", ["11:00", "11:30"]),
        ("Skip E1: 11:00/12:00", ["11:00", "12:00"]),
    ]
    for label, times in configs_2e:
        cfg = _base()
        cfg.entry_times = times
        configs.append((label, cfg))

    # ── 4. Wider spread on later entries ──────────────────────────
    # (E3 at 11:15 enters after market found direction — maybe tighter is OK)
    # Can't do per-entry spread width in current engine, skip this

    # ── 5. Shift everything 15min later ──────────────────────────
    late_starts = [
        ("10:30/11:00/11:30", ["10:30", "11:00", "11:30"]),
        ("10:45/11:15/11:45", ["10:45", "11:15", "11:45"]),
        ("11:00/11:30/12:00", ["11:00", "11:30", "12:00"]),
    ]
    for label, times in late_starts:
        cfg = _base()
        cfg.entry_times = times
        configs.append((label, cfg))

    # ── 6. Wider gaps between entries ─────────────────────────────
    wide_gaps = [
        ("10:15/11:00/11:45 (45min)", ["10:15", "11:00", "11:45"]),
        ("10:15/11:15/12:15 (60min)", ["10:15", "11:15", "12:15"]),
        ("10:00/11:00/12:00 (60min)", ["10:00", "11:00", "12:00"]),
    ]
    for label, times in wide_gaps:
        cfg = _base()
        cfg.entry_times = times
        configs.append((label, cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Testing {n} timing/avoidance strategies, {N_WORKERS} workers\n", flush=True)
    print(f"{'Strategy':<45s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}")
    print(f"{'─'*80}", flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)

    results.sort(key=lambda x: x[0])
    baseline_pnl = results[0][2]["total_pnl"]

    # Sort by Sharpe
    by_sharpe = sorted(results, key=lambda x: x[2]["sharpe"], reverse=True)
    for _, label, m in by_sharpe:
        marker = " ◄" if "Baseline" in label else ""
        print(f"{label:<45s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%{marker}", flush=True)

    # Also show sorted by P&L
    print(f"\n── Sorted by Total P&L ──")
    by_pnl = sorted(results, key=lambda x: x[2]["total_pnl"], reverse=True)
    for _, label, m in by_pnl[:10]:
        delta = m["total_pnl"] - baseline_pnl
        print(f"  {label:<45s}  ${m['total_pnl']:>+9,.0f} ({delta:>+7,.0f})  Sharpe {m['sharpe']:.3f}", flush=True)


if __name__ == "__main__":
    main()
