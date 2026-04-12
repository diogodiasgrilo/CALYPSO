"""
Sweep: Slippage calibration + parameter re-optimization

Phase 1: Calibrate slippage (find combo that best matches live P&L on Apr 6-8)
Phase 2: Re-sweep put buffer with calibrated slippage
Phase 3: Re-sweep call buffer with calibrated slippage
Phase 4: Re-sweep credit gates with calibrated slippage

Run: python -m backtest.sweep_slippage_calibration --workers 8
"""
import argparse
import csv
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

try:
    from rich.console import Console
    _RICH = True
except ImportError:
    _RICH = False

# Phase 1: calibration against 3 live days with exact strategy
CAL_START = date(2026, 4, 6)
CAL_END   = date(2026, 4, 8)
CAL_LIVE  = {"2026-04-06": 1475, "2026-04-07": -1100, "2026-04-08": -430}

# Phase 2-4: full historical sweep
FULL_START = date(2022, 5, 16)
FULL_END   = date(2026, 4, 8)


def build_cfg(start, end, resolution="1min", **overrides):
    cfg = live_config()
    cfg.start_date = start
    cfg.end_date = end
    cfg.data_resolution = resolution
    cfg.use_real_greeks = True
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def summarise(results: List[DayResult], label: str, live_pnl: dict = None) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl = sum(daily_pnls)
    total_days = len(results)
    win_days = sum(1 for p in daily_pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    total_stops = sum(r.stops_hit for r in results)

    mean = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    # Accuracy vs live (if provided)
    live_error = 0
    if live_pnl:
        for r in results:
            d = r.date.isoformat()
            if d in live_pnl:
                live_error += abs(r.net_pnl - live_pnl[d])

    return {
        "label": label, "days": total_days, "win": win_days,
        "win_rate": win_days / total_days * 100 if total_days else 0,
        "total_pnl": total_pnl, "mean_daily": mean, "sharpe": sharpe,
        "max_dd": max_dd, "calmar": calmar,
        "placed": placed, "total_stops": total_stops,
        "stop_rate": total_stops / placed * 100 if placed else 0,
        "live_error": live_error,
    }


def _run_one(args):
    label, cfg, live_pnl = args
    results = run_backtest(cfg, verbose=False)
    return summarise(results, label, live_pnl)


def run_phase(title, configs, n_workers, sort_key="sharpe", reverse=True):
    all_stats = []
    total = len(configs)

    print(f"\n{title}")
    print(f"  Running {total} configs with {n_workers} workers...")

    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_run_one, item): item[0] for item in configs}
        done = 0
        for future in as_completed(futures):
            stats = future.result()
            all_stats.append(stats)
            done += 1
            if done % 5 == 0 or done == total:
                print(f"  [{done}/{total}] {stats['label']} — Sharpe {stats['sharpe']:.3f}")

    all_stats.sort(key=lambda s: s[sort_key], reverse=reverse)
    return all_stats


