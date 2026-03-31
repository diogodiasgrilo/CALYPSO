"""Quick sweep: VIX 20-30 regime variants. Run: python -m backtest.sweep_vix_regime_detail"""
import statistics, multiprocessing as mp, os
from copy import deepcopy
from backtest.config import live_config
from backtest.engine import run_backtest

def run_one(args):
    idx, label, cfg = args
    r = run_backtest(cfg, verbose=False)
    d = [x.net_pnl for x in r]
    mean = statistics.mean(d)
    std = statistics.stdev(d) if len(d) > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    peak = cum = mdd = 0.0
    for p in d:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wins = sum(1 for p in d if p > 0)
    return (idx, label, len(d), sum(d), sharpe, mdd, wins/len(d)*100 if d else 0)

def base():
    cfg = live_config()
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    cfg.skip_weekdays = [2, 3]
    cfg.dow_max_entries = {4: 2}
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    return cfg

def main():
    configs = []

    cfg = base()
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 2.00, 3.00]
    configs.append((0, "VIX20-30: 2e + $2.00 buf (CURRENT)", cfg))

    cfg = base()
    cfg.vix_regime_max_entries = [2, None, None, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 2.00, 3.00]
    configs.append((1, "VIX20-30: 3e + $2.00 buf", cfg))

    cfg = base()
    cfg.vix_regime_max_entries = [2, None, 0, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, None, 3.00]
    configs.append((2, "VIX20-30: SKIP (0 entries)", cfg))

    cfg = base()
    cfg.vix_regime_max_entries = [2, None, 1, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 2.00, 3.00]
    configs.append((3, "VIX20-30: 1e + $2.00 buf", cfg))

    cfg = base()
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 2.50, 3.00]
    configs.append((4, "VIX20-30: 2e + $2.50 buf", cfg))

    cfg = base()
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, None, 3.00]
    configs.append((5, "VIX20-30: 2e + $1.55 buf", cfg))

    n_workers = min(8, os.cpu_count() or 4)
    print(f"Sweeping 6 VIX 20-30 variants with {n_workers} workers...", flush=True)
    results = []
    with mp.Pool(n_workers, maxtasksperchild=4) as pool:
        for res in pool.imap_unordered(run_one, configs):
            results.append(res)
            idx, label, days, pnl, sharpe, mdd, wr = res
            print(f"  [{len(results)}/6] {label:40s} Sharpe {sharpe:.3f}", flush=True)

    print(f"\n{'Config':<40s}  {'Days':>4s}  {'P&L':>10s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Win%':>5s}")
    print(f"{'─'*80}")
    for idx, label, days, pnl, sharpe, mdd, wr in sorted(results, key=lambda x: -x[4]):
        marker = " ◄" if idx == 0 else ""
        print(f"{label:<40s}  {days:>4d}  ${pnl:>+9,.0f}  {sharpe:>7.3f}  ${mdd:>7,.0f}  {wr:>5.1f}%{marker}")

if __name__ == "__main__":
    main()
