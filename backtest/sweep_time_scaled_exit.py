"""
Sweep: Time-Scaled Return Exit — dynamic profit target based on credit
captured relative to time remaining.

Idea: early in the day, take smaller gains (more risk time ahead).
Late in the day, hold for theta (less time = less risk, more decay).

Formula: exit when captured_pct >= base / sqrt(hours_left / 6.5)

Example thresholds at different times (base=0.30):
  10:30 AM (5.5h left):  30% / sqrt(5.5/6.5) = 33%  → exit if captured 33%+
  11:30 AM (4.5h left):  30% / sqrt(4.5/6.5) = 36%  → exit if captured 36%+
  12:30 PM (3.5h left):  30% / sqrt(3.5/6.5) = 41%  → exit if captured 41%+
   1:30 PM (2.5h left):  30% / sqrt(2.5/6.5) = 48%  → exit if captured 48%+
   2:30 PM (1.5h left):  30% / sqrt(1.5/6.5) = 62%  → exit if captured 62%+
   3:30 PM (0.5h left):  30% / sqrt(0.5/6.5) = 108% → never exits (>100%)

Also tests fixed return % for comparison, and dollar targets.

Run: python -m backtest.sweep_time_scaled_exit
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

    # ── 0. Baseline ──────────────────────────────────────────────────────
    configs.append(("Baseline: hold to expiry", _base()))

    # ── 1. Fixed return % targets ────────────────────────────────────────
    for pct in [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
        cfg = _base()
        cfg.net_return_exit_pct = pct
        configs.append((f"Fixed {pct:.0%} of credit", cfg))

    # ── 2. Time-scaled return (base / sqrt(hours_left/6.5)) ──────────────
    #    Lower base = more aggressive (exits earlier/more often)
    #    Higher base = more conservative (only exits on big gains)
    for base in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70, 0.80]:
        cfg = _base()
        cfg.time_scaled_return_base = base
        configs.append((f"TimeScaled base={base:.0%}", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Testing {n} strategies across ~938 days, {N_WORKERS} workers\n", flush=True)
    hdr = (f"{'Strategy':<32s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}  {'Exits':>5s}")
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            marker = " ◄" if "Baseline" in label else ""
            print(f"  [{idx+1:2d}/{n}] {label:<30s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
                  f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
                  f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}  "
                  f"{m['early_exits']:>5d}{marker}", flush=True)
            results.append(res)

    # Sort by Sharpe
    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)

    print()
    print("=" * 105)
    print("RESULTS SORTED BY SHARPE RATIO")
    print("=" * 105)
    print(f"{'Strategy':<32s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}  {'Exits':>5s}")
    print("─" * 105)

    baseline = next((m for _, l, m in results if "Baseline" in l), None)
    for _, label, m in results:
        marker = " ◄ BASELINE" if "Baseline" in label else ""
        print(f"{label:<32s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}  "
              f"{m['early_exits']:>5d}{marker}", flush=True)

    if baseline:
        print()
        print("── P&L Delta vs Baseline ──")
        by_pnl = sorted(results, key=lambda x: x[2]["total_pnl"], reverse=True)
        for _, label, m in by_pnl:
            delta = m["total_pnl"] - baseline["total_pnl"]
            print(f"  {label:<32s}  ${m['total_pnl']:>+9,.0f} ({delta:>+8,.0f})  "
                  f"Sharpe {m['sharpe']:.3f}  Win% {m['win_rate']:.1f}%  "
                  f"Exits {m['early_exits']}", flush=True)


if __name__ == "__main__":
    main()
