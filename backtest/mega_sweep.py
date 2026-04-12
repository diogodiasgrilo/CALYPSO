"""
MEGA SWEEP: Full combinatorial optimization with slippage model.

Tests 6,912 combinations of 11 interacting parameters.
All other params locked at confirmed-optimal values.

Outputs:
  1. Top 50 combinations ranked by Sharpe
  2. Top 10 by P&L, by MaxDD (lowest), by Win Rate
  3. Full day-by-day simulation of the #1 combo
  4. CSV with all results

Run: python -m backtest.mega_sweep
     python -m backtest.mega_sweep --workers 8
     python -m backtest.mega_sweep --top 100

Expected runtime: ~7 hours with 8 workers on 1-min data.
"""
import argparse
import csv
import multiprocessing as mp
import statistics
import time
from datetime import date, datetime as dt
from itertools import product
from pathlib import Path
from typing import Dict, List, Optional

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

# ── Date range ───────────────────────────────────────────────────────────────
FULL_START = date(2022, 5, 16)
FULL_END   = date(2026, 4, 8)

# ── Slippage model (calibrated on 6 live days) ──────────────────────────────
SLIPPAGE = 30.0    # $0.30/leg
MARKUP   = 0.10    # 10% spread markup

# ── Parameters to sweep (all combinations) ───────────────────────────────────
GRID = {
    "put_stop_buffer":              [175.0, 200.0, 225.0],
    "call_stop_buffer":             [75.0, 100.0, 125.0],
    "buffer_decay_start_mult":      [2.5, 3.0],
    "buffer_decay_hours":           [3.0, 4.0],
    "max_spread_width":             [110, 120],
    "put_credit_floor":             [2.00, 2.50, 2.75],
    "min_call_credit":              [1.50, 2.00],
    "min_put_credit":               [2.75, 3.00],
    "vix_regime_credit":            ["remove", "keep"],
    "base_entry_downday_callonly_pct": [0.30, 0.57],
    "downday_theoretical_put_credit": [200.0, 260.0],
}


def _build_combo_cfg(combo: dict) -> BacktestConfig:
    """Build a config from a parameter combination."""
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = SLIPPAGE
    cfg.stop_spread_markup_pct = MARKUP

    # Apply sweep values
    cfg.put_stop_buffer = combo["put_stop_buffer"]
    cfg.call_stop_buffer = combo["call_stop_buffer"]
    cfg.buffer_decay_start_mult = combo["buffer_decay_start_mult"]
    cfg.buffer_decay_hours = combo["buffer_decay_hours"]
    cfg.max_spread_width = combo["max_spread_width"]
    cfg.put_credit_floor = combo["put_credit_floor"]
    cfg.min_call_credit = combo["min_call_credit"]
    cfg.min_put_credit = combo["min_put_credit"]
    cfg.base_entry_downday_callonly_pct = combo["base_entry_downday_callonly_pct"]
    cfg.downday_theoretical_put_credit = combo["downday_theoretical_put_credit"]

    # VIX regime credit overrides
    if combo["vix_regime_credit"] == "remove":
        cfg.vix_regime_min_call_credit = [None, None, None, None]
        cfg.vix_regime_min_put_credit = [None, None, None, None]
    # else: keep live_config() defaults [None, 1.35, None, None] / [None, 2.10, None, None]

    return cfg


def _combo_label(combo: dict) -> str:
    """Short label for a combo."""
    return (
        f"pb{combo['put_stop_buffer']:.0f}_cb{combo['call_stop_buffer']:.0f}_"
        f"d{combo['buffer_decay_start_mult']:.1f}x{combo['buffer_decay_hours']:.0f}h_"
        f"sw{combo['max_spread_width']}_pf{combo['put_credit_floor']:.2f}_"
        f"mc{combo['min_call_credit']:.2f}_mp{combo['min_put_credit']:.2f}_"
        f"vr{'N' if combo['vix_regime_credit']=='remove' else 'Y'}_"
        f"dd{combo['base_entry_downday_callonly_pct']:.2f}_"
        f"tp{combo['downday_theoretical_put_credit']:.0f}"
    )


