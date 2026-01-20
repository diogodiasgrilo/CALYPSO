"""
strategy.py - Rolling Put Diagonal Strategy Implementation

Bill Belt's Rolling Put Diagonal strategy on QQQ:
- Buy 14 DTE put at 33 delta for protection (long put)
- Sell daily ATM puts for income (short put)
- Roll short puts daily based on market direction
- Close campaign 1-2 days before long put expires

Entry Filters (all must be true):
1. Price > 9 EMA (bullish bias)
2. MACD histogram rising (momentum confirmation)
3. CCI < 100 (not overbought)
4. Weekly trend not bearish

Roll Types:
- Vertical Roll: When price >= short strike (bullish) - roll to new ATM
- Horizontal Roll: When price < short strike (bearish) - roll to SAME strike

Author: Trading Bot Developer
Date: 2026
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell, OrderType
from shared.market_hours import get_us_market_time, is_weekend, is_market_holiday, is_market_open
from shared.technical_indicators import (
    calculate_all_indicators,
    TechnicalIndicatorValues,
)
from shared.event_calendar import (
    is_event_approaching,
    should_close_for_event,
    get_event_status_message,
    get_next_fomc_date,
)

# Path for persistent metrics storage
METRICS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "rolling_put_diagonal_metrics.json"
)

# Configure module logger
logger = logging.getLogger(__name__)


class RPDState(Enum):
    """States of the Rolling Put Diagonal strategy."""
    IDLE = "Idle"                           # No positions, checking entry filters
    WAITING_ENTRY = "WaitingEntry"          # Entry filters not met, waiting
    WAITING_EVENT = "WaitingEvent"          # Closed for FOMC/earnings event
    POSITION_OPEN = "PositionOpen"          # Full diagonal active
    ROLLING_SHORT = "RollingShort"          # Daily roll in progress
    ROLLING_LONG = "RollingLong"            # Rolling up long put (delta < 20)
    CLOSING_CAMPAIGN = "ClosingCampaign"    # Closing at 1-2 DTE on long
    EMERGENCY_EXIT = "EmergencyExit"        # Price significantly below 9 EMA
    CIRCUIT_OPEN = "CircuitOpen"            # Safety halt - manual intervention needed


class RollType(Enum):
    """Types of short put rolls."""
    VERTICAL = "Vertical"      # Roll to new ATM (bullish move)
    HORIZONTAL = "Horizontal"  # Roll to same strike (bearish move)


@dataclass
class PutPosition:
    """
    Represents a single put option position.

    Attributes:
        position_id: Unique identifier from the broker
        uic: Unique Instrument Code
        strike: Strike price
        expiry: Expiration date string
        quantity: Number of contracts (positive=long, negative=short)
        entry_price: Price at entry
        current_price: Current market price
        delta: Position delta (negative for puts)
        theta: Position theta (time decay)
        gamma: Position gamma
        vega: Position vega
    """
    position_id: str = ""
    uic: int = 0
    strike: float = 0.0
    expiry: str = ""
    quantity: int = 0
    entry_price: float = 0.0
    current_price: float = 0.0
    delta: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0

    @property
    def is_long(self) -> bool:
        """Check if this is a long position."""
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        """Check if this is a short position."""
        return self.quantity < 0

    @property
    def dte(self) -> int:
        """Calculate days to expiration."""
        if not self.expiry:
            return 0
        try:
            exp_date = datetime.strptime(self.expiry[:10], "%Y-%m-%d").date()
            today = datetime.now().date()
            return max(0, (exp_date - today).days)
        except ValueError:
            return 0

    @property
    def market_value(self) -> float:
        """Calculate current market value (positive for long, negative for short)."""
        return self.current_price * abs(self.quantity) * 100


@dataclass
class DiagonalPosition:
    """
    Represents a put diagonal spread position.

    Long put (protection): 14 DTE, 33 delta, further OTM
    Short put (income): 1 DTE, ATM

    Attributes:
        long_put: The long put position (protection)
        short_put: The short put position (income)
        campaign_number: Sequential campaign number
        campaign_start_date: When this campaign started
        total_premium_collected: Sum of all premium from short puts
        roll_count: Number of times short put was rolled
        vertical_roll_count: Number of vertical rolls (new ATM strike)
        horizontal_roll_count: Number of horizontal rolls (same strike)
        last_roll_type: Type of last roll (vertical/horizontal)
    """
    long_put: Optional[PutPosition] = None
    short_put: Optional[PutPosition] = None
    campaign_number: int = 1
    campaign_start_date: str = ""
    total_premium_collected: float = 0.0
    roll_count: int = 0
    vertical_roll_count: int = 0
    horizontal_roll_count: int = 0
    last_roll_type: Optional[RollType] = None

    @property
    def is_complete(self) -> bool:
        """Check if both legs of the diagonal are active."""
        return self.long_put is not None and self.short_put is not None

    @property
    def has_long_only(self) -> bool:
        """Check if only long put is active (short expired or closed)."""
        return self.long_put is not None and self.short_put is None

    @property
    def long_dte(self) -> int:
        """Get DTE of long put."""
        return self.long_put.dte if self.long_put else 0

    @property
    def short_dte(self) -> int:
        """Get DTE of short put."""
        return self.short_put.dte if self.short_put else 0

    @property
    def total_delta(self) -> float:
        """Calculate total position delta."""
        long_delta = self.long_put.delta if self.long_put else 0
        short_delta = self.short_put.delta if self.short_put else 0
        return long_delta + short_delta

    @property
    def long_delta_abs(self) -> float:
        """Get absolute delta of long put."""
        return abs(self.long_put.delta) if self.long_put else 0

    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized P&L of the position."""
        pnl = 0.0
        if self.long_put:
            # Long put: profit if current > entry, loss if current < entry
            pnl += (self.long_put.current_price - self.long_put.entry_price) * abs(self.long_put.quantity) * 100
        if self.short_put:
            # Short put: profit if current < entry (option decayed)
            pnl += (self.short_put.entry_price - self.short_put.current_price) * abs(self.short_put.quantity) * 100
        return pnl


