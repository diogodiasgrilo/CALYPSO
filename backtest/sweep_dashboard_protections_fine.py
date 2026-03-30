"""
HYDRA Protection Features Fine-Grain — 1-Minute Resolution

Fine-tunes the protection combo: daily loss limit × whipsaw multiplier.
Spread cap had zero impact, VIX spike had minimal — focus on the two that matter.

Run: python -m backtest.sweep_dashboard_protections_fine
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

# 2D grid: daily_loss_limit × whipsaw_range_skip_mult
LOSS_LIMITS = [None, -300.0, -400.0, -500.0, -600.0, -750.0, -1000.0, -1500.0]
WHIPSAW_MULTS = [None, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]


def _run_single(args: Tuple) -> Dict:
    loss_limit, whipsaw, label = args
    from backtest.config import live_config
    from backtest.engine import run_backtest

    cfg = live_config()
    cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    cfg.daily_loss_limit = loss_limit
    cfg.whipsaw_range_skip_mult = whipsaw

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
    worst_day = min(pnls) if pnls else 0

    return {
        "loss_limit": loss_limit, "whipsaw": whipsaw, "label": label,
        "sharpe": round(sharpe, 4), "total_pnl": round(total, 2),
        "max_dd": round(dd, 2), "calmar": round(calmar, 4),
        "win_rate": round(wins / n * 100, 1) if n else 0,
        "worst_day": round(worst_day, 2),
        "placed": placed, "stops": stops,
    }


def main():
    console = Console()
    n_workers = min(8, os.cpu_count() or 4)

    tasks = []
    for ll in LOSS_LIMITS:
        for wm in WHIPSAW_MULTS:
            ll_str = "OFF" if ll is None else f"${ll:.0f}"
            wm_str = "OFF" if wm is None else f"{wm:.2f}×"
            label = f"L={ll_str} W={wm_str}"
            tasks.append((ll, wm, label))

    total = len(tasks)

    console.print()
    console.print(Panel(
        f"[bold cyan]Protection Fine-Grain: Loss Limit × Whipsaw[/]\n"
        f"[dim]1-min · {n_workers} workers · {total} combos[/]\n\n"
        f"  Loss limits : [white]{[('OFF' if l is None else f'${l:.0f}') for l in LOSS_LIMITS]}[/]\n"
        f"  Whipsaw     : [white]{[('OFF' if w is None else f'{w}×') for w in WHIPSAW_MULTS]}[/]",
        box=box.DOUBLE, border_style="cyan",
        title="[bold white]⚡ 2D Protection Sweep[/]"))
    console.print()

    all_results = []
    best = None

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=28, complete_style="cyan", finished_style="green"),
        MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        task = progress.add_task(f"2D sweep ({n_workers} workers)", total=total)
        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(_run_single, tasks):
                all_results.append(result)
                if best is None or result["sharpe"] > best["sharpe"]:
                    best = result
                progress.update(task, completed=len(all_results),
                    description=f"2D sweep  [green]best:{best['label'][:20]} Sh={best['sharpe']:.3f}[/]")

    # Top 20 by Sharpe
    results = sorted(all_results, key=lambda r: r["sharpe"], reverse=True)
    console.print()
    tbl = Table(title="[bold yellow]Top 20 by Sharpe[/]",
                box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Loss Limit", width=10)
    tbl.add_column("Whipsaw", width=8)
    tbl.add_column("Sharpe", justify="right", width=8)
    tbl.add_column("P&L", justify="right", width=10)
    tbl.add_column("MaxDD", justify="right", width=10)
    tbl.add_column("Calmar", justify="right", width=8)
    tbl.add_column("Worst Day", justify="right", width=10)
    tbl.add_column("Placed", justify="right", width=7)
    tbl.add_column("", width=14)

    for i, r in enumerate(results[:20], 1):
        is_baseline = r["loss_limit"] is None and r["whipsaw"] is None
        is_best = i == 1
        tag = ""
        if is_best: tag = "[bold green]◀ BEST[/]"
        elif is_baseline: tag = "[cyan]◀ BASELINE[/]"
        style = "bold green" if is_best else ("cyan" if is_baseline else None)
        def fmt(val, s=style):
            return f"[{s}]{val}[/{s}]" if s else str(val)
        ll_str = "OFF" if r["loss_limit"] is None else f"${r['loss_limit']:.0f}"
        wm_str = "OFF" if r["whipsaw"] is None else f"{r['whipsaw']:.2f}×"
        tbl.add_row(str(i), fmt(ll_str), fmt(wm_str), fmt(f"{r['sharpe']:.3f}"),
            fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"),
            fmt(f"{r['calmar']:.3f}"), fmt(f"${r['worst_day']:,.0f}"),
            fmt(f"{r['placed']}"), tag)

    console.print(tbl)

    # Best whipsaw for each loss limit
    console.print()
    by_ll = {}
    for r in all_results:
        ll = r["loss_limit"]
        if ll not in by_ll or r["sharpe"] > by_ll[ll]["sharpe"]:
            by_ll[ll] = r

    ltbl = Table(title="[bold yellow]Best Whipsaw per Loss Limit[/]",
                 box=box.SIMPLE_HEAVY, header_style="bold yellow")
    ltbl.add_column("Loss Limit", width=10)
    ltbl.add_column("Best Whipsaw", width=10)
    ltbl.add_column("Sharpe", justify="right", width=8)
    ltbl.add_column("P&L", justify="right", width=10)
    ltbl.add_column("MaxDD", justify="right", width=10)
    ltbl.add_column("Worst Day", justify="right", width=10)

    for ll in LOSS_LIMITS:
        r = by_ll[ll]
        ll_str = "OFF" if ll is None else f"${ll:.0f}"
        wm_str = "OFF" if r["whipsaw"] is None else f"{r['whipsaw']:.2f}×"
        is_best = r["sharpe"] == best["sharpe"]
        style = "bold green" if is_best else None
        def fmt(val, s=style):
            return f"[{s}]{val}[/{s}]" if s else str(val)
        ltbl.add_row(fmt(ll_str), fmt(wm_str), fmt(f"{r['sharpe']:.3f}"),
            fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"),
            fmt(f"${r['worst_day']:,.0f}"))

    console.print(ltbl)

    # Baseline vs best
    baseline = next(r for r in all_results if r["loss_limit"] is None and r["whipsaw"] is None)
    console.print()
    console.print(Panel(
        f"  BASELINE : Sharpe={baseline['sharpe']:.3f}  P&L=${baseline['total_pnl']:,.0f}  "
        f"MaxDD=${baseline['max_dd']:,.0f}  Worst=${baseline['worst_day']:,.0f}\n"
        f"  BEST     : [bold green]{best['label']}[/]  Sharpe={best['sharpe']:.3f}  "
        f"P&L=${best['total_pnl']:,.0f}  MaxDD=${best['max_dd']:,.0f}  "
        f"Worst=${best['worst_day']:,.0f}",
        title="[bold]Verdict[/]", box=box.DOUBLE, border_style="green"))

    console.print()
    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"sweep_protections_fine_1min_{ts}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
        w.writeheader(); w.writerows(all_results)
    console.print(f"  [dim]Results saved → {csv_path}[/]\n")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
