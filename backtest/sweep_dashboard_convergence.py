"""
HYDRA Convergence Test — 1-Minute Resolution

Tight sweep of ALL 12 non-trivial parameters in one round.
If current values are all within 0.01 Sharpe of #1 → converged.

Run: python -m backtest.sweep_dashboard_convergence
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
    "spread_vix_mult": {
        "param": "spread_vix_multiplier",
        "values": [v / 10 for v in range(48, 59)],
        "label_fmt": "{:.1f}x",
        "live_value": 5.3,
    },
    "max_spread_width": {
        "param": "max_spread_width",
        "values": list(range(77, 94)),
        "label_fmt": "{}pt",
        "live_value": 83,
    },
    "min_call_credit": {
        "param": "min_call_credit",
        "values": [v / 100 for v in range(115, 160, 5)],
        "label_fmt": "${:.2f}",
        "live_value": 1.35,
    },
    "min_put_credit": {
        "param": "min_put_credit",
        "values": sorted(set([v / 100 for v in range(195, 225, 3)] + [2.10])),
        "label_fmt": "${:.2f}",
        "live_value": 2.10,
    },
    "call_credit_floor": {
        "param": "call_credit_floor",
        "values": [v / 100 for v in range(60, 95, 5)],
        "label_fmt": "${:.2f}",
        "live_value": 0.75,
    },
    "put_credit_floor": {
        "param": "put_credit_floor",
        "values": sorted(set([v / 100 for v in range(192, 222, 3)] + [2.07])),
        "label_fmt": "${:.2f}",
        "live_value": 2.07,
    },
    "call_stop_buffer": {
        "param": "call_stop_buffer",
        "values": [float(v) for v in range(25, 45)],
        "label_fmt": "${:.2f}",
        "label_div": 100,
        "live_value": 35.0,
    },
    "put_stop_buffer": {
        "param": "put_stop_buffer",
        "values": [float(v) for v in range(130, 180, 5)],
        "label_fmt": "${:.2f}",
        "label_div": 100,
        "live_value": 155.0,
    },
    "downday_theo_put": {
        "param": "downday_theoretical_put_credit",
        "values": [float(v) for v in range(220, 310, 10)],
        "label_fmt": "${:.2f}",
        "label_div": 100,
        "live_value": 260.0,
    },
    "downday_pct": {
        "param": "base_entry_downday_callonly_pct",
        "values": sorted(set([v / 100 for v in range(44, 66, 2)] + [0.57])),
        "label_fmt": "{:.2f}%",
        "live_value": 0.57,
    },
    "upday_threshold": {
        "param": "upday_threshold_pct",
        "values": [v / 100 for v in range(38, 60, 2)],
        "label_fmt": "{:.2f}%",
        "live_value": 0.48,
    },
    "put_only_max_vix": {
        "param": "put_only_max_vix",
        "values": [float(v) for v in range(12, 19)],
        "label_fmt": "VIX<{:.0f}",
        "live_value": 15.0,
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

    return {
        "sweep": sweep_name, "param": param_name,
        "value": param_value, "label": label,
        "days": n, "sharpe": round(sharpe, 4),
        "total_pnl": round(total, 2), "max_dd": round(dd, 2),
        "calmar": round(calmar, 4), "win_rate": round(wins / n * 100, 1) if n else 0,
        "placed": placed, "stops": stops,
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
        sweep_lines.append(f"  [cyan]{name:<22}[/] {n:>3} configs  {lo}–{hi}  [dim]({live})[/]")

    header = (
        f"[bold white]HYDRA Convergence Test[/]\n"
        f"[dim]All 12 params · tight ranges · 1-min · {n_workers} workers[/]\n\n"
        f"  Configs : [white]{total_tasks}[/] total\n"
        f"  Rule    : current within [white]0.01 Sharpe[/] of #1 → [green]CONVERGED[/]\n"
        f"            shifted > [white]0.02 Sharpe[/] → [yellow]NEEDS UPDATE[/]\n\n"
        + "\n".join(sweep_lines)
    )
    console.print()
    console.print(Panel(header, box=box.DOUBLE, border_style="white",
                        title="[bold white]⚡ CONVERGENCE TEST[/]"))
    console.print()

    sweep_progress = {name: 0 for name in SWEEPS}
    sweep_results: Dict[str, List[Dict]] = {name: [] for name in SWEEPS}
    sweep_best: Dict[str, Optional[Dict]] = {name: None for name in SWEEPS}
    completed = 0
    start_time = time.time()

    with Progress(
        SpinnerColumn(style="white"),
        TextColumn("[bold white]{task.description}"),
        BarColumn(bar_width=28, complete_style="white", finished_style="green"),
        MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        overall_task = progress.add_task(f"[bold]Overall ({n_workers} workers)", total=total_tasks)
        sweep_tasks = {}
        for name in SWEEPS:
            sweep_tasks[name] = progress.add_task(f"  {name:<22}", total=sweep_counts[name])

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
                    description=f"  {sname:<22} [green]best:{best['label'][:10]} Sh={best['sharpe']:.3f}[/]")
                progress.update(overall_task, completed=completed)

    elapsed = time.time() - start_time

    # Results — compact: top 5 per sweep
    console.print()
    converged_count = 0
    shifted_count = 0
    shifts = []

    for sname, cfg in SWEEPS.items():
        results = sorted(sweep_results[sname], key=lambda r: r["sharpe"], reverse=True)
        live_val = cfg["live_value"]
        best = results[0]

        # Find current result
        live_result = None
        for r in results:
            if isinstance(live_val, float) and abs(r["value"] - live_val) < 0.001:
                live_result = r; break
            elif isinstance(live_val, int) and r["value"] == live_val:
                live_result = r; break

        live_sharpe = live_result["sharpe"] if live_result else 0
        delta = best["sharpe"] - live_sharpe
        is_converged = delta < 0.02

        if is_converged:
            converged_count += 1
            status = "[green]✓ CONVERGED[/]"
        else:
            shifted_count += 1
            status = f"[bold yellow]SHIFT → {best['label']}[/]"
            shifts.append((sname, cfg["live_value"], best["value"], best["label"], delta))

        tbl = Table(box=box.SIMPLE, show_header=True, header_style="dim",
                    title=f"[bold]{sname}[/] {status}  (Δ={delta:+.3f})")
        tbl.add_column("#", style="dim", width=3)
        tbl.add_column("Value", width=10)
        tbl.add_column("Sharpe", justify="right", width=8)
        tbl.add_column("P&L", justify="right", width=10)
        tbl.add_column("MaxDD", justify="right", width=9)
        tbl.add_column("", width=14)

        for i, r in enumerate(results[:5], 1):
            if isinstance(live_val, float):
                is_live = abs(r["value"] - live_val) < 0.001
            else:
                is_live = r["value"] == live_val
            is_best = i == 1
            tag = ""
            if is_best and is_live: tag = "[bold green]◀ BEST+CUR[/]"
            elif is_best: tag = "[bold green]◀ BEST[/]"
            elif is_live: tag = "[cyan]◀ CUR[/]"
            style = "bold green" if is_best else ("cyan" if is_live else None)
            def fmt(val, s=style):
                return f"[{s}]{val}[/{s}]" if s else str(val)
            tbl.add_row(str(i), fmt(r['label']), fmt(f"{r['sharpe']:.3f}"),
                fmt(f"${r['total_pnl']:,.0f}"), fmt(f"${r['max_dd']:,.0f}"), tag)

        console.print(tbl)

    # Grand verdict
    console.print()
    if shifted_count == 0:
        verdict = (
            f"[bold green]ALL 12 PARAMETERS CONVERGED[/]\n\n"
            f"  Every parameter's current value is within 0.01 Sharpe of its optimal.\n"
            f"  No further optimization needed. Config is stable."
        )
        border = "green"
    else:
        shift_lines = "\n".join(f"  [yellow]{s[0]}[/]: {s[1]} → {s[3]} (Δ={s[4]:+.3f})" for s in shifts)
        verdict = (
            f"[bold yellow]{shifted_count} PARAMETER(S) SHIFTED[/]  |  "
            f"[green]{converged_count} converged[/]\n\n"
            f"{shift_lines}\n\n"
            f"  Update these and run convergence test again."
        )
        border = "yellow"

    console.print(Panel(verdict, title="[bold]Convergence Verdict[/]",
                        box=box.DOUBLE, border_style=border))

    console.print()
    console.print(Panel(
        f"  Total configs : [white]{total_tasks}[/]\n"
        f"  Workers       : [white]{n_workers}[/]\n"
        f"  Wall time     : [yellow]{elapsed/60:.1f} min[/]\n"
        f"  Avg per config: [dim]{elapsed/total_tasks:.1f}s[/]",
        title="[bold]Timing[/]", box=box.ROUNDED, border_style="dim"))

    out_dir = Path("backtest/results"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    all_results = []
    for sname in SWEEPS: all_results.extend(sweep_results[sname])
    csv_path = out_dir / f"sweep_convergence_1min_{ts}.csv"
    if all_results:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            w.writeheader(); w.writerows(all_results)
    console.print(f"\n  [dim]Results saved → {csv_path}[/]\n")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
