"""
Credit Gate Sweep: Optimize min_call_credit and min_put_credit with new baseline

New baseline parameters (from combo sweep optimal):
- put_stop_buffer: $1.75
- call_stop_buffer: $0.75
- buffer_decay_start_mult: 2.5
- buffer_decay_hours: 4.0
- put_credit_floor: $2.75
- max_spread_width: 110
- downday_theoretical_put_credit: $2.60

Testing: min_call_credit × min_put_credit (7 × 6 = 42 combos)

Run: python -u -m backtest.sweep_credit_gates --workers 8
"""
import argparse
import csv
import multiprocessing as mp
import statistics
import sys
import time
import traceback
from datetime import date, datetime as dt
from itertools import product
from pathlib import Path

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

FULL_START = date(2022, 5, 16)
FULL_END = date(2026, 4, 8)
SLIPPAGE = 30.0
MARKUP = 0.10

# New baseline (from combo sweep optimal)
BASELINE = {
    "put_stop_buffer": 175.0,  # $1.75
    "call_stop_buffer": 75.0,  # $0.75
    "buffer_decay_start_mult": 2.5,
    "buffer_decay_hours": 4.0,
    "put_credit_floor": 2.75,
    "max_spread_width": 110,
    "downday_theoretical_put_credit": 260.0,  # $2.60
}

# Grid: sweep credit gates
GRID = {
    "min_call_credit": [1.00, 1.35, 1.50, 1.75, 2.00, 2.25, 2.50],
    "min_put_credit": [2.00, 2.25, 2.50, 2.75, 3.00, 3.25],
}

OUT_DIR = Path("backtest/results")
PROGRESS_FILE = OUT_DIR / "credit_gates_progress.txt"
RESULTS_CSV = OUT_DIR / "credit_gates_results.csv"

