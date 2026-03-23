"""
HYDRA Parameter Grid Optimizer

Walk-forward grid search over the 5 highest-impact HYDRA parameters.
Optimizes on training period, validates on out-of-sample period.

Usage:
    python -m backtest.optimize                    # full 1260-combo grid (~7 min on 12 cores)
    python -m backtest.optimize --quick            # 108-combo test grid (~1 min)
    python -m backtest.optimize --xl               # XL 18,144-combo grid
    python -m backtest.optimize --train-end 2024-12-31 --val-start 2025-01-01
    python -m backtest.optimize --workers 4
    python -m backtest.optimize --top-n 20
    python -m backtest.optimize --no-validate
    python -m backtest.optimize --output results.csv
    python -m backtest.optimize --no-rich          # plain progress bar (no rich TUI)
"""
import argparse
import contextlib
import copy
import csv
import dataclasses
import io
import itertools
import math
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Rich TUI (graceful fallback to plain progress if not installed)
try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                               SpinnerColumn, TextColumn, TimeElapsedColumn,
                               TimeRemainingColumn)
    from rich.table import Table
    from rich.text import Text
    from rich import box
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.config import BacktestConfig, live_config
from backtest.engine import run_backtest, DayResult


# ── Entry schedule presets ───────────────────────────────────────────────────

ENTRY_SCHEDULES = {
    "current":   ["10:15", "10:45", "11:15", "11:45", "12:15"],           # 5 entries (live HYDRA)
    "meic_6":    ["10:05", "10:35", "11:05", "11:35", "12:05", "12:35"],  # 6 entries (original MEIC)
    "morning_3": ["10:15", "10:45", "11:15"],                             # 3 morning-only entries
    "hourly_4":  ["10:15", "11:15", "12:15", "13:00"],                    # 4 hourly entries
}


# ── Parameter grids ─────────────────────────────────────────────────────────

XL_GRID = {
    "put_stop_buffer":            [100],              # LOCKED
    "min_put_credit":             [2.25],            # LOCKED (confirmed optimal: sweet spot between quality/quantity)
    "min_call_credit":            [1.25],            # LOCKED
    "stop_buffer":                [10],              # LOCKED
    "one_sided_entries_enabled":  [True],            # LOCKED
    "entry_schedule":             ["current"],       # LOCKED
    "early_exit_time":            [None],            # LOCKED
    "fomc_t1_callonly_enabled":   [True],            # LOCKED
    "put_only_max_vix":           [25.0],            # LOCKED
    "target_delta":               [8.0],             # LOCKED
    "conditional_e6_enabled":     [False],             # LOCKED (baseline Sharpe best; E6 adds P&L but hurts MaxDD)
    "conditional_e7_enabled":     [True],              # LOCKED
    "downday_threshold_pct":      [0.3],               # LOCKED (0.4% raises E7 bar too, hurts performance)
    "downday_reference":          ["open"],            # LOCKED
    "conditional_upday_e6_enabled": [True],            # LOCKED
    "conditional_upday_e7_enabled": [False],           # LOCKED
    "upday_threshold_pct":        [0.40],              # LOCKED
    "upday_reference":            ["open"],            # LOCKED
    "downday_theoretical_put_credit": [1000],             # LOCKED ($10.00 × 100 — call-only stop buffer)
    "upday_theoretical_call_credit":  [0],                # LOCKED ($0 — tight stop correct for put-only)
    "net_return_exit_pct":            [None],              # LOCKED (hold to 4PM beats all thresholds)
    "callside_min_upday_pct":         [None],              # LOCKED (full IC on all days beats call-only-on-up-days)
    "base_entry_downday_callonly_pct": [0.40],  # LOCKED (32% MaxDD reduction, near-zero Sharpe cost)
    "base_entry_upday_putonly_pct":    [None],  # LOCKED (Upday-035 E6 already covers up-day; adding would concentrate put risk)
    "movement_entry_pct":  [None, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],  # SWEEPING: SPX move % to trigger next E1-E5 slot early
    "max_vix_entry":       [None],              # LOCKED pending movement sweep result
}
# 8 combinations (sweeping movement_entry_pct)

FULL_GRID = {
    "put_stop_buffer":            [100, 200, 300, 400, 500, 600, 700, 800, 1000],
    "min_put_credit":             [1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00],
    "min_call_credit":            [0.40, 0.50, 0.60, 0.75, 1.00],
    "one_sided_entries_enabled":  [True, False],
    "fomc_t1_callonly_enabled":   [True, False],
}
# 9 × 7 × 5 × 2 × 2 = 1,260 combinations

