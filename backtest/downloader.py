"""
ThetaData Downloader

Downloads and caches:
  - SPX 1-min price (index)
  - VIX 1-min price (index)
  - SPXW 0DTE option chain quotes at 5-min intervals (full chain per day)

Data is stored as parquet files in the cache directory.
Re-running will skip already-cached dates.
"""
import os
import json
import time
import requests
import pandas as pd
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading


THETA_HOST = "http://127.0.0.1:25510"

# ms-of-day boundaries for regular trading hours
RTH_START_MS = 34_200_000   # 9:30 AM ET
RTH_END_MS   = 57_600_000   # 4:00 PM ET


# ── Low-level API helpers ──────────────────────────────────────────────────

def _get(endpoint: str, params: dict, retries: int = 3, read_timeout: int = 60) -> Optional[dict]:
    url = f"{THETA_HOST}{endpoint}"
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=(10, read_timeout))
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 472:
                # No data for this contract/date
                return None
            else:
                print(f"  HTTP {r.status_code} for {endpoint} {params} — retrying...")
                time.sleep(2 ** attempt)
        except requests.exceptions.Timeout:
            print(f"  Timeout on attempt {attempt+1}/{retries}")
            time.sleep(5)
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(2 ** attempt)
    return None


def _date_str(d: date) -> str:
    return d.strftime("%Y%m%d")


# ── Index data ─────────────────────────────────────────────────────────────

def download_index_month(symbol: str, year: int, month: int, cache_dir: Path) -> bool:
    """Download a full month of 1-min index data and cache to parquet."""
    out_path = cache_dir / "index" / f"{symbol}_{year}{month:02d}.parquet"
    if out_path.exists():
        return True  # already cached

    start = date(year, month, 1)
    # Last day of month
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    end = min(end, date.today() - timedelta(days=1))

    print(f"  Downloading {symbol} index {year}-{month:02d}...", end=" ", flush=True)

    data = _get("/v2/hist/index/price", {
        "root": symbol,
        "start_date": _date_str(start),
        "end_date": _date_str(end),
        "ivl": 60000,    # 1-minute
    })

    if not data or "response" not in data:
        print("no data")
        return False

    fields = data["header"]["format"]
    rows = data["response"]
    if not rows:
        print("empty")
        return False

    df = pd.DataFrame(rows, columns=fields)
    # Filter to RTH only (9:30–16:00 ET)
    df = df[(df["ms_of_day"] >= RTH_START_MS) & (df["ms_of_day"] <= RTH_END_MS)]
    # Convert date int to proper date
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df["ms_of_day"] = df["ms_of_day"].astype(int)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    print(f"✓ {len(df)} rows")
    return True


def load_index_day(symbol: str, d: date, cache_dir: Path) -> pd.DataFrame:
    """Load cached 1-min index data for a specific date."""
    path = cache_dir / "index" / f"{symbol}_{d.year}{d.month:02d}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    day_df = df[df["date"] == pd.Timestamp(d)]
    return day_df.sort_values("ms_of_day").reset_index(drop=True)


# ── Options chain data ─────────────────────────────────────────────────────

def download_chain_day(expiry: date, cache_dir: Path, fast_mode: bool = False) -> bool:
    """
    Download the full SPXW 0DTE chain for a given expiry date.
    Returns a flat DataFrame with columns:
        strike, right, ms_of_day, bid, ask, mid
    Cached to parquet at options/SPXW_YYYYMMDD.parquet

    fast_mode: use 30s timeout and 1 retry — skips slow/hanging dates immediately
               without creating a placeholder (so they can be retried later).
    """
    out_path = cache_dir / "options" / f"SPXW_{_date_str(expiry)}.parquet"
    if out_path.exists():
        # Empty placeholder = 0 rows (parquet still has non-zero file size due to format overhead).
        # In fast mode: treat as cached (skip it, slow pass will retry).
        # In slow mode: delete and re-attempt so we actually get the data.
        if pd.read_parquet(out_path).shape[0] > 0:
            return True
        if fast_mode:
            return True
        out_path.unlink()  # delete empty placeholder and try again

    exp_str = _date_str(expiry)
    print(f"  Downloading SPXW chain {expiry}...", end=" ", flush=True)

    retries = 1 if fast_mode else 3
    read_timeout = 30 if fast_mode else 300  # 5min for slow pass — large chains need up to 3min

    data = _get("/v2/bulk_hist/option/quote", {
        "root": "SPXW",
        "exp": exp_str,
        "start_date": exp_str,
        "end_date": exp_str,
        "ivl": 300000,   # 5-minute intervals
    }, retries=retries, read_timeout=read_timeout)

    if not data or "response" not in data:
        print("no data")
        return False

    rows_raw = data["response"]
    if not rows_raw:
        print("empty")
        return False

    # Each element: {"ticks": [...], "contract": {"root", "expiration", "strike", "right"}}
    records = []
    for item in rows_raw:
        contract = item["contract"]
        strike = contract["strike"] / 1000  # millionths → actual (e.g. 4550000 → 4550)
        right = contract["right"]           # "C" or "P"
        for tick in item["ticks"]:
            ms = tick[0]
            bid = tick[3]
            ask = tick[7]
            # Only keep RTH ticks with valid quotes
            if ms < RTH_START_MS or ms > RTH_END_MS:
                continue
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0.0
            records.append((strike, right, ms, bid, ask, mid))

    if not records:
        print("no RTH data")
        return False

    df = pd.DataFrame(records, columns=["strike", "right", "ms_of_day", "bid", "ask", "mid"])
    df = df.astype({
        "strike": "float32",
        "ms_of_day": "int32",
        "bid": "float32",
        "ask": "float32",
        "mid": "float32",
    })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    print(f"✓ {len(df)} rows ({len(df['strike'].unique())} strikes)")
    return True