@dataclass
class StrategyMetrics:
    """
    Tracks strategy performance metrics.

    Attributes:
        total_premium_collected: Total premium from short puts
        total_long_cost: Total cost of long puts
        realized_pnl: Realized profit/loss from closed positions
        campaign_count: Number of completed campaigns
        roll_count: Total number of short rolls
        vertical_rolls: Number of vertical rolls (bullish)
        horizontal_rolls: Number of horizontal rolls (bearish)
    """
    total_premium_collected: float = 0.0
    total_long_cost: float = 0.0
    realized_pnl: float = 0.0
    campaign_count: int = 0
    roll_count: int = 0
    vertical_rolls: int = 0
    horizontal_rolls: int = 0
    # Trade statistics
    winning_campaigns: int = 0
    losing_campaigns: int = 0
    best_campaign_pnl: float = 0.0
    worst_campaign_pnl: float = 0.0
    # Drawdown tracking
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    # Daily tracking
    daily_pnl_start: float = 0.0
    qqq_open: float = 0.0
    qqq_high: float = 0.0
    qqq_low: float = 0.0

    def reset_daily_tracking(self, current_pnl: float, qqq_price: float):
        """Reset daily tracking at start of trading day."""
        self.daily_pnl_start = current_pnl
        self.qqq_open = qqq_price
        self.qqq_high = qqq_price
        self.qqq_low = qqq_price

    def update_daily_tracking(self, qqq_price: float):
        """Update daily high/low tracking."""
        if qqq_price > self.qqq_high:
            self.qqq_high = qqq_price
        if qqq_price < self.qqq_low or self.qqq_low == 0:
            self.qqq_low = qqq_price

    @property
    def total_pnl(self) -> float:
        """Calculate total realized P&L."""
        return self.realized_pnl

    @property
    def win_rate(self) -> float:
        """Calculate campaign win rate."""
        total = self.winning_campaigns + self.losing_campaigns
        if total == 0:
            return 0.0
        return self.winning_campaigns / total

    def record_campaign(self, pnl: float):
        """Record a completed campaign."""
        self.campaign_count += 1
        if pnl > 0:
            self.winning_campaigns += 1
        else:
            self.losing_campaigns += 1
        if pnl > self.best_campaign_pnl:
            self.best_campaign_pnl = pnl
        if pnl < self.worst_campaign_pnl:
            self.worst_campaign_pnl = pnl

    def update_drawdown(self, current_pnl: float):
        """Update peak P&L and max drawdown."""
        if current_pnl > self.peak_pnl:
            self.peak_pnl = current_pnl
        drawdown = self.peak_pnl - current_pnl
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary for persistence."""
        return {
            "total_premium_collected": self.total_premium_collected,
            "total_long_cost": self.total_long_cost,
            "realized_pnl": self.realized_pnl,
            "campaign_count": self.campaign_count,
            "roll_count": self.roll_count,
            "vertical_rolls": self.vertical_rolls,
            "horizontal_rolls": self.horizontal_rolls,
            "winning_campaigns": self.winning_campaigns,
            "losing_campaigns": self.losing_campaigns,
            "best_campaign_pnl": self.best_campaign_pnl,
            "worst_campaign_pnl": self.worst_campaign_pnl,
            "peak_pnl": self.peak_pnl,
            "max_drawdown": self.max_drawdown,
            "last_updated": datetime.now().isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyMetrics":
        """Create StrategyMetrics from saved dictionary."""
        metrics = cls()
        metrics.total_premium_collected = data.get("total_premium_collected", 0.0)
        metrics.total_long_cost = data.get("total_long_cost", 0.0)
        metrics.realized_pnl = data.get("realized_pnl", 0.0)
        metrics.campaign_count = data.get("campaign_count", 0)
        metrics.roll_count = data.get("roll_count", 0)
        metrics.vertical_rolls = data.get("vertical_rolls", 0)
        metrics.horizontal_rolls = data.get("horizontal_rolls", 0)
        metrics.winning_campaigns = data.get("winning_campaigns", 0)
        metrics.losing_campaigns = data.get("losing_campaigns", 0)
        metrics.best_campaign_pnl = data.get("best_campaign_pnl", 0.0)
        metrics.worst_campaign_pnl = data.get("worst_campaign_pnl", 0.0)
        metrics.peak_pnl = data.get("peak_pnl", 0.0)
        metrics.max_drawdown = data.get("max_drawdown", 0.0)
        return metrics

    def save_to_file(self, filepath: str = None) -> bool:
        """Save metrics to JSON file for persistence."""
        filepath = filepath or METRICS_FILE
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
            logger.info(f"Saved strategy metrics to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save metrics: {e}")
            return False

    @classmethod
    def load_from_file(cls, filepath: str = None) -> Optional["StrategyMetrics"]:
        """Load metrics from JSON file."""
        filepath = filepath or METRICS_FILE
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    data = json.load(f)
                logger.info(f"Loaded strategy metrics from {filepath}")
                return cls.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load metrics: {e}")
        return None


class RollingPutDiagonalStrategy:
    """
    Rolling Put Diagonal Strategy Implementation.

    Bill Belt's strategy that generates daily income by selling
    ATM puts against a longer-dated OTM put for protection.

    Attributes:
        client: SaxoClient instance for API calls
        config: Strategy configuration dictionary
        state: Current strategy state
        diagonal: Current diagonal position
        indicators: Current technical indicator values
        metrics: Strategy performance metrics
    """

    def __init__(
        self,
        client: SaxoClient,
        config: Dict[str, Any],
        trade_logger: Any = None,
        dry_run: bool = False
    ):
        """
        Initialize the strategy.

        Args:
            client: SaxoClient instance for API operations
            config: Configuration dictionary with strategy parameters
            trade_logger: Optional logger service for trade logging
            dry_run: If True, simulate trades without placing real orders
        """
        self.client = client
        self.config = config
        self.strategy_config = config["strategy"]
        self.trade_logger = trade_logger
        self.dry_run = dry_run or self.strategy_config.get("dry_run", False)

        # Strategy state
        self.state = RPDState.IDLE
        self.diagonal: Optional[DiagonalPosition] = None

        # Technical indicators
        self.indicators: Optional[TechnicalIndicatorValues] = None

        # Load persisted metrics or start fresh
        saved_metrics = StrategyMetrics.load_from_file()
        self.metrics = saved_metrics if saved_metrics else StrategyMetrics()

        # Underlying tracking
        self.underlying_uic = self.strategy_config["underlying_uic"]
        self.underlying_symbol = self.strategy_config["underlying_symbol"]

        # Current market data
        self.current_price: float = 0.0
        self.current_ema_9: float = 0.0

        # Configuration parameters
        self.long_put_config = self.strategy_config["long_put"]
        self.short_put_config = self.strategy_config["short_put"]
        self.indicator_config = self.strategy_config["indicators"]
        self.management_config = self.strategy_config["management"]
        self.event_config = self.strategy_config.get("event_risk", {})

        # Safety: Consecutive failure tracking
        self._consecutive_failures: int = 0
        self._max_consecutive_failures: int = config.get("circuit_breaker", {}).get(
            "max_consecutive_failures", 3
        )
        self._circuit_breaker_open: bool = False
        self._circuit_breaker_reason: str = ""
        self._last_failure_time: Optional[datetime] = None

        # Safety: Orphaned order tracking
        self._orphaned_orders: List[str] = []

        # Safety: Action cooldown
        self._action_cooldowns: Dict[str, datetime] = {}
        self._cooldown_seconds: int = config.get("circuit_breaker", {}).get(
            "action_cooldown_seconds", 300
        )

        # Order timeout
        self._order_timeout: int = self.management_config.get("order_timeout_seconds", 60)

        # Position isolation: Only look at QQQ options
        self._position_filter_prefix = "QQQ/"

        logger.info(f"RollingPutDiagonalStrategy initialized")
        logger.info(f"  Underlying: {self.underlying_symbol} (UIC: {self.underlying_uic})")
        logger.info(f"  Dry run: {self.dry_run}")
        logger.info(f"  Long put: {self.long_put_config['target_dte']} DTE, {self.long_put_config['target_delta']} delta")
        logger.info(f"  Short put: {self.short_put_config['target_dte']} DTE, ATM")

    # =========================================================================
    # SAFETY MECHANISMS
    # =========================================================================

    def _increment_failure_count(self, reason: str) -> None:
        """
        Increment consecutive failure count and check circuit breaker.

        Called when an operation fails. If we hit MAX_CONSECUTIVE_FAILURES,
        the circuit breaker opens and halts all trading.

        Args:
            reason: Description of the failure
        """
        self._consecutive_failures += 1
        self._last_failure_time = datetime.now()

        logger.warning(f"Operation failed: {reason}")
        logger.warning(f"Consecutive failures: {self._consecutive_failures}/{self._max_consecutive_failures}")

        if self._consecutive_failures >= self._max_consecutive_failures:
            self._open_circuit_breaker(reason)

    def _reset_failure_count(self) -> None:
        """Reset consecutive failure count after successful operation."""
        if self._consecutive_failures > 0:
            logger.info(f"Resetting failure count (was {self._consecutive_failures})")
            self._consecutive_failures = 0

    def _open_circuit_breaker(self, reason: str) -> None:
        """
        Open circuit breaker to halt all trading.

        This is a CRITICAL safety mechanism. When open:
        - No new orders will be placed
        - No rolls will be attempted
        - Manual intervention is required

        Args:
            reason: Description of why the circuit breaker opened
        """
        self._circuit_breaker_open = True
        self._circuit_breaker_reason = reason
        self.state = RPDState.CIRCUIT_OPEN

        logger.critical("=" * 70)
        logger.critical("CIRCUIT BREAKER OPEN - ALL TRADING HALTED")
        logger.critical("=" * 70)
        logger.critical(f"Reason: {reason}")
        logger.critical(f"Consecutive failures: {self._consecutive_failures}")
        logger.critical(f"Time: {datetime.now().isoformat()}")
        logger.critical("")
        logger.critical("MANUAL INTERVENTION REQUIRED:")
        logger.critical("1. Check Saxo positions in SaxoTraderGO")
        logger.critical("2. Verify no orphaned orders are pending")
        logger.critical("3. Fix any position discrepancies")
        logger.critical("4. Restart the bot after fixing issues")
        logger.critical("=" * 70)

        # Log to Google Sheets
        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "event_type": "RPD_CIRCUIT_BREAKER_OPEN",
                "description": reason,
                "action_taken": "HALT_TRADING",
                "result": "Requires manual intervention",
                "qqq_price": self.current_price,
                "state": self.state.value,
            })

    def _check_circuit_breaker(self) -> bool:
        """
        Check if circuit breaker is open.

        Returns:
            True if circuit breaker is open (trading halted), False otherwise
        """
        if self._circuit_breaker_open:
            logger.warning(f"Circuit breaker OPEN: {self._circuit_breaker_reason}")
            return True
        return False

    def _is_action_on_cooldown(self, action_type: str) -> bool:
        """
        Check if an action is on cooldown after a recent failure.

        Prevents rapid retry loops.

        Args:
            action_type: Type of action (e.g., "enter_campaign", "roll_short")

        Returns:
            True if action is on cooldown, False if OK to proceed
        """
        if action_type not in self._action_cooldowns:
            return False

        last_attempt = self._action_cooldowns[action_type]
        elapsed = (datetime.now() - last_attempt).total_seconds()

        if elapsed < self._cooldown_seconds:
            remaining = self._cooldown_seconds - elapsed
            logger.info(f"Action '{action_type}' on cooldown for {remaining:.0f}s more")
            return True

        # Cooldown expired
        del self._action_cooldowns[action_type]
        return False

    def _set_action_cooldown(self, action_type: str) -> None:
        """Set cooldown for an action after failure."""
        self._action_cooldowns[action_type] = datetime.now()
        logger.info(f"Action '{action_type}' placed on {self._cooldown_seconds}s cooldown")

    def _track_orphaned_order(self, order_id: str) -> None:
        """Track an order that couldn't be cancelled."""
        self._orphaned_orders.append(order_id)
        logger.error(f"ORPHANED ORDER detected: {order_id}")

        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "event_type": "RPD_ORPHANED_ORDER",
                "description": f"Order {order_id} could not be cancelled",
                "action_taken": "HALT_TRADING",
                "result": "Manual cancellation required",
            })

        # Orphaned orders are critical - open circuit breaker
        self._open_circuit_breaker(f"Orphaned order detected: {order_id}")

    # =========================================================================
    # POSITION RECOVERY
    # =========================================================================

    def _filter_qqq_options(self, positions: List[Dict]) -> List[Dict]:
        """
        Filter positions to QQQ options only.

        Uses strict prefix matching to avoid collision with other QQQ-related
        ETFs like QQQM, QQQJ, etc.

        Args:
            positions: List of position dictionaries from Saxo

        Returns:
            List of QQQ option positions only
        """
        qqq_options = []
        for pos in positions:
            symbol = pos.get("DisplayAndFormat", {}).get("Symbol", "")
            asset_type = pos.get("PositionBase", {}).get("AssetType", "")

            # Strict matching: Symbol must START with "QQQ/"
            if symbol.upper().startswith(self._position_filter_prefix) and asset_type == "StockOption":
                qqq_options.append(pos)

        return qqq_options

    def recover_positions(self) -> bool:
        """
        Reconstruct strategy state from broker positions on startup.

        This is critical for recovering from bot restarts without orphaning
        positions. We look for existing QQQ put options and categorize them
        as long (positive quantity) or short (negative quantity).

        Returns:
            True if positions were recovered, False if no positions found
        """
        logger.info("Recovering positions from broker...")

        try:
            all_positions = self.client.get_positions()
            if not all_positions:
                logger.info("No positions found in account")
                return False

            # Filter to QQQ options only
            qqq_options = self._filter_qqq_options(all_positions)
            if not qqq_options:
                logger.info("No QQQ options found")
                return False

            logger.info(f"Found {len(qqq_options)} QQQ option position(s)")

            # Categorize by long/short
            long_puts = []
            short_puts = []

            for pos in qqq_options:
                amount = pos.get("PositionBase", {}).get("Amount", 0)
                option_type = pos.get("DisplayAndFormat", {}).get("Description", "")

                # Only interested in puts for this strategy
                if "PUT" not in option_type.upper():
                    continue

                if amount > 0:
                    long_puts.append(pos)
                elif amount < 0:
                    short_puts.append(pos)

            logger.info(f"Long puts: {len(long_puts)}, Short puts: {len(short_puts)}")

            # Reconstruct diagonal if we have positions
            if long_puts or short_puts:
                self.diagonal = DiagonalPosition()

                if long_puts:
                    # Take the first long put (should only be one)
                    lp = long_puts[0]
                    self.diagonal.long_put = self._position_dict_to_put(lp)
                    logger.info(f"Recovered long put: strike {self.diagonal.long_put.strike}, "
                               f"expiry {self.diagonal.long_put.expiry}, DTE {self.diagonal.long_put.dte}")

                if short_puts:
                    # Take the first short put (should only be one)
                    sp = short_puts[0]
                    self.diagonal.short_put = self._position_dict_to_put(sp)
                    logger.info(f"Recovered short put: strike {self.diagonal.short_put.strike}, "
                               f"expiry {self.diagonal.short_put.expiry}, DTE {self.diagonal.short_put.dte}")

                # Set state based on what we found
                if self.diagonal.is_complete:
                    self.state = RPDState.POSITION_OPEN
                    logger.info("Full diagonal recovered - state set to POSITION_OPEN")
                elif self.diagonal.has_long_only:
                    self.state = RPDState.POSITION_OPEN
                    logger.info("Long-only recovered - need to sell new short")

                return True

            return False

        except Exception as e:
            logger.error(f"Error recovering positions: {e}")
            return False

    def _position_dict_to_put(self, pos_dict: Dict) -> PutPosition:
        """Convert Saxo position dictionary to PutPosition dataclass."""
        base = pos_dict.get("PositionBase", {})
        display = pos_dict.get("DisplayAndFormat", {})

        # Extract strike and expiry from symbol (e.g., "QQQ/21Jan26P500")
        symbol = display.get("Symbol", "")
        strike = 0.0
        expiry = ""

        # Try to parse from symbol - format varies
        # Also check structured fields
        if "StrikePrice" in base:
            strike = base.get("StrikePrice", 0)
        if "ExpiryDate" in base:
            expiry = base.get("ExpiryDate", "")

        return PutPosition(
            position_id=str(base.get("PositionId", "")),
            uic=base.get("Uic", 0),
            strike=strike,
            expiry=expiry,
            quantity=base.get("Amount", 0),
            entry_price=base.get("OpenPrice", 0.0),
            current_price=pos_dict.get("PositionView", {}).get("CurrentPrice", 0.0),
            delta=pos_dict.get("Greeks", {}).get("Delta", 0.0),
            theta=pos_dict.get("Greeks", {}).get("Theta", 0.0),
        )

    def _check_stuck_state(self) -> bool:
        """
        Detect if bot is stuck in a transient state.

        Transient states like ROLLING_SHORT or CLOSING_CAMPAIGN should
        complete quickly. If we start up in one of these, something went wrong.

        Returns:
            True if stuck state was detected and recovered, False otherwise
        """
        transient_states = [
            RPDState.ROLLING_SHORT,
            RPDState.ROLLING_LONG,
            RPDState.CLOSING_CAMPAIGN,
        ]

        if self.state in transient_states:
            logger.warning(f"Bot started in transient state: {self.state.value}")
            logger.warning("Recovering actual state from broker positions...")

            # Reset state and recover from positions
            self.state = RPDState.IDLE
            self.recover_positions()
            return True

        return False

    # =========================================================================
    # MARKET DATA
    # =========================================================================

    def update_market_data(self) -> bool:
        """
        Update current market data and technical indicators.

        Returns:
            True if data updated successfully, False otherwise
        """
        try:
            # Get current QQQ price
            quote = self.client.get_quote(self.underlying_uic, asset_type="Etf")
            if quote and "Quote" in quote:
                q = quote["Quote"]
                self.current_price = q.get("Mid") or q.get("LastTraded", 0)
            else:
                logger.warning("Failed to get QQQ quote")
                return False

            if self.current_price <= 0:
                logger.warning("Invalid QQQ price")
                return False

            # Get chart data for technical indicators
            daily_bars = self.client.get_daily_ohlc(
                uic=self.underlying_uic,
                asset_type="Etf",
                days=50
            )

            if daily_bars and len(daily_bars) >= 30:
                # Extract price arrays
                closes = [bar.get("Close", 0) for bar in daily_bars]
                highs = [bar.get("High", 0) for bar in daily_bars]
                lows = [bar.get("Low", 0) for bar in daily_bars]

                # Calculate indicators
                self.indicators = calculate_all_indicators(
                    prices=closes,
                    highs=highs,
                    lows=lows,
                    current_price=self.current_price,
                    ema_period=self.indicator_config.get("ema_period", 9),
                    macd_fast=self.indicator_config.get("macd_fast", 12),
                    macd_slow=self.indicator_config.get("macd_slow", 26),
                    macd_signal=self.indicator_config.get("macd_signal", 9),
                    cci_period=self.indicator_config.get("cci_period", 20),
                    cci_overbought=self.indicator_config.get("cci_overbought", 100),
                )

                self.current_ema_9 = self.indicators.ema_9
                logger.debug(f"QQQ: ${self.current_price:.2f}, EMA9: ${self.current_ema_9:.2f}")

            else:
                logger.warning("Insufficient chart data for indicators")
                # Create basic indicators without full data
                self.indicators = TechnicalIndicatorValues(current_price=self.current_price)

            # Update daily tracking
            self.metrics.update_daily_tracking(self.current_price)

            return True

        except Exception as e:
            logger.error(f"Error updating market data: {e}")
            return False

    # =========================================================================
    # ENTRY CONDITIONS
    # =========================================================================

    def check_entry_conditions(self) -> Tuple[bool, str]:
        """
        Check if all entry conditions are met.

        Entry requires:
        1. Price > 9 EMA (bullish bias)
        2. MACD histogram rising (momentum)
        3. CCI < 100 (not overbought)
        4. Weekly trend not bearish (optional)
        5. No upcoming FOMC or major earnings

        Returns:
            Tuple of (conditions_met, reason_if_not_met)
        """
        if self.indicators is None:
            return False, "No indicator data available"

        # Check event risk first
        should_close, event_reason = should_close_for_event(
            days_before_fomc=self.event_config.get("fomc_blackout_days", 1),
            days_before_earnings=self.event_config.get("earnings_blackout_days", 1),
        )
        if should_close:
            return False, f"Event risk: {event_reason}"

        # Check entry filters
        if not self.indicators.price_above_ema:
            return False, f"Price ${self.current_price:.2f} below 9 EMA ${self.indicators.ema_9:.2f}"

        if not self.indicators.macd_histogram_rising and not self.indicators.macd_histogram_positive:
            return False, f"MACD histogram not rising (current: {self.indicators.macd_histogram:.4f})"

        if self.indicators.cci_overbought:
            return False, f"CCI overbought: {self.indicators.cci:.2f} > 100"

        if self.indicators.weekly_trend_bearish:
            return False, "Weekly trend is bearish"

        return True, "All entry conditions met"

    # =========================================================================
    # CORE STRATEGY LOGIC
    # =========================================================================

    def enter_campaign(self) -> bool:
        """
        Enter a new put diagonal campaign.

        1. Buy long put (14 DTE, 33 delta)
        2. Sell short put (1 DTE, ATM)

        Returns:
            True if campaign entered successfully, False otherwise
        """
        if self._check_circuit_breaker():
            return False

        if self._is_action_on_cooldown("enter_campaign"):
            return False

        if self.diagonal and self.diagonal.is_complete:
            logger.warning("Cannot enter - diagonal already exists")
            return False

        logger.info("=" * 50)
        logger.info("ENTERING NEW CAMPAIGN")
        logger.info("=" * 50)

        try:
            # Step 1: Find and buy long put (14 DTE, 33 delta)
            long_put_data = self.client.find_put_by_delta(
                underlying_uic=self.underlying_uic,
                underlying_price=self.current_price,
                target_delta=self.long_put_config["target_delta"],
                target_dte=self.long_put_config["target_dte"],
                delta_tolerance=self.long_put_config.get("delta_tolerance", 0.05),
            )

            if not long_put_data:
                logger.error("Failed to find suitable long put")
                self._increment_failure_count("Failed to find long put")
                self._set_action_cooldown("enter_campaign")
                return False

            logger.info(f"Found long put: strike ${long_put_data['strike']}, "
                       f"delta {long_put_data['delta']:.3f}, DTE {long_put_data['dte']}")

            # Step 2: Find short put (1 DTE, ATM)
            next_expiry = self.client.find_next_trading_day_expiry(self.underlying_uic)
            if not next_expiry:
                logger.error("Failed to find next trading day expiry")
                self._increment_failure_count("Failed to find short put expiry")
                self._set_action_cooldown("enter_campaign")
                return False

            short_put_data = self.client.find_atm_put_for_expiry(
                underlying_uic=self.underlying_uic,
                underlying_price=self.current_price,
                expiry_date=next_expiry["expiry"],
            )

            if not short_put_data:
                logger.error("Failed to find ATM short put")
                self._increment_failure_count("Failed to find short put")
                self._set_action_cooldown("enter_campaign")
                return False

            logger.info(f"Found short put: strike ${short_put_data['strike']}, "
                       f"DTE {short_put_data['dte']}")

            # Step 3: Place orders
            if self.dry_run:
                logger.info("[DRY RUN] Would place orders:")
                logger.info(f"  BUY long put: UIC {long_put_data['uic']}, strike {long_put_data['strike']}")
                logger.info(f"  SELL short put: UIC {short_put_data['uic']}, strike {short_put_data['strike']}")

                # Simulate success in dry run
                self.diagonal = DiagonalPosition(
                    long_put=PutPosition(
                        uic=long_put_data["uic"],
                        strike=long_put_data["strike"],
                        expiry=long_put_data["expiry"],
                        quantity=1,
                        entry_price=0.0,  # Unknown in dry run
                        delta=long_put_data["delta"],
                    ),
                    short_put=PutPosition(
                        uic=short_put_data["uic"],
                        strike=short_put_data["strike"],
                        expiry=short_put_data["expiry"],
                        quantity=-1,
                        entry_price=0.0,  # Unknown in dry run
                    ),
                    campaign_number=self.metrics.campaign_count + 1,
                    campaign_start_date=datetime.now().strftime("%Y-%m-%d"),
                )

                # Log the simulated trade to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action="[SIMULATED] ENTER_CAMPAIGN",
                        strike=f"{long_put_data['strike']}/{short_put_data['strike']}",
                        price=0.0,  # Unknown in dry run
                        delta=long_put_data.get("delta", -0.33),
                        pnl=0.0,
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Put Diagonal",
                        expiry_date=long_put_data["expiry"],
                        premium_received=0.0,  # Unknown in dry run
                        trade_reason=f"[DRY RUN] Campaign #{self.diagonal.campaign_number}"
                    )

                self.state = RPDState.POSITION_OPEN
                self._reset_failure_count()
                return True

            # LIVE: Place actual orders
            # Buy long put first
            long_result = self._place_protected_order(
                uic=long_put_data["uic"],
                buy_sell=BuySell.BUY,
                quantity=self.strategy_config.get("position_size", 1),
                description=f"Buy {self.underlying_symbol} long put ${long_put_data['strike']}",
            )

            if not long_result.get("success"):
                logger.error("Failed to buy long put")
                self._increment_failure_count("Long put order failed")
                self._set_action_cooldown("enter_campaign")
                return False

            # Sell short put
            short_result = self._place_protected_order(
                uic=short_put_data["uic"],
                buy_sell=BuySell.SELL,
                quantity=self.strategy_config.get("position_size", 1),
                description=f"Sell {self.underlying_symbol} short put ${short_put_data['strike']}",
            )

            if not short_result.get("success"):
                logger.error("Failed to sell short put - PARTIAL FILL detected")
                # We have a long put but no short - this is a partial fill
                self._handle_partial_fill("enter_campaign", ["long_put"])
                return False

            # Both orders filled - create diagonal position
            self.diagonal = DiagonalPosition(
                long_put=PutPosition(
                    position_id=long_result.get("position_id", ""),
                    uic=long_put_data["uic"],
                    strike=long_put_data["strike"],
                    expiry=long_put_data["expiry"],
                    quantity=self.strategy_config.get("position_size", 1),
                    entry_price=long_result.get("fill_price", 0),
                    delta=long_put_data["delta"],
                ),
                short_put=PutPosition(
                    position_id=short_result.get("position_id", ""),
                    uic=short_put_data["uic"],
                    strike=short_put_data["strike"],
                    expiry=short_put_data["expiry"],
                    quantity=-self.strategy_config.get("position_size", 1),
                    entry_price=short_result.get("fill_price", 0),
                ),
                campaign_number=self.metrics.campaign_count + 1,
                campaign_start_date=datetime.now().strftime("%Y-%m-%d"),
            )

            # Update metrics
            self.metrics.total_long_cost += long_result.get("fill_price", 0) * 100
            self.diagonal.total_premium_collected += short_result.get("fill_price", 0) * 100
            self.metrics.total_premium_collected += short_result.get("fill_price", 0) * 100

            self.state = RPDState.POSITION_OPEN
            self._reset_failure_count()

            logger.info("Campaign entered successfully!")
            logger.info(f"  Long put: ${long_put_data['strike']} @ ${long_result.get('fill_price', 0):.2f}")
            logger.info(f"  Short put: ${short_put_data['strike']} @ ${short_result.get('fill_price', 0):.2f}")

            # Log to Google Sheets
            if self.trade_logger:
                self.trade_logger.log_trade(
                    action="ENTER_CAMPAIGN",
                    strike=f"{long_put_data['strike']}/{short_put_data['strike']}",
                    price=long_result.get("fill_price", 0) - short_result.get("fill_price", 0),
                    delta=long_put_data.get("delta", -0.33),
                    pnl=0.0,
                    saxo_client=self.client,
                    underlying_price=self.current_price,
                    option_type="Put Diagonal",
                    expiry_date=long_put_data["expiry"],
                    premium_received=short_result.get("fill_price", 0),
                    trade_reason=f"Campaign #{self.diagonal.campaign_number}"
                )

            return True

        except Exception as e:
            logger.error(f"Error entering campaign: {e}")
            self._increment_failure_count(f"Enter campaign exception: {e}")
            self._set_action_cooldown("enter_campaign")
            return False

    def should_roll_short(self) -> Tuple[bool, RollType]:
        """
        Determine if short put should be rolled and what type of roll.

        Roll Types:
        - Vertical: Price >= short strike (bullish) - roll to new ATM
        - Horizontal: Price < short strike (bearish) - roll to SAME strike

        Returns:
            Tuple of (should_roll, roll_type)
        """
        if not self.diagonal or not self.diagonal.short_put:
            return False, RollType.VERTICAL

        short_dte = self.diagonal.short_put.dte

        # Roll when short put is at or near expiry (0-1 DTE)
        if short_dte > 1:
            return False, RollType.VERTICAL

        # Determine roll type based on price vs strike
        if self.current_price >= self.diagonal.short_put.strike:
            # Bullish: roll to new ATM
            return True, RollType.VERTICAL
        else:
            # Bearish: roll to same strike (capture intrinsic on rebound)
            return True, RollType.HORIZONTAL

    def execute_roll(self, roll_type: RollType) -> bool:
        """
        Execute a short put roll.

        Args:
            roll_type: VERTICAL (new ATM) or HORIZONTAL (same strike)

        Returns:
            True if roll completed successfully, False otherwise
        """
        if self._check_circuit_breaker():
            return False

        if self._is_action_on_cooldown("roll_short"):
            return False

        if not self.diagonal or not self.diagonal.short_put:
            logger.warning("No short put to roll")
            return False

        old_short = self.diagonal.short_put
        logger.info("=" * 50)
        logger.info(f"ROLLING SHORT PUT ({roll_type.value})")
        logger.info("=" * 50)
        logger.info(f"Current short: strike ${old_short.strike}, DTE {old_short.dte}")

        try:
            self.state = RPDState.ROLLING_SHORT

            # Step 1: Buy to close current short put
            if self.dry_run:
                logger.info(f"[DRY RUN] Would BUY TO CLOSE: UIC {old_short.uic}")
            else:
                close_result = self._place_protected_order(
                    uic=old_short.uic,
                    buy_sell=BuySell.BUY,
                    quantity=abs(old_short.quantity),
                    description=f"Buy to close short put ${old_short.strike}",
                )

                if not close_result.get("success"):
                    logger.error("Failed to close short put")
                    self._increment_failure_count("Failed to close short in roll")
                    self._set_action_cooldown("roll_short")
                    self.state = RPDState.POSITION_OPEN
                    return False

            # Step 2: Find new short put
            next_expiry = self.client.find_next_trading_day_expiry(self.underlying_uic)
            if not next_expiry:
                logger.error("Failed to find next trading day expiry")
                self._increment_failure_count("No expiry for roll")
                self._set_action_cooldown("roll_short")
                self.state = RPDState.POSITION_OPEN
                return False

            if roll_type == RollType.VERTICAL:
                # Roll to new ATM strike
                new_short_data = self.client.find_atm_put_for_expiry(
                    underlying_uic=self.underlying_uic,
                    underlying_price=self.current_price,
                    expiry_date=next_expiry["expiry"],
                )
                new_strike = new_short_data["strike"] if new_short_data else None
            else:
                # Roll to SAME strike (horizontal)
                # Find put at same strike for tomorrow's expiry
                new_strike = old_short.strike
                new_short_data = self._find_put_at_strike(
                    expiry_options=next_expiry["options"],
                    target_strike=new_strike,
                    expiry=next_expiry["expiry"],
                )

            if not new_short_data:
                logger.error(f"Failed to find new short put for {roll_type.value} roll")
                self._increment_failure_count("No new short found for roll")
                self._set_action_cooldown("roll_short")
                self.state = RPDState.POSITION_OPEN
                return False

            # Step 3: Sell new short put
            if self.dry_run:
                logger.info(f"[DRY RUN] Would SELL: strike ${new_short_data['strike']}, "
                           f"expiry {next_expiry['expiry'][:10]}")

                # Update diagonal in dry run
                old_strike = old_short.strike
                self.diagonal.short_put = PutPosition(
                    uic=new_short_data["uic"],
                    strike=new_short_data["strike"],
                    expiry=next_expiry["expiry"],
                    quantity=-1,
                )
                self.diagonal.roll_count += 1
                self.diagonal.last_roll_type = roll_type
                if roll_type == RollType.VERTICAL:
                    self.diagonal.vertical_roll_count += 1
                    self.metrics.vertical_rolls += 1
                else:
                    self.diagonal.horizontal_roll_count += 1
                    self.metrics.horizontal_rolls += 1
                self.metrics.roll_count += 1

                # Log the simulated roll to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action=f"[SIMULATED] ROLL_{roll_type.value.upper()}",
                        strike=f"{old_strike}->{new_short_data['strike']}",
                        price=0.0,  # Unknown in dry run
                        delta=0.0,
                        pnl=0.0,
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Short Put Roll",
                        expiry_date=next_expiry["expiry"],
                        premium_received=0.0,  # Unknown in dry run
                        trade_reason=f"[DRY RUN] {roll_type.value} roll #{self.diagonal.roll_count}"
                    )

                self.state = RPDState.POSITION_OPEN
                self._reset_failure_count()
                return True

            # LIVE: Sell new short put
            sell_result = self._place_protected_order(
                uic=new_short_data["uic"],
                buy_sell=BuySell.SELL,
                quantity=self.strategy_config.get("position_size", 1),
                description=f"Sell new short put ${new_short_data['strike']}",
            )

            if not sell_result.get("success"):
                logger.error("Failed to sell new short put - PARTIAL ROLL")
                self._handle_partial_fill("roll_short", ["close_old"])
                self.state = RPDState.POSITION_OPEN
                return False

            # Update diagonal
            self.diagonal.short_put = PutPosition(
                position_id=sell_result.get("position_id", ""),
                uic=new_short_data["uic"],
                strike=new_short_data["strike"],
                expiry=next_expiry["expiry"],
                quantity=-self.strategy_config.get("position_size", 1),
                entry_price=sell_result.get("fill_price", 0),
            )

            self.diagonal.roll_count += 1
            self.diagonal.last_roll_type = roll_type
            self.diagonal.total_premium_collected += sell_result.get("fill_price", 0) * 100
            if roll_type == RollType.VERTICAL:
                self.diagonal.vertical_roll_count += 1
                self.metrics.vertical_rolls += 1
            else:
                self.diagonal.horizontal_roll_count += 1
                self.metrics.horizontal_rolls += 1
            self.metrics.roll_count += 1
            self.metrics.total_premium_collected += sell_result.get("fill_price", 0) * 100

            self.state = RPDState.POSITION_OPEN
            self._reset_failure_count()

            logger.info(f"Roll completed successfully!")
            logger.info(f"  Old strike: ${old_short.strike} -> New strike: ${new_short_data['strike']}")
            logger.info(f"  Premium collected: ${sell_result.get('fill_price', 0):.2f}")

            # Log to Google Sheets
            if self.trade_logger:
                self.trade_logger.log_trade(
                    action=f"ROLL_{roll_type.value.upper()}",
                    strike=f"{old_short.strike}->{new_short_data['strike']}",
                    price=sell_result.get("fill_price", 0) - buy_result.get("fill_price", 0),
                    delta=-0.50,  # ATM put delta
                    pnl=sell_result.get("fill_price", 0) - buy_result.get("fill_price", 0),
                    saxo_client=self.client,
                    underlying_price=self.current_price,
                    option_type=f"Short Put Roll ({roll_type.value})",
                    expiry_date=next_expiry["expiry"][:10],
                    premium_received=sell_result.get("fill_price", 0),
                    trade_reason=f"Roll #{self.diagonal.roll_count}"
                )

            return True

        except Exception as e:
            logger.error(f"Error during roll: {e}")
            self._increment_failure_count(f"Roll exception: {e}")
            self._set_action_cooldown("roll_short")
            self.state = RPDState.POSITION_OPEN
            return False

    def _find_put_at_strike(
        self,
        expiry_options: List[Dict],
        target_strike: float,
        expiry: str
    ) -> Optional[Dict]:
        """Find a put option at a specific strike in the options list."""
        for opt in expiry_options:
            if opt.get("PutCall") == "Put":
                strike = opt.get("StrikePrice", 0)
                if abs(strike - target_strike) < 0.50:  # Allow small tolerance
                    today = datetime.now().date()
                    exp_date = datetime.strptime(expiry[:10], "%Y-%m-%d").date()
                    dte = (exp_date - today).days
                    return {
                        "uic": opt.get("Uic"),
                        "strike": strike,
                        "expiry": expiry,
                        "dte": dte,
                    }
        return None

    def should_roll_long_up(self) -> bool:
        """
        Check if long put should be rolled up.

        Roll long put up when delta drops below threshold (20 delta).

        Returns:
            True if long put should be rolled up
        """
        if not self.diagonal or not self.diagonal.long_put:
            return False

        threshold = abs(self.long_put_config.get("roll_delta_threshold", -0.20))
        current_delta = self.diagonal.long_delta_abs

        return current_delta < threshold

    def roll_long_up(self) -> bool:
        """
        Roll the long put up to a new 33 delta strike.

        Called when long put delta drops below 20.

        Returns:
            True if roll successful, False otherwise
        """
        if self._check_circuit_breaker():
            return False

        if self._is_action_on_cooldown("roll_long"):
            return False

        if not self.diagonal or not self.diagonal.long_put:
            return False

        old_long = self.diagonal.long_put
        logger.info("=" * 50)
        logger.info("ROLLING LONG PUT UP (delta too low)")
        logger.info("=" * 50)
        logger.info(f"Current long: strike ${old_long.strike}, delta {old_long.delta:.3f}")

        try:
            self.state = RPDState.ROLLING_LONG

            # Find new long put at 33 delta, same expiry
            # Note: This is a simplification - in production we might adjust DTE
            new_long_data = self.client.find_put_by_delta(
                underlying_uic=self.underlying_uic,
                underlying_price=self.current_price,
                target_delta=self.long_put_config["target_delta"],
                target_dte=old_long.dte,  # Keep same expiry
                delta_tolerance=self.long_put_config.get("delta_tolerance", 0.05),
            )

            if not new_long_data:
                logger.warning("No suitable long put found for roll up")
                self.state = RPDState.POSITION_OPEN
                return False

            if self.dry_run:
                logger.info(f"[DRY RUN] Would roll long from ${old_long.strike} to ${new_long_data['strike']}")
                old_strike = old_long.strike
                self.diagonal.long_put.strike = new_long_data["strike"]
                self.diagonal.long_put.delta = new_long_data["delta"]

                # Log the simulated long roll to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action="[SIMULATED] ROLL_LONG_UP",
                        strike=f"{old_strike}->{new_long_data['strike']}",
                        price=0.0,  # Unknown in dry run
                        delta=new_long_data["delta"],
                        pnl=0.0,
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Long Put Roll",
                        expiry_date=new_long_data.get("expiry", ""),
                        premium_received=0.0,
                        trade_reason=f"[DRY RUN] Long delta too low ({old_long.delta:.3f})"
                    )

                self.state = RPDState.POSITION_OPEN
                return True

            # LIVE: Close old, open new
            # Close old long
            close_result = self._place_protected_order(
                uic=old_long.uic,
                buy_sell=BuySell.SELL,
                quantity=old_long.quantity,
                description=f"Sell old long put ${old_long.strike}",
            )

            if not close_result.get("success"):
                logger.error("Failed to close old long put")
                self._increment_failure_count("Failed to close long in roll")
                self._set_action_cooldown("roll_long")
                self.state = RPDState.POSITION_OPEN
                return False

            # Open new long
            buy_result = self._place_protected_order(
                uic=new_long_data["uic"],
                buy_sell=BuySell.BUY,
                quantity=self.strategy_config.get("position_size", 1),
                description=f"Buy new long put ${new_long_data['strike']}",
            )

            if not buy_result.get("success"):
                logger.error("Failed to buy new long put - PARTIAL ROLL")
                self._handle_partial_fill("roll_long", ["close_old"])
                self.state = RPDState.POSITION_OPEN
                return False

            # Update diagonal
            self.diagonal.long_put = PutPosition(
                position_id=buy_result.get("position_id", ""),
                uic=new_long_data["uic"],
                strike=new_long_data["strike"],
                expiry=new_long_data["expiry"],
                quantity=self.strategy_config.get("position_size", 1),
                entry_price=buy_result.get("fill_price", 0),
                delta=new_long_data["delta"],
            )

            self.state = RPDState.POSITION_OPEN
            self._reset_failure_count()

            logger.info(f"Long put rolled up: ${old_long.strike} -> ${new_long_data['strike']}")

            return True

        except Exception as e:
            logger.error(f"Error rolling long up: {e}")
            self._increment_failure_count(f"Roll long exception: {e}")
            self._set_action_cooldown("roll_long")
            self.state = RPDState.POSITION_OPEN
            return False

    def should_close_campaign(self) -> Tuple[bool, str]:
        """
        Check if campaign should be closed.

        Close reasons:
        1. Long put near expiry (1-2 DTE)
        2. FOMC or major earnings approaching
        3. Emergency exit (price significantly below EMA)

        Returns:
            Tuple of (should_close, reason)
        """
        if not self.diagonal or not self.diagonal.long_put:
            return False, ""

        # Check long put DTE
        close_dte = self.management_config.get("campaign_close_dte", 2)
        if self.diagonal.long_dte <= close_dte:
            return True, f"Long put at {self.diagonal.long_dte} DTE (threshold: {close_dte})"

        # Check event risk
        should_close, event_reason = should_close_for_event(
            days_before_fomc=self.event_config.get("fomc_blackout_days", 1),
            days_before_earnings=self.event_config.get("earnings_blackout_days", 1),
        )
        if should_close:
            return True, f"Event risk: {event_reason}"

        # Check emergency exit (price significantly below EMA)
        if self.indicators and self.indicators.ema_9 > 0:
            distance_pct = (self.indicators.ema_9 - self.current_price) / self.indicators.ema_9 * 100
            if distance_pct > 3.0:  # Price 3%+ below EMA
                return True, f"Emergency: Price {distance_pct:.1f}% below 9 EMA"

        return False, ""

    def close_campaign(self, reason: str) -> bool:
        """
        Close the entire diagonal campaign.

        Args:
            reason: Reason for closing

        Returns:
            True if closed successfully, False otherwise
        """
        if self._check_circuit_breaker():
            return False

        if not self.diagonal:
            return False

        logger.info("=" * 50)
        logger.info("CLOSING CAMPAIGN")
        logger.info("=" * 50)
        logger.info(f"Reason: {reason}")

        try:
            self.state = RPDState.CLOSING_CAMPAIGN
            campaign_pnl = 0.0

            # Close short put if exists
            if self.diagonal.short_put:
                if self.dry_run:
                    logger.info(f"[DRY RUN] Would close short put ${self.diagonal.short_put.strike}")
                else:
                    result = self._place_protected_order(
                        uic=self.diagonal.short_put.uic,
                        buy_sell=BuySell.BUY,
                        quantity=abs(self.diagonal.short_put.quantity),
                        description=f"Close short put ${self.diagonal.short_put.strike}",
                    )
                    if result.get("success"):
                        # P&L = entry - exit (for short)
                        pnl = (self.diagonal.short_put.entry_price - result.get("fill_price", 0)) * 100
                        campaign_pnl += pnl

            # Close long put
            if self.diagonal.long_put:
                if self.dry_run:
                    logger.info(f"[DRY RUN] Would close long put ${self.diagonal.long_put.strike}")
                else:
                    result = self._place_protected_order(
                        uic=self.diagonal.long_put.uic,
                        buy_sell=BuySell.SELL,
                        quantity=self.diagonal.long_put.quantity,
                        description=f"Close long put ${self.diagonal.long_put.strike}",
                    )
                    if result.get("success"):
                        # P&L = exit - entry (for long)
                        pnl = (result.get("fill_price", 0) - self.diagonal.long_put.entry_price) * 100
                        campaign_pnl += pnl

            # Add premium collected to P&L
            campaign_pnl += self.diagonal.total_premium_collected

            # Update metrics
            self.metrics.realized_pnl += campaign_pnl
            self.metrics.record_campaign(campaign_pnl)
            self.metrics.update_drawdown(self.metrics.total_pnl)
            self.metrics.save_to_file()

            logger.info(f"Campaign closed - P&L: ${campaign_pnl:.2f}")
            logger.info(f"  Rolls: {self.diagonal.roll_count}")
            logger.info(f"  Premium collected: ${self.diagonal.total_premium_collected:.2f}")

            # Log to Google Sheets
            if self.trade_logger:
                action_prefix = "[SIMULATED] " if self.dry_run else ""
                self.trade_logger.log_trade(
                    action=f"{action_prefix}CLOSE_CAMPAIGN",
                    strike=f"{self.diagonal.long_put.strike}/{self.diagonal.short_put.strike if self.diagonal.short_put else 'N/A'}",
                    price=self.diagonal.total_premium_collected,
                    delta=self.diagonal.long_put.delta if self.diagonal.long_put else 0,
                    pnl=campaign_pnl,
                    saxo_client=self.client,
                    underlying_price=self.current_price,
                    option_type="Campaign Close",
                    expiry_date=self.diagonal.long_put.expiry if self.diagonal.long_put else None,
                    premium_received=self.diagonal.total_premium_collected,
                    trade_reason=f"{reason} - Campaign #{self.diagonal.campaign_number}, Rolls: {self.diagonal.roll_count}"
                )
                # Also log the campaign summary
                self.trade_logger.log_campaign({
                    "campaign_number": self.diagonal.campaign_number,
                    "start_date": self.diagonal.campaign_start_date,
                    "end_date": datetime.now().strftime("%Y-%m-%d"),
                    "duration_days": (datetime.now() - datetime.strptime(self.diagonal.campaign_start_date, "%Y-%m-%d")).days if self.diagonal.campaign_start_date else 0,
                    "long_put_strike": self.diagonal.long_put.strike if self.diagonal.long_put else 0,
                    "long_put_entry": self.diagonal.long_put.entry_price if self.diagonal.long_put else 0,
                    "long_put_exit": long_pnl if 'long_pnl' in locals() else 0,
                    "total_rolls": self.diagonal.roll_count,
                    "vertical_rolls": self.diagonal.vertical_roll_count,
                    "horizontal_rolls": self.diagonal.horizontal_roll_count,
                    "total_premium": self.diagonal.total_premium_collected,
                    "long_put_pnl": long_pnl if 'long_pnl' in locals() else 0,
                    "net_pnl": campaign_pnl,
                    "close_reason": reason,
                })

            # Clear diagonal
            self.diagonal = None

            # Set appropriate state
            if "Event risk" in reason:
                self.state = RPDState.WAITING_EVENT
            else:
                self.state = RPDState.IDLE

            self._reset_failure_count()
            return True

        except Exception as e:
            logger.error(f"Error closing campaign: {e}")
            self._increment_failure_count(f"Close campaign exception: {e}")
            self.state = RPDState.POSITION_OPEN
            return False

    # =========================================================================
    # ORDER MANAGEMENT
    # =========================================================================

    def _place_protected_order(
        self,
        uic: int,
        buy_sell: BuySell,
        quantity: int,
        description: str
    ) -> Dict:
        """
        Place an order with timeout and orphan protection.

        Args:
            uic: Instrument UIC
            buy_sell: BUY or SELL
            quantity: Number of contracts
            description: Order description for logging

        Returns:
            Dict with success status and fill info
        """
        logger.info(f"Placing order: {description}")

        try:
            # Get current quote for limit price
            quote = self.client.get_quote(uic, asset_type="StockOption")
            if not quote or "Quote" not in quote:
                return {"success": False, "error": "Failed to get quote"}

            q = quote["Quote"]
            if buy_sell == BuySell.BUY:
                # Buy at ask
                limit_price = q.get("Ask") or q.get("Mid", 0)
            else:
                # Sell at bid
                limit_price = q.get("Bid") or q.get("Mid", 0)

            if limit_price <= 0:
                return {"success": False, "error": "Invalid limit price"}

            # Place order with timeout
            result = self.client.place_limit_order_with_timeout(
                uic=uic,
                asset_type="StockOption",
                buy_sell=buy_sell.value,
                quantity=quantity,
                limit_price=limit_price,
                timeout_seconds=self._order_timeout,
            )

            if result.get("cancel_failed"):
                # Order couldn't be cancelled - orphaned
                self._track_orphaned_order(result.get("order_id", "unknown"))
                return {"success": False, "error": "Orphaned order"}

            if result.get("filled"):
                return {
                    "success": True,
                    "order_id": result.get("order_id"),
                    "position_id": result.get("position_id", ""),
                    "fill_price": result.get("fill_price", limit_price),
                }
            else:
                return {"success": False, "error": result.get("error", "Order not filled")}

        except Exception as e:
            logger.error(f"Order exception: {e}")
            return {"success": False, "error": str(e)}

    def _handle_partial_fill(self, action: str, filled_legs: List[str]) -> None:
        """
        Handle partial fill situation.

        This is a critical error - some legs filled, some didn't.
        Opens circuit breaker for manual resolution.

        Args:
            action: Action that caused partial fill
            filled_legs: List of legs that were filled
        """
        logger.critical("=" * 70)
        logger.critical("PARTIAL FILL DETECTED")
        logger.critical("=" * 70)
        logger.critical(f"Action: {action}")
        logger.critical(f"Filled legs: {filled_legs}")
        logger.critical("Manual intervention required to resolve position mismatch")

        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "event_type": "RPD_PARTIAL_FILL",
                "description": f"Partial fill during {action}",
                "action_taken": "HALT_TRADING",
                "result": f"Filled: {filled_legs}",
                "qqq_price": self.current_price,
            })

        self._open_circuit_breaker(f"Partial fill during {action}")

    # =========================================================================
    # MAIN STRATEGY LOOP
    # =========================================================================

    def run_iteration(self) -> None:
        """
        Run one iteration of the strategy loop.

        Called by main.py every 60 seconds during market hours.
        """
        # Safety check: circuit breaker
        if self._check_circuit_breaker():
            return

        # Update market data
        if not self.update_market_data():
            logger.warning("Failed to update market data")
            return

        logger.info(f"QQQ: ${self.current_price:.2f} | EMA9: ${self.current_ema_9:.2f} | "
                   f"State: {self.state.value}")

        # State machine
        if self.state == RPDState.IDLE or self.state == RPDState.WAITING_ENTRY:
            # Check entry conditions
            can_enter, reason = self.check_entry_conditions()
            if can_enter:
                self.enter_campaign()
            else:
                logger.info(f"Entry blocked: {reason}")
                self.state = RPDState.WAITING_ENTRY

        elif self.state == RPDState.WAITING_EVENT:
            # Check if event has passed
            approaching, event = is_event_approaching(days_ahead=1)
            if not approaching:
                logger.info("Event passed - checking entry conditions")
                self.state = RPDState.IDLE

        elif self.state == RPDState.POSITION_OPEN:
            # Check if we need to close campaign
            should_close, reason = self.should_close_campaign()
            if should_close:
                self.close_campaign(reason)
                return

            # Check if short put needs rolling
            if self.diagonal and self.diagonal.short_put:
                should_roll, roll_type = self.should_roll_short()
                if should_roll:
                    self.execute_roll(roll_type)
                    return
            elif self.diagonal and self.diagonal.has_long_only:
                # Short expired - need to sell new one
                logger.info("Short put expired - selling new one")
                self._sell_new_short()
                return

            # Check if long put needs rolling up
            if self.should_roll_long_up():
                self.roll_long_up()
                return

            # Update position prices
            self._update_position_prices()

    def _sell_new_short(self) -> bool:
        """Sell a new short put when the previous one expired."""
        if self._is_action_on_cooldown("sell_short"):
            return False

        try:
            next_expiry = self.client.find_next_trading_day_expiry(self.underlying_uic)
            if not next_expiry:
                return False

            short_put_data = self.client.find_atm_put_for_expiry(
                underlying_uic=self.underlying_uic,
                underlying_price=self.current_price,
                expiry_date=next_expiry["expiry"],
            )

            if not short_put_data:
                return False

            if self.dry_run:
                logger.info(f"[DRY RUN] Would sell new short: ${short_put_data['strike']}")
                self.diagonal.short_put = PutPosition(
                    uic=short_put_data["uic"],
                    strike=short_put_data["strike"],
                    expiry=short_put_data["expiry"],
                    quantity=-1,
                )
                return True

            result = self._place_protected_order(
                uic=short_put_data["uic"],
                buy_sell=BuySell.SELL,
                quantity=self.strategy_config.get("position_size", 1),
                description=f"Sell new short put ${short_put_data['strike']}",
            )

            if result.get("success"):
                self.diagonal.short_put = PutPosition(
                    position_id=result.get("position_id", ""),
                    uic=short_put_data["uic"],
                    strike=short_put_data["strike"],
                    expiry=short_put_data["expiry"],
                    quantity=-self.strategy_config.get("position_size", 1),
                    entry_price=result.get("fill_price", 0),
                )
                self.diagonal.total_premium_collected += result.get("fill_price", 0) * 100
                self.metrics.total_premium_collected += result.get("fill_price", 0) * 100
                logger.info(f"Sold new short put: ${short_put_data['strike']}")
                return True

            self._set_action_cooldown("sell_short")
            return False

        except Exception as e:
            logger.error(f"Error selling new short: {e}")
            return False

    def _update_position_prices(self) -> None:
        """Update current prices for position tracking."""
        if not self.diagonal:
            return

        try:
            if self.diagonal.long_put:
                quote = self.client.get_quote(
                    self.diagonal.long_put.uic,
                    asset_type="StockOption"
                )
                if quote and "Quote" in quote:
                    mid = quote["Quote"].get("Mid", 0)
                    self.diagonal.long_put.current_price = mid

            if self.diagonal.short_put:
                quote = self.client.get_quote(
                    self.diagonal.short_put.uic,
                    asset_type="StockOption"
                )
                if quote and "Quote" in quote:
                    mid = quote["Quote"].get("Mid", 0)
                    self.diagonal.short_put.current_price = mid

        except Exception as e:
            logger.debug(f"Error updating prices: {e}")

    def get_status_summary(self) -> Dict[str, Any]:
        """Get current strategy status for dashboard display."""
        summary = {
            "state": self.state.value,
            "qqq_price": self.current_price,
            "ema_9": self.current_ema_9,
            "circuit_breaker": self._circuit_breaker_open,
            "dry_run": self.dry_run,
            "total_pnl": self.metrics.total_pnl,
            "campaign_count": self.metrics.campaign_count,
            "roll_count": self.metrics.roll_count,
        }

        if self.diagonal:
            summary["position"] = {
                "long_strike": self.diagonal.long_put.strike if self.diagonal.long_put else None,
                "long_dte": self.diagonal.long_dte,
                "short_strike": self.diagonal.short_put.strike if self.diagonal.short_put else None,
                "short_dte": self.diagonal.short_dte,
                "campaign_rolls": self.diagonal.roll_count,
                "premium_collected": self.diagonal.total_premium_collected,
            }

        if self.indicators:
            summary["indicators"] = {
                "price_above_ema": self.indicators.price_above_ema,
                "macd_rising": self.indicators.macd_histogram_rising,
                "cci": self.indicators.cci,
                "entry_conditions_met": self.indicators.entry_conditions_met,
            }

        return summary

    # =========================================================================
    # GOOGLE SHEETS LOGGING METHODS
    # =========================================================================

    def log_daily_summary(self):
        """
        Log daily summary to Google Sheets Daily Summary tab.

        Rolling Put Diagonal specific columns:
        - QQQ Close, 9 EMA, MACD Histogram, CCI (technical indicators)
        - Roll Type, Short Premium, Campaign #
        - Daily P&L, Cumulative P&L
        - Long Put Delta, Entry Conditions Met
        """
        if not self.trade_logger:
            return

        # Get EUR conversion rate
        daily_pnl = self.metrics.realized_pnl  # Today's realized P&L
        daily_pnl_eur = daily_pnl
        try:
            rate = self.client.get_usd_to_account_currency_rate()
            if rate:
                daily_pnl_eur = daily_pnl * rate
        except Exception:
            pass

        # Determine today's roll type (if any)
        roll_type = ""
        if self.diagonal and self.diagonal.last_roll_type:
            roll_type = self.diagonal.last_roll_type.value

        # Get today's short premium (from current campaign)
        short_premium = 0.0
        if self.diagonal:
            short_premium = self.diagonal.total_premium_collected

        # Get long put delta
        long_delta = 0.0
        if self.diagonal and self.diagonal.long_put:
            long_delta = self.diagonal.long_put.delta or 0.0

        # Entry conditions from indicators
        entry_conditions_met = False
        if self.indicators:
            entry_conditions_met = self.indicators.entry_conditions_met

        # Rolling Put Diagonal specific summary - matches the rolling_put_diagonal Daily Summary columns
        summary = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "qqq_close": self.current_price,  # QQQ Close
            "ema_9": self.indicators.ema_9 if self.indicators else 0,
            "macd_histogram": self.indicators.macd_histogram if self.indicators else 0,
            "cci": self.indicators.cci if self.indicators else 0,
            "roll_type": roll_type,
            "short_premium": short_premium,
            "campaign_number": self.diagonal.campaign_number if self.diagonal else 0,
            "daily_pnl": daily_pnl,
            "daily_pnl_eur": daily_pnl_eur,
            "cumulative_pnl": self.metrics.total_pnl,
            "long_put_delta": long_delta,
            "entry_conditions_met": "Yes" if entry_conditions_met else "No",
            "notes": f"RPD - State: {self.state.value}, Rolls: {self.metrics.roll_count}"
        }

        self.trade_logger.log_daily_summary(summary)
        logger.info(
            f"Daily summary logged: QQQ=${self.current_price:.2f}, "
            f"Rolls={self.metrics.roll_count}, P&L=${self.metrics.total_pnl:.2f}"
        )

    def log_position_to_sheets(self):
        """
        Log current positions to Google Sheets Positions tab.

        Rolling Put Diagonal specific columns:
        - Position Type (Long Put / Short Put)
        - Strike, Expiry, DTE, Delta
        - Entry Price, Current Price, P&L
        - Campaign #, Premium Collected, Status
        """
        if not self.trade_logger or not self.diagonal:
            return

        positions = []

        # Long Put position
        if self.diagonal.long_put:
            long_pnl = 0.0
            if self.diagonal.long_put.current_price and self.diagonal.long_put.entry_price:
                long_pnl = (self.diagonal.long_put.current_price - self.diagonal.long_put.entry_price) * 100

            positions.append({
                "type": "Long Put (Protection)",
                "strike": self.diagonal.long_put.strike,
                "expiry": self.diagonal.long_put.expiry,
                "dte": self.diagonal.long_dte,
                "delta": self.diagonal.long_put.delta or 0,
                "entry_price": self.diagonal.long_put.entry_price or 0,
                "current_price": self.diagonal.long_put.current_price or 0,
                "pnl": long_pnl,
                "campaign_number": self.diagonal.campaign_number,
                "premium_collected": 0,  # Long put doesn't collect premium
                "status": "OPEN"
            })

        # Short Put position
        if self.diagonal.short_put:
            short_pnl = 0.0
            if self.diagonal.short_put.current_price and self.diagonal.short_put.entry_price:
                # Short position: profit when price decreases
                short_pnl = (self.diagonal.short_put.entry_price - self.diagonal.short_put.current_price) * 100

            positions.append({
                "type": "Short Put (Income)",
                "strike": self.diagonal.short_put.strike,
                "expiry": self.diagonal.short_put.expiry,
                "dte": self.diagonal.short_dte,
                "delta": -0.50,  # ATM put is approximately -0.50 delta
                "entry_price": self.diagonal.short_put.entry_price or 0,
                "current_price": self.diagonal.short_put.current_price or 0,
                "pnl": short_pnl,
                "campaign_number": self.diagonal.campaign_number,
                "premium_collected": self.diagonal.total_premium_collected,
                "status": "OPEN"
            })

        if positions:
            self.trade_logger.log_position_snapshot(positions)

    def log_performance_metrics(self):
        """
        Log performance metrics to Google Sheets Performance Metrics tab.

        Rolling Put Diagonal specific columns:
        - Total P&L, Realized/Unrealized
        - Total Premium Collected, Avg Daily Premium
        - Campaigns Completed, Avg Campaign P&L, Best/Worst Campaign
        - Total Rolls, Vertical Rolls, Horizontal Rolls
        - Win Rate, Max Drawdown, Avg Campaign Days
        """
        if not self.trade_logger:
            return

        # Calculate campaign stats
        campaigns_completed = self.metrics.campaign_count
        avg_campaign_pnl = 0.0
        if campaigns_completed > 0:
            avg_campaign_pnl = self.metrics.realized_pnl / campaigns_completed

        # Calculate average daily premium
        avg_daily_premium = 0.0
        if self.metrics.roll_count > 0:
            avg_daily_premium = self.metrics.total_premium_collected / self.metrics.roll_count

        # Calculate win rate
        winning_campaigns = self.metrics.winning_campaigns if hasattr(self.metrics, 'winning_campaigns') else 0
        losing_campaigns = self.metrics.losing_campaigns if hasattr(self.metrics, 'losing_campaigns') else 0
        total_settled = winning_campaigns + losing_campaigns
        win_rate = (winning_campaigns / total_settled * 100) if total_settled > 0 else 0.0

        # Unrealized P&L from current position
        unrealized_pnl = 0.0
        if self.diagonal:
            if self.diagonal.long_put and self.diagonal.long_put.current_price:
                unrealized_pnl += (self.diagonal.long_put.current_price - (self.diagonal.long_put.entry_price or 0)) * 100
            if self.diagonal.short_put and self.diagonal.short_put.current_price:
                unrealized_pnl += ((self.diagonal.short_put.entry_price or 0) - self.diagonal.short_put.current_price) * 100

        metrics = {
            # P&L
            "total_pnl": self.metrics.total_pnl,
            "realized_pnl": self.metrics.realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            # Premium Tracking
            "total_premium_collected": self.metrics.total_premium_collected,
            "avg_daily_premium": avg_daily_premium,
            # Campaign Stats
            "campaigns_completed": campaigns_completed,
            "avg_campaign_pnl": avg_campaign_pnl,
            "best_campaign": self.metrics.best_campaign if hasattr(self.metrics, 'best_campaign') else 0,
            "worst_campaign": self.metrics.worst_campaign if hasattr(self.metrics, 'worst_campaign') else 0,
            # Roll Stats
            "total_rolls": self.metrics.roll_count,
            "vertical_rolls": self.metrics.vertical_rolls,
            "horizontal_rolls": self.metrics.horizontal_rolls,
            # Stats
            "win_rate": win_rate,
            "max_drawdown": self.metrics.max_drawdown if hasattr(self.metrics, 'max_drawdown') else 0,
            "avg_campaign_days": self.metrics.avg_campaign_days if hasattr(self.metrics, 'avg_campaign_days') else 0
        }

        self.trade_logger.log_performance_metrics(
            period="Daily",
            metrics=metrics,
            saxo_client=self.client
        )

    def log_account_summary(self):
        """
        Log account summary to Google Sheets Account Summary tab.

        Rolling Put Diagonal specific columns:
        - QQQ Price, 9 EMA, MACD Histogram, CCI (market data + indicators)
        - Long Put: Strike, Expiry, DTE, Delta
        - Short Put: Strike, Expiry, Premium
        - Campaign #, Total Premium Collected, Unrealized P&L
        - State, Exchange Rate
        """
        if not self.trade_logger:
            return

        # Calculate unrealized P&L
        unrealized_pnl = 0.0
        if self.diagonal:
            if self.diagonal.long_put and self.diagonal.long_put.current_price:
                unrealized_pnl += (self.diagonal.long_put.current_price - (self.diagonal.long_put.entry_price or 0)) * 100
            if self.diagonal.short_put and self.diagonal.short_put.current_price:
                unrealized_pnl += ((self.diagonal.short_put.entry_price or 0) - self.diagonal.short_put.current_price) * 100

        # Short premium for current position
        short_premium = 0.0
        if self.diagonal and self.diagonal.short_put:
            short_premium = self.diagonal.short_put.entry_price or 0

        strategy_data = {
            # Market Data + Indicators
            "qqq_price": self.current_price,
            "ema_9": self.indicators.ema_9 if self.indicators else 0,
            "macd_histogram": self.indicators.macd_histogram if self.indicators else 0,
            "cci": self.indicators.cci if self.indicators else 0,
            # Long Put (Protection)
            "long_put_strike": self.diagonal.long_put.strike if self.diagonal and self.diagonal.long_put else 0,
            "long_put_expiry": self.diagonal.long_put.expiry if self.diagonal and self.diagonal.long_put else "",
            "long_put_dte": self.diagonal.long_dte if self.diagonal else 0,
            "long_put_delta": self.diagonal.long_put.delta if self.diagonal and self.diagonal.long_put else 0,
            # Short Put (Income)
            "short_put_strike": self.diagonal.short_put.strike if self.diagonal and self.diagonal.short_put else 0,
            "short_put_expiry": self.diagonal.short_put.expiry if self.diagonal and self.diagonal.short_put else "",
            "short_premium": short_premium,
            # Position Status
            "campaign_number": self.diagonal.campaign_number if self.diagonal else 0,
            "total_premium_collected": self.diagonal.total_premium_collected if self.diagonal else 0,
            "unrealized_pnl": unrealized_pnl,
            # Meta
            "state": self.state.value
        }

        self.trade_logger.log_account_summary(
            strategy_data=strategy_data,
            saxo_client=self.client,
            environment="LIVE" if not self.dry_run else "SIM"
        )
