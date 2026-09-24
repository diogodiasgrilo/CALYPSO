"""Where today's volatility sits in its own recent history, and how many days said so.

Extracted 2026-09-24 so **H and E share one definition**. Both sources ask the
same question in the same words — Tompkins wants "IV percentile below ~35%",
OptionsKit wants to enter "when the implied volatility is at the **lower end of
the spectrum**" — and two strategies answering it with two implementations would
be free to drift apart on arithmetic rather than on config, which is the rule
already applied to the expected move shared between H and F.

⚠️ **THIS IS A VIX PROXY, NOT A PER-OPTION IV PERCENTILE.** The 2026-09-23 RTH
probe established that IBKR exposes no per-option IV fields at all, so a true
per-contract IV percentile is not computable in this repo. VIX is a 30-day index
vol. For an S&P-tracking underlying that is a close stand-in for the metric a
retail platform labels "IV percentile" — the UNDERLYING's ~30-day implied vol
ranked over a year — which is almost certainly what both sources are reading off
their screens. Callers must still say "VIX percentile" in anything they record,
so no later analysis can promote the proxy to the real thing.

THE SAMPLE FLOOR IS THE POINT
------------------------------
``iv_percentile`` had no minimum until 2026-09-23. One prior day returns 0.0 or
100.0: a value with the exact shape of a percentile, the full authority of one,
and no information in it at all. A gate comparing that to "below 35%" decides
whether a strategy trades, and nothing about it looks broken from the outside.
Below ``min_history`` these return None — "unknown" — which every caller must
treat as a skip and never as a pass.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def iv_percentile(current_iv: float, history: Sequence[float],
                  min_history: int = 1) -> Optional[float]:
    """Where ``current_iv`` sits within ``history``, as a 0–100 percentile.

    Returns None on an empty history OR on a sample below ``min_history`` —
    "unknown", which a caller must not treat as "passes the filter".
    """
    series = [float(v) for v in history if v is not None and v > 0]
    if len(series) < max(1, int(min_history)):
        return None
    at_or_below = sum(1 for v in series if v <= current_iv)
    return at_or_below / len(series) * 100.0


def iv_percentile_with_n(current_iv: float, history: Sequence[float],
                         min_history: int = 1) -> Tuple[Optional[float], int]:
    """``(percentile, n)`` — the value AND the sample it was computed over.

    A bare percentile is uninterpretable after the fact: a stored ``11.9`` could
    be a one-year reading or a three-day one, and the difference decides whether
    a later analysis means anything. Callers record ``n`` beside the value.
    """
    series = [float(v) for v in history if v is not None and v > 0]
    return iv_percentile(current_iv, series, min_history=min_history), len(series)


def vix_history_from_db(db_path: Optional[str], before_date: str,
                        lookback: int = 252) -> List[float]:
    """One VIX close per prior trading day, oldest first. ``[]`` on any failure.

    Read-only and exception-swallowing: a volatility filter must degrade to
    "unknown" rather than break an entry path.

    ``before_date`` is exclusive — today's own ticks would rank the current
    reading against itself and drag the percentile toward the middle on every
    tick.

    TWO SOURCES, UNIONED BY DATE. ``market_ticks`` holds only what the bot itself
    observed — the live seat began mid-2026, so it could supply ~94 days against
    a 252-day request, i.e. a four-month percentile wearing a one-year label.
    ``vix_daily`` is the backfill (``scripts/backfill_vix_history.py``, Yahoo
    closes, marked ``source='yahoo'``) and reaches back years.

    ``vix_daily`` wins on dates both hold: it is the official close, where
    ``market_ticks`` has whatever tick happened to land last that day. Dates only
    ``market_ticks`` has are still used, so a session the backfill has not caught
    up to is never dropped.
    """
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            by_date = {}
            # Bot-observed first, so the backfill overwrites on collision.
            for src, sql in (
                ("ticks",
                 "SELECT d, vix_level FROM ("
                 "  SELECT date(timestamp) AS d, vix_level,"
                 "         ROW_NUMBER() OVER (PARTITION BY date(timestamp)"
                 "                            ORDER BY timestamp DESC) AS rn"
                 "  FROM market_ticks WHERE vix_level > 0 AND date(timestamp) < ?"
                 ") WHERE rn = 1"),
                ("yahoo",
                 "SELECT date, close FROM vix_daily WHERE close > 0 AND date < ?"),
            ):
                try:
                    for d, v in con.execute(sql, (before_date,)):
                        if v and float(v) > 0:
                            by_date[d] = float(v)
                except sqlite3.Error:
                    # vix_daily may not exist yet (pre-backfill); market_ticks
                    # may be absent on an isolated DB. Either alone is usable.
                    logger.debug("VIX source %s unavailable in %s", src, db_path)
            if not by_date:
                return []
            newest = sorted(by_date)[-int(lookback):]
            return [by_date[d] for d in newest]
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001 — a filter must not break entry
        logger.warning("VIX history unavailable for the percentile: %s", e)
        return []
