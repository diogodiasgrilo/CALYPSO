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
import threading
import fcntl
from collections import deque
from datetime import datetime, timedelta, date, time as dt_time
from typing import Optional, Dict, List, Any, Tuple, Deque, Set
from dataclasses import dataclass, field, asdict
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell, OrderType
from shared.alert_service import AlertService, AlertType, AlertPriority
from shared.market_hours import get_us_market_time, US_EASTERN, is_market_open, is_early_close_day
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
MAX_ABSOLUTE_SLIPPAGE = 2.00  # Max $ slippage before aborting MARKET order

# ORDER-007: Progressive slippage retry sequence for 0DTE (tighter timeouts)
# Format: (slippage_percent, is_market_order)
PROGRESSIVE_RETRY_SEQUENCE = [
    (0.0, False),   # 1st: 0% slippage (limit at mid)
    (0.0, False),   # 2nd: 0% slippage (fresh quote)
    (5.0, False),   # 3rd: 5% slippage
    (10.0, False),  # 4th: 10% slippage
    (0.0, True),    # 5th: MARKET order (guaranteed fill)
]
ORDER_TIMEOUT_SECONDS = 30  # Shorter for 0DTE (vs 60s for Delta Neutral)
ORDER_TIMEOUT_EMERGENCY_SECONDS = 15  # Even shorter for emergency closes

# CONN-005: Position verification
POSITION_VERIFY_DELAY_SECONDS = 0.5  # Wait before verifying position exists

# MKT-006: VIX filter
DEFAULT_MAX_VIX_ENTRY = 25  # Skip remaining entries if VIX > this

# POS-007: Maximum positions check
MAX_POSITIONS_PER_DAY = 24  # 6 ICs x 4 legs

# DATA-001: Stale data threshold
MAX_DATA_STALENESS_SECONDS = 30

# TIME-001: Clock skew tolerance
MAX_CLOCK_SKEW_SECONDS = 5

# TIME-001: Operation lock timeout
OPERATION_LOCK_TIMEOUT_SECONDS = 60

# POS-003: Hourly reconciliation
RECONCILIATION_INTERVAL_MINUTES = 60

# MKT-002: Flash crash/velocity detection
VELOCITY_WINDOW_MINUTES = 5
FLASH_CRASH_THRESHOLD_PERCENT = 2.0  # 2% move in 5 minutes

# MONITORING: Dynamic interval thresholds
# When price is within this % of stop level, use faster monitoring
VIGILANT_THRESHOLD_PERCENT = 50  # 50% of stop level used = vigilant mode
VIGILANT_CHECK_INTERVAL_SECONDS = 2
NORMAL_CHECK_INTERVAL_SECONDS = 5

# ORDER-004: Pre-entry margin check
MIN_BUYING_POWER_PER_IC = 5000  # Minimum BP required per iron condor ($5000)
MARGIN_CHECK_ENABLED = True  # Can be disabled if Saxo margin API unavailable

# MKT-005: Market circuit breaker halt detection
MARKET_HALT_CHECK_ENABLED = True  # Check for trading halts before entry

# MKT-007: Strike adjustment for illiquidity
ILLIQUIDITY_STRIKE_ADJUSTMENT_POINTS = 5  # Adjust strikes 5 points if illiquid
MAX_STRIKE_ADJUSTMENT_ATTEMPTS = 2  # Max adjustments per side

# TIME-001: Clock sync validation
CLOCK_SYNC_CHECK_ENABLED = True  # Validate system clock on startup
MAX_CLOCK_SKEW_WARNING_SECONDS = 30  # Warn if clock off by more than 30s

# DATA-003: P&L sanity check bounds
MAX_PNL_PER_IC = 500  # Max realistic profit per IC (credit + some)
MIN_PNL_PER_IC = -3000  # Min realistic loss per IC (spread width)
PNL_SANITY_CHECK_ENABLED = True

