"""
Sweep: min_call_credit and min_put_credit (credit gate — MKT-011)

Tests each dimension independently using real-Greeks strict mode.
Part 1: vary min_call_credit (hold min_put_credit at live value 2.25)
Part 2: vary min_put_credit (hold min_call_credit at live value 1.25)

Current live values: min_call_credit=1.25, min_put_credit=2.25

Run: python -m backtest.sweep_credit_gate
"""
import csv
import statistics
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import live_config, BacktestConfig
from backtest.engine import run_backtest, DayResult

try:
    from rich.console import Console
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                               SpinnerColumn, TextColumn, TimeElapsedColumn,
                               TimeRemainingColumn)
    from rich.table import Table
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)

# Part 1: vary min_call_credit, hold min_put_credit=2.25
CALL_CREDIT_VALUES = [0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00]

# Part 2: vary min_put_credit, hold min_call_credit=1.25
PUT_CREDIT_VALUES  = [1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00]


def build_cfg(min_call: float, min_put: float) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date      = START_DATE
    cfg.end_date        = END_DATE
    cfg.use_real_greeks = True
    cfg.min_call_credit = min_call
    cfg.min_put_credit  = min_put
    # floor is always min - $0.10
    cfg.call_credit_floor = max(0.10, min_call - 0.10)
    cfg.put_credit_floor  = max(0.10, min_put  - 0.10)
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    daily_pnls  = [r.net_pnl for r in results]
    total_pnl   = sum(daily_pnls)
    total_days  = len(results)
    win_days    = sum(1 for p in daily_pnls if p > 0)
    loss_days   = sum(1 for p in daily_pnls if p < 0)

    placed      = sum(r.entries_placed for r in results)
    skipped     = sum(r.entries_skipped for r in results)
    total_stops = sum(r.stops_hit for r in results)

    full_ic   = sum(sum(1 for e in r.entries if e.entry_type == "full_ic")   for r in results)
    call_only = sum(sum(1 for e in r.entries if e.entry_type == "call_only") for r in results)
    put_only  = sum(sum(1 for e in r.entries if e.entry_type == "put_only")  for r in results)

    call_credits = [e.call_credit for r in results for e in r.entries if e.call_credit > 0]
    put_credits  = [e.put_credit  for r in results for e in r.entries if e.put_credit  > 0]
    avg_call_credit = statistics.mean(call_credits) if call_credits else 0
    avg_put_credit  = statistics.mean(put_credits)  if put_credits  else 0

    mean   = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev  = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum  += p
        peak  = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    call_stops = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops  = sum(sum(1 for e in r.entries if e.put_outcome  == "stopped") for r in results)

    return {
        "label":           label,
        "days":            total_days,
        "win":             win_days,
        "loss":            loss_days,
        "win_rate":        win_days / total_days * 100 if total_days else 0,
        "total_pnl":       total_pnl,
        "mean_daily":      mean,
        "stdev_daily":     stdev,
        "sharpe":          sharpe,
        "max_dd":          max_dd,
        "calmar":          calmar,
        "placed":          placed,
        "skipped":         skipped,
        "total_stops":     total_stops,
        "stop_rate":       total_stops / placed * 100 if placed else 0,
        "call_stops":      call_stops,
        "put_stops":       put_stops,
        "full_ic":         full_ic,
        "call_only":       call_only,
        "put_only":        put_only,
        "avg_call_credit": avg_call_credit,
        "avg_put_credit":  avg_put_credit,
    }


METRICS = [
    ("Win rate %",          "win_rate",        "{:.1f}%"),
    ("Total net P&L",       "total_pnl",       "${:,.0f}"),
    ("Mean daily P&L",      "mean_daily",      "${:.2f}"),
    ("Sharpe (annualised)", "sharpe",          "{:.3f}"),
    ("Max drawdown",        "max_dd",          "${:,.0f}"),
    ("Calmar ratio",        "calmar",          "{:.3f}"),
    ("Placed",              "placed",          "{:.0f}"),
    ("Skipped",             "skipped",         "{:.0f}"),
    ("Total stops",         "total_stops",     "{:.0f}"),
    ("Stop rate %",         "stop_rate",       "{:.1f}%"),
    ("Full IC",             "full_ic",         "{:.0f}"),
    ("Call-only",           "call_only",       "{:.0f}"),
    ("Put-only",            "put_only",        "{:.0f}"),
    ("Avg call credit",     "avg_call_credit", "${:.2f}"),
    ("Avg put credit",      "avg_put_credit",  "${:.2f}"),
]

