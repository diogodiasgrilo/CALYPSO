"""
Sweep: One-sided entry parameters (call-only + put-only)

Tests credit floors, VIX gate, and theoretical put credit for one-sided entries:
  Part 1: call_credit_floor (MKT-029 hard floor for call-only entries)
  Part 2: put_credit_floor (MKT-029 hard floor for put-only entries)
  Part 3: put_only_max_vix (MKT-032 VIX gate for put-only entries)
  Part 4: downday_theoretical_put_credit (theo put in call-only stop formula)

Current live: call_floor=$0.75, put_floor=$2.00, put_only_max_vix=15.0, theo_put=$2.60

Run: python -m backtest.sweep_onesided --workers 8
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
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                               SpinnerColumn, TextColumn, TimeElapsedColumn,
                               TimeRemainingColumn)
    from rich.table import Table
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)


def build_cfg(**overrides) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# Part 1: call_credit_floor — floor for call-only entries (downday, MKT-040, MKT-038)
CALL_FLOOR_VALUES = [0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00]

# Part 2: put_credit_floor — floor for put-only entries (E6, MKT-039)
PUT_FLOOR_VALUES = [1.00, 1.25, 1.50, 1.75, 2.00, 2.25, 2.50, 2.75]

# Part 3: put_only_max_vix — VIX gate for put-only entries
VIX_GATE_VALUES = [10.0, 12.0, 14.0, 15.0, 18.0, 20.0, 25.0, 30.0]

# Part 4: downday_theoretical_put_credit — theo put in call-only stop formula
THEO_PUT_VALUES = [150.0, 175.0, 200.0, 225.0, 250.0, 260.0, 275.0, 300.0, 350.0]


def summarise(results: List[DayResult], label: str) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl = sum(daily_pnls)
    total_days = len(results)
    win_days = sum(1 for p in daily_pnls if p > 0)

    placed = sum(r.entries_placed for r in results)
    skipped = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)

    full_ic = sum(sum(1 for e in r.entries if e.entry_type == "full_ic") for r in results)
    call_only = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in results)
    put_only = sum(sum(1 for e in r.entries if e.entry_type == "put_only") for r in results)

    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops = sum(sum(1 for e in r.entries if e.put_outcome == "stopped") for r in results)

    call_otms = [e.short_call - e.spx_at_entry for r in results for e in r.entries
                 if e.short_call > 0 and e.spx_at_entry > 0]
    put_otms = [e.spx_at_entry - e.short_put for r in results for e in r.entries
                if e.short_put > 0 and e.spx_at_entry > 0]
    avg_call_otm = statistics.mean(call_otms) if call_otms else 0
    avg_put_otm = statistics.mean(put_otms) if put_otms else 0

    mean = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    return {
        "label": label, "days": total_days, "win": win_days,
        "win_rate": win_days / total_days * 100 if total_days else 0,
        "total_pnl": total_pnl, "mean_daily": mean, "sharpe": sharpe,
        "max_dd": max_dd, "calmar": calmar,
        "placed": placed, "skipped": skipped,
        "total_stops": total_stops,
        "stop_rate": total_stops / placed * 100 if placed else 0,
        "call_stops": call_stops, "put_stops": put_stops,
        "full_ic": full_ic, "call_only": call_only, "put_only": put_only,
        "avg_call_otm": avg_call_otm, "avg_put_otm": avg_put_otm,
    }


METRICS = [
    ("Sharpe (ann)",   "sharpe",     "{:.3f}"),
    ("Total net P&L",  "total_pnl",  "${:,.0f}"),
    ("Max drawdown",   "max_dd",     "${:,.0f}"),
    ("Win rate %",     "win_rate",   "{:.1f}%"),
    ("Stop rate %",    "stop_rate",  "{:.1f}%"),
    ("Placed",         "placed",     "{:.0f}"),
    ("Full IC",        "full_ic",    "{:.0f}"),
    ("Call-only",      "call_only",  "{:.0f}"),
    ("Put-only",       "put_only",   "{:.0f}"),
    ("Call stops",     "call_stops", "{:.0f}"),
    ("Put stops",      "put_stops",  "{:.0f}"),
    ("Avg call OTM",   "avg_call_otm", "{:.0f}pt"),
    ("Avg put OTM",    "avg_put_otm",  "{:.0f}pt"),
]


def _run_one(args):
    label, cfg = args
    results = run_backtest(cfg, verbose=False)
    return summarise(results, label)


def run_section(title, configs, n_workers, console=None):
    all_stats = []
    total = len(configs)

    if _RICH:
        console.print(f"\n[bold yellow]{title}[/]")
        with Progress(
            SpinnerColumn(), TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40), MofNCompleteColumn(),
            TextColumn("[yellow]{task.percentage:.0f}%"),
            TextColumn("*"), TimeElapsedColumn(),
            TextColumn("*"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
            console=console, refresh_per_second=4,
        ) as progress:
            task = progress.add_task("Running", total=total)
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                futures = {pool.submit(_run_one, item): item[0] for item in configs}
                for future in as_completed(futures):
                    stats = future.result()
                    all_stats.append(stats)
                    progress.update(task, description=stats["label"])
                    progress.advance(task)
    else:
        print(title)
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_run_one, item): item[0] for item in configs}
            for future in as_completed(futures):
                stats = future.result()
                all_stats.append(stats)
                print(f"  Done: {stats['label']} Sharpe={stats['sharpe']:.3f}")

    all_stats.sort(key=lambda s: s["sharpe"], reverse=True)

    # Print plain-text table (Rich truncates columns)
    print(f"\n{'Label':<22} {'Sharpe':>7} {'P&L':>10} {'MaxDD':>8} {'WR%':>5} {'StopR':>6} {'Place':>6} {'CallO':>5} {'PutO':>5} {'CStops':>6} {'PStops':>6} {'AvgCOTM':>8} {'AvgPOTM':>8}")
    print("-" * 120)
    for s in all_stats:
        live = " *" if "LIVE" in s["label"] else ""
        print(f"{s['label']:<22} {s['sharpe']:>7.3f} {s['total_pnl']:>10,.0f} {s['max_dd']:>8,.0f} {s['win_rate']:>4.1f}% {s['stop_rate']:>5.1f}% {s['placed']:>6} {s['call_only']:>5} {s['put_only']:>5} {s['call_stops']:>6} {s['put_stops']:>6} {s['avg_call_otm']:>7.0f}pt {s['avg_put_otm']:>7.0f}pt{live}")

    best = max(all_stats, key=lambda s: s["sharpe"])
    print(f"\n  Best Sharpe: {best['label']} ({best['sharpe']:.3f})")

    return all_stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    console = Console() if _RICH else None
    n = args.workers

    print(f"\nSweep: One-Sided Entry Parameters")
    print(f"Base: live_config()  |  Period: {START_DATE} -> {END_DATE}")
    print(f"Workers: {n}  |  1-min data  |  Real Greeks\n")

    all_results = []

    # Part 1: call_credit_floor
    configs = [(f"call_floor=${v:.2f}" + (" LIVE" if v == 0.75 else ""),
                build_cfg(call_credit_floor=v))
               for v in CALL_FLOOR_VALUES]
    all_results += run_section("Part 1: call_credit_floor (MKT-029 call-only floor)", configs, n, console)

    # Part 2: put_credit_floor
    configs = [(f"put_floor=${v:.2f}" + (" LIVE" if v == 2.00 else ""),
                build_cfg(put_credit_floor=v))
               for v in PUT_FLOOR_VALUES]
    all_results += run_section("Part 2: put_credit_floor (MKT-029 put-only floor)", configs, n, console)

    # Part 3: put_only_max_vix
    configs = [(f"vix_gate={v:.0f}" + (" LIVE" if v == 15.0 else ""),
                build_cfg(put_only_max_vix=v))
               for v in VIX_GATE_VALUES]
    all_results += run_section("Part 3: put_only_max_vix (MKT-032 VIX gate)", configs, n, console)

    # Part 4: downday_theoretical_put_credit
    configs = [(f"theo_put=${v/100:.2f}" + (" LIVE" if v == 260.0 else ""),
                build_cfg(downday_theoretical_put_credit=v))
               for v in THEO_PUT_VALUES]
    all_results += run_section("Part 4: downday_theoretical_put_credit (call-only stop)", configs, n, console)

    # Save CSV
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"onesided_sweep_{ts}.csv"
    csv_keys = ["label", "days", "win", "win_rate", "total_pnl", "mean_daily",
                "sharpe", "max_dd", "calmar", "placed", "skipped", "total_stops",
                "stop_rate", "call_stops", "put_stops", "full_ic", "call_only",
                "put_only", "avg_call_otm", "avg_put_otm"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_results)
    print(f"\n  Results saved -> {csv_path}")
