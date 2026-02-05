"""
strategy.py - MEIC-TF (Trend Following Hybrid) Strategy Implementation

This module extends the base MEIC strategy with EMA-based trend direction detection.
Before each entry, it checks 20 EMA vs 40 EMA on SPX 1-minute bars to determine
whether to place a full iron condor, call spread only, or put spread only.

Trend Detection:
- BULLISH (20 EMA > 40 EMA by >0.1%): Place PUT spread only (calls are risky)
- BEARISH (20 EMA < 40 EMA by >0.1%): Place CALL spread only (puts are risky)
- NEUTRAL (within 0.1%): Place full iron condor (standard MEIC behavior)

The idea comes from Tammy Chambless running MEIC alongside METF (Multiple Entry Trend Following).
For capital-constrained accounts, this hybrid combines both concepts in one bot.

Author: Trading Bot Developer
Date: 2026-02-04

Based on: bots/meic/strategy.py (MEIC v1.2.0)
See docs/MEIC_STRATEGY_SPECIFICATION.md for base MEIC details.
"""

import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell, OrderType
from shared.alert_service import AlertService, AlertType, AlertPriority
from shared.market_hours import get_us_market_time
from shared.technical_indicators import get_current_ema

# Import the base MEIC classes we need
from bots.meic.strategy import (
    MEICStrategy,
    MEICState,
    IronCondorEntry,
    MEICDailyState,
    MarketData,
    PositionRegistry,
    REGISTRY_FILE,
    ENTRY_MAX_RETRIES,
    ENTRY_RETRY_DELAY_SECONDS,
    is_fomc_meeting_day,
)

# Configure module logger
logger = logging.getLogger(__name__)


# =============================================================================
# TREND DETECTION
# =============================================================================

class TrendSignal(Enum):
    """
    Trend direction signal based on EMA crossover.

    Detection logic:
    - BULLISH: 20 EMA > 40 EMA by more than threshold (uptrend)
    - BEARISH: 20 EMA < 40 EMA by more than threshold (downtrend)
    - NEUTRAL: EMAs are within threshold of each other (range-bound)
    """
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


# =============================================================================
# EXTENDED ENTRY CLASS
# =============================================================================

@dataclass
class TFIronCondorEntry(IronCondorEntry):
    """
    Extended IronCondorEntry that tracks which sides were placed.

    For trend-following entries, only one side may be placed:
    - BULLISH trend: put_only = True (only put spread placed)
    - BEARISH trend: call_only = True (only call spread placed)
    - NEUTRAL: full IC (both sides placed, call_only=False, put_only=False)
    """
    # Track what was actually placed
    call_only: bool = False   # Only call spread was placed (bearish signal)
    put_only: bool = False    # Only put spread was placed (bullish signal)
    trend_signal: Optional[TrendSignal] = None  # The trend signal at entry time

    @property
    def is_one_sided(self) -> bool:
        """True if only one side was placed (not a full IC)."""
        return self.call_only or self.put_only

    @property
    def total_credit(self) -> float:
        """Total credit received (may be just one side for trend entries)."""
        if self.call_only:
            return self.call_spread_credit
        elif self.put_only:
            return self.put_spread_credit
        return self.call_spread_credit + self.put_spread_credit


# =============================================================================
# MEIC-TF STRATEGY
# =============================================================================

