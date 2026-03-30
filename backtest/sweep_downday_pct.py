"""
Sweep: base_entry_downday_callonly_pct — % SPX drop from open that forces
E1-E5 base entries to call-only (no put side).

None = disabled (full IC regardless of direction).
0.40 = current VM setting (0.4% drop triggers call-only).
Higher = less sensitive (more drops pass through as full IC).
Lower  = more sensitive (smaller drops force call-only).

Base config: live_config() — exact VM parameters.

Run: python -m backtest.sweep_downday_pct
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

# None = disabled (no call-only conversion on down days)
# Current VM: 0.40 (= 0.4% drop)
DOWNDAY_VALUES: List[Optional[float]] = [None, 0.20, 0.30, 0.40, 0.50, 0.60, 0.80, 1.00]

START_DATE = date(2022, 5, 16)
END_DATE   = date(2026, 3, 27)


def base_cfg(pct: Optional[float]) -> object:
    cfg = live_config()
    cfg.start_date      = START_DATE
    cfg.end_date        = END_DATE
    cfg.use_real_greeks = True
    cfg.base_entry_downday_callonly_pct = pct
    return cfg


def summarise(results: List[DayResult], label: str) -> dict:
    daily_pnls = [r.net_pnl for r in results]
    total_pnl  = sum(daily_pnls)
    total_days = len(results)
    win_days   = sum(1 for p in daily_pnls if p > 0)
    loss_days  = sum(1 for p in daily_pnls if p < 0)

    placed      = sum(r.entries_placed for r in results)
    total_stops = sum(r.stops_hit for r in results)

    call_only_entries = sum(
        sum(1 for e in r.entries if e.entry_type == "call_only"
            and e.skip_reason in ("base_downday", "[BASE-DOWNDAY]", "")
            and e.entry_type == "call_only")
        for r in results
    )
    # Count all call-only entries (any reason)
    all_call_only = sum(
        sum(1 for e in r.entries if e.entry_type == "call_only")
        for r in results
    )
    put_stops = sum(
        sum(1 for e in r.entries if e.put_outcome == "stopped")
        for r in results
    )
    call_stops = sum(
        sum(1 for e in r.entries if e.call_outcome == "stopped")
        for r in results
    )

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
        "win_rate":     win_days / total_days * 100 if total_days else 0,
        "total_pnl":    total_pnl,
        "mean_daily":   mean,
        "stdev_daily":  stdev,
        "sharpe":       sharpe,
        "max_dd":       max_dd,
        "calmar":       calmar,
        "placed":       placed,
        "total_stops":  total_stops,
        "stop_rate":    total_stops / placed * 100 if placed else 0,
        "call_only":    all_call_only,
        "put_stops":    put_stops,
        "call_stops":   call_stops,
    }


METRICS = [
    ("Win rate %",          "win_rate",    "{:.1f}%"),
    ("Total net P&L",       "total_pnl",   "${:,.0f}"),
    ("Mean daily P&L",      "mean_daily",  "${:.2f}"),
    ("Sharpe (annualised)", "sharpe",      "{:.3f}"),
    ("Max drawdown",        "max_dd",      "${:,.0f}"),
    ("Calmar ratio",        "calmar",      "{:.3f}"),
    ("Total stops",         "total_stops", "{:.0f}"),
    ("Stop rate %",         "stop_rate",   "{:.1f}%"),
    ("Call-only entries",   "call_only",   "{:.0f}"),
    ("Put-side stops",      "put_stops",   "{:.0f}"),
    ("Call-side stops",     "call_stops",  "{:.0f}"),
]


if __name__ == "__main__":
    total     = len(DOWNDAY_VALUES)
    all_stats = []

    if _RICH:
        console = Console()
        console.print(f"\n[bold cyan]Sweep: base_entry_downday_callonly_pct[/]")
        console.print(f"Base: live_config() — exact VM parameters")
        console.print(f"Values: {DOWNDAY_VALUES}  (None=disabled, 0.40=current VM)")
        console.print(f"Effect: E1-E5 become call-only when SPX drops >= X% from open")
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
            for i, v in enumerate(DOWNDAY_VALUES, 1):
                label = "disabled" if v is None else f"{v:.2f}%"
                progress.update(task, description=f"downday_pct = {label}  ({i}/{total})")
                results = run_backtest(base_cfg(v), verbose=False)
                all_stats.append(summarise(results, label))
                progress.advance(task)
    else:
        print(f"Sweep: base_entry_downday_callonly_pct  |  Values: {DOWNDAY_VALUES}")
        for v in DOWNDAY_VALUES:
            label = "disabled" if v is None else f"{v:.2f}%"
            print(f"  Running downday_pct = {label}...")
            results = run_backtest(base_cfg(v), verbose=False)
            all_stats.append(summarise(results, label))

    # ── Table ─────────────────────────────────────────────────────────────────
    col_w = 10
    if _RICH:
        tbl = Table(title="base_entry_downday_callonly_pct Sweep",
                    box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=22)
        for s in all_stats:
            tbl.add_column(s["label"], justify="right", width=col_w)
        for metric, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            if key in ("sharpe", "total_pnl", "calmar", "win_rate"):
                bi = max(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[bi] = f"[bold green]{row_vals[bi]}[/]"
            if key in ("max_dd", "stop_rate", "put_stops"):
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

    best_sh  = max(range(len(all_stats)), key=lambda i: all_stats[i]["sharpe"])
    best_pnl = max(range(len(all_stats)), key=lambda i: all_stats[i]["total_pnl"])
    live_idx = next((i for i, s in enumerate(all_stats) if s["label"] == "0.40%"), None)
    msg1 = f"\n  Best Sharpe:    {all_stats[best_sh]['label']}  ({all_stats[best_sh]['sharpe']:.3f})"
    msg2 = f"  Best total P&L: {all_stats[best_pnl]['label']}  (${all_stats[best_pnl]['total_pnl']:,.0f})"
    msg3 = f"  Live (0.40%): Sharpe {all_stats[live_idx]['sharpe']:.3f}  P&L ${all_stats[live_idx]['total_pnl']:,.0f}" if live_idx is not None else ""
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
    csv_path = out_dir / f"downday_pct_sweep_{ts}.csv"
    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "placed", "total_stops", "stop_rate", "call_only", "put_stops", "call_stops"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader(); writer.writerows(all_stats)
    msg = f"  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