HIGHER_IS_BETTER = {"win_rate", "total_pnl", "sharpe", "calmar", "mean_daily",
                    "placed", "full_ic", "avg_call_credit", "avg_put_credit"}
LOWER_IS_BETTER  = {"max_dd", "stop_rate", "skipped", "total_stops",
                    "call_stops", "put_stops"}


def run_section(title: str, configs: list, console=None) -> list:
    all_stats = []
    total = len(configs)

    if _RICH:
        console.print(f"\n[bold yellow]{title}[/]")
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[yellow]{task.percentage:.0f}%"),
            TextColumn("•"), TimeElapsedColumn(),
            TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
            console=console, refresh_per_second=4,
        ) as progress:
            task = progress.add_task("Running", total=total)
            for i, (label, min_call, min_put) in enumerate(configs, 1):
                progress.update(task, description=f"{label}  ({i}/{total})")
                results = run_backtest(build_cfg(min_call, min_put), verbose=False)
                all_stats.append(summarise(results, label))
                progress.advance(task)
    else:
        print(title)
        for label, min_call, min_put in configs:
            print(f"  Running {label}...")
            results = run_backtest(build_cfg(min_call, min_put), verbose=False)
            all_stats.append(summarise(results, label))

    col_w = 11
    if _RICH:
        tbl = Table(title=title, box=box.SIMPLE_HEAVY,
                    show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=22)
        for s in all_stats:
            tbl.add_column(s["label"], justify="right", width=col_w)
        for metric, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            if key in HIGHER_IS_BETTER:
                bi = max(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[bi] = f"[bold green]{row_vals[bi]}[/]"
            elif key in LOWER_IS_BETTER:
                bi = min(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[bi] = f"[bold green]{row_vals[bi]}[/]"
            tbl.add_row(metric, *row_vals)
        console.print(); console.print(tbl)
    else:
        header = f"  {'Metric':<22}"
        for s in all_stats:
            header += f"  {s['label']:>{col_w}}"
        print(); print(header)
        print("─" * (24 + (col_w + 2) * len(all_stats)))
        for metric, key, fmt in METRICS:
            row = f"  {metric:<22}"
            for s in all_stats:
                row += f"  {fmt.format(s[key]):>{col_w}}"
            print(row)

    best_sh = max(range(len(all_stats)), key=lambda i: all_stats[i]["sharpe"])
    msg = f"\n  Best Sharpe: {all_stats[best_sh]['label']}  ({all_stats[best_sh]['sharpe']:.3f})"
    console.print(msg) if _RICH else print(msg)

    return all_stats


if __name__ == "__main__":
    console = Console() if _RICH else None

    if _RICH:
        console.print("\n[bold cyan]Sweep: Credit Gate (MKT-011) — Real Greeks, Honest Engine[/]")
        console.print(f"Base: live_config()  |  Period: {START_DATE} → {END_DATE}")
        console.print(f"[green]Real Greeks strict mode[/]  |  [dim]Engine: long_bid=0 fix applied[/]\n")
    else:
        print(f"Sweep: Credit Gate | Real Greeks | {START_DATE} → {END_DATE}\n")

    call_configs = [
        (f"call=${v:.2f} (put=2.25)", v, 2.25) for v in CALL_CREDIT_VALUES
    ]
    put_configs = [
        (f"put=${v:.2f} (call=1.25)", 1.25, v) for v in PUT_CREDIT_VALUES
    ]

    call_stats = run_section("Part 1: min_call_credit sweep (min_put_credit=2.25 fixed)", call_configs, console)
    put_stats  = run_section("Part 2: min_put_credit sweep (min_call_credit=1.25 fixed)", put_configs, console)

    # ── CSV ────────────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"credit_gate_sweep_{ts}.csv"
    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed", "skipped", "total_stops", "stop_rate",
                "call_stops", "put_stops", "full_ic", "call_only", "put_only",
                "avg_call_credit", "avg_put_credit"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(call_stats + put_stats)
    msg = f"\n  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