QUICK_GRID = {
    "put_stop_buffer":            [200, 500, 800],
    "min_put_credit":             [2.00, 2.50, 3.00],
    "min_call_credit":            [0.50, 0.60, 0.75],
    "one_sided_entries_enabled":  [True, False],
    "fomc_t1_callonly_enabled":   [True, False],
}
# 3 × 3 × 3 × 2 × 2 = 108 combinations


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class OptCombo:
    """One parameter combination with its training and (optionally) validation metrics."""
    combo_id: int

    # Parameters
    put_stop_buffer: float
    min_put_credit: float
    min_call_credit: float
    one_sided_entries_enabled: bool
    fomc_t1_callonly_enabled: bool = True   # legacy field; XL grid fixes at True
    stop_buffer: float = 10.0               # call-side stop buffer
    entry_schedule: str = "current"         # entry time preset name
    early_exit_time: Optional[str] = None   # HH:MM or None
    put_only_max_vix: float = 25.0          # VIX ceiling for put-only entries
    target_delta: float = 8.0              # OTM delta target for strike selection
    conditional_e6_enabled: bool = False   # MKT-035: E6 conditional down-day entry
    conditional_e7_enabled: bool = False   # MKT-035: E7 conditional down-day entry
    downday_threshold_pct: float = 0.3     # % SPX drop below open to trigger conditional
    downday_reference: str = "open"        # reference price: "open" or "high"
    conditional_upday_e6_enabled: bool = False  # upday put-only at 12:45
    conditional_upday_e7_enabled: bool = False  # upday put-only at 13:15
    upday_threshold_pct: float = 0.3       # % SPX rise to trigger up-day put-only
    upday_reference: str = "open"          # reference price: "open" or "low"
    downday_theoretical_put_credit: float = 1000.0  # $ added to call-only stop level (locked)
    upday_theoretical_call_credit: float = 0.0      # $ added to put-only stop level
    net_return_exit_pct: Optional[float] = None     # exit when net_pnl/credit >= this fraction
    callside_min_upday_pct: Optional[float] = None  # only place calls on E1-E5 if SPX up >= this %
    base_entry_downday_callonly_pct: Optional[float] = None  # E1-E5 call-only when SPX down >= this %
    base_entry_upday_putonly_pct: Optional[float] = None     # E1-E5 put-only when SPX up >= this %
    movement_entry_pct: Optional[float] = None               # fire next E1-E5 slot when SPX moves >= this % from last entry
    max_vix_entry: Optional[float] = None                    # skip all entries if VIX >= this

    # Training metrics
    train_net_pnl: float = 0.0
    train_sharpe: float = 0.0
    train_max_dd: float = 0.0
    train_win_rate: float = 0.0
    train_avg_net_per_day: float = 0.0
    train_stop_rate: float = 0.0
    train_total_entries: int = 0
    train_total_skipped: int = 0
    train_days: int = 0

    # Validation metrics (populated for top-N only)
    val_net_pnl: Optional[float] = None
    val_sharpe: Optional[float] = None
    val_max_dd: Optional[float] = None
    val_win_rate: Optional[float] = None
    val_avg_net_per_day: Optional[float] = None
    val_stop_rate: Optional[float] = None
    val_days: Optional[int] = None


# ── Grid builder ─────────────────────────────────────────────────────────────

def build_grid(grid_def: dict) -> List[dict]:
    """Expand parameter grid dict into flat list of {param: value} dicts."""
    keys = list(grid_def.keys())
    values = list(grid_def.values())
    return [
        {"combo_id": i, **dict(zip(keys, combo_values))}
        for i, combo_values in enumerate(itertools.product(*values))
    ]


# ── Metrics computation ──────────────────────────────────────────────────────

def compute_metrics(results: List[DayResult]) -> dict:
    """
    Compute all ranking metrics from a list of DayResult objects.
    Mirrors engine.py:print_stats() exactly for consistency.
    """
    if not results:
        return {
            "net_pnl": 0.0, "sharpe": -999.0, "max_dd": 0.0,
            "win_rate": 0.0, "avg_net_per_day": 0.0, "stop_rate": 0.0,
            "total_entries": 0, "total_skipped": 0, "days": 0,
        }

    daily_net = [r.net_pnl for r in results]
    total_net = sum(daily_net)
    winning_days = sum(1 for x in daily_net if x > 0)
    win_rate = winning_days / len(daily_net) * 100

    arr = pd.Series(daily_net)
    sharpe = float(arr.mean() / arr.std() * math.sqrt(252)) if arr.std() > 0 else 0.0

    cumulative = arr.cumsum()
    max_dd = float((cumulative - cumulative.cummax()).min())

    avg_net_per_day = total_net / len(results)

    all_entries = [e for r in results for e in r.entries]
    placed = [e for e in all_entries if e.entry_type != "skipped"]
    skipped = [e for e in all_entries if e.entry_type == "skipped"]

    total_stops = sum(
        (1 if e.call_outcome == "stopped" else 0) +
        (1 if e.put_outcome == "stopped" else 0)
        for e in placed
    )
    stop_rate = total_stops / (len(placed) * 2) * 100 if placed else 0.0

    return {
        "net_pnl": total_net,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "win_rate": win_rate,
        "avg_net_per_day": avg_net_per_day,
        "stop_rate": stop_rate,
        "total_entries": len(placed),
        "total_skipped": len(skipped),
        "days": len(results),
    }


# ── Progress display ─────────────────────────────────────────────────────────

def _print_progress(done: int, total: int, start_time: float, prefix: str = ""):
    """Print a single-line progress bar using only stdlib. Overwrites previous line."""
    elapsed = time.time() - start_time
    rate = done / elapsed if elapsed > 0 else 0
    eta = (total - done) / rate if rate > 0 else 0
    pct = done / total * 100

    bar_width = 30
    filled = int(bar_width * done / total)
    bar = "=" * filled + "-" * (bar_width - filled)

    eta_str = f"{int(eta//60)}m{int(eta%60):02d}s" if eta > 0 else "--:--"
    line = f"\r{prefix}[{bar}] {done}/{total} ({pct:.0f}%)  ETA {eta_str}"
    print(line, end="", flush=True)
    if done == total:
        print()  # final newline


# ── Worker function (must be top-level for macOS spawn pickling) ─────────────

