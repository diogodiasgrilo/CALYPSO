"""
Sweep credit floors (MKT-029 fallback) with final config.

Tests call_credit_floor × put_credit_floor on top of:
- New credit gates: call=$2.00, put=$2.75
- VIX regime: [2,None,None,1] entries, $1.25 put buffer at VIX<14

Run: python -m backtest.sweep_credit_floors_final
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
        return {"total_pnl": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0}
    mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = sum(1 for p in daily if p > 0)
    return {"total_pnl": sum(daily), "sharpe": sharpe, "max_dd": max_dd,
            "win_rate": wins / n * 100}


def _run_one(args):
    idx, label, cfg = args
    r = run_backtest(cfg, verbose=False)
    m = _metrics(r)
    return (idx, label, m)


def _base():
    cfg = live_config()
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    return cfg


def main():
    configs = []

    # Baseline (current: call_floor=$0.75, put_floor=$2.00)
    cfg = _base()
    configs.append(("Baseline cf=$0.75 pf=$2.00", cfg))

    # 2D grid
    call_floors = [0.25, 0.40, 0.50, 0.60, 0.75, 1.00, 1.25, 1.50, 1.75]
    put_floors = [1.25, 1.50, 1.75, 2.00, 2.25, 2.50, 2.65]

    for cf in call_floors:
        for pf in put_floors:
            if cf == 0.75 and pf == 2.00:
                continue  # skip baseline
            cfg = _base()
            cfg.call_credit_floor = cf
            cfg.put_credit_floor = pf
            configs.append((f"cf=${cf:.2f} pf=${pf:.2f}", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Sweeping {n} credit floor combos, {N_WORKERS} workers\n", flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            print(f"  [{len(results)}/{n}] {label:30s} P&L ${m['total_pnl']:+>9,.0f}  "
                  f"Sharpe {m['sharpe']:.3f}  MaxDD ${m['max_dd']:>7,.0f}", flush=True)

    results.sort(key=lambda x: x[0])
    baseline_pnl = results[0][2]["total_pnl"]

    by_pnl = sorted(results, key=lambda x: x[2]["total_pnl"], reverse=True)
    print(f"\n── Top 15 by P&L ──")
    print(f"{'Config':<30s}  {'P&L':>10s}  {'vs Base':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}")
    print(f"{'─'*75}")
    for _, label, m in by_pnl[:15]:
        dp = m["total_pnl"] - baseline_pnl
        marker = " ◄" if "Baseline" in label else ""
        print(f"{label:<30s}  ${m['total_pnl']:>+9,.0f}  ${dp:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%{marker}", flush=True)

    by_sharpe = sorted(results, key=lambda x: x[2]["sharpe"], reverse=True)
    print(f"\n── Top 10 by Sharpe ──")
    for _, label, m in by_sharpe[:10]:
        dp = m["total_pnl"] - baseline_pnl
        print(f"  {label:30s}  Sharpe {m['sharpe']:.3f}  P&L ${m['total_pnl']:>+9,.0f} ({dp:>+7,.0f})  "
              f"MaxDD ${m['max_dd']:>7,.0f}", flush=True)


if __name__ == "__main__":
    main()
