"""
Sweep: put_only_max_vix (the VIX gate that blocks put-only entries).

When VIX >= this threshold, put-only entries (one-sided) are blocked.
Full ICs and call-only entries are unaffected.

Base config: live_config() — exact parameters running on HYDRA.
Only put_only_max_vix varies.

Run: python -m backtest.sweep_vix_gate
"""
import csv
import statistics
from datetime import date, datetime as dt
from pathlib import Path
from typing import List

from backtest.config import live_config
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

# Values to test — None = gate disabled (always allow put-only regardless of VIX)
VIX_GATE_VALUES = [15, 18, 20, 22, 23, 24, 25, 26, 27, 30, None]

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 22)


def base_cfg(vix_gate):
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date   = END_DATE
    cfg.base_entry_downday_callonly_pct = 0.40  # VM: 0.004 decimal = 0.4%
    cfg.put_only_max_vix = vix_gate if vix_gate is not None else 999.0
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    total_pnl   = sum(r.net_pnl for r in results)
    total_days  = len(results)
    win_days    = sum(1 for r in results if r.net_pnl > 0)
    loss_days   = sum(1 for r in results if r.net_pnl < 0)

    total_entries   = sum(len(r.entries) for r in results)
    placed_entries  = sum(r.entries_placed for r in results)
    total_stops     = sum(r.stops_hit for r in results)

    put_only_entries = sum(
        sum(1 for e in r.entries if e.entry_type == "put_only")
        for r in results
    )
    put_only_stops = sum(
        sum(1 for e in r.entries if e.entry_type == "put_only" and e.put_outcome == "stopped")
        for r in results
    )

    daily_pnls = [r.net_pnl for r in results]
    mean  = statistics.mean(daily_pnls) if daily_pnls else 0
    stdev = statistics.stdev(daily_pnls) if len(daily_pnls) > 1 else 0
    sharpe = mean / stdev * (252 ** 0.5) if stdev > 0 else 0

    peak = cum = max_dd = 0.0
    for p in daily_pnls:
        cum  += p
        peak  = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    calmar = mean * 252 / max_dd if max_dd > 0 else 0

    return {
        "label":             label,
        "days":              total_days,
        "win":               win_days,
        "loss":              loss_days,
        "win_rate":          win_days / total_days * 100 if total_days else 0,
        "total_pnl":         total_pnl,
        "mean_daily":        mean,
        "stdev_daily":       stdev,
        "sharpe":            sharpe,
        "max_dd":            max_dd,
        "calmar":            calmar,
        "placed_entries":    placed_entries,
        "total_stops":       total_stops,
        "stop_rate":         total_stops / placed_entries * 100 if placed_entries else 0,
        "put_only_entries":  put_only_entries,
        "put_only_stops":    put_only_stops,
        "put_only_stop_rate": put_only_stops / put_only_entries * 100 if put_only_entries else 0,
    }


METRICS = [
    ("Win rate %",           "win_rate",           "{:.1f}%"),
    ("Total net P&L",        "total_pnl",          "${:,.0f}"),
    ("Mean daily P&L",       "mean_daily",          "${:.2f}"),
    ("Sharpe (annualised)",  "sharpe",              "{:.3f}"),
    ("Max drawdown",         "max_dd",              "${:,.0f}"),
    ("Calmar ratio",         "calmar",              "{:.3f}"),
    ("Total stops",          "total_stops",         "{:.0f}"),
    ("Stop rate %",          "stop_rate",           "{:.1f}%"),
    ("Put-only entries",     "put_only_entries",    "{:.0f}"),
    ("Put-only stops",       "put_only_stops",      "{:.0f}"),
    ("Put-only stop rate %", "put_only_stop_rate",  "{:.1f}%"),
]


