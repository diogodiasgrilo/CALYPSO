"""
Sweep: Credit minimums + OTM floor distance

Two research questions:
  1. Lower credit minimums → entries stay further OTM (less credit, fewer stops)
  2. Raise OTM floor → skip entries that would be too close to ATM

Current live config: min_call_credit=$2.00, min_put_credit=$2.75,
                     min_call_otm_distance=25pt, min_put_otm_distance=25pt

Uses live_config() as base, 1-min data, real Greeks, 8 workers.

Run: python -m backtest.sweep_otm_credit
     python -m backtest.sweep_otm_credit --workers 8
"""
import argparse
import csv
import multiprocessing as mp
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


# ── Part 1: Credit minimums ─────────────────────────────────────────────────
# Lower credit → MKT-020/022 accepts entries further OTM → fewer stops but less premium
CREDIT_COMBOS = [
    # (label, min_call, min_put, call_floor, put_floor)
    ("call$0.75 put$2.00",  0.75, 2.00, 0.50, 1.75),
    ("call$1.00 put$2.00",  1.00, 2.00, 0.75, 1.75),
    ("call$1.00 put$2.25",  1.00, 2.25, 0.75, 2.00),
    ("call$1.25 put$2.25",  1.25, 2.25, 0.75, 2.00),
    ("call$1.25 put$2.50",  1.25, 2.50, 0.75, 2.00),
    ("call$1.50 put$2.50",  1.50, 2.50, 0.75, 2.00),
    ("call$1.50 put$2.75",  1.50, 2.75, 0.75, 2.00),
    ("call$1.75 put$2.75",  1.75, 2.75, 0.75, 2.00),
    ("call$2.00 put$2.75",  2.00, 2.75, 0.75, 2.00),  # current live
    ("call$2.00 put$3.00",  2.00, 3.00, 0.75, 2.50),
    ("call$2.25 put$3.00",  2.25, 3.00, 0.75, 2.50),
    ("call$2.50 put$3.25",  2.50, 3.25, 1.00, 2.75),
]

# ── Part 2: OTM floor distance ──────────────────────────────────────────────
# Higher floor → skip entries where tightening gets too close to ATM
OTM_FLOOR_VALUES = [15, 20, 25, 30, 35, 40, 45, 50, 55, 60]


def build_credit_cfg(min_call, min_put, call_floor, put_floor):
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.min_call_credit = min_call
    cfg.min_put_credit = min_put
    cfg.call_credit_floor = call_floor
    cfg.put_credit_floor = put_floor
    return cfg


def build_otm_cfg(otm_floor):
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.data_resolution = "1min"
    cfg.use_real_greeks = True
    cfg.min_call_otm_distance = otm_floor
    cfg.min_put_otm_distance = otm_floor
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl = sum(daily_pnls)
    total_days = len(results)
    win_days = sum(1 for p in daily_pnls if p > 0)
    loss_days = sum(1 for p in daily_pnls if p < 0)

    placed = sum(r.entries_placed for r in results)
    skipped = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)

    full_ic = sum(sum(1 for e in r.entries if e.entry_type == "full_ic") for r in results)
    call_only = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in results)
    put_only = sum(sum(1 for e in r.entries if e.entry_type == "put_only") for r in results)

    call_credits = [e.call_credit for r in results for e in r.entries if e.call_credit > 0]
    put_credits = [e.put_credit for r in results for e in r.entries if e.put_credit > 0]
    avg_call_credit = statistics.mean(call_credits) if call_credits else 0
    avg_put_credit = statistics.mean(put_credits) if put_credits else 0

    # Average OTM distance — computed from strikes and SPX at entry
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

    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops = sum(sum(1 for e in r.entries if e.put_outcome == "stopped") for r in results)

    return {
        "label": label, "days": total_days, "win": win_days, "loss": loss_days,
        "win_rate": win_days / total_days * 100 if total_days else 0,
        "total_pnl": total_pnl, "mean_daily": mean, "stdev_daily": stdev,
        "sharpe": sharpe, "max_dd": max_dd, "calmar": calmar,
        "placed": placed, "skipped": skipped,
        "total_stops": total_stops,
        "stop_rate": total_stops / placed * 100 if placed else 0,
        "call_stops": call_stops, "put_stops": put_stops,
        "full_ic": full_ic, "call_only": call_only, "put_only": put_only,
        "avg_call_credit": avg_call_credit, "avg_put_credit": avg_put_credit,
        "avg_call_otm": avg_call_otm, "avg_put_otm": avg_put_otm,
    }


METRICS = [
    ("Win rate %",          "win_rate",        "{:.1f}%"),
    ("Total net P&L",       "total_pnl",       "${:,.0f}"),
    ("Mean daily P&L",      "mean_daily",      "${:.2f}"),
    ("Sharpe (ann)",        "sharpe",          "{:.3f}"),
    ("Max drawdown",        "max_dd",          "${:,.0f}"),
    ("Calmar ratio",        "calmar",          "{:.3f}"),
    ("Placed",              "placed",          "{:.0f}"),
    ("Skipped",             "skipped",         "{:.0f}"),
    ("Total stops",         "total_stops",     "{:.0f}"),
    ("Stop rate %",         "stop_rate",       "{:.1f}%"),
    ("Full IC",             "full_ic",         "{:.0f}"),
    ("Call-only",           "call_only",       "{:.0f}"),
    ("Put-only",            "put_only",        "{:.0f}"),
    ("Avg call credit",     "avg_call_credit", "${:.2f}"),
    ("Avg put credit",      "avg_put_credit",  "${:.2f}"),
    ("Avg call OTM",        "avg_call_otm",    "{:.0f}pt"),
    ("Avg put OTM",         "avg_put_otm",     "{:.0f}pt"),
]

