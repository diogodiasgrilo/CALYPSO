"""Fine-grained max_spread_width sweep (every 5pt from 25 to 200).

Run: python -m backtest.sweep_max_spread_fine
"""
import multiprocessing as mp
import os
import statistics
from typing import Tuple


def run_one(msw: int) -> Tuple:
    from backtest.config import live_config
    from backtest.engine import run_backtest
    cfg = live_config()
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    cfg.max_spread_width = msw
    r = run_backtest(cfg, verbose=False)
    d = [x.net_pnl for x in r]
    mean = statistics.mean(d)
    std = statistics.stdev(d) if len(d) > 1 else 0
    sharpe = mean / std * 252**0.5 if std > 0 else 0
    neg = [p for p in d if p < 0]
    dd_dev = (sum(p**2 for p in neg) / len(d))**0.5 if neg else 0
    sortino = mean / dd_dev * 252**0.5 if dd_dev > 0 else 0
    peak = cum = mdd = 0.0
    for p in d:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return (msw, sum(d), sharpe, sortino, mdd, len(r))


def main():
    n_workers = min(8, os.cpu_count() or 4)
    widths = list(range(25, 205, 5))
    print(f"Sweeping {len(widths)} max_spread_width values with {n_workers} workers...", flush=True)

    results = []
    with mp.Pool(n_workers) as pool:
        for i, res in enumerate(pool.imap_unordered(run_one, widths)):
            results.append(res)
            msw, pnl, sharpe, sortino, mdd, days = res
            print(f"  [{i+1}/{len(widths)}] max_spread={msw:3d}: Sharpe {sharpe:.3f}  P&L ${pnl:+>9,.0f}  MaxDD ${mdd:,.0f}", flush=True)

    results.sort(key=lambda x: x[0])
    print(f"\n{'='*85}")
    print(f"{'max_spread':>10s}  {'P&L':>10s}  {'Sharpe':>7s}  {'Sortino':>8s}  {'MaxDD':>8s}")
    print(f"{'='*85}")
    for msw, pnl, sharpe, sortino, mdd, _ in results:
        marker = " <-- CURRENT" if msw == 85 else ""
        print(f"{msw:>10d}  ${pnl:>+9,.0f}  {sharpe:>7.3f}  {sortino:>8.3f}  ${mdd:>7,.0f}{marker}")

    print(f"\n--- TOP 10 by Sharpe ---")
    by_sharpe = sorted(results, key=lambda x: x[2], reverse=True)
    for msw, pnl, sharpe, sortino, mdd, _ in by_sharpe[:10]:
        print(f"  max_spread={msw:3d}: Sharpe {sharpe:.3f}  P&L ${pnl:+,.0f}  Sortino {sortino:.3f}  MaxDD ${mdd:,.0f}")


if __name__ == "__main__":
    main()
