"""
Ultra fine-grain around x2.1/2.0h winner.
Mult 1.9-2.3 (step 0.05) × Hours 1.5-2.5 (step 0.25), all with calm entry.

Run: python -m backtest.sweep_buffer_decay_ultra
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
    cfg.calm_entry_lookback_min = 3
    cfg.calm_entry_threshold_pts = 15.0
    cfg.calm_entry_max_delay_min = 5
    return cfg


def main():
    configs = []

    # Baseline (calm only)
    configs.append(("Calm only", _base()))

    # Ultra fine: mult 1.90-2.30 (step 0.05) × hours 1.50-2.75 (step 0.25)
    for mult_x100 in range(190, 235, 5):  # 1.90, 1.95, 2.00, ..., 2.30
        mult = mult_x100 / 100.0
        for hours_x100 in range(150, 300, 25):  # 1.50, 1.75, 2.00, 2.25, 2.50, 2.75
            hours = hours_x100 / 100.0
            cfg = _base()
            cfg.buffer_decay_start_mult = mult
            cfg.buffer_decay_hours = hours
            configs.append((f"x{mult:.2f} {hours:.2f}h", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Ultra fine-grain: {n} configs, {N_WORKERS} workers\n", flush=True)
    hdr = (f"{'Strategy':<16s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}")
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "Calm" in label else ""
            print(f"  [{idx+1:2d}/{n}] {label:<14s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
                  f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}{marker}", flush=True)
            results.append(res)

    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)
    baseline = next((m for _, l, m in results if "Calm" in l), None)

    print()
    print("=" * 100)
    print("ALL RESULTS SORTED BY SHARPE (with calm entry)")
    print("=" * 100)
    print(f"{'Strategy':<16s}  {'P&L':>10s}  {'Δ P&L':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
          f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}")
    print("─" * 100)

    for _, label, m in results:
        marker = " ◄ BASE" if "Calm" in label else ""
        delta = m["total_pnl"] - baseline["total_pnl"] if baseline else 0
        print(f"{label:<16s}  ${m['total_pnl']:>+9,.0f}  {delta:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}{marker}", flush=True)


if __name__ == "__main__":
    main()
