"""
strategy.py - Delta Neutral Strategy Implementation

This module implements the core trading strategy logic including:
- Long Straddle entry with VIX filter
- Weekly Short Strangle income generation
- 5-Point Recentering rule
- Rolling and exit management

Strategy Overview:
------------------
1. Buy ATM Long Straddle (90-120 DTE) when VIX < 18
2. Sell weekly Short Strangles at 1.5-2x expected move
3. If SPY moves 5 points from initial strike, recenter:
   - Close current Long Straddle
   - Open new ATM Long Straddle at same expiration
   - Reset Weekly Shorts
4. Roll weekly shorts on Friday
5. Exit entire trade when 30-60 DTE remains on Longs

Author: Trading Bot Developer
Date: 2024
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
from shared.market_hours import get_us_market_time

# Path for persistent metrics storage (now in project root data/ folder)
METRICS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "delta_neutral_metrics.json")

# Configure module logger
logger = logging.getLogger(__name__)


class PositionType(Enum):
    """Types of positions in the strategy."""
    LONG_CALL = "LongCall"
    LONG_PUT = "LongPut"
    SHORT_CALL = "ShortCall"
    SHORT_PUT = "ShortPut"


class StrategyState(Enum):
    """States of the trading strategy."""
    IDLE = "Idle"                          # No positions, waiting for entry
    WAITING_VIX = "WaitingForVIX"          # Waiting for VIX < 18
    LONG_STRADDLE_ACTIVE = "LongStraddleActive"  # Long straddle entered
    FULL_POSITION = "FullPosition"          # Long straddle + short strangle active
    RECENTERING = "Recentering"             # In process of recentering
    ROLLING_SHORTS = "RollingShorts"        # Rolling weekly shorts
    EXITING = "Exiting"                     # Closing all positions


@dataclass
class OptionPosition:
    """
    Represents a single option position.

    Attributes:
        position_id: Unique identifier from the broker
        uic: Unique Instrument Code
        strike: Strike price
        expiry: Expiration date
        option_type: Call or Put
        position_type: Long or Short position
        quantity: Number of contracts
        entry_price: Price at entry
        current_price: Current market price
        delta: Position delta
        gamma: Position gamma (rate of change of delta)
        theta: Position theta (time decay)
        vega: Position vega (volatility sensitivity)
    """
    position_id: str
    uic: int
    strike: float
    expiry: str
    option_type: str  # "Call" or "Put"
    position_type: PositionType
    quantity: int
    entry_price: float
    current_price: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0


@dataclass
class StraddlePosition:
    """
    Represents a straddle position (long call + long put at same strike).

    Attributes:
        call: The call option position
        put: The put option position
        initial_strike: The strike price at entry
        entry_underlying_price: Underlying price when position was opened
        entry_date: Date when the position was opened (for historical P&L tracking)
    """
    call: Optional[OptionPosition] = None
    put: Optional[OptionPosition] = None
    initial_strike: float = 0.0
    entry_underlying_price: float = 0.0
    entry_date: str = ""  # Format: YYYY-MM-DD or ISO timestamp

    @property
    def is_complete(self) -> bool:
        """Check if both legs of the straddle are active."""
        return self.call is not None and self.put is not None

    @property
    def total_delta(self) -> float:
        """Calculate total delta of the straddle."""
        call_delta = self.call.delta if self.call else 0
        put_delta = self.put.delta if self.put else 0
        return call_delta + put_delta

    @property
    def total_value(self) -> float:
        """Calculate total current value of the straddle."""
        call_value = (self.call.current_price * self.call.quantity * 100) if self.call else 0
        put_value = (self.put.current_price * self.put.quantity * 100) if self.put else 0
        return call_value + put_value


@dataclass
class StranglePosition:
    """
    Represents a strangle position (short call + short put at different strikes).

    Attributes:
        call: The short call option position
        put: The short put option position
        call_strike: Call strike price
        put_strike: Put strike price
        expiry: Expiration date
        entry_date: Date when the position was opened (for theta tracking)
    """
    call: Optional[OptionPosition] = None
    put: Optional[OptionPosition] = None
    call_strike: float = 0.0
    put_strike: float = 0.0
    expiry: str = ""
    entry_date: str = ""  # Format: YYYY-MM-DD

    @property
    def is_complete(self) -> bool:
        """Check if both legs of the strangle are active."""
        return self.call is not None and self.put is not None

    @property
    def total_delta(self) -> float:
        """Calculate total delta of the strangle."""
        call_delta = self.call.delta if self.call else 0
        put_delta = self.put.delta if self.put else 0
        return call_delta + put_delta

    @property
    def premium_collected(self) -> float:
        """Calculate total premium collected from selling the strangle."""
        call_premium = (self.call.entry_price * self.call.quantity * 100) if self.call else 0
        put_premium = (self.put.entry_price * self.put.quantity * 100) if self.put else 0
        return call_premium + put_premium

    @property
    def days_held(self) -> int:
        """Calculate number of days the position has been held."""
        if not self.entry_date:
            return 0
        try:
            entry = datetime.strptime(self.entry_date, "%Y-%m-%d")
            return (datetime.now() - entry).days
        except ValueError:
            return 0

    @property
    def days_to_expiry(self) -> int:
        """Calculate calendar days until expiration (not time-based)."""
        if not self.expiry:
            return 0
        try:
            expiry_date = datetime.strptime(self.expiry, "%Y-%m-%d").date()
            today = datetime.now().date()
            return max(0, (expiry_date - today).days)
        except ValueError:
            return 0


@dataclass
class StrategyMetrics:
    """
    Tracks strategy performance metrics.

    Attributes:
        total_premium_collected: Total premium from short positions
        total_straddle_cost: Cost of long straddles
        realized_pnl: Realized profit/loss
        unrealized_pnl: Unrealized profit/loss
        recenter_count: Number of times position was recentered
        roll_count: Number of times shorts were rolled
        daily_pnl_start: P&L at start of trading day (for daily tracking)
        spy_open: SPY price at market open
        spy_high: SPY high of day
        spy_low: SPY low of day
        vix_high: VIX high of day
        vix_samples: List of VIX readings for daily average
    """
    total_premium_collected: float = 0.0
    total_straddle_cost: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    recenter_count: int = 0
    roll_count: int = 0
    # Trade tracking
    trade_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    total_trade_pnl: float = 0.0  # Sum of all closed trade P&L
    # Drawdown tracking
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    # Daily tracking
    daily_pnl_start: float = 0.0
    spy_open: float = 0.0
    spy_high: float = 0.0
    spy_low: float = 0.0
    vix_high: float = 0.0
    vix_samples: list = None
    # Daily roll/recenter tracking (reset each day)
    daily_roll_count: int = 0
    daily_recenter_count: int = 0

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.vix_samples is None:
            self.vix_samples = []

    def reset_daily_tracking(self, current_pnl: float, spy_price: float, vix: float):
        """Reset daily tracking at start of trading day."""
        self.daily_pnl_start = current_pnl
        self.spy_open = spy_price
        self.spy_high = spy_price
        self.spy_low = spy_price
        self.vix_high = vix
        self.vix_samples = [vix]
        # Reset daily roll/recenter counts
        self.daily_roll_count = 0
        self.daily_recenter_count = 0

    def update_daily_tracking(self, spy_price: float, vix: float):
        """Update daily high/low tracking."""
        if spy_price > self.spy_high:
            self.spy_high = spy_price
        if spy_price < self.spy_low or self.spy_low == 0:
            self.spy_low = spy_price
        if vix > self.vix_high:
            self.vix_high = vix
        self.vix_samples.append(vix)

    @property
    def spy_range(self) -> float:
        """Calculate SPY range for the day."""
        return self.spy_high - self.spy_low

    @property
    def vix_avg(self) -> float:
        """Calculate VIX average for the day."""
        if self.vix_samples:
            return sum(self.vix_samples) / len(self.vix_samples)
        return 0.0

    @property
    def total_pnl(self) -> float:
        """Calculate total P&L."""
        return self.realized_pnl + self.unrealized_pnl

    def record_trade(self, pnl: float):
        """Record a completed trade for statistics tracking."""
        self.trade_count += 1
        self.total_trade_pnl += pnl
        if pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        if pnl > self.best_trade_pnl:
            self.best_trade_pnl = pnl
        if pnl < self.worst_trade_pnl:
            self.worst_trade_pnl = pnl

    def update_drawdown(self, current_pnl: float):
        """Update peak P&L and max drawdown."""
        if current_pnl > self.peak_pnl:
            self.peak_pnl = current_pnl
        drawdown = self.peak_pnl - current_pnl
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

    @property
    def win_rate(self) -> float:
        """Calculate win rate as decimal for Google Sheets (0.50 = 50%)."""
        if self.trade_count == 0:
            return 0.0
        return self.winning_trades / self.trade_count

    @property
    def avg_trade_pnl(self) -> float:
        """Calculate average P&L per trade."""
        if self.trade_count == 0:
            return 0.0
        return self.total_trade_pnl / self.trade_count

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary for persistence."""
        return {
            "total_premium_collected": self.total_premium_collected,
            "total_straddle_cost": self.total_straddle_cost,
            "realized_pnl": self.realized_pnl,
            "recenter_count": self.recenter_count,
            "roll_count": self.roll_count,
            "trade_count": self.trade_count,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "best_trade_pnl": self.best_trade_pnl,
            "worst_trade_pnl": self.worst_trade_pnl,
            "total_trade_pnl": self.total_trade_pnl,
            "peak_pnl": self.peak_pnl,
            "max_drawdown": self.max_drawdown,
            "last_updated": datetime.now().isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyMetrics":
        """Create StrategyMetrics from saved dictionary."""
        metrics = cls()
        metrics.total_premium_collected = data.get("total_premium_collected", 0.0)
        metrics.total_straddle_cost = data.get("total_straddle_cost", 0.0)
        metrics.realized_pnl = data.get("realized_pnl", 0.0)
        metrics.recenter_count = data.get("recenter_count", 0)
        metrics.roll_count = data.get("roll_count", 0)
        metrics.trade_count = data.get("trade_count", 0)
        metrics.winning_trades = data.get("winning_trades", 0)
        metrics.losing_trades = data.get("losing_trades", 0)
        metrics.best_trade_pnl = data.get("best_trade_pnl", 0.0)
        metrics.worst_trade_pnl = data.get("worst_trade_pnl", 0.0)
        metrics.total_trade_pnl = data.get("total_trade_pnl", 0.0)
        metrics.peak_pnl = data.get("peak_pnl", 0.0)
        metrics.max_drawdown = data.get("max_drawdown", 0.0)
        return metrics

    def save_to_file(self, filepath: str = None) -> bool:
        """
        Save metrics to JSON file for persistence across bot restarts.

        Args:
            filepath: Path to save file. Defaults to METRICS_FILE.

        Returns:
            True if save successful, False otherwise.
        """
        filepath = filepath or METRICS_FILE
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            with open(filepath, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
            logger.info(f"Saved strategy metrics to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save metrics to {filepath}: {e}")
            return False

    @classmethod
    def load_from_file(cls, filepath: str = None) -> Optional["StrategyMetrics"]:
        """
        Load metrics from JSON file.

        Args:
            filepath: Path to load from. Defaults to METRICS_FILE.

        Returns:
            StrategyMetrics instance if file exists, None otherwise.
        """
        filepath = filepath or METRICS_FILE
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    data = json.load(f)
                metrics = cls.from_dict(data)
                last_updated = data.get("last_updated", "unknown")
                logger.info(f"Loaded strategy metrics from {filepath} (last updated: {last_updated})")
                logger.info(f"  Realized P&L: ${metrics.realized_pnl:.2f}")
                logger.info(f"  Total Premium Collected: ${metrics.total_premium_collected:.2f}")
                logger.info(f"  Trade Count: {metrics.trade_count}, Win Rate: {metrics.win_rate*100:.1f}%")
                return metrics
            else:
                logger.info(f"No saved metrics found at {filepath}, starting fresh")
                return None
        except Exception as e:
            logger.error(f"Failed to load metrics from {filepath}: {e}")
            return None


class DeltaNeutralStrategy:
    """
    Delta Neutral Strategy Implementation.

    This class implements the complete delta neutral trading strategy
    with the 5-point recentering rule, VIX filter, and weekly income
    generation through short strangles.

    Attributes:
        client: SaxoClient instance for API calls
        config: Strategy configuration dictionary
        state: Current strategy state
        long_straddle: Current long straddle position
        short_strangle: Current short strangle position
        metrics: Strategy performance metrics

    Example:
        >>> strategy = DeltaNeutralStrategy(client, config)
        >>> strategy.run()
    """

    def __init__(self, client: SaxoClient, config: Dict[str, Any], trade_logger: Any = None, dry_run: bool = False):
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
        self.dry_run = dry_run

        # Strategy state
        self.state = StrategyState.IDLE
        self.long_straddle: Optional[StraddlePosition] = None
        self.short_strangle: Optional[StranglePosition] = None

        # Load persisted metrics or start fresh
        saved_metrics = StrategyMetrics.load_from_file()
        self.metrics = saved_metrics if saved_metrics else StrategyMetrics()
        self._metrics_loaded_from_file = saved_metrics is not None

        # Underlying tracking
        self.underlying_uic = self.strategy_config["underlying_uic"]
        self.underlying_symbol = self.strategy_config["underlying_symbol"]
        self.vix_uic = self.strategy_config["vix_uic"]

        # Current market data
        self.current_underlying_price: float = 0.0
        self.current_vix: float = 0.0
        self.initial_straddle_strike: float = 0.0

        # Track when shorts closed due to debit (wait for expiry before new shorts)
        # Per Brian Terry: "You aren't rolling, you're starting a new weekly cycle"
        self._shorts_closed_date: Optional[date] = None

        # CIRCUIT BREAKER: Consecutive failure tracking to prevent death loops
        # If we hit MAX_CONSECUTIVE_FAILURES, halt all trading
        self._consecutive_failures: int = 0
        self._max_consecutive_failures: int = 3
        self._circuit_breaker_open: bool = False
        self._circuit_breaker_reason: str = ""
        self._last_failure_time: Optional[datetime] = None

        # Track orphaned orders that couldn't be cancelled
        self._orphaned_orders: List[str] = []

        # ACTION COOLDOWN: Prevent rapid retry of same failed action
        # Maps action_type -> last_attempt_time
        self._action_cooldowns: Dict[str, datetime] = {}
        self._cooldown_seconds: int = 300  # 5 minute cooldown after failed action

        # Strategy parameters
        self.recenter_threshold = self.strategy_config["recenter_threshold_points"]
        self.max_vix = self.strategy_config["max_vix_entry"]
        self.vix_defensive_threshold = self.strategy_config.get("vix_defensive_threshold", 25.0)
        self.target_dte = self.strategy_config.get("long_straddle_target_dte", 120)
        self.exit_dte_min = self.strategy_config["exit_dte_min"]
        self.exit_dte_max = self.strategy_config["exit_dte_max"]
        self.strangle_multiplier_min = self.strategy_config["weekly_strangle_multiplier_min"]
        self.strangle_multiplier_max = self.strategy_config["weekly_strangle_multiplier_max"]
        self.weekly_target_return_pct = self.strategy_config.get("weekly_target_return_percent", None)
        self.short_strangle_max_multiplier = self.strategy_config.get("short_strangle_max_multiplier", 1.5)
        self.short_strangle_entry_fee_per_leg = self.strategy_config.get("short_strangle_entry_fee_per_leg", 2.0)
        self.position_size = self.strategy_config["position_size"]
        self.max_spread_percent = self.strategy_config["max_bid_ask_spread_percent"]
        self.roll_days = self.strategy_config["roll_days"]
        self.order_timeout_seconds = self.strategy_config.get("order_timeout_seconds", 60)

        logger.info(f"DeltaNeutralStrategy initialized for {self.underlying_symbol}")
        logger.info(f"Recenter threshold: {self.recenter_threshold} points")
        logger.info(f"VIX entry threshold: < {self.max_vix}")
        if self.weekly_target_return_pct and self.weekly_target_return_pct > 0:
            logger.info(f"Target return mode ENABLED: {self.weekly_target_return_pct}% weekly")
        else:
            logger.info(f"Using multiplier mode: {self.strangle_multiplier_min}-{self.strangle_multiplier_max}x expected move")
        if self.dry_run:
            logger.warning("DRY RUN MODE - No real orders will be placed")
        logger.info(f"Slippage protection: {self.order_timeout_seconds}s timeout on limit orders")

    # =========================================================================
    # CIRCUIT BREAKER - DEATH LOOP PREVENTION
    # =========================================================================

    def _increment_failure_count(self, reason: str) -> None:
        """
        Increment the consecutive failure count and check circuit breaker.

        Called when an operation fails. If we hit MAX_CONSECUTIVE_FAILURES,
        the circuit breaker opens and halts all trading.

        Args:
            reason: Description of the failure for logging
        """
        self._consecutive_failures += 1
        self._last_failure_time = datetime.now()

        logger.warning(f"⚠️ Operation failed: {reason}")
        logger.warning(f"   Consecutive failures: {self._consecutive_failures}/{self._max_consecutive_failures}")

        if self._consecutive_failures >= self._max_consecutive_failures:
            self._open_circuit_breaker(reason)

    def _reset_failure_count(self) -> None:
        """Reset the consecutive failure count after a successful operation."""
        if self._consecutive_failures > 0:
            logger.info(f"✓ Resetting failure count (was {self._consecutive_failures})")
            self._consecutive_failures = 0

    def _open_circuit_breaker(self, reason: str) -> None:
        """
        Open the circuit breaker to halt all trading.

        This is a CRITICAL safety mechanism. When open:
        - No new orders will be placed
        - No rolls or recenters will be attempted
        - Manual intervention is required

        Args:
            reason: Description of why the circuit breaker opened
        """
        self._circuit_breaker_open = True
        self._circuit_breaker_reason = reason

        logger.critical("=" * 70)
        logger.critical("🚨 CIRCUIT BREAKER OPEN - ALL TRADING HALTED 🚨")
        logger.critical("=" * 70)
        logger.critical(f"Reason: {reason}")
        logger.critical(f"Consecutive failures: {self._consecutive_failures}")
        logger.critical(f"Time: {datetime.now().isoformat()}")
        logger.critical("")
        logger.critical("MANUAL INTERVENTION REQUIRED:")
        logger.critical("1. Check Saxo positions in SaxoTraderGO")
        logger.critical("2. Verify no orphaned orders are pending")
        logger.critical("3. Fix any position discrepancies")
        logger.critical("4. Restart the bot to reset circuit breaker")
        logger.critical("=" * 70)

        # Log to trade logger if available
        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "event_type": "CIRCUIT_BREAKER_OPEN",
                "severity": "CRITICAL",
                "spy_price": self.current_underlying_price,
                "initial_strike": self.initial_straddle_strike,
                "vix": self.current_vix,
                "action_taken": "TRADING HALTED",
                "description": f"Circuit breaker opened after {self._consecutive_failures} consecutive failures. Reason: {reason}",
                "result": "HALTED"
            })

    def _check_circuit_breaker(self) -> bool:
        """
        Check if the circuit breaker is open.

        Returns:
            bool: True if circuit breaker is open (trading should stop), False otherwise
        """
        if self._circuit_breaker_open:
            logger.warning(f"🚨 Circuit breaker is OPEN - trading halted. Reason: {self._circuit_breaker_reason}")
            return True
        return False

    def reset_circuit_breaker(self) -> None:
        """
        Manually reset the circuit breaker.

        This should only be called after manual verification that:
        1. All positions are correct in Saxo
        2. No orphaned orders are pending
        3. The issue that caused the failures has been resolved
        """
        if self._circuit_breaker_open:
            logger.info("=" * 50)
            logger.info("Circuit breaker manually reset")
            logger.info(f"Previous reason: {self._circuit_breaker_reason}")
            logger.info("=" * 50)

        self._circuit_breaker_open = False
        self._circuit_breaker_reason = ""
        self._consecutive_failures = 0
        self._orphaned_orders = []

    def _add_orphaned_order(self, order_id: str) -> None:
        """
        Track an orphaned order that couldn't be cancelled.

        Args:
            order_id: The order ID that is still open on Saxo
        """
        if order_id and order_id not in self._orphaned_orders:
            self._orphaned_orders.append(order_id)
            logger.critical(f"⚠️ ORPHANED ORDER TRACKED: {order_id}")
            logger.critical(f"   Total orphaned orders: {len(self._orphaned_orders)}")

    def _check_for_orphaned_orders(self) -> bool:
        """
        Check Saxo for any orphaned orders before placing new ones.

        Returns:
            bool: True if orphaned orders were found (should not proceed), False if clean
        """
        if not self._orphaned_orders:
            return False

        logger.warning(f"⚠️ Checking for {len(self._orphaned_orders)} potential orphaned orders...")

        open_orders = self.client.get_open_orders()
        orphans_still_open = []

        for order_id in self._orphaned_orders:
            if any(o.get("OrderId") == order_id for o in open_orders):
                orphans_still_open.append(order_id)
                logger.critical(f"   ORPHAN STILL OPEN: {order_id}")

        if orphans_still_open:
            logger.critical(f"🚨 {len(orphans_still_open)} orphaned orders still pending on Saxo!")
            logger.critical("   These MUST be cancelled manually before trading can continue")
            self._open_circuit_breaker(f"Orphaned orders detected: {orphans_still_open}")
            return True

        # All orphans have been filled or cancelled
        logger.info("✓ All tracked orphaned orders have been resolved")
        self._orphaned_orders = []
        return False

    def _is_action_on_cooldown(self, action_type: str) -> bool:
        """
        Check if an action is on cooldown after a recent failure.

        This prevents rapid retry loops where the bot keeps attempting
        the same failed action every iteration.

        Args:
            action_type: Type of action (e.g., "recenter", "roll_shorts", "enter_shorts")

        Returns:
            bool: True if action is on cooldown and should be skipped, False if OK to proceed
        """
        if action_type not in self._action_cooldowns:
            return False

        last_attempt = self._action_cooldowns[action_type]
        elapsed = (datetime.now() - last_attempt).total_seconds()

        if elapsed < self._cooldown_seconds:
            remaining = self._cooldown_seconds - elapsed
            logger.info(f"⏳ Action '{action_type}' on cooldown for {remaining:.0f}s more (failed recently)")
            return True

        # Cooldown expired, remove from tracking
        del self._action_cooldowns[action_type]
        return False

    def _set_action_cooldown(self, action_type: str) -> None:
        """
        Set a cooldown for an action after it fails.

        Args:
            action_type: Type of action that failed
        """
        self._action_cooldowns[action_type] = datetime.now()
        logger.warning(f"⏳ Action '{action_type}' on {self._cooldown_seconds}s cooldown after failure")

    def _clear_action_cooldown(self, action_type: str) -> None:
        """
        Clear cooldown for an action after it succeeds.

        Args:
            action_type: Type of action that succeeded
        """
        if action_type in self._action_cooldowns:
            del self._action_cooldowns[action_type]
            logger.info(f"✓ Cooldown cleared for '{action_type}' after success")

    # =========================================================================
    # SLIPPAGE PROTECTION - ORDER PLACEMENT WITH TIMEOUT
    # =========================================================================

    def _place_protected_multi_leg_order(
        self,
        legs: List[Dict],
        total_limit_price: float,
        order_description: str
    ) -> Dict:
        """
        Place individual orders for each leg with slippage protection.

        NOTE: Saxo Live API does not support multi-leg orders across different UICs.
        This method places each leg as a separate limit order.

        Per strategy spec: "Use Limit Orders only, and if a 'Recenter' or 'Roll'
        isn't filled within 60 seconds, it should alert rather than chasing the price."

        Args:
            legs: List of leg dictionaries with uic, asset_type, buy_sell, amount, price
            total_limit_price: Total limit price (for logging only)
            order_description: Description for logging (e.g., "LONG_STRADDLE", "SHORT_STRANGLE")

        Returns:
            dict: {
                "success": bool,
                "filled": bool,
                "order_id": str or None,
                "message": str
            }
        """
        from shared.saxo_client import BuySell

        logger.info(f"Placing {order_description} as individual orders (Saxo Live requirement)")
        logger.info(f"  Total limit price: ${total_limit_price:.2f}")
        logger.info(f"  Legs: {len(legs)}")
        logger.info(f"  Timeout per leg: {self.order_timeout_seconds}s")

        # In dry_run mode, simulate success
        if self.dry_run:
            logger.info(f"[DRY RUN] Simulating {order_description} order (no real order placed)")
            return {
                "success": True,
                "filled": True,
                "order_id": f"SIMULATED_{int(time.time())}",
                "message": "[DRY RUN] Order simulated successfully"
            }

        # Place each leg as an individual order
        filled_orders = []
        failed = False
        failure_message = ""

        for i, leg in enumerate(legs):
            leg_uic = leg["uic"]
            leg_asset_type = leg["asset_type"]
            leg_buy_sell = BuySell.BUY if leg["buy_sell"] == "Buy" else BuySell.SELL
            leg_amount = leg["amount"]
            # Use per-leg price (already in per-share format, need to use raw price)
            leg_price = leg.get("price", 0) / 100 if leg.get("price", 0) > 100 else leg.get("price", 0)

            # Get fresh quote for accurate limit price
            quote = self.client.get_quote(leg_uic, leg_asset_type)
            if quote and "Quote" in quote:
                if leg_buy_sell == BuySell.BUY:
                    leg_price = quote["Quote"].get("Ask", leg_price) or leg_price
                else:
                    leg_price = quote["Quote"].get("Bid", leg_price) or leg_price

            # Get to_open_close from the leg data (default ToOpen)
            leg_to_open_close = leg.get("to_open_close", "ToOpen")

            logger.info(f"  Leg {i+1}/{len(legs)}: {leg_buy_sell.value} {leg_amount} x UIC {leg_uic} @ ${leg_price:.2f} ({leg_to_open_close})")

            result = self.client.place_limit_order_with_timeout(
                uic=leg_uic,
                asset_type=leg_asset_type,
                buy_sell=leg_buy_sell,
                amount=leg_amount,
                limit_price=leg_price,
                timeout_seconds=self.order_timeout_seconds,
                to_open_close=leg_to_open_close
            )

            if result["filled"]:
                filled_orders.append(result["order_id"])
                logger.info(f"  ✓ Leg {i+1} filled: {result['order_id']}")
            else:
                failed = True
                failure_message = f"Leg {i+1} failed: {result['message']}"
                logger.error(f"  ✗ {failure_message}")

                # CRITICAL: Check if cancel failed - this means order is STILL OPEN on Saxo
                if result.get("cancel_failed"):
                    orphaned_order_id = result.get("order_id")
                    logger.critical(f"  🚨 CANCEL FAILED - Order {orphaned_order_id} is STILL OPEN on Saxo!")
                    logger.critical("     This order MUST be cancelled manually before bot continues")

                    # Track the orphaned order
                    if orphaned_order_id:
                        self._add_orphaned_order(orphaned_order_id)

                    # Increment failure count - this is a serious issue
                    self._increment_failure_count(f"cancel_failed_leg_{i+1}_{order_description}")

                break

        if failed:
            # Log alert for partial fill or failure
            logger.critical(f"⚠️ SLIPPAGE ALERT: {order_description} NOT FULLY FILLED")
            logger.critical(f"   {failure_message}")
            logger.critical(f"   Filled legs: {len(filled_orders)}/{len(legs)}")

            # CRITICAL: If we have partially filled legs, this is a serious state inconsistency
            if filled_orders:
                logger.critical("   ⚠️ PARTIAL FILL DETECTED - some legs filled, some did not!")
                logger.critical("   The bot's position tracking may be inconsistent with Saxo")
                logger.critical("   MANUAL VERIFICATION REQUIRED in SaxoTraderGO")

                # Track the filled orders as potential orphans until we verify state
                for filled_order_id in filled_orders:
                    logger.warning(f"   Partially filled order tracked: {filled_order_id}")

            logger.critical("   Manual intervention may be required!")

            # Log safety event
            if self.trade_logger:
                self.trade_logger.log_safety_event({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "event_type": "SLIPPAGE_TIMEOUT",
                    "severity": "CRITICAL",
                    "spy_price": self.current_underlying_price,
                    "initial_strike": self.initial_straddle_strike,
                    "vix": self.current_vix,
                    "action_taken": f"{order_description} partially filled ({len(filled_orders)}/{len(legs)} legs)",
                    "description": failure_message,
                    "result": "PARTIAL_FILL" if filled_orders else "FAILED"
                })

            # Increment failure count for any order failure
            self._increment_failure_count(f"{order_description}_order_failed")

            return {
                "success": False,
                "filled": False,
                "order_id": ",".join(filled_orders) if filled_orders else None,
                "message": failure_message,
                "partial_fill": len(filled_orders) > 0
            }

        # All legs filled successfully
        logger.info(f"✓ All {len(legs)} legs filled for {order_description}")

        # Reset failure count on successful order
        self._reset_failure_count()

        return {
            "success": True,
            "filled": True,
            "order_id": ",".join(filled_orders),
            "message": f"All {len(legs)} legs filled successfully"
        }

    def _calculate_combo_limit_price(
        self,
        legs: List[Dict],
        buy_sell_direction: str
    ) -> float:
        """
        Calculate appropriate limit price for a multi-leg combo.

        For buying (straddle): Use the ask prices (we pay)
        For selling (strangle): Use the bid prices (we receive)

        Args:
            legs: List of leg dictionaries with uic
            buy_sell_direction: "Buy" or "Sell" for the overall combo

        Returns:
            float: Total limit price for the combo
        """
        total_price = 0.0

        for leg in legs:
            quote = self.client.get_quote(leg["uic"], "StockOption")
            if quote:
                if buy_sell_direction == "Buy":
                    # Buying: use ask price
                    price = quote["Quote"].get("Ask", 0)
                else:
                    # Selling: use bid price
                    price = quote["Quote"].get("Bid", 0)
                total_price += price * 100  # Convert to dollar value

        return total_price

    # =========================================================================
    # POSITION RECOVERY METHODS
    # =========================================================================

    def recover_positions(self) -> bool:
        """
        Recover existing positions from Saxo on bot startup.

        This method queries Saxo for open SPY option positions and reconstructs
        the strategy state. Essential for bot restarts and GCP VM recovery.

        Returns:
            bool: True if positions were recovered, False if starting fresh
        """
        logger.info("Checking for existing positions to recover...")

        # Get all open positions from Saxo
        positions = self.client.get_positions()
        if not positions:
            logger.info("No existing positions found - starting fresh")
            return False

        # Filter for SPY options only
        spy_options = self._filter_spy_options(positions)
        if not spy_options:
            logger.info("No SPY option positions found - starting fresh")
            return False

        logger.info(f"Found {len(spy_options)} SPY option positions to analyze")

        # Categorize positions by type and expiry
        long_positions = []
        short_positions = []

        for pos in spy_options:
            pos_base = pos.get("PositionBase", {})
            amount = pos_base.get("Amount", 0)

            if amount > 0:
                long_positions.append(pos)
            elif amount < 0:
                short_positions.append(pos)

        logger.info(f"Long positions: {len(long_positions)}, Short positions: {len(short_positions)}")

        # Try to reconstruct long straddle (long call + long put at same strike)
        straddle_recovered = self._recover_long_straddle(long_positions)

        # Try to reconstruct short strangle (short call + short put at different strikes)
        strangle_recovered = self._recover_short_strangle(short_positions)

        # Determine strategy state based on recovered positions
        if straddle_recovered and strangle_recovered:
            self.state = StrategyState.FULL_POSITION
            logger.info("RECOVERED: Full position (long straddle + short strangle)")
        elif straddle_recovered:
            self.state = StrategyState.LONG_STRADDLE_ACTIVE
            logger.info("RECOVERED: Long straddle active (no short strangle)")
        elif strangle_recovered:
            # Unusual state - short strangle without long straddle
            self.state = StrategyState.FULL_POSITION
            logger.warning("RECOVERED: Short strangle without long straddle (unusual)")
        else:
            logger.info("Could not reconstruct strategy positions - starting fresh")
            return False

        # Fetch current market data for logging
        self.update_market_data()

        # Log recovery to trade logger and Google Sheets
        if self.trade_logger:
            self.trade_logger.log_event("=" * 50)
            self.trade_logger.log_event("POSITION RECOVERY COMPLETED")
            self.trade_logger.log_event(f"State: {self.state.value}")

            # Build list of all individual positions (4 legs) for comprehensive logging
            individual_positions = []

            # Add long straddle legs (2 positions: call + put)
            if self.long_straddle:
                straddle_expiry = self.long_straddle.call.expiry if self.long_straddle.call else "N/A"
                straddle_strike = self.long_straddle.initial_strike

                self.trade_logger.log_event(
                    f"Long Straddle: Strike ${straddle_strike:.2f}, Expiry {straddle_expiry}"
                )

                if self.long_straddle.call:
                    individual_positions.append({
                        "position_type": "LONG",
                        "option_type": "Call",
                        "strike": self.long_straddle.call.strike,
                        "expiry": self.long_straddle.call.expiry,
                        "quantity": self.long_straddle.call.quantity,
                        "entry_price": self.long_straddle.call.entry_price,
                        "current_price": self.long_straddle.call.current_price,
                        "delta": self.long_straddle.call.delta,
                        "gamma": self.long_straddle.call.gamma,
                        "theta": self.long_straddle.call.theta,
                        "vega": self.long_straddle.call.vega
                    })

                if self.long_straddle.put:
                    individual_positions.append({
                        "position_type": "LONG",
                        "option_type": "Put",
                        "strike": self.long_straddle.put.strike,
                        "expiry": self.long_straddle.put.expiry,
                        "quantity": self.long_straddle.put.quantity,
                        "entry_price": self.long_straddle.put.entry_price,
                        "current_price": self.long_straddle.put.current_price,
                        "delta": self.long_straddle.put.delta,
                        "gamma": self.long_straddle.put.gamma,
                        "theta": self.long_straddle.put.theta,
                        "vega": self.long_straddle.put.vega
                    })

            # Add short strangle legs (2 positions: call + put)
            if self.short_strangle:
                strangle_expiry = self.short_strangle.expiry

                self.trade_logger.log_event(
                    f"Short Strangle: Call ${self.short_strangle.call_strike:.2f}, "
                    f"Put ${self.short_strangle.put_strike:.2f}, Expiry {strangle_expiry}"
                )

                if self.short_strangle.call:
                    individual_positions.append({
                        "position_type": "SHORT",
                        "option_type": "Call",
                        "strike": self.short_strangle.call.strike,
                        "expiry": self.short_strangle.call.expiry,
                        "quantity": self.short_strangle.call.quantity,
                        "entry_price": self.short_strangle.call.entry_price,
                        "current_price": self.short_strangle.call.current_price,
                        "delta": self.short_strangle.call.delta,
                        "gamma": self.short_strangle.call.gamma,
                        "theta": self.short_strangle.call.theta,
                        "vega": self.short_strangle.call.vega
                    })

                if self.short_strangle.put:
                    individual_positions.append({
                        "position_type": "SHORT",
                        "option_type": "Put",
                        "strike": self.short_strangle.put.strike,
                        "expiry": self.short_strangle.put.expiry,
                        "quantity": self.short_strangle.put.quantity,
                        "entry_price": self.short_strangle.put.entry_price,
                        "current_price": self.short_strangle.put.current_price,
                        "delta": self.short_strangle.put.delta,
                        "gamma": self.short_strangle.put.gamma,
                        "theta": self.short_strangle.put.theta,
                        "vega": self.short_strangle.put.vega
                    })

            # Check if ANY position is already logged (to avoid duplicates on restart)
            already_logged = False
            if individual_positions:
                first_pos = individual_positions[0]
                already_logged = self.trade_logger.check_position_logged(
                    first_pos["position_type"],
                    first_pos["strike"],
                    first_pos["expiry"]
                )

            if not already_logged and individual_positions:
                # Log ALL 4 positions to ALL sheets (Trades, Positions, Greeks, Safety Events)
                # Pass saxo_client so FX rate can be fetched for currency conversion
                self.trade_logger.log_recovered_positions_full(
                    individual_positions=individual_positions,
                    underlying_price=self.current_underlying_price,
                    vix=self.current_vix,
                    saxo_client=self.client
                )
                self.trade_logger.log_event(f"  -> Logged {len(individual_positions)} individual positions to ALL Google Sheets tabs")
            else:
                self.trade_logger.log_event("  -> Positions already logged in Google Sheets (skipping)")

            self.trade_logger.log_event("=" * 50)

        # Load historical P&L from closed positions (only if not loaded from file)
        self.load_historical_pnl_into_metrics()

        return True

    def _filter_spy_options(self, positions: List[Dict]) -> List[Dict]:
        """
        Filter positions to only include SPY options.

        Args:
            positions: List of all positions from Saxo API

        Returns:
            List of SPY option positions only
        """
        spy_options = []

        for pos in positions:
            display_format = pos.get("DisplayAndFormat", {})
            symbol = display_format.get("Symbol", "")
            asset_type = pos.get("PositionBase", {}).get("AssetType", "")

            # Check if this is a SPY option
            # Symbol format is typically like "SPY:xnas/20250321/C575" or similar
            if ("SPY" in symbol.upper() or self.underlying_symbol.upper() in symbol.upper()) and \
               asset_type in ["StockOption", "ContractFutures"]:
                spy_options.append(pos)
                logger.debug(f"Found SPY option: {symbol}")

        return spy_options

    def _recover_long_straddle(self, long_positions: List[Dict]) -> bool:
        """
        Attempt to recover a long straddle from long option positions.

        A long straddle consists of:
        - 1 long call at strike X
        - 1 long put at strike X (same strike as call)
        - Both with the same expiry (typically 90-120 DTE)

        Args:
            long_positions: List of long option positions

        Returns:
            bool: True if straddle was recovered
        """
        if len(long_positions) < 2:
            return False

        # Parse positions into call/put groups by strike and expiry
        calls_by_strike = {}
        puts_by_strike = {}

        for pos in long_positions:
            parsed = self._parse_option_position(pos)
            if not parsed:
                continue

            key = (parsed["strike"], parsed["expiry"])

            if parsed["option_type"] == "Call":
                calls_by_strike[key] = parsed
            elif parsed["option_type"] == "Put":
                puts_by_strike[key] = parsed

        # Find matching call/put pairs (same strike and expiry)
        for key, call_data in calls_by_strike.items():
            if key in puts_by_strike:
                put_data = puts_by_strike[key]

                # Found a straddle! Create the position objects with Greeks
                call_option = OptionPosition(
                    position_id=call_data["position_id"],
                    uic=call_data["uic"],
                    strike=call_data["strike"],
                    expiry=call_data["expiry"],
                    option_type="Call",
                    position_type=PositionType.LONG_CALL,
                    quantity=call_data["quantity"],
                    entry_price=call_data["entry_price"],
                    current_price=call_data["current_price"],
                    delta=call_data.get("delta", 0.5),
                    gamma=call_data.get("gamma", 0),
                    theta=call_data.get("theta", 0),
                    vega=call_data.get("vega", 0)
                )

                put_option = OptionPosition(
                    position_id=put_data["position_id"],
                    uic=put_data["uic"],
                    strike=put_data["strike"],
                    expiry=put_data["expiry"],
                    option_type="Put",
                    position_type=PositionType.LONG_PUT,
                    quantity=put_data["quantity"],
                    entry_price=put_data["entry_price"],
                    current_price=put_data["current_price"],
                    delta=put_data.get("delta", -0.5),
                    gamma=put_data.get("gamma", 0),
                    theta=put_data.get("theta", 0),
                    vega=put_data.get("vega", 0)
                )

                # Get entry date from call or put position (use whichever has it)
                straddle_entry_date = call_data.get("entry_date") or put_data.get("entry_date") or ""

                self.long_straddle = StraddlePosition(
                    call=call_option,
                    put=put_option,
                    initial_strike=call_data["strike"],
                    entry_underlying_price=call_data["strike"],  # Approximate
                    entry_date=straddle_entry_date
                )

                # Set the initial straddle strike for recentering logic
                self.initial_straddle_strike = call_data["strike"]

                # Set metrics for recovered straddle (entry prices * 100 * quantity for total value)
                # Only set if not loaded from file (persisted values are more accurate)
                qty = call_data["quantity"]  # Assuming call and put have same quantity
                straddle_cost = (call_data["entry_price"] + put_data["entry_price"]) * 100 * qty
                if not self._metrics_loaded_from_file:
                    self.metrics.total_straddle_cost = straddle_cost

                logger.info(
                    f"Recovered long straddle: Strike ${call_data['strike']:.2f}, "
                    f"Expiry {call_data['expiry']}, "
                    f"Qty {qty}, Cost ${straddle_cost:.2f}"
                )
                return True

        return False

    def _recover_short_strangle(self, short_positions: List[Dict]) -> bool:
        """
        Attempt to recover a short strangle from short option positions.

        A short strangle consists of:
        - 1 short call at strike X (OTM)
        - 1 short put at strike Y (OTM), where Y < X
        - Both with the same expiry (typically weekly)

        Args:
            short_positions: List of short option positions

        Returns:
            bool: True if strangle was recovered
        """
        if len(short_positions) < 2:
            return False

        # Parse positions into call/put groups by expiry
        calls_by_expiry = {}
        puts_by_expiry = {}

        for pos in short_positions:
            parsed = self._parse_option_position(pos)
            if not parsed:
                continue

            expiry = parsed["expiry"]

            if parsed["option_type"] == "Call":
                if expiry not in calls_by_expiry:
                    calls_by_expiry[expiry] = []
                calls_by_expiry[expiry].append(parsed)
            elif parsed["option_type"] == "Put":
                if expiry not in puts_by_expiry:
                    puts_by_expiry[expiry] = []
                puts_by_expiry[expiry].append(parsed)

        # Find matching call/put pairs (same expiry, different strikes)
        for expiry, calls in calls_by_expiry.items():
            if expiry in puts_by_expiry:
                puts = puts_by_expiry[expiry]

                # Take the first call and put (typically only one of each)
                call_data = calls[0]
                put_data = puts[0]

                # Verify this looks like a strangle (call strike > put strike)
                if call_data["strike"] <= put_data["strike"]:
                    logger.warning(
                        f"Short positions don't form valid strangle: "
                        f"Call ${call_data['strike']}, Put ${put_data['strike']}"
                    )
                    continue

                # Found a strangle! Create the position objects with Greeks
                call_option = OptionPosition(
                    position_id=call_data["position_id"],
                    uic=call_data["uic"],
                    strike=call_data["strike"],
                    expiry=call_data["expiry"],
                    option_type="Call",
                    position_type=PositionType.SHORT_CALL,
                    quantity=call_data["quantity"],
                    entry_price=call_data["entry_price"],
                    current_price=call_data["current_price"],
                    delta=call_data.get("delta", -0.15),
                    gamma=call_data.get("gamma", 0),
                    theta=call_data.get("theta", 0),
                    vega=call_data.get("vega", 0)
                )

                put_option = OptionPosition(
                    position_id=put_data["position_id"],
                    uic=put_data["uic"],
                    strike=put_data["strike"],
                    expiry=put_data["expiry"],
                    option_type="Put",
                    position_type=PositionType.SHORT_PUT,
                    quantity=put_data["quantity"],
                    entry_price=put_data["entry_price"],
                    current_price=put_data["current_price"],
                    delta=put_data.get("delta", 0.15),
                    gamma=put_data.get("gamma", 0),
                    theta=put_data.get("theta", 0),
                    vega=put_data.get("vega", 0)
                )

                # Estimate entry date: weekly strangles opened on Friday (7 days before Friday expiry)
                # For recovery, calculate entry_date as (expiry - 7 days) or today if that's in the future
                entry_date = datetime.now().strftime("%Y-%m-%d")
                try:
                    expiry_date = datetime.strptime(expiry, "%Y-%m-%d")
                    estimated_entry = expiry_date - timedelta(days=7)  # Previous Friday
                    if estimated_entry <= datetime.now():
                        entry_date = estimated_entry.strftime("%Y-%m-%d")
                except ValueError:
                    pass  # Keep today as default

                self.short_strangle = StranglePosition(
                    call=call_option,
                    put=put_option,
                    call_strike=call_data["strike"],
                    put_strike=put_data["strike"],
                    expiry=expiry,
                    entry_date=entry_date
                )

                # Set metrics for recovered strangle (entry prices * 100 * quantity for total value)
                # Only set if not loaded from file (persisted values include historical data)
                qty = call_data["quantity"]  # Assuming call and put have same quantity
                premium_collected = (call_data["entry_price"] + put_data["entry_price"]) * 100 * qty
                if not self._metrics_loaded_from_file:
                    self.metrics.total_premium_collected = premium_collected

                logger.info(
                    f"Recovered short strangle: Call ${call_data['strike']:.2f}, "
                    f"Put ${put_data['strike']:.2f}, Expiry {expiry}, Entry {entry_date}, "
                    f"Qty {qty}, Premium ${premium_collected:.2f}"
                )
                return True

        return False

    def _parse_option_position(self, pos: Dict) -> Optional[Dict]:
        """
        Parse a Saxo position response into a standardized format.

        Args:
            pos: Raw position dictionary from Saxo API

        Returns:
            Parsed position dict or None if parsing fails
        """
        import re

        try:
            display_format = pos.get("DisplayAndFormat", {})
            pos_base = pos.get("PositionBase", {})
            pos_view = pos.get("PositionView", {})

            symbol = display_format.get("Symbol", "")

            # Parse the symbol to extract option details
            strike = None
            expiry = None
            option_type = None

            symbol_upper = symbol.upper()

            # Saxo symbol format: SPY/DDMYYC{STRIKE}:xcbf or SPY/DDMYYP{STRIKE}:xcbf
            # Example: SPY/31H26C690:xcbf = SPY Call 690 expiring March 31, 2026
            # Month codes: F=Jan, G=Feb, H=Mar, J=Apr, K=May, M=Jun,
            #              N=Jul, Q=Aug, U=Sep, V=Oct, X=Nov, Z=Dec
            month_codes = {
                'F': '01', 'G': '02', 'H': '03', 'J': '04', 'K': '05', 'M': '06',
                'N': '07', 'Q': '08', 'U': '09', 'V': '10', 'X': '11', 'Z': '12'
            }

            # Try Saxo format: SPY/DDMYYC{STRIKE}:xcbf
            saxo_match = re.match(r'SPY/(\d{2})([FGHJKMQUVXZ])(\d{2})([CP])(\d+)', symbol_upper)
            if saxo_match:
                day = saxo_match.group(1)
                month_code = saxo_match.group(2)
                year = saxo_match.group(3)
                cp = saxo_match.group(4)
                strike_str = saxo_match.group(5)

                month = month_codes.get(month_code, '01')
                expiry = f"20{year}-{month}-{day}"  # Format: 2026-03-31 (match Saxo API format)
                option_type = "Call" if cp == 'C' else "Put"
                strike = float(strike_str)

                logger.debug(f"Parsed Saxo symbol {symbol}: {option_type} ${strike} exp {expiry}")

            # If Saxo format didn't match, try other formats
            if not option_type:
                # Determine call or put from symbol
                if "/C" in symbol_upper or "C" in symbol_upper.split("/")[-1].split(":")[0]:
                    option_type = "Call"
                elif "/P" in symbol_upper or "P" in symbol_upper.split("/")[-1].split(":")[0]:
                    option_type = "Put"

            # Try to get strike from the position data if not parsed
            if not strike:
                strike = pos_base.get("Strike") or display_format.get("Strike")
                if not strike:
                    # Try to parse from symbol - look for number after C or P
                    strike_match = re.search(r'[CP](\d+(?:\.\d+)?)', symbol_upper)
                    if strike_match:
                        strike = float(strike_match.group(1))

            # Try to get expiry from position data if not parsed
            if not expiry:
                expiry = pos_base.get("ExpiryDate") or display_format.get("ExpiryDate")
                if not expiry:
                    # Try to parse from symbol - look for date pattern
                    date_match = re.search(r'(\d{8})', symbol)  # Format: 20250321
                    if date_match:
                        expiry = date_match.group(1)

            # Final fallback - use any available data
            if not all([strike, expiry, option_type]):
                logger.warning(f"Could not fully parse position: {symbol}")
                if not strike:
                    strike = pos_base.get("Strike", 0)
                if not expiry:
                    expiry = pos_base.get("ExpiryDate", "Unknown")
                if not option_type:
                    option_type = pos_base.get("PutCall", "Unknown")

            # Only return if we have essential data
            if not strike or strike == 0:
                logger.warning(f"No strike price found for {symbol}, skipping")
                return None

            # Extract Greeks from the dedicated Greeks FieldGroup (if available)
            # Saxo returns Greeks in a separate "Greeks" object when requested
            # Note: Saxo uses "Instrument" prefix for Greeks (InstrumentDelta, InstrumentGamma, etc.)
            greeks = pos.get("Greeks", {})

            # Delta can come from either Greeks object (with Instrument prefix) or PositionView
            delta = greeks.get("InstrumentDelta") or greeks.get("Delta") or pos_view.get("Delta", 0)
            gamma = greeks.get("InstrumentGamma") or greeks.get("Gamma", 0)
            theta = greeks.get("InstrumentTheta") or greeks.get("Theta", 0)
            vega = greeks.get("InstrumentVega") or greeks.get("Vega", 0)

            # Log if we got Greeks
            if any([gamma, theta, vega]):
                logger.info(f"Greeks for {symbol}: Delta={delta:.4f}, Gamma={gamma:.4f}, Theta={theta:.4f}, Vega={vega:.4f}")

            entry_price = pos_base.get("OpenPrice", 0) or pos_view.get("AverageOpenPrice", 0)
            current_price = pos_view.get("CurrentPrice", 0) or pos_view.get("MarketValue", 0)

            # Get entry date from ExecutionTimeOpen (for historical P&L tracking)
            entry_date = pos_base.get("ExecutionTimeOpen", "")

            logger.info(f"Position {symbol}: Entry=${entry_price:.4f}, Current=${current_price:.4f}")

            return {
                "position_id": str(pos_base.get("PositionId", "")),
                "uic": pos_base.get("Uic", 0),
                "symbol": symbol,
                "strike": float(strike) if strike else 0,
                "expiry": str(expiry) if expiry else "",
                "option_type": option_type,
                "quantity": abs(pos_base.get("Amount", 0)),
                "entry_price": entry_price,
                "current_price": current_price,
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
                "entry_date": entry_date,
            }

        except Exception as e:
            logger.error(f"Error parsing option position {symbol}: {e}")
            return None

    def calculate_historical_pnl(self) -> float:
        """
        Calculate realized P&L from closed SPY positions since the long straddle opened.

        Queries Saxo API for all closed SPY option positions since the current
        long straddle's entry date. This captures P&L from previous short strangle
        rolls that occurred before the bot was restarted.

        Returns:
            float: Total realized P&L from closed short strangles, or 0.0 if none found.
        """
        if not self.long_straddle:
            logger.info("No long straddle - cannot calculate historical P&L")
            return 0.0

        # Get the entry date of the current long straddle
        straddle_entry_date = self.long_straddle.entry_date

        if not straddle_entry_date:
            logger.warning("Long straddle has no entry date - cannot calculate historical P&L")
            return 0.0

        # Parse date if it's a string
        if isinstance(straddle_entry_date, str):
            try:
                straddle_entry_date = datetime.fromisoformat(straddle_entry_date.replace("Z", "+00:00"))
            except ValueError:
                # Try simpler format
                try:
                    straddle_entry_date = datetime.strptime(straddle_entry_date[:10], "%Y-%m-%d")
                except ValueError:
                    logger.error(f"Could not parse straddle entry date: {straddle_entry_date}")
                    return 0.0

        from_date = straddle_entry_date.strftime("%Y-%m-%d")
        logger.info(f"Calculating historical P&L from closed positions since {from_date}")

        # Get closed SPY positions from Saxo API
        closed_positions = self.client.get_closed_spy_positions(from_date)
        if not closed_positions:
            logger.info("No closed SPY positions found since straddle opened")
            return 0.0

        # Calculate total P&L from closed SHORT positions opened on/after straddle entry
        # Saxo field names:
        #   - Amount: negative for shorts
        #   - PnLUSD: realized P&L in USD
        #   - TradeDateOpen: when position was opened
        #   - InstrumentDescription: option description
        total_pnl = 0.0
        short_strangle_count = 0

        for pos in closed_positions:
            try:
                amount = pos.get("Amount", 0)
                trade_open_date = pos.get("TradeDateOpen", "")

                # Only count SHORT positions (Amount < 0)
                if amount >= 0:
                    continue

                # Only count positions opened ON OR AFTER the straddle entry date
                # This excludes old short strangles from before the current straddle
                if trade_open_date < from_date:
                    logger.debug(f"Skipping short position opened before straddle: {pos.get('InstrumentDescription', '')}")
                    continue

                # Get P&L - Saxo uses PnLUSD for USD P&L
                pnl = pos.get("PnLUSD") or pos.get("PnLAccountCurrency") or 0
                description = pos.get("InstrumentDescription", "") or pos.get("InstrumentSymbol", "")
                close_date = pos.get("TradeDateClose", "")

                total_pnl += pnl
                short_strangle_count += 1

                # Record this trade in metrics for win rate, trade count, best/worst tracking
                self.metrics.record_trade(pnl)

                logger.info(f"Historical short strangle: {description[:40]}, Open: {trade_open_date}, Close: {close_date}, P&L: ${pnl:.2f}")

            except Exception as e:
                logger.warning(f"Error processing closed position: {e}")
                continue

        logger.info(f"Historical P&L from {short_strangle_count} closed short positions: ${total_pnl:.2f}")
        return total_pnl

    def load_historical_pnl_into_metrics(self) -> bool:
        """
        Load historical P&L from closed positions into the metrics.

        This should be called after position recovery if metrics were not loaded
        from file. It ensures that P&L from previous short strangle rolls is
        captured even on fresh bot starts.

        Returns:
            bool: True if historical P&L was loaded, False otherwise.
        """
        # Only load if we didn't load from file (file has accurate cumulative data)
        if self._metrics_loaded_from_file:
            logger.info("Metrics loaded from file - skipping historical P&L query")
            return False

        historical_pnl = self.calculate_historical_pnl()

        if historical_pnl != 0.0:
            # Add historical P&L to realized P&L
            self.metrics.realized_pnl += historical_pnl
            logger.info(f"Added historical P&L to metrics: ${historical_pnl:.2f}")
            logger.info(f"Total realized P&L now: ${self.metrics.realized_pnl:.2f}")
        else:
            logger.info("No historical P&L to add (no closed short positions found)")

        # Always save metrics after querying historical P&L (even if 0)
        # This prevents duplicate queries on subsequent restarts
        if not self.dry_run:
            self.metrics.save_to_file()
            logger.info("Saved metrics to prevent duplicate historical P&L queries on restart")

        return historical_pnl != 0.0

    # =========================================================================
    # MARKET DATA METHODS
    # =========================================================================

    def update_market_data(self) -> bool:
        """
        Update current market data for underlying and VIX with PriceInfo fallback.

        Returns:
            bool: True if data updated successfully, False otherwise.
        """
        try:
            # Get underlying price (SPY) - with external feed fallback for simulation
            quote = self.client.get_spy_price(self.underlying_uic, symbol=self.underlying_symbol)
            if quote:
                quote_data = quote.get("Quote", {})
                price_info = quote.get("PriceInfo", {})

                # Check if using external source
                if quote_data.get("_external_source"):
                    logger.info(f"{self.underlying_symbol}: Using external price feed (simulation only)")

                # Priority: 1. Mid/LastTraded from Quote, 2. Last from PriceInfo
                self.current_underlying_price = (
                    quote_data.get("Mid") or
                    quote_data.get("LastTraded") or
                    price_info.get("Last") or
                    quote_data.get("Bid") or
                    quote_data.get("Ask") or
                    0.0
                )

                if self.current_underlying_price > 0:
                    logger.debug(f"{self.underlying_symbol} price: ${self.current_underlying_price:.2f}")
                else:
                    logger.error(f"{self.underlying_symbol}: No price data found")
                    return False
            else:
                logger.error(f"Failed to get underlying quote for {self.underlying_symbol}")
                return False

            # Get VIX price (This now uses your updated logic in saxo_client.py)
            vix_price = self.client.get_vix_price(self.vix_uic)
            if vix_price:
                self.current_vix = vix_price
                logger.debug(f"VIX: {self.current_vix:.2f}")
            else:
                logger.warning("Failed to get VIX price, using last known value")

            return True

        except Exception as e:
            logger.error(f"Error updating market data: {e}")
            return False

    def refresh_position_prices(self) -> bool:
        """
        Refresh current prices and Greeks for all option positions from Saxo API.

        This is needed after position recovery when the market is closed
        and current_price may not have been populated.

        Returns:
            bool: True if prices refreshed successfully
        """
        try:
            # Get fresh positions from Saxo
            positions = self.client.get_positions()
            if not positions:
                logger.warning("No positions returned from Saxo for price refresh")
                return False

            # Filter for SPY options only
            spy_options = [p for p in positions if "SPY" in p.get("DisplayAndFormat", {}).get("Symbol", "")]

            for pos in spy_options:
                pos_view = pos.get("PositionView", {})
                pos_base = pos.get("PositionBase", {})
                symbol = pos.get("DisplayAndFormat", {}).get("Symbol", "")

                # Get current price from position view
                current_price = pos_view.get("CurrentPrice", 0) or pos_view.get("MarketValue", 0)
                strike = pos_base.get("Strike", 0)

                # Extract Greeks from the position data
                greeks = pos.get("Greeks", {})
                theta = greeks.get("InstrumentTheta") or greeks.get("Theta", 0)
                delta = greeks.get("InstrumentDelta") or greeks.get("Delta") or pos_view.get("Delta", 0)
                gamma = greeks.get("InstrumentGamma") or greeks.get("Gamma", 0)
                vega = greeks.get("InstrumentVega") or greeks.get("Vega", 0)

                # Also try to get price from Greeks if available
                if current_price == 0:
                    # Try fetching individual option quote
                    uic = pos_base.get("Uic")
                    if uic:
                        quote = self.client.get_quote(uic, asset_type="StockOption")
                        if quote and "Quote" in quote:
                            current_price = (
                                quote["Quote"].get("Mid") or
                                quote["Quote"].get("LastTraded") or
                                quote["Quote"].get("Bid") or
                                0
                            )

                if current_price == 0:
                    logger.debug(f"Could not get current price for {symbol} (market may be closed)")
                    continue

                # Update the corresponding position in our strategy
                # Match by UIC
                uic = pos_base.get("Uic")

                if self.long_straddle:
                    if self.long_straddle.call and self.long_straddle.call.uic == uic:
                        self.long_straddle.call.current_price = current_price
                        self.long_straddle.call.theta = theta
                        self.long_straddle.call.delta = delta
                        self.long_straddle.call.gamma = gamma
                        self.long_straddle.call.vega = vega
                        logger.debug(f"Updated long call: price=${current_price:.4f}, theta={theta:.4f}")
                    if self.long_straddle.put and self.long_straddle.put.uic == uic:
                        self.long_straddle.put.current_price = current_price
                        self.long_straddle.put.theta = theta
                        self.long_straddle.put.delta = delta
                        self.long_straddle.put.gamma = gamma
                        self.long_straddle.put.vega = vega
                        logger.debug(f"Updated long put: price=${current_price:.4f}, theta={theta:.4f}")

                if self.short_strangle:
                    if self.short_strangle.call and self.short_strangle.call.uic == uic:
                        self.short_strangle.call.current_price = current_price
                        self.short_strangle.call.theta = theta
                        self.short_strangle.call.delta = delta
                        self.short_strangle.call.gamma = gamma
                        self.short_strangle.call.vega = vega
                        logger.debug(f"Updated short call: price=${current_price:.4f}, theta={theta:.4f}")
                    if self.short_strangle.put and self.short_strangle.put.uic == uic:
                        self.short_strangle.put.current_price = current_price
                        self.short_strangle.put.theta = theta
                        self.short_strangle.put.delta = delta
                        self.short_strangle.put.gamma = gamma
                        self.short_strangle.put.vega = vega
                        logger.debug(f"Updated short put: price=${current_price:.4f}, theta={theta:.4f}")

            logger.info("Position prices refreshed from Saxo")
            return True

        except Exception as e:
            logger.error(f"Error refreshing position prices: {e}")
            return False

    def handle_price_update(self, uic: int, data: Dict):
        """
        Handle real-time price update from WebSocket.

        Args:
            uic: Instrument UIC that was updated
            data: Price data from the streaming update
        """
        if uic == self.underlying_uic:
            if "Quote" in data:
                new_price = (
                    data["Quote"].get("Mid") or
                    data["Quote"].get("LastTraded")
                )
                if new_price:
                    old_price = self.current_underlying_price
                    self.current_underlying_price = new_price

                    # Check for recenter condition
                    if self.state == StrategyState.FULL_POSITION:
                        self._check_recenter_condition()

                    logger.debug(f"Price update: ${old_price:.2f} -> ${new_price:.2f}")

    # =========================================================================
    # VIX CHECK
    # =========================================================================

    def check_vix_entry_condition(self) -> bool:
        """
        Check if VIX is below the threshold for entry.

        The strategy only enters when VIX < 18 to avoid entering
        during high volatility periods.

        Returns:
            bool: True if VIX condition is met, False otherwise.
        """
        if self.current_vix <= 0:
            logger.warning("VIX data not available, cannot check entry condition")
            return False

        is_below_threshold = self.current_vix < self.max_vix

        if is_below_threshold:
            logger.info(f"VIX entry condition MET: {self.current_vix:.2f} < {self.max_vix}")
        else:
            logger.info(f"VIX entry condition NOT met: {self.current_vix:.2f} >= {self.max_vix}")

        return is_below_threshold

    def check_fed_meeting_filter(self) -> bool:
        """
        Check if there's an upcoming Fed/FOMC meeting within blackout period.

        Avoids entering positions before major binary events that can cause
        large volatility spikes.

        Returns:
            bool: True if safe to enter (no Fed meeting soon), False otherwise.
        """
        # 2026 FOMC Meeting Dates (update annually)
        # Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
        fomc_dates_2026 = [
            datetime(2026, 1, 28).date(),  # Jan 27-28
            datetime(2026, 3, 18).date(),  # Mar 17-18
            datetime(2026, 5, 6).date(),   # May 5-6
            datetime(2026, 6, 17).date(),  # Jun 16-17
            datetime(2026, 7, 29).date(),  # Jul 28-29
            datetime(2026, 9, 16).date(),  # Sep 15-16
            datetime(2026, 11, 4).date(),  # Nov 3-4
            datetime(2026, 12, 16).date(), # Dec 15-16
        ]

        today = datetime.now().date()
        blackout_days = self.strategy_config.get("fed_blackout_days", 2)

        for meeting_date in fomc_dates_2026:
            days_until_meeting = (meeting_date - today).days

            if 0 <= days_until_meeting <= blackout_days:
                logger.warning(
                    f"Fed meeting on {meeting_date} is in {days_until_meeting} days - "
                    f"within {blackout_days}-day blackout period. Entry blocked."
                )
                return False

        return True

    # =========================================================================
    # SAFETY CHECKS
    # =========================================================================

    def check_shorts_itm_risk(self) -> bool:
        """
        Check if short options are at risk of expiring In-The-Money.

        Video rule: "Never let the shorts go In-The-Money (ITM)"

        Returns:
            bool: True if shorts need immediate action, False if safe.
        """
        if not self.short_strangle or not self.current_underlying_price:
            return False

        call_strike = self.short_strangle.call_strike
        put_strike = self.short_strangle.put_strike
        price = self.current_underlying_price

        # Check if shorts are ITM or dangerously close (within $1 of strike)
        # This is more precise than percentage-based - $1 buffer gives time to react
        itm_buffer = 1.0  # dollars
        call_itm = price >= (call_strike - itm_buffer)
        put_itm = price <= (put_strike + itm_buffer)

        if call_itm:
            distance = call_strike - price
            logger.critical(
                f"SHORT CALL ITM RISK! Price ${price:.2f} is ${distance:.2f} from "
                f"strike ${call_strike:.2f}. Immediate action required."
            )
            return True

        if put_itm:
            distance = price - put_strike
            logger.critical(
                f"SHORT PUT ITM RISK! Price ${price:.2f} is ${distance:.2f} from "
                f"strike ${put_strike:.2f}. Immediate action required."
            )
            return True

        return False

    def check_emergency_exit_condition(self) -> bool:
        """
        Check for massive move that breaches shorts requiring hard exit.

        Video rule: "If massive move (5%+) blows through shorts and can't adjust
        for credit, close entire trade"

        Returns:
            bool: True if emergency exit needed, False otherwise.
        """
        if not self.initial_straddle_strike or not self.current_underlying_price:
            return False

        # Calculate percent move from initial entry
        percent_move = abs(
            (self.current_underlying_price - self.initial_straddle_strike) /
            self.initial_straddle_strike
        ) * 100

        emergency_threshold = self.strategy_config.get("emergency_exit_percent", 5.0)

        if percent_move >= emergency_threshold:
            logger.critical(
                f"EMERGENCY EXIT CONDITION! {percent_move:.2f}% move from initial strike. "
                f"Price: ${self.current_underlying_price:.2f}, Initial: ${self.initial_straddle_strike:.2f}"
            )
            return True

        return False

    # =========================================================================
    # LONG STRADDLE METHODS
    # =========================================================================

    def enter_long_straddle(self) -> bool:
        """
        Enter a new long straddle position.

        Buys 1 ATM Call and 1 ATM Put with 90-120 DTE.
        Only enters if VIX < 18.

        Returns:
            bool: True if straddle entered successfully, False otherwise.
        """
        logger.info("Attempting to enter long straddle...")

        # Check VIX condition
        if not self.check_vix_entry_condition():
            self.state = StrategyState.WAITING_VIX
            return False

        # Check Fed meeting filter
        if not self.check_fed_meeting_filter():
            logger.info("Entry blocked due to upcoming Fed meeting")
            self.state = StrategyState.WAITING_VIX  # Stay in waiting state
            return False

        # Update market data
        if not self.update_market_data():
            logger.error("Failed to update market data before entry")
            return False

        # Find ATM options - closest expiration to target DTE (120 days)
        atm_options = self.client.find_atm_options(
            self.underlying_uic,
            self.current_underlying_price,
            target_dte=self.target_dte
        )

        if not atm_options:
            logger.error("Failed to find ATM options for straddle")
            return False

        call_option = atm_options["call"]
        put_option = atm_options["put"]

        logger.info(f"Checking spreads for Call UIC: {call_option['uic']}, Put UIC: {put_option['uic']}")

        # Check bid-ask spreads
        call_spread_ok, call_spread = self.client.check_bid_ask_spread(
            call_option["uic"],
            "StockOption",
            self.max_spread_percent
        )
        put_spread_ok, put_spread = self.client.check_bid_ask_spread(
            put_option["uic"],
            "StockOption",
            self.max_spread_percent
        )

        if not call_spread_ok or not put_spread_ok:
            logger.warning(
                f"Bid-ask spread too wide. Call: {call_spread:.2f}%, Put: {put_spread:.2f}%"
            )
            return False

        # Get current prices for the options
        call_quote = self.client.get_quote(call_option["uic"], "StockOption")
        put_quote = self.client.get_quote(put_option["uic"], "StockOption")

        if not call_quote or not put_quote:
            logger.error("Failed to get option quotes")
            return False

        call_price = call_quote["Quote"].get("Ask", 0)
        put_price = put_quote["Quote"].get("Ask", 0)

        # Place multi-leg order with slippage protection (limit orders + 60s timeout)
        # Each leg needs price and to_open_close for live trading
        legs = [
            {
                "uic": call_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.position_size,
                "price": call_price * 100,  # Per contract price
                "to_open_close": "ToOpen"
            },
            {
                "uic": put_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.position_size,
                "price": put_price * 100,  # Per contract price
                "to_open_close": "ToOpen"
            }
        ]

        # Calculate limit price (sum of ask prices for buying)
        total_limit_price = (call_price + put_price) * 100 * self.position_size

        # Place order with slippage protection
        order_result = self._place_protected_multi_leg_order(
            legs=legs,
            total_limit_price=total_limit_price,
            order_description="LONG_STRADDLE_ENTRY"
        )

        if not order_result["filled"]:
            logger.error("Failed to place straddle order - slippage protection triggered")
            return False

        order_response = {"OrderId": order_result["order_id"]}

        # Create straddle position object
        self.long_straddle = StraddlePosition(
            call=OptionPosition(
                position_id=str(order_response.get("OrderId", "")) + "_call",
                uic=call_option["uic"],
                strike=call_option["strike"],
                expiry=call_option["expiry"],
                option_type="Call",
                position_type=PositionType.LONG_CALL,
                quantity=self.position_size,
                entry_price=call_price,
                current_price=call_price,
                delta=0.5  # ATM call delta approximation
            ),
            put=OptionPosition(
                position_id=str(order_response.get("OrderId", "")) + "_put",
                uic=put_option["uic"],
                strike=put_option["strike"],
                expiry=put_option["expiry"],
                option_type="Put",
                position_type=PositionType.LONG_PUT,
                quantity=self.position_size,
                entry_price=put_price,
                current_price=put_price,
                delta=-0.5  # ATM put delta approximation
            ),
            initial_strike=call_option["strike"],
            entry_underlying_price=self.current_underlying_price,
            entry_date=datetime.now().isoformat()
        )

        self.initial_straddle_strike = call_option["strike"]

        # Update metrics
        straddle_cost = (call_price + put_price) * self.position_size * 100
        self.metrics.total_straddle_cost += straddle_cost
        if not self.dry_run:
            self.metrics.save_to_file()  # Persist metrics after entry

        self.state = StrategyState.LONG_STRADDLE_ACTIVE

        logger.info(
            f"Long straddle entered: Strike {call_option['strike']}, "
            f"Expiry {call_option['expiry']}, Cost ${straddle_cost:.2f}"
        )

        # Log trade
        if self.trade_logger:
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            dte = self._calculate_dte(call_option["expiry"])
            self.trade_logger.log_trade(
                action=f"{action_prefix}OPEN_LONG_STRADDLE",
                strike=call_option["strike"],
                price=call_price + put_price,
                delta=0.0,  # ATM straddle is approximately delta neutral
                pnl=0.0,
                saxo_client=self.client,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                option_type="Long Straddle",
                expiry_date=call_option["expiry"],
                dte=dte,
                premium_received=None,  # Buying, not receiving premium
                trade_reason="Initial Entry"
            )

            # Add positions to Positions sheet
            self.trade_logger.add_position({
                "type": "Long Call",
                "strike": call_option["strike"],
                "expiry": call_option["expiry"],
                "dte": dte,
                "entry_price": call_price,
                "current_price": call_price,
                "theta": -0.15,  # Approximate theta for ATM call
                "pnl": 0.0,
                "pnl_eur": 0.0,
                "status": "Active"
            })
            self.trade_logger.add_position({
                "type": "Long Put",
                "strike": put_option["strike"],
                "expiry": put_option["expiry"],
                "dte": dte,
                "entry_price": put_price,
                "current_price": put_price,
                "theta": -0.10,  # Approximate theta for ATM put
                "pnl": 0.0,
                "pnl_eur": 0.0,
                "status": "Active"
            })

        return True

    def _enter_straddle_with_options(self, atm_options: dict) -> bool:
        """
        Enter a long straddle using pre-found ATM options.

        Used during recenter to enter at the same expiry as the original straddle,
        rather than using the config's 90-120 DTE range.

        Args:
            atm_options: Dict with 'call' and 'put' option data from find_atm_options

        Returns:
            bool: True if straddle entered successfully, False otherwise.
        """
        call_option = atm_options["call"]
        put_option = atm_options["put"]

        logger.info(f"Entering straddle with pre-found options: Call {call_option['strike']}, Put {put_option['strike']}")

        # Check bid-ask spreads
        call_spread_ok, call_spread = self.client.check_bid_ask_spread(
            call_option["uic"],
            "StockOption",
            self.max_spread_percent
        )
        put_spread_ok, put_spread = self.client.check_bid_ask_spread(
            put_option["uic"],
            "StockOption",
            self.max_spread_percent
        )

        if not call_spread_ok or not put_spread_ok:
            logger.warning(
                f"Bid-ask spread too wide. Call: {call_spread:.2f}%, Put: {put_spread:.2f}%"
            )
            return False

        # Get current prices for the options
        call_quote = self.client.get_quote(call_option["uic"], "StockOption")
        put_quote = self.client.get_quote(put_option["uic"], "StockOption")

        if not call_quote or not put_quote:
            logger.error("Failed to get option quotes")
            return False

        call_price = call_quote["Quote"].get("Ask", 0)
        put_price = put_quote["Quote"].get("Ask", 0)

        # Place multi-leg order with slippage protection (limit orders + 60s timeout)
        # CRITICAL: Recenter must complete to maintain delta neutrality
        legs = [
            {
                "uic": call_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.position_size,
                "price": call_price * 100,
                "to_open_close": "ToOpen"
            },
            {
                "uic": put_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.position_size,
                "price": put_price * 100,
                "to_open_close": "ToOpen"
            }
        ]

        # Calculate limit price (sum of ask prices for buying)
        total_limit_price = (call_price + put_price) * 100 * self.position_size

        # Place order with slippage protection
        order_result = self._place_protected_multi_leg_order(
            legs=legs,
            total_limit_price=total_limit_price,
            order_description="RECENTER_STRADDLE"
        )

        if not order_result["filled"]:
            logger.error("CRITICAL: Failed to place recenter straddle - slippage protection triggered")
            return False

        order_response = {"OrderId": order_result["order_id"]}

        # Create straddle position object
        self.long_straddle = StraddlePosition(
            call=OptionPosition(
                position_id=str(order_response.get("OrderId", "")) + "_call",
                uic=call_option["uic"],
                strike=call_option["strike"],
                expiry=call_option["expiry"],
                option_type="Call",
                position_type=PositionType.LONG_CALL,
                quantity=self.position_size,
                entry_price=call_price,
                current_price=call_price,
                delta=0.5  # ATM call delta approximation
            ),
            put=OptionPosition(
                position_id=str(order_response.get("OrderId", "")) + "_put",
                uic=put_option["uic"],
                strike=put_option["strike"],
                expiry=put_option["expiry"],
                option_type="Put",
                position_type=PositionType.LONG_PUT,
                quantity=self.position_size,
                entry_price=put_price,
                current_price=put_price,
                delta=-0.5  # ATM put delta approximation
            ),
            initial_strike=call_option["strike"],
            entry_underlying_price=self.current_underlying_price,
            entry_date=datetime.now().isoformat()
        )

        self.initial_straddle_strike = call_option["strike"]

        # Update metrics
        straddle_cost = (call_price + put_price) * self.position_size * 100
        self.metrics.total_straddle_cost += straddle_cost
        if not self.dry_run:
            self.metrics.save_to_file()  # Persist metrics after entry

        logger.info(
            f"Long straddle entered (recenter): Strike {call_option['strike']}, "
            f"Expiry {call_option['expiry']}, Cost ${straddle_cost:.2f}"
        )

        # Log trade
        if self.trade_logger:
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            dte = self._calculate_dte(call_option["expiry"])
            self.trade_logger.log_trade(
                action=f"{action_prefix}OPEN_LONG_STRADDLE",
                strike=call_option["strike"],
                price=call_price + put_price,
                delta=0.0,
                pnl=0.0,
                saxo_client=self.client,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                option_type="Long Straddle (Recenter)",
                expiry_date=call_option["expiry"],
                dte=dte,
                premium_received=None,
                trade_reason="5-Point Recenter"
            )

        return True

    def close_long_straddle(self) -> bool:
        """
        Close the current long straddle position with slippage protection.

        Returns:
            bool: True if closed successfully, False otherwise.
        """
        if not self.long_straddle or not self.long_straddle.is_complete:
            logger.warning("No complete long straddle to close")
            return False

        logger.info("Closing long straddle...")

        # Get current prices for the legs
        call_quote = self.client.get_quote(self.long_straddle.call.uic, "StockOption")
        put_quote = self.client.get_quote(self.long_straddle.put.uic, "StockOption")
        call_bid = call_quote["Quote"].get("Bid", 0) if call_quote else 0
        put_bid = put_quote["Quote"].get("Bid", 0) if put_quote else 0

        # Place sell orders for both legs with slippage protection
        legs = [
            {
                "uic": self.long_straddle.call.uic,
                "asset_type": "StockOption",
                "buy_sell": "Sell",
                "amount": self.long_straddle.call.quantity,
                "price": call_bid * 100,
                "to_open_close": "ToClose"
            },
            {
                "uic": self.long_straddle.put.uic,
                "asset_type": "StockOption",
                "buy_sell": "Sell",
                "amount": self.long_straddle.put.quantity,
                "price": put_bid * 100,
                "to_open_close": "ToClose"
            }
        ]

        # Calculate limit price (sum of bid prices for selling)
        total_limit_price = self._calculate_combo_limit_price(legs, "Sell")

        # Place order with slippage protection
        order_result = self._place_protected_multi_leg_order(
            legs=legs,
            total_limit_price=total_limit_price,
            order_description="CLOSE_LONG_STRADDLE"
        )

        if not order_result["filled"]:
            logger.error("Failed to close straddle - slippage protection triggered")
            return False

        # Calculate realized P&L
        entry_cost = (
            self.long_straddle.call.entry_price +
            self.long_straddle.put.entry_price
        ) * self.position_size * 100

        exit_value = self.long_straddle.total_value
        realized_pnl = exit_value - entry_cost
        self.metrics.realized_pnl += realized_pnl
        self.metrics.record_trade(realized_pnl)
        if not self.dry_run:
            self.metrics.save_to_file()  # Persist metrics after trade

        logger.info(
            f"Long straddle closed. Entry cost: ${entry_cost:.2f}, "
            f"Exit value: ${exit_value:.2f}, P&L: ${realized_pnl:.2f}"
        )

        # Log trade (capture expiry before we clear the position)
        straddle_expiry = self.long_straddle.call.expiry if self.long_straddle.call else None
        if self.trade_logger:
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            # Determine trade reason based on current state
            if self.state == StrategyState.RECENTERING:
                reason = "5-Point Recenter"
            else:
                reason = "Exit"
            self.trade_logger.log_trade(
                action=f"{action_prefix}CLOSE_LONG_STRADDLE",
                strike=self.long_straddle.initial_strike,
                price=(self.long_straddle.call.current_price +
                       self.long_straddle.put.current_price),
                delta=self.long_straddle.total_delta,
                pnl=realized_pnl,
                saxo_client=self.client,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                option_type="Long Straddle",
                expiry_date=straddle_expiry,
                dte=self._calculate_dte(straddle_expiry) if straddle_expiry else None,
                premium_received=None,
                trade_reason=reason
            )

            # Remove positions from Positions sheet
            self.trade_logger.remove_position("Long Call", self.long_straddle.initial_strike)
            self.trade_logger.remove_position("Long Put", self.long_straddle.initial_strike)

        self.long_straddle = None
        return True

    # =========================================================================
    # SHORT STRANGLE METHODS
    # =========================================================================

    def enter_short_strangle(self, for_roll: bool = False, quote_only: bool = False) -> bool:
        """
        Enter a weekly short strangle for income generation.

        If weekly_target_return_percent is configured, finds strikes that meet
        the target return. Otherwise uses multiplier on expected move.

        Args:
            for_roll: If True, this is for rolling shorts (look for next week's expiry).
                     If False, this is initial entry (look for current week's expiry).
            quote_only: If True, only fetch quotes to calculate premium (no orders, no logging).
                       Used by roll_weekly_shorts to check if roll would result in net credit.

        Returns:
            bool: True if strangle entered successfully, False otherwise.
        """
        logger.info("Attempting to enter short strangle...")

        # CRITICAL: VIX Defensive Mode Check
        # Per strategy spec: "If the VIX spikes to 25 while a trade is open, the bot
        # should be in Defensive Mode (stop selling new shorts)"
        if self.current_vix and self.current_vix >= self.vix_defensive_threshold:
            logger.warning(f"⚠️ VIX DEFENSIVE MODE ACTIVE - VIX at {self.current_vix:.2f} >= {self.vix_defensive_threshold}")
            logger.warning("Refusing to sell new shorts - focus on managing existing positions")

            # Log safety event
            if self.trade_logger:
                self.trade_logger.log_safety_event({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "event_type": "VIX_DEFENSIVE_MODE",
                    "severity": "WARNING",
                    "spy_price": self.current_underlying_price,
                    "initial_strike": self.initial_straddle_strike,
                    "vix": self.current_vix,
                    "action_taken": "Blocked new short strangle entry - VIX too high",
                    "description": f"VIX {self.current_vix:.2f} >= defensive threshold {self.vix_defensive_threshold}",
                    "result": "BLOCKED"
                })

            return False

        if not self.current_underlying_price:
            if not self.update_market_data():
                return False

        # Check if we should use target return approach
        if self.weekly_target_return_pct and self.weekly_target_return_pct > 0:
            return self._enter_strangle_by_target_return(for_roll=for_roll, quote_only=quote_only)

        # Otherwise use the multiplier approach
        return self._enter_strangle_by_multiplier(for_roll=for_roll, quote_only=quote_only)

    def _enter_strangle_by_target_return(self, for_roll: bool = False, quote_only: bool = False) -> bool:
        """
        Enter strangle based on target NET weekly return percentage.

        NEW LOGIC (Brian Terry 1% NET of Long Straddle Cost):
        1. Calculate target 1% NET of long straddle cost (not margin)
        2. Find widest strikes that meet the minimum required gross premium
        3. Cap any strike beyond 1.5x expected move back to 1.5x
        4. Optimize: push tighter strikes OUT while staying >= 1% NET

        Net Return = (Gross Premium - Theta Cost - Entry Fees) / Long Straddle Cost

        Args:
            for_roll: If True, look for next week's expiry (rolling shorts).
            quote_only: If True, only calculate quotes/premium without placing orders or logging.

        Returns:
            bool: True if strangle entered successfully, False otherwise.
        """
        logger.info("=" * 70)
        logger.info(f"1% NET OF LONG STRADDLE COST MODE")
        logger.info(f"Target: {self.weekly_target_return_pct}% NET | Max Multiplier: {self.short_strangle_max_multiplier}x")
        logger.info("=" * 70)

        # =====================================================================
        # STEP 1: Calculate target based on LONG STRADDLE COST (not margin)
        # =====================================================================
        long_straddle_cost = self._get_long_straddle_cost()
        if long_straddle_cost <= 0:
            logger.error("Cannot determine long straddle cost - cannot calculate target")
            return False

        weekly_theta_cost = self._get_long_straddle_weekly_theta()
        total_entry_fees = self.short_strangle_entry_fee_per_leg * 2 * self.position_size

        # Target NET = 1% of long straddle cost
        target_net = long_straddle_cost * (self.weekly_target_return_pct / 100)

        # Required gross = target NET + theta + fees
        required_gross = target_net + weekly_theta_cost + total_entry_fees

        logger.info(f"SPY: ${self.current_underlying_price:.2f}")
        logger.info(f"Long Straddle Cost: ${long_straddle_cost:,.2f}")
        logger.info(f"Target NET ({self.weekly_target_return_pct}%): ${target_net:.2f}")
        logger.info(f"Weekly Theta Cost: ${weekly_theta_cost:.2f}")
        logger.info(f"Entry Fees: ${total_entry_fees:.2f}")
        logger.info(f"REQUIRED GROSS PREMIUM: ${required_gross:.2f}")

        # =====================================================================
        # STEP 2: Get expected move from ATM straddle
        # =====================================================================
        expected_move = self.client.get_expected_move_from_straddle(
            self.underlying_uic,
            self.current_underlying_price,
            for_roll=for_roll
        )

        if not expected_move:
            logger.error("Failed to get expected move from straddle prices")
            return False

        logger.info(f"Expected Move: ±${expected_move:.2f} ({(expected_move/self.current_underlying_price)*100:.2f}%)")

        # =====================================================================
        # STEP 3: Scan all available strikes and collect premium data
        # =====================================================================
        expirations = self.client.get_option_expirations(self.underlying_uic)
        if not expirations:
            logger.error("Failed to get option expirations")
            return False

        # Find the next Friday expiration
        # ALWAYS use next Friday - short strangles must have 7+ DTE
        # This ensures we never enter short-dated positions that could get
        # exercised before we can roll them
        today = datetime.now().date()
        weekly_exp = None

        # Collect all Friday expirations with 7+ DTE
        friday_candidates = []
        for exp_data in expirations:
            exp_date_str = exp_data.get("Expiry", "")[:10]
            if exp_date_str:
                exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days

                # Check if this is a Friday (weekday() == 4)
                if exp_date.weekday() != 4:
                    continue

                # Always use next Friday (7+ days out)
                if dte >= 7:
                    friday_candidates.append((dte, exp_date_str, exp_data))

        # Sort by DTE to get the nearest Friday
        if friday_candidates:
            friday_candidates.sort(key=lambda x: x[0])
            weekly_dte, weekly_expiry, weekly_exp = friday_candidates[0]
            logger.info(f"Next Friday Expiry: {weekly_expiry} ({weekly_dte} DTE)")
        else:
            logger.error("Could not find next Friday expiration (7+ DTE)")
            return False

        # Collect all OTM options with their premiums
        calls = []
        puts = []
        specific_options = weekly_exp.get("SpecificOptions", [])

        for opt in specific_options:
            strike = opt.get("StrikePrice", 0)
            uic = opt.get("Uic")
            put_call = opt.get("PutCall")

            # Only look at strikes within reasonable range (20 points)
            if strike < self.current_underlying_price - 20 or strike > self.current_underlying_price + 20:
                continue

            quote = self.client.get_quote(uic, "StockOption")
            if not quote:
                continue

            bid = quote["Quote"].get("Bid", 0) or 0
            if bid <= 0:
                continue

            distance = abs(strike - self.current_underlying_price)
            mult = distance / expected_move if expected_move > 0 else 0
            premium = bid * 100 * self.position_size

            data = {
                "strike": strike,
                "uic": uic,
                "bid": bid,
                "premium": premium,
                "distance": distance,
                "mult": mult,
                "expiry": weekly_exp.get("Expiry", "")[:10]
            }

            if put_call == "Call" and strike > self.current_underlying_price:
                calls.append(data)
            elif put_call == "Put" and strike < self.current_underlying_price:
                puts.append(data)

        if not calls or not puts:
            logger.error("Could not find sufficient OTM options")
            return False

        # Sort: calls by strike ascending (closest to furthest OTM)
        # puts by strike descending (closest to furthest OTM)
        calls.sort(key=lambda x: x["strike"])
        puts.sort(key=lambda x: x["strike"], reverse=True)

        logger.info(f"Found {len(calls)} OTM calls, {len(puts)} OTM puts")

        # =====================================================================
        # STEP 4: Find optimal strikes with fallback logic
        # =====================================================================
        # Strategy:
        # 1. Start at 1.0x minimum multiplier
        # 2. Find combinations, apply 1.5x cap, optimize for safety
        # 3. If no valid options found, progressively reduce minimum multiplier
        # 4. Track which strikes were capped so we can reverse if needed
        # =====================================================================

        max_mult = self.short_strangle_max_multiplier  # 1.5x cap
        min_target_return = self.weekly_target_return_pct

        # Try progressively lower minimum multipliers until we find valid options
        MIN_MULT_ATTEMPTS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5]

        final_call = None
        final_put = None

        for min_mult_threshold in MIN_MULT_ATTEMPTS:
            logger.info(f"Trying with MIN_MULTIPLIER = {min_mult_threshold}x...")

            # Find all combinations meeting minimum requirements
            combinations = []
            for c in calls:
                for p in puts:
                    if c["mult"] < min_mult_threshold or p["mult"] < min_mult_threshold:
                        continue

                    gross = c["premium"] + p["premium"]
                    if gross < required_gross:
                        continue

                    net = gross - weekly_theta_cost - total_entry_fees
                    net_return = (net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0
                    min_mult = min(c["mult"], p["mult"])

                    combinations.append({
                        "call": c,
                        "put": p,
                        "gross": gross,
                        "net": net,
                        "return": net_return,
                        "min_mult": min_mult
                    })

            if not combinations:
                logger.info(f"  No combinations at {min_mult_threshold}x minimum")
                continue

            # Sort by minimum multiplier (widest/safest first)
            combinations.sort(key=lambda x: x["min_mult"], reverse=True)
            logger.info(f"  Found {len(combinations)} combinations")

            # Start with the widest combination
            best = combinations[0]
            working_call = best["call"]
            working_put = best["put"]

            # Track original strikes before any capping
            original_call = working_call
            original_put = working_put
            call_was_capped = False
            put_was_capped = False

            logger.info(f"  Widest: Put ${working_put['strike']:.0f} ({working_put['mult']:.2f}x) / Call ${working_call['strike']:.0f} ({working_call['mult']:.2f}x)")

            # Apply 1.5x cap - track which sides were capped
            # We want the option CLOSEST to 1.5x (but not over), so iterate from furthest to closest
            if working_call["mult"] > max_mult:
                call_was_capped = True
                # Calls are sorted ascending (closest first), so reverse to check furthest first
                for c in reversed(calls):
                    if c["mult"] <= max_mult:
                        working_call = c
                        logger.info(f"  Call capped: ${original_call['strike']:.0f} ({original_call['mult']:.2f}x) -> ${working_call['strike']:.0f} ({working_call['mult']:.2f}x)")
                        break

            if working_put["mult"] > max_mult:
                put_was_capped = True
                # Puts are sorted descending (closest first), so reverse to check furthest first
                for p in reversed(puts):
                    if p["mult"] <= max_mult:
                        working_put = p
                        logger.info(f"  Put capped: ${original_put['strike']:.0f} ({original_put['mult']:.2f}x) -> ${working_put['strike']:.0f} ({working_put['mult']:.2f}x)")
                        break

            # Optimize: Push tighter strike OUT while staying >= target
            current_gross = working_call["premium"] + working_put["premium"]
            current_net = current_gross - weekly_theta_cost - total_entry_fees
            current_return = (current_net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

            # Track optimization changes
            call_was_optimized = False
            put_was_optimized = False
            pre_opt_call = working_call
            pre_opt_put = working_put

            if current_return > min_target_return:
                # Find tighter side and push out
                if working_call["mult"] < working_put["mult"]:
                    # Call is tighter, try wider calls
                    for c in calls:
                        if c["mult"] > max_mult or c["strike"] <= working_call["strike"]:
                            continue
                        test_gross = c["premium"] + working_put["premium"]
                        test_net = test_gross - weekly_theta_cost - total_entry_fees
                        test_return = (test_net / long_straddle_cost) * 100
                        if test_return >= min_target_return:
                            working_call = c
                            call_was_optimized = True
                        else:
                            break
                else:
                    # Put is tighter, try wider puts
                    for p in puts:
                        if p["mult"] > max_mult or p["strike"] >= working_put["strike"]:
                            continue
                        test_gross = working_call["premium"] + p["premium"]
                        test_net = test_gross - weekly_theta_cost - total_entry_fees
                        test_return = (test_net / long_straddle_cost) * 100
                        if test_return >= min_target_return:
                            working_put = p
                            put_was_optimized = True
                        else:
                            break

                # Try pushing OTHER leg too
                current_gross = working_call["premium"] + working_put["premium"]
                current_net = current_gross - weekly_theta_cost - total_entry_fees
                current_return = (current_net / long_straddle_cost) * 100

                if current_return > min_target_return:
                    if working_call["mult"] < working_put["mult"]:
                        for c in calls:
                            if c["mult"] > max_mult or c["strike"] <= working_call["strike"]:
                                continue
                            test_gross = c["premium"] + working_put["premium"]
                            test_net = test_gross - weekly_theta_cost - total_entry_fees
                            test_return = (test_net / long_straddle_cost) * 100
                            if test_return >= min_target_return:
                                working_call = c
                                call_was_optimized = True
                            else:
                                break
                    else:
                        for p in puts:
                            if p["mult"] > max_mult or p["strike"] >= working_put["strike"]:
                                continue
                            test_gross = working_call["premium"] + p["premium"]
                            test_net = test_gross - weekly_theta_cost - total_entry_fees
                            test_return = (test_net / long_straddle_cost) * 100
                            if test_return >= min_target_return:
                                working_put = p
                                put_was_optimized = True
                            else:
                                break

            # Now verify we can actually get quotes for these strikes
            # If not, use fallback logic to reverse changes
            call_quote = self.client.get_quote(working_call["uic"], "StockOption")
            put_quote = self.client.get_quote(working_put["uic"], "StockOption")

            if call_quote and put_quote:
                # Check bid prices are still valid
                call_bid = call_quote["Quote"].get("Bid", 0) or 0
                put_bid = put_quote["Quote"].get("Bid", 0) or 0

                if call_bid > 0 and put_bid > 0:
                    # Update with fresh prices
                    working_call["bid"] = call_bid
                    working_call["premium"] = call_bid * 100 * self.position_size
                    working_put["bid"] = put_bid
                    working_put["premium"] = put_bid * 100 * self.position_size

                    final_call = working_call
                    final_put = working_put
                    logger.info(f"  SUCCESS: Found valid options at {min_mult_threshold}x minimum")
                    break

            # Fallback: Reverse optimization changes first
            logger.info(f"  Options unavailable, trying fallback...")

            fallback_attempts = []

            # 1. If call was optimized, try pre-optimization call
            if call_was_optimized:
                fallback_attempts.append((pre_opt_call, working_put, "reverse call optimization"))

            # 2. If put was optimized, try pre-optimization put
            if put_was_optimized:
                fallback_attempts.append((working_call, pre_opt_put, "reverse put optimization"))

            # 3. If call was capped, try original (wider) call
            if call_was_capped:
                fallback_attempts.append((original_call, working_put, "reverse call cap"))

            # 4. If put was capped, try original (wider) put
            if put_was_capped:
                fallback_attempts.append((working_call, original_put, "reverse put cap"))

            # 5. Try both original (pre-cap) strikes
            if call_was_capped or put_was_capped:
                fallback_attempts.append((original_call, original_put, "both original strikes"))

            for fb_call, fb_put, fb_desc in fallback_attempts:
                fb_call_quote = self.client.get_quote(fb_call["uic"], "StockOption")
                fb_put_quote = self.client.get_quote(fb_put["uic"], "StockOption")

                if fb_call_quote and fb_put_quote:
                    fb_call_bid = fb_call_quote["Quote"].get("Bid", 0) or 0
                    fb_put_bid = fb_put_quote["Quote"].get("Bid", 0) or 0

                    if fb_call_bid > 0 and fb_put_bid > 0:
                        fb_call["bid"] = fb_call_bid
                        fb_call["premium"] = fb_call_bid * 100 * self.position_size
                        fb_put["bid"] = fb_put_bid
                        fb_put["premium"] = fb_put_bid * 100 * self.position_size

                        final_call = fb_call
                        final_put = fb_put
                        logger.info(f"  FALLBACK SUCCESS: {fb_desc}")
                        break

            if final_call and final_put:
                break

        if not final_call or not final_put:
            logger.error("No valid strike combinations found after all fallback attempts")
            logger.error("Tried minimum multipliers: " + ", ".join([f"{m}x" for m in MIN_MULT_ATTEMPTS]))
            return False

        # =====================================================================
        # STEP 5: Calculate final P&L and log results
        # =====================================================================
        final_gross = final_call["premium"] + final_put["premium"]
        final_net = final_gross - weekly_theta_cost - total_entry_fees
        final_return = (final_net / long_straddle_cost) * 100 if long_straddle_cost > 0 else 0

        logger.info("=" * 70)
        logger.info("FINAL STRIKE SELECTION")
        logger.info("=" * 70)
        logger.info(f"Short Put:  ${final_put['strike']:.0f} @ ${final_put['bid']:.2f} = ${final_put['premium']:.2f} ({final_put['mult']:.2f}x exp move)")
        logger.info(f"Short Call: ${final_call['strike']:.0f} @ ${final_call['bid']:.2f} = ${final_call['premium']:.2f} ({final_call['mult']:.2f}x exp move)")
        logger.info("-" * 70)
        logger.info(f"Gross Premium:     +${final_gross:.2f}")
        logger.info(f"Weekly Theta:      -${weekly_theta_cost:.2f}")
        logger.info(f"Entry Fees:        -${total_entry_fees:.2f}")
        logger.info(f"NET Premium:       +${final_net:.2f}")
        logger.info(f"NET Return:        {final_return:.2f}% (target was {self.weekly_target_return_pct}%)")
        logger.info(f"Profit Zone: ${final_put['strike']:.0f} - ${final_call['strike']:.0f} (${final_call['strike'] - final_put['strike']:.0f} points)")
        logger.info("=" * 70)

        # Check minimum return
        if final_return < min_target_return:
            logger.warning(f"Final return {final_return:.2f}% is below target {min_target_return}%")
            logger.warning("Proceeding anyway - this is the best available after applying caps")

        # Prepare option data for order execution
        call_option = {
            "uic": final_call["uic"],
            "strike": final_call["strike"],
            "expiry": final_call["expiry"],
            "bid": final_call["bid"]
        }
        put_option = {
            "uic": final_put["uic"],
            "strike": final_put["strike"],
            "expiry": final_put["expiry"],
            "bid": final_put["bid"]
        }

        # Continue with order placement (or just quote calculation if quote_only)
        return self._execute_strangle_order(call_option, put_option, final_call["bid"], final_put["bid"], quote_only=quote_only)

    def _enter_strangle_by_multiplier(self, for_roll: bool = False, quote_only: bool = False) -> bool:
        """
        Enter strangle using expected move multiplier approach.

        This is the original approach: calculate expected move from VIX and apply multiplier.

        Args:
            for_roll: If True, look for next week's expiry (rolling shorts).
            quote_only: If True, only calculate quotes/premium without placing orders or logging.
        """
        logger.info("Using expected move multiplier approach")

        # First, get the weekly expiration to determine actual DTE
        expirations = self.client.get_option_expirations(self.underlying_uic)
        if not expirations:
            logger.error("Failed to get option expirations")
            return False

        # Find actual DTE for weekly options
        # When rolling: look for next week (5-12 DTE)
        # When entering fresh: look for current week (0-7 DTE)
        from datetime import datetime
        today = datetime.now().date()
        weekly_dte = 7  # Default fallback

        dte_min = 5 if for_roll else 0
        dte_max = 12 if for_roll else 7

        for exp_data in expirations:
            exp_date_str = exp_data.get("Expiry")
            if exp_date_str:
                exp_date = datetime.strptime(exp_date_str[:10], "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if dte_min < dte <= dte_max:
                    weekly_dte = dte
                    logger.info(f"Found weekly expiration with {dte} DTE")
                    break

        # Calculate expected move from ATM straddle price (accurate market-based calculation)
        expected_move = self.client.get_expected_move_from_straddle(
            self.underlying_uic,
            self.current_underlying_price,
            for_roll=for_roll
        )

        if not expected_move:
            logger.error("Failed to get expected move from straddle prices")
            return False

        logger.info(f"Weekly expected move from ATM straddle: ${expected_move:.2f}")

        # Use middle of the multiplier range
        multiplier = (self.strangle_multiplier_min + self.strangle_multiplier_max) / 2

        # Find strangle options
        strangle_options = self.client.find_strangle_options(
            self.underlying_uic,
            self.current_underlying_price,
            expected_move,
            multiplier,
            weekly=True,
            for_roll=for_roll
        )

        if not strangle_options:
            logger.error("Failed to find strangle options")
            return False

        call_option = strangle_options["call"]
        put_option = strangle_options["put"]

        # Check bid-ask spreads
        call_spread_ok, _ = self.client.check_bid_ask_spread(
            call_option["uic"],
            "StockOption",
            self.max_spread_percent
        )
        put_spread_ok, _ = self.client.check_bid_ask_spread(
            put_option["uic"],
            "StockOption",
            self.max_spread_percent
        )

        if not call_spread_ok or not put_spread_ok:
            logger.warning("Bid-ask spread too wide for strangle")
            return False

        # Get current prices
        call_quote = self.client.get_quote(call_option["uic"], "StockOption")
        put_quote = self.client.get_quote(put_option["uic"], "StockOption")

        if not call_quote or not put_quote:
            logger.error("Failed to get strangle option quotes")
            return False

        call_price = call_quote["Quote"].get("Bid", 0)
        put_price = put_quote["Quote"].get("Bid", 0)

        return self._execute_strangle_order(call_option, put_option, call_price, put_price, quote_only=quote_only)

    def _execute_strangle_order(
        self,
        call_option: dict,
        put_option: dict,
        call_price: float,
        put_price: float,
        quote_only: bool = False
    ) -> bool:
        """
        Execute the strangle order with slippage protection.
        Shared by both target return and multiplier approaches.

        If quote_only=True, just updates the internal strangle position object
        to calculate premium without placing orders or logging trades.
        """

        # If quote_only, just create the position object to calculate premium
        # Don't place orders, don't log trades, don't update metrics
        if quote_only:
            # Create temporary strangle position just for premium calculation
            self.short_strangle = StranglePosition(
                call=OptionPosition(
                    position_id="QUOTE_ONLY_call",
                    uic=call_option["uic"],
                    strike=call_option["strike"],
                    expiry=call_option["expiry"],
                    option_type="Call",
                    position_type=PositionType.SHORT_CALL,
                    quantity=self.position_size,
                    entry_price=call_price,
                    current_price=call_price,
                    delta=-0.15
                ),
                put=OptionPosition(
                    position_id="QUOTE_ONLY_put",
                    uic=put_option["uic"],
                    strike=put_option["strike"],
                    expiry=put_option["expiry"],
                    option_type="Put",
                    position_type=PositionType.SHORT_PUT,
                    quantity=self.position_size,
                    entry_price=put_price,
                    current_price=put_price,
                    delta=0.15
                ),
                call_strike=call_option["strike"],
                put_strike=put_option["strike"],
                expiry=call_option["expiry"]
            )
            # Return True to indicate quotes were successfully fetched
            return True

        # Place sell orders for strangle with slippage protection
        legs = [
            {
                "uic": call_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Sell",
                "amount": self.position_size,
                "price": call_price * 100,
                "to_open_close": "ToOpen"  # Selling to open a short position
            },
            {
                "uic": put_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Sell",
                "amount": self.position_size,
                "price": put_price * 100,
                "to_open_close": "ToOpen"  # Selling to open a short position
            }
        ]

        # Calculate limit price (sum of bid prices for selling)
        total_limit_price = (call_price + put_price) * 100 * self.position_size

        # Place order with slippage protection
        order_result = self._place_protected_multi_leg_order(
            legs=legs,
            total_limit_price=total_limit_price,
            order_description="SHORT_STRANGLE_ENTRY"
        )

        if not order_result["filled"]:
            logger.error("Failed to place strangle order - slippage protection triggered")
            return False

        order_response = {"OrderId": order_result["order_id"]}

        # Create strangle position object
        self.short_strangle = StranglePosition(
            call=OptionPosition(
                position_id=str(order_response.get("OrderId", "")) + "_call",
                uic=call_option["uic"],
                strike=call_option["strike"],
                expiry=call_option["expiry"],
                option_type="Call",
                position_type=PositionType.SHORT_CALL,
                quantity=self.position_size,
                entry_price=call_price,
                current_price=call_price,
                delta=-0.15  # OTM short call delta approximation
            ),
            put=OptionPosition(
                position_id=str(order_response.get("OrderId", "")) + "_put",
                uic=put_option["uic"],
                strike=put_option["strike"],
                expiry=put_option["expiry"],
                option_type="Put",
                position_type=PositionType.SHORT_PUT,
                quantity=self.position_size,
                entry_price=put_price,
                current_price=put_price,
                delta=0.15  # OTM short put delta approximation
            ),
            call_strike=call_option["strike"],
            put_strike=put_option["strike"],
            expiry=call_option["expiry"]
        )

        # Update metrics
        premium = self.short_strangle.premium_collected
        self.metrics.total_premium_collected += premium
        if not self.dry_run:
            self.metrics.save_to_file()  # Persist metrics after entry

        self.state = StrategyState.FULL_POSITION

        logger.info(
            f"Short strangle entered: Put {put_option['strike']} / Call {call_option['strike']}, "
            f"Expiry {call_option['expiry']}, Premium ${premium:.2f}"
        )

        # Log each leg individually to Trades tab for detailed premium tracking
        if self.trade_logger:
            action_prefix = "[SIMULATED] " if self.dry_run else ""

            # Log Short Call
            self.trade_logger.log_trade(
                action=f"{action_prefix}OPEN_SHORT_CALL",
                strike=call_option['strike'],
                price=call_price,
                delta=-0.15,  # Approximation for OTM call
                pnl=0.0,
                saxo_client=self.client,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                option_type="Short Call",
                expiry_date=call_option['expiry'],
                dte=self._calculate_dte(call_option['expiry']),
                premium_received=call_price * self.position_size * 100
            )

            # Log Short Put
            self.trade_logger.log_trade(
                action=f"{action_prefix}OPEN_SHORT_PUT",
                strike=put_option['strike'],
                price=put_price,
                delta=0.15,  # Approximation for OTM put
                pnl=0.0,
                saxo_client=self.client,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                option_type="Short Put",
                expiry_date=put_option['expiry'],
                dte=self._calculate_dte(put_option['expiry']),
                premium_received=put_price * self.position_size * 100
            )

            logger.info(f"Logged short strangle legs to Trades: Call ${call_option['strike']} (+${call_price * 100:.2f}), Put ${put_option['strike']} (+${put_price * 100:.2f})")

            # Add positions to Positions sheet
            call_dte = self._calculate_dte(call_option['expiry'])
            put_dte = self._calculate_dte(put_option['expiry'])
            self.trade_logger.add_position({
                "type": "Short Call",
                "strike": call_option['strike'],
                "expiry": call_option['expiry'],
                "dte": call_dte,
                "entry_price": call_price,
                "current_price": call_price,
                "theta": 0.30,  # Approximate theta for OTM short call (positive = income)
                "pnl": 0.0,
                "pnl_eur": 0.0,
                "status": "Active"
            })
            self.trade_logger.add_position({
                "type": "Short Put",
                "strike": put_option['strike'],
                "expiry": put_option['expiry'],
                "dte": put_dte,
                "entry_price": put_price,
                "current_price": put_price,
                "theta": 0.30,  # Approximate theta for OTM short put (positive = income)
                "pnl": 0.0,
                "pnl_eur": 0.0,
                "status": "Active"
            })

        return True

    def close_short_strangle(self) -> bool:
        """
        Close the current short strangle position with slippage protection.

        Returns:
            bool: True if closed successfully, False otherwise.
        """
        if not self.short_strangle or not self.short_strangle.is_complete:
            logger.warning("No complete short strangle to close")
            return False

        logger.info("Closing short strangle...")

        # Get current prices for the legs
        call_quote = self.client.get_quote(self.short_strangle.call.uic, "StockOption")
        put_quote = self.client.get_quote(self.short_strangle.put.uic, "StockOption")
        call_ask = call_quote["Quote"].get("Ask", 0) if call_quote else 0
        put_ask = put_quote["Quote"].get("Ask", 0) if put_quote else 0

        # Place buy orders to close with slippage protection
        legs = [
            {
                "uic": self.short_strangle.call.uic,
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.short_strangle.call.quantity,
                "price": call_ask * 100,
                "to_open_close": "ToClose"
            },
            {
                "uic": self.short_strangle.put.uic,
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.short_strangle.put.quantity,
                "price": put_ask * 100,
                "to_open_close": "ToClose"
            }
        ]

        # Calculate limit price (sum of ask prices for buying back)
        total_limit_price = self._calculate_combo_limit_price(legs, "Buy")

        # Place order with slippage protection
        order_result = self._place_protected_multi_leg_order(
            legs=legs,
            total_limit_price=total_limit_price,
            order_description="CLOSE_SHORT_STRANGLE"
        )

        if not order_result["filled"]:
            logger.error("Failed to close strangle - slippage protection triggered")
            return False

        # Calculate P&L
        premium_received = self.short_strangle.premium_collected

        # Get current prices to calculate close cost
        call_quote = self.client.get_quote(self.short_strangle.call.uic, "StockOption")
        put_quote = self.client.get_quote(self.short_strangle.put.uic, "StockOption")

        close_cost = 0.0
        if call_quote and put_quote:
            close_cost = (
                call_quote["Quote"].get("Ask", 0) +
                put_quote["Quote"].get("Ask", 0)
            ) * self.position_size * 100

        realized_pnl = premium_received - close_cost
        self.metrics.realized_pnl += realized_pnl
        self.metrics.record_trade(realized_pnl)
        if not self.dry_run:
            self.metrics.save_to_file()  # Persist metrics after trade

        logger.info(
            f"Short strangle closed. Premium: ${premium_received:.2f}, "
            f"Close cost: ${close_cost:.2f}, P&L: ${realized_pnl:.2f}"
        )

        # Log trade (capture strikes/expiry before we clear the position)
        strangle_expiry = self.short_strangle.call.expiry if self.short_strangle.call else None
        call_strike = self.short_strangle.call_strike
        put_strike = self.short_strangle.put_strike
        if self.trade_logger:
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            self.trade_logger.log_trade(
                action=f"{action_prefix}CLOSE_SHORT_STRANGLE",
                strike=f"{put_strike}/{call_strike}",
                price=close_cost / (self.position_size * 100),
                delta=self.short_strangle.total_delta,
                pnl=realized_pnl,
                saxo_client=self.client,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                option_type="Short Strangle",
                expiry_date=strangle_expiry,
                dte=self._calculate_dte(strangle_expiry) if strangle_expiry else None,
                premium_received=premium_received
            )

            # Remove positions from Positions sheet
            self.trade_logger.remove_position("Short Call", call_strike)
            self.trade_logger.remove_position("Short Put", put_strike)

        self.short_strangle = None
        return True

    # =========================================================================
    # THETA CALCULATION FOR NET RETURN
    # =========================================================================

    def _get_long_straddle_weekly_theta(self) -> float:
        """
        Get the weekly theta cost of the long straddle position.

        Theta represents daily decay, so we multiply by 7 for weekly cost.
        This is used to calculate the NET return after accounting for
        the cost of holding the long straddle hedge.

        Returns:
            float: Weekly theta cost in dollars (positive value = cost).
                   Returns 0 if unable to get theta.
        """
        weekly_theta_cost = 0.0

        # If we have an existing long straddle position, get its theta
        if self.long_straddle:
            call_theta = 0.0
            put_theta = 0.0

            # Try to get theta from Saxo API
            if self.long_straddle.call and self.long_straddle.call.uic:
                greeks = self.client.get_option_greeks(self.long_straddle.call.uic)
                if greeks:
                    # Theta is typically negative (decay), we want the absolute cost
                    call_theta = abs(greeks.get("Theta", 0))
                    logger.debug(f"Long call theta: {call_theta}")

            if self.long_straddle.put and self.long_straddle.put.uic:
                greeks = self.client.get_option_greeks(self.long_straddle.put.uic)
                if greeks:
                    put_theta = abs(greeks.get("Theta", 0))
                    logger.debug(f"Long put theta: {put_theta}")

            # Daily theta cost per contract (in dollars)
            # Theta is per-share, multiply by 100 for per-contract
            daily_theta_cost = (call_theta + put_theta) * 100 * self.position_size
            weekly_theta_cost = daily_theta_cost * 7

            logger.info(f"Long straddle theta: Call={call_theta:.4f}, Put={put_theta:.4f}")
            logger.info(f"Daily theta cost: ${daily_theta_cost:.2f}, Weekly: ${weekly_theta_cost:.2f}")

        else:
            # No position yet - estimate theta for ATM options at target DTE
            # This is used during initial entry when we don't have positions yet
            logger.info("No existing long straddle - estimating theta for ATM options")

            # Get ATM options at target DTE to estimate theta
            atm_options = self.client.find_atm_options(
                self.underlying_uic,
                self.current_underlying_price,
                target_dte=self.target_dte
            )

            if atm_options:
                call_theta = 0.0
                put_theta = 0.0

                call_greeks = self.client.get_option_greeks(atm_options["call"]["uic"])
                if call_greeks:
                    call_theta = abs(call_greeks.get("Theta", 0))

                put_greeks = self.client.get_option_greeks(atm_options["put"]["uic"])
                if put_greeks:
                    put_theta = abs(put_greeks.get("Theta", 0))

                daily_theta_cost = (call_theta + put_theta) * 100 * self.position_size
                weekly_theta_cost = daily_theta_cost * 7

                logger.info(f"Estimated theta for {self.target_dte} DTE ATM options:")
                logger.info(f"  Call theta: {call_theta:.4f}, Put theta: {put_theta:.4f}")
                logger.info(f"  Daily cost: ${daily_theta_cost:.2f}, Weekly: ${weekly_theta_cost:.2f}")
            else:
                logger.warning("Could not estimate theta - using 0 (will underestimate required premium)")

        return weekly_theta_cost

    def _get_long_straddle_cost(self) -> float:
        """
        Get the total cost of the current long straddle position.

        This is used as the base for calculating the 1% NET weekly return target.
        Per Brian Terry's strategy, the target return is based on the long straddle
        investment, not on margin requirements.

        Returns:
            float: Total long straddle cost in dollars.
                   Returns 0 if no position exists or cost cannot be determined.
        """
        if self.long_straddle and self.long_straddle.call and self.long_straddle.put:
            call_cost = self.long_straddle.call.entry_price * 100 * self.position_size
            put_cost = self.long_straddle.put.entry_price * 100 * self.position_size
            total_cost = call_cost + put_cost
            logger.debug(f"Long straddle cost: Call ${call_cost:.2f} + Put ${put_cost:.2f} = ${total_cost:.2f}")
            return total_cost

        # Fallback to persisted metrics if position not loaded but cost was tracked
        if self.metrics.total_straddle_cost > 0:
            logger.debug(f"Using persisted straddle cost: ${self.metrics.total_straddle_cost:.2f}")
            return self.metrics.total_straddle_cost

        # If no position yet, estimate based on current ATM prices
        if self.current_underlying_price:
            atm_options = self.client.find_atm_options(
                self.underlying_uic,
                self.current_underlying_price,
                target_dte=self.target_dte
            )
            if atm_options:
                call_quote = self.client.get_quote(atm_options["call"]["uic"], "StockOption")
                put_quote = self.client.get_quote(atm_options["put"]["uic"], "StockOption")

                if call_quote and put_quote:
                    call_mid = call_quote["Quote"].get("Mid") or (
                        (call_quote["Quote"].get("Bid", 0) + call_quote["Quote"].get("Ask", 0)) / 2
                    )
                    put_mid = put_quote["Quote"].get("Mid") or (
                        (put_quote["Quote"].get("Bid", 0) + put_quote["Quote"].get("Ask", 0)) / 2
                    )
                    estimated_cost = (call_mid + put_mid) * 100 * self.position_size
                    logger.info(f"Estimated long straddle cost (no position): ${estimated_cost:.2f}")
                    return estimated_cost

        logger.warning("Could not determine long straddle cost")
        return 0.0

    # =========================================================================
    # 5-POINT RECENTERING LOGIC
    # =========================================================================

    def _check_recenter_condition(self) -> bool:
        """
        Check if the 5-point recenter condition is met.

        The position should be recentered if the underlying price moves
        5 or more points from the initial straddle strike.

        Returns:
            bool: True if recenter is needed, False otherwise.
        """
        if not self.initial_straddle_strike:
            return False

        price_move = abs(self.current_underlying_price - self.initial_straddle_strike)

        if price_move >= self.recenter_threshold:
            direction = "up" if self.current_underlying_price > self.initial_straddle_strike else "down"
            logger.info(
                f"RECENTER CONDITION MET: {self.underlying_symbol} moved {price_move:.2f} points {direction} "
                f"from initial strike {self.initial_straddle_strike:.2f} to {self.current_underlying_price:.2f}"
            )
            return True

        return False

    def execute_recenter(self) -> bool:
        """
        Execute the 5-point recentering procedure.

        Per Brian Terry's strategy:
        1. Close the current long straddle
        2. Open a new ATM long straddle at the same expiration
        3. KEEP existing short strangle (don't close it during recenter)

        Short strangle is only rolled/adjusted:
        - On Friday (normal weekly roll)
        - When a strike is challenged (defensive roll)

        Returns:
            bool: True if recenter successful, False otherwise.
        """
        logger.info("=" * 50)
        logger.info("EXECUTING 5-POINT RECENTER")
        logger.info("=" * 50)

        self.state = StrategyState.RECENTERING

        # Store the original expiry to maintain it
        original_expiry = None
        if self.long_straddle and self.long_straddle.call:
            original_expiry = self.long_straddle.call.expiry

        # CRITICAL FIX: Save the previous state before RECENTERING
        # If recenter fails, we need to restore the state to avoid being stuck
        previous_state = StrategyState.FULL_POSITION if self.short_strangle else StrategyState.LONG_STRADDLE_ACTIVE

        # Step 1: Close current long straddle
        if self.long_straddle:
            if not self.close_long_straddle():
                logger.error("Failed to close long straddle during recenter")
                # CRITICAL FIX: Restore state before returning to avoid stuck RECENTERING
                self.state = previous_state
                self._increment_failure_count("recenter_close_failed")
                return False

        # Step 2: Open new ATM long straddle at same expiration
        # We need to find ATM options at the new price but same expiry
        if original_expiry:
            # Calculate DTE for the original expiry
            expiry_date = datetime.strptime(original_expiry[:10], "%Y-%m-%d").date()
            dte = (expiry_date - datetime.now().date()).days

            # Find new ATM options at the SAME expiry (not the config's 90-120 DTE)
            atm_options = self.client.find_atm_options(
                self.underlying_uic,
                self.current_underlying_price,
                max(1, dte - 5),  # Allow some flexibility
                dte + 5
            )

            if atm_options:
                # Enter new straddle using the found options (not enter_long_straddle which uses config DTE)
                if not self._enter_straddle_with_options(atm_options):
                    logger.error("Failed to enter new long straddle during recenter")
                    # CRITICAL FIX: Restore state - we closed old straddle but couldn't open new one
                    # This is serious - we have no long protection! Set to IDLE so bot re-enters
                    self.state = StrategyState.IDLE
                    self._increment_failure_count("recenter_enter_failed")
                    return False
            else:
                logger.error("Failed to find ATM options for recentered straddle")
                # CRITICAL FIX: Same as above
                self.state = StrategyState.IDLE
                self._increment_failure_count("recenter_find_options_failed")
                return False

        # Step 3: Keep existing short strangle (DO NOT CLOSE)
        # Short strangle will be rolled separately on Friday or when challenged
        if self.short_strangle:
            logger.info("Keeping existing short strangle (will be rolled on schedule or if challenged)")
        else:
            # If we don't have a short strangle yet, try to enter one
            logger.info("No existing short strangle - attempting to enter new one")
            if not self.enter_short_strangle():
                logger.warning("Failed to enter short strangle during recenter")
                # Continue anyway, straddle is more important

        self.metrics.recenter_count += 1
        self.metrics.daily_recenter_count += 1  # Track daily recenter
        if not self.dry_run:
            self.metrics.save_to_file()  # Persist metrics after recenter

        # Set state based on current positions
        if self.long_straddle and self.short_strangle:
            self.state = StrategyState.FULL_POSITION
        elif self.long_straddle:
            self.state = StrategyState.LONG_STRADDLE_ACTIVE
        else:
            self.state = StrategyState.IDLE

        logger.info(
            f"Recenter complete. New strike: {self.initial_straddle_strike:.2f}, "
            f"Total recenters: {self.metrics.recenter_count}, State: {self.state.value}"
        )

        # Log trade
        straddle_expiry = self.long_straddle.call.expiry if self.long_straddle and self.long_straddle.call else None
        if self.trade_logger:
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            self.trade_logger.log_trade(
                action=f"{action_prefix}RECENTER",
                strike=self.initial_straddle_strike,
                price=self.current_underlying_price,
                delta=self.get_total_delta(),
                pnl=self.metrics.total_pnl,
                saxo_client=self.client,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                option_type="Recenter",
                expiry_date=straddle_expiry,
                dte=self._calculate_dte(straddle_expiry) if straddle_expiry else None,
                premium_received=None
            )

        return True

    # =========================================================================
    # ROLLING AND EXIT LOGIC
    # =========================================================================

    def should_roll_shorts(self) -> tuple:
        """
        Check if weekly shorts should be rolled.

        Shorts should be rolled on Thursday 3PM EST, Friday 10AM EST, or if challenged
        (price approaching short strikes).

        Returns:
            tuple: (should_roll: bool, challenged_side: str or None)
                   challenged_side is "call", "put", or None for scheduled roll
        """
        # Check if it's time to roll shorts
        # Per Brian Terry: "Thursday 3PM EST or Friday 10AM EST"
        # CRITICAL: Use US Eastern time, not server local time
        now_est = get_us_market_time()
        today = now_est.strftime("%A")
        current_hour = now_est.hour

        is_roll_time = False

        if today == "Thursday" and current_hour >= 15:
            is_roll_time = True
            logger.info(f"Thursday 3PM+ EST ({now_est.strftime('%H:%M')}) - scheduled roll time for weekly shorts")
        elif today == "Friday" and current_hour >= 10:
            is_roll_time = True
            logger.info(f"Friday 10AM+ EST ({now_est.strftime('%H:%M')}) - scheduled roll time for weekly shorts")

        if is_roll_time:
            # CRITICAL: Check if current shorts already have 7+ DTE (already rolled to next week)
            # This prevents immediate re-rolling of shorts that were just entered with next week's expiry
            if self.short_strangle and self.short_strangle.expiry:
                try:
                    expiry_date = datetime.strptime(self.short_strangle.expiry[:10], "%Y-%m-%d").date()
                    dte = (expiry_date - now_est.date()).days
                    if dte >= 7:
                        logger.info(f"Shorts already have {dte} DTE (next week expiry) - no roll needed")
                        return (False, None)
                except (ValueError, TypeError):
                    pass  # If we can't parse expiry, proceed with roll check
            return (True, None)  # Scheduled roll, no specific challenge

        # Check if shorts are being challenged
        # Per spec: "within 0.5% of Short_Call_Strike or Short_Put_Strike"
        if self.short_strangle and self.current_underlying_price:
            call_strike = self.short_strangle.call_strike
            put_strike = self.short_strangle.put_strike
            price = self.current_underlying_price

            # Calculate 0.5% threshold for each strike
            call_threshold = call_strike * 0.005  # 0.5% of call strike
            put_threshold = put_strike * 0.005    # 0.5% of put strike

            # Distance from current price to strikes
            call_distance = call_strike - price
            put_distance = price - put_strike

            # If price is within 0.5% of either strike, roll early
            if call_distance <= call_threshold:
                pct_from_strike = (call_distance / call_strike) * 100
                logger.warning(f"Short call CHALLENGED! Price ${price:.2f} within {pct_from_strike:.2f}% of call strike ${call_strike:.2f}")
                return (True, "call")

            if put_distance <= put_threshold:
                pct_from_strike = (put_distance / put_strike) * 100
                logger.warning(f"Short put CHALLENGED! Price ${price:.2f} within {pct_from_strike:.2f}% of put strike ${put_strike:.2f}")
                return (True, "put")

        return (False, None)

    def roll_weekly_shorts(self, challenged_side: str = None) -> bool:
        """
        Roll the weekly short strangle to the next week.

        Per the video strategy:
        1. Close current short strangle
        2. Open new strangle centered on CURRENT price (not initial strike)
        3. This naturally moves the challenged side further away
        4. And moves the unchallenged side closer for more credit

        Args:
            challenged_side: "call" or "put" if rolling due to challenge, None for regular roll

        Returns:
            bool: True if roll successful, False otherwise.
        """
        logger.info("=" * 50)
        logger.info("ROLLING WEEKLY SHORTS")

        old_call_strike = None
        old_put_strike = None
        old_premium = 0

        # Log what we're rolling from
        if self.short_strangle:
            old_call_strike = self.short_strangle.call_strike
            old_put_strike = self.short_strangle.put_strike
            logger.info(f"Current strangle: Put ${old_put_strike} / Call ${old_call_strike}")
            logger.info(f"Challenged side: {challenged_side or 'None (regular roll)'}")
            logger.info(f"Current SPY: ${self.current_underlying_price:.2f}")

        self.state = StrategyState.ROLLING_SHORTS

        # CRITICAL: Per spec "The roll must result in a Net Credit. Never roll for a debit"
        # Step 1: Calculate cost to close current shorts
        old_close_cost = 0.0
        if self.short_strangle:
            old_premium = self.short_strangle.premium_collected

            # Get current market prices for the shorts
            call_quote = self.client.get_quote(self.short_strangle.call.uic, "StockOption")
            put_quote = self.client.get_quote(self.short_strangle.put.uic, "StockOption")

            if call_quote and put_quote:
                call_ask = call_quote["Quote"].get("Ask", 0)
                put_ask = put_quote["Quote"].get("Ask", 0)
                old_close_cost = (call_ask + put_ask) * 100 * self.position_size
                logger.info(f"Cost to close current shorts: ${old_close_cost:.2f}")
            else:
                logger.warning("Could not get quotes for current shorts - proceeding with roll")

        # Step 2: Get quotes for new shorts BEFORE closing current ones
        # This allows us to verify we'll get a net credit
        logger.info("Fetching quotes for new shorts to verify net credit...")

        # CRITICAL FIX: Save the current short strangle before quote check
        # The quote_only mode overwrites self.short_strangle with a temporary object
        # which caused state pollution and death loops in the past
        saved_short_strangle = self.short_strangle
        new_premium = 0

        # Use try-finally to GUARANTEE restoration even if exception occurs
        try:
            # Get quotes for new strangle without placing orders or logging trades
            # quote_only=True tells enter_short_strangle to only calculate premium
            new_shorts_success = self.enter_short_strangle(for_roll=True, quote_only=True)
            new_premium = self.short_strangle.premium_collected if self.short_strangle and new_shorts_success else 0
        except Exception as e:
            logger.error(f"Exception during quote check: {e}")
            new_shorts_success = False
        finally:
            # CRITICAL FIX: ALWAYS restore the REAL short strangle after quote check
            # This prevents state pollution from quote-only data even on exceptions
            self.short_strangle = saved_short_strangle

        # Step 3: Calculate net credit
        net_credit = new_premium - old_close_cost

        logger.info(f"Roll P&L calculation:")
        logger.info(f"  New premium: ${new_premium:.2f}")
        logger.info(f"  Close cost: ${old_close_cost:.2f}")
        logger.info(f"  Net credit: ${net_credit:.2f}")

        # Step 4: Verify net credit constraint
        if net_credit <= 0:
            logger.critical(f"ROLL REJECTED: Net credit ${net_credit:.2f} is not positive (would be a debit)")
            logger.critical("Per strategy spec: 'Never roll for a debit' - must close entire position")
            self.state = StrategyState.FULL_POSITION
            return False

        logger.info(f"✓ Roll will result in net credit of ${net_credit:.2f} - proceeding")

        # Step 5: Now actually close current shorts
        if self.short_strangle:
            if not self.close_short_strangle():
                logger.error("Failed to close shorts for rolling")
                # CRITICAL FIX: Restore state to avoid stuck ROLLING_SHORTS
                self.state = StrategyState.FULL_POSITION
                self._increment_failure_count("roll_close_shorts_failed")
                return False

        # Step 6: Enter new shorts for next week (for real this time)
        # CRITICAL: Pass for_roll=True to look for NEXT week's expiry (5-12 DTE)
        # Per Brian Terry's strategy: "roll the date out one week"
        if not self.enter_short_strangle(for_roll=True):
            logger.error("Failed to enter new shorts after rolling")
            # CRITICAL FIX: We closed old shorts but couldn't enter new ones
            # Set to LONG_STRADDLE_ACTIVE so bot can retry short entry
            self.state = StrategyState.LONG_STRADDLE_ACTIVE
            self._increment_failure_count("roll_enter_shorts_failed")
            return False

        # Log the roll details
        new_call_strike = self.short_strangle.call_strike if self.short_strangle else 0
        new_put_strike = self.short_strangle.put_strike if self.short_strangle else 0
        new_premium = self.short_strangle.premium_collected if self.short_strangle else 0

        logger.info(f"New strangle: Put ${new_put_strike} / Call ${new_call_strike}")
        logger.info(f"New premium collected: ${new_premium:.2f}")

        # Log the adjustment made
        if old_call_strike and old_put_strike:
            call_adjustment = new_call_strike - old_call_strike
            put_adjustment = new_put_strike - old_put_strike
            logger.info(f"Call strike adjusted: {'+' if call_adjustment >= 0 else ''}{call_adjustment:.0f}")
            logger.info(f"Put strike adjusted: {'+' if put_adjustment >= 0 else ''}{put_adjustment:.0f}")

        self.metrics.roll_count += 1
        self.metrics.daily_roll_count += 1  # Track daily roll
        if not self.dry_run:
            self.metrics.save_to_file()  # Persist metrics after roll
        self.state = StrategyState.FULL_POSITION

        logger.info(f"Weekly shorts rolled successfully. Total rolls: {self.metrics.roll_count}")
        logger.info("=" * 50)

        # Log safety event for the roll
        if self.trade_logger:
            self.trade_logger.log_safety_event({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "event_type": "SHORT_ROLL",
                "severity": "INFO",
                "spy_price": self.current_underlying_price,
                "initial_strike": self.initial_straddle_strike,
                "distance_pct": abs(self.current_underlying_price - self.initial_straddle_strike) / self.initial_straddle_strike * 100 if self.initial_straddle_strike else 0,
                "vix": self.current_vix,
                "action_taken": f"Rolled shorts ({challenged_side or 'scheduled'})",
                "short_call_strike": new_call_strike,
                "short_put_strike": new_put_strike,
                "description": f"Rolled from Put ${old_put_strike}/Call ${old_call_strike} to Put ${new_put_strike}/Call ${new_call_strike}. Premium: ${new_premium:.2f}",
                "result": "SUCCESS"
            })

        return True

    def should_exit_trade(self) -> bool:
        """
        Check if the entire trade should be exited.

        Per spec: "If Long Straddle DTE < 60 days, close the entire position"

        Returns:
            bool: True if should exit, False otherwise.
        """
        if not self.long_straddle or not self.long_straddle.call:
            return False

        # Calculate DTE for long straddle
        expiry_str = self.long_straddle.call.expiry
        if not expiry_str:
            return False

        expiry_date = datetime.strptime(expiry_str[:10], "%Y-%m-%d").date()
        dte = (expiry_date - datetime.now().date()).days

        # Exit when DTE drops below 60 days
        if dte < 60:
            logger.info(
                f"EXIT CONDITION MET: {dte} DTE on long straddle (threshold: < 60 DTE)"
            )
            return True

        return False

    def exit_all_positions(self) -> bool:
        """
        Exit all positions and close the trade.

        Returns:
            bool: True if exit successful, False otherwise.
        """
        logger.info("=" * 50)
        logger.info("EXITING ALL POSITIONS")
        logger.info("=" * 50)

        self.state = StrategyState.EXITING

        success = True

        # Close short strangle first
        if self.short_strangle:
            if not self.close_short_strangle():
                logger.error("Failed to close short strangle during exit")
                success = False
                self._increment_failure_count("exit_close_shorts_failed")

        # Close long straddle
        if self.long_straddle:
            if not self.close_long_straddle():
                logger.error("Failed to close long straddle during exit")
                success = False
                self._increment_failure_count("exit_close_straddle_failed")

        if success:
            self.state = StrategyState.IDLE
            logger.info(
                f"All positions closed. Total P&L: ${self.metrics.total_pnl:.2f}, "
                f"Recenters: {self.metrics.recenter_count}, Rolls: {self.metrics.roll_count}"
            )

            # Log trade
            if self.trade_logger:
                action_prefix = "[SIMULATED] " if self.dry_run else ""
                self.trade_logger.log_trade(
                    action=f"{action_prefix}EXIT_ALL",
                    strike=self.initial_straddle_strike,
                    price=self.current_underlying_price,
                    delta=0.0,
                    pnl=self.metrics.total_pnl,
                    saxo_client=self.client,
                    underlying_price=self.current_underlying_price,
                    vix=self.current_vix,
                    option_type="Exit All",
                    expiry_date=None,
                    dte=None,
                    premium_received=None
                )

                # Clear all positions from Positions sheet
                self.trade_logger.clear_all_positions()
        else:
            # CRITICAL FIX: Restore state based on what positions remain
            # Don't leave in EXITING state which causes the bot to freeze
            logger.warning("Exit incomplete - restoring state based on remaining positions")
            if self.long_straddle and self.short_strangle:
                self.state = StrategyState.FULL_POSITION
            elif self.long_straddle:
                self.state = StrategyState.LONG_STRADDLE_ACTIVE
            elif self.short_strangle:
                # Unusual state - only shorts remain, force to IDLE to re-enter longs
                logger.warning("Only shorts remain after failed exit - setting IDLE")
                self.state = StrategyState.IDLE
            else:
                self.state = StrategyState.IDLE
            logger.info(f"State restored to: {self.state.value}")

        return success

    # =========================================================================
    # MAIN STRATEGY LOOP
    # =========================================================================

    def get_total_delta(self) -> float:
        """Calculate total portfolio delta."""
        delta = 0.0
        if self.long_straddle:
            delta += self.long_straddle.total_delta
        if self.short_strangle:
            delta += self.short_strangle.total_delta
        return delta

    def _calculate_dte(self, expiry_str: str) -> Optional[int]:
        """
        Calculate days to expiration from expiry string.

        Args:
            expiry_str: Expiry date string (YYYY-MM-DD format)

        Returns:
            int: Days to expiration, or None if parsing fails
        """
        if not expiry_str:
            return None
        try:
            expiry_date = datetime.strptime(expiry_str[:10], "%Y-%m-%d").date()
            return (expiry_date - datetime.now().date()).days
        except (ValueError, TypeError):
            return None

    def get_current_positions_for_sync(self) -> List[Dict[str, Any]]:
        """
        Get current positions in format suitable for Positions sheet sync.

        Returns:
            list: List of position dictionaries for the Positions sheet
        """
        positions = []

        # Get FX rate for EUR conversion
        fx_rate = 0.0
        try:
            fx_rate = self.client.get_fx_rate("USD", "EUR") or 0.0
        except Exception:
            pass

        # Add long straddle legs
        if self.long_straddle:
            if self.long_straddle.call:
                pnl = (self.long_straddle.call.current_price - self.long_straddle.call.entry_price) * 100
                positions.append({
                    "type": "Long Call",
                    "strike": self.long_straddle.call.strike,
                    "expiry": self.long_straddle.call.expiry,
                    "dte": self._calculate_dte(self.long_straddle.call.expiry),
                    "entry_price": self.long_straddle.call.entry_price,
                    "current_price": self.long_straddle.call.current_price,
                    "theta": getattr(self.long_straddle.call, 'theta', 0),
                    "pnl": pnl,
                    "pnl_eur": pnl * fx_rate if fx_rate else 0.0,
                    "status": "Active"
                })
            if self.long_straddle.put:
                pnl = (self.long_straddle.put.current_price - self.long_straddle.put.entry_price) * 100
                positions.append({
                    "type": "Long Put",
                    "strike": self.long_straddle.put.strike,
                    "expiry": self.long_straddle.put.expiry,
                    "dte": self._calculate_dte(self.long_straddle.put.expiry),
                    "entry_price": self.long_straddle.put.entry_price,
                    "current_price": self.long_straddle.put.current_price,
                    "theta": getattr(self.long_straddle.put, 'theta', 0),
                    "pnl": pnl,
                    "pnl_eur": pnl * fx_rate if fx_rate else 0.0,
                    "status": "Active"
                })

        # Add short strangle legs
        if self.short_strangle:
            if self.short_strangle.call:
                pnl = (self.short_strangle.call.entry_price - self.short_strangle.call.current_price) * 100
                positions.append({
                    "type": "Short Call",
                    "strike": self.short_strangle.call.strike,
                    "expiry": self.short_strangle.call.expiry,
                    "dte": self._calculate_dte(self.short_strangle.call.expiry),
                    "entry_price": self.short_strangle.call.entry_price,
                    "current_price": self.short_strangle.call.current_price,
                    "theta": getattr(self.short_strangle.call, 'theta', 0),
                    "pnl": pnl,
                    "pnl_eur": pnl * fx_rate if fx_rate else 0.0,
                    "status": "Active"
                })
            if self.short_strangle.put:
                pnl = (self.short_strangle.put.entry_price - self.short_strangle.put.current_price) * 100
                positions.append({
                    "type": "Short Put",
                    "strike": self.short_strangle.put.strike,
                    "expiry": self.short_strangle.put.expiry,
                    "dte": self._calculate_dte(self.short_strangle.put.expiry),
                    "entry_price": self.short_strangle.put.entry_price,
                    "current_price": self.short_strangle.put.current_price,
                    "theta": getattr(self.short_strangle.put, 'theta', 0),
                    "pnl": pnl,
                    "pnl_eur": pnl * fx_rate if fx_rate else 0.0,
                    "status": "Active"
                })

        return positions

    def sync_positions_sheet(self):
        """
        Sync the Positions sheet with current strategy positions.

        Call this on startup to ensure the sheet reflects actual state.
        """
        if not self.trade_logger:
            return

        positions = self.get_current_positions_for_sync()
        self.trade_logger.sync_positions_with_saxo(positions)
        logger.info(f"Synced Positions sheet with {len(positions)} current positions")

    def run_strategy_check(self) -> str:
        """
        Run a single iteration of the strategy logic.

        This should be called periodically (e.g., every minute) or
        on price updates to check conditions and take actions.

        Returns:
            str: Description of action taken, if any.
        """
        action_taken = "No action"

        # CRITICAL: Check strategy-level circuit breaker first
        if self._check_circuit_breaker():
            return f"🚨 CIRCUIT BREAKER OPEN - {self._circuit_breaker_reason}"

        # Check Saxo client circuit breaker
        if self.client.is_circuit_open():
            return "Circuit breaker open - trading halted"

        # CRITICAL: Check for orphaned orders before any trading
        if self._check_for_orphaned_orders():
            return "🚨 ORPHANED ORDERS DETECTED - Manual cancellation required"

        # Update market data
        if not self.update_market_data():
            return "Failed to update market data"

        # CRITICAL: Handle stuck states (RECENTERING, EXITING, ROLLING_SHORTS)
        # These states should only be transient - if we're stuck, something went wrong
        if self.state in [StrategyState.RECENTERING, StrategyState.EXITING, StrategyState.ROLLING_SHORTS]:
            logger.warning(f"⚠️ Bot found in transient state: {self.state.value}")
            logger.warning("   This may indicate a previous operation failed")
            logger.warning("   Attempting to recover based on current positions...")

            # Check what positions we actually have on Saxo
            self.recover_positions()

            # Update state based on recovered positions
            if self.long_straddle and self.short_strangle:
                self.state = StrategyState.FULL_POSITION
                logger.info("   Recovered to FULL_POSITION state")
            elif self.long_straddle:
                self.state = StrategyState.LONG_STRADDLE_ACTIVE
                logger.info("   Recovered to LONG_STRADDLE_ACTIVE state")
            else:
                self.state = StrategyState.IDLE
                logger.info("   Recovered to IDLE state")

            return f"Recovered from stuck state ({self.state.value})"

        # PRIORITY SAFETY CHECKS (before normal logic)
        # Check for emergency exit condition (5%+ move)
        if self.check_emergency_exit_condition():
            logger.critical("EMERGENCY EXIT TRIGGERED - Closing all positions immediately")
            if self.exit_all_positions():
                self._reset_failure_count()
                return "EMERGENCY EXIT - Massive move detected"
            else:
                self._increment_failure_count("emergency_exit_failed")
                return "EMERGENCY EXIT FAILED - Manual intervention required"

        # Check for ITM risk on short options
        if self.check_shorts_itm_risk():
            logger.critical("ITM RISK DETECTED - Rolling shorts immediately")
            if self.roll_weekly_shorts():
                self._reset_failure_count()
                return "Emergency roll - shorts approaching ITM"
            else:
                logger.critical("Failed to roll shorts at ITM risk - closing all positions")
                if self.exit_all_positions():
                    self._reset_failure_count()
                    return "Emergency exit - could not roll ITM shorts"
                else:
                    self._increment_failure_count("itm_risk_exit_failed")
                    # CRITICAL FIX: Explicit return to prevent falling through to state machine
                    # This was a gap that could cause repeated retries without proper tracking
                    return "ITM RISK EXIT FAILED - Manual intervention required"

        # State machine logic
        if self.state == StrategyState.IDLE:
            # Try to enter the trade
            if self.enter_long_straddle():
                action_taken = "Entered long straddle"
                # Also try to enter short strangle
                # CRITICAL: If it's roll time (Thu 3PM+ or Fri 10AM+), enter with NEXT WEEK's
                # expiry directly to avoid immediate roll triggering
                should_roll, _ = self.should_roll_shorts()
                if should_roll:
                    logger.info("Roll time detected during entry - entering shorts with NEXT WEEK expiry")
                if self.enter_short_strangle(for_roll=should_roll):
                    action_taken = "Entered long straddle and short strangle"

        elif self.state == StrategyState.WAITING_VIX:
            # Check if VIX condition is now met
            if self.check_vix_entry_condition():
                self.state = StrategyState.IDLE
                action_taken = "VIX condition met, ready to enter"

        elif self.state == StrategyState.LONG_STRADDLE_ACTIVE:
            # CRITICAL: Check recenter FIRST before adding shorts
            # Per strategy spec: "recenter_longs function always executes before checking for new short entry"
            if self._check_recenter_condition():
                # Check cooldown before attempting recenter
                if self._is_action_on_cooldown("recenter"):
                    action_taken = "Recenter on cooldown after recent failure"
                elif self.execute_recenter():
                    self._clear_action_cooldown("recenter")
                    action_taken = "Executed 5-point recenter (before adding shorts)"
                else:
                    self._set_action_cooldown("recenter")
                    action_taken = "Recenter failed - on cooldown"
            else:
                # Check if we're waiting for old shorts to expire (debit roll skip)
                # Per Brian Terry: "If you didn't roll on Thursday because of the debit,
                # you check again on Friday morning (10:00 AM EST) or even Monday morning"
                now_est = get_us_market_time()
                today = now_est.date()
                today_weekday = now_est.strftime("%A")
                current_hour = now_est.hour

                if self._shorts_closed_date:
                    # We skipped a roll due to debit - when can we try again?
                    closed_weekday = self._shorts_closed_date.strftime("%A")

                    # Per Brian Terry's "Wait for Premium" logic:
                    # - If closed Thursday -> try again Friday 10AM EST
                    # - If closed Friday -> wait until Monday
                    # - Once current week expires, open fresh shorts for following Friday

                    can_try_new_shorts = False

                    if closed_weekday == "Thursday" and today_weekday == "Friday" and current_hour >= 10:
                        # Closed Thursday, now it's Friday 10AM+ EST - try again
                        logger.info("Thursday roll skipped - checking Friday 10AM EST for new shorts")
                        can_try_new_shorts = True
                    elif today_weekday in ["Monday", "Tuesday", "Wednesday"]:
                        # New week - definitely can enter new shorts
                        logger.info(f"New week ({today_weekday}) - ready to enter new shorts")
                        can_try_new_shorts = True
                    elif today > self._shorts_closed_date and today_weekday == "Monday":
                        # Monday after the close
                        can_try_new_shorts = True
                    else:
                        logger.info(f"Waiting for premium opportunity (closed {closed_weekday}, today is {today_weekday} {current_hour}:00 EST)")
                        action_taken = "Waiting for Friday 10AM or Monday to enter new shorts"

                    if can_try_new_shorts:
                        # Clear the flag and try to enter new shorts
                        logger.info("Attempting to enter fresh short strangle (new weekly cycle)")
                        self._shorts_closed_date = None
                        if self.enter_short_strangle(for_roll=False):
                            action_taken = "Added short strangle (new weekly cycle)"
                        else:
                            # Still can't get credit - set flag again and wait
                            logger.warning("Still cannot enter for credit - will try Monday")
                            self._shorts_closed_date = today
                            action_taken = "New shorts still not viable - waiting for Monday"
                else:
                    # Normal operation - add short strangle
                    # CRITICAL: If roll time, enter with next week's expiry directly
                    should_roll, _ = self.should_roll_shorts()
                    if should_roll:
                        logger.info("Roll time detected during entry - entering shorts with NEXT WEEK expiry")
                    if self.enter_short_strangle(for_roll=should_roll):
                        action_taken = "Added short strangle"

        elif self.state == StrategyState.FULL_POSITION:
            # Check exit condition first
            if self.should_exit_trade():
                if self.exit_all_positions():
                    action_taken = "Exited all positions (DTE threshold)"
                else:
                    self._set_action_cooldown("exit")
                    action_taken = "Exit failed - on cooldown"

            # Check recenter condition
            elif self._check_recenter_condition():
                # Check cooldown before attempting recenter
                if self._is_action_on_cooldown("recenter"):
                    action_taken = "Recenter on cooldown after recent failure"
                elif self.execute_recenter():
                    self._clear_action_cooldown("recenter")
                    action_taken = "Executed 5-point recenter"
                else:
                    self._set_action_cooldown("recenter")
                    action_taken = "Recenter failed - on cooldown"

            # Check roll condition
            else:
                should_roll, challenged_side = self.should_roll_shorts()
                if should_roll:
                    # Check cooldown before attempting roll
                    if self._is_action_on_cooldown("roll_shorts"):
                        action_taken = "Roll shorts on cooldown after recent failure"
                    elif self.roll_weekly_shorts(challenged_side=challenged_side):
                        self._clear_action_cooldown("roll_shorts")
                        if challenged_side:
                            action_taken = f"Rolled weekly shorts ({challenged_side} challenged)"
                        else:
                            action_taken = "Rolled weekly shorts (scheduled)"
                    else:
                        # Roll failed (likely due to debit or spread issues)
                        # CRITICAL DISTINCTION per Brian Terry:
                        # - CHALLENGED + DEBIT: EXIT ALL (close longs + shorts, take profit on longs)
                        # - UNCHALLENGED + DEBIT: Let shorts expire, open new shorts Friday/Monday
                        if challenged_side:
                            # CHALLENGED + DEBIT: Price threatening our shorts AND can't roll for credit
                            # Per Brian Terry: "Close the entire position - your Long leg has likely
                            # gained so much value that the entire position is already profitable"
                            logger.critical("=" * 60)
                            logger.critical(f"CHALLENGED ROLL FAILED - {challenged_side} side under pressure")
                            logger.critical("Cannot roll for credit - EXITING ENTIRE POSITION")
                            logger.critical("Per Brian Terry: Take profit on longs, cover shorts, reset")
                            logger.critical("=" * 60)

                            if self.exit_all_positions():
                                action_taken = f"HARD EXIT - Challenged roll failed ({challenged_side})"

                                # Log safety event
                                if self.trade_logger:
                                    self.trade_logger.log_safety_event({
                                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "event_type": "HARD_EXIT_CHALLENGED_ROLL_FAILED",
                                        "severity": "CRITICAL",
                                        "spy_price": self.current_underlying_price,
                                        "initial_strike": self.initial_straddle_strike,
                                        "vix": self.current_vix,
                                        "action_taken": "Exited all positions - challenged roll could not be done for credit",
                                        "description": f"Challenged side: {challenged_side}. Longs likely profitable. Cycle complete.",
                                        "result": "HARD_EXIT"
                                    })
                            else:
                                self._set_action_cooldown("challenged_exit")
                                action_taken = "CRITICAL: Challenged roll failed AND exit failed - MANUAL INTERVENTION REQUIRED"
                                logger.critical("MANUAL INTERVENTION REQUIRED - Could not exit positions!")
                        else:
                            # UNCHALLENGED + DEBIT (scheduled roll on Thurs/Fri)
                            # Per Brian Terry: "Let current shorts expire worthless, wait until
                            # Friday/Monday to open new shorts - you aren't rolling, you're
                            # starting a new weekly cycle with a clean net credit"
                            logger.warning("=" * 60)
                            logger.warning("SCHEDULED ROLL SKIPPED - Cannot roll for credit (low IV)")
                            logger.warning("Shorts are NOT challenged - letting them expire worthless")
                            logger.warning("Will open fresh shorts Friday/Monday for next week")
                            logger.warning("=" * 60)

                            # Close the current shorts for pennies (or let expire)
                            # Then transition to LONG_STRADDLE_ACTIVE so bot enters new shorts next week
                            now_est = get_us_market_time()
                            if self.close_short_strangle():
                                action_taken = "Closed shorts for pennies - will open new cycle next week"
                                logger.info("Short strangle closed. Longs remain active.")
                                # Clear short strangle reference
                                self.short_strangle = None
                                # Mark the date - won't enter new shorts until after this week's expiry
                                self._shorts_closed_date = now_est.date()
                                # Transition back to LONG_STRADDLE_ACTIVE
                                # Bot will check _shorts_closed_date before entering new shorts
                                self.state = StrategyState.LONG_STRADDLE_ACTIVE
                                logger.info(f"State -> LONG_STRADDLE_ACTIVE (will enter new shorts Monday)")
                            else:
                                action_taken = "Letting shorts expire naturally - new cycle next week"
                                # Mark the date anyway - shorts will expire worthless
                                self._shorts_closed_date = now_est.date()
                                # Even if close fails, shorts will expire worthless
                                # Bot will detect no shorts and re-enter on Monday

                            # Log as INFO event, not critical
                            if self.trade_logger:
                                self.trade_logger.log_safety_event({
                                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "event_type": "SCHEDULED_ROLL_SKIPPED",
                                    "severity": "INFO",
                                    "spy_price": self.current_underlying_price,
                                    "initial_strike": self.initial_straddle_strike,
                                    "vix": self.current_vix,
                                    "action_taken": "Letting shorts expire - low IV environment",
                                    "description": "Scheduled roll resulted in debit. Shorts unchallenged and will expire worthless. New shorts will be opened Friday/Monday.",
                                    "result": "SKIPPED"
                                })

        logger.info(f"Strategy check: {action_taken} | State: {self.state.value}")

        return action_taken

    def get_status_summary(self) -> Dict:
        """
        Get a summary of the current strategy status.

        Returns:
            dict: Status summary with positions and metrics.
        """
        summary = {
            "state": self.state.value,
            "environment": self.client.environment,
            "is_simulation": self.client.is_simulation,
            "underlying_price": self.current_underlying_price,
            "vix": self.current_vix,
            "initial_strike": self.initial_straddle_strike,
            "price_from_strike": abs(self.current_underlying_price - self.initial_straddle_strike)
                                if self.initial_straddle_strike else 0,
            "has_long_straddle": self.long_straddle is not None and self.long_straddle.is_complete,
            "has_short_strangle": self.short_strangle is not None and self.short_strangle.is_complete,
            "total_delta": self.get_total_delta(),
            "total_pnl": self.metrics.total_pnl,
            "realized_pnl": self.metrics.realized_pnl,
            "unrealized_pnl": self.metrics.unrealized_pnl,
            "premium_collected": self.metrics.total_premium_collected,
            "straddle_cost": self.metrics.total_straddle_cost,
            "recenter_count": self.metrics.recenter_count,
            "roll_count": self.metrics.roll_count
        }

        # Add currency conversion if enabled
        if self.trade_logger and self.trade_logger.currency_enabled:
            try:
                rate = self.client.get_fx_rate(
                    self.trade_logger.base_currency,
                    self.trade_logger.account_currency
                )
                if rate:
                    summary["exchange_rate"] = rate
                    summary["total_pnl_eur"] = self.metrics.total_pnl * rate
                    summary["realized_pnl_eur"] = self.metrics.realized_pnl * rate
                    summary["unrealized_pnl_eur"] = self.metrics.unrealized_pnl * rate
            except Exception as e:
                logger.warning(f"Could not fetch FX rate for status: {e}")

        return summary

    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """
        Get comprehensive SPY strategy metrics for the Looker dashboard.

        Returns all metrics needed for:
        - Account Summary worksheet (strategy values, Greeks, strikes)
        - Performance Metrics worksheet (P&L breakdown, KPIs)

        Returns:
            dict: Complete strategy metrics for dashboard logging
        """
        # Get total Greeks
        greeks = self.get_total_greeks()

        # Calculate position values
        long_straddle_value = 0.0
        short_strangle_value = 0.0
        long_straddle_pnl = 0.0
        short_strangle_pnl = 0.0

        # Long straddle value and P&L (multiply by quantity for multiple contracts)
        if self.long_straddle and self.long_straddle.is_complete:
            if self.long_straddle.call:
                qty = self.long_straddle.call.quantity
                call_value = self.long_straddle.call.current_price * 100 * qty
                call_cost = self.long_straddle.call.entry_price * 100 * qty
                long_straddle_value += call_value
                long_straddle_pnl += (call_value - call_cost)
            if self.long_straddle.put:
                qty = self.long_straddle.put.quantity
                put_value = self.long_straddle.put.current_price * 100 * qty
                put_cost = self.long_straddle.put.entry_price * 100 * qty
                long_straddle_value += put_value
                long_straddle_pnl += (put_value - put_cost)

        # Short strangle value and P&L (negative value = we owe, positive P&L when value decreases)
        if self.short_strangle and self.short_strangle.is_complete:
            if self.short_strangle.call:
                qty = self.short_strangle.call.quantity
                call_value = self.short_strangle.call.current_price * 100 * qty
                call_premium = self.short_strangle.call.entry_price * 100 * qty
                short_strangle_value -= call_value  # Liability
                short_strangle_pnl += (call_premium - call_value)  # Profit when value drops
            if self.short_strangle.put:
                qty = self.short_strangle.put.quantity
                put_value = self.short_strangle.put.current_price * 100 * qty
                put_premium = self.short_strangle.put.entry_price * 100 * qty
                short_strangle_value -= put_value
                short_strangle_pnl += (put_premium - put_value)

        # Get strike prices
        # Straddle uses initial_strike (same for call and put)
        # Strangle uses call_strike and put_strike (different strikes)
        long_call_strike = self.long_straddle.initial_strike if self.long_straddle else 0
        long_put_strike = self.long_straddle.initial_strike if self.long_straddle else 0
        short_call_strike = self.short_strangle.call_strike if self.short_strangle else 0
        short_put_strike = self.short_strangle.put_strike if self.short_strangle else 0

        # Count positions (4 legs when fully deployed)
        position_count = 0
        if self.long_straddle:
            if self.long_straddle.call:
                position_count += 1
            if self.long_straddle.put:
                position_count += 1
        if self.short_strangle:
            if self.short_strangle.call:
                position_count += 1
            if self.short_strangle.put:
                position_count += 1

        # Calculate theta (daily) - multiply by 100 for contract size and by quantity
        # Long theta is negative (costs us), Short theta is positive (earns us)
        long_theta_cost = 0.0
        short_theta_income = 0.0

        if self.long_straddle:
            if self.long_straddle.call:
                qty = self.long_straddle.call.quantity
                long_theta_cost += abs(getattr(self.long_straddle.call, 'theta', 0)) * 100 * qty
            if self.long_straddle.put:
                qty = self.long_straddle.put.quantity
                long_theta_cost += abs(getattr(self.long_straddle.put, 'theta', 0)) * 100 * qty

        if self.short_strangle:
            if self.short_strangle.call:
                qty = self.short_strangle.call.quantity
                short_theta_income += abs(getattr(self.short_strangle.call, 'theta', 0)) * 100 * qty
            if self.short_strangle.put:
                qty = self.short_strangle.put.quantity
                short_theta_income += abs(getattr(self.short_strangle.put, 'theta', 0)) * 100 * qty

        net_theta = short_theta_income - long_theta_cost

        # Update metrics with calculated unrealized P&L
        self.metrics.unrealized_pnl = long_straddle_pnl + short_strangle_pnl

        # Update drawdown tracking
        self.metrics.update_drawdown(self.metrics.total_pnl)

        # Get margin from Saxo balance API
        strategy_margin = 0.0
        try:
            balance = self.client.get_balance()
            if balance:
                # Use CostToClosePositions as a proxy for margin requirement
                # This represents the cost to close all positions
                strategy_margin = abs(balance.get("CostToClosePositions", 0))
        except Exception as e:
            logger.debug(f"Could not fetch margin from Saxo: {e}")

        # Calculate P&L percentage as decimal for Google Sheets (0.0404 = 4.04%)
        initial_cost = self.metrics.total_straddle_cost or 1  # Avoid division by zero
        pnl_percent = (self.metrics.total_pnl / initial_cost) if initial_cost > 0 else 0

        # Calculate max drawdown percentage as decimal (0.0404 = 4.04%)
        max_dd_percent = 0.0
        if initial_cost > 0 and self.metrics.max_drawdown > 0:
            max_dd_percent = self.metrics.max_drawdown / initial_cost

        # Get individual deltas for short positions (for Account Summary)
        short_call_delta = 0.0
        short_put_delta = 0.0
        if self.short_strangle:
            if self.short_strangle.call:
                # Delta is per contract, multiply by quantity for total delta exposure
                qty = self.short_strangle.call.quantity
                short_call_delta = getattr(self.short_strangle.call, 'delta', 0) * qty
            if self.short_strangle.put:
                qty = self.short_strangle.put.quantity
                short_put_delta = getattr(self.short_strangle.put, 'delta', 0) * qty

        return {
            # Account Summary fields
            "spy_price": self.current_underlying_price,
            "vix": self.current_vix,
            "unrealized_pnl": self.metrics.unrealized_pnl,
            "long_straddle_value": long_straddle_value,
            "short_strangle_value": short_strangle_value,
            "strategy_margin": strategy_margin,
            "total_delta": greeks["delta"],
            "total_theta": net_theta,
            "position_count": position_count,
            "long_call_strike": long_call_strike,
            "long_put_strike": long_put_strike,
            "short_call_strike": short_call_strike,
            "short_put_strike": short_put_strike,
            # Individual short deltas for Account Summary
            "short_call_delta": short_call_delta,
            "short_put_delta": short_put_delta,

            # Performance Metrics fields
            "total_pnl": self.metrics.total_pnl,
            "realized_pnl": self.metrics.realized_pnl,
            "premium_collected": self.metrics.total_premium_collected,
            "theta_cost": long_theta_cost,
            "net_theta": net_theta,
            "long_straddle_pnl": long_straddle_pnl,
            "short_strangle_pnl": short_strangle_pnl,
            "trade_count": self.metrics.trade_count,
            "roll_count": self.metrics.roll_count,
            "recenter_count": self.metrics.recenter_count,

            # New KPI fields
            "pnl_percent": pnl_percent,
            "win_rate": self.metrics.win_rate,
            "sharpe_ratio": 0.0,  # TODO: implement if needed
            "max_drawdown": self.metrics.max_drawdown,
            "max_drawdown_pct": max_dd_percent,
            "avg_trade_pnl": self.metrics.avg_trade_pnl,
            "best_trade": self.metrics.best_trade_pnl,
            "worst_trade": self.metrics.worst_trade_pnl,

            # Theta accumulation tracking
            # Try to get actual accumulated theta from Daily Summary logs first
            # Falls back to estimate (net_theta × days_held) if no data available
            "estimated_theta_earned": self._get_theta_earned_or_estimate(net_theta),
            # Cumulative Net Theta = ALL-TIME sum of daily net theta from Daily Summary
            # This never resets - tracks total theta earned across all positions
            "cumulative_net_theta": self._get_cumulative_net_theta(),
            # Current daily net theta rate
            "daily_net_theta": net_theta,
            "days_held": self.short_strangle.days_held if self.short_strangle else 0,
            "days_to_expiry": self.short_strangle.days_to_expiry if self.short_strangle else 0,

            # Additional Greeks
            "total_gamma": greeks["gamma"],
            "total_vega": greeks["vega"],

            # State info
            "state": self.state.value,
            "has_long_straddle": self.long_straddle is not None and self.long_straddle.is_complete,
            "has_short_strangle": self.short_strangle is not None and self.short_strangle.is_complete,
        }

    def _get_theta_earned_or_estimate(self, current_net_theta: float) -> float:
        """
        Get actual accumulated theta from Daily Summary, plus estimate for weekends.

        This method:
        1. Sums actual logged daily theta values from Daily Summary
        2. Adds estimated theta for weekends/holidays (days without logging)

        Weekend theta is real - options decay every calendar day. But we only log
        on trading days, so we estimate weekend theta using the average daily rate.

        Args:
            current_net_theta: Current daily net theta rate

        Returns:
            float: Accumulated theta earned (actual + weekend estimate)
        """
        days_held = self.short_strangle.days_held if self.short_strangle else 0

        if days_held == 0:
            # Same day entry - just return today's theta if we have a Daily Summary
            if self.trade_logger and self.short_strangle:
                try:
                    entry_date = self.short_strangle.entry_date
                    actual_theta = self.trade_logger.get_accumulated_theta_from_daily_summary(since_date=entry_date)
                    if actual_theta is not None:
                        return actual_theta
                except Exception:
                    pass
            return 0.0

        # Try to get actual accumulated theta from Daily Summary
        if self.trade_logger and self.short_strangle:
            try:
                entry_date = self.short_strangle.entry_date  # YYYY-MM-DD format

                # Get actual theta from logged trading days
                actual_theta = self.trade_logger.get_accumulated_theta_from_daily_summary(since_date=entry_date)

                if actual_theta is not None:
                    # Count trading days logged vs total calendar days
                    # days_held = elapsed days (e.g., Fri entry -> Mon = 3)
                    # But we need total days including entry day = days_held + 1
                    # Example: Fri entry, Mon check
                    #   - days_held = 3 (Fri->Sat->Sun->Mon)
                    #   - total_calendar_days = 4 (Fri, Sat, Sun, Mon)
                    #   - trading_days_logged = 2 (Fri, Mon)
                    #   - weekend_days = 4 - 2 = 2 (Sat, Sun) ✓
                    trading_days_logged = self.trade_logger.get_daily_summary_count(since_date=entry_date)
                    total_calendar_days = days_held + 1  # Include entry day

                    if trading_days_logged and trading_days_logged > 0:
                        # Calculate average daily theta from actual logs
                        avg_daily_theta = actual_theta / trading_days_logged
                        # Estimate theta for non-trading days (weekends/holidays)
                        non_trading_days = total_calendar_days - trading_days_logged
                        weekend_theta = avg_daily_theta * non_trading_days if non_trading_days > 0 else 0

                        total_theta = actual_theta + weekend_theta
                        logger.debug(
                            f"Theta earned: ${actual_theta:.2f} (logged) + ${weekend_theta:.2f} (weekend estimate) "
                            f"= ${total_theta:.2f} total ({trading_days_logged} trading days, {non_trading_days} weekend days)"
                        )
                        return total_theta
                    else:
                        return actual_theta
            except Exception as e:
                logger.debug(f"Could not get accumulated theta from logs: {e}")

        # Fall back to pure estimate
        estimate = current_net_theta * days_held
        logger.debug(f"Using estimated theta: ${current_net_theta:.2f}/day × {days_held} days = ${estimate:.2f}")
        return estimate

    def _get_cumulative_net_theta(self) -> float:
        """
        Get ALL-TIME cumulative net theta from Daily Summary.

        This is the sum of ALL daily net theta values ever logged - it never resets.
        This tracks the total theta earned across all positions over time.

        Returns:
            float: Cumulative net theta (all-time), or 0.0 if unavailable
        """
        if not self.trade_logger:
            return 0.0

        try:
            # Get all-time cumulative theta (no since_date filter)
            cumulative = self.trade_logger.get_accumulated_theta_from_daily_summary(since_date=None)
            return cumulative if cumulative is not None else 0.0
        except Exception as e:
            logger.debug(f"Could not get cumulative net theta: {e}")
            return 0.0

    def get_total_greeks(self) -> Dict[str, float]:
        """
        Calculate total Greeks across all positions.

        Returns:
            dict: Total delta, gamma, theta, vega
        """
        total_delta = 0.0
        total_gamma = 0.0
        total_theta = 0.0
        total_vega = 0.0

        if self.long_straddle:
            if self.long_straddle.call:
                total_delta += self.long_straddle.call.delta
                total_gamma += getattr(self.long_straddle.call, 'gamma', 0)
                total_theta += getattr(self.long_straddle.call, 'theta', 0)
                total_vega += getattr(self.long_straddle.call, 'vega', 0)
            if self.long_straddle.put:
                total_delta += self.long_straddle.put.delta
                total_gamma += getattr(self.long_straddle.put, 'gamma', 0)
                total_theta += getattr(self.long_straddle.put, 'theta', 0)
                total_vega += getattr(self.long_straddle.put, 'vega', 0)

        if self.short_strangle:
            if self.short_strangle.call:
                total_delta += self.short_strangle.call.delta
                total_gamma -= getattr(self.short_strangle.call, 'gamma', 0)  # Short = negative gamma
                total_theta -= getattr(self.short_strangle.call, 'theta', 0)  # Short = positive theta (earns)
                total_vega -= getattr(self.short_strangle.call, 'vega', 0)    # Short = negative vega
            if self.short_strangle.put:
                total_delta += self.short_strangle.put.delta
                total_gamma -= getattr(self.short_strangle.put, 'gamma', 0)
                total_theta -= getattr(self.short_strangle.put, 'theta', 0)
                total_vega -= getattr(self.short_strangle.put, 'vega', 0)

        return {
            "delta": total_delta,
            "gamma": total_gamma,
            "theta": total_theta,
            "vega": total_vega
        }

    def log_daily_summary(self) -> bool:
        """
        Log daily summary to Google Sheets at end of trading day.

        Includes P&L, Greeks, premium collected, and market data.

        Returns:
            bool: True if logged successfully
        """
        if not self.trade_logger:
            return False

        # Use get_dashboard_metrics() which calculates theta_cost correctly (scaled by 100 × qty)
        metrics = self.get_dashboard_metrics()

        # Calculate daily P&L
        daily_pnl = self.metrics.total_pnl - self.metrics.daily_pnl_start

        # Build summary data
        # Use vix_avg if available (tracked during market hours), otherwise fall back to current VIX
        vix_value = self.metrics.vix_avg if self.metrics.vix_avg > 0 else (self.current_vix or 0)

        summary = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "state": self.state.value,
            "spy_open": self.metrics.spy_open,
            "spy_close": self.current_underlying_price,
            "spy_range": self.metrics.spy_range,
            "vix_avg": vix_value,  # Use current VIX as fallback if daily avg not available
            "vix_high": self.metrics.vix_high if self.metrics.vix_high > 0 else (self.current_vix or 0),
            "total_delta": metrics.get("total_delta", 0),
            "total_gamma": metrics.get("total_gamma", 0),
            "total_theta": metrics.get("net_theta", 0),  # Use scaled net theta
            "theta_cost": metrics.get("theta_cost", 0),  # Scaled theta cost from longs
            "daily_pnl": daily_pnl,
            "realized_pnl": self.metrics.realized_pnl,
            "unrealized_pnl": self.metrics.unrealized_pnl,
            "premium_collected": self.metrics.total_premium_collected,
            "trades_count": self.metrics.trade_count,  # Use actual trade count
            "recenter_count": self.metrics.recenter_count,
            "roll_count": self.metrics.roll_count,
            "cumulative_pnl": self.metrics.total_pnl,
            "pnl_eur": 0.0,
            "notes": "",
            # Daily roll/recenter tracking for Daily Summary
            "rolled_today": self.metrics.daily_roll_count > 0,
            "recentered_today": self.metrics.daily_recenter_count > 0
        }

        # Add EUR conversion if available
        if hasattr(self.trade_logger, 'currency_enabled') and self.trade_logger.currency_enabled:
            try:
                rate = self.client.get_fx_rate(
                    self.trade_logger.base_currency,
                    self.trade_logger.account_currency
                )
                if rate:
                    summary["pnl_eur"] = daily_pnl * rate
            except Exception as e:
                logger.warning(f"Could not fetch FX rate for daily summary: {e}")

        # Log to Google Sheets
        self.trade_logger.log_daily_summary(summary)
        logger.info(f"Daily summary logged: P&L ${daily_pnl:.2f}, Net Theta ${metrics.get('net_theta', 0):.2f}")

        return True

    def start_new_trading_day(self):
        """
        Initialize tracking for a new trading day.

        Call this at market open or first check of the day.
        """
        self.metrics.reset_daily_tracking(
            current_pnl=self.metrics.total_pnl,
            spy_price=self.current_underlying_price or 0,
            vix=self.current_vix or 0
        )
        logger.info(f"New trading day started. Opening P&L: ${self.metrics.total_pnl:.2f}")

    def update_intraday_tracking(self):
        """Update intraday high/low tracking."""
        if self.current_underlying_price and self.current_vix:
            self.metrics.update_daily_tracking(
                spy_price=self.current_underlying_price,
                vix=self.current_vix
            )