if __name__ == "__main__":
    total = len(VIX_GATE_VALUES)
    all_stats = []

    if _RICH:
        console = Console()
        console.print(f"\n[bold cyan]Sweep: put_only_max_vix (VIX gate for put-only entries)[/]")
        console.print(f"Base: live_config() — exact VM parameters")
        console.print(f"Values: {VIX_GATE_VALUES}  (None = gate disabled)")
        console.print(f"Period: {START_DATE} → {END_DATE}\n")

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
            for i, v in enumerate(VIX_GATE_VALUES, 1):
                label = "disabled" if v is None else str(v)
                progress.update(task, description=f"VIX gate = {label}  ({i}/{total})")
                results = run_backtest(base_cfg(v), verbose=False)
                all_stats.append(summarise(results, label))
                progress.advance(task)
    else:
        print(f"Sweep: put_only_max_vix  |  Values: {VIX_GATE_VALUES}")
        for v in VIX_GATE_VALUES:
            label = "disabled" if v is None else str(v)
            print(f"  Running VIX gate = {label}...")
            results = run_backtest(base_cfg(v), verbose=False)
            all_stats.append(summarise(results, label))

    # ── Results table ──────────────────────────────────────────────────────────
    col_w = 10
    if _RICH:
        tbl = Table(title="VIX Gate Sweep — put_only_max_vix",
                    box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=24)
        for s in all_stats:
            tbl.add_column(f"VIX<{s['label']}", justify="right", width=col_w)

        for label, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            # highlight best
            if key in ("sharpe", "total_pnl", "calmar", "win_rate"):
                best_i = max(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[best_i] = f"[bold green]{row_vals[best_i]}[/]"
            if key in ("max_dd", "stop_rate", "put_only_stop_rate"):
                best_i = min(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[best_i] = f"[bold green]{row_vals[best_i]}[/]"
            tbl.add_row(label, *row_vals)

        console.print()
        console.print(tbl)
    else:
        header = f"  {'Metric':<24}"
        for s in all_stats:
            lbl = f"VIX<{s['label']}"
            header += f"  {lbl:>{col_w}}"
        print()
        print(header)
        print("─" * (26 + (col_w + 2) * len(all_stats)))
        for label, key, fmt in METRICS:
            row = f"  {label:<24}"
            for s in all_stats:
                row += f"  {fmt.format(s[key]):>{col_w}}"
            print(row)

    best_sharpe_idx = max(range(len(all_stats)), key=lambda i: all_stats[i]["sharpe"])
    best_pnl_idx    = max(range(len(all_stats)), key=lambda i: all_stats[i]["total_pnl"])
    if _RICH:
        console.print(f"\n  [bold]Best Sharpe:[/]    VIX < [green]{all_stats[best_sharpe_idx]['label']}[/]  "
                      f"({all_stats[best_sharpe_idx]['sharpe']:.3f})")
        console.print(f"  [bold]Best total P&L:[/] VIX < [green]{all_stats[best_pnl_idx]['label']}[/]  "
                      f"(${all_stats[best_pnl_idx]['total_pnl']:,.0f})")
        console.print(f"\n  [dim]Live config uses VIX < 25 — "
                      f"Sharpe {all_stats[next(i for i,s in enumerate(all_stats) if s['label']=='25')]['sharpe']:.3f}[/]\n")
    else:
        print(f"\n  Best Sharpe:    VIX < {all_stats[best_sharpe_idx]['label']}  ({all_stats[best_sharpe_idx]['sharpe']:.3f})")
        print(f"  Best total P&L: VIX < {all_stats[best_pnl_idx]['label']}  (${all_stats[best_pnl_idx]['total_pnl']:,.0f})")
        live_idx = next((i for i, s in enumerate(all_stats) if s["label"] == "25"), None)
        if live_idx is not None:
            print(f"  Live config (VIX < 25) Sharpe: {all_stats[live_idx]['sharpe']:.3f}")

    # ── CSV export ─────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"vix_gate_sweep_{ts}.csv"

    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed_entries", "total_stops", "stop_rate",
                "put_only_entries", "put_only_stops", "put_only_stop_rate"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_stats)

    msg = f"\n  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
