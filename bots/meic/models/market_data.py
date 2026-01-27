"""
MEIC Market Data Tracking.

This module provides market data tracking with staleness detection
and intraday statistics for the MEIC trading strategy.

Extracted from strategy.py for better code organization.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque, List, Optional, Tuple

from shared.market_hours import get_us_market_time


# Constants
MAX_DATA_STALENESS_SECONDS = 30  # Maximum age for price data before considered stale
FLASH_CRASH_THRESHOLD_PERCENT = 2.0  # 2% move triggers flash crash alert
VELOCITY_WINDOW_MINUTES = 5  # Time window for velocity detection


@dataclass
class MarketData:
    """Tracks market data with staleness detection and intraday statistics."""
    spx_price: float = 0.0
    vix: float = 0.0
    last_spx_update: Optional[datetime] = None
    last_vix_update: Optional[datetime] = None

    # P3: Intraday high/low tracking
    spx_high: float = 0.0
    spx_low: float = float('inf')
    vix_high: float = 0.0
    vix_samples: List[float] = field(default_factory=list)

    # P3: Flash crash velocity tracking
    price_history: Deque[Tuple[datetime, float]] = field(default_factory=lambda: deque(maxlen=100))

    def update_spx(self, price: float):
        """Update SPX price with timestamp and track high/low."""
        if price > 0:
            self.spx_price = price
            self.last_spx_update = get_us_market_time()

            # Track intraday high/low
            if price > self.spx_high:
                self.spx_high = price
            if price < self.spx_low:
                self.spx_low = price

            # Track for velocity detection
            self.price_history.append((self.last_spx_update, price))

    def update_vix(self, vix: float):
        """Update VIX with timestamp and track high/average."""
        if vix > 0:
            self.vix = vix
            self.last_vix_update = get_us_market_time()

            # Track VIX high and samples for average
            if vix > self.vix_high:
                self.vix_high = vix
            self.vix_samples.append(vix)

    def is_spx_stale(self, max_age: int = MAX_DATA_STALENESS_SECONDS) -> bool:
        """Check if SPX data is stale."""
        if not self.last_spx_update:
            return True
        age = (get_us_market_time() - self.last_spx_update).total_seconds()
        return age > max_age

    def is_vix_stale(self, max_age: int = MAX_DATA_STALENESS_SECONDS) -> bool:
        """Check if VIX data is stale."""
        if not self.last_vix_update:
            return True
        age = (get_us_market_time() - self.last_vix_update).total_seconds()
        return age > max_age

    def get_spx_range(self) -> float:
        """Get intraday SPX range (high - low)."""
        if self.spx_low == float('inf') or self.spx_high == 0:
            return 0.0
        return self.spx_high - self.spx_low

    def get_vix_average(self) -> float:
        """Get average VIX for the day."""
        if not self.vix_samples:
            return self.vix
        return sum(self.vix_samples) / len(self.vix_samples)

    def check_flash_crash_velocity(self) -> Tuple[bool, str, float]:
        """
        MKT-002: Check for flash crash conditions (2%+ move in 5 minutes).

        Returns:
            Tuple of (is_flash_crash, direction, percent_change)
        """
        if len(self.price_history) < 2:
            return False, "", 0.0

        now = get_us_market_time()
        window_start = now - timedelta(minutes=VELOCITY_WINDOW_MINUTES)

        # Find the oldest price in the velocity window
        oldest_price = None
        oldest_time = None
        for ts, price in self.price_history:
            if ts >= window_start:
                if oldest_price is None:
                    oldest_price = price
                    oldest_time = ts
                break

        if oldest_price is None or oldest_price == 0:
            return False, "", 0.0

        # Calculate percentage change
        current = self.spx_price
        pct_change = ((current - oldest_price) / oldest_price) * 100

        if abs(pct_change) >= FLASH_CRASH_THRESHOLD_PERCENT:
            direction = "up" if pct_change > 0 else "down"
            return True, direction, pct_change

        return False, "", pct_change

    def reset_daily_tracking(self):
        """Reset intraday tracking for new day."""
        self.spx_high = 0.0
        self.spx_low = float('inf')
        self.vix_high = 0.0
        self.vix_samples.clear()
        self.price_history.clear()