def _worker(args: Tuple[dict, date, date, str]) -> dict:
    """
    Top-level worker — module-level required for macOS multiprocessing spawn.

    Runs one backtest for the given combo + date range.
    Returns the combo dict merged with train_* metrics.
    Never raises — returns sentinel metrics on error.
    """
    combo, start, end, cache_dir = args

    cfg = copy.deepcopy(live_config())
    cfg.start_date = start
    cfg.end_date = end
    cfg.cache_dir = cache_dir

    cfg.put_stop_buffer = combo["put_stop_buffer"]
    cfg.min_put_credit = combo["min_put_credit"]
    cfg.min_call_credit = combo["min_call_credit"]
    cfg.one_sided_entries_enabled = combo["one_sided_entries_enabled"]
    cfg.fomc_t1_callonly_enabled = combo.get("fomc_t1_callonly_enabled", False)

    # XL grid parameters (optional — not present in QUICK/FULL grids)
    if "stop_buffer" in combo:
        cfg.stop_buffer = combo["stop_buffer"]
    if "entry_schedule" in combo:
        cfg.entry_times = ENTRY_SCHEDULES[combo["entry_schedule"]]
    if "early_exit_time" in combo:
        cfg.early_exit_time = combo["early_exit_time"]
    if "put_only_max_vix" in combo:
        cfg.put_only_max_vix = combo["put_only_max_vix"]
    if "target_delta" in combo:
        cfg.target_delta = combo["target_delta"]
    if "conditional_e6_enabled" in combo:
        cfg.conditional_e6_enabled = combo["conditional_e6_enabled"]
    if "conditional_e7_enabled" in combo:
        cfg.conditional_e7_enabled = combo["conditional_e7_enabled"]
    if "downday_threshold_pct" in combo:
        cfg.downday_threshold_pct = combo["downday_threshold_pct"]
    if "downday_reference" in combo:
        cfg.downday_reference = combo["downday_reference"]
    if "conditional_upday_e6_enabled" in combo:
        cfg.conditional_upday_e6_enabled = combo["conditional_upday_e6_enabled"]
    if "conditional_upday_e7_enabled" in combo:
        cfg.conditional_upday_e7_enabled = combo["conditional_upday_e7_enabled"]
    if "upday_threshold_pct" in combo:
        cfg.upday_threshold_pct = combo["upday_threshold_pct"]
    if "upday_reference" in combo:
        cfg.upday_reference = combo["upday_reference"]
    if "downday_theoretical_put_credit" in combo:
        cfg.downday_theoretical_put_credit = combo["downday_theoretical_put_credit"]
    if "upday_theoretical_call_credit" in combo:
        cfg.upday_theoretical_call_credit = combo["upday_theoretical_call_credit"]
    if "net_return_exit_pct" in combo:
        cfg.net_return_exit_pct = combo["net_return_exit_pct"]
    if "callside_min_upday_pct" in combo:
        cfg.callside_min_upday_pct = combo["callside_min_upday_pct"]
    if "base_entry_downday_callonly_pct" in combo:
        cfg.base_entry_downday_callonly_pct = combo["base_entry_downday_callonly_pct"]
    if "base_entry_upday_putonly_pct" in combo:
        cfg.base_entry_upday_putonly_pct = combo["base_entry_upday_putonly_pct"]
    if "movement_entry_pct" in combo:
        cfg.movement_entry_pct = combo["movement_entry_pct"]
    if "max_vix_entry" in combo:
        cfg.max_vix_entry = combo["max_vix_entry"]

    try:
        # Suppress run_backtest()'s internal print() calls
        with contextlib.redirect_stdout(io.StringIO()):
            results = run_backtest(cfg)
        metrics = compute_metrics(results)
    except Exception as e:
        metrics = compute_metrics([])
        metrics["_error"] = str(e)

    return {**combo, **{f"train_{k}": v for k, v in metrics.items()}}


# ── Rich leaderboard table (printed at intervals during training) ─────────────

def _build_leaderboard_table(
    raw_results: list,
    done: int,
    total: int,
    t0: float,
    n_workers: int,
    top_n: int = 10,
):
    """Return a rich Table showing current top-N combos plus run stats."""
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed > 0 else 0
    eta_s = (total - done) / rate if rate > 0 else 0
    eta_str = (f"{int(eta_s//3600)}h{int((eta_s%3600)//60):02d}m"
               if eta_s >= 3600 else f"{int(eta_s//60)}m{int(eta_s%60):02d}s"
               if eta_s > 0 else "--")

    sorted_r = sorted(
        raw_results,
        key=lambda r: (r.get("train_sharpe", -999), r.get("train_net_pnl", 0)),
        reverse=True,
    )[:top_n]

    t = Table(
        title=f"[bold yellow]★ Live Leaderboard[/]  "
              f"[cyan]{done:,}/{total:,}[/] ({done/total*100:.1f}%)  "
              f"[green]{rate:.1f}/s[/]  ETA [magenta]{eta_str}[/]",
        box=box.ROUNDED,
        border_style="yellow",
        show_lines=False,
        padding=(0, 1),
    )
    t.add_column("#", style="bold yellow", width=3, justify="right")
    t.add_column("Sharpe", width=7, justify="right")
    t.add_column("Net P&L", width=10, justify="right")
    t.add_column("Win%", width=5, justify="right")
    t.add_column("MaxDD", width=9, justify="right")
    t.add_column("Stop%", width=6, justify="right")
    t.add_column("PutBuf", width=6, justify="right")
    t.add_column("PutMin", width=6, justify="right")
    t.add_column("CallMin", width=7, justify="right")
    t.add_column("1Sd", width=3, justify="center")
    t.add_column("Sched", width=8)
    t.add_column("Exit", width=6)

    for rank, r in enumerate(sorted_r, 1):
        sh = r.get("train_sharpe", 0)
        pnl = r.get("train_net_pnl", 0)
        win = r.get("train_win_rate", 0)
        dd = r.get("train_max_dd", 0)
        stops = r.get("train_stop_rate", 0)

        sh_style = ("bold green" if sh > 2.0 else "green" if sh > 1.0
                    else "yellow" if sh > 0 else "red")
        pnl_style = "green" if pnl > 0 else "red"

        t.add_row(
            str(rank),
            Text(f"{sh:.2f}", style=sh_style),
            Text(f"${pnl:+,.0f}", style=pnl_style),
            f"{win:.1f}%",
            Text(f"${dd:,.0f}", style="dim red" if dd < -2000 else "dim"),
            f"{stops:.1f}%",
            f"${r.get('put_stop_buffer', 0)/100:.2g}",
            f"{r.get('min_put_credit', 0):.2f}",
            f"{r.get('min_call_credit', 0):.2f}",
            "Y" if r.get("one_sided_entries_enabled") else "N",
            r.get("entry_schedule", "current"),
            r.get("early_exit_time") or "4PM",
        )

    if not sorted_r:
        t.add_row("—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—")

    return t


