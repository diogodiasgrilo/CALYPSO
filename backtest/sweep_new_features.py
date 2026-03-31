"""
HYDRA New Features Sweep — 1-Minute Resolution

Tests 4 new strategy features against the full backtest dataset:
  1. Day-of-week filter (skip individual days or cap entries per day)
  2. VIX-regime adaptive parameters (different configs per VIX level at open)
  3. Trailing stop / profit lock (tighten stop once position is profitable)
  4. Replacement entries after stops (re-enter further OTM after a side is stopped)

Each feature is tested individually vs baseline, then the best from each
category are combined.

Run: python -m backtest.sweep_new_features
"""
import csv
import multiprocessing as mp
import os
import statistics
import time
from copy import copy
from datetime import date, datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
    TaskProgressColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn,
)
from rich.table import Table
from rich import box

from backtest.config import BacktestConfig, live_config
from backtest.engine import run_backtest, DayResult

START_DATE = date(2022, 5, 16)
END_DATE = date(2026, 3, 27)


# ── Metrics helper ────────────────────────────────────────────────────────

def compute_metrics(results: List[DayResult]) -> Dict[str, Any]:
    """Compute standard performance metrics from backtest results."""
    if not results:
        return {"days": 0, "net_pnl": 0, "sharpe": 0, "sortino": 0,
                "max_dd": 0, "win_rate": 0, "calmar": 0, "avg_daily": 0,
                "entries_placed": 0, "total_stops": 0}

    daily = [r.net_pnl for r in results]
    total = sum(daily)
    n = len(daily)
    win = sum(1 for p in daily if p > 0)
    loss = sum(1 for p in daily if p < 0)

    mean = statistics.mean(daily) if daily else 0
    std = statistics.stdev(daily) if n > 1 else 0
    sharpe = mean / std * (252 ** 0.5) if std > 0 else 0

    neg = [p for p in daily if p < 0]
    down_dev = (sum(p ** 2 for p in neg) / n) ** 0.5 if neg else 0
    sortino = mean / down_dev * (252 ** 0.5) if down_dev > 0 else 0

    # Max drawdown
    peak = cum = max_dd = 0.0
    for p in daily:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    calmar = (mean * 252) / abs(max_dd) if max_dd != 0 else 0

    all_entries = [e for r in results for e in r.entries]
    placed = [e for e in all_entries if e.entry_type != "skipped"]
    stops = sum(
        (1 if e.call_outcome == "stopped" else 0) +
        (1 if e.put_outcome == "stopped" else 0)
        for e in placed
    )
    replacements = sum(1 for e in placed if e.entry_num >= 20)

    return {
        "days": n,
        "net_pnl": total,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": max_dd,
        "win_rate": win / n * 100 if n > 0 else 0,
        "calmar": calmar,
        "avg_daily": mean,
        "entries_placed": len(placed),
        "total_stops": stops,
        "replacements": replacements,
    }


# ── Config builder helpers ────────────────────────────────────────────────

def _base() -> BacktestConfig:
    cfg = live_config()
    cfg.start_date = START_DATE
    cfg.end_date = END_DATE
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    return cfg


