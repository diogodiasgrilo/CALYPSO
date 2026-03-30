"""
Price-based stop sweep — INWARD direction (fires N pts BEFORE short strike).

Uses the optimal config from the 2026-03-24 768-combo sweep.
Everything is locked — only price_based_stop_points varies.

Run: python -m backtest.compare_stop_direction
"""
from datetime import date
from backtest.engine import run_backtest, DayResult
from backtest.config import BacktestConfig
from typing import List

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


# None = credit-based (optimal), 0.3 = best price-based N from fixed-engine sweep
INWARD_VALUES = [None, 0.3]


def base_cfg() -> BacktestConfig:
    """Fully locked optimal config — all params confirmed by 2026-03-24 sweeps."""
    return BacktestConfig(
        start_date=date(2022, 5, 16),
        end_date=date(2026, 3, 22),
        entry_times=["10:15", "10:45", "11:15", "11:45", "12:15"],
        conditional_e6_enabled=False,
        conditional_e7_enabled=True,
        conditional_upday_e6_enabled=True,
        conditional_upday_e7_enabled=False,
        downday_threshold_pct=0.30,
        upday_threshold_pct=0.60,
        base_entry_downday_callonly_pct=0.30,
        downday_theoretical_put_credit=175.0,   # $1.75 × 100 — sweep optimal 2026-03-24
        upday_theoretical_call_credit=0,
        fomc_t1_callonly_enabled=False,
        min_call_credit=1.25,
        min_put_credit=1.75,
        put_stop_buffer=100.0,
        call_stop_buffer=10.0,
        one_sided_entries_enabled=True,
        put_only_max_vix=25.0,
        stop_slippage_per_leg=0.0,
        target_delta=8.0,
    )


def summarise(results: List[DayResult], label: str) -> dict:
    total_pnl = sum(r.net_pnl for r in results)
    total_days = len(results)
    win_days = sum(1 for r in results if r.net_pnl > 0)
    loss_days = sum(1 for r in results if r.net_pnl < 0)
    flat_days = total_days - win_days - loss_days

    total_stops = sum(
        sum(1 for e in r.entries if e.call_outcome == "stopped" or e.put_outcome == "stopped")
        for r in results
    )
    total_entries = sum(len(r.entries) for r in results)

    daily_pnls = [r.net_pnl for r in results]
    import statistics
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
        "flat": flat_days,
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
]


if __name__ == "__main__":
    import csv
    from datetime import datetime as dt
    from pathlib import Path

    total = len(INWARD_VALUES)
    all_stats = []

    if _RICH:
        console = Console()
        console.print(f"\n[bold cyan]Price-based stop sweep — INWARD (fires N pts BEFORE strike)[/]")
        console.print(f"Config: optimal 2026-03-24 768-combo sweep  |  Period: 2022-05-16 → 2026-03-22")
        console.print(f"Values: {INWARD_VALUES}\n")

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
            for i, n in enumerate(INWARD_VALUES, 1):
                label = "credit-based" if n is None else f"{n}"
                progress.update(task, description=f"N = {label}  ({i}/{total})")
                cfg = base_cfg()
                cfg.price_based_stop_points = n
                cfg.price_stop_inward = True
                results = run_backtest(cfg, verbose=False)
                all_stats.append(summarise(results, label))
                progress.advance(task)
    else:
        print(f"Price-based stop sweep — INWARD  |  Values: {INWARD_VALUES}")
        for n in INWARD_VALUES:
            label = "credit-based" if n is None else f"{n}"
            print(f"  Running N = {label}...")
            cfg = base_cfg()
            cfg.price_based_stop_points = n
            cfg.price_stop_inward = True
            results = run_backtest(cfg, verbose=False)
            all_stats.append(summarise(results, label))

    # ── Results table ─────────────────────────────────────────────────────────
    if _RICH:
        tbl = Table(title="Price-Based Stop Sweep Results", box=box.SIMPLE_HEAVY,
                    show_header=True, header_style="bold yellow")
        tbl.add_column("Metric", style="cyan", width=26)
        for s in all_stats:
            tbl.add_column(f"N={s['label']}", justify="right", width=12)

        for label, key, fmt in METRICS:
            row_vals = [fmt.format(s[key]) for s in all_stats]
            # highlight best Sharpe column green
            if key == "sharpe":
                best_i = max(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[best_i] = f"[bold green]{row_vals[best_i]}[/]"
            if key == "total_pnl":
                best_i = max(range(len(all_stats)), key=lambda i: all_stats[i][key])
                row_vals[best_i] = f"[bold green]{row_vals[best_i]}[/]"
            tbl.add_row(label, *row_vals)

        console.print()
        console.print(tbl)
    else:
        col_w = 13
        header = f"  {'Metric':<26}"
        for s in all_stats:
            header += f"  {'N='+s['label']:>{col_w}}"
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
        console.print(f"\n  [bold]Best Sharpe:[/]    N = [green]{all_stats[best_sharpe_idx]['label']}[/] pt  "
                      f"({all_stats[best_sharpe_idx]['sharpe']:.3f})")
        console.print(f"  [bold]Best total P&L:[/] N = [green]{all_stats[best_pnl_idx]['label']}[/] pt  "
                      f"(${all_stats[best_pnl_idx]['total_pnl']:,.0f})")
    else:
        print(f"\n  Best Sharpe:    N = {all_stats[best_sharpe_idx]['label']} pt")
        print(f"  Best total P&L: N = {all_stats[best_pnl_idx]['label']} pt")

    # ── CSV export ────────────────────────────────────────────────────────────
    out_dir = Path("backtest/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"price_stop_sweep_{ts}.csv"

    csv_keys = ["label", "days", "win", "loss", "win_rate", "total_pnl",
                "mean_daily", "stdev_daily", "sharpe", "max_dd", "calmar",
                "total_entries", "total_stops", "stop_rate"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_stats)

    msg = f"\n  Results saved → {csv_path}"
    console.print(msg) if _RICH else print(msg)