# ── Tracked worker (for rich TUI — records active PID → combo info) ──────────

def _worker_tracked(args: Tuple[dict, date, date, str, object]) -> dict:
    """
    Wrapper around _worker that records itself in a Manager dict so the main
    process can display which combo each worker is currently running.
    """
    combo, start, end, cache_dir, active_dict = args
    pid = os.getpid()
    # Build a short label for the worker status panel
    buf = f"${combo['put_stop_buffer']/100:.2g}"
    put = f"{combo['min_put_credit']:.2f}"
    call = f"{combo['min_call_credit']:.2f}"
    sched = combo.get("entry_schedule", "current")
    exit_t = combo.get("early_exit_time") or "4PM"
    label = f"buf={buf} put≥{put} call≥{call} sched={sched} exit={exit_t}"
    active_dict[pid] = label

    result = _worker((combo, start, end, cache_dir))

    try:
        del active_dict[pid]
    except KeyError:
        pass
    return result


# ── Rich dashboard builder ────────────────────────────────────────────────────

def _combo_short_label(c) -> str:
    """Very compact parameter summary for the leaderboard table."""
    buf = f"${c.put_stop_buffer/100:.2g}"
    one_s = "Y" if c.one_sided_entries_enabled else "N"
    exit_t = c.early_exit_time or "4PM"
    return f"b={buf} p={c.min_put_credit:.2f} c={c.min_call_credit:.2f} 1s={one_s} {c.entry_schedule}/{exit_t}"


def _make_dashboard(
    done: int,
    total: int,
    t0: float,
    active_dict: dict,
    raw_results: list,
    n_workers: int,
    grid_label: str,
    train_start: date,
    train_end: date,
):
    """Build the full rich Layout that Live will render on every update."""
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed > 0 else 0
    eta_s = (total - done) / rate if rate > 0 else 0
    pct = done / total * 100

    # ── Header ────────────────────────────────────────────────────────────
    header_text = Text()
    header_text.append("  HYDRA PARAMETER OPTIMIZER", style="bold cyan")
    header_text.append(
        f"   Grid: {grid_label}  |  Workers: {n_workers}  |  "
        f"Training: {train_start} → {train_end}",
        style="dim",
    )

    # ── Progress bar ──────────────────────────────────────────────────────
    bar_width = 50
    filled = int(bar_width * done / total)
    bar = "█" * filled + "░" * (bar_width - filled)

    eta_str = f"{int(eta_s//3600)}h{int((eta_s%3600)//60):02d}m{int(eta_s%60):02d}s" if eta_s >= 3600 else \
              f"{int(eta_s//60)}m{int(eta_s%60):02d}s" if eta_s > 0 else "--:--"
    elapsed_str = f"{int(elapsed//3600)}h{int((elapsed%3600)//60):02d}m{int(elapsed%60):02d}s" if elapsed >= 3600 else \
                  f"{int(elapsed//60)}m{int(elapsed%60):02d}s"

    progress_text = Text()
    progress_text.append(f"  [{bar}]  ", style="green")
    progress_text.append(f"{done:,}/{total:,} ", style="bold white")
    progress_text.append(f"({pct:.1f}%)  ", style="bold yellow")
    progress_text.append(f"Elapsed: {elapsed_str}  ", style="cyan")
    progress_text.append(f"Speed: {rate:.1f}/s  ", style="cyan")
    progress_text.append(f"ETA: {eta_str}", style="magenta")

    progress_panel = Panel(progress_text, title="[bold]Progress", border_style="green")

    # ── Active workers / stats ────────────────────────────────────────────
    in_flight = min(n_workers, total - done)
    workers_text = Text()
    workers_text.append(f"  Workers:    {n_workers}\n", style="white")
    workers_text.append(f"  In-flight:  ~{max(0,in_flight)}\n", style="cyan")
    workers_text.append(f"  Completed:  {done:,}\n", style="green")
    workers_text.append(f"  Remaining:  {total - done:,}\n", style="yellow")
    if done > 0:
        avg_ms = elapsed / done * 1000
        workers_text.append(f"  Avg/combo:  {avg_ms:.0f}ms\n", style="dim")
        total_cpu_min = elapsed * n_workers / 60
        workers_text.append(f"  CPU-time:   {total_cpu_min:.1f}m\n", style="dim")

    workers_panel = Panel(
        workers_text,
        title=f"[bold]Run Stats",
        border_style="blue",
    )

    # ── Live leaderboard ──────────────────────────────────────────────────
    top_n = 12
    if raw_results:
        sorted_results = sorted(
            raw_results,
            key=lambda r: (r.get("train_sharpe", -999), r.get("train_net_pnl", 0)),
            reverse=True,
        )[:top_n]
    else:
        sorted_results = []

    lb_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    lb_table.add_column("#", style="bold yellow", width=3, justify="right")
    lb_table.add_column("Sharpe", width=7, justify="right")
    lb_table.add_column("Net P&L", width=9, justify="right")
    lb_table.add_column("Win%", width=5, justify="right")
    lb_table.add_column("MaxDD", width=9, justify="right")
    lb_table.add_column("Stops%", width=6, justify="right")
    lb_table.add_column("Parameters", width=42)

    for rank, r in enumerate(sorted_results, 1):
        sharpe = r.get("train_sharpe", 0)
        pnl = r.get("train_net_pnl", 0)
        win = r.get("train_win_rate", 0)
        dd = r.get("train_max_dd", 0)
        stops = r.get("train_stop_rate", 0)

        buf = f"${r.get('put_stop_buffer', 0)/100:.2g}"
        cbuf = f"${r.get('stop_buffer', 10)/100:.2f}"
        put_min = r.get("min_put_credit", 0)
        call_min = r.get("min_call_credit", 0)
        one_s = "Y" if r.get("one_sided_entries_enabled", False) else "N"
        sched = r.get("entry_schedule", "current")
        exit_t = r.get("early_exit_time") or "4PM"
        theo = r.get("downday_theoretical_put_credit", 250)
        params = f"put={buf} theo=${theo/100:.2f} p≥{put_min:.2f} c≥{call_min:.2f} cb={cbuf} 1s={one_s} {sched}/{exit_t}"

        sharpe_style = "bold green" if sharpe > 1.5 else ("green" if sharpe > 0.5 else ("yellow" if sharpe > 0 else "red"))
        pnl_style = "green" if pnl > 0 else "red"

        lb_table.add_row(
            str(rank),
            Text(f"{sharpe:.2f}", style=sharpe_style),
            Text(f"${pnl:+,.0f}", style=pnl_style),
            f"{win:.1f}%",
            Text(f"${dd:,.0f}", style="red" if dd < -1000 else "dim"),
            f"{stops:.1f}%",
            params,
        )

    if not sorted_results:
        lb_table.add_row("—", "—", "—", "—", "—", "—", "[dim]waiting for first result...[/dim]")

    lb_panel = Panel(
        lb_table,
        title=f"[bold]Live Leaderboard — Top {top_n} (of {done:,} completed)",
        border_style="yellow",
    )

    # ── Assemble layout ───────────────────────────────────────────────────
    layout = Layout()
    layout.split_column(
        Layout(Panel(header_text, border_style="cyan", padding=(0, 1)), size=3),
        Layout(progress_panel, size=5),
        Layout(name="lower"),
    )
    layout["lower"].split_row(
        Layout(workers_panel, ratio=2),
        Layout(lb_panel, ratio=5),
    )
    return layout


