"""
Call Stop Buffer Sweep (NEW COMBO): Re-test with improved credit gate combo.

SCOPE: Tests call_stop_buffer variations with the NEW credit combo (call=$2.00, put=$2.75).
- call_stop_buffer: [10.0, 20.0, 30.0, 35.0, 40.0, 50.0, 75.0] ($0.10 to $0.75)
- Credit gates: FIXED at min_call_credit=2.00, min_put_credit=2.75 (showed +0.047 Sharpe improvement)
- Total: 7 backtests (~5 minutes)

Background: The put_stop_buffer sweep found that (call=$2.00, put=$2.75) achieves Sharpe 0.972
vs baseline 0.925. This sweep tests if call_stop_buffer values also improve with the new combo.

Run: python -u -m backtest.sweep_call_stop_buffer_new_combo --workers 2
"""
import argparse
import csv
import multiprocessing as mp
import sys
import time
from datetime import date, datetime as dt
from pathlib import Path

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest

FULL_START = date(2022, 5, 16)
FULL_END = date(2026, 4, 8)
SLIPPAGE = 30.0
MARKUP = 0.10

# Baseline with NEW improved credit combo
BASE_BASELINE = {
    "put_stop_buffer": 175.0,
    "call_stop_buffer": 35.0,  # Will be overridden by sweep
    "buffer_decay_start_mult": 2.5,
    "buffer_decay_hours": 4.0,
    "put_credit_floor": 2.75,
    "max_spread_width": 110,
    "downday_theoretical_put_credit": 260.0,
    "vix_regime_min_call_credit": [None, None, None, None],
    "vix_regime_min_put_credit": [None, None, None, None],
    "vix_regime_put_stop_buffer": [None, None, None, None],
    # NEW CREDIT COMBO
    "min_call_credit": 2.00,
    "min_put_credit": 2.75,
}

# Call stop buffer values to test ($)
CALL_STOP_BUFFERS = [10.0, 20.0, 30.0, 35.0, 40.0, 50.0, 75.0]

OUT_DIR = Path("backtest/results")
RESULTS_CSV = OUT_DIR / "call_stop_buffer_new_combo_results.csv"
PROGRESS_FILE = OUT_DIR / "call_stop_buffer_new_combo_progress.txt"

CSV_FIELDS = [
    "call_stop_buffer",
    "sharpe", "total_pnl", "max_dd", "calmar",
    "win_rate", "stop_rate", "stops", "days",
    "elapsed_sec", "timestamp",
]

CURRENT_BEST = 0.925


def _build_cfg(baseline: dict, call_stop_buffer: float) -> BacktestConfig:
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

    # Override with sweep parameter
    cfg.call_stop_buffer = call_stop_buffer

    return cfg


def _worker(args):
    """Run single backtest. Returns dict or None on error."""
    idx, call_stop_buffer = args
    t0 = time.time()

    try:
        cfg = _build_cfg(BASE_BASELINE, call_stop_buffer)
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
            "call_stop_buffer": call_stop_buffer,
            "sharpe": sharpe,
            "total_pnl": total_net,
            "max_dd": max_dd,
            "calmar": calmar,
            "win_rate": win_rate,
            "num_stops": stops,
            "stop_rate": stop_rate,
            "days": len(results),
            "elapsed": elapsed,
        }
    except Exception as e:
        return None


