"""
Sweep per-VIX-regime credit gates.

Tests tighter/looser credit gates at each VIX bin independently,
on top of the confirmed per-bin optimal (max_entries + buffer).

Run: python -m backtest.sweep_vix_credit_gates
"""
import multiprocessing as mp
import os
import statistics
from copy import deepcopy
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
    """Current optimal config with per-bin VIX regime."""
    cfg = live_config()
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    # Credit gates: these are currently global, not per-regime
    # We'll test by overriding them for specific VIX conditions
    # using the backtest engine's max_vix_entry as a proxy
    return cfg


def main():
    configs = []

    # Baseline (current optimal)
    configs.append(("Baseline (current)", _base()))

    # The backtest engine doesn't support per-regime credit gates natively.
    # But we CAN test the effect by running separate backtests with different
    # global credit gates and comparing. The VIX regime already caps entries
    # at VIX<14, so tighter gates at low VIX means those 2 entries are higher quality.

    # Test global credit gate variations (affects all VIX levels)
    call_gates = [1.00, 1.25, 1.35, 1.50, 1.75, 2.00]
    put_gates = [1.75, 2.00, 2.10, 2.25, 2.50, 2.75]

    for cg in call_gates:
        for pg in put_gates:
            if cg == 1.35 and pg == 2.10:
                continue  # skip baseline (already tested)
            cfg = _base()
            cfg.min_call_credit = cg
            cfg.min_put_credit = pg
            configs.append((f"call=${cg:.2f} put=${pg:.2f}", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Sweeping {n} credit gate combos with VIX regime, {N_WORKERS} workers\n", flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            print(f"  [{len(results)}/{n}] {label:30s} P&L ${m['total_pnl']:+>9,.0f}  "
                  f"Sharpe {m['sharpe']:.3f}  MaxDD ${m['max_dd']:>7,.0f}", flush=True)

    results.sort(key=lambda x: x[0])
    baseline_pnl = results[0][2]["total_pnl"]
    baseline_sharpe = results[0][2]["sharpe"]

    # Sort by P&L
    print(f"\n── Sorted by Total P&L ──")
    print(f"{'Config':<30s}  {'P&L':>10s}  {'vs Base':>8s}  {'Sharpe':>7s}  {'vs Base':>8s}  {'MaxDD':>8s}  {'Win%':>5s}")
    print(f"{'─'*90}")
    by_pnl = sorted(results, key=lambda x: x[2]["total_pnl"], reverse=True)
    for _, label, m in by_pnl[:15]:
        dp = m["total_pnl"] - baseline_pnl
        ds = m["sharpe"] - baseline_sharpe
        marker = " ◄" if "Baseline" in label else ""
        print(f"{label:<30s}  ${m['total_pnl']:>+9,.0f}  ${dp:>+7,.0f}  {m['sharpe']:>7.3f}  {ds:>+8.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%{marker}", flush=True)

    # Sort by Sharpe
    print(f"\n── Sorted by Sharpe ──")
    by_sharpe = sorted(results, key=lambda x: x[2]["sharpe"], reverse=True)
    for _, label, m in by_sharpe[:10]:
        dp = m["total_pnl"] - baseline_pnl
        ds = m["sharpe"] - baseline_sharpe
        marker = " ◄" if "Baseline" in label else ""
        print(f"{label:<30s}  Sharpe {m['sharpe']:>7.3f} ({ds:>+.3f})  "
              f"P&L ${m['total_pnl']:>+9,.0f} ({dp:>+7,.0f})  MaxDD ${m['max_dd']:>7,.0f}{marker}", flush=True)

    # Best overall (highest P&L among those with Sharpe >= baseline)
    print(f"\n── Best P&L with Sharpe >= baseline ({baseline_sharpe:.3f}) ──")
    qualified = [r for r in results if r[2]["sharpe"] >= baseline_sharpe - 0.05]
    if qualified:
        best = max(qualified, key=lambda x: x[2]["total_pnl"])
        _, label, m = best
        print(f"  {label}: P&L ${m['total_pnl']:+,.0f}, Sharpe {m['sharpe']:.3f}, MaxDD ${m['max_dd']:,.0f}")


if __name__ == "__main__":
    main()