class MEICTFStrategy(MEICStrategy):
    """
    MEIC-TF (Trend Following Hybrid) Strategy Implementation.

    Extends MEICStrategy with EMA-based trend detection:
    - Before each entry, checks 20 EMA vs 40 EMA on SPX 1-minute bars
    - BULLISH: Place PUT spread only (calls are risky in uptrend)
    - BEARISH: Place CALL spread only (puts are risky in downtrend)
    - NEUTRAL: Place full iron condor (standard MEIC behavior)

    All other functionality (stop losses, position management, reconciliation)
    is inherited from MEICStrategy.
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
        Initialize the MEIC-TF strategy.

        Args:
            saxo_client: Authenticated Saxo API client
            config: Strategy configuration dictionary
            logger_service: Trade logging service
            dry_run: If True, simulate trades without placing real orders
            alert_service: Optional AlertService for SMS/email notifications
        """
        # Initialize trend filter config BEFORE calling super().__init__
        # because parent __init__ calls methods that might need these values
        self.trend_config = config.get("trend_filter", {})
        self.trend_enabled = self.trend_config.get("enabled", True)
        self.ema_short_period = self.trend_config.get("ema_short_period", 20)
        self.ema_long_period = self.trend_config.get("ema_long_period", 40)
        self.ema_neutral_threshold = self.trend_config.get("ema_neutral_threshold", 0.001)
        self.recheck_each_entry = self.trend_config.get("recheck_each_entry", True)
        self.chart_bars_count = self.trend_config.get("chart_bars_count", 50)
        self.chart_horizon_minutes = self.trend_config.get("chart_horizon_minutes", 1)

        # Track current trend signal
        self._current_trend: Optional[TrendSignal] = None
        self._last_trend_check: Optional[datetime] = None

        # Call parent init (this sets up everything else)
        super().__init__(saxo_client, config, logger_service, dry_run, alert_service)

        # Update alert service name
        if not alert_service:
            self.alert_service = AlertService(config, "MEIC-TF")

        logger.info(f"MEIC-TF Strategy initialized")
        logger.info(f"  Trend filter enabled: {self.trend_enabled}")
        logger.info(f"  EMA periods: {self.ema_short_period}/{self.ema_long_period}")
        logger.info(f"  Neutral threshold: {self.ema_neutral_threshold * 100:.2f}%")
        logger.info(f"  Recheck each entry: {self.recheck_each_entry}")

    # =========================================================================
    # TREND DETECTION
    # =========================================================================

    def _get_trend_signal(self) -> TrendSignal:
        """
        Check 20/40 EMA crossover for trend direction.

        Uses SPX 1-minute bars from Saxo Chart API.

        Returns:
            TrendSignal indicating market direction
        """
        if not self.trend_enabled:
            return TrendSignal.NEUTRAL

        try:
            # Fetch 1-minute bars for SPX (via US500.I CFD)
            chart_data = self.client.get_chart_data(
                uic=self.underlying_uic,
                asset_type="CfdOnIndex",  # US500.I is a CFD
                horizon=self.chart_horizon_minutes,
                count=self.chart_bars_count
            )

            if not chart_data or "Data" not in chart_data:
                logger.warning("Could not fetch chart data for trend detection")
                return TrendSignal.NEUTRAL

            bars = chart_data["Data"]
            if len(bars) < self.ema_long_period:
                logger.warning(f"Insufficient bars for EMA: {len(bars)} < {self.ema_long_period}")
                return TrendSignal.NEUTRAL

            # Extract close prices (Saxo CFD data uses CloseBid, not Close)
            closes = []
            for bar in bars:
                close = bar.get("CloseBid") or bar.get("Close") or 0
                if close > 0:
                    closes.append(close)

            if len(closes) < self.ema_long_period:
                logger.warning(f"Insufficient valid closes: {len(closes)}")
                return TrendSignal.NEUTRAL

            # Calculate EMAs
            ema_short = get_current_ema(closes, self.ema_short_period)
            ema_long = get_current_ema(closes, self.ema_long_period)

            if ema_short <= 0 or ema_long <= 0:
                logger.warning(f"Invalid EMA values: short={ema_short}, long={ema_long}")
                return TrendSignal.NEUTRAL

            # Calculate percentage difference
            diff_pct = (ema_short - ema_long) / ema_long

            logger.info(
                f"Trend detection: EMA{self.ema_short_period}={ema_short:.2f}, "
                f"EMA{self.ema_long_period}={ema_long:.2f}, "
                f"diff={diff_pct*100:.3f}%"
            )

            # Determine trend signal
            if diff_pct > self.ema_neutral_threshold:
                signal = TrendSignal.BULLISH
            elif diff_pct < -self.ema_neutral_threshold:
                signal = TrendSignal.BEARISH
            else:
                signal = TrendSignal.NEUTRAL

            self._current_trend = signal
            self._last_trend_check = get_us_market_time()

            logger.info(f"Trend signal: {signal.value.upper()}")
            return signal

        except Exception as e:
            logger.error(f"Error in trend detection: {e}")
            return TrendSignal.NEUTRAL

    # =========================================================================
    # OVERRIDE: Entry initiation with trend detection
    # =========================================================================

    def _initiate_entry(self) -> str:
        """
        Initiate an entry with trend-based decision making.

        Overrides MEICStrategy._initiate_entry() to:
        1. Check trend signal before entry
        2. Place one-sided spread if trending
        3. Place full IC if neutral

        Returns:
            str: Description of action taken
        """
        entry_num = self._next_entry_index + 1
        logger.info(f"MEIC-TF: Initiating Entry #{entry_num} of {len(self.entry_times)}")

        # Check trend signal (or reuse if recent)
        if self.recheck_each_entry or self._current_trend is None:
            trend = self._get_trend_signal()
        else:
            trend = self._current_trend
            logger.info(f"Using cached trend signal: {trend.value}")

        # Pre-entry checks from parent
        if self._has_orphaned_orders():
            logger.error(f"Entry #{entry_num} blocked by orphaned orders")
            self._next_entry_index += 1
            return f"Entry #{entry_num} skipped - orphaned orders blocking"

        is_halted, halt_reason = self._check_market_halt()
        if is_halted:
            logger.warning(f"MKT-005: Market halt detected - {halt_reason}")
            return f"Entry #{entry_num} delayed - {halt_reason}"

        has_bp, bp_message = self._check_buying_power()
        if not has_bp:
            logger.warning(f"ORDER-004: {bp_message}")
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            self.alert_service.send_alert(
                alert_type=AlertType.MAX_LOSS,
                title=f"Entry #{entry_num} Skipped - Insufficient Margin",
                message=bp_message,
                priority=AlertPriority.HIGH,
                details={"entry_number": entry_num, "reason": "margin"}
            )
            return f"Entry #{entry_num} skipped - {bp_message}"

        self._entry_in_progress = True
        self.state = MEICState.ENTRY_IN_PROGRESS

        # Entry retry loop
        last_error = None
        for attempt in range(ENTRY_MAX_RETRIES):
            try:
                if attempt > 0:
                    logger.info(f"Entry #{entry_num} retry {attempt + 1}/{ENTRY_MAX_RETRIES}")
                    time.sleep(ENTRY_RETRY_DELAY_SECONDS)

                    if not self._is_entry_time():
                        logger.warning(f"Entry #{entry_num} window expired after {attempt} retries")
                        break

                    # Re-check trend on retry
                    if self.recheck_each_entry:
                        trend = self._get_trend_signal()

                # Create extended entry object
                entry = TFIronCondorEntry(entry_number=entry_num)
                entry.strategy_id = f"meic_tf_{get_us_market_time().strftime('%Y%m%d')}_entry{entry_num}"
                entry.trend_signal = trend
                self._current_entry = entry

                # Calculate strikes
                if not self._calculate_strikes(entry):
                    last_error = "Failed to calculate strikes"
                    continue

                # Execute based on trend signal
                if trend == TrendSignal.BULLISH:
                    logger.info(f"BULLISH trend → placing PUT spread only")
                    entry.put_only = True
                    if self.dry_run:
                        success = self._simulate_one_sided_entry(entry, "put")
                    else:
                        success = self._execute_put_spread_only(entry)

                elif trend == TrendSignal.BEARISH:
                    logger.info(f"BEARISH trend → placing CALL spread only")
                    entry.call_only = True
                    if self.dry_run:
                        success = self._simulate_one_sided_entry(entry, "call")
                    else:
                        success = self._execute_call_spread_only(entry)

                else:  # NEUTRAL
                    logger.info(f"NEUTRAL → placing full iron condor")
                    if self.dry_run:
                        success = self._simulate_entry(entry)
                    else:
                        success = self._execute_entry(entry)

                if success:
                    entry.entry_time = get_us_market_time()
                    entry.is_complete = True
                    self.daily_state.entries.append(entry)
                    self.daily_state.entries_completed += 1
                    self.daily_state.total_credit_received += entry.total_credit

                    # Track commission (2 or 4 legs)
                    legs = 2 if entry.is_one_sided else 4
                    entry.open_commission = legs * self.commission_per_leg * self.contracts_per_entry
                    self.daily_state.total_commission += entry.open_commission

                    # Calculate stop losses
                    self._calculate_stop_levels_tf(entry)

                    # Log to Google Sheets
                    self._log_entry(entry)

                    # Send alert with trend info
                    if entry.call_only:
                        position_summary = f"MEIC-TF Entry #{entry_num} [BEARISH]: Call {entry.short_call_strike}/{entry.long_call_strike}"
                    elif entry.put_only:
                        position_summary = f"MEIC-TF Entry #{entry_num} [BULLISH]: Put {entry.short_put_strike}/{entry.long_put_strike}"
                    else:
                        position_summary = f"MEIC-TF Entry #{entry_num} [NEUTRAL]: Full IC"

                    self.alert_service.position_opened(
                        position_summary=position_summary,
                        cost_or_credit=entry.total_credit,
                        details={
                            "spx_price": self.current_price,
                            "entry_number": entry_num,
                            "trend": trend.value,
                            "one_sided": entry.is_one_sided,
                            "attempts": attempt + 1
                        }
                    )

                    self._record_api_result(True)
                    self._next_entry_index += 1
                    self._entry_in_progress = False
                    self._current_entry = None

                    if self._next_entry_index < len(self.entry_times):
                        self.state = MEICState.MONITORING
                    else:
                        self.state = MEICState.MONITORING

                    self._save_state_to_disk()

                    result_msg = f"Entry #{entry_num} [{trend.value.upper()}] complete - Credit: ${entry.total_credit:.2f}"
                    if attempt > 0:
                        result_msg += f" (after {attempt + 1} attempts)"
                    return result_msg
                else:
                    last_error = "Entry execution failed"

            except Exception as e:
                logger.error(f"Entry #{entry_num} attempt {attempt + 1} error: {e}")
                last_error = str(e)

        # All retries exhausted
        self.daily_state.entries_failed += 1
        self._record_api_result(False, f"Entry #{entry_num} failed: {last_error}")
        self._next_entry_index += 1

        self._entry_in_progress = False
        self._current_entry = None

        if self.daily_state.active_entries:
            self.state = MEICState.MONITORING
        elif self._next_entry_index < len(self.entry_times):
            self.state = MEICState.WAITING_FIRST_ENTRY
        else:
            self.state = MEICState.DAILY_COMPLETE

        return f"Entry #{entry_num} failed after {ENTRY_MAX_RETRIES} retries: {last_error}"

    # =========================================================================
    # ONE-SIDED ENTRY EXECUTION
    # =========================================================================

    def _execute_call_spread_only(self, entry: TFIronCondorEntry) -> bool:
        """
        Execute only the call spread (for BEARISH signal).

        Leg order (safest):
        1. Long Call (buy protection first)
        2. Short Call (sell, now hedged)

        Args:
            entry: TFIronCondorEntry to execute

        Returns:
            True if both legs filled successfully
        """
        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error("Could not determine today's expiry")
            return False

        filled_legs = []

        try:
            # 1. Long Call (buy protection first)
            logger.info(f"Placing Long Call at {entry.long_call_strike}")
            long_call_result = self._place_option_order(
                strike=entry.long_call_strike,
                put_call="Call",
                buy_sell=BuySell.BUY,
                expiry=expiry,
                external_ref=f"{entry.strategy_id}_LC"
            )
            if not long_call_result:
                raise Exception("Long Call order failed")
            entry.long_call_position_id = long_call_result.get("position_id")
            entry.long_call_uic = long_call_result.get("uic")
            long_call_debit = long_call_result.get("debit", 0)
            filled_legs.append(("long_call", entry.long_call_position_id, entry.long_call_uic))
            self._register_position(entry, "long_call")

            # 2. Short Call (now hedged)
            logger.info(f"Placing Short Call at {entry.short_call_strike}")
            short_call_result = self._place_option_order(
                strike=entry.short_call_strike,
                put_call="Call",
                buy_sell=BuySell.SELL,
                expiry=expiry,
                external_ref=f"{entry.strategy_id}_SC"
            )
            if not short_call_result:
                raise Exception("Short Call order failed")
            entry.short_call_position_id = short_call_result.get("position_id")
            entry.short_call_uic = short_call_result.get("uic")
            short_call_credit = short_call_result.get("credit", 0)
            entry.call_spread_credit = short_call_credit - long_call_debit
            filled_legs.append(("short_call", entry.short_call_position_id, entry.short_call_uic))
            self._register_position(entry, "short_call")

            logger.info(f"Call spread complete: Credit ${entry.call_spread_credit:.2f}")
            return True

        except Exception as e:
            logger.error(f"Call spread entry failed: {e}")
            # Unwind if needed
            if filled_legs:
                self._unwind_partial_entry(filled_legs, entry)
            return False

    def _execute_put_spread_only(self, entry: TFIronCondorEntry) -> bool:
        """
        Execute only the put spread (for BULLISH signal).

        Leg order (safest):
        1. Long Put (buy protection first)
        2. Short Put (sell, now hedged)

        Args:
            entry: TFIronCondorEntry to execute

        Returns:
            True if both legs filled successfully
        """
        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error("Could not determine today's expiry")
            return False

        filled_legs = []

        try:
            # 1. Long Put (buy protection first)
            logger.info(f"Placing Long Put at {entry.long_put_strike}")
            long_put_result = self._place_option_order(
                strike=entry.long_put_strike,
                put_call="Put",
                buy_sell=BuySell.BUY,
                expiry=expiry,
                external_ref=f"{entry.strategy_id}_LP"
            )
            if not long_put_result:
                raise Exception("Long Put order failed")
            entry.long_put_position_id = long_put_result.get("position_id")
            entry.long_put_uic = long_put_result.get("uic")
            long_put_debit = long_put_result.get("debit", 0)
            filled_legs.append(("long_put", entry.long_put_position_id, entry.long_put_uic))
            self._register_position(entry, "long_put")

            # 2. Short Put (now hedged)
            logger.info(f"Placing Short Put at {entry.short_put_strike}")
            short_put_result = self._place_option_order(
                strike=entry.short_put_strike,
                put_call="Put",
                buy_sell=BuySell.SELL,
                expiry=expiry,
                external_ref=f"{entry.strategy_id}_SP"
            )
            if not short_put_result:
                raise Exception("Short Put order failed")
            entry.short_put_position_id = short_put_result.get("position_id")
            entry.short_put_uic = short_put_result.get("uic")
            short_put_credit = short_put_result.get("credit", 0)
            entry.put_spread_credit = short_put_credit - long_put_debit
            filled_legs.append(("short_put", entry.short_put_position_id, entry.short_put_uic))
            self._register_position(entry, "short_put")

            logger.info(f"Put spread complete: Credit ${entry.put_spread_credit:.2f}")
            return True

        except Exception as e:
            logger.error(f"Put spread entry failed: {e}")
            # Unwind if needed
            if filled_legs:
                self._unwind_partial_entry(filled_legs, entry)
            return False

    def _simulate_one_sided_entry(self, entry: TFIronCondorEntry, side: str) -> bool:
        """
        Simulate a one-sided entry (dry-run mode).

        Args:
            entry: TFIronCondorEntry to simulate
            side: "call" or "put"

        Returns:
            True if simulation successful
        """
        credit_ratio = 0.025
        base_id = int(datetime.now().timestamp() * 1000)

        if side == "call":
            entry.call_spread_credit = self.spread_width * credit_ratio * 100
            entry.short_call_position_id = f"DRY_{base_id}_SC"
            entry.long_call_position_id = f"DRY_{base_id}_LC"
            logger.info(f"[DRY RUN] Simulated Call Spread: Credit ${entry.call_spread_credit:.2f}")
        else:
            entry.put_spread_credit = self.spread_width * credit_ratio * 100
            entry.short_put_position_id = f"DRY_{base_id}_SP"
            entry.long_put_position_id = f"DRY_{base_id}_LP"
            logger.info(f"[DRY RUN] Simulated Put Spread: Credit ${entry.put_spread_credit:.2f}")

        return True

    # =========================================================================
    # STOP LOSS CALCULATION FOR ONE-SIDED ENTRIES
    # =========================================================================

    def _calculate_stop_levels_tf(self, entry: TFIronCondorEntry):
        """
        Calculate stop loss levels for trend-following entries.

        For one-sided entries, stop = credit received for that side.
        For full ICs, uses parent's logic (stop = total credit).

        Args:
            entry: TFIronCondorEntry to calculate stops for
        """
        MIN_STOP_LEVEL = 50.0

        if entry.call_only:
            # Only call spread placed
            credit = entry.call_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(f"CRITICAL: Low credit ${credit:.2f}, using minimum stop")
                credit = MIN_STOP_LEVEL

            if self.meic_plus_enabled:
                min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
                if credit > min_credit_for_meic_plus:
                    stop_level = credit - self.meic_plus_reduction
                else:
                    stop_level = credit
            else:
                stop_level = credit

            entry.call_side_stop = stop_level
            entry.put_side_stop = 0  # No put side
            logger.info(f"Stop level for call spread: ${stop_level:.2f}")

        elif entry.put_only:
            # Only put spread placed
            credit = entry.put_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(f"CRITICAL: Low credit ${credit:.2f}, using minimum stop")
                credit = MIN_STOP_LEVEL

            if self.meic_plus_enabled:
                min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
                if credit > min_credit_for_meic_plus:
                    stop_level = credit - self.meic_plus_reduction
                else:
                    stop_level = credit
            else:
                stop_level = credit

            entry.put_side_stop = stop_level
            entry.call_side_stop = 0  # No call side
            logger.info(f"Stop level for put spread: ${stop_level:.2f}")

        else:
            # Full IC - use parent's logic
            self._calculate_stop_levels(entry)

    # =========================================================================
    # OVERRIDE: Stop loss checking for one-sided entries
    # =========================================================================

    def _check_stop_losses(self) -> Optional[str]:
        """
        Check all active entries for stop loss triggers.

        Overrides parent to handle one-sided entries correctly.

        Returns:
            str describing stop action taken, or None
        """
        for entry in self.daily_state.active_entries:
            # Handle as TFIronCondorEntry if possible
            is_tf_entry = isinstance(entry, TFIronCondorEntry)

            if is_tf_entry and entry.call_only:
                # Only check call side
                if entry.call_side_stopped:
                    continue

                self._update_entry_prices(entry)

                pnl_valid, pnl_message = self._validate_pnl_sanity(entry)
                if not pnl_valid:
                    logger.error(f"DATA-003: Skipping stop check - {pnl_message}")
                    continue

                MIN_VALID_STOP = 50.0
                if entry.call_side_stop < MIN_VALID_STOP:
                    logger.error(f"SAFETY: Invalid call stop ${entry.call_side_stop:.2f}")
                    continue

                if entry.call_spread_value >= entry.call_side_stop:
                    return self._execute_stop_loss(entry, "call")

            elif is_tf_entry and entry.put_only:
                # Only check put side
                if entry.put_side_stopped:
                    continue

                self._update_entry_prices(entry)

                pnl_valid, pnl_message = self._validate_pnl_sanity(entry)
                if not pnl_valid:
                    logger.error(f"DATA-003: Skipping stop check - {pnl_message}")
                    continue

                MIN_VALID_STOP = 50.0
                if entry.put_side_stop < MIN_VALID_STOP:
                    logger.error(f"SAFETY: Invalid put stop ${entry.put_side_stop:.2f}")
                    continue

                if entry.put_spread_value >= entry.put_side_stop:
                    return self._execute_stop_loss(entry, "put")

            else:
                # Full IC - use parent's logic
                if entry.call_side_stopped and entry.put_side_stopped:
                    continue

                self._update_entry_prices(entry)

                pnl_valid, pnl_message = self._validate_pnl_sanity(entry)
                if not pnl_valid:
                    logger.error(f"DATA-003: Skipping stop check - {pnl_message}")
                    continue

                MIN_VALID_STOP = 50.0
                if entry.call_side_stop < MIN_VALID_STOP or entry.put_side_stop < MIN_VALID_STOP:
                    logger.error(f"SAFETY: Invalid stop levels")
                    continue

                if not entry.call_side_stopped:
                    if entry.call_spread_value >= entry.call_side_stop:
                        return self._execute_stop_loss(entry, "call")

                if not entry.put_side_stopped:
                    if entry.put_spread_value >= entry.put_side_stop:
                        return self._execute_stop_loss(entry, "put")

        return None

    # =========================================================================
    # OVERRIDE: Status summary with trend info
    # =========================================================================

    def get_status_summary(self) -> Dict:
        """
        Get current strategy status summary with trend info.

        Returns:
            Dict with status information including current trend
        """
        status = super().get_status_summary()

        # Add trend info
        status['current_trend'] = self._current_trend.value if self._current_trend else "unknown"
        status['trend_enabled'] = self.trend_enabled

        return status

    def get_detailed_position_status(self) -> List[str]:
        """
        Get detailed position status lines with trend info.

        Returns:
            List of status lines for logging
        """
        lines = super().get_detailed_position_status()

        # Add trend line at the start
        if self._current_trend:
            trend_line = f"  Trend: {self._current_trend.value.upper()} (EMA {self.ema_short_period}/{self.ema_long_period})"
            lines.insert(0, trend_line)

        return lines

    # =========================================================================
    # OVERRIDE: Logging for trend-following entries
    # =========================================================================

    def _log_entry(self, entry):
        """
        Log entry to Google Sheets with trend info.

        Overrides parent to:
        - Use "MEIC-TF" instead of "MEIC"
        - Include trend signal in the action
        - Handle one-sided entries (show only placed side's strikes)
        """
        try:
            # Determine entry type and format strikes accordingly
            is_tf_entry = isinstance(entry, TFIronCondorEntry)

            if is_tf_entry and entry.call_only:
                # Call spread only (bearish)
                strike_str = f"C:{entry.short_call_strike}/{entry.long_call_strike}"
                entry_type = "Call Spread"
                trend_tag = "[BEARISH]"
            elif is_tf_entry and entry.put_only:
                # Put spread only (bullish)
                strike_str = f"P:{entry.short_put_strike}/{entry.long_put_strike}"
                entry_type = "Put Spread"
                trend_tag = "[BULLISH]"
            else:
                # Full IC (neutral)
                strike_str = (
                    f"C:{entry.short_call_strike}/{entry.long_call_strike} "
                    f"P:{entry.short_put_strike}/{entry.long_put_strike}"
                )
                entry_type = "Iron Condor"
                trend_tag = "[NEUTRAL]"

            self.trade_logger.log_trade(
                action=f"MEIC-TF Entry #{entry.entry_number} {trend_tag}",
                strike=strike_str,
                price=entry.total_credit,
                delta=0.0,
                pnl=0.0,
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type=entry_type,
                premium_received=entry.total_credit,
                trade_reason=f"Entry | Trend: {trend_tag} | Credit: ${entry.total_credit:.2f}"
            )

            logger.info(
                f"Entry #{entry.entry_number} {trend_tag} logged to Sheets: "
                f"SPX={self.current_price:.2f}, Credit=${entry.total_credit:.2f}, "
                f"Type={entry_type}, Strikes: {strike_str}"
            )
        except Exception as e:
            logger.error(f"Failed to log entry: {e}")
