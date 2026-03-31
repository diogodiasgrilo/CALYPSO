"""
Finish the regime grid sweep — Phase 2C (cross skip×cap) + Phase 3 (cross all).

Uses hardcoded results from the completed Phase 1 + 2A + 2B.
Run: python -m backtest.sweep_regime_grid_finish
"""
import multiprocessing as mp
import os
import statistics
import time
from copy import deepcopy
from datetime import date, datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Tuple

from backtest.config import live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
N_WORKERS = min(8, os.cpu_count() or 4)


def _metrics(results):
    daily = [r.net_pnl for r in results]
    n = len(daily)
    if n == 0:
        return {"days": 0, "net_pnl": 0, "sharpe": 0, "sortino": 0, "max_dd": 0, "win_rate": 0}
    total = sum(daily); mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    neg = [p for p in daily if p < 0]
    dd_dev = (sum(p**2 for p in neg) / n)**0.5 if neg else 0
    sortino = mean / dd_dev * 252**0.5 if dd_dev > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = sum(1 for p in daily if p > 0)
    return {"days": n, "net_pnl": total, "sharpe": sharpe, "sortino": sortino,
            "max_dd": max_dd, "win_rate": wins / n * 100 if n > 0 else 0}


def _run_one(args):
    idx, label, cfg = args
    results = run_backtest(cfg, verbose=False)
    m = _metrics(results)
    return (idx, label, m)


def _base():
    cfg = live_config()
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    return cfg


def _run_sweep(configs):
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            print(f"  [{len(results)}/{len(tasks)}] {label:55s} Sharpe {m['sharpe']:.3f}  "
                  f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}", flush=True)
    results.sort(key=lambda x: x[0])
    return results


