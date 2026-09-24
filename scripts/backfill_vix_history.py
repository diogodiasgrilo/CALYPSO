"""Backfill daily VIX closes so the percentile gates measure a real year.

WHY THIS EXISTS
---------------
H's IV filter and E's low-IV gate both ask where today's volatility sits in its
own recent history, and both configs request a **252-day** (one trading year)
lookback because that is what "IV percentile" conventionally means. The live-seat
database only began recording in mid-2026, so the gates were actually computing a
**~94-day** percentile while the config said 252 — a four-month reading wearing a
one-year label.

That is not fixable by waiting: the gates would keep making decisions on a short
window for roughly six more months, and every entry taken in the meantime would
carry the caveat permanently. The closes themselves are public, so the honest fix
is to go and get them.

WHAT IT WRITES, AND WHERE IT REFUSES TO
----------------------------------------
Rows go into a **dedicated ``vix_daily`` table**, never into ``market_ticks``.
That boundary is deliberate: ``market_ticks`` is the live seat's own recording of
what the bot saw, it is the input to the fill/slippage analyses, and injecting
synthetic history into it would corrupt a record several other tools treat as
observed fact. ``vix_daily`` is additive, ignorable, and clearly separate.

Backfilled rows are marked ``source='yahoo'`` so they can never be mistaken for
the bot's own observations, and the table is upserted by date so re-running is
harmless.

USAGE
-----
    python -m scripts.backfill_vix_history --db data/variant_b/backtesting.db
    python -m scripts.backfill_vix_history --db ... --days 400 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger("backfill_vix")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vix_daily (
    date   TEXT PRIMARY KEY,
    close  REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'yahoo'
);
"""


def fetch_vix_closes(days: int) -> list:
    """``[(YYYY-MM-DD, close), …]`` oldest first, from Yahoo Finance.

    Uses the same public chart endpoint ``shared/external_price_feed.py`` already
    relies on for its VIX fallback, so this introduces no new dependency and no
    new credential.
    """
    import json
    import urllib.request

    end = int(dt.datetime.now(dt.timezone.utc).timestamp())
    # Generous calendar padding: ~252 TRADING days is ~366 calendar days.
    start = end - int(days * 1.55 + 30) * 86400
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
        f"?period1={start}&period2={end}&interval=1d"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)

    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out = []
    for ts, close in zip(stamps, closes):
        if close is None or close <= 0:
            continue                      # market holiday / bad print
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
        out.append((d, round(float(close), 4)))
    return out


def backfill(db_path: str, days: int = 400, dry_run: bool = False) -> dict:
    rows = fetch_vix_closes(days)
    if not rows:
        raise RuntimeError("Yahoo returned no usable VIX closes")

    if dry_run:
        return {"fetched": len(rows), "written": 0,
                "first": rows[0], "last": rows[-1], "dry_run": True}

    con = sqlite3.connect(db_path, timeout=30)
    try:
        with con:
            con.executescript(SCHEMA)
            con.executemany(
                "INSERT INTO vix_daily (date, close, source) VALUES (?,?,'yahoo') "
                "ON CONFLICT(date) DO UPDATE SET close=excluded.close, "
                "source=excluded.source",
                rows,
            )
        total = con.execute("SELECT COUNT(*) FROM vix_daily").fetchone()[0]
    finally:
        con.close()
    return {"fetched": len(rows), "written": len(rows), "total_rows": total,
            "first": rows[0], "last": rows[-1], "dry_run": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="target backtesting.db")
    ap.add_argument("--days", type=int, default=400,
                    help="trading days to target (default 400 → comfortably >252)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    info = backfill(a.db, days=a.days, dry_run=a.dry_run)
    for k, v in info.items():
        logger.info("%-11s %s", k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
