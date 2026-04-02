"""
Sweep: Cushion Recovery Exit — close a side when spread value nearly hits
stop level (danger zone) but then recovers.

Logic per side:
  1. Track if spread_value >= nearstop_pct × stop_level (entered danger zone)
  2. If danger flag set AND spread_value drops to <= recovery_pct × stop_level → close

This catches "near-miss" patterns: position almost stopped, recovered, but
the next breach might actually stop it.  Better to close on the recovery
while there's still credit value left.

Sweeps nearstop_pct (70-95%) × recovery_pct (30-70%).

Run: python -m backtest.sweep_cushion_recovery
"""
import multiprocessing as mp
import os
import statistics
from datetime import date

from backtest.config import live_config
from backtest.engine import run_backtest

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
N_WORKERS = min(8, os.cpu_count() or 4)


def _metrics(results):
    daily = [r.net_pnl for r in results]
    n = len(daily)
    if n == 0:
        return {"total_pnl": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0, "days": 0,
                "avg_win": 0, "avg_loss": 0, "early_exits": 0}
    mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = [p for p in daily if p > 0]
    losses = [p for p in daily if p < 0]
    avg_win = statistics.mean(wins) if wins else 0
    avg_loss = statistics.mean(losses) if losses else 0

    early_exits = 0
    for r in results:
        for e in r.entries:
            if e.call_outcome == "early_exit" or e.put_outcome == "early_exit":
                early_exits += 1
                break

    return {
        "total_pnl": sum(daily), "sharpe": sharpe, "max_dd": max_dd,
        "win_rate": len(wins) / n * 100, "days": n,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "early_exits": early_exits,
    }


def _run_one(args):
    idx, label, cfg = args
    r = run_backtest(cfg, verbose=False)
    m = _metrics(r)
    return (idx, label, m)


def _base():
    cfg = live_config()
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [125.0, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    return cfg


def main():
    configs = []

    # Baseline
    configs.append(("Baseline: hold to expiry", _base()))

    # Sweep: nearstop × recovery combinations
    for near in [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]:
        for recv in [0.30, 0.40, 0.50, 0.60, 0.70]:
            if recv >= near:
                continue  # recovery must be below danger threshold
            cfg = _base()
            cfg.cushion_nearstop_pct = near
            cfg.cushion_recovery_pct = recv
            configs.append((f"Near {near:.0%} Recv {recv:.0%}", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Testing {n} cushion-recovery strategies across ~938 days, {N_WORKERS} workers\n", flush=True)
    hdr = (f"{'Strategy':<28s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}  {'Exits':>5s}")
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "Baseline" in label else ""
            print(f"  [{idx+1:2d}/{n}] {label:<26s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
                  f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}  "
                  f"{m['early_exits']:>5d}{marker}", flush=True)
            results.append(res)

    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)
    baseline = next((m for _, l, m in results if "Baseline" in l), None)

    print()
    print("=" * 105)
    print("RESULTS SORTED BY SHARPE RATIO")
    print("=" * 105)
    print(f"{'Strategy':<28s}  {'P&L':>10s}  {'Δ P&L':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
          f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}  {'Exits':>5s}")
    print("─" * 105)

    for _, label, m in results:
        marker = " ◄ BASELINE" if "Baseline" in label else ""
        delta = m["total_pnl"] - baseline["total_pnl"] if baseline else 0
        print(f"{label:<28s}  ${m['total_pnl']:>+9,.0f}  {delta:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}  "
              f"{m['early_exits']:>5d}{marker}", flush=True)


if __name__ == "__main__":
    main()
