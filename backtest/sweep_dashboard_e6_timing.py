"""
HYDRA E6 Upday Timing Sweep — 1-Minute Resolution

Tests E6 upday put-only at different times, plus OFF entirely.

Run: python -m backtest.sweep_dashboard_e6_timing
"""
import csv
import multiprocessing as mp
import os
import statistics
import time
from datetime import date, datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
    TaskProgressColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn,
)
from rich.table import Table
from rich import box

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)
ACCOUNT = 35000

# E6 times to test: OFF, then 11:45 through 14:00 in 15-min steps
# conditional_entry_times[0] is E6, [1] is E7
E6_CONFIGS = [
    ("OFF", False, "12:45"),  # disabled
    ("11:45", True, "11:45"),
    ("12:00", True, "12:00"),
    ("12:15", True, "12:15"),
    ("12:30", True, "12:30"),
    ("12:45", True, "12:45"),  # current
    ("13:00", True, "13:00"),
    ("13:15", True, "13:15"),
    ("13:30", True, "13:30"),
    ("13:45", True, "13:45"),
    ("14:00", True, "14:00"),
]


def _run_single(args: Tuple) -> Dict:
    label, enabled, e6_time = args
    from backtest.config import live_config
    from backtest.engine import run_backtest

    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    cfg.conditional_upday_e6_enabled = enabled
    cfg.conditional_entry_times = [e6_time, "13:15"]  # E6 at test time, E7 stays 13:15 (but E7 is OFF)

    results = run_backtest(cfg, verbose=False)
    pnls = [r.net_pnl for r in results]
    n = len(pnls); total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    stops = sum(r.stops_hit for r in results)
    mean = statistics.mean(pnls) if pnls else 0
    std = statistics.stdev(pnls) if n > 1 else 0
    sharpe = mean / std * (252 ** 0.5) if std > 0 else 0
    peak = cum = dd = 0.0
    for p in pnls:
        cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
    calmar = mean * 252 / dd if dd > 0 else 0

    # Count breach days and E6 fires
    breach = 0
    e6_fires = 0
    for r in results:
        dc = 0
        for e in r.entries:
            if e.entry_type == 'skipped': continue
            if e.entry_type == 'full_ic': dc += max(e.call_spread_width, e.put_spread_width) * 100
            elif e.entry_type == 'call_only': dc += e.call_spread_width * 100
            elif e.entry_type == 'put_only': dc += e.put_spread_width * 100
        if dc > ACCOUNT: breach += 1
        if len([e for e in r.entries if e.entry_type != 'skipped']) > 3:
            e6_fires += 1

    return {
        "label": label, "enabled": enabled, "e6_time": e6_time,
        "days": n, "sharpe": round(sharpe, 4),
        "total_pnl": round(total, 2), "max_dd": round(dd, 2),
        "calmar": round(calmar, 4), "win_rate": round(wins / n * 100, 1) if n else 0,
        "placed": placed, "stops": stops,
        "breach_days": breach, "breach_pct": round(breach / n * 100, 1),
        "e6_fires": e6_fires,
    }


def main():
    console = Console()
    n_workers = min(8, os.cpu_count() or 4)

    console.print()
    console.print(Panel(
        f"[bold cyan]E6 Upday Put-Only Timing Sweep[/]\n"
        f"[dim]1-min · {n_workers} workers · {len(E6_CONFIGS)} configs · OFF + 11:45–14:00[/]\n\n"
        f"  Account margin: [white]$35,000[/]\n"
        f"  Base entries:   [white]10:15, 10:45, 11:15[/]\n"
        f"  Current E6:     [cyan]12:45 (ON)[/]",
        box=box.ROUNDED, border_style="cyan", title="[bold white]⚡ E6 Timing[/]"))
    console.print()

    all_results = []
    best = None
    start_time = time.time()

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=30, complete_style="cyan", finished_style="green"),
        MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        task = progress.add_task("E6 timing sweep", total=len(E6_CONFIGS))
        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(_run_single, E6_CONFIGS):
                all_results.append(result)
                if best is None or result["sharpe"] > best["sharpe"]:
                    best = result
                progress.update(task, completed=len(all_results),
                    description=f"E6 timing  [green]best:{best['label']} Sh={best['sharpe']:.3f}[/]")

    elapsed = time.time() - start_time
    results = sorted(all_results, key=lambda r: r["sharpe"], reverse=True)

    tbl = Table(title="[bold yellow]E6 Upday Put-Only: Time vs OFF[/]",
                box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("E6 Time", width=8)
    tbl.add_column("Sharpe", justify="right", width=8)
    tbl.add_column("P&L", justify="right", width=10)
    tbl.add_column("MaxDD", justify="right", width=10)
    tbl.add_column("Calmar", justify="right", width=8)
    tbl.add_column("Win%", justify="right", width=6)
    tbl.add_column("E6 Fires", justify="right", width=8)
    tbl.add_column("Breach", justify="right", width=8)
    tbl.add_column("Breach%", justify="right", width=8)
    tbl.add_column("", width=14)

    for i, r in enumerate(results, 1):
        is_current = r["label"] == "12:45"
        is_off = r["label"] == "OFF"
        is_best = i == 1
        tag = ""
        if is_best and is_current: tag = "[bold green]◀ BEST+CURRENT[/]"
        elif is_best: tag = "[bold green]◀ BEST[/]"
        elif is_current: tag = "[cyan]◀ CURRENT[/]"
        elif is_off: tag = "[dim]◀ OFF[/]"

        style = "bold green" if is_best else ("cyan" if is_current else None)
        def fmt(val, s=style):
            return f"[{s}]{val}[/{s}]" if s else str(val)

        breach_style = "red" if r["breach_pct"] > 5 else ("yellow" if r["breach_pct"] > 0 else "green")
        tbl.add_row(
            str(i), fmt(r['label']), fmt(f"{r['sharpe']:.3f}"),
            fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"),
            fmt(f"{r['calmar']:.3f}"), fmt(f"{r['win_rate']:.0f}%"),
            fmt(f"{r['e6_fires']}"),
            f"[{breach_style}]{r['breach_days']}[/{breach_style}]",
            f"[{breach_style}]{r['breach_pct']:.1f}%[/{breach_style}]",
            tag,
        )

    console.print()
    console.print(tbl)

    # Summary
    off_result = next(r for r in results if r["label"] == "OFF")
    console.print(f"\n  E6 OFF:     Sharpe={off_result['sharpe']:.3f}  P&L=${off_result['total_pnl']:,.0f}  Breach={off_result['breach_days']} ({off_result['breach_pct']:.1f}%)")
    console.print(f"  BEST:       {best['label']}  Sharpe={best['sharpe']:.3f}  P&L=${best['total_pnl']:,.0f}  Breach={best['breach_days']} ({best['breach_pct']:.1f}%)")

    console.print()
    console.print(Panel(
        f"  Total configs : [white]{len(E6_CONFIGS)}[/]\n"
        f"  Wall time     : [yellow]{elapsed/60:.1f} min[/]",
        title="[bold]Timing[/]", box=box.ROUNDED, border_style="dim"))

    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"sweep_e6_timing_1min_{ts}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader(); w.writerows(results)
    console.print(f"\n  [dim]Results saved → {csv_path}[/]\n")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
