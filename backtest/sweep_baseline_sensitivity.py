"""
Baseline Sensitivity Sweep (OPTIMIZED): Test put_stop_buffer impact on credit gates.

SCOPE: Tests put_stop_buffer variations with all 42 credit gate combinations.
- put_stop_buffer: [1.50, 1.75, 2.00, 2.25]
- Credit gates: all 42 combos (7 call × 6 put values)
- Total: 168 backtests (~10 minutes)

Answers: Does buffer width shift optimal credit gate values?
- If all param values converge on same credit gates → buffer width doesn't matter much
- If different buffers → different optimal gates → strong interaction exists

Run: python -u -m backtest.sweep_baseline_sensitivity --workers 10
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

# Baseline params to test (one at a time)
# OPTIMIZED: Test only put_stop_buffer to fit in ~10 minutes (168 backtests)
BASELINE_PARAMS = {
    "put_stop_buffer": [150.0, 175.0, 200.0, 225.0],  # $1.50, $1.75, $2.00, $2.25
}
# To test call_stop_buffer or buffer_decay_start_mult separately, uncomment below:
# "call_stop_buffer": [50.0, 75.0, 100.0, 125.0],   # $0.50, $0.75, $1.00, $1.25
# "buffer_decay_start_mult": [2.0, 2.5, 3.0, 3.5],

OUT_DIR = Path("backtest/results")
RESULTS_CSV = OUT_DIR / "baseline_sensitivity_results.csv"
PROGRESS_FILE = OUT_DIR / "baseline_sensitivity_progress.txt"

CSV_FIELDS = [
    "param_name", "param_value", "combo_id",
    "sharpe", "total_pnl", "max_dd", "calmar",
    "win_rate", "stop_rate", "stops", "days",
    "min_call_credit", "min_put_credit",
    "elapsed_sec", "timestamp",
]

CURRENT_BEST = 0.925  # From credit_gates sweep
CURRENT_BEST_CONFIG = "call=$2.00, put=$2.75"


def _format_param_value(name: str, value: float) -> str:
    """Format param value for display."""
    if name == "buffer_decay_start_mult":
        return f"{value:.1f}×"
    elif name in ["put_stop_buffer", "call_stop_buffer"]:
        return f"${value/100:.2f}"
    return f"{value:.2f}"


def _build_cfg(baseline: dict, combo: dict) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = FULL_START
    cfg.end_date = FULL_END
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.stop_slippage_per_leg = SLIPPAGE
    cfg.stop_spread_markup_pct = MARKUP

    # Apply baseline (with override for current param)
    for k, v in baseline.items():
        setattr(cfg, k, v)

    # Apply combo (credit gates)
    for k, v in combo.items():
        setattr(cfg, k, v)

    return cfg


def _worker(args):
    """Run single backtest. Returns dict or None on error."""
    param_name, param_value, combo_id, combo, baseline = args
    t0 = time.time()

    try:
        cfg = _build_cfg(baseline, combo)
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
            "param_name": param_name,
            "param_value": param_value,
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


def _write_progress(overall_pct, param_name, param_val, param_pct, completed_overall, total_overall, best_overall):
    """Write progress file."""
    bar_width = 40
    overall_bar = "█" * int(bar_width * overall_pct) + "░" * (bar_width - int(bar_width * overall_pct))
    param_bar = "█" * int(bar_width * param_pct) + "░" * (bar_width - int(bar_width * param_pct))

    lines = [
        f"BASELINE SENSITIVITY SWEEP",
        f"",
        f"  Overall [{overall_bar}] {overall_pct*100:.1f}%",
        f"  Completed: {completed_overall} / {total_overall}",
        f"",
        f"  Current param: {param_name} = {_format_param_value(param_name, param_val)}",
        f"  [{param_bar}] {param_pct*100:.1f}%",
        f"",
        f"  GLOBAL BEST FOUND:",
        f"    {best_overall['status']}",
        f"    call={best_overall['call']:.2f}, put={best_overall['put']:.2f}",
        f"    Sharpe={best_overall['sharpe']:.3f} (vs current 0.925, Δ{best_overall['delta']:+.3f})",
        f"    P&L ${best_overall['pnl']:+,.0f}",
    ]

    with open(PROGRESS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--param", type=str, default=None,
                       help="Test specific param (buffer_decay_start_mult, put_stop_buffer, call_stop_buffer)")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate combos
    combos = list(product(CREDIT_GRID["min_call_credit"], CREDIT_GRID["min_put_credit"]))
    combos = [{"min_call_credit": c[0], "min_put_credit": c[1]} for c in combos]
    combo_count = len(combos)

    # Determine which params to test
    params_to_test = BASELINE_PARAMS if not args.param else {args.param: BASELINE_PARAMS[args.param]}
    total_param_values = sum(len(v) for v in params_to_test.values())
    total_backtests = total_param_values * combo_count

    # Write CSV header
    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()

    print(f"\n{'='*90}")
    print(f"📊 BASELINE SENSITIVITY SWEEP")
    print(f"{'='*90}")
    print(f"Params to test: {len(params_to_test)}")
    print(f"Param values: {total_param_values} total")
    print(f"Credit gate combos: {combo_count}")
    print(f"Total backtests: {total_backtests}")
    print(f"Workers: {args.workers}")
    print(f"Period: {FULL_START} to {FULL_END} (945 trading days)")
    print(f"Current best: Sharpe 0.925 ({CURRENT_BEST_CONFIG})")
    print(f"{'='*90}\n")

    global_best_sharpe = -999
    global_best_result = None
    completed_overall = 0
    t_overall_start = time.time()

    # Test each param
    for param_idx, (param_name, param_values) in enumerate(params_to_test.items()):
        print(f"\n{'='*90}")
        print(f"[{param_idx+1}/{len(params_to_test)}] TESTING: {param_name}")
        print(f"{'='*90}\n")

        param_best_sharpe = -999
        param_best_result = None

        for val_idx, param_value in enumerate(param_values):
            # Build baseline with this param overridden
            baseline = BASE_BASELINE.copy()
            baseline[param_name] = param_value

            # Create worker args for all combos
            worker_args = [
                (param_name, param_value, i+1, combo, baseline)
                for i, combo in enumerate(combos)
            ]

            combo_results = []
            completed_combo = 0
            t_start = time.time()

            param_str = _format_param_value(param_name, param_value)
            print(f"[{val_idx+1}/{len(param_values)}] {param_name}={param_str}")
            print(f"Running {combo_count} credit gate combos with {args.workers} workers...")
            print()

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
                                "param_name": result["param_name"],
                                "param_value": result["param_value"],
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
                        if result["sharpe"] > param_best_sharpe:
                            param_best_sharpe = result["sharpe"]
                            param_best_result = result

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
                        param_pct = completed_combo / combo_count
                        best_info = {
                            "status": "✓ FOUND" if (global_best_sharpe > CURRENT_BEST + 0.01) else ("~ TIED" if abs(global_best_sharpe - CURRENT_BEST) < 0.01 else "✗ WORSE"),
                            "call": global_best_result["min_call_credit"] if global_best_result else 0,
                            "put": global_best_result["min_put_credit"] if global_best_result else 0,
                            "sharpe": global_best_sharpe,
                            "delta": global_best_sharpe - CURRENT_BEST,
                            "pnl": global_best_result["total_pnl"] if global_best_result else 0,
                        }
                        _write_progress(overall_pct, param_name, param_value, param_pct, completed_overall, total_backtests, best_info)

                        # Console output every 5 combos
                        if completed_combo % 5 == 0 or completed_combo == combo_count:
                            print(f"  [{bar}] {pct*100:.1f}% | "
                                  f"Best: call=${result['min_call_credit']:.2f} put=${result['min_put_credit']:.2f} | "
                                  f"Sharpe {result['sharpe']:.3f} | ETA {remaining/60:.1f}min")
                            sys.stdout.flush()
                    else:
                        completed_combo += 1
                        completed_overall += 1

            # Summary for this param value
            if param_best_result:
                diff = param_best_sharpe - CURRENT_BEST
                if diff > 0.01:
                    status = "🟢 BETTER"
                elif diff < -0.01:
                    status = "🔴 WORSE"
                else:
                    status = "🟡 SIMILAR"

                print(f"\n  {status} Best for {param_name}={param_str}:")
                print(f"        call=${param_best_result['min_call_credit']:.2f}, put=${param_best_result['min_put_credit']:.2f}")
                print(f"        Sharpe {param_best_sharpe:.3f} (Δ{diff:+.3f})")
                print(f"        P&L ${param_best_result['total_pnl']:+,.0f}\n")

    # Final summary
    print(f"\n{'='*90}")
    print(f"✅ SENSITIVITY SWEEP COMPLETE")
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
        print(f"  call=${global_best_result['min_call_credit']:.2f}, put=${global_best_result['min_put_credit']:.2f}")
        print(f"  via {global_best_result['param_name']}={_format_param_value(global_best_result['param_name'], global_best_result['param_value'])}")
        print(f"  Sharpe {global_best_sharpe:.3f} (vs current 0.925, Δ{diff:+.3f})")
        print(f"  P&L ${global_best_result['total_pnl']:+,.0f}")
        print(f"\nCurrent best (unchanged):")
        print(f"  call=$2.00, put=$2.75")
        print(f"  Sharpe 0.925")
        print(f"  P&L $+41,274")
    else:
        print("ERROR: No valid results")

    print(f"\n{'='*90}")
    print(f"Results: {RESULTS_CSV}")
    print(f"Progress: {PROGRESS_FILE}")
    print(f"{'='*90}\n")
