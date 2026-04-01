"""Sweep E6 upday threshold with final credit gates ($2.00/$2.75).
Run: python -m backtest.sweep_e6_threshold_final
"""
import multiprocessing as mp, os, statistics
from backtest.config import live_config
from backtest.engine import run_backtest

N_WORKERS = min(8, os.cpu_count() or 4)

def _run_one(args):
    idx, label, cfg = args
    r = run_backtest(cfg, verbose=False)
    d = [x.net_pnl for x in r]
    n = len(d)
    if n == 0: return (idx, label, {"total_pnl": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0})
    mean = statistics.mean(d); std = statistics.stdev(d) if n > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    peak = cum = mdd = 0.0
    for p in d: cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wins = sum(1 for p in d if p > 0)
    return (idx, label, {"total_pnl": sum(d), "sharpe": sharpe, "max_dd": mdd, "win_rate": wins/n*100})

def _base():
    cfg = live_config()
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [1.25, None, None, None]
    cfg.vix_regime_call_stop_buffer = [None, None, None, None]
    return cfg

def main():
    configs = []
    cfg = _base(); cfg.conditional_upday_e6_enabled = False
    configs.append(("E6 OFF", cfg))
    for thr in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.48, 0.50, 0.60, 0.70, 0.80, 1.00]:
        cfg = _base(); cfg.conditional_upday_e6_enabled = True; cfg.upday_threshold_pct = thr
        configs.append((f"E6 ON thr={thr:.2f}%", cfg))

    n = len(configs)
    tasks = [(i, label, cfg) for i, (label, cfg) in enumerate(configs)]
    print(f"Sweeping {n} E6 upday configs, {N_WORKERS} workers\n", flush=True)
    results = []
    with mp.Pool(N_WORKERS, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(_run_one, tasks):
            results.append(res)

    results.sort(key=lambda x: x[0])
    print(f"{'Config':<25s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}")
    print(f"{'─'*60}")
    by_pnl = sorted(results, key=lambda x: x[2]["total_pnl"], reverse=True)
    for _, label, m in by_pnl:
        print(f"{label:<25s}  ${m['total_pnl']:>+9,.0f}  {m['sharpe']:>7.3f}  ${m['max_dd']:>7,.0f}  {m['win_rate']:>5.1f}%", flush=True)

if __name__ == "__main__":
    main()
