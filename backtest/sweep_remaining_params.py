"""
Sweep remaining unverified parameters with max_spread_width=110.

Round 1: call_starting_otm_multiplier × put_starting_otm_multiplier (2D)
Round 2: target_delta
Round 3: call_min_spread_width × put_min_spread_width (2D)
Round 4: entry_times (top timing combos)

Each round locks the best and feeds into the next.

Run: python -m backtest.sweep_remaining_params
"""
import multiprocessing as mp
import os
import statistics
from copy import deepcopy
from datetime import date
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
        return {"days": 0, "net_pnl": 0, "sharpe": 0, "sortino": 0, "max_dd": 0,
                "win_rate": 0, "total_pnl": 0}
    mean = statistics.mean(daily)
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    neg = [p for p in daily if p < 0]
    dd_dev = (sum(p**2 for p in neg) / n)**0.5 if neg else 0
    sortino = mean / dd_dev * 252**0.5 if dd_dev > 0 else 0
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p; peak = max(peak, cum); max_dd = min(max_dd, cum - peak)
    wins = sum(1 for p in daily if p > 0)
    return {"days": n, "net_pnl": sum(daily), "sharpe": sharpe, "sortino": sortino,
            "max_dd": max_dd, "win_rate": wins / n * 100 if n > 0 else 0, "total_pnl": sum(daily)}


def _run_one(args):
    idx, label, cfg = args
    results = run_backtest(cfg, verbose=False)
    m = _metrics(results)
    return (idx, label, m)


def _base():
    cfg = live_config()
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    # Apply VIX regime (the confirmed optimal)
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    return cfg


def _sweep(configs, title):
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    n = len(tasks)
    print(f"\n── {title} ({n} configs) ──\n", flush=True)
    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)
            idx, label, m = res
            print(f"  [{len(results)}/{n}] {label:45s} Sharpe {m['sharpe']:.3f}  "
                  f"P&L ${m['total_pnl']:+>9,.0f}  MaxDD ${m['max_dd']:>7,.0f}", flush=True)
    results.sort(key=lambda x: x[0])
    by_sharpe = sorted(results, key=lambda x: x[2]["sharpe"], reverse=True)
    print(f"\n  Top 5:")
    for _, label, m in by_sharpe[:5]:
        print(f"    {label:45s} Sharpe {m['sharpe']:.3f}  P&L ${m['total_pnl']:+>9,.0f}  "
              f"MaxDD ${m['max_dd']:>7,.0f}", flush=True)
    return by_sharpe


def main():
    print(f"{'='*80}")
    print(f"REMAINING PARAMETER SWEEP (locked: max_spread=110, all reconverged params)")
    print(f"Period: {START_DATE} → {END_DATE} | 1-min | Real Greeks | {N_WORKERS} workers")
    print(f"{'='*80}")

    cfg = _base()

    # ── Round 1: OTM multipliers (2D) ────────────────────────────────
    configs = []
    call_mults = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    put_mults = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5]
    for cm in call_mults:
        for pm in put_mults:
            c = deepcopy(cfg)
            c.call_starting_otm_multiplier = cm
            c.put_starting_otm_multiplier = pm
            configs.append((f"call={cm:.1f}× put={pm:.1f}×", c))

    r1 = _sweep(configs, "Round 1: OTM Multipliers (call × put)")
    best_label = r1[0][1]
    best_cm = float(best_label.split("call=")[1].split("×")[0])
    best_pm = float(best_label.split("put=")[1].split("×")[0])
    print(f"\n  ★ BEST: call={best_cm:.1f}× put={best_pm:.1f}×  Sharpe {r1[0][2]['sharpe']:.3f}")
    cfg.call_starting_otm_multiplier = best_cm
    cfg.put_starting_otm_multiplier = best_pm

    # ── Round 2: target_delta ─────────────────────────────────────────
    configs = []
    for delta in [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 15.0]:
        c = deepcopy(cfg)
        c.target_delta = delta
        configs.append((f"delta={delta:.0f}", c))

    r2 = _sweep(configs, "Round 2: Target Delta")
    best_delta = float(r2[0][1].split("=")[1])
    print(f"\n  ★ BEST: delta={best_delta:.0f}  Sharpe {r2[0][2]['sharpe']:.3f}")
    cfg.target_delta = best_delta

    # ── Round 3: min spread widths (2D) ───────────────────────────────
    configs = []
    for cw in [15, 20, 25, 30, 35, 40]:
        for pw in [15, 20, 25, 30, 35, 40]:
            c = deepcopy(cfg)
            c.call_min_spread_width = cw
            c.put_min_spread_width = pw
            configs.append((f"call_min={cw}pt put_min={pw}pt", c))

    r3 = _sweep(configs, "Round 3: Min Spread Widths (call × put)")
    best_label = r3[0][1]
    best_cw = int(best_label.split("call_min=")[1].split("pt")[0])
    best_pw = int(best_label.split("put_min=")[1].split("pt")[0])
    print(f"\n  ★ BEST: call_min={best_cw}pt put_min={best_pw}pt  Sharpe {r3[0][2]['sharpe']:.3f}")
    cfg.call_min_spread_width = best_cw
    cfg.put_min_spread_width = best_pw

    # ── Round 4: entry_times ──────────────────────────────────────────
    configs = []
    timing_options = [
        ("10:15/10:45/11:15 (current)", ["10:15", "10:45", "11:15"]),
        ("10:05/10:35/11:05 (old)", ["10:05", "10:35", "11:05"]),
        ("10:00/10:30/11:00", ["10:00", "10:30", "11:00"]),
        ("10:15/10:45/11:15/11:45 (4e)", ["10:15", "10:45", "11:15", "11:45"]),
        ("10:15/11:15 (2e wide)", ["10:15", "11:15"]),
        ("10:30/11:00/11:30", ["10:30", "11:00", "11:30"]),
        ("10:15/10:45/11:15/11:45/12:15 (5e)", ["10:15", "10:45", "11:15", "11:45", "12:15"]),
        ("10:00/10:45/11:30 (45min gap)", ["10:00", "10:45", "11:30"]),
    ]
    for label, times in timing_options:
        c = deepcopy(cfg)
        c.entry_times = times
        configs.append((label, c))

    r4 = _sweep(configs, "Round 4: Entry Times")
    best_times_label = r4[0][1]
    print(f"\n  ★ BEST: {best_times_label}  Sharpe {r4[0][2]['sharpe']:.3f}")

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"FINAL RESULTS")
    print(f"{'='*80}")
    print(f"  OTM multipliers:  call={best_cm:.1f}× put={best_pm:.1f}×  (was 3.5×/4.0×)")
    print(f"  Target delta:     {best_delta:.0f}  (was 8)")
    print(f"  Min spread width: call={best_cw}pt put={best_pw}pt  (was 25/25)")
    print(f"  Entry times:      {best_times_label}")
    print(f"  Final Sharpe:     {r4[0][2]['sharpe']:.3f}")
    print(f"  Final P&L:        ${r4[0][2]['total_pnl']:+,.0f}")
    print(f"  Final MaxDD:      ${r4[0][2]['max_dd']:,.0f}")


if __name__ == "__main__":
    main()
