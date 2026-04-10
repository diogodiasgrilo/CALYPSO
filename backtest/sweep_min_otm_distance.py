"""
Min OTM Distance Sensitivity Sweep: Test if raising minimum OTM distance improves Sharpe.

SCOPE: Tests min_otm_distance variations with all 42 credit gate combinations.
- min_otm_distance: [25, 35, 50, 75] points
- Credit gates: all 42 combos (7 call × 6 put values)
- Total: 168 backtests (~10 minutes)

Answers: Does minimum OTM distance affect optimal credit gates or overall Sharpe?
- Current: min_otm_distance=25pt achieves Sharpe 0.972
- Question: Can wider OTM distances (35, 50, 75pt) exceed 0.972 or are they suboptimal?

Run: python -u -m backtest.sweep_min_otm_distance --workers 10
"""
import argparse
import csv
import multiprocessing as mp
import sys
import time
from datetime import date, datetime as dt
from itertools import product
from pathlib import Path

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest

FULL_START = date(2022, 5, 16)
FULL_END = date(2026, 4, 8)
SLIPPAGE = 30.0
MARKUP = 0.10

# LOCKED baseline (from optimized combo sweep)
BASE_BASELINE = {
    "put_stop_buffer": 175.0,  # $1.75
    "call_stop_buffer": 75.0,  # $0.75
    "buffer_decay_start_mult": 2.5,
    "buffer_decay_hours": 4.0,
    "put_credit_floor": 2.75,
    "max_spread_width": 110,
    "downday_theoretical_put_credit": 260.0,
    "vix_regime_min_call_credit": [None, None, None, None],
    "vix_regime_min_put_credit": [None, None, None, None],
    "vix_regime_put_stop_buffer": [None, None, None, None],
}

# Credit gate grid (same as credit_gates sweep)
CREDIT_GRID = {
    "min_call_credit": [1.00, 1.35, 1.50, 1.75, 2.00, 2.25, 2.50],
    "min_put_credit": [2.00, 2.25, 2.50, 2.75, 3.00, 3.25],
}

# Min OTM distance values to test
MIN_OTM_DISTANCES = [25, 35, 50, 75]

OUT_DIR = Path("backtest/results")
RESULTS_CSV = OUT_DIR / "min_otm_distance_results.csv"
PROGRESS_FILE = OUT_DIR / "min_otm_distance_progress.txt"

CSV_FIELDS = [
    "min_otm_distance", "combo_id",
    "sharpe", "total_pnl", "max_dd", "calmar",
    "win_rate", "stop_rate", "stops", "days",
    "min_call_credit", "min_put_credit",
    "elapsed_sec", "timestamp",
]

CURRENT_BEST = 0.925  # From credit_gates sweep
CURRENT_BEST_CONFIG = "call=$2.00, put=$2.75, min_otm_distance=25pt"


def _build_cfg(baseline: dict, combo: dict, min_otm_dist: int) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = SLIPPAGE
    cfg.stop_spread_markup_pct = MARKUP

    # Apply baseline
    for k, v in baseline.items():
        setattr(cfg, k, v)

    # Apply min_otm_distance override
    cfg.min_otm_distance = min_otm_dist

    # Apply combo (credit gates)
    for k, v in combo.items():
        setattr(cfg, k, v)

    return cfg


def _worker(args):
    """Run single backtest. Returns dict or None on error."""
    min_otm_dist, combo_id, combo = args
    t0 = time.time()

    try:
        cfg = _build_cfg(BASE_BASELINE, combo, min_otm_dist)
        results = run_backtest(cfg, verbose=False)
        elapsed = time.time() - t0

        if not results:
            return None

        import pandas as pd
        import math

        daily_net = [r.net_pnl for r in results]
        total_net = sum(daily_net)
        winning_days = sum(1 for x in daily_net if x > 0)
        win_rate = winning_days / len(daily_net) if daily_net else 0

        all_entries = [e for r in results for e in r.entries]
        placed = [e for e in all_entries if e.entry_type != "skipped"]
        stops = sum(1 for e in placed if e.call_outcome == "stopped" or e.put_outcome == "stopped")
        stop_rate = stops / len(placed) if placed else 0

        if len(daily_net) > 1:
            arr = pd.Series(daily_net)
            sharpe = arr.mean() / arr.std() * math.sqrt(252) if arr.std() > 0 else 0
        else:
            sharpe = 0

        cumulative = pd.Series(daily_net).cumsum()
        rolling_max = cumulative.cummax()
        drawdown = cumulative - rolling_max
        max_dd = float(drawdown.min())
        calmar = sharpe * (arr.std() / abs(max_dd)) if max_dd < 0 else 0

        return {
            "min_otm_distance": min_otm_dist,
            "combo_id": combo_id,
            "sharpe": sharpe,
            "total_pnl": total_net,
            "max_dd": max_dd,
            "calmar": calmar,
            "win_rate": win_rate,
            "num_stops": stops,
            "stop_rate": stop_rate,
            "days": len(results),
            "min_call_credit": combo["min_call_credit"],
            "min_put_credit": combo["min_put_credit"],
            "elapsed": elapsed,
        }
    except Exception as e:
        return None


