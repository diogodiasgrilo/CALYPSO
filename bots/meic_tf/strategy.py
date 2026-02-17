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

Based on: bots/meic/strategy.py (MEIC v1.2.8)
See docs/MEIC_STRATEGY_SPECIFICATION.md for base MEIC details.
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell
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
    # P&L sanity check constants (Fix #39 - one-sided entry validation)
    PNL_SANITY_CHECK_ENABLED,
    MAX_PNL_PER_IC,
    MIN_PNL_PER_IC,
)

# =============================================================================
# MEIC-TF SPECIFIC FILE PATHS (separate from MEIC)
# =============================================================================

# CRITICAL: MEIC-TF must use separate state files from MEIC to prevent conflicts
# when both bots run simultaneously. Each bot maintains its own:
# - State file: Tracks entries, P&L, stops for the day
# - Metrics file: Historical performance tracking
# The Position Registry is SHARED (for multi-bot position isolation on same underlying)

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data"
)
MEIC_TF_STATE_FILE = os.path.join(DATA_DIR, "meic_tf_state.json")
MEIC_TF_METRICS_FILE = os.path.join(DATA_DIR, "meic_tf_metrics.json")

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

    Override reasons (Fix #49):
    - "trend": One-sided due to EMA trend filter (BULLISH/BEARISH)
    - "mkt-011": One-sided due to credit gate (non-viable credit)
    - "mkt-010": One-sided due to illiquidity fallback
    - None: Full IC (no override)
    """
    # Track what was actually placed
    call_only: bool = False   # Only call spread was placed (bearish signal)
    put_only: bool = False    # Only put spread was placed (bullish signal)
    trend_signal: Optional[TrendSignal] = None  # The trend signal at entry time

    # Fix #49: Track why entry became one-sided (for correct logging)
    override_reason: Optional[str] = None  # "trend", "mkt-011", "mkt-010", or None

    # Fix #59: Track EMA values at entry time for Trades tab logging
    ema_20_at_entry: Optional[float] = None
    ema_40_at_entry: Optional[float] = None

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

    Extends MEICStrategy with EMA-based trend detection and credit validation:
    - Before each entry, checks 20 EMA vs 40 EMA on SPX 1-minute bars
    - BULLISH: Place PUT spread only (calls are risky in uptrend)
    - BEARISH: Place CALL spread only (puts are risky in downtrend)
    - NEUTRAL: Place full iron condor (standard MEIC behavior)

    Credit Gate (MKT-011):
    - Estimates credit from quotes BEFORE placing orders
    - Skips or converts entry if credit < min_viable_credit_per_side
    - MKT-010 illiquidity check is fallback when quotes unavailable

    All other functionality (stop losses, position management, reconciliation)
    is inherited from MEICStrategy.

    Version: 1.2.7 (2026-02-16)
    """

    # Bot name for Position Registry - overrides MEIC's hardcoded "MEIC"
    # This ensures MEIC-TF positions are isolated in the registry
    BOT_NAME = "MEIC-TF"

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

        # Track current trend signal and EMA values for logging
        self._current_trend: Optional[TrendSignal] = None
        self._last_trend_check: Optional[datetime] = None
        self._last_ema_short: float = 0.0
        self._last_ema_long: float = 0.0
        self._last_ema_diff_pct: float = 0.0

        # CRITICAL: Set state file path BEFORE calling parent init
        # Parent's __init__ calls _recover_positions_from_saxo() which needs the correct state file
        # This prevents conflicts when both MEIC and MEIC-TF run simultaneously
        self.state_file = MEIC_TF_STATE_FILE

        # Call parent init (this sets up everything else including recovery)
        super().__init__(saxo_client, config, logger_service, dry_run, alert_service)

        logger.info(f"MEIC-TF using state file: {self.state_file}")

        # Update alert service name
        if not alert_service:
            self.alert_service = AlertService(config, "MEIC-TF")

        logger.info(f"MEIC-TF Strategy initialized")
        logger.info(f"  Trend filter enabled: {self.trend_enabled}")
        logger.info(f"  EMA periods: {self.ema_short_period}/{self.ema_long_period}")
        logger.info(f"  Neutral threshold: {self.ema_neutral_threshold * 100:.2f}%")
        logger.info(f"  Recheck each entry: {self.recheck_each_entry}")

        # MKT-016: Stop cascade breaker - pause entries after N stops in a day
        strategy_config = config.get("strategy", {})
        self.max_daily_stops_before_pause = strategy_config.get("max_daily_stops_before_pause", 3)
        self._stop_cascade_triggered = False
        logger.info(f"  Stop cascade breaker: pause after {self.max_daily_stops_before_pause} stops")

    # =========================================================================
    # ENTRY GATING (MKT-016)
    # =========================================================================

    def _should_attempt_entry(self, now) -> bool:
        """
        Override parent to add stop cascade breaker (MKT-016).

        After max_daily_stops_before_pause stops in a single day, skip all
        remaining entries. This prevents placing new entries into a market
        that has already stopped multiple existing positions.

        Existing positions continue to be monitored for stops normally.
        """
        # MKT-016: Check if stop cascade breaker has been triggered
        total_stops = self.daily_state.call_stops_triggered + self.daily_state.put_stops_triggered
        if total_stops >= self.max_daily_stops_before_pause and not self._stop_cascade_triggered:
            # First time detecting cascade - log and skip remaining entries
            self._stop_cascade_triggered = True
            remaining = len(self.entry_times) - self._next_entry_index
            logger.warning(
                f"MKT-016 STOP CASCADE BREAKER: {total_stops} stops today "
                f"(threshold: {self.max_daily_stops_before_pause}) - "
                f"pausing {remaining} remaining entries"
            )
            self.daily_state.entries_skipped += remaining
            self._next_entry_index = len(self.entry_times)
            return False

        if self._stop_cascade_triggered:
            return False

        # Delegate to parent for normal time-window checks
        return super()._should_attempt_entry(now)

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

            # Store EMA values for heartbeat logging
            self._last_ema_short = ema_short
            self._last_ema_long = ema_long
            self._last_ema_diff_pct = diff_pct

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
    # MKT-011: Credit Gate for MEIC-TF (one-sided entry support)
    # =========================================================================

    def _check_credit_gate_tf(self, entry: TFIronCondorEntry) -> Tuple[str, bool]:
        """
        MKT-011: Check if estimated credit is above minimum viable threshold.

        Unlike base MEIC which skips entire entry if either side is non-viable,
        MEIC-TF can convert to one-sided entry when only one side is non-viable.

        Args:
            entry: TFIronCondorEntry with strikes calculated

        Returns:
            Tuple of (result, estimation_worked):
            - result: "proceed", "call_only", "put_only", or "skip"
            - estimation_worked: True if we got valid quotes, False if estimation failed
        """
        estimated_call, estimated_put = self._estimate_entry_credit(entry)

        # If we couldn't estimate credit, signal that estimation failed
        # MKT-010 will run as fallback
        if estimated_call == 0.0 and estimated_put == 0.0:
            logger.warning(
                f"MKT-011: Could not estimate credit for Entry #{entry.entry_number} - "
                f"falling back to MKT-010 illiquidity check"
            )
            return ("proceed", False)  # estimation_worked = False

        call_viable = estimated_call >= self.min_viable_credit_per_side
        put_viable = estimated_put >= self.min_viable_credit_per_side

        if call_viable and put_viable:
            logger.info(
                f"MKT-011: Credit gate PASSED for Entry #{entry.entry_number}: "
                f"Call ${estimated_call:.2f}, Put ${estimated_put:.2f} "
                f"(min: ${self.min_viable_credit_per_side:.2f})"
            )
            return ("proceed", True)  # estimation_worked = True

        if not call_viable and not put_viable:
            logger.warning(
                f"MKT-011: SKIPPING Entry #{entry.entry_number} - both sides non-viable. "
                f"Call ${estimated_call:.2f}, Put ${estimated_put:.2f} "
                f"(min: ${self.min_viable_credit_per_side:.2f})"
            )
            self._log_safety_event(
                "MKT-011_ENTRY_SKIPPED",
                f"Entry #{entry.entry_number} - call ${estimated_call:.2f}, put ${estimated_put:.2f}",
                "Skipped"
            )
            return ("skip", True)  # estimation_worked = True

        # One side viable, other not - convert to one-sided entry
        if not call_viable:
            logger.warning(
                f"MKT-011: Entry #{entry.entry_number} call credit non-viable "
                f"(${estimated_call:.2f} < ${self.min_viable_credit_per_side:.2f}) - "
                f"converting to PUT-only (put ${estimated_put:.2f} is viable)"
            )
            self._log_safety_event(
                "MKT-011_CREDIT_OVERRIDE",
                f"Entry #{entry.entry_number} - call ${estimated_call:.2f} non-viable → put-only",
                "Converted to Put-Only"
            )
            return ("put_only", True)  # estimation_worked = True
        else:
            logger.warning(
                f"MKT-011: Entry #{entry.entry_number} put credit non-viable "
                f"(${estimated_put:.2f} < ${self.min_viable_credit_per_side:.2f}) - "
                f"converting to CALL-only (call ${estimated_call:.2f} is viable)"
            )
            self._log_safety_event(
                "MKT-011_CREDIT_OVERRIDE",
                f"Entry #{entry.entry_number} - put ${estimated_put:.2f} non-viable → call-only",
                "Converted to Call-Only"
            )
            return ("call_only", True)  # estimation_worked = True

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
                # Fix #52: Set contract count for multi-contract support
                entry.contracts = self.contracts_per_entry
                self._current_entry = entry

                # Calculate strikes
                if not self._calculate_strikes(entry):
                    last_error = "Failed to calculate strikes"
                    continue

                # MKT-011: Check minimum credit gate before placing orders (primary check)
                credit_gate_handled = False
                original_trend = trend  # Save original trend for hybrid logic
                if not self.dry_run:
                    gate_result, estimation_worked = self._check_credit_gate_tf(entry)

                    if gate_result == "skip":
                        # Both sides non-viable, skip this entry
                        self._entry_in_progress = False
                        self._current_entry = None
                        self.state = MEICState.MONITORING
                        self._next_entry_index += 1
                        return f"Entry #{entry_num} skipped - both sides below minimum viable credit"
                    elif gate_result == "call_only":
                        # Put credit too low - but respect trend filter!
                        if original_trend == TrendSignal.BULLISH:
                            # BULLISH wants puts, but puts non-viable - skip entirely
                            # Don't place calls in a bullish market (contradicts trend filter)
                            logger.warning(
                                f"MKT-011: Entry #{entry_num} put credit non-viable, "
                                f"but trend is BULLISH (can't place calls) - SKIPPING"
                            )
                            self._log_safety_event(
                                "MKT-011_TREND_CONFLICT",
                                f"Entry #{entry_num} - put non-viable + BULLISH trend → skip",
                                "Skipped - Trend Conflict"
                            )
                            # Fix #53/#57: Track skipped entries and credit gate skips
                            self.daily_state.entries_skipped += 1
                            self.daily_state.credit_gate_skips += 1
                            self._entry_in_progress = False
                            self._current_entry = None
                            self.state = MEICState.MONITORING
                            self._next_entry_index += 1
                            return f"Entry #{entry_num} skipped - put non-viable in bullish market"
                        else:
                            # NEUTRAL or BEARISH - OK to convert to call-only
                            logger.info(f"MKT-011: Put credit non-viable → converting to CALL-only (trend: {original_trend.value})")
                            trend = TrendSignal.BEARISH  # Force bearish to get call-only
                            entry.override_reason = "mkt-011"  # Fix #49: Track override reason
                            credit_gate_handled = True
                    elif gate_result == "put_only":
                        # Call credit too low - but respect trend filter!
                        if original_trend == TrendSignal.BEARISH:
                            # BEARISH wants calls, but calls non-viable - skip entirely
                            # Don't place puts in a bearish market (contradicts trend filter)
                            logger.warning(
                                f"MKT-011: Entry #{entry_num} call credit non-viable, "
                                f"but trend is BEARISH (can't place puts) - SKIPPING"
                            )
                            self._log_safety_event(
                                "MKT-011_TREND_CONFLICT",
                                f"Entry #{entry_num} - call non-viable + BEARISH trend → skip",
                                "Skipped - Trend Conflict"
                            )
                            # Fix #53/#57: Track skipped entries and credit gate skips
                            self.daily_state.entries_skipped += 1
                            self.daily_state.credit_gate_skips += 1
                            self._entry_in_progress = False
                            self._current_entry = None
                            self.state = MEICState.MONITORING
                            self._next_entry_index += 1
                            return f"Entry #{entry_num} skipped - call non-viable in bearish market"
                        else:
                            # NEUTRAL or BULLISH - OK to convert to put-only
                            logger.info(f"MKT-011: Call credit non-viable → converting to PUT-only (trend: {original_trend.value})")
                            trend = TrendSignal.BULLISH  # Force bullish to get put-only
                            entry.override_reason = "mkt-011"  # Fix #49: Track override reason
                            credit_gate_handled = True
                    elif estimation_worked:
                        # MKT-011 worked and said proceed - skip MKT-010
                        credit_gate_handled = True
                    # else: estimation failed, fall through to MKT-010

                # MKT-010: Fallback ONLY when MKT-011 couldn't estimate credit
                # When one wing is illiquid, that spread has REDUCED width and POOR credit
                # Trade the OTHER side which has full width and viable credit
                # IMPORTANT: Also respects trend filter (same hybrid logic as MKT-011)
                if not credit_gate_handled and not self.dry_run:
                    logger.info("MKT-010: Running as fallback (credit estimation failed)")
                    if entry.call_wing_illiquid and not entry.put_wing_illiquid:
                        # Call wing illiquid = call spread has reduced width/credit
                        # Put spread is unaffected = has viable credit
                        if original_trend == TrendSignal.BEARISH:
                            # BEARISH wants calls, but calls illiquid - skip entirely
                            logger.warning(
                                f"MKT-010: Entry #{entry_num} call wing illiquid, "
                                f"but trend is BEARISH (can't place puts) - SKIPPING"
                            )
                            self._log_safety_event(
                                "MKT-010_TREND_CONFLICT",
                                f"Entry #{entry_num} - call illiquid + BEARISH trend → skip",
                                "Skipped - Trend Conflict"
                            )
                            # Fix #53/#57: Track skipped entries and credit gate skips
                            self.daily_state.entries_skipped += 1
                            self.daily_state.credit_gate_skips += 1
                            self._entry_in_progress = False
                            self._current_entry = None
                            self.state = MEICState.MONITORING
                            self._next_entry_index += 1
                            return f"Entry #{entry_num} skipped - call illiquid in bearish market"
                        logger.info(
                            f"MKT-010: Call wing illiquid → "
                            f"converting to PUT-only (trend: {original_trend.value})"
                        )
                        trend = TrendSignal.BULLISH  # Force to get put-only
                        entry.override_reason = "mkt-010"  # Fix #49: Track override reason
                    elif entry.put_wing_illiquid and not entry.call_wing_illiquid:
                        # Put wing illiquid = put spread has reduced width/credit
                        # Call spread is unaffected = has viable credit
                        if original_trend == TrendSignal.BULLISH:
                            # BULLISH wants puts, but puts illiquid - skip entirely
                            logger.warning(
                                f"MKT-010: Entry #{entry_num} put wing illiquid, "
                                f"but trend is BULLISH (can't place calls) - SKIPPING"
                            )
                            self._log_safety_event(
                                "MKT-010_TREND_CONFLICT",
                                f"Entry #{entry_num} - put illiquid + BULLISH trend → skip",
                                "Skipped - Trend Conflict"
                            )
                            # Fix #53/#57: Track skipped entries and credit gate skips
                            self.daily_state.entries_skipped += 1
                            self.daily_state.credit_gate_skips += 1
                            self._entry_in_progress = False
                            self._current_entry = None
                            self.state = MEICState.MONITORING
                            self._next_entry_index += 1
                            return f"Entry #{entry_num} skipped - put illiquid in bullish market"
                        logger.info(
                            f"MKT-010: Put wing illiquid → "
                            f"converting to CALL-only (trend: {original_trend.value})"
                        )
                        trend = TrendSignal.BEARISH  # Force to get call-only
                        entry.override_reason = "mkt-010"  # Fix #49: Track override reason
                    elif entry.call_wing_illiquid and entry.put_wing_illiquid:
                        # Both wings illiquid = very unusual, skip entry
                        # Fix #53: Skip immediately, don't retry (this isn't a transient failure)
                        # Fix #57: Track credit gate skip
                        logger.warning(
                            f"MKT-010: Both wings illiquid, skipping entry #{entry.entry_number}"
                        )
                        self._log_safety_event(
                            "MKT-010_BOTH_ILLIQUID",
                            f"Entry #{entry.entry_number} - both wings illiquid → skip",
                            "Skipped - Both Illiquid"
                        )
                        self.daily_state.entries_skipped += 1
                        self.daily_state.credit_gate_skips += 1
                        self._entry_in_progress = False
                        self._current_entry = None
                        self.state = MEICState.MONITORING
                        self._next_entry_index += 1
                        return f"Entry #{entry_num} skipped - both wings illiquid"

                # Execute based on trend signal (may have been overridden by MKT-011 or MKT-010)
                # Fix #49: Log the actual reason for one-sided entries
                if trend == TrendSignal.BULLISH:
                    if entry.override_reason == "mkt-011":
                        logger.info(f"MKT-011 override → placing PUT spread only (actual trend: {original_trend.value})")
                    elif entry.override_reason == "mkt-010":
                        logger.info(f"MKT-010 fallback → placing PUT spread only (actual trend: {original_trend.value})")
                    else:
                        logger.info(f"BULLISH trend → placing PUT spread only")
                        entry.override_reason = "trend"  # Track that trend was the reason
                    entry.put_only = True
                    if self.dry_run:
                        success = self._simulate_one_sided_entry(entry, "put")
                    else:
                        success = self._execute_put_spread_only(entry)

                elif trend == TrendSignal.BEARISH:
                    if entry.override_reason == "mkt-011":
                        logger.info(f"MKT-011 override → placing CALL spread only (actual trend: {original_trend.value})")
                    elif entry.override_reason == "mkt-010":
                        logger.info(f"MKT-010 fallback → placing CALL spread only (actual trend: {original_trend.value})")
                    else:
                        logger.info(f"BEARISH trend → placing CALL spread only")
                        entry.override_reason = "trend"  # Track that trend was the reason
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
                    # Fix #59: Capture EMA values at entry time for Trades tab logging
                    entry.ema_20_at_entry = self._last_ema_short
                    entry.ema_40_at_entry = self._last_ema_long
                    self.daily_state.entries.append(entry)
                    self.daily_state.entries_completed += 1
                    self.daily_state.total_credit_received += entry.total_credit

                    # Fix #55/#56: Track one-sided entries and trend overrides
                    if entry.is_one_sided:
                        self.daily_state.one_sided_entries += 1
                        if entry.override_reason == "trend":
                            # Trend filter (not MKT-011/MKT-010) caused one-sided entry
                            self.daily_state.trend_overrides += 1

                    # Track commission (2 or 4 legs)
                    legs = 2 if entry.is_one_sided else 4
                    entry.open_commission = legs * self.commission_per_leg * self.contracts_per_entry
                    self.daily_state.total_commission += entry.open_commission

                    # Calculate stop losses
                    self._calculate_stop_levels_tf(entry)

                    # Log to Google Sheets
                    self._log_entry(entry)

                    # Send alert with trend/illiquidity info
                    if entry.call_only:
                        if entry.put_wing_illiquid:
                            # MKT-010: Call-only because PUT wing was illiquid (reduced credit)
                            position_summary = f"MEIC-TF Entry #{entry_num} [MKT-010]: Call {entry.short_call_strike}/{entry.long_call_strike} (put illiq→call)"
                        else:
                            position_summary = f"MEIC-TF Entry #{entry_num} [BEARISH]: Call {entry.short_call_strike}/{entry.long_call_strike}"
                    elif entry.put_only:
                        if entry.call_wing_illiquid:
                            # MKT-010: Put-only because CALL wing was illiquid (reduced credit)
                            position_summary = f"MEIC-TF Entry #{entry_num} [MKT-010]: Put {entry.short_put_strike}/{entry.long_put_strike} (call illiq→put)"
                        else:
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

                    # Fix #49: Show correct label in completion message
                    if entry.override_reason == "mkt-011":
                        label = "MKT-011"
                    elif entry.override_reason == "mkt-010":
                        label = "MKT-010"
                    else:
                        label = original_trend.value.upper()

                    result_msg = f"Entry #{entry_num} [{label}] complete - Credit: ${entry.total_credit:.2f}"
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

            # FIX #70 Part A: Verify fill prices against PositionBase.OpenPrice
            self._verify_entry_fill_prices(entry)

            logger.info(f"Call spread complete: Credit ${entry.call_spread_credit:.2f}")

            # FIX #47: Mark put side as "skipped" (not stopped) since it was never opened
            # For BEARISH (call-only) entries, there's no put spread to monitor
            # Skipped is semantically different from stopped (which implies a loss was incurred)
            entry.put_side_skipped = True
            logger.info(f"Entry #{entry.entry_number}: Put side SKIPPED (call-only entry, no loss)")

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

            # FIX #70 Part A: Verify fill prices against PositionBase.OpenPrice
            self._verify_entry_fill_prices(entry)

            logger.info(f"Put spread complete: Credit ${entry.put_spread_credit:.2f}")

            # FIX #47: Mark call side as "skipped" (not stopped) since it was never opened
            # For BULLISH (put-only) entries, there's no call spread to monitor
            # Skipped is semantically different from stopped (which implies a loss was incurred)
            entry.call_side_skipped = True
            logger.info(f"Entry #{entry.entry_number}: Call side SKIPPED (put-only entry, no loss)")

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
            # FIX #47: Mark put side as SKIPPED (not stopped) since it was never opened
            entry.put_side_skipped = True
            logger.info(f"[DRY RUN] Simulated Call Spread: Credit ${entry.call_spread_credit:.2f} (put side skipped)")
        else:
            entry.put_spread_credit = self.spread_width * credit_ratio * 100
            entry.short_put_position_id = f"DRY_{base_id}_SP"
            entry.long_put_position_id = f"DRY_{base_id}_LP"
            # FIX #47: Mark call side as SKIPPED (not stopped) since it was never opened
            entry.call_side_skipped = True
            logger.info(f"[DRY RUN] Simulated Put Spread: Credit ${entry.put_spread_credit:.2f} (call side skipped)")

        return True

    # =========================================================================
    # STOP LOSS CALCULATION FOR ONE-SIDED ENTRIES
    # =========================================================================

    def _calculate_stop_levels_tf(self, entry: TFIronCondorEntry):
        """
        Calculate stop loss levels for trend-following entries.

        FIX #40 (2026-02-06): For one-sided entries, stop = 2× credit received.

        PROBLEM: Previously used stop = credit, but spread_value (cost-to-close)
        approximately equals credit at entry time (due to bid-ask spread, mid prices
        are slightly higher than fill prices). This caused immediate stop triggers
        ~10 seconds after entry when spread_value >= stop_level became true.

        SOLUTION: Use 2× credit as stop level for one-sided entries to match the
        effective behavior of full ICs:
        - Full IC: stop_level = total_credit, each side's value ≈ total_credit/2
          → Each side has ~2× headroom before stop
        - One-sided: stop_level = 2 × single_side_credit
          → Same ~2× headroom as full ICs

        This means: Stop triggers when cost-to-close = 2× credit received,
        which equals P&L = -credit (you've lost what you collected).

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

            # FIX #40: Use 2× credit for one-sided entries to match full IC behavior
            # This prevents immediate false stop triggers from bid-ask spread
            base_stop = credit * 2

            if self.meic_plus_enabled:
                min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
                if credit > min_credit_for_meic_plus:
                    stop_level = base_stop - self.meic_plus_reduction
                else:
                    stop_level = base_stop
            else:
                stop_level = base_stop

            entry.call_side_stop = stop_level
            entry.put_side_stop = 0  # No put side
            logger.info(f"Stop level for call spread: ${stop_level:.2f} (2× credit ${credit:.2f})")

        elif entry.put_only:
            # Only put spread placed
            credit = entry.put_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(f"CRITICAL: Low credit ${credit:.2f}, using minimum stop")
                credit = MIN_STOP_LEVEL

            # FIX #40: Use 2× credit for one-sided entries to match full IC behavior
            # This prevents immediate false stop triggers from bid-ask spread
            base_stop = credit * 2

            if self.meic_plus_enabled:
                min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
                if credit > min_credit_for_meic_plus:
                    stop_level = base_stop - self.meic_plus_reduction
                else:
                    stop_level = base_stop
            else:
                stop_level = base_stop

            entry.put_side_stop = stop_level
            entry.call_side_stop = 0  # No call side
            logger.info(f"Stop level for put spread: ${stop_level:.2f} (2× credit ${credit:.2f})")

        else:
            # Full IC - use parent's logic
            self._calculate_stop_levels(entry)

    # =========================================================================
    # OVERRIDE: P&L sanity validation for one-sided entries (Fix #39)
    # =========================================================================

    def _validate_pnl_sanity(self, entry: IronCondorEntry) -> Tuple[bool, str]:
        """
        DATA-003/DATA-004: Validate P&L values for one-sided entries.

        OVERRIDE (Fix #39, 2026-02-05): The parent class checks both call and put
        sides unconditionally. For one-sided entries (call_only or put_only),
        the non-placed side has zero prices, which triggers DATA-004 warnings
        every ~8 seconds. This override only validates the side that was actually
        placed.

        Args:
            entry: IronCondorEntry (or TFIronCondorEntry) to validate

        Returns:
            Tuple of (is_valid, message)
        """
        if not PNL_SANITY_CHECK_ENABLED:
            return True, "P&L sanity check disabled"

        is_tf_entry = isinstance(entry, TFIronCondorEntry)

        # =====================================================================
        # CALL-ONLY ENTRY: Only validate call side prices
        # =====================================================================
        if is_tf_entry and entry.call_only:
            # Only check call side - put side was never placed
            if not entry.call_side_stopped:
                if entry.short_call_price == 0 and entry.long_call_price == 0:
                    logger.warning(
                        f"DATA-004: Entry #{entry.entry_number} call side has zero prices "
                        f"(SC=${entry.short_call_price:.2f}, LC=${entry.long_call_price:.2f}) - skipping stop check"
                    )
                    return False, "Call side prices are zero"
                if entry.short_call_price == 0 or entry.long_call_price == 0:
                    logger.warning(
                        f"DATA-004: Entry #{entry.entry_number} call side has partial zero prices "
                        f"(SC=${entry.short_call_price:.2f}, LC=${entry.long_call_price:.2f}) - skipping stop check"
                    )
                    return False, "Call side has partial zero price"

            # Check P&L bounds for call-only entry
            pnl = entry.unrealized_pnl
            if pnl > MAX_PNL_PER_IC:
                logger.error(
                    f"DATA-003: Impossible P&L for call-only Entry #{entry.entry_number}: "
                    f"${pnl:.2f} > max ${MAX_PNL_PER_IC}"
                )
                return False, f"P&L too high: ${pnl:.2f}"
            if pnl < MIN_PNL_PER_IC:
                logger.error(
                    f"DATA-003: Impossible P&L for call-only Entry #{entry.entry_number}: "
                    f"${pnl:.2f} < min ${MIN_PNL_PER_IC}"
                )
                return False, f"P&L too low: ${pnl:.2f}"

            return True, "OK"

        # =====================================================================
        # PUT-ONLY ENTRY: Only validate put side prices
        # =====================================================================
        elif is_tf_entry and entry.put_only:
            # Only check put side - call side was never placed
            if not entry.put_side_stopped:
                if entry.short_put_price == 0 and entry.long_put_price == 0:
                    logger.warning(
                        f"DATA-004: Entry #{entry.entry_number} put side has zero prices "
                        f"(SP=${entry.short_put_price:.2f}, LP=${entry.long_put_price:.2f}) - skipping stop check"
                    )
                    return False, "Put side prices are zero"
                if entry.short_put_price == 0 or entry.long_put_price == 0:
                    logger.warning(
                        f"DATA-004: Entry #{entry.entry_number} put side has partial zero prices "
                        f"(SP=${entry.short_put_price:.2f}, LP=${entry.long_put_price:.2f}) - skipping stop check"
                    )
                    return False, "Put side has partial zero price"

            # Check P&L bounds for put-only entry
            pnl = entry.unrealized_pnl
            if pnl > MAX_PNL_PER_IC:
                logger.error(
                    f"DATA-003: Impossible P&L for put-only Entry #{entry.entry_number}: "
                    f"${pnl:.2f} > max ${MAX_PNL_PER_IC}"
                )
                return False, f"P&L too high: ${pnl:.2f}"
            if pnl < MIN_PNL_PER_IC:
                logger.error(
                    f"DATA-003: Impossible P&L for put-only Entry #{entry.entry_number}: "
                    f"${pnl:.2f} < min ${MIN_PNL_PER_IC}"
                )
                return False, f"P&L too low: ${pnl:.2f}"

            return True, "OK"

        # =====================================================================
        # FULL IC: Use parent's validation for both sides
        # =====================================================================
        else:
            return super()._validate_pnl_sanity(entry)

    # =========================================================================
    # OVERRIDE: Price updates for one-sided entries (Fix #41, 2026-02-05)
    # =========================================================================

    def _update_entry_prices(self, entry: IronCondorEntry):
        """
        Update option prices for an entry.

        OVERRIDE (Fix #41, 2026-02-05): Parent class fetches prices for ALL 4 legs
        unconditionally. For MEIC-TF one-sided entries, this causes DATA-004 warnings
        because it tries to fetch prices for legs that don't exist (UIC=0).

        This override only fetches prices for the legs that were actually placed:
        - call_only entries: Only fetch call side prices
        - put_only entries: Only fetch put side prices
        - Full IC entries: Fetch all 4 legs (via parent method)

        Args:
            entry: The entry to update prices for
        """
        # Check if this is a TFIronCondorEntry with one-sided flags
        is_tf_entry = isinstance(entry, TFIronCondorEntry)

        if is_tf_entry and entry.call_only:
            # CALL-ONLY ENTRY: Only fetch call side prices
            if self.dry_run:
                self._simulate_tf_entry_prices(entry)
                return

            # Short Call
            if entry.short_call_uic:
                quote = self.client.get_quote(entry.short_call_uic, asset_type="StockIndexOption")
                entry.short_call_price = self._extract_mid_price(quote) or 0

            # Long Call
            if entry.long_call_uic:
                quote = self.client.get_quote(entry.long_call_uic, asset_type="StockIndexOption")
                entry.long_call_price = self._extract_mid_price(quote) or 0

            # Put side prices stay at 0 - they were never placed
            # No DATA-004 warnings because we don't try to fetch non-existent legs

        elif is_tf_entry and entry.put_only:
            # PUT-ONLY ENTRY: Only fetch put side prices
            if self.dry_run:
                self._simulate_tf_entry_prices(entry)
                return

            # Short Put
            if entry.short_put_uic:
                quote = self.client.get_quote(entry.short_put_uic, asset_type="StockIndexOption")
                entry.short_put_price = self._extract_mid_price(quote) or 0

            # Long Put
            if entry.long_put_uic:
                quote = self.client.get_quote(entry.long_put_uic, asset_type="StockIndexOption")
                entry.long_put_price = self._extract_mid_price(quote) or 0

            # Call side prices stay at 0 - they were never placed

        else:
            # FULL IC: Use parent's method to fetch all 4 legs
            super()._update_entry_prices(entry)

    def _simulate_tf_entry_prices(self, entry: IronCondorEntry):
        """
        Simulate option prices for TF entries in dry-run mode.

        Similar to parent's _simulate_entry_prices but handles one-sided entries.

        Args:
            entry: The entry to simulate prices for
        """
        if not entry.entry_time:
            return

        # Calculate time decay factor (theta)
        hold_minutes = (get_us_market_time() - entry.entry_time).total_seconds() / 60
        decay_factor = 1 - (hold_minutes / 360)  # Assume ~6 hours to expiry
        decay_factor = max(0.1, decay_factor)  # Floor at 10%

        is_tf_entry = isinstance(entry, TFIronCondorEntry)

        if is_tf_entry and entry.call_only:
            # Only simulate call side
            initial_short_price = entry.call_spread_credit / 100  # Per contract
            entry.short_call_price = initial_short_price * decay_factor
            entry.long_call_price = initial_short_price * decay_factor * 0.3

        elif is_tf_entry and entry.put_only:
            # Only simulate put side
            initial_short_price = entry.put_spread_credit / 100  # Per contract
            entry.short_put_price = initial_short_price * decay_factor
            entry.long_put_price = initial_short_price * decay_factor * 0.3

        else:
            # Full IC - use parent's simulation
            # But call our parent's method for consistency
            initial_short_price = entry.total_credit / 200  # Per contract
            entry.short_call_price = initial_short_price * decay_factor
            entry.short_put_price = initial_short_price * decay_factor
            entry.long_call_price = initial_short_price * decay_factor * 0.3
            entry.long_put_price = initial_short_price * decay_factor * 0.3

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
                # FIX #47: Check stopped/expired/skipped for completeness
                call_done = entry.call_side_stopped or entry.call_side_expired or entry.call_side_skipped
                put_done = entry.put_side_stopped or entry.put_side_expired or entry.put_side_skipped
                if call_done and put_done:
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

                if not call_done:
                    if entry.call_spread_value >= entry.call_side_stop:
                        return self._execute_stop_loss(entry, "call")

                if not put_done:
                    if entry.put_spread_value >= entry.put_side_stop:
                        return self._execute_stop_loss(entry, "put")

        return None

    # =========================================================================
    # OVERRIDE: Status summary with trend info
    # =========================================================================

    def get_status_summary(self) -> Dict:
        """
        Get current strategy status summary with trend info.

        Automatically refreshes trend detection if not checked in last 60 seconds
        to ensure heartbeat displays accurate trend data.

        Returns:
            Dict with status information including current trend and EMA values
        """
        # Refresh trend if not checked recently (every 60 seconds)
        # This ensures heartbeat shows current trend without excessive API calls
        now = get_us_market_time()
        if self._last_trend_check is None or (now - self._last_trend_check).total_seconds() >= 60:
            try:
                self._get_trend_signal()
            except Exception as e:
                logger.warning(f"Failed to refresh trend for status: {e}")

        status = super().get_status_summary()

        # Add trend info
        status['current_trend'] = self._current_trend.value if self._current_trend else "unknown"
        status['trend_enabled'] = self.trend_enabled

        # Add EMA values for detailed logging
        status['ema_short'] = self._last_ema_short
        status['ema_long'] = self._last_ema_long
        status['ema_diff_pct'] = self._last_ema_diff_pct

        return status

    def get_detailed_position_status(self) -> List[str]:
        """
        Get detailed position status lines with trend info.

        Returns:
            List of status lines for logging with EMA values
        """
        lines = super().get_detailed_position_status()

        # Add trend line at the start with actual EMA values
        if self._current_trend:
            if self._last_ema_short > 0 and self._last_ema_long > 0:
                diff_sign = "+" if self._last_ema_diff_pct >= 0 else ""
                trend_line = (
                    f"  Trend: {self._current_trend.value.upper()} | "
                    f"EMA{self.ema_short_period}: {self._last_ema_short:.2f} | "
                    f"EMA{self.ema_long_period}: {self._last_ema_long:.2f} | "
                    f"Diff: {diff_sign}{self._last_ema_diff_pct*100:.3f}%"
                )
            else:
                trend_line = f"  Trend: {self._current_trend.value.upper()} (EMA {self.ema_short_period}/{self.ema_long_period})"
            lines.insert(0, trend_line)

        return lines

    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """
        Get dashboard metrics with MEIC-TF specific fields.

        Adds to base MEIC metrics:
        - Current trend signal (BULLISH/BEARISH/NEUTRAL)
        - EMA values
        - One-sided entry counts
        - Trend override statistics

        Returns:
            Dict with all dashboard metrics including trend data
        """
        # Fix #62: Ensure trend detection runs to populate EMA values
        # On startup, _last_ema_short/long are initialized to 0.0, not None
        if self._last_ema_short <= 0 or self._last_ema_long <= 0:
            try:
                self._get_trend_signal()
            except Exception as e:
                logger.warning(f"Could not detect trend for dashboard: {e}")

        # Get base MEIC metrics
        metrics = super().get_dashboard_metrics()

        # Add trend data
        metrics['current_trend'] = self._current_trend.value if self._current_trend else "NEUTRAL"
        metrics['ema_20'] = self._last_ema_short
        metrics['ema_40'] = self._last_ema_long
        metrics['ema_diff_pct'] = self._last_ema_diff_pct

        # Fix #65: Count signals using actual trend_signal, not entry type
        # Entry type (call_only/put_only) can differ from trend signal when MKT-011
        # credit gate converts a NEUTRAL entry to one-sided
        full_ics = 0
        one_sided = 0
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0

        for entry in self.daily_state.entries:
            # Count entry type (full IC vs one-sided)
            if isinstance(entry, TFIronCondorEntry) and entry.is_one_sided:
                one_sided += 1
            else:
                full_ics += 1

            # Count trend signals from actual trend_signal field
            if isinstance(entry, TFIronCondorEntry) and entry.trend_signal:
                if entry.trend_signal == TrendSignal.BULLISH:
                    bullish_count += 1
                elif entry.trend_signal == TrendSignal.BEARISH:
                    bearish_count += 1
                else:
                    neutral_count += 1
            else:
                neutral_count += 1

        metrics['full_ics'] = full_ics
        metrics['one_sided_entries'] = one_sided
        metrics['bullish_signals'] = bullish_count
        metrics['bearish_signals'] = bearish_count
        metrics['neutral_signals'] = neutral_count

        # Track MKT-010/MKT-011 overrides (trend overrides and credit gate skips)
        # Fix #55/#56/#57: Now tracked directly on daily_state
        metrics['trend_overrides'] = self.daily_state.trend_overrides
        metrics['credit_gate_skips'] = self.daily_state.credit_gate_skips

        return metrics

    def get_daily_summary(self) -> Dict:
        """
        Get daily summary with MEIC-TF specific fields.

        Adds to base MEIC summary:
        - Full IC vs one-sided entry counts
        - Trend signal distribution

        Returns:
            Dict with daily summary data including trend statistics
        """
        # Get base MEIC summary
        summary = super().get_daily_summary()

        # Fix #65: Count signals using actual trend_signal, not entry type
        full_ics = 0
        one_sided = 0
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0

        for entry in self.daily_state.entries:
            # Count entry type (full IC vs one-sided)
            if isinstance(entry, TFIronCondorEntry) and entry.is_one_sided:
                one_sided += 1
            else:
                full_ics += 1

            # Count trend signals from actual trend_signal field
            if isinstance(entry, TFIronCondorEntry) and entry.trend_signal:
                if entry.trend_signal == TrendSignal.BULLISH:
                    bullish_count += 1
                elif entry.trend_signal == TrendSignal.BEARISH:
                    bearish_count += 1
                else:
                    neutral_count += 1
            else:
                neutral_count += 1

        summary['full_ics'] = full_ics
        summary['one_sided_entries'] = one_sided
        summary['bullish_signals'] = bullish_count
        summary['bearish_signals'] = bearish_count
        summary['neutral_signals'] = neutral_count

        return summary

    # =========================================================================
    # OVERRIDE: Logging for trend-following entries
    # =========================================================================

    def log_account_summary(self):
        """
        Log MEIC-TF account summary to Google Sheets dashboard.

        Overrides parent to include EMA values in the Account Summary tab.
        Fix #62: EMA 20/40 values were showing as N/A because parent's
        log_account_summary() didn't pass them to the logger.
        """
        try:
            metrics = self.get_dashboard_metrics()
            self.trade_logger.log_account_summary({
                # Market data
                "spx_price": metrics["spx_price"],
                "vix": metrics["vix"],
                # Entry status
                "entries_completed": metrics["entries_completed"],
                "active_ics": metrics["active_entries"],
                "entries_skipped": metrics["entries_skipped"],
                # P&L
                "total_credit": metrics["total_credit"],
                "unrealized_pnl": metrics["unrealized_pnl"],
                "realized_pnl": metrics["realized_pnl"],
                # Stops
                "call_stops": metrics["call_stops"],
                "put_stops": metrics["put_stops"],
                # MEIC-TF specific: Trend data (Fix #62)
                "current_trend": metrics.get("current_trend", "NEUTRAL"),
                "ema_20": metrics.get("ema_20"),
                "ema_40": metrics.get("ema_40"),
                # Risk
                "daily_loss_percent": metrics["pnl_percent"],
                "circuit_breaker": metrics["circuit_breaker_open"],
                # State
                "state": metrics["state"]
            })
        except Exception as e:
            logger.error(f"Failed to log MEIC-TF account summary: {e}")

    def log_performance_metrics(self):
        """
        Log MEIC-TF performance metrics to Google Sheets.

        Overrides parent to include TF-specific fields:
        - full_ics / one_sided_entries counts
        - trend_overrides / credit_gate_skips counts

        Fix #69: Parent's log_performance_metrics() builds a NEW dict that
        doesn't include TF-specific keys from get_dashboard_metrics().
        The logger reads metrics.get("full_ics", 0) which defaults to 0.
        """
        try:
            metrics = self.get_dashboard_metrics()
            cumulative = self.cumulative_metrics or {}

            # Calculate win/breakeven/loss rates
            completed = metrics["entries_completed"]
            if completed > 0:
                win_rate = (metrics["entries_with_no_stops"] / completed) * 100
                breakeven_rate = (metrics["entries_with_one_stop"] / completed) * 100
                loss_rate = (metrics["entries_with_both_stops"] / completed) * 100
            else:
                win_rate = breakeven_rate = loss_rate = 0

            # Risk & return metrics
            capital_deployed = self._calculate_capital_deployed()
            net_pnl = metrics["total_pnl"] - self.daily_state.total_commission

            self.trade_logger.log_performance_metrics(
                period="Intraday",
                metrics={
                    # P&L
                    "total_pnl": metrics["total_pnl"],
                    "realized_pnl": metrics["realized_pnl"],
                    "unrealized_pnl": metrics["unrealized_pnl"],
                    "pnl_percent": metrics["pnl_percent"],
                    # Credit tracking
                    "total_credit": metrics["total_credit"],
                    "avg_credit_per_ic": metrics["total_credit"] / completed if completed > 0 else 0,
                    # Entry stats
                    "total_entries": metrics["entries_scheduled"],
                    "entries_completed": metrics["entries_completed"],
                    "entries_skipped": metrics["entries_skipped"],
                    # MEIC-TF specific: entry type counts
                    "full_ics": metrics.get("full_ics", 0),
                    "one_sided_entries": metrics.get("one_sided_entries", 0),
                    # Stop stats
                    "call_stops": metrics["call_stops"],
                    "put_stops": metrics["put_stops"],
                    "double_stops": metrics["double_stops"],
                    # Outcome rates
                    "win_rate": win_rate,
                    "breakeven_rate": breakeven_rate,
                    "loss_rate": loss_rate,
                    # MEIC-TF specific: trend stats
                    "trend_overrides": metrics.get("trend_overrides", 0),
                    "credit_gate_skips": metrics.get("credit_gate_skips", 0),
                    # Risk
                    "max_drawdown": cumulative.get("max_drawdown", 0),
                    "max_drawdown_pct": cumulative.get("max_drawdown_pct", 0),
                    "avg_daily_pnl": cumulative.get("avg_daily_pnl", 0),
                    # Risk & return metrics
                    "max_loss_stops": self._calculate_max_loss_with_stops(),
                    "max_loss_catastrophic": self._calculate_max_loss_catastrophic(),
                    "capital_deployed": capital_deployed,
                    "return_on_capital": (net_pnl / capital_deployed * 100) if capital_deployed > 0 else 0,
                },
                saxo_client=self.client
            )
        except Exception as e:
            logger.error(f"Failed to log MEIC-TF performance metrics: {e}")

    def log_position_snapshot(self):
        """
        Log current position snapshot to the Positions tab in Google Sheets.

        Fix #69: Positions tab was created with correct headers but never populated.
        Writes one row per SIDE (call/put) for each entry, showing:
        - Entry credit, current spread value, P&L
        - Stop level, distance to stop, whether triggered
        - Trend signal and status (ACTIVE/STOPPED/EXPIRED/SKIPPED)
        """
        try:
            today = get_us_market_time().strftime("%Y-%m-%d")
            positions = []

            # Get EUR exchange rate (once per snapshot, not per position)
            eur_rate = 0
            if self.trade_logger.currency_enabled:
                try:
                    eur_rate = self.client.get_fx_rate(
                        self.trade_logger.base_currency,
                        self.trade_logger.account_currency
                    ) or 0
                except Exception:
                    eur_rate = 0

            for entry in self.daily_state.entries:
                is_tf = isinstance(entry, TFIronCondorEntry)
                trend_signal = entry.trend_signal.value.upper() if is_tf and entry.trend_signal else "NEUTRAL"

                # Call side
                call_skipped = getattr(entry, 'call_side_skipped', False)
                if not call_skipped:
                    if entry.call_side_stopped:
                        status = "STOPPED"
                        current_value = entry.call_side_stop
                        pnl = -(entry.call_side_stop - entry.call_spread_credit)
                    elif getattr(entry, 'call_side_expired', False):
                        status = "EXPIRED"
                        current_value = 0
                        pnl = entry.call_spread_credit
                    else:
                        status = "ACTIVE"
                        current_value = entry.call_spread_value if entry.call_spread_value else 0
                        pnl = entry.call_spread_credit - current_value

                    stop_level = entry.call_side_stop if entry.call_side_stop else 0
                    distance = stop_level - current_value if status == "ACTIVE" and stop_level > 0 else 0
                    spread_width = abs(entry.long_call_strike - entry.short_call_strike) if entry.long_call_strike else 0

                    positions.append({
                        "entry_number": entry.entry_number,
                        "leg_type": "Call Spread",
                        "strike": entry.short_call_strike,
                        "expiry": today,
                        "entry_credit": entry.call_spread_credit,
                        "current_value": current_value,
                        "pnl": pnl,
                        "pnl_eur": pnl * eur_rate,
                        "stop_level": stop_level,
                        "distance_to_stop": distance,
                        "stop_triggered": "Yes" if entry.call_side_stopped else "No",
                        "side": "Call",
                        "spread_width": spread_width,
                        "position_id": entry.short_call_position_id or "",
                        "trend_signal": trend_signal,
                        "status": status
                    })

                # Put side
                put_skipped = getattr(entry, 'put_side_skipped', False)
                if not put_skipped:
                    if entry.put_side_stopped:
                        status = "STOPPED"
                        current_value = entry.put_side_stop
                        pnl = -(entry.put_side_stop - entry.put_spread_credit)
                    elif getattr(entry, 'put_side_expired', False):
                        status = "EXPIRED"
                        current_value = 0
                        pnl = entry.put_spread_credit
                    else:
                        status = "ACTIVE"
                        current_value = entry.put_spread_value if entry.put_spread_value else 0
                        pnl = entry.put_spread_credit - current_value

                    stop_level = entry.put_side_stop if entry.put_side_stop else 0
                    distance = stop_level - current_value if status == "ACTIVE" and stop_level > 0 else 0
                    spread_width = abs(entry.short_put_strike - entry.long_put_strike) if entry.long_put_strike else 0

                    positions.append({
                        "entry_number": entry.entry_number,
                        "leg_type": "Put Spread",
                        "strike": entry.short_put_strike,
                        "expiry": today,
                        "entry_credit": entry.put_spread_credit,
                        "current_value": current_value,
                        "pnl": pnl,
                        "pnl_eur": pnl * eur_rate,
                        "stop_level": stop_level,
                        "distance_to_stop": distance,
                        "stop_triggered": "Yes" if entry.put_side_stopped else "No",
                        "side": "Put",
                        "spread_width": spread_width,
                        "position_id": entry.short_put_position_id or "",
                        "trend_signal": trend_signal,
                        "status": status
                    })

            self.trade_logger.log_position_snapshot(positions)
        except Exception as e:
            logger.error(f"Failed to log MEIC-TF position snapshot: {e}")

    def _log_entry(self, entry):
        """
        Log entry to Google Sheets with trend info.

        Overrides parent to:
        - Use "MEIC-TF" instead of "MEIC"
        - Include trend signal in the action
        - Handle one-sided entries (show only placed side's strikes)

        Fix #49: Use override_reason to determine correct tag:
        - MKT-011: Credit gate triggered override
        - MKT-010: Illiquidity fallback triggered override
        - Trend: EMA trend filter determined one-sided
        """
        try:
            # Determine entry type and format strikes accordingly
            is_tf_entry = isinstance(entry, TFIronCondorEntry)

            if is_tf_entry and entry.call_only:
                # Call spread only
                strike_str = f"C:{entry.short_call_strike}/{entry.long_call_strike}"
                entry_type = "Call Spread"
                # Fix #49: Use override_reason for correct tag
                override_reason = getattr(entry, 'override_reason', None)
                if override_reason == "mkt-011":
                    trend_tag = "[MKT-011]"
                elif override_reason == "mkt-010":
                    trend_tag = "[MKT-010]"
                else:
                    trend_tag = "[BEARISH]"
            elif is_tf_entry and entry.put_only:
                # Put spread only
                strike_str = f"P:{entry.short_put_strike}/{entry.long_put_strike}"
                entry_type = "Put Spread"
                # Fix #49: Use override_reason for correct tag
                override_reason = getattr(entry, 'override_reason', None)
                if override_reason == "mkt-011":
                    trend_tag = "[MKT-011]"
                elif override_reason == "mkt-010":
                    trend_tag = "[MKT-010]"
                else:
                    trend_tag = "[BULLISH]"
            else:
                # Full IC (neutral)
                strike_str = (
                    f"C:{entry.short_call_strike}/{entry.long_call_strike} "
                    f"P:{entry.short_put_strike}/{entry.long_put_strike}"
                )
                entry_type = "Iron Condor"
                trend_tag = "[NEUTRAL]"

            # Fix #54: Add expiry_date and dte for 0DTE options
            # Fix #59: Add EMA values to trade_reason instead of Account Summary
            today_str = get_us_market_time().strftime("%Y-%m-%d")
            ema_20 = getattr(entry, 'ema_20_at_entry', None)
            ema_40 = getattr(entry, 'ema_40_at_entry', None)
            ema_info = ""
            if ema_20 is not None and ema_40 is not None:
                ema_info = f" | EMA20: {ema_20:.2f}, EMA40: {ema_40:.2f}"

            self.trade_logger.log_trade(
                action=f"MEIC-TF Entry #{entry.entry_number} {trend_tag}",
                strike=strike_str,
                price=entry.total_credit,
                delta=0.0,
                pnl=0.0,
                saxo_client=self.client,  # Fix #63: Enable EUR conversion
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type=entry_type,
                expiry_date=today_str,  # Fix #54: 0DTE expiry is today
                dte=0,  # Fix #54: 0DTE
                premium_received=entry.total_credit,
                trade_reason=f"Entry | Trend: {trend_tag} | Credit: ${entry.total_credit:.2f}{ema_info}"
            )

            logger.info(
                f"Entry #{entry.entry_number} {trend_tag} logged to Sheets: "
                f"SPX={self.current_price:.2f}, Credit=${entry.total_credit:.2f}, "
                f"Type={entry_type}, Strikes: {strike_str}"
            )
        except Exception as e:
            logger.error(f"Failed to log entry: {e}")

    # =========================================================================
    # STATE FILE OVERRIDES (MEIC-TF uses separate state file from MEIC)
    # =========================================================================
    # CRITICAL: These overrides ensure MEIC-TF uses its own state file
    # (meic_tf_state.json) instead of sharing with MEIC (meic_state.json).
    # This is necessary when both bots may run simultaneously.

    def _save_state_to_disk(self):
        """
        Save current daily state to disk for crash recovery.

        OVERRIDE: Uses MEIC_TF_STATE_FILE instead of MEIC's STATE_FILE.
        Also saves trend-following specific fields (call_only, put_only, trend_signal).
        """
        try:
            state_data = {
                "bot_type": "meic_tf",  # Identify this as MEIC-TF state
                "date": self.daily_state.date,
                "state": self.state.value,
                "next_entry_index": self._next_entry_index,
                "entries_completed": self.daily_state.entries_completed,
                "entries_failed": self.daily_state.entries_failed,
                "entries_skipped": self.daily_state.entries_skipped,
                "total_credit_received": self.daily_state.total_credit_received,
                "total_realized_pnl": self.daily_state.total_realized_pnl,
                "total_commission": self.daily_state.total_commission,
                "call_stops_triggered": self.daily_state.call_stops_triggered,
                "put_stops_triggered": self.daily_state.put_stops_triggered,
                "double_stops": self.daily_state.double_stops,
                "circuit_breaker_opens": self.daily_state.circuit_breaker_opens,
                # Fix #55/#56/#57: MEIC-TF specific counters
                "one_sided_entries": self.daily_state.one_sided_entries,
                "trend_overrides": self.daily_state.trend_overrides,
                "credit_gate_skips": self.daily_state.credit_gate_skips,
                "entries": []
            }

            # Serialize each entry with TF-specific fields
            for entry in self.daily_state.entries:
                is_tf_entry = isinstance(entry, TFIronCondorEntry)
                entry_data = {
                    "entry_number": entry.entry_number,
                    "entry_time": entry.entry_time.isoformat() if hasattr(entry.entry_time, 'isoformat') else entry.entry_time,
                    "strategy_id": entry.strategy_id,
                    # Strikes
                    "short_call_strike": entry.short_call_strike,
                    "long_call_strike": entry.long_call_strike,
                    "short_put_strike": entry.short_put_strike,
                    "long_put_strike": entry.long_put_strike,
                    # Position IDs
                    "short_call_position_id": entry.short_call_position_id,
                    "long_call_position_id": entry.long_call_position_id,
                    "short_put_position_id": entry.short_put_position_id,
                    "long_put_position_id": entry.long_put_position_id,
                    # UICs
                    "short_call_uic": entry.short_call_uic,
                    "long_call_uic": entry.long_call_uic,
                    "short_put_uic": entry.short_put_uic,
                    "long_put_uic": entry.long_put_uic,
                    # Credits
                    "call_spread_credit": entry.call_spread_credit,
                    "put_spread_credit": entry.put_spread_credit,
                    # Stops
                    "call_side_stop": entry.call_side_stop,
                    "put_side_stop": entry.put_side_stop,
                    # Status
                    "is_complete": entry.is_complete,
                    "call_side_stopped": entry.call_side_stopped,
                    "put_side_stopped": entry.put_side_stopped,
                    "call_side_expired": entry.call_side_expired,
                    "put_side_expired": entry.put_side_expired,
                    "call_side_skipped": entry.call_side_skipped,
                    "put_side_skipped": entry.put_side_skipped,
                    # Fix #61: Position merge tracking
                    "call_side_merged": entry.call_side_merged,
                    "put_side_merged": entry.put_side_merged,
                    # Commission tracking
                    "open_commission": entry.open_commission,
                    "close_commission": entry.close_commission,
                    # MEIC-TF specific: trend-following fields
                    # FIX #43: Use getattr to handle both TFIronCondorEntry and
                    # dynamically-added attributes on IronCondorEntry (from recovery)
                    "call_only": getattr(entry, 'call_only', False),
                    "put_only": getattr(entry, 'put_only', False),
                    "trend_signal": getattr(entry, 'trend_signal', None).value if getattr(entry, 'trend_signal', None) else None,
                    # Fix #49: Track override reason for correct logging after recovery
                    "override_reason": getattr(entry, 'override_reason', None),
                    # Fix #52: Contract count for multi-contract support
                    "contracts": entry.contracts,
                    # Fix #59: EMA values at entry time for Trades tab logging
                    "ema_20_at_entry": getattr(entry, 'ema_20_at_entry', None),
                    "ema_40_at_entry": getattr(entry, 'ema_40_at_entry', None),
                }
                state_data["entries"].append(entry_data)

            # Persist intraday OHLC so it survives mid-day restarts
            state_data["market_data_ohlc"] = {
                "spx_open": self.market_data.spx_open,
                "spx_high": self.market_data.spx_high,
                "spx_low": self.market_data.spx_low if self.market_data.spx_low != float('inf') else 0.0,
                "vix_open": self.market_data.vix_open,
                "vix_high": self.market_data.vix_high,
                "vix_low": self.market_data.vix_low if self.market_data.vix_low != float('inf') else 0.0,
            }

            state_data["last_saved"] = get_us_market_time().isoformat()

            # Write atomically using temp file (uses self.state_file set in __init__)
            temp_file = self.state_file + ".tmp"
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)

            with open(temp_file, 'w') as f:
                json.dump(state_data, f, indent=2)

            os.replace(temp_file, self.state_file)
            logger.debug(f"MEIC-TF state saved to {self.state_file}")

        except Exception as e:
            logger.error(f"Failed to save MEIC-TF state: {e}")

    def _register_position(self, entry: IronCondorEntry, leg_name: str):
        """
        Register a position leg with the Position Registry using MEIC-TF bot name.

        Override from MEIC to use "MEIC-TF" instead of "MEIC" for proper isolation
        when both bots run simultaneously.

        Args:
            entry: IronCondorEntry containing the position
            leg_name: Which leg ("short_call", "long_call", "short_put", "long_put")
        """
        position_id = getattr(entry, f"{leg_name}_position_id")
        # CRITICAL: Reject both None and the string "None"
        if not position_id or position_id == "None":
            if position_id == "None":
                logger.error(f"BUG DETECTED: {leg_name} has string 'None' as position_id - not registering")
            return

        strike = getattr(entry, f"{leg_name}_strike")

        try:
            self.registry.register(
                position_id=position_id,
                bot_name="MEIC-TF",  # Use MEIC-TF instead of MEIC for isolation
                strategy_id=entry.strategy_id,
                metadata={
                    "entry_number": entry.entry_number,
                    "leg_type": leg_name,
                    "strike": strike
                }
            )
        except Exception as e:
            logger.error(f"Registry error registering {leg_name} position {position_id}: {e}")

    def _check_state_consistency(self) -> Optional[str]:
        """
        STATE-002: Validate that strategy state matches actual positions.

        OVERRIDE: Uses BOT_NAME ("MEIC-TF") instead of hardcoded "MEIC" in parent class.

        Returns:
            Error message if inconsistent, None if OK
        """
        from bots.meic.strategy import MEICState

        active_entries = len(self.daily_state.active_entries)
        my_positions = self.registry.get_positions(self.BOT_NAME)  # Use MEIC-TF, not MEIC

        # Check state vs position count
        if self.state == MEICState.MONITORING and active_entries == 0:
            return "State is MONITORING but no active entries"

        if self.state == MEICState.IDLE and active_entries > 0:
            return f"State is IDLE but have {active_entries} active entries"

        if self.state == MEICState.WAITING_FIRST_ENTRY and active_entries > 0:
            # This is OK - waiting for next entry while monitoring existing
            pass

        # Cross-check with Position Registry
        expected_positions = sum(len(e.all_position_ids) for e in self.daily_state.active_entries)
        registry_count = len(my_positions)

        if abs(expected_positions - registry_count) > 2:  # Allow small discrepancy
            return f"Position count mismatch: expected {expected_positions}, registry has {registry_count}"

        return None

    def _check_hourly_reconciliation(self):
        """
        POS-003: Perform hourly position reconciliation during market hours.

        OVERRIDE: Uses BOT_NAME ("MEIC-TF") instead of hardcoded "MEIC" in parent class.

        Compares expected positions vs actual Saxo positions to detect:
        - Early assignment
        - Manual intervention
        - Orphaned positions
        """
        from bots.meic.strategy import is_market_open, RECONCILIATION_INTERVAL_MINUTES

        if not is_market_open():
            return

        now = get_us_market_time()

        # Check if it's time for reconciliation
        if self._last_reconciliation_time:
            elapsed_minutes = (now - self._last_reconciliation_time).total_seconds() / 60
            if elapsed_minutes < RECONCILIATION_INTERVAL_MINUTES:
                return

        logger.info("POS-003: Performing hourly position reconciliation")
        self._last_reconciliation_time = now

        try:
            # Get actual positions from Saxo
            actual_positions = self.client.get_positions()
            actual_position_ids = {str(p.get("PositionId")) for p in actual_positions}

            # Get expected positions from our tracking
            expected_position_ids = set()
            for entry in self.daily_state.active_entries:
                expected_position_ids.update(entry.all_position_ids)

            # Check for missing positions (closed manually or assigned)
            missing = expected_position_ids - actual_position_ids
            if missing:
                logger.warning(f"POS-003: {len(missing)} expected positions NOT FOUND in Saxo!")
                logger.warning(f"  Missing IDs: {missing}")

                # This is serious - positions may have been manually closed
                self.alert_service.send_alert(
                    alert_type=AlertType.CRITICAL_INTERVENTION,
                    title="Position Mismatch Detected",
                    message=f"{len(missing)} {self.BOT_NAME} positions missing from Saxo. Manual intervention suspected.",
                    priority=AlertPriority.HIGH,
                    details={"missing_ids": list(missing)}
                )

                # Clean up registry and daily state
                self._handle_missing_positions(missing)

            # Check for unexpected positions (assigned, etc.)
            my_registry_positions = self.registry.get_positions(self.BOT_NAME)  # Use MEIC-TF, not MEIC
            unexpected = (actual_position_ids & my_registry_positions) - expected_position_ids
            if unexpected:
                logger.warning(f"POS-003: {len(unexpected)} unexpected {self.BOT_NAME} positions found")

            # Persist state after reconciliation
            self._save_state_to_disk()

            logger.info(f"POS-003: Reconciliation complete - {len(expected_position_ids)} expected, {len(actual_position_ids & my_registry_positions)} found")

        except Exception as e:
            logger.error(f"POS-003: Reconciliation failed: {e}")

    def _reset_for_new_day(self):
        """
        Reset state for a new trading day.

        OVERRIDE: Uses BOT_NAME ("MEIC-TF") instead of hardcoded "MEIC" in parent class.
        """
        from bots.meic.strategy import MEICDailyState

        logger.info("Resetting for new trading day")

        # STATE-004: Check for overnight 0DTE positions (should NEVER happen)
        try:
            my_position_ids = self.registry.get_positions(self.BOT_NAME)  # Use MEIC-TF, not MEIC
        except Exception as e:
            logger.error(f"Registry error checking for overnight positions: {e}")
            my_position_ids = set()
        if my_position_ids:
            # This is a critical error - 0DTE positions should never survive to next day
            error_msg = f"CRITICAL: {len(my_position_ids)} {self.BOT_NAME} positions survived overnight! 0DTE should expire same day."
            logger.critical(error_msg)
            self.alert_service.send_alert(
                alert_type=AlertType.CRITICAL_INTERVENTION,
                title=f"{self.BOT_NAME} Overnight Position Detected!",
                message=error_msg,
                priority=AlertPriority.CRITICAL,
                details={"position_ids": list(my_position_ids)}
            )
            # Halt trading - manual intervention required
            self._critical_intervention_required = True
            self._critical_intervention_reason = "Overnight 0DTE positions detected - investigate immediately"
            return  # Don't reset state, need to handle existing positions

        self.daily_state = MEICDailyState()
        self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")

        self._next_entry_index = 0
        self._daily_summary_sent = False
        self._circuit_breaker_open = False
        self._consecutive_failures = 0
        self._api_results_window.clear()
        self._stop_cascade_triggered = False  # MKT-016: Reset cascade breaker

        # P3: Reset intraday market data tracking
        self.market_data.reset_daily_tracking()

        # P2: Clear WebSocket price cache
        self._ws_price_cache.clear()

        # Reset reconciliation timer
        self._last_reconciliation_time = None

        # POS-004: Reset settlement reconciliation flag for new day
        self._settlement_reconciliation_complete = False

        self.state = MEICState.IDLE

        # Save clean state to disk
        self._save_state_to_disk()

    def check_after_hours_settlement(self) -> bool:
        """
        POS-004: Check if 0DTE positions have been settled after market close.

        OVERRIDE: Uses BOT_NAME ("MEIC-TF") instead of hardcoded "MEIC" in parent class.

        Called on every heartbeat after market close until all positions
        are confirmed settled. This handles the fact that Saxo settles 0DTE
        options sometime between 4:00 PM and 7:00 PM EST.

        Returns:
            True if all positions are settled (or were already confirmed settled)
            False if positions still exist on Saxo (settlement pending)
        """
        # Already confirmed settled for today - skip check
        if self._settlement_reconciliation_complete:
            return True

        # Check how many positions we think we have in registry
        my_position_ids = self.registry.get_positions(self.BOT_NAME)  # Use MEIC-TF, not MEIC

        if not my_position_ids:
            # Registry is already empty - mark as complete
            logger.info(f"POS-004: No {self.BOT_NAME} positions in registry - settlement reconciliation complete")
            self._settlement_reconciliation_complete = True
            return True

        # We have positions in registry - check if they still exist on Saxo
        logger.info(f"POS-004: Checking settlement status for {len(my_position_ids)} {self.BOT_NAME} positions...")

        try:
            # Query Saxo for actual positions
            actual_positions = self.client.get_positions()
            actual_position_ids = {str(p.get("PositionId")) for p in actual_positions}

            # Find which of our registered positions still exist
            still_open = my_position_ids & actual_position_ids
            settled = my_position_ids - actual_position_ids

            if settled:
                logger.info(f"POS-004: {len(settled)} positions settled/expired - cleaning up registry")

                # Clean up settled positions from registry
                for pos_id in settled:
                    try:
                        self.registry.unregister(pos_id)
                        logger.info(f"  Unregistered settled position: {pos_id}")
                    except Exception as e:
                        logger.error(f"Registry error unregistering {pos_id}: {e}")

                # Also clean up from daily state entries
                # Clear BOTH position_id AND uic when options settle
                for entry in self.daily_state.entries:
                    for leg_name in ["short_call", "long_call", "short_put", "long_put"]:
                        pos_id = getattr(entry, f"{leg_name}_position_id")
                        if pos_id and pos_id in settled:
                            setattr(entry, f"{leg_name}_position_id", None)
                            setattr(entry, f"{leg_name}_uic", None)  # Also clear UIC
                            logger.debug(f"  Cleared {leg_name} position_id and uic from entry #{entry.entry_number}")

                # FIX #43 (2026-02-10): Track expired positions and add their credit to realized P&L
                # BUG: Previously, when positions expired worthless at settlement, the code just
                # marked them as "stopped" without adding the credit (which is now profit) to
                # total_realized_pnl. This caused Feb 9 to show -$360 when actual P&L was +$170.
                expired_call_credit = 0.0
                expired_put_credit = 0.0

                for entry in self.daily_state.entries:
                    # FIX #47: Check skipped flag - skipped sides were never opened
                    # Check call side - only process if it had positions (not a put-only entry)
                    call_had_positions = entry.short_call_strike > 0 or entry.long_call_strike > 0
                    call_positions_gone = not entry.short_call_position_id and not entry.long_call_position_id

                    if call_had_positions and call_positions_gone:
                        # Only mark as expired if it wasn't already stopped, expired, OR skipped
                        if not entry.call_side_stopped and not entry.call_side_expired and not entry.call_side_skipped:
                            # Call side EXPIRED (not stopped) - credit is profit!
                            entry.call_side_expired = True
                            credit = entry.call_spread_credit
                            if credit > 0:
                                expired_call_credit += credit
                                logger.info(
                                    f"  Entry #{entry.entry_number} call side EXPIRED worthless: "
                                    f"+${credit:.2f} profit (credit kept)"
                                )

                    # Check put side - only process if it had positions (not a call-only entry)
                    put_had_positions = entry.short_put_strike > 0 or entry.long_put_strike > 0
                    put_positions_gone = not entry.short_put_position_id and not entry.long_put_position_id

                    if put_had_positions and put_positions_gone:
                        # Only mark as expired if it wasn't already stopped, expired, OR skipped
                        if not entry.put_side_stopped and not entry.put_side_expired and not entry.put_side_skipped:
                            # Put side EXPIRED (not stopped) - credit is profit!
                            entry.put_side_expired = True
                            credit = entry.put_spread_credit
                            if credit > 0:
                                expired_put_credit += credit
                                logger.info(
                                    f"  Entry #{entry.entry_number} put side EXPIRED worthless: "
                                    f"+${credit:.2f} profit (credit kept)"
                                )

                    # Mark complete if both sides done (stopped OR expired OR skipped)
                    # For one-sided entries (MEIC-TF), check the appropriate side
                    if entry.call_only:
                        # Call-only entry - done when call side is stopped or expired
                        if entry.call_side_stopped or entry.call_side_expired:
                            entry.is_complete = True
                    elif entry.put_only:
                        # Put-only entry - done when put side is stopped or expired
                        if entry.put_side_stopped or entry.put_side_expired:
                            entry.is_complete = True
                    else:
                        # Full IC - done when both sides are done (stopped/expired/skipped)
                        call_done = entry.call_side_stopped or entry.call_side_expired or entry.call_side_skipped or not call_had_positions
                        put_done = entry.put_side_stopped or entry.put_side_expired or entry.put_side_skipped or not put_had_positions
                        if call_done and put_done:
                            entry.is_complete = True

                # Add expired credits to realized P&L
                total_expired_credit = expired_call_credit + expired_put_credit
                if total_expired_credit > 0:
                    self.daily_state.total_realized_pnl += total_expired_credit
                    logger.info(
                        f"POS-004: Added ${total_expired_credit:.2f} from expired positions to realized P&L "
                        f"(Calls: ${expired_call_credit:.2f}, Puts: ${expired_put_credit:.2f})"
                    )
                    logger.info(
                        f"POS-004: Updated total_realized_pnl: ${self.daily_state.total_realized_pnl:.2f}"
                    )

                # Save updated state
                self._save_state_to_disk()

            if still_open:
                logger.info(f"POS-004: {len(still_open)} positions still open on Saxo - awaiting settlement")
                return False
            else:
                # All positions settled
                logger.info(f"POS-004: All {self.BOT_NAME} positions confirmed settled - reconciliation complete")
                self._settlement_reconciliation_complete = True

                # Log safety event
                self._log_safety_event(
                    "SETTLEMENT_COMPLETE",
                    f"All {len(settled) if settled else len(my_position_ids)} positions settled after market close",
                    "Complete"
                )

                return True

        except Exception as e:
            logger.error(f"POS-004: Settlement check failed: {e}")
            return False

    def _log_safety_event(self, event_type: str, details: str, result: str = "Acknowledged"):
        """
        Log safety events to Google Sheets for audit trail.

        OVERRIDE: Uses BOT_NAME ("MEIC-TF") instead of hardcoded "MEIC" in parent class.

        Args:
            event_type: Type of safety event (e.g., "CIRCUIT_BREAKER_OPEN", "NAKED_SHORT_DETECTED")
            details: Human-readable description of the event
            result: Outcome of the event (default: "Acknowledged")
        """
        try:
            self.trade_logger.log_safety_event({
                "timestamp": get_us_market_time().strftime("%Y-%m-%d %H:%M:%S"),
                "event_type": event_type,
                "bot": self.BOT_NAME,  # Use MEIC-TF, not MEIC
                "state": self.state.value,
                "spy_price": self.current_price,  # Logger expects 'spy_price' not 'spx_price'
                "vix": self.current_vix,
                "active_entries": len(self.daily_state.active_entries),
                "description": details,  # Logger expects 'description' not 'details'
                "result": result
            })
            logger.info(f"Safety event logged: {event_type} - {details}")
        except Exception as e:
            # Don't let logging failure affect trading
            logger.error(f"Failed to log safety event: {e}")

    def _reconstruct_entry_from_positions(
        self,
        entry_number: int,
        positions: List[Dict]
    ) -> Optional[TFIronCondorEntry]:
        """
        Reconstruct a TFIronCondorEntry from Saxo position data.

        OVERRIDE (Fix #40, 2026-02-05): Parent class creates IronCondorEntry objects
        which don't have call_only/put_only fields. For MEIC-TF, we must create
        TFIronCondorEntry objects and set the one-sided flags based on which legs
        exist. Without this, recovery of one-sided entries triggers false stops.

        Args:
            entry_number: The entry number (1-N, based on configured entry_times)
            positions: List of parsed position dicts for this entry

        Returns:
            Reconstructed TFIronCondorEntry or None if invalid
        """
        # Create TFIronCondorEntry instead of IronCondorEntry
        entry = TFIronCondorEntry(entry_number=entry_number)
        entry.strategy_id = f"meic_tf_{get_us_market_time().strftime('%Y%m%d')}_{entry_number:03d}"
        # Fix #52: Set contract count for multi-contract support
        entry.contracts = self.contracts_per_entry

        # Use dictionary approach to handle positions in any order
        entry_prices = {
            "short_call": 0.0,
            "long_call": 0.0,
            "short_put": 0.0,
            "long_put": 0.0,
        }

        # First pass: collect all positions and entry prices
        for pos in positions:
            leg_type = pos.get("leg_type")
            strike = pos.get("strike")
            is_long = pos.get("is_long")

            # Validate leg type matches expected
            expected_long = leg_type in ["long_call", "long_put"]
            if expected_long != is_long:
                logger.warning(f"Entry #{entry_number}: Leg {leg_type} direction mismatch!")

            # Store entry price for later NET calculation
            # Fix #52: Multiply by entry.contracts for multi-contract support
            entry_prices[leg_type] = pos.get("entry_price", 0) * 100 * entry.contracts

            if leg_type == "short_call":
                entry.short_call_position_id = pos["position_id"]
                entry.short_call_uic = pos["uic"]
                entry.short_call_strike = strike
                entry.short_call_price = pos.get("current_price", 0)

            elif leg_type == "long_call":
                entry.long_call_position_id = pos["position_id"]
                entry.long_call_uic = pos["uic"]
                entry.long_call_strike = strike
                entry.long_call_price = pos.get("current_price", 0)

            elif leg_type == "short_put":
                entry.short_put_position_id = pos["position_id"]
                entry.short_put_uic = pos["uic"]
                entry.short_put_strike = strike
                entry.short_put_price = pos.get("current_price", 0)

            elif leg_type == "long_put":
                entry.long_put_position_id = pos["position_id"]
                entry.long_put_uic = pos["uic"]
                entry.long_put_strike = strike
                entry.long_put_price = pos.get("current_price", 0)

        # Second pass: calculate NET credits (short - long)
        entry.call_spread_credit = entry_prices["short_call"] - entry_prices["long_call"]
        entry.put_spread_credit = entry_prices["short_put"] - entry_prices["long_put"]

        logger.debug(
            f"Entry #{entry_number} recovered credits: "
            f"Call=${entry.call_spread_credit:.2f} (short ${entry_prices['short_call']:.2f} - long ${entry_prices['long_call']:.2f}), "
            f"Put=${entry.put_spread_credit:.2f} (short ${entry_prices['short_put']:.2f} - long ${entry_prices['long_put']:.2f})"
        )

        # Check which legs exist
        has_call_side = entry.short_call_position_id and entry.long_call_position_id
        has_put_side = entry.short_put_position_id and entry.long_put_position_id
        has_all_legs = has_call_side and has_put_side

        if not has_all_legs:
            # Partial entry - determine if it's a one-sided TF entry or a stopped entry
            legs_found = []
            if entry.short_call_position_id:
                legs_found.append("SC")
            if entry.long_call_position_id:
                legs_found.append("LC")
            if entry.short_put_position_id:
                legs_found.append("SP")
            if entry.long_put_position_id:
                legs_found.append("LP")

            logger.warning(f"Entry #{entry_number} is PARTIAL: only {legs_found}")

            # MEIC-TF SPECIFIC: Determine if this is a one-sided entry (by design)
            # or if a side was stopped out
            # If we have exactly call side OR put side, it's likely a one-sided TF entry
            # FIX #47: Use "skipped" instead of "stopped" for sides that were never opened
            if has_call_side and not has_put_side:
                # Has call spread only - could be BEARISH (call_only) entry
                # Mark as call_only - stop checking will only monitor call side
                entry.call_only = True
                entry.put_only = False
                entry.call_side_stopped = False
                entry.put_side_skipped = True  # Put was never opened, not stopped
                logger.info(f"Entry #{entry_number}: Detected as CALL-ONLY entry (bearish trend, put side skipped)")
            elif has_put_side and not has_call_side:
                # Has put spread only - could be BULLISH (put_only) entry
                entry.call_only = False
                entry.put_only = True
                entry.call_side_skipped = True  # Call was never opened, not stopped
                entry.put_side_stopped = False
                logger.info(f"Entry #{entry_number}: Detected as PUT-ONLY entry (bullish trend, call side skipped)")
            else:
                # Mixed partial - probably a stopped entry (not skipped)
                entry.call_side_stopped = not has_call_side
                entry.put_side_stopped = not has_put_side

        entry.is_complete = has_all_legs

        # Calculate stop levels based on recovered credit
        total_credit = entry.call_spread_credit + entry.put_spread_credit

        # CRITICAL SAFETY CHECK: Prevent zero stop levels
        MIN_STOP_LEVEL = 50.0

        # FIX #40 (2026-02-06): For one-sided entries, use 2× credit for stop
        # This matches _calculate_stop_levels_tf behavior and prevents immediate false triggers
        # due to bid-ask spread making spread_value slightly higher than credit at entry
        if entry.call_only:
            credit = entry.call_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(
                    f"Recovery CRITICAL: Entry #{entry.entry_number} (call-only) has low credit "
                    f"(${credit:.2f}). Using minimum stop level ${MIN_STOP_LEVEL:.2f}."
                )
                credit = MIN_STOP_LEVEL

            # FIX #40: Use 2× credit for one-sided entries to match full IC behavior
            base_stop = credit * 2

            # Apply MEIC+ reduction if enabled (must match _calculate_stop_levels_tf behavior)
            if self.meic_plus_enabled:
                min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
                if credit > min_credit_for_meic_plus:
                    stop_level = base_stop - self.meic_plus_reduction
                else:
                    stop_level = base_stop
            else:
                stop_level = base_stop

            entry.call_side_stop = stop_level
            entry.put_side_stop = 0  # No put side to monitor
            logger.info(f"Recovery: Call-only stop = ${stop_level:.2f} (2× credit ${credit:.2f})")

        elif entry.put_only:
            credit = entry.put_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(
                    f"Recovery CRITICAL: Entry #{entry.entry_number} (put-only) has low credit "
                    f"(${credit:.2f}). Using minimum stop level ${MIN_STOP_LEVEL:.2f}."
                )
                credit = MIN_STOP_LEVEL

            # FIX #40: Use 2× credit for one-sided entries to match full IC behavior
            base_stop = credit * 2

            # Apply MEIC+ reduction if enabled (must match _calculate_stop_levels_tf behavior)
            if self.meic_plus_enabled:
                min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
                if credit > min_credit_for_meic_plus:
                    stop_level = base_stop - self.meic_plus_reduction
                else:
                    stop_level = base_stop
            else:
                stop_level = base_stop

            entry.put_side_stop = stop_level
            entry.call_side_stop = 0  # No call side to monitor
            logger.info(f"Recovery: Put-only stop = ${stop_level:.2f} (2× credit ${credit:.2f})")
        else:
            # Full IC or stopped entry - use total credit per side
            if total_credit < MIN_STOP_LEVEL:
                logger.critical(
                    f"Recovery CRITICAL: Entry #{entry.entry_number} has low credit "
                    f"(${total_credit:.2f}). Using minimum stop level ${MIN_STOP_LEVEL:.2f}."
                )
                total_credit = MIN_STOP_LEVEL

            if self.meic_plus_enabled:
                min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
                if total_credit > min_credit_for_meic_plus:
                    stop_level = total_credit - self.meic_plus_reduction
                else:
                    stop_level = total_credit
                entry.call_side_stop = stop_level
                entry.put_side_stop = stop_level
            else:
                entry.call_side_stop = total_credit
                entry.put_side_stop = total_credit

        return entry

    def _recover_from_state_file_uics(self, all_positions: List[Dict]) -> Dict[int, List[Dict]]:
        """
        Override to use MEIC-TF bot name in registry during UIC-based recovery.

        This is a fallback recovery method when registry-based recovery fails.
        Uses UICs stored in the state file to match positions and re-registers
        them with the correct bot name (MEIC-TF instead of MEIC).

        Args:
            all_positions: All positions from Saxo API

        Returns:
            Dict mapping entry_number -> list of position data dicts, or empty dict
        """

        logger.info("Attempting UIC-based recovery from state file...")

        try:
            if not os.path.exists(self.state_file):
                logger.warning(f"State file not found: {self.state_file}")
                return {}

            with open(self.state_file, 'r') as f:
                state_data = json.load(f)

            # Check if it's from today
            saved_date = state_data.get("date", "")
            today = get_us_market_time().strftime("%Y-%m-%d")
            if saved_date != today:
                logger.warning(f"State file is from {saved_date}, not today ({today}) - cannot use for recovery")
                return {}

            entries_data = state_data.get("entries", [])
            if not entries_data:
                logger.info("State file has no entries")
                return {}

            logger.info(f"Found {len(entries_data)} entries in state file")

            # Build UIC to entry/leg mapping from state file
            uic_to_entry_leg: Dict[int, Tuple[int, str]] = {}
            for entry_data in entries_data:
                entry_num = entry_data.get("entry_number")
                if entry_num is None:
                    continue

                # Map each UIC to its entry and leg type
                for leg in ["short_call", "long_call", "short_put", "long_put"]:
                    uic = entry_data.get(f"{leg}_uic")
                    if uic:
                        uic_to_entry_leg[uic] = (entry_num, leg)

            logger.info(f"Built UIC map with {len(uic_to_entry_leg)} UICs")

            # Match Saxo positions by UIC
            entries_by_number: Dict[int, List[Dict]] = {}
            matched_count = 0

            for pos in all_positions:
                pos_base = pos.get("PositionBase", {})
                uic = pos_base.get("Uic")

                if uic and uic in uic_to_entry_leg:
                    entry_num, leg_type = uic_to_entry_leg[uic]

                    # Parse the position
                    parsed = self._parse_spx_option_position(pos)
                    if parsed:
                        parsed["leg_type"] = leg_type
                        parsed["entry_number"] = entry_num

                        if entry_num not in entries_by_number:
                            entries_by_number[entry_num] = []
                        entries_by_number[entry_num].append(parsed)
                        matched_count += 1

                        # Re-register this position with correct ID
                        pos_id = str(pos.get("PositionId"))
                        if pos_id and pos_id != "None":
                            # Find the entry data to get strategy_id
                            for entry_data in entries_data:
                                if entry_data.get("entry_number") == entry_num:
                                    strategy_id = entry_data.get("strategy_id", f"meic_tf_{today}_entry{entry_num}")
                                    try:
                                        self.registry.register(
                                            position_id=pos_id,
                                            bot_name=self.BOT_NAME,  # Use MEIC-TF
                                            strategy_id=strategy_id,
                                            metadata={
                                                "entry_number": entry_num,
                                                "leg_type": leg_type,
                                                "strike": parsed.get("strike")
                                            }
                                        )
                                        logger.info(f"Re-registered position {pos_id} (UIC {uic}) as Entry #{entry_num} {leg_type}")
                                    except Exception as e:
                                        logger.error(f"Registry error re-registering {pos_id}: {e}")
                                    break

            logger.info(f"UIC-based recovery matched {matched_count} positions to {len(entries_by_number)} entries")
            return entries_by_number

        except Exception as e:
            logger.error(f"UIC-based recovery failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}

    def _load_state_file_history(self) -> bool:
        """
        FIX #41 (2026-02-06): Load historical data from state file when no active positions.

        When all positions have been stopped out, we still need to preserve:
        - Completed entries (for tracking which entries are done)
        - Realized P&L from stopped entries
        - Stop counters (put_stops, call_stops, double_stops)
        - Commission totals
        - Next entry index

        This method loads the state file and restores this historical data
        to daily_state, even when there are no active positions to monitor.

        Returns:
            bool: True if historical data was loaded, False if no valid state file
        """
        today = get_us_market_time().strftime("%Y-%m-%d")

        try:
            if not os.path.exists(self.state_file):
                logger.info("No state file found - truly starting fresh")
                return False

            with open(self.state_file, 'r') as f:
                saved_state = json.load(f)

            # Only use saved state if it's from today
            if saved_state.get("date") != today:
                logger.info(f"State file is from {saved_state.get('date')}, not today ({today}) - starting fresh")
                return False

            # Restore historical data
            self.daily_state.date = today
            self.daily_state.total_realized_pnl = saved_state.get("total_realized_pnl", 0.0)
            self.daily_state.put_stops_triggered = saved_state.get("put_stops_triggered", 0)
            self.daily_state.call_stops_triggered = saved_state.get("call_stops_triggered", 0)
            self.daily_state.double_stops = saved_state.get("double_stops", 0)
            self.daily_state.total_commission = saved_state.get("total_commission", 0.0)
            self.daily_state.entries_completed = saved_state.get("entries_completed", 0)
            self.daily_state.entries_failed = saved_state.get("entries_failed", 0)
            self.daily_state.entries_skipped = saved_state.get("entries_skipped", 0)
            self.daily_state.total_credit_received = saved_state.get("total_credit_received", 0.0)
            # Fix #55/#56/#57: Restore MEIC-TF specific counters
            self.daily_state.one_sided_entries = saved_state.get("one_sided_entries", 0)
            self.daily_state.trend_overrides = saved_state.get("trend_overrides", 0)
            self.daily_state.credit_gate_skips = saved_state.get("credit_gate_skips", 0)

            # Restore intraday OHLC so mid-day restart doesn't lose open/high/low
            ohlc = saved_state.get("market_data_ohlc", {})
            if ohlc:
                self.market_data.spx_open = ohlc.get("spx_open", 0.0)
                self.market_data.spx_high = ohlc.get("spx_high", 0.0)
                spx_low = ohlc.get("spx_low", 0.0)
                if spx_low > 0:
                    self.market_data.spx_low = spx_low
                self.market_data.vix_open = ohlc.get("vix_open", 0.0)
                self.market_data.vix_high = ohlc.get("vix_high", 0.0)
                vix_low = ohlc.get("vix_low", 0.0)
                if vix_low > 0:
                    self.market_data.vix_low = vix_low

            # Restore stopped entries (entries that have no live positions)
            # FIX #47: Also consider expired and skipped flags
            stopped_entries_restored = 0
            for entry_data in saved_state.get("entries", []):
                call_stopped = entry_data.get("call_side_stopped", False)
                put_stopped = entry_data.get("put_side_stopped", False)
                call_expired = entry_data.get("call_side_expired", False)
                put_expired = entry_data.get("put_side_expired", False)
                call_skipped = entry_data.get("call_side_skipped", False)
                put_skipped = entry_data.get("put_side_skipped", False)
                call_only = entry_data.get("call_only", False)
                put_only = entry_data.get("put_only", False)

                # A side is "done" if stopped, expired, or skipped
                call_done = call_stopped or call_expired or call_skipped
                put_done = put_stopped or put_expired or put_skipped

                # Check if this entry is fully done (no live positions)
                is_fully_done = False
                if call_only and call_done:
                    is_fully_done = True
                elif put_only and put_done:
                    is_fully_done = True
                elif not call_only and not put_only and call_done and put_done:
                    is_fully_done = True

                if is_fully_done:
                    # Reconstruct the entry from saved state
                    entry_num = entry_data.get("entry_number")
                    stopped_entry = TFIronCondorEntry(entry_number=entry_num)

                    # Parse entry_time if it's a string
                    entry_time_str = entry_data.get("entry_time")
                    if entry_time_str:
                        if isinstance(entry_time_str, str):
                            try:
                                stopped_entry.entry_time = datetime.fromisoformat(entry_time_str)
                            except ValueError:
                                stopped_entry.entry_time = None
                        else:
                            stopped_entry.entry_time = entry_time_str

                    stopped_entry.strategy_id = entry_data.get("strategy_id", f"meic_tf_{today.replace('-', '')}_{entry_num:03d}")
                    stopped_entry.short_call_strike = entry_data.get("short_call_strike", 0)
                    stopped_entry.long_call_strike = entry_data.get("long_call_strike", 0)
                    stopped_entry.short_put_strike = entry_data.get("short_put_strike", 0)
                    stopped_entry.long_put_strike = entry_data.get("long_put_strike", 0)
                    stopped_entry.call_spread_credit = entry_data.get("call_spread_credit", 0)
                    stopped_entry.put_spread_credit = entry_data.get("put_spread_credit", 0)
                    stopped_entry.call_side_stop = entry_data.get("call_side_stop", 0)
                    stopped_entry.put_side_stop = entry_data.get("put_side_stop", 0)
                    # FIX #47: Restore all status flags (stopped/expired/skipped)
                    stopped_entry.call_side_stopped = call_stopped
                    stopped_entry.put_side_stopped = put_stopped
                    stopped_entry.call_side_expired = call_expired
                    stopped_entry.put_side_expired = put_expired
                    stopped_entry.call_side_skipped = call_skipped
                    stopped_entry.put_side_skipped = put_skipped
                    # Fix #61: Restore merge flags
                    stopped_entry.call_side_merged = entry_data.get("call_side_merged", False)
                    stopped_entry.put_side_merged = entry_data.get("put_side_merged", False)
                    stopped_entry.is_complete = True
                    stopped_entry.open_commission = entry_data.get("open_commission", 0)
                    stopped_entry.close_commission = entry_data.get("close_commission", 0)
                    stopped_entry.call_only = call_only
                    stopped_entry.put_only = put_only
                    # Fix #52: Restore contract count (default to current config if not saved)
                    stopped_entry.contracts = entry_data.get("contracts", self.contracts_per_entry)

                    if entry_data.get("trend_signal"):
                        try:
                            stopped_entry.trend_signal = TrendSignal(entry_data["trend_signal"])
                        except ValueError:
                            pass

                    # Fix #49: Restore override_reason for correct logging
                    stopped_entry.override_reason = entry_data.get("override_reason", None)
                    # Fix #59: Restore EMA values for Trades tab logging
                    stopped_entry.ema_20_at_entry = entry_data.get("ema_20_at_entry", None)
                    stopped_entry.ema_40_at_entry = entry_data.get("ema_40_at_entry", None)

                    self.daily_state.entries.append(stopped_entry)
                    stopped_entries_restored += 1

            # Update next_entry_index based on restored entries
            if self.daily_state.entries:
                max_entry_num = max(e.entry_number for e in self.daily_state.entries)
                self._next_entry_index = max_entry_num  # Next entry is the one after max

            logger.info(f"FIX #41: Loaded state file history - P&L: ${self.daily_state.total_realized_pnl:.2f}, "
                       f"entries_completed: {self.daily_state.entries_completed}, "
                       f"stopped_entries_restored: {stopped_entries_restored}, "
                       f"next_entry_index: {self._next_entry_index}")

            return True

        except Exception as e:
            logger.warning(f"Could not load state file history: {e}")
            return False

    def _recover_positions_from_saxo(self) -> bool:
        """
        Override to use MEIC-TF bot name in registry queries and logging.

        This is the main recovery method that queries Saxo API for positions
        and uses the Position Registry to identify which belong to MEIC-TF.

        Returns:
            bool: True if positions were recovered, False if starting fresh
        """
        logger.info("=" * 60)
        logger.info("POSITION RECOVERY: Querying Saxo API for source of truth...")
        logger.info("=" * 60)

        today = get_us_market_time().strftime("%Y-%m-%d")

        try:
            # Step 1: Get ALL positions from Saxo
            all_positions = self.client.get_positions()
            if not all_positions:
                logger.info("No positions found in Saxo account")
                # FIX #41: Still load historical data from state file
                self._load_state_file_history()
                self.daily_state.date = today
                return False

            logger.info(f"Found {len(all_positions)} total positions in account")

            # Step 2: Get valid position IDs and clean up registry orphans
            valid_ids = {str(p.get("PositionId")) for p in all_positions}
            if not self.dry_run:
                try:
                    orphans = self.registry.cleanup_orphans(valid_ids)
                    if orphans:
                        logger.warning(f"Cleaned up {len(orphans)} orphaned registry entries (positions closed externally)")
                        self._log_safety_event("ORPHAN_CLEANUP", f"Removed {len(orphans)} orphaned positions from registry")
                except Exception as e:
                    logger.error(f"Registry error during orphan cleanup: {e}")
            else:
                logger.debug("Skipping orphan cleanup in dry-run mode")

            # Step 3: Get MEIC-TF positions from registry (using class constant)
            my_position_ids = self.registry.get_positions(self.BOT_NAME)
            if not my_position_ids:
                logger.info(f"No {self.BOT_NAME} positions in registry")
                # FIX #41: Still load historical data from state file
                self._load_state_file_history()
                self.daily_state.date = today
                return False

            logger.info(f"Found {len(my_position_ids)} {self.BOT_NAME} positions in registry")

            # Step 4: Filter Saxo positions to just MEIC-TF positions
            meic_tf_positions = []
            for pos in all_positions:
                pos_id = str(pos.get("PositionId"))
                if pos_id in my_position_ids:
                    meic_tf_positions.append(pos)

            if not meic_tf_positions:
                logger.warning(f"Registry says we have {self.BOT_NAME} positions but none found in Saxo! Cleaning registry...")
                for pos_id in my_position_ids:
                    try:
                        self.registry.unregister(pos_id)
                    except Exception as e:
                        logger.error(f"Registry error unregistering {pos_id}: {e}")
                self._log_safety_event("REGISTRY_CLEARED", f"All {self.BOT_NAME} positions removed - not found in Saxo")
                # FIX #41: Still load historical data from state file
                self._load_state_file_history()
                self.daily_state.date = today
                return False

            logger.info(f"Matched {len(meic_tf_positions)} positions to {self.BOT_NAME} in Saxo")

            # Step 5: Group positions by entry number using registry metadata
            entries_by_number = self._group_positions_by_entry(meic_tf_positions, my_position_ids)

            if not entries_by_number:
                logger.warning("Could not group positions into entries via registry - trying UIC fallback...")
                entries_by_number = self._recover_from_state_file_uics(all_positions)
                if not entries_by_number:
                    logger.warning("UIC-based recovery also failed - manual review needed")
                    self._log_safety_event("RECOVERY_FAILED", "Could not reconstruct entries from positions or UICs", "Manual Review Needed")
                    self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")
                    return False
                else:
                    logger.info(f"UIC-based recovery succeeded: found {len(entries_by_number)} entries")

            # Step 6: Reconstruct IronCondorEntry objects
            recovered_entries = []
            for entry_num, positions in entries_by_number.items():
                entry = self._reconstruct_entry_from_positions(entry_num, positions)
                if entry:
                    recovered_entries.append(entry)
                    logger.info(
                        f"  Entry #{entry_num}: "
                        f"SC={entry.short_call_strike} LC={entry.long_call_strike} "
                        f"SP={entry.short_put_strike} LP={entry.long_put_strike}"
                    )

            if not recovered_entries:
                logger.warning("Failed to reconstruct any entries from Saxo positions")
                self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")
                return False

            # Step 7: Update local state to match Saxo
            today = get_us_market_time().strftime("%Y-%m-%d")

            # Load existing state file to preserve realized P&L
            preserved_realized_pnl = 0.0
            preserved_put_stops = 0
            preserved_call_stops = 0
            preserved_double_stops = 0
            preserved_total_commission = 0.0
            # Fix #65: Preserve additional counters from state file
            preserved_total_credit_received = 0.0
            preserved_entries_completed = 0
            preserved_entries_failed = 0
            preserved_entries_skipped = 0
            preserved_one_sided_entries = 0
            preserved_trend_overrides = 0
            preserved_credit_gate_skips = 0
            preserved_entry_credits = {}
            preserved_stopped_entries = []  # FIX #43: Fully stopped entries (no live positions)
            preserved_market_ohlc = {}
            try:
                if os.path.exists(self.state_file):
                    with open(self.state_file, "r") as f:
                        saved_state = json.load(f)
                        if saved_state.get("date") == today:
                            preserved_realized_pnl = saved_state.get("total_realized_pnl", 0.0)
                            preserved_put_stops = saved_state.get("put_stops_triggered", 0)
                            preserved_call_stops = saved_state.get("call_stops_triggered", 0)
                            preserved_double_stops = saved_state.get("double_stops", 0)
                            preserved_total_commission = saved_state.get("total_commission", 0.0)
                            # Fix #65: Also preserve total_credit_received and other counters
                            preserved_total_credit_received = saved_state.get("total_credit_received", 0.0)
                            preserved_entries_completed = saved_state.get("entries_completed", 0)
                            preserved_entries_failed = saved_state.get("entries_failed", 0)
                            preserved_entries_skipped = saved_state.get("entries_skipped", 0)
                            preserved_one_sided_entries = saved_state.get("one_sided_entries", 0)
                            preserved_trend_overrides = saved_state.get("trend_overrides", 0)
                            preserved_credit_gate_skips = saved_state.get("credit_gate_skips", 0)
                            preserved_market_ohlc = saved_state.get("market_data_ohlc", {})
                            for entry_data in saved_state.get("entries", []):
                                entry_num = entry_data.get("entry_number")
                                if entry_num:
                                    preserved_entry_credits[entry_num] = {
                                        "call_credit": entry_data.get("call_spread_credit", 0),
                                        "put_credit": entry_data.get("put_spread_credit", 0),
                                        "call_stop": entry_data.get("call_side_stop", 0),
                                        "put_stop": entry_data.get("put_side_stop", 0),
                                        "short_call_strike": entry_data.get("short_call_strike", 0),
                                        "long_call_strike": entry_data.get("long_call_strike", 0),
                                        "short_put_strike": entry_data.get("short_put_strike", 0),
                                        "long_put_strike": entry_data.get("long_put_strike", 0),
                                        "call_side_stopped": entry_data.get("call_side_stopped", False),
                                        "put_side_stopped": entry_data.get("put_side_stopped", False),
                                        "call_side_expired": entry_data.get("call_side_expired", False),
                                        "put_side_expired": entry_data.get("put_side_expired", False),
                                        "call_side_skipped": entry_data.get("call_side_skipped", False),
                                        "put_side_skipped": entry_data.get("put_side_skipped", False),
                                        "open_commission": entry_data.get("open_commission", 0),
                                        "close_commission": entry_data.get("close_commission", 0),
                                        # MEIC-TF specific fields (Fix #40)
                                        "call_only": entry_data.get("call_only", False),
                                        "put_only": entry_data.get("put_only", False),
                                        "trend_signal": entry_data.get("trend_signal"),
                                        # Fix #49: Preserve override_reason for correct logging
                                        "override_reason": entry_data.get("override_reason"),
                                        # Fix #67: Preserve UICs for merged position recovery
                                        "long_call_uic": entry_data.get("long_call_uic"),
                                        "long_put_uic": entry_data.get("long_put_uic"),
                                        "short_call_uic": entry_data.get("short_call_uic"),
                                        "short_put_uic": entry_data.get("short_put_uic"),
                                    }
                                    # FIX #43 + FIX #47: Check if this entry is fully done (no live positions)
                                    # A side is "done" if it was stopped OR expired OR skipped
                                    call_stopped = entry_data.get("call_side_stopped", False)
                                    put_stopped = entry_data.get("put_side_stopped", False)
                                    call_expired = entry_data.get("call_side_expired", False)
                                    put_expired = entry_data.get("put_side_expired", False)
                                    call_skipped = entry_data.get("call_side_skipped", False)
                                    put_skipped = entry_data.get("put_side_skipped", False)
                                    call_only = entry_data.get("call_only", False)
                                    put_only = entry_data.get("put_only", False)

                                    call_done = call_stopped or call_expired or call_skipped
                                    put_done = put_stopped or put_expired or put_skipped

                                    is_fully_done = False
                                    if call_only and call_done:
                                        is_fully_done = True
                                    elif put_only and put_done:
                                        is_fully_done = True
                                    elif not call_only and not put_only and call_done and put_done:
                                        is_fully_done = True

                                    if is_fully_done:
                                        preserved_stopped_entries.append(entry_data)

                            logger.info(f"Preserved from state file: realized_pnl=${preserved_realized_pnl:.2f}, "
                                       f"put_stops={preserved_put_stops}, call_stops={preserved_call_stops}, "
                                       f"stopped_entries={len(preserved_stopped_entries)}")
            except Exception as e:
                logger.warning(f"Could not load state file for preservation: {e}")

            # Apply preserved credits, stop levels, and strikes to recovered entries
            for entry in recovered_entries:
                if entry.entry_number in preserved_entry_credits:
                    saved = preserved_entry_credits[entry.entry_number]
                    entry.call_spread_credit = saved["call_credit"]
                    entry.put_spread_credit = saved["put_credit"]
                    entry.call_side_stop = saved["call_stop"]
                    entry.put_side_stop = saved["put_stop"]

                    # Fix #65: Restore ALL status flags from state file (authoritative source)
                    # The reconstruction code guesses entry types from positions, but the state
                    # file knows the actual history (e.g., full IC with stopped put vs call-only entry)
                    entry.call_side_stopped = saved.get("call_side_stopped", False)
                    entry.put_side_stopped = saved.get("put_side_stopped", False)
                    entry.call_side_expired = saved.get("call_side_expired", False)
                    entry.put_side_expired = saved.get("put_side_expired", False)
                    entry.call_side_skipped = saved.get("call_side_skipped", False)
                    entry.put_side_skipped = saved.get("put_side_skipped", False)

                    entry.open_commission = saved.get("open_commission", 0)
                    entry.close_commission = saved.get("close_commission", 0)

                    # Fix #65: Always restore entry type from state file (authoritative source)
                    # Without this, a full IC with a stopped put side gets misclassified as
                    # call_only by _reconstruct_entry_from_positions() (it only sees call positions)
                    entry.call_only = saved.get("call_only", False)
                    entry.put_only = saved.get("put_only", False)
                    if entry.call_only:
                        logger.info(f"Entry #{entry.entry_number}: Restored as CALL-ONLY from state file")
                    elif entry.put_only:
                        logger.info(f"Entry #{entry.entry_number}: Restored as PUT-ONLY from state file")
                    else:
                        logger.info(f"Entry #{entry.entry_number}: Restored as FULL IC from state file")

                    # Restore trend signal and override reason if saved
                    if saved.get("trend_signal"):
                        try:
                            entry.trend_signal = TrendSignal(saved["trend_signal"])
                        except ValueError:
                            pass  # Invalid trend signal value, ignore
                    # Fix #65: Restore override_reason for correct logging (was missing)
                    entry.override_reason = saved.get("override_reason", None)

                    if entry.call_side_stopped and entry.short_call_strike == 0:
                        entry.short_call_strike = saved.get("short_call_strike", 0)
                        entry.long_call_strike = saved.get("long_call_strike", 0)
                        logger.info(f"Entry #{entry.entry_number}: Restored stopped call strikes "
                                   f"(short={entry.short_call_strike}, long={entry.long_call_strike})")
                    if entry.put_side_stopped and entry.short_put_strike == 0:
                        entry.short_put_strike = saved.get("short_put_strike", 0)
                        entry.long_put_strike = saved.get("long_put_strike", 0)
                        logger.info(f"Entry #{entry.entry_number}: Restored stopped put strikes "
                                   f"(short={entry.short_put_strike}, long={entry.long_put_strike})")

                    # Fix #67: Restore missing strikes/UICs for active sides.
                    # When Saxo merges long positions at the same strike (MKT-015 scenario),
                    # recovery can't find the older entry's long leg. The state file has
                    # the correct values from before the merge.
                    if not entry.call_side_stopped and entry.long_call_strike == 0:
                        saved_lc_strike = saved.get("long_call_strike", 0)
                        saved_lc_uic = saved.get("long_call_uic")
                        if saved_lc_strike:
                            entry.long_call_strike = saved_lc_strike
                            if saved_lc_uic:
                                entry.long_call_uic = saved_lc_uic
                            logger.warning(
                                f"Entry #{entry.entry_number}: Restored missing long call from state file "
                                f"(strike={saved_lc_strike}, uic={saved_lc_uic}) - likely merged position"
                            )
                    if not entry.put_side_stopped and entry.long_put_strike == 0:
                        saved_lp_strike = saved.get("long_put_strike", 0)
                        saved_lp_uic = saved.get("long_put_uic")
                        if saved_lp_strike:
                            entry.long_put_strike = saved_lp_strike
                            if saved_lp_uic:
                                entry.long_put_uic = saved_lp_uic
                            logger.warning(
                                f"Entry #{entry.entry_number}: Restored missing long put from state file "
                                f"(strike={saved_lp_strike}, uic={saved_lp_uic}) - likely merged position"
                            )

                    logger.info(f"Entry #{entry.entry_number}: Restored credits from state file "
                               f"(call=${saved['call_credit']:.2f}, put=${saved['put_credit']:.2f}, "
                               f"stop=${saved['call_stop']:.2f})")

            # FIX #43 (2026-02-05): Reconstruct fully stopped entries that have no live positions
            recovered_entry_nums = {e.entry_number for e in recovered_entries}
            for stopped_entry_data in preserved_stopped_entries:
                entry_num = stopped_entry_data.get("entry_number")
                if entry_num and entry_num not in recovered_entry_nums:
                    # Reconstruct TFIronCondorEntry from saved state data
                    stopped_entry = TFIronCondorEntry(entry_number=entry_num)
                    stopped_entry.entry_time = stopped_entry_data.get("entry_time")
                    stopped_entry.strategy_id = stopped_entry_data.get("strategy_id", f"meic_tf_{today.replace('-', '')}_{entry_num:03d}")

                    # Strikes
                    stopped_entry.short_call_strike = stopped_entry_data.get("short_call_strike", 0)
                    stopped_entry.long_call_strike = stopped_entry_data.get("long_call_strike", 0)
                    stopped_entry.short_put_strike = stopped_entry_data.get("short_put_strike", 0)
                    stopped_entry.long_put_strike = stopped_entry_data.get("long_put_strike", 0)

                    # Credits and stops
                    stopped_entry.call_spread_credit = stopped_entry_data.get("call_spread_credit", 0)
                    stopped_entry.put_spread_credit = stopped_entry_data.get("put_spread_credit", 0)
                    stopped_entry.call_side_stop = stopped_entry_data.get("call_side_stop", 0)
                    stopped_entry.put_side_stop = stopped_entry_data.get("put_side_stop", 0)

                    # Stopped/expired/skipped flags - entry is fully done (FIX #47)
                    stopped_entry.call_side_stopped = stopped_entry_data.get("call_side_stopped", False)
                    stopped_entry.put_side_stopped = stopped_entry_data.get("put_side_stopped", False)
                    stopped_entry.call_side_expired = stopped_entry_data.get("call_side_expired", False)
                    stopped_entry.put_side_expired = stopped_entry_data.get("put_side_expired", False)
                    stopped_entry.call_side_skipped = stopped_entry_data.get("call_side_skipped", False)
                    stopped_entry.put_side_skipped = stopped_entry_data.get("put_side_skipped", False)
                    # Fix #61: Restore merge flags
                    stopped_entry.call_side_merged = stopped_entry_data.get("call_side_merged", False)
                    stopped_entry.put_side_merged = stopped_entry_data.get("put_side_merged", False)
                    stopped_entry.is_complete = True

                    # Commission
                    stopped_entry.open_commission = stopped_entry_data.get("open_commission", 0)
                    stopped_entry.close_commission = stopped_entry_data.get("close_commission", 0)

                    # MEIC-TF specific: One-sided entry flags
                    stopped_entry.call_only = stopped_entry_data.get("call_only", False)
                    stopped_entry.put_only = stopped_entry_data.get("put_only", False)
                    # Fix #52: Restore contract count (default to current config if not saved)
                    stopped_entry.contracts = stopped_entry_data.get("contracts", self.contracts_per_entry)
                    if stopped_entry_data.get("trend_signal"):
                        try:
                            stopped_entry.trend_signal = TrendSignal(stopped_entry_data["trend_signal"])
                        except ValueError:
                            pass

                    # Fix #49: Restore override_reason for correct logging
                    stopped_entry.override_reason = stopped_entry_data.get("override_reason", None)
                    # Fix #59: Restore EMA values for Trades tab logging
                    stopped_entry.ema_20_at_entry = stopped_entry_data.get("ema_20_at_entry", None)
                    stopped_entry.ema_40_at_entry = stopped_entry_data.get("ema_40_at_entry", None)

                    # Position IDs are None (positions closed)
                    stopped_entry.short_call_position_id = None
                    stopped_entry.long_call_position_id = None
                    stopped_entry.short_put_position_id = None
                    stopped_entry.long_put_position_id = None

                    recovered_entries.append(stopped_entry)

                    one_sided_info = ""
                    if stopped_entry.call_only:
                        one_sided_info = ", call_only=True"
                    elif stopped_entry.put_only:
                        one_sided_info = ", put_only=True"
                    logger.info(f"FIX #43: Restored fully stopped Entry #{entry_num} from state file "
                               f"(credit=${stopped_entry.total_credit:.2f}{one_sided_info})")

            # Sort recovered entries by entry number
            recovered_entries.sort(key=lambda e: e.entry_number)

            # Reset daily state but preserve date
            self.daily_state = MEICDailyState()
            self.daily_state.date = today
            self.daily_state.entries = recovered_entries
            self.daily_state.entries_completed = len(recovered_entries)

            # Restore preserved P&L, stop counters, and other state from state file
            self.daily_state.total_realized_pnl = preserved_realized_pnl
            self.daily_state.put_stops_triggered = preserved_put_stops
            self.daily_state.call_stops_triggered = preserved_call_stops
            self.daily_state.double_stops = preserved_double_stops
            self.daily_state.total_commission = preserved_total_commission
            # Fix #65: Restore additional counters that were previously lost on recovery
            self.daily_state.entries_failed = preserved_entries_failed
            self.daily_state.entries_skipped = preserved_entries_skipped
            self.daily_state.one_sided_entries = preserved_one_sided_entries
            self.daily_state.trend_overrides = preserved_trend_overrides
            self.daily_state.credit_gate_skips = preserved_credit_gate_skips

            # Restore intraday OHLC so mid-day restart doesn't lose open/high/low
            if preserved_market_ohlc:
                self.market_data.spx_open = preserved_market_ohlc.get("spx_open", 0.0)
                self.market_data.spx_high = preserved_market_ohlc.get("spx_high", 0.0)
                spx_low = preserved_market_ohlc.get("spx_low", 0.0)
                if spx_low > 0:
                    self.market_data.spx_low = spx_low
                self.market_data.vix_open = preserved_market_ohlc.get("vix_open", 0.0)
                self.market_data.vix_high = preserved_market_ohlc.get("vix_high", 0.0)
                vix_low = preserved_market_ohlc.get("vix_low", 0.0)
                if vix_low > 0:
                    self.market_data.vix_low = vix_low

            # Determine next entry index
            if recovered_entries:
                max_entry_num = max(e.entry_number for e in recovered_entries)
                self._next_entry_index = max_entry_num
            else:
                self._next_entry_index = 0

            # Set state based on recovered positions
            # FIX #43 + FIX #47: For one-sided entries, check only the placed side
            # A side is "done" if stopped, expired, or skipped
            if recovered_entries:
                def is_entry_active(entry):
                    call_done = entry.call_side_stopped or entry.call_side_expired or entry.call_side_skipped
                    put_done = entry.put_side_stopped or entry.put_side_expired or entry.put_side_skipped
                    if getattr(entry, 'call_only', False):
                        return not call_done
                    elif getattr(entry, 'put_only', False):
                        return not put_done
                    else:
                        return not (call_done and put_done)

                active_entries = [e for e in recovered_entries if is_entry_active(e)]
                if active_entries:
                    self.state = MEICState.MONITORING
                elif self._next_entry_index < len(self.entry_times):
                    self.state = MEICState.WAITING_FIRST_ENTRY
                else:
                    self.state = MEICState.DAILY_COMPLETE

            # Fix #65: Use preserved total_credit from state file if available,
            # rather than recalculating (recalculation depends on correct call_only/put_only flags
            # which may have been wrong before state file restoration in earlier versions)
            if preserved_total_credit_received > 0:
                total_credit = preserved_total_credit_received
                self.daily_state.total_credit_received = preserved_total_credit_received
            else:
                total_credit = sum(e.total_credit for e in recovered_entries)
                self.daily_state.total_credit_received = total_credit

            # Retroactively calculate commission for entries without commission data
            # BUG FIX: Use 2 legs for one-sided entries, 4 for full ICs
            if self.daily_state.total_commission == 0 and recovered_entries:
                retroactive_commission = 0.0
                for entry in recovered_entries:
                    if entry.open_commission == 0:
                        # One-sided entries have 2 legs, full ICs have 4
                        is_one_sided = getattr(entry, 'call_only', False) or getattr(entry, 'put_only', False)
                        open_legs = 2 if is_one_sided else 4
                        entry.open_commission = open_legs * self.commission_per_leg * self.contracts_per_entry
                        retroactive_commission += entry.open_commission
                    if entry.close_commission == 0:
                        if entry.call_side_stopped:
                            close_comm = 2 * self.commission_per_leg * self.contracts_per_entry
                            entry.close_commission += close_comm
                            retroactive_commission += close_comm
                        if entry.put_side_stopped:
                            close_comm = 2 * self.commission_per_leg * self.contracts_per_entry
                            entry.close_commission += close_comm
                            retroactive_commission += close_comm
                self.daily_state.total_commission = retroactive_commission
                if retroactive_commission > 0:
                    logger.info(f"Retroactively calculated commission: ${retroactive_commission:.2f} "
                               f"(from {len(recovered_entries)} entries)")

            logger.info("=" * 60)
            logger.info(f"RECOVERY COMPLETE: {len(recovered_entries)} entries recovered")
            logger.info(f"  State: {self.state.value}")
            logger.info(f"  Next entry index: {self._next_entry_index}")
            logger.info(f"  Total credit: ${total_credit:.2f}")
            logger.info("=" * 60)

            # Send recovery alert
            self.alert_service.send_alert(
                alert_type=AlertType.POSITION_OPENED,
                title=f"{self.BOT_NAME} Position Recovery",
                message=f"Recovered {len(recovered_entries)} iron condor(s) from Saxo API",
                priority=AlertPriority.MEDIUM,
                details={
                    "entries_recovered": len(recovered_entries),
                    "state": self.state.value,
                    "total_credit": total_credit
                }
            )

            # Save recovered state to disk
            self._save_state_to_disk()

            return True

        except Exception as e:
            logger.error(f"Position recovery failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self._log_safety_event("RECOVERY_ERROR", str(e), "Error")
            self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")
            return False
