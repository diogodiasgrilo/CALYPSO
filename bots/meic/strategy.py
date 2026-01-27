"""
strategy.py - MEIC (Multiple Entry Iron Condors) Strategy Implementation

This module implements Tammy Chambless's MEIC 0DTE strategy:
- 6 scheduled iron condor entries per day (10:00, 10:30, 11:00, 11:30, 12:00, 12:30 AM ET)
- OTM call spread + OTM put spread per entry (4 legs = 1 IC)
- Per-side stop losses equal to total credit received
- MEIC+ modification: stop = credit - $0.10 for small wins on stop days

Strategy Source: Tammy Chambless (Queen of 0DTE)
Reference: https://www.thetaprofits.com/tammy-chambless-explains-her-meic-strategy-for-trading-0dte-options/

Key Metrics (Tammy Chambless, Jan 2023 - present):
- 20.7% CAGR, 4.31% max drawdown, 4.8 Calmar ratio
- ~70% win rate
- Risk Rating: 3.5/10

Author: Trading Bot Developer
Date: 2026-01-27

Edge Case Audit: 2026-01-27
- 75 edge cases analyzed pre-implementation (see docs/MEIC_EDGE_CASES.md)
- Critical priorities: ORDER-002 (naked position), STOP-001 (stop calculation)

See docs/MEIC_STRATEGY_SPECIFICATION.md for full specification.
"""

import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta, date, time as dt_time
from typing import Optional, Dict, List, Any, Tuple, Deque, Set
from dataclasses import dataclass, field
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell, OrderType
from shared.alert_service import AlertService, AlertType, AlertPriority
from shared.market_hours import get_us_market_time, US_EASTERN, is_market_open
from shared.event_calendar import is_fomc_announcement_day
from shared.position_registry import PositionRegistry

# Configure module logger
logger = logging.getLogger(__name__)

# =============================================================================
# PATH CONSTANTS
# =============================================================================

# Paths for persistent storage
DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data"
)
METRICS_FILE = os.path.join(DATA_DIR, "meic_metrics.json")
STATE_FILE = os.path.join(DATA_DIR, "meic_state.json")
REGISTRY_FILE = os.path.join(DATA_DIR, "position_registry.json")

# =============================================================================
# SAFETY CONSTANTS
# =============================================================================

# MULTI-001: Continue with remaining entries if one fails
MAX_FAILED_ENTRIES_BEFORE_HALT = 4  # Stop trying if 4+ entries fail in a day

# CONN-001: Entry window retry settings
ENTRY_MAX_RETRIES = 3  # Retry entry this many times
ENTRY_RETRY_DELAY_SECONDS = 30  # Delay between retries
ENTRY_WINDOW_MINUTES = 5  # How long after scheduled time to attempt entry

# ORDER-002: Naked position safety - CRITICAL
NAKED_POSITION_MAX_AGE_SECONDS = 30  # Must hedge/close within 30 seconds

# STOP-002: Stop loss retry configuration
STOP_LOSS_MAX_RETRIES = 5
STOP_LOSS_RETRY_DELAY_SECONDS = 2

# CONN-002: Circuit breaker settings (matching Iron Fly)
MAX_CONSECUTIVE_FAILURES = 5
SLIDING_WINDOW_SIZE = 10
SLIDING_WINDOW_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_COOLDOWN_MINUTES = 5

# ORDER-006: Wide bid-ask spread thresholds
MAX_BID_ASK_SPREAD_PERCENT_WARNING = 50  # Log warning
MAX_BID_ASK_SPREAD_PERCENT_SKIP = 100  # Skip entry

# MKT-006: VIX filter
DEFAULT_MAX_VIX_ENTRY = 25  # Skip remaining entries if VIX > this

# POS-007: Maximum positions check
MAX_POSITIONS_PER_DAY = 24  # 6 ICs x 4 legs

# DATA-001: Stale data threshold
MAX_DATA_STALENESS_SECONDS = 30

# TIME-001: Clock skew tolerance
MAX_CLOCK_SKEW_SECONDS = 5


# =============================================================================
# ENTRY SCHEDULE
# =============================================================================

DEFAULT_ENTRY_TIMES = [
    dt_time(10, 0),   # Entry 1: 10:00 AM ET
    dt_time(10, 30),  # Entry 2: 10:30 AM ET
    dt_time(11, 0),   # Entry 3: 11:00 AM ET
    dt_time(11, 30),  # Entry 4: 11:30 AM ET
    dt_time(12, 0),   # Entry 5: 12:00 PM ET
    dt_time(12, 30),  # Entry 6: 12:30 PM ET
]

# TIME-003: Early close day truncated schedule (1:00 PM close)
EARLY_CLOSE_ENTRY_TIMES = [
    dt_time(10, 0),   # Entry 1
    dt_time(10, 30),  # Entry 2
    # Skip 11:00+ entries to allow time for theta decay
]


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

class MEICState(Enum):
    """
    States of the MEIC strategy state machine.

    State transitions:
    IDLE -> WAITING_FIRST_ENTRY (9:30 AM)
    WAITING_FIRST_ENTRY -> ENTRY_IN_PROGRESS (10:00 AM)
    ENTRY_IN_PROGRESS -> MONITORING (after entry completes)
    MONITORING -> ENTRY_IN_PROGRESS (next scheduled entry time)
    MONITORING -> STOP_TRIGGERED (price hits stop level)
    MONITORING -> DAILY_COMPLETE (all entries done, end of day)
    STOP_TRIGGERED -> MONITORING (stop processed)
    Any -> CIRCUIT_BREAKER (too many failures)
    Any -> HALTED (critical intervention required)
    """
    IDLE = "Idle"                           # No position, waiting for market open
    WAITING_FIRST_ENTRY = "WaitingFirstEntry"  # Market open, waiting for 10:00 AM
    ENTRY_IN_PROGRESS = "EntryInProgress"   # Currently placing an IC entry
    MONITORING = "Monitoring"               # Active ICs, watching for stops
    STOP_TRIGGERED = "StopTriggered"        # Processing a stop loss
    DAILY_COMPLETE = "DailyComplete"        # All done for today
    CIRCUIT_BREAKER = "CircuitBreaker"      # Too many failures, cooling down
    HALTED = "Halted"                       # Critical error, manual intervention required


