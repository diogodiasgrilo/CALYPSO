"""
APOLLO market data — fetches pre-market data from Yahoo Finance.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def fetch_market_snapshot() -> Dict[str, Any]:
    """
    Fetch current market data for the morning briefing.

    Returns:
        Dict with vix, spy, es_futures values (None if unavailable).
    """
    snapshot = {
        "vix": _get_yahoo_price("^VIX"),
        "spy": _get_yahoo_price("SPY"),
        "es_futures": _get_yahoo_price("ES=F"),
    }

    available = {k: v for k, v in snapshot.items() if v is not None}
    logger.info(f"Market snapshot: {available}")

    return snapshot


def _get_yahoo_price(symbol: str) -> Optional[float]:
    """Fetch a price from Yahoo Finance using ExternalPriceFeed pattern."""
    try:
        from shared.external_price_feed import ExternalPriceFeed

        feed = ExternalPriceFeed()
        price = feed.get_price(symbol)
        if price and price > 0:
            return price
        logger.warning(f"Invalid price for {symbol}: {price}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch {symbol}: {e}")
        return None
