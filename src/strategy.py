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
4. Roll weekly shorts on Thursday/Friday
5. Exit entire trade when 30-60 DTE remains on Longs

Author: Trading Bot Developer
Date: 2024
"""

import logging
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from src.saxo_client import SaxoClient, BuySell, OrderType

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


@dataclass
class StraddlePosition:
    """
    Represents a straddle position (long call + long put at same strike).

    Attributes:
        call: The call option position
        put: The put option position
        initial_strike: The strike price at entry
        entry_underlying_price: Underlying price when position was opened
    """
    call: Optional[OptionPosition] = None
    put: Optional[OptionPosition] = None
    initial_strike: float = 0.0
    entry_underlying_price: float = 0.0

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
    """
    call: Optional[OptionPosition] = None
    put: Optional[OptionPosition] = None
    call_strike: float = 0.0
    put_strike: float = 0.0
    expiry: str = ""

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
    """
    total_premium_collected: float = 0.0
    total_straddle_cost: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    recenter_count: int = 0
    roll_count: int = 0

    @property
    def total_pnl(self) -> float:
        """Calculate total P&L."""
        return self.realized_pnl + self.unrealized_pnl


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

    def __init__(self, client: SaxoClient, config: Dict[str, Any], trade_logger: Any = None):
        """
        Initialize the strategy.

        Args:
            client: SaxoClient instance for API operations
            config: Configuration dictionary with strategy parameters
            trade_logger: Optional logger service for trade logging
        """
        self.client = client
        self.config = config
        self.strategy_config = config["strategy"]
        self.trade_logger = trade_logger

        # Strategy state
        self.state = StrategyState.IDLE
        self.long_straddle: Optional[StraddlePosition] = None
        self.short_strangle: Optional[StranglePosition] = None
        self.metrics = StrategyMetrics()

        # Underlying tracking
        self.underlying_uic = self.strategy_config["underlying_uic"]
        self.underlying_symbol = self.strategy_config["underlying_symbol"]
        self.vix_uic = self.strategy_config["vix_uic"]

        # Current market data
        self.current_underlying_price: float = 0.0
        self.current_vix: float = 0.0
        self.initial_straddle_strike: float = 0.0

        # Strategy parameters
        self.recenter_threshold = self.strategy_config["recenter_threshold_points"]
        self.max_vix = self.strategy_config["max_vix_entry"]
        self.min_dte = self.strategy_config["long_straddle_min_dte"]
        self.max_dte = self.strategy_config["long_straddle_max_dte"]
        self.exit_dte_min = self.strategy_config["exit_dte_min"]
        self.exit_dte_max = self.strategy_config["exit_dte_max"]
        self.strangle_multiplier_min = self.strategy_config["weekly_strangle_multiplier_min"]
        self.strangle_multiplier_max = self.strategy_config["weekly_strangle_multiplier_max"]
        self.position_size = self.strategy_config["position_size"]
        self.max_spread_percent = self.strategy_config["max_bid_ask_spread_percent"]
        self.roll_days = self.strategy_config["roll_days"]

        logger.info(f"DeltaNeutralStrategy initialized for {self.underlying_symbol}")
        logger.info(f"Recenter threshold: {self.recenter_threshold} points")
        logger.info(f"VIX entry threshold: < {self.max_vix}")

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

        # Check if shorts are ITM or very close
        call_itm = price >= call_strike * 0.98  # Within 2% of strike
        put_itm = price <= put_strike * 1.02    # Within 2% of strike

        if call_itm:
            logger.critical(
                f"SHORT CALL ITM RISK! Price ${price:.2f} at/above strike ${call_strike:.2f}. "
                f"Immediate action required."
            )
            return True

        if put_itm:
            logger.critical(
                f"SHORT PUT ITM RISK! Price ${price:.2f} at/below strike ${put_strike:.2f}. "
                f"Immediate action required."
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

        # Place multi-leg order for the straddle
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

        order_response = self.client.place_multi_leg_order(legs)

        if not order_response:
            logger.error("Failed to place straddle order")
            return False

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
            entry_underlying_price=self.current_underlying_price
        )

        self.initial_straddle_strike = call_option["strike"]

        # Update metrics
        straddle_cost = (call_price + put_price) * self.position_size * 100
        self.metrics.total_straddle_cost += straddle_cost

        self.state = StrategyState.LONG_STRADDLE_ACTIVE

        logger.info(
            f"Long straddle entered: Strike {call_option['strike']}, "
            f"Expiry {call_option['expiry']}, Cost ${straddle_cost:.2f}"
        )

        # Log trade
        if self.trade_logger:
            self.trade_logger.log_trade(
                action="OPEN_LONG_STRADDLE",
                strike=call_option["strike"],
                price=call_price + put_price,
                delta=0.0,  # ATM straddle is approximately delta neutral
                pnl=0.0,
                saxo_client=self.client
            )

        return True

    def close_long_straddle(self) -> bool:
        """
        Close the current long straddle position.

        Returns:
            bool: True if closed successfully, False otherwise.
        """
        if not self.long_straddle or not self.long_straddle.is_complete:
            logger.warning("No complete long straddle to close")
            return False

        logger.info("Closing long straddle...")

        # Place sell orders for both legs
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

        order_response = self.client.place_multi_leg_order(legs)

        if not order_response:
            logger.error("Failed to close straddle")
            return False

        # Calculate realized P&L
        entry_cost = (
            self.long_straddle.call.entry_price +
            self.long_straddle.put.entry_price
        ) * self.position_size * 100

        exit_value = self.long_straddle.total_value
        realized_pnl = exit_value - entry_cost
        self.metrics.realized_pnl += realized_pnl

        logger.info(
            f"Long straddle closed. Entry cost: ${entry_cost:.2f}, "
            f"Exit value: ${exit_value:.2f}, P&L: ${realized_pnl:.2f}"
        )

        # Log trade
        if self.trade_logger:
            self.trade_logger.log_trade(
                action="CLOSE_LONG_STRADDLE",
                strike=self.long_straddle.initial_strike,
                price=(self.long_straddle.call.current_price +
                       self.long_straddle.put.current_price),
                delta=self.long_straddle.total_delta,
                pnl=realized_pnl,
                saxo_client=self.client
            )

        self.long_straddle = None
        return True

    # =========================================================================
    # SHORT STRANGLE METHODS
    # =========================================================================

    def enter_short_strangle(self) -> bool:
        """
        Enter a weekly short strangle for income generation.

        Sells OTM Call and Put at 1.5-2x the weekly expected move.

        Returns:
            bool: True if strangle entered successfully, False otherwise.
        """
        logger.info("Attempting to enter short strangle...")

        if not self.current_underlying_price:
            if not self.update_market_data():
                return False

        # Calculate expected weekly move
        # Using VIX as a proxy for implied volatility
        iv = self.current_vix / 100  # Convert VIX to decimal
        expected_move = self.client.calculate_expected_move(
            self.current_underlying_price,
            iv,
            days=7
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
            weekly=True
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

        # Place sell orders for strangle
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

        order_response = self.client.place_multi_leg_order(legs)

        if not order_response:
            logger.error("Failed to place strangle order")
            return False

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

        self.state = StrategyState.FULL_POSITION

        logger.info(
            f"Short strangle entered: Put {put_option['strike']} / Call {call_option['strike']}, "
            f"Expiry {call_option['expiry']}, Premium ${premium:.2f}"
        )

        # Log trade
        if self.trade_logger:
            self.trade_logger.log_trade(
                action="OPEN_SHORT_STRANGLE",
                strike=f"{put_option['strike']}/{call_option['strike']}",
                price=call_price + put_price,
                delta=self.short_strangle.total_delta,
                pnl=0.0,
                saxo_client=self.client
            )

        return True

    def close_short_strangle(self) -> bool:
        """
        Close the current short strangle position.

        Returns:
            bool: True if closed successfully, False otherwise.
        """
        if not self.short_strangle or not self.short_strangle.is_complete:
            logger.warning("No complete short strangle to close")
            return False

        logger.info("Closing short strangle...")

        # Place buy orders to close
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

        order_response = self.client.place_multi_leg_order(legs)

        if not order_response:
            logger.error("Failed to close strangle")
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

        logger.info(
            f"Short strangle closed. Premium: ${premium_received:.2f}, "
            f"Close cost: ${close_cost:.2f}, P&L: ${realized_pnl:.2f}"
        )

        # Log trade
        if self.trade_logger:
            self.trade_logger.log_trade(
                action="CLOSE_SHORT_STRANGLE",
                strike=f"{self.short_strangle.put_strike}/{self.short_strangle.call_strike}",
                price=close_cost / (self.position_size * 100),
                delta=self.short_strangle.total_delta,
                pnl=realized_pnl,
                saxo_client=self.client
            )

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

        This involves:
        1. Closing the current long straddle
        2. Opening a new ATM long straddle at the same expiration
        3. Closing and resetting the weekly shorts

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

        # Step 1: Close current short strangle
        if self.short_strangle:
            if not self.close_short_strangle():
                logger.error("Failed to close short strangle during recenter")
                return False

        # Step 2: Close current long straddle
        if self.long_straddle:
            if not self.close_long_straddle():
                logger.error("Failed to close long straddle during recenter")
                return False

        # Step 3: Open new ATM long straddle at same expiration
        # We need to find ATM options at the new price but same expiry
        if original_expiry:
            # Calculate DTE for the original expiry
            expiry_date = datetime.strptime(original_expiry[:10], "%Y-%m-%d").date()
            dte = (expiry_date - datetime.now().date()).days

            # Find new ATM options
            atm_options = self.client.find_atm_options(
                self.underlying_uic,
                self.current_underlying_price,
                max(1, dte - 5),  # Allow some flexibility
                dte + 5
            )

            if atm_options:
                # Enter new straddle (simplified - reusing enter method)
                if not self.enter_long_straddle():
                    logger.error("Failed to enter new long straddle during recenter")
                    return False
            else:
                logger.error("Failed to find ATM options for recentered straddle")
                return False

        # Step 4: Enter new short strangle
        if not self.enter_short_strangle():
            logger.warning("Failed to enter new short strangle during recenter")
            # Continue anyway, straddle is more important

        self.metrics.recenter_count += 1

        logger.info(
            f"Recenter complete. New strike: {self.initial_straddle_strike:.2f}, "
            f"Total recenters: {self.metrics.recenter_count}"
        )

        # Log trade
        if self.trade_logger:
            self.trade_logger.log_trade(
                action="RECENTER",
                strike=self.initial_straddle_strike,
                price=self.current_underlying_price,
                delta=self.get_total_delta(),
                pnl=self.metrics.total_pnl,
                saxo_client=self.client
            )

        return True

    # =========================================================================
    # ROLLING AND EXIT LOGIC
    # =========================================================================

    def should_roll_shorts(self) -> bool:
        """
        Check if weekly shorts should be rolled.

        Shorts should be rolled on Thursday/Friday or if challenged
        (price approaching short strikes).

        Returns:
            bool: True if shorts should be rolled, False otherwise.
        """
        # Check if it's a roll day
        today = datetime.now().strftime("%A")
        is_roll_day = today in self.roll_days

        if is_roll_day:
            logger.info(f"Today is {today} - roll day for weekly shorts")
            return True

        # Check if shorts are being challenged (price within 50% of strike distance)
        if self.short_strangle and self.current_underlying_price:
            call_strike = self.short_strangle.call_strike
            put_strike = self.short_strangle.put_strike

            # Distance from current price to strikes
            call_distance = call_strike - self.current_underlying_price
            put_distance = self.current_underlying_price - put_strike

            # If price is within 50% of the distance to either strike, roll early
            original_call_distance = call_strike - self.initial_straddle_strike
            original_put_distance = self.initial_straddle_strike - put_strike

            if call_distance < original_call_distance * 0.5:
                logger.warning(f"Short call being challenged! Price ${self.current_underlying_price:.2f} approaching ${call_strike}")
                return True

            if put_distance < original_put_distance * 0.5:
                logger.warning(f"Short put being challenged! Price ${self.current_underlying_price:.2f} approaching ${put_strike}")
                return True

        return False

    def roll_weekly_shorts(self) -> bool:
        """
        Roll the weekly short strangle to the next week.

        Returns:
            bool: True if roll successful, False otherwise.
        """
        logger.info("Rolling weekly shorts...")

        self.state = StrategyState.ROLLING_SHORTS

        # Close current shorts
        if self.short_strangle:
            if not self.close_short_strangle():
                logger.error("Failed to close shorts for rolling")
                return False

        # Enter new shorts for next week
        if not self.enter_short_strangle():
            logger.error("Failed to enter new shorts after rolling")
            return False

        self.metrics.roll_count += 1
        self.state = StrategyState.FULL_POSITION

        logger.info(f"Weekly shorts rolled successfully. Total rolls: {self.metrics.roll_count}")

        return True

    def should_exit_trade(self) -> bool:
        """
        Check if the entire trade should be exited.

        Exit when 30-60 DTE remains on the long straddle.

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

        if self.exit_dte_min <= dte <= self.exit_dte_max:
            logger.info(
                f"EXIT CONDITION MET: {dte} DTE on long straddle "
                f"(threshold: {self.exit_dte_min}-{self.exit_dte_max} DTE)"
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
                self.trade_logger.log_trade(
                    action="EXIT_ALL",
                    strike=self.initial_straddle_strike,
                    price=self.current_underlying_price,
                    delta=0.0,
                    pnl=self.metrics.total_pnl,
                    saxo_client=self.client
                )

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
                if self.enter_short_strangle():
                    action_taken = "Entered long straddle and short strangle"

        elif self.state == StrategyState.WAITING_VIX:
            # Check if VIX condition is now met
            if self.check_vix_entry_condition():
                self.state = StrategyState.IDLE
                action_taken = "VIX condition met, ready to enter"

        elif self.state == StrategyState.LONG_STRADDLE_ACTIVE:
            # Try to add short strangle
            if self.enter_short_strangle():
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
            elif self.should_roll_shorts():
                if self.roll_weekly_shorts():
                    action_taken = "Rolled weekly shorts"

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