def load_chain_day(expiry: date, cache_dir: Path) -> pd.DataFrame:
    """Load cached SPXW chain data for a specific date."""
    path = cache_dir / "options" / f"SPXW_{_date_str(expiry)}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


# ── Trading calendar ────────────────────────────────────────────────────────

NYSE_HOLIDAYS = {
        # 2022
        date(2022, 1, 17),   # MLK Day
        date(2022, 2, 21),   # Presidents Day
        date(2022, 4, 15),   # Good Friday
        date(2022, 5, 30),   # Memorial Day
        date(2022, 6, 20),   # Juneteenth (observed)
        date(2022, 7, 4),    # Independence Day
        date(2022, 9, 5),    # Labor Day
        date(2022, 11, 24),  # Thanksgiving
        date(2022, 12, 26),  # Christmas (observed)
        # 2023
        date(2023, 1, 2),    # New Year's (observed)
        date(2023, 1, 16),   # MLK Day
        date(2023, 2, 20),   # Presidents Day
        date(2023, 4, 7),    # Good Friday
        date(2023, 5, 29),   # Memorial Day
        date(2023, 6, 19),   # Juneteenth
        date(2023, 7, 4),    # Independence Day
        date(2023, 9, 4),    # Labor Day
        date(2023, 11, 23),  # Thanksgiving
        date(2023, 12, 25),  # Christmas
        # 2024
        date(2024, 1, 1),    # New Year's
        date(2024, 1, 15),   # MLK Day
        date(2024, 2, 19),   # Presidents Day
        date(2024, 3, 29),   # Good Friday
        date(2024, 5, 27),   # Memorial Day
        date(2024, 6, 19),   # Juneteenth
        date(2024, 7, 4),    # Independence Day
        date(2024, 9, 2),    # Labor Day
        date(2024, 11, 28),  # Thanksgiving
        date(2024, 12, 25),  # Christmas
        # 2025
        date(2025, 1, 1),    # New Year's
        date(2025, 1, 9),    # National Day of Mourning (Carter)
        date(2025, 1, 20),   # MLK Day
        date(2025, 2, 17),   # Presidents Day
        date(2025, 4, 18),   # Good Friday
        date(2025, 5, 26),   # Memorial Day
        date(2025, 6, 19),   # Juneteenth
        date(2025, 7, 4),    # Independence Day
        date(2025, 9, 1),    # Labor Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
        # 2026
        date(2026, 1, 1),    # New Year's
        date(2026, 1, 19),   # MLK Day
        date(2026, 2, 16),   # Presidents Day
        date(2026, 4, 3),    # Good Friday
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2026, 7, 3),    # Independence Day (observed)
        date(2026, 9, 7),    # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
}


def get_trading_days(start: date, end: date, cache_dir: Path) -> List[date]:
    """
    Return all NYSE trading days (Mon–Fri, non-holiday) in the range.
    NYSE holidays hardcoded for 2022–2026.
    """
    result = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in NYSE_HOLIDAYS:
            result.append(d)
        d += timedelta(days=1)
    return result


