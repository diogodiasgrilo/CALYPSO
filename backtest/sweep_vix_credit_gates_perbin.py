"""
Per-VIX-regime credit gate sweep.

For each VIX bin independently, test different call/put credit minimums
while keeping other bins at the global optimal ($2.00/$2.75).

Run: python -m backtest.sweep_vix_credit_gates_perbin
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
BREAKPOINTS = [14.0, 20.0, 30.0]
BIN_LABELS = ["VIX<14", "VIX 14-20", "VIX 20-30", "VIX≥30"]


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
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = BREAKPOINTS
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    # Initialize per-regime credit overrides to None (use global gates)
    cfg.vix_regime_min_call_credit = [None, None, None, None]
    cfg.vix_regime_min_put_credit = [None, None, None, None]
    return cfg


def main():
    # Credit gate combos to test per bin
    # Format: (call_gate, put_gate, label)
    gate_combos = [
        (None, None, "global $2.00/$2.75"),  # use global (no override)
        (1.35, 2.10, "$1.35/$2.10 (old)"),
        (1.50, 2.25, "$1.50/$2.25"),
        (1.75, 2.50, "$1.75/$2.50"),
        (2.00, 2.75, "$2.00/$2.75 (=global)"),
        (2.25, 3.00, "$2.25/$3.00"),
        (2.50, 3.25, "$2.50/$3.25"),
        (2.75, 3.50, "$2.75/$3.50"),
    ]

    # Baseline
    all_configs = [("Baseline (global $2.00/$2.75)", _base())]

    # Per-bin sweep: override ONE bin at a time
    for bin_idx in range(4):
        for call_g, put_g, gate_label in gate_combos:
            if call_g is None:
                continue  # skip "use global" — that's the baseline
            cfg = _base()
            mcc = [None, None, None, None]
            mpc = [None, None, None, None]
            mcc[bin_idx] = call_g
            mpc[bin_idx] = put_g
            cfg.vix_regime_min_call_credit = mcc
            cfg.vix_regime_min_put_credit = mpc
            all_configs.append((f"{BIN_LABELS[bin_idx]}: {gate_label}", cfg))

    n = len(all_configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(all_configs)]
    print(f"Sweeping {n} per-bin credit gate configs, {N_WORKERS} workers\n", flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            print(f"  [{len(results)}/{n}] {label:40s} P&L ${m['total_pnl']:+>9,.0f}  "
                  f"Sharpe {m['sharpe']:.3f}  MaxDD ${m['max_dd']:>7,.0f}", flush=True)

    results.sort(key=lambda x: x[0])
    baseline_pnl = results[0][2]["total_pnl"]
    baseline_sharpe = results[0][2]["sharpe"]

    # Show results per bin
    for bin_idx in range(4):
        print(f"\n── {BIN_LABELS[bin_idx]} ──")
        bin_results = [(label, m) for _, label, m in results if label.startswith(BIN_LABELS[bin_idx])]
        # Add baseline for comparison
        bin_results.insert(0, ("Baseline (global)", results[0][2]))
        by_pnl = sorted(bin_results, key=lambda x: x[1]["total_pnl"], reverse=True)
        for label, m in by_pnl:
            dp = m["total_pnl"] - baseline_pnl
            ds = m["sharpe"] - baseline_sharpe
            print(f"  {label:40s}  ${m['total_pnl']:>+9,.0f} ({dp:>+7,.0f})  "
                  f"Sharpe {m['sharpe']:.3f} ({ds:>+.3f})  MaxDD ${m['max_dd']:>7,.0f}", flush=True)

    # Overall best
    print(f"\n── Overall Top 10 by P&L ──")
    by_pnl_all = sorted(results, key=lambda x: x[2]["total_pnl"], reverse=True)
    for _, label, m in by_pnl_all[:10]:
        dp = m["total_pnl"] - baseline_pnl
        print(f"  {label:40s}  ${m['total_pnl']:>+9,.0f} ({dp:>+7,.0f})  "
              f"Sharpe {m['sharpe']:.3f}  MaxDD ${m['max_dd']:>7,.0f}", flush=True)


if __name__ == "__main__":
    main()
