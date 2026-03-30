"""
Targeted sweep: downday_theoretical_put_credit

All other parameters locked to optimal from 2026-03-24 768-combo sweep.
Finds the best theoretical put credit for call-only stop formula.

Stop formula: call_credit + downday_theoretical_put_credit + call_stop_buffer
Higher value = harder to stop out on call-only entries.

Run: python -m backtest.sweep_theo_put_credit
"""
import csv
import statistics
from datetime import date, datetime as dt
from pathlib import Path
from typing import List, Optional

from backtest.config import BacktestConfig, live_config
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

# Values to test: $0.50, $1.00, $1.50, $2.50, $5.00 (VM), $7.50, $10.00
# Stored as dollars × 100 in backtest
THEO_PUT_VALUES = [50, 100, 150, 175, 250, 500, 750, 1000]


def base_cfg() -> BacktestConfig:
    """Current live_config() — all confirmed optimal params, real Greeks strict mode."""
    cfg = live_config()
    cfg.start_date      = date(2022, 5, 16)
    cfg.end_date        = date(2026, 3, 27)
    cfg.use_real_greeks = True
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    total_pnl = sum(r.net_pnl for r in results)
    total_days = len(results)
    win_days = sum(1 for r in results if r.net_pnl > 0)
    loss_days = sum(1 for r in results if r.net_pnl < 0)

    total_stops = sum(
        sum(1 for e in r.entries if e.call_outcome == "stopped" or e.put_outcome == "stopped")
        for r in results
    )
    call_only_stops = sum(
        sum(1 for e in r.entries if e.call_outcome == "stopped" and e.entry_type == "call_only")
        for r in results
    )
    total_entries = sum(len(r.entries) for r in results)
    call_only_entries = sum(
        sum(1 for e in r.entries if e.entry_type == "call_only")
        for r in results
    )

    daily_pnls = [r.net_pnl for r in results]
    mean = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = (mean / stdev * (252 ** 0.5)) if stdev > 0 else 0

    max_dd = 0.0
    peak = 0.0
    cum = 0.0
    for p in daily_pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    calmar = (mean * 252 / max_dd) if max_dd > 0 else 0

    return {
        "label": label,
        "days": total_days,
        "win": win_days,
        "loss": loss_days,
        "win_rate": win_days / total_days * 100 if total_days else 0,
        "total_pnl": total_pnl,
        "mean_daily": mean,
        "stdev_daily": stdev,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "calmar": calmar,
        "total_entries": total_entries,
        "total_stops": total_stops,
        "stop_rate": total_stops / total_entries * 100 if total_entries else 0,
        "call_only_entries": call_only_entries,
        "call_only_stops": call_only_stops,
        "call_only_stop_rate": call_only_stops / call_only_entries * 100 if call_only_entries else 0,
    }


METRICS = [
    ("Win rate %",             "win_rate",            "{:.1f}%"),
    ("Total net P&L",          "total_pnl",           "${:,.0f}"),
    ("Mean daily P&L",         "mean_daily",          "${:.2f}"),
    ("Sharpe (annualised)",    "sharpe",              "{:.3f}"),
    ("Max drawdown",           "max_dd",              "${:,.0f}"),
    ("Calmar ratio",           "calmar",              "{:.3f}"),
    ("Total stops",            "total_stops",         "{:.0f}"),
    ("Stop rate %",            "stop_rate",           "{:.1f}%"),
    ("Call-only entries",      "call_only_entries",   "{:.0f}"),
    ("Call-only stops",        "call_only_stops",     "{:.0f}"),
    ("Call-only stop rate %",  "call_only_stop_rate", "{:.1f}%"),
]


if __name__ == "__main__":
    total = len(THEO_PUT_VALUES)
    all_stats = []

    if _RICH:
        console = Console()
        console.print(f"\n[bold cyan]Sweep: downday_theoretical_put_credit[/]")
        console.print(f"Stop formula: call_credit + theo_put + call_stop_buffer")
        console.print(f"Values ($): {[v/100 for v in THEO_PUT_VALUES]}")
        console.print(f"Period: 2022-05-16 → 2026-03-22  (965 trading days)\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[yellow]{task.percentage:.0f}%"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TextColumn("[magenta]ETA"),
            TimeRemainingColumn(),
            console=console,
            refresh_per_second=4,
        ) as progress:
            task = progress.add_task("Running", total=total)
            for i, v in enumerate(THEO_PUT_VALUES, 1):
                progress.update(task, description=f"${v/100:.2f}  ({i}/{total})")
                cfg = base_cfg()
                cfg.downday_theoretical_put_credit = float(v)
                results = run_backtest(cfg, verbose=False)
                all_stats.append(summarise(results, f"${v/100:.2f}"))
                progress.advance(task)
    else:
        print(f"Sweep: downday_theoretical_put_credit  |  Values: {[v/100 for v in THEO_PUT_VALUES]}")
        for v in THEO_PUT_VALUES:
            print(f"  Running ${v/100:.2f}...")
            cfg = base_cfg()
            cfg.downday_theoretical_put_credit = float(v)
            results = run_backtest(cfg, verbose=False)
            all_stats.append(summarise(results, f"${v/100:.2f}"))

    # ── Results table ─────────────────────────────────────────────────────────
    if _RICH:
        tbl = Table(title="Theoretical Put Credit Sweep", box=box.SIMPLE_HEAVY,
                    show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=26)
        for s in all_stats:
            tbl.add_column(s["label"], justify="right", width=10)

        for label, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            best_i = max(range(len(all_stats)), key=lambda i: all_stats[i][key])
            if key in ("sharpe", "total_pnl", "calmar", "win_rate"):
                row_vals[best_i] = f"[bold green]{row_vals[best_i]}[/]"
            if key in ("max_dd", "stop_rate", "call_only_stop_rate"):
                best_i = min(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[best_i] = f"[bold green]{row_vals[best_i]}[/]"
            tbl.add_row(label, *row_vals)

        console.print()
        console.print(tbl)
    else:
        col_w = 10
        header = f"  {'Metric':<26}"
        for s in all_stats:
            header += f"  {s['label']:>{col_w}}"
        print()
        print(header)
        print("─" * (28 + (col_w + 2) * len(all_stats)))
        for label, key, fmt in METRICS:
            row = f"  {label:<26}"
            for s in all_stats:
                row += f"  {fmt.format(s[key]):>{col_w}}"
            print(row)

    best_sharpe_idx = max(range(len(all_stats)), key=lambda i: all_stats[i]["sharpe"])
    best_pnl_idx    = max(range(len(all_stats)), key=lambda i: all_stats[i]["total_pnl"])
    if _RICH:
        console.print(f"\n  [bold]Best Sharpe:[/]    {all_stats[best_sharpe_idx]['label']}  "
                      f"({all_stats[best_sharpe_idx]['sharpe']:.3f})")
        console.print(f"  [bold]Best total P&L:[/] {all_stats[best_pnl_idx]['label']}  "
                      f"(${all_stats[best_pnl_idx]['total_pnl']:,.0f})")
    else:
        print(f"\n  Best Sharpe:    {all_stats[best_sharpe_idx]['label']}")
        print(f"  Best total P&L: {all_stats[best_pnl_idx]['label']}")

    # ── CSV export ────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"theo_put_credit_sweep_{ts}.csv"

    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "total_entries", "total_stops", "stop_rate",
                "call_only_entries", "call_only_stops", "call_only_stop_rate"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_stats)

    msg = f"\n  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
