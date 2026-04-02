"""
Sweep: Range-Consumption Exit — close all positions when intraday SPX range
exceeds X% of the VIX-implied expected daily move.

Hypothesis: On swing days (early profit → late loss), the intraday range
expands beyond what the expected move priced in. Detecting this DURING the
day could let us close surviving positions before cascading stops hit.

Tests:
  - range_exit_pct: 0.50 to 2.00 (50% to 200% of expected move)
  - range_exit_after: None (always), "10:30", "11:00", "11:30" (only after this time)
  - Combined with VIX regime (current live config)

Run: python -m backtest.sweep_range_exit
"""
import multiprocessing as mp
import os
import statistics
from copy import deepcopy
from datetime import date
from typing import Tuple

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

    # Count days where range exit actually fired
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

    # ── 0. Baseline (no range exit) ──────────────────────────────────────
    configs.append(("Baseline: hold to expiry", _base()))

    # ── 1. Simple range thresholds (check from market open) ──────────────
    for pct in [0.50, 0.60, 0.70, 0.75, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30, 1.50, 1.75, 2.00]:
        cfg = _base()
        cfg.range_exit_pct = pct
        configs.append((f"Range exit >= {pct:.0%} EM", cfg))

    # ── 2. Range thresholds with time delay (only after entries start) ───
    for after_time in ["10:30", "11:00", "11:30"]:
        for pct in [0.60, 0.75, 0.90, 1.00, 1.20]:
            cfg = _base()
            cfg.range_exit_pct = pct
            cfg.range_exit_after = after_time
            configs.append((f"Range >= {pct:.0%} after {after_time}", cfg))

    # ── 3. Tighter range thresholds for high-VIX only ────────────────────
    #    (simulate by testing range_exit_pct values that are tighter —
    #     on high-VIX days expected_move is larger, so range_exit fires less)
    #    This tests if the signal is more useful on certain days.
    for pct in [0.50, 0.60, 0.70, 0.80]:
        cfg = _base()
        cfg.range_exit_pct = pct
        cfg.range_exit_after = "11:00"  # after E3 at 11:15 is placed
        configs.append((f"Range >= {pct:.0%} after 11:00 (tight)", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Testing {n} range-exit strategies across ~938 days, {N_WORKERS} workers\n", flush=True)
    print(f"{'Strategy':<42s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}  "
          f"{'AvgWin':>7s}  {'AvgLoss':>8s}  {'Exits':>5s}")
    print("─" * 105, flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "Baseline" in label else ""
            print(f"  [{idx+1:2d}/{n}] {label:<40s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
                  f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}  "
                  f"{m['early_exits']:>5d}{marker}", flush=True)
            results.append(res)

    # Sort by Sharpe for final summary
    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)

    print()
    print("=" * 105)
    print("RESULTS SORTED BY SHARPE RATIO")
    print("=" * 105)
    print(f"{'Strategy':<42s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}  "
          f"{'AvgWin':>7s}  {'AvgLoss':>8s}  {'Exits':>5s}")
    print("─" * 105)

    baseline_pnl = None
    baseline_sharpe = None
    for _, label, m in results:
        if "Baseline" in label:
            baseline_pnl = m["total_pnl"]
            baseline_sharpe = m["sharpe"]

    for _, label, m in results:
        marker = " ◄ BASELINE" if "Baseline" in label else ""
        delta_pnl = m["total_pnl"] - baseline_pnl if baseline_pnl is not None else 0
        print(f"{label:<42s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}  "
              f"{m['early_exits']:>5d}{marker}", flush=True)

    # Also show P&L delta vs baseline
    print()
    print("── P&L Delta vs Baseline ──")
    by_delta = sorted(results, key=lambda x: x[2]["total_pnl"], reverse=True)
    for _, label, m in by_delta[:15]:
        delta = m["total_pnl"] - baseline_pnl if baseline_pnl else 0
        print(f"  {label:<42s}  ${m['total_pnl']:>+9,.0f} ({delta:>+8,.0f})  "
              f"Sharpe {m['sharpe']:.3f}  Exits {m['early_exits']}", flush=True)

    # Show worst-case analysis: days where range exit HELPED vs HURT
    print()
    print("── Exit Frequency Analysis ──")
    for _, label, m in results[:5]:
        if m["early_exits"] > 0:
            pct_days = m["early_exits"] / m["days"] * 100
            print(f"  {label}: exits fired on {m['early_exits']}/{m['days']} days ({pct_days:.1f}%)")


if __name__ == "__main__":
    main()
