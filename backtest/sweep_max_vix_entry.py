"""
Sweep: max_vix_entry — skip ALL entries on days when VIX >= threshold.

None = disabled (current behaviour — always enter regardless of VIX).
Base config: live_config() — exact VM parameters.

Run: python -m backtest.sweep_max_vix_entry
"""
import csv
import statistics
from datetime import date, datetime as dt
from pathlib import Path
from typing import List, Optional

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

# None = disabled (no gate). Values = skip day when VIX >= threshold.
VIX_ENTRY_VALUES: List[Optional[float]] = [22, 24, 25, 26, 27, 28, 30, 35, None]

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 22)


def base_cfg(max_vix: Optional[float]) -> object:
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date   = END_DATE
    cfg.base_entry_downday_callonly_pct = 0.40
    cfg.max_vix_entry = max_vix
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    daily_pnls  = [r.net_pnl for r in results]
    total_pnl   = sum(daily_pnls)
    total_days  = len(results)
    win_days    = sum(1 for p in daily_pnls if p > 0)
    loss_days   = sum(1 for p in daily_pnls if p < 0)
    flat_days   = total_days - win_days - loss_days

    placed      = sum(r.entries_placed for r in results)
    skipped_days = sum(1 for r in results if r.entries_placed == 0)
    total_stops = sum(r.stops_hit for r in results)

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
        "label":        label,
        "days":         total_days,
        "win":          win_days,
        "loss":         loss_days,
        "flat":         flat_days,
        "win_rate":     win_days / total_days * 100 if total_days else 0,
        "total_pnl":    total_pnl,
        "mean_daily":   mean,
        "stdev_daily":  stdev,
        "sharpe":       sharpe,
        "max_dd":       max_dd,
        "calmar":       calmar,
        "placed":       placed,
        "skipped_days": skipped_days,
        "total_stops":  total_stops,
        "stop_rate":    total_stops / placed * 100 if placed else 0,
    }


METRICS = [
    ("Win rate %",          "win_rate",     "{:.1f}%"),
    ("Total net P&L",       "total_pnl",    "${:,.0f}"),
    ("Mean daily P&L",      "mean_daily",   "${:.2f}"),
    ("Sharpe (annualised)", "sharpe",       "{:.3f}"),
    ("Max drawdown",        "max_dd",       "${:,.0f}"),
    ("Calmar ratio",        "calmar",       "{:.3f}"),
    ("Days skipped (VIX)",  "skipped_days", "{:.0f}"),
    ("Total stops",         "total_stops",  "{:.0f}"),
    ("Stop rate %",         "stop_rate",    "{:.1f}%"),
]


if __name__ == "__main__":
    total     = len(VIX_ENTRY_VALUES)
    all_stats = []

    if _RICH:
        console = Console()
        console.print(f"\n[bold cyan]Sweep: max_vix_entry (skip ALL entries when VIX ≥ threshold)[/]")
        console.print(f"Base: live_config() — exact VM parameters")
        console.print(f"Values: {VIX_ENTRY_VALUES}  (None = disabled, current setting)")
        console.print(f"Period: {START_DATE} → {END_DATE}\n")

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
            for i, v in enumerate(VIX_ENTRY_VALUES, 1):
                label = "disabled" if v is None else f">={v}"
                progress.update(task, description=f"VIX gate = {label}  ({i}/{total})")
                results = run_backtest(base_cfg(v), verbose=False)
                all_stats.append(summarise(results, label))
                progress.advance(task)
    else:
        print(f"Sweep: max_vix_entry  |  Values: {VIX_ENTRY_VALUES}")
        for v in VIX_ENTRY_VALUES:
            label = "disabled" if v is None else f">={v}"
            print(f"  Running VIX skip >= {label}...")
            results = run_backtest(base_cfg(v), verbose=False)
            all_stats.append(summarise(results, label))

    # ── Table ─────────────────────────────────────────────────────────────────
    col_w = 10
    if _RICH:
        tbl = Table(title="max_vix_entry Sweep", box=box.SIMPLE_HEAVY,
                    show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=24)
        for s in all_stats:
            tbl.add_column(s["label"], justify="right", width=col_w)
        for metric, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            if key in ("sharpe", "total_pnl", "calmar", "win_rate"):
                bi = max(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[bi] = f"[bold green]{row_vals[bi]}[/]"
            if key in ("max_dd", "stop_rate"):
                bi = min(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[bi] = f"[bold green]{row_vals[bi]}[/]"
            tbl.add_row(metric, *row_vals)
        console.print(); console.print(tbl)
    else:
        header = f"  {'Metric':<24}"
        for s in all_stats:
            header += f"  {s['label']:>{col_w}}"
        print(); print(header)
        print("─" * (26 + (col_w + 2) * len(all_stats)))
        for metric, key, fmt in METRICS:
            row = f"  {metric:<24}"
            for s in all_stats:
                row += f"  {fmt.format(s[key]):>{col_w}}"
            print(row)

    best_sh  = max(range(len(all_stats)), key=lambda i: all_stats[i]["sharpe"])
    best_pnl = max(range(len(all_stats)), key=lambda i: all_stats[i]["total_pnl"])
    live_idx = next((i for i, s in enumerate(all_stats) if s["label"] == "disabled"), None)
    msg1 = f"\n  Best Sharpe:    VIX skip {all_stats[best_sh]['label']}  ({all_stats[best_sh]['sharpe']:.3f})"
    msg2 = f"  Best total P&L: VIX skip {all_stats[best_pnl]['label']}  (${all_stats[best_pnl]['total_pnl']:,.0f})"
    msg3 = f"  Live (disabled): Sharpe {all_stats[live_idx]['sharpe']:.3f}  P&L ${all_stats[live_idx]['total_pnl']:,.0f}" if live_idx is not None else ""
    if _RICH:
        console.print(msg1); console.print(msg2)
        if msg3: console.print(f"  [dim]{msg3.strip()}[/]")
        console.print()
    else:
        print(msg1); print(msg2)
        if msg3: print(msg3)

    # ── CSV ───────────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"max_vix_entry_sweep_{ts}.csv"
    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed", "skipped_days", "total_stops", "stop_rate"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader(); writer.writerows(all_stats)
    msg = f"  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