# ── Results assembly ─────────────────────────────────────────────────────────

def build_opt_combos(raw_results: List[dict]) -> List[OptCombo]:
    """Convert raw worker output dicts to OptCombo list sorted by Sharpe then P&L."""
    combos = []
    for r in raw_results:
        c = OptCombo(
            combo_id=r["combo_id"],
            put_stop_buffer=r["put_stop_buffer"],
            min_put_credit=r["min_put_credit"],
            min_call_credit=r["min_call_credit"],
            one_sided_entries_enabled=r["one_sided_entries_enabled"],
            fomc_t1_callonly_enabled=r.get("fomc_t1_callonly_enabled", True),
            stop_buffer=r.get("stop_buffer", 10.0),
            entry_schedule=r.get("entry_schedule", "current"),
            early_exit_time=r.get("early_exit_time", None),
            put_only_max_vix=r.get("put_only_max_vix", 25.0),
            target_delta=r.get("target_delta", 8.0),
            conditional_e6_enabled=r.get("conditional_e6_enabled", False),
            conditional_e7_enabled=r.get("conditional_e7_enabled", False),
            downday_threshold_pct=r.get("downday_threshold_pct", 0.3),
            downday_reference=r.get("downday_reference", "open"),
            conditional_upday_e6_enabled=r.get("conditional_upday_e6_enabled", False),
            conditional_upday_e7_enabled=r.get("conditional_upday_e7_enabled", False),
            upday_threshold_pct=r.get("upday_threshold_pct", 0.3),
            upday_reference=r.get("upday_reference", "open"),
            downday_theoretical_put_credit=r.get("downday_theoretical_put_credit", 1000.0),
            upday_theoretical_call_credit=r.get("upday_theoretical_call_credit", 0.0),
            net_return_exit_pct=r.get("net_return_exit_pct", None),
            callside_min_upday_pct=r.get("callside_min_upday_pct", None),
            base_entry_downday_callonly_pct=r.get("base_entry_downday_callonly_pct", None),
            base_entry_upday_putonly_pct=r.get("base_entry_upday_putonly_pct", None),
            movement_entry_pct=r.get("movement_entry_pct", None),
            max_vix_entry=r.get("max_vix_entry", None),
            train_net_pnl=r.get("train_net_pnl", 0.0),
            train_sharpe=r.get("train_sharpe", -999.0),
            train_max_dd=r.get("train_max_dd", 0.0),
            train_win_rate=r.get("train_win_rate", 0.0),
            train_avg_net_per_day=r.get("train_avg_net_per_day", 0.0),
            train_stop_rate=r.get("train_stop_rate", 0.0),
            train_total_entries=r.get("train_total_entries", 0),
            train_total_skipped=r.get("train_total_skipped", 0),
            train_days=r.get("train_days", 0),
        )
        combos.append(c)

    combos.sort(key=lambda c: (c.train_sharpe, c.train_net_pnl), reverse=True)
    return combos


