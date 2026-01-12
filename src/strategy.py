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
import time
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
        if self.dry_run:
            logger.warning("DRY RUN MODE - No real orders will be placed")

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

                self.long_straddle = StraddlePosition(
                    call=call_option,
                    put=put_option,
                    initial_strike=call_data["strike"],
                    entry_underlying_price=call_data["strike"]  # Approximate
                )

                # Set the initial straddle strike for recentering logic
                self.initial_straddle_strike = call_data["strike"]

                logger.info(
                    f"Recovered long straddle: Strike ${call_data['strike']:.2f}, "
                    f"Expiry {call_data['expiry']}, "
                    f"Qty {call_data['quantity']}"
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

                self.short_strangle = StranglePosition(
                    call=call_option,
                    put=put_option,
                    call_strike=call_data["strike"],
                    put_strike=put_data["strike"],
                    expiry=expiry
                )

                logger.info(
                    f"Recovered short strangle: Call ${call_data['strike']:.2f}, "
                    f"Put ${put_data['strike']:.2f}, Expiry {expiry}"
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
                expiry = f"20{year}{month}{day}"  # Format: 20260331
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

            return {
                "position_id": str(pos_base.get("PositionId", "")),
                "uic": pos_base.get("Uic", 0),
                "symbol": symbol,
                "strike": float(strike) if strike else 0,
                "expiry": str(expiry) if expiry else "",
                "option_type": option_type,
                "quantity": abs(pos_base.get("Amount", 0)),
                "entry_price": pos_base.get("OpenPrice", 0) or pos_view.get("AverageOpenPrice", 0),
                "current_price": pos_view.get("CurrentPrice", 0) or pos_view.get("MarketValue", 0),
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
            }

        except Exception as e:
            logger.error(f"Error parsing option position {symbol}: {e}")
            return None

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

        # In dry_run mode, simulate the order
        if self.dry_run:
            order_response = {"OrderId": f"SIMULATED_{int(time.time())}"}
            logger.info("[DRY RUN] Simulating long straddle order (no real order placed)")
        else:
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
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            self.trade_logger.log_trade(
                action=f"{action_prefix}OPEN_LONG_STRADDLE",
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

        # In dry_run mode, simulate the order
        if self.dry_run:
            order_response = {"OrderId": f"SIMULATED_{int(time.time())}"}
            logger.info("[DRY RUN] Simulating close straddle order (no real order placed)")
        else:
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
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            self.trade_logger.log_trade(
                action=f"{action_prefix}CLOSE_LONG_STRADDLE",
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

        # In dry_run mode, simulate the order
        if self.dry_run:
            order_response = {"OrderId": f"SIMULATED_{int(time.time())}"}
            logger.info("[DRY RUN] Simulating short strangle order (no real order placed)")
        else:
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

        # Log each leg individually to Trades tab for detailed premium tracking
        if self.trade_logger:
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            roll_reason = "Weekly Roll" if self.metrics.roll_count > 0 else "Initial Entry"

            # Log Short Call
            self.trade_logger.log_trade(
                action=f"{action_prefix}OPEN_SHORT_Call",
                strike=call_option['strike'],
                price=call_price,
                delta=-0.15,  # Approximation for OTM call
                pnl=0.0,
                option_type="Short Call",
                expiry_date=call_option['expiry'],
                quantity=self.position_size,
                trade_reason=roll_reason,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                premium_received=call_price * self.position_size * 100,
                saxo_client=self.client
            )

            # Log Short Put
            self.trade_logger.log_trade(
                action=f"{action_prefix}OPEN_SHORT_Put",
                strike=put_option['strike'],
                price=put_price,
                delta=0.15,  # Approximation for OTM put
                pnl=0.0,
                option_type="Short Put",
                expiry_date=put_option['expiry'],
                quantity=self.position_size,
                trade_reason=roll_reason,
                underlying_price=self.current_underlying_price,
                vix=self.current_vix,
                premium_received=put_price * self.position_size * 100,
                saxo_client=self.client
            )

            logger.info(f"Logged short strangle legs to Trades: Call ${call_option['strike']} (+${call_price * 100:.2f}), Put ${put_option['strike']} (+${put_price * 100:.2f})")

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

        # In dry_run mode, simulate the order
        if self.dry_run:
            order_response = {"OrderId": f"SIMULATED_{int(time.time())}"}
            logger.info("[DRY RUN] Simulating close strangle order (no real order placed)")
        else:
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
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            self.trade_logger.log_trade(
                action=f"{action_prefix}CLOSE_SHORT_STRANGLE",
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
            action_prefix = "[SIMULATED] " if self.dry_run else ""
            self.trade_logger.log_trade(
                action=f"{action_prefix}RECENTER",
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

    def should_roll_shorts(self) -> tuple:
        """
        Check if weekly shorts should be rolled.

        Shorts should be rolled on Thursday/Friday or if challenged
        (price approaching short strikes).

        Returns:
            tuple: (should_roll: bool, challenged_side: str or None)
                   challenged_side is "call", "put", or None for scheduled roll
        """
        # Check if it's a roll day
        today = datetime.now().strftime("%A")
        is_roll_day = today in self.roll_days

        if is_roll_day:
            logger.info(f"Today is {today} - roll day for weekly shorts")
            return (True, None)  # Scheduled roll, no specific challenge

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
                return (True, "call")

            if put_distance < original_put_distance * 0.5:
                logger.warning(f"Short put being challenged! Price ${self.current_underlying_price:.2f} approaching ${put_strike}")
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

        # Close current shorts and capture P&L
        if self.short_strangle:
            # Get current value before closing
            old_premium = self.short_strangle.premium_collected
            if not self.close_short_strangle():
                logger.error("Failed to close shorts for rolling")
                return False

        # Enter new shorts for next week
        # The enter_short_strangle() method uses CURRENT price to calculate strikes
        # This naturally implements the "roll both sides" strategy:
        # - Challenged side: moves further from current price
        # - Unchallenged side: moves closer to current price (more credit)
        if not self.enter_short_strangle():
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
                action_prefix = "[SIMULATED] " if self.dry_run else ""
                self.trade_logger.log_trade(
                    action=f"{action_prefix}EXIT_ALL",
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
            else:
                should_roll, challenged_side = self.should_roll_shorts()
                if should_roll:
                    if self.roll_weekly_shorts(challenged_side=challenged_side):
                        if challenged_side:
                            action_taken = f"Rolled weekly shorts ({challenged_side} challenged)"
                        else:
                            action_taken = "Rolled weekly shorts (scheduled)"

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

        # Long straddle value and P&L
        if self.long_straddle and self.long_straddle.is_complete:
            if self.long_straddle.call:
                call_value = self.long_straddle.call.current_price * 100  # Per contract
                call_cost = self.long_straddle.call.entry_price * 100
                long_straddle_value += call_value
                long_straddle_pnl += (call_value - call_cost)
            if self.long_straddle.put:
                put_value = self.long_straddle.put.current_price * 100
                put_cost = self.long_straddle.put.entry_price * 100
                long_straddle_value += put_value
                long_straddle_pnl += (put_value - put_cost)

        # Short strangle value and P&L (negative value = we owe, positive P&L when value decreases)
        if self.short_strangle and self.short_strangle.is_complete:
            if self.short_strangle.call:
                call_value = self.short_strangle.call.current_price * 100
                call_premium = self.short_strangle.call.entry_price * 100
                short_strangle_value -= call_value  # Liability
                short_strangle_pnl += (call_premium - call_value)  # Profit when value drops
            if self.short_strangle.put:
                put_value = self.short_strangle.put.current_price * 100
                put_premium = self.short_strangle.put.entry_price * 100
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

        # Calculate theta (daily) - multiply by 100 for contract size
        # Long theta is negative (costs us), Short theta is positive (earns us)
        long_theta_cost = 0.0
        short_theta_income = 0.0

        if self.long_straddle:
            if self.long_straddle.call:
                long_theta_cost += abs(getattr(self.long_straddle.call, 'theta', 0)) * 100
            if self.long_straddle.put:
                long_theta_cost += abs(getattr(self.long_straddle.put, 'theta', 0)) * 100

        if self.short_strangle:
            if self.short_strangle.call:
                short_theta_income += abs(getattr(self.short_strangle.call, 'theta', 0)) * 100
            if self.short_strangle.put:
                short_theta_income += abs(getattr(self.short_strangle.put, 'theta', 0)) * 100

        net_theta = short_theta_income - long_theta_cost

        return {
            # Account Summary fields
            "spy_price": self.current_underlying_price,
            "vix": self.current_vix,
            "unrealized_pnl": self.metrics.unrealized_pnl,
            "long_straddle_value": long_straddle_value,
            "short_strangle_value": short_strangle_value,
            "strategy_margin": 0,  # Would need Saxo API call for margin
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
            "trade_count": self.metrics.trade_count if hasattr(self.metrics, 'trade_count') else 0,
            "roll_count": self.metrics.roll_count,
            "recenter_count": self.metrics.recenter_count,

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

        greeks = self.get_total_greeks()

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
            "total_delta": greeks["delta"],
            "total_gamma": greeks["gamma"],
            "total_theta": greeks["theta"],
            "daily_pnl": daily_pnl,
            "realized_pnl": self.metrics.realized_pnl,
            "unrealized_pnl": self.metrics.unrealized_pnl,
            "premium_collected": self.metrics.total_premium_collected,
            "trades_count": 0,  # Could track if needed
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
        logger.info(f"Daily summary logged: P&L ${daily_pnl:.2f}, Theta ${greeks['theta']:.2f}")

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
