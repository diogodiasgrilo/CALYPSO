#!/usr/bin/env python3
"""
External Price Feed Module

Provides fallback price data from external sources (Yahoo Finance)
for use in Saxo simulation environment where US equity data has NoAccess.

Only used when:
1. Environment is "sim"
2. Saxo API returns NoAccess for the instrument
3. External feeds are enabled in config

For live trading, this module is NOT used.
"""

import requests
import json
import time
from typing import Optional, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ExternalPriceFeed:
    """
    Fetches prices from external sources when Saxo returns NoAccess.

    Currently supports:
    - Yahoo Finance (free, 15-min delayed)
    """

    def __init__(self, enabled: bool = True):
        """
        Initialize external price feed.

        Args:
            enabled: Whether to use external feeds (should be True only in sim)
        """
        self.enabled = enabled
        self.cache = {}  # Cache prices to avoid rate limits
        self.cache_ttl = 60  # Cache for 60 seconds

        if self.enabled:
            logger.warning("="*70)
            logger.warning("EXTERNAL PRICE FEED ENABLED")
            logger.warning("Using Yahoo Finance for SPY/VIX prices (15-min delayed)")
            logger.warning("This is ONLY for simulation/testing purposes")
            logger.warning("Real trading will use Saxo's live price feed")
            logger.warning("="*70)

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Get current price for a symbol from external source.

        Args:
            symbol: Stock symbol (e.g., "SPY", "^VIX")

        Returns:
            float: Current price, or None if unavailable
        """
        if not self.enabled:
            return None

        # Check cache first
        if symbol in self.cache:
            cached_price, cached_time = self.cache[symbol]
            if time.time() - cached_time < self.cache_ttl:
                return cached_price

        # Fetch from Yahoo Finance
        price = self._fetch_from_yahoo(symbol)

        if price:
            # Cache the result
            self.cache[symbol] = (price, time.time())
            logger.info(f"External feed: {symbol} = ${price:.2f} (Yahoo Finance)")
        else:
            logger.error(f"External feed: Failed to fetch price for {symbol}")

        return price

    def _fetch_from_yahoo(self, symbol: str) -> Optional[float]:
        """
        Fetch price from Yahoo Finance API.

        Yahoo Finance uses ^VIX for VIX index.

        Args:
            symbol: Stock symbol (SPY) or ^VIX

        Returns:
            float: Current price or None
        """
        try:
            # Convert VIX.I to ^VIX for Yahoo Finance
            if symbol == "VIX.I" or symbol == "VIX":
                yahoo_symbol = "^VIX"
            else:
                yahoo_symbol = symbol

            # Use Yahoo Finance v8 API (unofficial but widely used)
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
            params = {
                "interval": "1m",
                "range": "1d"
            }

            headers = {
                "User-Agent": "Mozilla/5.0 (Calypso Trading Bot)"
            }

            response = requests.get(url, params=params, headers=headers, timeout=5)

            if response.status_code == 200:
                data = response.json()

                # Extract current price from the chart data
                chart = data.get("chart", {})
                result = chart.get("result", [])

                if result and len(result) > 0:
                    meta = result[0].get("meta", {})

                    # Try regularMarketPrice first (most accurate during market hours)
                    price = meta.get("regularMarketPrice")

                    # Fallback to previousClose if market is closed
                    if not price:
                        price = meta.get("previousClose")

                    # Last fallback: latest quote from indicators
                    if not price:
                        indicators = result[0].get("indicators", {})
                        quote = indicators.get("quote", [])
                        if quote and len(quote) > 0:
                            close_prices = quote[0].get("close", [])
                            # Get the last non-None close price
                            close_prices = [p for p in close_prices if p is not None]
                            if close_prices:
                                price = close_prices[-1]

                    if price:
                        return float(price)

            logger.warning(f"Yahoo Finance returned status {response.status_code} for {yahoo_symbol}")
            return None

        except requests.exceptions.Timeout:
            logger.error(f"Timeout fetching {symbol} from Yahoo Finance")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching {symbol} from Yahoo Finance: {e}")
            return None
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Error parsing Yahoo Finance response for {symbol}: {e}")
            return None

    def get_spy_price(self) -> Optional[float]:
        """Convenience method to get SPY price."""
        return self.get_price("SPY")

    def get_vix_price(self) -> Optional[float]:
        """Convenience method to get VIX price."""
        return self.get_price("VIX.I")

    def clear_cache(self):
        """Clear the price cache."""
        self.cache.clear()
        logger.debug("External price feed cache cleared")


# Test function
def test_external_feed():
    """Test the external price feed."""
    print("\n" + "="*70)
    print("TESTING EXTERNAL PRICE FEED (Yahoo Finance)")
    print("="*70)

    feed = ExternalPriceFeed(enabled=True)

    print("\n1. Fetching SPY price...")
    spy_price = feed.get_spy_price()
    if spy_price:
        print(f"   ✅ SPY: ${spy_price:.2f}")
    else:
        print("   ❌ Failed to fetch SPY price")

    print("\n2. Fetching VIX price...")
    vix_price = feed.get_vix_price()
    if vix_price:
        print(f"   ✅ VIX: {vix_price:.2f}")
    else:
        print("   ❌ Failed to fetch VIX price")

    print("\n3. Testing cache (should be instant)...")
    start = time.time()
    spy_cached = feed.get_spy_price()
    elapsed = time.time() - start
    print(f"   ✅ SPY (cached): ${spy_cached:.2f} - fetched in {elapsed*1000:.1f}ms")

    print("\n" + "="*70)
    print("External price feed is working!" if spy_price and vix_price else "Some prices failed")
    print("="*70)


if __name__ == "__main__":
    test_external_feed()