# ── Output tables ────────────────────────────────────────────────────────────

def print_training_table(combos: List[OptCombo], top_n: int = 20):
    """Print top-N training results as an aligned table."""
    top_n = min(top_n, len(combos))
    # Detect if XL grid params are present
    has_xl = any(
        c.entry_schedule != "current" or c.early_exit_time is not None
        or c.stop_buffer != 10.0 or c.net_return_exit_pct is not None
        or c.callside_min_upday_pct is not None
        or c.base_entry_downday_callonly_pct is not None
        or c.base_entry_upday_putonly_pct is not None
        for c in combos[:top_n]
    )

    if has_xl:
        print(f"\n{'='*155}")
        print(f"  TOP {top_n} CONFIGURATIONS — TRAINING (ranked by Sharpe)")
        print(f"{'='*155}")
        print(
            f"  {'#':>3}  {'PutBuf':>7}  {'PutMin':>7}  {'CallMin':>7}  {'CallBuf':>7}  "
            f"{'1-Sided':>7}  {'Schedule':>9}  {'Exit':>6}  {'NRet%':>6}  "
            f"{'DnCall%':>7}  {'UpPut%':>6}  "
            f"{'Sharpe':>7}  {'Net P&L':>10}  {'Win%':>5}  "
            f"{'MaxDD':>10}  {'Stops%':>7}  {'Entries':>7}  {'Days':>5}"
        )
        print(f"  {'-'*168}")
        for rank, c in enumerate(combos[:top_n], 1):
            buf_str  = f"${c.put_stop_buffer/100:.2g}"
            cbuf_str = f"${c.stop_buffer/100:.2f}"
            one_sided = "Yes" if c.one_sided_entries_enabled else "No"
            exit_str  = c.early_exit_time or "4:00PM"
            nret_str  = f"{c.net_return_exit_pct:.0%}" if c.net_return_exit_pct else "off"
            dn_str    = f"{c.base_entry_downday_callonly_pct:.2f}%" if c.base_entry_downday_callonly_pct else "off"
            up_str    = f"{c.base_entry_upday_putonly_pct:.2f}%" if c.base_entry_upday_putonly_pct else "off"
            print(
                f"  {rank:>3}  {buf_str:>7}  {c.min_put_credit:>7.2f}  {c.min_call_credit:>7.2f}  "
                f"{cbuf_str:>7}  {one_sided:>7}  {c.entry_schedule:>9}  {exit_str:>6}  {nret_str:>6}  "
                f"{dn_str:>7}  {up_str:>6}  "
                f"{c.train_sharpe:>7.2f}  ${c.train_net_pnl:>9,.0f}  "
                f"{c.train_win_rate:>4.1f}%  ${c.train_max_dd:>9,.0f}  "
                f"{c.train_stop_rate:>6.1f}%  {c.train_total_entries:>7}  {c.train_days:>5}"
            )
        print(f"{'='*168}\n")
    else:
        print(f"\n{'='*115}")
        print(f"  TOP {top_n} CONFIGURATIONS — TRAINING (ranked by Sharpe)")
        print(f"{'='*115}")
        print(
            f"  {'#':>3}  {'PutBuf':>7}  {'PutMin':>7}  {'CallMin':>7}  "
            f"{'1-Sided':>7}  {'FomcT1':>7}  "
            f"{'Sharpe':>7}  {'Net P&L':>10}  {'Win%':>5}  "
            f"{'MaxDD':>10}  {'Stops%':>7}  {'Entries':>7}  {'Days':>5}"
        )
        print(f"  {'-'*110}")
        for rank, c in enumerate(combos[:top_n], 1):
            buf_str = f"${c.put_stop_buffer/100:.2g}"
            one_sided = "Yes" if c.one_sided_entries_enabled else "No"
            fomc_t1 = "Yes" if c.fomc_t1_callonly_enabled else "No"
            print(
                f"  {rank:>3}  {buf_str:>7}  {c.min_put_credit:>7.2f}  {c.min_call_credit:>7.2f}  "
                f"{one_sided:>7}  {fomc_t1:>7}  "
                f"{c.train_sharpe:>7.2f}  ${c.train_net_pnl:>9,.0f}  "
                f"{c.train_win_rate:>4.1f}%  ${c.train_max_dd:>9,.0f}  "
                f"{c.train_stop_rate:>6.1f}%  {c.train_total_entries:>7}  {c.train_days:>5}"
            )
        print(f"{'='*115}\n")


def print_validation_comparison(combos: List[OptCombo], val_count: int = 5):
    """Print side-by-side training vs validation for top-N validated combos."""
    validated = [c for c in combos if c.val_sharpe is not None][:val_count]
    if not validated:
        return

    print(f"\n{'='*100}")
    print(f"  WALK-FORWARD VALIDATION — TOP {len(validated)} CONFIGS")
    print(f"{'='*100}")
    print(
        f"  {'#':>3}  {'Config':<38}  "
        f"{'TrainSharpe':>11}  {'ValSharpe':>10}  "
        f"{'Train P&L':>10}  {'Val P&L':>10}  {'Robust?':>8}"
    )
    print(f"  {'-'*95}")

    for rank, c in enumerate(validated, 1):
        buf = f"${c.put_stop_buffer/100:.2g}"
        label = (
            f"buf={buf} put={c.min_put_credit:.2f} call={c.min_call_credit:.2f} "
            f"1s={'Y' if c.one_sided_entries_enabled else 'N'} "
            f"f1={'Y' if c.fomc_t1_callonly_enabled else 'N'}"
        )
        # Robust = val Sharpe positive AND at least 50% of training Sharpe
        robust = (
            c.val_sharpe is not None
            and c.val_sharpe > 0
            and c.val_sharpe >= c.train_sharpe * 0.50
        )
        print(
            f"  {rank:>3}  {label:<38}  "
            f"{c.train_sharpe:>11.2f}  {c.val_sharpe:>10.2f}  "
            f"${c.train_net_pnl:>9,.0f}  ${c.val_net_pnl:>9,.0f}  "
            f"{'✓ YES' if robust else '✗ no':>8}"
        )

    print(f"{'='*100}\n")


