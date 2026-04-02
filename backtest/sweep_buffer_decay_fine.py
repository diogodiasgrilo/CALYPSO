"""
Fine-grain buffer decay sweep around x1.75/2.0h winner, WITH calm entry enabled
(since that's the deployed combo).

Run: python -m backtest.sweep_buffer_decay_fine
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
    # Always include calm entry (deployed combo)
    cfg.calm_entry_lookback_min = 3
    cfg.calm_entry_threshold_pts = 15.0
    cfg.calm_entry_max_delay_min = 5
    return cfg


def main():
    configs = []

    # Baseline (calm entry only, no buffer decay)
    configs.append(("Calm only (base)", _base()))

    # Fine-grain: mult 1.4-2.2 (step 0.1) × hours 1.0-3.5 (step 0.5)
    for mult_x10 in range(14, 23):  # 1.4, 1.5, ..., 2.2
        mult = mult_x10 / 10.0
        for hours_x2 in range(2, 8):  # 1.0, 1.5, 2.0, 2.5, 3.0, 3.5
            hours = hours_x2 / 2.0
            cfg = _base()
            cfg.buffer_decay_start_mult = mult
            cfg.buffer_decay_hours = hours
            configs.append((f"x{mult:.1f} {hours:.1f}h", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Fine-grain buffer+calm sweep: {n} configs, {N_WORKERS} workers\n", flush=True)
    hdr = (f"{'Strategy':<14s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}")
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "base" in label else ""
            print(f"  [{idx+1:2d}/{n}] {label:<12s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
                  f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}{marker}", flush=True)
            results.append(res)

    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)
    baseline = next((m for _, l, m in results if "base" in l), None)

    print()
    print("=" * 95)
    print("TOP 20 BY SHARPE (all include calm entry L3/T15/D5)")
    print("=" * 95)
    print(f"{'Strategy':<14s}  {'P&L':>10s}  {'Δ P&L':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
          f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}")
    print("─" * 95)

    for _, label, m in results[:20]:
        marker = " ◄ BASE" if "base" in label else ""
        delta = m["total_pnl"] - baseline["total_pnl"] if baseline else 0
        print(f"{label:<14s}  ${m['total_pnl']:>+9,.0f}  {delta:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}{marker}", flush=True)

    # Heat map
    print()
    print("── SHARPE HEAT MAP (Multiplier × Hours) — all with calm entry ──")
    mults = [m / 10.0 for m in range(14, 23)]
    hours_list = [h / 2.0 for h in range(2, 8)]
    result_map = {l: m for _, l, m in results if "base" not in l}

    print(f"{'Mult↓ Hrs→':<12s}", end="")
    for h in hours_list:
        print(f"  {h:>5.1f}h", end="")
    print()
    print("─" * (12 + len(hours_list) * 7))

    for mult in mults:
        print(f"  x{mult:<8.1f}  ", end="")
        for h in hours_list:
            key = f"x{mult:.1f} {h:.1f}h"
            if key in result_map:
                s = result_map[key]["sharpe"]
                marker = "*" if s > baseline["sharpe"] else " "
                print(f" {s:>5.3f}{marker}", end="")
            else:
                print(f"     - ", end="")
        print()

    print(f"\nCalm-only baseline Sharpe: {baseline['sharpe']:.3f}  (* = beats baseline)")


if __name__ == "__main__":
    main()