def build_configs() -> List[Tuple[str, str, BacktestConfig]]:
    """Build all configs: (category, label, cfg) tuples."""
    configs: List[Tuple[str, str, BacktestConfig]] = []

    # ── Baseline ──────────────────────────────────────────────────────
    configs.append(("baseline", "LIVE BASELINE", _base()))

    # ── 1. Day-of-week: skip individual days ──────────────────────────
    day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    for dow, name in day_names.items():
        cfg = _base()
        cfg.skip_weekdays = [dow]
        configs.append(("dow_skip", f"Skip {name}", cfg))

    # Day-of-week: cap entries on specific days
    for dow, name in [(0, "Mon"), (4, "Fri")]:
        for cap in [1, 2]:
            cfg = _base()
            cfg.dow_max_entries = {dow: cap}
            configs.append(("dow_cap", f"{name}={cap}e", cfg))

    # Both Mon+Fri capped
    cfg = _base()
    cfg.dow_max_entries = {0: 2, 4: 2}
    configs.append(("dow_cap", "Mon+Fri=2e", cfg))

    # ── 2. VIX regime: adaptive parameters ────────────────────────────
    # Regime A: Conservative at extremes
    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_max_entries = [2, None, 2, 1]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 300.0]
    configs.append(("vix_regime", "Conservative extremes", cfg))

    # Regime B: Tighter gates in low VIX, wider buffers in high VIX
    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [14.0, 20.0, 30.0]
    cfg.vix_regime_min_call_credit = [1.75, None, None, None]  # tighter in low VIX
    cfg.vix_regime_min_put_credit = [2.50, None, None, None]
    cfg.vix_regime_put_stop_buffer = [None, None, 200.0, 250.0]
    configs.append(("vix_regime", "Tight low-VIX gates", cfg))

    # Regime C: Scale entries with VIX
    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [15.0, 22.0]
    cfg.vix_regime_max_entries = [2, None, 2]
    configs.append(("vix_regime", "2e low/high 3e mid", cfg))

    # Regime D: Only modify high VIX (wider buffer)
    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [20.0]
    cfg.vix_regime_put_stop_buffer = [None, 200.0]
    configs.append(("vix_regime", "HighVIX wider put buf", cfg))

    # Regime E: Reduce to 2 entries only when VIX > 25
    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [25.0]
    cfg.vix_regime_max_entries = [None, 2]
    configs.append(("vix_regime", "2e when VIX>25", cfg))

    # Regime F: Widen both buffers when VIX > 20
    cfg = _base()
    cfg.vix_regime_enabled = True
    cfg.vix_regime_breakpoints = [20.0]
    cfg.vix_regime_call_stop_buffer = [None, 50.0]
    cfg.vix_regime_put_stop_buffer = [None, 250.0]
    configs.append(("vix_regime", "Wide bufs VIX>20", cfg))

    # ── 3. Trailing stop: sweep trigger × buffer combos ───────────────
    for trigger in [0.30, 0.40, 0.50, 0.60]:
        for call_buf, put_buf, buf_label in [
            (5.0,  30.0, "tight"),
            (10.0, 50.0, "med"),
            (20.0, 80.0, "wide"),
            (35.0, 155.0, "=base"),  # same as original = no effective tightening
        ]:
            # Skip the "=base" case for non-0.50 triggers (redundant)
            if buf_label == "=base" and trigger != 0.50:
                continue
            cfg = _base()
            cfg.trailing_stop_enabled = True
            cfg.trailing_stop_trigger_decay = trigger
            cfg.trailing_stop_call_buffer = call_buf
            cfg.trailing_stop_put_buffer = put_buf
            configs.append(("trailing", f"trig={trigger} {buf_label}", cfg))

    # ── 4. Replacement entries: sweep extra_otm × max × cutoff ────────
    for extra_otm in [5, 10, 15, 20]:
        for max_per_day in [1, 2, 3]:
            cfg = _base()
            cfg.replacement_entry_enabled = True
            cfg.replacement_entry_extra_otm = extra_otm
            cfg.replacement_entry_max_per_day = max_per_day
            cfg.replacement_entry_delay_minutes = 5
            cfg.replacement_entry_cutoff = "14:00"
            configs.append(("replacement", f"+{extra_otm}pt max{max_per_day}", cfg))

    # Replacement with different cutoff times
    for cutoff in ["12:00", "13:00", "15:00"]:
        cfg = _base()
        cfg.replacement_entry_enabled = True
        cfg.replacement_entry_extra_otm = 10
        cfg.replacement_entry_max_per_day = 2
        cfg.replacement_entry_delay_minutes = 5
        cfg.replacement_entry_cutoff = cutoff
        configs.append(("replacement", f"+10pt max2 cut{cutoff}", cfg))

    return configs


# ── Worker function (for multiprocessing) ─────────────────────────────────