HIGHER_IS_BETTER = {"win_rate", "total_pnl", "sharpe", "calmar", "mean_daily",
                    "placed", "full_ic", "avg_call_credit", "avg_put_credit",
                    "avg_call_otm", "avg_put_otm"}
LOWER_IS_BETTER = {"max_dd", "stop_rate", "skipped", "total_stops"}


def _run_one(args):
    """Worker function for parallel execution."""
    label, cfg = args
    results = run_backtest(cfg, verbose=False)
    return summarise(results, label)


def run_section(title, configs_and_labels, n_workers, console=None):
    all_stats = []
    total = len(configs_and_labels)

    if _RICH:
        console.print(f"\n[bold yellow]{title}[/]")
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[yellow]{task.percentage:.0f}%"),
            TextColumn("*"), TimeElapsedColumn(),
            TextColumn("*"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
            console=console, refresh_per_second=4,
        ) as progress:
            task = progress.add_task("Running", total=total)
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                futures = {pool.submit(_run_one, item): item[0] for item in configs_and_labels}
                for future in as_completed(futures):
                    stats = future.result()
                    all_stats.append(stats)
                    progress.update(task, description=f"{stats['label']}")
                    progress.advance(task)
    else:
        print(title)
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_run_one, item): item[0] for item in configs_and_labels}
            for future in as_completed(futures):
                stats = future.result()
                all_stats.append(stats)
                print(f"  Done: {stats['label']} — Sharpe {stats['sharpe']:.3f}")

    # Sort by Sharpe descending for display
    all_stats.sort(key=lambda s: s["sharpe"], reverse=True)

    if _RICH:
        tbl = Table(title=title, box=box.SIMPLE_HEAVY,
                    show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=18)
        for s in all_stats:
            tbl.add_column(s["label"], justify="right", width=14)
        for metric, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            if key in HIGHER_IS_BETTER:
                bi = max(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[bi] = f"[bold green]{row_vals[bi]}[/]"
            elif key in LOWER_IS_BETTER:
                bi = min(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[bi] = f"[bold green]{row_vals[bi]}[/]"
            tbl.add_row(metric, *row_vals)
        console.print()
        console.print(tbl)
    else:
        for s in all_stats:
            print(f"  {s['label']}: Sharpe={s['sharpe']:.3f}, P&L=${s['total_pnl']:,.0f}, "
                  f"DD=${s['max_dd']:,.0f}, WR={s['win_rate']:.1f}%, "
                  f"Stops={s['stop_rate']:.1f}%, OTM={s['avg_call_otm']:.0f}/{s['avg_put_otm']:.0f}")

    best = max(all_stats, key=lambda s: s["sharpe"])
    msg = f"\n  Best Sharpe: {best['label']} ({best['sharpe']:.3f})"
    console.print(msg) if _RICH else print(msg)

    return all_stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sweep credit minimums + OTM floors")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--credit-only", action="store_true", help="Only run credit sweep")
    parser.add_argument("--otm-only", action="store_true", help="Only run OTM floor sweep")
    args = parser.parse_args()

    console = Console() if _RICH else None
    n_workers = args.workers

    if _RICH:
        console.print("\n[bold cyan]Sweep: Credit Minimums + OTM Floor Distance[/]")
        console.print(f"Base: live_config()  |  Period: {START_DATE} -> {END_DATE}")
        console.print(f"Workers: {n_workers}  |  1-min data  |  Real Greeks\n")

    all_credit_stats = []
    all_otm_stats = []

    # ── Part 1: Credit minimums ──────────────────────────────────────────────
    if not args.otm_only:
        credit_configs = [
            (label, build_credit_cfg(mc, mp, cf, pf))
            for label, mc, mp, cf, pf in CREDIT_COMBOS
        ]
        all_credit_stats = run_section(
            "Part 1: Credit Minimums (lower = entries stay further OTM)",
            credit_configs, n_workers, console
        )

    # ── Part 2: OTM floor distance ──────────────────────────────────────────
    if not args.credit_only:
        otm_configs = [
            (f"floor={v}pt", build_otm_cfg(v))
            for v in OTM_FLOOR_VALUES
        ]
        all_otm_stats = run_section(
            "Part 2: OTM Floor Distance (higher = skip entries too close to ATM)",
            otm_configs, n_workers, console
        )

    # ── Save CSV ─────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"otm_credit_sweep_{ts}.csv"
    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed", "skipped", "total_stops", "stop_rate",
                "call_stops", "put_stops", "full_ic", "call_only", "put_only",
                "avg_call_credit", "avg_put_credit", "avg_call_otm", "avg_put_otm"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_credit_stats + all_otm_stats)
    msg = f"\n  Results saved -> {csv_path}"
    console.print(msg) if _RICH else print(msg)
