"""
HYDRA Edge Check — max_spread_width

Run: python -m backtest.sweep_dashboard_max_spread_edge
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

SWEEPS = {
    "max_spread_width": {
        "param": "max_spread_width",
        "values": [100, 115, 125, 135, 150, 165, 175, 200, 225, 250, 300],
        "label_fmt": "{}pt",
        "live_value": 150,
        "description": "Max spread width cap (edge extension ↑)",
    },
}


def _run_single(args: Tuple) -> Dict:
    sweep_name, param_name, param_value, label = args
    from backtest.config import live_config
    from backtest.engine import run_backtest

    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    setattr(cfg, param_name, param_value)

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

    return {
        "sweep": sweep_name, "param": param_name,
        "value": param_value, "label": label,
        "days": n, "sharpe": round(sharpe, 4),
        "total_pnl": round(total, 2), "max_dd": round(dd, 2),
        "calmar": round(calmar, 4), "win_rate": round(wins / n * 100, 1) if n else 0,
        "placed": placed, "stops": stops,
        "stop_rate": round(stops / placed * 100, 1) if placed else 0,
    }


def main():
    console = Console()
    n_workers = min(8, os.cpu_count() or 4)

    tasks = []
    for sweep_name, sweep_cfg in SWEEPS.items():
        for val in sweep_cfg["values"]:
            label = sweep_cfg["label_fmt"].format(val)
            tasks.append((sweep_name, sweep_cfg["param"], val, label))

    total_tasks = len(tasks)
    console.print()
    console.print(Panel(
        f"[bold yellow]Max Spread Width Edge Check[/]\n"
        f"[dim]1-min · {n_workers} workers · {total_tasks} configs · 100pt–300pt[/]",
        box=box.ROUNDED, border_style="yellow", title="[bold white]⚡ Edge Check[/]"))
    console.print()

    sweep_results: List[Dict] = []
    sweep_best: Optional[Dict] = None
    completed = 0

    with Progress(
        SpinnerColumn(style="yellow"),
        TextColumn("[bold yellow]{task.description}"),
        BarColumn(bar_width=30, complete_style="yellow", finished_style="green"),
        MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        task = progress.add_task(f"max_spread_width", total=total_tasks)
        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(_run_single, tasks):
                sweep_results.append(result)
                completed += 1
                if sweep_best is None or result["sharpe"] > sweep_best["sharpe"]:
                    sweep_best = result
                progress.update(task, completed=completed,
                    description=f"max_spread_width  [green]best:{sweep_best['label']} Sh={sweep_best['sharpe']:.3f}[/]")

    results = sorted(sweep_results, key=lambda r: r["sharpe"], reverse=True)
    tbl = Table(title="[bold yellow]Max Spread Width Cap[/]",
                box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Value", width=8)
    tbl.add_column("Sharpe", justify="right", width=8)
    tbl.add_column("P&L", justify="right", width=10)
    tbl.add_column("MaxDD", justify="right", width=10)
    tbl.add_column("Calmar", justify="right", width=8)
    tbl.add_column("Win%", justify="right", width=6)
    tbl.add_column("Stops", justify="right", width=6)
    tbl.add_column("", width=14)

    for i, r in enumerate(results, 1):
        is_live = r["value"] == 150
        is_best = i == 1
        tag = ""
        if is_best and is_live: tag = "[bold green]◀ BEST+CURRENT[/]"
        elif is_best: tag = "[bold green]◀ BEST[/]"
        elif is_live: tag = "[cyan]◀ CURRENT[/]"
        style = "bold green" if is_best else ("cyan" if is_live else None)
        def fmt(val, s=style):
            return f"[{s}]{val}[/{s}]" if s else str(val)
        tbl.add_row(str(i), fmt(r['label']), fmt(f"{r['sharpe']:.3f}"),
            fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"),
            fmt(f"{r['calmar']:.3f}"), fmt(f"{r['win_rate']:.0f}%"),
            fmt(f"{r['stops']}"), tag)

    console.print()
    console.print(tbl)

    at_edge = results[0]["value"] == max(s["values"] for s in SWEEPS.values())
    verdict = "[bold red]STILL AT EDGE — need wider range[/]" if at_edge else "[bold green]✓ Peak found[/]"
    console.print(f"\n  {verdict}")
    console.print()

    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"sweep_max_spread_edge_{ts}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader(); w.writerows(results)
    console.print(f"  [dim]Results saved → {csv_path}[/]\n")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
