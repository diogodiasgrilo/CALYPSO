"""
Credit gate sweep on final baseline (new buffers + slippage + put_floor $2.75 + spread 130pt).

Run: python -m backtest.sweep_credit_final
"""
from backtest.config import live_config
from backtest.engine import run_backtest
from datetime import date
from concurrent.futures import ProcessPoolExecutor, as_completed
import statistics

FULL_START = date(2022, 5, 16)
FULL_END   = date(2026, 4, 8)


def build_cfg(**overrides):
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = 30.0
    cfg.stop_spread_markup_pct = 0.10
    cfg.put_stop_buffer = 200.0
    cfg.call_stop_buffer = 100.0
    cfg.buffer_decay_start_mult = 3.0
    cfg.buffer_decay_hours = 4.0
    cfg.vix_regime_min_call_credit = [None, None, None, None]
    cfg.vix_regime_min_put_credit = [None, None, None, None]
    cfg.put_credit_floor = 2.75
    cfg.max_spread_width = 120
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def summarise(results, label):
    pnls = [r.net_pnl for r in results]
    total = sum(pnls)
    days = len(results)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    stops = sum(r.stops_hit for r in results)
    mean = statistics.mean(pnls) if pnls else 0
    stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0
    peak = cum = dd = 0.0
    for p in pnls:
        cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
    return {
        "label": label, "sharpe": sharpe, "total_pnl": total,
        "max_dd": dd, "win_rate": wins / days * 100 if days else 0,
        "stop_rate": stops / placed * 100 if placed else 0,
        "stops": stops, "placed": placed,
    }


def _run(args):
    label, cfg = args
    return summarise(run_backtest(cfg, verbose=False), label)


if __name__ == "__main__":
    combos = [
        ("c$1.25 p$2.25", 1.25, 2.25),
        ("c$1.50 p$2.50", 1.50, 2.50),
        ("c$1.75 p$2.50", 1.75, 2.50),
        ("c$1.75 p$2.75", 1.75, 2.75),
        ("c$2.00 p$2.75 CURRENT", 2.00, 2.75),
        ("c$2.00 p$3.00", 2.00, 3.00),
        ("c$2.25 p$3.00", 2.25, 3.00),
        ("c$2.25 p$3.25", 2.25, 3.25),
        ("c$2.50 p$3.25", 2.50, 3.25),
        ("c$1.50 p$2.75", 1.50, 2.75),
        ("c$1.00 p$2.75", 1.00, 2.75),
    ]

    configs = [(label, build_cfg(min_call_credit=mc, min_put_credit=mp))
               for label, mc, mp in combos]

    print(f"Credit Gate Sweep (final baseline + slippage)")
    print(f"{len(configs)} configs, 8 workers\n")

    all_stats = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_run, c): c[0] for c in configs}
        for f in as_completed(futures):
            all_stats.append(f.result())

    all_stats.sort(key=lambda s: s["sharpe"], reverse=True)

    print(f"{'Label':<26} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>8} {'WR%':>6} {'StopR':>6} {'Stops':>6}")
    print("-" * 72)
    for s in all_stats:
        cur = " *" if "CURRENT" in s["label"] else ""
        print(f"{s['label']:<26} {s['sharpe']:>7.3f} {s['total_pnl']:>10,.0f} {s['max_dd']:>8,.0f} {s['win_rate']:>5.1f}% {s['stop_rate']:>5.1f}% {s['stops']:>6}{cur}")

    best = all_stats[0]
    print(f"\nBest: {best['label']} (Sharpe {best['sharpe']:.3f})")
