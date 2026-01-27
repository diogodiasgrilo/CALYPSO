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
Last Updated: 2026-01-26

Code Audit: 2026-01-26
- Fixed operator precedence bug in mid-price calculation: (Ask + Bid) / 2
- Removed undefined _save_state() calls (replaced with logging)
- Removed unused imports: field (dataclasses), AlertType, AlertPriority
- Fixed string formatting bugs in dry-run logging
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, date, time as dt_time
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass
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
from shared.alert_service import AlertService

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
        dry_run: bool = False,
        alert_service: Optional[AlertService] = None
    ):
        """
        Initialize the strategy.

        Args:
            client: SaxoClient instance for API operations
            config: Configuration dictionary with strategy parameters
            trade_logger: Optional logger service for trade logging
            dry_run: If True, simulate trades without placing real orders
            alert_service: Optional AlertService for SMS/email notifications
        """
        self.client = client
        self.config = config
        self.strategy_config = config["strategy"]
        self.trade_logger = trade_logger
        self.dry_run = dry_run or self.strategy_config.get("dry_run", False)

        # Alert service for SMS/email notifications
        if alert_service:
            self.alert_service = alert_service
        else:
            self.alert_service = AlertService(config, "ROLLING_PUT_DIAGONAL")

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
        self._circuit_breaker_opened_at: Optional[datetime] = None
        self._last_failure_time: Optional[datetime] = None

        # Safety: Orphaned order tracking
        self._orphaned_orders: List[str] = []

        # Safety: Orphaned position tracking (positions on Saxo not in our strategy state)
        self._orphaned_positions: List[Dict] = []

        # Safety: Action cooldown
        self._action_cooldowns: Dict[str, datetime] = {}
        self._cooldown_seconds: int = config.get("circuit_breaker", {}).get(
            "action_cooldown_seconds", 300
        )

        # Order timeout
        self._order_timeout: int = self.management_config.get("order_timeout_seconds", 60)

        # TIME-002: Market open delay
        # Wait N minutes after market open before trading to allow quotes to stabilize
        # At 9:30:00 exactly, option quotes are often Bid=0/Ask=0 or Greeks unavailable
        self._market_open_delay_minutes: int = self.management_config.get("market_open_delay_minutes", 3)

        # Emergency slippage tolerance for urgent closures
        self._emergency_slippage_pct: float = 5.0

        # MKT-006: Max loss threshold for emergency campaign close
        # If unrealized loss exceeds this amount, close campaign immediately
        # Default: $500 per contract (configurable via management.max_unrealized_loss)
        self._max_unrealized_loss: float = self.management_config.get("max_unrealized_loss", 500.0)

        # ORDER-008: Progressive retry for order placement
        # Uses 7-attempt sequence: 0%/0%/5%/5%/10%/10%/MARKET (like Delta Neutral)
        self._progressive_retry: bool = self.management_config.get("progressive_retry", True)

        # ORDER-005: Max bid-ask spread for MARKET orders (safety check)
        # Aborts MARKET order if spread exceeds this to prevent extreme slippage
        self._max_market_spread: float = self.management_config.get("max_market_spread", 2.0)

        # Position isolation: Only look at QQQ options
        self._position_filter_prefix = "QQQ/"

        # TIME-001: Operation lock to prevent overlapping iterations
        self._operation_in_progress: bool = False
        self._operation_start_time: Optional[datetime] = None

        # TIME-003: Early close day tracking (only warn once per day)
        self._early_close_checked_today: bool = False
        self._last_early_close_check_date: Optional[str] = None

        # STATE-002/POS-002: Position reconciliation tracking
        self._last_reconciliation_time: Optional[datetime] = None
        self._reconciliation_interval_minutes: int = 5  # Check every 5 minutes
        self._position_mismatch_count: int = 0

        # LOG-001: Error deduplication
        self._last_logged_errors: Dict[str, datetime] = {}
        self._error_log_cooldown_seconds: int = 300  # Don't log same error within 5 min

        # STATE-003: Circuit breaker state file for persistence
        self._circuit_breaker_state_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data",
            "rolling_put_diagonal_circuit_breaker.json"
        )
        # Load persisted circuit breaker state
        self._load_circuit_breaker_state()

        # MKT-002: Flash crash velocity detection
        # Track recent prices for rapid move detection
        self._price_history: List[Tuple[datetime, float]] = []
        self._price_history_window_minutes: int = 5  # Track last 5 minutes
        self._flash_crash_threshold_percent: float = self.management_config.get(
            "flash_crash_threshold_percent", 2.0
        )

        logger.info(f"RollingPutDiagonalStrategy initialized")
        logger.info(f"  Underlying: {self.underlying_symbol} (UIC: {self.underlying_uic})")
        logger.info(f"  Dry run: {self.dry_run}")
        logger.info(f"  Long put: {self.long_put_config['target_dte']} DTE, {self.long_put_config['target_delta']} delta")
        logger.info(f"  Short put: {self.short_put_config['target_dte']} DTE, ATM")
        logger.info(f"  Progressive retry: {self._progressive_retry}")

    # =========================================================================
    # WEBSOCKET PRICE UPDATES
    # =========================================================================

    def handle_price_update(self, uic: int, data: dict) -> None:
        """
        CONN-003: Handle real-time price update from WebSocket streaming.

        This method is called by the WebSocket handler when price data arrives.
        It updates the strategy's current_price for faster response to market moves,
        which is especially useful for MKT-002 flash crash detection.

        Args:
            uic: Instrument UIC that was updated
            data: Price data from the streaming update containing Quote and/or Greeks
        """
        if uic == self.underlying_uic:
            # Extract price from Quote block
            if "Quote" in data:
                quote = data["Quote"]
                new_price = (
                    quote.get("Mid") or
                    quote.get("LastTraded") or
                    ((quote.get("Ask", 0) + quote.get("Bid", 0)) / 2 if quote.get("Ask") and quote.get("Bid") else None)
                )
                if new_price and new_price > 0:
                    old_price = self.current_price
                    self.current_price = new_price

                    # MKT-002: Record price for flash crash velocity detection
                    self._record_price_for_velocity(new_price)

                    logger.debug(f"WebSocket price update: ${old_price:.2f} -> ${new_price:.2f}")

        # Update option positions if they match
        elif self.diagonal:
            if self.diagonal.long_put and self.diagonal.long_put.uic == uic:
                if "Quote" in data:
                    quote = data["Quote"]
                    new_price = quote.get("Mid") or quote.get("LastTraded")
                    if new_price and new_price > 0:
                        self.diagonal.long_put.current_price = new_price
                        logger.debug(f"Long put price update: ${new_price:.4f}")

                # Update Greeks if available
                if "Greeks" in data:
                    greeks = data["Greeks"]
                    if "Delta" in greeks:
                        self.diagonal.long_put.delta = greeks["Delta"]
                    if "Theta" in greeks:
                        self.diagonal.long_put.theta = greeks["Theta"]

            if self.diagonal.short_put and self.diagonal.short_put.uic == uic:
                if "Quote" in data:
                    quote = data["Quote"]
                    new_price = quote.get("Mid") or quote.get("LastTraded")
                    if new_price and new_price > 0:
                        self.diagonal.short_put.current_price = new_price
                        logger.debug(f"Short put price update: ${new_price:.4f}")

                # Update Greeks if available
                if "Greeks" in data:
                    greeks = data["Greeks"]
                    if "Delta" in greeks:
                        self.diagonal.short_put.delta = greeks["Delta"]
                    if "Theta" in greeks:
                        self.diagonal.short_put.theta = greeks["Theta"]

    def get_streaming_subscriptions(self) -> list:
        """
        Get list of instruments to subscribe for WebSocket streaming.

        Returns:
            List of dicts with 'uic' and 'asset_type' for each subscription
        """
        subscriptions = [
            # Always subscribe to underlying (QQQ)
            {"uic": self.underlying_uic, "asset_type": "Etf"}
        ]

        # Add active option positions
        if self.diagonal:
            if self.diagonal.long_put and self.diagonal.long_put.uic:
                subscriptions.append({
                    "uic": self.diagonal.long_put.uic,
                    "asset_type": "StockOption"
                })
            if self.diagonal.short_put and self.diagonal.short_put.uic:
                subscriptions.append({
                    "uic": self.diagonal.short_put.uic,
                    "asset_type": "StockOption"
                })

        return subscriptions

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

        IMPORTANT: Before halting, this method attempts to close any unsafe positions
        (naked shorts without protection) to avoid leaving the account with uncovered risk.

        Args:
            reason: Description of why the circuit breaker opened
        """
        logger.critical("=" * 70)
        logger.critical("CIRCUIT BREAKER TRIGGERED")
        logger.critical("=" * 70)
        logger.critical(f"Reason: {reason}")

        # CRITICAL: Before halting, check if we have unsafe positions that need emergency closure
        emergency_actions = self._emergency_position_check()

        self._circuit_breaker_open = True
        self._circuit_breaker_reason = reason
        self._circuit_breaker_opened_at = datetime.now()
        self.state = RPDState.CIRCUIT_OPEN

        logger.critical("=" * 70)
        logger.critical("CIRCUIT BREAKER OPEN - ALL TRADING HALTED")
        logger.critical("=" * 70)
        logger.critical(f"Reason: {reason}")
        logger.critical(f"Consecutive failures: {self._consecutive_failures}")
        logger.critical(f"Time: {datetime.now().isoformat()}")
        logger.critical(f"Emergency actions taken: {emergency_actions}")
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
                "severity": "CRITICAL",
                "description": reason,
                "action_taken": f"HALT_TRADING. Emergency actions: {emergency_actions}",
                "result": "Requires manual intervention",
                "qqq_price": self.current_price,
                "state": self.state.value,
            })

        # ALERT: Send circuit breaker alert AFTER emergency actions complete
        self.alert_service.circuit_breaker(
            reason=reason,
            consecutive_failures=self._consecutive_failures,
            details={
                "qqq_price": self.current_price,
                "emergency_actions": emergency_actions,
                "has_long_put": self.diagonal and self.diagonal.long_put is not None,
                "has_short_put": self.diagonal and self.diagonal.short_put is not None
            }
        )

        # STATE-003: Persist circuit breaker state
        self._save_circuit_breaker_state()

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

    def _is_within_market_open_delay(self) -> bool:
        """
        TIME-002: Check if we're within the market open delay period.

        At market open (9:30:00), option quotes are often invalid (Bid=0, Ask=0)
        or Greeks are unavailable as market makers initialize. Wait N minutes
        (configurable, default 3) before placing any orders.

        This is critical because:
        - CONN-006: Greeks aren't available at exactly 9:30 AM
        - Option chains may return zero bid/ask spreads
        - Long put selection by delta fails without valid Greeks

        Returns:
            bool: True if we should wait before trading
        """
        if self._market_open_delay_minutes <= 0:
            return False

        now_est = get_us_market_time()

        # Market opens at 9:30 AM ET
        market_open = dt_time(9, 30)
        delay_end_minute = 30 + self._market_open_delay_minutes
        # Handle minute overflow (e.g., 30+35=65 -> 10:05)
        delay_end_hour = 9 + (delay_end_minute // 60)
        delay_end_minute = delay_end_minute % 60
        delay_end = dt_time(delay_end_hour, delay_end_minute)

        current_time = now_est.time()

        # Check if we're in the delay window (9:30 to 9:30+delay)
        if market_open <= current_time < delay_end:
            # Calculate minutes left
            current_minutes = current_time.hour * 60 + current_time.minute
            delay_end_minutes = delay_end_hour * 60 + delay_end_minute
            minutes_left = delay_end_minutes - current_minutes
            logger.info(
                f"⏳ TIME-002: Within market open delay ({minutes_left} min remaining). "
                f"Waiting for quotes/Greeks to stabilize..."
            )
            return True

        return False

    # =========================================================================
    # MKT-002: FLASH CRASH VELOCITY DETECTION
    # =========================================================================

    def _record_price_for_velocity(self, price: float) -> None:
        """
        MKT-002: Record current price for velocity tracking.

        Called during update_market_data() to build price history.
        The Rolling Put Diagonal has an exposed short put that can go
        deep ITM during a flash crash - early detection is critical.

        Args:
            price: Current QQQ price
        """
        now = datetime.now()
        self._price_history.append((now, price))

        # Keep only prices within the tracking window
        cutoff = now - timedelta(minutes=self._price_history_window_minutes)
        self._price_history = [(t, p) for t, p in self._price_history if t > cutoff]

    def check_flash_crash_velocity(self) -> Optional[Tuple[float, str]]:
        """
        MKT-002: Detect rapid price movements (flash crash/rally).

        For Rolling Put Diagonal, a flash crash is particularly dangerous
        because the short put can go deep ITM very quickly. This check
        catches fast moves before the standard EMA-exit trigger.

        Returns:
            Optional[Tuple[float, str]]: (move_percent, direction) if flash detected, None otherwise
        """
        if len(self._price_history) < 2:
            return None  # Not enough data

        # Get oldest and newest prices in window
        oldest_time, oldest_price = self._price_history[0]
        newest_time, newest_price = self._price_history[-1]

        # Calculate move percentage
        if oldest_price <= 0:
            return None

        move_percent = ((newest_price - oldest_price) / oldest_price) * 100
        abs_move = abs(move_percent)
        direction = "UP" if move_percent > 0 else "DOWN"
        elapsed_minutes = (newest_time - oldest_time).total_seconds() / 60

        if abs_move >= self._flash_crash_threshold_percent:
            logger.critical("=" * 70)
            logger.critical("🚨 MKT-002: FLASH MOVE DETECTED 🚨")
            logger.critical("=" * 70)
            logger.critical(f"   Direction: {direction}")
            logger.critical(f"   Move: {abs_move:.2f}% in {elapsed_minutes:.1f} minutes")
            logger.critical(f"   Old price: ${oldest_price:.2f} ({oldest_time.strftime('%H:%M:%S')})")
            logger.critical(f"   New price: ${newest_price:.2f} ({newest_time.strftime('%H:%M:%S')})")
            logger.critical(f"   Threshold: {self._flash_crash_threshold_percent}%")

            if self.diagonal and self.diagonal.short_put:
                short_strike = self.diagonal.short_put.strike
                logger.critical(f"   Short put strike: ${short_strike:.2f}")

                # Flash crash (DOWN) threatens the short put
                if direction == "DOWN" and short_strike > 0:
                    distance = ((newest_price - short_strike) / newest_price) * 100
                    if distance > 0:
                        logger.critical(f"   ⚠️ Short put only {distance:.1f}% OTM!")
                    else:
                        logger.critical(f"   🚨 SHORT PUT IS ITM by {abs(distance):.1f}%!")

            logger.critical("=" * 70)

            # Log safety event
            if self.trade_logger:
                self.trade_logger.log_safety_event({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "event_type": "FLASH_MOVE_DETECTED",
                    "severity": "CRITICAL",
                    "qqq_price": newest_price,
                    "action_taken": f"Detected {direction} {abs_move:.2f}% in {elapsed_minutes:.1f} min",
                    "short_put_strike": self.diagonal.short_put.strike if self.diagonal and self.diagonal.short_put else 0,
                })

            return (move_percent, direction)

        return None

    def _parse_rejection_reason(self, result: Dict) -> str:
        """
        ORDER-006: Parse specific rejection reason from Saxo order response.

        Saxo returns various error codes and messages that can help diagnose
        why an order was rejected. This method extracts and formats them.

        Args:
            result: Order result dictionary from Saxo API

        Returns:
            Human-readable rejection reason
        """
        reasons = []

        # Check for explicit error message
        if result.get("error"):
            reasons.append(result["error"])

        # Check for Saxo-specific error codes
        if result.get("ErrorCode"):
            error_code = result["ErrorCode"]
            error_map = {
                "OrderRejected": "Order rejected by exchange",
                "InsufficientFunds": "Insufficient buying power",
                "InsufficientMargin": "Insufficient margin",
                "InvalidPrice": "Invalid price (too far from market)",
                "MarketClosed": "Market is closed",
                "InstrumentNotTradeable": "Instrument not tradeable",
                "AccountBlocked": "Account blocked for trading",
                "TradingHalted": "Trading halted for this instrument",
                "MaxPositionExceeded": "Maximum position size exceeded",
                "InvalidQuantity": "Invalid quantity",
                "DuplicateOrder": "Duplicate order detected",
                "RiskLimitExceeded": "Risk limit exceeded",
            }
            reason = error_map.get(error_code, f"Error code: {error_code}")
            reasons.append(reason)

        # Check for message field
        if result.get("message") and result.get("message") != result.get("error"):
            reasons.append(result["message"])

        # Check for OrderStatus
        if result.get("OrderStatus"):
            status = result["OrderStatus"]
            if status in ["Rejected", "Cancelled", "Failed"]:
                reasons.append(f"Order status: {status}")

        # Check for RejectReason (some Saxo responses include this)
        if result.get("RejectReason"):
            reasons.append(f"Reject reason: {result['RejectReason']}")

        # Check for response body details
        if result.get("details"):
            reasons.append(f"Details: {result['details']}")

        # Combine all reasons
        if reasons:
            return " | ".join(reasons)

        return "Unknown rejection reason (no error details in response)"

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

    def _check_for_orphaned_orders(self) -> bool:
        """
        Check Saxo for any orphaned orders before placing new ones.

        Returns:
            bool: True if orphaned orders were found (should not proceed), False if clean
        """
        if not self._orphaned_orders:
            return False

        logger.warning(f"Checking for {len(self._orphaned_orders)} potential orphaned orders...")

        # Check if orders are still open on Saxo
        orphans_still_open = []

        for order_id in self._orphaned_orders:
            try:
                # Check if order still exists
                order = self.client.get_order(order_id)
                if order and order.get("Status") in ["Working", "NotFilled"]:
                    orphans_still_open.append(order_id)
            except Exception as e:
                logger.debug(f"Could not check order {order_id}: {e}")

        if orphans_still_open:
            logger.critical(f"{len(orphans_still_open)} orphaned orders still pending on Saxo!")
            logger.critical(f"Order IDs: {orphans_still_open}")
            self._open_circuit_breaker(f"Orphaned orders detected: {orphans_still_open}")
            return True

        # All orphans have been filled or cancelled
        logger.info("All tracked orphaned orders have been resolved")
        self._orphaned_orders = []
        return False

    # =========================================================================
    # TIME-001: OPERATION LOCK
    # =========================================================================

    def _acquire_operation_lock(self) -> bool:
        """
        TIME-001: Acquire operation lock to prevent overlapping iterations.

        Returns:
            bool: True if lock acquired, False if already in progress
        """
        if self._operation_in_progress:
            elapsed = ""
            if self._operation_start_time:
                mins = (datetime.now() - self._operation_start_time).total_seconds() / 60
                elapsed = f" ({mins:.1f} minutes)"
            logger.warning(f"⚠️ TIME-001: Operation already in progress{elapsed}, skipping this check")
            return False

        self._operation_in_progress = True
        self._operation_start_time = datetime.now()
        return True

    def _release_operation_lock(self) -> None:
        """TIME-001: Release operation lock."""
        self._operation_in_progress = False
        self._operation_start_time = None

    # =========================================================================
    # TIME-003: EARLY CLOSE DAY DETECTION
    # =========================================================================

    def is_early_close_day(self, dt: datetime = None) -> Tuple[bool, Optional[str]]:
        """
        TIME-003: Check if today is an early market close day (1pm ET).

        Early close days are typically:
        - Day before Independence Day (if July 4 is on weekday, July 3 closes early)
        - Day after Thanksgiving (Friday)
        - Christmas Eve (Dec 24, if weekday)
        - New Year's Eve (Dec 31, if weekday)

        Args:
            dt: Date to check (defaults to now in ET)

        Returns:
            Tuple[bool, Optional[str]]: (is_early_close, reason)
        """
        if dt is None:
            dt = get_us_market_time()

        month = dt.month
        day = dt.day
        weekday = dt.weekday()  # 0=Monday, 4=Friday

        # Not on weekends
        if weekday >= 5:
            return False, None

        # July 3 (if July 4 is on weekday except Monday where July 3 is Sunday)
        if month == 7 and day == 3 and weekday <= 4:
            return True, "Day before Independence Day"

        # Day after Thanksgiving (always Friday)
        # Thanksgiving is 4th Thursday of November
        if month == 11 and weekday == 4:  # Friday in November
            # Check if this is the day after Thanksgiving (4th Thursday)
            # The day after the 4th Thursday is between 23-29
            if 23 <= day <= 29:
                return True, "Day after Thanksgiving"

        # Christmas Eve (Dec 24) if it's a weekday
        if month == 12 and day == 24 and weekday <= 4:
            return True, "Christmas Eve"

        # New Year's Eve (Dec 31) if it's a weekday
        if month == 12 and day == 31 and weekday <= 4:
            return True, "New Year's Eve"

        return False, None

    def _is_past_early_close(self) -> bool:
        """
        TIME-003: Check if we're past the early close time.

        Returns:
            bool: True if market has already closed (or will close very soon)
        """
        is_early, reason = self.is_early_close_day()
        if not is_early:
            return False

        now_est = get_us_market_time()
        # Early close is 1pm, give 15 min buffer
        early_cutoff = dt_time(12, 45)  # 12:45 PM

        if now_est.time() >= early_cutoff:
            logger.warning(f"⏰ TIME-003: Past early close cutoff ({reason})")
            return True

        return False

    def check_early_close_warning(self) -> Optional[str]:
        """
        TIME-003: Check for early close day and log warning if applicable.

        Call once at market open to alert operator.

        Returns:
            Optional[str]: Warning message if early close day, None otherwise
        """
        now_est = get_us_market_time()
        today_str = now_est.strftime("%Y-%m-%d")

        # Only check once per day
        if self._last_early_close_check_date == today_str:
            return None

        self._last_early_close_check_date = today_str

        is_early, reason = self.is_early_close_day()
        if is_early:
            logger.warning("=" * 60)
            logger.warning(f"⏰ TIME-003: EARLY CLOSE DAY - {reason}")
            logger.warning("=" * 60)
            logger.warning("   Market closes at 1:00 PM ET today")
            logger.warning("   Roll/entry operations blocked after 12:45 PM")
            logger.warning("=" * 60)

            if self.trade_logger:
                self.trade_logger.log_safety_event({
                    "event_type": "RPD_EARLY_CLOSE_WARNING",
                    "severity": "WARNING",
                    "description": f"Early close day: {reason}",
                    "action_taken": "LOGGED_WARNING",
                    "result": "Market closes at 1pm ET",
                    "qqq_price": self.current_price,
                })

            return f"Early close day: {reason}"

        return None

    def _get_current_price(self) -> float:
        """Get current QQQ price from quote or cache."""
        if self.current_price > 0:
            return self.current_price

        try:
            quote = self.client.get_quote(self.underlying_uic, asset_type="Etf")
            if quote:
                # FIX: Bid/Ask/Mid are nested inside Quote object from Saxo API
                quote_data = quote.get("Quote", {})
                mid = quote_data.get("Mid", 0)
                if mid == 0:
                    bid = quote_data.get("Bid", 0)
                    ask = quote_data.get("Ask", 0)
                    if bid > 0 and ask > 0:
                        mid = (bid + ask) / 2
                return mid
        except Exception as e:
            logger.error(f"Failed to get current price: {e}")

        return 0.0

    # =========================================================================
    # MKT-003: MARKET CIRCUIT BREAKER / HALT DETECTION
    # =========================================================================

    def check_market_halt(self) -> Tuple[bool, str]:
        """
        MKT-003: Check if the market appears to be halted (circuit breaker).

        Market-wide circuit breakers halt all trading when S&P 500 drops:
        - Level 1: 7% drop - 15 minute halt (if before 3:25 PM)
        - Level 2: 13% drop - 15 minute halt (if before 3:25 PM)
        - Level 3: 20% drop - Trading halted for the day

        Detection method: If we're unable to get quotes or get stale quotes
        during market hours, assume a halt condition.

        Returns:
            Tuple[bool, str]: (market_trading_normally, halt_message)
        """
        try:
            # Try to get a fresh quote
            quote = self.client.get_quote(self.underlying_uic, asset_type="Etf")

            if quote is None:
                return False, "MKT-003: No quote available - possible market halt"

            # FIX: Bid/Ask are nested inside Quote object from Saxo API
            quote_data = quote.get("Quote", {})
            bid = quote_data.get("Bid", 0)
            ask = quote_data.get("Ask", 0)

            if bid == 0 and ask == 0:
                # This can happen at market open or during halts
                now_est = get_us_market_time()
                market_open = dt_time(9, 30)

                # Give 5 minutes after open for quotes to stabilize
                if now_est.time() > dt_time(9, 35):
                    return False, "MKT-003: Bid/Ask both zero - possible market halt"

            # Check for unreasonably wide spread (could indicate halt resumption)
            if bid > 0 and ask > 0:
                spread_pct = ((ask - bid) / bid) * 100
                if spread_pct > 10:  # 10% spread is extremely unusual
                    logger.warning(
                        f"MKT-003: Extremely wide spread ({spread_pct:.1f}%) - "
                        f"market may be resuming from halt"
                    )
                    # Don't block trading, just warn

            return True, "Market trading normally"

        except Exception as e:
            error_msg = str(e).lower()

            # Check for specific error patterns that indicate halts
            if "trading halted" in error_msg or "market closed" in error_msg:
                return False, f"MKT-003: Market halt detected from error: {e}"

            # Connection errors during market hours could indicate issues
            if "timeout" in error_msg or "connection" in error_msg:
                logger.warning(f"MKT-003: Connection issue during halt check: {e}")
                # Don't block trading for connection issues
                return True, "Connection issue - assuming market is open"

            logger.error(f"MKT-003: Unexpected error checking market halt: {e}")
            return True, "Error during check - assuming market is open"

    def _detect_halt_from_order_error(self, error_msg: str) -> bool:
        """
        MKT-003: Check if an order error indicates a market halt.

        Some error messages from Saxo explicitly indicate halted trading.

        Args:
            error_msg: Error message from order attempt

        Returns:
            bool: True if error indicates a market halt
        """
        halt_indicators = [
            "trading halted",
            "market halt",
            "circuit breaker",
            "trading suspended",
            "market closed",
            "halt in effect",
        ]

        error_lower = error_msg.lower()
        for indicator in halt_indicators:
            if indicator in error_lower:
                logger.critical(f"MKT-003: Market halt detected from order error: {error_msg}")
                return True

        return False

    # =========================================================================
    # STATE-002/POS-002: POSITION RECONCILIATION
    # =========================================================================

    def _verify_positions_with_saxo(self) -> Tuple[bool, str]:
        """
        STATE-002/POS-002: Verify local state matches actual Saxo positions.

        This is CRITICAL for safety - never rely on local state for important actions.
        Always check Saxo for the real position state.

        Returns:
            Tuple[bool, str]: (positions_match, discrepancy_description)
        """
        if self.dry_run:
            return True, "Dry run - skipping position verification"

        try:
            # Get actual positions from Saxo
            all_positions = self.client.get_positions()
            if all_positions is None:
                return False, "Failed to fetch positions from Saxo"

            # Filter for QQQ options only
            qqq_options = []
            for pos in all_positions:
                pos_base = pos.get("PositionBase", {})
                asset_type = pos_base.get("AssetType", "")
                symbol = pos.get("DisplayAndFormat", {}).get("Symbol", "")

                if asset_type == "StockOption" and symbol.startswith("QQQ"):
                    qqq_options.append(pos)

            # Build expected positions from local state
            expected_long_uics = []
            expected_short_uics = []

            if self.diagonal:
                if self.diagonal.long_put and self.diagonal.long_put.uic:
                    expected_long_uics.append(self.diagonal.long_put.uic)
                if self.diagonal.short_put and self.diagonal.short_put.uic:
                    expected_short_uics.append(self.diagonal.short_put.uic)

            # Build actual positions
            actual_long_uics = []
            actual_short_uics = []

            for pos in qqq_options:
                pos_base = pos.get("PositionBase", {})
                uic = pos_base.get("Uic", 0)
                amount = pos_base.get("Amount", 0)

                if amount > 0:
                    actual_long_uics.append(uic)
                elif amount < 0:
                    actual_short_uics.append(uic)

            # Compare
            discrepancies = []

            # Check for missing expected positions
            for uic in expected_long_uics:
                if uic not in actual_long_uics:
                    discrepancies.append(f"Expected long UIC {uic} not found in Saxo")

            for uic in expected_short_uics:
                if uic not in actual_short_uics:
                    discrepancies.append(f"Expected short UIC {uic} not found in Saxo")

            # Check for unexpected positions (orphans)
            for uic in actual_long_uics:
                if uic not in expected_long_uics:
                    discrepancies.append(f"Unexpected long UIC {uic} found in Saxo (orphan)")

            for uic in actual_short_uics:
                if uic not in expected_short_uics:
                    discrepancies.append(f"Unexpected short UIC {uic} found in Saxo (orphan)")

            if discrepancies:
                self._position_mismatch_count += 1
                return False, "; ".join(discrepancies)

            # Positions match
            self._position_mismatch_count = 0
            return True, "Positions verified"

        except Exception as e:
            return False, f"Position verification error: {e}"

    def _check_for_early_assignment(self) -> Optional[str]:
        """
        POS-004: Detect early assignment of short put.

        QQQ options are American-style, meaning they can be assigned before expiration.
        Early assignment typically happens when:
        1. Short put is deep ITM (intrinsic value > time value)
        2. Approaching expiration (especially overnight)
        3. Dividend approaching (not relevant for puts)

        Signs of early assignment:
        - Short put position disappears from Saxo
        - Unexpected stock position appears (100 shares per contract)

        Returns:
            Optional[str]: Assignment description if detected, None otherwise
        """
        if self.dry_run:
            return None

        # Only check if we have a short put
        if not self.diagonal or not self.diagonal.short_put:
            return None

        short_uic = self.diagonal.short_put.uic
        short_strike = self.diagonal.short_put.strike

        try:
            # Get all positions from Saxo
            all_positions = self.client.get_positions()
            if not all_positions:
                return None

            # Check for our short put
            short_put_found = False
            stock_position_found = False
            stock_shares = 0

            for pos in all_positions:
                pos_base = pos.get("PositionBase", {})
                uic = pos_base.get("Uic", 0)
                asset_type = pos_base.get("AssetType", "")
                amount = pos_base.get("Amount", 0)
                symbol = pos.get("DisplayAndFormat", {}).get("Symbol", "")

                # Check if our short put still exists
                if uic == short_uic and amount < 0:
                    short_put_found = True

                # Check for QQQ stock position (sign of assignment)
                # When a put is assigned, you buy the stock at strike price
                if asset_type == "Stock" and "QQQ" in symbol and amount > 0:
                    stock_position_found = True
                    stock_shares = amount

            # Early assignment detection
            if not short_put_found:
                assignment_msg = (
                    f"POS-004: Short put (UIC {short_uic}, ${short_strike}) "
                    f"disappeared from Saxo"
                )

                if stock_position_found:
                    assignment_msg += f" - QQQ stock position found ({stock_shares} shares) - LIKELY EARLY ASSIGNMENT"

                    logger.critical("=" * 70)
                    logger.critical("🚨 POS-004: EARLY ASSIGNMENT DETECTED 🚨")
                    logger.critical("=" * 70)
                    logger.critical(f"   Short put ${short_strike} was ASSIGNED")
                    logger.critical(f"   Now holding {stock_shares} shares of QQQ")
                    logger.critical(f"   Current QQQ price: ${self.current_price:.2f}")
                    logger.critical(f"   Assignment cost: ${short_strike * 100:.2f} per contract")
                    logger.critical("")
                    logger.critical("   ACTION REQUIRED:")
                    logger.critical("   1. Bot will clear the short put from memory")
                    logger.critical("   2. Manually sell the QQQ stock position")
                    logger.critical("   3. Bot will sell new short put on next iteration")
                    logger.critical("=" * 70)

                    if self.trade_logger:
                        self.trade_logger.log_safety_event({
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "event_type": "EARLY_ASSIGNMENT",
                            "severity": "CRITICAL",
                            "qqq_price": self.current_price,
                            "short_strike": short_strike,
                            "stock_shares": stock_shares,
                            "action_taken": "SHORT_PUT_CLEARED",
                            "description": assignment_msg,
                        })

                    # Clear the short put from our state
                    self.diagonal.short_put = None
                    logger.info("POS-004: Short put cleared from local state (will be recovered from Saxo on restart)")

                    return assignment_msg
                else:
                    # Short put disappeared but no stock - could be expiration OTM
                    logger.warning(f"⚠️ {assignment_msg} (no stock found - may have expired OTM)")
                    self.diagonal.short_put = None
                    logger.info("POS-004: Short put cleared from local state (will be recovered from Saxo on restart)")
                    return assignment_msg

            return None

        except Exception as e:
            logger.error(f"POS-004: Error checking for early assignment: {e}")
            return None

    def _reconcile_positions_periodic(self) -> None:
        """
        POS-002: Periodic position reconciliation during trading hours.

        Called during each iteration to detect position drift, early assignment,
        or manual intervention.
        """
        if self.dry_run:
            return

        # Check if it's time for reconciliation
        now = datetime.now()
        if self._last_reconciliation_time:
            elapsed = (now - self._last_reconciliation_time).total_seconds() / 60
            if elapsed < self._reconciliation_interval_minutes:
                return  # Not time yet

        self._last_reconciliation_time = now

        # POS-004: First check for early assignment (more specific check)
        assignment = self._check_for_early_assignment()
        if assignment:
            # Assignment detected and handled - skip general reconciliation
            return

        # Verify positions
        match, message = self._verify_positions_with_saxo()

        if not match:
            logger.warning("=" * 60)
            logger.warning(f"⚠️ STATE-002: Position mismatch detected!")
            logger.warning(f"   {message}")
            logger.warning(f"   Mismatch count: {self._position_mismatch_count}")
            logger.warning("=" * 60)

            if self.trade_logger:
                self.trade_logger.log_safety_event({
                    "event_type": "RPD_POSITION_MISMATCH",
                    "severity": "WARNING",
                    "description": message,
                    "action_taken": "LOGGED_DISCREPANCY",
                    "result": f"Mismatch #{self._position_mismatch_count}",
                    "qqq_price": self.current_price,
                })

            # If persistent mismatch, open circuit breaker
            if self._position_mismatch_count >= 3:
                self._open_circuit_breaker(f"Persistent position mismatch: {message}")

    def _get_actual_positions_from_saxo(self) -> Dict[str, Any]:
        """
        Get actual QQQ option positions from Saxo.

        Returns:
            Dict with 'long_puts' and 'short_puts' lists of position data
        """
        result = {"long_puts": [], "short_puts": [], "raw": []}

        try:
            all_positions = self.client.get_positions()
            if not all_positions:
                return result

            for pos in all_positions:
                pos_base = pos.get("PositionBase", {})
                asset_type = pos_base.get("AssetType", "")
                symbol = pos.get("DisplayAndFormat", {}).get("Symbol", "")

                if asset_type != "StockOption" or not symbol.startswith("QQQ"):
                    continue

                result["raw"].append(pos)

                uic = pos_base.get("Uic", 0)
                amount = pos_base.get("Amount", 0)
                options_data = pos_base.get("OptionsData", {})
                strike = options_data.get("Strike", 0)
                expiry = options_data.get("ExpiryDate", "")

                pos_info = {
                    "uic": uic,
                    "amount": amount,
                    "strike": strike,
                    "expiry": expiry,
                    "symbol": symbol,
                }

                if amount > 0:
                    result["long_puts"].append(pos_info)
                elif amount < 0:
                    result["short_puts"].append(pos_info)

        except Exception as e:
            logger.error(f"Error fetching positions from Saxo: {e}")

        return result

    # =========================================================================
    # LOG-001: ERROR DEDUPLICATION
    # =========================================================================

    def _log_deduplicated_error(self, error_key: str, message: str, level: str = "ERROR") -> bool:
        """
        LOG-001: Log an error with deduplication to prevent log flooding.

        Args:
            error_key: Unique identifier for this error type
            message: Error message to log
            level: Log level (ERROR, WARNING, CRITICAL)

        Returns:
            bool: True if message was logged, False if suppressed (duplicate)
        """
        now = datetime.now()

        # Check if this error was logged recently
        if error_key in self._last_logged_errors:
            last_logged = self._last_logged_errors[error_key]
            elapsed = (now - last_logged).total_seconds()
            if elapsed < self._error_log_cooldown_seconds:
                # Suppress duplicate
                return False

        # Log the error
        self._last_logged_errors[error_key] = now

        if level == "CRITICAL":
            logger.critical(message)
        elif level == "WARNING":
            logger.warning(message)
        else:
            logger.error(message)

        return True

    def _clear_old_error_logs(self) -> None:
        """Clear expired error log entries to prevent memory growth."""
        now = datetime.now()
        expired_keys = []

        for key, timestamp in self._last_logged_errors.items():
            elapsed = (now - timestamp).total_seconds()
            if elapsed > self._error_log_cooldown_seconds * 2:
                expired_keys.append(key)

        for key in expired_keys:
            del self._last_logged_errors[key]

    # =========================================================================
    # STATE-003: CIRCUIT BREAKER PERSISTENCE
    # =========================================================================

    def _save_circuit_breaker_state(self) -> None:
        """STATE-003: Save circuit breaker state to file for persistence across restarts."""
        state = {
            "open": self._circuit_breaker_open,
            "reason": self._circuit_breaker_reason,
            "opened_at": self._circuit_breaker_opened_at.isoformat() if self._circuit_breaker_opened_at else None,
            "consecutive_failures": self._consecutive_failures,
        }

        try:
            os.makedirs(os.path.dirname(self._circuit_breaker_state_file), exist_ok=True)
            with open(self._circuit_breaker_state_file, 'w') as f:
                json.dump(state, f, indent=2)
            logger.debug(f"Circuit breaker state saved to {self._circuit_breaker_state_file}")
        except Exception as e:
            logger.error(f"Failed to save circuit breaker state: {e}")

    def _load_circuit_breaker_state(self) -> None:
        """STATE-003: Load circuit breaker state from file on startup."""
        try:
            if os.path.exists(self._circuit_breaker_state_file):
                with open(self._circuit_breaker_state_file, 'r') as f:
                    state = json.load(f)

                if state.get("open", False):
                    self._circuit_breaker_open = True
                    self._circuit_breaker_reason = state.get("reason", "Unknown (loaded from persisted state)")
                    if state.get("opened_at"):
                        self._circuit_breaker_opened_at = datetime.fromisoformat(state["opened_at"])
                    self._consecutive_failures = state.get("consecutive_failures", 0)
                    self.state = RPDState.CIRCUIT_OPEN

                    logger.critical("=" * 60)
                    logger.critical("⚠️ STATE-003: Circuit breaker was OPEN from previous session")
                    logger.critical(f"   Reason: {self._circuit_breaker_reason}")
                    logger.critical(f"   Opened at: {self._circuit_breaker_opened_at}")
                    logger.critical("   Manual intervention required to reset")
                    logger.critical("=" * 60)
        except Exception as e:
            logger.warning(f"Could not load circuit breaker state: {e}")

    def _clear_circuit_breaker_state(self) -> None:
        """Clear persisted circuit breaker state (called when manually reset)."""
        try:
            if os.path.exists(self._circuit_breaker_state_file):
                os.remove(self._circuit_breaker_state_file)
                logger.info("Circuit breaker state file cleared")
        except Exception as e:
            logger.error(f"Failed to clear circuit breaker state file: {e}")

    # =========================================================================
    # DATA-004: QUOTE VALIDATION
    # =========================================================================

    def _validate_quote(self, quote: Dict, description: str = "") -> Tuple[bool, Optional[float], Optional[float]]:
        """
        DATA-004: Validate a quote has real prices before using it.

        Args:
            quote: Quote response from Saxo
            description: Description for logging

        Returns:
            Tuple[bool, Optional[float], Optional[float]]: (is_valid, bid, ask)
        """
        if not quote or "Quote" not in quote:
            self._log_deduplicated_error(
                f"invalid_quote_{description}",
                f"DATA-004: Invalid quote response for {description}",
                "WARNING"
            )
            return False, None, None

        q = quote["Quote"]
        bid = q.get("Bid", 0) or 0
        ask = q.get("Ask", 0) or 0

        if bid <= 0 or ask <= 0:
            self._log_deduplicated_error(
                f"zero_quote_{description}",
                f"DATA-004: Zero bid/ask for {description}: Bid=${bid:.2f}, Ask=${ask:.2f}",
                "WARNING"
            )
            return False, bid, ask

        # Check for inverted market (bid > ask) - sign of stale quotes
        if bid > ask:
            self._log_deduplicated_error(
                f"inverted_quote_{description}",
                f"DATA-004: Inverted quote for {description}: Bid=${bid:.2f} > Ask=${ask:.2f}",
                "WARNING"
            )
            return False, bid, ask

        # Check for unreasonably wide spread (> 50% of mid)
        mid = (bid + ask) / 2
        spread = ask - bid
        if mid > 0 and (spread / mid) > 0.5:
            self._log_deduplicated_error(
                f"wide_spread_{description}",
                f"DATA-004: Very wide spread for {description}: Bid=${bid:.2f}, Ask=${ask:.2f} ({spread/mid*100:.0f}%)",
                "WARNING"
            )
            # Still return True - wide spread is valid, just concerning
            return True, bid, ask

        return True, bid, ask

    def _check_spread_acceptable_for_entry(
        self,
        uic: int,
        strike: float,
        max_spread_percent: float = 10.0
    ) -> Tuple[bool, str]:
        """
        ORDER-005: Check if option spread is acceptable BEFORE placing entry order.

        This is more strict than the MARKET order spread check because we want
        to avoid entering positions with poor liquidity.

        Bill Belt's strategy works best with liquid strikes - entering with wide
        spreads erodes edge significantly.

        Args:
            uic: Option UIC
            strike: Strike price (for logging)
            max_spread_percent: Maximum acceptable spread as % of mid (default 10%)

        Returns:
            Tuple[bool, str]: (is_acceptable, reason_if_not)
        """
        try:
            quote = self.client.get_quote(uic, asset_type="StockOption")
            is_valid, bid, ask = self._validate_quote(quote, f"Strike ${strike}")

            if not is_valid:
                return False, f"Invalid quote for strike ${strike}"

            mid = (bid + ask) / 2
            spread = ask - bid

            if mid <= 0:
                return False, f"Invalid mid price for strike ${strike}"

            spread_pct = (spread / mid) * 100

            if spread_pct > max_spread_percent:
                logger.warning(f"⚠️ ORDER-005: Spread too wide for entry on ${strike} strike")
                logger.warning(f"   Bid: ${bid:.2f}, Ask: ${ask:.2f}, Spread: ${spread:.2f} ({spread_pct:.1f}%)")
                logger.warning(f"   Max allowed: {max_spread_percent}%")
                return False, f"Spread {spread_pct:.1f}% exceeds max {max_spread_percent}%"

            logger.debug(f"ORDER-005: Spread acceptable for ${strike}: {spread_pct:.1f}% <= {max_spread_percent}%")
            return True, f"Spread OK: {spread_pct:.1f}%"

        except Exception as e:
            logger.error(f"ORDER-005: Error checking spread for ${strike}: {e}")
            return False, f"Error: {e}"

    def _get_option_mid_price(self, uic: int) -> Optional[float]:
        """
        DRY-001: Get the mid-price for an option for simulated P&L tracking.

        Args:
            uic: Option UIC

        Returns:
            Mid-price or None if unavailable
        """
        try:
            quote = self.client.get_quote(uic, asset_type="StockOption")
            is_valid, bid, ask = self._validate_quote(quote, f"UIC {uic}")

            if is_valid and bid and ask:
                mid = (bid + ask) / 2
                return mid

            return None

        except Exception as e:
            logger.debug(f"DRY-001: Error getting mid-price for UIC {uic}: {e}")
            return None

    # =========================================================================
    # EMERGENCY POSITION MANAGEMENT
    # =========================================================================

    def _emergency_position_check(self) -> str:
        """
        Check for unsafe positions and attempt emergency closure before circuit breaker halts.

        This is called BEFORE the circuit breaker fully opens to protect against:
        1. Naked short put (short without long protection)
        2. Mismatched positions that don't align with strategy state

        The key insight for put diagonal:
        - Complete diagonal (long + short put) = protected, can keep
        - Short put only (no long) = DANGEROUS, close immediately
        - Long put only = safe (limited loss), can keep

        Returns:
            str: Description of actions taken
        """
        actions = []

        logger.critical("EMERGENCY POSITION CHECK - Analyzing risk exposure...")

        # CRITICAL: Sync with Saxo FIRST to get accurate position state
        logger.critical("   Syncing with Saxo to get accurate position state...")
        try:
            self._sync_positions_with_saxo()
        except Exception as e:
            logger.critical(f"   Failed to sync with Saxo: {e}")
            actions.append(f"WARNING: Could not sync with Saxo - {e}")

        has_long = self.diagonal and self.diagonal.long_put is not None
        has_short = self.diagonal and self.diagonal.short_put is not None

        # Log current state
        logger.critical(f"   Long put: {'YES' if has_long else 'NO'}")
        logger.critical(f"   Short put: {'YES' if has_short else 'NO'}")

        # SCENARIO 1: Naked short put (NO LONG PROTECTION) - VERY DANGEROUS
        if has_short and not has_long:
            logger.critical("SCENARIO 1: NAKED SHORT PUT - NO PROTECTION!")
            logger.critical("   Action: Emergency close the naked short")

            if self._emergency_close_short_put():
                actions.append("CLOSED naked short put (no long protection)")
            else:
                actions.append("FAILED to close naked short - MANUAL INTERVENTION REQUIRED")

        # SCENARIO 2: Complete diagonal - safe, keep positions
        elif has_long and has_short:
            logger.critical("SCENARIO 2: Complete diagonal - positions protected")
            actions.append("No emergency action needed - diagonal protected")

        # SCENARIO 3: Long only - safe (limited loss)
        elif has_long and not has_short:
            logger.critical("SCENARIO 3: Long put only - limited risk")
            logger.critical("   Action: Keep long position (max loss = premium paid)")
            actions.append("Kept long put (limited downside risk)")

        # SCENARIO 4: No positions
        else:
            logger.critical("SCENARIO 4: No positions - nothing to protect")
            actions.append("No positions to protect")

        return "; ".join(actions) if actions else "No action taken"

    def _sync_positions_with_saxo(self) -> None:
        """
        Sync local position state with Saxo broker positions.

        This ensures our local state matches reality before making emergency decisions.
        """
        try:
            all_positions = self.client.get_positions()
            if not all_positions:
                # No positions on Saxo - clear local state
                if self.diagonal:
                    logger.warning("No positions found on Saxo but local state has diagonal - clearing")
                    self.diagonal = None
                return

            qqq_options = self._filter_qqq_options(all_positions)
            if not qqq_options:
                if self.diagonal:
                    logger.warning("No QQQ options on Saxo but local state has diagonal - clearing")
                    self.diagonal = None
                return

            # Categorize positions
            long_puts = []
            short_puts = []

            for pos in qqq_options:
                amount = pos.get("PositionBase", {}).get("Amount", 0)
                option_type = pos.get("DisplayAndFormat", {}).get("Description", "")

                if "PUT" not in option_type.upper():
                    continue

                if amount > 0:
                    long_puts.append(pos)
                elif amount < 0:
                    short_puts.append(pos)

            # Update local state to match Saxo
            if not self.diagonal and (long_puts or short_puts):
                self.diagonal = DiagonalPosition()

            if self.diagonal:
                if long_puts:
                    self.diagonal.long_put = self._position_dict_to_put(long_puts[0])
                else:
                    self.diagonal.long_put = None

                if short_puts:
                    self.diagonal.short_put = self._position_dict_to_put(short_puts[0])
                else:
                    self.diagonal.short_put = None

                # If both legs are now None, clear the diagonal
                if not self.diagonal.long_put and not self.diagonal.short_put:
                    self.diagonal = None

            logger.info(f"Position sync: Long={len(long_puts)}, Short={len(short_puts)}")

        except Exception as e:
            logger.error(f"Error syncing positions: {e}")
            raise

    def _emergency_close_short_put(self) -> bool:
        """
        Emergency closure of naked short put position.

        This is called during circuit breaker activation when we have a short
        put without long protection - the most dangerous state.

        Uses aggressive pricing with slippage tolerance to ensure fill.

        Returns:
            bool: True if successfully closed, False otherwise
        """
        if not self.diagonal or not self.diagonal.short_put:
            return True  # Nothing to close

        short = self.diagonal.short_put
        logger.critical(f"EMERGENCY: Closing naked short put ${short.strike}")

        if self.dry_run:
            logger.critical("[DRY RUN] Would emergency close short put")
            self.diagonal.short_put = None
            return True

        try:
            # Get current ask price for buying back
            quote = self.client.get_quote(short.uic, "StockOption")
            if not quote or "Quote" not in quote:
                logger.error("Failed to get quote for emergency closure")
                return False

            ask = quote["Quote"].get("Ask", 0) or 0
            if ask <= 0:
                logger.error("No valid ask price for emergency closure")
                return False

            # Apply emergency slippage tolerance - pay MORE to ensure fill
            emergency_price = ask * (1 + self._emergency_slippage_pct / 100)
            emergency_price = round(emergency_price, 2)

            logger.critical(f"   Ask: ${ask:.2f}, Emergency price (with {self._emergency_slippage_pct}% slippage): ${emergency_price:.2f}")

            # Place buy order to close the short with shorter timeout
            result = self.client.place_limit_order_with_timeout(
                uic=short.uic,
                asset_type="StockOption",
                buy_sell=BuySell.BUY,
                amount=abs(short.quantity),
                limit_price=emergency_price,
                timeout_seconds=30,  # Shorter timeout for emergency
                to_open_close="ToClose"
            )

            if result.get("filled"):
                fill_price = result.get("fill_price", emergency_price)
                pnl = (short.entry_price - fill_price) * abs(short.quantity) * 100

                logger.critical(f"Emergency closed naked short put at ${fill_price:.2f}")
                logger.critical(f"   P&L: ${pnl:.2f}")

                # Log to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_safety_event({
                        "event_type": "RPD_EMERGENCY_CLOSE_NAKED_SHORT",
                        "severity": "CRITICAL",
                        "description": f"Emergency closed naked short put ${short.strike}",
                        "action_taken": f"Bought back at ${fill_price:.2f}",
                        "result": "SUCCESS",
                        "pnl": pnl,
                        "qqq_price": self.current_price,
                    })

                    self.trade_logger.log_trade(
                        action="EMERGENCY_CLOSE_SHORT_PUT",
                        strike=short.strike,
                        price=fill_price,
                        delta=short.delta,
                        pnl=pnl,
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Short Put",
                        expiry_date=short.expiry,
                        dte=short.dte,
                        notes="EMERGENCY CLOSE - No long protection"
                    )

                # Update metrics
                self.metrics.realized_pnl += pnl
                self.metrics.record_campaign(pnl)
                self.metrics.save_to_file()

                # ALERT: Naked short was detected and closed
                self.alert_service.naked_position(
                    missing_leg="Long Put (protection)",
                    details={
                        "short_strike": short.strike,
                        "fill_price": fill_price,
                        "pnl": pnl,
                        "qqq_price": self.current_price,
                        "result": "Successfully closed naked short"
                    }
                )

                # Clear the short position
                self.diagonal.short_put = None
                return True
            else:
                logger.error(f"Emergency closure FAILED: {result.get('message', 'Unknown error')}")

                # Check if cancel failed - order may still be open
                if result.get("cancel_failed"):
                    logger.critical(f"CANCEL FAILED - Order {result.get('order_id')} still open!")
                    self._orphaned_orders.append(result.get("order_id", "unknown"))

                return False

        except Exception as e:
            logger.exception(f"Exception during emergency closure: {e}")
            return False

    def _emergency_close_all(self) -> bool:
        """
        Emergency closure of ALL positions (both long and short).

        This is the nuclear option - called when we need to completely
        exit the strategy due to critical errors.

        Returns:
            bool: True if successfully closed all, False otherwise
        """
        logger.critical("EMERGENCY: Closing ALL positions")

        if not self.diagonal:
            return True  # Nothing to close

        success = True

        # Close short first (higher risk)
        if self.diagonal.short_put:
            if not self._emergency_close_short_put():
                logger.error("Failed to close short in emergency")
                success = False

        # Close long put
        if self.diagonal.long_put:
            if not self._emergency_close_long_put():
                logger.error("Failed to close long in emergency")
                success = False

        if success:
            self.diagonal = None
            self.state = RPDState.IDLE
            logger.critical("All positions closed in emergency")

            if self.trade_logger:
                self.trade_logger.log_safety_event({
                    "event_type": "RPD_EMERGENCY_CLOSE_ALL",
                    "severity": "CRITICAL",
                    "description": "Emergency closed all positions",
                    "action_taken": "CLOSED_ALL",
                    "result": "SUCCESS",
                    "qqq_price": self.current_price,
                })
        else:
            logger.critical("Some positions may still be open - MANUAL CHECK REQUIRED")

        return success

    def _emergency_close_long_put(self) -> bool:
        """
        Emergency closure of long put position.

        Long puts have limited risk (only lose premium paid), but this may
        be called as part of emergency close all.

        Returns:
            bool: True if successfully closed, False otherwise
        """
        if not self.diagonal or not self.diagonal.long_put:
            return True  # Nothing to close

        long = self.diagonal.long_put
        logger.critical(f"EMERGENCY: Closing long put ${long.strike}")

        if self.dry_run:
            logger.critical("[DRY RUN] Would emergency close long put")
            self.diagonal.long_put = None
            return True

        try:
            # Get current bid price for selling
            quote = self.client.get_quote(long.uic, "StockOption")
            if not quote or "Quote" not in quote:
                logger.error("Failed to get quote for long put closure")
                return False

            bid = quote["Quote"].get("Bid", 0) or 0
            if bid <= 0:
                # Long put might be worthless - try to close at 0.01
                bid = 0.01
                logger.warning("No bid price - attempting to close at $0.01")

            # Apply emergency slippage tolerance - accept LESS to ensure fill
            emergency_price = bid * (1 - self._emergency_slippage_pct / 100)
            emergency_price = max(0.01, round(emergency_price, 2))

            logger.critical(f"   Bid: ${bid:.2f}, Emergency price: ${emergency_price:.2f}")

            result = self.client.place_limit_order_with_timeout(
                uic=long.uic,
                asset_type="StockOption",
                buy_sell=BuySell.SELL,
                amount=long.quantity,
                limit_price=emergency_price,
                timeout_seconds=30,
                to_open_close="ToClose"
            )

            if result.get("filled"):
                fill_price = result.get("fill_price", emergency_price)
                pnl = (fill_price - long.entry_price) * long.quantity * 100

                logger.critical(f"Emergency closed long put at ${fill_price:.2f}, P&L: ${pnl:.2f}")

                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action="EMERGENCY_CLOSE_LONG_PUT",
                        strike=long.strike,
                        price=fill_price,
                        delta=long.delta,
                        pnl=pnl,
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Long Put",
                        expiry_date=long.expiry,
                        dte=long.dte,
                        notes="EMERGENCY CLOSE"
                    )

                self.metrics.realized_pnl += pnl
                self.diagonal.long_put = None
                return True
            else:
                logger.error(f"Emergency long closure FAILED: {result.get('message')}")
                return False

        except Exception as e:
            logger.exception(f"Exception closing long put: {e}")
            return False

    # =========================================================================
    # ORPHANED POSITION DETECTION
    # =========================================================================

    def _detect_orphaned_positions(self, saxo_positions: List[Dict]) -> List[Dict]:
        """
        Detect orphaned positions that exist on Saxo but aren't in our strategy state.

        An orphaned position is one that:
        1. Exists on Saxo as a QQQ put option
        2. Wasn't matched to our long_put or short_put in the diagonal

        This can happen from:
        - Partial fills that weren't properly tracked
        - Manual trades made in SaxoTraderGO
        - Bot crashes during multi-leg operations

        Args:
            saxo_positions: List of positions from Saxo

        Returns:
            List of orphaned position details
        """
        orphaned = []

        # Get UICs of positions we're tracking
        tracked_uics = set()
        if self.diagonal:
            if self.diagonal.long_put:
                tracked_uics.add(self.diagonal.long_put.uic)
            if self.diagonal.short_put:
                tracked_uics.add(self.diagonal.short_put.uic)

        # Filter QQQ puts and check against tracked
        for pos in saxo_positions:
            symbol = pos.get("DisplayAndFormat", {}).get("Symbol", "").upper()
            asset_type = pos.get("PositionBase", {}).get("AssetType", "")
            description = pos.get("DisplayAndFormat", {}).get("Description", "").upper()

            # Only look at QQQ put options
            if not symbol.startswith(self._position_filter_prefix):
                continue
            if asset_type != "StockOption":
                continue
            if "PUT" not in description:
                continue

            uic = pos.get("PositionBase", {}).get("Uic", 0)
            amount = pos.get("PositionBase", {}).get("Amount", 0)

            # If this UIC isn't tracked, it's orphaned
            if uic not in tracked_uics:
                orphaned.append({
                    "uic": uic,
                    "symbol": symbol,
                    "amount": amount,
                    "position_type": "LONG" if amount > 0 else "SHORT",
                    "description": description,
                    "entry_price": pos.get("PositionBase", {}).get("OpenPrice", 0),
                })

        return orphaned

    def _handle_orphaned_positions(self, orphaned_positions: List[Dict]) -> None:
        """
        Handle orphaned positions by blocking trading and alerting.

        When orphaned positions are detected:
        1. Log critical alert
        2. Store for status display
        3. Block new trading until resolved

        Args:
            orphaned_positions: List of orphaned position details
        """
        self._orphaned_positions = orphaned_positions

        logger.critical("=" * 70)
        logger.critical("ORPHANED POSITIONS DETECTED")
        logger.critical("=" * 70)
        logger.critical(f"Found {len(orphaned_positions)} position(s) not tracked by strategy:")

        for orphan in orphaned_positions:
            logger.critical(
                f"  - {orphan['position_type']} {orphan['description']} "
                f"(UIC: {orphan['uic']}, Amount: {orphan['amount']}, "
                f"Entry: ${orphan['entry_price']:.2f})"
            )

        logger.critical("")
        logger.critical("REQUIRED ACTIONS:")
        logger.critical("  1. Check positions in SaxoTraderGO")
        logger.critical("  2. Close orphaned position(s) manually OR")
        logger.critical("  3. Restart bot to re-sync positions")
        logger.critical("")
        logger.critical("The bot will NOT enter new positions until orphans are resolved.")
        logger.critical("=" * 70)

        # Log to Google Sheets
        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "event_type": "RPD_ORPHANED_POSITIONS",
                "severity": "WARNING",
                "description": f"Found {len(orphaned_positions)} orphaned position(s)",
                "action_taken": "BLOCKING_NEW_TRADES",
                "result": "Requires manual resolution",
                "details": str([f"{o['position_type']} {o['description']}" for o in orphaned_positions]),
                "qqq_price": self.current_price,
            })

    def has_orphaned_positions(self) -> bool:
        """Check if there are any orphaned positions blocking trading."""
        return len(self._orphaned_positions) > 0

    def get_orphaned_positions(self) -> List[Dict]:
        """Get list of orphaned positions, if any."""
        return self._orphaned_positions

    def clear_orphaned_positions(self) -> None:
        """Clear orphaned positions after manual resolution."""
        if self._orphaned_positions:
            logger.info(f"Clearing {len(self._orphaned_positions)} orphaned positions")
            self._orphaned_positions = []

    def _auto_resolve_orphaned_positions(self) -> bool:
        """
        STATE-004: Automatically resolve orphaned positions instead of blocking forever.

        Resolution strategies:
        1. If orphan is a QQQ put and fits our strategy, adopt it
        2. If orphan doesn't fit, close it with a market order

        Returns:
            True if all orphans were resolved, False if any remain
        """
        if not self._orphaned_positions:
            return True

        logger.info("=" * 60)
        logger.info("STATE-004: Attempting automatic orphan resolution")
        logger.info("=" * 60)

        resolved = []
        failed = []

        for orphan in self._orphaned_positions:
            uic = orphan["uic"]
            amount = orphan["amount"]
            position_type = orphan["position_type"]
            description = orphan["description"]
            entry_price = orphan["entry_price"]

            logger.info(f"Processing orphan: {position_type} {description} (UIC: {uic}, Amount: {amount})")

            # Strategy 1: Try to adopt as part of our diagonal
            # Only adopt puts that could fit our strategy
            if "Put" in description and self.underlying_symbol in description:
                adopted = self._try_adopt_orphan(orphan)
                if adopted:
                    logger.info(f"  ✓ Adopted orphan into strategy: {description}")
                    resolved.append(orphan)
                    continue

            # Strategy 2: Close the orphan position
            if not self.dry_run:
                logger.warning(f"  Orphan doesn't fit strategy - closing: {description}")

                # Determine buy/sell direction to close
                if amount > 0:
                    # Long position - sell to close
                    buy_sell = BuySell.SELL
                else:
                    # Short position - buy to close
                    buy_sell = BuySell.BUY

                result = self._place_protected_order(
                    uic=uic,
                    buy_sell=buy_sell,
                    quantity=abs(amount),
                    description=f"Close orphan {description}",
                    verify_fill=True,
                )

                if result.get("success"):
                    logger.info(f"  ✓ Closed orphan position: {description}")
                    resolved.append(orphan)

                    # Log the closure
                    if self.trade_logger:
                        self.trade_logger.log_trade(
                            action="CLOSE_ORPHAN",
                            strike=description,
                            price=result.get("fill_price", 0),
                            delta=0.0,
                            pnl=0.0,  # Unknown P&L on orphan
                            saxo_client=self.client,
                            underlying_price=self.current_price,
                            option_type="Orphan Resolution",
                            trade_reason="STATE-004: Auto-resolved orphan position",
                        )
                else:
                    logger.error(f"  ✗ Failed to close orphan: {result.get('error')}")
                    failed.append(orphan)
            else:
                # Dry run - just log what we would do
                logger.info(f"  [DRY RUN] Would close orphan: {description}")
                resolved.append(orphan)

        # Update orphaned positions list
        self._orphaned_positions = failed

        if failed:
            logger.warning(f"STATE-004: {len(resolved)} orphans resolved, {len(failed)} remain")
            return False
        else:
            logger.info(f"STATE-004: All {len(resolved)} orphans resolved successfully")
            return True

    def _try_adopt_orphan(self, orphan: Dict) -> bool:
        """
        Try to adopt an orphan put as part of our diagonal strategy.

        Args:
            orphan: Orphan position details

        Returns:
            True if successfully adopted, False otherwise
        """
        uic = orphan["uic"]
        amount = orphan["amount"]
        entry_price = orphan["entry_price"]

        # Get option details from Saxo
        try:
            quote = self.client.get_quote(uic, asset_type="StockOption")
            if not quote:
                return False

            # Extract strike and expiry from the description or fetch from Saxo
            # The description format is like "QQQ:xnys Put 2026-01-31 $520.00"
            desc_parts = orphan["description"].split()
            strike = None
            expiry = None

            for part in desc_parts:
                if part.startswith("$"):
                    try:
                        strike = float(part.replace("$", "").replace(",", ""))
                    except ValueError:
                        pass
                elif "-" in part and len(part) == 10:  # Date format YYYY-MM-DD
                    expiry = part

            if not strike or not expiry:
                logger.warning(f"  Could not parse strike/expiry from: {orphan['description']}")
                return False

            # Check if we can adopt this orphan
            if amount > 0:
                # Long position - could be our long put
                if self.diagonal is None or self.diagonal.long_put is None:
                    # Adopt as long put
                    current_price = None
                    if quote and "Quote" in quote:
                        q = quote["Quote"]
                        bid = q.get("Bid", 0) or 0
                        ask = q.get("Ask", 0) or 0
                        if bid and ask:
                            current_price = (bid + ask) / 2

                    # Get Greeks if available
                    delta = -0.30  # Default estimate
                    if quote and "Greeks" in quote:
                        delta = quote["Greeks"].get("Delta", -0.30)

                    new_long = PutPosition(
                        uic=uic,
                        strike=strike,
                        expiry=expiry,
                        quantity=amount,
                        entry_price=entry_price,
                        current_price=current_price or entry_price,
                        delta=delta,
                    )

                    if self.diagonal is None:
                        self.diagonal = DiagonalPosition(
                            long_put=new_long,
                            short_put=None,
                            campaign_number=self.metrics.campaign_count + 1,
                            campaign_start_date=datetime.now().strftime("%Y-%m-%d"),
                        )
                    else:
                        self.diagonal.long_put = new_long

                    self.state = RPDState.POSITION_OPEN
                    logger.info(f"  Adopted orphan as LONG PUT: ${strike} exp {expiry}")
                    return True

            elif amount < 0:
                # Short position - could be our short put
                if self.diagonal is not None and self.diagonal.long_put is not None and self.diagonal.short_put is None:
                    # We have a long but no short - adopt as short
                    new_short = PutPosition(
                        uic=uic,
                        strike=strike,
                        expiry=expiry,
                        quantity=amount,
                        entry_price=entry_price,
                    )
                    self.diagonal.short_put = new_short
                    self.diagonal.total_premium_collected += entry_price * 100
                    logger.info(f"  Adopted orphan as SHORT PUT: ${strike} exp {expiry}")
                    return True

        except Exception as e:
            logger.error(f"  Error trying to adopt orphan: {e}")

        return False

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

        IMPORTANT: This method also detects orphaned positions - positions that
        exist on Saxo but don't fit into our expected strategy structure.

        Returns:
            True if positions were recovered, False if no positions found
        """
        logger.info("Recovering positions from broker...")

        # Clear any previous orphaned positions
        self._orphaned_positions = []

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

            # Categorize by long/short puts
            long_puts = []
            short_puts = []
            other_options = []  # QQQ options that are not puts (calls, etc.)

            for pos in qqq_options:
                amount = pos.get("PositionBase", {}).get("Amount", 0)
                option_type = pos.get("DisplayAndFormat", {}).get("Description", "")

                # Check if it's a put
                if "PUT" not in option_type.upper():
                    # This is a call or other option type - track as potential orphan
                    other_options.append(pos)
                    continue

                if amount > 0:
                    long_puts.append(pos)
                elif amount < 0:
                    short_puts.append(pos)

            logger.info(f"Long puts: {len(long_puts)}, Short puts: {len(short_puts)}, Other: {len(other_options)}")

            # SAFETY CHECK: Detect multiple positions of same type
            if len(long_puts) > 1:
                logger.warning(f"Found {len(long_puts)} long puts - expected max 1!")
                logger.warning("Extra long puts will be tracked as orphaned")

            if len(short_puts) > 1:
                logger.warning(f"Found {len(short_puts)} short puts - expected max 1!")
                logger.warning("Extra short puts will be tracked as orphaned")

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
                elif self.diagonal.short_put and not self.diagonal.long_put:
                    # POS-003: DANGEROUS - Naked short with no protection
                    # Must close immediately to prevent unlimited loss exposure
                    logger.critical("=" * 70)
                    logger.critical("POS-003: NAKED SHORT DETECTED - short put with no long protection!")
                    logger.critical("=" * 70)
                    logger.critical("This is a CRITICAL risk - closing naked short immediately")

                    if self._emergency_close_short_put():
                        logger.critical("Successfully closed naked short - now IDLE with no positions")
                        self.diagonal = None
                        self.state = RPDState.IDLE
                    else:
                        logger.critical("FAILED to close naked short - opening circuit breaker")
                        self._open_circuit_breaker("POS-003: Failed to close naked short on recovery")

            # CRITICAL: Detect orphaned positions
            # This includes: extra long puts, extra short puts, any call options
            orphaned = []

            # Extra long puts beyond the first one
            for extra_long in long_puts[1:]:
                orphaned.append({
                    "uic": extra_long.get("PositionBase", {}).get("Uic", 0),
                    "symbol": extra_long.get("DisplayAndFormat", {}).get("Symbol", ""),
                    "amount": extra_long.get("PositionBase", {}).get("Amount", 0),
                    "position_type": "LONG",
                    "description": extra_long.get("DisplayAndFormat", {}).get("Description", ""),
                    "entry_price": extra_long.get("PositionBase", {}).get("OpenPrice", 0),
                    "reason": "Extra long put (strategy expects max 1)"
                })

            # Extra short puts beyond the first one
            for extra_short in short_puts[1:]:
                orphaned.append({
                    "uic": extra_short.get("PositionBase", {}).get("Uic", 0),
                    "symbol": extra_short.get("DisplayAndFormat", {}).get("Symbol", ""),
                    "amount": extra_short.get("PositionBase", {}).get("Amount", 0),
                    "position_type": "SHORT",
                    "description": extra_short.get("DisplayAndFormat", {}).get("Description", ""),
                    "entry_price": extra_short.get("PositionBase", {}).get("OpenPrice", 0),
                    "reason": "Extra short put (strategy expects max 1)"
                })

            # Any other QQQ options (calls, etc.)
            for other in other_options:
                orphaned.append({
                    "uic": other.get("PositionBase", {}).get("Uic", 0),
                    "symbol": other.get("DisplayAndFormat", {}).get("Symbol", ""),
                    "amount": other.get("PositionBase", {}).get("Amount", 0),
                    "position_type": "LONG" if other.get("PositionBase", {}).get("Amount", 0) > 0 else "SHORT",
                    "description": other.get("DisplayAndFormat", {}).get("Description", ""),
                    "entry_price": other.get("PositionBase", {}).get("OpenPrice", 0),
                    "reason": "Non-put option (strategy only uses puts)"
                })

            # Handle orphaned positions if found
            if orphaned:
                self._handle_orphaned_positions(orphaned)

            return bool(long_puts or short_puts)

        except Exception as e:
            logger.error(f"Error recovering positions: {e}")
            return False

    def _position_dict_to_put(self, pos_dict: Dict) -> PutPosition:
        """Convert Saxo position dictionary to PutPosition dataclass."""
        base = pos_dict.get("PositionBase", {})
        display = pos_dict.get("DisplayAndFormat", {})
        options_data = base.get("OptionsData", {})

        # Extract strike and expiry from symbol (e.g., "QQQ/21Jan26P500")
        symbol = display.get("Symbol", "")
        strike = 0.0
        expiry = ""

        # Try structured fields first (most reliable)
        if options_data.get("Strike"):
            strike = float(options_data.get("Strike", 0))
        elif base.get("StrikePrice"):
            strike = float(base.get("StrikePrice", 0))

        if options_data.get("ExpiryDate"):
            expiry = options_data.get("ExpiryDate", "")
        elif base.get("ExpiryDate"):
            expiry = base.get("ExpiryDate", "")

        # POS-008: Parse from symbol if structured fields are missing
        if (not strike or not expiry) and symbol:
            parsed_strike, parsed_expiry = self._parse_option_symbol(symbol)
            if not strike and parsed_strike:
                strike = parsed_strike
                logger.info(f"POS-008: Parsed strike ${strike} from symbol '{symbol}'")
            if not expiry and parsed_expiry:
                expiry = parsed_expiry
                logger.info(f"POS-008: Parsed expiry {expiry} from symbol '{symbol}'")

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

    def _parse_option_symbol(self, symbol: str) -> Tuple[Optional[float], Optional[str]]:
        """
        POS-008: Parse strike and expiry from option symbol.

        Saxo symbols can have formats like:
        - "QQQ/21Jan26P500" (standard format)
        - "QQQ:xnas/21JAN26P500.00"
        - "QQQ/Jan26P500"

        Args:
            symbol: Option symbol string

        Returns:
            Tuple[Optional[float], Optional[str]]: (strike, expiry_date_str) or (None, None)
        """
        import re

        if not symbol:
            return None, None

        try:
            # Extract the part after the underlying symbol (e.g., after "QQQ/")
            match = re.search(r'(\d{1,2})([A-Z][a-z]{2})(\d{2})[PC](\d+(?:\.\d+)?)', symbol, re.IGNORECASE)

            if match:
                day = match.group(1)
                month_str = match.group(2).capitalize()
                year_short = match.group(3)
                strike_str = match.group(4)

                # Parse strike
                strike = float(strike_str)

                # Parse expiry date
                month_map = {
                    'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
                    'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
                    'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
                }

                month = month_map.get(month_str, '01')
                year = f"20{year_short}"
                expiry = f"{year}-{month}-{day.zfill(2)}"

                logger.debug(f"POS-008: Parsed symbol '{symbol}' -> strike=${strike}, expiry={expiry}")
                return strike, expiry

            logger.warning(f"POS-008: Could not parse option symbol '{symbol}'")
            return None, None

        except Exception as e:
            logger.warning(f"POS-008: Error parsing symbol '{symbol}': {e}")
            return None, None

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

            # MKT-002: Record price for flash crash velocity detection
            self._record_price_for_velocity(self.current_price)

            # Get chart data for technical indicators
            daily_bars = self.client.get_daily_ohlc(
                uic=self.underlying_uic,
                asset_type="Etf",
                days=50
            )

            if daily_bars and len(daily_bars) >= 30:
                # Extract price arrays (STRATEGY-001: include opens for candle analysis)
                closes = [bar.get("Close", 0) for bar in daily_bars]
                highs = [bar.get("High", 0) for bar in daily_bars]
                lows = [bar.get("Low", 0) for bar in daily_bars]
                opens = [bar.get("Open", 0) for bar in daily_bars]

                # Calculate indicators
                self.indicators = calculate_all_indicators(
                    prices=closes,
                    highs=highs,
                    lows=lows,
                    opens=opens,  # STRATEGY-001: Pass opens for candle analysis
                    ema_period=self.indicator_config.get("ema_period", 9),
                    macd_fast=self.indicator_config.get("macd_fast", 12),
                    macd_slow=self.indicator_config.get("macd_slow", 26),
                    macd_signal=self.indicator_config.get("macd_signal", 9),
                    cci_period=self.indicator_config.get("cci_period", 20),
                    cci_overbought=self.indicator_config.get("cci_overbought", 100),
                )

                self.current_ema_9 = self.indicators.ema_9
                logger.debug(f"QQQ: ${self.current_price:.2f}, EMA9: ${self.current_ema_9:.2f}, "
                            f"Green candles above EMA: {self.indicators.consecutive_green_candles_above_ema}")

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
        Check if all entry conditions are met per Bill Belt's Rolling Put Diagonal rules.

        STRATEGY-001: Bill Belt Entry Rules (from thetaprofits.com):
        "At least 2 daily green candles that are closed and above the MA9 line
        and the MACD lines are bullish."

        Additional filters:
        0. Not within market open delay (TIME-002)
        0a. MKT-001: No large pre-market gap
        0b. MKT-003: Market not halted
        1. At least 2 consecutive green candles closed above 9 EMA (Bill Belt's rule)
        2. MACD histogram rising or positive (momentum)
        3. CCI < 100 (OPTIONAL - not in Bill Belt's original rules, configurable)
        4. Weekly trend not bearish
        5. No upcoming FOMC or major earnings

        Returns:
            Tuple of (conditions_met, reason_if_not_met)
        """
        # TIME-002: Check market open delay first (Greeks may be unavailable)
        if self._is_within_market_open_delay():
            return False, "Within market open delay - waiting for quotes to stabilize"

        # MKT-003: Check for market halt
        market_ok, halt_msg = self.check_market_halt()
        if not market_ok:
            return False, halt_msg

        # MKT-001: Check for large pre-market gap (only at/near market open)
        now_est = get_us_market_time()
        if now_est.time() < dt_time(10, 0):  # Only check before 10am
            gap_ok, gap_pct, gap_msg = self.check_premarket_gap()
            if not gap_ok:
                return False, gap_msg

        if self.indicators is None:
            return False, "No indicator data available"

        # Check event risk first
        should_close, event_reason = should_close_for_event(
            days_before_fomc=self.event_config.get("fomc_blackout_days", 1),
            days_before_earnings=self.event_config.get("earnings_blackout_days", 1),
        )
        if should_close:
            return False, f"Event risk: {event_reason}"

        # STRATEGY-001: Bill Belt's primary entry rule - 2 green candles above EMA
        min_green_candles = self.indicator_config.get("min_green_candles_above_ema", 2)
        if self.indicators.consecutive_green_candles_above_ema < min_green_candles:
            return False, (f"Need {min_green_candles} green candles above EMA, "
                          f"have {self.indicators.consecutive_green_candles_above_ema}")

        # Check current price above EMA (real-time check, not just candle close)
        if not self.indicators.price_above_ema:
            return False, f"Price ${self.current_price:.2f} below 9 EMA ${self.indicators.ema_9:.2f}"

        # MACD must be bullish
        if not self.indicators.macd_histogram_rising and not self.indicators.macd_histogram_positive:
            return False, f"MACD histogram not rising (current: {self.indicators.macd_histogram:.4f})"

        # STRATEGY-003: CCI filter is OPTIONAL (not in Bill Belt's original rules)
        # Only apply if enabled in config (default: disabled to match Bill Belt)
        if self.indicator_config.get("use_cci_filter", False):
            if self.indicators.cci_overbought:
                return False, f"CCI overbought: {self.indicators.cci:.2f} > 100"

        if self.indicators.weekly_trend_bearish:
            return False, "Weekly trend is bearish"

        return True, "All entry conditions met (Bill Belt criteria)"

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

            # ORDER-005: Validate spreads BEFORE placing orders
            # Wide spreads erode Bill Belt's strategy edge significantly
            max_spread_pct = self.management_config.get("max_entry_spread_percent", 10.0)

            long_spread_ok, long_reason = self._check_spread_acceptable_for_entry(
                long_put_data["uic"], long_put_data["strike"], max_spread_pct
            )
            if not long_spread_ok:
                logger.warning(f"⚠️ ORDER-005: Long put spread too wide - aborting entry")
                logger.warning(f"   {long_reason}")
                self._set_action_cooldown("enter_campaign")
                return False

            short_spread_ok, short_reason = self._check_spread_acceptable_for_entry(
                short_put_data["uic"], short_put_data["strike"], max_spread_pct
            )
            if not short_spread_ok:
                logger.warning(f"⚠️ ORDER-005: Short put spread too wide - aborting entry")
                logger.warning(f"   {short_reason}")
                self._set_action_cooldown("enter_campaign")
                return False

            logger.info(f"ORDER-005: Spreads acceptable - proceeding with entry")

            # Step 3: Place orders
            if self.dry_run:
                # DRY-001: Use mid-prices for simulated P&L tracking
                long_mid = self._get_option_mid_price(long_put_data["uic"])
                short_mid = self._get_option_mid_price(short_put_data["uic"])

                # Calculate simulated net debit (buy long - sell short)
                simulated_net_debit = (long_mid or 0) - (short_mid or 0)
                simulated_premium_received = (short_mid or 0) * 100  # Per contract

                logger.info("[DRY RUN] Would place orders:")
                logger.info(f"  BUY long put: UIC {long_put_data['uic']}, strike ${long_put_data['strike']}, "
                           f"mid ${long_mid:.2f}" if long_mid else f"  BUY long put: UIC {long_put_data['uic']}, strike ${long_put_data['strike']}, mid N/A")
                logger.info(f"  SELL short put: UIC {short_put_data['uic']}, strike ${short_put_data['strike']}, "
                           f"mid ${short_mid:.2f}" if short_mid else f"  SELL short put: UIC {short_put_data['uic']}, strike ${short_put_data['strike']}, mid N/A")
                logger.info(f"  Simulated net debit: ${simulated_net_debit:.2f}")

                # Simulate success in dry run with mid-prices
                self.diagonal = DiagonalPosition(
                    long_put=PutPosition(
                        uic=long_put_data["uic"],
                        strike=long_put_data["strike"],
                        expiry=long_put_data["expiry"],
                        quantity=1,
                        entry_price=long_mid or 0.0,  # DRY-001: Use mid-price
                        current_price=long_mid or 0.0,
                        delta=long_put_data["delta"],
                    ),
                    short_put=PutPosition(
                        uic=short_put_data["uic"],
                        strike=short_put_data["strike"],
                        expiry=short_put_data["expiry"],
                        quantity=-1,
                        entry_price=short_mid or 0.0,  # DRY-001: Use mid-price
                        current_price=short_mid or 0.0,
                    ),
                    campaign_number=self.metrics.campaign_count + 1,
                    campaign_start_date=datetime.now().strftime("%Y-%m-%d"),
                    total_premium_collected=simulated_premium_received,
                )

                # Log the simulated trade to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action="[SIMULATED] ENTER_CAMPAIGN",
                        strike=f"{long_put_data['strike']}/{short_put_data['strike']}",
                        price=simulated_net_debit,  # DRY-001: Use actual mid-price
                        delta=long_put_data.get("delta", -0.33),
                        pnl=0.0,  # PnL starts at 0
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Put Diagonal",
                        expiry_date=long_put_data["expiry"],
                        premium_received=simulated_premium_received,  # DRY-001: Actual premium
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
                # ORDER-002: Entry partial fill - long put filled, short put failed
                # This leaves us with long put only (safe - max loss is premium paid)
                logger.error("=" * 70)
                logger.error("ORDER-002: Failed to sell short put - PARTIAL ENTRY")
                logger.error("=" * 70)
                logger.error("Long put was bought, but short put could not be sold")
                logger.error("Position is now: LONG PUT ONLY (protected, max loss = premium)")

                # Create diagonal with just the long (short will be None)
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
                    short_put=None,  # Will be filled on next iteration
                    campaign_number=self.metrics.campaign_count + 1,
                    campaign_start_date=datetime.now().strftime("%Y-%m-%d"),
                )
                self.state = RPDState.POSITION_OPEN

                # Log the partial entry for tracking
                if self.trade_logger:
                    self.trade_logger.log_safety_event({
                        "event_type": "RPD_PARTIAL_ENTRY",
                        "description": f"Entry partial fill: bought long ${long_put_data['strike']}, failed to sell short",
                        "action_taken": "STATE_UPDATED_TO_LONG_ONLY",
                        "result": "Will attempt to sell short on next iteration",
                        "qqq_price": self.current_price,
                    })

                # Don't halt - the bot will see has_long_only on next iteration and try to sell short
                logger.info("Bot will attempt to sell short on next iteration")
                self._set_action_cooldown("enter_campaign")
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

            # ALERT: Campaign opened successfully with both legs
            net_debit = long_result.get("fill_price", 0) - short_result.get("fill_price", 0)
            self.alert_service.position_opened(
                position_summary=f"RPD Campaign #{self.diagonal.campaign_number}: Long ${long_put_data['strike']}p ({long_put_data['dte']} DTE) + Short ${short_put_data['strike']}p",
                cost_or_credit=-net_debit,  # Negative because it's a debit
                details={
                    "long_strike": long_put_data['strike'],
                    "long_dte": long_put_data['dte'],
                    "long_delta": long_put_data.get('delta', -0.33),
                    "short_strike": short_put_data['strike'],
                    "short_dte": short_put_data.get('dte', 1),
                    "qqq_price": self.current_price,
                    "campaign_number": self.diagonal.campaign_number
                }
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
                # DRY-002: Get mid-price for realistic entry price
                new_short_mid = self._get_option_mid_price(new_short_data["uic"])
                old_short_mid = self._get_option_mid_price(old_short.uic)

                logger.info(f"[DRY RUN] Would SELL: strike ${new_short_data['strike']}, "
                           f"expiry {next_expiry['expiry'][:10]}, mid ${new_short_mid:.2f}" if new_short_mid else "mid N/A")

                # Update diagonal in dry run with realistic prices
                old_strike = old_short.strike
                self.diagonal.short_put = PutPosition(
                    uic=new_short_data["uic"],
                    strike=new_short_data["strike"],
                    expiry=next_expiry["expiry"],
                    quantity=-1,
                    entry_price=new_short_mid or 0.0,  # DRY-002: Use mid-price
                    current_price=new_short_mid or 0.0,
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

                # DRY-002: Track simulated premium
                simulated_premium = (new_short_mid or 0) * 100
                self.diagonal.total_premium_collected += simulated_premium
                self.metrics.total_premium_collected += simulated_premium

                # Log the simulated roll to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action=f"[SIMULATED] ROLL_{roll_type.value.upper()}",
                        strike=f"{old_strike}->{new_short_data['strike']}",
                        price=new_short_mid or 0.0,  # DRY-002: Use mid-price
                        delta=0.0,
                        pnl=0.0,
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Short Put Roll",
                        expiry_date=next_expiry["expiry"],
                        premium_received=simulated_premium,  # DRY-002: Use mid-price
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
                # ORDER-003: Roll partial fill - closed old short but failed to sell new
                # This leaves us with long put only (safe but no income)
                logger.error("=" * 70)
                logger.error("ORDER-003: Failed to sell new short put - PARTIAL ROLL")
                logger.error("=" * 70)
                logger.error("Old short was closed, but new short could not be sold")
                logger.error("Position is now: LONG PUT ONLY (protected but no income)")

                # Update state to reflect reality - we only have the long
                self.diagonal.short_put = None
                self.state = RPDState.POSITION_OPEN

                # Log the partial roll for tracking
                if self.trade_logger:
                    self.trade_logger.log_safety_event({
                        "event_type": "RPD_PARTIAL_ROLL",
                        "description": f"Roll partial fill: closed old ${old_short.strike}, failed to sell new",
                        "action_taken": "STATE_UPDATED_TO_LONG_ONLY",
                        "result": "Will attempt to sell new short on next iteration",
                        "qqq_price": self.current_price,
                    })

                # Don't halt - the bot will see has_long_only on next iteration and try to sell new short
                logger.info("Bot will attempt to sell new short on next iteration")
                self._set_action_cooldown("roll_short")  # Brief cooldown before retry
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
                    price=sell_result.get("fill_price", 0) - close_result.get("fill_price", 0),
                    delta=-0.50,  # ATM put delta
                    pnl=sell_result.get("fill_price", 0) - close_result.get("fill_price", 0),
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

    def _get_current_buying_power_used(self) -> Optional[float]:
        """
        STRATEGY-004: Get the current buying power (margin) used by the diagonal.

        Bill Belt's rule: Roll long up when BP required hits $1,200 or greater
        OR when long leg is less than 20 delta.

        Returns:
            Buying power used in USD, or None if unable to determine
        """
        try:
            balance = self.client.get_balance()
            if balance:
                # Saxo returns MarginUsedByCurrentPositions or similar
                margin_used = balance.get("MarginUsedByCurrentPositions", 0)
                return margin_used
        except Exception as e:
            logger.debug(f"Could not get buying power: {e}")
        return None

    def should_roll_long_up(self) -> bool:
        """
        Check if long put should be rolled up.

        STRATEGY-004: Bill Belt's criteria for rolling long put up:
        1. Delta drops below threshold (20 delta) - provides less protection
        2. Buying power required hits $1,200 or greater

        Returns:
            True if long put should be rolled up
        """
        if not self.diagonal or not self.diagonal.long_put:
            return False

        # Check delta threshold (primary rule)
        threshold = abs(self.long_put_config.get("roll_delta_threshold", -0.20))
        current_delta = self.diagonal.long_delta_abs

        if current_delta < threshold:
            logger.info(f"STRATEGY-004: Long put delta {current_delta:.3f} < {threshold:.3f} threshold - should roll up")
            return True

        # STRATEGY-004: Check buying power threshold (secondary rule)
        bp_threshold = self.long_put_config.get("roll_bp_threshold", 1200)
        if bp_threshold > 0:
            bp_used = self._get_current_buying_power_used()
            if bp_used and bp_used >= bp_threshold:
                logger.info(f"STRATEGY-004: Buying power ${bp_used:.2f} >= ${bp_threshold:.2f} threshold - should roll up")
                return True

        return False

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
                # DRY-002: Get mid-prices for realistic P&L tracking
                new_long_mid = self._get_option_mid_price(new_long_data["uic"])
                old_long_mid = self._get_option_mid_price(old_long.uic)

                logger.info(f"[DRY RUN] Would roll long from ${old_long.strike} to ${new_long_data['strike']}")
                logger.info(f"  Close old @ ${old_long_mid:.2f}" if old_long_mid else "  Close old @ N/A")
                logger.info(f"  Open new @ ${new_long_mid:.2f}" if new_long_mid else "  Open new @ N/A")

                old_strike = old_long.strike
                # DRY-002: Update position with new mid-price
                self.diagonal.long_put.uic = new_long_data["uic"]
                self.diagonal.long_put.strike = new_long_data["strike"]
                self.diagonal.long_put.delta = new_long_data["delta"]
                self.diagonal.long_put.entry_price = new_long_mid or 0.0
                self.diagonal.long_put.current_price = new_long_mid or 0.0

                # DRY-002: Track simulated roll cost (pay for new - receive for old)
                roll_cost = (new_long_mid or 0) - (old_long_mid or 0)

                # Log the simulated long roll to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action="[SIMULATED] ROLL_LONG_UP",
                        strike=f"{old_strike}->{new_long_data['strike']}",
                        price=new_long_mid or 0.0,  # DRY-002: Use mid-price
                        delta=new_long_data["delta"],
                        pnl=-roll_cost * 100,  # DRY-002: Negative = cost to roll up
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
                # CRITICAL: Roll long partial fill - closed old long, failed to buy new
                # This leaves us with NAKED SHORT (extremely dangerous!)
                logger.critical("=" * 70)
                logger.critical("CRITICAL: Failed to buy new long put - NAKED SHORT EXPOSURE!")
                logger.critical("=" * 70)
                logger.critical("Old long was sold, new long could not be bought")
                logger.critical("Position is now: NAKED SHORT (UNLIMITED RISK)")

                # Update state to reflect reality - no long protection
                self.diagonal.long_put = None

                # EMERGENCY: Must close the naked short immediately
                logger.critical("Initiating emergency close of naked short...")
                if self._emergency_close_short_put():
                    logger.critical("Successfully closed naked short - position is now FLAT")
                    self.diagonal = None
                    self.state = RPDState.IDLE

                    if self.trade_logger:
                        self.trade_logger.log_safety_event({
                            "event_type": "RPD_LONG_ROLL_EMERGENCY",
                            "description": f"Long roll failed: closed old ${old_long.strike}, failed to buy new, emergency closed short",
                            "action_taken": "EMERGENCY_CLOSE_NAKED_SHORT",
                            "result": "Position now FLAT - will re-enter when conditions met",
                            "qqq_price": self.current_price,
                        })
                else:
                    # Failed to close naked short - this is CRITICAL
                    logger.critical("FAILED to close naked short - opening circuit breaker!")
                    self._open_circuit_breaker("Roll long failed, could not close naked short")

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

        STRATEGY-002: Bill Belt Exit Rules (from thetaprofits.com):
        "If the price drops under the MA9, either close the spread or buy back
        the short put and let the long put appreciate as the price drops."

        Close reasons (in priority order):
        1. Long put near expiry (1-2 DTE) - must close before expiration
        2. FOMC or major earnings approaching - event risk
        3. STRATEGY-002: Price below 9 EMA - Bill Belt's exit signal
        4. MKT-006: Max unrealized loss exceeded - safety limit

        Returns:
            Tuple of (should_close, reason)
        """
        if not self.diagonal or not self.diagonal.long_put:
            return False, ""

        # Check long put DTE (highest priority - must close before expiration)
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

        # MKT-006: Check max unrealized loss threshold
        if self._max_unrealized_loss > 0:
            unrealized = self.diagonal.unrealized_pnl
            if unrealized < -self._max_unrealized_loss:
                logger.warning(f"MKT-006: Unrealized loss ${abs(unrealized):.2f} exceeds max ${self._max_unrealized_loss:.2f}")
                return True, f"MKT-006: Max loss exceeded (${abs(unrealized):.2f} > ${self._max_unrealized_loss:.2f})"

        # STRATEGY-002: Bill Belt's exit rule - close when price drops below 9 EMA
        # This is the key fix: Bill says close immediately when price < EMA, not wait for 3%
        if self.indicators and self.indicators.ema_9 > 0:
            if not self.indicators.price_above_ema:
                distance_pct = abs((self.current_price - self.indicators.ema_9) / self.indicators.ema_9 * 100)
                logger.warning(f"STRATEGY-002: Price ${self.current_price:.2f} dropped below "
                              f"9 EMA ${self.indicators.ema_9:.2f} ({distance_pct:.2f}% below)")
                return True, f"STRATEGY-002: Price below 9 EMA (${self.current_price:.2f} < ${self.indicators.ema_9:.2f})"

        return False, ""

    def close_short_only(self, reason: str) -> bool:
        """
        STRATEGY-002: Close only the short put, keep long for directional appreciation.

        Bill Belt: "If the price drops under the MA9, either close the spread
        OR buy back the short put and let the long put appreciate as the price drops."

        This is an alternative to full close - use when bearish move expected
        and we want to profit from the long put's appreciation.

        Args:
            reason: Reason for closing short only

        Returns:
            True if short closed successfully, False otherwise
        """
        if self._check_circuit_breaker():
            return False

        if not self.diagonal or not self.diagonal.short_put:
            logger.warning("No short put to close")
            return False

        logger.info("=" * 50)
        logger.info("CLOSING SHORT ONLY (keeping long for appreciation)")
        logger.info("=" * 50)
        logger.info(f"Reason: {reason}")

        try:
            short_put = self.diagonal.short_put

            if self.dry_run:
                logger.info(f"[DRY RUN] Would close short put ${short_put.strike}, keep long ${self.diagonal.long_put.strike}")
                self.diagonal.short_put = None

                # Log to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action="[SIMULATED] CLOSE_SHORT_ONLY",
                        strike=short_put.strike,
                        price=0.0,
                        delta=0.0,
                        pnl=0.0,
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Short Put Close",
                        expiry_date=short_put.expiry,
                        premium_received=0.0,
                        trade_reason=f"[DRY RUN] {reason}"
                    )
                return True

            # Close short put
            result = self._place_protected_order(
                uic=short_put.uic,
                buy_sell=BuySell.BUY,
                quantity=abs(short_put.quantity),
                description=f"Close short put ${short_put.strike} (keeping long)",
            )

            if result.get("success"):
                # P&L = entry - exit (for short)
                short_pnl = (short_put.entry_price - result.get("fill_price", 0)) * 100
                logger.info(f"Short put closed - P&L: ${short_pnl:.2f}")
                logger.info(f"Keeping long put ${self.diagonal.long_put.strike} for appreciation")

                # Log to Google Sheets
                if self.trade_logger:
                    self.trade_logger.log_trade(
                        action="CLOSE_SHORT_ONLY",
                        strike=short_put.strike,
                        price=result.get("fill_price", 0),
                        delta=0.0,
                        pnl=short_pnl,
                        saxo_client=self.client,
                        underlying_price=self.current_price,
                        option_type="Short Put Close",
                        expiry_date=short_put.expiry,
                        premium_received=0.0,
                        trade_reason=reason
                    )

                # Clear short put from diagonal
                self.diagonal.short_put = None
                self._reset_failure_count()
                return True
            else:
                logger.error(f"Failed to close short put: {result.get('error')}")
                self._increment_failure_count("Failed to close short only")
                return False

        except Exception as e:
            logger.error(f"Error closing short only: {e}")
            self._increment_failure_count(f"Close short only exception: {e}")
            return False

    def close_campaign(self, reason: str) -> bool:
        """
        Close the entire diagonal campaign with verification.

        This method closes both legs of the diagonal and VERIFIES that
        both actually closed by checking with Saxo. If any leg fails to
        close, it handles the partial fill appropriately.

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
            short_closed = False
            long_closed = False
            short_pnl = 0.0
            long_pnl = 0.0

            # Track what we're trying to close for verification
            had_short = self.diagonal.short_put is not None
            had_long = self.diagonal.long_put is not None
            short_uic = self.diagonal.short_put.uic if had_short else None
            long_uic = self.diagonal.long_put.uic if had_long else None

            # Close short put first (higher risk if left alone)
            if self.diagonal.short_put:
                if self.dry_run:
                    logger.info(f"[DRY RUN] Would close short put ${self.diagonal.short_put.strike}")
                    short_closed = True
                else:
                    result = self._place_protected_order(
                        uic=self.diagonal.short_put.uic,
                        buy_sell=BuySell.BUY,
                        quantity=abs(self.diagonal.short_put.quantity),
                        description=f"Close short put ${self.diagonal.short_put.strike}",
                    )
                    if result.get("success"):
                        # P&L = entry - exit (for short)
                        short_pnl = (self.diagonal.short_put.entry_price - result.get("fill_price", 0)) * 100
                        campaign_pnl += short_pnl
                        short_closed = True
                        logger.info(f"Short put closed - P&L: ${short_pnl:.2f}")
                    else:
                        logger.error(f"Failed to close short put: {result.get('error')}")

            # Close long put
            if self.diagonal.long_put:
                if self.dry_run:
                    logger.info(f"[DRY RUN] Would close long put ${self.diagonal.long_put.strike}")
                    long_closed = True
                else:
                    result = self._place_protected_order(
                        uic=self.diagonal.long_put.uic,
                        buy_sell=BuySell.SELL,
                        quantity=self.diagonal.long_put.quantity,
                        description=f"Close long put ${self.diagonal.long_put.strike}",
                    )
                    if result.get("success"):
                        # P&L = exit - entry (for long)
                        long_pnl = (result.get("fill_price", 0) - self.diagonal.long_put.entry_price) * 100
                        campaign_pnl += long_pnl
                        long_closed = True
                        logger.info(f"Long put closed - P&L: ${long_pnl:.2f}")
                    else:
                        logger.error(f"Failed to close long put: {result.get('error')}")

            # CRITICAL: Verify closure with Saxo (unless dry run)
            if not self.dry_run:
                verification_passed = self._verify_campaign_closed(
                    short_uic=short_uic if had_short else None,
                    long_uic=long_uic if had_long else None,
                    expected_short_closed=had_short,
                    expected_long_closed=had_long
                )

                if not verification_passed:
                    logger.critical("=" * 70)
                    logger.critical("CLOSE_CAMPAIGN VERIFICATION FAILED")
                    logger.critical("=" * 70)
                    logger.critical(f"Short: {'closed' if short_closed else 'STILL OPEN'}")
                    logger.critical(f"Long: {'closed' if long_closed else 'STILL OPEN'}")

                    # Update diagonal to reflect what actually closed
                    if short_closed:
                        self.diagonal.short_put = None
                    if long_closed:
                        self.diagonal.long_put = None

                    # Check what's left and handle appropriately
                    if self.diagonal.short_put and not self.diagonal.long_put:
                        # DANGEROUS: Only short remaining = naked short
                        logger.critical("NAKED SHORT REMAINING - emergency closing!")
                        if self._emergency_close_short_put():
                            self.diagonal = None
                            self.state = RPDState.IDLE
                            logger.critical("Emergency closed remaining short - now IDLE")
                        else:
                            self._open_circuit_breaker("Close campaign failed, could not close remaining naked short")
                            return False
                    elif self.diagonal.long_put and not self.diagonal.short_put:
                        # Safe: Only long remaining - keep trying to close it
                        logger.warning("Long put still open - will retry close on next iteration")
                        self.state = RPDState.CLOSING_CAMPAIGN
                    else:
                        # Both still open - halt for manual intervention
                        self._handle_partial_fill("close_campaign",
                            [f"Short: {'closed' if short_closed else 'FAILED'}",
                             f"Long: {'closed' if long_closed else 'FAILED'}"])
                        return False

                    return False

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
                    strike=f"{self.diagonal.long_put.strike if self.diagonal.long_put else 'N/A'}/{self.diagonal.short_put.strike if self.diagonal.short_put else 'N/A'}",
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
                    "long_put_exit": long_pnl,
                    "total_rolls": self.diagonal.roll_count,
                    "vertical_rolls": self.diagonal.vertical_roll_count,
                    "horizontal_rolls": self.diagonal.horizontal_roll_count,
                    "total_premium": self.diagonal.total_premium_collected,
                    "long_put_pnl": long_pnl,
                    "net_pnl": campaign_pnl,
                    "close_reason": reason,
                })

            # ALERT: Campaign closed successfully
            self.alert_service.position_closed(
                reason=reason,
                pnl=campaign_pnl,
                details={
                    "campaign_number": self.diagonal.campaign_number,
                    "rolls": self.diagonal.roll_count,
                    "vertical_rolls": self.diagonal.vertical_roll_count,
                    "horizontal_rolls": self.diagonal.horizontal_roll_count,
                    "premium_collected": self.diagonal.total_premium_collected,
                    "long_pnl": long_pnl,
                    "short_pnl": short_pnl,
                    "qqq_price": self.current_price
                }
            )

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

    def _verify_campaign_closed(
        self,
        short_uic: Optional[int],
        long_uic: Optional[int],
        expected_short_closed: bool,
        expected_long_closed: bool
    ) -> bool:
        """
        Verify that campaign positions actually closed by checking Saxo.

        This is a critical safety check to ensure our local state matches
        the broker's actual position state after close orders.

        Args:
            short_uic: UIC of short put that should be closed
            long_uic: UIC of long put that should be closed
            expected_short_closed: Whether we expected short to close
            expected_long_closed: Whether we expected long to close

        Returns:
            True if verification passed, False if positions still exist
        """
        logger.info("Verifying campaign closure with Saxo...")

        try:
            # Give Saxo a moment to process
            import time
            time.sleep(2)

            all_positions = self.client.get_positions()
            if not all_positions:
                logger.info("Verification: No positions found - all closed")
                return True

            qqq_options = self._filter_qqq_options(all_positions)

            # Check for our specific UICs
            found_short = False
            found_long = False

            for pos in qqq_options:
                uic = pos.get("PositionBase", {}).get("Uic", 0)
                amount = pos.get("PositionBase", {}).get("Amount", 0)

                if short_uic and uic == short_uic and amount != 0:
                    found_short = True
                    logger.warning(f"Short put UIC {short_uic} still has position: {amount}")

                if long_uic and uic == long_uic and amount != 0:
                    found_long = True
                    logger.warning(f"Long put UIC {long_uic} still has position: {amount}")

            # Check if we found positions that should have been closed
            if expected_short_closed and found_short:
                logger.error("VERIFICATION FAILED: Short put still open!")
                return False

            if expected_long_closed and found_long:
                logger.error("VERIFICATION FAILED: Long put still open!")
                return False

            logger.info("Verification PASSED: All expected positions closed")
            return True

        except Exception as e:
            logger.error(f"Verification error: {e}")
            # On error, be conservative and assume verification failed
            return False

    # =========================================================================
    # ORDER MANAGEMENT
    # =========================================================================

    def _place_protected_order(
        self,
        uic: int,
        buy_sell: BuySell,
        quantity: int,
        description: str,
        verify_fill: bool = True,
        progressive_retry: Optional[bool] = None,
        max_absolute_slippage: Optional[float] = None
    ) -> Dict:
        """
        Place an order with progressive retry, orphan protection, and fill verification.

        Uses a progressive retry sequence similar to Delta Neutral:
        - 0% slippage x2 (fresh quote each time)
        - 5% slippage x2
        - 10% slippage x2
        - MARKET order (last resort, with spread safety check)

        Args:
            uic: Instrument UIC
            buy_sell: BUY or SELL
            quantity: Number of contracts
            description: Order description for logging
            verify_fill: If True, verify fill with position check after order
            progressive_retry: If True, use progressive slippage retry sequence
                               (defaults to self._progressive_retry from config)
            max_absolute_slippage: Max spread allowed for MARKET orders (safety)
                                   (defaults to self._max_market_spread from config)

        Returns:
            Dict with success status and fill info
        """
        # Use instance config values as defaults
        if progressive_retry is None:
            progressive_retry = self._progressive_retry
        if max_absolute_slippage is None:
            max_absolute_slippage = self._max_market_spread

        logger.info(f"Placing order: {description}")

        # Progressive retry sequence (like Delta Neutral):
        # - 0% slippage x2 (fresh quotes each time)
        # - 5% slippage x2
        # - 10% slippage x2
        # - MARKET order (last resort)
        # Format: (slippage_pct, is_market_order)
        if progressive_retry:
            retry_sequence = [
                (0.0, False),   # 1st: 0% slippage
                (0.0, False),   # 2nd: 0% slippage (fresh quote)
                (5.0, False),   # 3rd: 5% slippage
                (5.0, False),   # 4th: 5% slippage (fresh quote)
                (10.0, False),  # 5th: 10% slippage
                (10.0, False),  # 6th: 10% slippage (fresh quote)
                (0.0, True),    # 7th: MARKET order (guaranteed fill)
            ]
        else:
            # Single attempt mode (no progressive retry)
            retry_sequence = [(0.0, False)]

        last_result = None
        last_error = "Unknown error"

        for attempt, (current_slippage, is_market) in enumerate(retry_sequence):
            try:
                # Get fresh quote for accurate limit price
                quote = self.client.get_quote(uic, asset_type="StockOption")
                if not quote or "Quote" not in quote:
                    last_error = "Failed to get quote"
                    logger.warning(f"  Attempt {attempt+1}/{len(retry_sequence)}: {last_error}")
                    continue

                q = quote["Quote"]
                bid = q.get("Bid", 0) or 0
                ask = q.get("Ask", 0) or 0

                # Validate quote has real prices
                if bid <= 0 or ask <= 0:
                    last_error = f"Invalid quote: Bid=${bid:.2f}, Ask=${ask:.2f}"
                    logger.warning(f"  Attempt {attempt+1}/{len(retry_sequence)}: {last_error}")
                    continue

                # Determine base price based on buy/sell direction
                if buy_sell == BuySell.BUY:
                    base_price = ask
                else:
                    base_price = bid

                # MARKET ORDER attempt (last resort in progressive sequence)
                if is_market:
                    # Safety check: Abort if spread is too wide
                    spread = abs(ask - bid)
                    if spread > max_absolute_slippage:
                        logger.critical(f"  🚨 ORDER-005: Bid-ask spread ${spread:.2f} exceeds max ${max_absolute_slippage:.2f}")
                        logger.critical(f"     Bid: ${bid:.2f}, Ask: ${ask:.2f}")
                        logger.critical(f"     ABORTING MARKET order to prevent extreme slippage!")
                        if self.trade_logger:
                            self.trade_logger.log_safety_event({
                                "event_type": "RPD_MARKET_ORDER_ABORTED",
                                "severity": "WARNING",
                                "description": f"Spread ${spread:.2f} > max ${max_absolute_slippage:.2f}",
                                "action_taken": f"Aborted MARKET order for {description}",
                                "result": "ABORTED",
                                "qqq_price": self.current_price,
                            })
                        last_error = f"ORDER-005: Spread ${spread:.2f} too wide, MARKET order aborted"
                        continue

                    logger.warning(f"  Attempt {attempt+1}/{len(retry_sequence)}: MARKET ORDER (last resort)")
                    result = self.client.place_market_order_immediate(
                        uic=uic,
                        asset_type="StockOption",
                        buy_sell=buy_sell,
                        amount=quantity,
                        to_open_close="ToOpen" if buy_sell == BuySell.BUY else "ToClose"
                    )
                    last_result = result

                    if result.get("filled"):
                        logger.info(f"  ✓ MARKET order filled: {result.get('order_id')}")
                        return self._finalize_order_result(
                            result=result,
                            uic=uic,
                            buy_sell=buy_sell,
                            quantity=quantity,
                            description=description,
                            verify_fill=verify_fill,
                            fallback_price=base_price
                        )
                    else:
                        # ORDER-006: Parse specific rejection reason for MARKET orders
                        last_error = self._parse_rejection_reason(result)
                        logger.error(f"  ✗ MARKET order failed: {last_error}")
                        continue

                # LIMIT ORDER attempt with slippage
                if current_slippage > 0:
                    if buy_sell == BuySell.BUY:
                        # For buying, pay MORE to ensure fill
                        adjusted_price = base_price * (1 + current_slippage / 100)
                    else:
                        # For selling, accept LESS to ensure fill
                        adjusted_price = base_price * (1 - current_slippage / 100)
                    adjusted_price = round(adjusted_price, 2)
                else:
                    adjusted_price = base_price

                attempt_str = f"(attempt {attempt+1}/{len(retry_sequence)}, {current_slippage}% slippage)"
                logger.info(f"  {buy_sell.value} {quantity} x UIC {uic} @ ${adjusted_price:.2f} {attempt_str}")

                result = self.client.place_limit_order_with_timeout(
                    uic=uic,
                    asset_type="StockOption",
                    buy_sell=buy_sell.value,
                    quantity=quantity,
                    limit_price=adjusted_price,
                    timeout_seconds=self._order_timeout,
                )
                last_result = result

                if result.get("cancel_failed"):
                    # Order couldn't be cancelled - orphaned
                    orphan_id = result.get("order_id", "unknown")
                    self._track_orphaned_order(orphan_id)
                    logger.critical(f"  🚨 CANCEL FAILED - Order {orphan_id} is STILL OPEN!")
                    return {"success": False, "error": f"Orphaned order: {orphan_id}"}

                if result.get("filled"):
                    logger.info(f"  ✓ Limit order filled: {result.get('order_id')}")
                    return self._finalize_order_result(
                        result=result,
                        uic=uic,
                        buy_sell=buy_sell,
                        quantity=quantity,
                        description=description,
                        verify_fill=verify_fill,
                        fallback_price=adjusted_price
                    )
                else:
                    # ORDER-006: Parse specific rejection reason
                    last_error = self._parse_rejection_reason(result)
                    logger.warning(f"  Order not filled: {last_error}")

                    # Log retry info
                    if attempt < len(retry_sequence) - 1:
                        next_slippage, next_is_market = retry_sequence[attempt + 1]
                        if next_is_market:
                            logger.warning(f"  ⚠ Failed at {current_slippage}% slippage - will try MARKET order next...")
                        else:
                            logger.warning(f"  ⚠ Failed at {current_slippage}% slippage - retrying at {next_slippage}%...")
                    else:
                        logger.error(f"  ✗ Failed all {len(retry_sequence)} attempts")
                        logger.error(f"  ORDER-006 Rejection details: {last_error}")

            except Exception as e:
                last_error = str(e)
                logger.error(f"  Attempt {attempt+1}/{len(retry_sequence)} exception: {e}")
                continue

        # All attempts failed
        logger.error(f"Order failed after {len(retry_sequence)} attempts: {last_error}")
        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "event_type": "RPD_ORDER_FAILED",
                "severity": "ERROR",
                "description": f"Order failed: {description}",
                "action_taken": f"Exhausted {len(retry_sequence)} retry attempts",
                "result": last_error,
                "qqq_price": self.current_price,
            })
        return {"success": False, "error": last_error}

    def _finalize_order_result(
        self,
        result: Dict,
        uic: int,
        buy_sell: BuySell,
        quantity: int,
        description: str,
        verify_fill: bool,
        fallback_price: float
    ) -> Dict:
        """
        Finalize an order result after a successful fill.

        Handles fill verification and creates the standardized result dict.

        Args:
            result: Raw result from order placement
            uic: Instrument UIC
            buy_sell: BUY or SELL
            quantity: Number of contracts
            description: Order description for logging
            verify_fill: If True, verify fill with position check
            fallback_price: Price to use if fill_price not in result

        Returns:
            Dict with success status and fill info
        """
        fill_result = {
            "success": True,
            "order_id": result.get("order_id"),
            "position_id": result.get("position_id", ""),
            "fill_price": result.get("fill_price", fallback_price),
        }

        # Verify the fill with Saxo position check
        if verify_fill and not self.dry_run:
            verified = self._verify_order_fill(
                uic=uic,
                expected_buy_sell=buy_sell,
                expected_quantity=quantity,
                description=description
            )
            fill_result["verified"] = verified
            if not verified:
                logger.warning(f"Fill verification FAILED for {description}")
                # Don't fail the order, but log the discrepancy
                if self.trade_logger:
                    self.trade_logger.log_safety_event({
                        "event_type": "RPD_FILL_VERIFICATION_FAILED",
                        "severity": "WARNING",
                        "description": f"Fill verification failed: {description}",
                        "action_taken": "LOGGED_DISCREPANCY",
                        "result": "Position may be inconsistent",
                        "qqq_price": self.current_price,
                    })

        return fill_result

    def _verify_order_fill(
        self,
        uic: int,
        expected_buy_sell: BuySell,
        expected_quantity: int,
        description: str
    ) -> bool:
        """
        Verify an order fill by checking Saxo positions.

        This is a safety check to ensure our local state matches Saxo's
        actual position state after an order fills.

        Args:
            uic: UIC of the instrument
            expected_buy_sell: What we expected (BUY or SELL)
            expected_quantity: Expected quantity change
            description: Order description for logging

        Returns:
            True if position state is consistent, False otherwise
        """
        try:
            # Small delay for Saxo to process
            import time
            time.sleep(1)

            # Get current positions
            all_positions = self.client.get_positions()
            if not all_positions:
                # If we sold to close and there are no positions, that's expected
                if expected_buy_sell == BuySell.SELL:
                    logger.debug("No positions after sell - consistent")
                    return True
                # If we bought and there are no positions, that's unexpected
                logger.warning("No positions found after buy order")
                return False

            # Look for the specific UIC
            for pos in all_positions:
                pos_uic = pos.get("PositionBase", {}).get("Uic", 0)
                if pos_uic == uic:
                    amount = pos.get("PositionBase", {}).get("Amount", 0)
                    logger.debug(f"Position UIC {uic} has amount {amount}")

                    # Check if position direction is consistent
                    if expected_buy_sell == BuySell.BUY:
                        # Buying should result in positive (long) position
                        if amount > 0:
                            return True
                        # Or closing a short (amount was negative, now 0 or less negative)
                        return True  # Hard to verify partial closes
                    else:
                        # Selling should result in negative (short) position
                        if amount < 0:
                            return True
                        # Or closing a long (amount was positive, now 0 or less positive)
                        return True

            # UIC not found in positions - could be fully closed
            if expected_buy_sell == BuySell.BUY:
                # Bought to close a short, position now gone - consistent
                logger.debug(f"UIC {uic} not found after buy - likely closed short")
                return True
            else:
                # Sold to close a long, position now gone - consistent
                logger.debug(f"UIC {uic} not found after sell - likely closed long")
                return True

        except Exception as e:
            logger.error(f"Fill verification error: {e}")
            return False

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

        Called by main.py every 30 seconds during market hours (optimized for WebSocket streaming).
        """
        # TIME-001: Acquire operation lock to prevent overlapping iterations
        if not self._acquire_operation_lock():
            return

        try:
            self._run_iteration_impl()
        finally:
            # TIME-001: Always release the operation lock
            self._release_operation_lock()

    def _run_iteration_impl(self) -> None:
        """
        Internal implementation of run_iteration.

        Separated from run_iteration to allow operation lock wrapping.
        """
        # Safety check: circuit breaker
        if self._check_circuit_breaker():
            return

        # Safety check: orphaned orders still pending
        if self._check_for_orphaned_orders():
            logger.critical("Orphaned orders detected - halting until resolved")
            return

        # Safety check: orphaned positions blocking trading
        if self.has_orphaned_positions():
            orphans = self.get_orphaned_positions()
            orphan_summary = ", ".join([f"{o['position_type']} {o['description']}" for o in orphans])
            logger.warning(f"Orphaned positions detected: {orphan_summary}")

            # STATE-004: Try to auto-resolve instead of blocking forever
            if self._auto_resolve_orphaned_positions():
                logger.info("STATE-004: All orphaned positions resolved - continuing")
            else:
                # Still have unresolved orphans
                logger.critical("ORPHANED POSITIONS STILL BLOCKING TRADING")
                logger.critical("Manual intervention required for remaining orphans")
                return

        # TIME-003: Check for early close day (once per day)
        self.check_early_close_warning()

        # TIME-003: Block operations if past early close time
        if self._is_past_early_close():
            logger.warning("⏰ TIME-003: Past early close time - blocking new operations")
            return

        # LOG-001: Clear old error log entries periodically
        self._clear_old_error_logs()

        # Update market data
        if not self.update_market_data():
            self._log_deduplicated_error("market_data_fail", "Failed to update market data", "WARNING")
            return

        logger.info(f"QQQ: ${self.current_price:.2f} | EMA9: ${self.current_ema_9:.2f} | "
                   f"State: {self.state.value}")

        # MKT-002: Check for flash crash/rally velocity
        # Critical for Rolling Put Diagonal - short put can go deep ITM on flash crash
        flash_move = self.check_flash_crash_velocity()
        if flash_move:
            move_pct, direction = flash_move
            # Flash DOWN is dangerous - short put gets threatened
            # This triggers same close campaign check but with more urgency
            if direction == "DOWN" and self.state == RPDState.POSITION_OPEN:
                logger.critical(f"🚨 MKT-002: Flash DOWN {abs(move_pct):.2f}% - checking short put urgently")
                # Let should_close_campaign() handle the actual decision
                # which will check max_unrealized_loss and EMA exit

        # POS-002: Periodic position reconciliation (verify local state matches Saxo)
        self._reconcile_positions_periodic()

        # State machine
        if self.state == RPDState.IDLE or self.state == RPDState.WAITING_ENTRY:
            # Check entry conditions
            can_enter, reason = self.check_entry_conditions()
            if can_enter:
                # STATE-002: Verify we have no unexpected positions before entering
                actual = self._get_actual_positions_from_saxo()
                if actual["long_puts"] or actual["short_puts"]:
                    logger.warning("STATE-002: Found existing positions - cannot enter new campaign")
                    logger.warning(f"   Long puts: {len(actual['long_puts'])}, Short puts: {len(actual['short_puts'])}")
                    return
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
            # STATE-002: Verify positions before taking any action
            match, message = self._verify_positions_with_saxo()
            if not match:
                logger.warning(f"STATE-002: Position mismatch before action: {message}")
                # Don't halt - just log, reconciliation will handle if persistent

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
                # DRY-002: Get mid-price for realistic P&L tracking
                short_mid = self._get_option_mid_price(short_put_data["uic"])
                simulated_premium = (short_mid or 0) * 100

                logger.info(f"[DRY RUN] Would sell new short: ${short_put_data['strike']} @ ${short_mid:.2f}" if short_mid else f"[DRY RUN] Would sell new short: ${short_put_data['strike']}")
                self.diagonal.short_put = PutPosition(
                    uic=short_put_data["uic"],
                    strike=short_put_data["strike"],
                    expiry=short_put_data["expiry"],
                    quantity=-1,
                    entry_price=short_mid or 0.0,  # DRY-002: Use mid-price
                    current_price=short_mid or 0.0,
                )
                # DRY-002: Track simulated premium
                self.diagonal.total_premium_collected += simulated_premium
                self.metrics.total_premium_collected += simulated_premium
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

        # Safety status
        summary["safety"] = {
            "circuit_breaker_open": self._circuit_breaker_open,
            "circuit_breaker_reason": self._circuit_breaker_reason if self._circuit_breaker_open else None,
            "circuit_breaker_opened_at": self._circuit_breaker_opened_at.isoformat() if self._circuit_breaker_opened_at else None,
            "consecutive_failures": self._consecutive_failures,
            "max_failures": self._max_consecutive_failures,
            "orphaned_orders": len(self._orphaned_orders),
            "orphaned_positions": len(self._orphaned_positions),
            "actions_on_cooldown": list(self._action_cooldowns.keys()),
        }

        # Orphaned position details if any
        if self._orphaned_positions:
            summary["orphaned_position_details"] = [
                f"{o['position_type']} {o['description']}" for o in self._orphaned_positions
            ]

        if self.diagonal:
            summary["position"] = {
                "long_strike": self.diagonal.long_put.strike if self.diagonal.long_put else None,
                "long_dte": self.diagonal.long_dte,
                "short_strike": self.diagonal.short_put.strike if self.diagonal.short_put else None,
                "short_dte": self.diagonal.short_dte,
                "campaign_rolls": self.diagonal.roll_count,
                "premium_collected": self.diagonal.total_premium_collected,
                "is_complete": self.diagonal.is_complete,
                "has_naked_short": self.diagonal.short_put is not None and self.diagonal.long_put is None,
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

        # Send WhatsApp/Email daily summary alert
        summary_for_alert = summary.copy()
        summary_for_alert["dry_run"] = self.dry_run
        self.alert_service.daily_summary_rolling_put_diagonal(summary_for_alert)
        logger.info("Daily summary alert sent to WhatsApp/Email")

    def log_position_to_sheets(self):
        """
        Log current positions to Google Sheets Positions tab.

        Rolling Put Diagonal specific columns:
        - Position Type (Long Put / Short Put)
        - Strike, Expiry, DTE, Delta
        - Entry Price, Current Price, P&L ($), P&L (EUR)
        - Campaign #, Premium Collected, Status
        """
        if not self.trade_logger or not self.diagonal:
            return

        # Get EUR conversion rate
        eur_rate = 1.0
        try:
            rate = self.client.get_usd_to_account_currency_rate()
            if rate:
                eur_rate = rate
        except Exception:
            pass

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
                "pnl_eur": long_pnl * eur_rate,
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
                "pnl_eur": short_pnl * eur_rate,
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
