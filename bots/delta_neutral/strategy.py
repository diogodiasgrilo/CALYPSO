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

        # Strategy parameters
        self.recenter_threshold = self.strategy_config["recenter_threshold_points"]
        self.max_vix = self.strategy_config["max_vix_entry"]
        self.vix_defensive_threshold = self.strategy_config.get("vix_defensive_threshold", 25.0)
        self.min_dte = self.strategy_config["long_straddle_min_dte"]
        self.max_dte = self.strategy_config["long_straddle_max_dte"]
        self.exit_dte_min = self.strategy_config["exit_dte_min"]
        self.exit_dte_max = self.strategy_config["exit_dte_max"]
        self.strangle_multiplier_min = self.strategy_config["weekly_strangle_multiplier_min"]
        self.strangle_multiplier_max = self.strategy_config["weekly_strangle_multiplier_max"]
        self.weekly_target_return_pct = self.strategy_config.get("weekly_target_return_percent", None)
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
    # SLIPPAGE PROTECTION - ORDER PLACEMENT WITH TIMEOUT
    # =========================================================================

    def _place_protected_multi_leg_order(
        self,
        legs: List[Dict],
        total_limit_price: float,
        order_description: str
    ) -> Dict:
        """
        Place a multi-leg order with slippage protection.

        Per strategy spec: "Use Limit Orders only, and if a 'Recenter' or 'Roll'
        isn't filled within 60 seconds, it should alert rather than chasing the price."

        Args:
            legs: List of leg dictionaries with uic, asset_type, buy_sell, amount
            total_limit_price: Limit price for the entire combo
            order_description: Description for logging (e.g., "LONG_STRADDLE", "SHORT_STRANGLE")

        Returns:
            dict: {
                "success": bool,
                "filled": bool,
                "order_id": str or None,
                "message": str
            }
        """
        logger.info(f"Placing {order_description} with slippage protection")
        logger.info(f"  Limit price: ${total_limit_price:.2f}")
        logger.info(f"  Timeout: {self.order_timeout_seconds}s")

        # In dry_run mode, simulate success
        if self.dry_run:
            logger.info(f"[DRY RUN] Simulating {order_description} order (no real order placed)")
            return {
                "success": True,
                "filled": True,
                "order_id": f"SIMULATED_{int(time.time())}",
                "message": "[DRY RUN] Order simulated successfully"
            }

        # Use the slippage-protected order placement
        result = self.client.place_multi_leg_limit_order_with_timeout(
            legs=legs,
            total_limit_price=total_limit_price,
            timeout_seconds=self.order_timeout_seconds
        )

        if not result["filled"]:
            # Order timed out - log alert
            logger.critical(f"⚠️ SLIPPAGE ALERT: {order_description} NOT FILLED")
            logger.critical(f"   {result['message']}")
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
                    "action_taken": f"{order_description} order cancelled after timeout",
                    "description": result['message'],
                    "result": "FAILED"
                })

        return result

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
        Refresh current prices for all option positions from Saxo API.

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
                        logger.debug(f"Updated long call price: ${current_price:.4f}")
                    if self.long_straddle.put and self.long_straddle.put.uic == uic:
                        self.long_straddle.put.current_price = current_price
                        logger.debug(f"Updated long put price: ${current_price:.4f}")

                if self.short_strangle:
                    if self.short_strangle.call and self.short_strangle.call.uic == uic:
                        self.short_strangle.call.current_price = current_price
                        logger.debug(f"Updated short call price: ${current_price:.4f}")
                    if self.short_strangle.put and self.short_strangle.put.uic == uic:
                        self.short_strangle.put.current_price = current_price
                        logger.debug(f"Updated short put price: ${current_price:.4f}")

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

        # Find ATM options
        atm_options = self.client.find_atm_options(
            self.underlying_uic,
            self.current_underlying_price,
            self.min_dte,
            self.max_dte
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
        legs = [
            {
                "uic": call_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.position_size
            },
            {
                "uic": put_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.position_size
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
                "amount": self.position_size
            },
            {
                "uic": put_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.position_size
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

        # Place sell orders for both legs with slippage protection
        legs = [
            {
                "uic": self.long_straddle.call.uic,
                "asset_type": "StockOption",
                "buy_sell": "Sell",
                "amount": self.long_straddle.call.quantity
            },
            {
                "uic": self.long_straddle.put.uic,
                "asset_type": "StockOption",
                "buy_sell": "Sell",
                "amount": self.long_straddle.put.quantity
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

    def enter_short_strangle(self, for_roll: bool = False) -> bool:
        """
        Enter a weekly short strangle for income generation.

        If weekly_target_return_percent is configured, finds strikes that meet
        the target return. Otherwise uses multiplier on expected move.

        Args:
            for_roll: If True, this is for rolling shorts (look for next week's expiry).
                     If False, this is initial entry (look for current week's expiry).

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
            return self._enter_strangle_by_target_return(for_roll=for_roll)

        # Otherwise use the multiplier approach
        return self._enter_strangle_by_multiplier(for_roll=for_roll)

    def _enter_strangle_by_target_return(self, for_roll: bool = False) -> bool:
        """
        Enter strangle based on target weekly return percentage.

        Calculates required premium and finds furthest OTM strikes that meet it.

        Args:
            for_roll: If True, look for next week's expiry (rolling shorts).
        """
        logger.info("=" * 60)
        logger.info(f"TARGET RETURN MODE: Seeking {self.weekly_target_return_pct}% weekly return")
        logger.info("=" * 60)

        # Calculate margin requirement (approx 20% of notional)
        margin_per_contract = self.current_underlying_price * 100 * 0.20

        # Calculate target premium
        target_premium = margin_per_contract * (self.weekly_target_return_pct / 100)

        logger.info(f"SPY: ${self.current_underlying_price:.2f} | Margin estimate: ${margin_per_contract:.2f}")
        logger.info(f"Target premium needed for {self.weekly_target_return_pct}%: ${target_premium:.2f}")

        # Find strangle options by target premium
        strangle_options = self.client.find_strangle_by_target_premium(
            self.underlying_uic,
            self.current_underlying_price,
            target_premium,
            weekly=True,
            for_roll=for_roll
        )

        if not strangle_options:
            logger.error(f"FAILED: Cannot find strikes that provide ${target_premium:.2f} premium")
            logger.warning(f"Consider lowering weekly_target_return_percent from {self.weekly_target_return_pct}%")
            return False

        call_option = strangle_options["call"]
        put_option = strangle_options["put"]

        # Log the actual premium we'll receive
        actual_premium = strangle_options.get("total_premium", 0)
        actual_return = (actual_premium / margin_per_contract) * 100 if margin_per_contract > 0 else 0

        logger.info("-" * 60)
        logger.info(f"STRIKES FOUND: Put ${put_option['strike']:.0f} / Call ${call_option['strike']:.0f}")
        logger.info(f"PREMIUM: ${actual_premium:.2f} (target was ${target_premium:.2f})")
        logger.info(f"ACTUAL RETURN: {actual_return:.2f}% (target was {self.weekly_target_return_pct}%)")
        logger.info("-" * 60)

        # Skip bid-ask check since we already have prices from find_strangle_by_target_premium
        call_price = call_option.get("bid", 0)
        put_price = put_option.get("bid", 0)

        if call_price <= 0 or put_price <= 0:
            logger.error("Invalid option prices")
            return False

        # Continue with order placement (same as multiplier approach)
        return self._execute_strangle_order(call_option, put_option, call_price, put_price)

    def _enter_strangle_by_multiplier(self, for_roll: bool = False) -> bool:
        """
        Enter strangle using expected move multiplier approach.

        This is the original approach: calculate expected move from VIX and apply multiplier.

        Args:
            for_roll: If True, look for next week's expiry (rolling shorts).
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

        # Calculate expected move using ACTUAL DTE, not hardcoded 7 days
        # Using VIX as a proxy for implied volatility
        iv = self.current_vix / 100  # Convert VIX to decimal
        expected_move = self.client.calculate_expected_move(
            self.current_underlying_price,
            iv,
            days=weekly_dte  # Use actual DTE instead of hardcoded 7
        )

        logger.info(f"Weekly expected move: ${expected_move:.2f} ({iv*100:.1f}% IV)")

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

        return self._execute_strangle_order(call_option, put_option, call_price, put_price)

    def _execute_strangle_order(
        self,
        call_option: dict,
        put_option: dict,
        call_price: float,
        put_price: float
    ) -> bool:
        """
        Execute the strangle order with slippage protection.
        Shared by both target return and multiplier approaches.
        """

        # Place sell orders for strangle with slippage protection
        legs = [
            {
                "uic": call_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Sell",
                "amount": self.position_size
            },
            {
                "uic": put_option["uic"],
                "asset_type": "StockOption",
                "buy_sell": "Sell",
                "amount": self.position_size
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

        # Place buy orders to close with slippage protection
        legs = [
            {
                "uic": self.short_strangle.call.uic,
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.short_strangle.call.quantity
            },
            {
                "uic": self.short_strangle.put.uic,
                "asset_type": "StockOption",
                "buy_sell": "Buy",
                "amount": self.short_strangle.put.quantity
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

        # Step 1: Close current long straddle
        if self.long_straddle:
            if not self.close_long_straddle():
                logger.error("Failed to close long straddle during recenter")
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
                    return False
            else:
                logger.error("Failed to find ATM options for recentered straddle")
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

        # Temporarily enter new strangle to get quotes (without actually placing orders)
        # We'll use the dry_run logic to simulate this
        old_dry_run_state = self.dry_run
        self.dry_run = True  # Temporarily enable dry run to get quotes only

        new_shorts_success = self.enter_short_strangle(for_roll=True)
        new_premium = self.short_strangle.premium_collected if self.short_strangle and new_shorts_success else 0

        self.dry_run = old_dry_run_state  # Restore original dry run state

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
                return False

        # Step 6: Enter new shorts for next week (for real this time)
        # CRITICAL: Pass for_roll=True to look for NEXT week's expiry (5-12 DTE)
        # Per Brian Terry's strategy: "roll the date out one week"
        if not self.enter_short_strangle(for_roll=True):
            logger.error("Failed to enter new shorts after rolling")
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

        # Close long straddle
        if self.long_straddle:
            if not self.close_long_straddle():
                logger.error("Failed to close long straddle during exit")
                success = False

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

        # Check circuit breaker
        if self.client.is_circuit_open():
            return "Circuit breaker open - trading halted"

        # Update market data
        if not self.update_market_data():
            return "Failed to update market data"

        # PRIORITY SAFETY CHECKS (before normal logic)
        # Check for emergency exit condition (5%+ move)
        if self.check_emergency_exit_condition():
            logger.critical("EMERGENCY EXIT TRIGGERED - Closing all positions immediately")
            if self.exit_all_positions():
                return "EMERGENCY EXIT - Massive move detected"
            else:
                return "EMERGENCY EXIT FAILED - Manual intervention required"

        # Check for ITM risk on short options
        if self.check_shorts_itm_risk():
            logger.critical("ITM RISK DETECTED - Rolling shorts immediately")
            if self.roll_weekly_shorts():
                return "Emergency roll - shorts approaching ITM"
            else:
                logger.critical("Failed to roll shorts at ITM risk - closing all positions")
                if self.exit_all_positions():
                    return "Emergency exit - could not roll ITM shorts"

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
                if self.execute_recenter():
                    action_taken = "Executed 5-point recenter (before adding shorts)"
            else:
                # Check if we're waiting for old shorts to expire (debit roll skip)
                # Per Brian Terry: "You aren't rolling, you're starting a new weekly cycle"
                now_est = get_us_market_time()
                today = now_est.date()
                today_weekday = now_est.strftime("%A")

                # If we closed shorts this week due to debit, wait until Monday
                if self._shorts_closed_date:
                    # Calculate when we can enter new shorts
                    # If closed on Thursday/Friday, wait until Monday
                    days_since_close = (today - self._shorts_closed_date).days
                    if days_since_close < 3 and today_weekday not in ["Monday", "Tuesday", "Wednesday"]:
                        logger.info(f"Waiting for new week to enter shorts (closed {self._shorts_closed_date}, today is {today_weekday})")
                        action_taken = "Waiting for Monday to enter new shorts"
                    else:
                        # New week - clear the flag and enter new shorts
                        logger.info(f"New week started - clearing shorts_closed_date flag")
                        self._shorts_closed_date = None
                        if self.enter_short_strangle(for_roll=False):
                            action_taken = "Added short strangle (new weekly cycle)"
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

            # Check recenter condition
            elif self._check_recenter_condition():
                if self.execute_recenter():
                    action_taken = "Executed 5-point recenter"

            # Check roll condition
            else:
                should_roll, challenged_side = self.should_roll_shorts()
                if should_roll:
                    if self.roll_weekly_shorts(challenged_side=challenged_side):
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
            # Estimated total theta earned = current daily net theta × days held since entry
            # This gives a reasonable estimate even when bot restarts
            "estimated_theta_earned": net_theta * (self.short_strangle.days_held if self.short_strangle else 0),
            # Weekly theta target = current daily net theta × 5 trading days
            "weekly_theta_target": net_theta * 5,
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

        # Use get_current_metrics() which calculates theta_cost correctly (scaled by 100 × qty)
        metrics = self.get_current_metrics()

        # Calculate daily P&L
        daily_pnl = self.metrics.total_pnl - self.metrics.daily_pnl_start

        # Build summary data
        summary = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "state": self.state.value,
            "spy_open": self.metrics.spy_open,
            "spy_close": self.current_underlying_price,
            "spy_range": self.metrics.spy_range,
            "vix_avg": self.metrics.vix_avg,
            "vix_high": self.metrics.vix_high,
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
            "notes": ""
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
