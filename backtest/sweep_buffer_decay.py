"""
Sweep: Time-Decaying Stop Buffer — start with wider buffers at entry,
linearly decay to normal config value over N hours.

Idea: new positions are most vulnerable to false stops (delta/gamma dominate).
Wider buffers early give room to breathe; tighten as theta takes over.

Sweeps:
  - Start multiplier: 1.25, 1.5, 1.75, 2.0, 2.5, 3.0
  - Decay hours: 1, 1.5, 2, 2.5, 3, 4

Run: python -m backtest.sweep_buffer_decay
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
                "avg_win": 0, "avg_loss": 0}
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
    return {
        "total_pnl": sum(daily), "sharpe": sharpe, "max_dd": max_dd,
        "win_rate": len(wins) / n * 100, "days": n,
        "avg_win": avg_win, "avg_loss": avg_loss,
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
    configs.append(("Baseline", _base()))

    # Sweep: multiplier × decay_hours
    for mult in [1.25, 1.5, 1.75, 2.0, 2.5, 3.0]:
        for hours in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]:
            cfg = _base()
            cfg.buffer_decay_start_mult = mult
            cfg.buffer_decay_hours = hours
            configs.append((f"x{mult:.2f} {hours:.1f}h", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Buffer decay sweep: {n} configs across ~938 days, {N_WORKERS} workers\n", flush=True)
    hdr = (f"{'Strategy':<14s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}")
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "Baseline" in label else ""
            print(f"  [{idx+1:2d}/{n}] {label:<12s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
                  f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}{marker}", flush=True)
            results.append(res)

    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)
    baseline = next((m for _, l, m in results if "Baseline" in l), None)

    print()
    print("=" * 95)
    print("TOP 20 BY SHARPE RATIO")
    print("=" * 95)
    print(f"{'Strategy':<14s}  {'P&L':>10s}  {'Δ P&L':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
          f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}")
    print("─" * 95)

    for _, label, m in results[:20]:
        marker = " ◄ BASE" if "Baseline" in label else ""
        delta = m["total_pnl"] - baseline["total_pnl"] if baseline else 0
        print(f"{label:<14s}  ${m['total_pnl']:>+9,.0f}  {delta:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}{marker}", flush=True)

    # Heat map
    print()
    print("── SHARPE HEAT MAP (Multiplier × Decay Hours) ──")
    mults = [1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
    hours_list = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    result_map = {l: m for _, l, m in results if "Baseline" not in l}

    print(f"{'Mult↓ Hours→':<14s}", end="")
    for h in hours_list:
        print(f"  {h:>5.1f}h", end="")
    print()
    print("─" * (14 + len(hours_list) * 7))

    for mult in mults:
        print(f"  x{mult:<10.2f}  ", end="")
        for h in hours_list:
            key = f"x{mult:.2f} {h:.1f}h"
            if key in result_map:
                s = result_map[key]["sharpe"]
                marker = "*" if s > baseline["sharpe"] else " "
                print(f" {s:>5.3f}{marker}", end="")
            else:
                print(f"     - ", end="")
        print()

    print(f"\nBaseline Sharpe: {baseline['sharpe']:.3f}  (* = beats baseline)")


if __name__ == "__main__":
    main()
