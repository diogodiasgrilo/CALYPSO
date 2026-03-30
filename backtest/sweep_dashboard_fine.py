"""
HYDRA Fine-Grain Parameter Sweep — 1-Minute Resolution

Re-confirms optimal values with tighter step sizes around the 1-min sweep winners.

Run: python -m backtest.sweep_dashboard_fine
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

SWEEPS = {
    "put_stop_buffer": {
        "param": "put_stop_buffer",
        "values": [float(v) for v in range(120, 185, 5)],  # $1.20–$1.80 in $0.05
        "label_fmt": "${:.2f}",
        "label_div": 100,
        "live_value": 155.0,
        "description": "Put-side stop buffer (fine)",
    },
    "spread_vix_mult": {
        "param": "spread_vix_multiplier",
        "values": [v / 10 for v in range(45, 65)],  # 4.5–6.3 in 0.1
        "label_fmt": "{:.1f}x",
        "live_value": 5.5,
        "description": "VIX spread width multiplier (fine)",
    },
    "call_credit_floor": {
        "param": "call_credit_floor",
        "values": [v / 100 for v in range(85, 120, 5)],  # $0.85–$1.15 in $0.05
        "label_fmt": "${:.2f}",
        "live_value": 1.00,
        "description": "MKT-029 call fallback floor (fine)",
    },
    "min_call_credit": {
        "param": "min_call_credit",
        "values": [v / 100 for v in range(120, 155, 5)],  # $1.20–$1.50 in $0.05
        "label_fmt": "${:.2f}",
        "live_value": 1.35,
        "description": "Min call credit gate (fine)",
    },
    "downday_theo_put": {
        "param": "downday_theoretical_put_credit",
        "values": [float(v) for v in range(230, 310, 10)],  # $2.30–$3.00 in $0.10
        "label_fmt": "${:.2f}",
        "label_div": 100,
        "live_value": 270.0,
        "description": "Theo put for call-only stops (fine)",
    },
}

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)


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
    n = len(pnls)
    total = sum(pnls)
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
            div = sweep_cfg.get("label_div", 1)
            label = sweep_cfg["label_fmt"].format(val / div if div > 1 else val)
            tasks.append((sweep_name, sweep_cfg["param"], val, label))

    total_tasks = len(tasks)
    sweep_counts = {name: len(cfg["values"]) for name, cfg in SWEEPS.items()}

    sweep_lines = []
    for name, cfg in SWEEPS.items():
        n = len(cfg["values"])
        div = cfg.get("label_div", 1)
        lo = cfg["label_fmt"].format(cfg["values"][0] / div if div > 1 else cfg["values"][0])
        hi = cfg["label_fmt"].format(cfg["values"][-1] / div if div > 1 else cfg["values"][-1])
        live = cfg["label_fmt"].format(cfg["live_value"] / div if div > 1 else cfg["live_value"])
        sweep_lines.append(f"  [cyan]{name:<22}[/] {n:>3} configs  {lo}–{hi}  [dim](current: {live})[/]")

    header = (
        f"[bold cyan]HYDRA Fine-Grain Sweep[/]\n"
        f"[dim]1-minute resolution · real Greeks · {n_workers} parallel workers[/]\n\n"
        f"  Period  : [yellow]{START_DATE}[/] → [yellow]{END_DATE}[/]\n"
        f"  Configs : [white]{total_tasks}[/] total across [white]{len(SWEEPS)}[/] sweeps\n"
        f"  Workers : [white]{n_workers}[/] parallel processes\n\n"
        + "\n".join(sweep_lines)
    )
    console.print()
    console.print(Panel(header, box=box.ROUNDED, border_style="cyan",
                        title="[bold white]⚡ HYDRA Fine-Grain[/]"))
    console.print()

    sweep_progress = {name: 0 for name in SWEEPS}
    sweep_results: Dict[str, List[Dict]] = {name: [] for name in SWEEPS}
    sweep_best: Dict[str, Optional[Dict]] = {name: None for name in SWEEPS}
    completed = 0
    start_time = time.time()

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=30, complete_style="cyan", finished_style="green"),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        overall_task = progress.add_task(
            f"[bold]Overall  ({n_workers} workers)", total=total_tasks)
        sweep_tasks = {}
        for name, cfg in SWEEPS.items():
            sweep_tasks[name] = progress.add_task(
                f"  {name:<22}", total=len(cfg["values"]))

        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(_run_single, tasks):
                sname = result["sweep"]
                sweep_results[sname].append(result)
                sweep_progress[sname] += 1
                completed += 1
                if sweep_best[sname] is None or result["sharpe"] > sweep_best[sname]["sharpe"]:
                    sweep_best[sname] = result
                best = sweep_best[sname]
                progress.update(sweep_tasks[sname], completed=sweep_progress[sname],
                    description=f"  {sname:<22} [green]best:{best['label']} Sh={best['sharpe']:.3f}[/]")
                progress.update(overall_task, completed=completed,
                    description=f"[bold]Overall  [green]{completed}/{total_tasks}[/]  ({n_workers} workers)")

    elapsed = time.time() - start_time

    # Results tables
    console.print()
    for sname, cfg in SWEEPS.items():
        results = sorted(sweep_results[sname], key=lambda r: r["sharpe"], reverse=True)
        live_val = cfg["live_value"]

        tbl = Table(
            title=f"[bold yellow]{cfg['description']}[/]",
            box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow",
        )
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
            is_live = abs(r["value"] - live_val) < 0.001
            is_best = i == 1
            tag = ""
            if is_best and is_live:
                tag = "[bold green]◀ BEST+CURRENT[/]"
            elif is_best:
                tag = "[bold green]◀ BEST[/]"
            elif is_live:
                tag = "[cyan]◀ CURRENT[/]"

            style = "bold green" if is_best else ("cyan" if is_live else None)
            def fmt(val, s=style):
                return f"[{s}]{val}[/{s}]" if s else str(val)
            tbl.add_row(
                str(i), fmt(r['label']), fmt(f"{r['sharpe']:.3f}"),
                fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"),
                fmt(f"{r['calmar']:.3f}"), fmt(f"{r['win_rate']:.0f}%"),
                fmt(f"{r['stops']}"), tag,
            )

        console.print(tbl)
        console.print()

    # Grand summary
    summary_tbl = Table(
        title="[bold]Fine-Grain Summary — Optimal vs Current[/]",
        box=box.ROUNDED, show_header=True, header_style="bold white",
        border_style="green",
    )
    summary_tbl.add_column("Parameter", style="cyan", width=25)
    summary_tbl.add_column("Current", justify="right", width=10)
    summary_tbl.add_column("Optimal", justify="right", width=10)
    summary_tbl.add_column("Opt Sharpe", justify="right", width=10)
    summary_tbl.add_column("Cur Sharpe", justify="right", width=10)
    summary_tbl.add_column("Delta", justify="right", width=8)
    summary_tbl.add_column("Action", width=14)

    for sname, cfg in SWEEPS.items():
        best = sweep_best[sname]
        live_val = cfg["live_value"]
        div = cfg.get("label_div", 1)
        live_label = cfg["label_fmt"].format(live_val / div if div > 1 else live_val)
        live_result = next((r for r in sweep_results[sname]
                           if abs(r["value"] - live_val) < 0.001), None)
        live_sharpe = live_result["sharpe"] if live_result else 0
        delta = best["sharpe"] - live_sharpe
        changed = abs(best["value"] - live_val) > 0.001
        action = "[bold yellow]CHANGE[/]" if changed and delta > 0.02 else "[green]✓ KEEP[/]"
        delta_style = "green" if delta > 0 else ("red" if delta < -0.01 else "dim")
        summary_tbl.add_row(
            sname, live_label, best["label"],
            f"[bold]{best['sharpe']:.3f}[/]", f"{live_sharpe:.3f}",
            f"[{delta_style}]{delta:+.3f}[/{delta_style}]", action,
        )

    console.print(summary_tbl)

    # Timing
    console.print()
    console.print(Panel(
        f"  Total configs : [white]{total_tasks}[/]\n"
        f"  Workers       : [white]{n_workers}[/]\n"
        f"  Wall time     : [yellow]{elapsed/60:.1f} min[/]\n"
        f"  Avg per config: [dim]{elapsed/total_tasks:.1f}s[/]",
        title="[bold]Timing[/]", box=box.ROUNDED, border_style="dim",
    ))

    # Save CSV
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    all_results = []
    for sname in SWEEPS:
        all_results.extend(sweep_results[sname])
    csv_path = out_dir / f"sweep_fine_1min_{ts}.csv"
    if all_results:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            w.writeheader()
            w.writerows(all_results)
    console.print(f"\n  [dim]Results saved → {csv_path}[/]")
    console.print()


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