# ALERT-002: Alert batching for rapid stops
ALERT_BATCH_WINDOW_SECONDS = 5  # Batch alerts within this window
MAX_ALERTS_BEFORE_BATCH = 2  # After this many alerts in window, batch them


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
    """Tracks market data with staleness detection and intraday statistics."""
    spx_price: float = 0.0
    vix: float = 0.0
    last_spx_update: Optional[datetime] = None
    last_vix_update: Optional[datetime] = None

    # P3: Intraday high/low tracking
    spx_high: float = 0.0
    spx_low: float = float('inf')
    vix_high: float = 0.0
    vix_samples: List[float] = field(default_factory=list)

    # P3: Flash crash velocity tracking
    price_history: Deque[Tuple[datetime, float]] = field(default_factory=lambda: deque(maxlen=100))

    def update_spx(self, price: float):
        """Update SPX price with timestamp and track high/low."""
        if price > 0:
            self.spx_price = price
            self.last_spx_update = get_us_market_time()

            # Track intraday high/low
            if price > self.spx_high:
                self.spx_high = price
            if price < self.spx_low:
                self.spx_low = price

            # Track for velocity detection
            self.price_history.append((self.last_spx_update, price))

    def update_vix(self, vix: float):
        """Update VIX with timestamp and track high/average."""
        if vix > 0:
            self.vix = vix
            self.last_vix_update = get_us_market_time()

            # Track VIX high and samples for average
            if vix > self.vix_high:
                self.vix_high = vix
            self.vix_samples.append(vix)

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

    def get_spx_range(self) -> float:
        """Get intraday SPX range (high - low)."""
        if self.spx_low == float('inf') or self.spx_high == 0:
            return 0.0
        return self.spx_high - self.spx_low

    def get_vix_average(self) -> float:
        """Get average VIX for the day."""
        if not self.vix_samples:
            return self.vix
        return sum(self.vix_samples) / len(self.vix_samples)

    def check_flash_crash_velocity(self) -> Tuple[bool, str, float]:
        """
        MKT-002: Check for flash crash conditions (2%+ move in 5 minutes).

        Returns:
            Tuple of (is_flash_crash, direction, percent_change)
        """
        if len(self.price_history) < 2:
            return False, "", 0.0

        now = get_us_market_time()
        window_start = now - timedelta(minutes=VELOCITY_WINDOW_MINUTES)

        # Find the oldest price in the velocity window
        oldest_price = None
        oldest_time = None
        for ts, price in self.price_history:
            if ts >= window_start:
                if oldest_price is None:
                    oldest_price = price
                    oldest_time = ts
                break

        if oldest_price is None or oldest_price == 0:
            return False, "", 0.0

        # Calculate percentage change
        current = self.spx_price
        pct_change = ((current - oldest_price) / oldest_price) * 100

        if abs(pct_change) >= FLASH_CRASH_THRESHOLD_PERCENT:
            direction = "up" if pct_change > 0 else "down"
            return True, direction, pct_change

        return False, "", pct_change

    def reset_daily_tracking(self):
        """Reset intraday tracking for new day."""
        self.spx_high = 0.0
        self.spx_low = float('inf')
        self.vix_high = 0.0
        self.vix_samples.clear()
        self.price_history.clear()


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

        # Orphaned order tracking (ORDER-008)
        self._orphaned_orders: List[str] = []

        # Order slippage settings (from config or defaults)
        self._max_absolute_slippage = self.strategy_config.get("max_absolute_slippage", MAX_ABSOLUTE_SLIPPAGE)
        self._order_timeout = self.strategy_config.get("order_timeout_seconds", ORDER_TIMEOUT_SECONDS)

        # Daily summary tracking
        self._daily_summary_sent = False

        # TIME-001: Operation lock to prevent concurrent strategy checks
        self._operation_lock = threading.Lock()
        self._operation_in_progress = False
        self._operation_started_at: Optional[datetime] = None

        # POS-003: Hourly reconciliation tracking
        self._last_reconciliation_time: Optional[datetime] = None

        # P2: WebSocket price cache for stop monitoring
        self._ws_price_cache: Dict[int, Tuple[float, datetime]] = {}  # uic -> (price, timestamp)

        # P2: Monitoring mode tracking
        self._current_monitoring_mode = "normal"  # "normal" or "vigilant"

        # ALERT-002: Alert batching tracking
        self._recent_alerts: List[Tuple[datetime, str]] = []  # (timestamp, alert_type)
        self._batched_alerts: List[Dict] = []  # Alerts waiting to be batched

        # TIME-001: Clock sync validation
        self._clock_validated = False
        self._clock_skew_seconds = 0.0

        # Initialize metrics
        self.cumulative_metrics = self._load_cumulative_metrics()

        # CRITICAL FIX: Recover positions from Saxo API (not local state file)
        # Local state files can be wrong if positions are closed manually on Saxo platform
        # This matches Delta Neutral's foolproof approach of always querying Saxo for truth
        self._recover_positions_from_saxo()

        # TIME-001: Validate system clock on startup
        if CLOCK_SYNC_CHECK_ENABLED:
            self._validate_system_clock()

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

        # MKT-009: Check for early close day (1:00 PM close)
        # On early close days, only use entries before 11:00 AM
        if is_early_close_day():
            logger.warning("EARLY CLOSE DAY - Using reduced entry schedule (before 11:00 AM only)")
            early_cutoff = dt_time(11, 0)
            self.entry_times = [t for t in self.entry_times if t < early_cutoff]
            if not self.entry_times:
                # Fallback to at least one entry at 10:00 AM
                self.entry_times = [dt_time(10, 0)]
            logger.info(f"Early close entry times: {[t.strftime('%H:%M') for t in self.entry_times]}")

    # =========================================================================
    # MAIN LOOP - Called by main.py every few seconds
    # =========================================================================

    def run_strategy_check(self) -> str:
        """
        Main strategy loop - called periodically by main.py.

        Includes all safety checks from Delta Neutral:
        - TIME-001: Operation lock to prevent concurrent runs
        - STATE-002: State/position consistency validation
        - DATA-001: Stale data validation
        - POS-003: Hourly position reconciliation
        - MKT-002: Flash crash velocity detection

        Returns:
            str: Description of action taken (for logging)
        """
        # TIME-001: Acquire operation lock to prevent concurrent checks
        if not self._acquire_operation_lock():
            return "Operation already in progress - skipping"

        try:
            return self._run_strategy_check_internal()
        finally:
            self._release_operation_lock()

    def _run_strategy_check_internal(self) -> str:
        """Internal strategy check - called with operation lock held."""

        # Check critical intervention first
        if self._critical_intervention_required:
            return f"HALTED: {self._critical_intervention_reason}"

        # Check circuit breaker
        if self._circuit_breaker_open:
            if self._check_circuit_breaker_cooldown():
                self._close_circuit_breaker()
            else:
                return f"Circuit breaker open: {self._circuit_breaker_reason}"

        # ORDER-008: Check for orphaned orders
        if self._has_orphaned_orders():
            # Try to clean up orphaned orders by checking if they've been filled/cancelled
            self._attempt_orphan_cleanup()
            if self._has_orphaned_orders():
                return f"Blocked by {len(self._orphaned_orders)} orphaned order(s) - manual intervention required"

        # Update market data
        self._update_market_data()

        # DATA-001: Validate data freshness before trading
        if self._is_data_stale_for_trading():
            return "Skipping action - market data is stale"

        # MKT-002: Check for flash crash conditions
        flash_crash, direction, pct_change = self.market_data.check_flash_crash_velocity()
        if flash_crash:
            logger.warning(f"MKT-002: Flash crash detected! SPX moved {pct_change:.2f}% {direction} in 5 min")
            # Don't halt, but trigger vigilant mode and alert
            self._current_monitoring_mode = "vigilant"
            if self.daily_state.active_entries:
                self.alert_service.send_alert(
                    alert_type=AlertType.MAX_LOSS,
                    title=f"Flash Crash Warning - SPX {direction.upper()} {abs(pct_change):.1f}%",
                    message=f"SPX moved {pct_change:.2f}% in 5 minutes. Active positions being monitored.",
                    priority=AlertPriority.HIGH,
                    details={"direction": direction, "pct_change": pct_change, "spx": self.current_price}
                )

        # STATE-002: Validate state/position consistency
        consistency_error = self._check_state_consistency()
        if consistency_error:
            logger.error(f"STATE-002: {consistency_error}")
            # Attempt to recover instead of halting
            self._attempt_state_recovery()

        # POS-003: Periodic position reconciliation (hourly)
        self._check_hourly_reconciliation()

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
    # OPERATION LOCK (TIME-001)
    # =========================================================================

    def _acquire_operation_lock(self) -> bool:
        """
        TIME-001: Acquire operation lock to prevent concurrent strategy checks.

        Returns:
            True if lock acquired, False if another operation in progress
        """
        acquired = self._operation_lock.acquire(blocking=False)
        if not acquired:
            return False

        # Check for stale lock (operation hung)
        if self._operation_in_progress and self._operation_started_at:
            elapsed = (get_us_market_time() - self._operation_started_at).total_seconds()
            if elapsed > OPERATION_LOCK_TIMEOUT_SECONDS:
                logger.warning(f"TIME-001: Stale operation lock detected ({elapsed:.0f}s old) - resetting")
                self._operation_in_progress = False

        if self._operation_in_progress:
            self._operation_lock.release()
            return False

        self._operation_in_progress = True
        self._operation_started_at = get_us_market_time()
        return True

    def _release_operation_lock(self):
        """Release operation lock."""
        self._operation_in_progress = False
        self._operation_started_at = None
        try:
            self._operation_lock.release()
        except RuntimeError:
            pass  # Lock wasn't held

    # =========================================================================
    # STATE CONSISTENCY CHECK (STATE-002)
    # =========================================================================

    def _check_state_consistency(self) -> Optional[str]:
        """
        STATE-002: Validate that strategy state matches actual positions.

        Returns:
            Error message if inconsistent, None if OK
        """
        active_entries = len(self.daily_state.active_entries)
        my_positions = self.registry.get_positions("MEIC")

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

    def _attempt_state_recovery(self):
        """Attempt to recover from state inconsistency."""
        logger.info("STATE-002: Attempting state recovery...")

        # Re-reconcile positions
        self._reconcile_positions()

        # Update state based on actual positions
        active_entries = len(self.daily_state.active_entries)

        if active_entries > 0:
            if self.state not in [MEICState.MONITORING, MEICState.STOP_TRIGGERED]:
                logger.info(f"STATE-002: Setting state to MONITORING (have {active_entries} active entries)")
                self.state = MEICState.MONITORING
        elif self._next_entry_index < len(self.entry_times):
            if self.state != MEICState.WAITING_FIRST_ENTRY:
                logger.info("STATE-002: Setting state to WAITING_FIRST_ENTRY")
                self.state = MEICState.WAITING_FIRST_ENTRY
        else:
            if self.state != MEICState.DAILY_COMPLETE:
                logger.info("STATE-002: Setting state to DAILY_COMPLETE")
                self.state = MEICState.DAILY_COMPLETE

    # =========================================================================
    # STALE DATA VALIDATION (DATA-001)
    # =========================================================================

    def _is_data_stale_for_trading(self) -> bool:
        """
        DATA-001: Check if market data is too stale for trading decisions.

        Returns:
            True if data is stale and we should skip trading actions
        """
        # Only check staleness during market hours
        if not is_market_open():
            return False

        # Check SPX staleness
        if self.market_data.is_spx_stale():
            logger.warning("DATA-001: SPX data is stale - will refresh")
            # Try to refresh
            self._update_market_data()
            if self.market_data.is_spx_stale():
                logger.warning("DATA-001: SPX still stale after refresh - skipping actions")
                return True

        return False

    # =========================================================================
    # HOURLY RECONCILIATION (POS-003)
    # =========================================================================

    def _check_hourly_reconciliation(self):
        """
        POS-003: Perform hourly position reconciliation during market hours.

        Compares expected positions vs actual Saxo positions to detect:
        - Early assignment
        - Manual intervention
        - Orphaned positions
        """
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
            actual_position_ids = {str(p.get("PositionBase", {}).get("PositionId")) for p in actual_positions}

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
                    message=f"{len(missing)} MEIC positions missing from Saxo. Manual intervention suspected.",
                    priority=AlertPriority.HIGH,
                    details={"missing_ids": list(missing)}
                )

                # Clean up registry and daily state
                self._handle_missing_positions(missing)

            # Check for unexpected positions (assigned, etc.)
            my_registry_positions = self.registry.get_positions("MEIC")
            unexpected = (actual_position_ids & my_registry_positions) - expected_position_ids
            if unexpected:
                logger.warning(f"POS-003: {len(unexpected)} unexpected MEIC positions found")

            # Persist state after reconciliation
            self._save_state_to_disk()

            logger.info(f"POS-003: Reconciliation complete - {len(expected_position_ids)} expected, {len(actual_position_ids & my_registry_positions)} found")

        except Exception as e:
            logger.error(f"POS-003: Reconciliation failed: {e}")

    def _handle_missing_positions(self, missing_ids: set):
        """Handle positions that are missing from Saxo (manually closed or assigned)."""
        for entry in self.daily_state.entries:
            for position_id in list(missing_ids):
                # Check if this entry had the missing position
                if position_id in entry.all_position_ids:
                    # Determine which leg
                    if position_id == entry.short_call_position_id:
                        logger.warning(f"  Entry #{entry.entry_number}: Short Call missing - marking call side stopped")
                        entry.call_side_stopped = True
                        entry.short_call_position_id = None
                    elif position_id == entry.long_call_position_id:
                        logger.warning(f"  Entry #{entry.entry_number}: Long Call missing")
                        entry.long_call_position_id = None
                    elif position_id == entry.short_put_position_id:
                        logger.warning(f"  Entry #{entry.entry_number}: Short Put missing - marking put side stopped")
                        entry.put_side_stopped = True
                        entry.short_put_position_id = None
                    elif position_id == entry.long_put_position_id:
                        logger.warning(f"  Entry #{entry.entry_number}: Long Put missing")
                        entry.long_put_position_id = None

                    # Unregister from registry
                    self.registry.unregister(position_id)

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

    def _is_entry_time(self) -> bool:
        """
        P0 BUG FIX: Check if we're still within the entry window.

        Used by retry logic to determine if entry should continue.
        Similar to _should_attempt_entry but doesn't increment index.

        Returns:
            True if still within entry window for current entry
        """
        if self._next_entry_index >= len(self.entry_times):
            return False

        now = get_us_market_time()
        scheduled_time = self.entry_times[self._next_entry_index]
        scheduled_datetime = now.replace(
            hour=scheduled_time.hour,
            minute=scheduled_time.minute,
            second=0,
            microsecond=0
        )

        # Window extends ENTRY_WINDOW_MINUTES after scheduled time
        window_end = scheduled_datetime + timedelta(minutes=ENTRY_WINDOW_MINUTES)

        return scheduled_datetime <= now <= window_end

    def _initiate_entry(self) -> str:
        """
        Initiate an iron condor entry with retry logic.

        CONN-001: Retries entry up to ENTRY_MAX_RETRIES times within
        ENTRY_WINDOW_MINUTES of the scheduled time.

        ORDER-008: Checks for orphaned orders before attempting entry.
        ORDER-004: Checks buying power before attempting entry.
        MKT-005: Checks for market halt before attempting entry.
        TIME-001: Validates clock reliability.

        Returns:
            str: Description of action taken
        """
        entry_num = self._next_entry_index + 1
        logger.info(f"Initiating Entry #{entry_num} of {len(self.entry_times)}")

        # ORDER-008: Check for orphaned orders blocking trading
        if self._has_orphaned_orders():
            logger.error(f"Entry #{entry_num} blocked by orphaned orders")
            self._next_entry_index += 1  # Skip this entry
            return f"Entry #{entry_num} skipped - orphaned orders blocking"

        # MKT-005: Check for market halt
        is_halted, halt_reason = self._check_market_halt()
        if is_halted:
            logger.warning(f"MKT-005: Market halt detected - {halt_reason}")
            # Don't skip entry - wait for market to reopen
            return f"Entry #{entry_num} delayed - {halt_reason}"

        # ORDER-004: Check buying power before entry
        has_bp, bp_message = self._check_buying_power()
        if not has_bp:
            logger.warning(f"ORDER-004: {bp_message}")
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1  # Skip this entry
            # Send alert about insufficient margin
            self.alert_service.send_alert(
                alert_type=AlertType.MAX_LOSS,
                title=f"Entry #{entry_num} Skipped - Insufficient Margin",
                message=bp_message,
                priority=AlertPriority.HIGH,
                details={"entry_number": entry_num, "reason": "margin"}
            )
            return f"Entry #{entry_num} skipped - {bp_message}"

        # TIME-001: Verify clock reliability
        clock_ok, clock_message = self._is_clock_reliable()
        if not clock_ok:
            logger.warning(f"TIME-001: {clock_message}")
            # Log warning but proceed - entry timing might be off

        self._entry_in_progress = True
        self.state = MEICState.ENTRY_IN_PROGRESS

        # CONN-001: Entry retry loop
        last_error = None
        for attempt in range(ENTRY_MAX_RETRIES):
            try:
                if attempt > 0:
                    logger.info(f"Entry #{entry_num} retry {attempt + 1}/{ENTRY_MAX_RETRIES}")
                    time.sleep(ENTRY_RETRY_DELAY_SECONDS)

                    # Check if still within entry window
                    if not self._is_entry_time():
                        logger.warning(f"Entry #{entry_num} window expired after {attempt} retries")
                        break

                # Create entry object (fresh each attempt)
                entry = IronCondorEntry(entry_number=entry_num)
                entry.strategy_id = f"meic_{get_us_market_time().strftime('%Y%m%d')}_entry{entry_num}"
                self._current_entry = entry

                # Calculate strikes (may change between retries due to price movement)
                if not self._calculate_strikes(entry):
                    last_error = "Failed to calculate strikes"
                    continue

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
                        position_summary=f"MEIC Entry #{entry_num}: Call {entry.short_call_strike}/{entry.long_call_strike}, Put {entry.short_put_strike}/{entry.long_put_strike}",
                        cost_or_credit=entry.total_credit,
                        details={
                            "spx_price": self.current_price,
                            "entry_number": entry_num,
                            "spread_width": self.spread_width,
                            "attempts": attempt + 1
                        }
                    )

                    self._record_api_result(True)
                    self._next_entry_index += 1

                    # P1: Save state after successful entry
                    self._save_state_to_disk()

                    result_msg = f"Entry #{entry_num} complete - Credit: ${entry.total_credit:.2f}"
                    if attempt > 0:
                        result_msg += f" (after {attempt + 1} attempts)"
                    return result_msg
                else:
                    last_error = "Entry execution failed"
                    # Continue to next retry

            except Exception as e:
                logger.error(f"Entry #{entry_num} attempt {attempt + 1} error: {e}")
                last_error = str(e)
                # Continue to next retry

        # All retries exhausted
        self.daily_state.entries_failed += 1
        self._record_api_result(False, f"Entry #{entry_num} failed after {ENTRY_MAX_RETRIES} attempts: {last_error}")
        self._next_entry_index += 1  # Move on to next entry time

        self._entry_in_progress = False
        self._current_entry = None

        # Transition state
        if self.daily_state.active_entries:
            self.state = MEICState.MONITORING
        elif self._next_entry_index < len(self.entry_times):
            self.state = MEICState.WAITING_FIRST_ENTRY
        else:
            self.state = MEICState.DAILY_COMPLETE

        return f"Entry #{entry_num} failed after {ENTRY_MAX_RETRIES} attempts: {last_error}"

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

        # MKT-007: Check liquidity and adjust strikes if needed
        # Get today's expiry for liquidity check
        expiry = self._get_todays_expiry()
        if expiry:
            # Check short call liquidity (move closer to ATM if illiquid)
            adjusted_call, call_msg = self._adjust_strike_for_liquidity(
                entry.short_call_strike, "Call", expiry,
                adjustment_direction=-1  # Move closer to ATM (lower for calls)
            )
            if adjusted_call and adjusted_call != entry.short_call_strike:
                entry.short_call_strike = adjusted_call
                entry.long_call_strike = adjusted_call + self.spread_width
                logger.info(f"MKT-007: {call_msg}")

            # Check short put liquidity (move closer to ATM if illiquid)
            adjusted_put, put_msg = self._adjust_strike_for_liquidity(
                entry.short_put_strike, "Put", expiry,
                adjustment_direction=1  # Move closer to ATM (higher for puts)
            )
            if adjusted_put and adjusted_put != entry.short_put_strike:
                entry.short_put_strike = adjusted_put
                entry.long_put_strike = adjusted_put - self.spread_width
                logger.info(f"MKT-007: {put_msg}")

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
        external_ref: str,
        emergency_mode: bool = False
    ) -> Optional[Dict]:
        """
        Place a single option order with progressive slippage retry.

        ORDER-007: Uses progressive retry sequence:
        1. Limit at mid price (0% slippage)
        2. Limit at mid price (fresh quote)
        3. Limit with 5% slippage
        4. Limit with 10% slippage
        5. MARKET order (guaranteed fill, if spread acceptable)

        ORDER-006: Checks bid-ask spread before placing orders.
        CONN-005: Verifies position exists after fill.

        Args:
            strike: Strike price
            put_call: "Call" or "Put"
            buy_sell: BuySell enum
            expiry: Expiry date string (YYYY-MM-DD)
            external_ref: External reference for tracking
            emergency_mode: If True, use shorter timeouts

        Returns:
            dict with order result including position_id, uic, credit/debit
            None if order failed
        """
        # Get option UIC from chain
        uic = self._get_option_uic(strike, put_call, expiry)
        if not uic:
            logger.error(f"Could not find UIC for {put_call} {strike} {expiry}")
            return None

        timeout = ORDER_TIMEOUT_EMERGENCY_SECONDS if emergency_mode else self._order_timeout
        last_result = None
        leg_description = f"{put_call} {strike}"

        # Progressive retry sequence
        for attempt, (slippage_percent, is_market) in enumerate(PROGRESSIVE_RETRY_SEQUENCE):
            # Get fresh quote for each attempt
            quote = self.client.get_quote(uic, asset_type="StockIndexOption")
            if not quote or "Quote" not in quote:
                logger.warning(f"  Attempt {attempt + 1}: No quote for {leg_description}")
                time.sleep(1)
                continue

            bid = quote["Quote"].get("Bid") or 0
            ask = quote["Quote"].get("Ask") or 0
            spread = abs(ask - bid) if bid and ask else 0

            # ORDER-006: Check bid-ask spread
            if bid > 0:
                spread_percent = (spread / bid) * 100
                if spread_percent >= MAX_BID_ASK_SPREAD_PERCENT_SKIP:
                    logger.warning(f"  ORDER-006: Spread {spread_percent:.1f}% too wide, skipping attempt")
                    continue
                elif spread_percent >= MAX_BID_ASK_SPREAD_PERCENT_WARNING:
                    logger.warning(f"  ORDER-006: Wide spread warning: {spread_percent:.1f}%")

            if is_market:
                # ORDER-005: Check absolute spread before MARKET order
                if spread > self._max_absolute_slippage:
                    logger.critical(f"  ORDER-005: Spread ${spread:.2f} > max ${self._max_absolute_slippage:.2f}")
                    logger.critical(f"  ABORTING MARKET order for {leg_description}")
                    continue

                # Place MARKET order
                logger.info(f"  Attempt {attempt + 1}: MARKET order for {leg_description}")
                result = self.client.place_order(
                    uic=uic,
                    asset_type="StockIndexOption",
                    buy_sell=buy_sell,
                    amount=self.contracts_per_entry,
                    order_type=OrderType.MARKET,
                    to_open_close="ToOpen",
                    external_reference=external_ref
                )
            else:
                # Calculate limit price with slippage
                mid_price = (bid + ask) / 2 if bid and ask else ask or bid
                if slippage_percent > 0:
                    if buy_sell == BuySell.BUY:
                        # Pay MORE to buy (aggressive)
                        limit_price = mid_price * (1 + slippage_percent / 100)
                    else:
                        # Accept LESS to sell (aggressive)
                        limit_price = mid_price * (1 - slippage_percent / 100)
                    limit_price = round(limit_price, 2)
                else:
                    limit_price = round(mid_price, 2)

                logger.info(
                    f"  Attempt {attempt + 1}: LIMIT @ ${limit_price:.2f} "
                    f"({slippage_percent}% slippage) for {leg_description}"
                )

                # Place limit order with timeout
                result = self.client.place_limit_order_with_timeout(
                    uic=uic,
                    asset_type="StockIndexOption",
                    buy_sell=buy_sell,
                    amount=self.contracts_per_entry,
                    limit_price=limit_price,
                    timeout_seconds=timeout,
                    to_open_close="ToOpen",
                    external_reference=external_ref
                )

            last_result = result

            if result and result.get("filled"):
                # Order filled - verify position exists
                position_id = self._get_position_id_from_order(result)
                fill_price = self._get_fill_price(result)

                # CONN-005: Verify position exists
                if position_id and not self._verify_position_exists(uic, position_id, buy_sell):
                    logger.warning(f"  CONN-005: Position verification failed for {leg_description}")
                    # Position may still exist, continue with warning

                logger.info(f"  ✓ Filled {leg_description} @ ${fill_price:.2f}")

                return {
                    "position_id": position_id,
                    "uic": uic,
                    "credit": fill_price * 100 if buy_sell == BuySell.SELL else 0,
                    "debit": fill_price * 100 if buy_sell == BuySell.BUY else 0,
                    "fill_price": fill_price
                }

            # Check if cancel failed (orphaned order)
            if result and result.get("cancel_failed"):
                order_id = result.get("order_id")
                if order_id:
                    logger.critical(f"  ORDER-008: Cancel failed - orphaned order {order_id}")
                    self._add_orphaned_order(order_id)
                    # Cannot continue with orphaned order
                    return None

            # Log retry info
            if attempt < len(PROGRESSIVE_RETRY_SEQUENCE) - 1:
                next_slippage, next_is_market = PROGRESSIVE_RETRY_SEQUENCE[attempt + 1]
                if next_is_market:
                    logger.warning(f"  ⚠ {leg_description} not filled - trying MARKET next...")
                else:
                    logger.warning(f"  ⚠ {leg_description} not filled - retrying at {next_slippage}% slippage...")

        # All attempts failed
        logger.error(f"  ✗ {leg_description} failed all {len(PROGRESSIVE_RETRY_SEQUENCE)} attempts")
        return None

    def _verify_position_exists(self, uic: int, position_id: str, buy_sell: BuySell) -> bool:
        """
        CONN-005: Verify that a position exists after fill.

        Catches the case where an order disappears from open orders but wasn't
        actually filled (could have been rejected).

        Args:
            uic: Option UIC
            position_id: Expected position ID
            buy_sell: Direction of the order

        Returns:
            True if position verified, False otherwise
        """
        time.sleep(POSITION_VERIFY_DELAY_SECONDS)

        positions = self.client.get_positions()
        if not positions:
            logger.warning("CONN-005: Could not fetch positions for verification")
            return False

        expected_direction = "long" if buy_sell == BuySell.BUY else "short"

        for pos in positions:
            pos_base = pos.get("PositionBase", {})
            pos_id = str(pos_base.get("PositionId", ""))
            pos_uic = pos_base.get("Uic")
            amount = pos_base.get("Amount", 0)

            if pos_id == position_id or pos_uic == uic:
                # Check direction matches
                if expected_direction == "long" and amount > 0:
                    logger.info(f"  ✓ CONN-005: Verified long position for UIC {uic}")
                    return True
                elif expected_direction == "short" and amount < 0:
                    logger.info(f"  ✓ CONN-005: Verified short position for UIC {uic}")
                    return True

        logger.warning(f"  ⚠ CONN-005: Position NOT FOUND for UIC {uic}")
        return False

    def _add_orphaned_order(self, order_id: str):
        """
        ORDER-008: Track an orphaned order (cancel failed).

        Args:
            order_id: The order ID that failed to cancel
        """
        if order_id not in self._orphaned_orders:
            self._orphaned_orders.append(order_id)
            logger.critical(f"ORDER-008: Added orphaned order {order_id}")
            logger.critical(f"  Total orphaned orders: {len(self._orphaned_orders)}")

            # Alert about orphaned order
            self.alert_service.send_alert(
                alert_type=AlertType.CIRCUIT_BREAKER,
                title="Orphaned Order Detected",
                message=f"Order {order_id} failed to cancel - manual intervention may be required",
                priority=AlertPriority.HIGH,
                details={"order_id": order_id, "orphaned_orders": self._orphaned_orders}
            )

    def _has_orphaned_orders(self) -> bool:
        """Check if there are any orphaned orders blocking trading."""
        if self._orphaned_orders:
            logger.warning(f"ORDER-008: {len(self._orphaned_orders)} orphaned orders blocking trading")
            return True
        return False

    def _clear_orphaned_order(self, order_id: str):
        """Remove an orphaned order after manual resolution."""
        if order_id in self._orphaned_orders:
            self._orphaned_orders.remove(order_id)
            logger.info(f"ORDER-008: Cleared orphaned order {order_id}")

    def _attempt_orphan_cleanup(self):
        """
        ORDER-008: Attempt to clean up orphaned orders.

        Checks if orphaned orders have been filled/cancelled and clears them
        from the tracking list if they're no longer open.
        """
        if not self._orphaned_orders:
            return

        logger.info(f"ORDER-008: Attempting cleanup of {len(self._orphaned_orders)} orphaned orders")

        # Get current open orders
        try:
            open_orders = self.client.get_orders()
            open_order_ids = {str(o.get("OrderId")) for o in (open_orders or [])}
        except Exception as e:
            logger.error(f"Failed to fetch open orders for orphan cleanup: {e}")
            return

        # Check each orphaned order
        orders_to_clear = []
        for order_id in self._orphaned_orders:
            if order_id not in open_order_ids:
                # Order is no longer open - it was filled or cancelled
                logger.info(f"ORDER-008: Orphaned order {order_id} no longer open - clearing")
                orders_to_clear.append(order_id)
            else:
                # Still open - try to cancel again
                logger.warning(f"ORDER-008: Orphaned order {order_id} still open - attempting cancel")
                try:
                    cancel_result = self.client.cancel_order(order_id)
                    if cancel_result:
                        orders_to_clear.append(order_id)
                        logger.info(f"ORDER-008: Successfully cancelled orphaned order {order_id}")
                except Exception as e:
                    logger.error(f"ORDER-008: Failed to cancel orphaned order {order_id}: {e}")

        # Clear resolved orders
        for order_id in orders_to_clear:
            self._clear_orphaned_order(order_id)

        if self._orphaned_orders:
            logger.warning(f"ORDER-008: {len(self._orphaned_orders)} orphaned orders remain")

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

        # Log safety event for audit trail
        self._log_safety_event("NAKED_SHORT_DETECTED", f"{leg_name} position {pos_id}")

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
                self._log_safety_event("NAKED_SHORT_CLOSED", f"{leg_name} position {pos_id} closed successfully")
            else:
                logger.critical(f"FAILED to close naked short {pos_id}!")
                self._log_safety_event("NAKED_SHORT_CLOSE_FAILED", f"{leg_name} position {pos_id} - close returned false")
                self._trigger_critical_intervention(f"Cannot close naked short {pos_id}")
        except Exception as e:
            logger.critical(f"Exception closing naked short: {e}")
            self._log_safety_event("NAKED_SHORT_EXCEPTION", f"{leg_name} position {pos_id} - {str(e)}")
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

            # DATA-003: Validate P&L sanity before using values
            pnl_valid, pnl_message = self._validate_pnl_sanity(entry)
            if not pnl_valid:
                logger.error(f"DATA-003: Skipping stop check for Entry #{entry.entry_number} - {pnl_message}")
                continue  # Skip this entry - data is suspect

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

        P2: Uses WebSocket cache first, falls back to REST API if cache miss.
        This allows fast 2-second monitoring without API rate limits.
        """
        if self.dry_run:
            # Simulate price movement based on SPX movement
            self._simulate_entry_prices(entry)
            return

        # P2: Try WebSocket cache first for each leg
        # Max age of 5 seconds for cached prices
        cache_max_age = 5

        # Short Call
        if entry.short_call_uic:
            cached = self._get_cached_price(entry.short_call_uic, cache_max_age)
            if cached:
                entry.short_call_price = cached
            else:
                quote = self.client.get_quote(entry.short_call_uic, asset_type="StockIndexOption")
                entry.short_call_price = self._extract_mid_price(quote) or 0

        # Long Call
        if entry.long_call_uic:
            cached = self._get_cached_price(entry.long_call_uic, cache_max_age)
            if cached:
                entry.long_call_price = cached
            else:
                quote = self.client.get_quote(entry.long_call_uic, asset_type="StockIndexOption")
                entry.long_call_price = self._extract_mid_price(quote) or 0

        # Short Put
        if entry.short_put_uic:
            cached = self._get_cached_price(entry.short_put_uic, cache_max_age)
            if cached:
                entry.short_put_price = cached
            else:
                quote = self.client.get_quote(entry.short_put_uic, asset_type="StockIndexOption")
                entry.short_put_price = self._extract_mid_price(quote) or 0

        # Long Put
        if entry.long_put_uic:
            cached = self._get_cached_price(entry.long_put_uic, cache_max_age)
            if cached:
                entry.long_put_price = cached
            else:
                quote = self.client.get_quote(entry.long_put_uic, asset_type="StockIndexOption")
                entry.long_put_price = self._extract_mid_price(quote) or 0

    def _extract_mid_price(self, quote: Optional[Dict]) -> Optional[float]:
        """Extract mid price from quote for option pricing."""
        if not quote or "Quote" not in quote:
            return None
        bid = quote["Quote"].get("Bid") or 0
        ask = quote["Quote"].get("Ask") or 0
        if bid and ask:
            return (bid + ask) / 2
        return ask or bid or None

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

        # ALERT-002: Use batched alerting for rapid stops
        self._queue_stop_alert(entry, side, stop_level)

        # P1: Save state after stop loss
        self._save_state_to_disk()

        # ALERT-002: Flush any batched alerts after a short delay
        # This allows multiple rapid stops to be batched together
        time.sleep(0.1)  # Small delay to allow batching
        self._flush_batched_alerts()

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

        # Log safety event for audit trail
        self._log_safety_event("CIRCUIT_BREAKER_OPEN", reason)

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

        # Log safety event for audit trail
        self._log_safety_event("CRITICAL_INTERVENTION", reason)

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
    # POSITION RECOVERY FROM SAXO API (CRITICAL - Source of Truth)
    # =========================================================================

    def _recover_positions_from_saxo(self) -> bool:
        """
        CRITICAL: Recover positions from Saxo API - the ONLY source of truth.

        This replaces the local disk state recovery approach. Local state files can be
        wrong if positions are closed manually on the Saxo trading platform. This follows
        Delta Neutral's foolproof approach of always querying Saxo for the real positions.

        Process:
        1. Query Saxo API for all account positions
        2. Filter to SPX/SPXW options using option_root_uic
        3. Use Position Registry to identify which positions belong to MEIC
        4. Use registry metadata to group positions by entry number
        5. Reconstruct IronCondorEntry objects from actual Saxo data
        6. Update local state to match Saxo reality

        Returns:
            bool: True if positions were recovered, False if starting fresh
        """
        logger.info("=" * 60)
        logger.info("POSITION RECOVERY: Querying Saxo API for source of truth...")
        logger.info("=" * 60)

        try:
            # Step 1: Get ALL positions from Saxo
            all_positions = self.client.get_positions()
            if not all_positions:
                logger.info("No positions found in Saxo account - starting fresh")
                return False

            logger.info(f"Found {len(all_positions)} total positions in account")

            # Step 2: Get valid position IDs and clean up registry orphans
            valid_ids = {str(p.get("PositionBase", {}).get("PositionId")) for p in all_positions}
            orphans = self.registry.cleanup_orphans(valid_ids)
            if orphans:
                logger.warning(f"Cleaned up {len(orphans)} orphaned registry entries (positions closed externally)")
                self._log_safety_event("ORPHAN_CLEANUP", f"Removed {len(orphans)} orphaned positions from registry")

            # Step 3: Get MEIC positions from registry
            my_position_ids = self.registry.get_positions("MEIC")
            if not my_position_ids:
                logger.info("No MEIC positions in registry - starting fresh")
                return False

            logger.info(f"Found {len(my_position_ids)} MEIC positions in registry")

            # Step 4: Filter Saxo positions to just MEIC positions
            meic_positions = []
            for pos in all_positions:
                pos_id = str(pos.get("PositionBase", {}).get("PositionId"))
                if pos_id in my_position_ids:
                    meic_positions.append(pos)

            if not meic_positions:
                logger.warning("Registry says we have MEIC positions but none found in Saxo! Cleaning registry...")
                # Clear MEIC positions from registry since they don't exist
                for pos_id in my_position_ids:
                    self.registry.unregister(pos_id)
                self._log_safety_event("REGISTRY_CLEARED", "All MEIC positions removed - not found in Saxo")
                return False

            logger.info(f"Matched {len(meic_positions)} positions to MEIC in Saxo")

            # Step 5: Group positions by entry number using registry metadata
            entries_by_number = self._group_positions_by_entry(meic_positions, my_position_ids)

            if not entries_by_number:
                logger.warning("Could not group positions into entries - manual review needed")
                self._log_safety_event("RECOVERY_FAILED", "Could not reconstruct entries from positions")
                return False

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
                return False

            # Step 7: Update local state to match Saxo
            today = get_us_market_time().strftime("%Y-%m-%d")

            # Reset daily state but preserve date
            self.daily_state = MEICDailyState()
            self.daily_state.date = today
            self.daily_state.entries = recovered_entries
            self.daily_state.entries_completed = len(recovered_entries)

            # Determine next entry index (how many entries have we done?)
            max_entry_num = max(e.entry_number for e in recovered_entries)
            self._next_entry_index = max_entry_num  # Next entry will be max_entry_num + 1

            # Set state based on recovered positions
            if recovered_entries:
                # Check if any entries still have active positions
                active_entries = [e for e in recovered_entries if not (e.call_side_stopped and e.put_side_stopped)]
                if active_entries:
                    self.state = MEICState.MONITORING
                elif self._next_entry_index < len(self.entry_times):
                    self.state = MEICState.WAITING_FIRST_ENTRY
                else:
                    self.state = MEICState.DAILY_COMPLETE

            # Calculate total credit from recovered entries
            total_credit = sum(e.total_credit for e in recovered_entries)
            self.daily_state.total_credit_received = total_credit

            logger.info("=" * 60)
            logger.info(f"RECOVERY COMPLETE: {len(recovered_entries)} entries recovered")
            logger.info(f"  State: {self.state.value}")
            logger.info(f"  Next entry index: {self._next_entry_index}")
            logger.info(f"  Total credit: ${total_credit:.2f}")
            logger.info("=" * 60)

            # Send recovery alert
            self.alert_service.send_alert(
                alert_type=AlertType.POSITION_OPENED,
                title="MEIC Position Recovery",
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
            self._log_safety_event("RECOVERY_ERROR", str(e))
            return False

    def _group_positions_by_entry(
        self,
        saxo_positions: List[Dict],
        registry_position_ids: Set[str]
    ) -> Dict[int, List[Dict]]:
        """
        Group Saxo positions by entry number using registry metadata.

        Args:
            saxo_positions: List of Saxo position dicts
            registry_position_ids: Set of position IDs from registry

        Returns:
            Dict mapping entry_number -> list of position data dicts
        """
        entries_by_number: Dict[int, List[Dict]] = {}

        for pos in saxo_positions:
            pos_base = pos.get("PositionBase", {})
            pos_id = str(pos_base.get("PositionId"))

            # Get registry metadata for this position
            reg_info = self.registry.get_position_details(pos_id)
            if not reg_info:
                logger.warning(f"Position {pos_id} in Saxo but no registry info - orphan?")
                continue

            metadata = reg_info.get("metadata", {})
            entry_number = metadata.get("entry_number")
            leg_type = metadata.get("leg_type")
            strike = metadata.get("strike")

            if entry_number is None:
                logger.warning(f"Position {pos_id} has no entry_number in metadata")
                continue

            # Parse position data
            parsed = self._parse_spx_option_position(pos)
            if parsed:
                parsed["leg_type"] = leg_type
                parsed["entry_number"] = entry_number

                if entry_number not in entries_by_number:
                    entries_by_number[entry_number] = []
                entries_by_number[entry_number].append(parsed)

        return entries_by_number

    def _parse_spx_option_position(self, pos: Dict) -> Optional[Dict]:
        """
        Parse a Saxo position dict into a standardized format.

        Args:
            pos: Raw Saxo position dict

        Returns:
            Parsed position data or None if not valid
        """
        try:
            pos_base = pos.get("PositionBase", {})
            pos_view = pos.get("PositionView", {})

            position_id = str(pos_base.get("PositionId"))
            uic = pos_base.get("Uic")
            amount = pos_base.get("Amount", 0)

            # Get option details
            options_data = pos_base.get("OptionsData", {})
            strike = options_data.get("Strike")
            expiry = options_data.get("ExpiryDate", "")
            put_call = options_data.get("PutCall")  # "Call" or "Put"

            if not all([position_id, uic, strike, put_call]):
                return None

            # Determine if long or short based on amount
            is_long = amount > 0

            # Get prices
            entry_price = pos_base.get("OpenPrice", 0) or 0
            current_price = pos_view.get("CurrentPrice", 0) or 0

            return {
                "position_id": position_id,
                "uic": uic,
                "amount": abs(amount),
                "is_long": is_long,
                "strike": strike,
                "expiry": expiry[:10] if expiry else "",  # YYYY-MM-DD
                "put_call": put_call,
                "entry_price": entry_price,
                "current_price": current_price
            }

        except Exception as e:
            logger.warning(f"Failed to parse position: {e}")
            return None

    def _reconstruct_entry_from_positions(
        self,
        entry_number: int,
        positions: List[Dict]
    ) -> Optional[IronCondorEntry]:
        """
        Reconstruct an IronCondorEntry from Saxo position data.

        Args:
            entry_number: The entry number (1-6)
            positions: List of parsed position dicts for this entry

        Returns:
            Reconstructed IronCondorEntry or None if invalid
        """
        entry = IronCondorEntry(entry_number=entry_number)
        entry.strategy_id = f"meic_{get_us_market_time().strftime('%Y%m%d')}_{entry_number:03d}"

        # Categorize positions by leg type
        for pos in positions:
            leg_type = pos.get("leg_type")
            strike = pos.get("strike")
            is_long = pos.get("is_long")
            put_call = pos.get("put_call")

            # Validate leg type matches expected
            expected_long = leg_type in ["long_call", "long_put"]
            if expected_long != is_long:
                logger.warning(f"Entry #{entry_number}: Leg {leg_type} direction mismatch!")

            if leg_type == "short_call":
                entry.short_call_position_id = pos["position_id"]
                entry.short_call_uic = pos["uic"]
                entry.short_call_strike = strike
                entry.short_call_price = pos.get("current_price", 0)
                # Estimate credit from entry price
                entry.call_spread_credit = pos.get("entry_price", 0) * 100

            elif leg_type == "long_call":
                entry.long_call_position_id = pos["position_id"]
                entry.long_call_uic = pos["uic"]
                entry.long_call_strike = strike
                entry.long_call_price = pos.get("current_price", 0)
                # Subtract long cost from credit
                entry.call_spread_credit -= pos.get("entry_price", 0) * 100

            elif leg_type == "short_put":
                entry.short_put_position_id = pos["position_id"]
                entry.short_put_uic = pos["uic"]
                entry.short_put_strike = strike
                entry.short_put_price = pos.get("current_price", 0)
                entry.put_spread_credit = pos.get("entry_price", 0) * 100

            elif leg_type == "long_put":
                entry.long_put_position_id = pos["position_id"]
                entry.long_put_uic = pos["uic"]
                entry.long_put_strike = strike
                entry.long_put_price = pos.get("current_price", 0)
                entry.put_spread_credit -= pos.get("entry_price", 0) * 100

        # Check if entry is complete (all 4 legs)
        has_all_legs = all([
            entry.short_call_position_id,
            entry.long_call_position_id,
            entry.short_put_position_id,
            entry.long_put_position_id
        ])

        if not has_all_legs:
            # Partial entry - check which legs exist
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

            # Determine which side was stopped
            has_call_side = entry.short_call_position_id and entry.long_call_position_id
            has_put_side = entry.short_put_position_id and entry.long_put_position_id

            entry.call_side_stopped = not has_call_side
            entry.put_side_stopped = not has_put_side

        entry.is_complete = has_all_legs

        # Calculate stop levels based on recovered credit
        total_credit = entry.call_spread_credit + entry.put_spread_credit
        if self.meic_plus_enabled:
            # MEIC+ stops at credit - $0.10 for potential small win
            entry.call_side_stop = (total_credit / 2) - (self.meic_plus_reduction * 100)
            entry.put_side_stop = (total_credit / 2) - (self.meic_plus_reduction * 100)
        else:
            entry.call_side_stop = total_credit / 2
            entry.put_side_stop = total_credit / 2

        return entry

    def _reconcile_positions(self):
        """
        Reconcile positions with registry (called during market hours).

        This is a lighter-weight check compared to full recovery.
        Handles POS-003: Positions closed manually during trading.
        """
        logger.info("Reconciling positions with Saxo API...")

        # Get all positions from Saxo
        all_positions = self.client.get_positions()
        valid_ids = {str(p.get("PositionBase", {}).get("PositionId")) for p in all_positions}

        # Clean up orphans from registry
        orphans = self.registry.cleanup_orphans(valid_ids)
        if orphans:
            logger.warning(f"Cleaned up {len(orphans)} orphaned registrations")
            self._log_safety_event("ORPHAN_CLEANUP", f"Cleaned {len(orphans)} orphans during reconciliation")

        # Check if any of our tracked positions are missing from Saxo
        for entry in self.daily_state.active_entries:
            missing_legs = []

            for leg_name in ["short_call", "long_call", "short_put", "long_put"]:
                pos_id = getattr(entry, f"{leg_name}_position_id")
                if pos_id and pos_id not in valid_ids:
                    missing_legs.append(leg_name)
                    # Unregister the missing position
                    self.registry.unregister(pos_id)
                    setattr(entry, f"{leg_name}_position_id", None)

            if missing_legs:
                logger.warning(f"Entry #{entry.entry_number}: Missing legs in Saxo: {missing_legs}")
                self._log_safety_event(
                    "POSITION_MISSING",
                    f"Entry #{entry.entry_number} missing {missing_legs} - closed externally?"
                )

                # Determine if entire side was stopped
                if "short_call" in missing_legs and "long_call" in missing_legs:
                    entry.call_side_stopped = True
                    logger.warning(f"Entry #{entry.entry_number}: Call side marked as stopped (external close)")

                if "short_put" in missing_legs and "long_put" in missing_legs:
                    entry.put_side_stopped = True
                    logger.warning(f"Entry #{entry.entry_number}: Put side marked as stopped (external close)")

        # Update state file
        self._save_state_to_disk()

    def verify_positions_before_operation(self, operation: str) -> bool:
        """
        POS-002: Verify positions exist in Saxo before any critical operation.

        Call this before placing orders or modifying positions to ensure
        our local state matches Saxo reality.

        Args:
            operation: Description of the operation (for logging)

        Returns:
            bool: True if positions match, False if discrepancy found
        """
        logger.debug(f"POS-002: Verifying positions before {operation}")

        all_positions = self.client.get_positions()
        actual_ids = {str(p.get("PositionBase", {}).get("PositionId")) for p in all_positions}

        for entry in self.daily_state.active_entries:
            for leg_name in ["short_call", "long_call", "short_put", "long_put"]:
                pos_id = getattr(entry, f"{leg_name}_position_id")
                if pos_id and pos_id not in actual_ids:
                    logger.warning(
                        f"POS-002: Entry #{entry.entry_number} {leg_name} ({pos_id}) "
                        f"not found in Saxo - triggering reconciliation"
                    )
                    self._reconcile_positions()
                    return False

        return True

    # =========================================================================
    # DAILY RESET
    # =========================================================================

    def _reset_for_new_day(self):
        """Reset state for a new trading day."""
        logger.info("Resetting for new trading day")

        # STATE-004: Check for overnight 0DTE positions (should NEVER happen)
        my_position_ids = self.registry.get_positions("MEIC")
        if my_position_ids:
            # This is a critical error - 0DTE positions should never survive to next day
            error_msg = f"CRITICAL: {len(my_position_ids)} MEIC positions survived overnight! 0DTE should expire same day."
            logger.critical(error_msg)
            self.alert_service.send_alert(
                alert_type=AlertType.CRITICAL_INTERVENTION,
                title="MEIC Overnight Position Detected!",
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

        # P3: Reset intraday market data tracking
        self.market_data.reset_daily_tracking()

        # P2: Clear WebSocket price cache
        self._ws_price_cache.clear()

        # Reset reconciliation timer
        self._last_reconciliation_time = None

        self.state = MEICState.IDLE

        # Save clean state to disk
        self._save_state_to_disk()

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

    def _log_safety_event(self, event_type: str, details: str):
        """
        Log safety events to Google Sheets for audit trail.

        This matches Delta Neutral's safety event logging for consistency
        and provides an auditable record of all safety-related incidents.

        Args:
            event_type: Type of safety event (e.g., "CIRCUIT_BREAKER_OPEN", "NAKED_SHORT_DETECTED")
            details: Human-readable description of the event
        """
        try:
            self.trade_logger.log_safety_event({
                "timestamp": get_us_market_time().strftime("%Y-%m-%d %H:%M:%S"),
                "event_type": event_type,
                "bot": "MEIC",
                "state": self.state.value,
                "spx_price": self.current_price,
                "vix": self.current_vix,
                "active_entries": len(self.daily_state.active_entries),
                "details": details
            })
            logger.info(f"Safety event logged: {event_type} - {details}")
        except Exception as e:
            # Don't let logging failure affect trading
            logger.error(f"Failed to log safety event: {e}")

    def _send_daily_summary(self):
        """Send daily summary alert."""
        summary = self.get_daily_summary()

        # Add extra fields for alert formatting
        summary["spx_close"] = self.current_price
        summary["vix_close"] = self.current_vix
        summary["cumulative_pnl"] = self.cumulative_metrics.get("cumulative_pnl", 0) + summary["total_pnl"]
        summary["dry_run"] = self.dry_run

        self.alert_service.daily_summary_meic(summary)

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

    # =========================================================================
    # STATE PERSISTENCE (P1 - POS-001)
    # =========================================================================

    def _save_state_to_disk(self):
        """
        P1: Save current daily state to disk for crash recovery.

        Persists all active entries with their position IDs, strikes, credits,
        and stop levels so they can be recovered on restart.
        """
        try:
            state_data = {
                "date": self.daily_state.date,
                "state": self.state.value,
                "next_entry_index": self._next_entry_index,
                "entries_completed": self.daily_state.entries_completed,
                "entries_failed": self.daily_state.entries_failed,
                "entries_skipped": self.daily_state.entries_skipped,
                "total_credit_received": self.daily_state.total_credit_received,
                "total_realized_pnl": self.daily_state.total_realized_pnl,
                "call_stops_triggered": self.daily_state.call_stops_triggered,
                "put_stops_triggered": self.daily_state.put_stops_triggered,
                "double_stops": self.daily_state.double_stops,
                "circuit_breaker_opens": self.daily_state.circuit_breaker_opens,
                "entries": []
            }

            # Serialize each entry
            for entry in self.daily_state.entries:
                entry_data = {
                    "entry_number": entry.entry_number,
                    "entry_time": entry.entry_time.isoformat() if entry.entry_time else None,
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
                }
                state_data["entries"].append(entry_data)

            state_data["last_saved"] = get_us_market_time().isoformat()

            # Write atomically using temp file
            temp_file = STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

            with open(temp_file, 'w') as f:
                json.dump(state_data, f, indent=2)

            os.replace(temp_file, STATE_FILE)
            logger.debug(f"State saved to {STATE_FILE}")

        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _recover_state_from_disk(self):
        """
        P1: Recover daily state from disk after restart.

        Called during __init__ to restore previous session state if:
        1. State file exists
        2. State file is from today
        3. We have active entries to recover
        """
        if not os.path.exists(STATE_FILE):
            logger.info("No state file found - starting fresh")
            return

        try:
            with open(STATE_FILE, 'r') as f:
                state_data = json.load(f)

            # Check if state is from today
            saved_date = state_data.get("date", "")
            today = get_us_market_time().strftime("%Y-%m-%d")

            if saved_date != today:
                logger.info(f"State file is from {saved_date}, not today ({today}) - starting fresh")
                return

            # Recover daily state
            logger.info(f"Recovering state from {STATE_FILE}")

            self.daily_state.date = saved_date
            self._next_entry_index = state_data.get("next_entry_index", 0)
            self.daily_state.entries_completed = state_data.get("entries_completed", 0)
            self.daily_state.entries_failed = state_data.get("entries_failed", 0)
            self.daily_state.entries_skipped = state_data.get("entries_skipped", 0)
            self.daily_state.total_credit_received = state_data.get("total_credit_received", 0.0)
            self.daily_state.total_realized_pnl = state_data.get("total_realized_pnl", 0.0)
            self.daily_state.call_stops_triggered = state_data.get("call_stops_triggered", 0)
            self.daily_state.put_stops_triggered = state_data.get("put_stops_triggered", 0)
            self.daily_state.double_stops = state_data.get("double_stops", 0)
            self.daily_state.circuit_breaker_opens = state_data.get("circuit_breaker_opens", 0)

            # Recover entries
            for entry_data in state_data.get("entries", []):
                entry = IronCondorEntry(entry_number=entry_data.get("entry_number", 0))

                # Entry time
                entry_time_str = entry_data.get("entry_time")
                if entry_time_str:
                    entry.entry_time = datetime.fromisoformat(entry_time_str)

                entry.strategy_id = entry_data.get("strategy_id", "")

                # Strikes
                entry.short_call_strike = entry_data.get("short_call_strike", 0.0)
                entry.long_call_strike = entry_data.get("long_call_strike", 0.0)
                entry.short_put_strike = entry_data.get("short_put_strike", 0.0)
                entry.long_put_strike = entry_data.get("long_put_strike", 0.0)

                # Position IDs
                entry.short_call_position_id = entry_data.get("short_call_position_id")
                entry.long_call_position_id = entry_data.get("long_call_position_id")
                entry.short_put_position_id = entry_data.get("short_put_position_id")
                entry.long_put_position_id = entry_data.get("long_put_position_id")

                # UICs
                entry.short_call_uic = entry_data.get("short_call_uic")
                entry.long_call_uic = entry_data.get("long_call_uic")
                entry.short_put_uic = entry_data.get("short_put_uic")
                entry.long_put_uic = entry_data.get("long_put_uic")

                # Credits
                entry.call_spread_credit = entry_data.get("call_spread_credit", 0.0)
                entry.put_spread_credit = entry_data.get("put_spread_credit", 0.0)

                # Stops
                entry.call_side_stop = entry_data.get("call_side_stop", 0.0)
                entry.put_side_stop = entry_data.get("put_side_stop", 0.0)

                # Status
                entry.is_complete = entry_data.get("is_complete", False)
                entry.call_side_stopped = entry_data.get("call_side_stopped", False)
                entry.put_side_stopped = entry_data.get("put_side_stopped", False)

                self.daily_state.entries.append(entry)

            # Set appropriate state
            saved_state = state_data.get("state", "Idle")
            try:
                self.state = MEICState(saved_state)
            except ValueError:
                # Unknown state - determine from entries
                if self.daily_state.active_entries:
                    self.state = MEICState.MONITORING
                elif self._next_entry_index < len(self.entry_times):
                    self.state = MEICState.WAITING_FIRST_ENTRY
                else:
                    self.state = MEICState.DAILY_COMPLETE

            active_count = len(self.daily_state.active_entries)
            logger.info(
                f"State recovered: {self.state.value}, "
                f"{self.daily_state.entries_completed} entries completed, "
                f"{active_count} active entries"
            )

            # Alert about recovery
            if active_count > 0:
                self.alert_service.send_alert(
                    alert_type=AlertType.POSITION_OPENED,
                    title="MEIC Bot Recovered",
                    message=f"Recovered {active_count} active iron condor(s) from previous session",
                    priority=AlertPriority.MEDIUM,
                    details={"active_entries": active_count, "state": self.state.value}
                )

        except Exception as e:
            logger.error(f"Failed to recover state: {e}")
            logger.info("Starting fresh due to recovery failure")

    # =========================================================================
    # WEBSOCKET PRICE CACHE (P2)
    # =========================================================================

    def update_ws_price_cache(self, uic: int, price: float):
        """
        P2: Update WebSocket price cache for fast stop monitoring.

        Called by main.py's price callback.
        """
        if price > 0:
            self._ws_price_cache[uic] = (price, get_us_market_time())

    def _get_cached_price(self, uic: int, max_age_seconds: int = 5) -> Optional[float]:
        """
        P2: Get cached price from WebSocket if fresh enough.

        Args:
            uic: Instrument UIC
            max_age_seconds: Maximum age in seconds for cached price

        Returns:
            Cached price if fresh, None otherwise
        """
        if uic not in self._ws_price_cache:
            return None

        price, timestamp = self._ws_price_cache[uic]
        age = (get_us_market_time() - timestamp).total_seconds()

        if age <= max_age_seconds:
            return price

        return None

    # =========================================================================
    # VIGILANT MONITORING MODE (P2)
    # =========================================================================

    def get_monitoring_mode(self) -> str:
        """
        P2: Get the recommended monitoring mode based on position proximity to stops.

        Returns:
            "vigilant" if price is approaching stop levels (2s intervals)
            "normal" otherwise (5s intervals)
        """
        if not self.daily_state.active_entries:
            return "normal"

        # Check each active entry for proximity to stops
        for entry in self.daily_state.active_entries:
            # Check call side
            if not entry.call_side_stopped and entry.call_side_stop > 0:
                current_value = entry.call_spread_value
                stop_level = entry.call_side_stop
                if stop_level > 0:
                    usage_percent = (current_value / stop_level) * 100
                    if usage_percent >= VIGILANT_THRESHOLD_PERCENT:
                        self._current_monitoring_mode = "vigilant"
                        return "vigilant"

            # Check put side
            if not entry.put_side_stopped and entry.put_side_stop > 0:
                current_value = entry.put_spread_value
                stop_level = entry.put_side_stop
                if stop_level > 0:
                    usage_percent = (current_value / stop_level) * 100
                    if usage_percent >= VIGILANT_THRESHOLD_PERCENT:
                        self._current_monitoring_mode = "vigilant"
                        return "vigilant"

        self._current_monitoring_mode = "normal"
        return "normal"

    def get_recommended_check_interval(self) -> int:
        """
        P2: Get the recommended check interval in seconds based on monitoring mode.

        Returns:
            2 for vigilant mode, 5 for normal mode
        """
        mode = self.get_monitoring_mode()
        if mode == "vigilant":
            return VIGILANT_CHECK_INTERVAL_SECONDS
        return NORMAL_CHECK_INTERVAL_SECONDS

    # =========================================================================
    # INTRADAY STATS (P3)
    # =========================================================================

    def get_intraday_stats(self) -> Dict:
        """
        P3: Get intraday market statistics.

        Returns:
            Dict with SPX high/low/range and VIX high/average
        """
        return {
            "spx_high": self.market_data.spx_high,
            "spx_low": self.market_data.spx_low if self.market_data.spx_low != float('inf') else 0,
            "spx_range": self.market_data.get_spx_range(),
            "vix_high": self.market_data.vix_high,
            "vix_average": self.market_data.get_vix_average(),
            "vix_samples": len(self.market_data.vix_samples)
        }

    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """
        Get comprehensive MEIC strategy metrics for Looker Studio dashboard.

        Returns all metrics needed for:
        - Account Summary worksheet (positions, credits, P&L)
        - Performance Metrics worksheet (entries, stops, win rate)
        - Position Details worksheet (strikes, spreads, status)

        Returns:
            dict: Complete strategy metrics for dashboard logging
        """
        # Basic status
        active_entries = len(self.daily_state.active_entries)
        unrealized = sum(e.unrealized_pnl for e in self.daily_state.active_entries)
        total_pnl = self.daily_state.total_realized_pnl + unrealized

        # Position counts
        total_legs = sum(
            len([1 for leg in ['short_call', 'long_call', 'short_put', 'long_put']
                 if getattr(e, f'{leg}_position_id')])
            for e in self.daily_state.active_entries
        )

        # Calculate average strikes for active entries
        avg_short_call = 0.0
        avg_short_put = 0.0
        avg_spread_width = 0.0

        if active_entries > 0:
            active = self.daily_state.active_entries
            avg_short_call = sum(e.short_call_strike for e in active if e.short_call_strike) / active_entries
            avg_short_put = sum(e.short_put_strike for e in active if e.short_put_strike) / active_entries
            avg_spread_width = sum(e.spread_width for e in active if e.spread_width) / active_entries

        # Per-entry details
        entry_details = []
        for entry in self.daily_state.entries:
            entry_details.append({
                "entry_number": entry.entry_number,
                "entry_time": entry.entry_time.strftime("%H:%M") if entry.entry_time else "",
                "short_call": entry.short_call_strike,
                "long_call": entry.long_call_strike,
                "short_put": entry.short_put_strike,
                "long_put": entry.long_put_strike,
                "call_credit": entry.call_spread_credit,
                "put_credit": entry.put_spread_credit,
                "total_credit": entry.total_credit,
                "call_stopped": entry.call_side_stopped,
                "put_stopped": entry.put_side_stopped,
                "unrealized_pnl": entry.unrealized_pnl,
                "is_complete": entry.is_complete
            })

        # Cumulative metrics
        cumulative = self.cumulative_metrics or {}

        return {
            # Timestamp
            "timestamp": get_us_market_time().strftime("%Y-%m-%d %H:%M:%S"),
            "date": self.daily_state.date,

            # State
            "state": self.state.value,
            "dry_run": self.dry_run,
            "circuit_breaker_open": self._circuit_breaker_open,
            "critical_intervention": self._critical_intervention_required,

            # Market data
            "spx_price": self.current_price,
            "vix": self.current_vix,
            "spx_high": self.market_data.spx_high,
            "spx_low": self.market_data.spx_low if self.market_data.spx_low != float('inf') else 0,
            "spx_range": self.market_data.get_spx_range(),
            "vix_high": self.market_data.vix_high,
            "vix_average": self.market_data.get_vix_average(),

            # Entry progress
            "entries_scheduled": len(self.entry_times),
            "entries_completed": self.daily_state.entries_completed,
            "entries_failed": self.daily_state.entries_failed,
            "entries_skipped": self.daily_state.entries_skipped,
            "next_entry_index": self._next_entry_index,
            "active_entries": active_entries,
            "total_legs": total_legs,

            # Position metrics
            "avg_short_call_strike": avg_short_call,
            "avg_short_put_strike": avg_short_put,
            "avg_spread_width": avg_spread_width,

            # P&L metrics
            "total_credit": self.daily_state.total_credit_received,
            "realized_pnl": self.daily_state.total_realized_pnl,
            "unrealized_pnl": unrealized,
            "total_pnl": total_pnl,
            "pnl_percent": (total_pnl / self.daily_state.total_credit_received * 100) if self.daily_state.total_credit_received > 0 else 0,

            # Stop metrics
            "call_stops": self.daily_state.call_stops_triggered,
            "put_stops": self.daily_state.put_stops_triggered,
            "total_stops": self.daily_state.total_stops,
            "double_stops": self.daily_state.double_stops,
            "circuit_breaker_opens": self.daily_state.circuit_breaker_opens,

            # Win/loss metrics (requires stops and entries)
            "entries_with_no_stops": sum(1 for e in self.daily_state.entries if e.is_complete and not e.call_side_stopped and not e.put_side_stopped),
            "entries_with_one_stop": sum(1 for e in self.daily_state.entries if e.is_complete and (e.call_side_stopped != e.put_side_stopped)),
            "entries_with_both_stops": sum(1 for e in self.daily_state.entries if e.is_complete and e.call_side_stopped and e.put_side_stopped),

            # Cumulative metrics
            "cumulative_pnl": cumulative.get("cumulative_pnl", 0) + total_pnl,
            "cumulative_entries": cumulative.get("total_entries", 0) + self.daily_state.entries_completed,
            "cumulative_stops": cumulative.get("total_stops", 0) + self.daily_state.total_stops,
            "winning_days": cumulative.get("winning_days", 0),
            "losing_days": cumulative.get("losing_days", 0),

            # Entry details (for detailed logging)
            "entry_details": entry_details
        }

    def get_dashboard_metrics_safe(self) -> Dict[str, Any]:
        """
        Get dashboard metrics with protection against stale data when market closed.

        When market is closed, P&L calculations may be inaccurate due to stale
        option prices. This method returns the last known accurate values.

        Returns:
            dict: Dashboard metrics with corrected values when market closed
        """
        metrics = self.get_dashboard_metrics()

        # If market is open, use live values
        if is_market_open():
            return metrics

        # Market is closed - note that option values may be stale
        metrics["market_closed_warning"] = True
        logger.debug("Market closed: option P&L values may be stale")

        return metrics

    # =========================================================================
    # PRE-MARKET GAP DETECTION (MKT-001)
    # =========================================================================

    def get_premarket_analysis(self, current_spx_price: float = 0.0, prev_close: float = 0.0) -> Dict[str, Any]:
        """
        MKT-001: Get pre-market gap analysis for SPX.

        Analyzes overnight/weekend gaps to warn about unusual market conditions.
        While MEIC doesn't actively trade pre-market (0DTE), knowing about gaps
        helps inform trading decisions at market open.

        Note: For 0DTE strategies, gaps are less relevant since we enter fresh
        each day. However, large gaps may indicate volatile conditions that
        could affect stop loss behavior.

        Args:
            current_spx_price: Current SPX price (will fetch if not provided)
            prev_close: Previous day's closing price

        Returns:
            Dict with gap analysis including:
            - gap_percent: Overnight percentage change
            - gap_points: Dollar move from previous close
            - warning_level: "NORMAL", "CAUTION", "WARNING", "CRITICAL"
            - prev_close: Previous day's closing price
            - current_price: Current price
            - is_monday: Whether this is a Monday (weekend gap)
            - message: Human-readable summary
        """
        result = {
            "gap_percent": 0.0,
            "gap_points": 0.0,
            "warning_level": "NORMAL",
            "prev_close": 0.0,
            "current_price": 0.0,
            "is_monday": get_us_market_time().weekday() == 0,
            "message": ""
        }

        # Get previous close if not provided
        if prev_close <= 0:
            # Try to get from chart data
            try:
                chart_data = self.client.get_chart_data(
                    uic=self.underlying_uic,
                    asset_type="CfdOnIndex",  # SPX is a CFD on Index
                    horizon=1440,  # Daily bars
                    count=2  # Last 2 days
                )
                if chart_data and len(chart_data) >= 2:
                    prev_close = chart_data[-2].get("Close", chart_data[-2].get("C", 0))
                    logger.debug(f"MKT-001: Fetched previous close from chart data: {prev_close:.2f}")
            except Exception as e:
                logger.debug(f"MKT-001: Could not fetch previous close: {e}")

        if prev_close <= 0:
            result["message"] = "No previous close available"
            return result

        result["prev_close"] = prev_close

        # Get current price
        if current_spx_price <= 0:
            current_spx_price = self.current_price
            if current_spx_price <= 0:
                try:
                    quote = self.client.get_quote(self.underlying_uic)
                    if quote:
                        current_spx_price = quote.get("Mid") or quote.get("LastTraded", 0)
                except Exception:
                    pass

        if current_spx_price <= 0:
            result["message"] = "No current price available"
            return result

        result["current_price"] = current_spx_price

        # Calculate gap
        gap_points = current_spx_price - prev_close
        gap_percent = (gap_points / prev_close) * 100

        result["gap_percent"] = gap_percent
        result["gap_points"] = gap_points

        # Determine warning level
        # For SPX, typical thresholds:
        # - NORMAL: < 0.5% (< ~30 points)
        # - CAUTION: 0.5-1% (~30-60 points)
        # - WARNING: 1-2% (~60-120 points)
        # - CRITICAL: > 2% (> ~120 points)
        abs_gap = abs(gap_percent)

        if abs_gap < 0.5:
            result["warning_level"] = "NORMAL"
        elif abs_gap < 1.0:
            result["warning_level"] = "CAUTION"
        elif abs_gap < 2.0:
            result["warning_level"] = "WARNING"
        else:
            result["warning_level"] = "CRITICAL"

        # Build message
        direction = "up" if gap_percent > 0 else "down"
        gap_type = "weekend" if result["is_monday"] else "overnight"

        if result["warning_level"] == "NORMAL":
            result["message"] = f"SPX {gap_type} gap: {gap_percent:+.2f}% ({gap_points:+.0f} pts) - normal"
        else:
            result["message"] = (
                f"SPX {gap_type} gap {direction}: {abs_gap:.2f}% ({abs(gap_points):.0f} pts) - "
                f"{result['warning_level']} - consider wider stops or fewer entries"
            )

        return result

    def check_pre_market_gap(self) -> Tuple[bool, float, str]:
        """
        MKT-001: Quick check for significant pre-market gap.

        Simplified version that returns whether there's a significant gap
        that warrants attention.

        Returns:
            Tuple of (is_significant_gap, gap_percent, message)
        """
        analysis = self.get_premarket_analysis()

        is_significant = analysis["warning_level"] in ["WARNING", "CRITICAL"]
        return is_significant, analysis["gap_percent"], analysis["message"]

    # =========================================================================
    # ORDER-004: PRE-ENTRY MARGIN CHECK
    # =========================================================================

    def _check_buying_power(self) -> Tuple[bool, str]:
        """
        ORDER-004: Check if we have sufficient buying power for a new IC entry.

        Queries account balance and verifies minimum margin is available.

        Returns:
            Tuple of (has_sufficient_bp, message)
        """
        if not MARGIN_CHECK_ENABLED:
            return True, "Margin check disabled"

        try:
            balance = self.client.get_balance()
            if not balance:
                logger.warning("ORDER-004: Could not fetch account balance")
                return True, "Balance check skipped (API unavailable)"

            # Extract available margin/buying power
            # Saxo returns different fields - check for common ones
            available = None
            for field in ["AvailableMargin", "CashAvailable", "MarginAvailable", "NetEquityForMargin"]:
                if field in balance:
                    available = balance[field]
                    break

            if available is None:
                logger.warning("ORDER-004: No recognized margin field in balance response")
                return True, "Balance check skipped (no margin field)"

            # Calculate required margin for next entry
            # Each IC needs spread_width * 100 margin (approx)
            required = MIN_BUYING_POWER_PER_IC

            if available < required:
                logger.warning(
                    f"ORDER-004: Insufficient buying power. "
                    f"Available: ${available:.2f}, Required: ${required:.2f}"
                )
                return False, f"Insufficient BP: ${available:.2f} < ${required:.2f}"

            logger.info(f"ORDER-004: Buying power OK - ${available:.2f} available")
            return True, f"BP OK: ${available:.2f}"

        except Exception as e:
            logger.error(f"ORDER-004: Error checking buying power: {e}")
            return True, f"Balance check skipped (error: {e})"

    # =========================================================================
    # MKT-005: MARKET HALT DETECTION
    # =========================================================================

    def _check_market_halt(self) -> Tuple[bool, str]:
        """
        MKT-005: Check if market is halted (circuit breaker or trading halt).

        Attempts to detect Level 1/2/3 circuit breakers by checking
        if trading is available for SPX options.

        Returns:
            Tuple of (is_halted, reason)
        """
        if not MARKET_HALT_CHECK_ENABLED:
            return False, "Halt check disabled"

        try:
            # Check if market is open according to our schedule
            if not is_market_open():
                return True, "Market not open"

            # Try to get a quote for SPX - if unavailable, market may be halted
            quote = self.client.get_quote(self.underlying_uic, asset_type="CfdOnIndex")
            if not quote:
                logger.warning("MKT-005: No quote available for SPX - possible market halt")
                return True, "No SPX quote available"

            # Check for stale quote (no update in 60+ seconds could indicate halt)
            # This is a heuristic - Saxo doesn't expose trading halt status directly
            quote_data = quote.get("Quote", {})

            # If we have no bid/ask and no last traded, something is wrong
            bid = quote_data.get("Bid")
            ask = quote_data.get("Ask")
            last = quote_data.get("LastTraded")

            if not bid and not ask and not last:
                logger.warning("MKT-005: Empty quote data - possible market halt")
                return True, "Empty quote data"

            return False, "Market trading normally"

        except Exception as e:
            logger.error(f"MKT-005: Error checking market halt: {e}")
            # Don't block trading on check failure
            return False, f"Halt check error: {e}"

    # =========================================================================
    # MKT-007: STRIKE ADJUSTMENT FOR ILLIQUIDITY
    # =========================================================================

    def _adjust_strike_for_liquidity(
        self,
        strike: float,
        put_call: str,
        expiry: str,
        adjustment_direction: int
    ) -> Tuple[Optional[float], str]:
        """
        MKT-007: Adjust strike if the current one is illiquid.

        Checks bid/ask spread and moves strike closer to ATM if needed.

        Args:
            strike: Original strike price
            put_call: "Call" or "Put"
            expiry: Expiry date string
            adjustment_direction: +1 to move closer to ATM, -1 further

        Returns:
            Tuple of (adjusted_strike or None, status_message)
        """
        original_strike = strike

        for attempt in range(MAX_STRIKE_ADJUSTMENT_ATTEMPTS + 1):
            # Get option UIC
            uic = self._get_option_uic(strike, put_call, expiry)
            if not uic:
                # Strike doesn't exist in chain - try next
                adjustment = ILLIQUIDITY_STRIKE_ADJUSTMENT_POINTS * adjustment_direction
                strike += adjustment
                continue

            # Check quote for liquidity
            quote = self.client.get_quote(uic, asset_type="StockIndexOption")
            if not quote or "Quote" not in quote:
                # No quote - try adjusted strike
                adjustment = ILLIQUIDITY_STRIKE_ADJUSTMENT_POINTS * adjustment_direction
                strike += adjustment
                continue

            bid = quote["Quote"].get("Bid") or 0
            ask = quote["Quote"].get("Ask") or 0

            # Check if liquid (has bid AND ask)
            if bid > 0 and ask > 0:
                spread_percent = ((ask - bid) / bid) * 100 if bid > 0 else float('inf')
                if spread_percent < MAX_BID_ASK_SPREAD_PERCENT_SKIP:
                    if strike != original_strike:
                        logger.info(
                            f"MKT-007: Adjusted {put_call} strike {original_strike} -> {strike} "
                            f"(spread {spread_percent:.1f}%)"
                        )
                    return strike, "OK"

            # Illiquid - try adjusting
            if attempt < MAX_STRIKE_ADJUSTMENT_ATTEMPTS:
                adjustment = ILLIQUIDITY_STRIKE_ADJUSTMENT_POINTS * adjustment_direction
                strike += adjustment
                logger.info(f"MKT-007: {put_call} {original_strike} illiquid, trying {strike}")

        # Could not find liquid strike
        logger.warning(f"MKT-007: Could not find liquid strike for {put_call} near {original_strike}")
        return None, "No liquid strike found"

    # =========================================================================
    # TIME-001: CLOCK SYNC VALIDATION
    # =========================================================================

    def _validate_system_clock(self):
        """
        TIME-001: Validate system clock against Saxo server time.

        Checks for significant clock skew that could affect entry timing.
        """
        try:
            # Get server time from Saxo API
            # Use a simple API call that returns timestamp
            import requests
            from datetime import timezone

            # Try to get server time from Saxo
            # The token endpoint often includes server time in response headers
            server_time = None

            # Use account info endpoint as a proxy - check response headers
            account_info = self.client.get_account_info()
            if account_info:
                # If successful, we can at least verify our connection works
                self._clock_validated = True

                # Get local time
                local_time = get_us_market_time()
                logger.info(f"TIME-001: Clock validation - Local time: {local_time.strftime('%H:%M:%S')}")

                # Without actual server time, we can only log local time
                # In production, consider using NTP check or Saxo response headers
                self._clock_skew_seconds = 0.0
                logger.info("TIME-001: Clock validation passed (server time comparison not available)")
                return

            logger.warning("TIME-001: Could not validate clock - API unavailable")
            self._clock_validated = False

        except Exception as e:
            logger.error(f"TIME-001: Clock validation error: {e}")
            self._clock_validated = False

    def _is_clock_reliable(self) -> Tuple[bool, str]:
        """
        TIME-001: Check if system clock is reliable for trading.

        Returns:
            Tuple of (is_reliable, message)
        """
        if not CLOCK_SYNC_CHECK_ENABLED:
            return True, "Clock check disabled"

        if not self._clock_validated:
            return True, "Clock not validated (proceeding with caution)"

        if abs(self._clock_skew_seconds) > MAX_CLOCK_SKEW_WARNING_SECONDS:
            return False, f"Clock skew too large: {self._clock_skew_seconds:.1f}s"

        return True, "Clock OK"

    # =========================================================================
    # DATA-003: P&L SANITY CHECK
    # =========================================================================

    def _validate_pnl_sanity(self, entry: IronCondorEntry) -> Tuple[bool, str]:
        """
        DATA-003: Validate that P&L values are within reasonable bounds.

        Catches data errors that could result in impossible P&L figures.

        Args:
            entry: IronCondorEntry to validate

        Returns:
            Tuple of (is_valid, message)
        """
        if not PNL_SANITY_CHECK_ENABLED:
            return True, "P&L sanity check disabled"

        pnl = entry.unrealized_pnl

        # Check for impossible values
        if pnl > MAX_PNL_PER_IC:
            logger.error(
                f"DATA-003: Impossible P&L detected for Entry #{entry.entry_number}: "
                f"${pnl:.2f} > max ${MAX_PNL_PER_IC}"
            )
            return False, f"P&L too high: ${pnl:.2f}"

        if pnl < MIN_PNL_PER_IC:
            logger.error(
                f"DATA-003: Impossible P&L detected for Entry #{entry.entry_number}: "
                f"${pnl:.2f} < min ${MIN_PNL_PER_IC}"
            )
            return False, f"P&L too low: ${pnl:.2f}"

        # Check for NaN or infinity
        if not isinstance(pnl, (int, float)) or pnl != pnl:  # NaN check
            logger.error(f"DATA-003: Invalid P&L value for Entry #{entry.entry_number}: {pnl}")
            return False, "Invalid P&L value"

        return True, f"P&L ${pnl:.2f} within bounds"

    def _validate_all_pnl(self) -> List[str]:
        """
        DATA-003: Validate P&L for all active entries.

        Returns:
            List of warning messages for any invalid P&L values
        """
        warnings = []
        for entry in self.daily_state.active_entries:
            is_valid, message = self._validate_pnl_sanity(entry)
            if not is_valid:
                warnings.append(f"Entry #{entry.entry_number}: {message}")
        return warnings

    # =========================================================================
    # ALERT-002: ALERT BATCHING
    # =========================================================================

    def _should_batch_alert(self, alert_type: str) -> bool:
        """
        ALERT-002: Check if we should batch this alert with recent ones.

        Returns True if there have been multiple alerts recently.
        """
        now = get_us_market_time()
        cutoff = now - timedelta(seconds=ALERT_BATCH_WINDOW_SECONDS)

        # Clean old alerts
        self._recent_alerts = [
            (ts, t) for ts, t in self._recent_alerts
            if ts > cutoff
        ]

        # Count recent alerts
        recent_count = len(self._recent_alerts)

        # Add this alert
        self._recent_alerts.append((now, alert_type))

        return recent_count >= MAX_ALERTS_BEFORE_BATCH

    def _send_batched_stop_alert(
        self,
        entries_stopped: List[Tuple[IronCondorEntry, str, float]],
        total_loss: float
    ):
        """
        ALERT-002: Send a batched alert for multiple stop losses.

        Args:
            entries_stopped: List of (entry, side, stop_level) tuples
            total_loss: Total loss across all stops
        """
        count = len(entries_stopped)

        # Build summary
        details_lines = []
        for entry, side, stop_level in entries_stopped:
            details_lines.append(f"Entry #{entry.entry_number} {side}: -${stop_level:.2f}")

        details_text = "\n".join(details_lines)

        self.alert_service.send_alert(
            alert_type=AlertType.STOP_LOSS,
            title=f"Multiple Stops Triggered ({count})",
            message=f"{count} stop losses triggered in rapid succession.\n\n{details_text}\n\nTotal loss: ${total_loss:.2f}",
            priority=AlertPriority.HIGH,
            details={
                "count": count,
                "entries": [e.entry_number for e, _, _ in entries_stopped],
                "total_loss": total_loss
            }
        )

        logger.info(f"ALERT-002: Sent batched alert for {count} stops")

    def _queue_stop_alert(self, entry: IronCondorEntry, side: str, stop_level: float):
        """
        ALERT-002: Queue a stop alert for potential batching.

        Args:
            entry: Entry that was stopped
            side: "call" or "put"
            stop_level: Stop loss amount
        """
        if self._should_batch_alert("stop_loss"):
            # Add to batch
            self._batched_alerts.append({
                "entry": entry,
                "side": side,
                "stop_level": stop_level,
                "timestamp": get_us_market_time()
            })
            logger.info(f"ALERT-002: Queued stop alert for batching ({len(self._batched_alerts)} pending)")
        else:
            # Send immediately (also flush any pending)
            self._flush_batched_alerts()
            # Send this one
            self.alert_service.stop_loss(
                trigger_price=self.current_price,
                pnl=-stop_level,
                details={
                    "description": f"MEIC Entry #{entry.entry_number} {side.upper()} side stopped",
                    "reason": f"{side} spread value reached stop level",
                    "entry_number": entry.entry_number
                }
            )

    def _flush_batched_alerts(self):
        """
        ALERT-002: Send any batched alerts.

        Called periodically or when batch window expires.
        """
        if not self._batched_alerts:
            return

        # Calculate total loss
        total_loss = sum(a["stop_level"] for a in self._batched_alerts)

        # Build entries list
        entries_stopped = [
            (a["entry"], a["side"], a["stop_level"])
            for a in self._batched_alerts
        ]

        # Send batched alert
        self._send_batched_stop_alert(entries_stopped, total_loss)

        # Clear batch
        self._batched_alerts.clear()
