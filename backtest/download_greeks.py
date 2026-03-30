"""
SPXW 0DTE Greeks Downloader

Downloads delta + implied_vol at 5-min intervals for every cached trading day.
Stores to: backtest/data/cache/greeks/SPXW_YYYYMMDD_greeks.parquet
Nothing in cache/options/ or cache/index/ is touched.

Safe to re-run — skips already-cached dates automatically.

Run: python -m backtest.download_greeks
"""
import sys
import threading
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from backtest.downloader import (
    get_spxw_trading_days,
    download_greeks_day,
    _date_str,
)

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
        TaskProgressColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn,
        TransferSpeedColumn,
    )
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

CACHE_DIR   = Path("backtest/data/cache")
START_DATE  = date(2022, 5, 16)
END_DATE    = date(2026, 3, 26)   # last confirmed available date
MAX_WORKERS = 4


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        START_DATE = date.fromisoformat(sys.argv[1])
        END_DATE   = date.fromisoformat(sys.argv[2])

    greeks_dir = CACHE_DIR / "greeks"
    greeks_dir.mkdir(parents=True, exist_ok=True)

    trading_days = get_spxw_trading_days(START_DATE, END_DATE, CACHE_DIR)

    # Split into cached vs needed
    to_download = []
    already_cached = []
    for d in trading_days:
        path = greeks_dir / f"SPXW_{_date_str(d)}_greeks.parquet"
        if path.exists() and pd.read_parquet(path).shape[0] > 0:
            already_cached.append(d)
        else:
            to_download.append(d)

    to_download.sort(reverse=True)  # newest first
    total      = len(trading_days)
    n_cached   = len(already_cached)
    n_needed   = len(to_download)

    if _RICH:
        console = Console()
        console.print()
        console.print(Panel.fit(
            f"[bold cyan]SPXW Greeks Downloader[/]\n"
            f"[dim]delta + implied_vol · 5-min intervals · {MAX_WORKERS} parallel workers[/]\n\n"
            f"  Period  : [yellow]{START_DATE}[/] → [yellow]{END_DATE}[/]\n"
            f"  Days    : [white]{total}[/] trading days\n"
            f"  Cached  : [green]{n_cached}[/] already done\n"
            f"  To fetch: [cyan]{n_needed}[/] remaining\n"
            f"  Output  : [dim]{greeks_dir.resolve()}[/]",
            box=box.ROUNDED, border_style="cyan"
        ))
        console.print()
    else:
        print(f"\nSPXW Greeks Downloader: {START_DATE} → {END_DATE}")
        print(f"Total: {total}  Cached: {n_cached}  To fetch: {n_needed}\n")

    if not to_download:
        msg = "✓ All Greeks already cached — nothing to do."
        console.print(f"[bold green]{msg}[/]") if _RICH else print(msg)
        sys.exit(0)

    # ── Download with progress bar ─────────────────────────────────────────
    lock     = threading.Lock()
    n_ok     = 0
    n_fail   = 0
    failed_dates = []

    def _dl(d: date):
        ok = download_greeks_day(d, CACHE_DIR)
        return d, ok

    if _RICH:
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=38, complete_style="cyan", finished_style="green"),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TextColumn("[magenta]ETA"),
            TimeRemainingColumn(),
            console=console,
            refresh_per_second=8,
        ) as progress:
            task = progress.add_task(
                f"Downloading Greeks  ({MAX_WORKERS} workers)",
                total=n_needed,
            )

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(_dl, d): d for d in to_download}
                for future in as_completed(futures):
                    d, ok = future.result()
                    with lock:
                        if ok:
                            n_ok += 1
                        else:
                            n_fail += 1
                            failed_dates.append(d)
                        done_total = n_cached + n_ok + n_fail
                        progress.update(
                            task,
                            advance=1,
                            description=(
                                f"Downloading Greeks  "
                                f"[green]{n_ok} ok[/]  "
                                f"[red]{n_fail} fail[/]  "
                                f"([dim]{done_total}/{total}[/])"
                            ),
                        )
    else:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(_dl, d): d for d in to_download}
            for future in as_completed(futures):
                d, ok = future.result()
                with lock:
                    if ok:
                        n_ok += 1
                    else:
                        n_fail += 1
                        failed_dates.append(d)
                    done_total = n_cached + n_ok + n_fail
                    print(f"  [{done_total}/{total}] {d} {'✓' if ok else '✗'}")

    # ── Summary ────────────────────────────────────────────────────────────
    if _RICH:
        console.print()
        tbl = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
        tbl.add_column(style="dim")
        tbl.add_column(justify="right")
        tbl.add_row("Already cached",   f"[green]{n_cached}[/]")
        tbl.add_row("Downloaded now",   f"[cyan]{n_ok}[/]")
        tbl.add_row("Failed",           f"[red]{n_fail}[/]" if n_fail else "[dim]0[/]")
        tbl.add_row("Total available",  f"[bold white]{n_cached + n_ok}[/] / {total}")
        console.print(Panel(tbl, title="[bold]Download Summary[/]",
                            border_style="green" if not n_fail else "yellow",
                            box=box.ROUNDED))
        if failed_dates:
            console.print(f"\n[yellow]  {n_fail} dates failed — re-run to retry:[/]")
            for d in sorted(failed_dates):
                console.print(f"  [dim]  {d}[/]")
        console.print()
    else:
        print(f"\nDone. {n_ok} downloaded, {n_cached} cached, {n_fail} failed.")
        if failed_dates:
            print("Failed dates:", [str(d) for d in sorted(failed_dates)])
