"""
SPXW 0DTE 1-Minute Data Downloader

Downloads option chain quotes AND Greeks at 1-minute intervals.
Stores in separate folders — does NOT touch existing 5-minute data.

  cache/options_1min/SPXW_YYYYMMDD.parquet        (quotes)
  cache/greeks_1min/SPXW_YYYYMMDD_greeks.parquet  (delta + IV)

Safe to re-run — skips already-cached dates automatically.

Usage:
  python -m backtest.download_1min                          # all days
  python -m backtest.download_1min 2026-03-01 2026-03-27   # date range
  python -m backtest.download_1min --greeks-only            # Greeks only
  python -m backtest.download_1min --options-only           # Options only
"""
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from backtest.downloader import (
    RTH_START_MS, RTH_END_MS,
    _date_str, _get, get_spxw_trading_days,
)

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
        TaskProgressColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn,
    )
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

CACHE_DIR        = Path("backtest/data/cache")
OPTIONS_1MIN_DIR = CACHE_DIR / "options_1min"
GREEKS_1MIN_DIR  = CACHE_DIR / "greeks_1min"
START_DATE       = date(2022, 5, 16)
END_DATE         = date(2026, 3, 27)
MAX_WORKERS      = 2  # 1-min data is ~6× heavier; 2 workers avoids overwhelming ThetaData


def download_chain_1min(expiry: date) -> bool:
    """Download 1-min option chain quotes for a single expiry date."""
    out_path = OPTIONS_1MIN_DIR / f"SPXW_{_date_str(expiry)}.parquet"
    if out_path.exists():
        try:
            if pd.read_parquet(out_path).shape[0] > 0:
                return True
        except Exception:
            pass

    exp_str = _date_str(expiry)
    data = _get("/v2/bulk_hist/option/quote", {
        "root": "SPXW",
        "exp": exp_str,
        "start_date": exp_str,
        "end_date": exp_str,
        "ivl": 60000,   # 1-minute
    }, retries=3, read_timeout=600)

    if not data or "response" not in data:
        return False

    rows_raw = data["response"]
    if not rows_raw:
        return False

    fmt = data["header"]["format"]
    idx_ms  = fmt.index("ms_of_day")
    idx_bid = fmt.index("bid")
    idx_ask = fmt.index("ask")

    records = []
    for item in rows_raw:
        contract = item["contract"]
        strike = contract["strike"] / 1000
        right = contract["right"]
        for tick in item["ticks"]:
            ms = tick[idx_ms]
            if ms < RTH_START_MS or ms > RTH_END_MS:
                continue
            bid = tick[idx_bid]
            ask = tick[idx_ask]
            mid = (bid + ask) / 2 if (bid + ask) > 0 else 0
            records.append({
                "strike": strike, "right": right,
                "ms_of_day": ms, "bid": bid, "ask": ask, "mid": mid,
            })

    if not records:
        return False

    df = pd.DataFrame(records)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return True


def download_greeks_1min(expiry: date) -> bool:
    """Download 1-min Greeks for a single expiry date."""
    out_path = GREEKS_1MIN_DIR / f"SPXW_{_date_str(expiry)}_greeks.parquet"
    if out_path.exists():
        try:
            if pd.read_parquet(out_path).shape[0] > 0:
                return True
        except Exception:
            pass

    exp_str = _date_str(expiry)
    data = _get("/v2/bulk_hist/option/greeks", {
        "root": "SPXW",
        "exp": exp_str,
        "start_date": exp_str,
        "end_date": exp_str,
        "ivl": 60000,   # 1-minute
    }, retries=3, read_timeout=600)

    if not data or "response" not in data:
        return False

    rows_raw = data["response"]
    if not rows_raw:
        return False

    fmt = data["header"]["format"]
    idx_ms    = fmt.index("ms_of_day")
    idx_delta = fmt.index("delta")
    idx_iv    = fmt.index("implied_vol")

    records = []
    for item in rows_raw:
        contract = item["contract"]
        strike = contract["strike"] / 1000
        right = contract["right"]
        for tick in item["ticks"]:
            ms = tick[idx_ms]
            if ms < RTH_START_MS or ms > RTH_END_MS:
                continue
            delta = tick[idx_delta]
            iv = tick[idx_iv]
            records.append({
                "strike": strike, "right": right,
                "ms_of_day": ms, "delta": delta, "implied_vol": iv,
            })

    if not records:
        return False

    df = pd.DataFrame(records)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return True