def main():
    print(f"{'='*80}")
    print(f"PHASE 2C + PHASE 3: Cross-interactions")
    print(f"{'='*80}\n")

    # ── Best per-bin VIX regime (from completed Phase 1) ──
    # VIX<14: 2e + $1.25 buf
    # VIX 14-20: 3e + default
    # VIX 20-30: 3e + default
    # VIX≥30: 1e + default

    # ── Best skip combos (from completed Phase 2A) ──
    # #1: Skip Wed+Thu+Fri (Sharpe 4.130)
    # #2: Skip Tue+Wed+Thu (Sharpe 4.018)
    # #3: Skip Wed+Thu (Sharpe 3.731)

    # ── Best entry caps (from completed Phase 2B) ──
    # #1: Wed+Thu=2e (Sharpe 2.591)
    # #2: Wed=1e (Sharpe 2.584)
    # #3: Wed=2e (Sharpe 2.533)

    configs = []

    # ── Phase 2C: Top 3 skips × Top 3 caps ──────────────────
    print("── Phase 2C: Skip × Cap combinations ──\n", flush=True)

    skip_options = [
        ("Skip Wed+Thu+Fri", [2, 3, 4]),
        ("Skip Tue+Wed+Thu", [1, 2, 3]),
        ("Skip Wed+Thu", [2, 3]),
    ]
    cap_options = [
        ("Wed+Thu=2e", {2: 2, 3: 2}),
        ("Wed=1e", {2: 1}),
        ("Wed=2e", {2: 2}),
        ("No cap", {}),
    ]

    # Baseline
    configs.append(("Baseline (no features)", _base()))

    for skip_label, skip_days in skip_options:
        for cap_label, cap_dict in cap_options:
            # Don't cap a day we're already skipping
            effective_cap = {k: v for k, v in cap_dict.items() if k not in skip_days}
            cfg = _base()
            cfg.skip_weekdays = skip_days
            cfg.dow_max_entries = effective_cap
            configs.append((f"{skip_label} + {cap_label}", cfg))

    # Also just the best caps without skip
    for cap_label, cap_dict in cap_options:
        if not cap_dict:
            continue
        cfg = _base()
        cfg.dow_max_entries = cap_dict
        configs.append((f"{cap_label} (no skip)", cfg))

    p2c_results = _run_sweep(configs)
    by_sharpe = sorted(p2c_results, key=lambda x: x[2]["sharpe"], reverse=True)
    baseline_sharpe = p2c_results[0][2]["sharpe"]

    print(f"\n  Top 10 Skip × Cap:")
    for _, label, m in by_sharpe[:10]:
        delta = m["sharpe"] - baseline_sharpe
        print(f"    {label:55s} Sharpe {m['sharpe']:.3f} ({delta:+.3f})  "
              f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}  Win {m['win_rate']:.1f}%  {m['days']} days",
              flush=True)

    best_dow_label, best_dow_skip, best_dow_cap = None, [], {}
    for _, label, m in by_sharpe:
        if label == "Baseline (no features)":
            continue
        best_dow_label = label
        # Parse from the known options
        for sl, sd in skip_options:
            if sl in label:
                best_dow_skip = sd; break
        for cl, cd in cap_options:
            if cl in label:
                best_dow_cap = {k: v for k, v in cd.items() if k not in best_dow_skip}; break
        break

    # ── Phase 3: Cross VIX regime × DoW ──────────────────────
    print(f"\n\n── Phase 3: Cross VIX regime × DoW ──\n", flush=True)

    configs3 = []

    # Baseline
    configs3.append(("Baseline", _base()))

    # Best VIX regime only (per-bin optimal)
    def apply_vix_regime(cfg):
        cfg.vix_regime_enabled = True
        cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
        cfg.vix_regime_max_entries = [2, None, None, 1]  # 2@<14, 3@14-30, 1@30+
        cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]  # $1.25@<14, default elsewhere
        cfg.vix_regime_call_stop_buffer = [None, None, None, None]
        return cfg

    cfg = apply_vix_regime(_base())
    configs3.append(("VIX regime only (per-bin optimal)", cfg))

    # Best DoW only
    cfg = _base()
    cfg.skip_weekdays = best_dow_skip
    cfg.dow_max_entries = best_dow_cap
    configs3.append((f"DoW only: {best_dow_label}", cfg))

    # Combined: VIX regime + DoW
    cfg = apply_vix_regime(_base())
    cfg.skip_weekdays = best_dow_skip
    cfg.dow_max_entries = best_dow_cap
    configs3.append((f"COMBINED: VIX + {best_dow_label}", cfg))

    # Combined at 2 contracts
    cfg = apply_vix_regime(_base())
    cfg.skip_weekdays = best_dow_skip
    cfg.dow_max_entries = best_dow_cap
    cfg.contracts = 2
    configs3.append((f"COMBINED 2c: VIX + {best_dow_label}", cfg))

    # Also test Skip Wed+Thu (simpler, 3rd best skip) + VIX regime
    cfg = apply_vix_regime(_base())
    cfg.skip_weekdays = [2, 3]
    configs3.append(("VIX + Skip Wed+Thu", cfg))

    cfg = apply_vix_regime(_base())
    cfg.skip_weekdays = [2, 3]
    cfg.contracts = 2
    configs3.append(("VIX + Skip Wed+Thu 2c", cfg))

    # Skip Wed+Thu alone at 2c
    cfg = _base()
    cfg.skip_weekdays = [2, 3]
    cfg.contracts = 2
    configs3.append(("Skip Wed+Thu 2c (no VIX regime)", cfg))

    p3_results = _run_sweep(configs3)
    by_sharpe3 = sorted(p3_results, key=lambda x: x[2]["sharpe"], reverse=True)
    baseline_sharpe3 = p3_results[0][2]["sharpe"]

    print(f"\n  Final rankings:")
    for _, label, m in by_sharpe3:
        delta = m["sharpe"] - baseline_sharpe3
        print(f"    {label:55s} Sharpe {m['sharpe']:.3f} ({delta:+.3f})  "
              f"P&L ${m['net_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}  Win {m['win_rate']:.1f}%  {m['days']} days",
              flush=True)

    print(f"\n{'='*80}")
    print(f"RECOMMENDED CONFIG:")
    best = by_sharpe3[0]
    print(f"  {best[1]}")
    print(f"  Sharpe {best[2]['sharpe']:.3f}  P&L ${best[2]['net_pnl']:+,.0f}  MaxDD ${best[2]['max_dd']:,.0f}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
