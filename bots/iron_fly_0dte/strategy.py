"""
strategy.py - 0DTE Iron Fly Strategy Implementation (Doc Severson)

This module implements the 0DTE Iron Fly strategy:
- Entry at 10:00 AM EST after opening range check
- Iron Butterfly: Sell ATM call+put, buy wings at expected move
- VIX filter: abort if VIX > 20 or spiking 5%+
- Opening range filter: price must be within 9:30-10:00 high/low
- Take profit: $50-$100 per contract (limit order)
- Stop loss: when SPX touches wing strikes (market order)
- Max hold: 18 minutes to 1 hour

Strategy Source: Doc Severson 0DTE Iron Fly
Video: https://www.youtube.com/watch?v=ad27qIuhgQ4

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
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, date, time as dt_time
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell, OrderType

# Path for persistent metrics storage
METRICS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "iron_fly_metrics.json"
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
DEFAULT_ORDER_TIMEOUT_SECONDS = 60  # Default timeout for limit orders
EMERGENCY_ORDER_TIMEOUT_SECONDS = 30  # Shorter timeout for emergency situations
EMERGENCY_SLIPPAGE_PERCENT = 5.0  # 5% slippage tolerance in emergency mode
MAX_CONSECUTIVE_FAILURES = 5  # Trigger circuit breaker after this many failures
CIRCUIT_BREAKER_COOLDOWN_MINUTES = 5  # Cooldown period when circuit breaker opens

# US Eastern timezone
try:
    import pytz
    US_EASTERN = pytz.timezone('US/Eastern')
except ImportError:
    US_EASTERN = None


def get_us_market_time() -> datetime:
    """Get current time in US Eastern timezone."""
    if US_EASTERN:
        return datetime.now(US_EASTERN)
    # Fallback: assume local time is Eastern (for development)
    return datetime.now()


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
        dry_run: bool = False
    ):
        """
        Initialize the 0DTE Iron Fly strategy.

        Args:
            saxo_client: Authenticated Saxo API client
            config: Strategy configuration dictionary
            logger_service: Trade logging service (Google Sheets, etc.)
            dry_run: If True, simulate trades without placing real orders
        """
        self.client = saxo_client
        self.config = config
        self.trade_logger = logger_service
        self.dry_run = dry_run

        # Strategy configuration
        self.strategy_config = config.get("strategy", {})
        self.underlying_symbol = self.strategy_config.get("underlying_symbol", "SPX:xcbf")
        self.underlying_uic = self.strategy_config.get("underlying_uic", 120)
        # VIX spot UIC for price monitoring (StockIndex type)
        self.vix_uic = self.strategy_config.get("vix_spot_uic", self.strategy_config.get("vix_uic", 10606))
        # Options UIC for SPXW
        self.options_uic = self.strategy_config.get("options_uic", 128)

        # Entry parameters
        self.entry_time = dt_time(10, 0)  # 10:00 AM EST
        self.max_vix = self.strategy_config.get("max_vix_entry", 20.0)
        self.vix_spike_threshold = self.strategy_config.get("vix_spike_threshold_percent", 5.0)

        # Exit parameters
        self.profit_target = self.strategy_config.get("profit_target_per_contract", 75.0)
        self.max_hold_minutes = self.strategy_config.get("max_hold_minutes", 60)
        self.position_size = self.strategy_config.get("position_size", 1)

        # Calibration mode: allow manual expected move override
        self.manual_expected_move = self.strategy_config.get("manual_expected_move", None)

        # Filter configuration
        self.filters_config = config.get("filters", {})
        self.fed_meeting_blackout = self.filters_config.get("fed_meeting_blackout", True)
        self.economic_calendar_check = self.filters_config.get("economic_calendar_check", True)

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

        # Circuit breaker tracking (matching Delta Neutral bot safety features)
        self._consecutive_failures = 0
        self._circuit_breaker_open = False
        self._circuit_breaker_reason = ""
        self._circuit_breaker_opened_at: Optional[datetime] = None

        # Order execution safety tracking
        self._orphaned_orders: List[Dict] = []  # Track orders that may need cleanup
        self._pending_order_ids: List[str] = []  # Track orders awaiting fill
        self._filled_orders: Dict[str, Dict] = {}  # Track filled orders by ID
        self.order_timeout_seconds = self.strategy_config.get(
            "order_timeout_seconds", DEFAULT_ORDER_TIMEOUT_SECONDS
        )

        # Cumulative metrics tracking (persisted across days)
        self.cumulative_metrics = load_cumulative_metrics()

        logger.info(f"IronFlyStrategy initialized - State: {self.state.value}")
        logger.info(f"  Underlying: {self.underlying_symbol} (UIC: {self.underlying_uic})")
        logger.info(f"  Entry time: {self.entry_time} EST")
        logger.info(f"  Max VIX: {self.max_vix}, Spike threshold: {self.vix_spike_threshold}%")
        logger.info(f"  Profit target: ${self.profit_target}, Max hold: {self.max_hold_minutes} min")
        logger.info(f"  FOMC blackout: {'ENABLED' if self.fed_meeting_blackout else 'DISABLED'}")
        logger.info(f"  Economic calendar check: {'ENABLED' if self.economic_calendar_check else 'DISABLED'}")
        if self.manual_expected_move:
            logger.info(f"  Manual expected move: {self.manual_expected_move} points (calibration mode)")

    # =========================================================================
    # CIRCUIT BREAKER METHODS (Safety feature from Delta Neutral bot)
    # =========================================================================

    def _increment_failure_count(self, reason: str) -> None:
        """Increment consecutive failure counter and open circuit breaker if threshold reached."""
        self._consecutive_failures += 1
        logger.warning(f"Order failure #{self._consecutive_failures}: {reason}")

        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._open_circuit_breaker(reason)

    def _reset_failure_count(self) -> None:
        """Reset failure counter after successful operation."""
        if self._consecutive_failures > 0:
            logger.info(f"Resetting failure count (was {self._consecutive_failures})")
            self._consecutive_failures = 0

    def _open_circuit_breaker(self, reason: str) -> None:
        """Open circuit breaker to prevent further order attempts."""
        self._circuit_breaker_open = True
        self._circuit_breaker_reason = reason
        self._circuit_breaker_opened_at = get_eastern_timestamp()

        logger.critical(
            f"CIRCUIT BREAKER OPENED: {reason} "
            f"(after {self._consecutive_failures} consecutive failures)"
        )

        self.trade_logger.log_safety_event({
            "event_type": "IRON_FLY_CIRCUIT_BREAKER_OPEN",
            "spy_price": self.current_price,
            "vix": self.current_vix,
            "description": f"Circuit breaker opened: {reason}",
            "consecutive_failures": self._consecutive_failures,
            "result": "Trading halted - manual intervention required"
        })

    def _check_circuit_breaker(self) -> bool:
        """
        Check if circuit breaker allows trading.

        Returns:
            bool: True if trading allowed, False if blocked
        """
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
                return True

            logger.warning(
                f"Circuit breaker still open: {self._circuit_breaker_reason} "
                f"({cooldown_seconds - elapsed:.0f}s remaining in cooldown)"
            )

        return False

    # =========================================================================
    # ORDER SAFETY METHODS (Safety feature from Delta Neutral bot)
    # =========================================================================

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

    def _place_limit_order_with_timeout(
        self,
        uic: int,
        amount: int,
        direction: BuySell,
        limit_price: float,
        timeout_seconds: Optional[int] = None,
        emergency_mode: bool = False
    ) -> Optional[Dict]:
        """
        Place a limit order with timeout and fill verification.

        This is the safe order placement method that:
        1. Places a limit order
        2. Waits for fill with timeout
        3. Cancels and retries if not filled
        4. Tracks orphaned orders

        Args:
            uic: Instrument UIC
            amount: Number of contracts
            direction: BuySell.Buy or BuySell.Sell
            limit_price: Limit price for the order
            timeout_seconds: How long to wait for fill (default from config)
            emergency_mode: If True, use emergency timeout and slippage

        Returns:
            Dict with order details if filled, None if failed
        """
        if timeout_seconds is None:
            timeout_seconds = EMERGENCY_ORDER_TIMEOUT_SECONDS if emergency_mode else self.order_timeout_seconds

        # Apply slippage in emergency mode
        if emergency_mode:
            slippage = limit_price * (EMERGENCY_SLIPPAGE_PERCENT / 100)
            if direction == BuySell.Buy:
                limit_price = limit_price + slippage  # Pay more to buy
            else:
                limit_price = limit_price - slippage  # Accept less to sell
            logger.warning(f"Emergency mode: adjusted limit price with {EMERGENCY_SLIPPAGE_PERCENT}% slippage to {limit_price:.2f}")

        logger.info(
            f"Placing limit order: {direction.value} {amount}x UIC {uic} @ {limit_price:.2f} "
            f"(timeout: {timeout_seconds}s, emergency: {emergency_mode})"
        )

        try:
            # Place the limit order
            order_result = self.client.place_order(
                uic=uic,
                amount=amount,
                direction=direction,
                order_type=OrderType.Limit,
                limit_price=limit_price
            )

            if not order_result or "OrderId" not in order_result:
                logger.error(f"Failed to place limit order: {order_result}")
                self._increment_failure_count("Order placement failed - no OrderId returned")
                return None

            order_id = order_result["OrderId"]
            self._pending_order_ids.append(order_id)

            # Wait for fill with polling
            start_time = time.time()
            poll_interval = 2  # Check every 2 seconds

            while time.time() - start_time < timeout_seconds:
                try:
                    order_status = self.client.get_order_status(order_id)
                    status = order_status.get("status", "Unknown")

                    if status == "Filled":
                        logger.info(f"Order {order_id} filled successfully")
                        self._pending_order_ids.remove(order_id)
                        self._filled_orders[order_id] = order_status
                        self._reset_failure_count()
                        return order_status

                    elif status in ["Cancelled", "Rejected"]:
                        logger.warning(f"Order {order_id} was {status}")
                        self._pending_order_ids.remove(order_id)
                        self._increment_failure_count(f"Order {status}")
                        return None

                    # Still working, wait and check again
                    time.sleep(poll_interval)

                except Exception as poll_err:
                    logger.error(f"Error polling order status: {poll_err}")
                    time.sleep(poll_interval)

            # Timeout reached - cancel the order
            logger.warning(f"Order {order_id} timeout after {timeout_seconds}s - cancelling")

            try:
                self.client.cancel_order(order_id)
                self._pending_order_ids.remove(order_id)
                logger.info(f"Cancelled timed-out order {order_id}")
            except Exception as cancel_err:
                logger.error(f"Failed to cancel order {order_id}: {cancel_err}")
                # Track as potentially orphaned
                self._add_orphaned_order({
                    "order_id": order_id,
                    "uic": uic,
                    "direction": direction.value,
                    "amount": amount,
                    "limit_price": limit_price,
                    "reason": "timeout_cancel_failed"
                })

            self._increment_failure_count("Order timeout")
            return None

        except Exception as e:
            logger.error(f"Exception placing limit order: {e}")
            self._increment_failure_count(f"Order exception: {str(e)}")
            return None

    def _verify_order_fill(
        self,
        order_id: str,
        leg_name: str,
        timeout_seconds: int = 30
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Verify that a market order has been filled.

        For market orders, this should be fast, but we still verify to catch
        any edge cases (partial fills, rejections, etc.)

        Args:
            order_id: The order ID to verify
            leg_name: Name of the leg (for logging)
            timeout_seconds: How long to wait for fill confirmation

        Returns:
            Tuple of (success: bool, fill_details: Optional[Dict])
        """
        logger.info(f"Verifying fill for {leg_name} order {order_id}...")

        start_time = time.time()
        poll_interval = 1  # Check every 1 second for market orders (should be fast)

        while time.time() - start_time < timeout_seconds:
            try:
                order_status = self.client.get_order_status(order_id)
                status = order_status.get("status", "Unknown")

                if status == "Filled":
                    fill_price = order_status.get("fill_price", 0)
                    logger.info(f"✓ {leg_name} order {order_id} FILLED at {fill_price}")
                    return True, order_status

                elif status in ["Cancelled", "Rejected"]:
                    logger.error(f"✗ {leg_name} order {order_id} was {status}")
                    return False, order_status

                elif status == "PartiallyFilled":
                    filled_qty = order_status.get("filled_quantity", 0)
                    total_qty = order_status.get("total_quantity", 0)
                    logger.warning(f"⚠ {leg_name} order {order_id} partially filled: {filled_qty}/{total_qty}")
                    # Continue waiting for full fill
                    time.sleep(poll_interval)

                else:
                    # Still working/pending
                    time.sleep(poll_interval)

            except Exception as e:
                logger.error(f"Error checking {leg_name} order status: {e}")
                time.sleep(poll_interval)

        # Timeout - order didn't fill in time
        logger.error(f"✗ {leg_name} order {order_id} timed out after {timeout_seconds}s")
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
            order_result = self.client.place_order_with_retry(
                uic=uic,
                asset_type="StockOption",
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

            # Verify the fill
            filled, fill_details = self._verify_order_fill(order_id, leg_name)

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
            logger.error(f"Exception placing {leg_name}: {e}")
            self._increment_failure_count(f"{leg_name} exception: {str(e)}")
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

        # SAFETY: Check circuit breaker before allowing new trades
        if not self._check_circuit_breaker():
            if self.state == IronFlyState.READY_TO_ENTER:
                return f"Circuit breaker open: {self._circuit_breaker_reason}"

        # Update market data
        self.update_market_data()

        current_time = get_us_market_time()

        # SAFETY: Check for stale market data when position is open
        if self.position and self.state in [IronFlyState.POSITION_OPEN, IronFlyState.MONITORING_EXIT]:
            if self.market_data.is_price_stale():
                self._consecutive_stale_data_warnings += 1
                stale_age = self.market_data.price_age_seconds()
                logger.warning(
                    f"STALE DATA WARNING #{self._consecutive_stale_data_warnings}: "
                    f"Price data is {stale_age:.1f}s old (max {MAX_DATA_STALENESS_SECONDS}s)"
                )
                # After 3 consecutive stale warnings, log critical but continue monitoring
                if self._consecutive_stale_data_warnings >= 3:
                    logger.critical(
                        "CRITICAL: Market data consistently stale with open position! "
                        "Stop-loss protection may be compromised."
                    )
                    self.trade_logger.log_safety_event({
                        "event_type": "IRON_FLY_STALE_DATA",
                        "spy_price": self.current_price,
                        "vix": self.current_vix,
                        "description": f"Price data stale for {stale_age:.1f}s with open position",
                        "result": "Continuing with last known price - STOP LOSS MAY BE DELAYED"
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
        """
        if self.dry_run:
            logger.info("Position reconciliation skipped (dry-run mode)")
            return

        try:
            logger.info("Reconciling positions with broker...")
            broker_positions = self.client.get_positions()

            if not broker_positions:
                logger.info("No positions found at broker - starting fresh")
                return

            # Look for iron fly positions (4 legs on same underlying)
            # This is a simplified check - in production, would need more sophisticated matching
            spx_options = [p for p in broker_positions
                          if p.get('AssetType') == 'StockOption'
                          and 'SPX' in str(p.get('Description', ''))]

            if len(spx_options) >= 4:
                logger.critical(
                    f"ORPHANED POSITION DETECTED! Found {len(spx_options)} SPX options at broker. "
                    "This may be a previously opened iron fly. Transitioning to MONITORING_EXIT state."
                )
                self.trade_logger.log_safety_event({
                    "event_type": "IRON_FLY_ORPHAN_DETECTED",
                    "spy_price": self.current_price,
                    "vix": self.current_vix,
                    "description": f"Found {len(spx_options)} orphaned SPX options at broker on startup",
                    "result": "Transitioning to MONITORING_EXIT - manual intervention may be required"
                })
                # Set state to monitoring but position details unknown
                self.state = IronFlyState.MONITORING_EXIT
                # We don't have full position details, but at least we're monitoring

            elif spx_options:
                logger.warning(
                    f"Found {len(spx_options)} SPX options at broker (not a full iron fly). "
                    "These may be partial fills or unrelated positions."
                )

        except Exception as e:
            logger.error(f"Error reconciling positions with broker: {e}")
            # Don't fail startup, but log the error

    def _handle_idle_state(self, current_time: datetime) -> str:
        """
        Handle IDLE state - check if we should start monitoring opening range.

        We transition to WAITING_OPENING_RANGE at 9:30 AM EST.
        """
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
        5. Price-in-range filter
        """
        # FILTER 1: FOMC Meeting check (highest priority - binary event)
        fomc_ok, fomc_reason = self.check_fed_meeting_filter()
        if not fomc_ok:
            self.state = IronFlyState.DAILY_COMPLETE
            self.trade_logger.log_event(f"FILTER BLOCKED: {fomc_reason}")
            self._log_filter_event("FOMC_BLACKOUT", fomc_reason)
            self._log_opening_range_to_sheets("SKIP", fomc_reason)
            return f"Entry blocked - {fomc_reason}"

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
        if not self.opening_range.is_price_in_range(self.current_price):
            self.state = IronFlyState.DAILY_COMPLETE
            reason = f"Price {self.current_price:.2f} outside range [{self.opening_range.low:.2f}-{self.opening_range.high:.2f}]"
            self.trade_logger.log_event(f"FILTER BLOCKED: Trend Day - {reason}")
            self._log_filter_event("TREND_DAY", reason)
            self._log_opening_range_to_sheets("SKIP", reason)
            return f"Entry blocked - Trend Day detected ({reason})"

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

        # EXIT CHECK 2: Profit target
        if self.position.unrealized_pnl >= self.profit_target * self.position.quantity:
            return self._close_position("PROFIT_TARGET",
                f"Profit target reached: ${self.position.unrealized_pnl:.2f} >= ${self.profit_target * self.position.quantity:.2f}")

        # EXIT CHECK 3: Time exit
        if self.position.hold_time_minutes >= self.max_hold_minutes:
            return self._close_position("TIME_EXIT",
                f"Max hold time reached: {self.position.hold_time_minutes} min >= {self.max_hold_minutes} min")

        # Still holding - update state and log
        self.state = IronFlyState.MONITORING_EXIT
        distance, wing = self.position.distance_to_wing(self.current_price)

        return (f"Monitoring - P&L: ${self.position.unrealized_pnl:.2f}, "
                f"Distance to {wing} wing: {distance:.2f} pts, "
                f"Hold time: {self.position.hold_time_minutes} min")

    def _handle_closing_state(self, current_time: datetime) -> str:
        """
        Handle CLOSING state - verify position is closed with timeout protection.

        This state handles the period between initiating close orders and
        confirming all legs are actually closed at the broker.

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

        # Verify position is closed at broker
        try:
            broker_positions = self.client.get_positions()
            if broker_positions:
                # Check for any remaining SPX options
                spx_options = [p for p in broker_positions
                              if p.get('AssetType') == 'StockOption'
                              and 'SPX' in str(p.get('Description', ''))]
                if spx_options:
                    return f"Waiting for close confirmation - {len(spx_options)} legs still open"

            # No positions found - close confirmed
            self.state = IronFlyState.DAILY_COMPLETE
            self.closing_started_at = None
            self.position = None
            return "Position closed - all legs confirmed closed at broker"

        except Exception as e:
            logger.error(f"Error verifying close: {e}")
            return f"Waiting for close confirmation (verification error: {e})"

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
            self.trade_logger.log_trade(
                action="[SIMULATED] OPEN_IRON_FLY",
                strike=f"{lower_wing}/{atm_strike}/{upper_wing}",
                price=simulated_credit / 100,  # Per-contract credit
                delta=0.0,  # Iron Fly is delta neutral at entry
                pnl=0.0,  # No P&L at entry
                saxo_client=self.client,  # For currency conversion
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type="Iron Fly",
                expiry_date=entry_time_eastern.strftime("%Y-%m-%d"),
                dte=0,  # 0DTE
                premium_received=simulated_credit,
                trade_reason="All filters passed"
            )

            logger.info(f"[DRY RUN] Iron Fly position created: Credit=${simulated_credit:.2f}, "
                       f"Wings={lower_wing}/{atm_strike}/{upper_wing}")
            return f"[DRY RUN] Entered Iron Fly at {atm_strike} with ${simulated_credit:.2f} credit"

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

        # Get quotes for credit calculation
        short_call_quote = self.client.get_quote(short_call_uic, "StockOption")
        short_put_quote = self.client.get_quote(short_put_uic, "StockOption")
        long_call_quote = self.client.get_quote(long_call_uic, "StockOption")
        long_put_quote = self.client.get_quote(long_put_uic, "StockOption")

        if not all([short_call_quote, short_put_quote, long_call_quote, long_put_quote]):
            error_msg = "Failed to get quotes for all iron fly legs - ENTRY ABORTED"
            logger.error(error_msg)
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        # Extract bid/ask prices (sell at bid, buy at ask)
        sc_bid = short_call_quote.get('Quote', {}).get('Bid', 0)
        sp_bid = short_put_quote.get('Quote', {}).get('Bid', 0)
        lc_ask = long_call_quote.get('Quote', {}).get('Ask', 0)
        lp_ask = long_put_quote.get('Quote', {}).get('Ask', 0)

        # Calculate net credit: premium received from shorts - premium paid for longs
        # Multiplied by 100 for contract multiplier
        credit_per_contract = (sc_bid + sp_bid - lc_ask - lp_ask)
        total_credit = credit_per_contract * self.position_size * 100

        logger.info(
            f"Iron Fly pricing: SC Bid={sc_bid:.2f}, SP Bid={sp_bid:.2f}, "
            f"LC Ask={lc_ask:.2f}, LP Ask={lp_ask:.2f}, "
            f"Net Credit=${total_credit:.2f}"
        )

        if total_credit <= 0:
            error_msg = f"Iron fly would result in debit (${total_credit:.2f}) - ENTRY ABORTED"
            logger.error(error_msg)
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_DEBIT_SPREAD",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"Iron fly would cost ${abs(total_credit):.2f} instead of receiving credit",
                "result": "Entry blocked - no position opened"
            })
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        # Step 3: Place all 4 orders with fill verification
        # Each leg is placed and verified before proceeding to the next
        entry_time_eastern = get_eastern_timestamp()
        orders_placed = []
        order_ids = {}
        fill_details = {}

        # Check circuit breaker before attempting entry
        if not self._check_circuit_breaker():
            error_msg = f"Circuit breaker open - entry blocked: {self._circuit_breaker_reason}"
            logger.error(error_msg)
            self.state = IronFlyState.DAILY_COMPLETE
            return error_msg

        try:
            # Leg 1: Sell ATM Call (short)
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

            # Leg 2: Sell ATM Put (short)
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

            # Leg 3: Buy Long Call (wing protection)
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

            # Leg 4: Buy Long Put (wing protection)
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

            # All 4 legs successfully placed and verified!
            logger.info(f"All 4 Iron Fly legs placed and verified: {order_ids}")

        except Exception as e:
            # CRITICAL: If any order fails after others succeeded, we have a partial fill
            error_msg = f"ORDER PLACEMENT/VERIFICATION FAILED: {e}"
            logger.critical(error_msg)
            logger.critical(f"Orders placed before failure: {orders_placed}")

            # Track the partially placed orders as orphaned for cleanup
            for leg_name in orders_placed:
                if leg_name in order_ids:
                    self._add_orphaned_order({
                        "order_id": order_ids[leg_name],
                        "leg_name": leg_name,
                        "reason": "partial_iron_fly_entry"
                    })

            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_PARTIAL_FILL",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"Partial fill during iron fly entry: {len(orders_placed)}/4 legs verified",
                "result": f"MANUAL INTERVENTION REQUIRED - Orders: {order_ids}",
                "orders_placed": orders_placed,
                "order_ids": order_ids
            })

            # Open circuit breaker to prevent further entry attempts today
            self._open_circuit_breaker(f"Partial fill: {len(orders_placed)}/4 legs")

            self.state = IronFlyState.DAILY_COMPLETE
            return f"CRITICAL: Partial fill - {len(orders_placed)}/4 legs verified. Manual intervention required!"

        # Step 4: Create position object
        self.position = IronFlyPosition(
            atm_strike=iron_fly_options["atm_strike"],
            upper_wing=iron_fly_options["upper_wing"],
            lower_wing=iron_fly_options["lower_wing"],
            entry_time=entry_time_eastern,
            entry_price=self.current_price,
            credit_received=total_credit,
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
            # Initial prices
            short_call_price=sc_bid,
            short_put_price=sp_bid,
            long_call_price=lc_ask,
            long_put_price=lp_ask
        )

        self.state = IronFlyState.POSITION_OPEN
        self.trades_today += 1
        self.daily_premium_collected += total_credit

        # Step 5: Subscribe to option price updates for position monitoring
        try:
            self.client.subscribe_to_option(short_call_uic, self.handle_price_update)
            self.client.subscribe_to_option(short_put_uic, self.handle_price_update)
            self.client.subscribe_to_option(long_call_uic, self.handle_price_update)
            self.client.subscribe_to_option(long_put_uic, self.handle_price_update)
            logger.info("Subscribed to option price streams for position monitoring")
        except Exception as e:
            logger.warning(f"Failed to subscribe to option streams (will use polling): {e}")

        # Log the trade to Google Sheets
        self.trade_logger.log_trade(
            action="OPEN_IRON_FLY",
            strike=f"{lower_wing}/{atm_strike}/{upper_wing}",
            price=credit_per_contract,
            delta=0.0,  # Iron Fly is delta neutral at entry
            pnl=0.0,
            saxo_client=self.client,
            underlying_price=self.current_price,
            vix=self.current_vix,
            option_type="Iron Fly",
            expiry_date=self.position.expiry,
            dte=0,
            premium_received=total_credit,
            trade_reason="All filters passed"
        )

        logger.info(
            f"IRON FLY OPENED: ATM={atm_strike}, Wings={lower_wing}/{upper_wing}, "
            f"Credit=${total_credit:.2f}, Orders={order_ids}"
        )

        return f"Entered Iron Fly at {atm_strike} with ${total_credit:.2f} credit"

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

        pnl = self.position.unrealized_pnl
        hold_time = self.position.hold_time_minutes

        self.trade_logger.log_event(
            f"CLOSING IRON FLY: {reason} - {description} | "
            f"P&L: ${pnl:.2f}, Hold time: {hold_time} min"
        )

        # Update daily tracking
        self.daily_pnl += pnl

        if self.dry_run:
            # Log the simulated close to Google Sheets
            self.trade_logger.log_trade(
                action=f"[SIMULATED] CLOSE_IRON_FLY_{reason}",
                strike=f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
                price=self.position.credit_received / 100,  # Original credit per contract
                delta=0.0,
                pnl=pnl,
                saxo_client=self.client,  # For currency conversion
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type="Iron Fly",
                expiry_date=self.position.expiry,
                dte=0,
                premium_received=self.position.credit_received,
                trade_reason=description
            )

            self.position = None
            self.state = IronFlyState.DAILY_COMPLETE
            return f"[DRY RUN] Closed position - {reason}: ${pnl:.2f} P&L in {hold_time} min"

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
        # STOP_LOSS: Use MARKET orders for immediate execution (price is touching wing!)
        # PROFIT_TARGET/TIME_EXIT: Can use limit orders for better fills
        use_market_orders = (reason == "STOP_LOSS")
        order_type = OrderType.MARKET if use_market_orders else OrderType.MARKET  # Use market for all for simplicity

        if use_market_orders:
            logger.warning("STOP LOSS TRIGGERED - Using MARKET orders for immediate close!")

        # Step 3: Close all 4 legs (reverse the entry trades)
        close_orders = []
        close_order_ids = {}

        try:
            # Buy back short call (was sold at entry)
            if self.position.short_call_uic:
                logger.info(f"Closing: BUY {self.position.quantity} Short Call at {self.position.atm_strike}")

                # For stop-loss, use emergency order that bypasses circuit breaker
                if use_market_orders:
                    sc_close = self.client.place_emergency_order(
                        uic=self.position.short_call_uic,
                        asset_type="StockOption",
                        buy_sell=BuySell.BUY,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                else:
                    sc_close = self.client.place_order_with_retry(
                        uic=self.position.short_call_uic,
                        asset_type="StockOption",
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
                        asset_type="StockOption",
                        buy_sell=BuySell.BUY,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                else:
                    sp_close = self.client.place_order_with_retry(
                        uic=self.position.short_put_uic,
                        asset_type="StockOption",
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
                        asset_type="StockOption",
                        buy_sell=BuySell.SELL,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                else:
                    lc_close = self.client.place_order_with_retry(
                        uic=self.position.long_call_uic,
                        asset_type="StockOption",
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
                        asset_type="StockOption",
                        buy_sell=BuySell.SELL,
                        amount=self.position.quantity,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                else:
                    lp_close = self.client.place_order_with_retry(
                        uic=self.position.long_put_uic,
                        asset_type="StockOption",
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

        # Log the close trade to Google Sheets
        self.trade_logger.log_trade(
            action=f"CLOSE_IRON_FLY_{reason}",
            strike=f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
            price=self.position.credit_received / 100,
            delta=0.0,
            pnl=pnl,
            saxo_client=self.client,
            underlying_price=self.current_price,
            vix=self.current_vix,
            option_type="Iron Fly",
            expiry_date=self.position.expiry,
            dte=0,
            premium_received=self.position.credit_received,
            trade_reason=description
        )

        logger.info(
            f"IRON FLY CLOSE INITIATED: {reason} - P&L=${pnl:.2f}, "
            f"Hold time={hold_time} min, Close orders={close_order_ids}"
        )

        # Transition to CLOSING state to wait for fill confirmation
        self.state = IronFlyState.CLOSING
        self.closing_started_at = get_eastern_timestamp()

        return f"Closing position ({reason}): P&L=${pnl:.2f}, {len(close_orders)}/4 orders placed"

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def update_market_data(self):
        """
        Update current price and VIX from market data.

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
                quote = self.client.get_quote(self.underlying_uic, asset_type=asset_type)
                if quote:
                    new_price = quote.get('Quote', {}).get('Mid')
                    if new_price and new_price > 0:
                        self.current_price = new_price
                        self.market_data.update_price(new_price)  # Track staleness

            # Get VIX (StockIndex type)
            # VIX doesn't have Bid/Ask/Mid - use PriceInfo.High or calculate from High+Low
            if self.vix_uic:
                vix_quote = self.client.get_quote(self.vix_uic, asset_type="StockIndex")
                if vix_quote:
                    # First try Quote.Mid (for tradeable indices)
                    mid = vix_quote.get('Quote', {}).get('Mid')
                    if mid and mid > 0:
                        self.current_vix = mid
                        self.market_data.update_vix(mid)  # Track staleness
                    else:
                        # Fall back to PriceInfo for non-tradeable indices like VIX
                        price_info = vix_quote.get('PriceInfo', {})
                        high = price_info.get('High', 0)
                        low = price_info.get('Low', 0)
                        if high > 0 and low > 0:
                            # Use midpoint of day's range as proxy
                            vix_value = (high + low) / 2
                            self.current_vix = vix_value
                            self.market_data.update_vix(vix_value)  # Track staleness
                            logger.debug(f"VIX from PriceInfo: High={high}, Low={low}, Using={vix_value}")
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
            hold_seconds = self.position.hold_time_seconds

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
                f"CostToClose=${new_cost_to_close:.2f}, P&L=${self.position.unrealized_pnl:.2f}"
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
            if self.position.short_call_uic:
                sc_quote = self.client.get_quote(self.position.short_call_uic, "StockOption")
                if sc_quote:
                    ask = sc_quote.get('Quote', {}).get('Ask', 0)
                    if ask > 0:
                        self.position.short_call_price = ask
                        prices_updated += 1

            # Get short put price (we need to BUY to close, so use Ask)
            if self.position.short_put_uic:
                sp_quote = self.client.get_quote(self.position.short_put_uic, "StockOption")
                if sp_quote:
                    ask = sp_quote.get('Quote', {}).get('Ask', 0)
                    if ask > 0:
                        self.position.short_put_price = ask
                        prices_updated += 1

            # Get long call price (we need to SELL to close, so use Bid)
            if self.position.long_call_uic:
                lc_quote = self.client.get_quote(self.position.long_call_uic, "StockOption")
                if lc_quote:
                    bid = lc_quote.get('Quote', {}).get('Bid', 0)
                    if bid > 0:
                        self.position.long_call_price = bid
                        prices_updated += 1

            # Get long put price (we need to SELL to close, so use Bid)
            if self.position.long_put_uic:
                lp_quote = self.client.get_quote(self.position.long_put_uic, "StockOption")
                if lp_quote:
                    bid = lp_quote.get('Quote', {}).get('Bid', 0)
                    if bid > 0:
                        self.position.long_put_price = bid
                        prices_updated += 1

            if prices_updated > 0:
                logger.debug(
                    f"Option prices updated ({prices_updated}/4): "
                    f"SC={self.position.short_call_price:.2f}, SP={self.position.short_put_price:.2f}, "
                    f"LC={self.position.long_call_price:.2f}, LP={self.position.long_put_price:.2f}, "
                    f"P&L=${self.position.unrealized_pnl:.2f}"
                )

        except Exception as e:
            logger.warning(f"Error polling option prices: {e}")

    def _round_up_to_strike(self, price: float, increment: float = 5.0) -> float:
        """
        Round price UP to next strike increment.

        Per Doc Severson's bias rule: use the first strike ABOVE current price
        to compensate for put skew.
        """
        import math
        return math.ceil(price / increment) * increment

    def _round_to_strike(self, price: float, increment: float = 5.0) -> float:
        """Round price to nearest strike increment."""
        return round(price / increment) * increment

    def _calculate_expected_move(self) -> float:
        """
        Calculate expected daily move from ATM straddle price.

        The ATM 0DTE straddle price IS the market's expected move for the day.
        This is more accurate than VIX-based calculations.

        Alternative methods (per Doc Severson):
        1. Use broker's "Expected Move" indicator if available
        2. Use cost of ATM straddle as proxy (THIS IS WHAT WE DO)
        3. Manual calibration mode

        Returns:
            float: Expected move rounded to strike increment
        """
        # Check for manual override (calibration mode)
        if self.manual_expected_move:
            logger.info(f"Using manual expected move: {self.manual_expected_move}")
            return self._round_to_strike(self.manual_expected_move)

        # Get expected move from 0DTE ATM straddle price
        expected_move = self.client.get_expected_move_from_straddle(
            self.underlying_uic,
            self.current_price,
            target_dte_min=0,
            target_dte_max=1,  # 0DTE
            option_root_uic=self.options_uic  # SPXW UIC 128 for StockIndexOptions
        )

        if expected_move:
            # Round to nearest strike increment
            rounded_move = self._round_to_strike(expected_move)

            # Minimum wing width of 5 points
            if rounded_move < 5:
                rounded_move = 5.0

            logger.info(f"Expected move from 0DTE straddle: ${expected_move:.2f}, Rounded: ${rounded_move:.2f}")
            return rounded_move

        # Fallback to VIX-based calculation if straddle pricing fails
        logger.warning("Could not get straddle price, falling back to VIX calculation")
        import math
        daily_vol = self.current_vix / math.sqrt(252)
        expected_move = self.current_price * (daily_vol / 100)

        # Round to nearest strike increment
        rounded_move = self._round_to_strike(expected_move)

        # Minimum wing width of 5 points
        if rounded_move < 5:
            rounded_move = 5.0

        logger.debug(f"Fallback expected move: VIX={self.current_vix:.2f}, "
                     f"Daily vol={daily_vol:.4f}%, Raw={expected_move:.2f}, "
                     f"Rounded={rounded_move}")

        return rounded_move

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

    # =========================================================================
    # PRE-TRADE FILTERS (FOMC / Economic Calendar)
    # =========================================================================

    def check_fed_meeting_filter(self) -> Tuple[bool, str]:
        """
        Check if today is an FOMC meeting day.

        Per Doc Severson: "NEVER trade on FOMC or major economic data days"
        The market will likely trend and blow past stops on Fed days.

        Returns:
            Tuple[bool, str]: (True if safe to trade, reason if blocked)
        """
        if not self.fed_meeting_blackout:
            return (True, "")

        # 2026 FOMC Meeting Dates (announcement days - typically 2:00 PM EST)
        # Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
        fomc_dates_2026 = [
            date(2026, 1, 29),   # Jan 28-29
            date(2026, 3, 19),   # Mar 18-19
            date(2026, 5, 7),    # May 6-7
            date(2026, 6, 18),   # Jun 17-18
            date(2026, 7, 30),   # Jul 29-30
            date(2026, 9, 17),   # Sep 16-17
            date(2026, 11, 5),   # Nov 4-5
            date(2026, 12, 17),  # Dec 16-17
        ]

        today = get_us_market_time().date()

        if today in fomc_dates_2026:
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

        # 2026 Major Economic Release Dates
        # CPI (Consumer Price Index) - typically 2nd week of month
        # PPI (Producer Price Index) - typically day after CPI
        # Jobs Report (NFP) - first Friday of month
        # These are high-impact events that cause market trending

        # Note: Dates are approximate - update from BLS calendar:
        # https://www.bls.gov/schedule/news_release/cpi.htm
        # https://www.bls.gov/schedule/news_release/empsit.htm

        major_economic_dates_2026 = {
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
        }

        today = get_us_market_time().date()

        if today in major_economic_dates_2026:
            event = major_economic_dates_2026[today]
            reason = f"Major economic event: {event}"
            logger.warning(f"ECONOMIC CALENDAR BLACKOUT: {reason} - Entry blocked")
            return (False, reason)

        return (True, "")

    # =========================================================================
    # STATUS AND MONITORING
    # =========================================================================

    def get_status_summary(self) -> Dict[str, Any]:
        """Get current strategy status for logging/display."""
        summary = {
            "state": self.state.value,
            "underlying_price": self.current_price,
            "vix": self.current_vix,
            "opening_range_high": self.opening_range.high if self.opening_range.high > 0 else None,
            "opening_range_low": self.opening_range.low if self.opening_range.low < float('inf') else None,
            "opening_range_width": self.opening_range.range_width,
            "opening_range_complete": self.opening_range.is_complete,
            "vix_spike_percent": self.opening_range.vix_spike_percent,
            "trades_today": self.trades_today,
            "daily_pnl": self.daily_pnl,
        }

        if self.position:
            distance, wing = self.position.distance_to_wing(self.current_price)
            summary.update({
                "position_active": True,
                "atm_strike": self.position.atm_strike,
                "upper_wing": self.position.upper_wing,
                "lower_wing": self.position.lower_wing,
                "credit_received": self.position.credit_received,
                "unrealized_pnl": self.position.unrealized_pnl,
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

        Updates underlying price, VIX, and option leg prices for P&L calculation.
        """
        # Update underlying price
        if uic == self.underlying_uic:
            mid = data.get('Quote', {}).get('Mid')
            if mid and mid > 0:
                self.current_price = mid
                self.market_data.update_price(mid)  # Track staleness
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
        summary = {
            "date": get_us_market_time().strftime("%Y-%m-%d"),
            "underlying_close": self.current_price,  # Generic name for SPX/SPY
            "vix": self.current_vix,
            "premium_collected": self.daily_premium_collected,
            "trades_today": self.trades_today,
            "win_rate": win_rate,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_eur": daily_pnl_eur,
            "cumulative_pnl": self.cumulative_metrics["cumulative_pnl"],
            "total_trades": self.cumulative_metrics["total_trades"],
            "winning_trades": self.cumulative_metrics.get("winning_trades", 0),
            "notes": f"Iron Fly 0DTE - State: {self.state.value}"
        }
        self.trade_logger.log_daily_summary(summary)
        logger.info(
            f"Daily summary logged: P&L=${self.daily_pnl:.2f}, Premium=${self.daily_premium_collected:.2f}, "
            f"Trades={self.trades_today}, Cumulative P&L=${self.cumulative_metrics['cumulative_pnl']:.2f}"
        )

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
            "current_value": self.position.credit_received / 100,  # Simplified - would need real quotes
            "pnl": self.position.unrealized_pnl,
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

        metrics = {
            "total_pnl": self.daily_pnl,
            "realized_pnl": self.daily_pnl,
            "unrealized_pnl": self.position.unrealized_pnl if self.position else 0,
            # Premium tracking (key KPI for iron fly)
            "premium_collected": self.daily_premium_collected,
            "cumulative_premium": self.cumulative_metrics.get("total_premium_collected", 0),
            # Stats
            "win_rate": win_rate,
            "max_drawdown": abs(min(0, self.daily_pnl)),
            "max_drawdown_pct": 0,
            # Counts
            "trade_count": self.trades_today,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            # Time tracking
            "avg_hold_time": avg_hold_time,
            "best_trade": self.cumulative_metrics.get("best_trade", 0),
            "worst_trade": self.cumulative_metrics.get("worst_trade", 0)
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
            # Position Values
            "credit_received": self.position.credit_received / 100 if self.position else 0,
            "current_value": self.position.credit_received / 100 if self.position else 0,  # Simplified
            "unrealized_pnl": self.position.unrealized_pnl if self.position else 0,
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
        """
        # Log daily summary before resetting
        if self.state == IronFlyState.DAILY_COMPLETE:
            self.log_daily_summary()

        # SAFETY: Check for orphaned positions before reset
        if self.position is not None:
            logger.critical(
                f"ORPHANED POSITION WARNING: Local position still exists during daily reset! "
                f"ATM={self.position.atm_strike}, P&L=${self.position.unrealized_pnl:.2f}"
            )
            self.trade_logger.log_safety_event({
                "event_type": "IRON_FLY_ORPHAN_ON_RESET",
                "spy_price": self.current_price,
                "vix": self.current_vix,
                "description": f"Position not properly closed before daily reset",
                "result": "Position cleared from local state - CHECK BROKER FOR ORPHANED POSITIONS"
            })

        # SAFETY: Also check broker for any positions (in non-dry-run mode)
        if not self.dry_run:
            try:
                broker_positions = self.client.get_positions()
                if broker_positions:
                    spx_options = [p for p in broker_positions
                                  if p.get('AssetType') == 'StockOption'
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
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.daily_premium_collected = 0.0
        self.closing_started_at = None
        self._position_reconciled = False  # Force reconciliation on next run
        self._consecutive_stale_data_warnings = 0
