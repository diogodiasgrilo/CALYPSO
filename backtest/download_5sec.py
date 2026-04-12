"""
SPXW 0DTE 5-Second Data Downloader

Downloads option chain quotes at 5-second intervals.
Stores in separate folder — does NOT touch 1-min or 5-min data.

  cache/options_5sec/SPXW_YYYYMMDD.parquet

No Greeks at 5-sec (not needed for stop monitoring — stop checks
use bid/ask prices, not delta/IV).

Usage:
  python -m backtest.download_5sec 2026-04-06 2026-04-08
"""
import sys
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
        TextColumn, TimeElapsedColumn, TimeRemainingColumn,
    )
    from rich.panel import Panel
    _RICH = True
except ImportError:
    _RICH = False

CACHE_DIR = Path("backtest/data/cache")
OPTIONS_5SEC_DIR = CACHE_DIR / "options_5sec"
MAX_WORKERS = 1  # 5-sec data is ~12x heavier than 1-min; be gentle on ThetaData


def download_chain_5sec(expiry: date) -> bool:
    """Download 5-sec option chain quotes for a single expiry date."""
    out_path = OPTIONS_5SEC_DIR / f"SPXW_{_date_str(expiry)}.parquet"
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
        "ivl": 5000,   # 5-second intervals
    }, retries=3, read_timeout=1200)  # Longer timeout for larger data

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


def get_trading_days(start: date, end: date) -> list:
    """Get weekday dates in range (simple calendar, no holiday check)."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d)
        d += timedelta(days=1)
    return days


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m backtest.download_5sec START_DATE END_DATE")
        print("  e.g. python -m backtest.download_5sec 2026-04-06 2026-04-08")
        sys.exit(1)

    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2])

    days = get_trading_days(start, end)
    OPTIONS_5SEC_DIR.mkdir(parents=True, exist_ok=True)

    # Check which days need downloading
    to_fetch = []
    cached = 0
    for d in days:
        path = OPTIONS_5SEC_DIR / f"SPXW_{_date_str(d)}.parquet"
        if path.exists():
            cached += 1
        else:
            to_fetch.append(d)

    print(f"\nSPXW 5-Second Data Downloader")
    print(f"Period: {start} -> {end} ({len(days)} trading days)")
    print(f"Cached: {cached}, To fetch: {len(to_fetch)}")
    print()

    if not to_fetch:
        print("All days already cached!")
        sys.exit(0)

    ok = 0
    fail = 0
    for i, d in enumerate(to_fetch, 1):
        print(f"  [{i}/{len(to_fetch)}] Downloading {d}...", end=" ", flush=True)
        if download_chain_5sec(d):
            size = (OPTIONS_5SEC_DIR / f"SPXW_{_date_str(d)}.parquet").stat().st_size / 1024 / 1024
            print(f"OK ({size:.1f} MB)")
            ok += 1
        else:
            print("FAILED")
            fail += 1

    print(f"\nDone: {ok} downloaded, {fail} failed")
    total_size = sum(f.stat().st_size for f in OPTIONS_5SEC_DIR.glob("*.parquet")) / 1024 / 1024
    print(f"Total 5-sec cache: {total_size:.0f} MB")
