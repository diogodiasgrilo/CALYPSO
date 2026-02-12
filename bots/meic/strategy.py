"""
strategy.py - MEIC (Multiple Entry Iron Condors) Strategy Implementation

This module implements Tammy Chambless's MEIC 0DTE strategy:
- 6 scheduled iron condor entries per day (10:05, 10:35, 11:05, 11:35, 12:05, 12:35 AM ET)
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
import math
import os
import time
import threading
from collections import deque
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, Dict, List, Any, Tuple, Deque, Set
from dataclasses import dataclass, field
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell, OrderType
from shared.alert_service import AlertService, AlertType, AlertPriority
from shared.market_hours import get_us_market_time, is_market_open, is_early_close_day
from shared.event_calendar import is_fomc_meeting_day
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

# ORDER-009: Retry delay to prevent API conflicts
# Without this, rapid retries cause 409 Conflict (stale order state) and 429 Rate Limit
ORDER_RETRY_DELAY_SECONDS = 2.0

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
# RATE LIMIT FIX (2026-02-04): Increased intervals to stay under Saxo's 120 req/min limit
# With 2 entries: 10 calls/cycle. At 6s interval = 600 cycles/hr = 6,000 calls/hr (under 7,200 limit)
# With 6 entries: 26 calls/cycle. At 15s interval = 240 cycles/hr = 6,240 calls/hr (under 7,200 limit)
VIGILANT_CHECK_INTERVAL_SECONDS = 5  # Was 2s - still faster when near stops
NORMAL_CHECK_INTERVAL_SECONDS = 10   # Was 3s - safe for up to 4 entries

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

# ORDER-006: Order size validation (bug protection)
# Prevents catastrophic losses from quantity calculation bugs
MAX_CONTRACTS_PER_ORDER = 10  # Max contracts in a single order
MAX_CONTRACTS_PER_UNDERLYING = 30  # Max total contracts (6 ICs × 4 legs × ~1 contract)

# ORDER-007: Fill price slippage monitoring
SLIPPAGE_WARNING_THRESHOLD_PERCENT = 5.0   # Warn at 5% slippage
SLIPPAGE_CRITICAL_THRESHOLD_PERCENT = 15.0  # Critical at 15% slippage

# EMERGENCY-001: Emergency close retry settings
EMERGENCY_CLOSE_MAX_ATTEMPTS = 5
EMERGENCY_CLOSE_RETRY_DELAY_SECONDS = 3

# ORDER-008: SPX Option Tick Size Rules (CBOE Official)
# Source: https://www.cboe.com/tradable_products/sp_500/spx_options/specifications/
# - Options trading below $3.00: Minimum tick of $0.05
# - Options $3.00 and above: Minimum tick of $0.10
SPX_TICK_SIZE_BELOW_3 = 0.05  # $0.05 for prices < $3.00
SPX_TICK_SIZE_ABOVE_3 = 0.10  # $0.10 for prices >= $3.00
SPX_TICK_THRESHOLD = 3.00     # Price threshold for tick size change


def round_to_spx_tick(price: float, round_up: bool = False) -> float:
    """
    Round a price to valid SPX option tick increments (CBOE rules).

    SPX options have different tick sizes based on price:
    - Below $3.00: $0.05 increments (e.g., $0.25, $0.30, $1.95, $2.90)
    - $3.00 and above: $0.10 increments (e.g., $3.00, $3.10, $5.50)

    Args:
        price: The price to round
        round_up: If True, always round up (for buys). If False, round to nearest.
                  For sells, caller should pass round_up=False and we round down
                  when the price is exactly between ticks.

    Returns:
        Price rounded to valid tick increment

    Note:
        FIX (2026-02-04): Added final round(result, 2) to fix floating point
        precision issues. E.g., 2.55 / 0.05 * 0.05 = 2.5500000000000003
        which Saxo rejects with PriceNotInTickSizeIncrements error.
    """
    if price <= 0:
        return 0.0

    # Determine tick size based on price level
    tick_size = SPX_TICK_SIZE_BELOW_3 if price < SPX_TICK_THRESHOLD else SPX_TICK_SIZE_ABOVE_3

    if round_up:
        # Round up to next tick (for aggressive buys)
        result = math.ceil(price / tick_size) * tick_size
    else:
        # Round to nearest tick
        result = round(price / tick_size) * tick_size

    # FIX: Round to 2 decimal places to fix floating point precision issues
    return round(result, 2)


# EMERGENCY-001: Spread validation for emergency closes
EMERGENCY_SPREAD_MAX_PERCENT = 50.0  # Max acceptable spread for emergency close
EMERGENCY_SPREAD_WAIT_SECONDS = 10  # Wait time for spread normalization
EMERGENCY_SPREAD_MAX_WAIT_ATTEMPTS = 3

# ACTIVITIES-001: Fill verification retry
ACTIVITIES_RETRY_ATTEMPTS = 3
ACTIVITIES_RETRY_DELAY_SECONDS = 1.0


# =============================================================================
# ENTRY SCHEDULE
# =============================================================================

DEFAULT_ENTRY_TIMES = [
    dt_time(10, 5),   # Entry 1: 10:05 AM ET
    dt_time(10, 35),  # Entry 2: 10:35 AM ET
    dt_time(11, 5),   # Entry 3: 11:05 AM ET
    dt_time(11, 35),  # Entry 4: 11:35 AM ET
    dt_time(12, 5),   # Entry 5: 12:05 PM ET
    dt_time(12, 35),  # Entry 6: 12:35 PM ET
]

# Note: Early close day handling is done dynamically in _parse_entry_times()
# by filtering DEFAULT_ENTRY_TIMES to only include entries before 11:00 AM.

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

    Side Status Tracking (Fix #46, #47):
    Each side (call/put) can be in one of these states:
    - **Active**: Position is open and being monitored (default)
    - **Stopped**: Side was opened but hit stop loss (LOSS)
    - **Expired**: Side was opened and expired worthless (PROFIT - kept credit)
    - **Skipped**: Side was never opened (MEIC-TF one-sided entry, NO P&L IMPACT)

    The flags are mutually exclusive - a side can only be in one state at a time.
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
    call_side_stopped: bool = False  # Call spread was stopped out (LOSS)
    put_side_stopped: bool = False   # Put spread was stopped out (LOSS)
    call_side_expired: bool = False  # Call spread expired worthless (PROFIT - kept credit)
    put_side_expired: bool = False   # Put spread expired worthless (PROFIT - kept credit)
    call_side_skipped: bool = False  # Call side was never opened (MEIC-TF one-sided entry)
    put_side_skipped: bool = False   # Put side was never opened (MEIC-TF one-sided entry)
    strategy_id: str = ""  # For Position Registry tracking

    # MKT-010: Wing illiquidity tracking (set by MKT-008 adjustment)
    # If a wing was illiquid and adjusted, that side is far OTM = SAFE
    # Used by MEIC-TF to place one-sided entries on the safe side
    call_wing_illiquid: bool = False  # Call wing was adjusted (calls far OTM = safe)
    put_wing_illiquid: bool = False   # Put wing was adjusted (puts far OTM = safe)

    # Fix #61: Position merge tracking
    # When two entries land on same strikes, Saxo merges them into one position
    # The older entry's position IDs become invalid, but credit is preserved
    call_side_merged: bool = False  # Call positions merged with another entry
    put_side_merged: bool = False   # Put positions merged with another entry

    # Commission tracking (display only - does not affect P&L calculations)
    # Open commission: $2.50 per leg × 4 legs = $10 per IC (charged on entry)
    # Close commission: $2.50 per leg × 2 legs per side (only charged when closed, not expired)
    open_commission: float = 0.0   # Commission paid to open this IC
    close_commission: float = 0.0  # Commission paid to close legs (accumulated on stops)

    # Fix #52: Contract size for multi-contract support
    # Stores the number of contracts for this entry (set at entry creation)
    # Used by spread_value properties and P&L calculations
    contracts: int = 1

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
        """Current value (cost to close) of call spread.

        Fix #52: Multiplies by contracts for multi-contract support.
        """
        # Buy back short, sell long
        # Multiply by 100 (option multiplier) and contracts
        return (self.short_call_price - self.long_call_price) * 100 * self.contracts

    @property
    def put_spread_value(self) -> float:
        """Current value (cost to close) of put spread.

        Fix #52: Multiplies by contracts for multi-contract support.
        """
        return (self.short_put_price - self.long_put_price) * 100 * self.contracts

    @property
    def unrealized_pnl(self) -> float:
        """
        Current unrealized P&L for this IC.

        Profit = Credit received - Cost to close

        FIX (2026-02-04): Corrected loss calculation for stopped sides.
        Previously used stop_level as loss, but actual loss = stop_level - credit.
        Example: stop=$250, credit=$125 per side, net loss=$125 (not $250).

        Fix #61/#62: Merged entries preserve their credit (transferred to surviving entry).
        For merged sides, treat as if the credit was kept (no loss, no cost to close).
        """
        # Fix #61: Handle merged entries
        # Merged sides have their credit preserved - count as profit, not loss
        call_merged = getattr(self, 'call_side_merged', False)
        put_merged = getattr(self, 'put_side_merged', False)

        # Check stopped status (note: merged overrides stopped)
        call_stopped = self.call_side_stopped and not call_merged
        put_stopped = self.put_side_stopped and not put_merged

        if call_stopped and put_stopped:
            # Both stopped - return realized loss
            # Net loss per side = stop_level - credit_for_that_side
            call_loss = self.call_side_stop - self.call_spread_credit
            put_loss = self.put_side_stop - self.put_spread_credit
            return -(call_loss + put_loss)
        elif call_stopped:
            # Call stopped (loss), put still open or merged
            loss_on_call = self.call_side_stop - self.call_spread_credit
            if put_merged:
                # Put merged - credit preserved (profit = credit)
                return self.put_spread_credit - loss_on_call
            else:
                # Put still open
                return (self.put_spread_credit - self.put_spread_value) - loss_on_call
        elif put_stopped:
            # Put stopped (loss), call still open or merged
            loss_on_put = self.put_side_stop - self.put_spread_credit
            if call_merged:
                # Call merged - credit preserved (profit = credit)
                return self.call_spread_credit - loss_on_put
            else:
                # Call still open
                return (self.call_spread_credit - self.call_spread_value) - loss_on_put
        elif call_merged or put_merged:
            # One or both sides merged, neither stopped
            call_pnl = self.call_spread_credit if call_merged else (self.call_spread_credit - self.call_spread_value)
            put_pnl = self.put_spread_credit if put_merged else (self.put_spread_credit - self.put_spread_value)
            return call_pnl + put_pnl
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

    # Commission tracking (display only - does not affect strategy logic)
    total_commission: float = 0.0  # Running total of all commissions paid today

    # Stop tracking
    call_stops_triggered: int = 0
    put_stops_triggered: int = 0
    double_stops: int = 0  # Both sides stopped on same IC

    # Circuit breaker
    circuit_breaker_opens: int = 0

    # MEIC-TF specific counters (used by trend-following variant)
    # These remain 0 for base MEIC, but are tracked in MEIC-TF
    one_sided_entries: int = 0  # Fix #55: Count of one-sided (put-only or call-only) entries
    trend_overrides: int = 0    # Fix #56: Times trend filter caused one-sided entry
    credit_gate_skips: int = 0  # Fix #57: Times credit gate skipped/modified entry

    @property
    def total_stops(self) -> int:
        """Total stop losses triggered."""
        return self.call_stops_triggered + self.put_stops_triggered

    @property
    def active_entries(self) -> List[IronCondorEntry]:
        """Get entries that have open positions.

        An entry is active if:
        - It's complete (all 4 legs) and not fully stopped, OR
        - It's partial (only call or put side remains) after the other side was stopped

        CRITICAL FIX (2026-02-03): Partial entries should still be monitored until expiry.
        """
        active = []
        for e in self.entries:
            # FIX #43: For one-sided entries (MEIC-TF), check only the placed side
            call_only = getattr(e, 'call_only', False)
            put_only = getattr(e, 'put_only', False)

            if call_only:
                # Call-only entry - stopped if call side is stopped
                if e.call_side_stopped:
                    continue
            elif put_only:
                # Put-only entry - stopped if put side is stopped
                if e.put_side_stopped:
                    continue
            elif e.call_side_stopped and e.put_side_stopped:
                # Full IC - stopped if both sides are stopped
                continue

            if e.is_complete:
                # Full IC with at least one side still open
                active.append(e)
            else:
                # Partial entry - check if ANY position ID exists
                has_any_position = any([
                    e.short_call_position_id,
                    e.long_call_position_id,
                    e.short_put_position_id,
                    e.long_put_position_id
                ])
                if has_any_position:
                    active.append(e)
        return active


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
        for ts, price in self.price_history:
            if ts >= window_start:
                if oldest_price is None:
                    oldest_price = price
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
    - 6 scheduled iron condor entries per day (10:00-12:30 AM ET)
    - VIX-adjusted strike selection for consistent ~8 delta targeting
    - Per-side stop losses equal to total credit (breakeven design)
    - MEIC+ modification for better breakeven days (configurable threshold)
    - Credit validation against configured min/max bounds
    - Position Registry integration for multi-bot support

    Key Features:
    - Safe partial fill handling (never leave naked shorts)
    - Automatic orphan cleanup on restart
    - Independent stop monitoring per IC
    - VIX filtering (skip entries if VIX > max_vix_entry)
    - FOMC blackout (skip all entries on Fed announcement days)
    - Comprehensive edge case handling (79 cases analyzed)

    Version: 1.2.3 (2026-02-08)
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

        # CONFIG-001: Validate configuration on startup
        # Note: _validate_config is called later after self is fully initialized
        # We store config first, validate after alert_service is ready

        # Alert service
        if alert_service:
            self.alert_service = alert_service
        else:
            self.alert_service = AlertService(config, "MEIC")

        # Position Registry for multi-bot isolation
        self.registry = PositionRegistry(REGISTRY_FILE)

        # Strategy configuration
        self.strategy_config = config.get("strategy", {})

        # CONFIG-001: Validate configuration early
        config_valid, config_errors = self._validate_config()
        if not config_valid:
            error_msg = f"Configuration validation failed: {'; '.join(config_errors)}"
            logger.critical(error_msg)
            raise ValueError(error_msg)

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
        # Config stores credit thresholds in per-contract dollars ($1.00 = $1.00 per contract)
        # But entry credits are stored in total dollars (fill_price × 100 multiplier)
        # So we multiply config values by 100 for consistent comparison
        self.min_credit_per_side = self.strategy_config.get("min_credit_per_side", 1.00) * 100
        self.max_credit_per_side = self.strategy_config.get("max_credit_per_side", 1.75) * 100
        # MKT-011: Minimum viable credit gate - entries with credit below this are SKIPPED (not just warned)
        # Default $0.50 per side = $50 total, matching MIN_STOP_LEVEL safety floor
        self.min_viable_credit_per_side = self.strategy_config.get("min_viable_credit_per_side", 0.50) * 100
        self.target_delta = self.strategy_config.get("target_delta", 8)
        self.min_delta = self.strategy_config.get("min_delta", 5)
        self.max_delta = self.strategy_config.get("max_delta", 15)

        # MEIC+ modification
        self.meic_plus_enabled = self.strategy_config.get("meic_plus_enabled", True)
        # Config stores reduction in per-contract dollars ($0.10), multiply by 100 for total dollars
        self.meic_plus_reduction = self.strategy_config.get("meic_plus_reduction", 0.10) * 100

        # Risk parameters
        self.max_daily_loss_percent = self.strategy_config.get("max_daily_loss_percent", 2.0)
        self.max_vix_entry = self.strategy_config.get("max_vix_entry", DEFAULT_MAX_VIX_ENTRY)
        self.contracts_per_entry = self.strategy_config.get("contracts_per_entry", 1)

        # Commission tracking (display only - does not affect strategy logic)
        # Saxo Bank charges $2.50 per leg per contract, round-trip = $5.00 per leg
        self.commission_per_leg = self.strategy_config.get("commission_per_leg", 2.50)

        # State file path - can be overridden by subclasses BEFORE calling super().__init__()
        # Check if subclass already set it to avoid overwriting (e.g., MEIC-TF uses different file)
        if not hasattr(self, 'state_file') or self.state_file is None:
            self.state_file = STATE_FILE

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

        # POS-004: After-hours settlement reconciliation tracking
        # Once all positions are confirmed settled, stop checking until next trading day
        self._settlement_reconciliation_complete: bool = False

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

        # Check for FOMC day (both days of meeting, not just announcement day)
        if is_fomc_meeting_day():
            logger.warning("TODAY IS FOMC MEETING DAY - No entries will be placed")

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

        # MKT-008: Skip all trading on FOMC days (both days of meeting)
        if is_fomc_meeting_day():
            if self.state != MEICState.DAILY_COMPLETE:
                self.state = MEICState.DAILY_COMPLETE
                logger.info("FOMC meeting day - skipping all entries")
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
        """
        Handle positions that are missing from Saxo (manually closed, assigned, or merged).

        Fix #61: Detect when positions were merged (same strikes as another entry)
        instead of incorrectly marking them as stopped. Merged positions preserve
        their credit and should count as wins in the win rate calculation.
        """
        for entry in self.daily_state.entries:
            for position_id in list(missing_ids):
                # Check if this entry had the missing position
                if position_id in entry.all_position_ids:
                    # Fix #61: Check if this might be a merge (another entry has same strikes)
                    is_merge = self._check_if_position_merged(entry, position_id)

                    # Determine which leg
                    if position_id == entry.short_call_position_id:
                        if is_merge:
                            logger.warning(f"  Entry #{entry.entry_number}: Short Call merged with another entry (same strikes)")
                            entry.call_side_merged = True
                        else:
                            logger.warning(f"  Entry #{entry.entry_number}: Short Call missing - marking call side stopped")
                            entry.call_side_stopped = True
                        entry.short_call_position_id = None
                    elif position_id == entry.long_call_position_id:
                        if is_merge:
                            logger.warning(f"  Entry #{entry.entry_number}: Long Call merged with another entry (same strikes)")
                            # Long call merged is tracked with short call merge flag
                        else:
                            logger.warning(f"  Entry #{entry.entry_number}: Long Call missing")
                        entry.long_call_position_id = None
                    elif position_id == entry.short_put_position_id:
                        if is_merge:
                            logger.warning(f"  Entry #{entry.entry_number}: Short Put merged with another entry (same strikes)")
                            entry.put_side_merged = True
                        else:
                            logger.warning(f"  Entry #{entry.entry_number}: Short Put missing - marking put side stopped")
                            entry.put_side_stopped = True
                        entry.short_put_position_id = None
                    elif position_id == entry.long_put_position_id:
                        if is_merge:
                            logger.warning(f"  Entry #{entry.entry_number}: Long Put merged with another entry (same strikes)")
                            # Long put merged is tracked with short put merge flag
                        else:
                            logger.warning(f"  Entry #{entry.entry_number}: Long Put missing")
                        entry.long_put_position_id = None

                    # Unregister from registry
                    try:
                        self.registry.unregister(position_id)
                    except Exception as e:
                        logger.error(f"Registry error unregistering position {position_id}: {e}")

    def _check_if_position_merged(self, entry: IronCondorEntry, position_id: str) -> bool:
        """
        Fix #61: Check if a missing position was merged with another entry.

        When two entries have the same strikes, Saxo merges them into one position.
        The older entry's position IDs become invalid, but the positions still exist
        under the newer entry.

        Args:
            entry: The entry whose position is missing
            position_id: The missing position ID

        Returns:
            True if another entry has the same strikes and valid position IDs
        """
        # Determine which side/strike to check based on position_id
        if position_id == entry.short_call_position_id or position_id == entry.long_call_position_id:
            # Call side - check if another entry has same call strikes
            target_short = entry.short_call_strike
            target_long = entry.long_call_strike
            is_call = True
        elif position_id == entry.short_put_position_id or position_id == entry.long_put_position_id:
            # Put side - check if another entry has same put strikes
            target_short = entry.short_put_strike
            target_long = entry.long_put_strike
            is_call = False
        else:
            return False

        # Check other entries for same strikes with valid position IDs
        for other_entry in self.daily_state.entries:
            if other_entry.entry_number == entry.entry_number:
                continue  # Skip self

            if is_call:
                if (other_entry.short_call_strike == target_short and
                    other_entry.long_call_strike == target_long and
                    other_entry.short_call_position_id is not None):
                    logger.info(f"  Fix #61: Entry #{entry.entry_number} call strikes ({target_short}/{target_long}) "
                               f"match Entry #{other_entry.entry_number} - likely merged")
                    return True
            else:
                if (other_entry.short_put_strike == target_short and
                    other_entry.long_put_strike == target_long and
                    other_entry.short_put_position_id is not None):
                    logger.info(f"  Fix #61: Entry #{entry.entry_number} put strikes ({target_short}/{target_long}) "
                               f"match Entry #{other_entry.entry_number} - likely merged")
                    return True

        return False

    # =========================================================================
    # STATE HANDLERS
    # =========================================================================

    def _handle_idle_state(self) -> str:
        """
        Handle IDLE state - waiting for market to open.

        Transitions to WAITING_FIRST_ENTRY when market opens.

        CRITICAL FIX (2026-02-03): Only call _reset_for_new_day() if there's an
        actual previous day's date. If daily_state.date is empty (e.g., recovery
        failed), just set today's date instead of treating it as "overnight".
        """
        if is_market_open():
            # Reconcile positions on startup
            self._reconcile_positions()

            # Reset daily state if new day
            today = get_us_market_time().strftime("%Y-%m-%d")
            if self.daily_state.date != today:
                # CRITICAL FIX: Only reset if there's an actual previous date
                # Empty string means recovery never set the date - don't treat as "overnight"
                if self.daily_state.date and self.daily_state.date != "":
                    self._reset_for_new_day()
                else:
                    # No previous date - just set today's date
                    logger.info(f"Setting initial date to {today} (no previous date to compare)")
                    self.daily_state.date = today

            self.state = MEICState.WAITING_FIRST_ENTRY
            logger.info("Market open - transitioning to WAITING_FIRST_ENTRY")
            return "Market open, waiting for first entry time"

        return "Waiting for market open"

    def _handle_waiting_first_entry(self) -> str:
        """
        Handle WAITING_FIRST_ENTRY state - waiting for 10:00 AM.

        Transitions to ENTRY_IN_PROGRESS when first entry time arrives.

        TIME-002: On startup/restart, skips past any entry times whose windows
        have already passed (e.g., if bot restarts at 10:37, skip 10:00 and 10:30
        entries and wait for 11:00).
        """
        now = get_us_market_time()

        # TIME-002: Skip past any missed entry windows (e.g., after restart)
        self._skip_missed_entries(now)

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

        TIME-002: Skips past any missed entry windows on each tick.
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

        # TIME-002: Skip past any missed entry windows (e.g., after restart)
        self._skip_missed_entries(now)

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

    def _skip_missed_entries(self, now: datetime) -> None:
        """
        TIME-002: Skip past any entry times whose windows have already passed.

        This handles the case where the bot restarts mid-day (e.g., at 10:37 AM)
        and needs to skip the 10:00 and 10:30 entries to wait for 11:00.

        Without this fix, the bot would get stuck forever waiting for the
        10:00 entry window that already passed.

        Args:
            now: Current time (US Eastern)
        """
        skipped = 0
        while self._next_entry_index < len(self.entry_times):
            scheduled_time = self.entry_times[self._next_entry_index]
            scheduled_datetime = now.replace(
                hour=scheduled_time.hour,
                minute=scheduled_time.minute,
                second=0,
                microsecond=0
            )
            window_end = scheduled_datetime + timedelta(minutes=ENTRY_WINDOW_MINUTES)

            # If current time is past the entry window, skip this entry
            if now > window_end:
                skipped += 1
                logger.info(
                    f"TIME-002: Skipping missed Entry #{self._next_entry_index + 1} "
                    f"at {scheduled_time.strftime('%H:%M')} (window ended at {window_end.strftime('%H:%M:%S')})"
                )
                self.daily_state.entries_skipped += 1
                self._next_entry_index += 1
            else:
                # Entry is either in the future or currently in its window
                break

        if skipped > 0:
            logger.info(f"TIME-002: Skipped {skipped} missed entries, next is Entry #{self._next_entry_index + 1}")

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
                # Fix #52: Set contract count for multi-contract support
                entry.contracts = self.contracts_per_entry
                self._current_entry = entry

                # Calculate strikes (may change between retries due to price movement)
                if not self._calculate_strikes(entry):
                    last_error = "Failed to calculate strikes"
                    continue

                # MKT-011: Check minimum credit gate before placing orders (live mode only)
                if not self.dry_run:
                    gate_passed, gate_reason = self._check_minimum_credit_gate(entry)
                    if not gate_passed:
                        # Don't retry if gate fails - it's a deliberate skip
                        self._entry_in_progress = False
                        self._current_entry = None
                        self.state = MEICState.MONITORING
                        return gate_reason

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

                    # Track commission (display only - 4 legs × $2.50 open = $10 per IC)
                    entry.open_commission = 4 * self.commission_per_leg * self.contracts_per_entry
                    self.daily_state.total_commission += entry.open_commission

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

                    # FIX (2026-02-03): Clear entry flag on SUCCESS (was only cleared on failure)
                    self._entry_in_progress = False
                    self._current_entry = None

                    # Transition to appropriate state after entry
                    if self._next_entry_index < len(self.entry_times):
                        self.state = MEICState.MONITORING  # More entries to come
                    else:
                        self.state = MEICState.MONITORING  # All entries done, just monitor

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

    def _get_vix_adjusted_spread_width(self, vix: float) -> int:
        """
        MKT-009: Calculate spread width dynamically based on VIX.

        Higher VIX = more volatility = wings have more value/liquidity = wider spreads OK
        Lower VIX = less volatility = far OTM wings become worthless = need tighter spreads

        Scaling:
            VIX > 30:  80 pts (high vol, excellent liquidity)
            VIX 25-30: 70 pts (elevated vol, more liquidity)
            VIX 20-25: 60 pts (normal conditions)
            VIX 15-20: 50 pts (low-medium vol)
            VIX < 15:  40 pts (very low vol, wings illiquid)

        Args:
            vix: Current VIX level

        Returns:
            Spread width in points (rounded to 5)
        """
        min_spread = self.strategy_config.get("min_spread_width", 25)

        if vix > 30:
            spread_width = 80
        elif vix >= 25:
            spread_width = 70
        elif vix >= 20:
            spread_width = 60
        elif vix >= 15:
            spread_width = 50
        else:
            spread_width = 40

        # Ensure we don't go below min_spread_width
        spread_width = max(min_spread, spread_width)

        return spread_width

    def _calculate_strikes(self, entry: IronCondorEntry) -> bool:
        """
        Calculate iron condor strikes based on current SPX price and VIX.

        Uses VIX-adjusted distance to approximate target delta (from config).
        Higher VIX = wider strikes needed to maintain same delta.

        The relationship: at VIX 15, ~40 points OTM gives ~8 delta for 0DTE.
        Scale linearly with VIX to maintain consistent probability.

        Args:
            entry: IronCondorEntry to populate with strikes

        Returns:
            True if strikes calculated successfully
        """
        spx = self.current_price
        if spx <= 0:
            logger.error("Cannot calculate strikes - no SPX price")
            return False

        vix = self.current_vix
        if vix <= 0:
            logger.warning("No VIX available - using default VIX=15 for strike calculation")
            vix = 15.0

        # Round SPX to nearest 5 (SPX strikes are 5-point increments)
        rounded_spx = round(spx / 5) * 5

        # VIX-adjusted OTM distance for target delta
        # Base calibration: At VIX 15, ~40 points OTM gives ~8 delta for 0DTE SPX
        # The relationship is roughly linear: distance scales with VIX
        #
        # Delta adjustment: target_delta of 8 = base, adjust for other targets
        # Lower target delta (e.g., 5) = further OTM = multiply by (8/5) = 1.6
        # Higher target delta (e.g., 15) = closer to ATM = multiply by (8/15) = 0.53
        base_distance_at_vix15 = 40  # Points OTM for ~8 delta at VIX 15
        delta_adjustment = 8.0 / self.target_delta  # Scale for target delta

        # VIX scaling factor (clamped to reasonable range)
        # Min 0.7 (VIX ~10) to prevent strikes too close
        # Max 2.5 (VIX ~37) to prevent strikes too far
        vix_factor = max(0.7, min(2.5, vix / 15.0))

        # Calculate OTM distance
        otm_distance = base_distance_at_vix15 * vix_factor * delta_adjustment
        otm_distance = round(otm_distance / 5) * 5  # Round to 5-point strikes
        otm_distance = max(25, min(120, otm_distance))  # Clamp to 25-120 points

        # MKT-009: VIX-adjusted spread width
        # Higher VIX = more liquidity on wings = can use wider spreads
        # Lower VIX = wings become worthless/illiquid = need tighter spreads
        dynamic_spread_width = self._get_vix_adjusted_spread_width(vix)

        logger.info(
            f"Strike calculation: VIX={vix:.1f}, target_delta={self.target_delta}, "
            f"vix_factor={vix_factor:.2f}, delta_adj={delta_adjustment:.2f}, "
            f"otm_distance={otm_distance} pts, spread_width={dynamic_spread_width} pts"
        )

        # Call side (above current price)
        entry.short_call_strike = rounded_spx + otm_distance
        entry.long_call_strike = entry.short_call_strike + dynamic_spread_width

        # Put side (below current price)
        entry.short_put_strike = rounded_spx - otm_distance
        entry.long_put_strike = entry.short_put_strike - dynamic_spread_width

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
                entry.long_call_strike = adjusted_call + dynamic_spread_width
                logger.info(f"MKT-007: {call_msg}")

            # Check short put liquidity (move closer to ATM if illiquid)
            adjusted_put, put_msg = self._adjust_strike_for_liquidity(
                entry.short_put_strike, "Put", expiry,
                adjustment_direction=1  # Move closer to ATM (higher for puts)
            )
            if adjusted_put and adjusted_put != entry.short_put_strike:
                entry.short_put_strike = adjusted_put
                entry.long_put_strike = adjusted_put - dynamic_spread_width
                logger.info(f"MKT-007: {put_msg}")

            # MKT-008: Check long wing liquidity and reduce spread width if needed
            # Long wings (hedges) can become illiquid as they move far OTM
            adjusted_long_call, call_adjusted = self._adjust_long_wing_for_liquidity(
                entry.long_call_strike, entry.short_call_strike,
                "Call", expiry, is_call=True
            )
            if call_adjusted:
                entry.long_call_strike = adjusted_long_call
                # MKT-010: Mark call wing as illiquid (far OTM = safe side)
                entry.call_wing_illiquid = True

            adjusted_long_put, put_adjusted = self._adjust_long_wing_for_liquidity(
                entry.long_put_strike, entry.short_put_strike,
                "Put", expiry, is_call=False
            )
            if put_adjusted:
                entry.long_put_strike = adjusted_long_put
                # MKT-010: Mark put wing as illiquid (far OTM = safe side)
                entry.put_wing_illiquid = True

        # Fix #44: Check for strike conflicts with existing entries
        # Long strikes cannot equal short strikes from other entries
        self._adjust_for_strike_conflicts(entry)

        # Fix #50: Check for same-strike overlap with existing entries
        # If multiple entries land on same short strikes, Saxo merges positions
        # causing tracking issues. Offset overlapping strikes further OTM.
        self._adjust_for_same_strike_overlap(entry)

        # Fix #66: Re-run Fix #44 after MKT-013, because MKT-013 shifts BOTH
        # short AND long strikes further OTM. The new long strike may now
        # conflict with an existing short strike that wasn't a problem before.
        # Example: Long call 6980 (OK) → MKT-013 shifts to 6985 → conflicts
        # with Entry #2's short call at 6985.
        self._adjust_for_strike_conflicts(entry)

        # MKT-015: Check for long-long strike overlap with existing entries.
        # Saxo merges positions at the same strike+direction into one position
        # (Amount=N). This applies to LONG positions too, not just shorts.
        # If two entries share the same long call strike, Saxo deletes the
        # older position ID. On recovery, the entry that lost its ID can't
        # track its long leg → DATA-004 errors, stop check disabled.
        self._adjust_for_long_strike_overlap(entry)

        logger.info(
            f"Strikes calculated for SPX {spx:.2f}: "
            f"Call {entry.short_call_strike}/{entry.long_call_strike}, "
            f"Put {entry.short_put_strike}/{entry.long_put_strike}"
        )

        return True

    def _get_occupied_short_strikes(self) -> set:
        """
        Get all short strikes currently in use by active entries.

        Fix #44: Used to prevent new entries from placing long positions
        at strikes where we already have short positions (Saxo doesn't allow
        long and short at same strike).

        Returns:
            Set of strikes currently occupied by short positions
        """
        occupied = set()
        for e in self.daily_state.entries:
            # Only consider entries that are active (not fully stopped)
            if e.call_side_stopped and e.put_side_stopped:
                continue
            # Add short strikes that are still active
            if not e.call_side_stopped and e.short_call_strike:
                occupied.add(e.short_call_strike)
            if not e.put_side_stopped and e.short_put_strike:
                occupied.add(e.short_put_strike)
        return occupied

    def _adjust_for_strike_conflicts(self, entry: IronCondorEntry):
        """
        Adjust long strikes if they conflict with existing short strikes.

        Fix #44: If a new entry's long strike equals an existing entry's short
        strike, Saxo will reject with "cannot open positions in opposite directions".

        Solution: Move the conflicting long strike further OTM by 5 points.
        - Long put conflicts: move DOWN (further OTM for puts)
        - Long call conflicts: move UP (further OTM for calls)

        This slightly increases spread width but maintains protection.

        Args:
            entry: IronCondorEntry to check and adjust
        """
        occupied = self._get_occupied_short_strikes()
        if not occupied:
            return  # No existing entries, no conflicts possible

        # Check long call strike
        original_long_call = entry.long_call_strike
        while entry.long_call_strike in occupied:
            entry.long_call_strike += 5  # Move further OTM (up for calls)
        if entry.long_call_strike != original_long_call:
            logger.warning(
                f"MKT-012: Long call {original_long_call} conflicts with existing short strike, "
                f"adjusted to {entry.long_call_strike}"
            )

        # Check long put strike
        original_long_put = entry.long_put_strike
        while entry.long_put_strike in occupied:
            entry.long_put_strike -= 5  # Move further OTM (down for puts)
        if entry.long_put_strike != original_long_put:
            logger.warning(
                f"MKT-012: Long put {original_long_put} conflicts with existing short strike, "
                f"adjusted to {entry.long_put_strike}"
            )

    def _adjust_for_same_strike_overlap(self, entry: IronCondorEntry):
        """
        Adjust short strikes if they overlap with existing entries' short strikes.

        Fix #50: When multiple entries land on the same short strikes (due to
        minimal SPX movement), Saxo merges them into a single position with
        increased Amount. This causes tracking issues because:
        1. The earlier entry's position_id becomes stale (Saxo uses the newer ID)
        2. P&L calculation for earlier entries returns 0 (position not found)
        3. Stop loss on one entry would partially close the shared position

        Solution: Offset overlapping short strikes by 5 points further OTM.
        - Short call overlaps: move UP by 5 (further OTM for calls)
        - Short put overlaps: move DOWN by 5 (further OTM for puts)
        - Corresponding long strikes also adjusted to maintain spread width

        Args:
            entry: IronCondorEntry to check and adjust
        """
        # Get existing short strikes from active entries
        existing_short_calls = set()
        existing_short_puts = set()

        for e in self.daily_state.entries:
            # Only consider entries that are still active
            call_active = not e.call_side_stopped and not getattr(e, 'call_side_skipped', False)
            put_active = not e.put_side_stopped and not getattr(e, 'put_side_skipped', False)

            if call_active and e.short_call_strike:
                existing_short_calls.add(e.short_call_strike)
            if put_active and e.short_put_strike:
                existing_short_puts.add(e.short_put_strike)

        if not existing_short_calls and not existing_short_puts:
            return  # No existing entries, no overlaps possible

        # Check and adjust short call strike
        original_short_call = entry.short_call_strike
        original_long_call = entry.long_call_strike
        while entry.short_call_strike and entry.short_call_strike in existing_short_calls:
            # Move both short and long call UP by 5 (further OTM)
            entry.short_call_strike += 5
            entry.long_call_strike += 5
        if entry.short_call_strike != original_short_call:
            logger.warning(
                f"MKT-013: Short call {original_short_call} overlaps existing entry, "
                f"adjusted to {entry.short_call_strike}/{entry.long_call_strike} "
                f"(was {original_short_call}/{original_long_call})"
            )
            self._log_safety_event(
                "MKT-013_STRIKE_OVERLAP",
                f"Entry #{entry.entry_number} call spread shifted: "
                f"{original_short_call}/{original_long_call} → "
                f"{entry.short_call_strike}/{entry.long_call_strike}"
            )
            # MKT-014: Re-check liquidity after MKT-013 adjustment
            # MKT-007 may have optimized away from an illiquid strike, but MKT-013
            # moved us back further OTM. Warn if we landed on an illiquid strike.
            self._warn_if_strike_illiquid(
                entry.short_call_strike, "Call", entry.entry_number,
                reason="MKT-013 overlap adjustment"
            )

        # Check and adjust short put strike
        original_short_put = entry.short_put_strike
        original_long_put = entry.long_put_strike
        while entry.short_put_strike and entry.short_put_strike in existing_short_puts:
            # Move both short and long put DOWN by 5 (further OTM)
            entry.short_put_strike -= 5
            entry.long_put_strike -= 5
        if entry.short_put_strike != original_short_put:
            logger.warning(
                f"MKT-013: Short put {original_short_put} overlaps existing entry, "
                f"adjusted to {entry.short_put_strike}/{entry.long_put_strike} "
                f"(was {original_short_put}/{original_long_put})"
            )
            self._log_safety_event(
                "MKT-013_STRIKE_OVERLAP",
                f"Entry #{entry.entry_number} put spread shifted: "
                f"{original_short_put}/{original_long_put} → "
                f"{entry.short_put_strike}/{entry.long_put_strike}"
            )
            # MKT-014: Re-check liquidity after MKT-013 adjustment
            self._warn_if_strike_illiquid(
                entry.short_put_strike, "Put", entry.entry_number,
                reason="MKT-013 overlap adjustment"
            )

    def _adjust_for_long_strike_overlap(self, entry: IronCondorEntry):
        """
        MKT-015: Adjust long strikes if they overlap with existing entries' long strikes.

        Fix #67: Saxo merges ALL positions at the same strike+direction, including
        longs. When two entries share the same long call strike (e.g., both at 6975),
        Saxo merges them into Amount=2 and deletes the older position ID.

        On recovery, the entry that lost its position ID has long_call_position_id=null,
        long_call_uic=null, long_call_strike=0. This causes:
        1. DATA-004 errors every monitoring cycle (can't fetch price for missing leg)
        2. Call side stop check DISABLED (safety risk)
        3. If stop triggered for other entry, it closes Amount=2, breaking both entries

        Solution: Move overlapping long strikes further OTM by 5 points.
        - Long call overlaps: move UP (further OTM for calls) → wider spread
        - Long put overlaps: move DOWN (further OTM for puts) → wider spread
        Wider spread is safe (more protection, slightly better net credit).

        Args:
            entry: IronCondorEntry to check and adjust
        """
        existing_long_calls = set()
        existing_long_puts = set()

        for e in self.daily_state.entries:
            call_active = not e.call_side_stopped and not getattr(e, 'call_side_skipped', False)
            put_active = not e.put_side_stopped and not getattr(e, 'put_side_skipped', False)

            if call_active and e.long_call_strike:
                existing_long_calls.add(e.long_call_strike)
            if put_active and e.long_put_strike:
                existing_long_puts.add(e.long_put_strike)

        if not existing_long_calls and not existing_long_puts:
            return

        # Check and adjust long call strike
        original_long_call = entry.long_call_strike
        while entry.long_call_strike and entry.long_call_strike in existing_long_calls:
            entry.long_call_strike += 5  # Move further OTM (up for calls)
        if entry.long_call_strike != original_long_call:
            logger.warning(
                f"MKT-015: Long call {original_long_call} overlaps existing entry's long call, "
                f"adjusted to {entry.long_call_strike} "
                f"(spread width {entry.long_call_strike - entry.short_call_strike} pts)"
            )
            self._log_safety_event(
                "MKT-015_LONG_OVERLAP",
                f"Entry #{entry.entry_number} long call shifted: "
                f"{original_long_call} → {entry.long_call_strike}"
            )

        # Check and adjust long put strike
        original_long_put = entry.long_put_strike
        while entry.long_put_strike and entry.long_put_strike in existing_long_puts:
            entry.long_put_strike -= 5  # Move further OTM (down for puts)
        if entry.long_put_strike != original_long_put:
            logger.warning(
                f"MKT-015: Long put {original_long_put} overlaps existing entry's long put, "
                f"adjusted to {entry.long_put_strike} "
                f"(spread width {entry.short_put_strike - entry.long_put_strike} pts)"
            )
            self._log_safety_event(
                "MKT-015_LONG_OVERLAP",
                f"Entry #{entry.entry_number} long put shifted: "
                f"{original_long_put} → {entry.long_put_strike}"
            )

    def _warn_if_strike_illiquid(
        self,
        strike: float,
        put_call: str,
        entry_number: int,
        reason: str = ""
    ):
        """
        MKT-014: Check and warn if a strike is illiquid after adjustment.

        This is called after MKT-013 moves a strike further OTM to avoid overlap.
        If MKT-007 previously moved away from an illiquid strike, MKT-013's
        adjustment might land us back on an illiquid strike.

        We warn but don't prevent the entry - MKT-011 credit gate will catch
        this if the credit is too low.

        Args:
            strike: Strike price to check
            put_call: "Call" or "Put"
            entry_number: Entry number for logging
            reason: Why we're checking (for log message)
        """
        expiry = self._get_todays_expiry()
        if not expiry:
            return

        uic = self._get_option_uic(strike, put_call, expiry)
        if not uic:
            logger.warning(
                f"MKT-014: Entry #{entry_number} {put_call} {strike} - "
                f"strike not found in chain after {reason}"
            )
            return

        quote = self.client.get_quote(uic, asset_type="StockIndexOption")
        if not quote or "Quote" not in quote:
            logger.warning(
                f"MKT-014: Entry #{entry_number} {put_call} {strike} - "
                f"no quote available after {reason}"
            )
            return

        bid = quote["Quote"].get("Bid") or 0
        ask = quote["Quote"].get("Ask") or 0

        if bid <= 0 or ask <= 0:
            logger.warning(
                f"MKT-014: Entry #{entry_number} {put_call} {strike} is ILLIQUID "
                f"(no bid/ask) after {reason}. MKT-011 credit gate will validate."
            )
            self._log_safety_event(
                "MKT-014_ILLIQUID_AFTER_ADJUSTMENT",
                f"Entry #{entry_number} {put_call} {strike} illiquid after {reason}",
                "Warning"
            )
            return

        spread_percent = ((ask - bid) / bid) * 100 if bid > 0 else float('inf')
        if spread_percent >= MAX_BID_ASK_SPREAD_PERCENT_SKIP:
            logger.warning(
                f"MKT-014: Entry #{entry_number} {put_call} {strike} has wide spread "
                f"({spread_percent:.1f}%) after {reason}. "
                f"Bid=${bid:.2f}, Ask=${ask:.2f}. MKT-011 credit gate will validate."
            )
            self._log_safety_event(
                "MKT-014_WIDE_SPREAD_AFTER_ADJUSTMENT",
                f"Entry #{entry_number} {put_call} {strike} spread {spread_percent:.1f}% after {reason}",
                "Warning"
            )
        elif spread_percent >= MAX_BID_ASK_SPREAD_PERCENT_WARNING:
            logger.info(
                f"MKT-014: Entry #{entry_number} {put_call} {strike} has moderate spread "
                f"({spread_percent:.1f}%) after {reason}. Bid=${bid:.2f}, Ask=${ask:.2f}."
            )

    def _calculate_stop_levels(self, entry: IronCondorEntry):
        """
        Calculate stop loss levels for an entry.

        MEIC Rule: Stop on each side = Total credit received
        MEIC+ Modification: Stop = Total credit - $0.10

        Also validates that credit received is within configured bounds.

        Args:
            entry: IronCondorEntry to calculate stops for
        """
        total_credit = entry.total_credit

        # CRITICAL SAFETY CHECK (2026-02-04): Prevent zero stop levels
        # If total_credit is 0 or very small, stop would trigger immediately
        # This can happen if fill_price wasn't captured correctly
        MIN_STOP_LEVEL = 50.0  # $50 minimum stop level (safety floor)
        if total_credit < MIN_STOP_LEVEL:
            logger.critical(
                f"CRITICAL: Entry #{entry.entry_number} has dangerously low credit "
                f"(${total_credit:.2f} < ${MIN_STOP_LEVEL:.2f}). "
                f"Call: ${entry.call_spread_credit:.2f}, Put: ${entry.put_spread_credit:.2f}. "
                f"Using minimum stop level to prevent immediate false triggers."
            )
            self._log_safety_event(
                "CRITICAL_LOW_CREDIT",
                f"Entry #{entry.entry_number} credit ${total_credit:.2f} - using min stop ${MIN_STOP_LEVEL:.2f}"
            )
            # Use minimum stop level to prevent immediate false trigger
            total_credit = MIN_STOP_LEVEL

        # Validate credit per side against configured bounds
        # This ensures we're getting adequate premium for the risk taken
        self._validate_entry_credit(entry)

        if self.meic_plus_enabled:
            # MEIC+ modification - smaller stop for breakeven days -> small wins
            # STOP-002: Don't apply if stop would be too tight (credit < $1.50)
            # Note: $1.50 is a safety threshold - with thin credits, reducing
            # the stop by $0.10 makes it proportionally too tight
            # Config is in per-contract dollars, multiply by 100 for total dollars comparison
            min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
            if total_credit > min_credit_for_meic_plus:
                stop_level = total_credit - self.meic_plus_reduction
            else:
                stop_level = total_credit
                logger.info(f"MEIC+ not applied - credit ${total_credit:.2f} < ${min_credit_for_meic_plus:.2f}")
        else:
            stop_level = total_credit

        # Both sides get the same stop level
        entry.call_side_stop = stop_level
        entry.put_side_stop = stop_level

        logger.info(
            f"Stop levels set for Entry #{entry.entry_number}: "
            f"${stop_level:.2f} per side (credit: ${total_credit:.2f})"
        )

    def _validate_entry_credit(self, entry: IronCondorEntry):
        """
        Validate that entry credit is within configured bounds.

        Logs warnings for credits outside the target range. Credits below
        minimum indicate potential liquidity issues or strike selection
        problems. Credits above maximum are unusual but acceptable.

        Args:
            entry: IronCondorEntry to validate
        """
        for side, credit in [("Call", entry.call_spread_credit),
                             ("Put", entry.put_spread_credit)]:
            if credit < self.min_credit_per_side:
                logger.warning(
                    f"LOW CREDIT: {side} credit ${credit:.2f} < minimum ${self.min_credit_per_side:.2f} - "
                    f"Entry #{entry.entry_number} may have insufficient premium protection"
                )
                # Log to safety events for tracking
                self._log_safety_event(
                    "LOW_CREDIT_WARNING",
                    f"Entry #{entry.entry_number} {side} credit ${credit:.2f} < ${self.min_credit_per_side:.2f}"
                )
            elif credit > self.max_credit_per_side:
                # High credit is unusual but not dangerous - just log info
                logger.info(
                    f"HIGH CREDIT: {side} credit ${credit:.2f} > target ${self.max_credit_per_side:.2f} - "
                    f"Entry #{entry.entry_number} (higher IV environment)"
                )

    def _estimate_entry_credit(self, entry: IronCondorEntry) -> Tuple[float, float]:
        """
        Estimate expected credit per side by fetching quotes BEFORE entry.

        MKT-011: This allows us to check if credit is viable before placing orders.

        Uses mid prices to estimate:
        - Call spread credit = short_call_mid - long_call_mid
        - Put spread credit = short_put_mid - long_put_mid

        Args:
            entry: IronCondorEntry with strikes already calculated

        Returns:
            Tuple of (estimated_call_credit, estimated_put_credit) in total dollars
            Returns (0.0, 0.0) if quotes unavailable
        """
        expiry = self._get_todays_expiry()
        if not expiry:
            logger.warning("Could not get expiry for credit estimation")
            return (0.0, 0.0)

        try:
            # Get UICs for all options
            short_call_uic = self._get_option_uic(entry.short_call_strike, "Call", expiry)
            long_call_uic = self._get_option_uic(entry.long_call_strike, "Call", expiry)
            short_put_uic = self._get_option_uic(entry.short_put_strike, "Put", expiry)
            long_put_uic = self._get_option_uic(entry.long_put_strike, "Put", expiry)

            if not all([short_call_uic, long_call_uic, short_put_uic, long_put_uic]):
                logger.warning("Could not get UICs for all legs - skipping credit estimation")
                return (0.0, 0.0)

            # Get quotes for all options
            short_call_quote = self.client.get_quote(short_call_uic, asset_type="StockIndexOption")
            long_call_quote = self.client.get_quote(long_call_uic, asset_type="StockIndexOption")
            short_put_quote = self.client.get_quote(short_put_uic, asset_type="StockIndexOption")
            long_put_quote = self.client.get_quote(long_put_uic, asset_type="StockIndexOption")

            def get_mid(quote) -> float:
                """Extract mid price from quote."""
                if not quote or "Quote" not in quote:
                    return 0.0
                q = quote["Quote"]
                bid = q.get("Bid", 0) or 0
                ask = q.get("Ask", 0) or 0
                if bid > 0 and ask > 0:
                    return (bid + ask) / 2
                return 0.0

            short_call_mid = get_mid(short_call_quote)
            long_call_mid = get_mid(long_call_quote)
            short_put_mid = get_mid(short_put_quote)
            long_put_mid = get_mid(long_put_quote)

            # Estimated net credit per side (in total dollars, × 100 multiplier)
            # Credit = short - long (we collect on short, pay on long)
            estimated_call_credit = (short_call_mid - long_call_mid) * 100
            estimated_put_credit = (short_put_mid - long_put_mid) * 100

            logger.debug(
                f"Credit estimation for Entry #{entry.entry_number}: "
                f"Call spread ${estimated_call_credit:.2f} "
                f"(short ${short_call_mid:.2f} - long ${long_call_mid:.2f}), "
                f"Put spread ${estimated_put_credit:.2f} "
                f"(short ${short_put_mid:.2f} - long ${long_put_mid:.2f})"
            )

            return (estimated_call_credit, estimated_put_credit)

        except Exception as e:
            logger.warning(f"Credit estimation failed: {e}")
            return (0.0, 0.0)

    def _check_minimum_credit_gate(self, entry: IronCondorEntry) -> Tuple[bool, str]:
        """
        MKT-011: Check if estimated credit is above minimum viable threshold.

        This gate prevents placing entries that would have insufficient premium
        to make the trade worthwhile. Called BEFORE placing orders.

        Args:
            entry: IronCondorEntry with strikes calculated

        Returns:
            Tuple of (should_proceed, reason_if_skipping)
            - (True, "") if entry should proceed
            - (False, "reason") if entry should be skipped
        """
        estimated_call, estimated_put = self._estimate_entry_credit(entry)

        # If we couldn't estimate credit, proceed with caution (don't block)
        if estimated_call == 0.0 and estimated_put == 0.0:
            logger.warning(
                f"MKT-011: Could not estimate credit for Entry #{entry.entry_number} - "
                f"proceeding without gate check"
            )
            return (True, "")

        # Check each side against minimum viable threshold
        call_viable = estimated_call >= self.min_viable_credit_per_side
        put_viable = estimated_put >= self.min_viable_credit_per_side

        if call_viable and put_viable:
            # Both sides have viable credit - proceed with full IC
            logger.info(
                f"MKT-011: Credit gate PASSED for Entry #{entry.entry_number}: "
                f"Call ${estimated_call:.2f}, Put ${estimated_put:.2f} "
                f"(min: ${self.min_viable_credit_per_side:.2f})"
            )
            return (True, "")

        # At least one side is non-viable
        if not call_viable and not put_viable:
            # Both sides below minimum - skip entry entirely
            reason = (
                f"MKT-011: SKIPPING Entry #{entry.entry_number} - both sides below minimum viable credit. "
                f"Call ${estimated_call:.2f}, Put ${estimated_put:.2f} "
                f"(min: ${self.min_viable_credit_per_side:.2f})"
            )
            logger.warning(reason)
            self._log_safety_event(
                "MKT-011_ENTRY_SKIPPED",
                f"Entry #{entry.entry_number} - call ${estimated_call:.2f}, put ${estimated_put:.2f}"
            )
            return (False, reason)

        # One side is viable, other is not
        # In base MEIC, we skip the entry entirely (one-sided entries are MEIC-TF only)
        if not call_viable:
            reason = (
                f"MKT-011: SKIPPING Entry #{entry.entry_number} - call side below minimum viable credit. "
                f"Call ${estimated_call:.2f} < ${self.min_viable_credit_per_side:.2f} "
                f"(Put ${estimated_put:.2f} is viable)"
            )
        else:
            reason = (
                f"MKT-011: SKIPPING Entry #{entry.entry_number} - put side below minimum viable credit. "
                f"Put ${estimated_put:.2f} < ${self.min_viable_credit_per_side:.2f} "
                f"(Call ${estimated_call:.2f} is viable)"
            )

        logger.warning(reason)
        self._log_safety_event(
            "MKT-011_ENTRY_SKIPPED",
            f"Entry #{entry.entry_number} - call ${estimated_call:.2f}, put ${estimated_put:.2f}"
        )
        return (False, reason)

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
        # Fix #52: Multiply by contracts_per_entry for multi-contract support
        credit_ratio = 0.025  # 2.5% of spread width per side
        entry.call_spread_credit = self.spread_width * credit_ratio * 100 * self.contracts_per_entry
        entry.put_spread_credit = self.spread_width * credit_ratio * 100 * self.contracts_per_entry

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
            long_call_debit = long_call_result.get("debit", 0)  # Track debit for net credit calc
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
            long_put_debit = long_put_result.get("debit", 0)  # Track debit for net credit calc
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
            # FIX (2026-02-04): Net credit = short credit - long debit (was only tracking short credit!)
            short_call_credit = short_call_result.get("credit", 0)
            entry.call_spread_credit = short_call_credit - long_call_debit
            logger.debug(f"Call spread: short ${short_call_credit:.2f} - long ${long_call_debit:.2f} = net ${entry.call_spread_credit:.2f}")
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
            # FIX (2026-02-04): Net credit = short credit - long debit (was only tracking short credit!)
            short_put_credit = short_put_result.get("credit", 0)
            entry.put_spread_credit = short_put_credit - long_put_debit
            logger.debug(f"Put spread: short ${short_put_credit:.2f} - long ${long_put_debit:.2f} = net ${entry.put_spread_credit:.2f}")
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

        leg_description = f"{put_call} {strike}"

        # ORDER-006: Validate order size before placing
        is_valid, error = self._validate_order_size(self.contracts_per_entry, leg_description)
        if not is_valid:
            logger.error(f"ORDER-006: Order size validation failed for {leg_description}")
            return None

        timeout = ORDER_TIMEOUT_EMERGENCY_SECONDS if emergency_mode else self._order_timeout

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
                    logger.warning(f"  ORDER-006: Spread {spread_percent:.1f}% too wide (bid=${bid:.2f}, ask=${ask:.2f}), skipping attempt")
                    continue
                elif spread_percent >= MAX_BID_ASK_SPREAD_PERCENT_WARNING:
                    logger.warning(f"  ORDER-006: Wide spread warning: {spread_percent:.1f}% (bid=${bid:.2f}, ask=${ask:.2f})")

            # ORDER-007: Track expected price for slippage monitoring
            expected_price = 0.0

            if is_market:
                # ORDER-005: Check absolute spread before MARKET order
                if spread > self._max_absolute_slippage:
                    logger.critical(f"  ORDER-005: Spread ${spread:.2f} > max ${self._max_absolute_slippage:.2f}")
                    logger.critical(f"  ABORTING MARKET order for {leg_description}")
                    continue

                # For MARKET orders, expected price is mid
                mid_price = (bid + ask) / 2 if bid and ask else ask or bid
                expected_price = mid_price

                # Place MARKET order
                logger.info(f"  Attempt {attempt + 1}: MARKET order for {leg_description}")
                market_result = self.client.place_order(
                    uic=uic,
                    asset_type="StockIndexOption",
                    buy_sell=buy_sell,
                    amount=self.contracts_per_entry,
                    order_type=OrderType.MARKET,
                    to_open_close="ToOpen",
                    external_reference=external_ref
                )

                # MARKET orders return {"OrderId": "..."} not {"filled": True}
                # Convert to same format as place_limit_order_with_timeout for uniform handling
                if market_result and market_result.get("OrderId"):
                    order_id = market_result.get("OrderId")
                    # Market orders fill immediately - verify via activities
                    time.sleep(1)  # Brief wait for activity sync (uses module-level import)
                    filled, fill_details = self.client.check_order_filled_by_activity(order_id, uic)
                    if filled:
                        fill_price = fill_details.get("fill_price") if fill_details else expected_price
                        result = {
                            "success": True,
                            "filled": True,
                            "order_id": order_id,
                            "fill_price": fill_price or expected_price,
                            "position_id": fill_details.get("position_id") if fill_details else None
                        }
                    else:
                        # Market order should always fill - treat as success with expected price
                        logger.warning(f"  MARKET order {order_id} - no fill activity found, assuming filled")
                        result = {
                            "success": True,
                            "filled": True,
                            "order_id": order_id,
                            "fill_price": expected_price
                        }
                else:
                    result = {"success": False, "filled": False, "message": "MARKET order failed"}
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
                else:
                    limit_price = mid_price

                # ORDER-008: Round to valid SPX tick size (CBOE rules)
                # Buy orders round UP (pay more to get filled)
                # Sell orders round DOWN (accept less to get filled)
                limit_price = round_to_spx_tick(limit_price, round_up=(buy_sell == BuySell.BUY))

                # For LIMIT orders, expected price is the limit price
                expected_price = limit_price

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

            if result and result.get("filled"):
                # Order filled - verify position exists
                position_id = self._get_position_id_from_order(result)
                fill_price = self._get_fill_price(result)

                # ORDER-007: Monitor fill price slippage
                self._monitor_fill_slippage(
                    expected_price=expected_price,
                    actual_fill_price=fill_price,
                    buy_sell=buy_sell,
                    leg_description=leg_description
                )

                # CONN-005: Verify position exists
                if position_id and not self._verify_position_exists(uic, position_id, buy_sell):
                    logger.warning(f"  CONN-005: Position verification failed for {leg_description}")
                    # Position may still exist, continue with warning

                logger.info(f"  ✓ Filled {leg_description} @ ${fill_price:.2f}")

                # Fix #52: Multiply by contracts_per_entry for multi-contract support
                return {
                    "position_id": position_id,
                    "uic": uic,
                    "credit": fill_price * 100 * self.contracts_per_entry if buy_sell == BuySell.SELL else 0,
                    "debit": fill_price * 100 * self.contracts_per_entry if buy_sell == BuySell.BUY else 0,
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

            # Log retry info and add delay before next attempt
            if attempt < len(PROGRESSIVE_RETRY_SEQUENCE) - 1:
                next_slippage, next_is_market = PROGRESSIVE_RETRY_SEQUENCE[attempt + 1]
                if next_is_market:
                    logger.warning(f"  ⚠ {leg_description} not filled - trying MARKET next...")
                else:
                    logger.warning(f"  ⚠ {leg_description} not filled - retrying at {next_slippage}% slippage...")

                # ORDER-009: Add delay between retries to let Saxo clear order state
                # Without this delay, rapid retries cause 409 Conflict and 429 Rate Limit errors
                logger.info(f"  Waiting {ORDER_RETRY_DELAY_SECONDS}s before retry (ORDER-009: prevent API conflicts)...")
                time.sleep(ORDER_RETRY_DELAY_SECONDS)

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
            # FIX (2026-02-03): PositionId is at TOP LEVEL, not in PositionBase
            pos_id = str(pos.get("PositionId", ""))
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
            open_orders = self.client.get_open_orders()
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

    # =========================================================================
    # ORDER SIZE VALIDATION (ORDER-006)
    # =========================================================================

    def _validate_order_size(self, amount: int, order_description: str) -> Tuple[bool, str]:
        """
        ORDER-006: Validate order size is within acceptable limits.

        Protections:
        1. Per-order maximum (catches single-order bugs)
        2. Total position limit (prevents over-concentration)

        This is a critical bug protection - a calculation error could cause
        the bot to order 100 or 1000 contracts instead of 1.

        Args:
            amount: Number of contracts to order
            order_description: Description for logging

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check 1: Per-order maximum
        if amount > MAX_CONTRACTS_PER_ORDER:
            error = (
                f"ORDER-006 REJECTED: Order size {amount} contracts exceeds "
                f"max {MAX_CONTRACTS_PER_ORDER}. Order: {order_description}"
            )
            logger.critical(error)
            self.alert_service.send_alert(
                alert_type=AlertType.CIRCUIT_BREAKER,
                title="ORDER SIZE LIMIT EXCEEDED",
                message=error,
                priority=AlertPriority.CRITICAL
            )
            self._log_safety_event("ORDER_SIZE_REJECTED", error)
            return (False, error)

        # Check 2: Would this exceed total position limit?
        current_position_size = self._get_current_position_size()
        projected_size = current_position_size + amount

        if projected_size > MAX_CONTRACTS_PER_UNDERLYING:
            error = (
                f"ORDER-006 REJECTED: Position limit exceeded. "
                f"Current={current_position_size}, Adding={amount}, "
                f"Projected={projected_size}, Max={MAX_CONTRACTS_PER_UNDERLYING}"
            )
            logger.critical(error)
            self.alert_service.send_alert(
                alert_type=AlertType.CIRCUIT_BREAKER,
                title="POSITION LIMIT EXCEEDED",
                message=error,
                priority=AlertPriority.CRITICAL
            )
            self._log_safety_event("POSITION_LIMIT_REJECTED", error)
            return (False, error)

        # All checks passed
        logger.debug(f"ORDER-006: Order size validated: {amount} contracts for {order_description}")
        return (True, "")

    def _get_current_position_size(self) -> int:
        """
        Calculate total contracts currently held across all entries.

        Returns:
            Total number of contracts (absolute sum across all legs)

        FIX (2026-02-03): Include partial entries (where one side was stopped).
        Previously skipped entries where is_complete=False, undercounting positions.
        """
        total = 0

        for entry in self.daily_state.entries:
            # Count each open leg that has a position ID
            # Works for both complete and partial entries
            if entry.short_call_position_id:
                total += self.contracts_per_entry
            if entry.long_call_position_id:
                total += self.contracts_per_entry
            if entry.short_put_position_id:
                total += self.contracts_per_entry
            if entry.long_put_position_id:
                total += self.contracts_per_entry

        return total

    # =========================================================================
    # FILL PRICE SLIPPAGE MONITORING (ORDER-007)
    # =========================================================================

    def _monitor_fill_slippage(
        self,
        expected_price: float,
        actual_fill_price: float,
        buy_sell: BuySell,
        leg_description: str
    ) -> None:
        """
        ORDER-007: Monitor fill price slippage and alert if excessive.

        Compares the expected fill price (limit or mid price) with the actual
        fill price to detect and log slippage events. Excessive slippage may
        indicate market conditions, liquidity issues, or execution problems.

        Slippage direction depends on buy/sell:
        - BUY: Slippage = (actual - expected) / expected (paying more = positive)
        - SELL: Slippage = (expected - actual) / expected (receiving less = positive)

        Args:
            expected_price: The price we expected to fill at (limit or mid)
            actual_fill_price: The actual fill price from the exchange
            buy_sell: BuySell direction
            leg_description: Description for logging (e.g., "Call 6050")
        """
        if expected_price <= 0 or actual_fill_price <= 0:
            logger.warning(
                f"ORDER-007: Cannot calculate slippage for {leg_description} - "
                f"expected=${expected_price:.2f}, actual=${actual_fill_price:.2f}"
            )
            return

        # Calculate slippage percentage (positive = worse for us)
        if buy_sell == BuySell.BUY:
            # Buying: paying more is bad
            slippage_pct = ((actual_fill_price - expected_price) / expected_price) * 100
        else:
            # Selling: receiving less is bad
            slippage_pct = ((expected_price - actual_fill_price) / expected_price) * 100

        slippage_dollar = abs(actual_fill_price - expected_price) * 100  # Per contract

        # Log and alert based on severity
        if slippage_pct >= SLIPPAGE_CRITICAL_THRESHOLD_PERCENT:
            # CRITICAL slippage
            logger.critical(
                f"ORDER-007 CRITICAL SLIPPAGE: {leg_description} - "
                f"Expected ${expected_price:.2f}, Got ${actual_fill_price:.2f} "
                f"({slippage_pct:+.1f}%, ${slippage_dollar:.2f}/contract)"
            )
            self.alert_service.send_alert(
                alert_type=AlertType.SLIPPAGE_ALERT,
                title="CRITICAL FILL SLIPPAGE",
                message=(
                    f"HIGH slippage on {leg_description}:\n"
                    f"Expected: ${expected_price:.2f}\n"
                    f"Actual: ${actual_fill_price:.2f}\n"
                    f"Slippage: {slippage_pct:+.1f}% (${slippage_dollar:.2f}/contract)"
                ),
                priority=AlertPriority.HIGH
            )
            self._log_safety_event(
                "CRITICAL_SLIPPAGE",
                f"{leg_description}: {slippage_pct:+.1f}% (${slippage_dollar:.2f})"
            )

        elif slippage_pct >= SLIPPAGE_WARNING_THRESHOLD_PERCENT:
            # WARNING level slippage
            logger.warning(
                f"ORDER-007 SLIPPAGE WARNING: {leg_description} - "
                f"Expected ${expected_price:.2f}, Got ${actual_fill_price:.2f} "
                f"({slippage_pct:+.1f}%, ${slippage_dollar:.2f}/contract)"
            )
            self._log_safety_event(
                "SLIPPAGE_WARNING",
                f"{leg_description}: {slippage_pct:+.1f}% (${slippage_dollar:.2f})"
            )

        elif slippage_pct > 0:
            # Minor slippage - just debug log
            logger.debug(
                f"ORDER-007: Minor slippage on {leg_description} - "
                f"{slippage_pct:+.1f}% (${slippage_dollar:.2f}/contract)"
            )

        elif slippage_pct < 0:
            # Negative slippage means we got a BETTER price than expected
            logger.info(
                f"ORDER-007: Favorable fill on {leg_description} - "
                f"Expected ${expected_price:.2f}, Got ${actual_fill_price:.2f} "
                f"({slippage_pct:+.1f}% BETTER)"
            )

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
        chain_response = self.client.get_option_chain(
            option_root_id=self.option_root_uic,
            expiry_dates=[expiry]
        )

        if not chain_response:
            logger.error(f"Could not fetch option chain for {expiry}")
            return None

        # Extract OptionSpace from response (get_option_chain returns a dict, not a list)
        # Structure: {"OptionSpace": [{"Expiry": "...", "SpecificOptions": [...]}]}
        option_space = chain_response.get("OptionSpace", [])
        if not option_space:
            logger.error(f"No OptionSpace in chain response for {expiry}")
            return None

        # Get the first (and typically only) expiration date's options
        expiry_data = option_space[0] if option_space else {}
        specific_options = expiry_data.get("SpecificOptions", [])

        if not specific_options:
            logger.error(f"No SpecificOptions in chain for {expiry}")
            return None

        # Search for matching strike and type
        # NOTE: Saxo API uses "StrikePrice" not "Strike" (verified in saxo_client.py)
        for option in specific_options:
            opt_strike = option.get("StrikePrice")
            opt_type = option.get("PutCall")

            if opt_strike == strike and opt_type == put_call:
                return option.get("Uic")

        logger.error(f"Strike {strike} {put_call} not found in chain for {expiry}")
        return None

    def _get_position_id_from_order(self, order_result: Dict) -> Optional[str]:
        """
        Get position ID from order result or activities.

        Args:
            order_result: Result from place_order() or place_limit_order_with_timeout()

        Returns:
            Position ID or None

        CRITICAL FIX (2026-02-03): Must check for None BEFORE converting to string,
        otherwise str(None) returns the string "None" which corrupts the registry
        and causes all positions to match on recovery.
        """
        # Check if directly in result (common path)
        # CRITICAL: Check is not None before str() to avoid "None" string bug
        if "PositionId" in order_result and order_result["PositionId"] is not None:
            return str(order_result["PositionId"])

        # Check position_id (lowercase, from place_limit_order_with_timeout)
        # CRITICAL: Check is not None before str() to avoid "None" string bug
        if "position_id" in order_result and order_result["position_id"] is not None:
            return str(order_result["position_id"])

        # Fallback: Try to get from activities via order_id and uic
        order_id = order_result.get("OrderId") or order_result.get("order_id")
        uic = order_result.get("Uic") or order_result.get("uic")

        if order_id and uic:
            # Use check_order_filled_by_activity which has retry logic
            filled, fill_details = self.client.check_order_filled_by_activity(str(order_id), uic)
            if filled and fill_details:
                pos_id = fill_details.get("position_id") or fill_details.get("PositionId")
                # CRITICAL: Check is not None before str() to avoid "None" string bug
                if pos_id is not None:
                    return str(pos_id)

        return None

    def _get_fill_price(self, order_result: Dict) -> float:
        """
        ACTIVITIES-001: Get fill price from order result with proper fallback chain.

        The shared saxo_client.place_limit_order_with_timeout() uses
        check_order_filled_by_activity() which has built-in retry logic
        (3 attempts with 1s delay) to handle activities endpoint sync delays.

        Fill price priority:
        1. fill_price - from place_limit_order_with_timeout() via activity check
        2. FilledPrice - direct from Saxo order response
        3. Price - legacy field name
        4. ExecutionPrice - alternative field name
        5. Fallback to order details API call

        Args:
            order_result: Order result dict from place_order or place_limit_order_with_timeout

        Returns:
            Fill price (float), or 0 if not found
        """
        # Priority 1: fill_price from place_limit_order_with_timeout (via activity check with retry)
        if "fill_price" in order_result:
            price = order_result["fill_price"]
            if price and price > 0:
                return float(price)

        # Priority 2-4: Try various direct field names
        for field in ["FilledPrice", "Price", "ExecutionPrice"]:
            if field in order_result:
                price = order_result[field]
                if price and price > 0:
                    return float(price)

        # Priority 5: Fallback to getting from order status
        order_id = order_result.get("OrderId") or order_result.get("order_id")
        if order_id:
            order_status = self.client.get_order_status(order_id)
            if order_status:
                fill_price = order_status.get("FilledPrice", 0)
                if fill_price and fill_price > 0:
                    return float(fill_price)

        logger.warning(
            f"ACTIVITIES-001: Could not extract fill price from order result. "
            f"Keys available: {list(order_result.keys())}"
        )
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

        CRITICAL FIX (2026-02-03): Explicitly reject "None" string to prevent
        registry corruption that causes all positions to match on recovery.

        Note: Registry errors are logged but don't crash - position monitoring
        continues even if registry fails.
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
                bot_name="MEIC",
                strategy_id=entry.strategy_id,
                metadata={
                    "entry_number": entry.entry_number,
                    "leg_type": leg_name,
                    "strike": strike
                }
            )
        except Exception as e:
            logger.error(f"Registry error registering {leg_name} position {position_id}: {e}")

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
            alert_type=AlertType.CIRCUIT_BREAKER,
            title="NAKED SHORT DETECTED",
            message=f"NAKED SHORT: {leg_name} (position {pos_id}) - closing immediately",
            priority=AlertPriority.CRITICAL
        )

        # Attempt to close the naked short using market order (not DELETE endpoint)
        # CRITICAL FIX (2026-02-03): Saxo DELETE endpoint returns 404 for SPX options
        try:
            # Naked shorts need BUY to close
            result = self.client.place_emergency_order(
                uic=uic,
                asset_type="StockIndexOption",
                buy_sell=BuySell.BUY,
                amount=self.contracts_per_entry,
                order_type=OrderType.MARKET,
                to_open_close="ToClose"
            )
            if result:
                logger.info(f"Closed naked short {pos_id} via order {result.get('OrderId')}")
                try:
                    self.registry.unregister(pos_id)
                except Exception as reg_e:
                    logger.error(f"Registry error unregistering {pos_id}: {reg_e}")
                self._log_safety_event("NAKED_SHORT_CLOSED", f"{leg_name} position {pos_id} closed successfully", "Closed")
            else:
                logger.critical(f"FAILED to close naked short {pos_id}!")
                self._log_safety_event("NAKED_SHORT_CLOSE_FAILED", f"{leg_name} position {pos_id} - close returned false", "Failed")
                self._trigger_critical_intervention(f"Cannot close naked short {pos_id}")
        except Exception as e:
            logger.critical(f"Exception closing naked short: {e}")
            self._log_safety_event("NAKED_SHORT_EXCEPTION", f"{leg_name} position {pos_id} - {str(e)}", "Exception")
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
            if pos_id and uic:
                try:
                    # CRITICAL FIX (2026-02-03): Use market order instead of DELETE endpoint
                    # Determine direction: short positions need BUY to close, long positions need SELL
                    buy_sell = BuySell.BUY if leg_name.startswith("short") else BuySell.SELL
                    result = self.client.place_emergency_order(
                        uic=uic,
                        asset_type="StockIndexOption",
                        buy_sell=buy_sell,
                        amount=self.contracts_per_entry,
                        order_type=OrderType.MARKET,
                        to_open_close="ToClose"
                    )
                    if result:
                        try:
                            self.registry.unregister(pos_id)
                        except Exception as reg_e:
                            logger.error(f"Registry error unregistering {pos_id}: {reg_e}")
                        logger.info(f"Unwound {leg_name}: {pos_id} via order {result.get('OrderId')}")
                    else:
                        logger.error(f"Failed to unwind {leg_name}: no result from close order")
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

            # SAFETY CHECK (2026-02-04): Skip stop check if stop levels are invalid
            # This prevents false triggers from zero/corrupted stop levels
            MIN_VALID_STOP = 50.0  # Must match MIN_STOP_LEVEL in _calculate_stop_levels
            if entry.call_side_stop < MIN_VALID_STOP or entry.put_side_stop < MIN_VALID_STOP:
                logger.error(
                    f"SAFETY: Entry #{entry.entry_number} has invalid stop levels "
                    f"(call: ${entry.call_side_stop:.2f}, put: ${entry.put_side_stop:.2f}) - "
                    f"skipping stop check to prevent false trigger"
                )
                continue

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

        REST-only mode: Always fetches fresh prices from REST API.
        This is more reliable than WebSocket caching which can have stale data.

        History: Switched from WebSocket cache to REST-only on 2026-02-01 after
        Delta Neutral experienced stale cache issues on 2026-01-27 that caused
        $0 price orders. REST-only is simpler and more reliable.

        Note: WebSocket cache methods are preserved in case rate limits become
        an issue with many bots in the future.
        """
        if self.dry_run:
            # Simulate price movement based on SPX movement
            self._simulate_entry_prices(entry)
            return

        # REST-only mode: Always fetch fresh prices from REST API
        # This avoids stale WebSocket cache issues

        # Short Call
        if entry.short_call_uic:
            quote = self.client.get_quote(entry.short_call_uic, asset_type="StockIndexOption")
            entry.short_call_price = self._extract_mid_price(quote) or 0

        # Long Call
        if entry.long_call_uic:
            quote = self.client.get_quote(entry.long_call_uic, asset_type="StockIndexOption")
            entry.long_call_price = self._extract_mid_price(quote) or 0

        # Short Put
        if entry.short_put_uic:
            quote = self.client.get_quote(entry.short_put_uic, asset_type="StockIndexOption")
            entry.short_put_price = self._extract_mid_price(quote) or 0

        # Long Put
        if entry.long_put_uic:
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
            # EMERGENCY-001: Include UICs for spread checking during close
            positions_to_close = [
                (entry.short_call_position_id, "short_call", entry.short_call_uic),
                (entry.long_call_position_id, "long_call", entry.long_call_uic)
            ]
            stop_level = entry.call_side_stop
        else:
            entry.put_side_stopped = True
            self.daily_state.put_stops_triggered += 1
            # EMERGENCY-001: Include UICs for spread checking during close
            positions_to_close = [
                (entry.short_put_position_id, "short_put", entry.short_put_uic),
                (entry.long_put_position_id, "long_put", entry.long_put_uic)
            ]
            stop_level = entry.put_side_stop

        # Check for double stop
        if entry.call_side_stopped and entry.put_side_stopped:
            self.daily_state.double_stops += 1
            logger.warning(f"DOUBLE STOP on Entry #{entry.entry_number}")

        # FIX #42 (2026-02-05): Track actual fill prices for accurate P&L calculation
        # Previous bug: Used theoretical stop_level instead of actual close cost
        # This caused P&L to show wrong values (even profits instead of losses!)
        actual_close_cost = 0.0  # Total cost to close the spread (in dollars)
        fill_prices_captured = True  # Track if we got actual prices

        if self.dry_run:
            logger.info(f"[DRY RUN] Would close {side} side of Entry #{entry.entry_number}")
            # In dry-run, use theoretical stop level for P&L
            actual_close_cost = stop_level
        else:
            # EMERGENCY-001: Close positions with enhanced retry logic and spread validation
            # FIX #42: Collect fill prices from each leg
            # Fix #45: Pass entry_number for merged position handling
            for pos_id, leg_name, uic in positions_to_close:
                if pos_id:
                    _, fill_price = self._close_position_with_retry(
                        pos_id, leg_name, uic=uic, entry_number=entry.entry_number
                    )
                    if fill_price is not None:
                        # Short leg: we BUY to close (cost us money)
                        # Long leg: we SELL to close (gives us money back)
                        # Fix #52: Multiply by entry.contracts for multi-contract support
                        if leg_name.startswith("short"):
                            # Buying back short at fill_price costs us money
                            actual_close_cost += fill_price * 100 * entry.contracts  # Convert to dollars
                            logger.info(f"FIX-42: {leg_name} close cost: +${fill_price * 100 * entry.contracts:.2f}")
                        else:
                            # Selling long at fill_price gives us money back
                            actual_close_cost -= fill_price * 100 * entry.contracts  # Subtract (reduces cost)
                            logger.info(f"FIX-42: {leg_name} close proceeds: -${fill_price * 100 * entry.contracts:.2f}")
                    else:
                        fill_prices_captured = False
                        logger.warning(f"FIX-42: No fill price for {leg_name}, will fall back to theoretical")

        # Calculate actual net loss
        # FIX #42 (2026-02-05): Use actual close cost when available
        # Net loss = (cost to buy back short - proceeds from selling long) - credit received
        # Example: Bought back short @ $8.90 ($890), sold long @ $1.55 ($155)
        #          actual_close_cost = $890 - $155 = $735
        #          credit_received = $560
        #          net_loss = $735 - $560 = $175 (loss)
        if side == "call":
            credit_received = entry.call_spread_credit
        else:
            credit_received = entry.put_spread_credit

        if fill_prices_captured and not self.dry_run:
            # Use actual close cost
            net_loss = actual_close_cost - credit_received
            logger.info(
                f"FIX-42: Actual P&L for Entry #{entry.entry_number} {side}: "
                f"close_cost=${actual_close_cost:.2f} - credit=${credit_received:.2f} = "
                f"net_loss=${net_loss:.2f}"
            )
        else:
            # Fallback to theoretical (stop_level = credit, so net_loss = 0 for MEIC)
            # This is a known limitation - log warning
            net_loss = stop_level - credit_received
            if not self.dry_run:
                logger.warning(
                    f"FIX-42: Using theoretical P&L (fill prices unavailable): "
                    f"stop_level=${stop_level:.2f} - credit=${credit_received:.2f} = "
                    f"net_loss=${net_loss:.2f} (may be inaccurate!)"
                )

        self.daily_state.total_realized_pnl -= net_loss

        # Track close commission (display only - 2 legs per side × $2.50 close = $5 per side)
        close_commission = 2 * self.commission_per_leg * self.contracts_per_entry
        entry.close_commission += close_commission
        self.daily_state.total_commission += close_commission

        # Log stop loss to Google Sheets (pass net loss, not gross)
        self._log_stop_loss(entry, side, stop_level, net_loss)

        # ALERT-002: Use batched alerting for rapid stops (pass net loss for display)
        self._queue_stop_alert(entry, side, stop_level, net_loss)

        # P1: Save state after stop loss
        self._save_state_to_disk()

        # ALERT-002: Flush any batched alerts after a short delay
        # This allows multiple rapid stops to be batched together
        time.sleep(0.1)  # Small delay to allow batching
        self._flush_batched_alerts()

        return f"Stop loss executed: Entry #{entry.entry_number} {side} side (${stop_level:.2f})"

    def _close_position_with_retry(
        self, position_id: str, leg_name: str, uic: int = None, entry_number: int = None
    ) -> Tuple[bool, Optional[float]]:
        """
        EMERGENCY-001: Close a position with enhanced retry logic and spread validation.

        Enhanced emergency close that:
        1. Checks bid-ask spread before closing (wide spreads = bad fills)
        2. Waits for spread normalization if too wide
        3. Uses progressive retry with escalating alerts
        4. Tracks slippage for monitoring
        5. FIX #42 (2026-02-05): Captures actual fill price for accurate P&L
        6. Fix #45 (2026-02-06): Handles merged positions (multiple entries at same strike)

        CRITICAL FIX (2026-02-03): Saxo's DELETE /trade/v2/positions/{id} endpoint
        returns 404 for SPX options. Must use place_emergency_order with ToClose instead.

        Args:
            position_id: Saxo position ID
            leg_name: Name for logging (e.g., "short_call", "long_put")
            uic: Option UIC for spread checking (required for placing close order)
            entry_number: Fix #45 - Entry number being closed (for merged position handling)

        Returns:
            Tuple of (success: bool, fill_price: Optional[float])
            - fill_price is the actual price at which the position was closed
            - If fill_price is None, P&L calculation should fall back to theoretical
        """
        # UIC is REQUIRED now - we need it to place the close order
        if not uic:
            logger.error(f"EMERGENCY-001: Cannot close {leg_name} without UIC!")
            return False, None

        # Fix #45: Check if this is a merged position (shared across multiple entries)
        is_shared, shared_entries = self._is_position_shared(position_id)
        amount_before = None
        is_partial_close = False

        if is_shared:
            # Get current position amount before closing
            amount_before = self._get_position_amount(position_id)
            if amount_before is not None:
                # This will be a partial close - only close 1 contract of the merged position
                is_partial_close = True
                logger.info(
                    f"Fix #45: Position {position_id} is shared across entries {shared_entries}, "
                    f"Amount={amount_before}. Performing partial close for Entry #{entry_number}"
                )

        # Determine direction: short positions need BUY to close, long positions need SELL to close
        if leg_name.startswith("short"):
            buy_sell = BuySell.BUY  # Buy back the short position
        else:
            buy_sell = BuySell.SELL  # Sell the long position

        # Fix #46: Check if we're in "limit orders only" period (after 3:45 PM ET)
        # Saxo requires limit orders for the final 15 minutes before market close
        now = get_us_market_time()
        is_limit_only_period = now.hour == 15 and now.minute >= 45

        for attempt in range(EMERGENCY_CLOSE_MAX_ATTEMPTS):
            attempt_num = attempt + 1

            try:
                # Fix #46: Check if position still exists before retrying
                # This prevents 409 Conflict errors when position is already closed
                if attempt > 0:
                    positions = self.client.get_positions()
                    position_exists = any(
                        str(p.get("PositionId", "")) == str(position_id)
                        for p in positions
                    )
                    if not position_exists:
                        logger.info(
                            f"Fix #46: Position {position_id} ({leg_name}) already closed, "
                            f"skipping retry"
                        )
                        # Position is gone - consider it successfully closed
                        # We don't have fill price but that's better than infinite retries
                        try:
                            self.registry.unregister(position_id)
                        except Exception:
                            pass
                        return True, None

                # EMERGENCY-001: Check spread before closing (if UIC available)
                if attempt > 0:  # Skip spread check on first attempt
                    spread_ok, spread_pct = self._check_spread_for_emergency_close(uic)
                    if not spread_ok:
                        logger.warning(
                            f"EMERGENCY-001: Wide spread ({spread_pct:.1f}%) on {leg_name}, "
                            f"waiting for normalization..."
                        )
                        self._wait_for_spread_normalization(uic, leg_name)

                # Fix #46: Use limit orders near market close
                order_type = OrderType.MARKET
                limit_price = None

                if is_limit_only_period:
                    logger.info(f"Fix #46: In limit-only period, using LIMIT order for {leg_name}")
                    quote = self.client.get_quote(uic, asset_type="StockIndexOption")
                    if quote:
                        quote_data = quote.get("Quote", quote)
                        if buy_sell == BuySell.BUY:
                            # Buying to close - use ask price (aggressive)
                            limit_price = quote_data.get("Ask") or quote_data.get("Mid")
                        else:
                            # Selling to close - use bid price (aggressive)
                            limit_price = quote_data.get("Bid") or quote_data.get("Mid")

                        if limit_price:
                            order_type = OrderType.LIMIT
                            logger.info(f"Fix #46: Using LIMIT @ ${limit_price:.2f}")

                # CRITICAL FIX: Use place_emergency_order with ToClose instead of DELETE endpoint
                # This is how Iron Fly and Delta Neutral successfully close positions
                logger.info(
                    f"EMERGENCY-001: Closing {leg_name} via {order_type.value} order "
                    f"(UIC={uic}, {buy_sell.value}, amount={self.contracts_per_entry})"
                    + (f" @ ${limit_price:.2f}" if limit_price else "")
                )
                result = self.client.place_emergency_order(
                    uic=uic,
                    asset_type="StockIndexOption",  # SPX options
                    buy_sell=buy_sell,
                    amount=self.contracts_per_entry,
                    order_type=order_type,
                    to_open_close="ToClose",
                    limit_price=limit_price
                )
                if result:
                    order_id = result.get('OrderId', 'unknown')
                    logger.info(f"EMERGENCY-001: Close order placed for {leg_name}: order {order_id}")

                    # SAFETY-024: Verify position is actually closed after order placement
                    # Fix #45: Pass partial close info for merged position verification
                    time.sleep(1.5)  # Wait for order to fill
                    if self._verify_position_closed(
                        position_id, leg_name, uic,
                        expected_amount_before=amount_before,
                        is_partial_close=is_partial_close
                    ):
                        # Fix #45: Handle registry update based on whether position is shared
                        if is_partial_close and entry_number is not None:
                            # Update registry to remove this entry from shared_entries
                            self._update_registry_for_partial_close(position_id, entry_number)
                        else:
                            # Full close - unregister the entire position
                            try:
                                self.registry.unregister(position_id)
                            except Exception as reg_e:
                                logger.error(f"Registry error unregistering {position_id}: {reg_e}")

                        # FIX #42 (2026-02-05): Get actual fill price from activities
                        fill_price = self._get_close_fill_price(order_id, uic, leg_name)
                        logger.info(f"EMERGENCY-001: Verified {leg_name} closed on attempt {attempt_num}, fill_price=${fill_price:.2f}" if fill_price else f"EMERGENCY-001: Verified {leg_name} closed on attempt {attempt_num}, fill_price=unknown")
                        return True, fill_price
                    else:
                        logger.warning(
                            f"EMERGENCY-001: Order {order_id} placed but position still exists, retrying..."
                        )
                        # Don't count this as fully failed - order may have been rejected
                else:
                    # Order placement returned None - likely an API error
                    logger.error(f"EMERGENCY-001: Close order returned None for {leg_name} on attempt {attempt_num}")

                    # SAFETY-024: Send alert on FIRST failure so we know immediately
                    if attempt_num == 1:
                        self.alert_service.send_alert(
                            alert_type=AlertType.EMERGENCY_CLOSE,
                            title="STOP CLOSE FAILED - IMMEDIATE",
                            message=f"First attempt to close {leg_name} failed! "
                                    f"Position ID: {position_id}, UIC: {uic}. "
                                    f"Will retry {EMERGENCY_CLOSE_MAX_ATTEMPTS - 1} more times.",
                            priority=AlertPriority.HIGH
                        )

            except Exception as e:
                logger.error(f"EMERGENCY-001: Close {leg_name} attempt {attempt_num} failed: {e}")

                # SAFETY-024: Send alert on FIRST failure with exception
                if attempt_num == 1:
                    self.alert_service.send_alert(
                        alert_type=AlertType.EMERGENCY_CLOSE,
                        title="STOP CLOSE EXCEPTION - IMMEDIATE",
                        message=f"Exception closing {leg_name}: {str(e)[:100]}. "
                                f"Position ID: {position_id}. Will retry.",
                        priority=AlertPriority.HIGH
                    )

                # Escalating alerts based on attempt number
                if attempt_num == 3:
                    logger.warning(f"EMERGENCY-001: {leg_name} close failed 3 times, continuing retries...")
                elif attempt_num >= 4:
                    self.alert_service.send_alert(
                        alert_type=AlertType.EMERGENCY_CLOSE,
                        title="EMERGENCY CLOSE STRUGGLING",
                        message=f"Failed to close {leg_name} after {attempt_num} attempts. "
                                f"Position ID: {position_id}. Error: {e}",
                        priority=AlertPriority.HIGH if attempt_num == 4 else AlertPriority.CRITICAL
                    )

            if attempt < EMERGENCY_CLOSE_MAX_ATTEMPTS - 1:
                time.sleep(EMERGENCY_CLOSE_RETRY_DELAY_SECONDS)

        # All attempts exhausted
        error_msg = (
            f"EMERGENCY-001 CRITICAL: FAILED to close {leg_name} after "
            f"{EMERGENCY_CLOSE_MAX_ATTEMPTS} attempts! Position ID: {position_id}"
        )
        logger.critical(error_msg)
        self.alert_service.send_alert(
            alert_type=AlertType.CIRCUIT_BREAKER,
            title="EMERGENCY CLOSE FAILED",
            message=error_msg,
            priority=AlertPriority.CRITICAL
        )
        self._log_safety_event("EMERGENCY_CLOSE_FAILED", error_msg, "Failed")
        return False, None

    def _check_spread_for_emergency_close(self, uic: int) -> Tuple[bool, float]:
        """
        EMERGENCY-001: Check if bid-ask spread is acceptable for emergency close.

        Wide spreads during emergency closes result in bad fill prices.
        This method checks if the spread is within acceptable limits.

        Args:
            uic: Option UIC to check

        Returns:
            Tuple of (is_acceptable, spread_percent)
        """
        try:
            quote = self.client.get_quote(uic, asset_type="StockIndexOption")
            if not quote:
                logger.warning(f"EMERGENCY-001: No quote for UIC {uic}, proceeding anyway")
                return (True, 0.0)  # Proceed if no quote available

            bid = quote.get("Quote", {}).get("Bid", 0)
            ask = quote.get("Quote", {}).get("Ask", 0)

            if bid <= 0 or ask <= 0:
                logger.warning(f"EMERGENCY-001: Invalid bid/ask for UIC {uic}, proceeding anyway")
                return (True, 0.0)

            mid = (bid + ask) / 2
            spread = ask - bid
            spread_pct = (spread / mid) * 100 if mid > 0 else 0

            is_acceptable = spread_pct <= EMERGENCY_SPREAD_MAX_PERCENT

            if not is_acceptable:
                logger.warning(
                    f"EMERGENCY-001: Wide spread detected - UIC {uic}: "
                    f"bid=${bid:.2f}, ask=${ask:.2f}, spread={spread_pct:.1f}% "
                    f"(max={EMERGENCY_SPREAD_MAX_PERCENT}%)"
                )

            return (is_acceptable, spread_pct)

        except Exception as e:
            logger.error(f"EMERGENCY-001: Error checking spread for UIC {uic}: {e}")
            return (True, 0.0)  # Proceed on error

    def _is_position_shared(self, position_id: str) -> Tuple[bool, List[int]]:
        """
        Fix #45: Check if a position is shared across multiple entries.

        When two entries have options at the same strike, Saxo merges them into
        a single position with Amount=-2. This function checks the registry
        metadata to determine if a position is shared.

        Args:
            position_id: Saxo position ID to check

        Returns:
            Tuple of (is_shared: bool, shared_entries: List[int])
            - is_shared: True if position has multiple entries sharing it
            - shared_entries: List of entry numbers sharing this position
        """
        try:
            reg_info = self.registry.get_position_info(position_id)
            if not reg_info:
                return False, []

            metadata = reg_info.get("metadata", {})
            shared_entries = metadata.get("shared_entries", [])

            if shared_entries and len(shared_entries) > 1:
                return True, shared_entries
            return False, []

        except Exception as e:
            logger.error(f"Fix #45: Error checking shared position {position_id}: {e}")
            return False, []

    def _get_position_amount(self, position_id: str) -> Optional[int]:
        """
        Fix #45: Get the current Amount for a position from Saxo.

        Args:
            position_id: Saxo position ID

        Returns:
            Position amount (negative for shorts), or None if not found
        """
        try:
            positions = self.client.get_positions()
            for pos in positions:
                pos_id = str(pos.get("PositionId", ""))
                if pos_id == str(position_id):
                    return pos.get("PositionBase", {}).get("Amount")
            return None
        except Exception as e:
            logger.error(f"Fix #45: Error getting position amount for {position_id}: {e}")
            return None

    def _update_registry_for_partial_close(self, position_id: str, closed_entry_number: int) -> bool:
        """
        Fix #45: Update registry when one entry of a shared position is closed.

        When Entry #4 stops out but Entry #5 still has contracts in the same
        merged position, we need to:
        1. Remove Entry #4 from the shared_entries list
        2. Keep the position registered for Entry #5
        3. If only one entry remains, remove shared_entries metadata

        Args:
            position_id: Saxo position ID
            closed_entry_number: Entry number that was stopped out

        Returns:
            True if registry updated successfully
        """
        try:
            reg_info = self.registry.get_position_info(position_id)
            if not reg_info:
                logger.warning(f"Fix #45: Position {position_id} not in registry for partial close update")
                return False

            metadata = reg_info.get("metadata", {})
            shared_entries = metadata.get("shared_entries", [])

            if not shared_entries:
                logger.warning(f"Fix #45: Position {position_id} has no shared_entries metadata")
                return False

            # Remove the closed entry from shared_entries
            if closed_entry_number in shared_entries:
                shared_entries.remove(closed_entry_number)
                logger.info(f"Fix #45: Removed Entry #{closed_entry_number} from shared_entries, remaining: {shared_entries}")

            # Update registry with new metadata
            if len(shared_entries) == 1:
                # Only one entry left - remove shared_entries, update primary entry_number
                remaining_entry = shared_entries[0]
                new_metadata = metadata.copy()
                del new_metadata["shared_entries"]
                new_metadata["entry_number"] = remaining_entry
                logger.info(f"Fix #45: Position {position_id} now solely owned by Entry #{remaining_entry}")
            elif len(shared_entries) > 1:
                # Still multiple entries - update shared_entries list
                new_metadata = metadata.copy()
                new_metadata["shared_entries"] = shared_entries
                # Update primary entry_number to first remaining entry
                new_metadata["entry_number"] = shared_entries[0]
            else:
                # No entries left - should not happen, but handle gracefully
                logger.warning(f"Fix #45: No entries left for position {position_id} after removing Entry #{closed_entry_number}")
                self.registry.unregister(position_id)
                return True

            # Update the registry entry with new metadata
            # Note: PositionRegistry doesn't have an update method, so we unregister and re-register
            bot_name = reg_info.get("bot_name", self.bot_name)
            strategy_id = reg_info.get("strategy_id", self.strategy_id)
            self.registry.unregister(position_id)
            self.registry.register(position_id, bot_name, strategy_id, new_metadata)

            logger.info(f"Fix #45: Updated registry for position {position_id}: {new_metadata}")
            return True

        except Exception as e:
            logger.error(f"Fix #45: Error updating registry for partial close: {e}")
            return False

    def _verify_position_closed(
        self, position_id: str, leg_name: str, uic: int,
        expected_amount_before: Optional[int] = None, is_partial_close: bool = False
    ) -> bool:
        """
        SAFETY-024: Verify that a position was actually closed after placing a close order.

        This is critical because the order placement can succeed (return an order ID)
        but the order might get rejected, leaving the position open. We need to verify
        the position is actually gone before marking it as closed.

        Fix #45: Now supports partial closes for merged positions. When two entries
        share a position (e.g., Amount=-2), closing one entry's contract should reduce
        Amount to -1, not fully close the position.

        Args:
            position_id: Saxo position ID that should be closed
            leg_name: Name for logging
            uic: UIC of the option (for additional verification)
            expected_amount_before: Fix #45 - Amount before close (e.g., -2 for merged short)
            is_partial_close: Fix #45 - True if this is a partial close (merged position)

        Returns:
            True if position is confirmed closed (or partially closed for merged), False otherwise
        """
        try:
            # Check if position still exists in Saxo
            positions = self.client.get_positions()

            # Look for the position by ID
            for pos in positions:
                pos_id = str(pos.get("PositionId", ""))
                if pos_id == str(position_id):
                    current_amount = pos.get("PositionBase", {}).get("Amount", 0)

                    # Fix #45: For partial closes on merged positions, verify Amount decreased
                    if is_partial_close and expected_amount_before is not None:
                        # For shorts: expected_amount_before = -2, after partial close = -1
                        # Amount should have increased (less negative)
                        if expected_amount_before < 0:
                            # Short position: Amount should be closer to 0
                            expected_amount_after = expected_amount_before + self.contracts_per_entry
                            if current_amount == expected_amount_after:
                                logger.info(
                                    f"Fix #45: Verified partial close on {leg_name}: "
                                    f"Amount changed from {expected_amount_before} to {current_amount}"
                                )
                                return True
                            else:
                                logger.warning(
                                    f"Fix #45: Partial close verification failed for {leg_name}: "
                                    f"Expected Amount={expected_amount_after}, got {current_amount}"
                                )
                                return False
                        else:
                            # Long position: Amount should be closer to 0
                            expected_amount_after = expected_amount_before - self.contracts_per_entry
                            if current_amount == expected_amount_after:
                                logger.info(
                                    f"Fix #45: Verified partial close on {leg_name}: "
                                    f"Amount changed from {expected_amount_before} to {current_amount}"
                                )
                                return True
                            else:
                                logger.warning(
                                    f"Fix #45: Partial close verification failed for {leg_name}: "
                                    f"Expected Amount={expected_amount_after}, got {current_amount}"
                                )
                                return False

                    # Position still exists and not a partial close
                    logger.warning(
                        f"SAFETY-024: Position {position_id} ({leg_name}) still exists after close order! "
                        f"Amount={current_amount}"
                    )
                    return False

            # Position not found - it was fully closed
            # Also check by UIC in case position ID changed
            for pos in positions:
                pos_uic = pos.get("PositionBase", {}).get("Uic")
                pos_asset_type = pos.get("PositionBase", {}).get("AssetType")
                if pos_uic == uic and pos_asset_type == "StockIndexOption":
                    # Found a position with same UIC - might be the same one
                    logger.warning(
                        f"SAFETY-024: Found position with UIC {uic} ({leg_name}) - may not be closed"
                    )
                    # Don't return False here - could be a different position
                    # Just log the warning

            logger.info(f"SAFETY-024: Verified position {position_id} ({leg_name}) is closed")
            return True

        except Exception as e:
            logger.error(f"SAFETY-024: Error verifying position closed: {e}")
            # On error, assume closed to avoid infinite retry loops
            # The alert was already sent, manual verification needed
            return True

    def _wait_for_spread_normalization(self, uic: int, leg_name: str) -> bool:
        """
        EMERGENCY-001: Wait for bid-ask spread to normalize before closing.

        If spread is too wide, wait a short time for it to normalize.
        This helps avoid extremely bad fills during volatile moments.

        Args:
            uic: Option UIC to monitor
            leg_name: Name for logging

        Returns:
            True if spread normalized, False if still wide after waiting
        """
        for wait_attempt in range(EMERGENCY_SPREAD_MAX_WAIT_ATTEMPTS):
            wait_num = wait_attempt + 1
            logger.info(
                f"EMERGENCY-001: Waiting {EMERGENCY_SPREAD_WAIT_SECONDS}s for {leg_name} "
                f"spread to normalize (attempt {wait_num}/{EMERGENCY_SPREAD_MAX_WAIT_ATTEMPTS})..."
            )

            time.sleep(EMERGENCY_SPREAD_WAIT_SECONDS)

            spread_ok, spread_pct = self._check_spread_for_emergency_close(uic)
            if spread_ok:
                logger.info(
                    f"EMERGENCY-001: Spread normalized for {leg_name} "
                    f"(now {spread_pct:.1f}%), proceeding with close"
                )
                return True

            logger.warning(
                f"EMERGENCY-001: Spread still wide for {leg_name} ({spread_pct:.1f}%), "
                f"will retry..."
            )

        # Spread still wide after all wait attempts
        logger.warning(
            f"EMERGENCY-001: Spread did not normalize for {leg_name} after "
            f"{EMERGENCY_SPREAD_MAX_WAIT_ATTEMPTS} waits, proceeding with close anyway"
        )
        return False

    def _get_close_fill_price(self, order_id: str, uic: int, leg_name: str) -> Optional[float]:
        """
        FIX #42 (2026-02-05): Get actual fill price for a close order.

        Queries the activities endpoint to find the fill price for accurate P&L.
        This fixes the bug where theoretical stop levels were used instead of
        actual close prices, causing massive P&L discrepancies.

        FIX #51 (2026-02-10): Reduced retries from 3 to 1 since this function is
        called AFTER _verify_position_closed() confirms the position is closed.
        We KNOW the order filled - if FilledPrice isn't populated yet due to
        Saxo's sync delay, use quote fallback immediately. Saves ~2-3 seconds
        per leg during stop loss execution.

        Args:
            order_id: The emergency close order ID
            uic: The instrument UIC
            leg_name: Name for logging (e.g., "short_call")

        Returns:
            Fill price in dollars (e.g., 8.90), or None if not found
        """
        try:
            # FIX #51: Only 1 retry since we already verified position is closed
            # If FilledPrice=0, use quote fallback immediately (saves 2-3 seconds)
            filled, fill_details = self.client.check_order_filled_by_activity(
                order_id=order_id,
                uic=uic,
                max_retries=1,
                retry_delay=0.5
            )

            if filled and fill_details:
                fill_price = fill_details.get("fill_price")
                if fill_price and fill_price > 0:
                    logger.info(f"FIX-42: Got actual fill price for {leg_name}: ${fill_price:.2f}")
                    return fill_price
                else:
                    logger.warning(f"FIX-42: Activity found but fill_price=0 for {leg_name}")

            # Fallback: Try to get from current quote (less accurate but better than nothing)
            quote = self.client.get_quote(uic, asset_type="StockIndexOption")
            if quote:
                # For buys (closing shorts), we paid the ask
                # For sells (closing longs), we received the bid
                is_short = leg_name.startswith("short")
                if is_short:
                    # We bought to close at ask price
                    price = quote.get("Quote", {}).get("Ask") or quote.get("Quote", {}).get("Mid")
                else:
                    # We sold to close at bid price
                    price = quote.get("Quote", {}).get("Bid") or quote.get("Quote", {}).get("Mid")
                if price:
                    logger.warning(f"FIX-42: Using current quote as fallback for {leg_name}: ${price:.2f}")
                    return price

            logger.error(f"FIX-42: Could not determine fill price for {leg_name}")
            return None

        except Exception as e:
            logger.error(f"FIX-42: Error getting fill price for {leg_name}: {e}")
            return None

    # =========================================================================
    # MARKET DATA
    # =========================================================================

    def _update_market_data(self):
        """Update SPX and VIX prices from cache or API."""
        # US500.I is a CFD that tracks SPX - use CfdOnIndex asset type
        quote = self.client.get_quote(self.underlying_uic, asset_type="CfdOnIndex")
        if quote:
            price = self._extract_price(quote, context="SPX")
            if price:
                self.market_data.update_spx(price)
                self.current_price = price

        # VIX - use get_vix_price which has Yahoo Finance fallback
        vix = self.client.get_vix_price(self.vix_uic)
        if vix:
            self.market_data.update_vix(vix)
            self.current_vix = vix

    def _extract_price(self, quote: Dict, context: str = "price") -> Optional[float]:
        """
        Extract price from quote response with freshness check.

        DATA-001: Checks quote staleness and logs warnings if data is old.

        Args:
            quote: Quote response from Saxo API
            context: Description for logging (e.g., "SPX", "short_call")

        Returns:
            Mid price, last traded, or None if no valid price
        """
        # DATA-001: Check quote freshness
        self._check_quote_freshness(quote, context)

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

    def _check_quote_freshness(self, quote: Dict, context: str = "quote") -> bool:
        """
        DATA-001: Check if quote data is fresh (< 60 seconds old).

        Logs warnings when quotes are stale, which could indicate API issues
        or slow data feeds that might affect trading decisions.

        Args:
            quote: Quote response from Saxo API
            context: Description for logging

        Returns:
            True if quote is fresh, False if stale or unknown
        """
        # Try to extract timestamp from quote
        # Saxo returns timestamps in various fields
        timestamp_str = None

        # Check common timestamp locations
        quote_block = quote.get("Quote", {})
        price_info = quote.get("PriceInfo", {})
        price_info_details = quote.get("PriceInfoDetails", {})

        # Try different timestamp fields
        for block in [quote_block, price_info, price_info_details]:
            for field in ["LastUpdated", "LastTradedAt", "DateTime"]:
                if field in block:
                    timestamp_str = block[field]
                    break
            if timestamp_str:
                break

        if not timestamp_str:
            # No timestamp found - can't determine freshness
            logger.debug(f"DATA-001: No timestamp in quote for {context}, cannot verify freshness")
            return True  # Assume fresh if we can't check

        try:
            # Parse ISO format timestamp
            from datetime import datetime, timezone
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1] + '+00:00'

            quote_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            age_seconds = (now - quote_time).total_seconds()

            if age_seconds > MAX_DATA_STALENESS_SECONDS:
                logger.warning(
                    f"DATA-001 STALE QUOTE: {context} quote is {age_seconds:.1f}s old "
                    f"(max {MAX_DATA_STALENESS_SECONDS}s). Consider refreshing."
                )
                self._log_safety_event("STALE_QUOTE", f"{context}: {age_seconds:.1f}s old")
                return False

            logger.debug(f"DATA-001: {context} quote is {age_seconds:.1f}s old (fresh)")
            return True

        except Exception as e:
            logger.debug(f"DATA-001: Could not parse quote timestamp for {context}: {e}")
            return True  # Assume fresh if parsing fails

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
        self._log_safety_event("CIRCUIT_BREAKER_OPEN", reason, "Bot Halted")

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
        self._log_safety_event("CRITICAL_INTERVENTION", reason, "Manual Action Required")

        self.alert_service.send_alert(
            alert_type=AlertType.CRITICAL_INTERVENTION,
            title="MEIC BOT HALTED",
            message=f"MEIC HALTED: {reason}. Manual intervention required.",
            priority=AlertPriority.CRITICAL
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

        # FIX (2026-02-03): Use Saxo's authoritative P&L
        unrealized = self._get_total_saxo_pnl()
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
                # CRITICAL FIX (2026-02-03): Set date even when returning False
                # to prevent _reset_for_new_day from treating this as "overnight"
                self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")
                return False

            logger.info(f"Found {len(all_positions)} total positions in account")

            # Step 2: Get valid position IDs and clean up registry orphans
            # FIX (2026-02-04): Skip in dry-run mode - DRY_ positions don't exist in Saxo
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

            # Step 3: Get MEIC positions from registry
            my_position_ids = self.registry.get_positions("MEIC")
            if not my_position_ids:
                logger.info("No MEIC positions in registry - starting fresh")
                # CRITICAL FIX (2026-02-03): Set date even when returning False
                self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")
                return False

            logger.info(f"Found {len(my_position_ids)} MEIC positions in registry")

            # Step 4: Filter Saxo positions to just MEIC positions
            meic_positions = []
            for pos in all_positions:
                pos_id = str(pos.get("PositionId"))
                if pos_id in my_position_ids:
                    meic_positions.append(pos)

            if not meic_positions:
                logger.warning("Registry says we have MEIC positions but none found in Saxo! Cleaning registry...")
                # Clear MEIC positions from registry since they don't exist
                for pos_id in my_position_ids:
                    try:
                        self.registry.unregister(pos_id)
                    except Exception as e:
                        logger.error(f"Registry error unregistering {pos_id}: {e}")
                self._log_safety_event("REGISTRY_CLEARED", "All MEIC positions removed - not found in Saxo")
                # CRITICAL FIX (2026-02-03): Set date even when returning False
                self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")
                return False

            logger.info(f"Matched {len(meic_positions)} positions to MEIC in Saxo")

            # Step 5: Group positions by entry number using registry metadata
            entries_by_number = self._group_positions_by_entry(meic_positions, my_position_ids)

            if not entries_by_number:
                logger.warning("Could not group positions into entries via registry - trying UIC fallback...")
                # CRITICAL FIX (2026-02-03): Try UIC-based recovery from state file
                entries_by_number = self._recover_from_state_file_uics(all_positions)
                if not entries_by_number:
                    logger.warning("UIC-based recovery also failed - manual review needed")
                    self._log_safety_event("RECOVERY_FAILED", "Could not reconstruct entries from positions or UICs", "Manual Review Needed")
                    # CRITICAL FIX (2026-02-03): Set date even when returning False
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
                # CRITICAL FIX (2026-02-03): Set date even when returning False
                self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")
                return False

            # Step 7: Update local state to match Saxo
            today = get_us_market_time().strftime("%Y-%m-%d")

            # CRITICAL FIX (2026-02-03): Load existing state file to preserve realized P&L
            # The state file contains historical data like realized P&L from closed positions
            # that cannot be recovered from Saxo's current positions alone
            preserved_realized_pnl = 0.0
            preserved_put_stops = 0
            preserved_call_stops = 0
            preserved_double_stops = 0
            preserved_total_commission = 0.0  # Commission tracking
            preserved_entry_credits = {}  # entry_number -> (call_credit, put_credit, call_stop, put_stop)
            preserved_stopped_entries = []  # FIX #43: Fully stopped entries (no live positions)
            try:
                if os.path.exists(self.state_file):
                    with open(self.state_file, "r") as f:
                        saved_state = json.load(f)
                        # Only use saved state if it's from today
                        if saved_state.get("date") == today:
                            preserved_realized_pnl = saved_state.get("total_realized_pnl", 0.0)
                            preserved_put_stops = saved_state.get("put_stops_triggered", 0)
                            preserved_call_stops = saved_state.get("call_stops_triggered", 0)
                            preserved_double_stops = saved_state.get("double_stops", 0)
                            preserved_total_commission = saved_state.get("total_commission", 0.0)
                            # Preserve original credits, stops, and strikes from entries
                            # Strikes are needed for stopped sides (no longer in Saxo)
                            for entry_data in saved_state.get("entries", []):
                                entry_num = entry_data.get("entry_number")
                                if entry_num:
                                    preserved_entry_credits[entry_num] = {
                                        "call_credit": entry_data.get("call_spread_credit", 0),
                                        "put_credit": entry_data.get("put_spread_credit", 0),
                                        "call_stop": entry_data.get("call_side_stop", 0),
                                        "put_stop": entry_data.get("put_side_stop", 0),
                                        # Preserve strikes for stopped sides (display purposes)
                                        "short_call_strike": entry_data.get("short_call_strike", 0),
                                        "long_call_strike": entry_data.get("long_call_strike", 0),
                                        "short_put_strike": entry_data.get("short_put_strike", 0),
                                        "long_put_strike": entry_data.get("long_put_strike", 0),
                                        # Preserve stopped/expired/skipped flags
                                        "call_side_stopped": entry_data.get("call_side_stopped", False),
                                        "put_side_stopped": entry_data.get("put_side_stopped", False),
                                        "call_side_expired": entry_data.get("call_side_expired", False),
                                        "put_side_expired": entry_data.get("put_side_expired", False),
                                        "call_side_skipped": entry_data.get("call_side_skipped", False),
                                        "put_side_skipped": entry_data.get("put_side_skipped", False),
                                        # Commission tracking
                                        "open_commission": entry_data.get("open_commission", 0),
                                        "close_commission": entry_data.get("close_commission", 0),
                                        # FIX #43: Preserve one-sided entry flags for MEIC-TF
                                        "call_only": entry_data.get("call_only", False),
                                        "put_only": entry_data.get("put_only", False),
                                        "trend_signal": entry_data.get("trend_signal"),
                                    }
                                    # FIX #43 + FIX #47: Check if this entry is fully done (no live positions)
                                    # A side is "done" if it was stopped OR expired OR skipped
                                    # Skipped = never opened (MEIC-TF one-sided entry)
                                    # For one-sided entries: done if placed side is done
                                    # For full IC: done if both sides are done
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
                                        is_fully_done = True  # One-sided call entry, call done
                                    elif put_only and put_done:
                                        is_fully_done = True  # One-sided put entry, put done
                                    elif not call_only and not put_only and call_done and put_done:
                                        is_fully_done = True  # Full IC, both sides done

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
                    # Restore original credits (not current spread values)
                    entry.call_spread_credit = saved["call_credit"]
                    entry.put_spread_credit = saved["put_credit"]
                    # Restore stop levels
                    entry.call_side_stop = saved["call_stop"]
                    entry.put_side_stop = saved["put_stop"]

                    # Restore stopped/expired flags from state file
                    # (may be more accurate than reconstruction from positions)
                    if saved.get("call_side_stopped"):
                        entry.call_side_stopped = True
                    if saved.get("put_side_stopped"):
                        entry.put_side_stopped = True
                    if saved.get("call_side_expired"):
                        entry.call_side_expired = True
                    if saved.get("put_side_expired"):
                        entry.put_side_expired = True
                    # FIX #47: Also restore skipped flags (for MEIC-TF one-sided entries)
                    if saved.get("call_side_skipped"):
                        entry.call_side_skipped = True
                    if saved.get("put_side_skipped"):
                        entry.put_side_skipped = True

                    # Restore commission tracking
                    entry.open_commission = saved.get("open_commission", 0)
                    entry.close_commission = saved.get("close_commission", 0)

                    # Restore strikes for stopped/expired/skipped sides (positions no longer exist in Saxo)
                    # Only restore if the strike is 0 (not recovered from Saxo)
                    call_done = entry.call_side_stopped or entry.call_side_expired or entry.call_side_skipped
                    put_done = entry.put_side_stopped or entry.put_side_expired or entry.put_side_skipped
                    if call_done and entry.short_call_strike == 0:
                        entry.short_call_strike = saved.get("short_call_strike", 0)
                        entry.long_call_strike = saved.get("long_call_strike", 0)
                        status = "stopped" if entry.call_side_stopped else "expired"
                        logger.info(f"Entry #{entry.entry_number}: Restored {status} call strikes "
                                   f"(short={entry.short_call_strike}, long={entry.long_call_strike})")
                    if put_done and entry.short_put_strike == 0:
                        entry.short_put_strike = saved.get("short_put_strike", 0)
                        entry.long_put_strike = saved.get("long_put_strike", 0)
                        status = "stopped" if entry.put_side_stopped else "expired"
                        logger.info(f"Entry #{entry.entry_number}: Restored {status} put strikes "
                                   f"(short={entry.short_put_strike}, long={entry.long_put_strike})")

                    logger.info(f"Entry #{entry.entry_number}: Restored credits from state file "
                               f"(call=${saved['call_credit']:.2f}, put=${saved['put_credit']:.2f}, "
                               f"stop=${saved['call_stop']:.2f})")

            # FIX #43 (2026-02-05): Reconstruct fully stopped entries that have no live positions
            # These entries won't be in recovered_entries (which comes from Saxo positions)
            # but we need them for accurate P&L tracking and display
            recovered_entry_nums = {e.entry_number for e in recovered_entries}
            for stopped_entry_data in preserved_stopped_entries:
                entry_num = stopped_entry_data.get("entry_number")
                if entry_num and entry_num not in recovered_entry_nums:
                    # Reconstruct IronCondorEntry from saved state data
                    stopped_entry = IronCondorEntry(entry_number=entry_num)
                    stopped_entry.entry_time = stopped_entry_data.get("entry_time")
                    stopped_entry.strategy_id = stopped_entry_data.get("strategy_id", f"meic_{today.replace('-', '')}_{entry_num:03d}")
                    # Fix #52: Restore contract count (default to current config if not saved)
                    stopped_entry.contracts = stopped_entry_data.get("contracts", self.contracts_per_entry)

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

                    # Stopped/expired/skipped flags - entry is fully done
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

                    # One-sided entry flags (MEIC-TF uses TFIronCondorEntry which has these)
                    # For base MEIC, these attributes don't exist in the dataclass, so we add them dynamically
                    # This allows MEIC-TF subclass to work with the preserved data
                    call_only = stopped_entry_data.get("call_only", False)
                    put_only = stopped_entry_data.get("put_only", False)
                    trend_signal = stopped_entry_data.get("trend_signal")
                    if call_only or put_only or trend_signal:
                        # Only set these for one-sided entries (MEIC-TF)
                        stopped_entry.call_only = call_only
                        stopped_entry.put_only = put_only
                        stopped_entry.trend_signal = trend_signal

                    # Position IDs are None (positions closed)
                    stopped_entry.short_call_position_id = None
                    stopped_entry.long_call_position_id = None
                    stopped_entry.short_put_position_id = None
                    stopped_entry.long_put_position_id = None

                    recovered_entries.append(stopped_entry)

                    # Log with one-sided info if applicable
                    one_sided_info = ""
                    if hasattr(stopped_entry, 'call_only') and stopped_entry.call_only:
                        one_sided_info = ", call_only=True"
                    elif hasattr(stopped_entry, 'put_only') and stopped_entry.put_only:
                        one_sided_info = ", put_only=True"
                    logger.info(f"FIX #43: Restored fully stopped Entry #{entry_num} from state file "
                               f"(credit=${stopped_entry.total_credit:.2f}{one_sided_info})")

            # Sort recovered entries by entry number for consistent ordering
            recovered_entries.sort(key=lambda e: e.entry_number)

            # Reset daily state but preserve date
            self.daily_state = MEICDailyState()
            self.daily_state.date = today
            self.daily_state.entries = recovered_entries
            self.daily_state.entries_completed = len(recovered_entries)

            # CRITICAL: Restore preserved P&L and stop counters
            self.daily_state.total_realized_pnl = preserved_realized_pnl
            self.daily_state.put_stops_triggered = preserved_put_stops
            self.daily_state.call_stops_triggered = preserved_call_stops
            self.daily_state.double_stops = preserved_double_stops
            self.daily_state.total_commission = preserved_total_commission

            # Determine next entry index (how many entries have we done?)
            if recovered_entries:
                max_entry_num = max(e.entry_number for e in recovered_entries)
                self._next_entry_index = max_entry_num  # Next entry will be max_entry_num + 1
            else:
                self._next_entry_index = 0  # No entries recovered, start fresh

            # Set state based on recovered positions
            if recovered_entries:
                # Check if any entries still have active positions
                # FIX #43 + FIX #47: For one-sided entries, check the placed side only
                # A side is "done" if stopped, expired, or skipped
                def is_entry_active(entry):
                    call_done = entry.call_side_stopped or entry.call_side_expired or entry.call_side_skipped
                    put_done = entry.put_side_stopped or entry.put_side_expired or entry.put_side_skipped
                    call_only = getattr(entry, 'call_only', False)
                    put_only = getattr(entry, 'put_only', False)
                    if call_only:
                        return not call_done
                    elif put_only:
                        return not put_done
                    else:
                        # Full IC - active if either side is not done
                        return not (call_done and put_done)

                active_entries = [e for e in recovered_entries if is_entry_active(e)]
                if active_entries:
                    self.state = MEICState.MONITORING
                elif self._next_entry_index < len(self.entry_times):
                    self.state = MEICState.WAITING_FIRST_ENTRY
                else:
                    self.state = MEICState.DAILY_COMPLETE

            # Calculate total credit from entries (using preserved/restored values)
            total_credit = sum(e.total_credit for e in recovered_entries)
            self.daily_state.total_credit_received = total_credit

            # Retroactively calculate commission for entries without commission data
            # This handles state files from before commission tracking was added (v1.2.2)
            if self.daily_state.total_commission == 0 and recovered_entries:
                retroactive_commission = 0.0
                for entry in recovered_entries:
                    # Open commission: 4 legs × $2.50 = $10 per full IC
                    # FIX #43: One-sided entries only have 2 legs
                    if entry.open_commission == 0:
                        call_only = getattr(entry, 'call_only', False)
                        put_only = getattr(entry, 'put_only', False)
                        num_legs = 2 if (call_only or put_only) else 4
                        entry.open_commission = num_legs * self.commission_per_leg * self.contracts_per_entry
                        retroactive_commission += entry.open_commission
                    # Close commission: 2 legs per stopped side × $2.50 = $5 per side
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
            self._log_safety_event("RECOVERY_ERROR", str(e), "Error")
            # CRITICAL FIX (2026-02-03): Set date even on exception
            self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")
            return False

    def _recover_from_state_file_uics(self, all_positions: List[Dict]) -> Dict[int, List[Dict]]:
        """
        CRITICAL FIX (2026-02-03): Fallback recovery using UICs from state file.

        When registry-based recovery fails (e.g., due to "None" string bug),
        try to match positions using the UICs stored in the state file.

        Args:
            all_positions: All positions from Saxo API

        Returns:
            Dict mapping entry_number -> list of position data dicts, or empty dict
        """
        logger.info("Attempting UIC-based recovery from state file...")

        # Load state file
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
                        # FIX (2026-02-03): PositionId is at TOP LEVEL, not in PositionBase
                        pos_id = str(pos.get("PositionId"))
                        if pos_id and pos_id != "None":
                            # Find the entry data to get strategy_id
                            for entry_data in entries_data:
                                if entry_data.get("entry_number") == entry_num:
                                    strategy_id = entry_data.get("strategy_id", f"meic_{today}_entry{entry_num}")
                                    try:
                                        self.registry.register(
                                            position_id=pos_id,
                                            bot_name="MEIC",
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
            # FIX (2026-02-03): PositionId is at TOP LEVEL, not in PositionBase
            pos_id = str(pos.get("PositionId"))

            # Get registry metadata for this position
            reg_info = self.registry.get_position_details(pos_id)
            if not reg_info:
                logger.warning(f"Position {pos_id} in Saxo but no registry info - orphan?")
                continue

            metadata = reg_info.get("metadata", {})
            entry_number = metadata.get("entry_number")
            leg_type = metadata.get("leg_type")
            # Fix #44: Support merged positions (multiple entries at same strike)
            shared_entries = metadata.get("shared_entries", [])
            # Note: strike from metadata not used - actual strike comes from parsed position

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

                # Fix #44: If position is shared across entries (merged), add to all entries
                for shared_entry_num in shared_entries:
                    if shared_entry_num != entry_number:  # Avoid duplicate
                        shared_parsed = parsed.copy()
                        shared_parsed["entry_number"] = shared_entry_num
                        if shared_entry_num not in entries_by_number:
                            entries_by_number[shared_entry_num] = []
                        entries_by_number[shared_entry_num].append(shared_parsed)
                        logger.info(f"Fix #44: Position {pos_id} is shared - added to Entry #{shared_entry_num}")

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

            # FIX (2026-02-03): PositionId is at TOP LEVEL, not in PositionBase
            position_id = str(pos.get("PositionId"))
            uic = pos_base.get("Uic")
            amount = pos_base.get("Amount", 0)

            # Get option details
            options_data = pos_base.get("OptionsData", {})
            # FIX (2026-02-03): Saxo API uses "Strike", not "StrikePrice"
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
        # Fix #52: Set contract count for multi-contract support
        entry.contracts = self.contracts_per_entry

        # FIX (2026-02-05): Use dictionary approach to handle positions in any order
        # Previous code assumed positions arrived in a specific order (short before long).
        # If long arrived first, the subtraction happened from 0, then short OVERWROTE it.
        # Now we collect ALL entry prices first, then calculate NET credit correctly.
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
        # This ensures correct calculation regardless of position processing order
        entry.call_spread_credit = entry_prices["short_call"] - entry_prices["long_call"]
        entry.put_spread_credit = entry_prices["short_put"] - entry_prices["long_put"]

        logger.debug(
            f"Entry #{entry_number} recovered credits: "
            f"Call=${entry.call_spread_credit:.2f} (short ${entry_prices['short_call']:.2f} - long ${entry_prices['long_call']:.2f}), "
            f"Put=${entry.put_spread_credit:.2f} (short ${entry_prices['short_put']:.2f} - long ${entry_prices['long_put']:.2f})"
        )

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
        # FIX (2026-02-03): Per MEIC spec, stop per side = TOTAL credit, NOT half
        # "Stop loss on each side = Total credit received for the FULL iron condor"
        # This ensures breakeven when one side stops and other expires worthless
        total_credit = entry.call_spread_credit + entry.put_spread_credit

        # CRITICAL SAFETY CHECK (2026-02-04): Prevent zero stop levels
        # Must match MIN_STOP_LEVEL in _calculate_stop_levels()
        MIN_STOP_LEVEL = 50.0
        if total_credit < MIN_STOP_LEVEL:
            logger.critical(
                f"Recovery CRITICAL: Entry #{entry.entry_number} has dangerously low credit "
                f"(${total_credit:.2f}). Using minimum stop level ${MIN_STOP_LEVEL:.2f}."
            )
            total_credit = MIN_STOP_LEVEL

        if self.meic_plus_enabled:
            # MEIC+ stops at credit - reduction for potential small win
            # STOP-002: Don't apply if stop would be too tight (credit < $1.50)
            # Must match logic in _calculate_stop_levels()
            min_credit_for_meic_plus = self.strategy_config.get("meic_plus_min_credit", 1.50) * 100
            if total_credit > min_credit_for_meic_plus:
                stop_level = total_credit - self.meic_plus_reduction
            else:
                stop_level = total_credit
                logger.info(f"Recovery: MEIC+ not applied - credit ${total_credit:.2f} < ${min_credit_for_meic_plus:.2f}")
            entry.call_side_stop = stop_level
            entry.put_side_stop = stop_level
        else:
            entry.call_side_stop = total_credit
            entry.put_side_stop = total_credit

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
        valid_ids = {str(p.get("PositionId")) for p in all_positions}

        # FIX (2026-02-04): Skip orphan cleanup in dry-run mode
        # Dry-run positions use synthetic IDs (DRY_xxx) that won't exist in Saxo,
        # so cleanup_orphans would incorrectly remove all of them.
        if not self.dry_run:
            try:
                orphans = self.registry.cleanup_orphans(valid_ids)
                if orphans:
                    logger.warning(f"Cleaned up {len(orphans)} orphaned registrations")
                    self._log_safety_event("ORPHAN_CLEANUP", f"Cleaned {len(orphans)} orphans during reconciliation")
            except Exception as e:
                logger.error(f"Registry error during orphan cleanup: {e}")
        else:
            logger.debug("Skipping orphan cleanup in dry-run mode")

        # Check if any of our tracked positions are missing from Saxo
        for entry in self.daily_state.active_entries:
            missing_legs = []

            for leg_name in ["short_call", "long_call", "short_put", "long_put"]:
                pos_id = getattr(entry, f"{leg_name}_position_id")
                if pos_id and pos_id not in valid_ids:
                    missing_legs.append(leg_name)
                    # Unregister the missing position
                    try:
                        self.registry.unregister(pos_id)
                    except Exception as e:
                        logger.error(f"Registry error unregistering {pos_id}: {e}")
                    setattr(entry, f"{leg_name}_position_id", None)
                    # FIX (2026-02-04): Also clear UIC to prevent IllegalInstrumentId errors
                    setattr(entry, f"{leg_name}_uic", None)

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

    # Note: POS-002 position verification is handled by _reconcile_positions()
    # which is called hourly and on state transitions.

    # =========================================================================
    # DAILY RESET
    # =========================================================================

    def _reset_for_new_day(self):
        """Reset state for a new trading day."""
        logger.info("Resetting for new trading day")

        # STATE-004: Check for overnight 0DTE positions (should NEVER happen)
        try:
            my_position_ids = self.registry.get_positions("MEIC")
        except Exception as e:
            logger.error(f"Registry error checking for overnight positions: {e}")
            my_position_ids = set()
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

        # POS-004: Reset settlement reconciliation flag for new day
        self._settlement_reconciliation_complete = False

        self.state = MEICState.IDLE

        # Save clean state to disk
        self._save_state_to_disk()

    # =========================================================================
    # AFTER-HOURS SETTLEMENT RECONCILIATION (POS-004)
    # =========================================================================

    def check_after_hours_settlement(self) -> bool:
        """
        POS-004: Check if 0DTE positions have been settled after market close.

        Called on every heartbeat after market close until all MEIC positions
        are confirmed settled. This handles the fact that Saxo settles 0DTE
        options sometime between 4:00 PM and 7:00 PM EST.

        Returns:
            True if all positions are settled (or were already confirmed settled)
            False if positions still exist on Saxo (settlement pending)
        """
        # Already confirmed settled for today - skip check
        if self._settlement_reconciliation_complete:
            return True

        # Check how many MEIC positions we think we have in registry
        my_position_ids = self.registry.get_positions("MEIC")

        if not my_position_ids:
            # Registry is already empty - mark as complete
            logger.info("POS-004: No MEIC positions in registry - settlement reconciliation complete")
            self._settlement_reconciliation_complete = True
            return True

        # We have positions in registry - check if they still exist on Saxo
        logger.info(f"POS-004: Checking settlement status for {len(my_position_ids)} MEIC positions...")

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
                # FIX (2026-02-04): Clear BOTH position_id AND uic when options settle
                # to prevent IllegalInstrumentId errors from _update_entry_prices
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
                #
                # The fix distinguishes between:
                # - STOPPED: Side was closed during the day due to stop loss (LOSS - already tracked)
                # - EXPIRED: Side expired worthless at settlement (PROFIT - credit is kept)
                expired_call_credit = 0.0
                expired_put_credit = 0.0

                for entry in self.daily_state.entries:
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

                    # Mark complete if both sides done (stopped OR expired)
                    # For one-sided entries (MEIC-TF), check the appropriate side
                    call_only = getattr(entry, 'call_only', False)
                    put_only = getattr(entry, 'put_only', False)

                    if call_only:
                        # Call-only entry - done when call side is stopped or expired
                        if entry.call_side_stopped or entry.call_side_expired:
                            entry.is_complete = True
                    elif put_only:
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
                total_settled = len(settled) if settled else len(my_position_ids)
                logger.info("POS-004: All MEIC positions confirmed settled - reconciliation complete")
                self._settlement_reconciliation_complete = True

                # Log safety event
                self._log_safety_event(
                    "SETTLEMENT_COMPLETE",
                    f"All {total_settled} positions confirmed settled after market close",
                    "Complete"
                )

                return True

        except Exception as e:
            logger.error(f"POS-004: Error checking settlement status: {e}")
            # Don't mark complete on error - try again next heartbeat
            return False

    # =========================================================================
    # LOGGING AND ALERTS
    # =========================================================================

    def _log_entry(self, entry: IronCondorEntry):
        """Log entry to Google Sheets using correct TradeLoggerService API."""
        try:
            # Format strikes as readable string for the strike field
            strike_str = (
                f"C:{entry.short_call_strike}/{entry.long_call_strike} "
                f"P:{entry.short_put_strike}/{entry.long_put_strike}"
            )

            self.trade_logger.log_trade(
                action=f"MEIC Entry #{entry.entry_number}",
                strike=strike_str,
                price=entry.total_credit,
                delta=0.0,  # Iron condors are delta neutral
                pnl=0.0,  # At entry, no P&L yet
                saxo_client=self.client,  # Fix #63: Enable EUR conversion
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type="Iron Condor",
                premium_received=entry.total_credit,
                trade_reason=f"Entry | Call Credit: ${entry.call_spread_credit:.2f} | Put Credit: ${entry.put_spread_credit:.2f}"
            )

            logger.info(
                f"Entry #{entry.entry_number} logged to Sheets: "
                f"SPX={self.current_price:.2f}, Credit=${entry.total_credit:.2f}, "
                f"Strikes: {strike_str}"
            )
        except Exception as e:
            logger.error(f"Failed to log entry: {e}")

    def _log_stop_loss(self, entry: IronCondorEntry, side: str, stop_level: float, realized_loss: float):
        """Log stop loss to Google Sheets."""
        try:
            if side == "call":
                strike_str = f"C:{entry.short_call_strike}/{entry.long_call_strike}"
            else:
                strike_str = f"P:{entry.short_put_strike}/{entry.long_put_strike}"

            self.trade_logger.log_trade(
                action=f"MEIC Stop #{entry.entry_number} ({side.upper()})",
                strike=strike_str,
                price=stop_level,
                delta=0.0,
                pnl=-realized_loss,  # Negative because it's a loss
                saxo_client=self.client,  # Fix #63: Enable EUR conversion
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type=f"IC {side.title()} Spread",
                trade_reason=f"Stop Loss | Level: ${stop_level:.2f}"
            )

            logger.info(
                f"Stop logged to Sheets: Entry #{entry.entry_number} {side} side, "
                f"Loss: ${realized_loss:.2f}"
            )
        except Exception as e:
            logger.error(f"Failed to log stop loss: {e}")

    def _log_safety_event(self, event_type: str, details: str, result: str = "Acknowledged"):
        """
        Log safety events to Google Sheets for audit trail.

        This matches Delta Neutral's safety event logging for consistency
        and provides an auditable record of all safety-related incidents.

        Args:
            event_type: Type of safety event (e.g., "CIRCUIT_BREAKER_OPEN", "NAKED_SHORT_DETECTED")
            details: Human-readable description of the event
            result: Outcome of the event (default: "Acknowledged")
        """
        try:
            self.trade_logger.log_safety_event({
                "timestamp": get_us_market_time().strftime("%Y-%m-%d %H:%M:%S"),
                "event_type": event_type,
                "bot": "MEIC",
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
        # FIX (2026-02-03): Use Saxo's authoritative P&L instead of mid-price calc
        unrealized = self._get_total_saxo_pnl()
        total_pnl = self.daily_state.total_realized_pnl + unrealized

        # PNL-001: Sanity check the P&L values
        self._check_pnl_sanity(total_pnl, "daily_total")

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
            "circuit_breaker_opens": self.daily_state.circuit_breaker_opens,
            "total_commission": self.daily_state.total_commission,
            "net_pnl": total_pnl - self.daily_state.total_commission  # P&L after commission
        }

    def get_status_summary(self) -> Dict:
        """Get current status summary for heartbeat logging."""
        active_entries = len(self.daily_state.active_entries)
        # FIX (2026-02-03): Use Saxo's authoritative P&L instead of mid-price calc
        unrealized = self._get_total_saxo_pnl()

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
            "circuit_breaker_open": self._circuit_breaker_open,
            "total_commission": self.daily_state.total_commission  # For net P&L display
        }

    def get_detailed_position_status(self) -> List[str]:
        """
        Get detailed status lines for each active position.

        Returns a list of formatted strings, one per active entry, showing:
        - Entry number
        - Strikes (call spread / put spread)
        - Credit received
        - Live P&L (from Saxo's ProfitLossOnTrade)
        - Distance to stop levels
        - Stop status

        Similar to Delta Neutral's detailed position logging.
        """
        lines = []

        if not self.daily_state.active_entries:
            return lines

        # Fetch positions once for all entries (avoid N API calls)
        try:
            positions = self.client.get_positions()
        except Exception:
            positions = []

        for entry in self.daily_state.active_entries:
            # FIX (2026-02-03): Use Saxo's authoritative P&L for display
            total_pnl = self._get_saxo_pnl_for_entry(entry, positions=positions)

            # FIX (2026-02-04): Use spread_value (cost to close) for cushion calculation
            # This matches the stop logic which triggers when spread_value >= stop_level
            # Previously used P&L with abs() which gave wrong cushion when profitable
            call_value = entry.call_spread_value if not entry.call_side_stopped else 0
            put_value = entry.put_spread_value if not entry.put_side_stopped else 0

            # Calculate distance to stop levels (as percentage of stop)
            # Cushion = (stop_level - current_value) / stop_level * 100
            # When value=0 (options worthless): cushion = 100%
            # When value=stop_level: cushion = 0% (stop triggered)
            call_dist = entry.call_side_stop - call_value if not entry.call_side_stopped else 0
            put_dist = entry.put_side_stop - put_value if not entry.put_side_stopped else 0
            call_pct = (call_dist / entry.call_side_stop * 100) if entry.call_side_stop > 0 else 0
            put_pct = (put_dist / entry.put_side_stop * 100) if entry.put_side_stop > 0 else 0

            # Status indicators
            # Fix #49: Show SKIPPED for one-sided entries that never opened a side
            if getattr(entry, 'call_side_skipped', False):
                call_status = "SKIPPED"
            elif entry.call_side_stopped:
                call_status = "STOPPED"
            else:
                call_status = f"{call_pct:.0f}% cushion"

            if getattr(entry, 'put_side_skipped', False):
                put_status = "SKIPPED"
            elif entry.put_side_stopped:
                put_status = "STOPPED"
            else:
                put_status = f"{put_pct:.0f}% cushion"

            # Warn if close to stop (only for active sides, not skipped/stopped)
            call_warning = "⚠️" if call_status.endswith("cushion") and call_pct < 30 else ""
            put_warning = "⚠️" if put_status.endswith("cushion") and put_pct < 30 else ""

            line = (
                f"  Entry #{entry.entry_number}: "
                f"C:{entry.short_call_strike}/{entry.long_call_strike} "
                f"P:{entry.short_put_strike}/{entry.long_put_strike} | "
                f"Credit: ${entry.total_credit:.0f} | "
                f"P&L: ${total_pnl:+.0f} | "
                f"Call: {call_status}{call_warning} | "
                f"Put: {put_status}{put_warning}"
            )
            lines.append(line)

        return lines

    def _calculate_side_pnl(self, entry: IronCondorEntry, side: str) -> float:
        """
        Calculate P&L for one side (call or put) of an iron condor.

        Args:
            entry: The IronCondorEntry
            side: "call" or "put"

        Returns:
            P&L in dollars (positive = profit, negative = loss)
        """
        try:
            if side == "call":
                if entry.call_side_stopped:
                    # FIX (2026-02-04): Return NET loss, not gross stop_level
                    # Net loss = stop_level - credit_received
                    return -(entry.call_side_stop - entry.call_spread_credit)
                short_uic = entry.short_call_uic
                long_uic = entry.long_call_uic
                credit = entry.call_spread_credit
            else:
                if entry.put_side_stopped:
                    # FIX (2026-02-04): Return NET loss, not gross stop_level
                    return -(entry.put_side_stop - entry.put_spread_credit)
                short_uic = entry.short_put_uic
                long_uic = entry.long_put_uic
                credit = entry.put_spread_credit

            # Get current prices
            short_price = self._get_option_price(short_uic)
            long_price = self._get_option_price(long_uic)

            if short_price is None or long_price is None:
                return 0.0

            # Current cost to close = buy back short - sell long
            current_cost = (short_price - long_price) * 100  # Convert to dollars

            # P&L = credit - current cost to close
            return credit - current_cost

        except Exception as e:
            logger.debug(f"Error calculating {side} P&L for Entry #{entry.entry_number}: {e}")
            return 0.0

    def _get_option_price(self, uic: int) -> Optional[float]:
        """Get mid price for an option UIC."""
        try:
            # FIX (2026-02-03): Must specify asset_type for SPX options
            quote = self.client.get_quote(uic, asset_type="StockIndexOption")
            if quote:
                # FIX (2026-02-03): Bid/Ask are inside Quote nested object, not at top level
                quote_data = quote.get("Quote", {})
                bid = quote_data.get("Bid", 0) or 0
                ask = quote_data.get("Ask", 0) or 0
                if bid > 0 and ask > 0:
                    return (bid + ask) / 2
                return ask or bid
        except Exception:
            pass
        return None

    def _get_saxo_pnl_for_entry(self, entry: IronCondorEntry, positions: Optional[List] = None) -> float:
        """
        Get the actual P&L for an entry directly from Saxo positions.

        This uses Saxo's ProfitLossOnTrade field which is the authoritative
        P&L calculation (accounts for actual fill prices and current market).

        FIX (2026-02-03): Previously used mid-price calculations which were
        systematically optimistic. Saxo uses bid for sells and ask for buys.

        Args:
            entry: The IronCondorEntry to get P&L for
            positions: Optional pre-fetched positions list (to avoid multiple API calls)

        Returns:
            Total P&L in dollars (positive = profit, negative = loss)
        """
        try:
            # Get positions from Saxo (or use provided list)
            if positions is None:
                positions = self.client.get_positions()

            total_pnl = 0.0

            # Map position IDs to their P&L
            position_ids = [
                entry.short_call_position_id,
                entry.long_call_position_id,
                entry.short_put_position_id,
                entry.long_put_position_id,
            ]

            for pos in positions:
                pos_id = str(pos.get("PositionId", ""))
                if pos_id in position_ids:
                    # Get Saxo's P&L calculation
                    pos_view = pos.get("PositionView", {})
                    pnl = pos_view.get("ProfitLossOnTrade", 0) or 0
                    total_pnl += pnl

            return total_pnl

        except Exception as e:
            logger.debug(f"Error getting Saxo P&L for Entry #{entry.entry_number}: {e}")
            # Fall back to mid-price calculation
            return entry.unrealized_pnl

    def _get_total_saxo_pnl(self) -> float:
        """
        Get total unrealized P&L for all active entries from Saxo.

        FIX (2026-02-03): Use Saxo's authoritative P&L instead of mid-price calc.

        Returns:
            Total unrealized P&L in dollars
        """
        try:
            # Fetch positions once and reuse for all entries
            positions = self.client.get_positions()
            total = 0.0
            for entry in self.daily_state.active_entries:
                total += self._get_saxo_pnl_for_entry(entry, positions=positions)
            return total
        except Exception as e:
            logger.debug(f"Error getting total Saxo P&L: {e}")
            # Fall back to mid-price calculation
            return sum(e.unrealized_pnl for e in self.daily_state.active_entries)

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
        """Log MEIC-specific account summary to Google Sheets dashboard."""
        try:
            # Use MEIC-specific metrics that match the worksheet columns
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
                # Risk
                "daily_loss_percent": metrics["pnl_percent"],
                "circuit_breaker": metrics["circuit_breaker_open"],
                # State
                "state": metrics["state"]
            })
        except Exception as e:
            logger.error(f"Failed to log account summary: {e}")

    def log_performance_metrics(self):
        """Log MEIC-specific performance metrics to Google Sheets."""
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
                    # Stop stats
                    "call_stops": metrics["call_stops"],
                    "put_stops": metrics["put_stops"],
                    "double_stops": metrics["double_stops"],
                    # Outcome rates
                    "win_rate": win_rate,
                    "breakeven_rate": breakeven_rate,
                    "loss_rate": loss_rate,
                    # Risk
                    "max_drawdown": cumulative.get("max_drawdown", 0),
                    "max_drawdown_pct": cumulative.get("max_drawdown_pct", 0),
                    "avg_daily_pnl": cumulative.get("avg_daily_pnl", 0)
                },
                saxo_client=self.client
            )
        except Exception as e:
            logger.error(f"Failed to log performance metrics: {e}")

    def log_daily_summary(self):
        """
        Log and send daily summary after settlement is confirmed.

        Called from main.py AFTER check_after_hours_settlement() returns True,
        ensuring that all 0DTE positions have been settled by Saxo and we have
        accurate final P&L figures.

        This method:
        1. Sends WhatsApp/Email alert via alert_service
        2. Logs to Google Sheets Daily Summary tab
        3. Updates cumulative metrics (winning/losing days, total P&L)
        4. Saves cumulative metrics to disk
        """
        # Get summary data
        summary = self.get_daily_summary()

        # Send alert (WhatsApp/Email)
        self._send_daily_summary()

        # Log to Google Sheets Daily Summary tab
        # Add extra fields needed by the logger
        sheets_summary = {
            **summary,
            "spx_close": self.current_price,
            "vix_close": self.current_vix,
            "daily_pnl": summary["total_pnl"],
            "daily_pnl_net": summary.get("net_pnl", summary["total_pnl"]),  # P&L after commission
            "total_commission": summary.get("total_commission", 0),
            "cumulative_pnl": self.cumulative_metrics.get("cumulative_pnl", 0) + summary["total_pnl"],
            "notes": "Post-settlement" if self._settlement_reconciliation_complete else ""
        }

        # Convert daily P&L to EUR if exchange rate available
        try:
            rate = self.client.get_fx_rate("USD", "EUR")
            if rate:
                sheets_summary["daily_pnl_eur"] = summary["total_pnl"] * rate
        except Exception:
            sheets_summary["daily_pnl_eur"] = 0

        if self.trade_logger:
            self.trade_logger.log_daily_summary(sheets_summary)
            logger.info(f"Daily summary logged to Google Sheets (P&L: ${summary['total_pnl']:.2f})")

        # Update cumulative metrics
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
                "total_commission": self.daily_state.total_commission,  # Commission tracking
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
                    # Fix #52: Contract count for multi-contract support
                    "contracts": entry.contracts,
                }
                state_data["entries"].append(entry_data)

            state_data["last_saved"] = get_us_market_time().isoformat()

            # Write atomically using temp file
            temp_file = self.state_file + ".tmp"
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)

            with open(temp_file, 'w') as f:
                json.dump(state_data, f, indent=2)

            os.replace(temp_file, self.state_file)
            logger.debug(f"State saved to {self.state_file}")

        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    # =========================================================================
    # WEBSOCKET PRICE CACHE (PRESERVED - NOT USED IN REST-ONLY MODE)
    # =========================================================================
    # These methods are preserved for potential future use if we need to enable
    # WebSocket streaming due to API rate limits with many bots.
    # Currently disabled: main.py has USE_WEBSOCKET_STREAMING = False
    # and _update_entry_prices() fetches from REST API directly.

    def update_ws_price_cache(self, uic: int, price: float):
        """
        Update WebSocket price cache for fast stop monitoring.

        Called by main.py's price callback when WebSocket mode is enabled.
        Currently not used in REST-only mode.
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

    # Note: Intraday stats (SPX high/low, VIX average) are tracked in MarketData
    # and can be accessed via market_data.get_spx_range(), market_data.get_vix_average()
    # when needed for future dashboard features.

    def _entry_is_win(self, entry: IronCondorEntry) -> bool:
        """
        Fix #58/#61: Determine if entry is a WIN (no stops, or merged with credit preserved).

        A win means:
        - Neither side was stopped (both expired worthless = kept credit), OR
        - Side(s) were merged with another entry (credit preserved)
        - Skipped sides don't count against win (MEIC-TF one-sided entries)

        Args:
            entry: IronCondorEntry to evaluate

        Returns:
            True if this entry is a win
        """
        # For one-sided entries (MEIC-TF), only check the side that was opened
        call_only = getattr(entry, 'call_only', False)
        put_only = getattr(entry, 'put_only', False)

        if call_only:
            # Call-only entry - win if call wasn't stopped (merged = win)
            return not entry.call_side_stopped
        elif put_only:
            # Put-only entry - win if put wasn't stopped (merged = win)
            return not entry.put_side_stopped
        else:
            # Full IC - win if neither side was stopped (merged = win)
            # Merged sides count as wins since credit is preserved
            return not entry.call_side_stopped and not entry.put_side_stopped

    def _entry_is_breakeven(self, entry: IronCondorEntry) -> bool:
        """
        Fix #58/#61: Determine if entry is BREAKEVEN (exactly one side stopped).

        Args:
            entry: IronCondorEntry to evaluate

        Returns:
            True if this entry is breakeven
        """
        # For one-sided entries (MEIC-TF), can't be breakeven (either win or loss)
        call_only = getattr(entry, 'call_only', False)
        put_only = getattr(entry, 'put_only', False)

        if call_only or put_only:
            return False  # One-sided entries are either win or loss, not breakeven

        # Full IC - breakeven if exactly one side stopped
        # Merged sides don't count as stopped
        return entry.call_side_stopped != entry.put_side_stopped

    def _entry_is_loss(self, entry: IronCondorEntry) -> bool:
        """
        Fix #58/#61: Determine if entry is a LOSS (both sides stopped for full IC).

        Args:
            entry: IronCondorEntry to evaluate

        Returns:
            True if this entry is a loss
        """
        # For one-sided entries (MEIC-TF), loss if the placed side was stopped
        call_only = getattr(entry, 'call_only', False)
        put_only = getattr(entry, 'put_only', False)

        if call_only:
            return entry.call_side_stopped
        elif put_only:
            return entry.put_side_stopped
        else:
            # Full IC - loss if BOTH sides stopped
            return entry.call_side_stopped and entry.put_side_stopped

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
        # Fetch positions once and reuse (avoid multiple API calls)
        try:
            positions = self.client.get_positions()
        except Exception:
            positions = []

        # Basic status
        active_entries = len(self.daily_state.active_entries)
        # FIX (2026-02-03): Use Saxo's authoritative P&L (pass positions to avoid re-fetch)
        unrealized = 0.0
        for entry in self.daily_state.active_entries:
            unrealized += self._get_saxo_pnl_for_entry(entry, positions=positions)
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

        # Per-entry details (use Saxo P&L for each entry, reuse positions)
        entry_details = []
        for entry in self.daily_state.entries:
            # Get Saxo P&L if entry has any positions (pass positions to avoid re-fetch)
            # FIX (2026-02-03): Calculate P&L for partial entries too, not just complete ones
            has_any_positions = any([
                entry.short_call_position_id,
                entry.long_call_position_id,
                entry.short_put_position_id,
                entry.long_put_position_id
            ])
            entry_pnl = self._get_saxo_pnl_for_entry(entry, positions=positions) if has_any_positions else 0
            entry_details.append({
                "entry_number": entry.entry_number,
                "entry_time": entry.entry_time.strftime("%H:%M") if hasattr(entry.entry_time, 'strftime') else (entry.entry_time or ""),
                "short_call": entry.short_call_strike,
                "long_call": entry.long_call_strike,
                "short_put": entry.short_put_strike,
                "long_put": entry.long_put_strike,
                "call_credit": entry.call_spread_credit,
                "put_credit": entry.put_spread_credit,
                "total_credit": entry.total_credit,
                "call_stopped": entry.call_side_stopped,
                "put_stopped": entry.put_side_stopped,
                "unrealized_pnl": entry_pnl,
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

            # Win/loss metrics - count entries by stop status
            # FIX (2026-02-03): Include partial entries (is_complete was excluding stopped entries)
            # Fix #58/#61: Merged entries count as wins (credit preserved), not stops
            "entries_with_no_stops": sum(1 for e in self.daily_state.entries if self._entry_is_win(e)),
            "entries_with_one_stop": sum(1 for e in self.daily_state.entries if self._entry_is_breakeven(e)),
            "entries_with_both_stops": sum(1 for e in self.daily_state.entries if self._entry_is_loss(e)),

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

    def _adjust_long_wing_for_liquidity(
        self,
        long_strike: float,
        short_strike: float,
        put_call: str,
        expiry: str,
        is_call: bool
    ) -> Tuple[float, bool]:
        """
        MKT-008: Adjust long wing strike if illiquid by reducing spread width.

        When the long wing (hedge) is illiquid (100%+ spread), try strikes
        closer to the short strike until we find liquidity or hit min_spread_width.

        Args:
            long_strike: Original long wing strike
            short_strike: Short strike (anchor)
            put_call: "Call" or "Put"
            expiry: Expiry date string
            is_call: True for call side, False for put side

        Returns:
            Tuple of (adjusted_long_strike, was_adjusted)
        """
        original_long_strike = long_strike
        min_spread_width = self.strategy_config.get("min_spread_width", 25)

        # Calculate adjustment direction (toward short strike)
        # For calls: long is above short, so decrease to get closer
        # For puts: long is below short, so increase to get closer
        adjustment_direction = -1 if is_call else 1

        # Max attempts = (current_spread_width - min_spread_width) / 5
        current_spread = abs(long_strike - short_strike)
        max_attempts = int((current_spread - min_spread_width) / ILLIQUIDITY_STRIKE_ADJUSTMENT_POINTS)
        max_attempts = max(0, min(6, max_attempts))  # Cap at 6 attempts

        for attempt in range(max_attempts + 1):
            # Calculate current spread width
            current_spread = abs(long_strike - short_strike)
            if current_spread < min_spread_width:
                logger.warning(
                    f"MKT-008: Cannot reduce spread further - "
                    f"at min_spread_width {min_spread_width}"
                )
                break

            # Get option UIC
            uic = self._get_option_uic(long_strike, put_call, expiry)
            if not uic:
                # Strike doesn't exist - try closer
                long_strike += adjustment_direction * ILLIQUIDITY_STRIKE_ADJUSTMENT_POINTS
                continue

            # Check quote for liquidity
            quote = self.client.get_quote(uic, asset_type="StockIndexOption")
            if not quote or "Quote" not in quote:
                long_strike += adjustment_direction * ILLIQUIDITY_STRIKE_ADJUSTMENT_POINTS
                continue

            bid = quote["Quote"].get("Bid") or 0
            ask = quote["Quote"].get("Ask") or 0

            # Check if liquid
            if bid > 0 and ask > 0:
                spread_percent = ((ask - bid) / bid) * 100 if bid > 0 else float('inf')
                if spread_percent < MAX_BID_ASK_SPREAD_PERCENT_SKIP:
                    # Found liquid strike
                    if long_strike != original_long_strike:
                        original_spread = abs(original_long_strike - short_strike)
                        new_spread = abs(long_strike - short_strike)
                        logger.info(
                            f"MKT-008: Adjusted long {put_call} {original_long_strike} -> {long_strike} "
                            f"(spread width {original_spread:.0f} -> {new_spread:.0f} pts, "
                            f"bid=${bid:.2f}, ask=${ask:.2f}, {spread_percent:.1f}%)"
                        )
                        return long_strike, True
                    return long_strike, False
                else:
                    logger.info(
                        f"MKT-008: Long {put_call} {long_strike} illiquid "
                        f"(bid=${bid:.2f}, ask=${ask:.2f}, {spread_percent:.1f}%), trying closer"
                    )

            # Illiquid - try closer to short strike
            if attempt < max_attempts:
                long_strike += adjustment_direction * ILLIQUIDITY_STRIKE_ADJUSTMENT_POINTS

        # Could not find liquid long wing - return original
        # MKT-010: Still mark as illiquid so MEIC-TF can use one-sided entry
        # The wing IS illiquid even though we're using the original strike
        logger.warning(
            f"MKT-008: Could not find liquid long {put_call} near {original_long_strike}, "
            f"using original (may fail during order placement)"
        )
        return original_long_strike, True  # True = wing is illiquid (for MKT-010)

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
            # Use account info endpoint as a proxy to verify API connectivity
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
    # CONFIG-001: CONFIGURATION VALIDATION
    # =========================================================================

    def _validate_config(self) -> Tuple[bool, List[str]]:
        """
        CONFIG-001: Validate configuration on startup.

        Checks for required fields and sensible values to catch config
        errors early rather than during trading.

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors = []
        warnings = []

        # Required top-level sections
        required_sections = ["saxo_api", "strategy"]
        for section in required_sections:
            if section not in self.config:
                errors.append(f"Missing required config section: {section}")

        # Saxo API config validation
        # Config structure: saxo_api.{environment}.app_key where environment is "live" or "sim"
        saxo_config = self.config.get("saxo_api", {})
        environment = saxo_config.get("environment", "sim")  # Default to sim for safety
        env_config = saxo_config.get(environment, {})
        if not env_config.get("app_key"):
            errors.append(f"Missing saxo_api.{environment}.app_key")
        if not env_config.get("app_secret"):
            errors.append(f"Missing saxo_api.{environment}.app_secret")

        # Strategy config validation
        strategy = self.config.get("strategy", {})

        # UIC validation (must be positive integers)
        uic_fields = ["underlying_uic", "option_root_uic", "vix_spot_uic"]
        for field in uic_fields:
            uic = strategy.get(field)
            if uic is not None and (not isinstance(uic, int) or uic <= 0):
                errors.append(f"Invalid {field}: must be a positive integer")

        # Spread width validation
        spread_width = strategy.get("spread_width", 50)
        if not (10 <= spread_width <= 100):
            warnings.append(f"Unusual spread_width: {spread_width} (expected 10-100)")

        # Delta validation
        min_delta = strategy.get("min_delta", 5)
        max_delta = strategy.get("max_delta", 15)
        target_delta = strategy.get("target_delta", 8)
        if not (1 <= min_delta <= max_delta <= 50):
            errors.append(f"Invalid delta range: min={min_delta}, max={max_delta}")
        if not (min_delta <= target_delta <= max_delta):
            warnings.append(f"target_delta ({target_delta}) outside min/max range")

        # Contracts validation
        contracts = strategy.get("contracts_per_entry", 1)
        if contracts < 1 or contracts > MAX_CONTRACTS_PER_ORDER:
            errors.append(f"Invalid contracts_per_entry: {contracts} (must be 1-{MAX_CONTRACTS_PER_ORDER})")

        # Credit validation
        min_credit = strategy.get("min_credit_per_side", 1.00)
        max_credit = strategy.get("max_credit_per_side", 1.75)
        if min_credit <= 0 or max_credit <= 0:
            errors.append("Credit values must be positive")
        if min_credit > max_credit:
            errors.append(f"min_credit ({min_credit}) > max_credit ({max_credit})")

        # VIX threshold validation
        max_vix = strategy.get("max_vix_entry", 25)
        if max_vix < 10 or max_vix > 50:
            warnings.append(f"Unusual max_vix_entry: {max_vix} (expected 10-50)")

        # Log results
        if errors:
            for error in errors:
                logger.error(f"CONFIG-001 ERROR: {error}")
        if warnings:
            for warning in warnings:
                logger.warning(f"CONFIG-001 WARNING: {warning}")

        is_valid = len(errors) == 0

        if is_valid:
            logger.info("CONFIG-001: Configuration validation passed")
        else:
            logger.critical(f"CONFIG-001: Configuration validation FAILED with {len(errors)} error(s)")

        return is_valid, errors

    # =========================================================================
    # DATA-003: P&L SANITY CHECK
    # =========================================================================

    def _validate_pnl_sanity(self, entry: IronCondorEntry) -> Tuple[bool, str]:
        """
        DATA-003/DATA-004: Validate that P&L values are within reasonable bounds
        and that option prices are valid (not zero).

        Catches data errors that could result in impossible P&L figures or
        false stop triggers from invalid price data.

        Args:
            entry: IronCondorEntry to validate

        Returns:
            Tuple of (is_valid, message)
        """
        if not PNL_SANITY_CHECK_ENABLED:
            return True, "P&L sanity check disabled"

        # DATA-004: Check for zero/invalid option prices FIRST
        # This prevents false stops when API returns $0.00 for option prices
        # FIX (2026-02-03): Added per user request to match Delta Neutral's DATA-004 pattern
        if not entry.call_side_stopped:
            if entry.short_call_price == 0 and entry.long_call_price == 0:
                logger.warning(
                    f"DATA-004: Entry #{entry.entry_number} call side has zero prices "
                    f"(SC=${entry.short_call_price:.2f}, LC=${entry.long_call_price:.2f}) - skipping stop check"
                )
                return False, "Call side prices are zero"
            # If only one leg is zero, that's suspicious too
            if entry.short_call_price == 0 or entry.long_call_price == 0:
                logger.warning(
                    f"DATA-004: Entry #{entry.entry_number} call side has partial zero prices "
                    f"(SC=${entry.short_call_price:.2f}, LC=${entry.long_call_price:.2f}) - skipping stop check"
                )
                return False, "Call side has partial zero price"

        if not entry.put_side_stopped:
            if entry.short_put_price == 0 and entry.long_put_price == 0:
                logger.warning(
                    f"DATA-004: Entry #{entry.entry_number} put side has zero prices "
                    f"(SP=${entry.short_put_price:.2f}, LP=${entry.long_put_price:.2f}) - skipping stop check"
                )
                return False, "Put side prices are zero"
            # If only one leg is zero, that's suspicious too
            if entry.short_put_price == 0 or entry.long_put_price == 0:
                logger.warning(
                    f"DATA-004: Entry #{entry.entry_number} put side has partial zero prices "
                    f"(SP=${entry.short_put_price:.2f}, LP=${entry.long_put_price:.2f}) - skipping stop check"
                )
                return False, "Put side has partial zero price"

        pnl = entry.unrealized_pnl

        # DATA-003: Check for impossible P&L values
        # Max profit = 100% of credit received (all options expire worthless)
        # Use entry's actual credit, not fixed constant, because credits vary
        max_profit = entry.total_credit if entry.total_credit > 0 else MAX_PNL_PER_IC
        # Add 20% buffer for floating point and timing differences
        max_profit_with_buffer = max_profit * 1.2

        if pnl > max_profit_with_buffer:
            logger.error(
                f"DATA-003: Impossible P&L detected for Entry #{entry.entry_number}: "
                f"${pnl:.2f} > max ${max_profit_with_buffer:.2f} (credit=${entry.total_credit:.2f})"
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

    def _check_pnl_sanity(self, total_pnl: float, context: str = "total") -> bool:
        """
        PNL-001: Check if total daily P&L is within realistic bounds.

        Called when calculating daily summary to catch calculation errors
        or data issues that result in impossible P&L figures.

        Args:
            total_pnl: The total P&L value to check
            context: Context description for logging (e.g., "daily_total")

        Returns:
            True if P&L is within bounds, False otherwise
        """
        if not PNL_SANITY_CHECK_ENABLED:
            return True

        # Calculate max possible P&L based on entries completed
        # Each IC can make at most MAX_PNL_PER_IC and lose at most MIN_PNL_PER_IC
        # Fix #52: Multiply by contracts_per_entry for multi-contract support
        num_entries = max(1, self.daily_state.entries_completed)
        max_possible = MAX_PNL_PER_IC * num_entries * self.contracts_per_entry
        min_possible = MIN_PNL_PER_IC * num_entries * self.contracts_per_entry

        # Check for NaN or infinity
        import math
        if math.isnan(total_pnl) or math.isinf(total_pnl):
            logger.critical(f"PNL-001 CRITICAL: Invalid P&L value detected ({context}): {total_pnl}")
            self.alert_service.send_alert(
                alert_type=AlertType.DATA_QUALITY,
                title="INVALID P&L DETECTED",
                message=f"P&L calculation returned invalid value: {total_pnl}. Check price data and calculations.",
                priority=AlertPriority.HIGH
            )
            self._log_safety_event("PNL_INVALID", f"{context}: {total_pnl}")
            return False

        # Check bounds
        if total_pnl > max_possible:
            logger.warning(
                f"PNL-001 WARNING: Unusually high P&L ({context}): ${total_pnl:.2f} "
                f"exceeds expected max ${max_possible:.2f} for {num_entries} entries"
            )
            self.alert_service.send_alert(
                alert_type=AlertType.DATA_QUALITY,
                title="UNUSUALLY HIGH P&L",
                message=f"Daily P&L ${total_pnl:.2f} exceeds expected maximum ${max_possible:.2f}. Verify trade data.",
                priority=AlertPriority.MEDIUM
            )
            self._log_safety_event("PNL_HIGH", f"{context}: ${total_pnl:.2f} > ${max_possible:.2f}")
            return False

        if total_pnl < min_possible:
            logger.warning(
                f"PNL-001 WARNING: Unusually low P&L ({context}): ${total_pnl:.2f} "
                f"below expected min ${min_possible:.2f} for {num_entries} entries"
            )
            self.alert_service.send_alert(
                alert_type=AlertType.DATA_QUALITY,
                title="UNUSUALLY LOW P&L",
                message=f"Daily P&L ${total_pnl:.2f} below expected minimum ${min_possible:.2f}. Verify trade data.",
                priority=AlertPriority.MEDIUM
            )
            self._log_safety_event("PNL_LOW", f"{context}: ${total_pnl:.2f} < ${min_possible:.2f}")
            return False

        logger.debug(f"PNL-001: P&L sanity check passed ({context}): ${total_pnl:.2f}")
        return True

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

        FIX (2026-02-04): Now receives net_loss instead of stop_level for accurate P&L.

        Args:
            entries_stopped: List of (entry, side, net_loss) tuples
            total_loss: Total NET loss across all stops
        """
        count = len(entries_stopped)

        # Build summary (using net_loss for accurate display)
        details_lines = []
        for entry, side, net_loss in entries_stopped:
            details_lines.append(f"Entry #{entry.entry_number} {side}: -${net_loss:.2f}")

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

    def _queue_stop_alert(self, entry: IronCondorEntry, side: str, stop_level: float, net_loss: float):
        """
        ALERT-002: Queue a stop alert for potential batching.

        Args:
            entry: Entry that was stopped
            side: "call" or "put"
            stop_level: Stop level that was triggered (cost to close)
            net_loss: Actual NET loss (stop_level - credit_for_side)
        """
        if self._should_batch_alert("stop_loss"):
            # Add to batch
            self._batched_alerts.append({
                "entry": entry,
                "side": side,
                "stop_level": stop_level,
                "net_loss": net_loss,  # FIX (2026-02-04): Track net loss for accurate alerts
                "timestamp": get_us_market_time()
            })
            logger.info(f"ALERT-002: Queued stop alert for batching ({len(self._batched_alerts)} pending)")
        else:
            # Send immediately (also flush any pending)
            self._flush_batched_alerts()
            # Send this one
            # FIX (2026-02-04): Use net_loss instead of stop_level for P&L display
            self.alert_service.stop_loss(
                trigger_price=self.current_price,
                pnl=-net_loss,
                details={
                    "description": f"MEIC Entry #{entry.entry_number} {side.upper()} side stopped",
                    "reason": f"{side} spread value reached stop level (${stop_level:.0f})",
                    "entry_number": entry.entry_number,
                    "net_loss": net_loss
                }
            )

    def _flush_batched_alerts(self):
        """
        ALERT-002: Send any batched alerts.

        Called periodically or when batch window expires.
        """
        if not self._batched_alerts:
            return

        # FIX (2026-02-04): Calculate total using NET loss, not stop_level
        total_loss = sum(a.get("net_loss", a["stop_level"]) for a in self._batched_alerts)

        # Build entries list with net_loss
        entries_stopped = [
            (a["entry"], a["side"], a.get("net_loss", a["stop_level"]))
            for a in self._batched_alerts
        ]

        # Send batched alert
        self._send_batched_stop_alert(entries_stopped, total_loss)

        # Clear batch
        self._batched_alerts.clear()
