"""
APOLLO market data — fetches pre-market data from Yahoo Finance.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def fetch_market_snapshot() -> Dict[str, Any]:
    """
    Fetch current market data for the morning briefing.

    Primary: SPX (^GSPC) and VIX — these are what HYDRA trades.
    Secondary: ES futures for overnight gap detection.

    Returns:
        Dict with vix, spx, es_futures, gap_points, gap_pct values.
    """
    spx = _get_yahoo_price("^GSPC")
    es = _get_yahoo_price("ES=F")
    vix = _get_yahoo_price("^VIX")

    snapshot: Dict[str, Any] = {
        "vix": vix,
        "spx": spx,
        "es_futures": es,
        "gap_points": None,
        "gap_pct": None,
    }

    # Calculate overnight gap: ES futures (trading now) vs SPX (last close)
    if spx is not None and es is not None:
        gap = es - spx
        snapshot["gap_points"] = round(gap, 2)
        snapshot["gap_pct"] = round((gap / spx) * 100, 2)

    available = {k: v for k, v in snapshot.items() if v is not None}
    logger.info(f"Market snapshot: {available}")

    return snapshot


def _get_yahoo_price(symbol: str) -> Optional[float]:
    """Fetch a price from Yahoo Finance using ExternalPriceFeed pattern."""
    try:
        from shared.external_price_feed import ExternalPriceFeed

        feed = ExternalPriceFeed()
        price = feed.get_price(symbol)
        if price is not None and price > 0:
            return price
        logger.warning(f"Invalid price for {symbol}: {price}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch {symbol}: {e}")
        return None
