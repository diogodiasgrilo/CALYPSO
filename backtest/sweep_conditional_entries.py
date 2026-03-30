"""
Sweep: Conditional entry on/off combinations (E6/E7)

Tests E7 (downday call-only at 13:15) and E6-upday (upday put-only at 12:45)
in all meaningful on/off combinations using real-Greeks strict mode.

Current live config: E7=ON, E6-upday=ON, E6-downday=OFF, E7-upday=OFF

Run: python -m backtest.sweep_conditional_entries
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

# (label, e7_downday, e6_upday, e6_downday, e7_upday)
# Focusing on E7-downday and E6-upday (the two that are live)
# e6_downday and e7_upday stay OFF (never enabled live)
CONFIGS = [
    ("E7=OFF  E6up=OFF  (baseline)",   False, False, False, False),
    ("E7=ON   E6up=OFF  (E7 only)",    True,  False, False, False),
    ("E7=OFF  E6up=ON   (E6up only)",  False, True,  False, False),
    ("E7=ON   E6up=ON   (LIVE)",       True,  True,  False, False),
    ("E7=ON   E6up=ON   E6dn=ON",      True,  True,  True,  False),
    ("E7=ON   E6up=ON   E7up=ON",      True,  True,  False, True),
    ("ALL ON",                          True,  True,  True,  True),
]


def build_cfg(e7_dn: bool, e6_up: bool, e6_dn: bool, e7_up: bool) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date                 = START_DATE
    cfg.end_date                   = END_DATE
    cfg.use_real_greeks            = True
    cfg.conditional_e7_enabled     = e7_dn
    cfg.conditional_upday_e6_enabled = e6_up
    cfg.conditional_e6_enabled     = e6_dn
    cfg.conditional_upday_e7_enabled = e7_up
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
        "label":       label,
        "days":        total_days,
        "win":         win_days,
        "loss":        loss_days,
        "win_rate":    win_days / total_days * 100 if total_days else 0,
        "total_pnl":   total_pnl,
        "mean_daily":  mean,
        "stdev_daily": stdev,
        "sharpe":      sharpe,
        "max_dd":      max_dd,
        "calmar":      calmar,
        "placed":      placed,
        "skipped":     skipped,
        "total_stops": total_stops,
        "stop_rate":   total_stops / placed * 100 if placed else 0,
        "call_stops":  call_stops,
        "put_stops":   put_stops,
        "full_ic":     full_ic,
        "call_only":   call_only,
        "put_only":    put_only,
    }


METRICS = [
    ("Win rate %",          "win_rate",    "{:.1f}%"),
    ("Total net P&L",       "total_pnl",   "${:,.0f}"),
    ("Mean daily P&L",      "mean_daily",  "${:.2f}"),
    ("Sharpe (annualised)", "sharpe",      "{:.3f}"),
    ("Max drawdown",        "max_dd",      "${:,.0f}"),
    ("Calmar ratio",        "calmar",      "{:.3f}"),
    ("Placed",              "placed",      "{:.0f}"),
    ("Skipped",             "skipped",     "{:.0f}"),
    ("Total stops",         "total_stops", "{:.0f}"),
    ("Stop rate %",         "stop_rate",   "{:.1f}%"),
    ("Full IC",             "full_ic",     "{:.0f}"),
    ("Call-only",           "call_only",   "{:.0f}"),
    ("Put-only",            "put_only",    "{:.0f}"),
]

HIGHER_IS_BETTER = {"win_rate", "total_pnl", "sharpe", "calmar", "mean_daily", "placed", "full_ic"}
LOWER_IS_BETTER  = {"max_dd", "stop_rate", "skipped", "total_stops"}


if __name__ == "__main__":
    total     = len(CONFIGS)
    all_stats = []
    console   = Console() if _RICH else None

    if _RICH:
        console.print("\n[bold cyan]Sweep: Conditional Entries (E6/E7) — Real Greeks, Honest Engine[/]")
        console.print(f"Base: live_config()  |  Period: {START_DATE} → {END_DATE}")
        console.print(f"[green]Real Greeks strict mode[/]  |  Current live: E7-downday=ON, E6-upday=ON\n")

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
            for i, (label, e7_dn, e6_up, e6_dn, e7_up) in enumerate(CONFIGS, 1):
                progress.update(task, description=f"{label[:30]}  ({i}/{total})")
                results = run_backtest(build_cfg(e7_dn, e6_up, e6_dn, e7_up), verbose=False)
                all_stats.append(summarise(results, label))
                progress.advance(task)
    else:
        print(f"Sweep: Conditional Entries | Real Greeks | {START_DATE} → {END_DATE}")
        for label, e7_dn, e6_up, e6_dn, e7_up in CONFIGS:
            print(f"  Running {label}...")
            results = run_backtest(build_cfg(e7_dn, e6_up, e6_dn, e7_up), verbose=False)
            all_stats.append(summarise(results, label))

    # ── Table ──────────────────────────────────────────────────────────────────
    col_w = 12
    if _RICH:
        tbl = Table(title="Conditional Entry Combinations",
                    box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=22)
        for s in all_stats:
            lbl = s["label"].split("(")[-1].rstrip(")")  # short label for column
            tbl.add_column(lbl, justify="right", width=col_w)
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
            header += f"  {s['label'][:col_w]:>{col_w}}"
        print(); print(header)
        print("─" * (24 + (col_w + 2) * len(all_stats)))
        for metric, key, fmt in METRICS:
            row = f"  {metric:<22}"
            for s in all_stats:
                row += f"  {fmt.format(s[key]):>{col_w}}"
            print(row)

    best_sh  = max(range(len(all_stats)), key=lambda i: all_stats[i]["sharpe"])
    best_pnl = max(range(len(all_stats)), key=lambda i: all_stats[i]["total_pnl"])
    live_idx = next((i for i, s in enumerate(all_stats) if "LIVE" in s["label"]), None)

    msgs = [
        f"\n  Best Sharpe:  {all_stats[best_sh]['label']}  ({all_stats[best_sh]['sharpe']:.3f})",
        f"  Best P&L:     {all_stats[best_pnl]['label']}  (${all_stats[best_pnl]['total_pnl']:,.0f})",
    ]
    if live_idx is not None:
        s = all_stats[live_idx]
        msgs.append(f"  Live config:  Sharpe={s['sharpe']:.3f}  P&L=${s['total_pnl']:,.0f}  MaxDD=${s['max_dd']:,.0f}")
    for m in msgs:
        console.print(m) if _RICH else print(m)
    if _RICH:
        console.print()

    # ── CSV ────────────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"conditional_entries_sweep_{ts}.csv"
    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed", "skipped", "total_stops", "stop_rate",
                "call_stops", "put_stops", "full_ic", "call_only", "put_only"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_stats)
    msg = f"  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
