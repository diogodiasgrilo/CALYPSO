"""
metrics.py - Strategy performance metrics tracking

This module defines the StrategyMetrics dataclass for tracking
P&L, trade statistics, and daily performance metrics.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional

# Path for persistent metrics storage (in project root data/ folder)
METRICS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data",
    "delta_neutral_metrics.json"
)

# Configure module logger
logger = logging.getLogger(__name__)


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

    def reset_cycle_metrics(self):
        """
        Reset cycle-specific metrics when starting a new trading cycle.

        This should be called after exit_all_positions() when the bot returns
        to IDLE state with no positions. It resets cumulative metrics for the
        cycle while preserving lifetime statistics.

        RESETS (cycle-specific):
            - total_premium_collected: Premium from shorts in this cycle
            - total_straddle_cost: Cost of longs in this cycle
            - realized_pnl: P&L from closed positions in this cycle
            - unrealized_pnl: Current open position P&L
            - recenter_count: Number of recenters this cycle
            - roll_count: Number of rolls this cycle

        PRESERVES (lifetime stats):
            - trade_count, winning_trades, losing_trades
            - best_trade_pnl, worst_trade_pnl, total_trade_pnl
            - peak_pnl, max_drawdown
            - daily_* metrics (handled separately by reset_daily_tracking)
        """
        logger.info("Resetting cycle metrics for new trading cycle")
        self.total_premium_collected = 0.0
        self.total_straddle_cost = 0.0
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.recenter_count = 0
        self.roll_count = 0
        # Note: daily_recenter_count and daily_roll_count are NOT reset here
        # They get reset by reset_daily_tracking() at market open

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
        from shared.market_hours import get_us_market_time
        return {
            "total_premium_collected": self.total_premium_collected,
            "total_straddle_cost": self.total_straddle_cost,
            "realized_pnl": self.realized_pnl,
            "recenter_count": self.recenter_count,
            "roll_count": self.roll_count,
            # Daily metrics (only valid for same trading day)
            "daily_recenter_count": self.daily_recenter_count,
            "daily_roll_count": self.daily_roll_count,
            "daily_pnl_start": self.daily_pnl_start,
            "spy_open": self.spy_open,
            "spy_high": self.spy_high,
            "spy_low": self.spy_low,
            "vix_high": self.vix_high,
            "vix_samples": self.vix_samples if self.vix_samples else [],
            "daily_metrics_date": get_us_market_time().strftime("%Y-%m-%d"),
            # Trade tracking
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
        from shared.market_hours import get_us_market_time
        metrics = cls()
        metrics.total_premium_collected = data.get("total_premium_collected", 0.0)
        metrics.total_straddle_cost = data.get("total_straddle_cost", 0.0)
        metrics.realized_pnl = data.get("realized_pnl", 0.0)
        metrics.recenter_count = data.get("recenter_count", 0)
        metrics.roll_count = data.get("roll_count", 0)

        # Only restore daily metrics if they're from today (same trading day)
        # This ensures daily tracking resets properly on new days
        saved_date = data.get("daily_metrics_date", data.get("daily_counts_date", ""))
        today = get_us_market_time().strftime("%Y-%m-%d")
        if saved_date == today:
            metrics.daily_recenter_count = data.get("daily_recenter_count", 0)
            metrics.daily_roll_count = data.get("daily_roll_count", 0)
            metrics.daily_pnl_start = data.get("daily_pnl_start", 0.0)
            metrics.spy_open = data.get("spy_open", 0.0)
            metrics.spy_high = data.get("spy_high", 0.0)
            metrics.spy_low = data.get("spy_low", 0.0)
            metrics.vix_high = data.get("vix_high", 0.0)
            metrics.vix_samples = data.get("vix_samples", [])
        # else: daily metrics stay at default (new day - will be initialized at market open)

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