def print_best_config(best: OptCombo):
    """Print the single best config clearly."""
    print(f"\n{'='*60}")
    print(f"  BEST CONFIG (highest training Sharpe)")
    print(f"{'='*60}")
    print(f"  put_stop_buffer:           ${best.put_stop_buffer/100:.2g}  "
          f"(config value: {best.put_stop_buffer})")
    print(f"  stop_buffer (calls):       ${best.stop_buffer/100:.2f}  "
          f"(config value: {best.stop_buffer})")
    print(f"  min_put_credit:            ${best.min_put_credit:.2f}")
    print(f"  min_call_credit:           ${best.min_call_credit:.2f}")
    print(f"  one_sided_entries_enabled:  {best.one_sided_entries_enabled}")
    print(f"  entry_schedule:             {best.entry_schedule}")
    print(f"  early_exit_time:            {best.early_exit_time or 'None (hold to 4PM)'}")
    nret = best.net_return_exit_pct
    print(f"  net_return_exit_pct:        {f'{nret:.0%}' if nret else 'None (disabled)'}")
    if best.fomc_t1_callonly_enabled is not None:
        print(f"  fomc_t1_callonly_enabled:   {best.fomc_t1_callonly_enabled}")
    print(f"")
    print(f"  ── Training ─────────────────────────────────────────")
    print(f"  Sharpe:       {best.train_sharpe:.2f}")
    print(f"  Net P&L:      ${best.train_net_pnl:+,.0f}")
    print(f"  Win rate:     {best.train_win_rate:.1f}%")
    print(f"  Max drawdown: ${best.train_max_dd:,.0f}")
    print(f"  Avg/day:      ${best.train_avg_net_per_day:+.0f}")
    print(f"  Stop rate:    {best.train_stop_rate:.1f}%")
    print(f"  Days:         {best.train_days}")
    if best.val_sharpe is not None:
        robust = best.val_sharpe > 0 and best.val_sharpe >= best.train_sharpe * 0.50
        print(f"")
        print(f"  ── Validation ───────────────────────────────────────")
        print(f"  Sharpe:       {best.val_sharpe:.2f}")
        print(f"  Net P&L:      ${best.val_net_pnl:+,.0f}")
        print(f"  Win rate:     {best.val_win_rate:.1f}%")
        print(f"  Days:         {best.val_days}")
        print(f"  Robust:       {'YES ✓' if robust else 'NO ✗'}")
    print(f"{'='*60}\n")


# ── CSV export ───────────────────────────────────────────────────────────────