@dataclass
class IronCondorEntry:
    """
    Represents a single iron condor entry (4 legs).

    Each MEIC day has up to 6 of these (one per entry time).

    Structure:
    - Short Call + Long Call = Call Spread (credit)
    - Short Put + Long Put = Put Spread (credit)
    """
    entry_number: int  # 1-6
    entry_time: Optional[datetime] = None

    # Strikes
    short_call_strike: float = 0.0
    long_call_strike: float = 0.0
    short_put_strike: float = 0.0
    long_put_strike: float = 0.0

    # Position IDs (from Saxo after fill)
    short_call_position_id: Optional[str] = None
    long_call_position_id: Optional[str] = None
    short_put_position_id: Optional[str] = None
    long_put_position_id: Optional[str] = None

    # UICs (for price streaming)
    short_call_uic: Optional[int] = None
    long_call_uic: Optional[int] = None
    short_put_uic: Optional[int] = None
    long_put_uic: Optional[int] = None

    # Credits received
    call_spread_credit: float = 0.0  # Credit from selling call spread
    put_spread_credit: float = 0.0   # Credit from selling put spread

    # Stop levels (calculated after entry)
    call_side_stop: float = 0.0  # Stop loss for call spread
    put_side_stop: float = 0.0   # Stop loss for put spread

    # Current option prices (for P&L calculation)
    short_call_price: float = 0.0
    long_call_price: float = 0.0
    short_put_price: float = 0.0
    long_put_price: float = 0.0

    # Status tracking
    is_complete: bool = False  # All 4 legs filled
    call_side_stopped: bool = False  # Call spread was stopped out
    put_side_stopped: bool = False   # Put spread was stopped out
    strategy_id: str = ""  # For Position Registry tracking

    @property
    def total_credit(self) -> float:
        """Total credit received from both spreads."""
        return self.call_spread_credit + self.put_spread_credit

    @property
    def spread_width(self) -> float:
        """Width of spreads (both should be equal)."""
        if self.long_call_strike and self.short_call_strike:
            return self.long_call_strike - self.short_call_strike
        return 0.0

    @property
    def call_spread_value(self) -> float:
        """Current value (cost to close) of call spread."""
        # Buy back short, sell long
        return (self.short_call_price - self.long_call_price) * 100

    @property
    def put_spread_value(self) -> float:
        """Current value (cost to close) of put spread."""
        return (self.short_put_price - self.long_put_price) * 100

    @property
    def unrealized_pnl(self) -> float:
        """
        Current unrealized P&L for this IC.

        Profit = Credit received - Cost to close
        """
        if self.call_side_stopped and self.put_side_stopped:
            # Both stopped - return realized loss
            return -(self.call_side_stop + self.put_side_stop) + self.total_credit
        elif self.call_side_stopped:
            # Call stopped, put still open
            loss_on_call = self.call_side_stop
            return self.put_spread_credit - self.put_spread_value - loss_on_call
        elif self.put_side_stopped:
            # Put stopped, call still open
            loss_on_put = self.put_side_stop
            return self.call_spread_credit - self.call_spread_value - loss_on_put
        else:
            # Both sides still open
            return self.total_credit - self.call_spread_value - self.put_spread_value

    @property
    def all_position_ids(self) -> List[str]:
        """Get all position IDs for this IC (for registry cleanup)."""
        ids = []
        if self.short_call_position_id:
            ids.append(self.short_call_position_id)
        if self.long_call_position_id:
            ids.append(self.long_call_position_id)
        if self.short_put_position_id:
            ids.append(self.short_put_position_id)
        if self.long_put_position_id:
            ids.append(self.long_put_position_id)
        return ids


@dataclass
class MEICDailyState:
    """
    Tracks all state for a single MEIC trading day.

    Persisted to disk for crash recovery (POS-001).
    """
    date: str = ""  # YYYY-MM-DD
    entries: List[IronCondorEntry] = field(default_factory=list)
    entries_completed: int = 0
    entries_failed: int = 0
    entries_skipped: int = 0  # Skipped due to VIX, margin, etc.

    # Aggregate P&L
    total_credit_received: float = 0.0
    total_realized_pnl: float = 0.0

    # Stop tracking
    call_stops_triggered: int = 0
    put_stops_triggered: int = 0
    double_stops: int = 0  # Both sides stopped on same IC

    # Circuit breaker
    circuit_breaker_opens: int = 0

    @property
    def total_stops(self) -> int:
        """Total stop losses triggered."""
        return self.call_stops_triggered + self.put_stops_triggered

    @property
    def active_entries(self) -> List[IronCondorEntry]:
        """Get entries that have open positions."""
        return [e for e in self.entries if e.is_complete and not (e.call_side_stopped and e.put_side_stopped)]


# =============================================================================
# MARKET DATA
# =============================================================================

@dataclass
class MarketData:
    """Tracks market data with staleness detection."""
    spx_price: float = 0.0
    vix: float = 0.0
    last_spx_update: Optional[datetime] = None
    last_vix_update: Optional[datetime] = None

    def update_spx(self, price: float):
        """Update SPX price with timestamp."""
        if price > 0:
            self.spx_price = price
            self.last_spx_update = get_us_market_time()

    def update_vix(self, vix: float):
        """Update VIX with timestamp."""
        if vix > 0:
            self.vix = vix
            self.last_vix_update = get_us_market_time()

    def is_spx_stale(self, max_age: int = MAX_DATA_STALENESS_SECONDS) -> bool:
        """Check if SPX data is stale."""
        if not self.last_spx_update:
            return True
        age = (get_us_market_time() - self.last_spx_update).total_seconds()
        return age > max_age

    def is_vix_stale(self, max_age: int = MAX_DATA_STALENESS_SECONDS) -> bool:
        """Check if VIX data is stale."""
        if not self.last_vix_update:
            return True
        age = (get_us_market_time() - self.last_vix_update).total_seconds()
        return age > max_age


# =============================================================================
# MAIN STRATEGY CLASS
# =============================================================================

