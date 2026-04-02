"""
Test all combinations of the three winning features:
  1. Buffer decay (x1.75, 2.0h)
  2. Cushion recovery (N96 R67)
  3. Calm entry (L3 T15 D5)

8 configs: baseline + each solo + each pair + all three.

Run: python -m backtest.sweep_combined_features
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
    return cfg


def _add_buffer_decay(cfg):
    cfg.buffer_decay_start_mult = 1.75
    cfg.buffer_decay_hours = 2.0

def _add_cushion_recovery(cfg):
    cfg.cushion_nearstop_pct = 0.96
    cfg.cushion_recovery_pct = 0.67

def _add_calm_entry(cfg):
    cfg.calm_entry_lookback_min = 3
    cfg.calm_entry_threshold_pts = 15.0
    cfg.calm_entry_max_delay_min = 5


def main():
    configs = []

    # 0. Baseline
    configs.append(("Baseline", _base()))

    # 1. Solo features
    c = _base(); _add_buffer_decay(c)
    configs.append(("BufferDecay only", c))

    c = _base(); _add_cushion_recovery(c)
    configs.append(("CushionRecov only", c))

    c = _base(); _add_calm_entry(c)
    configs.append(("CalmEntry only", c))

    # 2. Pairs
    c = _base(); _add_buffer_decay(c); _add_cushion_recovery(c)
    configs.append(("Buffer + Cushion", c))

    c = _base(); _add_buffer_decay(c); _add_calm_entry(c)
    configs.append(("Buffer + Calm", c))

    c = _base(); _add_cushion_recovery(c); _add_calm_entry(c)
    configs.append(("Cushion + Calm", c))

    # 3. All three
    c = _base(); _add_buffer_decay(c); _add_cushion_recovery(c); _add_calm_entry(c)
    configs.append(("ALL THREE", c))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Combined features test: {n} configs, {N_WORKERS} workers\n", flush=True)
    hdr = (f"{'Strategy':<22s}  {'P&L':>10s}  {'Δ P&L':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  "
           f"{'Win%':>5s}  {'AvgWin':>7s}  {'AvgLoss':>8s}")
    print(hdr)
    print("─" * len(hdr), flush=True)

    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            idx, label, m = res
            results.append(res)

    results.sort(key=lambda x: x[2]["sharpe"], reverse=True)
    baseline = next((m for _, l, m in results if "Baseline" in l), None)

    for _, label, m in results:
        marker = " ◄ BASE" if "Baseline" in label else ""
        delta = m["total_pnl"] - baseline["total_pnl"] if baseline else 0
        print(f"{label:<22s}  ${m['total_pnl']:>+9,.0f}  {delta:>+7,.0f}  {m['sharpe']:>7.3f}  "
              f"${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%  "
              f"${m['avg_win']:>6,.0f}  ${m['avg_loss']:>7,.0f}{marker}", flush=True)


if __name__ == "__main__":
    main()
