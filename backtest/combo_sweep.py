"""
Combination sweep: tests interactions between the 6 key params.
Writes results incrementally — every combo result saved immediately.

Progress file: backtest/results/combo_progress.txt (updated every result)
Results file:  backtest/results/combo_results.csv (appended every result)

Run: python -u -m backtest.combo_sweep --workers 10
"""
import argparse
import csv
import multiprocessing as mp
import statistics
import sys
import time
from datetime import date, datetime as dt
from itertools import product
from pathlib import Path

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

FULL_START = date(2022, 5, 16)
FULL_END   = date(2026, 4, 8)
SLIPPAGE = 30.0
MARKUP   = 0.10

GRID = {
    "put_stop_buffer":                [175.0, 200.0, 225.0],
    "call_stop_buffer":               [75.0, 100.0, 125.0],
    "buffer_decay_start_mult":        [2.5, 3.0],
    "buffer_decay_hours":             [3.0, 4.0],
    "put_credit_floor":               [2.00, 2.50, 2.75],
    "max_spread_width":               [110, 120],
    "downday_theoretical_put_credit": [200.0, 260.0],  # interacts with call_stop_buffer in stop formula
}

# Lock these at confirmed optimal (from individual sweeps on new baseline)
LOCKED = {
    "vix_regime_min_call_credit":  [None, None, None, None],  # remove credit override
    "vix_regime_min_put_credit":   [None, None, None, None],  # remove credit override
    "vix_regime_put_stop_buffer":  [None, None, None, None],  # don't override put buffer for VIX<14
}

OUT_DIR = Path("backtest/results")
PROGRESS_FILE = OUT_DIR / "combo_progress.txt"
RESULTS_CSV = OUT_DIR / "combo_results.csv"

CSV_FIELDS = [
    "rank", "combo_id", "sharpe", "total_pnl", "max_dd", "calmar",
    "win_rate", "stop_rate", "stops", "placed", "days",
    "put_stop_buffer", "call_stop_buffer",
    "buffer_decay_start_mult", "buffer_decay_hours",
    "put_credit_floor", "max_spread_width",
    "downday_theoretical_put_credit",
    "elapsed_sec", "timestamp",
]


def _build_cfg(combo: dict) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = SLIPPAGE
    cfg.stop_spread_markup_pct = MARKUP
    for k, v in LOCKED.items():
        setattr(cfg, k, v)
    for k, v in combo.items():
        setattr(cfg, k, v)
    return cfg


def _worker(args):
    combo_id, combo = args
    t0 = time.time()
    cfg = _build_cfg(combo)
    results = run_backtest(cfg, verbose=False)
    elapsed = time.time() - t0

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
    calmar = mean * 252 / dd if dd > 0 else 0

    return {
        "combo_id": combo_id,
        "sharpe": sharpe, "total_pnl": total, "max_dd": dd,
        "calmar": calmar,
        "win_rate": wins / days * 100 if days else 0,
        "stop_rate": stops / placed * 100 if placed else 0,
        "stops": stops, "placed": placed, "days": days,
        "elapsed_sec": round(elapsed, 1),
        "timestamp": dt.now().strftime("%H:%M:%S"),
        **combo,
    }


