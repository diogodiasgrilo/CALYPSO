"""
HYDRA Overfitting Validation — 1-Minute Resolution

Walk-forward test + parameter stability jitter on final optimised config.

Run: python -m backtest.sweep_dashboard_validation
"""
import multiprocessing as mp
import os
import random
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


def _run_backtest_with_overrides(args: Tuple) -> Dict:
    """Run a single backtest with config overrides."""
    label, start_date, end_date, overrides = args
    from backtest.config import live_config
    from backtest.engine import run_backtest

    cfg = live_config()
    cfg.start_date = start_date
    cfg.end_date = end_date
    cfg.use_real_greeks = True
    cfg.data_resolution = "1min"
    for k, v in overrides.items():
        setattr(cfg, k, v)

    results = run_backtest(cfg, verbose=False)
    pnls = [r.net_pnl for r in results]
    n = len(pnls); total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    placed = sum(r.entries_placed for r in results)
    stops = sum(r.stops_hit for r in results)
    mean = statistics.mean(pnls) if pnls else 0
    std = statistics.stdev(pnls) if n > 1 else 0
    sharpe = mean / std * (252 ** 0.5) if std > 0 else 0
    neg = [p for p in pnls if p < 0]
    dd_dev = (sum(p**2 for p in neg) / n) ** 0.5 if neg else 0
    sortino = mean / dd_dev * (252 ** 0.5) if dd_dev > 0 else 0
    peak = cum = dd = 0.0
    for p in pnls:
        cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
    calmar = mean * 252 / dd if dd > 0 else 0

    # Yearly breakdown
    from collections import defaultdict
    yearly = defaultdict(list)
    for r in results:
        yearly[r.date.year].append(r.net_pnl)

    return {
        "label": label, "days": n, "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4), "total_pnl": round(total, 2),
        "max_dd": round(dd, 2), "calmar": round(calmar, 4),
        "win_rate": round(wins / n * 100, 1) if n else 0,
        "placed": placed, "stops": stops,
        "avg_daily": round(mean, 2),
        "worst_day": round(min(pnls), 2) if pnls else 0,
        "best_day": round(max(pnls), 2) if pnls else 0,
        "yearly": {yr: round(sum(ps), 2) for yr, ps in yearly.items()},
        "overrides": overrides,
    }


# Parameters to jitter
JITTER_PARAMS = [
    ("min_call_credit", 1.35, 0.10),
    ("min_put_credit", 2.10, 0.10),
    ("call_credit_floor", 0.75, 0.10),
    ("put_credit_floor", 2.07, 0.10),
    ("call_stop_buffer", 35.0, 0.10),
    ("put_stop_buffer", 155.0, 0.10),
    ("downday_theoretical_put_credit", 260.0, 0.10),
    ("base_entry_downday_callonly_pct", 0.57, 0.10),
    ("spread_vix_multiplier", 5.3, 0.10),
    ("upday_threshold_pct", 0.48, 0.10),
    ("max_spread_width", 83, 0.10),
    ("whipsaw_range_skip_mult", 1.50, 0.10),
]


