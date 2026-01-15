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
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.current_vix = vix
        if vix > self.vix_high:
            self.vix_high = vix

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

    Attributes:
        atm_strike: The ATM strike price (center/body of butterfly)
        upper_wing: Long call strike (upper protection)
        lower_wing: Long put strike (lower protection)
        entry_time: When position was opened
        entry_price: Underlying price at entry
        credit_received: Net premium received (in dollars)
        quantity: Number of contracts
        expiry: Expiration date string (YYYY-MM-DD)
    """
    atm_strike: float = 0.0
    upper_wing: float = 0.0
    lower_wing: float = 0.0
    entry_time: Optional[datetime] = None
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
        """
        short_value = (self.short_call_price + self.short_put_price) * self.quantity * 100
        long_value = (self.long_call_price + self.long_put_price) * self.quantity * 100
        return short_value - long_value

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
        """Calculate minutes position has been held."""
        if not self.entry_time:
            return 0
        return int((datetime.now() - self.entry_time).total_seconds() / 60)

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

    def is_wing_breached(self, current_price: float) -> Tuple[bool, str]:
        """
        Check if price has touched or breached a wing.

        Returns:
            Tuple of (breached: bool, which_wing: str)
        """
        if current_price >= self.upper_wing:
            return (True, "upper")
        elif current_price <= self.lower_wing:
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
        self.underlying_symbol = self.strategy_config.get("underlying_symbol", "SPX")
        self.underlying_uic = self.strategy_config.get("underlying_uic")
        self.vix_uic = self.strategy_config.get("vix_uic", 10606)

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

        # Market data
        self.current_price = 0.0
        self.current_vix = 0.0

        # Daily tracking
        self.trades_today = 0
        self.daily_pnl = 0.0

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
        # Update market data
        self.update_market_data()

        current_time = get_us_market_time()

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
            # Start monitoring opening range
            self.state = IronFlyState.WAITING_OPENING_RANGE
            self.opening_range = OpeningRange(
                start_time=current_time,
                opening_vix=self.current_vix
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
            self.opening_range.is_complete = True
            self.state = IronFlyState.READY_TO_ENTER

            self.trade_logger.log_event(
                f"Opening range complete - High: {self.opening_range.high:.2f}, "
                f"Low: {self.opening_range.low:.2f}, Range: {self.opening_range.range_width:.2f}, "
                f"VIX: {self.current_vix:.2f}"
            )

            return (f"Opening range complete - High: {self.opening_range.high:.2f}, "
                    f"Low: {self.opening_range.low:.2f}, Range: {self.opening_range.range_width:.2f}")

        return (f"Monitoring opening range - Current: {self.current_price:.2f}, "
                f"High: {self.opening_range.high:.2f}, Low: {self.opening_range.low:.2f}, "
                f"Range: {self.opening_range.range_width:.2f}")

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

        # FILTER 3: VIX level check
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
        """Handle CLOSING state - verify position is closed."""
        # Check if all legs are closed
        # For now, just transition to DAILY_COMPLETE
        self.state = IronFlyState.DAILY_COMPLETE
        return "Position closed - daily trading complete"

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
            # Simulate position entry
            simulated_credit = expected_move * 0.7 * 100  # Rough simulation
            self.position = IronFlyPosition(
                atm_strike=atm_strike,
                upper_wing=upper_wing,
                lower_wing=lower_wing,
                entry_time=datetime.now(),
                entry_price=self.current_price,
                credit_received=simulated_credit,
                quantity=self.position_size,
                expiry=datetime.now().strftime("%Y-%m-%d"),
                # Simulate position IDs
                short_call_id="DRY_RUN_SC",
                short_put_id="DRY_RUN_SP",
                long_call_id="DRY_RUN_LC",
                long_put_id="DRY_RUN_LP"
            )
            self.state = IronFlyState.POSITION_OPEN
            self.trades_today += 1

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
                expiry_date=datetime.now().strftime("%Y-%m-%d"),
                dte=0,  # 0DTE
                premium_received=simulated_credit,
                trade_reason="All filters passed"
            )

            return f"[DRY RUN] Entered Iron Fly at {atm_strike} with ${simulated_credit:.2f} credit"

        # TODO: Implement actual order placement via Saxo API
        # 1. Find option UICs for each leg (search by symbol, strike, expiry)
        # 2. Get quotes for all 4 legs
        # 3. Calculate net credit
        # 4. Place 4 orders (or multi-leg order if supported by Saxo)
        # 5. Confirm fills and store position IDs
        # 6. Place profit-taking limit order immediately

        return "Iron Fly entry - implementation pending (use --dry-run for simulation)"

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

        # TODO: Implement actual close orders via Saxo API
        # 1. Cancel any open limit orders (profit taker)
        # 2. Close all 4 legs (market order for stop loss, limit for others)
        # 3. Confirm fills
        # 4. Log final P&L

        self.state = IronFlyState.CLOSING
        return f"Closing position: {reason} - implementation pending"

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def update_market_data(self):
        """Update current price and VIX from market data."""
        try:
            # Get underlying price (US500.I is CfdOnIndex)
            if self.underlying_uic:
                quote = self.client.get_quote(self.underlying_uic, asset_type="CfdOnIndex")
                if quote:
                    self.current_price = quote.get('Quote', {}).get('Mid', self.current_price)

            # Get VIX (StockIndex type)
            # VIX doesn't have Bid/Ask/Mid - use PriceInfo.High or calculate from High+Low
            if self.vix_uic:
                vix_quote = self.client.get_quote(self.vix_uic, asset_type="StockIndex")
                if vix_quote:
                    # First try Quote.Mid (for tradeable indices)
                    mid = vix_quote.get('Quote', {}).get('Mid')
                    if mid and mid > 0:
                        self.current_vix = mid
                    else:
                        # Fall back to PriceInfo for non-tradeable indices like VIX
                        price_info = vix_quote.get('PriceInfo', {})
                        high = price_info.get('High', 0)
                        low = price_info.get('Low', 0)
                        if high > 0 and low > 0:
                            # Use midpoint of day's range as proxy
                            self.current_vix = (high + low) / 2
                            logger.debug(f"VIX from PriceInfo: High={high}, Low={low}, Using={self.current_vix}")
        except Exception as e:
            logger.warning(f"Error updating market data: {e}")

    def _update_position_prices(self):
        """Update current prices for all position legs."""
        if not self.position:
            return

        # In dry run mode, simulate price movement
        if self.dry_run:
            # Simple simulation: position value decays towards profit
            decay_rate = 0.01  # 1% decay per check
            self.position.short_call_price *= (1 - decay_rate)
            self.position.short_put_price *= (1 - decay_rate)
            self.position.long_call_price *= (1 - decay_rate)
            self.position.long_put_price *= (1 - decay_rate)
            return

        # TODO: Get current prices for each leg from streaming or polling
        pass

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
            target_dte_max=1  # 0DTE
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
        """Handle real-time price updates from WebSocket streaming."""
        if uic == self.underlying_uic:
            mid = data.get('Quote', {}).get('Mid')
            if mid:
                self.current_price = mid
        elif uic == self.vix_uic:
            mid = data.get('Quote', {}).get('Mid')
            if mid:
                self.current_vix = mid
        # TODO: Handle option price updates for position legs

    def log_daily_summary(self):
        """Log daily summary to Google Sheets at end of trading day."""
        summary = {
            "date": get_us_market_time().strftime("%Y-%m-%d"),
            "spy_close": self.current_price,
            "vix": self.current_vix,
            "theta_cost": 0,  # Not applicable for 0DTE Iron Fly
            "premium_collected": self.position.credit_received if self.position else 0,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_eur": 0,  # Will be converted if currency enabled
            "cumulative_pnl": self.daily_pnl,  # Single day strategy
            "roll_count": 0,  # Not applicable
            "recenter_count": 0,  # Not applicable
            "notes": f"Iron Fly 0DTE - Trades: {self.trades_today}, State: {self.state.value}"
        }
        self.trade_logger.log_daily_summary(summary)
        logger.info(f"Daily summary logged: P&L=${self.daily_pnl:.2f}, Trades={self.trades_today}")

    def log_position_to_sheets(self):
        """Log current position to Positions worksheet."""
        if not self.position:
            return

        positions = [{
            "type": "Iron Fly",
            "strike": f"{self.position.lower_wing}/{self.position.atm_strike}/{self.position.upper_wing}",
            "expiry": self.position.expiry,
            "dte": 0,
            "entry_price": self.position.credit_received / 100,
            "current_price": self.position.credit_received / 100,  # Simplified - would need real quotes
            "pnl": self.position.unrealized_pnl,
            "theta": 0,  # Would need real greeks
            "status": "OPEN"
        }]
        self.trade_logger.log_position_snapshot(positions)

    def log_performance_metrics(self):
        """Log performance metrics to Google Sheets."""
        win_rate = 100.0 if self.trades_today > 0 and self.daily_pnl > 0 else 0.0

        metrics = {
            "total_pnl": self.daily_pnl,
            "realized_pnl": self.daily_pnl,
            "unrealized_pnl": 0,
            "premium_collected": self.position.credit_received if self.position else 0,
            "theta_cost": 0,
            "net_theta": 0,
            "long_straddle_pnl": 0,
            "short_strangle_pnl": 0,
            "win_rate": win_rate,
            "sharpe_ratio": 0,
            "max_drawdown": abs(min(0, self.daily_pnl)),
            "max_drawdown_pct": 0,
            "trade_count": self.trades_today,
            "roll_count": 0,
            "recenter_count": 0,
            "avg_trade_pnl": self.daily_pnl / self.trades_today if self.trades_today > 0 else 0,
            "best_trade": self.daily_pnl if self.daily_pnl > 0 else 0,
            "worst_trade": self.daily_pnl if self.daily_pnl < 0 else 0,
            "accumulated_theta_income": 0,
            "weekly_theta_income": 0,
            "days_held": 0,
            "days_to_expiry": 0
        }
        self.trade_logger.log_performance_metrics(
            period="Daily",
            metrics=metrics,
            saxo_client=self.client
        )

    def log_account_summary(self):
        """Log account summary to Google Sheets."""
        strategy_data = {
            "spy_price": self.current_price,
            "vix": self.current_vix,
            "unrealized_pnl": self.position.unrealized_pnl if self.position else 0,
            "long_straddle_value": 0,
            "short_strangle_value": 0,
            "strategy_margin": 0,
            "total_delta": 0,  # Iron Fly is delta neutral
            "total_theta": 0,
            "position_count": 4 if self.position else 0,
            "long_call_strike": self.position.upper_wing if self.position else None,
            "long_put_strike": self.position.lower_wing if self.position else None,
            "short_call_strike": self.position.atm_strike if self.position else None,
            "short_put_strike": self.position.atm_strike if self.position else None
        }
        self.trade_logger.log_account_summary(
            strategy_data=strategy_data,
            saxo_client=self.client,
            environment="LIVE" if not self.dry_run else "SIM"
        )

    def reset_for_new_day(self):
        """Reset strategy state for a new trading day."""
        # Log daily summary before resetting
        if self.state == IronFlyState.DAILY_COMPLETE:
            self.log_daily_summary()

        logger.info("Resetting strategy for new trading day")
        self.state = IronFlyState.IDLE
        self.opening_range = OpeningRange()
        self.position = None
        self.trades_today = 0
        self.daily_pnl = 0.0
