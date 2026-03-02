"""
strategy.py - 0DTE Iron Fly Strategy Implementation (Doc Severson + Jim Olson)

This module implements the 0DTE Iron Fly strategy:
- Entry at 10:00 AM EST after opening range check
- Iron Butterfly: Sell ATM call+put, buy wings at expected move OR min 40pt (Jim Olson)
- VIX filter: abort if VIX > 20 or spiking 5%+
- Opening range filter: price must be within 9:30-10:00 high/low
- Take profit: 30% of credit received (dynamic) with $25 minimum floor
- Stop loss: when SPX touches wing strikes (market order)
- Max hold: 60 minutes (11:00 AM rule)

Strategy Sources:
- Doc Severson 0DTE Iron Fly: https://www.youtube.com/watch?v=ad27qIuhgQ4
- Jim Olson Wing Width Rules: https://0dte.com/jim-olson-iron-butterfly-0dte-trade-plan
- Full spec: docs/IRON_FLY_STRATEGY_SPECIFICATION.md

Author: Trading Bot Developer
Date: 2025

Security Audit: 2026-01-19
- Added position reconciliation with broker on startup
- Added market data staleness detection
- Fixed state machine stuck states
- Added emergency stop-loss bypass for circuit breaker
- Fixed timezone handling for hold time calculations
- Added max trades per day guard
- Fixed dry-run simulation for realistic P&L

Edge Case Audit: 2026-01-22 to 2026-01-23
- 64 edge cases analyzed and resolved
- Added circuit breaker with sliding window failure detection (CONN-002)
- Added critical intervention flag for unrecoverable errors (ORDER-004)
- Added partial fill auto-unwind with actual UICs (ORDER-001, CB-001)
- Added stop loss retry escalation (5 retries per leg) (STOP-002)
- Added daily circuit breaker escalation (halt after 3 opens) (CB-004)
- Added flash crash velocity detection (MKT-001)
- Added market halt detection from error messages (MKT-002)
- Added extreme spread warning during exit (MKT-004)
- Added VIX re-check before order placement (FILTER-001)
- Added multi-year FOMC/economic calendar support (FILTER-002/003)
- Added position metadata persistence for crash recovery (POS-001)
- Added multiple iron fly detection and auto-selection (POS-004)
- Added pending order check on startup with auto-cancel (ORDER-006)
- Added timed-out order cancellation with retry logic (ORDER-007/008)

Code Audit: 2026-01-26
- Removed unused _pending_order_ids and _filled_orders variables
- Consolidated duplicate get_us_market_time() to use shared.market_hours
- Removed unused json import from main.py

Code Audit: 2026-02-02 (Wing Width + P&L Fixes)
- Added minimum wing width enforcement (Jim Olson: 40pt floor)
- Added dynamic profit target (30% of credit instead of fixed $75)
- Fixed fill price extraction from activities endpoint (FilledPrice field)
- Added commission tracking for accurate net P&L
- Added activities endpoint retry for sync delay (4 retries x 1.5s)

Multi-Bot Isolation: 2026-02-04 (POS-005)
- Added Position Registry integration for SPX multi-bot safety
- Iron Fly and MEIC can now both trade SPX 0DTE without interference
- Positions registered on fill, unregistered on close
- Reconciliation filters by registry first (fallback to strike detection for legacy)

See docs/IRON_FLY_EDGE_CASES.md for full edge case analysis.
See docs/IRON_FLY_STRATEGY_SPECIFICATION.md for full strategy rules.
"""

import json
import logging
import math
import os
import time
from collections import deque
from datetime import datetime, timedelta, date, time as dt_time
from typing import Optional, Dict, List, Any, Tuple, Deque
from dataclasses import dataclass
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell, OrderType
from shared.alert_service import AlertService, AlertType, AlertPriority
from shared.market_hours import get_us_market_time
from shared.position_registry import PositionRegistry

# Path for persistent metrics storage
METRICS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "iron_fly_metrics.json"
)

# POS-001: Path for position metadata persistence (for crash recovery)
POSITION_METADATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "iron_fly_position.json"
)

# Configure module logger
logger = logging.getLogger(__name__)

# =============================================================================
# SAFETY CONSTANTS
# =============================================================================
MAX_TRADES_PER_DAY = 1  # Iron fly should only enter once per day
MAX_DATA_STALENESS_SECONDS = 30  # Max age of market data before considered stale
MAX_CLOSING_TIMEOUT_SECONDS = 300  # 5 minutes max to close position
WING_BREACH_TOLERANCE = 0.10  # $0.10 tolerance for float comparison
DEFAULT_SIMULATED_CREDIT_PER_WING_POINT = 2.50  # Realistic: $2.50 per point of wing width

# Order execution safety constants (matching Delta Neutral bot)
# Market order fill verification timeout: 30s hardcoded in _verify_order_fill()
EMERGENCY_ORDER_TIMEOUT_SECONDS = 30  # Timeout for emergency/market orders
EMERGENCY_SLIPPAGE_PERCENT = 5.0  # 5% slippage tolerance in emergency mode
MAX_CONSECUTIVE_FAILURES = 5  # Trigger circuit breaker after this many failures
CIRCUIT_BREAKER_COOLDOWN_MINUTES = 5  # Cooldown period when circuit breaker opens

# CONN-002: Sliding window failure counter (catches intermittent failures)
SLIDING_WINDOW_SIZE = 10  # Track last 10 API calls
SLIDING_WINDOW_FAILURE_THRESHOLD = 5  # Trigger if 5+ of last 10 fail

# CONN-007: Data blackout emergency close threshold
MAX_STALE_DATA_WARNINGS_BEFORE_EMERGENCY = 5  # Trigger emergency close after 5 consecutive failures

# MKT-001: Flash crash detection constants
FLASH_CRASH_WINDOW_MINUTES = 5  # Track price over last 5 minutes
FLASH_CRASH_THRESHOLD_PERCENT = 2.0  # Alert if price moves 2%+ in window

# TIME-003: Early close days (1:00 PM ET close instead of 4:00 PM)
# Day before Independence Day, day after Thanksgiving, Christmas Eve, New Year's Eve
# Multi-year support to avoid yearly maintenance gaps
EARLY_CLOSE_DATES = {
    2026: [
        date(2026, 7, 3),    # Day before July 4th
        date(2026, 11, 27),  # Day after Thanksgiving (Black Friday)
        date(2026, 12, 24),  # Christmas Eve
        date(2026, 12, 31),  # New Year's Eve
    ],
    2027: [
        date(2027, 7, 2),    # Day before July 4th (July 4th is Sunday, observed Monday)
        date(2027, 11, 26),  # Day after Thanksgiving (Black Friday)
        date(2027, 12, 24),  # Christmas Eve
        date(2027, 12, 31),  # New Year's Eve
    ],
}
EARLY_CLOSE_TIME = dt_time(13, 0)  # 1:00 PM ET
EARLY_CLOSE_CUTOFF_MINUTES = 15  # Stop trading 15 min before early close (12:45 PM)

# MAX-LOSS: Absolute max loss circuit breaker (per contract)
# If unrealized P&L drops below this, emergency close regardless of wing position
# This protects against gaps through wings or illiquid stop fills
MAX_LOSS_PER_CONTRACT = 400.0  # $400 max loss per contract (above typical $300-350 stop)

# ORDER-005: Bid-ask spread validation
# If spread exceeds this percentage, log warning (and optionally block entry)
DEFAULT_MAX_BID_ASK_SPREAD_PERCENT = 20.0  # 20% max spread before warning

# MKT-002: Market halt detection keywords
# These keywords in error messages indicate exchange-wide trading halt
MARKET_HALT_KEYWORDS = [
    "halt", "halted", "suspended", "circuit breaker", "trading pause",
    "market closed", "exchange closed", "trading stopped", "luld",  # LULD = Limit Up Limit Down
]

# CB-004: Daily circuit breaker escalation
# If circuit breaker opens too many times in one day, halt for rest of day
MAX_CIRCUIT_BREAKER_OPENS_PER_DAY = 3  # After 3 CB opens, halt permanently for the day

# STOP-002: Stop loss retry configuration during API outage
STOP_LOSS_MAX_RETRIES = 5  # Retry stop loss this many times before giving up
STOP_LOSS_RETRY_DELAY_SECONDS = 2  # Delay between stop loss retries

# MKT-004: Extreme spread warning thresholds (for exit monitoring)
EXTREME_SPREAD_WARNING_PERCENT = 50.0  # Log warning if spread > 50% during exit
EXTREME_SPREAD_CRITICAL_PERCENT = 100.0  # Log critical if spread > 100%