def get_spxw_trading_days(start: date, end: date, cache_dir: Path) -> List[date]:
    """
    Return trading days where SPXW 0DTE data is available in the local cache.
    Reads the options/ directory — no network calls, works offline.
    Falls back to calendar-based list if cache is empty.
    """
    options_dir = Path(cache_dir) / "options"
    cached_dates = set()
    if options_dir.exists():
        for f in options_dir.glob("SPXW_*.parquet"):
            s = f.stem.replace("SPXW_", "")  # e.g. "20240102"
            try:
                d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
                if start <= d <= end:
                    cached_dates.add(d)
            except (ValueError, IndexError):
                pass

    if cached_dates:
        return sorted(cached_dates)

    # Fallback: calendar-based (no cache yet)
    print("  Warning: no cached SPXW files found — using calendar fallback")
    DAILY_START = date(2022, 5, 16)
    all_days = get_trading_days(start, end, cache_dir)
    result = []
    for d in all_days:
        if d >= DAILY_START:
            result.append(d)
        elif d.weekday() in (0, 2, 4):
            result.append(d)
    return result


# ── Master download ─────────────────────────────────────────────────────────

def download_all(start: date, end: date, cache_dir_str: str = "backtest/data/cache",
                 fast_mode: bool = False):
    """
    Download all data needed for backtesting the given date range.
    Safe to re-run — skips already-cached files.

    fast_mode: 30s timeout, 1 retry per date. Slow/hanging dates are skipped
               without creating a placeholder so they can be retried in a later
               normal-mode run. Use this to quickly collect all easy dates first.
    """
    cache_dir = Path(cache_dir_str)
    cache_dir.mkdir(parents=True, exist_ok=True)

    trading_days = get_trading_days(start, end, cache_dir)
    print(f"\n{'='*60}")
    print(f"Downloading data: {start} → {end}")
    print(f"Trading days: {len(trading_days)}")
    print(f"Cache: {cache_dir.resolve()}")
    print(f"{'='*60}\n")

    # ── Index data (month by month) ──────────────────────────────────────
    print("── Index data (SPX + VIX) ──────────────────────────────────")
    months_seen = set()
    for d in trading_days:
        key = (d.year, d.month)
        if key not in months_seen:
            months_seen.add(key)
            download_index_month("SPX", d.year, d.month, cache_dir)
            download_index_month("VIX", d.year, d.month, cache_dir)
    print()

    # ── Option chains (parallel download) ───────────────────────────────
    print("── SPXW 0DTE option chains ─────────────────────────────────")

    # Load slow-dates list (dates that timed out in fast mode — skip in fast, retry in normal)
    slow_dates_file = cache_dir / "slow_dates.txt"
    slow_dates: set = set()
    if fast_mode and slow_dates_file.exists():
        slow_dates = {line.strip() for line in slow_dates_file.read_text().splitlines() if line.strip()}

    days_to_download = []
    n_cached = 0
    n_slow = 0
    for d in trading_days:
        path = cache_dir / "options" / f"SPXW_{_date_str(d)}.parquet"
        if path.exists() and pd.read_parquet(path).shape[0] > 0:
            n_cached += 1
        elif fast_mode and _date_str(d) in slow_dates:
            n_slow += 1  # skip in fast mode — will retry in slow pass
        else:
            days_to_download.append(d)

    days_to_download.sort(reverse=True)  # newest first
    slow_note = f", {n_slow} deferred to slow pass" if n_slow else ""
    print(f"  {n_cached} already cached, {len(days_to_download)} to download{slow_note}")

    n_downloaded = 0
    n_failed = 0
    lock = threading.Lock()
    total = len(trading_days)

    def _download_one(d: date) -> tuple:
        ok = download_chain_day(d, cache_dir, fast_mode=fast_mode)
        if not ok:
            if fast_mode:
                # Fast mode: add to slow_dates.txt so it's skipped on future fast-mode
                # restarts. Will be retried when running without --fast.
                with lock:
                    with open(slow_dates_file, "a") as f:
                        f.write(f"{_date_str(d)}\n")
            else:
                # Normal mode: create empty placeholder for permanent skip.
                placeholder = cache_dir / "options" / f"SPXW_{_date_str(d)}.parquet"
                if not placeholder.exists():
                    import pandas as pd
                    pd.DataFrame(columns=["strike", "right", "ms_of_day", "bid", "ask", "mid"]).to_parquet(placeholder, index=False)
        return d, ok

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_download_one, d): d for d in days_to_download}
        for future in as_completed(futures):
            d, ok = future.result()
            with lock:
                if ok:
                    n_downloaded += 1
                else:
                    n_failed += 1
                done = n_cached + n_downloaded + n_failed
                print(f"  [{done}/{total}] {d} {'✓' if ok else '✗'}")

    print(f"\n{'='*60}")
    print(f"Done. {n_downloaded} downloaded, {n_cached} already cached, {n_failed} failed.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import sys
    from datetime import date

    start = date(2022, 5, 16)
    end = date.today()

    if len(sys.argv) >= 3:
        start = date.fromisoformat(sys.argv[1])
        end = date.fromisoformat(sys.argv[2])

    download_all(start, end)