def save_results_csv(combos: List[OptCombo], path: Path):
    """Save all results to CSV, one row per combination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [f.name for f in dataclasses.fields(OptCombo)]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in combos:
            writer.writerow(dataclasses.asdict(c))

    print(f"  Full results saved to: {path}")


# ── Validation runner ────────────────────────────────────────────────────────

def run_validation(
    combos: List[OptCombo],
    val_start: date,
    val_end: date,
    cache_dir: str,
    n: int = 5,
    workers: int = None,
) -> List[OptCombo]:
    """
    Run the top-N combos against the validation period.
    Populates val_* fields on the OptCombo objects in-place.
    """
    to_validate = combos[:n]
    print(f"\nValidating top {len(to_validate)} configs on {val_start} → {val_end}...\n")

    worker_args = []
    for c in to_validate:
        combo_dict = {
            "combo_id": c.combo_id,
            "put_stop_buffer": c.put_stop_buffer,
            "min_put_credit": c.min_put_credit,
            "min_call_credit": c.min_call_credit,
            "one_sided_entries_enabled": c.one_sided_entries_enabled,
            "fomc_t1_callonly_enabled": c.fomc_t1_callonly_enabled,
            "stop_buffer": c.stop_buffer,
            "entry_schedule": c.entry_schedule,
            "early_exit_time": c.early_exit_time,
        }
        worker_args.append((combo_dict, val_start, val_end, cache_dir))

    n_workers = workers or min(n, mp.cpu_count())
    start_time = time.time()

    raw_val = []
    with mp.Pool(processes=n_workers) as pool:
        for i, result_dict in enumerate(
            pool.imap_unordered(_worker, worker_args), 1
        ):
            raw_val.append(result_dict)
            _print_progress(i, len(to_validate), start_time, prefix="  Validation: ")

    # Match back to OptCombo by combo_id and assign val_* fields
    # Note: worker always returns train_* keys regardless of which period ran
    val_by_id = {r["combo_id"]: r for r in raw_val}
    for c in to_validate:
        r = val_by_id.get(c.combo_id)
        if r:
            c.val_net_pnl = r.get("train_net_pnl", 0.0)
            c.val_sharpe = r.get("train_sharpe", -999.0)
            c.val_max_dd = r.get("train_max_dd", 0.0)
            c.val_win_rate = r.get("train_win_rate", 0.0)
            c.val_avg_net_per_day = r.get("train_avg_net_per_day", 0.0)
            c.val_stop_rate = r.get("train_stop_rate", 0.0)
            c.val_days = r.get("train_days", 0)

    return combos


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="HYDRA Grid Optimizer — walk-forward parameter search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--xl", action="store_true",
                   help="Use XL 360-combo grid (put buffer $0.50-$2, call credit/buffer ranges, one-sided)")
    p.add_argument("--quick", action="store_true",
                   help="Use reduced 108-combo grid for testing")
    p.add_argument("--train-start", default="2022-05-16",
                   help="Training period start (default: 2022-05-16)")
    p.add_argument("--train-end", default="2024-12-31",
                   help="Training period end (default: 2024-12-31)")
    p.add_argument("--val-start", default="2025-01-01",
                   help="Validation period start (default: 2025-01-01)")
    p.add_argument("--val-end", default=str(date.today()),
                   help="Validation period end (default: today)")
    p.add_argument("--workers", type=int, default=None,
                   help="Number of parallel workers (default: cpu_count)")
    p.add_argument("--output", default="",
                   help="CSV path (default: backtest/results/optimize_TIMESTAMP.csv)")
    p.add_argument("--top-n", type=int, default=20,
                   help="Rows to show in training table (default: 20)")
    p.add_argument("--val-n", type=int, default=5,
                   help="Top configs to validate out-of-sample (default: 5)")
    p.add_argument("--no-validate", action="store_true",
                   help="Skip validation phase (training only)")
    p.add_argument("--no-rich", action="store_true",
                   help="Use plain text progress bar instead of rich TUI")
    p.add_argument("--cache-dir", default="backtest/data/cache",
                   help="Path to cached data (default: backtest/data/cache)")
    return p.parse_args()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    train_start = date.fromisoformat(args.train_start)
    train_end = date.fromisoformat(args.train_end)
    val_start = date.fromisoformat(args.val_start)
    val_end = date.fromisoformat(args.val_end)

    if args.xl:
        grid_def = XL_GRID
        grid_label = f"XL ({len(build_grid(XL_GRID))})"
    elif args.quick:
        grid_def = QUICK_GRID
        grid_label = f"QUICK ({len(build_grid(QUICK_GRID))})"
    else:
        grid_def = FULL_GRID
        grid_label = f"FULL ({len(build_grid(FULL_GRID))})"

    combos_raw = build_grid(grid_def)
    total = len(combos_raw)
    n_workers = args.workers or mp.cpu_count()

    print(f"\n{'='*60}")
    print(f"  HYDRA PARAMETER OPTIMIZER")
    print(f"{'='*60}")
    print(f"  Grid:         {grid_label} combinations")
    print(f"  Training:     {train_start} → {train_end}")
    print(f"  Validation:   {val_start} → {val_end}"
          + (" (skipped)" if args.no_validate else ""))
    print(f"  Workers:      {n_workers}")
    print(f"  Cache:        {args.cache_dir}")
    print(f"{'='*60}\n")

    worker_args = [
        (c, train_start, train_end, args.cache_dir)
        for c in combos_raw
    ]

    # ── Training phase ────────────────────────────────────────────────────
    use_rich = _RICH_AVAILABLE and not args.no_rich
    print(f"Running {total} backtests across {n_workers} workers...\n")
    print(f"  (first results arrive in ~20-30s while workers load {len(build_grid(grid_def))//n_workers} combos each)\n")
    t0 = time.time()

    raw_results = []

    if use_rich:
        console = Console()
        # Leaderboard print interval: every 5% or every 60s, whichever comes first
        lb_interval = max(1, total // 20)
        last_lb_print = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=45),
            MofNCompleteColumn(),
            TextColumn("[yellow]{task.percentage:.1f}%"),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TextColumn("[magenta]ETA"),
            TimeRemainingColumn(),
            TextColumn("• [green]{task.fields[rate]:.1f}/s"),
            console=console,
            refresh_per_second=4,
        ) as progress:
            task = progress.add_task(
                f"Training ({grid_label})", total=total, rate=0.0
            )
            with mp.Pool(processes=n_workers) as pool:
                for i, result_dict in enumerate(
                    pool.imap_unordered(_worker, worker_args), 1
                ):
                    raw_results.append(result_dict)
                    elapsed_so_far = time.time() - t0
                    rate = i / elapsed_so_far if elapsed_so_far > 0 else 0
                    progress.update(task, completed=i, rate=rate)

                    # Print live leaderboard at intervals
                    if i - last_lb_print >= lb_interval or i == total:
                        progress.console.print(
                            _build_leaderboard_table(raw_results, i, total, t0, n_workers)
                        )
                        last_lb_print = i
    else:
        with mp.Pool(processes=n_workers) as pool:
            for i, result_dict in enumerate(
                pool.imap_unordered(_worker, worker_args), 1
            ):
                raw_results.append(result_dict)
                _print_progress(i, total, t0, prefix="  Training: ")

    elapsed = time.time() - t0
    print(f"\n  Completed {total} runs in {elapsed:.1f}s "
          f"({elapsed/total:.2f}s avg/run, {n_workers} workers)\n")

    combos = build_opt_combos(raw_results)
    print_training_table(combos, top_n=args.top_n)

    # ── Validation phase ──────────────────────────────────────────────────
    if not args.no_validate and val_start < val_end:
        run_validation(
            combos, val_start, val_end, args.cache_dir,
            n=args.val_n, workers=n_workers,
        )
        print_validation_comparison(combos, val_count=args.val_n)

    # ── Best config ───────────────────────────────────────────────────────
    if combos:
        print_best_config(combos[0])

    # ── Save CSV ──────────────────────────────────────────────────────────
    if args.output:
        out_path = Path(args.output)
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = Path("backtest/results") / f"optimize_{ts}.csv"

    save_results_csv(combos, out_path)


if __name__ == "__main__":
    main()