if __name__ == "__main__":
    args = sys.argv[1:]
    greeks_only  = "--greeks-only" in args
    options_only = "--options-only" in args
    args = [a for a in args if not a.startswith("--")]

    if len(args) >= 2:
        START_DATE = date.fromisoformat(args[0])
        END_DATE   = date.fromisoformat(args[1])

    OPTIONS_1MIN_DIR.mkdir(parents=True, exist_ok=True)
    GREEKS_1MIN_DIR.mkdir(parents=True, exist_ok=True)

    # Use 5-min options cache to get trading days list
    trading_days = get_spxw_trading_days(START_DATE, END_DATE, CACHE_DIR)

    # Count cached
    opts_cached = 0
    grk_cached  = 0
    opts_needed = []
    grk_needed  = []

    for d in trading_days:
        op = OPTIONS_1MIN_DIR / f"SPXW_{_date_str(d)}.parquet"
        gp = GREEKS_1MIN_DIR / f"SPXW_{_date_str(d)}_greeks.parquet"
        if op.exists():
            try:
                if pd.read_parquet(op).shape[0] > 0:
                    opts_cached += 1
                else:
                    opts_needed.append(d)
            except Exception:
                opts_needed.append(d)
        else:
            opts_needed.append(d)
        if gp.exists():
            try:
                if pd.read_parquet(gp).shape[0] > 0:
                    grk_cached += 1
                else:
                    grk_needed.append(d)
            except Exception:
                grk_needed.append(d)
        else:
            grk_needed.append(d)

    opts_needed.sort(reverse=True)  # newest first
    grk_needed.sort(reverse=True)

    total = len(trading_days)
    mode = "Greeks only" if greeks_only else ("Options only" if options_only else "Options + Greeks")

    if _RICH:
        console = Console()
        console.print()
        console.print(Panel.fit(
            f"[bold cyan]SPXW 1-Minute Data Downloader[/]\n"
            f"[dim]bid/ask/mid + delta/IV · 1-min intervals · {MAX_WORKERS} parallel workers[/]\n\n"
            f"  Period     : [yellow]{START_DATE}[/] → [yellow]{END_DATE}[/]\n"
            f"  Days       : [white]{total}[/] trading days\n"
            f"  Options    : [green]{opts_cached} cached[/]  [cyan]{len(opts_needed)} to fetch[/]\n"
            f"  Greeks     : [green]{grk_cached} cached[/]  [cyan]{len(grk_needed)} to fetch[/]\n"
            f"  Mode       : [white]{mode}[/]\n"
            f"  Output     : [dim]{OPTIONS_1MIN_DIR}/[/]\n"
            f"               [dim]{GREEKS_1MIN_DIR}/[/]",
            box=box.ROUNDED, border_style="cyan"
        ))
        console.print()
    else:
        print(f"\nSPXW 1-Minute Data Downloader: {START_DATE} → {END_DATE}")
        print(f"Total: {total}  Options cached: {opts_cached}  Greeks cached: {grk_cached}")
        print(f"To fetch: {len(opts_needed)} options + {len(grk_needed)} greeks\n")

    # ── Download Options ──────────────────────────────────────────────────────
    lock = threading.Lock()

    if not greeks_only and opts_needed:
        n_ok = 0
        n_fail = 0
        failed = []

        def _dl_opt(d):
            return d, download_chain_1min(d)

        if _RICH:
            with Progress(
                SpinnerColumn(style="cyan"),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=38, complete_style="cyan", finished_style="green"),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TextColumn("•"), TimeElapsedColumn(),
                TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
                console=console, refresh_per_second=4,
            ) as progress:
                task = progress.add_task(
                    f"1-min Options  ({MAX_WORKERS} workers)", total=len(opts_needed))
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {executor.submit(_dl_opt, d): d for d in opts_needed}
                    for future in as_completed(futures):
                        d, ok = future.result()
                        with lock:
                            if ok: n_ok += 1
                            else: n_fail += 1; failed.append(d)
                            progress.update(task, advance=1, description=(
                                f"1-min Options  [green]{n_ok} ok[/]  [red]{n_fail} fail[/]"))
        else:
            for i, d in enumerate(opts_needed, 1):
                ok = download_chain_1min(d)
                if ok: n_ok += 1
                else: n_fail += 1; failed.append(d)
                print(f"  [{i}/{len(opts_needed)}] {d} options {'✓' if ok else '✗'}")

        if _RICH:
            console.print(f"  Options: [green]{n_ok} downloaded[/], [red]{n_fail} failed[/]")
    elif not greeks_only:
        if _RICH:
            console.print("[green]  ✓ All 1-min options already cached[/]")

    # ── Download Greeks ───────────────────────────────────────────────────────
    if not options_only and grk_needed:
        n_ok = 0
        n_fail = 0
        failed_grk = []

        def _dl_grk(d):
            return d, download_greeks_1min(d)

        if _RICH:
            with Progress(
                SpinnerColumn(style="cyan"),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=38, complete_style="cyan", finished_style="green"),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TextColumn("•"), TimeElapsedColumn(),
                TextColumn("•"), TextColumn("[magenta]ETA"), TimeRemainingColumn(),
                console=console, refresh_per_second=4,
            ) as progress:
                task = progress.add_task(
                    f"1-min Greeks  ({MAX_WORKERS} workers)", total=len(grk_needed))
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {executor.submit(_dl_grk, d): d for d in grk_needed}
                    for future in as_completed(futures):
                        d, ok = future.result()
                        with lock:
                            if ok: n_ok += 1
                            else: n_fail += 1; failed_grk.append(d)
                            progress.update(task, advance=1, description=(
                                f"1-min Greeks  [green]{n_ok} ok[/]  [red]{n_fail} fail[/]"))
        else:
            for i, d in enumerate(grk_needed, 1):
                ok = download_greeks_1min(d)
                if ok: n_ok += 1
                else: n_fail += 1; failed_grk.append(d)
                print(f"  [{i}/{len(grk_needed)}] {d} greeks {'✓' if ok else '✗'}")

        if _RICH:
            console.print(f"  Greeks: [green]{n_ok} downloaded[/], [red]{n_fail} failed[/]")
    elif not options_only:
        if _RICH:
            console.print("[green]  ✓ All 1-min Greeks already cached[/]")

    # ── Summary ───────────────────────────────────────────────────────────────
    opts_files = list(OPTIONS_1MIN_DIR.glob("*.parquet"))
    grk_files  = list(GREEKS_1MIN_DIR.glob("*.parquet"))
    opts_size  = sum(f.stat().st_size for f in opts_files)
    grk_size   = sum(f.stat().st_size for f in grk_files)

    if _RICH:
        console.print()
        tbl = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
        tbl.add_column(style="dim")
        tbl.add_column(justify="right")
        tbl.add_row("Options (1-min)", f"[white]{len(opts_files)}[/] files  ({opts_size/1024/1024:.0f} MB)")
        tbl.add_row("Greeks (1-min)",  f"[white]{len(grk_files)}[/] files  ({grk_size/1024/1024:.0f} MB)")
        tbl.add_row("Options (5-min)", f"[dim]{len(list((CACHE_DIR/'options').glob('*.parquet')))} files  (unchanged)[/]")
        tbl.add_row("Greeks (5-min)",  f"[dim]{len(list((CACHE_DIR/'greeks').glob('*.parquet')))} files  (unchanged)[/]")
        console.print(Panel(tbl, title="[bold]Data Summary[/]", border_style="green", box=box.ROUNDED))
        console.print()
    else:
        print(f"\nDone.")
        print(f"  Options 1-min: {len(opts_files)} files ({opts_size/1024/1024:.0f} MB)")
        print(f"  Greeks 1-min:  {len(grk_files)} files ({grk_size/1024/1024:.0f} MB)")
