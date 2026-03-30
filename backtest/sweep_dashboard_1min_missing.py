"""
HYDRA Missing Parameter Sweep — 1-Minute Resolution

Re-runs all parameters that were only tested on 5-min data.

Run: python -m backtest.sweep_dashboard_1min_missing
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

# Entry timing: regenerate the grid from the original sweep
def _generate_entry_schedules():
    starts = [600, 615, 630, 645, 660]  # 10:00–11:00
    intervals = [15, 20, 30, 45, 60]
    nums = [3, 4, 5, 6, 7]
    cutoff = 810  # 13:30
    schedules = []
    for s in starts:
        for iv in intervals:
            for n in nums:
                last = s + (n - 1) * iv
                if last <= cutoff:
                    times = [f"{(s + i*iv)//60}:{(s + i*iv)%60:02d}" for i in range(n)]
                    label = f"s{s//60}:{s%60:02d}_i{iv}m_n{n}"
                    schedules.append((label, times))
    return schedules

ENTRY_SCHEDULES = _generate_entry_schedules()

SWEEPS = {
    "entry_times": {
        "param": "_entry_times",  # special handling
        "values": list(range(len(ENTRY_SCHEDULES))),
        "label_fmt": "{}",
        "live_value": -1,  # matched by schedule
        "description": f"Entry timing ({len(ENTRY_SCHEDULES)} combos)",
    },
    "conditional_e7": {
        "param": "conditional_e7_enabled",
        "values": [False, True],
        "label_fmt": "{}",
        "live_value": False,
        "description": "E7 downday call-only (ON/OFF)",
    },
    "conditional_e6up": {
        "param": "conditional_upday_e6_enabled",
        "values": [False, True],
        "label_fmt": "{}",
        "live_value": False,
        "description": "E6 upday put-only (ON/OFF)",
    },
    "fomc_t1": {
        "param": "fomc_t1_callonly_enabled",
        "values": [False, True],
        "label_fmt": "{}",
        "live_value": True,
        "description": "FOMC T+1 call-only (ON/OFF)",
    },
    "one_sided": {
        "param": "one_sided_entries_enabled",
        "values": [False, True],
        "label_fmt": "{}",
        "live_value": True,
        "description": "One-sided entries (ON/OFF)",
    },
    "price_stop": {
        "param": "price_based_stop_points",
        "values": [None, 0.0, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0],
        "label_fmt": "{}",
        "live_value": None,
        "description": "Price-based stop (None=credit-based)",
    },
    "put_credit_floor": {
        "param": "put_credit_floor",
        "values": [v / 100 for v in range(150, 230, 5)],
        "label_fmt": "${:.2f}",
        "live_value": 2.15,
        "description": "MKT-029 put fallback floor",
    },
    "put_only_max_vix": {
        "param": "put_only_max_vix",
        "values": [15.0, 18.0, 20.0, 22.0, 25.0, 30.0, 35.0, 999.0],
        "label_fmt": "{}",
        "live_value": 25.0,
        "description": "Put-only max VIX gate",
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

    # Special handling for entry_times
    if param_name == "_entry_times":
        idx = param_value
        _, times = ENTRY_SCHEDULES[idx]
        cfg.entry_times = times
    elif param_name == "price_based_stop_points":
        cfg.price_based_stop_points = param_value
    else:
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

    # Build task list
    tasks = []
    for sweep_name, sweep_cfg in SWEEPS.items():
        for val in sweep_cfg["values"]:
            if sweep_name == "entry_times":
                idx = val
                sched_label, times = ENTRY_SCHEDULES[idx]
                label = sched_label
                is_live = (times == ["10:15", "10:45", "11:15"])
            elif sweep_name == "price_stop":
                label = "credit" if val is None else f"{val:.1f}pt"
                is_live = (val is None)
            elif isinstance(val, bool):
                label = "ON" if val else "OFF"
            elif isinstance(val, float) and sweep_name in ("put_only_max_vix",):
                label = f"VIX<{val:.0f}" if val < 999 else "disabled"
            else:
                div = sweep_cfg.get("label_div", 1)
                label = sweep_cfg["label_fmt"].format(val / div if div > 1 else val)
            tasks.append((sweep_name, sweep_cfg["param"], val, label))

    total_tasks = len(tasks)
    sweep_counts = {name: len(cfg["values"]) for name, cfg in SWEEPS.items()}

    # Header
    sweep_lines = []
    for name, cfg in SWEEPS.items():
        n = len(cfg["values"])
        sweep_lines.append(f"  [cyan]{name:<22}[/] {n:>3} configs  [dim]{cfg['description']}[/]")

    header = (
        f"[bold cyan]HYDRA Missing Parameters — 1-Min Retest[/]\n"
        f"[dim]1-minute resolution · real Greeks · {n_workers} parallel workers[/]\n\n"
        f"  Period  : [yellow]{START_DATE}[/] → [yellow]{END_DATE}[/]\n"
        f"  Configs : [white]{total_tasks}[/] total across [white]{len(SWEEPS)}[/] sweeps\n"
        f"  Workers : [white]{n_workers}[/] parallel processes\n\n"
        + "\n".join(sweep_lines)
    )
    console.print()
    console.print(Panel(header, box=box.ROUNDED, border_style="magenta",
                        title="[bold white]⚡ HYDRA 1-Min Retest[/]"))
    console.print()

    sweep_progress = {name: 0 for name in SWEEPS}
    sweep_results: Dict[str, List[Dict]] = {name: [] for name in SWEEPS}
    sweep_best: Dict[str, Optional[Dict]] = {name: None for name in SWEEPS}
    completed = 0
    start_time = time.time()

    with Progress(
        SpinnerColumn(style="magenta"),
        TextColumn("[bold magenta]{task.description}"),
        BarColumn(bar_width=30, complete_style="magenta", finished_style="green"),
        MofNCompleteColumn(),
        TaskProgressColumn(),
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
                    description=f"  {sname:<22} [green]best:{best['label'][:15]} Sh={best['sharpe']:.3f}[/]")
                progress.update(overall_task, completed=completed,
                    description=f"[bold]Overall  [green]{completed}/{total_tasks}[/]  ({n_workers} workers)")

    elapsed = time.time() - start_time

    # Results tables (top 15 for entry_times, full for others)
    console.print()
    for sname, cfg in SWEEPS.items():
        results = sorted(sweep_results[sname], key=lambda r: r["sharpe"], reverse=True)
        live_val = cfg["live_value"]
        max_show = 15 if sname == "entry_times" else len(results)

        tbl = Table(
            title=f"[bold yellow]{cfg['description']}[/]",
            box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow",
        )
        tbl.add_column("#", style="dim", width=3)
        tbl.add_column("Value", width=28 if sname == "entry_times" else 12)
        tbl.add_column("Sharpe", justify="right", width=8)
        tbl.add_column("P&L", justify="right", width=10)
        tbl.add_column("MaxDD", justify="right", width=10)
        tbl.add_column("Calmar", justify="right", width=8)
        tbl.add_column("Win%", justify="right", width=6)
        tbl.add_column("Stops", justify="right", width=6)
        tbl.add_column("", width=14)

        for i, r in enumerate(results[:max_show], 1):
            # Determine if this is the live value
            if sname == "entry_times":
                is_live = (r["label"] == "s10:15_i30m_n3")
            elif sname == "price_stop":
                is_live = (r["value"] is None)
            elif isinstance(live_val, bool):
                is_live = (r["value"] == live_val)
            else:
                is_live = (r["value"] is not None and live_val is not None
                          and abs(float(r["value"]) - float(live_val)) < 0.001)
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

        # For entry_times, show live rank
        if sname == "entry_times":
            live_r = next((r for r in results if r["label"] == "s10:15_i30m_n3"), None)
            if live_r:
                rank = next(i for i, r in enumerate(results, 1) if r["label"] == live_r["label"])
                tbl.caption = f"Live config rank: #{rank}/{len(results)}"

        console.print(tbl)
        console.print()

    # Grand summary
    summary_tbl = Table(
        title="[bold]1-Min Retest Summary[/]",
        box=box.ROUNDED, show_header=True, header_style="bold white",
        border_style="magenta",
    )
    summary_tbl.add_column("Parameter", style="cyan", width=25)
    summary_tbl.add_column("Current", justify="right", width=16)
    summary_tbl.add_column("1-Min Best", justify="right", width=16)
    summary_tbl.add_column("Best Sharpe", justify="right", width=10)
    summary_tbl.add_column("Action", width=14)

    for sname, cfg in SWEEPS.items():
        best = sweep_best[sname]
        if sname == "entry_times":
            current_label = "10:15,10:45,11:15"
            is_same = (best["label"] == "s10:15_i30m_n3")
        elif isinstance(cfg["live_value"], bool):
            current_label = "ON" if cfg["live_value"] else "OFF"
            is_same = (best["value"] == cfg["live_value"])
        elif cfg["live_value"] is None:
            current_label = "None"
            is_same = (best["value"] is None)
        else:
            current_label = str(cfg["live_value"])
            is_same = best["value"] is not None and abs(float(best["value"]) - float(cfg["live_value"])) < 0.001
        action = "[green]✓ CONFIRMED[/]" if is_same else "[bold yellow]CHANGED[/]"
        summary_tbl.add_row(
            sname, current_label, best["label"],
            f"[bold]{best['sharpe']:.3f}[/]", action,
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
    csv_path = out_dir / f"sweep_1min_missing_{ts}.csv"
    if all_results:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            w.writeheader(); w.writerows(all_results)
    console.print(f"\n  [dim]Results saved → {csv_path}[/]")
    console.print()


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