def _worker(args):
    """Top-level worker function for multiprocessing (must be picklable)."""
    combo_idx, combo = args
    cfg = _build_combo_cfg(combo)
    results = run_backtest(cfg, verbose=False)

    pnls = [r.net_pnl for r in results]
    total_pnl = sum(pnls)
    days = len(results)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    stops = sum(r.stops_hit for r in results)

    mean = statistics.mean(pnls) if pnls else 0
    stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    return {
        "idx": combo_idx,
        "label": _combo_label(combo),
        "sharpe": sharpe,
        "total_pnl": total_pnl,
        "max_dd": max_dd,
        "calmar": calmar,
        "win_rate": wins / days * 100 if days else 0,
        "stop_rate": stops / placed * 100 if placed else 0,
        "stops": stops,
        "placed": placed,
        "days": days,
        "mean_daily": mean,
        # Store combo params for later analysis
        **{f"p_{k}": v for k, v in combo.items()},
    }


def _run_best_with_daily(combo: dict) -> List[dict]:
    """Run the best combo and return per-day results."""
    cfg = _build_combo_cfg(combo)
    results = run_backtest(cfg, verbose=False)
    daily = []
    for r in results:
        daily.append({
            "date": r.date.isoformat(),
            "net_pnl": r.net_pnl,
            "entries_placed": r.entries_placed,
            "stops_hit": r.stops_hit,
            "entries_skipped": r.entries_skipped,
        })
    return daily


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mega combinatorial sweep")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--top", type=int, default=50, help="Show top N results")
    args = parser.parse_args()

    n_workers = args.workers

    # Build all combos
    keys = list(GRID.keys())
    values = [GRID[k] for k in keys]
    all_combos = [dict(zip(keys, vals)) for vals in product(*values)]
    total = len(all_combos)

    print(f"\n{'='*70}")
    print(f"  MEGA SWEEP: {total:,} combinations")
    print(f"  {len(keys)} params × {' × '.join(str(len(v)) for v in values)}")
    print(f"  Workers: {n_workers} | Data: 1-min | Slippage: $0.30/leg, 10% markup")
    print(f"  Period: {FULL_START} → {FULL_END}")
    print(f"  Est. time: {total / n_workers * 30 / 3600:.1f} hours")
    print(f"{'='*70}\n")

    # Run all combos
    worker_args = [(i, combo) for i, combo in enumerate(all_combos)]
    all_results = []
    t0 = time.time()

    with mp.Pool(processes=n_workers) as pool:
        for i, result in enumerate(pool.imap_unordered(_worker, worker_args), 1):
            all_results.append(result)
            if i % 100 == 0 or i == total:
                elapsed = time.time() - t0
                rate = i / elapsed
                eta = (total - i) / rate if rate > 0 else 0
                best_so_far = max(all_results, key=lambda r: r["sharpe"])
                print(
                    f"  [{i:>6,}/{total:,}] "
                    f"{elapsed/60:.0f}m elapsed, {eta/60:.0f}m remaining | "
                    f"Best Sharpe so far: {best_so_far['sharpe']:.3f}"
                )

    elapsed = time.time() - t0
    print(f"\n  Completed {total:,} combos in {elapsed/60:.0f} minutes ({elapsed/3600:.1f} hours)")

    # ── Sort and display results ─────────────────────────────────────────
    by_sharpe = sorted(all_results, key=lambda r: r["sharpe"], reverse=True)
    by_pnl = sorted(all_results, key=lambda r: r["total_pnl"], reverse=True)
    by_dd = sorted(all_results, key=lambda r: r["max_dd"])
    by_wr = sorted(all_results, key=lambda r: r["win_rate"], reverse=True)

    def print_top(title, results, n):
        print(f"\n{'='*90}")
        print(f"  {title}")
        print(f"{'='*90}")
        print(f"  {'#':>3} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>8} {'WR%':>6} {'StopR':>6} {'Stops':>6} {'Placed':>7}  Config")
        print(f"  {'-'*87}")
        for i, r in enumerate(results[:n], 1):
            # Decode key params from label
            short = (
                f"pb=${r['p_put_stop_buffer']/100:.2f} "
                f"cb=${r['p_call_stop_buffer']/100:.2f} "
                f"dec={r['p_buffer_decay_start_mult']:.1f}x/{r['p_buffer_decay_hours']:.0f}h "
                f"sw={r['p_max_spread_width']} "
                f"pf=${r['p_put_credit_floor']:.2f} "
                f"mc=${r['p_min_call_credit']:.2f} "
                f"mp=${r['p_min_put_credit']:.2f} "
                f"vr={'N' if r['p_vix_regime_credit']=='remove' else 'Y'} "
                f"dd={r['p_base_entry_downday_callonly_pct']:.2f} "
                f"tp=${r['p_downday_theoretical_put_credit']/100:.2f}"
            )
            print(
                f"  {i:>3} {r['sharpe']:>7.3f} {r['total_pnl']:>10,.0f} "
                f"{r['max_dd']:>8,.0f} {r['win_rate']:>5.1f}% {r['stop_rate']:>5.1f}% "
                f"{r['stops']:>6} {r['placed']:>7}  {short}"
            )

    print_top(f"TOP {args.top} BY SHARPE", by_sharpe, args.top)
    print_top("TOP 10 BY TOTAL P&L", by_pnl, 10)
    print_top("TOP 10 BY LOWEST MAX DRAWDOWN", by_dd, 10)
    print_top("TOP 10 BY WIN RATE", by_wr, 10)

    # ── Best combo analysis ──────────────────────────────────────────────
    best = by_sharpe[0]
    print(f"\n{'='*70}")
    print(f"  BEST COMBINATION (by Sharpe)")
    print(f"{'='*70}")
    print(f"  Sharpe:    {best['sharpe']:.3f}")
    print(f"  P&L:       ${best['total_pnl']:,.0f}")
    print(f"  Max DD:    ${best['max_dd']:,.0f}")
    print(f"  Win Rate:  {best['win_rate']:.1f}%")
    print(f"  Stop Rate: {best['stop_rate']:.1f}%")
    print(f"  Stops:     {best['stops']}")
    print(f"  Placed:    {best['placed']}")
    print(f"\n  Parameters:")
    for k in keys:
        val = best[f"p_{k}"]
        print(f"    {k:<40} {val}")

    # ── Run best combo day-by-day ────────────────────────────────────────
    print(f"\n  Running day-by-day simulation of best combo...")
    best_combo = {k: best[f"p_{k}"] for k in keys}
    daily = _run_best_with_daily(best_combo)

    print(f"\n  Day-by-day results ({len(daily)} trading days):")
    print(f"  {'Date':<12} {'P&L':>8} {'Entries':>8} {'Stops':>6} {'Cum P&L':>10}")
    print(f"  {'-'*48}")
    cum = 0
    for d in daily:
        cum += d["net_pnl"]
        print(f"  {d['date']:<12} {d['net_pnl']:>+8,.0f} {d['entries_placed']:>8} {d['stops_hit']:>6} {cum:>+10,.0f}")

    # ── Save all results to CSV ──────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")

    # Full results CSV
    csv_path = out_dir / f"mega_sweep_{ts}.csv"
    csv_keys = ["label", "sharpe", "total_pnl", "max_dd", "calmar",
                "win_rate", "stop_rate", "stops", "placed", "days", "mean_daily"]
    csv_keys += [f"p_{k}" for k in keys]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(by_sharpe)
    print(f"\n  All results saved → {csv_path}")

    # Daily results CSV for best combo
    daily_csv = out_dir / f"mega_sweep_best_daily_{ts}.csv"
    with open(daily_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "net_pnl", "entries_placed", "stops_hit", "entries_skipped"])
        w.writeheader()
        w.writerows(daily)
    print(f"  Best combo daily → {daily_csv}")

    print(f"\n  Done! {total:,} combos tested in {elapsed/60:.0f} minutes.")