def print_table(all_stats, metrics, title=""):
    if title:
        print(f"\n  {title}")
    header = f"  {'Label':<30}"
    for name, key, fmt in metrics:
        header += f" {name:>10}"
    print(header)
    print(f"  {'-' * (30 + 11 * len(metrics))}")
    for s in all_stats:
        row = f"  {s['label']:<30}"
        for name, key, fmt in metrics:
            row += f" {fmt.format(s[key]):>10}"
        print(row)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--phase", type=int, default=0, help="Run only phase N (0=all)")
    args = parser.parse_args()
    n = args.workers

    print(f"\nSlippage Calibration + Parameter Re-optimization")
    print(f"Workers: {n}")

    calibrated_slippage = 0.0
    calibrated_markup = 0.0

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: Calibrate slippage against live data (5-sec, Apr 6-8)
    # ══════════════════════════════════════════════════════════════════════
    if args.phase in (0, 1):
        slippage_values = [0.0, 15.0, 25.0, 35.0, 50.0, 75.0, 100.0]  # dollars (×100)
        markup_values = [0.0, 0.03, 0.05, 0.08, 0.10, 0.15]  # percentage

        configs = []
        cal_params = {}  # label -> (slip, markup) for later retrieval
        for slip in slippage_values:
            for markup in markup_values:
                label = f"slip=${slip/100:.2f} mk={markup*100:.0f}%"
                cfg = build_cfg(CAL_START, CAL_END, "5sec",
                                stop_slippage_per_leg=slip,
                                stop_spread_markup_pct=markup)
                configs.append((label, cfg, CAL_LIVE))
                cal_params[label] = (slip, markup)

        cal_stats = run_phase(
            f"PHASE 1: Calibrate slippage (5-sec data, Apr 6-8 vs live)",
            configs, n, sort_key="live_error", reverse=False
        )

        cal_metrics = [
            ("LiveErr", "live_error", "${:,.0f}"),
            ("P&L", "total_pnl", "${:+,.0f}"),
            ("Stops", "total_stops", "{:.0f}"),
            ("StopR%", "stop_rate", "{:.1f}%"),
        ]
        print_table(cal_stats[:15], cal_metrics, "Top 15 by live accuracy")

        best_cal = cal_stats[0]
        calibrated_slippage, calibrated_markup = cal_params[best_cal["label"]]
        print(f"\n  CALIBRATED: slippage=${calibrated_slippage/100:.2f}/leg, markup={calibrated_markup*100:.0f}%")
        print(f"  Live error: ${best_cal['live_error']:,.0f} (vs ${cal_stats[-1]['live_error']:,.0f} worst)")

    # For phases 2-4, use 1-min data (full history) with calibrated slippage
    if args.phase != 1 and calibrated_slippage == 0 and calibrated_markup == 0:
        # Fallback if calibration wasn't run (standalone --phase 2/3/4)
        calibrated_slippage = 50.0  # $0.50/leg
        calibrated_markup = 0.05    # 5%
        print(f"  Using default slippage: ${calibrated_slippage/100:.2f}/leg, markup: {calibrated_markup*100:.0f}%")

    base_overrides = {
        "stop_slippage_per_leg": calibrated_slippage,
        "stop_spread_markup_pct": calibrated_markup,
    }

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: Re-sweep put buffer with calibrated slippage
    # ══════════════════════════════════════════════════════════════════════
    if args.phase in (0, 2):
        put_buffers = [100, 125, 150, 155, 175, 200, 225, 250, 300, 400, 500]

        configs = []
        for pb in put_buffers:
            label = f"put_buf=${pb/100:.2f}" + (" LIVE" if pb == 155 else "")
            overrides = {**base_overrides, "put_stop_buffer": float(pb)}
            cfg = build_cfg(FULL_START, FULL_END, "1min", **overrides)
            configs.append((label, cfg, None))

        p2_stats = run_phase("PHASE 2: Put buffer re-sweep (with slippage)", configs, n)

        p2_metrics = [
            ("Sharpe", "sharpe", "{:.3f}"),
            ("P&L", "total_pnl", "${:,.0f}"),
            ("MaxDD", "max_dd", "${:,.0f}"),
            ("WR%", "win_rate", "{:.1f}%"),
            ("StopR%", "stop_rate", "{:.1f}%"),
            ("Stops", "total_stops", "{:.0f}"),
        ]
        print_table(p2_stats, p2_metrics, "Put buffer results (sorted by Sharpe)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: Re-sweep call buffer with calibrated slippage
    # ══════════════════════════════════════════════════════════════════════
    if args.phase in (0, 3):
        call_buffers = [10, 20, 25, 35, 50, 75, 100, 150]

        configs = []
        for cb in call_buffers:
            label = f"call_buf=${cb/100:.2f}" + (" LIVE" if cb == 35 else "")
            overrides = {**base_overrides, "call_stop_buffer": float(cb)}
            cfg = build_cfg(FULL_START, FULL_END, "1min", **overrides)
            configs.append((label, cfg, None))

        p3_stats = run_phase("PHASE 3: Call buffer re-sweep (with slippage)", configs, n)

        p3_metrics = [
            ("Sharpe", "sharpe", "{:.3f}"),
            ("P&L", "total_pnl", "${:,.0f}"),
            ("MaxDD", "max_dd", "${:,.0f}"),
            ("StopR%", "stop_rate", "{:.1f}%"),
            ("Stops", "total_stops", "{:.0f}"),
        ]
        print_table(p3_stats, p3_metrics, "Call buffer results (sorted by Sharpe)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4: Re-sweep credit gates with calibrated slippage
    # ══════════════════════════════════════════════════════════════════════
    if args.phase in (0, 4):
        credit_combos = [
            ("c$1.25 p$2.25", 1.25, 2.25, 0.75, 2.00),
            ("c$1.50 p$2.50", 1.50, 2.50, 0.75, 2.00),
            ("c$1.75 p$2.75", 1.75, 2.75, 0.75, 2.00),
            ("c$2.00 p$2.75 LIVE", 2.00, 2.75, 0.75, 2.00),
            ("c$2.00 p$3.00", 2.00, 3.00, 0.75, 2.50),
            ("c$2.25 p$3.00", 2.25, 3.00, 1.00, 2.50),
            ("c$2.50 p$3.25", 2.50, 3.25, 1.00, 2.75),
        ]

        configs = []
        for label, mc, mp, cf, pf in credit_combos:
            overrides = {
                **base_overrides,
                "min_call_credit": mc, "min_put_credit": mp,
                "call_credit_floor": cf, "put_credit_floor": pf,
            }
            cfg = build_cfg(FULL_START, FULL_END, "1min", **overrides)
            configs.append((label, cfg, None))

        p4_stats = run_phase("PHASE 4: Credit gates re-sweep (with slippage)", configs, n)

        p4_metrics = [
            ("Sharpe", "sharpe", "{:.3f}"),
            ("P&L", "total_pnl", "${:,.0f}"),
            ("MaxDD", "max_dd", "${:,.0f}"),
            ("WR%", "win_rate", "{:.1f}%"),
            ("StopR%", "stop_rate", "{:.1f}%"),
        ]
        print_table(p4_stats, p4_metrics, "Credit gates results (sorted by Sharpe)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 5: Re-sweep OTM floor with calibrated slippage
    # ══════════════════════════════════════════════════════════════════════
    if args.phase in (0, 5):
        otm_floors = [15, 20, 25, 30, 35, 40, 50, 60]

        configs = []
        for otm in otm_floors:
            label = f"otm_floor={otm}pt" + (" LIVE" if otm == 25 else "")
            overrides = {**base_overrides,
                         "min_call_otm_distance": otm,
                         "min_put_otm_distance": otm}
            cfg = build_cfg(FULL_START, FULL_END, "1min", **overrides)
            configs.append((label, cfg, None))

        p5_stats = run_phase("PHASE 5: OTM floor re-sweep (with slippage)", configs, n)

        p5_metrics = [
            ("Sharpe", "sharpe", "{:.3f}"),
            ("P&L", "total_pnl", "${:,.0f}"),
            ("MaxDD", "max_dd", "${:,.0f}"),
            ("WR%", "win_rate", "{:.1f}%"),
            ("StopR%", "stop_rate", "{:.1f}%"),
        ]
        print_table(p5_stats, p5_metrics, "OTM floor results (sorted by Sharpe)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 6: Re-sweep buffer decay with calibrated slippage
    # ══════════════════════════════════════════════════════════════════════
    if args.phase in (0, 6):
        decay_combos = [
            ("no_decay", None, None),
            ("1.5x/1.5h", 1.5, 1.5),
            ("1.5x/2.0h", 1.5, 2.0),
            ("2.0x/1.5h", 2.0, 1.5),
            ("2.0x/2.0h", 2.0, 2.0),
            ("2.1x/2.0h LIVE", 2.1, 2.0),
            ("2.1x/2.5h", 2.1, 2.5),
            ("2.1x/3.0h", 2.1, 3.0),
            ("2.5x/2.0h", 2.5, 2.0),
            ("2.5x/3.0h", 2.5, 3.0),
            ("3.0x/2.0h", 3.0, 2.0),
            ("3.0x/3.0h", 3.0, 3.0),
        ]

        configs = []
        for label, mult, hours in decay_combos:
            overrides = {**base_overrides}
            if mult is not None:
                overrides["buffer_decay_start_mult"] = mult
                overrides["buffer_decay_hours"] = hours
            else:
                overrides["buffer_decay_start_mult"] = None
                overrides["buffer_decay_hours"] = None
            cfg = build_cfg(FULL_START, FULL_END, "1min", **overrides)
            configs.append((label, cfg, None))

        p6_stats = run_phase("PHASE 6: Buffer decay re-sweep (with slippage)", configs, n)

        p6_metrics = [
            ("Sharpe", "sharpe", "{:.3f}"),
            ("P&L", "total_pnl", "${:,.0f}"),
            ("MaxDD", "max_dd", "${:,.0f}"),
            ("WR%", "win_rate", "{:.1f}%"),
            ("StopR%", "stop_rate", "{:.1f}%"),
        ]
        print_table(p6_stats, p6_metrics, "Buffer decay results (sorted by Sharpe)")

    # ══════════════════════════════════════════════════════════════════════
    # Save all results
    # ══════════════════════════════════════════════════════════════════════
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"slippage_calibration_{ts}.csv"

    all_results = []
    for stats in [locals().get("cal_stats"), locals().get("p2_stats"),
                  locals().get("p3_stats"), locals().get("p4_stats"),
                  locals().get("p5_stats"), locals().get("p6_stats")]:
        if stats:
            all_results.extend(stats)

    if all_results:
        csv_keys = list(all_results[0].keys())
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_results)
        print(f"\n  Results saved -> {csv_path}")

    print(f"\n  Calibrated slippage: ${calibrated_slippage/100:.2f}/leg, markup: {calibrated_markup*100:.0f}%")
    print("  Done!")