def _write_progress(pct, completed, total, best_result):
    """Write progress file."""
    bar_width = 40
    bar = "█" * int(bar_width * pct) + "░" * (bar_width - int(bar_width * pct))

    lines = [
        f"CALL STOP BUFFER SWEEP (NEW COMBO: call=$2.00, put=$2.75)",
        f"",
        f"  [{bar}] {pct*100:.1f}%",
        f"  Completed: {completed} / {total}",
        f"",
    ]

    if best_result:
        delta = best_result["sharpe"] - CURRENT_BEST
        status = "✓ BETTER" if delta > 0.01 else ("=" if abs(delta) <= 0.01 else "✗ WORSE")
        lines.extend([
            f"  BEST FOUND: {status}",
            f"    call_buffer=${best_result['call_stop_buffer']/100:.2f}, Sharpe={best_result['sharpe']:.3f} (Δ{delta:+.3f})",
            f"    P&L ${best_result['total_pnl']:+,.0f} | MaxDD ${best_result['max_dd']:.0f} | Win {best_result['win_rate']*100:.1f}%",
        ])

    with open(PROGRESS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total_backtests = len(CALL_STOP_BUFFERS)

    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()

    print(f"\n{'='*90}")
    print(f"📊 CALL STOP BUFFER SWEEP (NEW COMBO: call=$2.00, put=$2.75)")
    print(f"{'='*90}")
    print(f"Call stop buffers: {CALL_STOP_BUFFERS}")
    print(f"Total backtests: {total_backtests}")
    print(f"Workers: {args.workers}")
    print(f"Period: {FULL_START} to {FULL_END}")
    print(f"Baseline (locked): put_buffer=${BASE_BASELINE['put_stop_buffer']/100:.2f}, decay_mult={BASE_BASELINE['buffer_decay_start_mult']:.1f}×, decay_hours={BASE_BASELINE['buffer_decay_hours']:.1f}h")
    print(f"Current best: Sharpe {CURRENT_BEST}")
    print(f"{'='*90}\n")

    global_best_sharpe = -999
    global_best_result = None
    completed = 0
    t_overall_start = time.time()

    worker_args = list(enumerate(CALL_STOP_BUFFERS))

    print(f"Running {total_backtests} backtests with {args.workers} workers...\n")

    with mp.Pool(args.workers) as pool:
        for result in pool.imap_unordered(_worker, worker_args, chunksize=1):
            if result:
                completed += 1

                with open(RESULTS_CSV, "a", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                    writer.writerow({
                        "call_stop_buffer": f'{result["call_stop_buffer"]:.1f}',
                        "sharpe": f'{result["sharpe"]:.3f}',
                        "total_pnl": f'{result["total_pnl"]:.0f}',
                        "max_dd": f'{result["max_dd"]:.0f}',
                        "calmar": f'{result["calmar"]:.3f}',
                        "win_rate": f'{result["win_rate"]:.1%}',
                        "stop_rate": f'{result["stop_rate"]:.1%}',
                        "stops": result["num_stops"],
                        "days": result["days"],
                        "elapsed_sec": f'{result["elapsed"]:.1f}',
                        "timestamp": dt.now().isoformat(),
                    })

                if result["sharpe"] > global_best_sharpe:
                    global_best_sharpe = result["sharpe"]
                    global_best_result = result

                elapsed = time.time() - t_overall_start
                rate = completed / elapsed if elapsed > 0 else 0
                remaining = (total_backtests - completed) / rate if rate > 0 else 0
                pct = completed / total_backtests

                _write_progress(pct, completed, total_backtests, global_best_result)

                bar_width = 35
                bar = "█" * int(bar_width * pct) + "░" * (bar_width - int(bar_width * pct))
                print(f"  [{bar}] {pct*100:.1f}% | call_buffer=${result['call_stop_buffer']/100:.2f} Sharpe {result['sharpe']:.3f} | ETA {remaining/60:.1f}min")
                sys.stdout.flush()

    print(f"\n{'='*90}")
    print(f"✅ CALL STOP BUFFER SWEEP COMPLETE")
    print(f"Total: {completed}/{total_backtests}, Time: {(time.time()-t_overall_start)/60:.1f}min")
    if global_best_result:
        diff = global_best_sharpe - CURRENT_BEST
        verdict = "🟢 FOUND BETTER" if diff > 0.05 else ("🟡 MARGINAL" if diff > 0.01 else ("🟡 TIED" if diff > -0.01 else "🔴 WORSE"))
        print(f"{verdict}: Sharpe {global_best_sharpe:.3f} (call_buffer=${global_best_result['call_stop_buffer']/100:.2f})")
    print(f"Results: {RESULTS_CSV}")
    print(f"{'='*90}\n")
