"""
Fine-grain sweep of time-scaled return exit around the 55-85% base range.

Prior sweep found TimeScaled 60-80% beats baseline (Sharpe 2.147 vs 2.094).
This narrows down the exact optimum.

Run: python -m backtest.sweep_time_scaled_fine
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

    # Fine-grain sweep: 55% to 85% in 2.5% steps
    for base_pct in [55, 57.5, 60, 62.5, 65, 67.5, 70, 72.5, 75, 77.5, 80, 82.5, 85]:
        cfg = _base()
        cfg.time_scaled_return_base = base_pct / 100.0
        configs.append((f"TimeScaled {base_pct:.1f}%", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Fine-grain sweep: {n} configs across ~938 days, {N_WORKERS} workers\n", flush=True)
    hdr = (f"{'Strategy':<30s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}  {'Exits':>5s}")
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "Baseline" in label else ""
            print(f"  [{idx+1:2d}/{n}] {label:<28s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
                  f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}  "
                  f"{m['early_exits']:>5d}{marker}", flush=True)
            results.append(res)

    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)

    baseline = next((m for _, l, m in results if "Baseline" in l), None)

    print()
    print("=" * 100)
    print("RESULTS SORTED BY SHARPE RATIO")
    print("=" * 100)
    print(f"{'Strategy':<30s}  {'P&L':>10s}  {'Δ P&L':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
          f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}  {'Exits':>5s}")
    print("─" * 100)

    for _, label, m in results:
        marker = " ◄ BASELINE" if "Baseline" in label else ""
        delta = m["total_pnl"] - baseline["total_pnl"] if baseline else 0
        print(f"{label:<30s}  ${m['total_pnl']:>+9,.0f}  {delta:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}  "
              f"{m['early_exits']:>5d}{marker}", flush=True)


if __name__ == "__main__":
    main()