class MEICStrategy:
    """
    MEIC (Multiple Entry Iron Condors) Strategy Implementation.

    Implements Tammy Chambless's strategy with:
    - 6 scheduled iron condor entries per day
    - Per-side stop losses equal to total credit
    - MEIC+ modification for better breakeven days
    - Position Registry integration for multi-bot support

    Key Features:
    - Safe partial fill handling (never leave naked shorts)
    - Automatic orphan cleanup on restart
    - Independent stop monitoring per IC
    - Comprehensive edge case handling (75 cases analyzed)
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
        Initialize the MEIC strategy.

        Args:
            saxo_client: Authenticated Saxo API client
            config: Strategy configuration dictionary
            logger_service: Trade logging service
            dry_run: If True, simulate trades without placing real orders
            alert_service: Optional AlertService for SMS/email notifications
        """
        self.client = saxo_client
        self.config = config
        self.trade_logger = logger_service
        self.dry_run = dry_run

        # Alert service
        if alert_service:
            self.alert_service = alert_service
        else:
            self.alert_service = AlertService(config, "MEIC")

        # Position Registry for multi-bot isolation
        self.registry = PositionRegistry(REGISTRY_FILE)

        # Strategy configuration
        self.strategy_config = config.get("strategy", {})

        # Underlying (SPX via US500.I CFD)
        self.underlying_symbol = self.strategy_config.get("underlying_symbol", "US500.I")
        self.underlying_uic = self.strategy_config.get("underlying_uic", 4913)

        # Options (SPXW)
        self.option_root_uic = self.strategy_config.get("option_root_uic", 128)

        # VIX for filtering
        self.vix_uic = self.strategy_config.get("vix_spot_uic", 10606)

        # Entry parameters
        self._parse_entry_times()
        self.spread_width = self.strategy_config.get("spread_width", 50)
        self.min_credit_per_side = self.strategy_config.get("min_credit_per_side", 1.00)
        self.max_credit_per_side = self.strategy_config.get("max_credit_per_side", 1.75)
        self.target_delta = self.strategy_config.get("target_delta", 8)
        self.min_delta = self.strategy_config.get("min_delta", 5)
        self.max_delta = self.strategy_config.get("max_delta", 15)

        # MEIC+ modification
        self.meic_plus_enabled = self.strategy_config.get("meic_plus_enabled", True)
        self.meic_plus_reduction = self.strategy_config.get("meic_plus_reduction", 0.10)

        # Risk parameters
        self.max_daily_loss_percent = self.strategy_config.get("max_daily_loss_percent", 2.0)
        self.max_vix_entry = self.strategy_config.get("max_vix_entry", DEFAULT_MAX_VIX_ENTRY)
        self.contracts_per_entry = self.strategy_config.get("contracts_per_entry", 1)

        # State
        self.state = MEICState.IDLE
        self.daily_state = MEICDailyState()
        self.market_data = MarketData()

        # For backwards compatibility
        self.current_price = 0.0
        self.current_vix = 0.0

        # Entry tracking
        self._next_entry_index = 0  # Which entry time we're waiting for (0-5)
        self._current_entry: Optional[IronCondorEntry] = None
        self._entry_in_progress = False

        # Safety tracking
        self._consecutive_failures = 0
        self._circuit_breaker_open = False
        self._circuit_breaker_reason = ""
        self._circuit_breaker_opened_at: Optional[datetime] = None
        self._api_results_window: Deque[bool] = deque(maxlen=SLIDING_WINDOW_SIZE)

        # Critical intervention flag (ORDER-004)
        self._critical_intervention_required = False
        self._critical_intervention_reason = ""

        # Daily summary tracking
        self._daily_summary_sent = False

        # Initialize metrics
        self.cumulative_metrics = self._load_cumulative_metrics()

        logger.info(f"MEICStrategy initialized - State: {self.state.value}")
        logger.info(f"  Underlying: {self.underlying_symbol} (UIC: {self.underlying_uic})")
        logger.info(f"  Entry times: {[t.strftime('%H:%M') for t in self.entry_times]}")
        logger.info(f"  Spread width: {self.spread_width} points")
        logger.info(f"  MEIC+ enabled: {self.meic_plus_enabled}")
        logger.info(f"  Position Registry: {REGISTRY_FILE}")

        # Check for FOMC day
        if is_fomc_announcement_day():
            logger.warning("TODAY IS FOMC ANNOUNCEMENT DAY - No entries will be placed")

    def _parse_entry_times(self):
        """Parse entry times from config or use defaults."""
        entry_time_strs = self.strategy_config.get("entry_times", None)

        if entry_time_strs:
            self.entry_times = []
            for time_str in entry_time_strs:
                parts = time_str.split(":")
                self.entry_times.append(dt_time(int(parts[0]), int(parts[1])))
        else:
            self.entry_times = DEFAULT_ENTRY_TIMES.copy()

        # TIME-003: Check for early close day
        # TODO: Implement early close day detection

    # =========================================================================
    # MAIN LOOP - Called by main.py every few seconds
    # =========================================================================

    def run_strategy_check(self) -> str:
        """
        Main strategy loop - called periodically by main.py.

        Returns:
            str: Description of action taken (for logging)
        """
        # Check critical intervention first
        if self._critical_intervention_required:
            return f"HALTED: {self._critical_intervention_reason}"

        # Check circuit breaker
        if self._circuit_breaker_open:
            if self._check_circuit_breaker_cooldown():
                self._close_circuit_breaker()
            else:
                return f"Circuit breaker open: {self._circuit_breaker_reason}"

        # Update market data
        self._update_market_data()

        # MKT-008: Skip all trading on FOMC days
        if is_fomc_announcement_day():
            if self.state != MEICState.DAILY_COMPLETE:
                self.state = MEICState.DAILY_COMPLETE
                logger.info("FOMC announcement day - skipping all entries")
            return "FOMC day - no trading"

        # State machine
        if self.state == MEICState.IDLE:
            return self._handle_idle_state()

        elif self.state == MEICState.WAITING_FIRST_ENTRY:
            return self._handle_waiting_first_entry()

        elif self.state == MEICState.ENTRY_IN_PROGRESS:
            return self._handle_entry_in_progress()

        elif self.state == MEICState.MONITORING:
            return self._handle_monitoring()

        elif self.state == MEICState.STOP_TRIGGERED:
            return self._handle_stop_triggered()

        elif self.state == MEICState.DAILY_COMPLETE:
            return self._handle_daily_complete()

        else:
            return f"Unknown state: {self.state.value}"

    # =========================================================================
    # STATE HANDLERS
    # =========================================================================

    def _handle_idle_state(self) -> str:
        """
        Handle IDLE state - waiting for market to open.

        Transitions to WAITING_FIRST_ENTRY when market opens.
        """
        if is_market_open():
            # Reconcile positions on startup
            self._reconcile_positions()

            # Reset daily state if new day
            today = get_us_market_time().strftime("%Y-%m-%d")
            if self.daily_state.date != today:
                self._reset_for_new_day()

            self.state = MEICState.WAITING_FIRST_ENTRY
            logger.info("Market open - transitioning to WAITING_FIRST_ENTRY")
            return "Market open, waiting for first entry time"

        return "Waiting for market open"

    def _handle_waiting_first_entry(self) -> str:
        """
        Handle WAITING_FIRST_ENTRY state - waiting for 10:00 AM.

        Transitions to ENTRY_IN_PROGRESS when first entry time arrives.
        """
        now = get_us_market_time()

        # Check if we should attempt next entry
        if self._should_attempt_entry(now):
            return self._initiate_entry()

        # Calculate time until next entry
        next_entry = self._get_next_entry_time()
        if next_entry:
            minutes_until = self._minutes_until(next_entry)
            return f"Waiting for Entry #{self._next_entry_index + 1} at {next_entry.strftime('%H:%M')} ({minutes_until:.0f}m)"

        return "Waiting for entry window"

    def _handle_entry_in_progress(self) -> str:
        """
        Handle ENTRY_IN_PROGRESS state - currently placing an IC.

        This is a transient state during order execution.
        """
        if self._entry_in_progress:
            return "Entry in progress..."

        # Entry completed or failed, transition to appropriate state
        if self.daily_state.active_entries:
            self.state = MEICState.MONITORING
            return "Entry complete, monitoring positions"
        elif self._next_entry_index < len(self.entry_times):
            self.state = MEICState.WAITING_FIRST_ENTRY
            return "Entry failed, waiting for next entry time"
        else:
            self.state = MEICState.DAILY_COMPLETE
            return "All entries complete or failed"

    def _handle_monitoring(self) -> str:
        """
        Handle MONITORING state - watching positions for stop losses.

        Also checks for next scheduled entry time.
        """
        now = get_us_market_time()

        # Check for stop losses on all active entries
        stop_action = self._check_stop_losses()
        if stop_action:
            return stop_action

        # MULTI-005: Check daily loss limit before new entry
        if self._is_daily_loss_limit_reached():
            logger.warning("Daily loss limit reached - skipping remaining entries")
            self.state = MEICState.DAILY_COMPLETE
            return "Daily loss limit reached - done for today"

        # MKT-006: Check VIX before new entry
        if self.current_vix > self.max_vix_entry:
            logger.warning(f"VIX {self.current_vix:.1f} > {self.max_vix_entry} - skipping remaining entries")
            # Don't stop monitoring existing positions
            if self._next_entry_index < len(self.entry_times):
                self.daily_state.entries_skipped += (len(self.entry_times) - self._next_entry_index)
                self._next_entry_index = len(self.entry_times)  # Skip remaining
                return f"VIX too high ({self.current_vix:.1f}) - skipping remaining entries"

        # Check if we should attempt next entry
        if self._should_attempt_entry(now):
            return self._initiate_entry()

        # Continue monitoring
        active_count = len(self.daily_state.active_entries)
        next_entry = self._get_next_entry_time()

        if next_entry:
            minutes_until = self._minutes_until(next_entry)
            return f"Monitoring {active_count} ICs, Entry #{self._next_entry_index + 1} in {minutes_until:.0f}m"
        else:
            return f"Monitoring {active_count} ICs - all entries complete"

    def _handle_stop_triggered(self) -> str:
        """
        Handle STOP_TRIGGERED state - processing a stop loss.

        This is a transient state during stop order execution.
        """
        # The stop is processed in _check_stop_losses() and _execute_stop_loss()
        # This handler just ensures we transition back to MONITORING
        self.state = MEICState.MONITORING
        return "Stop processed, resuming monitoring"

    def _handle_daily_complete(self) -> str:
        """
        Handle DAILY_COMPLETE state - all trading done for today.

        Waits for market close, then sends daily summary.
        """
        if not is_market_open():
            if not self._daily_summary_sent:
                self._send_daily_summary()
                self._daily_summary_sent = True
            return "Market closed - day complete"

        # Continue monitoring existing positions until expiration
        active_count = len(self.daily_state.active_entries)
        if active_count > 0:
            stop_action = self._check_stop_losses()
            if stop_action:
                return stop_action
            return f"Daily entries complete, monitoring {active_count} ICs until expiration"

        return "Daily trading complete, no active positions"

    # =========================================================================
    # ENTRY LOGIC
    # =========================================================================

    def _should_attempt_entry(self, now: datetime) -> bool:
        """
        Check if we should attempt an entry now.

        Args:
            now: Current time (US Eastern)

        Returns:
            True if we should attempt entry
        """
        # No more entries scheduled
        if self._next_entry_index >= len(self.entry_times):
            return False

        # Get scheduled entry time
        scheduled_time = self.entry_times[self._next_entry_index]
        scheduled_datetime = now.replace(
            hour=scheduled_time.hour,
            minute=scheduled_time.minute,
            second=0,
            microsecond=0
        )

        # Check if we're within the entry window
        window_end = scheduled_datetime + timedelta(minutes=ENTRY_WINDOW_MINUTES)

        return scheduled_datetime <= now <= window_end

    def _get_next_entry_time(self) -> Optional[dt_time]:
        """Get the next scheduled entry time, or None if all done."""
        if self._next_entry_index >= len(self.entry_times):
            return None
        return self.entry_times[self._next_entry_index]

    def _minutes_until(self, target_time: dt_time) -> float:
        """Calculate minutes until a target time (today)."""
        now = get_us_market_time()
        target = now.replace(
            hour=target_time.hour,
            minute=target_time.minute,
            second=0,
            microsecond=0
        )
        if target < now:
            return 0.0
        return (target - now).total_seconds() / 60

    def _initiate_entry(self) -> str:
        """
        Initiate an iron condor entry.

        Returns:
            str: Description of action taken
        """
        entry_num = self._next_entry_index + 1
        logger.info(f"Initiating Entry #{entry_num} of {len(self.entry_times)}")

        self._entry_in_progress = True
        self.state = MEICState.ENTRY_IN_PROGRESS

        try:
            # Create entry object
            entry = IronCondorEntry(entry_number=entry_num)
            entry.strategy_id = f"meic_{get_us_market_time().strftime('%Y%m%d')}_entry{entry_num}"
            self._current_entry = entry

            # Calculate strikes
            if not self._calculate_strikes(entry):
                raise Exception("Failed to calculate strikes")

            if self.dry_run:
                # Simulate entry
                success = self._simulate_entry(entry)
            else:
                # Execute real entry
                success = self._execute_entry(entry)

            if success:
                entry.entry_time = get_us_market_time()
                entry.is_complete = True
                self.daily_state.entries.append(entry)
                self.daily_state.entries_completed += 1
                self.daily_state.total_credit_received += entry.total_credit

                # Calculate stop losses
                self._calculate_stop_levels(entry)

                # Log to Google Sheets
                self._log_entry(entry)

                # Send alert
                self.alert_service.position_opened(
                    description=f"MEIC Entry #{entry_num}",
                    entry_price=self.current_price,
                    strike_info=f"Call: {entry.short_call_strike}/{entry.long_call_strike}, "
                               f"Put: {entry.short_put_strike}/{entry.long_put_strike}",
                    credit=entry.total_credit
                )

                self._record_api_result(True)
                self._next_entry_index += 1
                return f"Entry #{entry_num} complete - Credit: ${entry.total_credit:.2f}"
            else:
                self.daily_state.entries_failed += 1
                self._record_api_result(False, f"Entry #{entry_num} failed")
                self._next_entry_index += 1  # Move on even if failed
                return f"Entry #{entry_num} failed"

        except Exception as e:
            logger.error(f"Entry #{entry_num} error: {e}")
            self.daily_state.entries_failed += 1
            self._record_api_result(False, str(e))
            self._next_entry_index += 1
            return f"Entry #{entry_num} error: {e}"

        finally:
            self._entry_in_progress = False
            self._current_entry = None

            # Transition state
            if self.daily_state.active_entries:
                self.state = MEICState.MONITORING
            elif self._next_entry_index < len(self.entry_times):
                self.state = MEICState.WAITING_FIRST_ENTRY
            else:
                self.state = MEICState.DAILY_COMPLETE

    def _calculate_strikes(self, entry: IronCondorEntry) -> bool:
        """
        Calculate iron condor strikes based on current SPX price.

        Uses delta targeting and spread width from config.

        Args:
            entry: IronCondorEntry to populate with strikes

        Returns:
            True if strikes calculated successfully
        """
        spx = self.current_price
        if spx <= 0:
            logger.error("Cannot calculate strikes - no SPX price")
            return False

        # Round SPX to nearest 5 (SPX strikes are 5-point increments)
        rounded_spx = round(spx / 5) * 5

        # For ~8 delta, typically 40-60 points OTM at typical IV
        # This should be calibrated based on actual option chain data
        # For now, use a simple distance based on target delta
        # Higher delta = closer to ATM, lower delta = further OTM
        # ~10 delta is typically around 1% OTM (0DTE with normal IV)
        otm_distance = int(rounded_spx * 0.007)  # ~0.7% OTM as starting point
        otm_distance = round(otm_distance / 5) * 5  # Round to 5
        otm_distance = max(35, min(65, otm_distance))  # Clamp to 35-65

        # Call side (above current price)
        entry.short_call_strike = rounded_spx + otm_distance
        entry.long_call_strike = entry.short_call_strike + self.spread_width

        # Put side (below current price)
        entry.short_put_strike = rounded_spx - otm_distance
        entry.long_put_strike = entry.short_put_strike - self.spread_width

        logger.info(
            f"Strikes calculated for SPX {spx:.2f}: "
            f"Call {entry.short_call_strike}/{entry.long_call_strike}, "
            f"Put {entry.short_put_strike}/{entry.long_put_strike}"
        )

        return True

    def _calculate_stop_levels(self, entry: IronCondorEntry):
        """
        Calculate stop loss levels for an entry.

        MEIC Rule: Stop on each side = Total credit received
        MEIC+ Modification: Stop = Total credit - $0.10

        Args:
            entry: IronCondorEntry to calculate stops for
        """
        total_credit = entry.total_credit

        if self.meic_plus_enabled:
            # MEIC+ modification - smaller stop for breakeven days -> small wins
            # STOP-002: Don't apply if stop would be too tight
            if total_credit > 1.50:  # Only apply if credit > $1.50
                stop_level = total_credit - self.meic_plus_reduction
            else:
                stop_level = total_credit
                logger.info(f"MEIC+ not applied - credit ${total_credit:.2f} too small")
        else:
            stop_level = total_credit

        # Both sides get the same stop level
        entry.call_side_stop = stop_level
        entry.put_side_stop = stop_level

        logger.info(
            f"Stop levels set for Entry #{entry.entry_number}: "
            f"${stop_level:.2f} per side (credit: ${total_credit:.2f})"
        )

    def _simulate_entry(self, entry: IronCondorEntry) -> bool:
        """
        Simulate an iron condor entry (dry-run mode).

        Args:
            entry: IronCondorEntry to simulate

        Returns:
            True if simulation successful
        """
        # Simulate realistic credits based on spread width
        # Typically collect 2-3% of spread width as credit
        credit_ratio = 0.025  # 2.5% of spread width per side
        entry.call_spread_credit = self.spread_width * credit_ratio * 100
        entry.put_spread_credit = self.spread_width * credit_ratio * 100

        # Generate fake position IDs
        base_id = int(datetime.now().timestamp() * 1000)
        entry.short_call_position_id = f"DRY_{base_id}_SC"
        entry.long_call_position_id = f"DRY_{base_id}_LC"
        entry.short_put_position_id = f"DRY_{base_id}_SP"
        entry.long_put_position_id = f"DRY_{base_id}_LP"

        logger.info(
            f"[DRY RUN] Simulated Entry #{entry.entry_number}: "
            f"Credit ${entry.total_credit:.2f}"
        )

        return True

    def _execute_entry(self, entry: IronCondorEntry) -> bool:
        """
        Execute a real iron condor entry.

        ORDER-002 Critical: If any short fills without its hedge, we must
        immediately hedge or close the short to avoid naked exposure.

        Leg order (safest):
        1. Long Call (buy protection first)
        2. Long Put (buy protection)
        3. Short Call (sell, hedged by long call)
        4. Short Put (sell, hedged by long put)

        Args:
            entry: IronCondorEntry to execute

        Returns:
            True if all 4 legs filled successfully
        """
        today_str = get_us_market_time().strftime("%Y-%m-%d")

        # Get option expiry (today for 0DTE)
        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error("Could not determine today's expiry")
            return False

        filled_legs = []  # Track what we've filled for rollback

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
            filled_legs.append(("long_call", entry.long_call_position_id, entry.long_call_uic))
            self._register_position(entry, "long_call")

            # 2. Long Put
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
            filled_legs.append(("long_put", entry.long_put_position_id, entry.long_put_uic))
            self._register_position(entry, "long_put")

            # 3. Short Call (now we have the hedge)
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
            entry.call_spread_credit = short_call_result.get("credit", 0)
            filled_legs.append(("short_call", entry.short_call_position_id, entry.short_call_uic))
            self._register_position(entry, "short_call")

            # 4. Short Put
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
            entry.put_spread_credit = short_put_result.get("credit", 0)
            filled_legs.append(("short_put", entry.short_put_position_id, entry.short_put_uic))
            self._register_position(entry, "short_put")

            logger.info(
                f"Entry #{entry.entry_number} complete: "
                f"Call credit ${entry.call_spread_credit:.2f}, "
                f"Put credit ${entry.put_spread_credit:.2f}"
            )

            return True

        except Exception as e:
            logger.error(f"Entry failed at leg {len(filled_legs) + 1}: {e}")

            # ORDER-002: Critical - check for naked shorts
            has_naked_short = False
            naked_short_info = None

            for leg_name, pos_id, uic in filled_legs:
                if leg_name.startswith("short_"):
                    # We have a short filled
                    hedge_name = "long_" + leg_name[6:]  # short_call -> long_call
                    hedge_filled = any(l[0] == hedge_name for l in filled_legs)
                    if not hedge_filled:
                        has_naked_short = True
                        naked_short_info = (leg_name, pos_id, uic)
                        break

            if has_naked_short:
                logger.critical(f"NAKED SHORT DETECTED: {naked_short_info[0]}")
                self._handle_naked_short(naked_short_info)

            # Unwind filled legs
            self._unwind_partial_entry(filled_legs, entry)

            return False

    def _place_option_order(
        self,
        strike: float,
        put_call: str,
        buy_sell: BuySell,
        expiry: str,
        external_ref: str
    ) -> Optional[Dict]:
        """
        Place a single option order.

        Args:
            strike: Strike price
            put_call: "Call" or "Put"
            buy_sell: BuySell enum
            expiry: Expiry date string (YYYY-MM-DD)
            external_ref: External reference for tracking

        Returns:
            dict with order result including position_id, uic, credit/debit
            None if order failed
        """
        # Get option UIC from chain
        uic = self._get_option_uic(strike, put_call, expiry)
        if not uic:
            logger.error(f"Could not find UIC for {put_call} {strike} {expiry}")
            return None

        # Place market order
        result = self.client.place_order(
            uic=uic,
            asset_type="StockIndexOption",
            buy_sell=buy_sell,
            amount=self.contracts_per_entry,
            order_type=OrderType.MARKET,
            to_open_close="ToOpen",
            external_reference=external_ref
        )

        if not result:
            logger.error(f"Order failed for {put_call} {strike}")
            return None

        # Get position ID from activities
        # This may require waiting for fill confirmation
        position_id = self._get_position_id_from_order(result)
        if not position_id:
            logger.warning(f"Could not get position ID for {put_call} {strike}")

        # Get fill price for credit calculation
        fill_price = self._get_fill_price(result)

        return {
            "position_id": position_id,
            "uic": uic,
            "credit": fill_price * 100 if buy_sell == BuySell.SELL else 0,
            "debit": fill_price * 100 if buy_sell == BuySell.BUY else 0
        }

    def _get_option_uic(self, strike: float, put_call: str, expiry: str) -> Optional[int]:
        """
        Get option UIC from the option chain.

        Args:
            strike: Strike price
            put_call: "Call" or "Put"
            expiry: Expiry date (YYYY-MM-DD)

        Returns:
            Option UIC or None if not found
        """
        # Query option chain
        chain = self.client.get_option_chain(
            option_root_id=self.option_root_uic,
            expiry_date=expiry
        )

        if not chain:
            logger.error(f"Could not fetch option chain for {expiry}")
            return None

        # Search for matching strike and type
        for option in chain:
            opt_strike = option.get("Strike")
            opt_type = option.get("PutCall")

            if opt_strike == strike and opt_type == put_call:
                return option.get("Uic")

        logger.error(f"Strike {strike} {put_call} not found in chain")
        return None

    def _get_position_id_from_order(self, order_result: Dict) -> Optional[str]:
        """
        Get position ID from order result or activities.

        Args:
            order_result: Result from place_order()

        Returns:
            Position ID or None
        """
        # Check if directly in result
        if "PositionId" in order_result:
            return str(order_result["PositionId"])

        # Get from order ID via activities
        order_id = order_result.get("OrderId")
        if order_id:
            # Query activities endpoint
            activities = self.client.get_recent_activities(minutes=5)
            for activity in activities:
                if activity.get("OrderId") == order_id:
                    pos_id = activity.get("PositionId")
                    if pos_id:
                        return str(pos_id)

        return None

    def _get_fill_price(self, order_result: Dict) -> float:
        """Get fill price from order result."""
        # Try various field names
        for field in ["FilledPrice", "Price", "ExecutionPrice"]:
            if field in order_result:
                return float(order_result[field])

        # Fallback to getting from order details
        order_id = order_result.get("OrderId")
        if order_id:
            order_details = self.client.get_order(order_id)
            if order_details:
                return order_details.get("FilledPrice", 0)

        return 0

    def _get_todays_expiry(self) -> Optional[str]:
        """Get today's expiry date string for 0DTE options."""
        return get_us_market_time().strftime("%Y-%m-%d")

    def _register_position(self, entry: IronCondorEntry, leg_name: str):
        """
        Register a position leg with the Position Registry.

        Args:
            entry: IronCondorEntry containing the position
            leg_name: Which leg ("short_call", "long_call", "short_put", "long_put")
        """
        position_id = getattr(entry, f"{leg_name}_position_id")
        if not position_id:
            return

        strike = getattr(entry, f"{leg_name}_strike")

        self.registry.register(
            position_id=position_id,
            bot_name="MEIC",
            strategy_id=entry.strategy_id,
            metadata={
                "entry_number": entry.entry_number,
                "leg_type": leg_name,
                "strike": strike
            }
        )

    def _handle_naked_short(self, naked_info: Tuple[str, str, int]):
        """
        Handle a naked short position - CRITICAL SAFETY.

        Must either hedge or close within NAKED_POSITION_MAX_AGE_SECONDS.

        Args:
            naked_info: Tuple of (leg_name, position_id, uic)
        """
        leg_name, pos_id, uic = naked_info

        logger.critical(f"HANDLING NAKED SHORT: {leg_name} position {pos_id}")

        self.alert_service.send_alert(
            AlertType.CIRCUIT_BREAKER,
            AlertPriority.CRITICAL,
            f"NAKED SHORT: {leg_name} - closing immediately"
        )

        # Attempt to close the naked short
        try:
            result = self.client.close_position(pos_id)
            if result:
                logger.info(f"Closed naked short {pos_id}")
                self.registry.unregister(pos_id)
            else:
                logger.critical(f"FAILED to close naked short {pos_id}!")
                self._trigger_critical_intervention(f"Cannot close naked short {pos_id}")
        except Exception as e:
            logger.critical(f"Exception closing naked short: {e}")
            self._trigger_critical_intervention(f"Exception closing naked short: {e}")

    def _unwind_partial_entry(self, filled_legs: List[Tuple], entry: IronCondorEntry):
        """
        Unwind partially filled legs on entry failure.

        Args:
            filled_legs: List of (leg_name, position_id, uic) tuples
            entry: The entry being unwound
        """
        logger.warning(f"Unwinding {len(filled_legs)} partially filled legs")

        for leg_name, pos_id, uic in filled_legs:
            if pos_id:
                try:
                    self.client.close_position(pos_id)
                    self.registry.unregister(pos_id)
                    logger.info(f"Unwound {leg_name}: {pos_id}")
                except Exception as e:
                    logger.error(f"Failed to unwind {leg_name}: {e}")

    # =========================================================================
    # STOP LOSS MONITORING
    # =========================================================================

    def _check_stop_losses(self) -> Optional[str]:
        """
        Check all active entries for stop loss triggers.

        Returns:
            str describing stop action taken, or None
        """
        for entry in self.daily_state.active_entries:
            # Skip if both sides already stopped
            if entry.call_side_stopped and entry.put_side_stopped:
                continue

            # Update option prices
            self._update_entry_prices(entry)

            # Check call side stop
            if not entry.call_side_stopped:
                if entry.call_spread_value >= entry.call_side_stop:
                    return self._execute_stop_loss(entry, "call")

            # Check put side stop
            if not entry.put_side_stopped:
                if entry.put_spread_value >= entry.put_side_stop:
                    return self._execute_stop_loss(entry, "put")

        return None

    def _update_entry_prices(self, entry: IronCondorEntry):
        """
        Update option prices for an entry.

        Uses WebSocket cache or REST API fallback.
        """
        if self.dry_run:
            # Simulate price movement based on SPX movement
            self._simulate_entry_prices(entry)
            return

        # Get current option prices from cache or API
        if entry.short_call_uic:
            entry.short_call_price = self.client.get_cached_price(entry.short_call_uic) or 0
        if entry.long_call_uic:
            entry.long_call_price = self.client.get_cached_price(entry.long_call_uic) or 0
        if entry.short_put_uic:
            entry.short_put_price = self.client.get_cached_price(entry.short_put_uic) or 0
        if entry.long_put_uic:
            entry.long_put_price = self.client.get_cached_price(entry.long_put_uic) or 0

    def _simulate_entry_prices(self, entry: IronCondorEntry):
        """Simulate option prices in dry-run mode."""
        if not entry.entry_time:
            return

        # Calculate time decay factor (theta)
        hold_minutes = (get_us_market_time() - entry.entry_time).total_seconds() / 60
        decay_factor = 1 - (hold_minutes / 360)  # Assume ~6 hours to expiry
        decay_factor = max(0.1, decay_factor)  # Floor at 10%

        # Simulate prices decaying towards 0
        initial_short_price = entry.total_credit / 200  # Per contract
        entry.short_call_price = initial_short_price * decay_factor
        entry.short_put_price = initial_short_price * decay_factor
        entry.long_call_price = initial_short_price * decay_factor * 0.3  # Wings worth less
        entry.long_put_price = initial_short_price * decay_factor * 0.3

    def _execute_stop_loss(self, entry: IronCondorEntry, side: str) -> str:
        """
        Execute a stop loss for one side of an IC.

        Args:
            entry: IronCondorEntry with stop triggered
            side: "call" or "put"

        Returns:
            str describing action taken
        """
        logger.warning(f"STOP TRIGGERED: Entry #{entry.entry_number} {side} side")

        self.state = MEICState.STOP_TRIGGERED

        if side == "call":
            entry.call_side_stopped = True
            self.daily_state.call_stops_triggered += 1
            positions_to_close = [
                (entry.short_call_position_id, "short_call"),
                (entry.long_call_position_id, "long_call")
            ]
            stop_level = entry.call_side_stop
        else:
            entry.put_side_stopped = True
            self.daily_state.put_stops_triggered += 1
            positions_to_close = [
                (entry.short_put_position_id, "short_put"),
                (entry.long_put_position_id, "long_put")
            ]
            stop_level = entry.put_side_stop

        # Check for double stop
        if entry.call_side_stopped and entry.put_side_stopped:
            self.daily_state.double_stops += 1
            logger.warning(f"DOUBLE STOP on Entry #{entry.entry_number}")

        if self.dry_run:
            logger.info(f"[DRY RUN] Would close {side} side of Entry #{entry.entry_number}")
        else:
            # Close positions with retry logic
            for pos_id, leg_name in positions_to_close:
                if pos_id:
                    self._close_position_with_retry(pos_id, leg_name)

        # Update realized P&L
        self.daily_state.total_realized_pnl -= stop_level

        # Send alert
        self.alert_service.stop_loss_triggered(
            description=f"MEIC Entry #{entry.entry_number} {side.upper()} side stopped",
            exit_price=self.current_price,
            loss=stop_level,
            reason=f"{side} spread value reached stop level"
        )

        return f"Stop loss executed: Entry #{entry.entry_number} {side} side (${stop_level:.2f})"

    def _close_position_with_retry(self, position_id: str, leg_name: str) -> bool:
        """
        Close a position with retry logic.

        Args:
            position_id: Saxo position ID
            leg_name: Name for logging

        Returns:
            True if closed successfully
        """
        for attempt in range(STOP_LOSS_MAX_RETRIES):
            try:
                result = self.client.close_position(position_id)
                if result:
                    self.registry.unregister(position_id)
                    logger.info(f"Closed {leg_name}: {position_id}")
                    return True
            except Exception as e:
                logger.error(f"Close {leg_name} attempt {attempt + 1} failed: {e}")

            if attempt < STOP_LOSS_MAX_RETRIES - 1:
                time.sleep(STOP_LOSS_RETRY_DELAY_SECONDS)

        logger.critical(f"FAILED to close {leg_name} after {STOP_LOSS_MAX_RETRIES} attempts!")
        return False

    # =========================================================================
    # MARKET DATA
    # =========================================================================

    def _update_market_data(self):
        """Update SPX and VIX prices from cache or API."""
        # US500.I is a CFD that tracks SPX - use CfdOnIndex asset type
        quote = self.client.get_quote(self.underlying_uic, asset_type="CfdOnIndex")
        if quote:
            price = self._extract_price(quote)
            if price:
                self.market_data.update_spx(price)
                self.current_price = price

        # VIX - use get_vix_price which has Yahoo Finance fallback
        vix = self.client.get_vix_price(self.vix_uic)
        if vix:
            self.market_data.update_vix(vix)
            self.current_vix = vix

    def _extract_price(self, quote: Dict) -> Optional[float]:
        """Extract price from quote response."""
        # Try mid price first
        bid = quote.get("Quote", {}).get("Bid")
        ask = quote.get("Quote", {}).get("Ask")
        if bid and ask:
            return (bid + ask) / 2

        # Fallback to last traded
        last = quote.get("Quote", {}).get("LastTraded")
        if last:
            return last

        return None

    def handle_price_update(self, uic: int, data: Dict):
        """
        Handle real-time price updates from WebSocket.

        Called by main.py's price callback.
        """
        if uic == self.underlying_uic:
            price = self._extract_price_from_ws(data)
            if price:
                self.market_data.update_spx(price)
                self.current_price = price

        elif uic == self.vix_uic:
            vix = data.get("LastTraded") or data.get("PriceInfoDetails", {}).get("LastTraded")
            if vix:
                self.market_data.update_vix(vix)
                self.current_vix = vix

    def _extract_price_from_ws(self, data: Dict) -> Optional[float]:
        """Extract price from WebSocket message."""
        # Try Quote block first
        quote = data.get("Quote", {})
        bid = quote.get("Bid")
        ask = quote.get("Ask")
        if bid and ask:
            return (bid + ask) / 2

        # Fallback
        mid = quote.get("Mid")
        if mid:
            return mid

        return quote.get("LastTraded")

    # =========================================================================
    # CIRCUIT BREAKER
    # =========================================================================

    def _record_api_result(self, success: bool, reason: str = ""):
        """Record API result for circuit breaker tracking."""
        self._api_results_window.append(success)

        if not success:
            self._consecutive_failures += 1
            logger.warning(f"API failure #{self._consecutive_failures}: {reason}")

            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self._open_circuit_breaker(f"Consecutive failures: {reason}")
                return
        else:
            self._consecutive_failures = 0

        # Check sliding window
        if len(self._api_results_window) >= SLIDING_WINDOW_SIZE:
            failures = sum(1 for r in self._api_results_window if not r)
            if failures >= SLIDING_WINDOW_FAILURE_THRESHOLD:
                self._open_circuit_breaker(
                    f"Intermittent failures: {failures}/{SLIDING_WINDOW_SIZE}"
                )

    def _open_circuit_breaker(self, reason: str):
        """Open the circuit breaker."""
        if self._circuit_breaker_open:
            return

        self._circuit_breaker_open = True
        self._circuit_breaker_reason = reason
        self._circuit_breaker_opened_at = get_us_market_time()
        self.daily_state.circuit_breaker_opens += 1

        logger.critical(f"CIRCUIT BREAKER OPEN: {reason}")

        self.alert_service.circuit_breaker(reason, self._consecutive_failures)

        # CB-004: Check daily escalation
        if self.daily_state.circuit_breaker_opens >= 3:
            self._trigger_critical_intervention(
                f"Circuit breaker opened {self.daily_state.circuit_breaker_opens} times today"
            )

        self.state = MEICState.CIRCUIT_BREAKER

    def _close_circuit_breaker(self):
        """Close the circuit breaker after cooldown."""
        logger.info("Circuit breaker closed - resuming trading")
        self._circuit_breaker_open = False
        self._circuit_breaker_reason = ""
        self._consecutive_failures = 0
        self._api_results_window.clear()

        # Return to appropriate state
        if self.daily_state.active_entries:
            self.state = MEICState.MONITORING
        elif self._next_entry_index < len(self.entry_times):
            self.state = MEICState.WAITING_FIRST_ENTRY
        else:
            self.state = MEICState.DAILY_COMPLETE

    def _check_circuit_breaker_cooldown(self) -> bool:
        """Check if circuit breaker cooldown has elapsed."""
        if not self._circuit_breaker_opened_at:
            return True

        elapsed = (get_us_market_time() - self._circuit_breaker_opened_at).total_seconds()
        return elapsed >= (CIRCUIT_BREAKER_COOLDOWN_MINUTES * 60)

    def _trigger_critical_intervention(self, reason: str):
        """Trigger critical intervention - halt all trading."""
        self._critical_intervention_required = True
        self._critical_intervention_reason = reason
        self.state = MEICState.HALTED

        logger.critical(f"CRITICAL INTERVENTION REQUIRED: {reason}")

        self.alert_service.send_alert(
            AlertType.CIRCUIT_BREAKER,
            AlertPriority.CRITICAL,
            f"MEIC HALTED: {reason}"
        )

    # =========================================================================
    # DAILY LOSS CHECK
    # =========================================================================

    def _is_daily_loss_limit_reached(self) -> bool:
        """Check if daily loss limit has been reached."""
        # Get account value for percentage calculation
        account_info = self.client.get_account_info()
        if not account_info:
            return False

        account_value = account_info.get("TotalValue", 50000)  # Default 50K
        max_loss = account_value * (self.max_daily_loss_percent / 100)

        # Calculate current unrealized + realized loss
        unrealized = sum(e.unrealized_pnl for e in self.daily_state.active_entries)
        total_loss = -unrealized - self.daily_state.total_realized_pnl

        if total_loss >= max_loss:
            logger.warning(
                f"Daily loss limit reached: ${total_loss:.2f} >= ${max_loss:.2f} "
                f"({self.max_daily_loss_percent}% of ${account_value:.0f})"
            )
            return True

        return False

    # =========================================================================
    # POSITION RECONCILIATION
    # =========================================================================

    def _reconcile_positions(self):
        """
        Reconcile positions with registry on startup.

        Handles POS-001: Bot restart recovery
        Handles POS-003: Positions closed manually
        """
        logger.info("Reconciling positions with registry...")

        # Get all positions from Saxo
        all_positions = self.client.get_positions()
        valid_ids = {str(p.get("PositionBase", {}).get("PositionId")) for p in all_positions}

        # Clean up orphans
        orphans = self.registry.cleanup_orphans(valid_ids)
        if orphans:
            logger.warning(f"Cleaned up {len(orphans)} orphaned registrations")

        # Get MEIC positions
        my_position_ids = self.registry.get_positions("MEIC")

        if my_position_ids:
            logger.info(f"Found {len(my_position_ids)} MEIC positions in registry")
            # TODO: Recover daily state from persisted state file
        else:
            logger.info("No existing MEIC positions found")

    # =========================================================================
    # DAILY RESET
    # =========================================================================

    def _reset_for_new_day(self):
        """Reset state for a new trading day."""
        logger.info("Resetting for new trading day")

        self.daily_state = MEICDailyState()
        self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")

        self._next_entry_index = 0
        self._daily_summary_sent = False
        self._circuit_breaker_open = False
        self._consecutive_failures = 0
        self._api_results_window.clear()

        self.state = MEICState.IDLE

    # =========================================================================
    # LOGGING AND ALERTS
    # =========================================================================

    def _log_entry(self, entry: IronCondorEntry):
        """Log entry to Google Sheets."""
        try:
            self.trade_logger.log_trade({
                "timestamp": entry.entry_time.isoformat() if entry.entry_time else "",
                "action": f"MEIC Entry #{entry.entry_number}",
                "underlying_price": self.current_price,
                "short_call": entry.short_call_strike,
                "long_call": entry.long_call_strike,
                "short_put": entry.short_put_strike,
                "long_put": entry.long_put_strike,
                "call_credit": entry.call_spread_credit,
                "put_credit": entry.put_spread_credit,
                "total_credit": entry.total_credit,
                "call_stop": entry.call_side_stop,
                "put_stop": entry.put_side_stop
            })
        except Exception as e:
            logger.error(f"Failed to log entry: {e}")

    def _send_daily_summary(self):
        """Send daily summary alert."""
        summary = self.get_daily_summary()

        self.alert_service.daily_summary(
            trades_executed=summary["entries_completed"],
            total_pnl=summary["total_pnl"],
            total_premium=summary["total_credit"],
            additional_stats={
                "Entries Failed": summary["entries_failed"],
                "Entries Skipped": summary["entries_skipped"],
                "Call Stops": summary["call_stops"],
                "Put Stops": summary["put_stops"],
                "Double Stops": summary["double_stops"]
            }
        )

    def get_daily_summary(self) -> Dict:
        """Get daily trading summary."""
        # Calculate total P&L
        unrealized = sum(e.unrealized_pnl for e in self.daily_state.active_entries)
        total_pnl = self.daily_state.total_realized_pnl + unrealized

        return {
            "date": self.daily_state.date,
            "entries_completed": self.daily_state.entries_completed,
            "entries_failed": self.daily_state.entries_failed,
            "entries_skipped": self.daily_state.entries_skipped,
            "total_credit": self.daily_state.total_credit_received,
            "total_pnl": total_pnl,
            "realized_pnl": self.daily_state.total_realized_pnl,
            "unrealized_pnl": unrealized,
            "call_stops": self.daily_state.call_stops_triggered,
            "put_stops": self.daily_state.put_stops_triggered,
            "double_stops": self.daily_state.double_stops,
            "circuit_breaker_opens": self.daily_state.circuit_breaker_opens
        }

    def get_status_summary(self) -> Dict:
        """Get current status summary for heartbeat logging."""
        active_entries = len(self.daily_state.active_entries)
        unrealized = sum(e.unrealized_pnl for e in self.daily_state.active_entries)

        return {
            "state": self.state.value,
            "underlying_price": self.current_price,
            "vix": self.current_vix,
            "entries_completed": self.daily_state.entries_completed,
            "entries_failed": self.daily_state.entries_failed,
            "active_entries": active_entries,
            "next_entry": self._next_entry_index + 1 if self._next_entry_index < len(self.entry_times) else None,
            "total_credit": self.daily_state.total_credit_received,
            "realized_pnl": self.daily_state.total_realized_pnl,
            "unrealized_pnl": unrealized,
            "total_stops": self.daily_state.call_stops_triggered + self.daily_state.put_stops_triggered,
            "circuit_breaker_open": self._circuit_breaker_open
        }

    # =========================================================================
    # METRICS PERSISTENCE
    # =========================================================================

    def _load_cumulative_metrics(self) -> Dict:
        """Load cumulative metrics from disk."""
        try:
            if os.path.exists(METRICS_FILE):
                with open(METRICS_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load cumulative metrics: {e}")

        return {
            "cumulative_pnl": 0.0,
            "total_trades": 0,
            "total_entries": 0,
            "winning_days": 0,
            "losing_days": 0,
            "total_credit_collected": 0.0,
            "total_stops": 0,
            "double_stops": 0,
            "last_updated": None
        }

    def _save_cumulative_metrics(self):
        """Save cumulative metrics to disk."""
        try:
            os.makedirs(os.path.dirname(METRICS_FILE), exist_ok=True)
            self.cumulative_metrics["last_updated"] = get_us_market_time().isoformat()
            with open(METRICS_FILE, 'w') as f:
                json.dump(self.cumulative_metrics, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cumulative metrics: {e}")

    # =========================================================================
    # ACCOUNT & DASHBOARD LOGGING (matching Iron Fly interface)
    # =========================================================================

    def log_account_summary(self):
        """Log account summary to Google Sheets dashboard."""
        try:
            account_info = self.client.get_account_info()
            if account_info:
                self.trade_logger.log_account_summary({
                    "timestamp": get_us_market_time().isoformat(),
                    "total_value": account_info.get("TotalValue"),
                    "cash_balance": account_info.get("CashBalance"),
                    "margin_used": account_info.get("MarginUsed"),
                    "unrealized_pnl": sum(e.unrealized_pnl for e in self.daily_state.active_entries)
                })
        except Exception as e:
            logger.error(f"Failed to log account summary: {e}")

    def log_performance_metrics(self):
        """Log performance metrics to Google Sheets."""
        try:
            summary = self.get_daily_summary()
            self.trade_logger.log_performance_metrics(
                period="Intraday",
                metrics={
                    "timestamp": get_us_market_time().isoformat(),
                    "daily_pnl": summary["total_pnl"],
                    "entries_completed": summary["entries_completed"],
                    "total_stops": summary["call_stops"] + summary["put_stops"],
                    "cumulative_pnl": self.cumulative_metrics.get("cumulative_pnl", 0) + summary["total_pnl"]
                },
                saxo_client=self.client
            )
        except Exception as e:
            logger.error(f"Failed to log performance metrics: {e}")

    def log_daily_summary(self):
        """Log and send daily summary at end of day."""
        self._send_daily_summary()

        # Update cumulative metrics
        summary = self.get_daily_summary()
        self.cumulative_metrics["cumulative_pnl"] += summary["total_pnl"]
        self.cumulative_metrics["total_entries"] += summary["entries_completed"]
        self.cumulative_metrics["total_credit_collected"] += summary["total_credit"]
        self.cumulative_metrics["total_stops"] += summary["call_stops"] + summary["put_stops"]
        self.cumulative_metrics["double_stops"] += summary["double_stops"]

        if summary["total_pnl"] >= 0:
            self.cumulative_metrics["winning_days"] += 1
        else:
            self.cumulative_metrics["losing_days"] += 1

        self._save_cumulative_metrics()

    def update_market_data(self):
        """Public method to update market data (called by main.py)."""
        self._update_market_data()
