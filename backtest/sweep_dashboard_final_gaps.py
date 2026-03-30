"""
HYDRA Final 2 Gaps — E6 time edge check + max_spread_width fine-grain

Run: python -m backtest.sweep_dashboard_final_gaps
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


def _run_single(args: Tuple) -> Dict:
    sweep_name, overrides, label = args
    from backtest.config import live_config
    from backtest.engine import run_backtest

    cfg = live_config()
    cfg.start_date = START_DATE; cfg.end_date = END_DATE
    cfg.use_real_greeks = True; cfg.data_resolution = "1min"

    for k, v in overrides.items():
        if k == "conditional_entry_times":
            cfg.conditional_entry_times = v
        else:
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

    breach = 0
    for r in results:
        dc = 0
        for e in r.entries:
            if e.entry_type == 'skipped': continue
            if e.entry_type == 'full_ic': dc += max(e.call_spread_width, e.put_spread_width) * 100
            elif e.entry_type == 'call_only': dc += e.call_spread_width * 100
            elif e.entry_type == 'put_only': dc += e.put_spread_width * 100
        if dc > ACCOUNT: breach += 1

    return {
        "sweep": sweep_name, "label": label,
        "days": n, "sharpe": round(sharpe, 4),
        "total_pnl": round(total, 2), "max_dd": round(dd, 2),
        "calmar": round(calmar, 4), "win_rate": round(wins / n * 100, 1) if n else 0,
        "placed": placed, "stops": stops,
        "breach_days": breach, "breach_pct": round(breach / n * 100, 1),
    }


def main():
    console = Console()
    n_workers = min(8, os.cpu_count() or 4)

    tasks = []

    # Gap 1: E6 time edge check (14:00 was at edge, test 14:15-15:00)
    for t in ["13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00"]:
        tasks.append(("e6_time", {"conditional_entry_times": [t, "13:15"]}, t))

    # Gap 2: max_spread_width fine-grain (105-125 in 1pt steps)
    for w in range(105, 126):
        tasks.append(("max_spread", {"max_spread_width": w}, f"{w}pt"))

    total_tasks = len(tasks)
    sweep_counts = {"e6_time": 7, "max_spread": 21}

    console.print()
    console.print(Panel(
        f"[bold cyan]HYDRA Final 2 Gaps[/]\n"
        f"[dim]1-min · {n_workers} workers · {total_tasks} configs[/]\n\n"
        f"  [cyan]e6_time      [/]  7 configs  13:30–15:00  [dim](current: 14:00, at edge)[/]\n"
        f"  [cyan]max_spread   [/] 21 configs  105–125pt in 1pt  [dim](current: 114pt)[/]",
        box=box.ROUNDED, border_style="cyan", title="[bold white]⚡ Final Gaps[/]"))
    console.print()

    sweep_progress = {"e6_time": 0, "max_spread": 0}
    sweep_results: Dict[str, List[Dict]] = {"e6_time": [], "max_spread": []}
    sweep_best: Dict[str, Optional[Dict]] = {"e6_time": None, "max_spread": None}
    completed = 0
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
        overall_task = progress.add_task(f"[bold]Overall ({n_workers} workers)", total=total_tasks)
        sweep_tasks = {
            "e6_time": progress.add_task("  e6_time", total=7),
            "max_spread": progress.add_task("  max_spread", total=21),
        }

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
                    description=f"  {sname:<14} [green]best:{best['label']} Sh={best['sharpe']:.3f}[/]")
                progress.update(overall_task, completed=completed)

    elapsed = time.time() - start_time

    # Results tables
    console.print()
    for sname, title in [("e6_time", "E6 Upday Time (edge check ↑)"), ("max_spread", "Max Spread Width (fine-grain)")]:
        results = sorted(sweep_results[sname], key=lambda r: r["sharpe"], reverse=True)

        current_label = "14:00" if sname == "e6_time" else "114pt"

        tbl = Table(title=f"[bold yellow]{title}[/]",
                    box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
        tbl.add_column("#", style="dim", width=3)
        tbl.add_column("Value", width=8)
        tbl.add_column("Sharpe", justify="right", width=8)
        tbl.add_column("P&L", justify="right", width=10)
        tbl.add_column("MaxDD", justify="right", width=10)
        tbl.add_column("Calmar", justify="right", width=8)
        tbl.add_column("Breach", justify="right", width=6)
        tbl.add_column("Breach%", justify="right", width=7)
        tbl.add_column("", width=14)

        for i, r in enumerate(results, 1):
            is_current = r["label"] == current_label
            is_best = i == 1
            at_edge = (r["label"] == results[0]["label"] and
                      (r["label"] == "15:00" or r["label"] == "125pt" or r["label"] == "105pt"))
            tag = ""
            if is_best and is_current: tag = "[bold green]◀ BEST+CURRENT[/]"
            elif is_best: tag = "[bold green]◀ BEST[/]"
            elif is_current: tag = "[cyan]◀ CURRENT[/]"

            style = "bold green" if is_best else ("cyan" if is_current else None)
            def fmt(val, s=style):
                return f"[{s}]{val}[/{s}]" if s else str(val)
            breach_style = "red" if r["breach_pct"] > 5 else ("yellow" if r["breach_pct"] > 0 else "green")
            tbl.add_row(
                str(i), fmt(r['label']), fmt(f"{r['sharpe']:.3f}"),
                fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"),
                fmt(f"{r['calmar']:.3f}"),
                f"[{breach_style}]{r['breach_days']}[/{breach_style}]",
                f"[{breach_style}]{r['breach_pct']:.1f}%[/{breach_style}]",
                tag,
            )

        # Check if best is at edge
        best = results[0]
        if sname == "e6_time" and best["label"] == "15:00":
            tbl.caption = "[bold red]⚠ STILL AT EDGE — need later times[/]"
        elif sname == "max_spread" and best["label"] in ("105pt", "125pt"):
            tbl.caption = "[bold red]⚠ STILL AT EDGE — need wider range[/]"
        else:
            tbl.caption = "[bold green]✓ Peak found (not at edge)[/]"

        console.print(tbl)
        console.print()

    # Summary
    summary_tbl = Table(title="[bold]Final Gaps Summary[/]",
                        box=box.ROUNDED, header_style="bold white", border_style="cyan")
    summary_tbl.add_column("Parameter", style="cyan", width=20)
    summary_tbl.add_column("Current", justify="right", width=10)
    summary_tbl.add_column("Optimal", justify="right", width=10)
    summary_tbl.add_column("Sharpe", justify="right", width=8)
    summary_tbl.add_column("At Edge?", width=12)
    summary_tbl.add_column("Action", width=14)

    for sname in ["e6_time", "max_spread"]:
        best = sweep_best[sname]
        current = "14:00" if sname == "e6_time" else "114pt"
        is_edge = (sname == "e6_time" and best["label"] == "15:00") or \
                  (sname == "max_spread" and best["label"] in ("105pt", "125pt"))
        is_same = best["label"] == current
        edge_str = "[bold red]YES[/]" if is_edge else "[green]No[/]"
        action = "[green]✓ KEEP[/]" if is_same else ("[bold yellow]CHANGE[/]" if not is_edge else "[bold red]EXTEND[/]")
        summary_tbl.add_row(sname, current, best["label"], f"{best['sharpe']:.3f}", edge_str, action)

    console.print(summary_tbl)

    console.print()
    console.print(Panel(
        f"  Total configs : [white]{total_tasks}[/]\n"
        f"  Workers       : [white]{n_workers}[/]\n"
        f"  Wall time     : [yellow]{elapsed/60:.1f} min[/]",
        title="[bold]Timing[/]", box=box.ROUNDED, border_style="dim"))

    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    all_results = []
    for sname in sweep_results: all_results.extend(sweep_results[sname])
    csv_path = out_dir / f"sweep_final_gaps_1min_{ts}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
        w.writeheader(); w.writerows(all_results)
    console.print(f"\n  [dim]Results saved → {csv_path}[/]\n")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