def load_cumulative_metrics() -> Dict[str, Any]:
    """
    Load cumulative metrics from persistent storage.

    Returns:
        dict: Cumulative metrics including total P&L, win rate, etc.
    """
    try:
        if os.path.exists(METRICS_FILE):
            with open(METRICS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load cumulative metrics: {e}")

    # Return default metrics if file doesn't exist or failed to load
    return {
        "cumulative_pnl": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_premium_collected": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "last_updated": None
    }


def save_cumulative_metrics(metrics: Dict[str, Any]) -> bool:
    """
    Save cumulative metrics to persistent storage.

    Args:
        metrics: Dictionary of cumulative metrics

    Returns:
        bool: True if save successful
    """
    try:
        # Ensure data directory exists
        data_dir = os.path.dirname(METRICS_FILE)
        os.makedirs(data_dir, exist_ok=True)

        # Update timestamp
        metrics["last_updated"] = get_us_market_time().isoformat()

        with open(METRICS_FILE, 'w') as f:
            json.dump(metrics, f, indent=2)

        logger.info(f"Saved cumulative metrics: P&L=${metrics.get('cumulative_pnl', 0):.2f}")
        return True

    except Exception as e:
        logger.error(f"Failed to save cumulative metrics: {e}")
        return False


def get_eastern_timestamp() -> datetime:
    """
    Get current timestamp in US Eastern timezone for consistent logging.
    All timestamps in this strategy use Eastern time since that's when US markets operate.
    """
    return get_us_market_time()


@dataclass
class MarketData:
    """
    Tracks market data with staleness detection.

    This prevents trading decisions based on old/stale price data,
    which could lead to missed stop-losses or incorrect entry decisions.

    All timestamps use US Eastern time for consistency.
    """
    price: float = 0.0
    vix: float = 0.0
    last_price_update: Optional[datetime] = None
    last_vix_update: Optional[datetime] = None

    def update_price(self, price: float):
        """Update price with current Eastern timestamp."""
        if price > 0:
            self.price = price
            self.last_price_update = get_eastern_timestamp()

    def update_vix(self, vix: float):
        """Update VIX with current Eastern timestamp."""
        if vix > 0:
            self.vix = vix
            self.last_vix_update = get_eastern_timestamp()

    def is_price_stale(self, max_age_seconds: int = MAX_DATA_STALENESS_SECONDS) -> bool:
        """Check if price data is stale."""
        if not self.last_price_update:
            return True
        now = get_eastern_timestamp()
        # Handle timezone-aware comparison
        if self.last_price_update.tzinfo and now.tzinfo:
            age = (now - self.last_price_update).total_seconds()
        else:
            # Fallback for naive datetimes
            age = (datetime.now() - self.last_price_update.replace(tzinfo=None) if self.last_price_update.tzinfo else datetime.now() - self.last_price_update).total_seconds()
        return age > max_age_seconds

    def is_vix_stale(self, max_age_seconds: int = MAX_DATA_STALENESS_SECONDS) -> bool:
        """Check if VIX data is stale."""
        if not self.last_vix_update:
            return True
        now = get_eastern_timestamp()
        if self.last_vix_update.tzinfo and now.tzinfo:
            age = (now - self.last_vix_update).total_seconds()
        else:
            age = (datetime.now() - self.last_vix_update.replace(tzinfo=None) if self.last_vix_update.tzinfo else datetime.now() - self.last_vix_update).total_seconds()
        return age > max_age_seconds

    def price_age_seconds(self) -> float:
        """Get age of price data in seconds."""
        if not self.last_price_update:
            return float('inf')
        now = get_eastern_timestamp()
        if self.last_price_update.tzinfo and now.tzinfo:
            return (now - self.last_price_update).total_seconds()
        return (datetime.now() - self.last_price_update.replace(tzinfo=None) if self.last_price_update.tzinfo else datetime.now() - self.last_price_update).total_seconds()

    def vix_age_seconds(self) -> float:
        """Get age of VIX data in seconds."""
        if not self.last_vix_update:
            return float('inf')
        now = get_eastern_timestamp()
        if self.last_vix_update.tzinfo and now.tzinfo:
            return (now - self.last_vix_update).total_seconds()
        return (datetime.now() - self.last_vix_update.replace(tzinfo=None) if self.last_vix_update.tzinfo else datetime.now() - self.last_vix_update).total_seconds()


class IronFlyState(Enum):
    """States of the 0DTE Iron Fly strategy."""
    IDLE = "Idle"                                  # No position, waiting for market open
    WAITING_OPENING_RANGE = "WaitingOpeningRange"  # Monitoring 9:30-10:00 AM
    READY_TO_ENTER = "ReadyToEnter"                # Opening range captured, checking filters
    POSITION_OPEN = "PositionOpen"                 # Iron fly position active
    MONITORING_EXIT = "MonitoringExit"             # Watching for profit/stop/time exit
    CLOSING = "Closing"                            # Executing close orders
    DAILY_COMPLETE = "DailyComplete"               # Done trading for today


@dataclass
class OpeningRange:
    """
    Tracks the opening range (9:30-10:00 AM EST).

    The opening range is the high and low of the first 30 minutes of trading.
    Doc Severson's strategy requires price to be WITHIN this range at 10:00 AM
    for an entry to be valid. If price breaks out of the range before 10:00 AM,
    it's likely a "trend day" and we should NOT enter.

    Attributes:
        high: Highest price during opening range
        low: Lowest price during opening range
        opening_vix: VIX at market open (9:30)
        current_vix: Latest VIX reading
        vix_high: Highest VIX during opening range (for spike detection)
        is_complete: Whether the 30 minutes have elapsed
        start_time: When we started tracking
    """
    high: float = 0.0
    low: float = float('inf')
    opening_vix: float = 0.0
    current_vix: float = 0.0
    vix_high: float = 0.0
    is_complete: bool = False
    start_time: Optional[datetime] = None

    def update(self, price: float, vix: float):
        """Update opening range with new price and VIX data."""
        # Guard against invalid/zero prices
        if price > 0:
            if price > self.high:
                self.high = price
            if price < self.low:
                self.low = price

        # Guard against invalid/zero VIX
        if vix > 0:
            self.current_vix = vix
            if vix > self.vix_high:
                self.vix_high = vix
            # Set opening VIX if not yet set
            if self.opening_vix <= 0:
                self.opening_vix = vix

    @property
    def range_width(self) -> float:
        """Calculate the width of the opening range in points."""
        if self.low == float('inf'):
            return 0.0
        return self.high - self.low

    @property
    def midpoint(self) -> float:
        """Calculate the midpoint of the opening range."""
        if self.low == float('inf'):
            return 0.0
        return (self.high + self.low) / 2

    @property
    def vix_spike_percent(self) -> float:
        """Calculate VIX spike percentage from opening."""
        if self.opening_vix <= 0:
            return 0.0
        return ((self.vix_high - self.opening_vix) / self.opening_vix) * 100

    def is_price_in_range(self, price: float) -> bool:
        """Check if price is within the opening range."""
        return self.low <= price <= self.high

    def distance_from_midpoint(self, price: float) -> float:
        """Calculate distance from range midpoint (positive = above, negative = below)."""
        return price - self.midpoint

    def price_position_percent(self, price: float) -> float:
        """
        Calculate where price is within the opening range as a percentage.

        Returns:
            float: Percentage position within the range:
                - 0% = at the low
                - 50% = at the midpoint (ideal for entry)
                - 100% = at the high
                - <0% = below the range (bearish breakout - trend day)
                - >100% = above the range (bullish breakout - trend day)

        If range is not yet established (high=0 or low=inf), returns 50% (neutral).
        """
        if self.high <= 0 or self.low == float('inf') or self.range_width <= 0:
            return 50.0  # Neutral if range not established

        return ((price - self.low) / self.range_width) * 100


@dataclass
class IronFlyPosition:
    """
    Represents an Iron Fly (Iron Butterfly) position.

    Structure (all same expiration - 0DTE):
    - Short ATM Call (sold)
    - Short ATM Put (sold)
    - Long OTM Call (bought for upper wing protection)
    - Long OTM Put (bought for lower wing protection)

    The short strikes are at the same ATM strike (hence "butterfly").
    The long strikes protect against unlimited loss.

    Max profit: Net credit received (when price expires exactly at ATM strike)
    Max loss: Wing width - Net credit (when price expires beyond a wing)

    All timestamps use US Eastern time for consistency with market hours.

    Attributes:
        atm_strike: The ATM strike price (center/body of butterfly)
        upper_wing: Long call strike (upper protection)
        lower_wing: Long put strike (lower protection)
        entry_time: When position was opened (US Eastern timezone)
        entry_price: Underlying price at entry
        credit_received: Net premium received (in dollars)
        quantity: Number of contracts
        expiry: Expiration date string (YYYY-MM-DD)
    """
    atm_strike: float = 0.0
    upper_wing: float = 0.0
    lower_wing: float = 0.0
    entry_time: Optional[datetime] = None  # Always US Eastern time
    entry_price: float = 0.0
    credit_received: float = 0.0
    quantity: int = 1
    expiry: str = ""

    # Position IDs from broker (for order management)
    short_call_id: Optional[str] = None
    short_put_id: Optional[str] = None
    long_call_id: Optional[str] = None
    long_put_id: Optional[str] = None

    # UICs for streaming price updates
    short_call_uic: Optional[int] = None
    short_put_uic: Optional[int] = None
    long_call_uic: Optional[int] = None
    long_put_uic: Optional[int] = None

    # Current option prices (updated via streaming or polling)
    short_call_price: float = 0.0
    short_put_price: float = 0.0
    long_call_price: float = 0.0
    long_put_price: float = 0.0

    # Order tracking
    profit_order_id: Optional[str] = None

    # Close order tracking (for verification during CLOSING state)
    close_order_ids: Optional[Dict[str, str]] = None  # {"short_call": "order_id", ...}
    close_legs_verified: Optional[Dict[str, bool]] = None  # Track which legs are verified closed

    # Dry-run simulation tracking
    simulated_current_value: float = 0.0  # Tracks simulated cost-to-close

    @property
    def wing_width(self) -> float:
        """Calculate the width of each wing (distance from ATM to wing strike)."""
        return self.upper_wing - self.atm_strike

    @property
    def is_complete(self) -> bool:
        """Check if all four legs are filled."""
        return all([
            self.short_call_id,
            self.short_put_id,
            self.long_call_id,
            self.long_put_id
        ])

    @property
    def current_value(self) -> float:
        """
        Calculate current position value (cost to close).

        For iron fly, we want to close for LESS than we received.
        Current value = cost to buy back shorts - proceeds from selling longs

        In dry-run mode, uses simulated_current_value if option prices aren't set.
        """
        # If we have real option prices, use them
        if any([self.short_call_price, self.short_put_price,
                self.long_call_price, self.long_put_price]):
            short_value = (self.short_call_price + self.short_put_price) * self.quantity * 100
            long_value = (self.long_call_price + self.long_put_price) * self.quantity * 100
            return short_value - long_value
        # Otherwise use simulated value for dry-run
        return self.simulated_current_value

    @property
    def unrealized_pnl(self) -> float:
        """
        Calculate unrealized P&L.

        Profit = Credit received - Cost to close
        If current_value < credit_received, we're profitable.
        """
        return self.credit_received - self.current_value

    @property
    def max_profit(self) -> float:
        """Maximum possible profit (entire credit received)."""
        return self.credit_received

    @property
    def max_loss(self) -> float:
        """Maximum possible loss (wing width * 100 - credit received)."""
        return (self.wing_width * self.quantity * 100) - self.credit_received

    @property
    def hold_time_minutes(self) -> int:
        """
        Calculate minutes position has been held.

        Uses US Eastern time consistently for all calculations.
        """
        if not self.entry_time:
            return 0

        now_eastern = get_eastern_timestamp()

        # Handle timezone-aware comparison
        if self.entry_time.tzinfo and now_eastern.tzinfo:
            return int((now_eastern - self.entry_time).total_seconds() / 60)

        # If entry_time is naive, compare with naive now
        if self.entry_time.tzinfo is None:
            # Use naive datetime for comparison
            now_naive = datetime.now()
            return int((now_naive - self.entry_time).total_seconds() / 60)

        # entry_time has tzinfo but now doesn't - strip tzinfo for comparison
        entry_naive = self.entry_time.replace(tzinfo=None)
        return int((datetime.now() - entry_naive).total_seconds() / 60)

    @property
    def hold_time_seconds(self) -> float:
        """Calculate seconds position has been held (for more precise tracking)."""
        if not self.entry_time:
            return 0.0

        now_eastern = get_eastern_timestamp()

        # Handle timezone-aware comparison
        if self.entry_time.tzinfo and now_eastern.tzinfo:
            return (now_eastern - self.entry_time).total_seconds()

        # Fallback for naive datetimes
        if self.entry_time.tzinfo is None:
            return (datetime.now() - self.entry_time).total_seconds()

        entry_naive = self.entry_time.replace(tzinfo=None)
        return (datetime.now() - entry_naive).total_seconds()

    def distance_to_wing(self, current_price: float) -> Tuple[float, str]:
        """
        Calculate distance to nearest wing strike.

        Returns:
            Tuple of (distance in points, which wing is closer: "upper" or "lower")

        Note: Negative distance means price has breached the wing!
        """
        dist_to_upper = self.upper_wing - current_price
        dist_to_lower = current_price - self.lower_wing

        if dist_to_upper < dist_to_lower:
            return (dist_to_upper, "upper")
        else:
            return (dist_to_lower, "lower")

    def is_wing_breached(self, current_price: float, tolerance: float = WING_BREACH_TOLERANCE) -> Tuple[bool, str]:
        """
        Check if price has touched or breached a wing.

        Uses tolerance to avoid floating-point comparison issues.

        Args:
            current_price: Current underlying price
            tolerance: Price tolerance for breach detection (default $0.10)

        Returns:
            Tuple of (breached: bool, which_wing: str)
        """
        # Use tolerance to handle floating-point comparison safely
        if current_price >= (self.upper_wing - tolerance):
            return (True, "upper")
        elif current_price <= (self.lower_wing + tolerance):
            return (True, "lower")
        return (False, "")


class IronFlyStrategy:
    """
    0DTE Iron Fly Strategy Implementation.

    This implements Doc Severson's "18-Minute" 0DTE Iron Fly strategy.

    Entry Rules:
    1. Wait for 10:00 AM EST (after 30-min opening range)
    2. VIX must be < 20 and not spiking > 5% during opening range
    3. Current price must be WITHIN opening range (not broken out)
    4. Ideally, price should be near the midpoint of the range
    5. Sell ATM straddle, buy wings at expected move distance

    Exit Rules:
    1. Take profit: $50-$100 per contract (place limit order immediately)
    2. Stop loss: SPX touches either wing strike (market order - punch out!)
    3. Time exit: Max hold time reached (e.g., 60 minutes or by 11:00 AM)

    Key Metrics (from Doc Severson):
    - Risk Rating: 4/10 (Moderate)
    - Win Rate Target: 85-95%
    - Profit Factor Target: > 2.0
    - Average Hold Time: 18 minutes
    """

    def __init__(
        self,
        saxo_client: SaxoClient,
        config: Dict[str, Any],
        logger_service: Any,
        dry_run: bool = False,
        alert_service: Optional[AlertService] = None
    ):
        """
        Initialize the 0DTE Iron Fly strategy.

        Args:
            saxo_client: Authenticated Saxo API client
            config: Strategy configuration dictionary
            logger_service: Trade logging service (Google Sheets, etc.)
            dry_run: If True, simulate trades without placing real orders
            alert_service: Optional AlertService for Telegram/Email notifications
        """
        self.client = saxo_client
        self.config = config
        self.trade_logger = logger_service
        self.dry_run = dry_run

        # Alert service for Telegram/Email notifications
        # If not provided, create one from config (will auto-disable if not configured)
        if alert_service:
            self.alert_service = alert_service
        else:
            self.alert_service = AlertService(config, "IRON_FLY")

        # Strategy configuration
        self.strategy_config = config.get("strategy", {})
        self.underlying_symbol = self.strategy_config.get("underlying_symbol", "SPX:xcbf")
        self.underlying_uic = self.strategy_config.get("underlying_uic", 120)
        # VIX spot UIC for price monitoring (StockIndex type)
        self.vix_uic = self.strategy_config.get("vix_spot_uic", self.strategy_config.get("vix_uic", 10606))
        # Options UIC for SPXW
        self.options_uic = self.strategy_config.get("options_uic", 128)

        # Entry parameters - parse from config (format: "HH:MM")
        entry_time_str = self.strategy_config.get("entry_time_est", "10:00")
        entry_parts = entry_time_str.split(":")
        self.entry_time = dt_time(int(entry_parts[0]), int(entry_parts[1]))
        self.max_vix = self.strategy_config.get("max_vix_entry", 20.0)
        self.vix_spike_threshold = self.strategy_config.get("vix_spike_threshold_percent", 5.0)

        # Exit parameters
        # FIX (2026-01-31): Support dynamic profit target as percentage of credit
        # If profit_target_percent is set, use that. Otherwise fall back to fixed dollar amount.
        self.profit_target_percent = self.strategy_config.get("profit_target_percent", None)
        self.profit_target_fixed = self.strategy_config.get("profit_target_per_contract", 75.0)
        self.profit_target_min = self.strategy_config.get("profit_target_min", 25.0)  # Floor
        self.max_hold_minutes = self.strategy_config.get("max_hold_minutes", 60)
        self.position_size = self.strategy_config.get("position_size", 1)

        # Commission tracking (2026-02-01)
        # Iron Fly has 4 legs, each with $2.50 open + $2.50 close = $5 round-trip per leg
        # Total commission per trade = 4 legs × $5 = $20
        self.commission_per_leg = self.strategy_config.get("commission_per_leg", 5.0)
        self.num_legs = 4  # Iron Fly always has 4 legs

        # Calibration mode: allow manual expected move override
        self.manual_expected_move = self.strategy_config.get("manual_expected_move", None)

        # Wing width configuration (Jim Olson rules)
        # Minimum wing width: If expected move is low, enforce a floor (default 40 points)
        self.min_wing_width = self.strategy_config.get("min_wing_width", 40)
        # Target credit as percentage of wing width (Jim Olson: 30-35%)
        self.target_credit_percent = self.strategy_config.get("target_credit_percent", 30)

        # ORDER-005: Bid-ask spread validation
        self.max_bid_ask_spread_percent = self.strategy_config.get(
            "max_bid_ask_spread_percent", DEFAULT_MAX_BID_ASK_SPREAD_PERCENT
        )

        # Filter configuration
        self.filters_config = config.get("filters", {})
        self.fed_meeting_blackout = self.filters_config.get("fed_meeting_blackout", True)
        self.economic_calendar_check = self.filters_config.get("economic_calendar_check", True)
        self.require_price_in_range = self.filters_config.get("require_price_in_range", True)
        self.require_price_near_midpoint = self.filters_config.get("require_price_near_midpoint", True)
        # How close to midpoint is "near"? Default 70% = price must be within middle 70% of range
        # If range is 20 pts, price must be within 7 pts of midpoint (not in outer 15% on each side)
        self.midpoint_tolerance_percent = self.filters_config.get("midpoint_tolerance_percent", 70.0)

        # State
        self.state = IronFlyState.IDLE
        self.opening_range = OpeningRange()
        self.position: Optional[IronFlyPosition] = None

        # Market data with staleness tracking (BUG FIX: prevents stale data decisions)
        self.market_data = MarketData()
        self.current_price = 0.0  # Kept for backwards compatibility
        self.current_vix = 0.0    # Kept for backwards compatibility

        # Daily tracking
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.daily_premium_collected = 0.0  # Track premium separately for accurate logging

        # Safety tracking (BUG FIX: prevents orphaned positions and stuck states)
        self.closing_started_at: Optional[datetime] = None  # Track when close started
        self._position_reconciled = False  # Has position been reconciled with broker?
        self._last_health_check = get_eastern_timestamp()
        self._consecutive_stale_data_warnings = 0

        # MKT-001: Flash crash velocity detection - track price history over 5 minutes
        self._price_history: List[Tuple[datetime, float]] = []

        # Circuit breaker tracking (matching Delta Neutral bot safety features)
        self._consecutive_failures = 0
        self._circuit_breaker_open = False
        self._circuit_breaker_reason = ""
        self._circuit_breaker_opened_at: Optional[datetime] = None

        # CONN-002: Sliding window failure counter for intermittent errors
        # True = success, False = failure. Triggers circuit breaker if 5+ of last 10 fail
        self._api_results_window: Deque[bool] = deque(maxlen=SLIDING_WINDOW_SIZE)

        # ORDER-004: Critical intervention flag - when set, ALL trading halts permanently
        # until manual intervention (no automatic cooldown like circuit breaker)
        self._critical_intervention_required = False
        self._critical_intervention_reason = ""
        self._critical_intervention_at: Optional[datetime] = None

        # MKT-002: Market halt detection
        self._market_halt_detected = False
        self._market_halt_reason = ""
        self._market_halt_detected_at: Optional[datetime] = None

        # CB-004: Daily circuit breaker tracking for escalation
        self._circuit_breaker_opens_today = 0
        self._daily_halt_triggered = False  # When True, no more trading today

        # CB-001: Track partial fill UICs for accurate emergency close
        # This stores UICs of legs that were actually placed (for partial fills)
        self._partial_fill_uics: Dict[str, int] = {}

        # Order execution safety tracking
        self._orphaned_orders: List[Dict] = []  # Track orders that may need cleanup
        # Note: Order timeout is hardcoded to 30s in _verify_order_fill() since we always use
        # market orders which should fill instantly. Config option removed as unused.

        # Cumulative metrics tracking (persisted across days)
        self.cumulative_metrics = load_cumulative_metrics()

        # Position Registry for multi-bot SPX isolation (2026-02-04)
        # When running Iron Fly + MEIC simultaneously on SPX, this ensures
        # each bot only sees its own positions
        self.registry = PositionRegistry()
        self.bot_name = "IRON_FLY_0DTE"

        logger.info(f"IronFlyStrategy initialized - State: {self.state.value}")
        logger.info(f"  Underlying: {self.underlying_symbol} (UIC: {self.underlying_uic})")
        logger.info(f"  Entry time: {self.entry_time} EST")
        logger.info(f"  Max VIX: {self.max_vix}, Spike threshold: {self.vix_spike_threshold}%")
        if self.profit_target_percent is not None:
            logger.info(f"  Profit target: {self.profit_target_percent}% of credit (min ${self.profit_target_min}), Max hold: {self.max_hold_minutes} min")
        else:
            logger.info(f"  Profit target: ${self.profit_target_fixed}/contract, Max hold: {self.max_hold_minutes} min")
        logger.info(f"  FOMC blackout: {'ENABLED' if self.fed_meeting_blackout else 'DISABLED'}")
        logger.info(f"  Economic calendar check: {'ENABLED' if self.economic_calendar_check else 'DISABLED'}")
        logger.info(f"  Wing width: min {self.min_wing_width}pt, target credit {self.target_credit_percent}% of width")
        if self.manual_expected_move:
            logger.info(f"  Manual expected move: {self.manual_expected_move} points (calibration mode)")

        # TIME-003: Check if today is an early close day and log warning
        self.check_early_close_warning()

    # =========================================================================
    # CIRCUIT BREAKER METHODS (Safety feature from Delta Neutral bot)
    # =========================================================================

    def _record_api_result(self, success: bool, reason: str = "") -> None:
        """
        CONN-002: Record API result to sliding window and check for intermittent failures.

        Unlike consecutive failure counting which resets on success, this tracks
        the last N results. If too many fail (even with successes in between),
        the circuit breaker opens.

        Args:
            success: True if API call succeeded, False if it failed
            reason: Description of the failure (only used if success=False)
        """
        self._api_results_window.append(success)

        if not success:
            # Also track consecutive failures for backward compatibility
            self._consecutive_failures += 1
            logger.warning(f"API failure #{self._consecutive_failures}: {reason}")

            # Check consecutive threshold (existing behavior)
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self._open_circuit_breaker(f"Consecutive failures: {reason}")
                return
        else:
            # Success resets consecutive counter
            if self._consecutive_failures > 0:
                logger.debug(f"Resetting consecutive failure count (was {self._consecutive_failures})")
                self._consecutive_failures = 0

        # CONN-002: Check sliding window threshold
        # Only check when we have enough samples
        if len(self._api_results_window) >= SLIDING_WINDOW_SIZE:
            failure_count = sum(1 for r in self._api_results_window if not r)
            if failure_count >= SLIDING_WINDOW_FAILURE_THRESHOLD:
                logger.warning(
                    f"CONN-002: Sliding window threshold breached - "
                    f"{failure_count}/{SLIDING_WINDOW_SIZE} recent API calls failed"
                )
                self._open_circuit_breaker(
                    f"Intermittent failures: {failure_count}/{SLIDING_WINDOW_SIZE} recent calls failed"
                )

    def _calculate_profit_target(self) -> float:
        """
        Calculate the profit target based on configuration.

        FIX (2026-01-31): Supports dynamic profit target as percentage of credit.
        FIX (2026-02-01): Now accounts for commission costs in target calculation.
        FIX (2026-02-02): Cap target at max possible profit (credit - commission).
                         Previous bug: $25 floor + $20 commission = $45 target,
                         but if credit is only $30, max profit is $30 - impossible!

        The target is now capped so it never exceeds what's achievable:
        - Max possible gross profit = credit received (100% premium capture)
        - Max possible net profit = credit - commission
        - Target = min(calculated_target, credit) so it's always reachable

        Returns:
            float: Profit target in dollars (GROSS - what we need to close the spread for)
        """
        if not self.position:
            return self.profit_target_fixed

        # Calculate total commission for the trade (entry + exit)
        total_commission = self._calculate_total_commission()

        # Credit received is the maximum possible gross profit (in dollars)
        credit_dollars = self.position.credit_received / 100
        max_achievable_gross = credit_dollars  # Can never make more than 100% of credit

        if self.profit_target_percent is not None:
            # Dynamic: percentage of credit received
            percent_target = credit_dollars * (self.profit_target_percent / 100)
            floor_target = self.profit_target_min * self.position.quantity

            # Use the higher of percent or floor, but NEVER exceed credit
            gross_target = min(
                max(percent_target, floor_target),
                max_achievable_gross  # Cap at max possible profit
            )

            # Add commission to target so NET profit equals the intended target
            # But also ensure the final target doesn't exceed credit (which would be impossible)
            target_with_commission = gross_target + total_commission

            # If target with commission exceeds credit, we need to lower expectations
            # The best we can do is credit (100% capture) which gives net = credit - commission
            if target_with_commission > max_achievable_gross:
                self.logger.warning(
                    f"Profit target ${target_with_commission:.2f} exceeds max profit ${credit_dollars:.2f}. "
                    f"Capping at ${credit_dollars:.2f} (net profit will be ${credit_dollars - total_commission:.2f})"
                )
                return max_achievable_gross

            return target_with_commission
        else:
            # Fixed: dollar amount per contract + commission
            fixed_target = (self.profit_target_fixed * self.position.quantity) + total_commission
            # Also cap fixed target at max achievable
            if fixed_target > max_achievable_gross:
                self.logger.warning(
                    f"Fixed profit target ${fixed_target:.2f} exceeds max profit ${credit_dollars:.2f}. "
                    f"Capping at ${credit_dollars:.2f}"
                )
                return max_achievable_gross
            return fixed_target

    def _calculate_total_commission(self) -> float:
        """
        Calculate total commission cost for the trade.

        Iron Fly has 4 legs, each with round-trip commission.
        Default: $5 per leg ($2.50 open + $2.50 close) = $20 total.

        Returns:
            float: Total commission in dollars
        """
        return self.commission_per_leg * self.num_legs

    def _calculate_net_pnl(self, gross_pnl_dollars: float) -> float:
        """
        Calculate net P&L after subtracting commissions.

        Args:
            gross_pnl_dollars: Gross P&L in dollars (before commissions)

        Returns:
            float: Net P&L in dollars (after commissions)
        """
        return gross_pnl_dollars - self._calculate_total_commission()

    def _increment_failure_count(self, reason: str) -> None:
        """Increment consecutive failure counter and open circuit breaker if threshold reached."""
        # Delegate to the new sliding window method
        self._record_api_result(success=False, reason=reason)

    def _reset_failure_count(self) -> None:
        """Reset failure counter after successful operation."""
        # Delegate to the new sliding window method
        self._record_api_result(success=True)

    def _open_circuit_breaker(self, reason: str) -> None:
        """
        Open circuit breaker to prevent further order attempts.

        IMPORTANT: Before halting, attempts emergency position closure if we have
        an active position. This prevents being stuck with an unmanaged position.

        CB-004: Tracks daily circuit breaker opens for escalation. After
        MAX_CIRCUIT_BREAKER_OPENS_PER_DAY opens, trading halts for rest of day.
        """
        # CB-004: Increment daily counter and check escalation
        self._circuit_breaker_opens_today += 1

        if self._circuit_breaker_opens_today >= MAX_CIRCUIT_BREAKER_OPENS_PER_DAY:
            logger.critical(
                f"CB-004: DAILY HALT TRIGGERED - Circuit breaker opened {self._circuit_breaker_opens_today} times today"
            )
            self._daily_halt_triggered = True
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_DAILY_HALT_ESCALATION",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"CB-004: {self._circuit_breaker_opens_today} circuit breaker opens today - DAILY HALT",
                "result": "Trading suspended for rest of day"
            })

        # CRITICAL: Attempt emergency position closure BEFORE halting
        # This is the nuclear option - better to close at a loss than be stuck
        if self.position and self.state in [IronFlyState.POSITION_OPEN, IronFlyState.MONITORING_EXIT]:
            logger.critical("🚨 CIRCUIT BREAKER: Attempting emergency position closure before halt")
            emergency_result = self._emergency_close_position(
                reason=f"CIRCUIT_BREAKER: {reason}"
            )
            logger.critical(f"Emergency close result: {emergency_result}")

        self._circuit_breaker_open = True
        self._circuit_breaker_reason = reason
        self._circuit_breaker_opened_at = get_eastern_timestamp()

        logger.critical(
            f"CIRCUIT BREAKER OPENED (#{self._circuit_breaker_opens_today} today): {reason} "
            f"(after {self._consecutive_failures} consecutive failures)"
        )

        self.trade_logger.log_safety_event({
            "event_type": "IRON_FLY_CIRCUIT_BREAKER_OPEN",
            "spy_price": self.current_price,
            "vix": self.current_vix,
            "description": f"Circuit breaker #{self._circuit_breaker_opens_today} opened: {reason}",
            "consecutive_failures": self._consecutive_failures,
            "result": "Trading halted - emergency close attempted if position was open"
        })

        # ALERT: Send Telegram/Email AFTER action is complete with actual results
        self.alert_service.circuit_breaker(
            reason=reason,
            consecutive_failures=self._consecutive_failures,
            details={
                "circuit_breaker_opens_today": self._circuit_breaker_opens_today,
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "daily_halt": self._daily_halt_triggered
            }
        )

    def _check_circuit_breaker(self) -> bool:
        """
        Check if circuit breaker allows trading.

        CB-004: Also checks for daily halt escalation - no automatic reset if
        circuit breaker has opened too many times today.

        Returns:
            bool: True if trading allowed, False if blocked
        """
        # CB-004: Check for daily halt escalation first (no automatic reset)
        if self._daily_halt_triggered:
            logger.warning(
                f"CB-004: DAILY HALT ACTIVE - Circuit breaker opened {self._circuit_breaker_opens_today} times today. "
                "Trading suspended until tomorrow."
            )
            return False

        if not self._circuit_breaker_open:
            return True

        # Check if cooldown period has passed
        if self._circuit_breaker_opened_at:
            elapsed = (get_eastern_timestamp() - self._circuit_breaker_opened_at).total_seconds()
            cooldown_seconds = CIRCUIT_BREAKER_COOLDOWN_MINUTES * 60

            if elapsed >= cooldown_seconds:
                logger.info(
                    f"Circuit breaker cooldown complete ({elapsed:.0f}s elapsed). "
                    "Resetting for retry."
                )
                self._circuit_breaker_open = False
                self._circuit_breaker_reason = ""
                self._circuit_breaker_opened_at = None
                self._consecutive_failures = 0
                self._api_results_window.clear()  # CONN-002: Reset sliding window too
                return True

            logger.warning(
                f"Circuit breaker still open: {self._circuit_breaker_reason} "
                f"({cooldown_seconds - elapsed:.0f}s remaining in cooldown)"
            )

        return False

    # =========================================================================
    # ORDER-004: CRITICAL INTERVENTION FLAG (No automatic reset)
    # =========================================================================

    def _set_critical_intervention(self, reason: str) -> None:
        """
        ORDER-004: Set critical intervention flag requiring manual reset.

        Unlike circuit breaker (which has cooldown), this PERMANENTLY halts trading
        until manual intervention. Used when:
        - Emergency close fails (legs stuck open)
        - Unrecoverable system state detected
        - Safety check identifies dangerous condition

        Args:
            reason: Why critical intervention is required
        """
        self._critical_intervention_required = True
        self._critical_intervention_reason = reason
        self._critical_intervention_at = get_eastern_timestamp()

        logger.critical("🚨🚨🚨 ORDER-004: CRITICAL INTERVENTION REQUIRED 🚨🚨🚨")
        logger.critical(f"Reason: {reason}")
        logger.critical("ALL TRADING HALTED - Manual reset required")
        logger.critical("To clear: Call strategy.reset_critical_intervention() or restart bot")

        self.trade_logger.log_safety_event({
            "event_type": "IRON_FLY_CRITICAL_INTERVENTION",
            "spy_price": self.current_price,
            "vix": self.current_vix,
            "description": f"CRITICAL: {reason}",
            "result": "Trading halted permanently until manual reset"
        })

        # ALERT: Critical intervention requires immediate attention
        self.alert_service.send_alert(
            alert_type=AlertType.CRITICAL_INTERVENTION,
            title="CRITICAL INTERVENTION REQUIRED",
            message=f"{reason}\n\nALL TRADING HALTED.\nManual reset required to resume.",
            priority=AlertPriority.CRITICAL,
            details={
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "position_open": self.position is not None
            }
        )

    def _check_critical_intervention(self) -> bool:
        """
        ORDER-004: Check if critical intervention flag is set.

        Returns:
            bool: True if trading allowed, False if blocked by critical intervention
        """
        if not self._critical_intervention_required:
            return True

        elapsed = 0
        if self._critical_intervention_at:
            elapsed = (get_eastern_timestamp() - self._critical_intervention_at).total_seconds()

        logger.critical(
            f"🚨 CRITICAL INTERVENTION ACTIVE ({elapsed/60:.1f}m): {self._critical_intervention_reason}"
        )
        logger.critical("Trading blocked - manual reset required")
        return False

    def reset_critical_intervention(self, confirm: str = "") -> str:
        """
        ORDER-004: Manually reset critical intervention flag.

        CAUTION: Only call after verifying all positions are properly closed
        and any underlying issues have been resolved.

        Args:
            confirm: Must be "CONFIRMED" to proceed (safety check)

        Returns:
            str: Result message
        """
        if confirm != "CONFIRMED":
            return (
                "Critical intervention reset BLOCKED - safety confirmation required. "
                "Call with confirm='CONFIRMED' after verifying all positions are closed."
            )

        if not self._critical_intervention_required:
            return "No critical intervention flag was set"

        old_reason = self._critical_intervention_reason
        elapsed = 0
        if self._critical_intervention_at:
            elapsed = (get_eastern_timestamp() - self._critical_intervention_at).total_seconds()

        self._critical_intervention_required = False
        self._critical_intervention_reason = ""
        self._critical_intervention_at = None

        # Also reset circuit breaker and failure counters for fresh start
        self._circuit_breaker_open = False
        self._circuit_breaker_reason = ""
        self._consecutive_failures = 0
        self._api_results_window.clear()

        logger.warning(f"CRITICAL INTERVENTION RESET after {elapsed/60:.1f}m")
        logger.warning(f"Previous reason was: {old_reason}")

        self.trade_logger.log_safety_event({
            "event_type": "IRON_FLY_CRITICAL_INTERVENTION_RESET",
            "spy_price": self.current_price,
            "vix": self.current_vix,
            "description": f"Manual reset after {elapsed/60:.1f} minutes",
            "previous_reason": old_reason,
            "result": "Trading enabled - all safety counters reset"
        })

        return f"Critical intervention cleared. Previous reason: {old_reason}"

    # =========================================================================
    # MKT-002: MARKET HALT DETECTION
    # =========================================================================

    def _check_for_market_halt(self, error_message: str) -> bool:
        """
        MKT-002: Check if an error message indicates a market-wide trading halt.

        Exchange circuit breakers (Level 1/2/3) or LULD halts can suspend trading.
        This is different from our internal circuit breaker (API errors).

        Args:
            error_message: Error string from API or order rejection

        Returns:
            bool: True if market halt detected, False otherwise
        """
        if not error_message:
            return False

        error_lower = error_message.lower()
        for keyword in MARKET_HALT_KEYWORDS:
            if keyword in error_lower:
                self._set_market_halt(f"Detected keyword '{keyword}' in: {error_message[:100]}")
                return True
        return False

    def _set_market_halt(self, reason: str) -> None:
        """
        MKT-002: Set market halt flag - trading paused until halt lifts.

        Unlike critical intervention (permanent halt), market halts are expected
        to lift within minutes to hours. We'll automatically retry periodically.

        Args:
            reason: Why market halt was detected
        """
        if self._market_halt_detected:
            return  # Already in halt mode

        self._market_halt_detected = True
        self._market_halt_reason = reason
        self._market_halt_detected_at = get_eastern_timestamp()

        logger.critical("🛑 MKT-002: MARKET HALT DETECTED 🛑")
        logger.critical(f"Reason: {reason}")
        logger.critical("Trading paused - will retry when halt lifts")

        self.trade_logger.log_safety_event({
            "event_type": "IRON_FLY_MARKET_HALT_DETECTED",
            "spy_price": self.current_price,
            "vix": self.current_vix,
            "description": f"Market halt: {reason}",
            "result": "Trading paused until halt lifts"
        })

    def _check_market_halt_status(self) -> bool:
        """
        MKT-002: Check if market halt is still in effect.

        Returns:
            bool: True if trading allowed, False if still halted
        """
        if not self._market_halt_detected:
            return True

        # Check if enough time has passed to retry (5 minutes minimum)
        if self._market_halt_detected_at:
            elapsed = (get_eastern_timestamp() - self._market_halt_detected_at).total_seconds()

            if elapsed >= 300:  # 5 minutes
                # Try to clear halt - next API call will re-detect if still halted
                logger.info(f"MKT-002: Market halt check - {elapsed/60:.1f}m elapsed, attempting to resume")
                self._market_halt_detected = False
                self._market_halt_reason = ""
                self._market_halt_detected_at = None
                return True

            logger.warning(
                f"🛑 MKT-002: Market halt still in effect ({elapsed/60:.1f}m elapsed): "
                f"{self._market_halt_reason}"
            )
        return False

    def _emergency_close_position(self, reason: str) -> str:
        """
        Emergency closure of Iron Fly position using market orders.

        This method bypasses the circuit breaker and uses aggressive market orders
        to close the position as quickly as possible. Called when:
        - Circuit breaker is about to open (prevents being stuck with position)
        - Critical system failure detected
        - Manual emergency trigger

        Args:
            reason: Why emergency closure was triggered

        Returns:
            str: Description of what happened
        """
        if not self.position:
            return "No position to close"

        logger.critical(f"🚨🚨🚨 EMERGENCY CLOSE: {reason} 🚨🚨🚨")

        pnl = self.position.unrealized_pnl
        hold_time = self.position.hold_time_minutes

        self.trade_logger.log_safety_event({
            "event_type": "IRON_FLY_EMERGENCY_CLOSE_START",
            "spy_price": self.current_price,
            "vix": self.current_vix,
            "description": f"Emergency close initiated: {reason}",
            "position_pnl": pnl,
            "hold_time_minutes": hold_time
        })

        if self.dry_run:
            # In dry-run, just clear the position
            # Convert cents to dollars for logging
            self.trade_logger.log_trade(
                action="[SIMULATED] EMERGENCY_CLOSE",
                strike=f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
                price=self.position.credit_received / 100,  # Cents to dollars
                delta=0.0,
                pnl=pnl / 100,  # Cents to dollars
                saxo_client=self.client,
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type="Iron Fly",
                expiry_date=self.position.expiry,
                dte=0,
                premium_received=self.position.credit_received / 100,  # Cents to dollars
                trade_reason=f"EMERGENCY: {reason}"
            )
            self.daily_pnl += pnl
            self.position = None
            self._unregister_positions_from_registry()  # POS-005: Clear from registry
            self.state = IronFlyState.DAILY_COMPLETE
            return f"[DRY RUN] Emergency close complete - P&L: ${pnl / 100:.2f}"

        # =================================================================
        # LIVE EMERGENCY CLOSE - Use market orders for all legs
        # =================================================================
        close_success = 0
        close_failed = 0
        close_order_ids = {}

        # Cancel any open limit orders first
        if self.position.profit_order_id:
            try:
                self.client.cancel_order(self.position.profit_order_id)
                logger.info(f"Cancelled profit order: {self.position.profit_order_id}")
            except Exception as e:
                logger.warning(f"Failed to cancel profit order: {e}")

        # Close all 4 legs with market orders (using emergency bypass)
        legs = [
            ("short_call", self.position.short_call_uic, BuySell.BUY, "StockIndexOption"),
            ("short_put", self.position.short_put_uic, BuySell.BUY, "StockIndexOption"),
            ("long_call", self.position.long_call_uic, BuySell.SELL, "StockIndexOption"),
            ("long_put", self.position.long_put_uic, BuySell.SELL, "StockIndexOption"),
        ]

        for leg_name, uic, buy_sell, asset_type in legs:
            if not uic:
                logger.warning(f"No UIC for {leg_name} - skipping")
                continue

            try:
                logger.critical(f"EMERGENCY: Closing {leg_name} with MARKET order")
                result = self.client.place_emergency_order(
                    uic=uic,
                    asset_type=asset_type,
                    buy_sell=buy_sell,
                    amount=self.position.quantity,
                    order_type=OrderType.MARKET,
                    to_open_close="ToClose"
                )
                if result:
                    close_order_ids[leg_name] = result.get("OrderId")
                    close_success += 1
                    logger.critical(f"✅ {leg_name} close order placed: {result.get('OrderId')}")
                else:
                    close_failed += 1
                    logger.critical(f"❌ {leg_name} close order FAILED - no result")
            except Exception as e:
                close_failed += 1
                logger.critical(f"❌ {leg_name} close order EXCEPTION: {e}")

        # Log results
        result_msg = f"Emergency close: {close_success}/4 legs closed, {close_failed} failed"

        if close_failed > 0:
            # ORDER-004: Set critical intervention flag - manual reset required
            self._set_critical_intervention(
                f"Emergency close failed: {close_failed}/4 legs could not be closed"
            )
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_EMERGENCY_CLOSE_PARTIAL",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": result_msg,
                "close_order_ids": close_order_ids,
                "critical_intervention_required": True,
                "result": "CRITICAL: MANUAL INTERVENTION REQUIRED - trading halted until reset"
            })
        else:
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_EMERGENCY_CLOSE_SUCCESS",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": result_msg,
                "close_order_ids": close_order_ids,
                "result": "All legs closed successfully"
            })

        # Log the trade - convert cents to dollars for logging
        self.trade_logger.log_trade(
            action="EMERGENCY_CLOSE",
            strike=f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
            price=self.position.credit_received / 100,  # Cents to dollars
            delta=0.0,
            pnl=pnl / 100,  # Cents to dollars
            saxo_client=self.client,
            underlying_price=self.current_price,
            vix=self.current_vix,
            option_type="Iron Fly",
            expiry_date=self.position.expiry,
            dte=0,
            premium_received=self.position.credit_received / 100,  # Cents to dollars
            trade_reason=f"EMERGENCY: {reason} - {close_success}/4 legs closed"
        )

        self.daily_pnl += pnl
        self.position = None
        self._clear_position_metadata()  # POS-001: Clear saved metadata
        self._unregister_positions_from_registry()  # POS-005: Clear from registry
        self.state = IronFlyState.DAILY_COMPLETE

        # ALERT: Send emergency exit alert AFTER close is complete with actual results
        pnl_dollars = pnl / 100
        self.alert_service.emergency_exit(
            reason=reason,
            pnl=pnl_dollars,
            details={
                "close_success": close_success,
                "close_failed": close_failed,
                "close_order_ids": close_order_ids,
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "hold_time_minutes": hold_time
            }
        )

        logger.critical(f"Emergency close complete: {result_msg}")
        return result_msg

    # =========================================================================
    # POS-001: POSITION METADATA PERSISTENCE (Crash Recovery)
    # =========================================================================

    def _save_position_metadata(self) -> None:
        """
        POS-001: Save position metadata to file for crash recovery.

        Saves entry_time, credit_received, and other critical position data
        so it can be restored if the bot crashes and restarts.
        """
        if not self.position:
            self._clear_position_metadata()
            return

        metadata = {
            "saved_at": get_eastern_timestamp().isoformat(),
            "atm_strike": self.position.atm_strike,
            "upper_wing": self.position.upper_wing,
            "lower_wing": self.position.lower_wing,
            "entry_time": self.position.entry_time.isoformat() if self.position.entry_time else None,
            "entry_price": self.position.entry_price,
            "credit_received": self.position.credit_received,
            "quantity": self.position.quantity,
            "expiry": self.position.expiry,
            "short_call_uic": self.position.short_call_uic,
            "short_put_uic": self.position.short_put_uic,
            "long_call_uic": self.position.long_call_uic,
            "long_put_uic": self.position.long_put_uic,
        }

        try:
            # Ensure data directory exists
            os.makedirs(os.path.dirname(POSITION_METADATA_FILE), exist_ok=True)

            with open(POSITION_METADATA_FILE, 'w') as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"POS-001: Saved position metadata to {POSITION_METADATA_FILE}")
        except Exception as e:
            logger.error(f"POS-001: Failed to save position metadata: {e}")

    def _load_position_metadata(self) -> Optional[Dict]:
        """
        POS-001: Load position metadata from file for crash recovery.

        Returns:
            Dict with metadata if found and valid, None otherwise
        """
        if not os.path.exists(POSITION_METADATA_FILE):
            return None

        try:
            with open(POSITION_METADATA_FILE, 'r') as f:
                metadata = json.load(f)

            # Validate it's from today (don't restore old positions)
            saved_at = metadata.get("saved_at", "")
            if saved_at:
                saved_date = saved_at.split("T")[0]
                today = get_us_market_time().strftime("%Y-%m-%d")
                if saved_date != today:
                    logger.info(f"POS-001: Found old position metadata from {saved_date}, ignoring")
                    self._clear_position_metadata()
                    return None

            logger.info(f"POS-001: Loaded position metadata from {POSITION_METADATA_FILE}")
            return metadata
        except Exception as e:
            logger.error(f"POS-001: Failed to load position metadata: {e}")
            return None

    def _clear_position_metadata(self) -> None:
        """
        POS-001: Clear the position metadata file after position is closed.
        """
        try:
            if os.path.exists(POSITION_METADATA_FILE):
                os.remove(POSITION_METADATA_FILE)
                logger.info("POS-001: Cleared position metadata file")
        except Exception as e:
            logger.error(f"POS-001: Failed to clear position metadata: {e}")

    # =========================================================================
    # POSITION REGISTRY METHODS (Multi-bot SPX isolation - 2026-02-04)
    # =========================================================================

    def _register_positions_with_registry(
        self,
        strategy_id: str,
        fill_details: Dict[str, Dict],
        iron_fly_options: Dict
    ) -> None:
        """
        POS-005: Register all 4 Iron Fly legs with the Position Registry.

        This is critical for multi-bot SPX isolation. When Iron Fly and MEIC
        both trade SPX 0DTE options, the registry tracks which positions belong
        to which bot, preventing interference.

        Args:
            strategy_id: Unique identifier for this trade (e.g., "iron_fly_20260204_100000")
            fill_details: Dict with fill info for each leg (contains position_id)
            iron_fly_options: Dict with strike info for metadata

        Note:
            Registry errors are logged but don't crash the bot - position monitoring
            continues even if registry fails. The position is already open at this point.
        """
        leg_names = ["long_call", "long_put", "short_call", "short_put"]
        strikes = {
            "long_call": iron_fly_options.get("upper_wing"),
            "long_put": iron_fly_options.get("lower_wing"),
            "short_call": iron_fly_options.get("atm_strike"),
            "short_put": iron_fly_options.get("atm_strike"),
        }
        registered_count = 0

        for leg_name in leg_names:
            fill_info = fill_details.get(leg_name, {})
            position_id = fill_info.get("position_id")

            # CRITICAL: Reject both None and the string "None" to prevent registry corruption
            # The string "None" can occur if str(None) is called somewhere in the fill chain
            if not position_id or position_id == "None":
                if position_id == "None":
                    logger.error(f"BUG DETECTED: {leg_name} has string 'None' as position_id - not registering")
                else:
                    logger.warning(f"No position_id available for {leg_name} - cannot register with registry")
                continue

            try:
                success = self.registry.register(
                    position_id=str(position_id),
                    bot_name=self.bot_name,
                    strategy_id=strategy_id,
                    metadata={
                        "leg_type": leg_name,
                        "strike": strikes.get(leg_name),
                        "structure": "iron_fly"
                    }
                )
                if success:
                    registered_count += 1
                    logger.debug(f"Registered {leg_name} position {position_id} with registry")
                else:
                    logger.warning(f"Failed to register {leg_name} position {position_id}")
            except Exception as e:
                logger.error(f"POS-005: Registry error for {leg_name} position {position_id}: {e}")

        logger.info(f"POS-005: Registered {registered_count}/4 positions with registry (strategy: {strategy_id})")

        if registered_count < 4:
            self.trade_logger.log_safety_event({
                "event_type": "REGISTRY_PARTIAL_REGISTRATION",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"Only {registered_count}/4 positions registered - some legs missing position_id",
                "strategy_id": strategy_id,
                "result": "Position reconciliation may be affected"
            })

    def _unregister_positions_from_registry(self) -> None:
        """
        POS-005: Unregister all Iron Fly positions from the registry.

        Called when position is closed (normal exit, stop loss, or emergency).
        Registry errors are logged but don't crash - position is already closed.
        """
        try:
            my_positions = self.registry.get_positions(self.bot_name)

            if not my_positions:
                logger.debug("No Iron Fly positions in registry to unregister")
                return

            unregistered = 0
            for pos_id in my_positions:
                try:
                    if self.registry.unregister(pos_id):
                        unregistered += 1
                except Exception as e:
                    logger.error(f"POS-005: Failed to unregister position {pos_id}: {e}")

            logger.info(f"POS-005: Unregistered {unregistered} positions from registry")
        except Exception as e:
            logger.error(f"POS-005: Registry error during unregister: {e}")

    # =========================================================================
    # ORDER SAFETY METHODS (Safety feature from Delta Neutral bot)
    # =========================================================================

    def _cancel_order_with_retry(self, order_id: str, reason: str = "", max_retries: int = 3) -> bool:
        """
        Cancel an order with retry logic.

        Args:
            order_id: The order ID to cancel
            reason: Why we're cancelling (for logging)
            max_retries: Maximum number of cancel attempts

        Returns:
            True if cancelled successfully, False otherwise

        Note:
            All order status checks are direct API calls to Saxo (no caching).
            This ensures we always have the real-time state from the broker.
        """
        for attempt in range(1, max_retries + 1):
            try:
                # First check if order is already filled/cancelled (direct API call - no cache)
                order_status = self.client.get_order_status(order_id)
                status = order_status.get("status", "Unknown") if order_status else "Unknown"

                if status in ["Filled", "Cancelled", "Rejected", "Expired"]:
                    logger.info(f"Order {order_id} already {status} - no cancel needed")
                    return True

                # Order still active - try to cancel
                logger.info(f"Cancelling order {order_id} (attempt {attempt}/{max_retries}): {reason}")
                self.client.cancel_order(order_id)

                # Verify cancellation
                time.sleep(0.5)
                verify_status = self.client.get_order_status(order_id)
                if verify_status and verify_status.get("status") in ["Cancelled", "Rejected"]:
                    logger.info(f"✓ Order {order_id} cancelled successfully")
                    return True

            except Exception as e:
                logger.warning(f"Cancel attempt {attempt}/{max_retries} failed for {order_id}: {e}")
                if attempt < max_retries:
                    time.sleep(1)

        logger.error(f"Failed to cancel order {order_id} after {max_retries} attempts")
        return False

    def _check_and_cancel_pending_orders_on_startup(self) -> None:
        """
        Check for any pending/working orders at broker on startup and handle them.

        This catches scenarios where:
        1. Bot crashed while placing orders
        2. Orders were placed but fills weren't verified
        3. Limit orders are still working from previous session

        For SPX options, we cancel working orders and let position reconciliation
        handle any filled positions.

        Note:
            get_open_orders() is a direct API call to Saxo (no caching).
            This ensures we always see the real-time order state from the broker.
        """
        if self.dry_run:
            logger.info("Pending order check skipped (dry-run mode)")
            return

        try:
            logger.info("Checking for pending orders at broker (fresh from Saxo API)...")
            open_orders = self.client.get_open_orders()

            if not open_orders:
                logger.info("No pending orders found at broker")
                return

            # Filter for SPX/SPXW option orders
            spx_orders = []
            for order in open_orders:
                description = str(order.get("Description", "")).upper()
                asset_type = order.get("AssetType", "")
                if asset_type in ["StockOption", "StockIndexOption"] and ("SPX" in description or "SPXW" in description):
                    spx_orders.append(order)

            if not spx_orders:
                logger.info("No SPX option orders pending")
                return

            logger.warning(f"Found {len(spx_orders)} pending SPX option orders!")

            cancelled_count = 0
            for order in spx_orders:
                order_id = order.get("OrderId")
                status = order.get("Status", "Unknown")
                description = order.get("Description", "Unknown")

                if status in ["Working", "Pending", "New"]:
                    logger.warning(f"Cancelling orphaned order: {order_id} ({description})")

                    if self._cancel_order_with_retry(order_id, "orphaned from previous session"):
                        cancelled_count += 1
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_ORPHAN_ORDER_CANCELLED",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "description": f"Cancelled orphaned order on startup: {description}",
                            "order_id": order_id,
                            "result": "Order cancelled successfully"
                        })
                    else:
                        logger.critical(f"FAILED to cancel orphaned order {order_id} - MANUAL INTERVENTION REQUIRED")
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_ORPHAN_ORDER_CANCEL_FAILED",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "description": f"Failed to cancel orphaned order: {description}",
                            "order_id": order_id,
                            "result": "MANUAL INTERVENTION REQUIRED"
                        })

            logger.info(f"Pending order cleanup complete: {cancelled_count}/{len(spx_orders)} orders cancelled")

        except Exception as e:
            logger.error(f"Error checking pending orders: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _add_orphaned_order(self, order_info: Dict) -> None:
        """Track a potentially orphaned order for later cleanup."""
        self._orphaned_orders.append({
            **order_info,
            "timestamp": get_eastern_timestamp().isoformat(),
            "status": "potentially_orphaned"
        })
        logger.warning(f"Added orphaned order to tracking: {order_info.get('order_id', 'unknown')}")

    def _check_for_orphaned_orders(self) -> None:
        """Check and attempt to clean up any tracked orphaned orders."""
        if not self._orphaned_orders:
            return

        logger.info(f"Checking {len(self._orphaned_orders)} potentially orphaned orders...")

        for order in self._orphaned_orders[:]:  # Iterate over copy
            order_id = order.get("order_id")
            if not order_id:
                self._orphaned_orders.remove(order)
                continue

            try:
                # Check if order is still active
                order_status = self.client.get_order_status(order_id)

                if order_status.get("status") in ["Filled", "Cancelled", "Rejected"]:
                    logger.info(f"Orphaned order {order_id} is {order_status.get('status')} - removing from tracking")
                    self._orphaned_orders.remove(order)
                elif order_status.get("status") in ["Working", "Pending"]:
                    logger.warning(f"Orphaned order {order_id} still active - attempting cancel")
                    try:
                        self.client.cancel_order(order_id)
                        order["status"] = "cancelled"
                        self._orphaned_orders.remove(order)
                        logger.info(f"Successfully cancelled orphaned order {order_id}")
                    except Exception as cancel_err:
                        logger.error(f"Failed to cancel orphaned order {order_id}: {cancel_err}")
            except Exception as e:
                logger.error(f"Error checking orphaned order {order_id}: {e}")

    def _verify_order_fill(
        self,
        order_id: str,
        leg_name: str,
        uic: int = None,
        timeout_seconds: int = 5
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Verify that a market order has been filled.

        FIX (2026-02-01): Completely rewritten to match Delta Neutral's efficient pattern.
        Market orders fill in ~1-3 seconds and DISAPPEAR from /orders/ endpoint.
        The old approach polled get_order_status() which was slow and inefficient.

        New approach (matches place_market_order_immediate in saxo_client.py):
        1. Wait 1 second for order to process
        2. Check if order is still in open orders (if not = filled)
        3. Verify fill via activities endpoint to get actual fill price
        4. Only poll if order is still open (unusual for market orders)

        Args:
            order_id: The order ID to verify
            leg_name: Name of the leg (for logging)
            uic: Instrument UIC (used to verify fills via position check)
            timeout_seconds: Max wait time (default 5s - market orders should fill in ~1s)

        Returns:
            Tuple of (success: bool, fill_details: Optional[Dict])
        """
        logger.info(f"Verifying fill for {leg_name} order {order_id}...")

        # Step 1: Brief delay to let order process (market orders fill in ~1-3s)
        time.sleep(1)

        # Step 2: Check if order is still in open orders
        # If NOT in open orders, it likely filled (market orders disappear after fill)
        open_orders = self.client.get_open_orders()
        order_still_open = any(
            str(o.get("OrderId")) == str(order_id) for o in open_orders
        )

        if not order_still_open:
            # Order not in open orders - likely filled, verify via activities
            logger.info(f"Order {order_id} not in open orders - checking activities for fill confirmation...")

            if uic:
                # FIX (2026-02-01): Try multiple times to get fill price from activities
                # Activities endpoint may have slight delay in syncing fill data
                for activity_attempt in range(3):
                    filled, fill_details = self.client.check_order_filled_by_activity(order_id, uic)
                    if filled:
                        fill_price = fill_details.get("fill_price") if fill_details else None
                        if fill_price and fill_price > 0:
                            logger.info(f"✓ {leg_name} order {order_id} FILLED @ ${fill_price:.2f} (verified via activity)")
                            return True, fill_details
                        elif activity_attempt < 2:
                            # Got fill confirmation but no price - wait and retry
                            logger.info(f"Fill confirmed but no price yet, waiting 1s (attempt {activity_attempt + 1}/3)...")
                            time.sleep(1)
                        else:
                            # Last attempt - accept fill without price (will fall back to quote)
                            logger.warning(f"✓ {leg_name} order {order_id} FILLED but no fill price available after 3 attempts")
                            return True, fill_details
                    elif activity_attempt < 2:
                        # Not found in activities yet - wait and retry
                        time.sleep(0.5)

            # Order not open AND not in activities after retries - assume filled
            # WARNING: This path means we'll fall back to quoted prices for P&L
            logger.warning(f"⚠ {leg_name} order {order_id} assumed filled (no activity data - P&L may use quoted price)")
            return True, {"status": "Filled", "order_id": order_id, "source": "assumed_filled", "fill_price": 0}

        # Step 3: Order still open - unusual for market order, poll until timeout
        logger.warning(f"⚠ Market order {order_id} still open after 1s - unusual, polling...")

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                order_status = self.client.get_order_status(order_id)

                if order_status is None:
                    # Order disappeared - check if it filled
                    if uic:
                        filled, fill_details = self.client.check_order_filled_by_activity(order_id, uic)
                        if filled:
                            logger.info(f"✓ {leg_name} order {order_id} confirmed FILLED via activity")
                            return True, fill_details
                    # Assume filled
                    return True, {"status": "Filled", "order_id": order_id, "source": "assumed_filled"}

                status = order_status.get("Status") or order_status.get("status", "Unknown")

                if status == "Filled":
                    fill_price = order_status.get("FilledPrice") or order_status.get("fill_price", 0)
                    logger.info(f"✓ {leg_name} order {order_id} FILLED at ${fill_price}")
                    return True, order_status

                elif status in ["Cancelled", "Rejected"]:
                    logger.error(f"✗ {leg_name} order {order_id} was {status}")
                    return False, order_status

                elif status == "PartiallyFilled":
                    filled_qty = order_status.get("FilledAmount", 0)
                    total_qty = order_status.get("Amount", 0)
                    logger.warning(f"⚠ {leg_name} order {order_id} partially filled: {filled_qty}/{total_qty}")

                time.sleep(1)

            except Exception as e:
                logger.error(f"Error checking {leg_name} order status: {e}")
                time.sleep(1)

        # Timeout - do final check
        elapsed = time.time() - start_time + 1  # +1 for initial sleep
        logger.warning(f"{leg_name} order {order_id} verification timeout after {elapsed:.1f}s")

        # Final activity check
        if uic:
            filled, fill_details = self.client.check_order_filled_by_activity(order_id, uic)
            if filled:
                logger.info(f"✓ {leg_name} order {order_id} confirmed FILLED on final check!")
                return True, fill_details

        # Try to cancel the unfilled order
        logger.error(f"✗ {leg_name} order {order_id} NOT filled - attempting to cancel")
        if self._cancel_order_with_retry(order_id, f"{leg_name} fill timeout"):
            logger.info(f"Timed-out order {order_id} cancelled successfully")
        else:
            # If cancel fails, order may have filled
            if uic:
                filled, fill_details = self.client.check_order_filled_by_activity(order_id, uic)
                if filled:
                    logger.info(f"✓ {leg_name} order {order_id} WAS filled (cancel failed)")
                    return True, fill_details

            # Track as orphaned
            logger.warning(f"Failed to cancel order {order_id} - tracking as orphaned")
            self._add_orphaned_order({
                "order_id": order_id,
                "leg_name": leg_name,
                "uic": uic,
                "reason": "fill_timeout_cancel_failed"
            })

        return False, None

    def _place_iron_fly_leg_with_verification(
        self,
        uic: int,
        direction: BuySell,
        amount: int,
        leg_name: str,
        strike: float
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Place a single Iron Fly leg with fill verification.

        Args:
            uic: Instrument UIC
            direction: BuySell.BUY or BuySell.SELL
            amount: Number of contracts
            leg_name: Name for logging (e.g., "short_call")
            strike: Strike price for logging

        Returns:
            Tuple of (success: bool, order_id: Optional[str], fill_details: Optional[Dict])
        """
        logger.info(f"Placing {leg_name}: {direction.value} {amount}x at strike {strike}")

        try:
            # LIVE-001: Use StockIndexOption for SPX/SPXW index options
            order_result = self.client.place_order_with_retry(
                uic=uic,
                asset_type="StockIndexOption",
                buy_sell=direction,
                amount=amount,
                order_type=OrderType.MARKET,
                to_open_close="ToOpen"
            )

            if not order_result:
                logger.error(f"Failed to place {leg_name} order - no result returned")
                self._increment_failure_count(f"{leg_name} order placement failed")
                return False, None, None

            order_id = order_result.get("OrderId")
            if not order_id:
                logger.error(f"Failed to place {leg_name} order - no OrderId in response")
                self._increment_failure_count(f"{leg_name} order has no OrderId")
                return False, None, None

            # Verify the fill - pass UIC so we can check positions if order disappears
            filled, fill_details = self._verify_order_fill(order_id, leg_name, uic=uic)

            if filled:
                self._reset_failure_count()
                return True, order_id, fill_details
            else:
                # Order was placed but not filled - track as orphaned
                self._add_orphaned_order({
                    "order_id": order_id,
                    "leg_name": leg_name,
                    "uic": uic,
                    "direction": direction.value,
                    "amount": amount,
                    "strike": strike,
                    "reason": "fill_verification_failed"
                })
                self._increment_failure_count(f"{leg_name} fill verification failed")
                return False, order_id, fill_details

        except Exception as e:
            error_str = str(e)
            logger.error(f"Exception placing {leg_name}: {error_str}")

            # MKT-002: Check if this error indicates a market halt
            if self._check_for_market_halt(error_str):
                logger.warning("MKT-002: Market halt detected from order error")

            self._increment_failure_count(f"{leg_name} exception: {error_str}")
            return False, None, None

    # =========================================================================
    # CORE STRATEGY METHODS
    # =========================================================================

    def run_strategy_check(self) -> str:
        """
        Main strategy loop - called periodically during market hours.

        This is the state machine that drives the strategy. It should be called
        every few seconds during market hours.

        Returns:
            str: Description of action taken (for logging)
        """
        # SAFETY: Reconcile position with broker on first run
        if not self._position_reconciled:
            self._reconcile_positions_with_broker()
            self._check_for_orphaned_orders()  # Clean up any orphaned orders
            self._position_reconciled = True

        # ORDER-004: Check critical intervention flag FIRST (most severe block)
        if not self._check_critical_intervention():
            return f"CRITICAL INTERVENTION REQUIRED: {self._critical_intervention_reason}"

        # SAFETY: Check circuit breaker before allowing new trades
        if not self._check_circuit_breaker():
            if self.state == IronFlyState.READY_TO_ENTER:
                return f"Circuit breaker open: {self._circuit_breaker_reason}"

        # MKT-002: Check if market halt is in effect
        if not self._check_market_halt_status():
            return f"Market halt in effect: {self._market_halt_reason}"

        # Update market data
        self.update_market_data()

        current_time = get_us_market_time()

        # SAFETY: Check for stale market data and use REST fallback if needed
        if self.market_data.is_price_stale():
            self._consecutive_stale_data_warnings += 1
            stale_age = self.market_data.price_age_seconds()

            # ACTIVE FIX: Poll via REST API when WebSocket data is stale
            logger.warning(
                f"STALE DATA #{self._consecutive_stale_data_warnings}: "
                f"Price {stale_age:.1f}s old - fetching via REST API"
            )
            try:
                # Fetch underlying price via REST (skip_cache=True to bypass stale streaming cache)
                quote = self.client.get_quote(self.underlying_uic, "CfdOnIndex", skip_cache=True)
                if quote:
                    mid = quote.get('Quote', {}).get('Mid') or quote.get('Mid')
                    if mid and mid > 0:
                        self.current_price = mid
                        self.market_data.update_price(mid)
                        self._record_price_for_velocity(mid)  # MKT-001: Track for flash crash detection
                        logger.info(f"REST fallback: Updated US500.I price to {mid:.2f}")

                # Fetch VIX via get_vix_price() which has Yahoo Finance fallback
                # This is important because Saxo may return "NoAccess" for VIX data
                vix_price = self.client.get_vix_price(self.vix_uic)
                if vix_price and vix_price > 0:
                    self.current_vix = vix_price
                    self.market_data.update_vix(vix_price)
                    logger.info(f"REST fallback: Updated VIX to {vix_price:.2f}")

                # Reset stale counter on successful REST fetch
                self._consecutive_stale_data_warnings = 0
            except Exception as e:
                logger.error(f"REST fallback failed: {e}")

                # CONN-007: Emergency close if position open AND data blackout persists
                if self.position and self.state in [IronFlyState.POSITION_OPEN, IronFlyState.MONITORING_EXIT]:
                    if self._consecutive_stale_data_warnings >= MAX_STALE_DATA_WARNINGS_BEFORE_EMERGENCY:
                        # CRITICAL: Trigger emergency close - we're flying blind with an open position
                        logger.critical(
                            f"CONN-007: DATA BLACKOUT EMERGENCY! {self._consecutive_stale_data_warnings} "
                            f"consecutive data failures with open position. TRIGGERING EMERGENCY CLOSE."
                        )
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_DATA_BLACKOUT_EMERGENCY",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "consecutive_failures": self._consecutive_stale_data_warnings,
                            "description": f"Data blackout: {self._consecutive_stale_data_warnings} consecutive failures - emergency close triggered",
                            "result": "EMERGENCY CLOSE INITIATED - better to exit than fly blind"
                        })
                        # Trigger emergency close
                        emergency_result = self._emergency_close_position(
                            reason=f"CONN-007: Data blackout ({self._consecutive_stale_data_warnings} failures)"
                        )
                        logger.critical(f"CONN-007 emergency close result: {emergency_result}")
                        return f"EMERGENCY: Data blackout triggered close - {emergency_result}"
                    elif self._consecutive_stale_data_warnings >= 3:
                        # Warning level - not yet emergency
                        logger.critical(
                            f"CRITICAL: Market data stale with open position! "
                            f"Failure #{self._consecutive_stale_data_warnings}/{MAX_STALE_DATA_WARNINGS_BEFORE_EMERGENCY} - "
                            f"emergency close at {MAX_STALE_DATA_WARNINGS_BEFORE_EMERGENCY}."
                        )
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_STALE_DATA",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "consecutive_failures": self._consecutive_stale_data_warnings,
                            "description": f"Price data stale for {stale_age:.1f}s - both WebSocket and REST failed",
                            "result": f"WARNING: {MAX_STALE_DATA_WARNINGS_BEFORE_EMERGENCY - self._consecutive_stale_data_warnings} more failures until emergency close"
                        })
        else:
            self._consecutive_stale_data_warnings = 0  # Reset on fresh data

        # SAFETY: Check max trades per day guard
        if self.trades_today >= MAX_TRADES_PER_DAY and self.state == IronFlyState.READY_TO_ENTER:
            self.state = IronFlyState.DAILY_COMPLETE
            logger.warning(f"Max trades per day ({MAX_TRADES_PER_DAY}) reached - blocking entry")
            return f"Max trades per day ({MAX_TRADES_PER_DAY}) reached"

        # State machine
        if self.state == IronFlyState.IDLE:
            return self._handle_idle_state(current_time)

        elif self.state == IronFlyState.WAITING_OPENING_RANGE:
            return self._handle_opening_range_state(current_time)

        elif self.state == IronFlyState.READY_TO_ENTER:
            return self._handle_ready_to_enter_state(current_time)

        elif self.state in [IronFlyState.POSITION_OPEN, IronFlyState.MONITORING_EXIT]:
            return self._handle_position_monitoring(current_time)

        elif self.state == IronFlyState.CLOSING:
            return self._handle_closing_state(current_time)

        elif self.state == IronFlyState.DAILY_COMPLETE:
            return "Daily trading complete - waiting for next day"

        return "No action"

    def _reconcile_positions_with_broker(self):
        """
        SAFETY: Check broker for existing positions and sync state on startup.

        This prevents the catastrophic scenario where:
        1. Bot crashes while holding a position
        2. Bot restarts and doesn't know about the position
        3. Position goes unmonitored (no stop-loss protection)
        4. Or worse, bot enters a NEW position (doubling exposure)

        Enhanced to properly reconstruct IronFlyPosition for full monitoring.
        Also checks for and cancels any orphaned pending orders.

        POS-005 (2026-02-04): Now uses Position Registry for multi-bot isolation.
        First checks registry for Iron Fly positions, only falls back to strike-based
        detection if registry has no entries (for backwards compatibility).
        """
        if self.dry_run:
            logger.info("Position reconciliation skipped (dry-run mode)")
            return

        # FIRST: Check for and cancel any orphaned pending orders
        # This must happen BEFORE position reconciliation to avoid confusion
        self._check_and_cancel_pending_orders_on_startup()

        try:
            logger.info("Reconciling positions with broker...")
            broker_positions = self.client.get_positions()

            if not broker_positions:
                logger.info("No positions found at broker - starting fresh")
                return

            # POS-005: First try to filter by Position Registry
            # This is the safe path when running with MEIC - each bot only sees its own positions
            try:
                my_registry_positions = self.registry.get_positions(self.bot_name)
            except Exception as e:
                logger.error(f"POS-005: Registry error getting positions: {e}")
                my_registry_positions = set()
            valid_position_ids = {str(p.get("PositionId")) for p in broker_positions}

            # Clean up orphaned registry entries (positions that no longer exist)
            try:
                orphans = self.registry.cleanup_orphans(valid_position_ids)
                if orphans:
                    logger.warning(f"POS-005: Cleaned up {len(orphans)} orphaned registry entries")
            except Exception as e:
                logger.error(f"POS-005: Registry error during orphan cleanup: {e}")

            if my_registry_positions:
                # We have registry entries - use registry-based reconciliation
                logger.info(f"POS-005: Found {len(my_registry_positions)} Iron Fly positions in registry")
                spx_options = [
                    p for p in broker_positions
                    if str(p.get("PositionId")) in my_registry_positions
                ]
                if len(spx_options) != len(my_registry_positions):
                    logger.warning(
                        f"POS-005: Registry/broker mismatch - registry has {len(my_registry_positions)}, "
                        f"broker has {len(spx_options)} matching positions"
                    )
            else:
                # No registry entries - fall back to strike-based detection
                # This is for backwards compatibility with positions opened before registry existed
                logger.info("POS-005: No positions in registry - using strike-based detection (legacy mode)")

                # Look for SPX/SPXW options (StockIndexOption type)
                # Note: Saxo API returns nested structure - PositionBase.AssetType and DisplayAndFormat.Description
                spx_options = []
                for p in broker_positions:
                    # Extract from nested Saxo structure
                    position_base = p.get('PositionBase', {})
                    display_format = p.get('DisplayAndFormat', {})
                    asset_type = position_base.get('AssetType', '')
                    description = str(display_format.get('Description', '')).upper()
                    # Match both StockOption and StockIndexOption for SPX
                    if asset_type in ['StockOption', 'StockIndexOption'] and ('SPX' in description or 'SPXW' in description):
                        spx_options.append(p)

            if not spx_options:
                logger.info("No SPX option positions found at broker - starting fresh")
                return

            logger.info(f"Found {len(spx_options)} SPX option positions to reconcile")

            # Categorize by call/put and long/short
            short_calls = []
            short_puts = []
            long_calls = []
            long_puts = []

            for pos in spx_options:
                pos_base = pos.get("PositionBase", {})
                amount = pos_base.get("Amount", 0)
                # PutCall is nested inside OptionsData
                options_data = pos_base.get("OptionsData", {})
                put_call = options_data.get("PutCall", "")

                if amount < 0:  # Short position
                    if put_call == "Call":
                        short_calls.append(pos)
                    elif put_call == "Put":
                        short_puts.append(pos)
                elif amount > 0:  # Long position
                    if put_call == "Call":
                        long_calls.append(pos)
                    elif put_call == "Put":
                        long_puts.append(pos)

            logger.info(f"Position breakdown: SC={len(short_calls)}, SP={len(short_puts)}, LC={len(long_calls)}, LP={len(long_puts)}")

            # POS-004: Detect multiple iron fly structures
            if len(short_calls) > 1 or len(short_puts) > 1 or len(long_calls) > 1 or len(long_puts) > 1:
                # Multiple potential iron flies detected - need to identify which legs belong together
                detected_flies = self._detect_multiple_iron_flies(short_calls, short_puts, long_calls, long_puts)
                if detected_flies:
                    logger.critical(
                        f"POS-004: MULTIPLE IRON FLIES DETECTED! Found {len(detected_flies)} potential structures"
                    )
                    # Use closest to current price if we can identify it
                    if self.current_price > 0:
                        best_fly = min(detected_flies, key=lambda f: abs(f["atm_strike"] - self.current_price))
                        logger.critical(
                            f"POS-004: Selecting iron fly closest to current price ({self.current_price:.2f}): "
                            f"ATM={best_fly['atm_strike']}"
                        )
                        # Reassign to the best match
                        short_calls = [best_fly["short_call"]]
                        short_puts = [best_fly["short_put"]]
                        long_calls = [best_fly["long_call"]]
                        long_puts = [best_fly["long_put"]]

                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_MULTIPLE_DETECTED",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "description": f"POS-004: Found {len(detected_flies)} iron fly structures, selected ATM={best_fly['atm_strike']}",
                            "detected_flies": [f"ATM={fly['atm_strike']}" for fly in detected_flies],
                            "result": "Monitoring closest structure - VERIFY OTHER POSITIONS MANUALLY"
                        })
                    else:
                        logger.critical(
                            "POS-004: Cannot select best iron fly (no current price). "
                            "MANUAL INTERVENTION REQUIRED."
                        )
                        self._set_critical_intervention(
                            f"POS-004: Multiple iron flies detected ({len(detected_flies)}) - manual selection required"
                        )
                        return

            # Check if we have a valid iron fly structure (1 of each)
            if len(short_calls) == 1 and len(short_puts) == 1 and len(long_calls) == 1 and len(long_puts) == 1:
                # Try to reconstruct the iron fly position
                sc = short_calls[0]
                sp = short_puts[0]
                lc = long_calls[0]
                lp = long_puts[0]

                # Get UICs for each leg (needed first to check against saved metadata)
                sc_uic = sc.get("Uic") or sc.get("PositionBase", {}).get("Uic")
                sp_uic = sp.get("Uic") or sp.get("PositionBase", {}).get("Uic")
                lc_uic = lc.get("Uic") or lc.get("PositionBase", {}).get("Uic")
                lp_uic = lp.get("Uic") or lp.get("PositionBase", {}).get("Uic")

                # Get quantity (absolute value since we know directions)
                quantity = abs(sc.get("PositionBase", {}).get("Amount", 1))

                # Try to extract strikes from nested Saxo structure
                # NOTE: Saxo may NOT return OptionsData.Strike depending on FieldGroups!
                sc_strike = sc.get("PositionBase", {}).get("OptionsData", {}).get("Strike", 0)
                sp_strike = sp.get("PositionBase", {}).get("OptionsData", {}).get("Strike", 0)
                lc_strike = lc.get("PositionBase", {}).get("OptionsData", {}).get("Strike", 0)
                lp_strike = lp.get("PositionBase", {}).get("OptionsData", {}).get("Strike", 0)

                # Get expiry from nested structure
                options_data = sc.get("PositionBase", {}).get("OptionsData", {})
                expiry_raw = options_data.get("ExpiryDate", "")
                expiry = expiry_raw[:10] if expiry_raw else ""

                # FIX (2026-01-23): If Saxo didn't return strike data (returns 0), try saved metadata
                # Match on UICs since those are reliable identifiers
                saved_metadata = self._load_position_metadata()
                used_saved_strikes = False

                if sc_strike == 0 and sp_strike == 0 and saved_metadata:
                    # Broker didn't return strike data - check if UICs match saved metadata
                    if (saved_metadata.get("short_call_uic") == sc_uic and
                        saved_metadata.get("short_put_uic") == sp_uic and
                        saved_metadata.get("long_call_uic") == lc_uic and
                        saved_metadata.get("long_put_uic") == lp_uic):
                        # UICs match! Use saved metadata for strikes
                        sc_strike = saved_metadata.get("atm_strike", 0)
                        sp_strike = saved_metadata.get("atm_strike", 0)
                        lc_strike = saved_metadata.get("upper_wing", 0)
                        lp_strike = saved_metadata.get("lower_wing", 0)
                        expiry = saved_metadata.get("expiry", "")
                        used_saved_strikes = True
                        logger.info(
                            f"POS-001: Using saved metadata strikes (UICs matched) - "
                            f"ATM={sc_strike}, Upper={lc_strike}, Lower={lp_strike}"
                        )
                    else:
                        logger.warning(
                            f"POS-001: Broker returned no strike data and UICs don't match saved metadata. "
                            f"Broker UICs: SC={sc_uic}, SP={sp_uic}, LC={lc_uic}, LP={lp_uic}. "
                            f"Saved UICs: SC={saved_metadata.get('short_call_uic')}, "
                            f"SP={saved_metadata.get('short_put_uic')}, "
                            f"LC={saved_metadata.get('long_call_uic')}, "
                            f"LP={saved_metadata.get('long_put_uic')}"
                        )

                # Validate iron fly structure: short strikes should be equal (ATM)
                if sc_strike == sp_strike and sc_strike > 0:
                    atm_strike = sc_strike
                    upper_wing = lc_strike
                    lower_wing = lp_strike

                    # POS-001: Use saved metadata for entry time, price, credit if available
                    if saved_metadata and (used_saved_strikes or (
                        saved_metadata.get("atm_strike") == atm_strike and
                        saved_metadata.get("upper_wing") == upper_wing and
                        saved_metadata.get("lower_wing") == lower_wing)):
                        entry_time_str = saved_metadata.get("entry_time")
                        entry_time = datetime.fromisoformat(entry_time_str) if entry_time_str else get_eastern_timestamp()
                        entry_price = saved_metadata.get("entry_price", self.current_price)
                        credit_received = saved_metadata.get("credit_received", 0.0)
                        logger.info(
                            f"POS-001: Using saved metadata - "
                            f"entry_time={entry_time}, credit=${credit_received / 100:.2f}"
                        )
                    else:
                        if saved_metadata:
                            logger.warning("POS-001: Saved metadata doesn't match broker positions, using defaults")
                        entry_time = get_eastern_timestamp()
                        entry_price = self.current_price
                        credit_received = 0.0

                    # credit_received is in CENTS, display in dollars
                    logger.critical(
                        f"🔄 RECONSTRUCTING IRON FLY from broker positions:\n"
                        f"   ATM Strike: {atm_strike}\n"
                        f"   Upper Wing: {upper_wing}\n"
                        f"   Lower Wing: {lower_wing}\n"
                        f"   Quantity: {quantity}\n"
                        f"   Expiry: {expiry}\n"
                        f"   Entry Time: {entry_time}\n"
                        f"   Credit: ${credit_received / 100:.2f}"
                    )

                    # Create the position object
                    self.position = IronFlyPosition(
                        atm_strike=atm_strike,
                        upper_wing=upper_wing,
                        lower_wing=lower_wing,
                        entry_time=entry_time,
                        entry_price=entry_price,
                        credit_received=credit_received,
                        quantity=quantity,
                        expiry=expiry,
                        short_call_uic=sc_uic,
                        short_put_uic=sp_uic,
                        long_call_uic=lc_uic,
                        long_put_uic=lp_uic
                    )

                    # Transition to monitoring state
                    self.state = IronFlyState.MONITORING_EXIT

                    self.trade_logger.log_safety_event({
                        "event_type": "IRON_FLY_POSITION_RECOVERED",
                        "spy_price": self.current_price,
                        "vix": self.current_vix,
                        "description": f"Recovered iron fly: {lower_wing}/{atm_strike}/{upper_wing} x{quantity}",
                        "result": "Position fully reconstructed - monitoring resumed"
                    })

                    logger.critical("✅ Iron Fly position recovered - monitoring wing breaches and exits")
                    return

                else:
                    logger.warning(f"Short strikes don't match (SC={sc_strike}, SP={sp_strike}) - not a standard iron fly")

            # If we get here, we have SPX options but not a valid iron fly structure
            if len(spx_options) >= 4:
                logger.critical(
                    f"ORPHANED POSITION DETECTED! Found {len(spx_options)} SPX options at broker "
                    "but cannot reconstruct valid iron fly. Transitioning to MONITORING_EXIT state."
                )
                self.trade_logger.log_safety_event({
                    "event_type": "IRON_FLY_ORPHAN_DETECTED",
                    "spy_price": self.current_price,
                    "vix": self.current_vix,
                    "description": f"Found {len(spx_options)} orphaned SPX options - invalid structure",
                    "result": "MANUAL INTERVENTION REQUIRED"
                })
                self.state = IronFlyState.MONITORING_EXIT
            elif spx_options:
                logger.warning(
                    f"Found {len(spx_options)} SPX options at broker (not a full iron fly). "
                    "These may be partial fills or unrelated positions."
                )

        except Exception as e:
            logger.error(f"Error reconciling positions with broker: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Don't fail startup, but log the error

    def _detect_multiple_iron_flies(
        self,
        short_calls: List[Dict],
        short_puts: List[Dict],
        long_calls: List[Dict],
        long_puts: List[Dict]
    ) -> List[Dict]:
        """
        POS-004: Detect and group multiple iron fly structures.

        When broker has more than 4 SPX options, tries to identify which legs
        belong together by matching:
        1. Same expiry date
        2. Short call and short put at same strike (ATM)
        3. Long call above ATM, long put below ATM

        Args:
            short_calls: List of short call positions
            short_puts: List of short put positions
            long_calls: List of long call positions
            long_puts: List of long put positions

        Returns:
            List of detected iron fly structures, each with leg references
        """
        detected_flies = []

        # Helper to extract expiry from nested Saxo structure
        # FIX (2026-01-23): ExpiryDate and Strike are in PositionBase.OptionsData
        def get_expiry(pos):
            return pos.get("PositionBase", {}).get("OptionsData", {}).get("ExpiryDate", "")[:10] or ""

        def get_strike(pos):
            return pos.get("PositionBase", {}).get("OptionsData", {}).get("Strike", 0)

        # Group by expiry first
        expiries = set()
        for pos in short_calls + short_puts + long_calls + long_puts:
            expiry = get_expiry(pos)
            if expiry:
                expiries.add(expiry)

        for expiry in expiries:
            # Filter legs by this expiry
            sc_for_expiry = [p for p in short_calls if get_expiry(p) == expiry]
            sp_for_expiry = [p for p in short_puts if get_expiry(p) == expiry]
            lc_for_expiry = [p for p in long_calls if get_expiry(p) == expiry]
            lp_for_expiry = [p for p in long_puts if get_expiry(p) == expiry]

            # Find matching short call/put pairs (same ATM strike)
            for sc in sc_for_expiry:
                sc_strike = get_strike(sc)
                for sp in sp_for_expiry:
                    sp_strike = get_strike(sp)
                    if sc_strike == sp_strike and sc_strike > 0:
                        atm_strike = sc_strike
                        # Find wings: long call above ATM, long put below ATM
                        upper_wings = [lc for lc in lc_for_expiry if get_strike(lc) > atm_strike]
                        lower_wings = [lp for lp in lp_for_expiry if get_strike(lp) < atm_strike]

                        if upper_wings and lower_wings:
                            # Pick closest wing strikes
                            upper_wing = min(upper_wings, key=lambda x: get_strike(x) or float('inf'))
                            lower_wing = max(lower_wings, key=lambda x: get_strike(x) or 0)

                            detected_flies.append({
                                "atm_strike": atm_strike,
                                "upper_wing_strike": get_strike(upper_wing),
                                "lower_wing_strike": get_strike(lower_wing),
                                "expiry": expiry,
                                "short_call": sc,
                                "short_put": sp,
                                "long_call": upper_wing,
                                "long_put": lower_wing
                            })

        logger.info(f"POS-004: Detected {len(detected_flies)} iron fly structures from {len(short_calls + short_puts + long_calls + long_puts)} options")
        return detected_flies

    def _handle_idle_state(self, current_time: datetime) -> str:
        """
        Handle IDLE state - check if we should start monitoring opening range.

        We transition to WAITING_OPENING_RANGE at 9:30 AM EST.
        """
        # FOMC-001: Check for FOMC blackout day FIRST (before all other checks)
        # Per Doc Severson: "NEVER trade on FOMC or major economic data days"
        # On FOMC days, skip the entire day immediately with clear logging
        if self.fed_meeting_blackout:
            from shared.event_calendar import is_fomc_meeting_day
            today = current_time.date()
            if is_fomc_meeting_day(today):
                fomc_msg = f"FOMC meeting day ({today.strftime('%b %d, %Y')})"

                # Log prominently (only on first detection - when state is not yet DAILY_COMPLETE)
                if self.state != IronFlyState.DAILY_COMPLETE:
                    logger.warning("=" * 70)
                    logger.warning(f"📅 FOMC-001: {fomc_msg}")
                    logger.warning("   Trading BLOCKED for the entire day.")
                    logger.warning("   Bot will send hourly heartbeats only.")
                    logger.warning("=" * 70)
                    self.trade_logger.log_event(f"FOMC BLACKOUT: {fomc_msg} - Trading blocked for today")
                    self._log_filter_event("FOMC_BLACKOUT", fomc_msg)
                    self.state = IronFlyState.DAILY_COMPLETE

                return f"📅 FOMC-001: FOMC blackout day - no trading (hourly heartbeat)"

        market_open = dt_time(9, 30)

        # Check if it's a new trading day (reset state)
        if current_time.time() < market_open:
            return "Waiting for market open (9:30 AM EST)"

        if current_time.time() >= market_open and current_time.time() < self.entry_time:
            # Ensure we have valid market data before starting opening range
            if self.current_price <= 0:
                return f"Waiting for market data (price={self.current_price:.2f})"

            # Start monitoring opening range
            self.state = IronFlyState.WAITING_OPENING_RANGE
            self.opening_range = OpeningRange(
                start_time=current_time,
                opening_vix=self.current_vix if self.current_vix > 0 else 0.0
            )
            self.opening_range.update(self.current_price, self.current_vix)

            self.trade_logger.log_event(
                f"Started monitoring opening range - {self.underlying_symbol}: "
                f"{self.current_price:.2f}, VIX: {self.current_vix:.2f}"
            )

            return f"Started monitoring opening range - {self.underlying_symbol}: {self.current_price:.2f}, VIX: {self.current_vix:.2f}"

        # It's past 10:00 AM and we're still idle - likely bot started late
        if current_time.time() >= self.entry_time:
            self.state = IronFlyState.DAILY_COMPLETE
            return "Bot started after entry window - skipping today"

        return "Waiting for market open"

    def _handle_opening_range_state(self, current_time: datetime) -> str:
        """
        Handle WAITING_OPENING_RANGE state - track high/low until 10:00 AM.

        During this phase, we're building up the opening range (9:30-10:00 AM).
        """
        # Update opening range with latest price/VIX
        self.opening_range.update(self.current_price, self.current_vix)

        # Check if opening range period complete (10:00 AM)
        if current_time.time() >= self.entry_time:
            # Validate we have valid opening range data
            if self.opening_range.high <= 0 or self.opening_range.low == float('inf'):
                logger.error(
                    f"Invalid opening range data: High={self.opening_range.high}, "
                    f"Low={self.opening_range.low} - skipping entry"
                )
                self.state = IronFlyState.DAILY_COMPLETE
                self.trade_logger.log_safety_event({
                    "event_type": "IRON_FLY_INVALID_OPENING_RANGE",
                    "spy_price": self.current_price,
                    "vix": self.current_vix,
                    "description": f"Opening range invalid: High={self.opening_range.high}, Low={self.opening_range.low}",
                    "result": "Entry blocked - no trade today"
                })
                return "Invalid opening range data - skipping entry"

            self.opening_range.is_complete = True
            self.state = IronFlyState.READY_TO_ENTER

            self.trade_logger.log_event(
                f"Opening range complete - High: {self.opening_range.high:.2f}, "
                f"Low: {self.opening_range.low:.2f}, Range: {self.opening_range.range_width:.2f}, "
                f"VIX: {self.current_vix:.2f}"
            )

            return (f"Opening range complete - High: {self.opening_range.high:.2f}, "
                    f"Low: {self.opening_range.low:.2f}, Range: {self.opening_range.range_width:.2f}")

        # Display current opening range data
        high_str = f"{self.opening_range.high:.2f}" if self.opening_range.high > 0 else "N/A"
        low_str = f"{self.opening_range.low:.2f}" if self.opening_range.low < float('inf') else "N/A"
        range_str = f"{self.opening_range.range_width:.2f}" if self.opening_range.range_width > 0 else "N/A"

        return (f"Monitoring opening range - Current: {self.current_price:.2f}, "
                f"High: {high_str}, Low: {low_str}, Range: {range_str}")

    def _handle_ready_to_enter_state(self, current_time: datetime) -> str:
        """
        Handle READY_TO_ENTER state - check filters and enter position.

        All filters are checked here before entry:
        1. FOMC meeting filter (binary event - skip entire day)
        2. Economic calendar filter (CPI/PPI/Jobs - skip entire day)
        3. VIX level filter
        4. VIX spike filter
        5. Price-in-range filter (Trend Day detection)
        6. Price-near-midpoint filter (directional bias detection)
        """
        # FILTER 1: FOMC Meeting check (highest priority - binary event)
        fomc_ok, fomc_reason = self.check_fed_meeting_filter()
        if not fomc_ok:
            self.state = IronFlyState.DAILY_COMPLETE
            self.trade_logger.log_event(f"FILTER BLOCKED: {fomc_reason}")
            self._log_filter_event("FOMC_BLACKOUT", fomc_reason)
            self._log_opening_range_to_sheets("SKIP", fomc_reason)
            return f"Entry blocked - {fomc_reason}"

        # FILTER 1.5: TIME-003 - Early close day cutoff check
        early_close_blocked, early_close_reason = self.is_past_early_close_cutoff()
        if early_close_blocked:
            self.state = IronFlyState.DAILY_COMPLETE
            self.trade_logger.log_event(f"FILTER BLOCKED: {early_close_reason}")
            self._log_filter_event("EARLY_CLOSE_CUTOFF", early_close_reason)
            self._log_opening_range_to_sheets("SKIP", early_close_reason)
            return f"Entry blocked - {early_close_reason}"

        # FILTER 2: Economic calendar check (CPI, PPI, Jobs Report)
        econ_ok, econ_reason = self.check_economic_calendar_filter()
        if not econ_ok:
            self.state = IronFlyState.DAILY_COMPLETE
            self.trade_logger.log_event(f"FILTER BLOCKED: {econ_reason}")
            self._log_filter_event("ECONOMIC_CALENDAR", econ_reason)
            self._log_opening_range_to_sheets("SKIP", econ_reason)
            return f"Entry blocked - {econ_reason}"

        # FILTER 3: VIX level check (at entry time)
        if self.current_vix > self.max_vix:
            self.state = IronFlyState.DAILY_COMPLETE
            reason = f"VIX {self.current_vix:.2f} > {self.max_vix}"
            self.trade_logger.log_event(f"FILTER BLOCKED: {reason}")
            self._log_filter_event("VIX_LEVEL", reason)
            self._log_opening_range_to_sheets("SKIP", reason)
            return f"Entry blocked - VIX too high ({reason})"

        # FILTER 4: VIX spike check
        if self.opening_range.vix_spike_percent > self.vix_spike_threshold:
            self.state = IronFlyState.DAILY_COMPLETE
            reason = f"VIX spike {self.opening_range.vix_spike_percent:.1f}% > {self.vix_spike_threshold}%"
            self.trade_logger.log_event(f"FILTER BLOCKED: {reason}")
            self._log_filter_event("VIX_SPIKE", reason)
            self._log_opening_range_to_sheets("SKIP", reason)
            return f"Entry blocked - {reason}"

        # FILTER 5: Price within opening range check (Trend Day detection)
        # Can be disabled via config: filters.require_price_in_range = false
        if self.require_price_in_range and not self.opening_range.is_price_in_range(self.current_price):
            self.state = IronFlyState.DAILY_COMPLETE
            reason = f"Price {self.current_price:.2f} outside range [{self.opening_range.low:.2f}-{self.opening_range.high:.2f}]"
            self.trade_logger.log_event(f"FILTER BLOCKED: Trend Day - {reason}")
            self._log_filter_event("TREND_DAY", reason)
            self._log_opening_range_to_sheets("SKIP", reason)
            return f"Entry blocked - Trend Day detected ({reason})"

        # FILTER 6: Price near midpoint check (ideal entry - avoids directional bias)
        # Doc Severson's strategy prefers entries when price is near the MIDDLE of the range,
        # not at extremes. Price near range high = bullish momentum, near low = bearish.
        # Can be disabled via config: filters.require_price_near_midpoint = false
        if self.require_price_near_midpoint and self.opening_range.range_width > 0:
            distance_from_mid = abs(self.opening_range.distance_from_midpoint(self.current_price))
            max_allowed_distance = (self.opening_range.range_width / 2) * (self.midpoint_tolerance_percent / 100)

            if distance_from_mid > max_allowed_distance:
                self.state = IronFlyState.DAILY_COMPLETE
                midpoint = self.opening_range.midpoint
                position_pct = ((self.current_price - self.opening_range.low) / self.opening_range.range_width) * 100
                direction = "HIGH (bullish bias)" if self.current_price > midpoint else "LOW (bearish bias)"
                reason = (f"Price {self.current_price:.2f} too far from midpoint {midpoint:.2f} "
                         f"(at {position_pct:.0f}% of range, near {direction})")
                self.trade_logger.log_event(f"FILTER BLOCKED: Midpoint - {reason}")
                self._log_filter_event("MIDPOINT_BIAS", reason)
                self._log_opening_range_to_sheets("SKIP", reason)
                return f"Entry blocked - {reason}"

        # All filters passed - enter position
        return self._enter_iron_fly()

    def _handle_position_monitoring(self, current_time: datetime) -> str:
        """
        Handle POSITION_OPEN/MONITORING_EXIT - check for exit conditions.

        Exit conditions (checked in order of priority):
        1. Stop loss: Price touches wing strike (IMMEDIATE market order)
        2. Take profit: Unrealized P&L >= profit target
        3. Time exit: Max hold time exceeded
        """
        if not self.position:
            return "No position to monitor"

        # Update position prices
        self._update_position_prices()

        # EXIT CHECK 1: Stop loss - wing breach (HIGHEST PRIORITY)
        breached, wing = self.position.is_wing_breached(self.current_price)
        if breached:
            return self._close_position("STOP_LOSS",
                f"Price {self.current_price:.2f} touched {wing} wing at {getattr(self.position, f'{wing}_wing'):.2f}")

        # EXIT CHECK 1.5: MKT-001 - Flash crash velocity detection
        flash_crash_result = self.check_flash_crash_and_close()
        if flash_crash_result:
            return flash_crash_result

        # EXIT CHECK 1.6: MAX-LOSS - Absolute loss circuit breaker
        # This protects against gaps through wings or illiquid stop fills
        # Note: unrealized_pnl is in CENTS, convert to dollars for comparison
        pnl_dollars = self.position.unrealized_pnl / 100
        max_loss_threshold = -MAX_LOSS_PER_CONTRACT * self.position.quantity  # In dollars
        if pnl_dollars <= max_loss_threshold:
            logger.critical(
                f"MAX-LOSS CIRCUIT BREAKER: P&L ${pnl_dollars:.2f} <= "
                f"threshold ${max_loss_threshold:.2f} - EMERGENCY CLOSE"
            )
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_MAX_LOSS_BREAKER",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "unrealized_pnl": pnl_dollars,
                "max_loss_threshold": max_loss_threshold,
                "description": f"Max loss circuit breaker triggered: P&L=${pnl_dollars:.2f}",
                "result": "EMERGENCY CLOSE TRIGGERED"
            })
            return self._close_position("MAX_LOSS",
                f"Max loss breaker: P&L ${pnl_dollars:.2f} <= ${max_loss_threshold:.2f}")

        # EXIT CHECK 2: Profit target
        # FIX (2026-01-31): Dynamic profit target based on credit received
        # FIX (2026-02-01): Profit target now includes commission to ensure net profit
        # Use _calculate_profit_target() which adds commission to the target
        profit_target_total = self._calculate_profit_target()
        net_pnl_dollars = self._calculate_net_pnl(pnl_dollars)

        if pnl_dollars >= profit_target_total:
            return self._close_position("PROFIT_TARGET",
                f"Profit target reached: Gross ${pnl_dollars:.2f} >= ${profit_target_total:.2f} (Net ${net_pnl_dollars:.2f})")

        # EXIT CHECK 3: Time exit
        if self.position.hold_time_minutes >= self.max_hold_minutes:
            return self._close_position("TIME_EXIT",
                f"Max hold time reached: {self.position.hold_time_minutes} min >= {self.max_hold_minutes} min")

        # Still holding - update state and log
        self.state = IronFlyState.MONITORING_EXIT
        distance, wing = self.position.distance_to_wing(self.current_price)

        return (f"Monitoring - Gross P&L: ${pnl_dollars:.2f}, Net: ${net_pnl_dollars:.2f}, "
                f"Distance to {wing} wing: {distance:.2f} pts, "
                f"Hold time: {self.position.hold_time_minutes} min")

    def _handle_closing_state(self, current_time: datetime) -> str:
        """
        Handle CLOSING state - verify position is closed with timeout protection.

        This state handles the period between initiating close orders and
        confirming all legs are actually closed at the broker.

        Enhanced verification (like Delta Neutral):
        1. Check each close order's fill status via order ID
        2. Track which legs are verified closed
        3. Log detailed progress

        SAFETY: Includes timeout detection to prevent getting stuck in CLOSING
        state indefinitely if orders fail.
        """
        # Initialize closing timestamp if not set
        if self.closing_started_at is None:
            self.closing_started_at = get_eastern_timestamp()
            logger.info("Close initiated - waiting for order confirmation")

        # In dry-run mode, immediately complete
        if self.dry_run:
            self.state = IronFlyState.DAILY_COMPLETE
            self.closing_started_at = None
            return "Position closed - daily trading complete"

        # Check for timeout (position stuck in CLOSING state)
        closing_duration = (current_time - self.closing_started_at).total_seconds()
        if closing_duration > MAX_CLOSING_TIMEOUT_SECONDS:
            logger.critical(
                f"CLOSING TIMEOUT: Position stuck in CLOSING state for {closing_duration:.0f}s "
                f"(max {MAX_CLOSING_TIMEOUT_SECONDS}s). Manual intervention required!"
            )
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_CLOSE_TIMEOUT",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"Position stuck in CLOSING state for {closing_duration:.0f}s",
                "result": "MANUAL INTERVENTION REQUIRED - Position may still be open at broker"
            })
            # Don't transition to DAILY_COMPLETE - leave in CLOSING so operator knows there's an issue
            return f"CRITICAL: Close timeout after {closing_duration:.0f}s - manual intervention required"

        # =====================================================================
        # ENHANCED VERIFICATION: Check each close order's fill status
        # =====================================================================
        if self.position and self.position.close_order_ids:
            legs_pending = []
            legs_verified = []

            for leg_name, order_id in self.position.close_order_ids.items():
                if not order_id:
                    continue

                # Skip already verified legs
                if (self.position.close_legs_verified and
                        self.position.close_legs_verified.get(leg_name)):
                    legs_verified.append(leg_name)
                    continue

                # Get the UIC for this leg (for activity/position check)
                leg_uic = None
                if leg_name == "short_call":
                    leg_uic = self.position.short_call_uic
                elif leg_name == "short_put":
                    leg_uic = self.position.short_put_uic
                elif leg_name == "long_call":
                    leg_uic = self.position.long_call_uic
                elif leg_name == "long_put":
                    leg_uic = self.position.long_put_uic

                try:
                    order_status = self.client.get_order_status(order_id)

                    # CRITICAL FIX (2026-01-23): Handle case where order is "not found"
                    # This means the order likely filled and was removed from the orders endpoint
                    if order_status is None:
                        logger.warning(
                            f"Close order {order_id} for {leg_name} not found in orders endpoint - "
                            f"checking activities/positions..."
                        )
                        # Check if filled via activities
                        if leg_uic:
                            filled, fill_details = self.client.check_order_filled_by_activity(order_id, leg_uic)
                            if filled:
                                logger.info(f"✓ Close order verified via activity: {leg_name} (order {order_id}) FILLED")
                                if self.position.close_legs_verified:
                                    self.position.close_legs_verified[leg_name] = True
                                legs_verified.append(leg_name)
                                continue

                        # If not confirmed filled, treat as pending (will be caught by position check below)
                        legs_pending.append(f"{leg_name}(verifying)")
                        continue

                    # Check the status field - Saxo uses "Status" not "status"
                    status = order_status.get("Status") or order_status.get("status", "Unknown")

                    if status == "Filled":
                        logger.info(f"✓ Close order verified: {leg_name} (order {order_id}) FILLED")
                        if self.position.close_legs_verified:
                            self.position.close_legs_verified[leg_name] = True
                        legs_verified.append(leg_name)

                    elif status in ["Cancelled", "Rejected"]:
                        logger.error(f"✗ Close order FAILED: {leg_name} (order {order_id}) status={status}")
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_CLOSE_ORDER_FAILED",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "description": f"Close order for {leg_name} was {status}",
                            "result": "MANUAL INTERVENTION MAY BE REQUIRED"
                        })
                        legs_pending.append(f"{leg_name}({status})")

                    elif status == "Unknown":
                        # FIX (2026-01-23): "Unknown" status often means order filled and disappeared
                        # Market orders fill in ~3 seconds - check activities immediately
                        logger.warning(
                            f"Close order {order_id} for {leg_name} has Unknown status - "
                            f"checking activities/positions..."
                        )
                        if leg_uic:
                            filled, fill_details = self.client.check_order_filled_by_activity(order_id, leg_uic)
                            if filled:
                                logger.info(f"✓ Close order verified via activity: {leg_name} (order {order_id}) FILLED")
                                if self.position.close_legs_verified:
                                    self.position.close_legs_verified[leg_name] = True
                                legs_verified.append(leg_name)
                                continue
                        # Still unknown - add to pending
                        legs_pending.append(f"{leg_name}({status})")

                    else:
                        # Still working/pending (e.g., "Placed", "Working")
                        legs_pending.append(f"{leg_name}({status})")

                except Exception as e:
                    logger.warning(f"Error checking {leg_name} close order status: {e}")
                    legs_pending.append(f"{leg_name}(error)")

            # Log progress
            if legs_verified:
                logger.debug(f"Close orders verified: {legs_verified}")
            if legs_pending:
                logger.info(f"Close orders pending: {legs_pending}")
                return f"Waiting for close orders: {len(legs_verified)}/4 verified, pending: {legs_pending}"

            # All close orders verified filled
            if len(legs_verified) >= len(self.position.close_order_ids):
                logger.info(f"All {len(legs_verified)} close orders verified FILLED")

        # =====================================================================
        # FALLBACK: Verify no positions remain at broker
        # =====================================================================
        try:
            broker_positions = self.client.get_positions()
            if broker_positions:
                # Check for any remaining SPX options (LIVE-001: check both asset types)
                # FIX (2026-01-23): Use nested Saxo structure for AssetType and Description
                spx_options = []
                for p in broker_positions:
                    pos_base = p.get('PositionBase', {})
                    display_format = p.get('DisplayAndFormat', {})
                    asset_type = pos_base.get('AssetType', '')
                    description = str(display_format.get('Description', '')).upper()
                    if asset_type in ['StockOption', 'StockIndexOption'] and ('SPX' in description or 'SPXW' in description):
                        spx_options.append(p)
                if spx_options:
                    logger.warning(f"Broker still shows {len(spx_options)} SPX options - waiting")
                    return f"Waiting for close confirmation - {len(spx_options)} legs still open at broker"

            # No positions found - close confirmed
            logger.info("Position close CONFIRMED - no SPX options remaining at broker")
            self.state = IronFlyState.DAILY_COMPLETE
            self.closing_started_at = None
            self.position = None
            self._clear_position_metadata()  # POS-001: Clear saved metadata
            self._unregister_positions_from_registry()  # POS-005: Clear from registry
            return "Position closed - all legs confirmed closed at broker"

        except Exception as e:
            logger.error(f"Error verifying close at broker: {e}")
            return f"Waiting for close confirmation (broker verification error: {e})"

    # =========================================================================
    # ENTRY AND EXIT METHODS
    # =========================================================================

    def _enter_iron_fly(self) -> str:
        """
        Enter an Iron Fly position.

        Structure:
        - Short ATM Call (at first strike ABOVE current price per Doc's bias rule)
        - Short ATM Put (at same strike)
        - Long Call (ATM + expected move)
        - Long Put (ATM - expected move)

        Returns:
            str: Description of action taken
        """
        # Calculate strikes per Doc Severson's rules
        # Bias: Use first strike ABOVE current price (put skew compensation)
        atm_strike = self._round_up_to_strike(self.current_price)
        expected_move = self._calculate_expected_move()
        upper_wing = atm_strike + expected_move
        lower_wing = atm_strike - expected_move

        self.trade_logger.log_event(
            f"ENTERING IRON FLY: ATM={atm_strike}, Wings={lower_wing}/{upper_wing}, "
            f"Expected Move={expected_move:.2f}, {self.underlying_symbol}={self.current_price:.2f}"
        )

        # Log opening range data for fact-checking
        self._log_opening_range_to_sheets(
            "ENTER",
            f"All filters passed - Entering Iron Fly at {atm_strike}",
            atm_strike=atm_strike,
            wing_width=expected_move
        )

        if self.dry_run:
            # REALISTIC CREDIT CALCULATION (BUG FIX: was $4900, now ~$150-300)
            # Real iron fly credit on SPX is typically $2-3 per point of wing width
            # Example: 70 point wings = $140-210 credit per contract
            simulated_credit = expected_move * DEFAULT_SIMULATED_CREDIT_PER_WING_POINT * self.position_size

            # Get current Eastern time for entry timestamp
            entry_time_eastern = get_eastern_timestamp()

            self.position = IronFlyPosition(
                atm_strike=atm_strike,
                upper_wing=upper_wing,
                lower_wing=lower_wing,
                entry_time=entry_time_eastern,  # Use Eastern time consistently
                entry_price=self.current_price,
                credit_received=simulated_credit,
                quantity=self.position_size,
                expiry=entry_time_eastern.strftime("%Y-%m-%d"),
                # Simulate position IDs
                short_call_id="DRY_RUN_SC",
                short_put_id="DRY_RUN_SP",
                long_call_id="DRY_RUN_LC",
                long_put_id="DRY_RUN_LP",
                # Initialize simulated value to credit received (cost to close = credit at entry)
                simulated_current_value=simulated_credit
            )
            self.state = IronFlyState.POSITION_OPEN
            self.trades_today += 1
            self.daily_premium_collected += simulated_credit  # Track premium for logging

            # Log the simulated trade to Google Sheets
            # Note: simulated_credit is in cents (multiplied by 100 for contract multiplier)
            self.trade_logger.log_trade(
                action="[SIMULATED] OPEN_IRON_FLY",
                strike=f"{lower_wing}/{atm_strike}/{upper_wing}",
                price=simulated_credit / 100,  # Cents to dollars (per-contract credit)
                delta=0.0,  # Iron Fly is delta neutral at entry
                pnl=0.0,  # No P&L at entry
                saxo_client=self.client,  # For currency conversion
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type="Iron Fly",
                expiry_date=entry_time_eastern.strftime("%Y-%m-%d"),
                dte=0,  # 0DTE
                premium_received=simulated_credit / 100,  # Cents to dollars
                trade_reason="All filters passed"
            )

            logger.info(f"[DRY RUN] Iron Fly position created: Credit=${simulated_credit / 100:.2f}, "
                       f"Wings={lower_wing}/{atm_strike}/{upper_wing}")
            return f"[DRY RUN] Entered Iron Fly at {atm_strike} with ${simulated_credit / 100:.2f} credit"

        # =================================================================
        # LIVE ORDER PLACEMENT
        # =================================================================

        # Step 1: Find option UICs for all 4 legs
        logger.info(f"Finding 0DTE options for Iron Fly: ATM={atm_strike}, Wings={lower_wing}/{upper_wing}")

        iron_fly_options = self.client.find_iron_fly_options(
            underlying_uic=self.underlying_uic,
            atm_strike=atm_strike,
            upper_wing_strike=upper_wing,
            lower_wing_strike=lower_wing,
            target_dte_min=0,
            target_dte_max=1,
            option_root_uic=self.options_uic  # SPXW UIC 128 for StockIndexOptions
        )

        if not iron_fly_options:
            error_msg = "Failed to find option UICs for iron fly - ENTRY ABORTED"
            logger.error(error_msg)
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_OPTION_LOOKUP_FAILED",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"Could not find 0DTE options at strikes {lower_wing}/{atm_strike}/{upper_wing}",
                "result": "Entry blocked - no position opened"
            })
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        # Step 2: Get quotes for all 4 legs to calculate credit
        short_call_uic = iron_fly_options["short_call"]["uic"]
        short_put_uic = iron_fly_options["short_put"]["uic"]
        long_call_uic = iron_fly_options["long_call"]["uic"]
        long_put_uic = iron_fly_options["long_put"]["uic"]

        logger.info(f"Option UICs - SC:{short_call_uic}, SP:{short_put_uic}, LC:{long_call_uic}, LP:{long_put_uic}")

        # Get quotes for credit calculation (LIVE-001: Use StockIndexOption for SPX)
        short_call_quote = self.client.get_quote(short_call_uic, "StockIndexOption")
        short_put_quote = self.client.get_quote(short_put_uic, "StockIndexOption")
        long_call_quote = self.client.get_quote(long_call_uic, "StockIndexOption")
        long_put_quote = self.client.get_quote(long_put_uic, "StockIndexOption")

        if not all([short_call_quote, short_put_quote, long_call_quote, long_put_quote]):
            error_msg = "Failed to get quotes for all iron fly legs - ENTRY ABORTED"
            logger.error(error_msg)
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        # Extract bid/ask prices (sell at bid, buy at ask)
        sc_bid = short_call_quote.get('Quote', {}).get('Bid', 0)
        sc_ask = short_call_quote.get('Quote', {}).get('Ask', 0)
        sp_bid = short_put_quote.get('Quote', {}).get('Bid', 0)
        sp_ask = short_put_quote.get('Quote', {}).get('Ask', 0)
        lc_bid = long_call_quote.get('Quote', {}).get('Bid', 0)
        lc_ask = long_call_quote.get('Quote', {}).get('Ask', 0)
        lp_bid = long_put_quote.get('Quote', {}).get('Bid', 0)
        lp_ask = long_put_quote.get('Quote', {}).get('Ask', 0)

        # ORDER-005: Bid-ask spread validation
        wide_spreads = []
        for leg_name, bid, ask in [
            ("Short Call", sc_bid, sc_ask),
            ("Short Put", sp_bid, sp_ask),
            ("Long Call", lc_bid, lc_ask),
            ("Long Put", lp_bid, lp_ask),
        ]:
            if bid > 0 and ask > 0:
                mid_price = (bid + ask) / 2
                spread_pct = ((ask - bid) / mid_price) * 100 if mid_price > 0 else 0
                if spread_pct > self.max_bid_ask_spread_percent:
                    wide_spreads.append(f"{leg_name}: {spread_pct:.1f}%")

        if wide_spreads:
            spread_warning = f"ORDER-005: Wide bid-ask spreads detected: {', '.join(wide_spreads)}"
            logger.warning(spread_warning)
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_WIDE_SPREAD_WARNING",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": spread_warning,
                "threshold": f"{self.max_bid_ask_spread_percent}%",
                "result": "Entry proceeding with warning - monitor for slippage"
            })

        # Calculate net credit: premium received from shorts - premium paid for longs
        # Multiplied by 100 for contract multiplier
        credit_per_contract = (sc_bid + sp_bid - lc_ask - lp_ask)
        total_credit = credit_per_contract * self.position_size * 100

        logger.info(
            f"Iron Fly pricing: SC Bid={sc_bid:.2f}, SP Bid={sp_bid:.2f}, "
            f"LC Ask={lc_ask:.2f}, LP Ask={lp_ask:.2f}, "
            f"Net Credit=${total_credit / 100:.2f}"
        )

        if total_credit <= 0:
            error_msg = f"Iron fly would result in debit (${total_credit / 100:.2f}) - ENTRY ABORTED"
            logger.error(error_msg)
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_DEBIT_SPREAD",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"Iron fly would cost ${abs(total_credit / 100):.2f} instead of receiving credit",
                "result": "Entry blocked - no position opened"
            })
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        # Step 3: Place all 4 orders with fill verification
        # Each leg is placed and verified before proceeding to the next
        # SAFETY: Longs are placed first to minimize naked short exposure on partial fills
        entry_time_eastern = get_eastern_timestamp()

        # ORDER-004: Check critical intervention first (most severe)
        if not self._check_critical_intervention():
            error_msg = f"CRITICAL: Entry blocked - {self._critical_intervention_reason}"
            logger.error(error_msg)
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        # Check circuit breaker before attempting entry
        if not self._check_circuit_breaker():
            error_msg = f"Circuit breaker open - entry blocked: {self._circuit_breaker_reason}"
            logger.error(error_msg)
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        # FILTER-001: Re-validate VIX immediately before order placement
        # VIX could have spiked since the initial filter check (READY_TO_ENTER state)
        fresh_vix = self.client.get_vix_price(self.vix_uic)
        if fresh_vix and fresh_vix > self.max_vix:
            error_msg = (
                f"FILTER-001: VIX re-check failed - VIX {fresh_vix:.2f} > {self.max_vix} "
                f"(was {self.current_vix:.2f} at filter check)"
            )
            logger.warning(error_msg)
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_VIX_RECHECK_FAILED",
                "spy_price": self.current_price,
                "vix": fresh_vix,
                "vix_at_filter": self.current_vix,
                "max_vix": self.max_vix,
                "description": "VIX spiked between filter check and order placement",
                "result": "Entry blocked - no position opened"
            })
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg
        elif fresh_vix:
            logger.info(f"FILTER-001: VIX re-check passed - VIX {fresh_vix:.2f} <= {self.max_vix}")
            self.current_vix = fresh_vix  # Update cached value

        # ENTRY RETRY LOGIC: Up to 3 attempts with 15-second delays between attempts
        # This handles transient network/API issues while still failing safely on persistent problems
        MAX_ENTRY_ATTEMPTS = 3
        ENTRY_RETRY_DELAY_SECONDS = 15

        # Map leg names to their UICs and directions for unwind
        leg_unwind_map = {
            "long_call": {"uic": long_call_uic, "close_direction": BuySell.SELL},
            "long_put": {"uic": long_put_uic, "close_direction": BuySell.SELL},
            "short_call": {"uic": short_call_uic, "close_direction": BuySell.BUY},
            "short_put": {"uic": short_put_uic, "close_direction": BuySell.BUY},
        }

        last_error = None
        for attempt in range(1, MAX_ENTRY_ATTEMPTS + 1):
            orders_placed = []
            order_ids = {}
            fill_details = {}

            logger.info(f"Iron Fly entry attempt {attempt}/{MAX_ENTRY_ATTEMPTS}")

            # CRITICAL FIX (2026-01-23): Before retrying, check if any positions already exist
            # This prevents duplicate orders if previous attempt filled but verification failed
            if attempt > 1:
                logger.info("Checking for existing positions before retry...")
                try:
                    existing_positions = self.client.get_positions(include_greeks=False)
                    existing_uics = []
                    if existing_positions:
                        for pos in existing_positions:
                            pos_uic = pos.get("PositionBase", {}).get("Uic")
                            pos_amount = pos.get("PositionBase", {}).get("Amount", 0)
                            if pos_uic in [long_call_uic, long_put_uic, short_call_uic, short_put_uic]:
                                existing_uics.append(pos_uic)
                                logger.warning(
                                    f"Position already exists for UIC {pos_uic} (amount={pos_amount}) - "
                                    f"previous order likely filled!"
                                )

                    if existing_uics:
                        # We have existing positions - abort retry to prevent duplicates!
                        logger.critical(
                            f"ABORTING RETRY: Found {len(existing_uics)} existing positions from failed attempt. "
                            f"UICs: {existing_uics}. Previous orders likely filled but verification failed. "
                            f"MANUAL INTERVENTION REQUIRED to manage these positions."
                        )
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_DUPLICATE_PREVENTION",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "description": f"Aborted retry - found {len(existing_uics)} existing positions",
                            "result": "MANUAL INTERVENTION REQUIRED - check positions at broker"
                        })
                        # Trigger circuit breaker
                        self._open_circuit_breaker(
                            f"Duplicate order prevention - found existing positions: {existing_uics}"
                        )
                        self.state = IronFlyState.DAILY_COMPLETE
                        return f"CRITICAL: Entry aborted - existing positions detected. Check broker manually."

                except Exception as e:
                    logger.error(f"Failed to check existing positions: {e}")

            try:
                # Leg 1: Buy Long Call (wing protection) - LONGS FIRST for safety
                lc_success, lc_order_id, lc_fill = self._place_iron_fly_leg_with_verification(
                    uic=long_call_uic,
                    direction=BuySell.BUY,
                    amount=self.position_size,
                    leg_name="long_call",
                    strike=upper_wing
                )
                if lc_success and lc_order_id:
                    order_ids["long_call"] = lc_order_id
                    fill_details["long_call"] = lc_fill
                    orders_placed.append("long_call")
                else:
                    raise Exception("Failed to place/verify long call order")

                # Leg 2: Buy Long Put (wing protection)
                lp_success, lp_order_id, lp_fill = self._place_iron_fly_leg_with_verification(
                    uic=long_put_uic,
                    direction=BuySell.BUY,
                    amount=self.position_size,
                    leg_name="long_put",
                    strike=lower_wing
                )
                if lp_success and lp_order_id:
                    order_ids["long_put"] = lp_order_id
                    fill_details["long_put"] = lp_fill
                    orders_placed.append("long_put")
                else:
                    raise Exception("Failed to place/verify long put order")

                # Leg 3: Sell ATM Call (short) - SHORTS AFTER longs are in place
                sc_success, sc_order_id, sc_fill = self._place_iron_fly_leg_with_verification(
                    uic=short_call_uic,
                    direction=BuySell.SELL,
                    amount=self.position_size,
                    leg_name="short_call",
                    strike=atm_strike
                )
                if sc_success and sc_order_id:
                    order_ids["short_call"] = sc_order_id
                    fill_details["short_call"] = sc_fill
                    orders_placed.append("short_call")
                else:
                    raise Exception("Failed to place/verify short call order")

                # Leg 4: Sell ATM Put (short)
                sp_success, sp_order_id, sp_fill = self._place_iron_fly_leg_with_verification(
                    uic=short_put_uic,
                    direction=BuySell.SELL,
                    amount=self.position_size,
                    leg_name="short_put",
                    strike=atm_strike
                )
                if sp_success and sp_order_id:
                    order_ids["short_put"] = sp_order_id
                    fill_details["short_put"] = sp_fill
                    orders_placed.append("short_put")
                else:
                    raise Exception("Failed to place/verify short put order")

                # All 4 legs successfully placed and verified!
                logger.info(f"All 4 Iron Fly legs placed and verified on attempt {attempt}: {order_ids}")
                break  # Success - exit retry loop

            except Exception as e:
                # Partial fill on this attempt
                last_error = e
                error_msg = f"ORDER PLACEMENT/VERIFICATION FAILED (attempt {attempt}/{MAX_ENTRY_ATTEMPTS}): {e}"
                logger.warning(error_msg)
                logger.warning(f"Orders placed before failure: {orders_placed}")

                # AUTO-UNWIND any filled legs immediately
                if orders_placed:
                    unwind_results = []
                    for leg_name in orders_placed:
                        if leg_name in leg_unwind_map:
                            leg_info = leg_unwind_map[leg_name]
                            logger.warning(f"AUTO-UNWINDING partial fill: {leg_name} (UIC: {leg_info['uic']})")
                            try:
                                unwind_result = self.client.place_emergency_order(
                                    uic=leg_info["uic"],
                                    asset_type="StockIndexOption",
                                    buy_sell=leg_info["close_direction"],
                                    amount=self.position_size,
                                    to_open_close="ToClose"
                                )
                                if unwind_result and unwind_result.get("OrderId"):
                                    unwind_results.append({
                                        "leg": leg_name,
                                        "order_id": unwind_result.get("OrderId"),
                                        "status": "UNWIND_PLACED"
                                    })
                                    logger.info(f"✓ Unwind order placed for {leg_name}: {unwind_result.get('OrderId')}")
                                else:
                                    unwind_results.append({"leg": leg_name, "status": "UNWIND_FAILED", "error": "No OrderId"})
                                    logger.error(f"✗ Unwind failed for {leg_name}: No OrderId returned")
                            except Exception as unwind_err:
                                unwind_results.append({"leg": leg_name, "status": "UNWIND_ERROR", "error": str(unwind_err)})
                                logger.error(f"✗ Unwind error for {leg_name}: {unwind_err}")

                    self.trade_logger.log_safety_event({
                        "event_type": "IRON_FLY_PARTIAL_FILL_UNWIND",
                        "spy_price": self.current_price,
                        "vix": self.current_vix,
                        "description": f"Partial fill on attempt {attempt}/{MAX_ENTRY_ATTEMPTS}: {len(orders_placed)}/4 legs - unwound",
                        "result": f"Unwind results: {unwind_results}",
                        "orders_placed": orders_placed,
                        "order_ids": order_ids,
                        "unwind_results": unwind_results,
                        "will_retry": attempt < MAX_ENTRY_ATTEMPTS
                    })

                # If not the last attempt, wait before retrying
                if attempt < MAX_ENTRY_ATTEMPTS:
                    logger.info(f"Waiting {ENTRY_RETRY_DELAY_SECONDS}s before retry attempt {attempt + 1}...")
                    time.sleep(ENTRY_RETRY_DELAY_SECONDS)
                    continue

                # Final attempt failed - open circuit breaker and halt
                logger.critical(f"All {MAX_ENTRY_ATTEMPTS} entry attempts failed - opening circuit breaker")
                self._open_circuit_breaker(f"Entry failed after {MAX_ENTRY_ATTEMPTS} attempts: {last_error}")
                self.state = IronFlyState.DAILY_COMPLETE
                return f"CRITICAL: Entry failed after {MAX_ENTRY_ATTEMPTS} attempts - {last_error}"

        # If we get here but don't have all 4 legs, something went wrong
        if len(orders_placed) != 4:
            error_msg = f"Entry loop completed but only {len(orders_placed)}/4 legs filled"
            logger.critical(error_msg)
            self._open_circuit_breaker(error_msg)
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        # Step 4: Extract ACTUAL fill prices from fill_details
        # FIX (2026-01-23): Use actual fill prices from broker, not quoted bid/ask
        # This ensures P&L calculations match what the broker shows
        def get_fill_price(fill_detail: Optional[Dict], fallback: float) -> float:
            """Extract fill price from fill_details, with fallback to quoted price."""
            if fill_detail:
                # Try different keys that may contain the fill price
                price = fill_detail.get("fill_price") or fill_detail.get("FilledPrice") or fill_detail.get("Price")
                if price and price > 0:
                    return float(price)
            return fallback

        # Extract actual fill prices (fallback to quoted prices if not available)
        actual_sc_fill = get_fill_price(fill_details.get("short_call"), sc_bid)
        actual_sp_fill = get_fill_price(fill_details.get("short_put"), sp_bid)
        actual_lc_fill = get_fill_price(fill_details.get("long_call"), lc_ask)
        actual_lp_fill = get_fill_price(fill_details.get("long_put"), lp_ask)

        # Calculate ACTUAL credit from fill prices
        # Shorts: we SOLD, so we received the fill price
        # Longs: we BOUGHT, so we paid the fill price
        actual_credit_per_contract = (actual_sc_fill + actual_sp_fill - actual_lc_fill - actual_lp_fill)
        actual_total_credit = actual_credit_per_contract * self.position_size * 100  # In cents

        # Log comparison between quoted and actual prices
        logger.info(
            f"Fill prices - Quoted vs Actual:\n"
            f"  Short Call: ${sc_bid:.2f} -> ${actual_sc_fill:.2f}\n"
            f"  Short Put:  ${sp_bid:.2f} -> ${actual_sp_fill:.2f}\n"
            f"  Long Call:  ${lc_ask:.2f} -> ${actual_lc_fill:.2f}\n"
            f"  Long Put:   ${lp_ask:.2f} -> ${actual_lp_fill:.2f}\n"
            f"  Credit: ${total_credit / 100:.2f} (quoted) -> ${actual_total_credit / 100:.2f} (actual)"
        )

        # Use actual credit for position tracking
        credit_for_position = actual_total_credit

        # Step 5: Create position object with ACTUAL fill prices
        self.position = IronFlyPosition(
            atm_strike=iron_fly_options["atm_strike"],
            upper_wing=iron_fly_options["upper_wing"],
            lower_wing=iron_fly_options["lower_wing"],
            entry_time=entry_time_eastern,
            entry_price=self.current_price,
            credit_received=credit_for_position,  # Use ACTUAL credit
            quantity=self.position_size,
            expiry=iron_fly_options["expiry"][:10],  # YYYY-MM-DD
            # Store order IDs for management
            short_call_id=order_ids.get("short_call"),
            short_put_id=order_ids.get("short_put"),
            long_call_id=order_ids.get("long_call"),
            long_put_id=order_ids.get("long_put"),
            # Store UICs for price streaming
            short_call_uic=short_call_uic,
            short_put_uic=short_put_uic,
            long_call_uic=long_call_uic,
            long_put_uic=long_put_uic,
            # Initial prices from ACTUAL fills (for accurate P&L from start)
            short_call_price=actual_sc_fill,
            short_put_price=actual_sp_fill,
            long_call_price=actual_lc_fill,
            long_put_price=actual_lp_fill
        )

        self.state = IronFlyState.POSITION_OPEN
        self.trades_today += 1
        self.daily_premium_collected += credit_for_position  # Use actual credit

        # POS-001: Save position metadata for crash recovery
        self._save_position_metadata()

        # POS-005: Register positions with Position Registry for multi-bot isolation (2026-02-04)
        # This ensures Iron Fly and MEIC can both trade SPX without interference
        strategy_id = f"iron_fly_{entry_time_eastern.strftime('%Y%m%d_%H%M%S')}"
        self._register_positions_with_registry(strategy_id, fill_details, iron_fly_options)

        # Step 5: Subscribe to option price updates for position monitoring
        # LIVE-001: Use StockIndexOption for SPX/SPXW index options (not StockOption)
        try:
            self.client.subscribe_to_option(short_call_uic, self.handle_price_update, asset_type="StockIndexOption")
            self.client.subscribe_to_option(short_put_uic, self.handle_price_update, asset_type="StockIndexOption")
            self.client.subscribe_to_option(long_call_uic, self.handle_price_update, asset_type="StockIndexOption")
            self.client.subscribe_to_option(long_put_uic, self.handle_price_update, asset_type="StockIndexOption")
            logger.info("Subscribed to option price streams for position monitoring (StockIndexOption)")
        except Exception as e:
            logger.warning(f"Failed to subscribe to option streams (will use polling): {e}")

        # Log the trade to Google Sheets
        # FIX (2026-01-23): Use actual credit from fill prices, not quoted prices
        self.trade_logger.log_trade(
            action="OPEN_IRON_FLY",
            strike=f"{lower_wing}/{atm_strike}/{upper_wing}",
            price=actual_credit_per_contract,  # Actual credit per contract in dollars
            delta=0.0,  # Iron Fly is delta neutral at entry
            pnl=0.0,
            saxo_client=self.client,
            underlying_price=self.current_price,
            vix=self.current_vix,
            option_type="Iron Fly",
            expiry_date=self.position.expiry,
            dte=0,
            premium_received=credit_for_position / 100,  # Actual credit in dollars
            trade_reason="All filters passed"
        )

        logger.info(
            f"IRON FLY OPENED: ATM={atm_strike}, Wings={lower_wing}/{upper_wing}, "
            f"Credit=${credit_for_position / 100:.2f} (actual), Orders={order_ids}"
        )

        # ALERT: Send position opened alert AFTER all 4 legs are filled successfully
        credit_dollars = credit_for_position / 100
        self.alert_service.position_opened(
            position_summary=f"Iron Fly @ {atm_strike} (wings: {lower_wing}/{upper_wing})",
            cost_or_credit=credit_dollars,
            details={
                "atm_strike": atm_strike,
                "lower_wing": lower_wing,
                "upper_wing": upper_wing,
                "expected_move": expected_move,
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "expiry": str(self.position.expiry),
                "quantity": self.position.quantity,
                "profit_target": self._calculate_profit_target(),
                "max_hold_minutes": self.max_hold_minutes
            }
        )

        return f"Entered Iron Fly at {atm_strike} with ${credit_dollars:.2f} credit (actual fills)"

    def _close_position(self, reason: str, description: str) -> str:
        """
        Close the Iron Fly position.

        Args:
            reason: Exit reason code (PROFIT_TARGET, STOP_LOSS, TIME_EXIT)
            description: Human-readable description

        Returns:
            str: Description of action taken
        """
        if not self.position:
            return "No position to close"

        pnl = self.position.unrealized_pnl  # In cents
        hold_time = self.position.hold_time_minutes

        self.trade_logger.log_event(
            f"CLOSING IRON FLY: {reason} - {description} | "
            f"P&L: ${pnl / 100:.2f}, Hold time: {hold_time} min"  # Convert cents to dollars
        )

        # Update daily tracking
        self.daily_pnl += pnl

        if self.dry_run:
            # Log the simulated close to Google Sheets
            # Convert cents to dollars for logging
            self.trade_logger.log_trade(
                action=f"[SIMULATED] CLOSE_IRON_FLY_{reason}",
                strike=f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
                price=self.position.credit_received / 100,  # Original credit per contract (dollars)
                delta=0.0,
                pnl=pnl / 100,  # Convert cents to dollars
                saxo_client=self.client,  # For currency conversion
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type="Iron Fly",
                expiry_date=self.position.expiry,
                dte=0,
                premium_received=self.position.credit_received / 100,  # Convert cents to dollars
                trade_reason=description
            )

            self.position = None
            self._unregister_positions_from_registry()  # POS-005: Clear from registry
            self.state = IronFlyState.DAILY_COMPLETE
            return f"[DRY RUN] Closed position - {reason}: ${pnl / 100:.2f} P&L in {hold_time} min"

        # =================================================================
        # LIVE ORDER CLOSING
        # =================================================================

        # Step 1: Cancel any open limit orders (profit taker)
        if self.position.profit_order_id:
            logger.info(f"Cancelling profit-taking order: {self.position.profit_order_id}")
            try:
                self.client.cancel_order(self.position.profit_order_id)
            except Exception as e:
                logger.warning(f"Failed to cancel profit order (may already be filled/cancelled): {e}")

        # Step 2: Determine order type based on exit reason
        # STOP_LOSS/MAX_LOSS: Use MARKET orders for immediate execution (price is touching wing!)
        # PROFIT_TARGET/TIME_EXIT: Also use market orders for simplicity and reliability
        # Per Doc Severson: "Don't overstay" - exiting quickly is more important than optimal fills
        use_market_orders = (reason == "STOP_LOSS" or reason == "MAX_LOSS")
        order_type = OrderType.MARKET  # Always use market orders for exits

        if use_market_orders:
            logger.warning("STOP LOSS TRIGGERED - Using MARKET orders for immediate close!")

        # STOP-002: For stop loss, use retry logic with escalation
        if use_market_orders:
            return self._close_position_with_retries(reason, description, pnl, hold_time)

        # Step 3: Close all 4 legs (reverse the entry trades)
        close_orders = []
        close_order_ids = {}

        try:
            # Buy back short call (was sold at entry)
            if self.position.short_call_uic:
                logger.info(f"Closing: BUY {self.position.quantity} Short Call at {self.position.atm_strike}")

                # For stop-loss, use emergency order that bypasses circuit breaker
                # LIVE-001: Use StockIndexOption for SPX/SPXW index options
                if use_market_orders:
                    sc_close = self.client.place_emergency_order(
                        uic=self.position.short_call_uic,
                        asset_type="StockIndexOption",
                        buy_sell=BuySell.BUY,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                else:
                    sc_close = self.client.place_order_with_retry(
                        uic=self.position.short_call_uic,
                        asset_type="StockIndexOption",
                        buy_sell=BuySell.BUY,
                        amount=self.position.quantity,
                        order_type=order_type,
                        to_open_close="ToClose"
                    )

                if sc_close:
                    close_order_ids["short_call"] = sc_close.get("OrderId")
                    close_orders.append(("short_call", sc_close))

            # Buy back short put (was sold at entry)
            if self.position.short_put_uic:
                logger.info(f"Closing: BUY {self.position.quantity} Short Put at {self.position.atm_strike}")

                if use_market_orders:
                    sp_close = self.client.place_emergency_order(
                        uic=self.position.short_put_uic,
                        asset_type="StockIndexOption",
                        buy_sell=BuySell.BUY,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                else:
                    sp_close = self.client.place_order_with_retry(
                        uic=self.position.short_put_uic,
                        asset_type="StockIndexOption",
                        buy_sell=BuySell.BUY,
                        amount=self.position.quantity,
                        order_type=order_type,
                        to_open_close="ToClose"
                    )

                if sp_close:
                    close_order_ids["short_put"] = sp_close.get("OrderId")
                    close_orders.append(("short_put", sp_close))

            # Sell long call (was bought at entry)
            if self.position.long_call_uic:
                logger.info(f"Closing: SELL {self.position.quantity} Long Call at {self.position.upper_wing}")

                if use_market_orders:
                    lc_close = self.client.place_emergency_order(
                        uic=self.position.long_call_uic,
                        asset_type="StockIndexOption",
                        buy_sell=BuySell.SELL,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                else:
                    lc_close = self.client.place_order_with_retry(
                        uic=self.position.long_call_uic,
                        asset_type="StockIndexOption",
                        buy_sell=BuySell.SELL,
                        amount=self.position.quantity,
                        order_type=order_type,
                        to_open_close="ToClose"
                    )

                if lc_close:
                    close_order_ids["long_call"] = lc_close.get("OrderId")
                    close_orders.append(("long_call", lc_close))

            # Sell long put (was bought at entry)
            if self.position.long_put_uic:
                logger.info(f"Closing: SELL {self.position.quantity} Long Put at {self.position.lower_wing}")

                if use_market_orders:
                    lp_close = self.client.place_emergency_order(
                        uic=self.position.long_put_uic,
                        asset_type="StockIndexOption",
                        buy_sell=BuySell.SELL,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                else:
                    lp_close = self.client.place_order_with_retry(
                        uic=self.position.long_put_uic,
                        asset_type="StockIndexOption",
                        buy_sell=BuySell.SELL,
                        amount=self.position.quantity,
                        order_type=order_type,
                        to_open_close="ToClose"
                    )

                if lp_close:
                    close_order_ids["long_put"] = lp_close.get("OrderId")
                    close_orders.append(("long_put", lp_close))

            logger.info(f"Close orders placed: {len(close_orders)}/4 legs")

        except Exception as e:
            error_msg = f"ERROR CLOSING POSITION: {e}"
            logger.critical(error_msg)
            logger.critical(f"Close orders placed before failure: {[o[0] for o in close_orders]}")

            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_CLOSE_FAILED",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"Failed to close all legs: {len(close_orders)}/4 closed",
                "result": f"MANUAL INTERVENTION REQUIRED - Close orders: {close_order_ids}"
            })

        # Store close order IDs on position for verification in CLOSING state
        self.position.close_order_ids = close_order_ids
        self.position.close_legs_verified = {
            "short_call": False,
            "short_put": False,
            "long_call": False,
            "long_put": False
        }

        # Log the close trade to Google Sheets (convert cents to dollars)
        # FIX (2026-02-01): Calculate both gross and net P&L with commission
        pnl_dollars = pnl / 100  # Gross P&L
        net_pnl_dollars = self._calculate_net_pnl(pnl_dollars)  # Net P&L after commission
        total_commission = self._calculate_total_commission()

        self.trade_logger.log_trade(
            action=f"CLOSE_IRON_FLY_{reason}",
            strike=f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
            price=self.position.credit_received / 100,  # Convert cents to dollars
            delta=0.0,
            pnl=net_pnl_dollars,  # Log NET P&L (after commission)
            saxo_client=self.client,
            underlying_price=self.current_price,
            vix=self.current_vix,
            option_type="Iron Fly",
            expiry_date=self.position.expiry,
            dte=0,
            premium_received=self.position.credit_received / 100,  # Convert cents to dollars
            trade_reason=f"{description} | Commission: ${total_commission:.2f}"
        )

        logger.info(
            f"IRON FLY CLOSE INITIATED: {reason} - Gross P&L=${pnl_dollars:.2f}, "
            f"Commission=${total_commission:.2f}, Net P&L=${net_pnl_dollars:.2f}, "
            f"Hold time={hold_time} min, Close orders={close_order_ids}"
        )

        # Transition to CLOSING state to wait for fill confirmation
        self.state = IronFlyState.CLOSING
        self.closing_started_at = get_eastern_timestamp()

        # ALERT: Send appropriate alert AFTER close orders are placed
        alert_details = {
            "strike": self.position.atm_strike,
            "wings": f"{self.position.lower_wing}/{self.position.upper_wing}",
            "hold_time_minutes": hold_time,
            "spy_price": self.current_price,
            "legs_closed": len(close_orders),
            "gross_pnl": pnl_dollars,
            "net_pnl": net_pnl_dollars,
            "commission": total_commission
        }

        if reason == "PROFIT_TARGET":
            self.alert_service.profit_target(
                target_amount=self._calculate_profit_target(),
                actual_pnl=net_pnl_dollars,  # Use net P&L for alerts
                details=alert_details
            )
        elif reason == "TIME_EXIT":
            self.alert_service.send_alert(
                alert_type=AlertType.TIME_EXIT,
                title="Time Exit - Max Hold Reached",
                message=f"Max hold time {self.max_hold_minutes} min reached.\nGross P&L: ${pnl_dollars:.2f}\nNet P&L: ${net_pnl_dollars:.2f} (after ${total_commission:.2f} commission)",
                priority=AlertPriority.MEDIUM,
                details=alert_details
            )
        elif reason == "MAX_LOSS":
            self.alert_service.send_alert(
                alert_type=AlertType.MAX_LOSS,
                title="Max Loss Triggered",
                message=f"Max loss threshold breached.\nGross P&L: ${pnl_dollars:.2f}\nNet P&L: ${net_pnl_dollars:.2f} (after ${total_commission:.2f} commission)",
                priority=AlertPriority.HIGH,
                details=alert_details
            )

        return f"Closing position ({reason}): Gross P&L=${pnl_dollars:.2f}, Net P&L=${net_pnl_dollars:.2f}, {len(close_orders)}/4 orders placed"

    def _close_position_with_retries(self, reason: str, description: str, pnl: float, hold_time: float) -> str:
        """
        STOP-002: Close position with retry logic for API outages.

        When stop-loss is triggered during API issues, this method:
        1. Attempts to close each leg with STOP_LOSS_MAX_RETRIES attempts
        2. Delays STOP_LOSS_RETRY_DELAY_SECONDS between retries
        3. If all retries fail, sets critical intervention flag
        4. MKT-004: Also logs extreme spread warnings during exit

        Args:
            reason: Exit reason code (STOP_LOSS, MAX_LOSS)
            description: Human-readable description
            pnl: Current unrealized P&L
            hold_time: How long position has been held

        Returns:
            str: Description of action taken
        """
        logger.critical(f"STOP-002: Stop loss close with retries - {reason}: {description}")

        # Cancel any open limit orders (profit taker)
        if self.position.profit_order_id:
            try:
                self.client.cancel_order(self.position.profit_order_id)
            except Exception as e:
                logger.warning(f"Failed to cancel profit order: {e}")

        # Define legs to close
        legs = [
            ("short_call", self.position.short_call_uic, BuySell.BUY, "StockIndexOption"),
            ("short_put", self.position.short_put_uic, BuySell.BUY, "StockIndexOption"),
            ("long_call", self.position.long_call_uic, BuySell.SELL, "StockIndexOption"),
            ("long_put", self.position.long_put_uic, BuySell.SELL, "StockIndexOption"),
        ]

        close_order_ids = {}
        close_orders = []
        failed_legs = []

        for leg_name, uic, buy_sell, asset_type in legs:
            if not uic:
                logger.warning(f"STOP-002: No UIC for {leg_name} - skipping")
                continue

            # MKT-004: Check spread before closing
            self._check_and_log_extreme_spread(uic, leg_name, asset_type)

            # Retry loop for this leg
            success = False
            last_error = None

            for attempt in range(1, STOP_LOSS_MAX_RETRIES + 1):
                try:
                    logger.critical(
                        f"STOP-002: Closing {leg_name} (attempt {attempt}/{STOP_LOSS_MAX_RETRIES})"
                    )
                    result = self.client.place_emergency_order(
                        uic=uic,
                        asset_type=asset_type,
                        buy_sell=buy_sell,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                    if result and result.get("OrderId"):
                        close_order_ids[leg_name] = result.get("OrderId")
                        close_orders.append((leg_name, result))
                        logger.critical(f"✅ STOP-002: {leg_name} closed on attempt {attempt}")
                        success = True
                        break
                    else:
                        last_error = "No OrderId returned"
                        logger.warning(f"STOP-002: {leg_name} attempt {attempt} - no OrderId")
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"STOP-002: {leg_name} attempt {attempt} failed: {e}")

                    # Check for market halt
                    self._check_for_market_halt(str(e))

                # Delay before retry (unless it was the last attempt)
                if attempt < STOP_LOSS_MAX_RETRIES:
                    logger.info(f"STOP-002: Waiting {STOP_LOSS_RETRY_DELAY_SECONDS}s before retry...")
                    time.sleep(STOP_LOSS_RETRY_DELAY_SECONDS)

            if not success:
                failed_legs.append({
                    "leg": leg_name,
                    "uic": uic,
                    "error": last_error,
                    "attempts": STOP_LOSS_MAX_RETRIES
                })
                logger.critical(
                    f"❌ STOP-002: {leg_name} FAILED after {STOP_LOSS_MAX_RETRIES} attempts - {last_error}"
                )

        # Log results
        logger.critical(f"STOP-002: Stop loss result - {len(close_orders)}/4 legs closed, {len(failed_legs)} failed")

        if failed_legs:
            # Set critical intervention - some legs couldn't be closed
            self._set_critical_intervention(
                f"STOP-002: Stop loss failed for {len(failed_legs)} legs after {STOP_LOSS_MAX_RETRIES} retries each"
            )
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_STOP_LOSS_FAILED",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"STOP-002: {len(failed_legs)}/4 legs could not be closed",
                "failed_legs": failed_legs,
                "successful_close_ids": close_order_ids,
                "result": "CRITICAL INTERVENTION REQUIRED"
            })
        else:
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_STOP_LOSS_SUCCESS",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": "STOP-002: All 4 legs closed successfully",
                "close_order_ids": close_order_ids,
                "result": "Stop loss executed"
            })

        # Store close order IDs on position for verification
        self.position.close_order_ids = close_order_ids
        self.position.close_legs_verified = {
            "short_call": False,
            "short_put": False,
            "long_call": False,
            "long_put": False
        }

        # Log the close trade - convert cents to dollars for logging
        # FIX (2026-02-01): Include commission in P&L tracking
        pnl_dollars = pnl / 100  # Gross P&L
        net_pnl_dollars = self._calculate_net_pnl(pnl_dollars)  # Net P&L after commission
        total_commission = self._calculate_total_commission()

        self.trade_logger.log_trade(
            action=f"CLOSE_IRON_FLY_{reason}",
            strike=f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
            price=self.position.credit_received / 100,  # Cents to dollars
            delta=0.0,
            pnl=net_pnl_dollars,  # Log NET P&L (after commission)
            saxo_client=self.client,
            underlying_price=self.current_price,
            vix=self.current_vix,
            option_type="Iron Fly",
            expiry_date=self.position.expiry,
            dte=0,
            premium_received=self.position.credit_received / 100,  # Cents to dollars
            trade_reason=f"STOP-002: {description} (retries used) | Commission: ${total_commission:.2f}"
        )

        self.state = IronFlyState.CLOSING
        self.closing_started_at = get_eastern_timestamp()

        # ALERT: Send stop loss alert AFTER close is complete with actual results
        if failed_legs:
            # Critical alert if legs failed to close
            self.alert_service.send_alert(
                alert_type=AlertType.STOP_LOSS,
                title="STOP LOSS PARTIAL FAILURE",
                message=f"{description}\n{len(close_orders)}/4 legs closed, {len(failed_legs)} FAILED\nGross P&L: ${pnl_dollars:.2f}\nNet P&L: ${net_pnl_dollars:.2f} (after ${total_commission:.2f} commission)\nCritical intervention required!",
                priority=AlertPriority.CRITICAL,
                details={
                    "spy_price": self.current_price,
                    "reason": reason,
                    "legs_closed": len(close_orders),
                    "legs_failed": len(failed_legs),
                    "gross_pnl": pnl_dollars,
                    "net_pnl": net_pnl_dollars,
                    "commission": total_commission,
                    "hold_time_minutes": hold_time
                }
            )
            return f"STOP-002 PARTIAL: {len(close_orders)}/4 closed, {len(failed_legs)} FAILED - CRITICAL INTERVENTION SET"

        # Successful stop loss
        self.alert_service.stop_loss(
            trigger_price=self.current_price,
            pnl=net_pnl_dollars,  # Use net P&L
            details={
                "strike": self.position.atm_strike,
                "wings": f"{self.position.lower_wing}/{self.position.upper_wing}",
                "hold_time_minutes": hold_time,
                "reason": description,
                "gross_pnl": pnl_dollars,
                "net_pnl": net_pnl_dollars,
                "commission": total_commission
            }
        )
        return f"STOP-002: Stop loss executed ({reason}): Gross P&L=${pnl_dollars:.2f}, Net P&L=${net_pnl_dollars:.2f}, all legs closed with retries"

    def _check_and_log_extreme_spread(self, uic: int, leg_name: str, asset_type: str) -> None:
        """
        MKT-004: Check and log extreme bid-ask spreads during position exit.

        Called before each close order to warn operator about slippage risk.

        Args:
            uic: The instrument UIC
            leg_name: Human-readable leg name for logging
            asset_type: Asset type for quote lookup
        """
        try:
            quote = self.client.get_quote(uic, asset_type=asset_type, skip_cache=True)
            if quote:
                bid = quote.get('Quote', {}).get('Bid', 0)
                ask = quote.get('Quote', {}).get('Ask', 0)

                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2
                    spread = ask - bid
                    spread_percent = (spread / mid) * 100 if mid > 0 else 0

                    if spread_percent >= EXTREME_SPREAD_CRITICAL_PERCENT:
                        logger.critical(
                            f"🚨 MKT-004: EXTREME SPREAD on {leg_name}! "
                            f"Bid={bid:.2f}, Ask={ask:.2f}, Spread={spread_percent:.1f}%"
                        )
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_EXTREME_SPREAD_CRITICAL",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "description": f"MKT-004: {leg_name} spread {spread_percent:.1f}% (CRITICAL)",
                            "bid": bid,
                            "ask": ask,
                            "spread_percent": spread_percent,
                            "result": "Proceeding with close anyway (must exit)"
                        })
                    elif spread_percent >= EXTREME_SPREAD_WARNING_PERCENT:
                        logger.warning(
                            f"⚠️ MKT-004: Wide spread on {leg_name} - "
                            f"Bid={bid:.2f}, Ask={ask:.2f}, Spread={spread_percent:.1f}%"
                        )
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_EXTREME_SPREAD_WARNING",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "description": f"MKT-004: {leg_name} spread {spread_percent:.1f}% (warning)",
                            "bid": bid,
                            "ask": ask,
                            "spread_percent": spread_percent,
                            "result": "Proceeding with close"
                        })
        except Exception as e:
            logger.warning(f"MKT-004: Could not check spread for {leg_name}: {e}")

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def update_market_data(self):
        """
        Update current price and VIX from market data.

        CRITICAL FIX: Always use skip_cache=True for underlying price to ensure
        we get fresh REST API data. The streaming cache for CfdOnIndex instruments
        (like US500.I) doesn't receive delta updates, so the cache can become stale
        while still appearing "valid" (has Quote.Mid > 0).

        Also updates MarketData staleness tracking to detect when
        data becomes stale and stop-loss protection may be compromised.
        """
        try:
            # Get underlying price (SPX is StockIndex, US500.I is CfdOnIndex)
            if self.underlying_uic:
                # Determine asset type based on symbol
                if "SPX" in self.underlying_symbol:
                    asset_type = "StockIndex"
                elif "US500" in self.underlying_symbol or self.underlying_symbol.endswith(".I"):
                    asset_type = "CfdOnIndex"
                else:
                    asset_type = "Etf"
                # CRITICAL: Use skip_cache=True to bypass stale streaming cache
                # CfdOnIndex WebSocket subscriptions don't receive delta updates reliably
                quote = self.client.get_quote(self.underlying_uic, asset_type=asset_type, skip_cache=True)
                if quote:
                    new_price = quote.get('Quote', {}).get('Mid')
                    if new_price and new_price > 0:
                        self.current_price = new_price
                        self.market_data.update_price(new_price)  # Track staleness
                        self._record_price_for_velocity(new_price)  # MKT-001: Track for flash crash detection

            # Get VIX using get_vix_price() which has Yahoo Finance fallback
            # Saxo may return "NoAccess" for VIX data, so fallback is important
            if self.vix_uic:
                vix_price = self.client.get_vix_price(self.vix_uic)
                if vix_price and vix_price > 0:
                    self.current_vix = vix_price
                    self.market_data.update_vix(vix_price)  # Track staleness
                    logger.debug(f"VIX updated to {vix_price:.2f}")
        except Exception as e:
            logger.warning(f"Error updating market data: {e}")
            # Don't update staleness tracking on error - data remains stale

    def _update_position_prices(self):
        """
        Update current prices for all position legs.

        In dry-run mode, simulates realistic P&L based on:
        - Time decay (theta): position value decays over time
        - Price movement: P&L affected by distance from ATM strike
        - Wing proximity: loss accelerates as price approaches wings
        """
        if not self.position:
            return

        # In dry run mode, simulate REALISTIC price movement
        if self.dry_run:
            # Get time in position (in minutes)
            hold_minutes = self.position.hold_time_minutes

            # Calculate price distance from ATM (normalized by wing width)
            price_distance = abs(self.current_price - self.position.atm_strike)
            wing_width = self.position.wing_width
            distance_ratio = price_distance / wing_width if wing_width > 0 else 0

            # Theta decay: Iron fly theta is highest when ATM, decays ~5-10% of credit per 10 min
            # Average hold time is 18 minutes per Doc Severson
            theta_decay_per_minute = 0.005  # 0.5% per minute when at ATM
            theta_decay = theta_decay_per_minute * hold_minutes

            # Reduce theta benefit when price moves away from ATM
            # (theta decreases as position moves ITM)
            theta_adjustment = max(0.2, 1.0 - distance_ratio)  # Min 20% theta when near wing
            effective_theta_decay = theta_decay * theta_adjustment

            # Price movement impact: Loss increases as price approaches wings
            # When distance_ratio >= 1.0, price has breached wing (stop loss)
            if distance_ratio >= 0.8:
                # Near wing: P&L becomes negative rapidly
                price_impact = -0.5 * (distance_ratio - 0.5)  # -25% to -50% loss near wing
            elif distance_ratio >= 0.5:
                # Half way to wing: small negative impact
                price_impact = -0.1 * (distance_ratio - 0.3)  # slight negative
            else:
                # Near ATM: theta works in our favor
                price_impact = 0.0

            # Calculate new simulated cost-to-close
            # At entry, cost-to-close = credit received (no profit yet)
            # As time passes with price near ATM, cost-to-close decreases (profit increases)
            # As price moves to wing, cost-to-close increases (loss increases)
            credit = self.position.credit_received
            decay_benefit = credit * effective_theta_decay  # Reduces cost to close
            price_penalty = credit * max(0, price_impact)  # Increases cost to close

            # New cost to close (what we'd pay to exit now)
            new_cost_to_close = credit - decay_benefit + price_penalty

            # Ensure cost-to-close doesn't go below 0 (max profit is full credit)
            # and doesn't exceed max loss (wing_width * 100 - credit)
            max_loss_value = (wing_width * self.position.quantity * 100)
            new_cost_to_close = max(0, min(new_cost_to_close, max_loss_value))

            # Update simulated current value
            self.position.simulated_current_value = new_cost_to_close

            logger.debug(
                f"[DRY RUN] P&L Simulation: Hold={hold_minutes}m, DistRatio={distance_ratio:.2f}, "
                f"Theta={effective_theta_decay:.3f}, PriceImpact={price_impact:.3f}, "
                f"CostToClose=${new_cost_to_close / 100:.2f}, P&L=${self.position.unrealized_pnl / 100:.2f}"
            )
            return

        # =================================================================
        # LIVE: Get current prices for each leg via polling
        # =================================================================
        # Note: If streaming is working, prices are updated via handle_price_update
        # This polling serves as a fallback when streaming is unavailable

        try:
            prices_updated = 0

            # Get short call price (we need to BUY to close, so use Ask)
            # LIVE-001: Use StockIndexOption for SPX/SPXW index options
            # FIX (2026-01-26): Now using WebSocket cache (binary parsing fixed).
            # Options are subscribed after entry, cache receives live updates.
            # This reduces REST API calls from 48/min to ~0 during position monitoring.
            if self.position.short_call_uic:
                sc_quote = self.client.get_quote(self.position.short_call_uic, "StockIndexOption")
                if sc_quote:
                    ask = sc_quote.get('Quote', {}).get('Ask', 0)
                    if ask > 0:
                        self.position.short_call_price = ask
                        prices_updated += 1

            # Get short put price (we need to BUY to close, so use Ask)
            if self.position.short_put_uic:
                sp_quote = self.client.get_quote(self.position.short_put_uic, "StockIndexOption")
                if sp_quote:
                    ask = sp_quote.get('Quote', {}).get('Ask', 0)
                    if ask > 0:
                        self.position.short_put_price = ask
                        prices_updated += 1

            # Get long call price (we need to SELL to close, so use Bid)
            if self.position.long_call_uic:
                lc_quote = self.client.get_quote(self.position.long_call_uic, "StockIndexOption")
                if lc_quote:
                    bid = lc_quote.get('Quote', {}).get('Bid', 0)
                    if bid > 0:
                        self.position.long_call_price = bid
                        prices_updated += 1

            # Get long put price (we need to SELL to close, so use Bid)
            if self.position.long_put_uic:
                lp_quote = self.client.get_quote(self.position.long_put_uic, "StockIndexOption")
                if lp_quote:
                    bid = lp_quote.get('Quote', {}).get('Bid', 0)
                    if bid > 0:
                        self.position.long_put_price = bid
                        prices_updated += 1

            if prices_updated > 0:
                logger.info(
                    f"Option prices updated ({prices_updated}/4): "
                    f"SC={self.position.short_call_price:.2f}, SP={self.position.short_put_price:.2f}, "
                    f"LC={self.position.long_call_price:.2f}, LP={self.position.long_put_price:.2f}, "
                    f"P&L=${self.position.unrealized_pnl / 100:.2f}"
                )

        except Exception as e:
            logger.warning(f"Error polling option prices: {e}")

    def _round_up_to_strike(self, price: float, increment: float = 5.0) -> float:
        """
        Round price UP to next strike increment.

        Per Doc Severson's bias rule: use the first strike ABOVE current price
        to compensate for put skew.
        """
        return math.ceil(price / increment) * increment

    def _round_to_strike(self, price: float, increment: float = 5.0) -> float:
        """Round price to nearest strike increment."""
        return round(price / increment) * increment

    def _calculate_expected_move(self) -> float:
        """
        Calculate wing width for Iron Fly based on expected move and Jim Olson's rules.

        Strategy (combining Doc Severson + Jim Olson):
        1. Get expected move from ATM 0DTE straddle price (Doc Severson)
        2. Enforce minimum wing width of 40 points (Jim Olson: use 50pt if EM < 30)
        3. Target credit should be ~30% of wing width (Jim Olson's rule of thumb)

        Jim Olson's key insight: "If the Implied Move is under $30, I will simply
        use $50 wings." This ensures adequate credit collection even on low-vol days.

        Returns:
            float: Wing width in points (rounded to strike increment)
        """
        # Check for manual override (calibration mode)
        if self.manual_expected_move:
            logger.info(f"Using manual expected move: {self.manual_expected_move} points")
            return self._round_to_strike(self.manual_expected_move)

        # Get expected move from 0DTE ATM straddle price
        raw_expected_move = self.client.get_expected_move_from_straddle(
            self.underlying_uic,
            self.current_price,
            target_dte_min=0,
            target_dte_max=1,  # 0DTE
            option_root_uic=self.options_uic,  # SPXW UIC 128 for StockIndexOptions
            option_asset_type="StockIndexOption"
        )

        if not raw_expected_move:
            # Fallback to VIX-based calculation if straddle pricing fails
            logger.warning("Could not get straddle price, falling back to VIX calculation")
            daily_vol = self.current_vix / math.sqrt(252)
            raw_expected_move = self.current_price * (daily_vol / 100)

        # Round to nearest strike increment
        expected_move = self._round_to_strike(raw_expected_move)

        # Jim Olson Rule: Enforce minimum wing width
        # "If the Implied Move is under $30, I will simply use $50 wings"
        # We use configurable minimum (default 40 points) for safety
        if expected_move < self.min_wing_width:
            logger.info(
                f"Expected move ${expected_move:.0f} < min wing width ${self.min_wing_width}. "
                f"Using minimum wing width of {self.min_wing_width} points (Jim Olson rule)."
            )
            wing_width = float(self.min_wing_width)
        else:
            wing_width = expected_move

        # Log the calculation details
        logger.info(
            f"Wing width calculation: Raw EM=${raw_expected_move:.2f}, "
            f"Rounded=${expected_move:.0f}, Min=${self.min_wing_width}, "
            f"Final wing width={wing_width:.0f} points"
        )

        return wing_width

    def _log_filter_event(self, event_type: str, description: str):
        """Log a filter event to the Safety Events sheet."""
        logger.info(f"Filter event: {event_type} - {description}")

        # Log to Safety Events worksheet
        self.trade_logger.log_safety_event({
            "event_type": f"IRON_FLY_{event_type}",
            "spy_price": self.current_price,
            "vix": self.current_vix,
            "description": description,
            "result": "Entry Blocked"
        })

    def _log_opening_range_to_sheets(
        self,
        entry_decision: str,
        reason: str,
        atm_strike: float = None,
        wing_width: float = None
    ):
        """
        Log opening range data to Google Sheets for fact-checking.

        Called when opening range period completes (10:00 AM EST) to record
        all the metrics used for the entry decision.

        Args:
            entry_decision: "ENTER" or "SKIP"
            reason: Human-readable reason for the decision
            atm_strike: Selected ATM strike (if entering)
            wing_width: Wing width / expected move (if entering)
        """
        current_time = get_us_market_time()

        # Build opening range data dict
        # Calculate price position percentage (0%=low, 50%=mid, 100%=high, <0 or >100 = outside range)
        price_position_pct = self.opening_range.price_position_percent(self.current_price)

        data = {
            "date": current_time.strftime("%Y-%m-%d"),
            "start_time": self.opening_range.start_time.strftime("%H:%M:%S") if self.opening_range.start_time else "",
            "end_time": current_time.strftime("%H:%M:%S"),
            "opening_price": self.opening_range.low if self.opening_range.low != float('inf') else 0,  # First price captured
            "range_high": self.opening_range.high,
            "range_low": self.opening_range.low if self.opening_range.low != float('inf') else 0,
            "range_width": self.opening_range.range_width,
            "current_price": self.current_price,
            "price_in_range": self.opening_range.is_price_in_range(self.current_price),
            "price_position_pct": price_position_pct,
            "opening_vix": self.opening_range.opening_vix,
            "vix_high": self.opening_range.vix_high,
            "current_vix": self.current_vix,
            "vix_spike_percent": self.opening_range.vix_spike_percent,
            "expected_move": wing_width if wing_width else self._calculate_expected_move(),
            "entry_decision": entry_decision,
            "reason": reason,
            "atm_strike": atm_strike,
            "wing_width": wing_width
        }

        # Log to Google Sheets via trade logger
        try:
            self.trade_logger.log_opening_range(data)
        except Exception as e:
            logger.warning(f"Failed to log opening range to sheets: {e}")

    def log_opening_range_snapshot(self):
        """
        Log real-time opening range snapshot to Google Sheets during monitoring.

        Called every 15 seconds during the 9:30-10:00 AM monitoring period.
        Updates a single row for today's date (upsert) rather than appending.
        Shows "MONITORING" as entry_decision until final decision at 10:00 AM.
        """
        if self.state != IronFlyState.WAITING_OPENING_RANGE:
            return

        current_time = get_us_market_time()

        # Build snapshot data
        # Calculate price position percentage (0%=low, 50%=mid, 100%=high, <0 or >100 = outside range)
        price_position_pct = self.opening_range.price_position_percent(self.current_price)

        data = {
            "date": current_time.strftime("%Y-%m-%d"),
            "start_time": self.opening_range.start_time.strftime("%H:%M:%S") if self.opening_range.start_time else "",
            "end_time": current_time.strftime("%H:%M:%S"),
            "opening_price": self.opening_range.low if self.opening_range.low != float('inf') else 0,
            "range_high": self.opening_range.high if self.opening_range.high > 0 else 0,
            "range_low": self.opening_range.low if self.opening_range.low != float('inf') else 0,
            "range_width": self.opening_range.range_width,
            "current_price": self.current_price,
            "price_in_range": self.opening_range.is_price_in_range(self.current_price) if self.opening_range.high > 0 else True,
            "price_position_pct": price_position_pct,
            "opening_vix": self.opening_range.opening_vix,
            "vix_high": self.opening_range.vix_high,
            "current_vix": self.current_vix,
            "vix_spike_percent": self.opening_range.vix_spike_percent,
            "expected_move": self._calculate_expected_move(),
            "entry_decision": "MONITORING",
            "reason": f"Monitoring range ({current_time.strftime('%H:%M:%S')})",
            "atm_strike": None,
            "wing_width": None
        }

        # Update (upsert) the row for today
        try:
            self.trade_logger.update_opening_range(data)
        except Exception as e:
            logger.warning(f"Failed to update opening range snapshot: {e}")

    # =========================================================================
    # PRE-TRADE FILTERS (FOMC / Economic Calendar)
    # =========================================================================

    def check_fed_meeting_filter(self) -> Tuple[bool, str]:
        """
        Check if today is ANY day of an FOMC meeting (day 1 or day 2).

        Per Doc Severson: "NEVER trade on FOMC or major economic data days"
        The market will likely trend and blow past stops on Fed days.

        IMPORTANT: Block BOTH days of FOMC meetings, not just the announcement day.
        Day 1 has anticipation volatility, Day 2 has announcement volatility.

        Uses shared/event_calendar.py as single source of truth for FOMC dates.

        Returns:
            Tuple[bool, str]: (True if safe to trade, reason if blocked)
        """
        if not self.fed_meeting_blackout:
            return (True, "")

        # FILTER-002: Use shared event calendar for FOMC dates
        # Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
        from shared.event_calendar import is_fomc_meeting_day, get_fomc_dates

        today = get_us_market_time().date()

        # Check if calendar has dates for current year
        fomc_dates = get_fomc_dates(today.year)
        if not fomc_dates:
            logger.warning(
                f"FILTER-002: FOMC calendar missing for {today.year}! "
                f"Update FOMC_DATES_{today.year} in shared/event_calendar.py"
            )
            # Conservative: allow trading but log warning
            return (True, "")

        if is_fomc_meeting_day(today):
            reason = f"FOMC meeting day ({today.strftime('%b %d')})"
            logger.warning(f"FOMC BLACKOUT: {reason} - Entry blocked")
            return (False, reason)

        return (True, "")

    def check_economic_calendar_filter(self) -> Tuple[bool, str]:
        """
        Check if today has major economic releases that could cause trending.

        Per Doc Severson: "Never trade this on days with major economic data
        (CPI, PPI, Jobs Report) as the market will likely trend and blow past stops."

        Major releases typically at 8:30 AM EST - before our 10:00 AM entry.

        Returns:
            Tuple[bool, str]: (True if safe to trade, reason if blocked)
        """
        if not self.economic_calendar_check:
            return (True, "")

        # FILTER-003: Multi-year Major Economic Release Dates
        # CPI (Consumer Price Index) - typically 2nd week of month
        # PPI (Producer Price Index) - typically day after CPI
        # Jobs Report (NFP) - first Friday of month
        # These are high-impact events that cause market trending

        # Note: Dates are approximate - update from BLS calendar:
        # https://www.bls.gov/schedule/news_release/cpi.htm
        # https://www.bls.gov/schedule/news_release/empsit.htm
        # IMPORTANT: Update this dictionary annually (typically Dec for next year)

        economic_dates_by_year = {
            2026: {
                # Jobs Reports (Non-Farm Payrolls) - First Friday each month
                date(2026, 1, 3): "Jobs Report (NFP)",
                date(2026, 2, 6): "Jobs Report (NFP)",
                date(2026, 3, 6): "Jobs Report (NFP)",
                date(2026, 4, 3): "Jobs Report (NFP)",
                date(2026, 5, 1): "Jobs Report (NFP)",
                date(2026, 6, 5): "Jobs Report (NFP)",
                date(2026, 7, 3): "Jobs Report (NFP)",
                date(2026, 8, 7): "Jobs Report (NFP)",
                date(2026, 9, 4): "Jobs Report (NFP)",
                date(2026, 10, 2): "Jobs Report (NFP)",
                date(2026, 11, 6): "Jobs Report (NFP)",
                date(2026, 12, 4): "Jobs Report (NFP)",

                # CPI Releases (Consumer Price Index) - ~2nd week each month
                date(2026, 1, 14): "CPI Release",
                date(2026, 2, 11): "CPI Release",
                date(2026, 3, 11): "CPI Release",
                date(2026, 4, 14): "CPI Release",
                date(2026, 5, 13): "CPI Release",
                date(2026, 6, 10): "CPI Release",
                date(2026, 7, 15): "CPI Release",
                date(2026, 8, 12): "CPI Release",
                date(2026, 9, 16): "CPI Release",
                date(2026, 10, 14): "CPI Release",
                date(2026, 11, 12): "CPI Release",
                date(2026, 12, 9): "CPI Release",

                # PPI Releases (Producer Price Index) - typically day after CPI
                date(2026, 1, 15): "PPI Release",
                date(2026, 2, 12): "PPI Release",
                date(2026, 3, 12): "PPI Release",
                date(2026, 4, 15): "PPI Release",
                date(2026, 5, 14): "PPI Release",
                date(2026, 6, 11): "PPI Release",
                date(2026, 7, 16): "PPI Release",
                date(2026, 8, 13): "PPI Release",
                date(2026, 9, 17): "PPI Release",
                date(2026, 10, 15): "PPI Release",
                date(2026, 11, 13): "PPI Release",
                date(2026, 12, 10): "PPI Release",
            },
            # 2027: {  # TODO: Add 2027 dates when BLS releases calendar (typically Dec 2026)
            #     # Jobs Reports - First Friday each month
            #     # CPI Releases - ~2nd week each month
            #     # PPI Releases - typically day after CPI
            # },
        }

        today = get_us_market_time().date()
        current_year = today.year

        # FILTER-003: Check if current year is in calendar
        if current_year not in economic_dates_by_year:
            logger.warning(
                f"FILTER-003: Economic calendar missing for {current_year}! "
                f"Update economic_dates_by_year in strategy.py. Available years: {list(economic_dates_by_year.keys())}"
            )
            # Conservative: allow trading but log warning (could also block here)
            return (True, "")

        economic_dates = economic_dates_by_year[current_year]
        if today in economic_dates:
            event = economic_dates[today]
            reason = f"Major economic event: {event}"
            logger.warning(f"ECONOMIC CALENDAR BLACKOUT: {reason} - Entry blocked")
            return (False, reason)

        return (True, "")

    # =========================================================================
    # TIME-003: EARLY CLOSE DAY DETECTION
    # =========================================================================

    def is_early_close_day(self) -> bool:
        """
        TIME-003: Check if today is an early close day (1:00 PM ET instead of 4:00 PM).

        Early close days include:
        - Day before Independence Day (July 3rd usually)
        - Day after Thanksgiving (Black Friday)
        - Christmas Eve
        - New Year's Eve

        Returns:
            bool: True if today is an early close day
        """
        today = get_us_market_time().date()
        current_year = today.year

        # Check multi-year calendar
        if current_year not in EARLY_CLOSE_DATES:
            logger.warning(
                f"TIME-003: Early close dates missing for {current_year}! "
                f"Update EARLY_CLOSE_DATES in strategy.py. Available years: {list(EARLY_CLOSE_DATES.keys())}"
            )
            return False  # Conservative: assume normal close if year missing

        return today in EARLY_CLOSE_DATES[current_year]

    def get_market_close_time_today(self) -> dt_time:
        """
        TIME-003: Get today's market close time.

        Returns:
            time: 1:00 PM on early close days, 4:00 PM otherwise
        """
        if self.is_early_close_day():
            return EARLY_CLOSE_TIME  # 1:00 PM
        return dt_time(16, 0)  # 4:00 PM

    def is_past_early_close_cutoff(self) -> Tuple[bool, str]:
        """
        TIME-003: Check if we're past the trading cutoff for early close days.

        On early close days (1:00 PM close), we stop all operations
        at 12:45 PM (15 minutes before close).

        Returns:
            Tuple of (is_past_cutoff: bool, reason: str)
        """
        if not self.is_early_close_day():
            return (False, "")

        current_time = get_us_market_time()
        cutoff_time = dt_time(12, 45)  # 12:45 PM - 15 min before 1PM close

        if current_time.time() >= cutoff_time:
            reason = f"Early close day - past {cutoff_time.strftime('%I:%M %p')} cutoff (market closes at 1:00 PM)"
            return (True, reason)

        return (False, "")

    def check_early_close_warning(self) -> None:
        """
        TIME-003: Log a warning at market open on early close days.

        Called once at the start of trading to alert operator.
        """
        if self.is_early_close_day():
            close_time = self.get_market_close_time_today()
            logger.warning(
                f"TIME-003: TODAY IS AN EARLY CLOSE DAY! "
                f"Market closes at {close_time.strftime('%I:%M %p')} ET. "
                f"Trading cutoff at 12:45 PM."
            )
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_EARLY_CLOSE_DAY",
                "close_time": close_time.strftime('%I:%M %p'),
                "cutoff_time": "12:45 PM",
                "description": "Early close day detected - reduced trading window",
                "result": "Operations will stop at 12:45 PM"
            })

    # =========================================================================
    # MKT-001: FLASH CRASH VELOCITY DETECTION
    # =========================================================================

    def _record_price_for_velocity(self, price: float) -> None:
        """
        MKT-001: Record price point for flash crash velocity tracking.

        Maintains a rolling window of prices over the last FLASH_CRASH_WINDOW_MINUTES.
        """
        if price <= 0:
            return

        now = get_eastern_timestamp()
        self._price_history.append((now, price))

        # Prune old entries beyond the window
        cutoff = now - timedelta(minutes=FLASH_CRASH_WINDOW_MINUTES)
        self._price_history = [(t, p) for t, p in self._price_history if t >= cutoff]

    def detect_flash_crash(self) -> Tuple[bool, str, float]:
        """
        MKT-001: Detect if a flash crash is occurring based on price velocity.

        A flash crash is defined as price moving >= FLASH_CRASH_THRESHOLD_PERCENT
        within FLASH_CRASH_WINDOW_MINUTES.

        Returns:
            Tuple[bool, str, float]: (is_flash_crash, description, percent_move)
        """
        if len(self._price_history) < 2:
            return (False, "", 0.0)

        # Get oldest and newest prices in window
        oldest_time, oldest_price = self._price_history[0]
        newest_time, newest_price = self._price_history[-1]

        if oldest_price <= 0:
            return (False, "", 0.0)

        # Calculate percent move
        percent_move = ((newest_price - oldest_price) / oldest_price) * 100.0

        if abs(percent_move) >= FLASH_CRASH_THRESHOLD_PERCENT:
            direction = "DOWN" if percent_move < 0 else "UP"
            window_seconds = (newest_time - oldest_time).total_seconds()
            description = (
                f"FLASH CRASH {direction}: {abs(percent_move):.2f}% move in "
                f"{window_seconds:.0f} seconds ({oldest_price:.2f} -> {newest_price:.2f})"
            )
            return (True, description, percent_move)

        return (False, "", percent_move)

    def check_flash_crash_and_close(self) -> Optional[str]:
        """
        MKT-001: Check for flash crash and trigger emergency close if detected.

        This should be called during position monitoring. If a flash crash is
        detected while we have an open position, we immediately close to
        prevent catastrophic losses.

        Returns:
            Optional[str]: Emergency close result if triggered, None otherwise
        """
        if not self.position or self.state not in [IronFlyState.POSITION_OPEN, IronFlyState.MONITORING_EXIT]:
            return None

        is_crash, description, percent_move = self.detect_flash_crash()

        if is_crash:
            logger.critical(f"MKT-001: {description} - TRIGGERING EMERGENCY CLOSE")

            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_FLASH_CRASH_DETECTED",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "percent_move": f"{percent_move:.2f}%",
                "window_minutes": FLASH_CRASH_WINDOW_MINUTES,
                "threshold_percent": FLASH_CRASH_THRESHOLD_PERCENT,
                "description": description,
                "result": "EMERGENCY CLOSE TRIGGERED"
            })

            return self._emergency_close_position(reason=f"MKT-001: {description}")

        return None

    # =========================================================================
    # STATUS AND MONITORING
    # =========================================================================

    def get_status_summary(self) -> Dict[str, Any]:
        """
        Get current strategy status for logging/display.

        Note: All monetary values are returned in DOLLARS (converted from internal cents storage).
        This makes the API consistent for all consumers.
        """
        summary = {
            "state": self.state.value,
            "underlying_price": self.current_price,
            "vix": self.current_vix,
            "opening_range_high": self.opening_range.high if self.opening_range.high > 0 else None,
            "opening_range_low": self.opening_range.low if self.opening_range.low < float('inf') else None,
            "opening_range_width": self.opening_range.range_width,
            "opening_range_complete": self.opening_range.is_complete,
            "price_in_range": self.opening_range.is_price_in_range(self.current_price) if self.opening_range.high > 0 else True,
            "price_position_pct": self.opening_range.price_position_percent(self.current_price),
            "vix_spike_percent": self.opening_range.vix_spike_percent,
            "trades_today": self.trades_today,
            "daily_pnl": self.daily_pnl / 100,  # Convert cents to dollars
        }

        if self.position:
            distance, wing = self.position.distance_to_wing(self.current_price)
            summary.update({
                "position_active": True,
                "atm_strike": self.position.atm_strike,
                "upper_wing": self.position.upper_wing,
                "lower_wing": self.position.lower_wing,
                "credit_received": self.position.credit_received / 100,  # Convert cents to dollars
                "unrealized_pnl": self.position.unrealized_pnl / 100,  # Convert cents to dollars
                "hold_time_minutes": self.position.hold_time_minutes,
                "distance_to_wing": distance,
                "nearest_wing": wing,
            })
        else:
            summary["position_active"] = False

        return summary

    def handle_price_update(self, uic: int, data: Dict[str, Any]):
        """
        Handle real-time price updates from WebSocket streaming.

        NOTE: This method is currently unused since REST-only mode was enabled.
        WebSocket streaming was disabled because the strategy already uses
        skip_cache=True for all price fetches in update_market_data().
        Kept for potential future use if WebSocket is re-enabled.

        Updates underlying price, VIX, and option leg prices for P&L calculation.
        """
        # Update underlying price
        if uic == self.underlying_uic:
            mid = data.get('Quote', {}).get('Mid')
            if mid and mid > 0:
                self.current_price = mid
                self.market_data.update_price(mid)  # Track staleness
                self._record_price_for_velocity(mid)  # MKT-001: Track for flash crash detection
            return

        # Update VIX
        if uic == self.vix_uic:
            mid = data.get('Quote', {}).get('Mid')
            if mid and mid > 0:
                self.current_vix = mid
                self.market_data.update_vix(mid)  # Track staleness
            return

        # Handle option price updates for position legs
        if self.position:
            quote = data.get('Quote', {})

            # Short call (we need to BUY to close, so use Ask)
            if uic == self.position.short_call_uic:
                ask = quote.get('Ask', 0)
                if ask > 0:
                    self.position.short_call_price = ask
                return

            # Short put (we need to BUY to close, so use Ask)
            if uic == self.position.short_put_uic:
                ask = quote.get('Ask', 0)
                if ask > 0:
                    self.position.short_put_price = ask
                return

            # Long call (we need to SELL to close, so use Bid)
            if uic == self.position.long_call_uic:
                bid = quote.get('Bid', 0)
                if bid > 0:
                    self.position.long_call_price = bid
                return

            # Long put (we need to SELL to close, so use Bid)
            if uic == self.position.long_put_uic:
                bid = quote.get('Bid', 0)
                if bid > 0:
                    self.position.long_put_price = bid
                return

    def log_daily_summary(self):
        """
        Log daily summary to Google Sheets at end of trading day.

        Uses daily_premium_collected which is tracked at entry time,
        not position.credit_received which may be None after close.
        Also updates and persists cumulative metrics across days.
        """
        # Get EUR conversion rate
        daily_pnl_eur = self.daily_pnl
        try:
            rate = self.client.get_usd_to_account_currency_rate()
            if rate:
                daily_pnl_eur = self.daily_pnl * rate
        except Exception:
            pass  # Keep USD value if conversion fails

        # Update cumulative metrics
        self.cumulative_metrics["cumulative_pnl"] += self.daily_pnl
        self.cumulative_metrics["total_trades"] += self.trades_today
        self.cumulative_metrics["total_premium_collected"] += self.daily_premium_collected

        # Track win/loss
        if self.trades_today > 0:
            if self.daily_pnl > 0:
                self.cumulative_metrics["winning_trades"] += 1
            elif self.daily_pnl < 0:
                self.cumulative_metrics["losing_trades"] += 1

            # Track best/worst trade
            if self.daily_pnl > self.cumulative_metrics.get("best_trade", 0):
                self.cumulative_metrics["best_trade"] = self.daily_pnl
            if self.daily_pnl < self.cumulative_metrics.get("worst_trade", 0):
                self.cumulative_metrics["worst_trade"] = self.daily_pnl

        # Save cumulative metrics to file
        save_cumulative_metrics(self.cumulative_metrics)

        # Calculate win rate for summary
        total_completed_trades = (
            self.cumulative_metrics.get("winning_trades", 0) +
            self.cumulative_metrics.get("losing_trades", 0)
        )
        win_rate = 0.0
        if total_completed_trades > 0:
            win_rate = (self.cumulative_metrics.get("winning_trades", 0) / total_completed_trades) * 100

        # Iron Fly specific summary - matches the iron_fly Daily Summary columns
        # All monetary values stored in CENTS internally, convert to DOLLARS for display
        summary = {
            "date": get_us_market_time().strftime("%Y-%m-%d"),
            "underlying_close": self.current_price,  # Generic name for SPX/SPY
            "vix": self.current_vix,
            "premium_collected": self.daily_premium_collected / 100,  # Convert cents to dollars
            "trades_today": self.trades_today,
            "win_rate": win_rate,
            "daily_pnl": self.daily_pnl / 100,  # Convert cents to dollars
            "daily_pnl_eur": daily_pnl_eur / 100,  # Convert cents to EUR
            "cumulative_pnl": self.cumulative_metrics["cumulative_pnl"] / 100,  # Convert cents to dollars
            "total_trades": self.cumulative_metrics["total_trades"],
            "winning_trades": self.cumulative_metrics.get("winning_trades", 0),
            "notes": f"Iron Fly 0DTE - State: {self.state.value}"
        }
        self.trade_logger.log_daily_summary(summary)
        logger.info(
            f"Daily summary logged: P&L=${self.daily_pnl / 100:.2f}, Premium=${self.daily_premium_collected / 100:.2f}, "
            f"Trades={self.trades_today}, Cumulative P&L=${self.cumulative_metrics['cumulative_pnl'] / 100:.2f}"
        )

        # Send Telegram/Email daily summary alert
        summary_for_alert = summary.copy()
        summary_for_alert["dry_run"] = self.dry_run
        self.alert_service.daily_summary_iron_fly(summary_for_alert)
        logger.info("Daily summary alert sent to Telegram/Email")

    def log_position_to_sheets(self):
        """Log current position to Positions worksheet (iron fly format)."""
        if not self.position:
            return

        # Calculate distance to nearest wing
        distance_to_wing = min(
            abs(self.current_price - self.position.upper_wing),
            abs(self.current_price - self.position.lower_wing)
        ) if self.current_price else 0

        positions = [{
            "type": "Iron Fly",
            "strike": f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
            "expiry": self.position.expiry,
            "dte": 0,
            "entry_credit": self.position.credit_received / 100,
            "current_value": self.position.current_value / 100,  # Cost to close from real-time option prices
            "pnl": self.position.unrealized_pnl / 100,  # Convert cents to dollars
            "hold_time": self.position.hold_time_minutes,
            "distance_to_wing": distance_to_wing,
            "status": "OPEN"
        }]
        self.trade_logger.log_position_snapshot(positions)

    def log_performance_metrics(self):
        """
        Log performance metrics to Google Sheets (iron fly format).

        Iron fly specific columns:
        - Premium tracking instead of theta
        - Hold time instead of days held
        - Win/loss counts
        """
        # Calculate win rate
        total_trades = self.cumulative_metrics.get("total_trades", 0)
        winning_trades = self.cumulative_metrics.get("winning_trades", 0)
        losing_trades = self.cumulative_metrics.get("losing_trades", 0)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

        # Calculate hold time if position exists
        hold_minutes = self.position.hold_time_minutes if self.position else 0

        # Calculate average hold time from cumulative data (if we start tracking)
        avg_hold_time = hold_minutes  # Simplified for now

        # All monetary values are stored in CENTS internally, convert to DOLLARS for display
        metrics = {
            "total_pnl": self.daily_pnl / 100,
            "realized_pnl": self.daily_pnl / 100,
            "unrealized_pnl": (self.position.unrealized_pnl / 100) if self.position else 0,
            # Premium tracking (key KPI for iron fly)
            "premium_collected": self.daily_premium_collected / 100,
            "cumulative_premium": self.cumulative_metrics.get("total_premium_collected", 0) / 100,
            # Stats
            "win_rate": win_rate,
            "max_drawdown": abs(min(0, self.daily_pnl / 100)),
            "max_drawdown_pct": 0,
            # Counts
            "trade_count": self.trades_today,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            # Time tracking
            "avg_hold_time": avg_hold_time,
            "best_trade": self.cumulative_metrics.get("best_trade", 0) / 100,
            "worst_trade": self.cumulative_metrics.get("worst_trade", 0) / 100
        }
        self.trade_logger.log_performance_metrics(
            period="Daily",
            metrics=metrics,
            saxo_client=self.client
        )

    def log_account_summary(self):
        """Log account summary to Google Sheets (iron fly format)."""
        # Calculate distance to nearest wing
        distance_to_wing = 0
        wing_width = 0
        if self.position and self.current_price:
            distance_to_wing = min(
                abs(self.current_price - self.position.upper_wing),
                abs(self.current_price - self.position.lower_wing)
            )
            wing_width = self.position.upper_wing - self.position.atm_strike

        strategy_data = {
            # Market Data
            "underlying_price": self.current_price,
            "vix": self.current_vix,
            # Position Values (all in DOLLARS, divide cents by 100)
            "credit_received": self.position.credit_received / 100 if self.position else 0,
            "current_value": self.position.current_value / 100 if self.position else 0,  # Real-time cost to close
            "unrealized_pnl": self.position.unrealized_pnl / 100 if self.position else 0,  # P&L in dollars
            # Strikes
            "atm_strike": self.position.atm_strike if self.position else 0,
            "lower_wing": self.position.lower_wing if self.position else 0,
            "upper_wing": self.position.upper_wing if self.position else 0,
            "wing_width": wing_width,
            # Position Status
            "distance_to_wing": distance_to_wing,
            "hold_time": self.position.hold_time_minutes if self.position else 0
        }
        self.trade_logger.log_account_summary(
            strategy_data=strategy_data,
            saxo_client=self.client,
            environment="LIVE" if not self.dry_run else "SIM"
        )

    def reset_for_new_day(self):
        """
        Reset strategy state for a new trading day.

        SAFETY: Checks for orphaned positions before resetting to prevent
        losing track of open positions at the broker.

        NOTE: Daily summary is handled separately in main.py at market close (4-5 PM ET)
        to avoid duplicate alerts when calendar day changes at midnight UTC (7 PM ET).
        """
        # SAFETY: Check for orphaned positions before reset
        if self.position is not None:
            logger.critical(
                f"ORPHANED POSITION WARNING: Local position still exists during daily reset! "
                f"ATM={self.position.atm_strike}, P&L=${self.position.unrealized_pnl / 100:.2f}"
            )
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_ORPHAN_ON_RESET",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": "Position not properly closed before daily reset",
                "result": "Position cleared from local state - CHECK BROKER FOR ORPHANED POSITIONS"
            })

        # SAFETY: Also check broker for any positions (in non-dry-run mode)
        if not self.dry_run:
            try:
                broker_positions = self.client.get_positions()
                if broker_positions:
                    # LIVE-001: Check both asset types for SPX options
                    spx_options = [p for p in broker_positions
                                  if p.get('AssetType') in ['StockOption', 'StockIndexOption']
                                  and 'SPX' in str(p.get('Description', ''))]
                    if spx_options:
                        logger.critical(
                            f"ORPHANED BROKER POSITIONS: Found {len(spx_options)} SPX options at broker during reset!"
                        )
                        self.trade_logger.log_safety_event({
                            "event_type": "IRON_FLY_BROKER_ORPHAN_ON_RESET",
                            "spy_price": self.current_price,
                            "vix": self.current_vix,
                            "description": f"Found {len(spx_options)} SPX options at broker during daily reset",
                            "result": "NOT resetting state - investigate orphaned positions!"
                        })
                        # DON'T reset if broker has positions - force investigation
                        return
            except Exception as e:
                logger.error(f"Error checking broker positions during reset: {e}")

        logger.info("Resetting strategy for new trading day")
        self.state = IronFlyState.IDLE
        self.opening_range = OpeningRange()
        self.position = None
        self._clear_position_metadata()  # POS-001: Clear any stale metadata
        self._unregister_positions_from_registry()  # POS-005: Clear stale registry entries
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.daily_premium_collected = 0.0
        self.closing_started_at = None
        self._position_reconciled = False  # Force reconciliation on next run
        self._consecutive_stale_data_warnings = 0

        # CB-004: Reset daily circuit breaker tracking
        self._circuit_breaker_opens_today = 0
        self._daily_halt_triggered = False

        # CB-001: Clear partial fill tracking
        self._partial_fill_uics.clear()
