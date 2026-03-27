"""
Sweep: spread_vix_multiplier — VIX-scaled spread width formula.

Formula: round(VIX × mult / 5) × 5, floor=call/put_min_spread_width, cap=max_spread_width
All runs use: floor=25pt, cap=100pt, real-Greeks strict mode.

Tests mult values 2.0–5.5 plus fixed-50pt baseline for reference.

Bug note: Engine was fixed 2026-03-26 so that stops fire correctly when
long leg bid=$0 (was silently disabling stops on far-OTM longs). Results
here reflect the honest engine.

Run: python -m backtest.sweep_spread_vix_mult
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
END_DATE   = date(2026, 3, 27)   # full dataset as of 2026-03-27
FLOOR      = 25
CAP        = 100

# (label, mult, floor, cap)  — None mult = fixed 50pt baseline
CONFIGS = [
    ("fixed 50pt",  None,  50,    50),
    ("mult=2.0",    2.0,   FLOOR, CAP),
    ("mult=2.5",    2.5,   FLOOR, CAP),
    ("mult=3.0",    3.0,   FLOOR, CAP),
    ("mult=3.5",    3.5,   FLOOR, CAP),
    ("mult=4.0",    4.0,   FLOOR, CAP),
    ("mult=4.5",    4.5,   FLOOR, CAP),
    ("mult=5.0",    5.0,   FLOOR, CAP),
    ("mult=5.5",    5.5,   FLOOR, CAP),
]


def build_cfg(mult, floor, cap) -> BacktestConfig:
    cfg = live_config()
    cfg.start_date      = START_DATE
    cfg.end_date        = END_DATE
    cfg.use_real_greeks = True
    if mult is None:
        # Fixed 50pt baseline
        cfg.call_min_spread_width = 50
        cfg.put_min_spread_width  = 50
        cfg.max_spread_width      = 50
    else:
        cfg.spread_vix_multiplier = mult
        cfg.call_min_spread_width = floor
        cfg.put_min_spread_width  = floor
        cfg.max_spread_width      = cap
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    daily_pnls  = [r.net_pnl for r in results]
    total_pnl   = sum(daily_pnls)
    total_days  = len(results)
    win_days    = sum(1 for p in daily_pnls if p > 0)
    loss_days   = sum(1 for p in daily_pnls if p < 0)

    placed      = sum(r.entries_placed for r in results)
    total_stops = sum(r.stops_hit for r in results)
    call_stops  = sum(sum(1 for e in r.entries if e.call_outcome == "stopped") for r in results)
    put_stops   = sum(sum(1 for e in r.entries if e.put_outcome  == "stopped") for r in results)

    call_widths = [e.call_spread_width for r in results for e in r.entries
                   if e.entry_type != "skipped" and e.call_spread_width > 0]
    put_widths  = [e.put_spread_width  for r in results for e in r.entries
                   if e.entry_type != "skipped" and e.put_spread_width  > 0]
    avg_call_width = statistics.mean(call_widths) if call_widths else 0
    avg_put_width  = statistics.mean(put_widths)  if put_widths  else 0

    mean   = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev  = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum  += p
        peak  = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    return {
        "label":          label,
        "days":           total_days,
        "win":            win_days,
        "loss":           loss_days,
        "win_rate":       win_days / total_days * 100 if total_days else 0,
        "total_pnl":      total_pnl,
        "mean_daily":     mean,
        "stdev_daily":    stdev,
        "sharpe":         sharpe,
        "max_dd":         max_dd,
        "calmar":         calmar,
        "placed":         placed,
        "total_stops":    total_stops,
        "stop_rate":      total_stops / placed * 100 if placed else 0,
        "call_stops":     call_stops,
        "put_stops":      put_stops,
        "avg_call_width": avg_call_width,
        "avg_put_width":  avg_put_width,
    }


METRICS = [
    ("Win rate %",          "win_rate",       "{:.1f}%"),
    ("Total net P&L",       "total_pnl",      "${:,.0f}"),
    ("Mean daily P&L",      "mean_daily",     "${:.2f}"),
    ("Sharpe (annualised)", "sharpe",         "{:.3f}"),
    ("Max drawdown",        "max_dd",         "${:,.0f}"),
    ("Calmar ratio",        "calmar",         "{:.3f}"),
    ("Total stops",         "total_stops",    "{:.0f}"),
    ("Stop rate %",         "stop_rate",      "{:.1f}%"),
    ("Call-side stops",     "call_stops",     "{:.0f}"),
    ("Put-side stops",      "put_stops",      "{:.0f}"),
    ("Avg call width pt",   "avg_call_width", "{:.1f}pt"),
    ("Avg put width pt",    "avg_put_width",  "{:.1f}pt"),
]

HIGHER_IS_BETTER = {"win_rate", "total_pnl", "sharpe", "calmar", "mean_daily"}
LOWER_IS_BETTER  = {"max_dd", "stop_rate", "total_stops", "call_stops", "put_stops"}


if __name__ == "__main__":
    total     = len(CONFIGS)
    all_stats = []

    if _RICH:
        console = Console()
        console.print(f"\n[bold cyan]Sweep: spread_vix_multiplier (honest engine, real Greeks)[/]")
        console.print(f"Formula: round(VIX × mult / 5) × 5, floor={FLOOR}pt, cap={CAP}pt")
        console.print(f"Period: {START_DATE} → {END_DATE}  |  [green]Real Greeks strict[/]\n")

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
            for i, (label, mult, floor, cap) in enumerate(CONFIGS, 1):
                progress.update(task, description=f"{label}  ({i}/{total})")
                results = run_backtest(build_cfg(mult, floor, cap), verbose=False)
                all_stats.append(summarise(results, label))
                progress.advance(task)
    else:
        print(f"Sweep: spread_vix_multiplier | floor={FLOOR} cap={CAP} | Real Greeks")
        print(f"Period: {START_DATE} → {END_DATE}\n")
        for label, mult, floor, cap in CONFIGS:
            print(f"  Running {label}...")
            results = run_backtest(build_cfg(mult, floor, cap), verbose=False)
            all_stats.append(summarise(results, label))

    # ── Table ──────────────────────────────────────────────────────────────────
    col_w = 11
    if _RICH:
        tbl = Table(title="VIX Multiplier Sweep — Honest Engine (long_bid=0 fix)",
                    box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
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

    # ── Summary ────────────────────────────────────────────────────────────────
    best_sh  = max(range(len(all_stats)), key=lambda i: all_stats[i]["sharpe"])
    best_pnl = max(range(len(all_stats)), key=lambda i: all_stats[i]["total_pnl"])
    live_idx = next((i for i, s in enumerate(all_stats) if s["label"] == "mult=4.0"), None)

    msgs = [
        f"\n  Best Sharpe:    {all_stats[best_sh]['label']}   Sharpe={all_stats[best_sh]['sharpe']:.3f}",
        f"  Best P&L:       {all_stats[best_pnl]['label']}   P&L=${all_stats[best_pnl]['total_pnl']:,.0f}",
    ]
    if live_idx is not None:
        s = all_stats[live_idx]
        msgs.append(f"  Live (mult=4.0): Sharpe={s['sharpe']:.3f}  P&L=${s['total_pnl']:,.0f}  MaxDD=${s['max_dd']:,.0f}")

    for m in msgs:
        console.print(m) if _RICH else print(m)
    if _RICH:
        console.print()

    # ── CSV ────────────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"spread_vix_mult_sweep_{ts}.csv"
    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed", "total_stops", "stop_rate", "call_stops", "put_stops",
                "avg_call_width", "avg_put_width"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_stats)
    msg = f"  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