def _write_progress(overall_pct, otm_dist, otm_pct, completed_overall, total_overall, best_overall):
    """Write progress file."""
    bar_width = 40
    overall_bar = "█" * int(bar_width * overall_pct) + "░" * (bar_width - int(bar_width * overall_pct))
    otm_bar = "█" * int(bar_width * otm_pct) + "░" * (bar_width - int(bar_width * otm_pct))

    lines = [
        f"MIN OTM DISTANCE SENSITIVITY SWEEP",
        f"",
        f"  Overall [{overall_bar}] {overall_pct*100:.1f}%",
        f"  Completed: {completed_overall} / {total_overall}",
        f"",
        f"  Current param: min_otm_distance = {otm_dist}pt",
        f"  [{otm_bar}] {otm_pct*100:.1f}%",
        f"",
        f"  GLOBAL BEST FOUND:",
        f"    {best_overall['status']}",
        f"    min_otm_distance={best_overall['otm_dist']}pt, call={best_overall['call']:.2f}, put={best_overall['put']:.2f}",
        f"    Sharpe={best_overall['sharpe']:.3f} (vs current 0.925, Δ{best_overall['delta']:+.3f})",
        f"    P&L ${best_overall['pnl']:+,.0f}",
    ]

    with open(PROGRESS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate combos
    combos = list(product(CREDIT_GRID["min_call_credit"], CREDIT_GRID["min_put_credit"]))
    combos = [{"min_call_credit": c[0], "min_put_credit": c[1]} for c in combos]
    combo_count = len(combos)

    total_backtests = len(MIN_OTM_DISTANCES) * combo_count

    # Write CSV header
    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()

    print(f"\n{'='*90}")
    print(f"📊 MIN OTM DISTANCE SENSITIVITY SWEEP")
    print(f"{'='*90}")
    print(f"Min OTM distances: {MIN_OTM_DISTANCES}")
    print(f"Credit gate combos: {combo_count}")
    print(f"Total backtests: {total_backtests}")
    print(f"Workers: {args.workers}")
    print(f"Period: {FULL_START} to {FULL_END} (945 trading days)")
    print(f"Slippage: ${SLIPPAGE/100:.2f}/leg, Markup: {MARKUP*100:.0f}%")
    print(f"Baseline (locked):")
    print(f"  put_stop_buffer: ${BASE_BASELINE['put_stop_buffer']/100:.2f}")
    print(f"  call_stop_buffer: ${BASE_BASELINE['call_stop_buffer']/100:.2f}")
    print(f"  buffer_decay: {BASE_BASELINE['buffer_decay_start_mult']}× → 1× over {BASE_BASELINE['buffer_decay_hours']:.1f}h")
    print(f"  put_credit_floor: ${BASE_BASELINE['put_credit_floor']:.2f}")
    print(f"  max_spread_width: {BASE_BASELINE['max_spread_width']}pt")
    print(f"Current best: Sharpe 0.925 ({CURRENT_BEST_CONFIG})")
    print(f"{'='*90}\n")

    global_best_sharpe = -999
    global_best_result = None
    completed_overall = 0
    t_overall_start = time.time()

    # Test each min_otm_distance
    for otm_idx, min_otm_dist in enumerate(MIN_OTM_DISTANCES):
        print(f"\n{'='*90}")
        print(f"[{otm_idx+1}/{len(MIN_OTM_DISTANCES)}] MIN_OTM_DISTANCE = {min_otm_dist}pt")
        print(f"{'='*90}\n")

        otm_best_sharpe = -999
        otm_best_result = None

        # Create worker args for all combos
        worker_args = [
            (min_otm_dist, i+1, combo)
            for i, combo in enumerate(combos)
        ]

        combo_results = []
        completed_combo = 0
        t_start = time.time()

        print(f"Running {combo_count} credit gate combos with {args.workers} workers...\n")

        with mp.Pool(args.workers) as pool:
            for result in pool.imap_unordered(_worker, worker_args, chunksize=1):
                if result:
                    completed_combo += 1
                    completed_overall += 1
                    combo_results.append(result)

                    # Write to CSV immediately
                    with open(RESULTS_CSV, "a", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                        writer.writerow({
                            "min_otm_distance": result["min_otm_distance"],
                            "combo_id": result["combo_id"],
                            "sharpe": f'{result["sharpe"]:.3f}',
                            "total_pnl": f'{result["total_pnl"]:.0f}',
                            "max_dd": f'{result["max_dd"]:.0f}',
                            "calmar": f'{result["calmar"]:.3f}',
                            "win_rate": f'{result["win_rate"]:.1%}',
                            "stop_rate": f'{result["stop_rate"]:.1%}',
                            "stops": result["num_stops"],
                            "days": result["days"],
                            "min_call_credit": f'{result["min_call_credit"]:.2f}',
                            "min_put_credit": f'{result["min_put_credit"]:.2f}',
                            "elapsed_sec": f'{result["elapsed"]:.1f}',
                            "timestamp": dt.now().isoformat(),
                        })

                    # Track bests
                    if result["sharpe"] > otm_best_sharpe:
                        otm_best_sharpe = result["sharpe"]
                        otm_best_result = result

                    if result["sharpe"] > global_best_sharpe:
                        global_best_sharpe = result["sharpe"]
                        global_best_result = result

                    # Progress bar
                    elapsed = time.time() - t_start
                    rate = completed_combo / elapsed if elapsed > 0 else 0
                    remaining = (combo_count - completed_combo) / rate if rate > 0 else 0
                    pct = completed_combo / combo_count

                    bar_width = 35
                    bar = "█" * int(bar_width * pct) + "░" * (bar_width - int(bar_width * pct))

                    # Write progress file
                    overall_pct = completed_overall / total_backtests
                    otm_pct = completed_combo / combo_count
                    best_info = {
                        "status": "✓ FOUND" if (global_best_sharpe > CURRENT_BEST + 0.01) else ("~ TIED" if abs(global_best_sharpe - CURRENT_BEST) < 0.01 else "✗ WORSE"),
                        "otm_dist": global_best_result["min_otm_distance"] if global_best_result else 0,
                        "call": global_best_result["min_call_credit"] if global_best_result else 0,
                        "put": global_best_result["min_put_credit"] if global_best_result else 0,
                        "sharpe": global_best_sharpe,
                        "delta": global_best_sharpe - CURRENT_BEST,
                        "pnl": global_best_result["total_pnl"] if global_best_result else 0,
                    }
                    _write_progress(overall_pct, min_otm_dist, otm_pct, completed_overall, total_backtests, best_info)

                    # Console output every 5 combos
                    if completed_combo % 5 == 0 or completed_combo == combo_count:
                        print(f"  [{bar}] {pct*100:.1f}% | "
                              f"Best: call=${result['min_call_credit']:.2f} put=${result['min_put_credit']:.2f} | "
                              f"Sharpe {result['sharpe']:.3f} | ETA {remaining/60:.1f}min")
                        sys.stdout.flush()
                else:
                    completed_combo += 1
                    completed_overall += 1

        # Summary for this min_otm_distance
        if otm_best_result:
            diff = otm_best_sharpe - CURRENT_BEST
            if diff > 0.01:
                status = "🟢 BETTER"
            elif diff < -0.01:
                status = "🔴 WORSE"
            else:
                status = "🟡 SIMILAR"

            print(f"\n  {status} Best for min_otm_distance={min_otm_dist}pt:")
            print(f"        call=${otm_best_result['min_call_credit']:.2f}, put=${otm_best_result['min_put_credit']:.2f}")
            print(f"        Sharpe {otm_best_sharpe:.3f} (Δ{diff:+.3f})")
            print(f"        P&L ${otm_best_result['total_pnl']:+,.0f}\n")

    # Final summary
    print(f"\n{'='*90}")
    print(f"✅ MIN OTM DISTANCE SWEEP COMPLETE")
    print(f"{'='*90}\n")

    print(f"Total backtests: {completed_overall} / {total_backtests}")
    print(f"Total time: {(time.time() - t_overall_start)/60:.1f} minutes\n")

    if global_best_result:
        diff = global_best_sharpe - CURRENT_BEST
        if diff > 0.05:
            verdict = "🟢 FOUND BETTER CONFIG"
        elif diff > 0.01:
            verdict = "🟡 MARGINAL IMPROVEMENT"
        elif diff > -0.01:
            verdict = "🟡 ESSENTIALLY TIED"
        else:
            verdict = "🔴 NO IMPROVEMENT"

        print(f"{verdict}")
        print(f"\nGlobal best found:")
        print(f"  min_otm_distance={global_best_result['min_otm_distance']}pt")
        print(f"  call=${global_best_result['min_call_credit']:.2f}, put=${global_best_result['min_put_credit']:.2f}")
        print(f"  Sharpe {global_best_sharpe:.3f} (vs current 0.925, Δ{diff:+.3f})")
        print(f"  P&L ${global_best_result['total_pnl']:+,.0f}")
        print(f"\nCurrent best (unchanged):")
        print(f"  min_otm_distance=25pt, call=$2.00, put=$2.75")
        print(f"  Sharpe 0.925")
        print(f"  P&L $+41,274")
    else:
        print("ERROR: No valid results")

    print(f"\n{'='*90}")
    print(f"Results: {RESULTS_CSV}")
    print(f"Progress: {PROGRESS_FILE}")
    print(f"{'='*90}\n")