def main():
    console = Console()
    n_workers = min(8, os.cpu_count() or 4)

    FULL_START = date(2022, 5, 16)
    FULL_END = date(2026, 3, 27)
    TRAIN_END = date(2024, 12, 31)
    TEST_START = date(2025, 1, 1)

    # ── Build all tasks ──────────────────────────────────────────────────
    tasks = []

    # Walk-forward: 3 periods
    tasks.append(("WF: TRAIN (2022-2024)", FULL_START, TRAIN_END, {}))
    tasks.append(("WF: TEST (2025-2026)", TEST_START, FULL_END, {}))
    tasks.append(("WF: FULL", FULL_START, FULL_END, {}))

    # Individual jitter: each param ±10%
    for param, value, pct in JITTER_PARAMS:
        low = value * (1 - pct)
        high = value * (1 + pct)
        if isinstance(value, int):
            low = int(round(low)); high = int(round(high))
        tasks.append((f"JITTER -{pct*100:.0f}%: {param}", FULL_START, FULL_END, {param: low}))
        tasks.append((f"JITTER +{pct*100:.0f}%: {param}", FULL_START, FULL_END, {param: high}))

    # Multi-parameter random jitter: 15 trials, all params ±5%
    for trial in range(15):
        overrides = {}
        random.seed(trial * 42 + 7)
        for param, value, _ in JITTER_PARAMS:
            jittered = value * (1 + random.uniform(-0.05, 0.05))
            if isinstance(value, int):
                jittered = int(round(jittered))
            overrides[param] = jittered
        tasks.append((f"MULTI #{trial+1}", FULL_START, FULL_END, overrides))

    total_tasks = len(tasks)
    n_wf = 3
    n_individual = len(JITTER_PARAMS) * 2
    n_multi = 15

    header = (
        f"[bold cyan]HYDRA Overfitting Validation[/]\n"
        f"[dim]1-minute resolution · real Greeks · {n_workers} parallel workers[/]\n\n"
        f"  Walk-forward    : [white]{n_wf}[/] configs (train/test/full)\n"
        f"  Individual ±10% : [white]{n_individual}[/] configs ({len(JITTER_PARAMS)} params × 2)\n"
        f"  Multi-jitter ±5%: [white]{n_multi}[/] random trials\n"
        f"  Total           : [white]{total_tasks}[/] configs"
    )
    console.print()
    console.print(Panel(header, box=box.ROUNDED, border_style="blue",
                        title="[bold white]⚡ HYDRA Validation[/]"))
    console.print()

    all_results = []
    start_time = time.time()

    with Progress(
        SpinnerColumn(style="blue"),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=30, complete_style="blue", finished_style="green"),
        MofNCompleteColumn(), TaskProgressColumn(),
        TextColumn("•"), TimeElapsedColumn(),
        TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
        console=console, refresh_per_second=4,
    ) as progress:
        task = progress.add_task(f"Validation ({n_workers} workers)", total=total_tasks)
        with mp.Pool(processes=n_workers) as pool:
            for result in pool.imap_unordered(_run_backtest_with_overrides, tasks):
                all_results.append(result)
                progress.update(task, completed=len(all_results),
                    description=f"Validation  [green]{len(all_results)}/{total_tasks}[/]  ({n_workers} workers)")

    elapsed = time.time() - start_time

    # ── Walk-Forward Results ─────────────────────────────────────────────
    wf_results = {r["label"]: r for r in all_results if r["label"].startswith("WF:")}
    train = wf_results.get("WF: TRAIN (2022-2024)")
    test = wf_results.get("WF: TEST (2025-2026)")
    full = wf_results.get("WF: FULL")

    console.print()
    tbl = Table(title="[bold yellow]Walk-Forward Validation[/]",
                box=box.ROUNDED, header_style="bold yellow", border_style="yellow")
    tbl.add_column("Period", style="cyan", width=22)
    tbl.add_column("Days", justify="right", width=6)
    tbl.add_column("Sharpe", justify="right", width=8)
    tbl.add_column("Sortino", justify="right", width=8)
    tbl.add_column("P&L", justify="right", width=10)
    tbl.add_column("Avg/Day", justify="right", width=9)
    tbl.add_column("MaxDD", justify="right", width=10)
    tbl.add_column("Calmar", justify="right", width=8)
    tbl.add_column("Win%", justify="right", width=6)
    tbl.add_column("Worst Day", justify="right", width=10)

    for r in [train, test, full]:
        if r:
            tbl.add_row(
                r["label"].replace("WF: ", ""),
                str(r["days"]), f"{r['sharpe']:.3f}", f"{r['sortino']:.3f}",
                f"${r['total_pnl']:,.0f}", f"${r['avg_daily']:,.2f}",
                f"${r['max_dd']:,.0f}", f"{r['calmar']:.3f}",
                f"{r['win_rate']:.0f}%", f"${r['worst_day']:,.0f}",
            )

    console.print(tbl)

    if train and test:
        ratio = test["sharpe"] / train["sharpe"] * 100 if train["sharpe"] > 0 else 0
        if ratio >= 70:
            verdict = f"[bold green]ROBUST[/] — TEST = {ratio:.0f}% of TRAIN (≥70% threshold)"
        elif ratio >= 50:
            verdict = f"[bold yellow]MARGINAL[/] — TEST = {ratio:.0f}% of TRAIN"
        else:
            verdict = f"[bold red]OVERFIT[/] — TEST = {ratio:.0f}% of TRAIN (<50%)"
        console.print(f"\n  {verdict}")

        # Yearly breakdown
        console.print(f"\n  Yearly breakdown:")
        if full:
            for yr in sorted(full["yearly"].keys()):
                console.print(f"    {yr}: ${full['yearly'][yr]:>9,.0f}")

    # ── Individual Jitter Results ────────────────────────────────────────
    console.print()
    base_sharpe = full["sharpe"] if full else 0

    jitter_tbl = Table(title="[bold yellow]Individual Parameter Jitter (±10%)[/]",
                       box=box.SIMPLE_HEAVY, header_style="bold yellow")
    jitter_tbl.add_column("Parameter", style="cyan", width=35)
    jitter_tbl.add_column("Value", width=8)
    jitter_tbl.add_column("-10%", width=8)
    jitter_tbl.add_column("+10%", width=8)
    jitter_tbl.add_column("Sh(base)", justify="right", width=8)
    jitter_tbl.add_column("Sh(-10%)", justify="right", width=9)
    jitter_tbl.add_column("Sh(+10%)", justify="right", width=9)
    jitter_tbl.add_column("Stable?", justify="right", width=12)

    fragile_count = 0
    for param, value, pct in JITTER_PARAMS:
        low = value * (1 - pct)
        high = value * (1 + pct)
        low_r = next((r for r in all_results if r["label"] == f"JITTER -{pct*100:.0f}%: {param}"), None)
        high_r = next((r for r in all_results if r["label"] == f"JITTER +{pct*100:.0f}%: {param}"), None)
        sh_low = low_r["sharpe"] if low_r else 0
        sh_high = high_r["sharpe"] if high_r else 0
        drop_low = (base_sharpe - sh_low) / base_sharpe * 100 if base_sharpe > 0 else 0
        drop_high = (base_sharpe - sh_high) / base_sharpe * 100 if base_sharpe > 0 else 0
        max_drop = max(drop_low, drop_high)
        if max_drop >= 15:
            stable = "[bold red]FRAGILE[/]"
            fragile_count += 1
        else:
            stable = "[green]✓[/]"

        if isinstance(value, int):
            vstr = str(value); lstr = str(int(round(low))); hstr = str(int(round(high)))
        elif value >= 10:
            vstr = f"{value:.0f}"; lstr = f"{low:.0f}"; hstr = f"{high:.0f}"
        else:
            vstr = f"{value:.2f}"; lstr = f"{low:.2f}"; hstr = f"{high:.2f}"

        jitter_tbl.add_row(param, vstr, lstr, hstr,
                          f"{base_sharpe:.3f}", f"{sh_low:.3f}", f"{sh_high:.3f}", stable)

    console.print(jitter_tbl)

    # ── Multi-Parameter Jitter ───────────────────────────────────────────
    console.print()
    multi_results = sorted(
        [r for r in all_results if r["label"].startswith("MULTI")],
        key=lambda r: r["sharpe"], reverse=True
    )

    multi_tbl = Table(title="[bold yellow]Multi-Parameter Random Jitter (all ±5%, 15 trials)[/]",
                      box=box.SIMPLE_HEAVY, header_style="bold yellow")
    multi_tbl.add_column("Trial", width=10)
    multi_tbl.add_column("Sharpe", justify="right", width=8)
    multi_tbl.add_column("P&L", justify="right", width=10)
    multi_tbl.add_column("MaxDD", justify="right", width=10)
    multi_tbl.add_column("vs Base", justify="right", width=8)

    for r in multi_results:
        delta = (r["sharpe"] - base_sharpe) / base_sharpe * 100
        delta_style = "green" if delta > 0 else ("red" if delta < -15 else "yellow")
        multi_tbl.add_row(
            r["label"], f"{r['sharpe']:.3f}", f"${r['total_pnl']:,.0f}",
            f"${r['max_dd']:,.0f}", f"[{delta_style}]{delta:+.1f}%[/{delta_style}]",
        )

    console.print(multi_tbl)

    if multi_results:
        sharpes = [r["sharpe"] for r in multi_results]
        avg_sh = statistics.mean(sharpes)
        min_sh = min(sharpes)
        worst_deg = (base_sharpe - min_sh) / base_sharpe * 100
        console.print(f"\n  Jitter Sharpe range: [white]{min_sh:.3f}[/] – [white]{max(sharpes):.3f}[/] (avg [white]{avg_sh:.3f}[/])")
        console.print(f"  Baseline Sharpe:     [white]{base_sharpe:.3f}[/]")
        console.print(f"  Worst degradation:   [{'red' if worst_deg > 20 else 'yellow'} ]{worst_deg:.1f}%[/]")

    # ── Final Verdict ────────────────────────────────────────────────────
    console.print()
    verdict_lines = []
    if train and test:
        ratio = test["sharpe"] / train["sharpe"] * 100 if train["sharpe"] > 0 else 0
        verdict_lines.append(f"  Walk-forward: TEST/TRAIN = [white]{ratio:.0f}%[/] {'[green]PASS[/]' if ratio >= 70 else '[red]FAIL[/]'}")
    verdict_lines.append(f"  Individual jitter: [white]{len(JITTER_PARAMS) - fragile_count}/{len(JITTER_PARAMS)}[/] stable {'[green]PASS[/]' if fragile_count <= 2 else '[red]FAIL[/]'}")
    if multi_results:
        avg_deg = (base_sharpe - avg_sh) / base_sharpe * 100
        verdict_lines.append(f"  Multi-jitter avg degradation: [white]{avg_deg:.1f}%[/] {'[green]PASS[/]' if avg_deg < 20 else '[red]FAIL[/]'}")
        verdict_lines.append(f"  Realistic live Sharpe estimate: [bold white]{avg_sh:.3f}[/]")

    console.print(Panel(
        "\n".join(verdict_lines),
        title="[bold]Validation Verdict[/]",
        box=box.ROUNDED, border_style="blue",
    ))

    # Timing
    console.print()
    console.print(Panel(
        f"  Total configs : [white]{total_tasks}[/]\n"
        f"  Workers       : [white]{n_workers}[/]\n"
        f"  Wall time     : [yellow]{elapsed/60:.1f} min[/]",
        title="[bold]Timing[/]", box=box.ROUNDED, border_style="dim",
    ))
    console.print()


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