def _write_progress(done, total, elapsed, avg_sec, best, latest):
    remaining = (total - done) * avg_sec
    pct = done / total * 100

    bar_width = 40
    filled = int(bar_width * done / total)
    bar = "█" * filled + "░" * (bar_width - filled)

    lines = [
        f"COMBO SWEEP PROGRESS",
        f"",
        f"  [{bar}] {pct:.1f}%",
        f"",
        f"  Done:      {done} / {total}",
        f"  Elapsed:   {elapsed/60:.0f} min",
        f"  Avg/combo: {avg_sec:.0f} sec",
        f"  Remaining: {remaining/60:.0f} min ({remaining/3600:.1f} hours)",
        f"  ETA:       {dt.fromtimestamp(time.time() + remaining).strftime('%H:%M')}",
        f"",
        f"  BEST SO FAR:",
        f"    Sharpe={best['sharpe']:.3f}  P&L=${best['total_pnl']:,.0f}  DD=${best['max_dd']:,.0f}",
        f"    put=${best['put_stop_buffer']/100:.2f}  call=${best['call_stop_buffer']/100:.2f}  "
        f"decay={best['buffer_decay_start_mult']:.1f}x/{best['buffer_decay_hours']:.0f}h  "
        f"pf=${best['put_credit_floor']:.2f}  sw={best['max_spread_width']}  "
        f"tp=${best['downday_theoretical_put_credit']/100:.2f}",
        f"",
        f"  LATEST:",
        f"    #{latest['combo_id']} Sharpe={latest['sharpe']:.3f} ({latest['elapsed_sec']:.0f}s)",
    ]

    with open(PROGRESS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    keys = list(GRID.keys())
    values = [GRID[k] for k in keys]
    all_combos = [dict(zip(keys, vals)) for vals in product(*values)]
    total = len(all_combos)

    print(f"COMBO SWEEP: {total} combinations, {args.workers} workers", flush=True)
    print(f"Params: {' × '.join(f'{len(v)}' for v in values)}", flush=True)
    print(f"Slippage: $0.30/leg, 10% markup", flush=True)
    print(f"Progress: {PROGRESS_FILE}", flush=True)
    print(f"Results:  {RESULTS_CSV}", flush=True)
    print(flush=True)

    # Initialize CSV with headers
    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        f.flush()

    worker_args = [(i, combo) for i, combo in enumerate(all_combos)]
    all_results = []
    t0 = time.time()
    combo_times = []

    with mp.Pool(processes=args.workers) as pool:
        for result in pool.imap_unordered(_worker, worker_args):
            all_results.append(result)
            combo_times.append(result["elapsed_sec"])
            done = len(all_results)
            elapsed = time.time() - t0
            avg_sec = statistics.mean(combo_times)

            best = max(all_results, key=lambda r: r["sharpe"])

            # Append to CSV immediately
            result["rank"] = 0  # Will be updated at end
            with open(RESULTS_CSV, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
                writer.writerow(result)
                f.flush()

            # Update progress file
            _write_progress(done, total, elapsed, avg_sec, best, result)

            # Print to console
            remaining = (total - done) * avg_sec
            print(
                f"  [{done:>3}/{total}] "
                f"Sharpe={result['sharpe']:.3f} "
                f"({result['elapsed_sec']:.0f}s) "
                f"Best={best['sharpe']:.3f} "
                f"ETA {remaining/60:.0f}m",
                flush=True
            )

    # Final: sort by Sharpe, rewrite CSV with ranks
    all_results.sort(key=lambda r: r["sharpe"], reverse=True)
    for i, r in enumerate(all_results):
        r["rank"] = i + 1

    final_csv = OUT_DIR / f"combo_sweep_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(final_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_results)

    # Print top 20
    print(f"\n{'='*95}", flush=True)
    print(f"  TOP 20 BY SHARPE ({total} combos tested)", flush=True)
    print(f"{'='*95}", flush=True)
    print(f"  {'#':>3} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>8} {'WR%':>6} {'StopR':>6} {'Stops':>6}  Config", flush=True)
    print(f"  {'-'*90}", flush=True)
    for r in all_results[:20]:
        print(
            f"  {r['rank']:>3} {r['sharpe']:>7.3f} {r['total_pnl']:>10,.0f} {r['max_dd']:>8,.0f} "
            f"{r['win_rate']:>5.1f}% {r['stop_rate']:>5.1f}% {r['stops']:>6}  "
            f"pb=${r['put_stop_buffer']/100:.2f} cb=${r['call_stop_buffer']/100:.2f} "
            f"d={r['buffer_decay_start_mult']:.1f}x/{r['buffer_decay_hours']:.0f}h "
            f"pf=${r['put_credit_floor']:.2f} sw={r['max_spread_width']} "
            f"tp=${r['downday_theoretical_put_credit']/100:.2f}",
            flush=True
        )

    # Run best combo day-by-day
    best = all_results[0]
    print(f"\n  Running day-by-day for #{best['rank']}...", flush=True)
    best_combo = {k: best[k] for k in keys}
    cfg = _build_cfg(best_combo)
    daily = run_backtest(cfg, verbose=False)

    daily_csv = OUT_DIR / f"combo_best_daily_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(daily_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "net_pnl", "entries_placed", "stops_hit"])
        w.writeheader()
        cum = 0
        for r in daily:
            cum += r.net_pnl
            w.writerow({"date": r.date.isoformat(), "net_pnl": r.net_pnl,
                        "entries_placed": r.entries_placed, "stops_hit": r.stops_hit})
        f.flush()

    elapsed = time.time() - t0
    print(f"\n  Done! {total} combos in {elapsed/60:.0f} min", flush=True)
    print(f"  Ranked results: {final_csv}", flush=True)
    print(f"  Best daily:     {daily_csv}", flush=True)
