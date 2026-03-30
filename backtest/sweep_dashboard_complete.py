"""
HYDRA Complete Gap Sweep — 1-Minute Resolution

All remaining missing and coarse-only parameters.

Run: python -m backtest.sweep_dashboard_complete
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
    "conditional_e6_dn": {
        "param": "conditional_e6_enabled",
        "values": [False, True],
        "label_fmt": "{}",
        "live_value": False,
        "description": "E6 downday call-only (ON/OFF)",
    },
    "conditional_e7_up": {
        "param": "conditional_upday_e7_enabled",
        "values": [False, True],
        "label_fmt": "{}",
        "live_value": False,
        "description": "E7 upday put-only (ON/OFF)",
    },
    "upday_threshold": {
        "param": "upday_threshold_pct",
        "values": [v / 100 for v in range(30, 80, 2)],  # 0.30%–0.78% in 0.02%
        "label_fmt": "{:.2f}%",
        "live_value": 0.60,
        "description": "E6 upday threshold (fine-grain)",
    },
    "downday_pct": {
        "param": "base_entry_downday_callonly_pct",
        "values": [v / 100 for v in range(45, 76, 2)],  # 0.45%–0.74% in 0.02%
        "label_fmt": "{:.2f}%",
        "live_value": 0.60,
        "description": "Down-day call-only threshold (fine)",
    },
    "min_put_credit": {
        "param": "min_put_credit",
        "values": [v / 100 for v in range(195, 240, 3)],  # $1.95–$2.37 in $0.03
        "label_fmt": "${:.2f}",
        "live_value": 2.15,
        "description": "Min put credit gate (fine)",
    },
    "put_credit_floor": {
        "param": "put_credit_floor",
        "values": [v / 100 for v in range(180, 215, 3)],  # $1.80–$2.12 in $0.03
        "label_fmt": "${:.2f}",
        "live_value": 2.00,
        "description": "MKT-029 put fallback floor (fine)",
    },
    "put_only_max_vix": {
        "param": "put_only_max_vix",
        "values": [10.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 20.0],
        "label_fmt": "VIX<{:.0f}",
        "live_value": 15.0,
        "description": "Put-only max VIX gate (fine)",
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
            if isinstance(val, bool):
                label = "ON" if val else "OFF"
            else:
                label = sweep_cfg["label_fmt"].format(val)
            tasks.append((sweep_name, sweep_cfg["param"], val, label))

    total_tasks = len(tasks)
    sweep_counts = {name: len(cfg["values"]) for name, cfg in SWEEPS.items()}

    sweep_lines = []
    for name, cfg in SWEEPS.items():
        n = len(cfg["values"])
        if isinstance(cfg["values"][0], bool):
            range_str = "ON/OFF"
        else:
            lo = cfg["label_fmt"].format(cfg["values"][0])
            hi = cfg["label_fmt"].format(cfg["values"][-1])
            range_str = f"{lo}–{hi}"
        if isinstance(cfg["live_value"], bool):
            live_str = "ON" if cfg["live_value"] else "OFF"
        else:
            live_str = cfg["label_fmt"].format(cfg["live_value"])
        sweep_lines.append(f"  [cyan]{name:<22}[/] {n:>3} configs  {range_str:<20}  [dim](current: {live_str})[/]")

    header = (
        f"[bold cyan]HYDRA Complete Gap Sweep[/]\n"
        f"[dim]1-minute resolution · real Greeks · {n_workers} parallel workers[/]\n\n"
        f"  Period  : [yellow]{START_DATE}[/] → [yellow]{END_DATE}[/]\n"
        f"  Configs : [white]{total_tasks}[/] total across [white]{len(SWEEPS)}[/] sweeps\n"
        f"  Workers : [white]{n_workers}[/] parallel processes\n\n"
        + "\n".join(sweep_lines)
    )
    console.print()
    console.print(Panel(header, box=box.ROUNDED, border_style="red",
                        title="[bold white]⚡ HYDRA Complete Check[/]"))
    console.print()

    sweep_progress = {name: 0 for name in SWEEPS}
    sweep_results: Dict[str, List[Dict]] = {name: [] for name in SWEEPS}
    sweep_best: Dict[str, Optional[Dict]] = {name: None for name in SWEEPS}
    completed = 0
    start_time = time.time()

    with Progress(
        SpinnerColumn(style="red"),
        TextColumn("[bold red]{task.description}"),
        BarColumn(bar_width=30, complete_style="red", finished_style="green"),
        MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        overall_task = progress.add_task(
            f"[bold]Overall  ({n_workers} workers)", total=total_tasks)
        sweep_tasks = {}
        for name in SWEEPS:
            sweep_tasks[name] = progress.add_task(
                f"  {name:<22}", total=sweep_counts[name])

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
                    description=f"  {sname:<22} [green]best:{best['label'][:12]} Sh={best['sharpe']:.3f}[/]")
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
        tbl.add_column("Value", width=10)
        tbl.add_column("Sharpe", justify="right", width=8)
        tbl.add_column("P&L", justify="right", width=10)
        tbl.add_column("MaxDD", justify="right", width=10)
        tbl.add_column("Calmar", justify="right", width=8)
        tbl.add_column("Win%", justify="right", width=6)
        tbl.add_column("Stops", justify="right", width=6)
        tbl.add_column("", width=14)

        for i, r in enumerate(results, 1):
            if isinstance(live_val, bool):
                is_live = (r["value"] == live_val)
            elif isinstance(r["value"], float) and isinstance(live_val, float):
                is_live = abs(r["value"] - live_val) < 0.001
            else:
                is_live = (r["value"] == live_val)
            is_best = i == 1
            tag = ""
            if is_best and is_live: tag = "[bold green]◀ BEST+CURRENT[/]"
            elif is_best: tag = "[bold green]◀ BEST[/]"
            elif is_live: tag = "[cyan]◀ CURRENT[/]"

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

    # Summary
    summary_tbl = Table(
        title="[bold]Complete Gap Sweep Summary[/]",
        box=box.ROUNDED, show_header=True, header_style="bold white",
        border_style="red",
    )
    summary_tbl.add_column("Parameter", style="cyan", width=25)
    summary_tbl.add_column("Current", justify="right", width=12)
    summary_tbl.add_column("Optimal", justify="right", width=12)
    summary_tbl.add_column("Opt Sharpe", justify="right", width=10)
    summary_tbl.add_column("Cur Sharpe", justify="right", width=10)
    summary_tbl.add_column("Delta", justify="right", width=8)
    summary_tbl.add_column("Action", width=14)

    for sname, cfg in SWEEPS.items():
        best = sweep_best[sname]
        live_val = cfg["live_value"]
        if isinstance(live_val, bool):
            live_label = "ON" if live_val else "OFF"
            is_same = (best["value"] == live_val)
        elif isinstance(live_val, float):
            live_label = cfg["label_fmt"].format(live_val)
            is_same = abs(best["value"] - live_val) < 0.001
        else:
            live_label = str(live_val)
            is_same = (best["value"] == live_val)

        live_result = next((r for r in sweep_results[sname]
                           if (isinstance(live_val, bool) and r["value"] == live_val) or
                              (isinstance(live_val, float) and abs(r["value"] - live_val) < 0.001)), None)
        live_sharpe = live_result["sharpe"] if live_result else 0
        delta = best["sharpe"] - live_sharpe
        action = "[green]✓ KEEP[/]" if is_same or delta < 0.02 else "[bold yellow]CHANGE[/]"
        delta_style = "green" if delta > 0.02 else ("red" if delta < -0.01 else "dim")
        summary_tbl.add_row(
            sname, live_label, best["label"],
            f"[bold]{best['sharpe']:.3f}[/]", f"{live_sharpe:.3f}",
            f"[{delta_style}]{delta:+.3f}[/{delta_style}]", action,
        )

    console.print(summary_tbl)

    console.print()
    console.print(Panel(
        f"  Total configs : [white]{total_tasks}[/]\n"
        f"  Workers       : [white]{n_workers}[/]\n"
        f"  Wall time     : [yellow]{elapsed/60:.1f} min[/]\n"
        f"  Avg per config: [dim]{elapsed/total_tasks:.1f}s[/]",
        title="[bold]Timing[/]", box=box.ROUNDED, border_style="dim",
    ))

    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    all_results = []
    for sname in SWEEPS: all_results.extend(sweep_results[sname])
    csv_path = out_dir / f"sweep_complete_gaps_1min_{ts}.csv"
    if all_results:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            w.writeheader(); w.writerows(all_results)
    console.print(f"\n  [dim]Results saved → {csv_path}[/]")
    console.print()


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