CSV_FIELDS = [
    "rank", "combo_id", "sharpe", "total_pnl", "max_dd", "calmar",
    "win_rate", "stop_rate", "stops", "days",
    "min_call_credit", "min_put_credit",
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

    # Apply baseline
    for k, v in BASELINE.items():
        setattr(cfg, k, v)

    # Apply combo
    for k, v in combo.items():
        setattr(cfg, k, v)

    return cfg


def _worker(args):
    """Run single backtest. Returns dict or None on error."""
    combo_id, combo = args
    t0 = time.time()

    try:
        cfg = _build_cfg(combo)
        results = run_backtest(cfg, verbose=False)
        elapsed = time.time() - t0

        if not results:
            return None

        # Compute stats
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
        sys.stderr.write(f"ERROR [combo {combo_id}]: {str(e)[:100]}\n")
        sys.stderr.flush()
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate combos
    combos = list(product(GRID["min_call_credit"], GRID["min_put_credit"]))
    combos = [{"min_call_credit": c[0], "min_put_credit": c[1]} for c in combos]

    total = len(combos)
    combo_with_id = [(i+1, c) for i, c in enumerate(combos)]

    print(f"\n{'='*70}")
    print(f"📊 CREDIT GATE SWEEP")
    print(f"{'='*70}")
    print(f"Combos: {len(GRID['min_call_credit'])} call × {len(GRID['min_put_credit'])} put = {total} total")
    print(f"Workers: {args.workers}")
    print(f"Period: {FULL_START} to {FULL_END} ({945} trading days)")
    print(f"Slippage: ${SLIPPAGE/100:.2f}/leg, Markup: {MARKUP*100:.0f}%")
    print(f"\nBaseline (locked):")
    print(f"  put_stop_buffer: $1.75")
    print(f"  call_stop_buffer: $0.75")
    print(f"  buffer_decay: 2.5× → 1× over 4.0h")
    print(f"  put_credit_floor: $2.75")
    print(f"  max_spread_width: 110pt")
    print(f"{'='*70}\n")

    # Write CSV header
    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()

    # Run sweep
    t_start = time.time()
    results_list = []
    completed = 0

    if args.workers > 1:
        print(f"Starting {args.workers} workers...\n")
        with mp.Pool(args.workers) as pool:
            for result in pool.imap_unordered(_worker, combo_with_id, chunksize=1):
                if result:
                    completed += 1
                    results_list.append(result)

                    # Write to CSV immediately (unbuffered)
                    with open(RESULTS_CSV, "a", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                        writer.writerow({
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
                            "rank": "TBD",
                        })

                    # Progress with ETA
                    elapsed_total = time.time() - t_start
                    rate = completed / elapsed_total if elapsed_total > 0 else 0
                    remaining = (total - completed) / rate if rate > 0 else 0
                    eta_min = remaining / 60

                    # Print progress (flush immediately with unbuffered output)
                    print(f"[{completed:2d}/{total}] call=${result['min_call_credit']:.2f} "
                          f"put=${result['min_put_credit']:.2f} | "
                          f"Sharpe {result['sharpe']:.3f} | "
                          f"P&L ${result['total_pnl']:+.0f} | "
                          f"ETA {eta_min:.1f}min")
                    sys.stdout.flush()
                else:
                    # Combo failed
                    completed += 1
                    print(f"[{completed:2d}/{total}] ❌ FAILED")
                    sys.stdout.flush()
    else:
        # Single worker (easier to debug)
        for combo_id, combo in combo_with_id:
            result = _worker((combo_id, combo))
            completed += 1

            if result:
                results_list.append(result)
                print(f"[{completed:2d}/{total}] call=${result['min_call_credit']:.2f} "
                      f"put=${result['min_put_credit']:.2f} | "
                      f"Sharpe {result['sharpe']:.3f} | "
                      f"P&L ${result['total_pnl']:+.0f}")
                sys.stdout.flush()
            else:
                print(f"[{completed:2d}/{total}] ❌ FAILED")
                sys.stdout.flush()

    # Sort by Sharpe
    results_list.sort(key=lambda x: x["sharpe"], reverse=True)

    # Display results
    print(f"\n{'='*70}")
    print(f"TOP 10 RESULTS (by Sharpe)")
    print(f"{'='*70}")
    print(f"{'#':<3} {'Call':<8} {'Put':<8} {'Sharpe':<10} {'P&L':<12} {'MaxDD':<12} {'Win%':<8}")
    print("-" * 70)

    for i, r in enumerate(results_list[:10], 1):
        print(f"{i:<3} ${r['min_call_credit']:<7.2f} ${r['min_put_credit']:<7.2f} "
              f"{r['sharpe']:<10.3f} ${r['total_pnl']:<11.0f} ${r['max_dd']:<11.0f} {r['win_rate']:<7.1%}")

    # Current vs Optimal
    print(f"\n{'='*70}")
    print(f"CURRENT CONFIG vs OPTIMAL")
    print(f"{'='*70}")

    current_call = 2.00
    current_put = 2.75

    current_result = next((r for r in results_list
                          if abs(r['min_call_credit'] - current_call) < 0.01
                          and abs(r['min_put_credit'] - current_put) < 0.01), None)
    optimal = results_list[0] if results_list else None

    if current_result and optimal:
        sharpe_diff = optimal['sharpe'] - current_result['sharpe']
        pnl_diff = optimal['total_pnl'] - current_result['total_pnl']

        print(f"\n📍 Current:  call=${current_call:.2f}, put=${current_put:.2f}")
        print(f"   Sharpe: {current_result['sharpe']:.3f}")
        print(f"   P&L:    ${current_result['total_pnl']:+.0f}")
        print(f"   MaxDD:  ${current_result['max_dd']:.0f}")
        print(f"   WinRate: {current_result['win_rate']:.1%}")

        print(f"\n🎯 Optimal:  call=${optimal['min_call_credit']:.2f}, put=${optimal['min_put_credit']:.2f}")
        print(f"   Sharpe: {optimal['sharpe']:.3f}")
        print(f"   P&L:    ${optimal['total_pnl']:+.0f}")
        print(f"   MaxDD:  ${optimal['max_dd']:.0f}")
        print(f"   WinRate: {optimal['win_rate']:.1%}")

        print(f"\n📊 Improvement:")
        print(f"   ΔSharpe: {sharpe_diff:+.3f}")
        print(f"   ΔP&L:    ${pnl_diff:+.0f}")

        if sharpe_diff > 0.05:
            print(f"\n✅ SIGNIFICANT IMPROVEMENT")
            print(f"   Recommend: Update to call=${optimal['min_call_credit']:.2f}, put=${optimal['min_put_credit']:.2f}")
        elif abs(sharpe_diff) < 0.01:
            print(f"\n➖ NO MEANINGFUL DIFFERENCE")
            print(f"   Current values are fine, no change needed")
        else:
            print(f"\n⚠️  SLIGHT DEGRADATION")
            print(f"   Current values are slightly better, keep as-is")
    elif optimal:
        print(f"Current config (call=$2.00, put=$2.75) NOT IN RESULTS")
        print(f"Best found: call=${optimal['min_call_credit']:.2f}, put=${optimal['min_put_credit']:.2f}")
    else:
        print("ERROR: No results collected")

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"Total time: {elapsed/60:.1f} minutes ({elapsed:.0f} seconds)")
    print(f"Results saved: {RESULTS_CSV}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
