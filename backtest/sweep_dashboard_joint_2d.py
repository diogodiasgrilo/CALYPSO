"""
HYDRA Joint 2D Sweep — spread_vix_multiplier × max_spread_width

These two parameters are coupled (formula: min(VIX × mult, max_width))
and can't be optimized independently. This finds the true optimal pair.

Run: python -m backtest.sweep_dashboard_joint_2d
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

# Grid
MULTS = [v / 10 for v in range(45, 70, 2)]  # 4.5, 4.7, 4.9, ..., 6.9
WIDTHS = list(range(80, 116, 3))  # 80, 83, 86, ..., 113

CURRENT_MULT = 5.4
CURRENT_WIDTH = 96


def _run_single(args: Tuple) -> Dict:
    mult, width, label = args
    from backtest.config import live_config
    from backtest.engine import run_backtest

    cfg = live_config()
    cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    cfg.spread_vix_multiplier = mult
    cfg.max_spread_width = width

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

    # Breach check
    breach = 0
    for r in results:
        dc = 0
        for e in r.entries:
            if e.entry_type == 'skipped': continue
            if e.entry_type == 'full_ic': dc += max(e.call_spread_width, e.put_spread_width) * 100
            elif e.entry_type == 'call_only': dc += e.call_spread_width * 100
            elif e.entry_type == 'put_only': dc += e.put_spread_width * 100
        if dc > 35000: breach += 1

    return {
        "mult": mult, "width": width, "label": label,
        "sharpe": round(sharpe, 4), "total_pnl": round(total, 2),
        "max_dd": round(dd, 2), "calmar": round(calmar, 4),
        "win_rate": round(wins / n * 100, 1) if n else 0,
        "placed": placed, "stops": stops,
        "breach": breach, "breach_pct": round(breach / n * 100, 1) if n else 0,
    }


def main():
    console = Console()
    n_workers = min(8, os.cpu_count() or 4)

    tasks = []
    for mult in MULTS:
        for width in WIDTHS:
            label = f"{mult:.1f}x/{width}pt"
            tasks.append((mult, width, label))

    total_tasks = len(tasks)

    console.print()
    console.print(Panel(
        f"[bold cyan]Joint 2D Sweep: VIX Multiplier × Max Spread Width[/]\n"
        f"[dim]1-min · {n_workers} workers · formula: min(VIX × mult, max_width)[/]\n\n"
        f"  Multipliers : [white]{MULTS[0]:.1f}× – {MULTS[-1]:.1f}×[/] ({len(MULTS)} values)\n"
        f"  Widths      : [white]{WIDTHS[0]}pt – {WIDTHS[-1]}pt[/] ({len(WIDTHS)} values)\n"
        f"  Total       : [white]{total_tasks}[/] combinations\n"
        f"  Current     : [cyan]{CURRENT_MULT:.1f}× / {CURRENT_WIDTH}pt[/]",
        box=box.DOUBLE, border_style="cyan",
        title="[bold white]⚡ 2D Joint Optimization[/]"))
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
        task = progress.add_task(f"2D sweep ({n_workers} workers)", total=total_tasks)
        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(_run_single, tasks):
                all_results.append(result)
                if best is None or result["sharpe"] > best["sharpe"]:
                    best = result
                progress.update(task, completed=len(all_results),
                    description=f"2D sweep  [green]best:{best['label']} Sh={best['sharpe']:.3f}[/]")

    elapsed = time.time() - start_time

    # Top 20 by Sharpe
    results = sorted(all_results, key=lambda r: r["sharpe"], reverse=True)
    console.print()
    tbl = Table(title="[bold yellow]Top 20 Combinations by Sharpe[/]",
                box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Mult", width=6)
    tbl.add_column("Width", width=6)
    tbl.add_column("Sharpe", justify="right", width=8)
    tbl.add_column("P&L", justify="right", width=10)
    tbl.add_column("MaxDD", justify="right", width=10)
    tbl.add_column("Calmar", justify="right", width=8)
    tbl.add_column("Breach%", justify="right", width=8)
    tbl.add_column("", width=14)

    for i, r in enumerate(results[:20], 1):
        is_current = abs(r["mult"] - CURRENT_MULT) < 0.01 and r["width"] == CURRENT_WIDTH
        is_best = i == 1
        tag = ""
        if is_best and is_current: tag = "[bold green]◀ BEST+CUR[/]"
        elif is_best: tag = "[bold green]◀ BEST[/]"
        elif is_current: tag = "[cyan]◀ CURRENT[/]"
        style = "bold green" if is_best else ("cyan" if is_current else None)
        def fmt(val, s=style):
            return f"[{s}]{val}[/{s}]" if s else str(val)
        breach_style = "red" if r["breach_pct"] > 10 else ("yellow" if r["breach_pct"] > 5 else "green")
        tbl.add_row(str(i), fmt(f"{r['mult']:.1f}×"), fmt(f"{r['width']}pt"),
            fmt(f"{r['sharpe']:.3f}"), fmt(f"${r['total_pnl']:,.0f}"),
            fmt(f"${r['max_dd']:,.0f}"), fmt(f"{r['calmar']:.3f}"),
            f"[{breach_style}]{r['breach_pct']:.1f}%[/{breach_style}]", tag)

    console.print(tbl)

    # Heatmap-style: best Sharpe for each mult
    console.print()
    by_mult = {}
    for r in all_results:
        m = r["mult"]
        if m not in by_mult or r["sharpe"] > by_mult[m]["sharpe"]:
            by_mult[m] = r

    htbl = Table(title="[bold yellow]Best Width per Multiplier[/]",
                 box=box.SIMPLE_HEAVY, header_style="bold yellow")
    htbl.add_column("Mult", width=6)
    htbl.add_column("Best Width", width=10)
    htbl.add_column("Sharpe", justify="right", width=8)
    htbl.add_column("P&L", justify="right", width=10)
    htbl.add_column("Breach%", justify="right", width=8)

    for m in sorted(by_mult.keys()):
        r = by_mult[m]
        is_best = r["sharpe"] == best["sharpe"]
        style = "bold green" if is_best else None
        def fmt(val, s=style):
            return f"[{s}]{val}[/{s}]" if s else str(val)
        htbl.add_row(fmt(f"{m:.1f}×"), fmt(f"{r['width']}pt"),
            fmt(f"{r['sharpe']:.3f}"), fmt(f"${r['total_pnl']:,.0f}"),
            f"{r['breach_pct']:.1f}%")

    console.print(htbl)

    # Best width for each mult
    console.print()
    by_width = {}
    for r in all_results:
        w = r["width"]
        if w not in by_width or r["sharpe"] > by_width[w]["sharpe"]:
            by_width[w] = r

    wtbl = Table(title="[bold yellow]Best Multiplier per Width[/]",
                 box=box.SIMPLE_HEAVY, header_style="bold yellow")
    wtbl.add_column("Width", width=6)
    wtbl.add_column("Best Mult", width=10)
    wtbl.add_column("Sharpe", justify="right", width=8)
    wtbl.add_column("P&L", justify="right", width=10)
    wtbl.add_column("Breach%", justify="right", width=8)

    for w in sorted(by_width.keys()):
        r = by_width[w]
        is_best = r["sharpe"] == best["sharpe"]
        style = "bold green" if is_best else None
        def fmt(val, s=style):
            return f"[{s}]{val}[/{s}]" if s else str(val)
        wtbl.add_row(fmt(f"{w}pt"), fmt(f"{r['mult']:.1f}×"),
            fmt(f"{r['sharpe']:.3f}"), fmt(f"${r['total_pnl']:,.0f}"),
            f"{r['breach_pct']:.1f}%")

    console.print(wtbl)

    # Current vs best
    current_r = next((r for r in all_results
                      if abs(r["mult"] - CURRENT_MULT) < 0.01 and r["width"] == CURRENT_WIDTH), None)

    console.print()
    console.print(Panel(
        f"  CURRENT : [cyan]{CURRENT_MULT:.1f}× / {CURRENT_WIDTH}pt[/]  "
        f"Sharpe={current_r['sharpe']:.3f}  P&L=${current_r['total_pnl']:,.0f}  "
        f"Breach={current_r['breach_pct']:.1f}%\n"
        f"  BEST    : [bold green]{best['mult']:.1f}× / {best['width']}pt[/]  "
        f"Sharpe={best['sharpe']:.3f}  P&L=${best['total_pnl']:,.0f}  "
        f"Breach={best['breach_pct']:.1f}%\n"
        f"  Δ Sharpe: [{'green' if best['sharpe'] > current_r['sharpe'] else 'dim'}]"
        f"{best['sharpe'] - current_r['sharpe']:+.3f}[/]",
        title="[bold]2D Verdict[/]", box=box.DOUBLE, border_style="green"))

    console.print()
    console.print(Panel(
        f"  Total combos  : [white]{total_tasks}[/]\n"
        f"  Workers       : [white]{n_workers}[/]\n"
        f"  Wall time     : [yellow]{elapsed/60:.1f} min[/]\n"
        f"  Avg per combo : [dim]{elapsed/total_tasks:.1f}s[/]",
        title="[bold]Timing[/]", box=box.ROUNDED, border_style="dim"))

    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"sweep_joint_2d_1min_{ts}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
        w.writeheader(); w.writerows(all_results)
    console.print(f"\n  [dim]Results saved → {csv_path}[/]\n")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
