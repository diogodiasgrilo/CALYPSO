"""
HYDRA Protection Features Sweep — 1-Minute Resolution

Tests 4 protection features individually and combined.

Run: python -m backtest.sweep_dashboard_protections
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
    "daily_loss_limit": {
        "param": "daily_loss_limit",
        "values": [None, -500.0, -750.0, -1000.0, -1500.0, -2000.0, -3000.0],
        "label_fn": lambda v: "OFF" if v is None else f"${v:.0f}",
        "live_value": None,
        "description": "Daily loss limit (skip remaining entries)",
    },
    "spread_cap": {
        "param": "spread_value_cap_at_stop",
        "values": [False, True],
        "label_fn": lambda v: "ON" if v else "OFF",
        "live_value": False,
        "description": "Cap spread close cost at width×100",
    },
    "vix_spike": {
        "param": "vix_spike_skip_points",
        "values": [None, 3.0, 5.0, 7.0, 10.0],
        "label_fn": lambda v: "OFF" if v is None else f"+{v:.0f}pt",
        "live_value": None,
        "description": "VIX spike gate (skip if VIX > open + X)",
    },
    "whipsaw": {
        "param": "whipsaw_range_skip_mult",
        "values": [None, 1.5, 2.0, 2.5, 3.0],
        "label_fn": lambda v: "OFF" if v is None else f"{v:.1f}×EM",
        "live_value": None,
        "description": "Anti-whipsaw (skip if range > X × expected move)",
    },
}


def _run_single(args: Tuple) -> Dict:
    sweep_name, param_name, param_value, label = args
    from backtest.config import live_config
    from backtest.engine import run_backtest

    cfg = live_config()
    cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
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
    worst_day = min(pnls) if pnls else 0

    return {
        "sweep": sweep_name, "param": param_name,
        "value": param_value, "label": label,
        "days": n, "sharpe": round(sharpe, 4),
        "total_pnl": round(total, 2), "max_dd": round(dd, 2),
        "calmar": round(calmar, 4), "win_rate": round(wins / n * 100, 1) if n else 0,
        "placed": placed, "stops": stops, "worst_day": round(worst_day, 2),
    }


def _run_combo(args: Tuple) -> Dict:
    """Run a combination of protections."""
    label, overrides = args
    from backtest.config import live_config
    from backtest.engine import run_backtest

    cfg = live_config()
    cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"
    for k, v in overrides.items():
        setattr(cfg, k, v)

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
        "label": label, "sharpe": round(sharpe, 4),
        "total_pnl": round(total, 2), "max_dd": round(dd, 2),
        "calmar": round(calmar, 4), "worst_day": round(worst_day, 2),
        "placed": placed, "stops": stops,
    }


def main():
    console = Console()
    n_workers = min(8, os.cpu_count() or 4)

    # Individual sweep tasks
    tasks = []
    for sweep_name, sweep_cfg in SWEEPS.items():
        for val in sweep_cfg["values"]:
            label = sweep_cfg["label_fn"](val)
            tasks.append((sweep_name, sweep_cfg["param"], val, label))

    # Combination tasks
    combos = [
        ("BASELINE (no protections)", {}),
        ("Spread cap only", {"spread_value_cap_at_stop": True}),
        ("Loss limit -$1000", {"daily_loss_limit": -1000.0}),
        ("Loss limit -$1000 + cap", {"daily_loss_limit": -1000.0, "spread_value_cap_at_stop": True}),
        ("VIX spike +5 + cap", {"vix_spike_skip_points": 5.0, "spread_value_cap_at_stop": True}),
        ("Whipsaw 2× + cap", {"whipsaw_range_skip_mult": 2.0, "spread_value_cap_at_stop": True}),
        ("ALL: limit -$1000 + cap + VIX +5 + whip 2×", {
            "daily_loss_limit": -1000.0, "spread_value_cap_at_stop": True,
            "vix_spike_skip_points": 5.0, "whipsaw_range_skip_mult": 2.0,
        }),
        ("LIGHT: cap + VIX +7", {
            "spread_value_cap_at_stop": True, "vix_spike_skip_points": 7.0,
        }),
        ("MEDIUM: cap + limit -$1500 + VIX +5", {
            "spread_value_cap_at_stop": True, "daily_loss_limit": -1500.0,
            "vix_spike_skip_points": 5.0,
        }),
    ]

    total_tasks = len(tasks) + len(combos)

    console.print()
    console.print(Panel(
        f"[bold cyan]HYDRA Protection Features Sweep[/]\n"
        f"[dim]1-min · {n_workers} workers · {total_tasks} configs[/]\n\n"
        f"  Individual sweeps : [white]{len(tasks)}[/] configs (4 features)\n"
        f"  Combinations      : [white]{len(combos)}[/] configs\n"
        f"  Total             : [white]{total_tasks}[/]",
        box=box.ROUNDED, border_style="cyan",
        title="[bold white]⚡ Protection Features[/]"))
    console.print()

    # Run individual sweeps
    sweep_results: Dict[str, List[Dict]] = {name: [] for name in SWEEPS}
    all_individual = []
    completed = 0

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=28, complete_style="cyan", finished_style="green"),
        MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        task = progress.add_task(f"Individual sweeps ({n_workers} workers)", total=len(tasks))
        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(_run_single, tasks):
                all_individual.append(result)
                sweep_results[result["sweep"]].append(result)
                completed += 1
                progress.update(task, completed=completed)

    # Run combos
    combo_results = []
    with Progress(
        SpinnerColumn(style="yellow"),
        TextColumn("[bold yellow]{task.description}"),
        BarColumn(bar_width=28, complete_style="yellow", finished_style="green"),
        MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        task = progress.add_task(f"Combinations ({n_workers} workers)", total=len(combos))
        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(_run_combo, combos):
                combo_results.append(result)
                progress.update(task, completed=len(combo_results))

    # Individual results tables
    console.print()
    for sname, cfg in SWEEPS.items():
        results = sorted(sweep_results[sname], key=lambda r: r["sharpe"], reverse=True)

        tbl = Table(title=f"[bold yellow]{cfg['description']}[/]",
                    box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
        tbl.add_column("#", style="dim", width=3)
        tbl.add_column("Value", width=10)
        tbl.add_column("Sharpe", justify="right", width=8)
        tbl.add_column("P&L", justify="right", width=10)
        tbl.add_column("MaxDD", justify="right", width=10)
        tbl.add_column("Calmar", justify="right", width=8)
        tbl.add_column("Worst Day", justify="right", width=10)
        tbl.add_column("Placed", justify="right", width=7)
        tbl.add_column("", width=14)

        for i, r in enumerate(results, 1):
            is_current = str(r["value"]) == str(cfg["live_value"])
            is_best = i == 1
            tag = ""
            if is_best and is_current: tag = "[bold green]◀ BEST+CUR[/]"
            elif is_best: tag = "[bold green]◀ BEST[/]"
            elif is_current: tag = "[cyan]◀ CUR[/]"
            style = "bold green" if is_best else ("cyan" if is_current else None)
            def fmt(val, s=style):
                return f"[{s}]{val}[/{s}]" if s else str(val)
            tbl.add_row(str(i), fmt(r['label']), fmt(f"{r['sharpe']:.3f}"),
                fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"),
                fmt(f"{r['calmar']:.3f}"), fmt(f"${r['worst_day']:,.0f}"),
                fmt(f"{r['placed']}"), tag)

        console.print(tbl)
        console.print()

    # Combo results
    combo_results.sort(key=lambda r: r["sharpe"], reverse=True)
    ctbl = Table(title="[bold yellow]Combinations[/]",
                 box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
    ctbl.add_column("#", style="dim", width=3)
    ctbl.add_column("Config", width=45)
    ctbl.add_column("Sharpe", justify="right", width=8)
    ctbl.add_column("P&L", justify="right", width=10)
    ctbl.add_column("MaxDD", justify="right", width=10)
    ctbl.add_column("Calmar", justify="right", width=8)
    ctbl.add_column("Worst Day", justify="right", width=10)

    for i, r in enumerate(combo_results, 1):
        is_baseline = "BASELINE" in r["label"]
        style = "bold green" if i == 1 else ("cyan" if is_baseline else None)
        def fmt(val, s=style):
            return f"[{s}]{val}[/{s}]" if s else str(val)
        ctbl.add_row(str(i), fmt(r['label']), fmt(f"{r['sharpe']:.3f}"),
            fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"),
            fmt(f"{r['calmar']:.3f}"), fmt(f"${r['worst_day']:,.0f}"))

    console.print(ctbl)

    console.print()
    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"sweep_protections_1min_{ts}.csv"
    all_data = all_individual + [{"sweep": "combo", **r} for r in combo_results]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_data[0].keys()), extrasaction="ignore")
        w.writeheader(); w.writerows(all_data)
    console.print(f"  [dim]Results saved → {csv_path}[/]\n")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