def _run_one(args: Tuple[int, str, str, BacktestConfig]) -> Tuple[int, str, str, Dict]:
    idx, category, label, cfg = args
    results = run_backtest(cfg, verbose=False)
    metrics = compute_metrics(results)
    return (idx, category, label, metrics)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    console = Console()
    configs = build_configs()
    n_total = len(configs)

    console.print(Panel.fit(
        f"[bold cyan]HYDRA New Features Sweep[/]\n"
        f"Period: {START_DATE} → {END_DATE}  |  1-min data  |  Real Greeks\n"
        f"Configs to test: {n_total}",
        border_style="cyan",
    ))

    # Prepare worker args
    worker_args = [(i, cat, label, cfg) for i, (cat, label, cfg) in enumerate(configs)]

    # Use multiprocessing for speed
    n_workers = min(8, mp.cpu_count() or 4)
    console.print(f"[yellow]Running with {n_workers} workers...[/]\n")

    all_results: List[Tuple[int, str, str, Dict]] = []
    start_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Sweeping...", total=n_total)

        with mp.Pool(n_workers) as pool:
            for result in pool.imap_unordered(_run_one, worker_args):
                all_results.append(result)
                idx, cat, label, metrics = result
                progress.update(task, advance=1,
                    description=f"[green]{label}[/] Sharpe={metrics['sharpe']:.3f}")

    elapsed = time.time() - start_time
    console.print(f"\n[green]Completed {n_total} configs in {elapsed:.0f}s[/]\n")

    # Sort by original order
    all_results.sort(key=lambda x: x[0])

    # ── Print results by category ─────────────────────────────────────
    baseline_sharpe = 0
    baseline_pnl = 0
    categories = ["baseline", "dow_skip", "dow_cap", "vix_regime", "trailing", "replacement"]

    for cat in categories:
        cat_results = [(label, m) for (_, c, label, m) in all_results if c == cat]
        if not cat_results:
            continue

        cat_titles = {
            "baseline": "Baseline",
            "dow_skip": "Day-of-Week: Skip Day",
            "dow_cap": "Day-of-Week: Cap Entries",
            "vix_regime": "VIX Regime Adaptive",
            "trailing": "Trailing Stop / Profit Lock",
            "replacement": "Replacement Entries After Stops",
        }

        table = Table(
            title=f"\n{cat_titles.get(cat, cat)}",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold",
        )
        table.add_column("Config", style="cyan", min_width=25)
        table.add_column("Days", justify="right")
        table.add_column("Net P&L", justify="right")
        table.add_column("Sharpe", justify="right")
        table.add_column("Sortino", justify="right")
        table.add_column("MaxDD", justify="right")
        table.add_column("Win%", justify="right")
        table.add_column("Calmar", justify="right")
        table.add_column("Entries", justify="right")
        table.add_column("Stops", justify="right")
        if cat == "replacement":
            table.add_column("Repl", justify="right")

        for label, m in cat_results:
            if cat == "baseline":
                baseline_sharpe = m["sharpe"]
                baseline_pnl = m["net_pnl"]

            sharpe_delta = m["sharpe"] - baseline_sharpe if cat != "baseline" else 0
            s_color = "green" if sharpe_delta > 0.02 else ("red" if sharpe_delta < -0.02 else "white")

            row = [
                label,
                str(m["days"]),
                f"${m['net_pnl']:+,.0f}",
                f"[{s_color}]{m['sharpe']:.3f} ({sharpe_delta:+.3f})[/]" if cat != "baseline" else f"{m['sharpe']:.3f}",
                f"{m['sortino']:.3f}",
                f"${m['max_dd']:,.0f}",
                f"{m['win_rate']:.1f}%",
                f"{m['calmar']:.2f}",
                str(m["entries_placed"]),
                str(m["total_stops"]),
            ]
            if cat == "replacement":
                row.append(str(m.get("replacements", 0)))
            table.add_row(*row)

        console.print(table)

    # ── Best from each category ───────────────────────────────────────
    console.print("\n[bold cyan]Best Config per Category (by Sharpe):[/]")
    best_per_cat = {}
    for cat in categories:
        if cat == "baseline":
            continue
        cat_results = [(label, m) for (_, c, label, m) in all_results if c == cat]
        if cat_results:
            best_label, best_m = max(cat_results, key=lambda x: x[1]["sharpe"])
            best_per_cat[cat] = (best_label, best_m)
            delta = best_m["sharpe"] - baseline_sharpe
            color = "green" if delta > 0 else "red"
            console.print(
                f"  {cat:15s}: [{color}]{best_label:30s} Sharpe {best_m['sharpe']:.3f} ({delta:+.3f})[/]"
                f"  P&L ${best_m['net_pnl']:+,.0f}  MaxDD ${best_m['max_dd']:,.0f}"
            )

    # ── Save CSV ──────────────────────────────────────────────────────
    ts = dt.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path("backtest/results") / f"new_features_sweep_1min_{ts}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "category", "label", "days", "net_pnl", "sharpe", "sortino",
            "max_dd", "win_rate", "calmar", "avg_daily", "entries_placed",
            "total_stops", "replacements",
        ])
        for idx, cat, label, m in all_results:
            writer.writerow([
                cat, label, m["days"], f"{m['net_pnl']:.2f}",
                f"{m['sharpe']:.4f}", f"{m['sortino']:.4f}",
                f"{m['max_dd']:.2f}", f"{m['win_rate']:.2f}",
                f"{m['calmar']:.4f}", f"{m['avg_daily']:.2f}",
                m["entries_placed"], m["total_stops"],
                m.get("replacements", 0),
            ])

    console.print(f"\n[green]Results saved to {csv_path}[/]")


if __name__ == "__main__":
    main()
