"""
base_strategy.py - HYDRA's base strategy class (HYDRA-owned).

⚠️ MIGRATION NOTE (P1 of HYDRA_STANDALONE_COMPLETION_PLAN.md, 2026-05-21):
This file is a copy of the former `bots/meic/strategy.py`, relocated
into the HYDRA package so `HydraStrategy` no longer depends on
`bots/meic/`. It is HYDRA-OWNED now — `bots/meic/` is a retired bot
slated for deletion. The class is still named `MEICStrategy` during the
transition; it is trimmed (P2: dead methods removed) and de-Saxo'd
(P3/F6 + P4) by the subsequent completion-plan phases. Do NOT treat
this as shared MEIC code — it serves HYDRA exclusively.

Original module implemented Tammy Chambless's MEIC 0DTE strategy:
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

import abc
import json
import logging
import math
import os
import time
import threading
import uuid
from collections import deque
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, Dict, List, Any, Tuple, Deque, Set
from dataclasses import dataclass, field
from enum import Enum

from shared.ib_client import IBClient
from bots.hydra.order_types import BuySell, OrderType
from bots.hydra.leg import LEG_NAMES, Leg, _new_legset, bind_leg_bridge
from shared.alert_service import AlertService, AlertType, AlertPriority
from shared.market_hours import get_us_market_time, is_market_open, is_early_close_day, get_market_close_time
from shared.event_calendar import is_fomc_meeting_day, is_fomc_announcement_day
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
ENTRY_RETRY_DELAY_SECONDS = 15  # Delay between retries (reduced from 30s with batch quote headroom)
# How long after scheduled time to attempt entry. Widened 5→8 (2026-06-08
# forensic finding D): a legitimate 4-leg IC at 7-10 contracts, with the
# fill-the-remainder loop completing partials + the size-scaled fill timeout,
# can take ~3-4min — the old 5-min window let an entry that needed a retry hit
# "window expired" mid-build. 8min stays well clear of the 30-min entry spacing
# (no slot overlap) and a 0DTE entry a few min late is acceptable. Pairs with
# the MKT-043 calm-wait cap (CALM_PLACEMENT_HEADROOM_SEC) so the calm delay
# can't consume the window.
ENTRY_WINDOW_MINUTES = 8

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
# Fix #83: Far-OTM wings (bid <= $0.25) always have wide % spreads but tiny $ impact.
# A $0.05 bid / $0.10 ask = 100% spread, but only $5 absolute slippage per contract.
# Skip percentage check for cheap options — ORDER-005 absolute check still protects.
ORDER_006_CHEAP_OPTION_THRESHOLD = 0.25

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
ORDER_RETRY_DELAY_SECONDS = 1.2  # Reduced from 2.0s with batch quote headroom

# CONN-005: Position verification
POSITION_VERIFY_DELAY_SECONDS = 0.5  # Wait before verifying position exists

# MKT-006: VIX filter
DEFAULT_MAX_VIX_ENTRY = 25  # Skip remaining entries if VIX >= this

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
# RATE LIMIT UPDATE (2026-02-19): Batch quote API reduces monitoring to ~2-3 calls/cycle
# (was 26 calls/cycle with individual quotes). Safe to use faster intervals.
# Vigilant at 2s = ~90 calls/min. Normal at 5s = ~36 calls/min. Limit: 120/min.
VIGILANT_CHECK_INTERVAL_SECONDS = 2  # Near stops: 2s detection (was 5s pre-batch)
NORMAL_CHECK_INTERVAL_SECONDS = 5    # Far from stops: 5s detection (was 10s pre-batch)

# ORDER-004: Pre-entry margin check
MIN_BUYING_POWER_PER_IC = 5000  # Minimum BP required per iron condor ($5000)
MARGIN_CHECK_ENABLED = True  # Can be disabled if the broker margin API is unavailable

# MKT-005: Market circuit breaker halt detection
MARKET_HALT_CHECK_ENABLED = True  # Check for trading halts before entry

# MKT-007: Strike adjustment for illiquidity
# MKT-007/008 illiquidity strike step now uses self.strike_increment
# (was a hardcoded 5pt constant — modularity-audit item 2 completion).
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
EMERGENCY_CLOSE_RETRY_DELAY_SECONDS = 2  # Reduced from 3s with batch quote headroom

# CLOSE-LMT (2026-06-08 research): IBKR routes an OPTION "market" order as a
# series of CAPPED marketable-limit orders with a documented non-fill
# possibility, and Market-with-Protection is unavailable for OPT via the Client
# Portal API. So the close-retry path CROSSES the spread with a MARKETABLE
# LIMIT (a BUY-to-close ≥ ask, a SELL ≤ bid fills against the resting touch),
# walking it a little wider each attempt; a plain MARKET is only the no-quote
# fallback. There is NO IBKR equivalent of Saxo's "market-only after 15:30 ET".
#
# CLOSE-CROSS FIX (2026-06-12): the old cross (STEP $0.50, CAP $3.00) was an
# ABSOLUTE pay-up — fine for a $5 option but absurd for a $0.45 0DTE wing, where
# it bid ask+$3 ≈ 6× the option's value. IBKR HARD-REJECTS a limit that far from
# the NBBO ("Limit price too far outside of NBBO" / "at or more aggressive") and
# also raises an unmapped "Number of ticks constraint of 20" prompt, so EVERY
# close attempt failed and the position rode to settlement (variant-C E#2
# take-profit on 2026-06-12 retried 84× and never closed). A BUY at the ask is
# ALREADY marketable; we only need a few ticks of headroom for a moving touch.
# Cross is now small + tick-scaled so the limit stays inside IBKR's NBBO
# tolerance. (The remaining precaution is also answered in ib_client.)
CLOSE_LIMIT_CROSS_STEP = 0.05   # $ crossed THROUGH the touch per attempt (1 SPX tick)
CLOSE_LIMIT_CROSS_CAP = 0.25    # max cross-through (≈5 ticks) — stays inside IBKR's NBBO cap

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
    WAITING_FIRST_ENTRY -> DAILY_COMPLETE (all entries used, no active positions - e.g. post-restart)
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


@bind_leg_bridge
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
    - **Skipped**: Side was never opened (HYDRA one-sided entry, NO P&L IMPACT)

    The flags are mutually exclusive - a side can only be in one state at a time.
    """
    entry_number: int  # 1-6
    entry_time: Optional[datetime] = None

    # Legs — the entry is a set of first-class Leg objects keyed by canonical
    # name (short_call/long_call/short_put/long_put). The flat
    # {leg}_{strike,position_id,uic,price,fill_price,mid_at_fill} attributes
    # are backward-compat @property bridges installed by @bind_leg_bridge; they
    # read/write legs[...]. See docs/PR_SCOPE_LEG_INSTRUMENT.md.
    legs: Dict[str, Leg] = field(default_factory=_new_legset)

    # Strikes — bridge properties over legs[*].strike
    # Position IDs (Saxo-era; None on IBKR) — bridge properties over legs[*].position_id
    # UICs (= conid on IBKR; the F4 reconcile key) — bridge properties over legs[*].uic

    # Credits received
    call_spread_credit: float = 0.0  # Credit from selling call spread
    put_spread_credit: float = 0.0   # Credit from selling put spread

    # Stop levels (calculated after entry)
    call_side_stop: float = 0.0  # Stop loss for call spread
    put_side_stop: float = 0.0   # Stop loss for put spread

    # Actual cost-to-close from market order (dollar amount, 0 = not stopped or not yet known)
    # Set by _apply_stop_loss_meic (immediate fill) or _spawn_async_fill_correction (deferred)
    actual_call_stop_debit: float = 0.0
    actual_put_stop_debit: float = 0.0

    # Real realized NET P&L for THIS entry (account dollars, contract-scaled).
    # Accumulated incrementally as each side closes via ANY path (credit+buffer
    # stop, Brandon TP/breach, MKT-025, salvage, worthless/ITM expiry) by
    # MEICStrategy._book_realized_pnl, which mirrors the EXACT amount booked to
    # the day aggregate daily_state.total_realized_pnl — so per-entry sums equal
    # the day total BY CONSTRUCTION (a settlement reconciliation guard logs any
    # drift). Persisted to the state file + booked to trade_entries.realized_pnl
    # at settlement. This is the per-entry P&L slot_edge.py needs; before this the
    # Brandon variants recorded real per-entry P&L nowhere (only the daily total).
    # GROSS of commission (matches the aggregate; commission accrues separately in
    # total_commission). Brandon defensive-overlay hedge P&L IS folded in as of
    # 2026-07-02 (_brandon_settle_hedges books each hedge's gross P&L here + to the
    # aggregate via _book_realized_pnl, once, guarded by overlay_pnl_booked). Overlay
    # COMMISSION is still not tracked separately (a minor pre-existing gap).
    realized_pnl: float = 0.0

    # Idempotency guard for the overlay-hedge P&L booking. Set True the first time
    # _brandon_settle_hedges books this entry's overlay P&L; persisted + restored so
    # a post-close restart (which re-runs the settle sweep) cannot double-book.
    overlay_pnl_booked: bool = False

    # Current option prices (updated every heartbeat for P&L / cushion) —
    # bridge properties over legs[*].price
    # Fill prices at entry (option points; ×100 for dollars). Set in
    # _execute_entry(), corrected by _verify_entry_fill_prices() — bridge
    # properties over legs[*].fill_price.
    # Per-leg MID at fill time ((bid+ask)/2 of the filling attempt). REAL entry
    # slippage reference = fill - mid (0.0 = not captured, e.g. dry-run / no
    # quote) — bridge properties over legs[*].mid_at_fill.

    # Status tracking
    is_complete: bool = False  # All 4 legs filled
    call_side_stopped: bool = False  # Call spread was stopped out (LOSS)
    put_side_stopped: bool = False   # Put spread was stopped out (LOSS)
    call_side_expired: bool = False  # Call spread expired worthless (PROFIT - kept credit)
    put_side_expired: bool = False   # Put spread expired worthless (PROFIT - kept credit)
    call_side_skipped: bool = False  # Call side was never opened (HYDRA one-sided entry)
    put_side_skipped: bool = False   # Put side was never opened (HYDRA one-sided entry)
    # Directional-pivot close (directional_pivot, introduced 2026-05-01): closed mid-day
    # because SPX moved >= 0.25% from session open. Distinguished from
    # *_side_stopped (which uses credit+buffer threshold) so the journal /
    # dashboard / HOMER prompt can attribute the close to the pivot rule
    # rather than the standard stop. P&L treatment is identical to a stop.
    call_side_pivot_closed: bool = False
    put_side_pivot_closed: bool = False
    call_stop_time: str = ""   # ET ISO timestamp when call side stop executed
    put_stop_time: str = ""    # ET ISO timestamp when put side stop executed
    strategy_id: str = ""  # For Position Registry tracking

    # MKT-010: Wing illiquidity tracking (set by MKT-008 adjustment)
    # If a wing was illiquid and adjusted, that side is far OTM = SAFE
    # Used by HYDRA to place one-sided entries on the safe side
    call_wing_illiquid: bool = False  # Call wing was adjusted (calls far OTM = safe)
    put_wing_illiquid: bool = False   # Put wing was adjusted (puts far OTM = safe)

    # Fix #61: Position merge tracking
    # When two entries land on same strikes, Saxo merges them into one position
    # The older entry's position IDs become invalid, but credit is preserved
    call_side_merged: bool = False  # Call positions merged with another entry
    put_side_merged: bool = False   # Put positions merged with another entry

    # Commission tracking (display only - does not affect P&L calculations)
    # Open commission: commission_per_leg × 4 legs per IC (charged on entry)
    # Close commission: commission_per_leg × 2 legs per side (only charged when closed, not expired)
    # NOTE: commission_per_leg is the live configured rate ($1.15 since the
    # 2026-05-29 IBKR cutover; the old $2.50/$5.00 Saxo-era figures are stale).
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
        """Width of spreads - max of call/put for accurate capital calculation.

        Fix #74: Previously only checked call strikes, which understated capital
        when MKT-008 narrowed call spread but not put spread, and returned 0
        for put-only entries after restart (call strikes not reconstructed).
        Now returns max(call_width, put_width) for correct risk/margin.
        """
        call_width = 0.0
        put_width = 0.0
        if self.long_call_strike and self.short_call_strike:
            call_width = self.long_call_strike - self.short_call_strike
        if self.short_put_strike and self.long_put_strike:
            put_width = self.short_put_strike - self.long_put_strike
        return max(call_width, put_width)

    @property
    def call_spread_value(self) -> float:
        """Current value (cost to close) of call spread.

        Fix #52: Multiplies by contracts for multi-contract support.
        L-C2b (2026-06-10): clamp to the spread's structural [0, width] range.
        A short vertical's cost-to-close can never exceed its width (the
        intrinsic cap) nor fall below 0, yet it is built from two independently
        mid'd legs — a wide/crossed close-auction quote on ONE leg yields an
        economically impossible value (live 2026-06-10: a put spread marked
        $4270 on a 5pt/$3500-max structure). That phantom inflated the displayed
        cushion + P&L AND fed noisy values into the MKT-046 stop confirmation
        (oscillating across the trigger resets the 10s timer). Clamp to reality.
        """
        raw = (self.short_call_price - self.long_call_price) * 100 * self.contracts
        width = self.long_call_strike - self.short_call_strike
        if width and width > 0:
            return min(max(raw, 0.0), width * 100 * self.contracts)
        return raw

    @property
    def put_spread_value(self) -> float:
        """Current value (cost to close) of put spread. Fix #52: × contracts.
        See call_spread_value for the L-C2b [0, width] clamp rationale (the
        2026-06-10 wide-quote phantom that inflated cushion/P&L and the stop)."""
        raw = (self.short_put_price - self.long_put_price) * 100 * self.contracts
        width = self.short_put_strike - self.long_put_strike
        if width and width > 0:
            return min(max(raw, 0.0), width * 100 * self.contracts)
        return raw

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
            # Use actual stop debit when available (includes slippage), fall back to theoretical
            call_debit = self.actual_call_stop_debit if self.actual_call_stop_debit > 0 else self.call_side_stop
            put_debit = self.actual_put_stop_debit if self.actual_put_stop_debit > 0 else self.put_side_stop
            call_loss = call_debit - self.call_spread_credit
            put_loss = put_debit - self.put_spread_credit
            return -(call_loss + put_loss)
        elif call_stopped:
            # Call stopped (loss), put still open or merged
            call_debit = self.actual_call_stop_debit if self.actual_call_stop_debit > 0 else self.call_side_stop
            loss_on_call = call_debit - self.call_spread_credit
            if put_merged:
                # Put merged - credit preserved (profit = credit)
                return self.put_spread_credit - loss_on_call
            else:
                # Put still open
                return (self.put_spread_credit - self.put_spread_value) - loss_on_call
        elif put_stopped:
            # Put stopped (loss), call still open or merged
            # Use actual stop debit when available (includes slippage), fall back to theoretical
            put_debit = self.actual_put_stop_debit if self.actual_put_stop_debit > 0 else self.put_side_stop
            loss_on_put = put_debit - self.put_spread_credit
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

    # HYDRA specific counters (used by trend-following variant)
    # These remain 0 for base MEIC, but are tracked in HYDRA
    one_sided_entries: int = 0  # Fix #55: Count of one-sided (put-only or call-only) entries
    trend_overrides: int = 0    # Fix #56: Times trend filter caused one-sided entry
    credit_gate_skips: int = 0  # Fix #57: Times credit gate skipped/modified entry
    stops_avoided_mkt036: int = 0  # MKT-036: Times breach recovered before confirmation

    # Directional-pivot strategy state (directional_pivot, introduced 2026-05-01).
    # Always 0/empty/None for base MEIC and for HYDRA variant A which has
    # the pivot rule disabled. Tracked here (not subclassed) so state
    # serialization stays uniform across all variants.
    #
    # `directional_pivot_fired`: True once the continuous-monitor breach
    #     trigger has fired today. Idempotent — won't re-fire same day.
    # `directional_pivot_direction`: "up" (SPX +0.25% from open) or "down".
    #     None until the trigger fires.
    # `directional_pivot_fired_at`: ISO timestamp of the first breach. None
    #     until fired.
    # `deferred_entries`: dict of entry_index → {deferred_at, deadline,
    #     direction} for the pre-entry defer-and-watch window. Cleared
    #     on placement OR on cascade-skip when pivot fires.
    directional_pivot_fired: bool = False
    directional_pivot_direction: Optional[str] = None
    directional_pivot_fired_at: str = ""
    deferred_entries: dict = field(default_factory=dict)

    @property
    def total_stops(self) -> int:
        """Total stop losses triggered."""
        return self.call_stops_triggered + self.put_stops_triggered

    @property
    def active_entries(self) -> List[IronCondorEntry]:
        """Get entries that have open positions.

        An entry is active if it has at least one side that is still open
        (not stopped, not expired, not skipped).

        CRITICAL FIX (2026-02-03): Partial entries should still be monitored until expiry.
        Fix #73: Also check expired and skipped flags, not just stopped.
        """
        active = []
        for e in self.entries:
            # FIX #43: For one-sided entries (HYDRA), check only the placed side
            call_only = getattr(e, 'call_only', False)
            put_only = getattr(e, 'put_only', False)

            # Fix #73: A side is "done" if stopped, expired, skipped (never
            # opened), or pivot-closed (HYDRA directional-pivot rule, 2026-05-01).
            call_done = (
                e.call_side_stopped or
                getattr(e, 'call_side_expired', False) or
                getattr(e, 'call_side_skipped', False) or
                getattr(e, 'call_side_pivot_closed', False)
            )
            put_done = (
                e.put_side_stopped or
                getattr(e, 'put_side_expired', False) or
                getattr(e, 'put_side_skipped', False) or
                getattr(e, 'put_side_pivot_closed', False)
            )

            if call_only:
                if call_done:
                    continue
            elif put_only:
                if put_done:
                    continue
            elif call_done and put_done:
                # Full IC - both sides resolved
                continue

            if e.is_complete:
                # Entry with at least one side still open
                active.append(e)
            else:
                # Partial entry - check if ANY leg is open. Key on the CONID
                # (uic) FIRST: IBKR has no per-leg position id (always None), so
                # a pos_id-only check dropped every recovered LIVE open entry
                # from monitoring after a mid-day restart (06-04 audit, same
                # class as the H9 _get_current_position_size fix). pos_id is the
                # dry-run DRY_* fallback.
                has_any_position = any([
                    getattr(e, 'short_call_uic', None),
                    getattr(e, 'long_call_uic', None),
                    getattr(e, 'short_put_uic', None),
                    getattr(e, 'long_put_uic', None),
                    e.short_call_position_id,
                    e.long_call_position_id,
                    e.short_put_position_id,
                    e.long_put_position_id,
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
    # #17/#18: last broker freshness flag seen for each feed (IBKR field 6509:
    # 'R'=real-time, 'D'=delayed, 'Z'=stale). None when the broker didn't
    # surface it. Used so staleness is derived from the QUOTE, not the local
    # fetch time — a frozen-but-responding feed (repeated 'Z') no longer reads
    # as "fresh" just because the bot keeps polling.
    last_spx_availability: Optional[str] = None
    last_vix_availability: Optional[str] = None

    # Intraday OHLC tracking
    spx_open: float = 0.0
    spx_high: float = 0.0
    spx_low: float = float('inf')
    vix_open: float = 0.0
    vix_high: float = 0.0
    vix_low: float = float('inf')
    vix_samples: List[float] = field(default_factory=list)

    # P3: Flash crash velocity tracking
    price_history: Deque[Tuple[datetime, float]] = field(default_factory=lambda: deque(maxlen=100))

    @staticmethod
    def _is_regular_session_or_later() -> bool:
        """True when current ET time is at-or-after 9:30 AM (regular-session open).

        Used to gate intraday-OHLC capture (spx_open / spx_high / spx_low /
        vix_open / vix_high / vix_low) so pre-9:30 ET ticks (from the broker's
        IBKR feed) can't contaminate the "% from open" reference. All downstream strategy
        decisions that compute SPX/VIX move from open — Upday-035,
        Downday-035, whipsaw filter, ROC gate, directional-pivot — depend on
        this anchor being the actual regular-session open (9:30 AM ET).

        Early-close days (Black Friday, day before holidays) still open at
        9:30 ET and only differ on the close side, so 9:30 is universal.
        """
        now = get_us_market_time()
        return (now.hour, now.minute) >= (9, 30)

    def update_spx(self, price: float, availability: Optional[str] = None):
        """Update SPX price with timestamp and track open/high/low.

        SPX live price (and flash-crash velocity tracking) is updated for
        every valid tick, including pre-market. But intraday-OHLC capture
        is gated to regular session (>= 9:30 ET) — see _is_regular_session_or_later
        for the rationale.

        #17/#18: ``availability`` is the broker's freshness flag (IBKR field
        6509: 'R'=real-time, 'D'=delayed, 'Z'=stale). When it is 'Z' the broker
        is serving a STALE tick — we record the flag but DO NOT advance
        ``last_spx_update``, so ``is_spx_stale`` / DATA-001 trips on a frozen
        upstream feed instead of being reset by every local poll. None (the
        default) preserves the prior behavior for callers/tests that don't plumb
        availability.
        """
        if price > 0:
            avail = availability.upper() if isinstance(availability, str) else None
            self.last_spx_availability = avail
            # IBKR-audit #2/#4: parse only the FIRST char of 6509 (IBKR appends
            # secondary/tertiary chars, e.g. 'RpB','Zp','DP'). First char:
            # R=RealTime, D=Delayed, Z=Frozen, Y=Frozen-Delayed, N=Not-Subscribed.
            first = avail[:1] if avail else None
            if first in ("Z", "Y", "N"):
                # NOT real-time (Frozen / Frozen-Delayed / Not-Subscribed): keep
                # the last price for display but do NOT refresh the freshness
                # clock — DATA-001 then trips so entries fail closed instead of
                # trading on a frozen/unentitled feed.
                # I-Low: dedup — this would otherwise flood every ~10s tick during
                # a long frozen window. Log once, then at most every 5 min.
                import time as _t
                _now = _t.monotonic()
                if _now - getattr(self, "_last_spx_notrt_warn_at", 0.0) >= 300:
                    self._last_spx_notrt_warn_at = _now
                    logger.warning(
                        "DATA-001: SPX quote is NOT real-time (6509=%r); not "
                        "refreshing freshness timestamp", avail
                    )
                return
            if first == "D":
                logger.warning(
                    "DATA: SPX quote is DELAYED (6509=%r) — proceeding but data "
                    "may lag real-time", avail
                )
            self.spx_price = price
            self.last_spx_update = get_us_market_time()

            # Velocity history is captured for all valid ticks (pre-market
            # included) so flash-crash detection works through extended hours.
            self.price_history.append((self.last_spx_update, price))

            # Intraday OHLC tracking: regular session only.
            if not self._is_regular_session_or_later():
                return

            # Track opening price (first valid update of the regular session)
            if self.spx_open == 0.0:
                self.spx_open = price
                logger.info(f"SPX session open captured: {price:.2f} at {self.last_spx_update.strftime('%H:%M:%S')} ET")

            # Track intraday high/low (regular session only)
            if price > self.spx_high:
                self.spx_high = price
            if price < self.spx_low:
                self.spx_low = price

    def update_vix(self, vix: float, availability: Optional[str] = None):
        """Update VIX with timestamp and track open/high/low/average.

        Same gating as update_spx: pre-market VIX (if available) updates
        the live `vix` field but does NOT contribute to vix_open/high/low
        or the samples list.

        #17/#18: like update_spx, a broker 'Z' (stale) availability flag does
        NOT advance ``last_vix_update`` so a frozen VIX feed is detectable;
        'D' (delayed) logs a warning. None preserves prior behavior.
        """
        if vix > 0:
            avail = availability.upper() if isinstance(availability, str) else None
            self.last_vix_availability = avail
            # IBKR-audit #2/#4: first char only (R/D/Z/Y/N + secondary chars).
            first = avail[:1] if avail else None
            if first in ("Z", "Y", "N"):
                # I-Low: dedup (see update_spx) — at most one warning per 5 min.
                import time as _t
                _now = _t.monotonic()
                if _now - getattr(self, "_last_vix_notrt_warn_at", 0.0) >= 300:
                    self._last_vix_notrt_warn_at = _now
                    logger.warning(
                        "DATA-001: VIX quote is NOT real-time (6509=%r); not "
                        "refreshing freshness timestamp", avail
                    )
                return
            if first == "D":
                logger.warning("DATA: VIX quote is DELAYED (6509=%r)", avail)
            self.vix = vix
            self.last_vix_update = get_us_market_time()

            if not self._is_regular_session_or_later():
                return

            # Track opening VIX (first valid update of the regular session)
            if self.vix_open == 0.0:
                self.vix_open = vix
                logger.info(f"VIX session open captured: {vix:.2f} at {self.last_vix_update.strftime('%H:%M:%S')} ET")

            # Track VIX high/low and samples for average (regular session only)
            if vix > self.vix_high:
                self.vix_high = vix
            if vix < self.vix_low:
                self.vix_low = vix
            self.vix_samples.append(vix)

    def is_spx_stale(self, max_age: int = MAX_DATA_STALENESS_SECONDS) -> bool:
        """Check if SPX data is stale.

        #17/#18: freshness is now derived from the broker QUOTE, not merely the
        local fetch cadence. update_spx refuses to advance last_spx_update on a
        broker 'Z' (stale) availability flag, so a frozen-but-responding upstream
        feed (repeated 'Z') ages out and trips this check — the local poll no
        longer resets the clock on data the broker itself flagged as not
        real-time.
        """
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
        self.spx_open = 0.0
        self.spx_high = 0.0
        self.spx_low = float('inf')
        self.vix_open = 0.0
        self.vix_high = 0.0
        self.vix_low = float('inf')
        self.vix_samples.clear()
        self.price_history.clear()


# =============================================================================
# MAIN STRATEGY CLASS
# =============================================================================

class ConfigError(ValueError):
    """Invalid strategy configuration (e.g. an instrument parameter is unset).

    Subclasses ValueError so existing broad config-error handling still catches it.
    """


class MEICStrategy(abc.ABC):
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
    - VIX filtering (skip entries if VIX >= max_vix_entry)
    - FOMC blackout (skip all entries on Fed announcement days)
    - Comprehensive edge case handling (79 cases analyzed)

    Version: 1.2.8 (2026-02-13)
    """

    BOT_NAME = "MEIC"

    # Instrument-parameter fallbacks (modularity-audit item 2). __init__ calls
    # _load_instrument_params() which sets INSTANCE attributes from config,
    # shadowing these. They exist so the threaded call sites (which read
    # self.underlying_symbol / .volatility_symbol / .trading_class / .exchange /
    # .strike_increment / .target_dte) never AttributeError on an object built
    # via __new__ that bypassed __init__ — a test-only construction path. Values
    # equal today's SPX/0DTE literals, so the fallback is behavior-identical.
    underlying_symbol = "SPX"
    volatility_symbol = "VIX"
    trading_class = "SPXW"
    exchange = "CBOE"
    strike_increment = 5
    target_dte = 0

    # Strategy risk policy (modularity-audit item 6a). Defined-risk strategies
    # (the iron-condor family) require every short to be hedged by a long wing —
    # an unhedged short is a CRITICAL naked-short emergency. An undefined-risk
    # strategy (e.g. a short strangle) sets this False: its shorts are naked by
    # design and must NOT be emergency-closed. Default True = today's behavior.
    requires_protective_wings = True

    # ── Strategy extension points (template-method hooks) ──────────────────
    # The base monitoring loop (_check_entries / run-loop) calls these; every
    # concrete strategy MUST implement them. Declared @abstractmethod so a new
    # strategy that forgets one fails fast at construction (a clear TypeError)
    # rather than mid-trade. MEICStrategy is therefore abstract and never
    # instantiated directly — only HydraStrategy / BrandonHydraStrategy are.
    # (modularity-audit item 4b)
    @abc.abstractmethod
    def _calculate_strikes(self, entry) -> bool:
        """Select and populate this entry's strikes. Return True if viable."""

    @abc.abstractmethod
    def _initiate_entry(self) -> str:
        """Attempt the next scheduled entry. Return a human-readable status."""

    @abc.abstractmethod
    def _check_stop_losses(self):
        """Check open positions for stop/exit conditions. Return an action string or None."""

    def __init__(
        self,
        broker: IBClient,
        config: Dict[str, Any],
        logger_service: Any,
        dry_run: bool = False,
        alert_service: Optional[AlertService] = None
    ):
        """
        Initialize the strategy.

        Args:
            broker: Connected IBClient (Interactive Brokers adapter).
            config: Strategy configuration dictionary
            logger_service: Trade logging service
            dry_run: If True, simulate trades without placing real orders
            alert_service: Optional AlertService for Telegram/Email notifications
        """
        self.broker = broker
        self.config = config
        self.trade_logger = logger_service
        self.dry_run = dry_run
        self._last_margin_snapshot = {}  # Populated by ORDER-004, read by DataRecorder

        # CONFIG-001: Validate configuration on startup
        # Note: _validate_config is called later after self is fully initialized
        # We store config first, validate after alert_service is ready

        # Alert service
        if alert_service:
            self.alert_service = alert_service
        else:
            # Variant-aware alert source so every email/Telegram alert identifies
            # WHICH HYDRA variant fired (A=HYDRA, B=HYDRA_B, C=HYDRA_C) instead of
            # a generic "HYDRA". Mirrors main._variant_bot_name() + the monitor-log
            # tag, and gives each variant its own dedup-fingerprint namespace so a
            # storm on one variant can't suppress another variant's alerts.
            _variant = (os.environ.get("HYDRA_VARIANT_ID", "") or "").strip().lower()
            _alert_name = f"{self.BOT_NAME}_{_variant.upper()}" if _variant else self.BOT_NAME
            self.alert_service = AlertService(config, _alert_name)

        # Position Registry for multi-bot isolation
        self.registry = PositionRegistry(REGISTRY_FILE)

        # Strategy configuration
        self.strategy_config = config.get("strategy", {})

        # Dry-run "force normal day" override.
        # When True AND dry_run is True, all DATE/EVENT-based skip rules are
        # bypassed so every weekday runs as a normal full-IC trading day. The
        # variant-comparison experiment depends on this — without it, FOMC weeks
        # produce zero data points for variant B, blowing holes in the dataset.
        # Live mode is unaffected: the flag has no effect when dry_run is False.
        # Currently bypassed: FOMC announcement skip (MEIC), FOMC T+1 skip
        # (HYDRA), MKT-038 FOMC T+1 call-only (HYDRA).
        # NOT bypassed: market-condition skips (whipsaw, MKT-011 credit gate,
        # MKT-040 put-non-viable, conditional Up/Down-day triggers) — those
        # reflect what the live bot would also do given current market state.
        self.dry_run_force_normal_day = bool(
            self.strategy_config.get("dry_run_force_normal_day", False)
        )
        if self.dry_run and self.dry_run_force_normal_day:
            logger.info(
                "  Dry-run override: force-normal-day ENABLED — "
                "FOMC/event-based skips bypassed (data collection mode)"
            )

        # CONFIG-001: Validate configuration early
        config_valid, config_errors = self._validate_config()
        if not config_valid:
            error_msg = f"Configuration validation failed: {'; '.join(config_errors)}"
            logger.critical(error_msg)
            raise ValueError(error_msg)

        # Underlying (SPX). P7-audit L5: `underlying_uic`,
        # `option_root_uic`, `vix_spot_uic` are vestigial Saxo identifiers
        # — IBKR resolves instruments by conid via `_read_option_chain`
        # / `IBClient.qualify_contract`, not these JSON-pinned IDs.
        # `underlying_symbol` is still meaningful (it's the lookup key
        # for `qualify_contract("SPX", sec_type="IND")`).
        # Instrument + expiry parameters (modularity-audit item 2) — defaults
        # equal today's SPX/0DTE literals, so an absent config is byte-identical.
        self._load_instrument_params()

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
        # SELL-leg net-credit prevention floor (2026-06-10): a short vertical leg
        # must never sell BELOW its paired long's fill + this minimum net credit,
        # so the vertical cannot leg into a net DEBIT (the Entry #2 inversion).
        # PER-SHARE dollars (compared against per-share limit prices), NOT ×100.
        self.min_net_credit_per_contract = float(
            self.strategy_config.get("min_net_credit_per_contract", 0.05)
        )
        # HONEST-DRY-RUN (2026-07-17): in dry-run, skip an entry whose simulated
        # credit can't clear the net-credit floor a LIVE entry would require. B was
        # booking phantom $0-credit ICs (degraded-Polygon 8δ picks) that live C
        # never takes — polluting the dashboard/journal and diverging the shadow.
        self.skip_dryrun_below_net_credit_floor = bool(
            self.strategy_config.get("skip_dryrun_below_net_credit_floor", True)
        )
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

        # ORDER-006 caps — config-overridable so variants that intentionally
        # size up (Brandon narrow-spread = many small spreads) don't trip the
        # 1c-baseline safety net. Defaults preserve the original limits for
        # variant A / MEIC. Variant C raises these in its config because 15c ×
        # 4 entries × 4 legs = 240 contracts per underlying, well beyond the
        # 30 default.
        self.max_contracts_per_order = self.strategy_config.get(
            "max_contracts_per_order", MAX_CONTRACTS_PER_ORDER
        )
        self.max_contracts_per_underlying = self.strategy_config.get(
            "max_contracts_per_underlying", MAX_CONTRACTS_PER_UNDERLYING
        )
        # ORDER-004 BP-per-IC gate — config-overridable for the same reason as
        # the order/underlying caps above. Default $5,000/contract is sized
        # for the wide HYDRA-baseline (50pt × $100). Brandon narrow-spread
        # variants (5pt × $100 = $500/contract) need to lower this or every
        # 15c entry gets margin-rejected on a $50K account. Lesson learned
        # 2026-05-06: B and C lost a full day's data because all entries
        # tripped 15c × $5,000 = $75K > $50,741 available BP.
        self.min_buying_power_per_ic = self.strategy_config.get(
            "min_buying_power_per_ic", MIN_BUYING_POWER_PER_IC
        )
        # DATA-003 P&L sanity bounds — used by both per-entry stop check and
        # daily total check. The min in particular is the same class of bug
        # as MIN_BUYING_POWER_PER_IC: -$3,000 was the floor for 1c × 50pt
        # baseline. At 15c × 5pt the real max loss is -$7,500, and at 15c ×
        # 10pt it's -$14,700 — a hardcoded -$3,000 floor would flag these as
        # "impossible" and silently skip the per-entry stop check. Now
        # config-overridable; B/C scale up to fit narrow-spread × 15c worst
        # case. The per-entry max check separately uses entry.total_credit
        # × 1.2 when available (so the max bound is mostly self-correcting),
        # but we still expose it for completeness.
        self.max_pnl_per_ic = self.strategy_config.get("max_pnl_per_ic", MAX_PNL_PER_IC)
        self.min_pnl_per_ic = self.strategy_config.get("min_pnl_per_ic", MIN_PNL_PER_IC)

        # Commission tracking (display only - does not affect strategy logic)
        # Commission per leg (display only). IBKR all-in SPX cost ~$1.15/leg/contract
        # since the 2026-05-29 cutover; variant configs set commission_per_leg=1.15.
        # The old $2.50/$5.00 Saxo-era figures are stale (see the dataclass note above).
        # Default fallback below is the legacy 2.50 and should be overridden by config.
        self.commission_per_leg = self.strategy_config.get("commission_per_leg", 2.50)

        # State and metrics file paths - can be overridden by subclasses BEFORE calling super().__init__()
        # Check if subclass already set them to avoid overwriting (e.g., HYDRA uses different files)
        if not hasattr(self, 'state_file') or self.state_file is None:
            self.state_file = STATE_FILE
        if not hasattr(self, 'metrics_file') or self.metrics_file is None:
            self.metrics_file = METRICS_FILE

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

        # FIX #75: Track background fill correction threads
        self._pending_fill_corrections: list = []

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

        # CRITICAL: Recover positions from the broker (not the local state
        # file) — the state file can be stale if positions were closed
        # manually; the broker is the source of truth.
        self._recover_positions_from_saxo()

        # TIME-001: Validate system clock on startup
        if CLOCK_SYNC_CHECK_ENABLED:
            self._validate_system_clock()

        logger.info(f"MEICStrategy initialized - State: {self.state.value}")
        logger.info(f"  Underlying: {self.underlying_symbol}")
        logger.info(f"  Entry times: {[t.strftime('%H:%M') for t in self.entry_times]}")
        min_spread = self.strategy_config.get("min_spread_width", 25)
        max_spread = self.strategy_config.get("max_spread_width", 100)
        logger.info(f"  Spread width: {min_spread}-{max_spread}pt (VIX-adjusted, floor={min_spread}pt)")
        logger.info(f"  MEIC+ enabled: {self.meic_plus_enabled}")
        logger.info(f"  Position Registry: {REGISTRY_FILE}")

        # Check for FOMC announcement day (day 2 only — day 1 is safe to trade)
        fomc_skip = self.strategy_config.get("fomc_announcement_skip", True)
        if is_fomc_announcement_day():
            if fomc_skip:
                logger.warning("TODAY IS FOMC ANNOUNCEMENT DAY - No entries will be placed")
            else:
                logger.warning("TODAY IS FOMC ANNOUNCEMENT DAY - Trading normally (fomc_announcement_skip=False)")
        elif is_fomc_meeting_day():
            logger.info("FOMC meeting day 1 (no announcement) - normal trading")

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

        # DATA-001: Validate data freshness before trading.
        if self._is_data_stale_for_trading():
            # L-H4: a stale SPX INDEX snapshot must NOT disable stop monitoring
            # on EXISTING positions. The default HYDRA stop is credit-based
            # (driven by OPTION leg mids, which `_check_stop_losses` re-fetches
            # itself and which can be fresh even when the SPX index snapshot is
            # stale), so blanket-returning here left open positions UNMONITORED
            # whenever SPX froze (fails-unmonitored, not fails-safe). Run the
            # stop check (it carries its own per-leg DATA-004 zero-price guard)
            # for any open positions before bailing — only NEW entries are gated
            # on SPX freshness.
            if self.daily_state.active_entries:
                try:
                    stop_result = self._check_stop_losses()
                    if stop_result:
                        return stop_result
                except Exception as exc:
                    logger.error(
                        f"DATA-001: stop check during stale-SPX window failed: {exc}"
                    )
            return "Skipping new entries - market data is stale (stops still monitored)"

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
            # Both STATE-vs-active-entries mismatches ("MONITORING but no active
            # entries" AND "IDLE but have N active entries") are benign, self-
            # recovering transients — _attempt_state_recovery() below sets the
            # correct state on the SAME tick. The "IDLE with active entries" case
            # is exactly how the multi-day CALENDAR variants (D/E) start each day:
            # the state file reloads an open calendar before the state machine
            # leaves IDLE, so logging it at ERROR emitted a false alarm every
            # morning (2026-07-02). Log any active-entries mismatch at WARNING
            # (auto-recovering); reserve ERROR for the genuinely-concerning
            # registry position-count drift (live-only), which does NOT contain
            # "active entries".
            if "active entries" in consistency_error:
                logger.warning(f"STATE-002 (auto-recovering): {consistency_error}")
            else:
                logger.error(f"STATE-002: {consistency_error}")
            # Attempt to recover instead of halting
            self._attempt_state_recovery()

        # POS-003: Periodic position reconciliation (hourly)
        self._check_hourly_reconciliation()

        # MKT-008: Skip all trading on FOMC announcement day (day 2 only)
        # Day 1 has no announcement/press conference — safe to trade normally.
        # Day 2 has the 2:00 PM ET announcement — high volatility risk.
        # Override via config: set "fomc_announcement_skip": false to trade normally on announcement day.
        # Dry-run force-normal-day also bypasses this (data-collection mode).
        fomc_skip = self.strategy_config.get("fomc_announcement_skip", True)
        if is_fomc_announcement_day() and fomc_skip and not self._force_normal_day():
            if self.state != MEICState.DAILY_COMPLETE:
                self.state = MEICState.DAILY_COMPLETE
                logger.info("FOMC announcement day - skipping all entries")
            return "FOMC announcement day - no trading"

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

        # Check if all entries have been used (e.g., restored from state file after restart)
        if self._next_entry_index >= len(self.entry_times) and not self.daily_state.active_entries:
            self.state = MEICState.DAILY_COMPLETE
            logger.info("All entry times used with no active positions - transitioning to DAILY_COMPLETE")
            return "All entries complete, daily session done"

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
        if self.current_vix >= self.max_vix_entry:
            logger.warning(f"VIX {self.current_vix:.1f} >= {self.max_vix_entry} - skipping remaining entries")
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
                    f"at {scheduled_time.strftime('%H:%M:%S')} (window ended at {window_end.strftime('%H:%M:%S')})"
                )
                self.daily_state.entries_skipped += 1
                self._next_entry_index += 1
            else:
                # Entry is either in the future or currently in its window
                break

        if skipped > 0:
            if self._next_entry_index < len(self.entry_times):
                logger.info(f"TIME-002: Skipped {skipped} missed entries, next is Entry #{self._next_entry_index + 1}")
            else:
                logger.info(f"TIME-002: Skipped {skipped} missed entries, all entries complete")

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
            entry.long_call_strike += self.strike_increment  # Move further OTM (up for calls)
        if entry.long_call_strike != original_long_call:
            logger.warning(
                f"MKT-012: Long call {original_long_call} conflicts with existing short strike, "
                f"adjusted to {entry.long_call_strike}"
            )

        # Check long put strike
        original_long_put = entry.long_put_strike
        while entry.long_put_strike in occupied:
            entry.long_put_strike -= self.strike_increment  # Move further OTM (down for puts)
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
            entry.short_call_strike += self.strike_increment
            entry.long_call_strike += self.strike_increment
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
            entry.short_put_strike -= self.strike_increment
            entry.long_put_strike -= self.strike_increment
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
            entry.long_call_strike += self.strike_increment  # Move further OTM (up for calls)
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
            entry.long_put_strike -= self.strike_increment  # Move further OTM (down for puts)
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

        quote = self._read_option_quote(uic)
        if not quote:
            logger.warning(
                f"MKT-014: Entry #{entry_number} {put_call} {strike} - "
                f"no quote available after {reason}"
            )
            return

        bid = quote.get("bid") or 0
        ask = quote.get("ask") or 0

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
        # GAP-B (F7.5): delegates to the IBClient credit estimator.
        return self._estimate_entry_credit_ib(entry)

    def _estimate_entry_credit_ib(self, entry: IronCondorEntry) -> Tuple[float, float]:
        """IBKR path of :meth:`_estimate_entry_credit` (GAP-B / F7.5).

        Resolves the (up to) 4 leg conids in one ``_read_option_chain``
        read (F3.2), batch-quotes them via ``_read_option_quotes_batch``
        (F3.4), and computes each spread's mid-credit with ``_quote_mid``
        (F4.9). Preserves the Saxo path's call_only / put_only side
        skipping and the "one side's conids missing → estimate / return
        the other" fallbacks that feed MKT-032/039/040.

        Returns ``(estimated_call_credit, estimated_put_credit)`` in
        total dollars, ``(0.0, 0.0)`` on failure.

        SIDE EFFECT (MKT-048, 2026-06-22): stashes the per-share *fillable*
        credit of each side — ``short_bid − long_mid`` — on the entry as
        ``_fillable_call_ps`` / ``_fillable_put_ps`` (``None`` when a leg is
        unquotable / crossed). The returned MID estimate can clear MKT-011's
        threshold
        on a spread whose ACTUAL fillable credit is a debit (fills are
        long@ask / short@bid). ``_check_credit_gate`` reads the stash to veto
        a structurally-unfillable side UP FRONT instead of discovering it at
        leg 3 (the 2026-06-22 C Entry#1 wall). Reset every call so a later
        re-estimate (e.g. the call-only put-tighten retry) can't leave a
        stale read.
        """
        entry._fillable_call_ps = None
        entry._fillable_put_ps = None

        expiry = self._get_todays_expiry()
        if not expiry:
            logger.warning("Could not get expiry for credit estimation")
            return (0.0, 0.0)

        try:
            need_call = not getattr(entry, "put_only", False)
            need_put = not getattr(entry, "call_only", False)

            # Resolve all needed strikes' conids in one chain read.
            wanted: List[float] = []
            if need_call:
                wanted += [float(entry.short_call_strike),
                           float(entry.long_call_strike)]
            if need_put:
                wanted += [float(entry.short_put_strike),
                           float(entry.long_put_strike)]
            call_map, put_map = self._read_option_chain(expiry, wanted)

            sc = call_map.get(float(entry.short_call_strike)) if need_call else None
            lc = call_map.get(float(entry.long_call_strike)) if need_call else None
            sp = put_map.get(float(entry.short_put_strike)) if need_put else None
            lp = put_map.get(float(entry.long_put_strike)) if need_put else None

            ids = [x for x in (sc, lc, sp, lp) if x]
            quotes = self._read_option_quotes_batch(ids) if ids else {}

            def _mid(cid) -> float:
                return self._quote_mid(quotes.get(cid)) if cid else 0.0

            def _sane_bid(cid) -> Optional[float]:
                """The bid a SHORT leg realistically sells into, but ONLY off a
                SANE (uncrossed) book. None when the leg is unquoted, has no
                bid, OR the book is crossed (bid > ask) — so MKT-048 fails open
                on garbage rather than vetoing off a crossed quote (the raw
                bid of a crossed book is nonsense; _parse_quote_row keeps it,
                only nulling `mid`). Mirrors the L9 crossed-market guard."""
                q = quotes.get(cid) if cid else None
                if not q:
                    return None
                b, a = q.get("bid"), q.get("ask")
                if b is None:
                    return None
                if a is not None and float(b) > float(a):
                    return None  # crossed book — not a usable bid
                return float(b)

            def _quoted(cid) -> bool:
                """True iff the batch actually returned a quote row for
                this conid. P7-audit H7: a conid that resolved but got
                NO quote makes _quote_mid return 0.0 — if that's the
                LONG leg, the spread credit becomes the full short
                premium (overstated), which could pass the MKT-011 gate
                on a trade that is actually sub-viable. A side is only
                priceable when BOTH its legs are genuinely quoted."""
                return cid is not None and quotes.get(cid) is not None

            estimated_call_credit = 0.0
            if need_call:
                if not sc or not lc:
                    if need_put:
                        logger.warning(
                            "Could not resolve call conids — "
                            "estimating put side only"
                        )
                    else:
                        logger.warning("Could not resolve call conids")
                        return (0.0, 0.0)
                elif not (_quoted(sc) and _quoted(lc)):
                    logger.warning(
                        "Call side has an unquoted leg — credit estimate "
                        "unreliable, treating call side as non-viable"
                    )
                else:
                    estimated_call_credit = (_mid(sc) - _mid(lc)) * 100

            estimated_put_credit = 0.0
            if need_put:
                if not sp or not lp:
                    if need_call and estimated_call_credit > 0:
                        logger.warning(
                            f"Could not resolve put conids — returning call "
                            f"estimate only (${estimated_call_credit/100:.2f})"
                        )
                        return (estimated_call_credit, 0.0)
                    logger.warning("Could not resolve put conids")
                    return (0.0, 0.0)
                elif not (_quoted(sp) and _quoted(lp)):
                    logger.warning(
                        "Put side has an unquoted leg — credit estimate "
                        "unreliable, treating put side as non-viable"
                    )
                else:
                    estimated_put_credit = (_mid(sp) - _mid(lp)) * 100

            # MKT-048: per-share fillable credit = short_bid − long_MID. The
            # placement net-credit floor (_sell_credit_floor_price) fills the
            # short only when its bid clears (long_FILL + min_net_credit). The
            # long is a BUY whose limit STARTS at mid (PROGRESSIVE_RETRY_SEQUENCE
            # attempt 1 = 0% slippage) and a mid-limit buy fills at ≤ mid, so
            # long_fill ≈ long_mid (≤ mid in the common case). Predicting with
            # long_ASK over-states the long's cost and false-vetoes fillable
            # spreads (adversarial review F1, 2026-06-22); long_mid is the
            # unbiased estimate. Penny-rounded so a float-subtraction artefact
            # (0.35−0.30=0.04999…) can't spuriously trip the gate's `< floor`
            # (review F3). A side is checkable only when both legs are quoted
            # and the short has a SANE (uncrossed) bid + the long a positive
            # mid; otherwise None → gate fails open.
            if need_call and sc and lc and _quoted(sc) and _quoted(lc):
                sc_bid, lc_mid = _sane_bid(sc), _mid(lc)
                if sc_bid is not None and lc_mid > 0:
                    entry._fillable_call_ps = round(sc_bid - lc_mid, 2)
            if need_put and sp and lp and _quoted(sp) and _quoted(lp):
                sp_bid, lp_mid = _sane_bid(sp), _mid(lp)
                if sp_bid is not None and lp_mid > 0:
                    entry._fillable_put_ps = round(sp_bid - lp_mid, 2)

            logger.debug(
                f"Credit estimation (IB) for Entry #{entry.entry_number}: "
                f"Call ${estimated_call_credit:.2f}, Put ${estimated_put_credit:.2f} | "
                f"fillable/sh Call={entry._fillable_call_ps} Put={entry._fillable_put_ps}"
            )
            return (estimated_call_credit, estimated_put_credit)

        except Exception as e:
            logger.warning(f"Credit estimation (IB) failed: {e}")
            return (0.0, 0.0)

    def _simulate_entry(self, entry: IronCondorEntry) -> bool:
        """
        Simulate an iron condor entry (dry-run mode).

        Path-B realism (2026-04-27): use REAL credits from MKT-011 estimator and
        REAL conids from the IBKR chain, so heartbeat price updates fetch real
        bid/ask. Only the actual order placement is skipped.
        """
        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error("[DRY RUN] Could not determine today's expiry — falling back to crude sim")
            credit_ratio = 0.025
            entry.call_spread_credit = self.spread_width * credit_ratio * 100 * self.contracts_per_entry
            entry.put_spread_credit = self.spread_width * credit_ratio * 100 * self.contracts_per_entry
        else:
            # Populate real UICs from chain so heartbeat fetches real quotes
            try:
                if entry.short_call_strike and entry.long_call_strike:
                    entry.short_call_uic = self._get_option_uic(entry.short_call_strike, "Call", expiry) or 0
                    entry.long_call_uic = self._get_option_uic(entry.long_call_strike, "Call", expiry) or 0
                if entry.short_put_strike and entry.long_put_strike:
                    entry.short_put_uic = self._get_option_uic(entry.short_put_strike, "Put", expiry) or 0
                    entry.long_put_uic = self._get_option_uic(entry.long_put_strike, "Put", expiry) or 0
            except Exception as e:
                logger.warning(f"[DRY RUN] UIC lookup failed for Entry #{entry.entry_number}: {e}")

            # Use REAL estimated credits from current chain (not 2.5% formula)
            try:
                est_call, est_put = self._estimate_entry_credit(entry)
                if est_call > 0:
                    entry.call_spread_credit = est_call * self.contracts_per_entry
                if est_put > 0:
                    entry.put_spread_credit = est_put * self.contracts_per_entry
            except Exception as e:
                logger.warning(f"[DRY RUN] Credit estimate failed for Entry #{entry.entry_number}: {e}")
                # Fallback to crude formula
                if not entry.call_spread_credit:
                    entry.call_spread_credit = self.spread_width * 0.025 * 100 * self.contracts_per_entry
                if not entry.put_spread_credit:
                    entry.put_spread_credit = self.spread_width * 0.025 * 100 * self.contracts_per_entry

        # HONEST-DRY-RUN (2026-07-17): a live entry ABORTS at the net-credit
        # guard-floor when a short can't clear long_fill + min_net_credit (that
        # day's degraded-Polygon 8δ picks collected ~$0 on BOTH sides → live C
        # placed 0 positions). Don't book a phantom $0-credit IC that live would
        # never take. Skip ONLY when NO ACTIVE side clears the floor — mirror
        # live: (a) a side we are not trading (put_only/call_only, or a
        # GEX-adjuster-skipped side) has credit 0.0 BY DESIGN and must NOT trip
        # the floor — that would wrongly drop a legit one-sided entry (2026-07-18
        # review); (b) a full IC with one viable side still legs that side in, so
        # a single transiently-unpriceable leg must not skip the whole entry
        # ("fails closed where live fails open"). call_active/put_active mirror
        # _execute_entry's gating. abort_entry_reason → clean skip in
        # _initiate_entry.
        if getattr(self, "skip_dryrun_below_net_credit_floor", True):
            floor = self.min_net_credit_per_contract * 100.0  # per-share → per-contract $
            n = max(getattr(self, "contracts_per_entry", 1) or 1, 1)
            put_only = getattr(entry, "put_only", False)
            call_only = getattr(entry, "call_only", False)
            call_active = (
                not put_only and not getattr(entry, "call_side_skipped", False)
                and bool(entry.short_call_strike)
            )
            put_active = (
                not call_only and not getattr(entry, "put_side_skipped", False)
                and bool(entry.short_put_strike)
            )
            per_ct_call = (entry.call_spread_credit or 0.0) / n
            per_ct_put = (entry.put_spread_credit or 0.0) / n
            call_clears = call_active and per_ct_call >= floor
            put_clears = put_active and per_ct_put >= floor
            if not call_clears and not put_clears:
                entry.abort_entry_reason = (
                    f"dry-run credit below net-credit floor — no active side clears "
                    f"${floor:.2f}/ct (call ${per_ct_call:.2f} active={call_active}, "
                    f"put ${per_ct_put:.2f} active={put_active}); live would place nothing"
                )
                logger.warning(
                    f"[DRY RUN] Entry #{entry.entry_number} SKIP: {entry.abort_entry_reason}"
                )
                return False

        # Generate fake position IDs (we have no real Saxo positions in dry mode)
        base_id = int(datetime.now().timestamp() * 1000)
        entry.short_call_position_id = f"DRY_{base_id}_SC"
        entry.long_call_position_id = f"DRY_{base_id}_LC"
        entry.short_put_position_id = f"DRY_{base_id}_SP"
        entry.long_put_position_id = f"DRY_{base_id}_LP"

        logger.info(
            f"[DRY RUN] Simulated Entry #{entry.entry_number}: "
            f"Real credits Call=${entry.call_spread_credit:.2f} Put=${entry.put_spread_credit:.2f} "
            f"UICs SC={entry.short_call_uic} LC={entry.long_call_uic} "
            f"SP={entry.short_put_uic} LP={entry.long_put_uic}"
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
            # ONE-SIDED SUPPORT (06-04 fix): place only the ACTIVE side(s). A side
            # is inactive when the HYDRA put_only/call_only flag is set, its
            # *_side_skipped flag is set, or its strikes are unset (0) — e.g. the
            # Brandon GEX adjuster zeroes the call strikes to route a put-only
            # entry. _simulate_entry already gates this way (line ~2094); the LIVE
            # path must match or it places a leg at strike 0.0 and aborts at leg 1
            # ("Placing Long Call at 0.0", the 06-04 incident). C is the only live
            # variant, so it was the only one exercising this path.
            put_only = getattr(entry, "put_only", False)
            call_only = getattr(entry, "call_only", False)
            call_active = (
                not put_only
                and bool(entry.short_call_strike) and bool(entry.long_call_strike)
                and not getattr(entry, "call_side_skipped", False)
            )
            put_active = (
                not call_only
                and bool(entry.short_put_strike) and bool(entry.long_put_strike)
                and not getattr(entry, "put_side_skipped", False)
            )
            if not call_active and not put_active:
                logger.error(
                    f"Entry #{entry.entry_number}: no active side to place "
                    f"(strikes C {entry.short_call_strike}/{entry.long_call_strike} "
                    f"P {entry.short_put_strike}/{entry.long_put_strike}, "
                    f"put_only={put_only} call_only={call_only})"
                )
                return False

            # Flag + zero-credit the side(s) we are NOT trading so monitoring,
            # settlement and the credit roll-up treat the entry as one-sided.
            if not call_active:
                entry.call_side_skipped = True
                entry.call_spread_credit = 0.0
            if not put_active:
                entry.put_side_skipped = True
                entry.put_spread_credit = 0.0

            long_call_debit = 0  # defined even when the call side is skipped
            long_put_debit = 0

            # 1. Long Call (buy protection first)
            if call_active:
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
                entry.long_call_fill_price = long_call_result.get("fill_price", 0)
                entry.long_call_mid_at_fill = long_call_result.get("mid_at_fill", 0)
                filled_legs.append(("long_call", entry.long_call_position_id, entry.long_call_uic))
                self._register_position(entry, "long_call")

            # 2. Long Put
            if put_active:
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
                entry.long_put_fill_price = long_put_result.get("fill_price", 0)
                entry.long_put_mid_at_fill = long_put_result.get("mid_at_fill", 0)
                filled_legs.append(("long_put", entry.long_put_position_id, entry.long_put_uic))
                self._register_position(entry, "long_put")

            # 3. Short Call (now we have the hedge)
            if call_active:
                logger.info(f"Placing Short Call at {entry.short_call_strike}")
                short_call_result = self._place_option_order(
                    strike=entry.short_call_strike,
                    put_call="Call",
                    buy_sell=BuySell.SELL,
                    expiry=expiry,
                    external_ref=f"{entry.strategy_id}_SC",
                    # net-credit floor vs the already-filled long call
                    paired_long_fill_per_share=entry.long_call_fill_price,
                )
                if not short_call_result:
                    raise Exception("Short Call order failed")
                entry.short_call_position_id = short_call_result.get("position_id")
                entry.short_call_uic = short_call_result.get("uic")
                entry.short_call_fill_price = short_call_result.get("fill_price", 0)
                entry.short_call_mid_at_fill = short_call_result.get("mid_at_fill", 0)
                # FIX (2026-02-04): Net credit = short credit - long debit (was only tracking short credit!)
                short_call_credit = short_call_result.get("credit", 0)
                entry.call_spread_credit = short_call_credit - long_call_debit
                logger.debug(f"Call spread: short ${short_call_credit:.2f} - long ${long_call_debit:.2f} = net ${entry.call_spread_credit:.2f}")
                filled_legs.append(("short_call", entry.short_call_position_id, entry.short_call_uic))
                self._register_position(entry, "short_call")

            # 4. Short Put
            if put_active:
                logger.info(f"Placing Short Put at {entry.short_put_strike}")
                short_put_result = self._place_option_order(
                    strike=entry.short_put_strike,
                    put_call="Put",
                    buy_sell=BuySell.SELL,
                    expiry=expiry,
                    external_ref=f"{entry.strategy_id}_SP",
                    # net-credit floor vs the already-filled long put
                    paired_long_fill_per_share=entry.long_put_fill_price,
                )
                if not short_put_result:
                    raise Exception("Short Put order failed")
                entry.short_put_position_id = short_put_result.get("position_id")
                entry.short_put_uic = short_put_result.get("uic")
                entry.short_put_fill_price = short_put_result.get("fill_price", 0)
                entry.short_put_mid_at_fill = short_put_result.get("mid_at_fill", 0)
                # FIX (2026-02-04): Net credit = short credit - long debit (was only tracking short credit!)
                short_put_credit = short_put_result.get("credit", 0)
                entry.put_spread_credit = short_put_credit - long_put_debit
                logger.debug(f"Put spread: short ${short_put_credit:.2f} - long ${long_put_debit:.2f} = net ${entry.put_spread_credit:.2f}")
                filled_legs.append(("short_put", entry.short_put_position_id, entry.short_put_uic))
                self._register_position(entry, "short_put")

            # FIX #70 Part A: Verify fill prices against PositionBase.OpenPrice
            # Activities endpoint may return limit price instead of actual execution price
            self._verify_entry_fill_prices(entry)

            # Set initial monitoring prices to fill prices so cushion calculation
            # works immediately (before first _update_entry_prices() heartbeat).
            # These get overwritten every 10s with current market mid prices.
            entry.short_call_price = entry.short_call_fill_price
            entry.long_call_price = entry.long_call_fill_price
            entry.short_put_price = entry.short_put_fill_price
            entry.long_put_price = entry.long_put_fill_price

            # GUARD-INVERT: reject + unwind a spread that legged into a net debit
            # (e.g. 2026-06-10 Entry #2 put side −$0.80/ct). Runs BEFORE booking.
            if not self._validate_realized_credit(
                entry, filled_legs,
                est_call_pc=getattr(entry, "_mkt011_est_call", None),
                est_put_pc=getattr(entry, "_mkt011_est_put", None),
            ):
                return False

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

            if has_naked_short and self.requires_protective_wings:
                logger.critical(f"NAKED SHORT DETECTED: {naked_short_info[0]}")
                self._handle_naked_short(naked_short_info)

            # Unwind filled legs
            self._unwind_partial_entry(filled_legs, entry)

            return False

    def _sell_credit_floor_price(
        self, buy_sell: BuySell, paired_long_fill_per_share: Optional[float]
    ) -> Optional[float]:
        """Per-share floor a SELL leg's limit must clear so the vertical stays a
        net credit: ``paired_long_fill + min_net_credit_per_contract``, rounded
        UP to the SPX tick (so a later round-DOWN of the limit can't dip under
        it). Returns ``None`` for BUY legs and for naked shorts (no paired long)
        — i.e. "no floor". Both inputs are PER-SHARE dollars.
        """
        if buy_sell != BuySell.SELL or paired_long_fill_per_share is None:
            return None
        return round_to_spx_tick(
            float(paired_long_fill_per_share) + self.min_net_credit_per_contract,
            round_up=True,
        )

    def _place_option_order(
        self,
        strike: float,
        put_call: str,
        buy_sell: BuySell,
        expiry: str,
        external_ref: str,
        emergency_mode: bool = False,
        paired_long_fill_per_share: Optional[float] = None,
    ) -> Optional[Dict]:
        """
        Place a single option order with progressive slippage retry.

        paired_long_fill_per_share: for a SHORT leg whose protective long already
        filled, the long's per-share fill price. Floors the SELL limit at
        (long + min_net_credit_per_contract) so the vertical can't leg into a net
        debit (2026-06-10 inversion fix). None for BUY legs and naked shorts.

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
        # SAFETY-DRY-01: Defense-in-depth dry-run gate.
        # Reaching this method in dry mode means an upstream gate failed —
        # _execute_entry should never be called when self.dry_run is True
        # (the fork at _initiate_entry routes to _simulate_entry instead).
        # This is a belt-and-braces check; if it ever fires, something is
        # wrong upstream — log loudly so it's visible in journalctl.
        if self.dry_run:
            logger.critical(
                f"[DRY RUN] SAFETY-DRY-01: _place_option_order called for "
                f"{put_call} {strike} ({buy_sell.value}) — NO real order will be placed. "
                f"This indicates an upstream gating bug; investigate immediately."
            )
            return None

        # F6.2 — places the leg via the IBClient write path.
        return self._place_option_order_ib(
            strike, put_call, buy_sell, expiry, external_ref,
            emergency_mode, paired_long_fill_per_share=paired_long_fill_per_share,
        )

    def _place_option_order_ib(
        self,
        strike: float,
        put_call: str,
        buy_sell: BuySell,
        expiry: str,
        external_ref: str,
        emergency_mode: bool = False,
        paired_long_fill_per_share: Optional[float] = None,
    ) -> Optional[Dict]:
        """IBKR path of :meth:`_place_option_order` (F6.2).

        Places ONE option leg with the same progressive-slippage retry
        sequence as the Saxo path — the strategy still owns "what retry
        level next"; `_place_leg_order` (F6.1) owns the place→poll-to-
        fill mechanics of one attempt. Spread guards (ORDER-005/006) are
        pure bid/ask math and carry over unchanged.

        Returns the same shape as the Saxo path —
        ``{position_id, uic, credit, debit, fill_price}`` on fill, None
        on total failure. ``position_id`` is None (IBKR has no per-leg
        id); ``uic`` carries the conid for the entry-object fields.

        Reached only when ``self.broker`` is set, past the SAFETY-DRY-01
        gate — the belt-and-braces dry-run check below is defense in
        depth, consistent with the v1.25 SAFETY-DRY philosophy.
        """
        if self.dry_run:
            logger.critical(
                "[DRY RUN] SAFETY-DRY-01b: _place_option_order_ib reached "
                "in dry mode — upstream gating bug; no order placed."
            )
            return None

        leg_description = f"{put_call} {strike}"

        # Resolve the conid via the broker-agnostic chain reader (F3.2).
        call_map, put_map = self._read_option_chain(expiry, [float(strike)])
        id_map = call_map if put_call == "Call" else put_map
        conid = id_map.get(float(strike)) or id_map.get(strike)
        if not conid:
            logger.error(
                f"_place_option_order_ib: no conid for {leg_description} "
                f"{expiry}"
            )
            return None

        # ORDER-006: order-size validation (broker-agnostic).
        is_valid, _err = self._validate_order_size(
            self.contracts_per_entry, leg_description
        )
        if not is_valid:
            logger.error(
                f"ORDER-006: order size validation failed for {leg_description}"
            )
            return None

        side = "BUY" if buy_sell == BuySell.BUY else "SELL"

        # ORDER-009b: fresh client-order-id base per placement invocation.
        # external_ref is stable per (day, entry, leg), so reusing it as the
        # cOID makes every progressive-slippage rung AND any same-day
        # unwind-and-re-enter collide on IBKR's server-side cOID dedup —
        # which silently rejected the retries/re-entries as duplicate orders.
        # A per-call nonce + the attempt index gives every distinct order
        # submission a unique cOID, while a network-level retry WITHIN a
        # single attempt still reuses that attempt's cOID (so a timed-out
        # place that secretly succeeded is still deduped, not double-filled).
        placement_nonce = uuid.uuid4().hex[:6]

        # ORDER-010 (fill-the-remainder): a multi-contract leg can fill in
        # pieces. Rather than ABORT the whole IC on the first partial — the
        # 2026-06-08 forensic's #1 entry-blocker, where a 2/7 partial unwound an
        # otherwise-fillable condor — we KEEP each filled chunk and re-place ONLY
        # the still-unfilled remainder up the progressive-slippage ladder until
        # the leg is whole. An accumulated partial is flattened ONLY as a last
        # resort (ambiguous placement / failed cancel / all rungs exhausted) so
        # we never carry an untracked naked 0DTE leg.
        target_qty = int(self.contracts_per_entry)
        filled_so_far = 0
        weighted_fill_sum = 0.0  # Σ (chunk fill_price × chunk qty) → blended avg
        first_fill_mid = None    # mid of the first filling chunk (slippage ref)
        bid = ask = spread = 0.0  # ORDER-DIAG: always defined for the failure log

        # Progressive retry sequence (same as the Saxo path).
        for attempt, (slippage_percent, is_market) in enumerate(
            PROGRESSIVE_RETRY_SEQUENCE
        ):
            attempt_coid = f"{external_ref}_{placement_nonce}{attempt}"
            remaining = target_qty - filled_so_far
            if remaining <= 0:
                break  # leg already whole (defensive — we return on completion)
            quote = self._read_option_quote(conid)
            if not quote:
                logger.warning(
                    f"  Attempt {attempt + 1}: no quote for {leg_description}"
                )
                time.sleep(0.5)
                continue

            # P7-audit L1: explicit None check distinguishes "no bid
            # field" from "bid=0.0" (legitimate worthless option). The
            # ORDER-006 branch below behaves identically for both (the
            # `if bid > 0:` guard sends both to the exempt path), but the
            # explicit form prevents a future refactor from accidentally
            # treating a real 0.0 bid as "missing data".
            _bid_raw = quote.get("bid")
            _ask_raw = quote.get("ask")
            bid = _bid_raw if _bid_raw is not None else 0
            ask = _ask_raw if _ask_raw is not None else 0
            spread = abs(ask - bid) if bid and ask else 0

            # ORDER-006: bid-ask spread guard (identical math to the Saxo path).
            if bid > 0:
                spread_percent = (spread / bid) * 100
                if bid <= ORDER_006_CHEAP_OPTION_THRESHOLD:
                    if spread_percent >= MAX_BID_ASK_SPREAD_PERCENT_SKIP:
                        logger.info(
                            f"  ORDER-006: cheap wing exempt "
                            f"({spread_percent:.0f}% spread, ${spread:.2f})"
                        )
                elif spread_percent >= MAX_BID_ASK_SPREAD_PERCENT_SKIP:
                    logger.warning(
                        f"  ORDER-006: spread {spread_percent:.1f}% too wide "
                        f"— skipping attempt"
                    )
                    continue
                elif spread_percent >= MAX_BID_ASK_SPREAD_PERCENT_WARNING:
                    logger.warning(
                        f"  ORDER-006: wide spread {spread_percent:.1f}%"
                    )

            mid_price = (bid + ask) / 2 if bid and ask else (ask or bid)

            # ORDER-DIAG (2026-06-25): the exact conid + the quote this attempt is
            # priced against, so a fill failure on a LIQUID book is diagnosable
            # (06-25 C long-call 0/7 even at MARKET while the strike showed
            # bid 0.20/ask 0.25 ×450 live → NOT liquidity; confirm the conid is the
            # live contract and compare the seen quote to the real market).
            logger.info(
                "  ORDER-DIAG %s a%d: conid=%s bid=$%.2f ask=$%.2f spread=$%.2f mid=$%.2f",
                leg_description, attempt + 1, conid, bid, ask, spread, mid_price,
            )

            # SELL-leg net-credit floor: a short vertical leg must clear at least
            # (paired long fill + min net credit), else the spread legs into a net
            # DEBIT (the 2026-06-10 Entry #2 inversion). None ⇒ no floor.
            sell_floor_ps = self._sell_credit_floor_price(
                buy_sell, paired_long_fill_per_share
            )

            if is_market:
                # GUARD-FLOOR: never MARKET-fill a floored SELL — a market order
                # can't honour the net-credit floor and could fill into a debit.
                # Skip it: the leg fails and the already-filled protective long is
                # unwound by the caller's except path. GUARD-INVERT is the
                # post-fill backstop for anything that still slips through.
                if sell_floor_ps is not None:
                    logger.warning(
                        f"  GUARD-FLOOR: skipping MARKET rung for SELL {leg_description} "
                        f"— cannot honour net-credit floor ${sell_floor_ps:.2f}; "
                        f"aborting leg (protective long will be unwound)"
                    )
                    continue
                # ORDER-005: absolute-spread guard before a MARKET order.
                if spread > self._max_absolute_slippage:
                    logger.critical(
                        f"  ORDER-005: spread ${spread:.2f} > max "
                        f"${self._max_absolute_slippage:.2f} — aborting MARKET "
                        f"for {leg_description}"
                    )
                    continue
                expected_price = mid_price
                logger.info(
                    f"  Attempt {attempt + 1}: MARKET order for {leg_description}"
                )
                result = self._place_leg_order(
                    instrument_id=conid, side=side,
                    quantity=remaining,
                    order_type="MKT", coid=attempt_coid,
                    ambiguous_on_timeout=True,  # L-H1: entry place — abort on transport-timeout
                )
            else:
                if slippage_percent > 0:
                    if buy_sell == BuySell.BUY:
                        limit_price = mid_price * (1 + slippage_percent / 100)
                    else:
                        limit_price = mid_price * (1 - slippage_percent / 100)
                else:
                    limit_price = mid_price
                limit_price = round_to_spx_tick(
                    limit_price, round_up=(buy_sell == BuySell.BUY)
                )
                # GUARD-FLOOR: keep the SELL at/above the net-credit floor so the
                # vertical stays a credit (the round above may have nudged it down).
                if sell_floor_ps is not None and limit_price < sell_floor_ps:
                    logger.info(
                        f"  GUARD-FLOOR: SELL {leg_description} limit ${limit_price:.2f} "
                        f"→ ${sell_floor_ps:.2f} (long ${paired_long_fill_per_share:.2f} "
                        f"+ ${self.min_net_credit_per_contract:.2f}/sh min credit)"
                    )
                    limit_price = sell_floor_ps
                expected_price = limit_price
                logger.info(
                    f"  Attempt {attempt + 1}: LIMIT @ ${limit_price:.2f} "
                    f"({slippage_percent}% slippage) for {leg_description}"
                )
                result = self._place_leg_order(
                    instrument_id=conid, side=side,
                    quantity=remaining,
                    order_type="LMT", limit_price=limit_price,
                    coid=attempt_coid,
                    ambiguous_on_timeout=True,  # L-H1: entry place — abort on transport-timeout
                )

            # ORDER double-execution guard: a placement whose live state could
            # NOT be confirmed (retryable failure during/after the POST + an
            # inconclusive cOID lookup) must ABORT the whole leg — NOT advance
            # to the next progressive rung, which uses a NEW cOID and would be
            # a genuinely new order (double-fill). The maybe-live order is left
            # for reconciliation; a CRITICAL alert already fired in
            # _place_leg_order.
            if result and result.get("ambiguous"):
                logger.critical(
                    f"  ORDER-DBL: aborting {leg_description} after an "
                    f"unconfirmed placement (coid={attempt_coid}) — will NOT "
                    f"retry under a new cOID; reconcile the possibly-open order."
                )
                # If PRIOR rungs already filled part of this leg, flatten that
                # confirmed partial so the abort never strands a naked leg.
                self._flatten_accumulated_partial(
                    conid, side, filled_so_far, external_ref,
                    f"{placement_nonce}flatamb", leg_description,
                )
                return None

            # Authoritative per-rung fill count + a HARD safety gate: we only
            # fold a rung's fills into the leg total (and continue to the next
            # rung) when that rung's order is CONFIRMED DEAD. A full fill is
            # terminal by definition; otherwise we cancel and require the cancel
            # to confirm "no longer working" before trusting the count — a fill
            # can race the poll timeout (ORDER-010 race fix), and re-placing
            # against a still-working order would double-fill (L-C3).
            if result and result.get("filled"):
                rung_filled = remaining  # order terminal-filled its full request
            else:
                snapshot = (result or {}).get("filled_quantity") or 0
                order_id = (result or {}).get("order_id")
                if not order_id:
                    rung_filled = 0  # nothing placed / nothing working
                else:
                    logger.info(
                        f"  Cancelling working order {order_id} "
                        f"({snapshot}/{remaining} filled) before next rung"
                    )
                    cancelled = self._cancel_order(order_id)
                    # Re-read the order's TERMINAL cumulative fill post-cancel:
                    # the remainder can fill between the last poll and the cancel
                    # taking effect (cancel is async). That count — not the
                    # pre-cancel snapshot — is authoritative.
                    terminal = self._extract_filled_quantity(
                        self._get_order_status(order_id)
                    )
                    confirmed = terminal if terminal is not None else snapshot
                    if cancelled:
                        # Order is dead — its fill count is final and safe to keep.
                        rung_filled = confirmed
                    else:
                        # L-C3 (a): cancel FAILED — the order may still be working.
                        # We must NOT trust this rung's fills or re-place against
                        # it (double-fill). Flatten the PRIOR confirmed partial
                        # (those orders were all confirmed dead), orphan this
                        # uncertain order, and abort the leg for reconciliation.
                        logger.critical(
                            f"  L-C3: cancel of order {order_id} on "
                            f"{leg_description} FAILED — order may still be "
                            f"working. Aborting leg (no re-place); flattening the "
                            f"prior {filled_so_far}-contract partial + flagging "
                            f"order {order_id} for reconciliation."
                        )
                        self._add_orphaned_order(order_id)
                        self._flatten_accumulated_partial(
                            conid, side, filled_so_far, external_ref,
                            f"{placement_nonce}flatlc", leg_description,
                        )
                        return None

            rung_filled = min(int(rung_filled or 0), remaining)

            if rung_filled > 0:
                fill_price = (result or {}).get("fill_price") or expected_price
                # ORDER-007: per-chunk fill-price slippage monitoring.
                self._monitor_fill_slippage(
                    expected_price=expected_price,
                    actual_fill_price=fill_price,
                    buy_sell=buy_sell,
                    leg_description=leg_description,
                )
                weighted_fill_sum += fill_price * rung_filled
                filled_so_far += rung_filled
                if first_fill_mid is None:
                    first_fill_mid = mid_price

                if filled_so_far >= target_qty:
                    # LEG WHOLE — return the blended average across all chunks.
                    avg_fill = weighted_fill_sum / filled_so_far
                    logger.info(
                        f"  ✓ Filled {leg_description} "
                        f"{filled_so_far}/{target_qty} @ avg ${avg_fill:.2f}"
                    )
                    qty_mult = 100 * target_qty
                    return {
                        "position_id": None,  # IBKR has no per-leg position id
                        "uic": conid,
                        "credit": avg_fill * qty_mult if buy_sell == BuySell.SELL else 0,
                        "debit": avg_fill * qty_mult if buy_sell == BuySell.BUY else 0,
                        "fill_price": avg_fill,
                        # MID of the FIRST filling chunk (the slippage reference).
                        # Real entry slippage = fill - mid; persisted per leg so
                        # the DB shows live execution quality.
                        "mid_at_fill": first_fill_mid,
                    }

                logger.warning(
                    f"  ORDER-010: partial {filled_so_far}/{target_qty} on "
                    f"{leg_description} — escalating to fill the remainder"
                )

            # Delay before the next retry level.
            if attempt < len(PROGRESSIVE_RETRY_SEQUENCE) - 1:
                if rung_filled <= 0:
                    logger.warning(
                        f"  ⚠ {leg_description} not filled "
                        f"({filled_so_far}/{target_qty}) — retrying "
                        f"(ORDER-009 {ORDER_RETRY_DELAY_SECONDS}s delay)..."
                    )
                time.sleep(ORDER_RETRY_DELAY_SECONDS)

        # All rungs exhausted. If we accumulated a PARTIAL leg we could not
        # complete, flatten it now — a residual naked 0DTE leg is the one
        # outcome we must never leave (filled_so_far == 0 → nothing to flatten).
        if filled_so_far > 0:
            logger.critical(
                f"  ORDER-010: {leg_description} only filled "
                f"{filled_so_far}/{target_qty} after all "
                f"{len(PROGRESSIVE_RETRY_SEQUENCE)} rungs — flattening the "
                f"partial to avoid an untracked naked leg"
            )
            self._flatten_accumulated_partial(
                conid, side, filled_so_far, external_ref,
                f"{placement_nonce}flatexh", leg_description,
            )
            return None

        logger.error(
            f"  ✗ {leg_description} failed all "
            f"{len(PROGRESSIVE_RETRY_SEQUENCE)} attempts (IBKR) "
            f"— conid={conid}, last quote bid=${bid:.2f}/ask=${ask:.2f}. "
            f"If the conid is the live contract and bid/ask look fillable, this is "
            f"order-routing/transient, NOT liquidity (see ORDER-DIAG lines above)."
        )
        return None

    def _flatten_accumulated_partial(
        self, conid, side: str, filled_qty: int, external_ref: str,
        coid_suffix: str, leg_description: str,
    ) -> None:
        """ORDER-010 last resort: market-close an accumulated partial leg.

        When the fill-the-remainder loop aborts (ambiguous placement, a failed
        cancel with unconfirmed fill, or all rungs exhausted) while some
        contracts of this leg ARE confirmed filled, those contracts are a naked
        0DTE leg. Flatten them with an opposite MARKET order so the abort starts
        the IC builder's unwind from a clean slate. ``filled_qty`` MUST be the
        CONFIRMED-dead count (orders whose cancel returned True / that fully
        filled) — never an uncertain still-working order, which is orphaned for
        POS-003 reconciliation instead. A failed flatten is flagged for manual
        intervention + the orphan sweep.
        """
        if filled_qty <= 0:
            return
        close_side = "SELL" if side == "BUY" else "BUY"
        flat = self._place_leg_order(
            instrument_id=conid, side=close_side, quantity=int(filled_qty),
            order_type="MKT", coid=f"{external_ref}_{coid_suffix}",
        )
        if not (flat and flat.get("filled")):
            logger.critical(
                f"  ORDER-010: FAILED to flatten the {filled_qty}-contract "
                f"partial on {leg_description} — MANUAL INTERVENTION REQUIRED"
            )
            self._add_orphaned_order(f"PARTIAL_{conid}")

    @staticmethod
    def _extract_filled_quantity(status: dict) -> Optional[int]:
        """ORDER-010: best-effort cumulative filled quantity from an order
        status dict.

        ``_get_order_status`` returns IBKR's raw order-status dict, whose
        filled-quantity field name varies by endpoint (snake_case vs
        camelCase, ``cum_fill`` on the per-order status endpoint). Mirror the
        defensive lookup in :func:`shared.ib_client._build_fill_result_dict`.

        Returns the int filled quantity, or ``None`` when no usable quantity
        field is present (empty/failed status) so the caller can fail closed
        (orphan + manual review) instead of trusting a stale snapshot.
        """
        if not status:
            return None
        for key in ("filled_quantity", "filledQuantity", "cum_fill", "filled"):
            if key in status and status[key] is not None and status[key] != "":
                try:
                    return int(float(status[key]))
                except (TypeError, ValueError):
                    continue
        return None

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
            open_orders = self._get_open_orders()
            open_order_ids = {
                str(o.get("orderId") or o.get("order_id") or o.get("OrderId"))
                for o in (open_orders or [])
            }
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
                    cancel_result = self._cancel_order(order_id)
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
        if amount > self.max_contracts_per_order:
            error = (
                f"ORDER-006 REJECTED: Order size {amount} contracts exceeds "
                f"max {self.max_contracts_per_order}. Order: {order_description}"
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

        if projected_size > self.max_contracts_per_underlying:
            error = (
                f"ORDER-006 REJECTED: Position limit exceeded. "
                f"Current={current_position_size}, Adding={amount}, "
                f"Projected={projected_size}, Max={self.max_contracts_per_underlying}"
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
            # Count each open leg by its conid (`*_uic`). P7-audit H9:
            # IBKR has no per-leg position id — `*_position_id` is always
            # None — so gating on it counted 0 and silently disabled the
            # ORDER-006 max_contracts_per_underlying cap. Works for both
            # complete and partial entries.
            if entry.short_call_uic:
                total += self.contracts_per_entry
            if entry.long_call_uic:
                total += self.contracts_per_entry
            if entry.short_put_uic:
                total += self.contracts_per_entry
            if entry.long_put_uic:
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
            Option instrument id (IBKR conid) or None.
        """
        # F7.6 — resolves the conid through _read_option_chain (F3.2).
        call_map, put_map = self._read_option_chain(expiry, [float(strike)])
        id_map = call_map if put_call == "Call" else put_map
        return id_map.get(float(strike)) or id_map.get(strike)

    def _verify_entry_fill_prices(self, entry: IronCondorEntry) -> None:
        """
        FIX #70 Part A: Verify entry fill prices against PositionBase.OpenPrice.

        After all legs are placed, the activities endpoint may have returned the
        submitted limit price rather than the actual execution price (when Saxo
        gives price improvement). PositionBase.OpenPrice always has the real
        execution price.

        This method calls get_positions() once, compares OpenPrice against
        recorded credits, and updates credits if price improvement is detected.

        Non-critical: if verification fails, original prices are kept.

        Args:
            entry: IronCondorEntry with all legs filled and position IDs set
        """
        try:
            # GAP-G (F7.7): IBKR keys the price lookup by conid (str) and
            # the legs by *_uic — IBKR has no per-leg position id, and
            # `avg_cost` is the actual execution price.
            #
            # P7-audit M6: the prior `if self.broker is not None:` guard
            # was vacuous (HYDRA always has a broker on the IBKR path) AND
            # buggy — `legs` / `price_lookup` were scoped inside the guard
            # but referenced outside, so a False guard would NameError. The
            # guard is now flattened.
            positions = self._read_open_positions()
            if not positions:
                logger.warning("FIX-70: Could not fetch positions for entry price verification")
                return
            price_lookup = {
                str(p["instrument_id"]): p["avg_cost"]
                for p in positions
                if p.get("instrument_id") is not None
                and (p.get("avg_cost") or 0) > 0
            }
            legs = [
                ("short_call_uic", "short_call"),
                ("long_call_uic", "long_call"),
                ("short_put_uic", "short_put"),
                ("long_put_uic", "long_put"),
            ]
            corrections = {}  # leg_name -> actual_price from PositionBase.OpenPrice

            for pos_attr, leg_name in legs:
                pos_id = getattr(entry, pos_attr, None)
                if not pos_id:
                    continue

                actual_price = price_lookup.get(str(pos_id))
                if actual_price is None:
                    continue

                corrections[leg_name] = actual_price

            # Recalculate call spread credit if we have corrections for call legs
            if "short_call" in corrections or "long_call" in corrections:
                short_call_price = corrections.get("short_call")
                long_call_price = corrections.get("long_call")

                if short_call_price is not None and long_call_price is not None:
                    new_call_credit = (short_call_price - long_call_price) * 100 * entry.contracts
                    old_call_credit = entry.call_spread_credit

                    if abs(new_call_credit - old_call_credit) > 0.01:
                        logger.info(
                            f"FIX-70: Price improvement detected on Entry #{entry.entry_number} CALL side: "
                            f"credit ${old_call_credit:.2f} -> ${new_call_credit:.2f} "
                            f"(short={short_call_price:.2f}, long={long_call_price:.2f})"
                        )
                        entry.call_spread_credit = new_call_credit
                    # Update fill prices with verified values
                    entry.short_call_fill_price = short_call_price
                    entry.long_call_fill_price = long_call_price

            # Recalculate put spread credit if we have corrections for put legs
            if "short_put" in corrections or "long_put" in corrections:
                short_put_price = corrections.get("short_put")
                long_put_price = corrections.get("long_put")

                if short_put_price is not None and long_put_price is not None:
                    new_put_credit = (short_put_price - long_put_price) * 100 * entry.contracts
                    old_put_credit = entry.put_spread_credit

                    if abs(new_put_credit - old_put_credit) > 0.01:
                        logger.info(
                            f"FIX-70: Price improvement detected on Entry #{entry.entry_number} PUT side: "
                            f"credit ${old_put_credit:.2f} -> ${new_put_credit:.2f} "
                            f"(short={short_put_price:.2f}, long={long_put_price:.2f})"
                        )
                        entry.put_spread_credit = new_put_credit
                    # Update fill prices with verified values
                    entry.short_put_fill_price = short_put_price
                    entry.long_put_fill_price = long_put_price

            logger.info(f"FIX-70: Entry #{entry.entry_number} price verification complete")

        except Exception as e:
            logger.warning(f"FIX-70: Entry price verification failed (non-critical): {e}")

    def _get_todays_expiry(self) -> Optional[str]:
        """Target option expiry as a YYYY-MM-DD string.

        Default (target_dte == 0) returns today's date — 0DTE, byte-identical
        to the prior hardcoded behavior. A positive target_dte (modularity-audit
        item 2) shifts the expiry forward by that many trading days (weekends
        skipped), enabling non-0DTE strategies. NOTE: weekday-only — exchange
        holidays are not yet skipped; a holiday-aware calendar is a future
        refinement (tracked in docs/PR_SCOPE_LEG_INSTRUMENT.md). The method name
        is retained (not _get_target_expiry) to avoid churning its 11 call sites.
        """
        base = get_us_market_time()
        dte = getattr(self, "target_dte", 0)
        if dte <= 0:
            return base.strftime("%Y-%m-%d")
        d = base
        added = 0
        while added < dte:
            d = d + timedelta(days=1)
            if d.weekday() < 5:  # Mon–Fri are trading days
                added += 1
        return d.strftime("%Y-%m-%d")

    def _alert_settlement_deferred(self) -> None:
        """Once-per-ET-day CRITICAL alert that settlement booking is deferred
        because the post-close SPX read failed (S-HIGH-3).

        Positions are NOT mis-booked — they wait for a readable SPX and the next
        heartbeat retries — but a persistent failure needs an operator to
        reconcile against the IBKR Option Cash Settlements report (cash posts T+1).
        """
        try:
            today = get_us_market_time().date()
        except Exception:
            today = None
        if getattr(self, "_settlement_deferred_alert_date", None) == today:
            return
        self._settlement_deferred_alert_date = today
        try:
            self.alert_service.send_alert(
                alert_type=AlertType.CRITICAL_INTERVENTION,
                title="Settlement booking DEFERRED — SPX unreadable at settlement",
                message=(
                    "Post-close SPX read failed, so settlement P&L booking is "
                    "deferred to avoid mis-booking an ITM-settled short as a "
                    "full-credit profit. Retrying each heartbeat. If this persists, "
                    "reconcile manually against the IBKR Option Cash Settlements report."
                ),
                priority=AlertPriority.CRITICAL,
                details={"bot": getattr(self, "BOT_NAME", "?")},
            )
        except Exception as exc:  # pragma: no cover - alert is best-effort
            logger.debug(f"settlement-deferred alert send failed (non-fatal): {exc}")

    def _alert_short_close_failed(self, entry, side_name: str) -> None:
        """B2: alert (once per entry/side per ET day) that a side's SHORT buy-back
        failed during an early close, so the long close was aborted to keep the
        defined-risk spread intact. No naked short exists — the full spread is
        still on and monitored — but the intended TP/breach close did not complete.
        """
        key = (getattr(entry, "entry_number", "?"), side_name)
        seen = getattr(self, "_b2_short_fail_alerted", None)
        if seen is None:
            seen = set()
            self._b2_short_fail_alerted = seen
        if key in seen:
            return
        seen.add(key)
        try:
            self.alert_service.send_alert(
                alert_type=AlertType.STOP_LOSS,
                title=f"Short close FAILED (hedge preserved) — E#{getattr(entry, 'entry_number', '?')} {side_name}",
                message=(
                    f"The {side_name} short buy-back failed during an early close; the "
                    f"long close was aborted to avoid a naked short. The full defined-risk "
                    f"spread is still on and monitored, and the close will retry. Investigate "
                    f"if it recurs."
                ),
                priority=AlertPriority.HIGH,
                details={"bot": getattr(self, "BOT_NAME", "?"), "entry": getattr(entry, "entry_number", "?")},
            )
        except Exception as exc:  # pragma: no cover - best-effort
            logger.debug(f"short-close-failed alert send failed (non-fatal): {exc}")

    def _handle_naked_short(self, naked_info: Tuple[str, str, int]):
        """
        Handle a naked short position - CRITICAL SAFETY.

        Must either hedge or close within NAKED_POSITION_MAX_AGE_SECONDS.

        Args:
            naked_info: Tuple of (leg_name, position_id, uic)
        """
        leg_name, pos_id, uic = naked_info

        # Item 6a: strategies that don't require protective wings (e.g. a short
        # strangle) hold naked shorts BY DESIGN — an unhedged short is the
        # position, not an emergency, so it must NOT be emergency-closed. The
        # iron-condor family keeps requires_protective_wings=True → no-op here.
        if not getattr(self, "requires_protective_wings", True):
            logger.info(
                f"_handle_naked_short skipped for {leg_name}: strategy is "
                f"undefined-risk (requires_protective_wings=False) — naked "
                f"shorts are held by design, not emergency-closed."
            )
            return

        # SAFETY-DRY-02: Defense-in-depth dry-run gate.
        # Naked-short detection happens after a leg "fills" during entry
        # placement. In dry mode, _simulate_entry doesn't place real orders
        # so naked shorts can't actually exist — but if a recovery/state-
        # restore path ever surfaces a synthetic naked short, we must NOT
        # send a real BUY-to-close to Saxo against an SPX option we don't
        # own. Log and return.
        if self.dry_run:
            logger.critical(
                f"[DRY RUN] SAFETY-DRY-02: _handle_naked_short called for {leg_name} "
                f"(pos={pos_id}) — NO real close order placed. Defense-in-depth: "
                f"naked-short detection should not surface in dry mode."
            )
            return

        logger.critical(f"HANDLING NAKED SHORT: {leg_name} position {pos_id}")

        # Log safety event for audit trail
        self._log_safety_event("NAKED_SHORT_DETECTED", f"{leg_name} position {pos_id}")

        self.alert_service.send_alert(
            alert_type=AlertType.CIRCUIT_BREAKER,
            title="NAKED SHORT DETECTED",
            message=f"NAKED SHORT: {leg_name} (position {pos_id}) - closing immediately",
            priority=AlertPriority.CRITICAL
        )

        # Attempt to close the naked short using a market order.
        # Naked shorts need BUY to close.
        try:
            # F6.4 — closes the leg via _close_leg_order (IBClient).
            # `result` is normalized to a {"OrderId": …}/None shape so
            # the downstream registry + logging logic is unchanged.
            #
            # P7-audit M6: the prior `if self.broker is not None:` guard
            # was vacuous (HYDRA always has a broker on the IBKR path)
            # AND latently buggy — `result` was only defined when the
            # guard was True, so a False guard would have raised
            # NameError at the `if result:` check below. Flattened.
            _res = self._close_leg_order(
                instrument_id=uic, side="BUY",
                quantity=self.contracts_per_entry,
            )
            result = (
                {"OrderId": _res.get("order_id")}
                if _res.get("filled") else None
            )
            if result:
                logger.info(f"Closed naked short {pos_id} via order {result.get('OrderId')}")
                try:
                    self.registry.unregister(pos_id)
                except Exception as reg_e:
                    logger.error(f"Registry error unregistering {pos_id}: {reg_e}")
                self._log_safety_event("NAKED_SHORT_CLOSED", f"{leg_name} position {pos_id} closed successfully", "Closed")
            else:
                # Audit fix: a non-full / timed-out market BUY-to-close leaves the
                # order WORKING in IBKR's book (place_and_wait_for_fill documents
                # this) — it could fill late and double-close. Cancel the working
                # remainder BEFORE escalating so we don't leave an unmanaged order
                # on the highest-risk naked-short state (mirrors the cancel in
                # _close_position_with_retry_ib).
                leftover_id = _res.get("order_id") if _res else None
                if leftover_id:
                    logger.warning(
                        f"  Cancelling unfilled naked-short close order "
                        f"{leftover_id} before escalating"
                    )
                    self._cancel_order(leftover_id)
                logger.critical(f"FAILED to close naked short {pos_id}!")
                self._log_safety_event("NAKED_SHORT_CLOSE_FAILED", f"{leg_name} position {pos_id} - close returned false", "Failed")
                self._trigger_critical_intervention(f"Cannot close naked short {pos_id}")
        except Exception as e:
            logger.critical(f"Exception closing naked short: {e}")
            self._log_safety_event("NAKED_SHORT_EXCEPTION", f"{leg_name} position {pos_id} - {str(e)}", "Exception")
            self._trigger_critical_intervention(f"Exception closing naked short: {e}")

    def _validate_realized_credit(
        self,
        entry: IronCondorEntry,
        filled_legs: List[Tuple],
        est_call_pc: Optional[float] = None,
        est_put_pc: Optional[float] = None,
    ) -> bool:
        """GUARD-INVERT (2026-06-10): reject an entry whose REALIZED per-side
        credit filled at a net DEBIT (or, optionally, collapsed far below the
        MKT-011 estimate), then unwind the just-filled spread.

        Entry legging places each leg as a separate order over several seconds;
        on a fast move the sequential fills can INVERT a vertical. 2026-06-10
        Entry #2 (variant C) sold its short put @$8.80 *after* buying the long
        put @$9.60 (32s apart, falling tape) → net −$0.80/contract (−$560), a
        debit IC the bot silently booked as a structural loser. This guard runs
        after the last fill and BEFORE the entry is booked.

        Rules (per ACTIVE side; a positive total can mask a one-sided inversion):
          Rule 1 (always on): realized credit/contract < ``min_realized_credit_per_contract``
                              (default $0.00 — a credit vertical must collect a credit).
          Rule 2 (opt-in):    realized < ``credit_realized_fraction`` × MKT-011 estimate
                              (default fraction 0.0 = OFF; needs a stashed estimate).

        On a violation, ``action`` (config ``strategy.realized_credit_guard.action``,
        default ``unwind``) decides: ``unwind`` closes the just-filled legs
        (SHORTS first, to avoid a transient naked short while unwinding a fully
        filled IC) via the existing :meth:`_unwind_partial_entry` and returns
        False; ``alert_only`` books the entry but fires the CRITICAL alert;
        ``off`` restores prior behavior. Forward-only + dry-run-safe: it never
        touches an already-open prior entry and is a no-op in ``dry_run`` (sim
        credit comes from the positive estimate and cannot invert).

        Returns True to ACCEPT the entry, False to REJECT (unwound).
        """
        if self.dry_run:
            return True
        contracts = getattr(entry, "contracts", 0) or 0
        if contracts <= 0:
            return True

        cfg = (getattr(self, "strategy_config", None) or {}).get("realized_credit_guard", {}) or {}
        action = str(cfg.get("action", "unwind")).lower()
        if action == "off":
            return True
        min_realized_pc = float(cfg.get("min_realized_credit_per_contract", 0.0))
        # Rule 2 OFF by default (0.0) — Rule 1 alone catches the incident with
        # zero false-positive risk. Raise this in config to enable Rule 2.
        realized_fraction = float(cfg.get("credit_realized_fraction", 0.0))

        EPS = 1e-9
        violations = []  # (side, realized_pc, reason)

        def _check(side: str, total_credit: float, est_pc: Optional[float]) -> None:
            realized_pc = (total_credit or 0.0) / (100.0 * contracts)
            if realized_pc < min_realized_pc - EPS:  # Rule 1 — hard inversion / non-credit
                violations.append(
                    (side, realized_pc,
                     f"{side} realized ${realized_pc:.2f}/ct < floor ${min_realized_pc:.2f}/ct")
                )
                return
            if realized_fraction > 0.0 and est_pc is not None:  # Rule 2 — estimate collapse
                est_dollars_pc = est_pc / 100.0
                if est_dollars_pc > 0 and realized_pc < (realized_fraction * est_dollars_pc) - EPS:
                    violations.append(
                        (side, realized_pc,
                         f"{side} realized ${realized_pc:.2f}/ct < {realized_fraction:.0%} of "
                         f"est ${est_dollars_pc:.2f}/ct")
                    )

        # Active side = its short leg actually filled (short_*_uic set) and not skipped.
        if getattr(entry, "short_call_uic", None) and not getattr(entry, "call_side_skipped", False):
            _check("call", getattr(entry, "call_spread_credit", 0) or 0, est_call_pc)
        if getattr(entry, "short_put_uic", None) and not getattr(entry, "put_side_skipped", False):
            _check("put", getattr(entry, "put_spread_credit", 0) or 0, est_put_pc)

        if not violations:
            return True

        detail = "; ".join(r for _, _, r in violations)
        logger.critical(
            f"GUARD-INVERT: Entry #{getattr(entry, 'entry_number', '?')} realized-credit "
            f"violation ({detail}); action={action}"
        )
        try:
            self.alert_service.send_alert(
                alert_type=AlertType.CIRCUIT_BREAKER,
                title="ENTRY CREDIT INVERSION",
                message=(
                    f"Entry #{getattr(entry, 'entry_number', '?')} filled at a non-credit / "
                    f"inverted price ({detail}). Action: {action}."
                ),
                priority=AlertPriority.CRITICAL,
                contracts=contracts,
            )
        except Exception as alert_e:
            logger.error(f"GUARD-INVERT alert failed: {alert_e}")
        self._log_safety_event("ENTRY_CREDIT_INVERSION", detail)

        if action == "alert_only":
            return True  # operator chose to keep the position; flagged loudly above

        # Default: unwind. Close SHORTS first (cover the risk) then longs — only
        # reorders the unwind sequence; placement order (longs-first, ORDER-002)
        # is unchanged.
        ordered = sorted(filled_legs, key=lambda l: 0 if str(l[0]).startswith("short") else 1)
        logger.critical(
            f"GUARD-INVERT: unwinding {len(ordered)} filled legs (shorts-first) for "
            f"Entry #{getattr(entry, 'entry_number', '?')}"
        )
        self._unwind_partial_entry(ordered, entry)
        return False

    def _unwind_partial_entry(self, filled_legs: List[Tuple], entry: IronCondorEntry):
        """
        Unwind partially filled legs on entry failure.

        Args:
            filled_legs: List of (leg_name, position_id, uic) tuples
            entry: The entry being unwound
        """
        # SAFETY-DRY-03: Defense-in-depth dry-run gate.
        # _unwind_partial_entry is invoked from _execute_entry after one or
        # more legs filled. In dry mode _execute_entry should never be
        # reached — _simulate_entry is the dry-mode fork. If we somehow get
        # here, do NOT send real close orders against SPX options that
        # don't actually exist on Saxo.
        if self.dry_run:
            logger.critical(
                f"[DRY RUN] SAFETY-DRY-03: _unwind_partial_entry called for "
                f"{len(filled_legs)} legs (entry #{entry.entry_number}) — NO real "
                f"close orders placed. Investigate why _execute_entry was reached."
            )
            return

        logger.warning(f"Unwinding {len(filled_legs)} partially filled legs")

        for leg_name, pos_id, uic in filled_legs:
            # P7-audit H1: gate on the conid (`uic`), NOT `pos_id` — IBKR
            # has no per-leg position id so `pos_id` is always None, which
            # made this rollback a silent no-op (a partially-filled IC
            # left open and untracked).
            if uic:
                try:
                    # Market-order close. short → BUY to close, long → SELL.
                    # v8: use entry.contracts (== config at placement).
                    # F6.4 — closes the leg via _close_leg_order (IBClient);
                    # `result` normalized to a {"OrderId": …}/None shape.
                    _res = self._close_leg_order(
                        instrument_id=uic,
                        side=("BUY" if leg_name.startswith("short")
                              else "SELL"),
                        quantity=entry.contracts,
                    )
                    result = (
                        {"OrderId": _res.get("order_id")}
                        if _res.get("filled") else None
                    )
                    if result:
                        try:
                            self.registry.unregister(pos_id)
                        except Exception as reg_e:
                            logger.error(f"Registry error unregistering {pos_id}: {reg_e}")
                        logger.info(f"Unwound {leg_name}: {pos_id} via order {result.get('OrderId')}")
                    else:
                        # Audit fix: a non-full / timed-out market close leaves the
                        # order WORKING and able to fill late (double-close).
                        # Cancel the leftover before giving up so we don't leave an
                        # unmanaged order on a partially-filled IC unwind.
                        leftover_id = _res.get("order_id") if _res else None
                        if leftover_id:
                            self._cancel_order(leftover_id)
                        logger.error(f"Failed to unwind {leg_name}: no result from close order")
                except Exception as e:
                    logger.error(f"Failed to unwind {leg_name}: {e}")

    # =========================================================================
    # STOP LOSS MONITORING
    # =========================================================================

    def _book_realized_pnl(self, amount: float, entry=None) -> None:
        """Book ``amount`` to the day's realized-P&L aggregate AND mirror it onto
        the specific entry's ``realized_pnl`` (per-entry attribution).

        The aggregate booking is IDENTICAL to the old direct
        ``daily_state.total_realized_pnl += amount`` — this method never changes
        the day total; it only ADDS per-entry attribution so ``sum(e.realized_pnl)``
        equals ``daily_state.total_realized_pnl`` by construction. Every close
        path that books a specific entry's realized P&L routes through here (the
        aggregate-only expiry sweep books per entry at its own call sites, then
        the sum into the aggregate). A settlement reconciliation guard logs any
        drift, which would flag a booking site that forgot to pass ``entry``.
        Introduced so the slot_edge analyzer has a trustworthy per-entry P&L —
        the Brandon variants recorded it nowhere before (only the daily total).
        """
        self.daily_state.total_realized_pnl += amount
        if entry is not None:
            entry.realized_pnl = getattr(entry, "realized_pnl", 0.0) + amount

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

        stop_time = get_us_market_time().isoformat()

        # CRITICAL #7: capture whether the other side was already stopped
        # BEFORE we mark this one, so a fail-closed revert (short close failed)
        # can undo BOTH this side's stopped flag and the double-stop counter
        # without corrupting the case where the other side was genuinely
        # stopped earlier.
        other_side_already_stopped = (
            entry.put_side_stopped if side == "call" else entry.call_side_stopped
        )

        if side == "call":
            entry.call_side_stopped = True
            entry.call_stop_time = stop_time
            self.daily_state.call_stops_triggered += 1
            # EMERGENCY-001: Include UICs for spread checking during close
            positions_to_close = [
                (entry.short_call_position_id, "short_call", entry.short_call_uic),
                (entry.long_call_position_id, "long_call", entry.long_call_uic)
            ]
            stop_level = entry.call_side_stop
            short_uic_for_side = entry.short_call_uic
        else:
            entry.put_side_stopped = True
            entry.put_stop_time = stop_time
            self.daily_state.put_stops_triggered += 1
            # EMERGENCY-001: Include UICs for spread checking during close
            positions_to_close = [
                (entry.short_put_position_id, "short_put", entry.short_put_uic),
                (entry.long_put_position_id, "long_put", entry.long_put_uic)
            ]
            stop_level = entry.put_side_stop
            short_uic_for_side = entry.short_put_uic

        # Check for double stop
        double_stop_counted = False
        if entry.call_side_stopped and entry.put_side_stopped:
            self.daily_state.double_stops += 1
            double_stop_counted = True
            logger.warning(f"DOUBLE STOP on Entry #{entry.entry_number}")

        # FIX #42 (2026-02-05): Track actual fill prices for accurate P&L calculation
        # Previous bug: Used theoretical stop_level instead of actual close cost
        # This caused P&L to show wrong values (even profits instead of losses!)
        actual_close_cost = 0.0  # Total cost to close the spread (in dollars)
        fill_prices_captured = True  # Track if we got actual prices
        deferred_legs = []  # FIX #75: Moved before dry_run check for async access

        # CRITICAL #7: track per-leg close outcomes so we only clear the
        # short leg's tracking (uic / position id) and book the loss when the
        # SHORT close actually succeeded. A breached 0DTE short whose buy-back
        # fails MUST stay tracked + retryable (fail closed) — never null its
        # uic, never mark it stopped-and-done, never book a theoretical loss as
        # if it were flat. Defaults below cover the dry-run path (no real
        # short leg exists) and the no-short-leg case (treated as "succeeded"
        # so the long leg can still clear normally).
        short_leg_present = bool(short_uic_for_side)
        short_close_succeeded = not short_leg_present  # vacuously true if no short

        if self.dry_run:
            # Dry run never places a real close — treat the short as closed so
            # the existing simulate-and-clear behavior is unchanged.
            short_close_succeeded = True
            logger.info(f"[DRY RUN] Would close {side} side of Entry #{entry.entry_number}")
            # Path-B realism: estimate close cost from current real bid/ask.
            # Close-both semantics: BUY back short at ask, SELL long at bid.
            # That's worst-case market-order fill on the spread.
            short_ask = getattr(entry, f"short_{side}_ask", None)
            long_bid = getattr(entry, f"long_{side}_bid", None)
            short_price = getattr(entry, f"short_{side}_price", 0)
            long_price = getattr(entry, f"long_{side}_price", 0)
            if short_ask and short_ask > 0 and long_bid is not None:
                # Real bid/ask available — use worst-case spread fill
                long_bid_safe = long_bid if long_bid is not None else 0
                actual_close_cost = (short_ask - long_bid_safe) * 100 * entry.contracts
                logger.info(f"[DRY RUN] Close-both spread fill: short_ask=${short_ask:.2f} - long_bid=${long_bid_safe:.2f} → cost ${actual_close_cost:.0f}")
            elif short_price > 0:
                # Fallback: spread mid × 1.10 slippage estimate
                spread_mid = max(0, short_price - long_price)
                actual_close_cost = spread_mid * 1.10 * 100 * entry.contracts
                logger.info(f"[DRY RUN] No bid/ask, using spread mid×1.10=${spread_mid*1.10:.2f} → cost ${actual_close_cost:.0f}")
            else:
                actual_close_cost = stop_level
                logger.info(f"[DRY RUN] No price data, using theoretical stop level ${stop_level:.0f}")
        else:
            # EMERGENCY-001: Close positions with enhanced retry logic and spread validation
            # FIX #42: Collect fill prices from each leg
            # Fix #45: Pass entry_number for merged position handling
            # FIX #70: Track deferred legs for batch price lookup after both legs close
            legs_actually_closed = 0  # Fix #83a: Track for accurate commission

            for pos_id, leg_name, uic in positions_to_close:
                # P7-audit C1: gate on the conid (`uic`), NOT `pos_id`.
                # IBKR has no per-leg position id — `pos_id` is always
                # None — so an `if pos_id:` gate skipped every close,
                # leaving the breached short open AND booking the stop
                # as a profit. `_close_position_with_retry` keys on `uic`.
                if uic:
                    # Fix #83a: Skip closing worthless long legs (bid=$0) on 0DTE
                    # Deep OTM longs often have no market — Saxo rejects market orders
                    # with "only limit orders allowed" and limit orders at $0.05 fail too.
                    # These expire worthless at 4 PM, so closing wastes API calls for ~$0.
                    if leg_name.startswith("long") and uic:
                        try:
                            quote = self._read_option_quote(uic)
                            bid = 0
                            if quote:
                                bid = quote.get("bid", 0) or 0
                            if bid <= 0:
                                logger.info(
                                    f"  Fix #83a: Skipping {leg_name} close for Entry #{entry.entry_number} "
                                    f"(bid=${bid:.2f}, expires worthless on 0DTE)"
                                )
                                # Long leg expires worthless — no P&L impact, no commission
                                continue
                        except Exception as e:
                            logger.warning(f"  Fix #83a: Quote check failed for {leg_name}: {e}, proceeding with close")

                    # v8: pass entry.contracts so the close order size matches what was
                    # actually opened, not current config (catches mid-day flip scenario).
                    success, fill_price, order_id = self._close_position_with_retry(
                        pos_id, leg_name, uic=uic, entry_number=entry.entry_number,
                        contracts=entry.contracts,
                    )
                    if success:
                        legs_actually_closed += 1
                    # CRITICAL #7: record the short-leg outcome specifically.
                    # The short is the naked-risk leg; its close MUST succeed
                    # before we forget it.
                    if leg_name.startswith("short"):
                        short_close_succeeded = bool(success)
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
                        logger.info(f"FIX-74: No immediate fill price for {leg_name}, will use deferred batch lookup")
                        # FIX #70: Track for deferred lookup if we have an order_id
                        if order_id:
                            deferred_legs.append((order_id, uic, leg_name))

            # FIX #75: Don't block main loop waiting for fill prices.
            # Positions are already closed - deferred lookup is just for P&L accuracy.
            # Fall through to theoretical P&L path below, then spawn background
            # thread to correct P&L with actual fill prices (~10s later).

        # CRITICAL #7: FAIL CLOSED on a breached short whose buy-back failed.
        # _close_position_with_retry returns success=False after exhausting
        # EMERGENCY_CLOSE_MAX_ATTEMPTS market-order attempts (broker outage,
        # repeated rejects, halt). If THIS side's short leg is still open we
        # must NOT (a) null its uic — the only handle to re-find/re-close it,
        # (b) leave the side marked stopped — that suppresses re-monitoring in
        # _check_stop_losses, or (c) book a theoretical net_loss as if flat.
        # Instead: keep the short tracked, re-arm the side so the next
        # _check_stop_losses tick retries the close, escalate, and return
        # WITHOUT the clear/book/commission/close-alert below. Only the long
        # leg (no naked risk; expires worthless) is cleared here.
        if not short_close_succeeded:
            # Mirrors the MKT-025 sibling (strategy.py CRITICAL C1): on a failed
            # short close we leave the side ACTIVE so _check_stop_losses
            # re-confirms the breach and re-attempts the close on later ticks.
            # We do NOT call _trigger_critical_intervention here — that halts the
            # bot (run_strategy_check short-circuits on _critical_intervention_
            # required) and would DEFEAT the retry. The retry path
            # (_close_position_with_retry_ib) has already fired its CRITICAL
            # EMERGENCY/CIRCUIT_BREAKER alert + logged EMERGENCY_CLOSE_FAILED, so
            # the operator is notified without a hard halt.
            logger.critical(
                f"CRITICAL #7: short {side} leg of Entry #{entry.entry_number} "
                f"FAILED to close after full retry budget (conid "
                f"{short_uic_for_side}) — leaving side ACTIVE (uic intact) for "
                f"retry on next tick. NOT marking stopped, NOT booking loss. "
                f"Live unhedged short remains open!"
            )
            # Re-arm the side (revert the up-front mark) so _check_stop_losses
            # re-evaluates it, and revert the counters we incremented up front so
            # a multi-tick retry doesn't inflate them on every pass.
            if side == "call":
                entry.call_side_stopped = False
                entry.call_stop_time = ""
                self.daily_state.call_stops_triggered -= 1
                # Long call carries no naked risk — clear its tracking (it
                # expires worthless / was already closed or skipped above).
                entry.long_call_position_id = None
                entry.long_call_uic = None
            else:
                entry.put_side_stopped = False
                entry.put_stop_time = ""
                self.daily_state.put_stops_triggered -= 1
                entry.long_put_position_id = None
                entry.long_put_uic = None
            if double_stop_counted:
                self.daily_state.double_stops -= 1
            self._log_safety_event(
                "STOP_CLOSE_FAILED_SHORT_OPEN",
                f"Entry #{entry.entry_number} {side} short (conid "
                f"{short_uic_for_side}) still open after retries — side kept "
                f"active for retry. stop_level=${stop_level:.2f}",
                "Retry Pending",
            )
            # Return to monitoring so the next tick re-runs the stop check
            # (only if the OTHER side isn't itself mid-stop).
            if not other_side_already_stopped:
                self.state = MEICState.MONITORING
            self._save_state_to_disk()
            # Flush the alerts already queued by the retry path.
            time.sleep(0.1)
            self._flush_batched_alerts()
            return (
                f"Stop loss FAILED to close Entry #{entry.entry_number} {side} "
                f"short — side kept active, will retry next tick"
            )

        # Fix #86: Clear position IDs and UICs for the stopped side immediately
        # after closing. Without this, all_position_ids still returns the closed
        # positions' IDs, and the hourly POS-003 reconciliation sees them as
        # "missing from Saxo" → fires a false "Position Mismatch Detected" HIGH
        # alert every time a stop occurs. The registry is already cleaned up by
        # _close_position_with_retry, but the entry object still held stale IDs.
        # UICs are also cleared to avoid unnecessary price fetches in
        # _batch_update_entry_prices (Fix #29 pattern).
        # CRITICAL #7: only reached when the short close SUCCEEDED (or there was
        # no short leg / dry run), so clearing the short's uic here is safe.
        if side == "call":
            entry.short_call_position_id = None
            entry.long_call_position_id = None
            entry.short_call_uic = None
            entry.long_call_uic = None
        else:
            entry.short_put_position_id = None
            entry.long_put_position_id = None
            entry.short_put_uic = None
            entry.long_put_uic = None

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

        # Use actual_close_cost when we have a meaningful estimate of it:
        # - LIVE mode + fill_prices_captured: real fill prices summed across legs
        # - DRY mode: actual_close_cost was computed from real bid/ask above
        #   (Path-B uses live Saxo quotes), giving a realistic estimate
        # In both cases we want to record actual_X_stop_debit on the entry so
        # the state file, the dashboard, and trade_stops.actual_debit DB rows
        # reflect a non-zero estimated debit instead of $0. P&L identity stays
        # correct (total_realized_pnl unchanged because net_loss math here
        # equals what the previous theoretical branch computed when stop fires
        # at the trigger level, which is approximately what dry-mode bid/ask
        # estimates produce at the same instant).
        use_actual_cost = self.dry_run or (fill_prices_captured and not self.dry_run)
        if use_actual_cost:
            net_loss = actual_close_cost - credit_received
            # Record actual debit for dashboard per-entry P&L + DB forensics
            if side == "call":
                entry.actual_call_stop_debit = actual_close_cost
            else:
                entry.actual_put_stop_debit = actual_close_cost
            mode_tag = "DRY-RUN estimate" if self.dry_run else "FIX-42 actual"
            logger.info(
                f"{mode_tag}: P&L for Entry #{entry.entry_number} {side}: "
                f"close_cost=${actual_close_cost:.2f} - credit=${credit_received:.2f} = "
                f"net_loss=${net_loss:.2f}"
            )
        else:
            # Live-mode fallback: fill prices unavailable, use theoretical
            # (stop_level approximates close cost since stop fires at that
            # spread value). The deferred fill-correction worker (Fix #75)
            # will update total_realized_pnl with actual prices ~10s later.
            net_loss = stop_level - credit_received
            logger.warning(
                f"FIX-42: Using theoretical P&L (fill prices unavailable): "
                f"stop_level=${stop_level:.2f} - credit=${credit_received:.2f} = "
                f"net_loss=${net_loss:.2f} (may be inaccurate!)"
            )

        self._book_realized_pnl(-net_loss, entry)

        # Track close commission — Fix #83a: use actual legs closed, not hardcoded 2
        # When worthless long legs are skipped (bid=$0), no trade = no commission
        if self.dry_run:
            close_legs = 2  # Dry run assumes both legs
        else:
            close_legs = legs_actually_closed if legs_actually_closed > 0 else 2
        # v8: scale by entry.contracts (stamped at creation), not current config —
        # preserves correct commission even if contracts_per_entry flipped since
        # this entry was opened.
        close_commission = close_legs * self.commission_per_leg * entry.contracts
        entry.close_commission += close_commission
        self.daily_state.total_commission += close_commission

        # Log stop loss to Google Sheets (pass net loss, not gross)
        self._log_stop_loss(entry, side, stop_level, net_loss)

        # ALERT-002: Use batched alerting for rapid stops (pass net loss for display)
        self._queue_stop_alert(entry, side, stop_level, net_loss)

        # P1: Save state after stop loss
        self._save_state_to_disk()

        # FIX #75: Spawn background thread for deferred fill price lookup
        # Positions are already closed - this is just P&L accounting correction
        # Pass actual_close_cost so the worker can add deferred legs' costs on top
        # of any legs that already had immediate fill prices
        if not self.dry_run and deferred_legs:
            self._spawn_async_fill_correction(
                deferred_legs, actual_close_cost, entry, side, credit_received, net_loss
            )

        # ALERT-002: Flush any batched alerts after a short delay
        # This allows multiple rapid stops to be batched together
        time.sleep(0.1)  # Small delay to allow batching
        self._flush_batched_alerts()

        return f"Stop loss executed: Entry #{entry.entry_number} {side} side (${stop_level:.2f})"

    def _close_position_with_retry(
        self, position_id: str, leg_name: str, uic: int = None, entry_number: int = None,
        contracts: Optional[int] = None,
    ) -> Tuple[bool, Optional[float], Optional[str]]:
        """
        EMERGENCY-001: Close a position with enhanced retry logic and spread validation.

        Enhanced emergency close that:
        1. Checks bid-ask spread before closing (wide spreads = bad fills)
        2. Waits for spread normalization if too wide
        3. Uses progressive retry with escalating alerts
        4. Tracks slippage for monitoring
        5. FIX #42 (2026-02-05): Captures actual fill price for accurate P&L
        6. Fix #45 (2026-02-06): Handles merged positions (multiple entries at same strike)
        7. FIX #70 (2026-02-12): Returns order_id for deferred price lookup
        8. v8 (2026-04-21): Accepts explicit `contracts` param so callers can pass the
           stamped entry.contracts instead of relying on self.contracts_per_entry. This
           matters after a mid-day config flip (1c→2c) with existing open 1c positions:
           the close order amount must match the ACTUAL size on Saxo, not the new config.

        CRITICAL FIX (2026-02-03): Saxo's DELETE /trade/v2/positions/{id} endpoint
        returns 404 for SPX options. Must use place_emergency_order with ToClose instead.

        Args:
            position_id: Saxo position ID
            leg_name: Name for logging (e.g., "short_call", "long_put")
            uic: Option UIC for spread checking (required for placing close order)
            entry_number: Fix #45 - Entry number being closed (for merged position handling)
            contracts: v8 - Contract count to close. Defaults to self.contracts_per_entry
                       when None (backwards-compat for call sites not yet updated).

        Returns:
            Tuple of (success: bool, fill_price: Optional[float], order_id: Optional[str])
            - fill_price is the actual price at which the position was closed
            - If fill_price is None, P&L calculation should fall back to theoretical
            - order_id is the close order ID for deferred price lookup (FIX #70)
        """
        # SAFETY-DRY-04: Defense-in-depth dry-run gate at the canonical close
        # function. Every closure path (stop loss, MKT-018 early close, MKT-025
        # short-only stop, MKT-033 long salvage, partial-entry unwind, naked-
        # short cleanup) eventually flows through here. With this gate at the
        # bottom of the stack, NO upstream gating bug can produce a real Saxo
        # close order in dry mode. Each upstream caller still has its own
        # gate (defense in depth), but this is the last-line guarantee.
        if self.dry_run:
            logger.warning(
                f"[DRY RUN] SAFETY-DRY-04: _close_position_with_retry called for "
                f"{leg_name} (pos={position_id}) — NO real close order will be placed."
            )
            return True, None, None

        # F6.3 — closes the leg via the IBClient write path.
        result = self._close_position_with_retry_ib(
            position_id, leg_name, uic=uic, entry_number=entry_number,
            contracts=contracts,
        )
        # POS-003 (2026-06-23): record a SUCCESSFUL real close so the hourly
        # orphan sweep can suppress it while IBKR's positions feed lags the close
        # out — a TP-closed leg lingered 83s past the 30s confirm window and fired
        # a spurious CRITICAL on live C. A close that PERSISTS past the settle
        # grace is still surfaced.
        try:
            if uic and result and result[0]:
                self._note_recent_close(uic)
        except Exception:
            pass
        return result

    def _note_recent_close(self, conid) -> None:
        """Record a conid the bot just closed at the broker, timestamped, so
        POS-003's orphan sweep can distinguish 'the bot's own close still lagging
        out of IBKR's positions feed' from a genuine untracked/crash orphan.
        Reset daily in :meth:`_reset_for_new_day`."""
        if not conid:
            return
        if getattr(self, "_recent_close_conids", None) is None:
            self._recent_close_conids = {}
        self._recent_close_conids[conid] = get_us_market_time()

    def _place_marketable_close(self, *, uic: int, side: str, quantity: int,
                                attempt_num: int) -> dict:
        """Place ONE close order, preferring an AGGRESSIVE MARKETABLE LIMIT that
        crosses the spread; fall back to a plain MARKET only when no usable
        quote is available.

        CLOSE-LMT (2026-06-08 research): IBKR routes an OPTION "market" order as
        a series of CAPPED marketable-limit orders with a documented "remote
        possibility" of non-execution, and Market-with-Protection is NOT
        available for OPT via the Client Portal API. An explicit marketable
        limit walked through the spread is the most reliable close — especially
        in the wide-NBBO window before the 4:00pm ET close, where a capped
        market can stall. The caller re-reads the quote each attempt (the close
        loop calls this per attempt), so the limit tracks the live touch; the
        cross widens with ``attempt_num`` up to CLOSE_LIMIT_CROSS_CAP, so we
        pay up to fill but never an uncapped sweep. A worthless long (no bid)
        falls through to MARKET and simply expires if it can't sell — harmless.
        Returns the same normalized dict as ``_close_leg_order``.

        MKT-047 (2026-06-17): inside the final minutes before the actual market
        close, a crossing marketable-limit can chase-and-miss a fast tape and
        then expire un-filled (the 06-17 FOMC late-breach failure: variant C's
        put stopped at 15:57 but every limit close "did not fill" until the 16:00
        expiry rejected it). When within ``eod_flatten_market_minutes`` of the
        close we escalate straight to a true MARKET order — take whatever fill is
        available rather than miss entirely. 0 / unset disables the escalation.
        """
        mkt_min = getattr(self, "eod_flatten_market_minutes", 0.0) or 0.0
        if mkt_min > 0:
            try:
                now = get_us_market_time()
                close_t = get_market_close_time(now)
                mins_to_close = ((close_t.hour * 60 + close_t.minute)
                                 - (now.hour * 60 + now.minute))
                if 0 <= mins_to_close <= mkt_min:
                    logger.warning(
                        f"  Close via MARKET (MKT-047 escalation) — {mins_to_close}min "
                        f"to {close_t.strftime('%H:%M')} close ≤{mkt_min:.0f}min "
                        f"({side} x{quantity}, attempt {attempt_num})"
                    )
                    return self._close_leg_order(
                        instrument_id=uic, side=side, quantity=quantity,
                    )
            except Exception as _e:
                # Never let the time check block a close — fall through to LMT.
                logger.debug(f"  MKT-047 escalation check skipped: {_e}")
        quote = self._read_option_quote(uic)
        limit_price = None
        if quote:
            bid = quote.get("bid")
            ask = quote.get("ask")
            cross = min(CLOSE_LIMIT_CROSS_STEP * max(1, attempt_num),
                        CLOSE_LIMIT_CROSS_CAP)
            if side == "BUY" and ask and ask > 0:
                # Cross UP through the offer — BUY ≥ ask is marketable.
                limit_price = round_to_spx_tick(ask + cross, round_up=True)
            elif side == "SELL" and bid and bid > 0:
                # Cross DOWN through the bid — SELL ≤ bid is marketable.
                limit_price = round_to_spx_tick(max(0.05, bid - cross),
                                                round_up=False)
        if limit_price is not None:
            logger.info(
                f"  Close via MARKETABLE LIMIT @ ${limit_price:.2f} "
                f"({side} x{quantity}, cross attempt {attempt_num})"
            )
            return self._place_leg_order(
                instrument_id=uic, side=side, quantity=quantity,
                order_type="LMT", limit_price=limit_price,
            )
        logger.info(
            f"  Close via MARKET — no usable quote to price a marketable limit "
            f"({side} x{quantity})"
        )
        return self._close_leg_order(
            instrument_id=uic, side=side, quantity=quantity,
        )

    def _emergency_close_alert_once(self, uic, *, alert_type, title, message,
                                    priority) -> None:
        """Fire an emergency-close-failure alert AT MOST ONCE per (conid, title)
        per ET day.

        The close-with-retry path is re-invoked on EVERY monitoring tick while a
        position stays unclosed, so without this a stuck close fired the same
        alert cluster ~84×/hr (2026-06-12: variant-C E#2's take-profit couldn't
        execute and re-alerted all afternoon). Keying on the broker conid + the
        alert title lets the full escalation story (IMMEDIATE → STRUGGLING →
        FAILED) come through ONCE, then stay quiet until the position resolves /
        the day resets. Mirrors the _b2_short_fail_alerted dedup. Wrapped so an
        alert failure can't break the close loop.
        """
        seen = getattr(self, "_emergency_close_alerted", None)
        if seen is None:
            seen = set()
            self._emergency_close_alerted = seen
        key = (uic, title)
        if key in seen:
            return
        seen.add(key)
        if getattr(self, "alert_service", None) is None:
            return
        try:
            self.alert_service.send_alert(
                alert_type=alert_type, title=title, message=message,
                priority=priority, details={"conid": uic},
            )
        except Exception as exc:
            logger.debug("emergency-close alert failed (non-fatal): %s", exc)

    def _close_position_with_retry_ib(
        self, position_id: str, leg_name: str, uic: int = None,
        entry_number: int = None, contracts: Optional[int] = None,
    ) -> Tuple[bool, Optional[float], Optional[str]]:
        """IBKR path of :meth:`_close_position_with_retry` (F6.3).

        Far simpler than the Saxo path: :meth:`_close_leg_order` (F6.1)
        already does place→poll-to-fill and carries the fill price, so
        there is no separate place / verify / fill-lookup three-step.
        IBKR settles by conid net quantity — no per-leg position id, no
        Saxo DELETE-endpoint 404 quirk, no merged-position partial-close
        math. ``uic`` carries the conid; ``position_id`` is unused on
        this path (IBKR has none).

        The Saxo path's pre-close spread-wait is intentionally NOT
        carried over: an emergency close prioritises a certain exit, and
        ``place_and_wait_for_fill`` already owns the market-order
        place→poll. The retry delay covers "let the book settle".

        Returns ``(success, fill_price, order_id)`` — the same contract
        as the Saxo path.
        """
        if self.dry_run:
            logger.warning(
                f"[DRY RUN] SAFETY-DRY-04b: _close_position_with_retry_ib "
                f"reached in dry mode for {leg_name} — no close order placed."
            )
            return True, None, None

        if not uic:
            logger.error(
                f"EMERGENCY-001: cannot close {leg_name} without a conid"
            )
            return False, None, None

        close_contracts = contracts if contracts else self.contracts_per_entry
        # short leg → BUY to close; long leg → SELL to close
        side = "BUY" if leg_name.startswith("short") else "SELL"

        # #14/#44/#46: track partial closes. A market close can partial-fill on
        # an illiquid SPXW leg (only possible when close_contracts >= 2). We must
        # treat that as PARTIALLY done: cancel the working remainder, reduce the
        # quantity we re-place by what already filled (NEVER re-place the full
        # size — that over-closes, potentially eating a co-located entry's short
        # on a merged conid book), and fold the partial's fill price into the
        # returned close cost so P&L stays accurate.
        qty_remaining = int(close_contracts)
        filled_price_qty = 0.0   # Σ fill_price_i × filled_qty_i (priced legs only)
        filled_qty_priced = 0    # Σ filled_qty_i for legs that carried a price
        any_partial = False
        last_order_id = None

        def _blended_fill_price():
            # Volume-weighted avg across all partials that carried a price; the
            # caller multiplies this by the FULL close_contracts, and since we
            # only return success once qty_remaining == 0 (everything closed),
            # vwap × close_contracts reconstructs the true total cost. None when
            # no partial carried a usable price (caller falls back to theoretical).
            if filled_qty_priced > 0:
                return filled_price_qty / filled_qty_priced
            return None

        for attempt in range(EMERGENCY_CLOSE_MAX_ATTEMPTS):
            attempt_num = attempt + 1
            try:
                # Already gone? The broker shows no open quantity at the conid.
                # L-H2: read positions STRICTLY. _read_open_positions() swallows
                # a fetch failure and returns [] — which _position_is_open reads
                # as "flat", so a transient read failure would END the close as
                # SUCCESS and abandon a possibly-still-open, breached short
                # (untracked naked 0DTE leg). With strict=True an inconclusive
                # read RAISES; we then do NOT assume closed — fall through and
                # retry the close. Only a positive qty-0 read ends it as closed.
                already_gone = False
                if attempt > 0:
                    try:
                        _positions = self._read_open_positions(strict=True)
                        already_gone = not self._position_is_open(
                            uic, positions=_positions
                        )
                    except Exception as exc:
                        logger.warning(
                            f"EMERGENCY-001: strict positions read failed for "
                            f"{leg_name} (conid {uic}): {exc} — NOT assuming "
                            f"closed; retrying the close"
                        )
                        already_gone = False
                if already_gone:
                    logger.info(
                        f"EMERGENCY-001: {leg_name} (conid {uic}) already "
                        f"closed — skipping retry"
                    )
                    # Only report a blended price if we actually priced the full
                    # size via partials; otherwise some contracts were closed
                    # without an observed price (e.g. settled/merged) — return
                    # None so the caller uses theoretical/deferred P&L rather
                    # than scaling a partial VWAP up to the full quantity.
                    out_price = (
                        _blended_fill_price()
                        if filled_qty_priced >= close_contracts else None
                    )
                    return True, out_price, last_order_id

                logger.info(
                    f"EMERGENCY-001: Closing {leg_name} "
                    f"(conid={uic}, {side}, amount={qty_remaining}, "
                    f"attempt {attempt_num}) — marketable limit, MARKET fallback"
                )
                res = self._place_marketable_close(
                    uic=uic, side=side, quantity=qty_remaining,
                    attempt_num=attempt_num,
                )
                last_order_id = res.get("order_id") or last_order_id
                if res.get("filled"):
                    fill_price = res.get("fill_price")
                    # Fold this final fill into the blended price so a prior
                    # partial isn't dropped from the accounted close cost.
                    if fill_price is not None:
                        filled_price_qty += fill_price * qty_remaining
                        filled_qty_priced += qty_remaining
                    if any_partial:
                        # Only return a blended price if EVERY closed contract
                        # carried a price (filled_qty_priced == close_contracts);
                        # otherwise the caller would scale a partial VWAP up to
                        # the full size and mis-state cost — return None so it
                        # falls back to theoretical/deferred P&L.
                        out_price = (
                            _blended_fill_price()
                            if filled_qty_priced >= close_contracts else None
                        )
                    else:
                        out_price = fill_price
                    logger.info(
                        f"EMERGENCY-001: Verified {leg_name} closed on "
                        f"attempt {attempt_num}, "
                        + (f"fill_price=${out_price:.2f}" if out_price is not None
                           else "fill_price=unknown")
                    )
                    return True, out_price, res.get("order_id")

                # #14/#44/#46: partial fill — SOME contracts closed but not all.
                # filled_quantity is the count from THIS order (each retry is a
                # fresh order). Credit them toward the close, cancel the working
                # remainder, shrink qty_remaining, and re-place ONLY the residual.
                partial_qty = int(res.get("filled_quantity") or 0)
                if 0 < partial_qty < qty_remaining:
                    any_partial = True
                    fill_price = res.get("fill_price")
                    if fill_price is not None:
                        filled_price_qty += fill_price * partial_qty
                        filled_qty_priced += partial_qty
                    qty_remaining -= partial_qty
                    logger.warning(
                        f"EMERGENCY-001: close {leg_name} attempt {attempt_num} "
                        f"PARTIAL fill {partial_qty} contract(s) "
                        f"(fill_price="
                        + (f"${fill_price:.2f}" if fill_price is not None
                           else "unknown")
                        + f"); {qty_remaining} remaining — cancelling working "
                        f"remainder + retrying residual only (no over-close)."
                    )
                    if res.get("order_id"):
                        self._cancel_order(res["order_id"])
                    if attempt < EMERGENCY_CLOSE_MAX_ATTEMPTS - 1:
                        time.sleep(EMERGENCY_CLOSE_RETRY_DELAY_SECONDS)
                    continue

                logger.warning(
                    f"EMERGENCY-001: close {leg_name} attempt {attempt_num} "
                    f"did not fill — retrying..."
                )
                # Cancel the unfilled close order before retrying — a
                # timed-out market order stays WORKING and could fill
                # late, double-closing the leg.
                if res and res.get("order_id"):
                    self._cancel_order(res["order_id"])
                if attempt_num == 1:
                    self._emergency_close_alert_once(
                        uic,
                        alert_type=AlertType.EMERGENCY_CLOSE,
                        title="STOP CLOSE FAILED - IMMEDIATE",
                        message=(
                            f"First attempt to close {leg_name} did not "
                            f"fill (conid {uic}). Will retry "
                            f"{EMERGENCY_CLOSE_MAX_ATTEMPTS - 1} more times."
                        ),
                        priority=AlertPriority.HIGH,
                    )

            except Exception as e:
                logger.error(
                    f"EMERGENCY-001: Close {leg_name} attempt {attempt_num} "
                    f"failed: {e}"
                )
                if attempt_num == 1:
                    self._emergency_close_alert_once(
                        uic,
                        alert_type=AlertType.EMERGENCY_CLOSE,
                        title="STOP CLOSE EXCEPTION - IMMEDIATE",
                        message=(
                            f"Exception closing {leg_name}: {str(e)[:100]}. "
                            f"conid {uic}. Will retry."
                        ),
                        priority=AlertPriority.HIGH,
                    )
                elif attempt_num >= 4:
                    self._emergency_close_alert_once(
                        uic,
                        alert_type=AlertType.EMERGENCY_CLOSE,
                        title="EMERGENCY CLOSE STRUGGLING",
                        message=(
                            f"Failed to close {leg_name} after {attempt_num} "
                            f"attempts. conid {uic}. Error: {e}"
                        ),
                        priority=(AlertPriority.HIGH if attempt_num == 4
                                  else AlertPriority.CRITICAL),
                    )

            if attempt < EMERGENCY_CLOSE_MAX_ATTEMPTS - 1:
                time.sleep(EMERGENCY_CLOSE_RETRY_DELAY_SECONDS)

        # #14/#44/#46: surface any partial progress so a manual close targets the
        # right residual size. Still fail closed — qty_remaining > 0 means the leg
        # is NOT fully closed, so we return success=False (caller keeps it tracked
        # + retries) and do NOT book the partial as a realized close.
        partial_note = (
            f" ({close_contracts - qty_remaining}/{close_contracts} contract(s) "
            f"partially closed, {qty_remaining} still open)"
            if qty_remaining != close_contracts else ""
        )
        error_msg = (
            f"EMERGENCY-001 CRITICAL: FAILED to fully close {leg_name} (conid "
            f"{uic}) after {EMERGENCY_CLOSE_MAX_ATTEMPTS} attempts!{partial_note}"
        )
        logger.critical(error_msg)
        self._emergency_close_alert_once(
            uic,
            alert_type=AlertType.CIRCUIT_BREAKER,
            title="EMERGENCY CLOSE FAILED",
            message=error_msg,
            priority=AlertPriority.CRITICAL,
        )
        self._log_safety_event("EMERGENCY_CLOSE_FAILED", error_msg, "Failed")
        return False, None, None

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
            quote = self._read_option_quote(uic)
            if not quote:
                logger.warning(f"EMERGENCY-001: No quote for UIC {uic}, proceeding anyway")
                return (True, 0.0)  # Proceed if no quote available

            bid = quote.get("bid") or 0
            ask = quote.get("ask") or 0

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

    def _spawn_async_fill_correction(
        self,
        deferred_legs: list,
        partial_close_cost: float,
        entry: IronCondorEntry,
        side: str,
        credit_received: float,
        theoretical_net_loss: float
    ):
        """
        FIX #75: deferred stop-fill P&L correction — no-op on IBKR.

        On IBKR this is intentionally a no-op. Stop closes route through
        ``_close_position_with_retry`` → ``_close_leg_order`` →
        ``place_and_wait_for_fill``, which polls to a terminal state and
        returns the actual ``avg_fill_price`` synchronously. The stop
        path therefore already records the real close cost into
        ``actual_call_stop_debit`` / ``actual_put_stop_debit`` and
        ``total_realized_pnl`` — there is no Saxo-style activity-stream
        sync lag to correct for. The method is kept so the stop-loss
        call site stays intact.
        """
        return

    def _wait_for_pending_fill_corrections(self, timeout: float = 15.0):
        """
        FIX #75: Wait for any pending async fill corrections to complete.

        Called before daily summary and on graceful shutdown to ensure all
        P&L corrections are applied before final accounting.

        Args:
            timeout: Maximum seconds to wait per thread (default 15s)
        """
        active = [t for t in self._pending_fill_corrections if t.is_alive()]
        if not active:
            self._pending_fill_corrections.clear()
            return
        logger.info(f"FIX-75: Waiting for {len(active)} pending fill correction(s)...")
        for t in active:
            t.join(timeout=timeout)
        still_alive = [t for t in active if t.is_alive()]
        if still_alive:
            logger.warning(
                f"FIX-75: {len(still_alive)} correction(s) still running after {timeout}s timeout"
            )
        self._pending_fill_corrections.clear()

    # =========================================================================
    # MARKET DATA
    # =========================================================================

    @staticmethod
    def _split_index_read(read_result):
        """#17/#18: normalize the return of `_read_index_price`.

        Historically `_read_index_price` returns a bare float (or None). The
        staleness fix wants the broker freshness flag (IBKR field 6509:
        'R'/'D'/'Z') alongside the price. To remain compatible whether or not
        the strategy-layer counterpart has been updated, accept BOTH shapes:
        a float/None, or a ``(price, availability)`` tuple. Returns
        ``(price, availability)`` with availability None when not supplied.
        """
        if isinstance(read_result, (tuple, list)) and len(read_result) == 2:
            return read_result[0], read_result[1]
        return read_result, None

    def _update_market_data(self):
        """Update SPX and VIX spot prices from the active broker.

        GAP-C (F7.2): rewired to the broker-agnostic `_read_index_price`
        helper. This sets `self.current_price` (SPX spot, used in every
        strike calc) and `self.current_vix` — it had no IBKR path
        before F7.

        #17/#18: the broker's per-quote freshness flag (availability
        'R'/'D'/'Z') is plumbed into update_spx/update_vix so a STALE ('Z')
        tick does not refresh the freshness clock (DATA-001 can then trip on a
        frozen feed). See _split_index_read for the compatibility shim.
        """
        price, spx_avail = self._split_index_read(self._read_index_price(self.underlying_symbol))
        if price:
            self.market_data.update_spx(price, availability=spx_avail)
            # #17/#18: only treat the SPX spot as current when the broker did
            # NOT flag it NON-real-time — otherwise keep the previous
            # current_price so strike calc / price-based stops don't act on a
            # frozen/unentitled quote. Mirror update_spx(): the first char of
            # 6509 in (Z=Frozen, Y=Frozen-Delayed, N=Not-Subscribed) is blocked;
            # R/D/None pass (D is delayed-but-usable, logged by update_spx).
            avail = spx_avail.upper() if isinstance(spx_avail, str) else None
            if (avail[:1] if avail else None) not in ("Z", "Y", "N"):
                self.current_price = price

        vix, vix_avail = self._split_index_read(self._read_index_price(self.volatility_symbol))
        if vix:
            self.market_data.update_vix(vix, availability=vix_avail)
            avail = vix_avail.upper() if isinstance(vix_avail, str) else None
            if (avail[:1] if avail else None) not in ("Z", "Y", "N"):
                self.current_vix = vix

    # P7-audit L4: removed dead Saxo-shaped `_extract_price` and
    # `_check_quote_freshness`. They read `quote["Quote"]["Bid"]` /
    # `quote["PriceInfo"]["LastUpdated"]` — Saxo response shapes that
    # IBKR's `IBClient.get_quote()` doesn't populate. Zero production
    # callers remain (verified via repo-wide grep on 2026-05-22). The
    # IBKR equivalent for "extract a usable price" is the explicit
    # `is not None` ladder in `strategy._read_index_price` / `_read_option_quote`.
    #
    # #17/#18 staleness: IBKR's per-quote `availability` flag (field 6509:
    # 'R'=real-time / 'D'=delayed / 'Z'=stale) is now ACTUALLY consumed —
    # _update_market_data plumbs it into MarketData.update_spx/update_vix, which
    # refuse to advance the freshness timestamp on a 'Z' tick (so DATA-001 trips
    # on a frozen-but-responding feed) and log on 'D'. This requires the
    # strategy-layer `_read_index_price` to surface availability alongside the
    # price (return `(price, availability)`); until/unless it does, the shim in
    # _split_index_read leaves availability None and behavior is unchanged.

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
            title=f"{self.BOT_NAME} BOT HALTED",
            message=f"{self.BOT_NAME} HALTED: {reason}. Manual intervention required.",
            priority=AlertPriority.CRITICAL
        )

    # =========================================================================
    # POSITION RECONCILIATION
    # =========================================================================

    def _reconcile_positions(self):
        """
        Reconcile positions with registry (called during market hours).

        This is a lighter-weight check compared to full recovery.
        Handles POS-003: Positions closed manually during trading.
        """
        # Path-B dry-run skip (2026-04-27): the entire reconciliation flow below
        # compares DRY_* IDs against Saxo's real position list. Every DRY_* ID
        # is "missing" by design, so the per-entry leg-missing block (lines
        # 6004-6034) marks every dry side as stopped — killing the entry. The
        # 2026-02-04 partial skip below covered orphan cleanup but missed this
        # path. Skip the whole reconciliation in dry mode.
        if self.dry_run:
            logger.debug("Path-B: skipping _reconcile_positions in dry-run mode (DRY_* IDs not in Saxo by design)")
            return

        logger.info("Reconciling positions with the broker...")

        # Get all open positions from the broker. IBKR has no per-leg
        # position id — positions are conid-keyed — so reconciliation
        # keys on the leg conid. NOTE: the per-leg `*_position_id` loop
        # below is Saxo-era and dormant on IBKR (`*_position_id` is
        # always None); conid-based mid-session POS-003 reconciliation
        # is tracked as DEF-7. _save_state_to_disk still runs.
        all_positions = self._read_open_positions()
        valid_ids = {str(p.get("instrument_id")) for p in all_positions}

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

        # Check if any of our tracked positions are missing from the broker.
        #
        # AUD2-M6 / DEF-7: this per-leg loop gates on `*_position_id` which
        # is Saxo-era. On IBKR `*_position_id` is ALWAYS None (no per-leg
        # position id), so `if pos_id and pos_id not in valid_ids:` below
        # never fires — the entire mid-session POS-003 reconciliation is
        # dormant on IBKR. Settlement reconciliation
        # (`check_after_hours_settlement` → `_side_positions_gone`) and
        # the stop monitor both use the conid model and still function.
        #
        # DEF-7 trigger (see docs/migration/DEFERRED_WORK.md): if mid-session
        # manual-close detection becomes a requirement, rewrite this loop
        # to iterate `*_uic` (conid) and use the `_position_is_open(conid,
        # side)` predicate. For paper-only single-bot HYDRA, the dormancy
        # is acceptable.
        for entry in self.daily_state.active_entries:
            missing_legs = []

            for leg_name in LEG_NAMES:
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
    # AFTER-HOURS SETTLEMENT RECONCILIATION (POS-004)
    # =========================================================================

    def _force_normal_day(self) -> bool:
        """Returns True iff the bot is in dry-run AND `dry_run_force_normal_day`
        is set, in which case every date/event-based skip should be bypassed.

        Used by FOMC announcement skip (this class), FOMC T+1 skip (HYDRA),
        MKT-038 FOMC T+1 call-only (HYDRA). Live mode never returns True.
        """
        return self.dry_run and self.dry_run_force_normal_day

    @staticmethod
    def _position_is_settled(pid) -> bool:
        """
        Position is "settled / gone from the broker" when the ID is missing
        OR is a Path-B dry-run synthetic ID (DRY_*).

        On the IBKR path `pid` is always None for live entries (IBKR has no
        per-leg position id — see F4 design doc), so the first branch fires
        unconditionally and settlement is gated on the conid-quantity
        reconciliation (`_expected_position_quantities`) instead. On legacy
        Saxo state-file rehydration `pid` may still carry a Saxo UUID; the
        same "missing/empty" test applies.

        DRY_* IDs are populated by _simulate_entry() so heartbeat monitoring
        treats the entry as live during the day. At settlement, they should
        be treated as gone — no real broker position ever existed to look
        up. Without this, _process_expired_credits() never marked dry-run
        sides as expired and credit was never added to total_realized_pnl,
        leaving winning dry-run days reporting net_pnl = -$commission
        (Apr 28-29 backfill).
        """
        if not pid:
            return True
        return str(pid).startswith("DRY_")

    # =========================================================================
    # LOGGING AND ALERTS
    # =========================================================================

    def _log_stop_loss(self, entry: IronCondorEntry, side: str, stop_level: float, realized_loss: float):
        """Log stop loss to Google Sheets."""
        try:
            if side == "call":
                strike_str = f"C:{entry.short_call_strike}/{entry.long_call_strike}"
            else:
                strike_str = f"P:{entry.short_put_strike}/{entry.long_put_strike}"

            # MKT-036: Include confirmation data in Notes if available
            trade_reason = f"Stop Loss | Level: ${stop_level:.2f}"
            conf_seconds = 0
            breach_recoveries = 0
            breach_time = getattr(entry, f'{side}_breach_time', None)
            if breach_time is not None:
                conf_seconds = int((datetime.now() - breach_time).total_seconds())
                breach_recoveries = getattr(entry, f'{side}_breach_count', 0)
                trade_reason += f" | MKT-036: {conf_seconds}s confirmed, {breach_recoveries} recoveries"

            self.trade_logger.log_trade(
                action=f"{self.BOT_NAME} Stop #{entry.entry_number} ({side.upper()})",
                strike=strike_str,
                price=stop_level,
                delta=0.0,
                pnl=-realized_loss,  # Negative because it's a loss
                saxo_client=self.broker,  # Fix #63: Enable EUR conversion
                underlying_price=self.current_price,
                vix=self.current_vix,
                option_type=f"IC {side.title()} Spread",
                trade_reason=trade_reason
            )

            logger.info(
                f"Stop queued for Sheets: Entry #{entry.entry_number} {side} side, "
                f"Loss: ${realized_loss:.2f}"
            )
        except Exception as e:
            logger.error(f"Failed to log stop loss: {e}")

    def _send_daily_summary(self):
        """Send daily summary alert."""
        summary = self.get_daily_summary()

        # Add extra fields for alert formatting
        summary["spx_close"] = self.current_price
        summary["vix_close"] = self.current_vix
        summary["cumulative_pnl"] = self.cumulative_metrics.get("cumulative_pnl", 0) + summary.get("net_pnl", summary["total_pnl"])
        summary["dry_run"] = self.dry_run
        summary["total_scheduled"] = len(self.entry_times)

        self.alert_service.daily_summary_ic(summary)

    def get_daily_summary(self) -> Dict:
        """Get daily trading summary."""
        # FIX (2026-02-03): Use Saxo's authoritative P&L instead of mid-price calc
        unrealized = self._get_total_saxo_pnl()
        total_pnl = self.daily_state.total_realized_pnl + unrealized

        # PNL-001: Sanity check the P&L values
        self._check_pnl_sanity(total_pnl, "daily_total")

        # P&L breakdown: compute net stop loss debits and expired credits from entries
        # Identity: Expired Credits - Stop Loss Debits - Commission = Daily P&L (net)
        #
        # FIX #78: Derive stop_loss_debits from the P&L identity instead of using
        # theoretical stop levels. Previous code used entry.call_side_stop (the TRIGGER
        # level) instead of the actual close cost. Market orders get price slippage,
        # so actual_close_cost != theoretical stop_level. total_realized_pnl already
        # tracks the accurate P&L (including async fill corrections from Fix #75),
        # so we derive: stop_loss_debits = expired_credits - total_realized_pnl
        expired_credits = 0.0
        for entry in self.daily_state.entries:
            if getattr(entry, 'call_side_expired', False):
                expired_credits += entry.call_spread_credit
            if getattr(entry, 'put_side_expired', False):
                expired_credits += entry.put_spread_credit
        # Derive net stop loss debits from identity:
        # total_realized_pnl = expired_credits - sum(net_losses_from_stops)
        # Therefore: sum(net_losses) = expired_credits - total_realized_pnl
        stop_loss_debits = max(0.0, expired_credits - self.daily_state.total_realized_pnl)

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
            "net_pnl": total_pnl - self.daily_state.total_commission,  # P&L after commission
            "stop_loss_debits": stop_loss_debits,
            "expired_credits": expired_credits,
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
            "total_commission": self.daily_state.total_commission,  # For net P&L display
            # Risk & return metrics
            "max_loss_stops": self._calculate_max_loss_with_stops(),
            "max_loss_catastrophic": self._calculate_max_loss_catastrophic(),
            "capital_deployed": self._calculate_capital_deployed(),
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
        positions = self._read_open_positions()

        for entry in self.daily_state.active_entries:
            # Use the broker's authoritative per-entry P&L for display
            total_pnl = self._get_broker_pnl_for_entry(entry, positions=positions)

            # FIX (2026-02-04): Use spread_value (cost to close) for cushion calculation
            # This matches the stop logic which triggers when spread_value >= stop_level
            # Previously used P&L with abs() which gave wrong cushion when profitable
            call_value = entry.call_spread_value if not entry.call_side_stopped else 0
            put_value = entry.put_spread_value if not entry.put_side_stopped else 0

            # Calculate distance to stop levels (as percentage of effective stop)
            # Uses _get_effective_stop_level() which HYDRA overrides to apply MKT-042 buffer decay
            # Cushion = (effective_stop - current_value) / effective_stop * 100
            # When value=0 (options worthless): cushion = 100%
            # When value=effective_stop: cushion = 0% (stop triggered)
            eff_call_stop = self._get_effective_stop_level(entry, "call") if not entry.call_side_stopped else 0
            eff_put_stop = self._get_effective_stop_level(entry, "put") if not entry.put_side_stopped else 0
            call_dist = eff_call_stop - call_value if not entry.call_side_stopped else 0
            put_dist = eff_put_stop - put_value if not entry.put_side_stopped else 0
            call_pct = (call_dist / eff_call_stop * 100) if eff_call_stop > 0 else 0
            put_pct = (put_dist / eff_put_stop * 100) if eff_put_stop > 0 else 0

            # Status indicators
            # Fix #49: Show SKIPPED for one-sided entries that never opened a side
            if getattr(entry, 'call_side_skipped', False):
                call_status = "SKIPPED"
            elif entry.call_side_stopped:
                # MKT-033: Show salvage revenue if long was sold
                if getattr(entry, 'call_long_sold', False):
                    salvage_rev = getattr(entry, 'call_long_sold_revenue', 0)
                    call_status = f"SALVAGED +${salvage_rev:.0f}"
                else:
                    call_status = "STOPPED"
            elif getattr(entry, 'call_breach_time', None) is not None:
                # MKT-036: Stop confirmation in progress
                elapsed = (datetime.now() - entry.call_breach_time).total_seconds()
                conf_window = getattr(self, 'stop_confirmation_seconds', 75)
                call_status = f"CONF {elapsed:.0f}s/{conf_window}s"
            else:
                call_status = f"{call_pct:.0f}% cushion"

            if getattr(entry, 'put_side_skipped', False):
                put_status = "SKIPPED"
            elif entry.put_side_stopped:
                # MKT-033: Show salvage revenue if long was sold
                if getattr(entry, 'put_long_sold', False):
                    salvage_rev = getattr(entry, 'put_long_sold_revenue', 0)
                    put_status = f"SALVAGED +${salvage_rev:.0f}"
                else:
                    put_status = "STOPPED"
            elif getattr(entry, 'put_breach_time', None) is not None:
                # MKT-036: Stop confirmation in progress
                elapsed = (datetime.now() - entry.put_breach_time).total_seconds()
                conf_window = getattr(self, 'stop_confirmation_seconds', 75)
                put_status = f"CONF {elapsed:.0f}s/{conf_window}s"
            else:
                put_status = f"{put_pct:.0f}% cushion"

            # Warn if close to stop (only for active sides, not skipped/stopped)
            call_warning = "⚠️" if call_status.endswith("cushion") and call_pct < 30 else ""
            put_warning = "⚠️" if put_status.endswith("cushion") and put_pct < 30 else ""

            # Spread values for backtesting DB (always include for HOMER parsing)
            csv = call_value if not entry.call_side_stopped and not getattr(entry, 'call_side_skipped', False) else 0
            psv = put_value if not entry.put_side_stopped and not getattr(entry, 'put_side_skipped', False) else 0

            line = (
                f"  Entry #{entry.entry_number}: "
                f"C:{entry.short_call_strike}/{entry.long_call_strike} "
                f"P:{entry.short_put_strike}/{entry.long_put_strike} | "
                f"Credit: ${entry.total_credit:.0f} | "
                f"P&L: ${total_pnl:+.0f} | "
                f"Call: {call_status}{call_warning} | "
                f"Put: {put_status}{put_warning} | "
                f"SV: {csv:.0f}/{psv:.0f}"
            )
            lines.append(line)

        return lines

    def _get_total_saxo_pnl(self) -> float:
        """
        Get total broker unrealized P&L for all active entries.

        NOTE: the ``saxo`` in the name is legacy — this is **broker-agnostic**
        and reads IBKR P&L on this branch (kept for back-compat / to avoid
        churn in the disabled MKT-018 callers; see GAP-A below).

        FIX (2026-02-03): Use the broker's authoritative P&L instead of a
        mid-price calc.

        GAP-A (F7.7): sums :meth:`_get_broker_pnl_for_entry` (conid-keyed,
        MKT-025 aware) per entry off a single :meth:`_read_open_positions`
        fetch. Both callers in HYDRA (early-close P&L) are inside
        MKT-018, which is disabled — not a hot path.

        Returns:
            Total unrealized P&L in dollars
        """
        try:
            # P7-audit M6: the prior `if self.broker is not None:` guard
            # was vacuous (HYDRA always has a broker on the IBKR path) AND
            # would silently return None from a `-> float` method if the
            # guard were ever False. Flattened — broker fetch failure
            # now hits the except branch with the mid-price fallback.
            positions = self._read_open_positions()
            return sum(
                self._get_broker_pnl_for_entry(entry, positions=positions)
                for entry in self.daily_state.active_entries
            )
        except Exception as e:
            logger.debug(f"Error getting total broker P&L: {e}")
            # Fall back to mid-price calculation
            return sum(e.unrealized_pnl for e in self.daily_state.active_entries)

    # =========================================================================
    # METRICS PERSISTENCE
    # =========================================================================

    def _load_cumulative_metrics(self) -> Dict:
        """Load cumulative metrics from disk.

        Durability fix: a PRESENT-but-corrupt metrics file means the multi-day
        running P&L / win-rate / credit history is about to be reset to zeros.
        That is data loss, not a normal fresh start, so it is logged at ERROR
        and alerted (recoverable from the daily GCS backup — see RB-7) rather
        than swallowed with a warning. A genuinely ABSENT file (first run) is
        the expected fresh-start path and stays quiet.
        """
        file_exists = os.path.exists(self.metrics_file)
        try:
            if file_exists:
                with open(self.metrics_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            # File exists but could not be parsed → corruption / truncation.
            logger.error(
                f"CORRUPT cumulative metrics file {self.metrics_file}: {e}. "
                f"Resetting multi-day history to ZERO — restore from the daily "
                f"GCS backup (RB-7) to recover prior P&L/win-rate history."
            )
            try:
                self.alert_service.send_alert(
                    alert_type=AlertType.CIRCUIT_BREAKER,
                    title="Cumulative Metrics Reset (corruption)",
                    message=(
                        f"{self.BOT_NAME}: {self.metrics_file} was unreadable "
                        f"({e}); multi-day P&L history reset to 0. Restore from "
                        f"GCS backup if accurate cumulative totals are needed."
                    ),
                    priority=AlertPriority.HIGH,
                )
            except Exception:
                # Alerting must never block startup.
                pass

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

    def _save_cumulative_metrics(self, trading_date: str = None):
        """Save cumulative metrics to disk.

        Args:
            trading_date: The trading date being summarized (YYYY-MM-DD format).
                Fix #83: Must use the TRADING date, not datetime.now(). When midnight
                settlement processes the previous day's data, datetime.now() returns
                the NEW day, poisoning the Fix #71 idempotency guard for the entire
                next trading day.
        """
        try:
            os.makedirs(os.path.dirname(self.metrics_file), exist_ok=True)
            # Fix #83: Store trading date, not current timestamp
            # Previous code used get_us_market_time().isoformat() which caused midnight
            # settlement for day N to store day N+1's date, blocking day N+1's summary
            self.cumulative_metrics["last_updated"] = trading_date or get_us_market_time().strftime("%Y-%m-%d")
            # Durability fix: write atomically (temp + fsync + os.replace) so a
            # crash / power loss / disk-full mid-write can never leave a
            # truncated/empty hydra_metrics.json that _load_cumulative_metrics
            # would silently reset to zeros, destroying multi-day P&L history.
            # Mirrors HYDRA's _atomic_write_json contract for hydra_state.json.
            tmp_file = self.metrics_file + ".tmp"
            with open(tmp_file, 'w') as f:
                json.dump(self.cumulative_metrics, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.metrics_file)
            # fsync the parent dir so the rename itself is durable.
            try:
                dir_fd = os.open(os.path.dirname(self.metrics_file), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                # Directory fsync is best-effort (not all filesystems support
                # it); the temp+rename already gives whole-or-old-file atomicity.
                pass
        except Exception as e:
            logger.error(f"Failed to save cumulative metrics: {e}")
            # Clean up a half-written temp file so it can't be mistaken for state.
            try:
                if os.path.exists(self.metrics_file + ".tmp"):
                    os.remove(self.metrics_file + ".tmp")
            except OSError:
                pass

    # =========================================================================
    # RISK & RETURN METRICS
    # =========================================================================

    def _calculate_max_loss_with_stops(self) -> float:
        """
        Calculate worst-case loss assuming all stop losses trigger correctly.

        For MEIC breakeven design, stop_level ≈ credit, so net loss per stop ≈ $0.
        For one-sided entries (Fix #40), stop = 2×credit, net loss = credit.
        """
        total = 0.0
        for entry in self.daily_state.entries:
            call_skipped = getattr(entry, 'call_side_skipped', False)
            call_expired = getattr(entry, 'call_side_expired', False)
            put_skipped = getattr(entry, 'put_side_skipped', False)
            put_expired = getattr(entry, 'put_side_expired', False)

            # Call side contribution
            if call_skipped or call_expired:
                call_loss = 0.0
            elif entry.call_side_stopped:
                call_loss = entry.call_side_stop - entry.call_spread_credit
            else:
                # Active side: worst case = stop triggers
                call_loss = entry.call_side_stop - entry.call_spread_credit

            # Put side contribution
            if put_skipped or put_expired:
                put_loss = 0.0
            elif entry.put_side_stopped:
                put_loss = entry.put_side_stop - entry.put_spread_credit
            else:
                put_loss = entry.put_side_stop - entry.put_spread_credit

            total += max(0.0, call_loss) + max(0.0, put_loss)
        return total

    def _calculate_max_loss_catastrophic(self) -> float:
        """
        Calculate worst-case loss if all stops fail (catastrophic scenario).

        For a full IC, only one side can go to full spread width at expiry.
        For one-sided entries, only the placed side is at risk.
        """
        total = 0.0
        for entry in self.daily_state.entries:
            sw = entry.spread_width * 100 * entry.contracts

            call_skipped = getattr(entry, 'call_side_skipped', False)
            call_expired = getattr(entry, 'call_side_expired', False)
            put_skipped = getattr(entry, 'put_side_skipped', False)
            put_expired = getattr(entry, 'put_side_expired', False)

            call_active = not entry.call_side_stopped and not call_skipped and not call_expired
            put_active = not entry.put_side_stopped and not put_skipped and not put_expired

            # A side that ALSO expired/early-closed (e.g. a Brandon TP that sets
            # both *_side_expired and *_side_stopped) realized its actual P&L
            # already and carries NO catastrophic stop-residual — guard the
            # residual additions with `and not *_expired` so a profitable TP
            # isn't counted as a phantom theoretical-stop loss (audit 2026-06-02:
            # variant B's display double-counted ~$2,025 on a +$200 TP).
            if call_active and put_active:
                # Full IC: only one side can go to full loss at expiry
                entry_loss = sw - entry.total_credit
            elif call_active:
                entry_loss = sw - entry.call_spread_credit
                if entry.put_side_stopped and not put_expired:
                    entry_loss += max(0.0, entry.put_side_stop - entry.put_spread_credit)
            elif put_active:
                entry_loss = sw - entry.put_spread_credit
                if entry.call_side_stopped and not call_expired:
                    entry_loss += max(0.0, entry.call_side_stop - entry.call_spread_credit)
            else:
                # Both sides done - only realized stop losses (a side that
                # expired/TP'd contributes nothing here).
                entry_loss = 0.0
                if entry.call_side_stopped and not call_expired:
                    entry_loss += max(0.0, entry.call_side_stop - entry.call_spread_credit)
                if entry.put_side_stopped and not put_expired:
                    entry_loss += max(0.0, entry.put_side_stop - entry.put_spread_credit)

            total += max(0.0, entry_loss)
        return total

    def _calculate_capital_deployed(self) -> float:
        """Peak CONCURRENT margin tied up across today's entries.

        Prior version SUMMED every entry's max-loss (spread_width × $100 ×
        contracts). For sessions where TPs cycled fast, the same dollars
        of margin got reused multiple times — the sum overstated capital.

        Implementation notes (2026-05-08 v2 — earlier v1 was a no-op):

        Two timing-related complications make this less straightforward than
        a textbook sweep-line:

        1. `entry.entry_time` is a `datetime`, but `entry.{call,put}_stop_time`
           is an ISO-format STRING (set via `.isoformat()` in stop paths).
           Comparing them with `str()` on the datetime gives "YYYY-MM-DD H…"
           (space) vs "YYYY-MM-DDTH…" (T) — space < T in ASCII, so all opens
           sort before all closes lexically and the sweep produces the SUM
           rather than the peak. v1 fix had this latent bug.

        2. Brandon TP/breach close paths route through `_close_entry_early`,
           which sets `*_side_expired=True` but does NOT set call_stop_time
           or put_stop_time. Without a close timestamp on those entries the
           sweep treats them as still-open through end-of-session — also
           collapsing back to the SUM behaviour. v1 fix had this bug too.

        Fix:
          • Coerce all timestamps to comparable datetimes via `_to_dt()`.
          • If neither stop_time is populated but the side is marked
            stopped/expired, treat the entry's most-recent disposition flag
            transition as "closed at heartbeat-now" — pessimistic but
            captures the freed-margin moment for non-HYDRA close paths.
          • Tie-break: closes before opens at the same instant, so a close
            and a same-second open don't double-count concurrent margin.
        """
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        def _to_dt(t) -> Optional[_dt]:
            """Coerce datetime / ISO string / empty-string to a datetime."""
            if t is None or t == "":
                return None
            if isinstance(t, _dt):
                return t
            try:
                return _dt.fromisoformat(str(t))
            except (ValueError, TypeError):
                return None

        # "Now" used for entries that are clearly closed but have no
        # explicit close timestamp (Brandon TP/breach path). Using now
        # rather than entry-time means closed entries aren't treated as
        # eternally open.
        now = _dt.now(_tz.utc)

        intervals = []
        any_missing_timing = False
        for entry in self.daily_state.entries:
            if entry.spread_width <= 0:
                continue
            margin = entry.spread_width * 100 * entry.contracts
            open_t = _to_dt(getattr(entry, "entry_time", None))
            if open_t is None:
                any_missing_timing = True
                continue
            # Prefer the unified close_time (set by HYDRA's _close_entry_early
            # for Brandon TP/breach paths). Fall back to side-specific
            # stop_time for HYDRA stop paths that haven't been migrated.
            close_t = (
                _to_dt(getattr(entry, "close_time", None))
                or _to_dt(getattr(entry, "call_stop_time", None))
                or _to_dt(getattr(entry, "put_stop_time", None))
            )
            # If no explicit close_time but the entry is actually closed
            # (any disposition flag set on a placed side), use "now" so
            # the margin is freed in the sweep instead of held forever.
            if close_t is None:
                placed_call = bool(getattr(entry, "short_call_position_id", None))
                placed_put = bool(getattr(entry, "short_put_position_id", None))
                call_done = (
                    getattr(entry, "call_side_stopped", False)
                    or getattr(entry, "call_side_expired", False)
                    or getattr(entry, "call_side_skipped", False)
                )
                put_done = (
                    getattr(entry, "put_side_stopped", False)
                    or getattr(entry, "put_side_expired", False)
                    or getattr(entry, "put_side_skipped", False)
                )
                # Same logic as is_complete in _close_entry_early but
                # evaluated locally so we don't depend on is_complete being
                # set correctly (it's True from placement, not from close).
                if placed_call and placed_put:
                    closed = call_done and put_done
                elif placed_call:
                    closed = call_done
                elif placed_put:
                    closed = put_done
                else:
                    closed = False
                if closed:
                    close_t = now

            # Coerce both to UTC-aware datetimes so comparisons work
            # regardless of how each timestamp was set. Naive datetimes
            # are assumed to be UTC.
            if open_t is not None and open_t.tzinfo is None:
                open_t = open_t.replace(tzinfo=_tz.utc)
            if close_t is not None and close_t.tzinfo is None:
                close_t = close_t.replace(tzinfo=_tz.utc)

            intervals.append((open_t, close_t, margin))

        if not intervals:
            return sum(
                e.spread_width * 100 * e.contracts
                for e in self.daily_state.entries
                if e.spread_width > 0
            )

        # Sweep-line. Process closes BEFORE opens at the same instant so a
        # close-then-immediate-reopen doesn't double the running total.
        events = []
        for open_t, close_t, margin in intervals:
            events.append((open_t, 0, margin))   # open: kind=0 (positive margin)
            if close_t is not None:
                events.append((close_t, -1, margin))  # close: kind=-1 sorts BEFORE 0
        events.sort(key=lambda x: (x[0], x[1]))

        peak = 0.0
        running = 0.0
        for _, kind, margin in events:
            running += margin if kind == 0 else -margin
            peak = max(peak, running)

        if any_missing_timing:
            peak += sum(
                e.spread_width * 100 * e.contracts
                for e in self.daily_state.entries
                if e.spread_width > 0 and getattr(e, "entry_time", None) is None
            )
        return peak

    def _calculate_sortino_ratio(self, today_net_pnl: float, today_capital: float) -> float:
        """
        Calculate annualized Sortino ratio from historical daily returns.

        Sortino = (mean_return - 0) / downside_deviation * sqrt(252)
        Uses % returns (net_pnl / capital_deployed) for each day.
        Returns 0.0 if < 2 days of data, 99.99 if no losing days.
        """
        daily_returns = self.cumulative_metrics.get("daily_returns", [])

        all_returns = [d["return_pct"] for d in daily_returns]
        if today_capital > 0:
            all_returns.append(today_net_pnl / today_capital)

        if len(all_returns) < 2:
            return 0.0

        mean_return = sum(all_returns) / len(all_returns)
        downside_sq = [r ** 2 for r in all_returns if r < 0]

        if not downside_sq:
            return 99.99  # No losing days - cap display

        downside_dev = math.sqrt(sum(downside_sq) / len(all_returns))
        if downside_dev == 0:
            return 99.99

        sortino = (mean_return / downside_dev) * math.sqrt(252)
        return round(min(99.99, max(-99.99, sortino)), 2)

    # =========================================================================
    # ACCOUNT & DASHBOARD LOGGING (matching Iron Fly interface)
    # =========================================================================

    def log_daily_summary(self):
        """
        Log and send daily summary after settlement is confirmed.

        Called from main.py AFTER check_after_hours_settlement() returns True,
        ensuring that all 0DTE positions have been settled by Saxo and we have
        accurate final P&L figures.

        This method:
        1. Sends Telegram/Email alert via alert_service
        2. Logs to Google Sheets Daily Summary tab
        3. Updates cumulative metrics (winning/losing days, total P&L)
        4. Saves cumulative metrics to disk
        """
        # Fix #71 + Fix #83: Guard against duplicate daily summary after restart
        # On restart, main.py's local daily_summary_sent_date resets to None,
        # which allows this method to be called again for the same day.
        # Check last_updated trading date in cumulative_metrics to detect duplicates.
        # Fix #83: last_updated now stores the TRADING DATE (YYYY-MM-DD), not a timestamp.
        # Previously, midnight settlement for day N stored day N+1's timestamp, blocking
        # the entire next trading day's summary.
        last_updated_str = self.cumulative_metrics.get("last_updated", "")
        if last_updated_str:
            try:
                metrics_date = datetime.strptime(last_updated_str[:10], "%Y-%m-%d").date()
                today_et = get_us_market_time().date()
                if metrics_date == today_et:
                    logger.warning(
                        "FIX-71: Daily summary already sent for trading date %s - skipping duplicate",
                        last_updated_str[:10]
                    )
                    return
            except (ValueError, TypeError):
                pass  # If parsing fails, proceed normally

        # FIX #75: Ensure all async fill corrections are applied before calculating summary
        self._wait_for_pending_fill_corrections(timeout=15.0)

        # Get summary data
        summary = self.get_daily_summary()

        # Fix #72: Use NET P&L (after commission) for all tracking
        # Previously used gross P&L which overstated actual returns
        net_pnl = summary.get("net_pnl", summary["total_pnl"])
        commission = summary.get("total_commission", 0)

        # Send alert (Telegram/Email)
        self._send_daily_summary()

        # Log to Google Sheets Daily Summary tab
        # Add extra fields needed by the logger
        capital_deployed = self._calculate_capital_deployed()
        cumulative_pnl = self.cumulative_metrics.get("cumulative_pnl", 0) + net_pnl

        # Compute cumulative ROC metrics (including today's values)
        past_returns = self.cumulative_metrics.get("daily_returns", [])
        past_capital_sum = sum(r.get("capital_deployed", 0) for r in past_returns)
        trading_days = len(past_returns) + 1  # +1 for today
        avg_capital_deployed = (past_capital_sum + capital_deployed) / trading_days if trading_days > 0 else 0
        cumulative_roc = (cumulative_pnl / avg_capital_deployed * 100) if avg_capital_deployed > 0 else 0
        avg_daily_roc = cumulative_roc / trading_days if trading_days > 0 else 0
        annualized_return = avg_daily_roc * 252  # Simple (non-compounded) annualization

        sheets_summary = {
            **summary,
            # Market data OHLC
            "spx_open": self.market_data.spx_open,
            "spx_close": self.current_price,
            "spx_high": self.market_data.spx_high,
            "spx_low": self.market_data.spx_low if self.market_data.spx_low != float('inf') else 0.0,
            "vix_open": self.market_data.vix_open,
            "vix_close": self.current_vix,
            "vix_high": self.market_data.vix_high,
            "vix_low": self.market_data.vix_low if self.market_data.vix_low != float('inf') else 0.0,
            # P&L
            "daily_pnl": net_pnl,
            "total_commission": commission,
            "cumulative_pnl": cumulative_pnl,
            # Risk & return metrics (use PEAK intraday values, not end-of-day snapshot)
            # At settlement all entries are settled, so snapshot methods would just show
            # realized stop losses. Investors need peak risk exposure during the day:
            #   Peak Max Loss (Stops) = total credit (MEIC breakeven: stop ≈ credit)
            #   Peak Max Loss (Catastrophic) = capital - credit (one side goes full width)
            "max_loss_stops": summary["total_credit"],
            "max_loss_catastrophic": max(0, capital_deployed - summary["total_credit"]),
            "capital_deployed": capital_deployed,
            "return_on_capital": (net_pnl / capital_deployed * 100) if capital_deployed > 0 else 0,
            "sortino_ratio": self._calculate_sortino_ratio(net_pnl, capital_deployed),
            # Cumulative ROC metrics
            "avg_capital_deployed": avg_capital_deployed,
            "cumulative_roc": cumulative_roc,
            "avg_daily_roc": avg_daily_roc,
            "annualized_return": annualized_return,
            # MKT-018: Early close columns (safe for base MEIC which doesn't have these attrs)
            "early_close": "Yes" if getattr(self, '_early_close_triggered', False) else "No",
            "early_close_time": getattr(self, '_early_close_time', None).strftime('%H:%M ET') if getattr(self, '_early_close_time', None) else "",
            "notes": (
                f"MKT-018 Early close at {getattr(self, '_early_close_time', None).strftime('%I:%M %p ET')}"
                if getattr(self, '_early_close_triggered', False) and getattr(self, '_early_close_time', None)
                else ("Post-settlement" if self._settlement_reconciliation_complete else "")
            ),
            # Phase 2 D-4: Sheets Daily Summary new Contracts column
            "contracts_per_entry": self.contracts_per_entry,
        }

        # Convert P&L to EUR if exchange rate available
        try:
            rate = self._read_fx_rate("USD", "EUR")
            if rate:
                sheets_summary["daily_pnl_eur"] = net_pnl * rate
                sheets_summary["cumulative_pnl_eur"] = cumulative_pnl * rate
        except Exception:
            sheets_summary["daily_pnl_eur"] = 0
            sheets_summary["cumulative_pnl_eur"] = 0

        if self.trade_logger:
            self.trade_logger.log_daily_summary(sheets_summary)
            logger.info(f"Daily summary logged to Google Sheets (Net P&L: ${net_pnl:.2f}, Commission: ${commission:.2f})")

        # Apply this day's results to the LIFETIME cumulative metrics + persist.
        # Idempotent by date — see _book_daily_cumulative.
        self._book_daily_cumulative(summary, net_pnl, capital_deployed)

    def _book_daily_cumulative(self, summary: dict, net_pnl: float,
                               capital_deployed: float) -> None:
        """Apply one trading day's results to the cumulative metrics + persist.

        IDEMPOTENT BY DATE (2026-06-08): if this date was already booked (its
        row is present in ``daily_returns``), DO NOT re-apply. A re-trigger of
        ``log_daily_summary`` for the same day — e.g. a restart that reset
        main.py's ``daily_summary_sent_date`` local (the Fix #82 scenario) —
        would otherwise double-count net P&L + entries into the lifetime totals
        AND append a duplicate ``daily_returns`` row. Observed: 2026-06-02 was
        booked twice on both A and C, inflating ``cumulative_pnl`` by that day's
        net P&L. The guard reads the in-memory ``cumulative_metrics`` (reloaded
        from disk at startup), so it catches both a same-process double-call and
        a restart re-run. (A 0-capital day appends no daily_returns row, but its
        increments are ~0 entries / ~0 P&L, so the edge is benign.)
        """
        date = summary["date"]
        if any(d.get("date") == date
               for d in self.cumulative_metrics.get("daily_returns", [])):
            logger.warning(
                f"Cumulative metrics already booked for {date} — skipping "
                f"re-apply (idempotent; avoids double-count). Expected no-op on "
                f"a same-day restart re-run of log_daily_summary."
            )
            return

        # Update cumulative metrics (using net P&L)
        self.cumulative_metrics["cumulative_pnl"] += net_pnl
        self.cumulative_metrics["total_entries"] += summary["entries_completed"]
        self.cumulative_metrics["total_credit_collected"] += summary["total_credit"]
        self.cumulative_metrics["total_stops"] += summary["call_stops"] + summary["put_stops"]
        self.cumulative_metrics["double_stops"] += summary["double_stops"]

        # Store daily return for Sortino ratio calculation
        if "daily_returns" not in self.cumulative_metrics:
            self.cumulative_metrics["daily_returns"] = []
        if capital_deployed > 0:
            self.cumulative_metrics["daily_returns"].append({
                "date": date,
                "net_pnl": net_pnl,
                "capital_deployed": capital_deployed,
                "return_pct": net_pnl / capital_deployed,
                # v8: record contract count per day so downstream (HERMES per-contract avg,
                # CLIO weekly aggregation) can normalize mixed-contract history correctly.
                # Readers that predate v8 should default to 1 via .get("contracts_per_entry", 1).
                "contracts_per_entry": self.contracts_per_entry,
            })

        # Derive winning/losing-day counts from daily_returns (the authoritative
        # per-TRADING-day record) instead of blind-incrementing. The old
        # `winning_days += 1` ran for EVERY day including 0-capital NO-TRADE days
        # (net_pnl 0 >= 0 counted as a "win") AND — because the idempotency guard
        # above keys on daily_returns, which a 0-capital day never appends to —
        # re-counted that phantom win on every restart, inflating winning_days
        # above the real daily_returns row count (observed 2026-06-10 on variant
        # C: winning_days 18 vs 14 actual trading days). Deriving makes the
        # counters self-healing + idempotent by construction, and correctly
        # treats a no-trade day as neither a win nor a loss.
        _dr = self.cumulative_metrics["daily_returns"]
        self.cumulative_metrics["winning_days"] = sum(1 for d in _dr if d.get("net_pnl", 0) >= 0)
        self.cumulative_metrics["losing_days"] = sum(1 for d in _dr if d.get("net_pnl", 0) < 0)

        self._save_cumulative_metrics(trading_date=date)

    def update_market_data(self):
        """Public method to update market data (called by main.py)."""
        self._update_market_data()

    # =========================================================================
    # WEBSOCKET PRICE CACHE (PRESERVED - NOT USED IN REST-ONLY MODE)
    # =========================================================================
    # These methods are preserved for potential future use if we need to enable
    # WebSocket streaming due to API rate limits with many bots.
    # Currently disabled: main.py has USE_WEBSOCKET_STREAMING = False
    # and _update_entry_prices() fetches from REST API directly.

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
            VIGILANT_CHECK_INTERVAL_SECONDS for vigilant mode,
            NORMAL_CHECK_INTERVAL_SECONDS for normal mode
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
        - Skipped sides don't count against win (HYDRA one-sided entries)

        Args:
            entry: IronCondorEntry to evaluate

        Returns:
            True if this entry is a win
        """
        # For one-sided entries (HYDRA), only check the side that was opened
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
        # For one-sided entries (HYDRA), can't be breakeven (either win or loss)
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
        # For one-sided entries (HYDRA), loss if the placed side was stopped
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
        Get comprehensive MEIC strategy metrics for the HYDRA dashboard.
        (Formerly fed the Google Sheets / Looker tabs named below — both retired
        2026-07-18; the field names are kept as the metric contract.)

        Returns all metrics needed for:
        - Account Summary worksheet (positions, credits, P&L)
        - Performance Metrics worksheet (entries, stops, win rate)
        - Position Details worksheet (strikes, spreads, status)

        Returns:
            dict: Complete strategy metrics for dashboard logging
        """
        # Fetch positions once and reuse (avoid multiple API calls)
        positions = self._read_open_positions()

        # Basic status
        active_entries = len(self.daily_state.active_entries)
        # Use the broker's authoritative per-entry P&L (pass positions to avoid re-fetch)
        unrealized = 0.0
        for entry in self.daily_state.active_entries:
            unrealized += self._get_broker_pnl_for_entry(entry, positions=positions)
        total_pnl = self.daily_state.total_realized_pnl + unrealized

        # Position counts
        # IBKR has no per-leg position id — count open legs by conid (*_uic).
        total_legs = sum(
            len([1 for leg in LEG_NAMES
                 if getattr(e, f'{leg}_uic')])
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

        # Per-entry details (use broker P&L for each entry, reuse positions)
        entry_details = []
        for entry in self.daily_state.entries:
            # Get broker P&L if entry has any open legs (pass positions to avoid re-fetch).
            # IBKR has no per-leg position id — key on the leg conids (*_uic).
            has_any_positions = any([
                entry.short_call_uic,
                entry.long_call_uic,
                entry.short_put_uic,
                entry.long_put_uic
            ])
            entry_pnl = self._get_broker_pnl_for_entry(entry, positions=positions) if has_any_positions else 0
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

    # =========================================================================
    # ORDER-004: PRE-ENTRY MARGIN CHECK
    # =========================================================================

    def _min_buying_power_per_unit(self) -> float:
        """Per-contract margin floor used by the ORDER-004 buying-power gate.

        Defined-risk default = the IC floor. A naked strategy (strangle)
        overrides this (S2) to a much larger naked-margin floor.
        """
        return self.min_buying_power_per_ic

    def _check_buying_power(self) -> Tuple[bool, str]:
        """
        ORDER-004: Check if we have sufficient buying power for a new IC entry.

        Verifies that available margin exceeds MIN_BUYING_POWER_PER_IC before
        allowing a new entry.

        IBKR path (current): _read_account_balance maps IBKR's
        get_balance()['tradable'] to the single field MarginAvailableForTrading,
        plus a _raw blob. IBKR does NOT surface per-position used-margin or a
        utilization percentage, so the diagnostic margin_used / utilization /
        total_value reads below .get() those with a 0 default and the snapshot
        log line reports 0 for them. The gate decision itself uses only
        MarginAvailableForTrading (available < required), which is correct.

        The extra field names in the fallback loop below (SpendingPower,
        CashAvailableForTrading, NetEquityForMargin) and the used-margin /
        utilization / total_value reads are Saxo-era leftovers retained only as
        harmless no-ops on the IBKR path — they never match and so never affect
        the decision. They are kept for backwards-compat with the Saxo balance
        shape should that broker be reattached.

        Returns:
            Tuple of (has_sufficient_bp, message)
        """
        if not MARGIN_CHECK_ENABLED:
            return True, "Margin check disabled"

        try:
            # GAP-F (F7.4): broker-agnostic — _read_account_balance keys
            # the IB balance with the same "MarginAvailableForTrading"
            # field name the Fix #85 lookup below reads.
            balance = self._read_account_balance()
            if not balance:
                logger.warning("ORDER-004: Could not fetch account balance")
                return True, "Balance check skipped (API unavailable)"

            # IBKR path: only MarginAvailableForTrading is populated (from
            # get_balance()['tradable']); the remaining names are inert Saxo-era
            # fallbacks (see docstring). Priority order preserved for the Saxo
            # shape: MarginAvailableForTrading → SpendingPower → CashAvailableForTrading
            # → NetEquityForMargin (total equity, last resort).
            available = None
            field_used = None
            for field in ["MarginAvailableForTrading", "SpendingPower",
                          "CashAvailableForTrading", "NetEquityForMargin"]:
                if field in balance:
                    available = balance[field]
                    field_used = field
                    break

            if available is None:
                logger.warning(
                    "ORDER-004: No recognized margin field in balance response. "
                    f"Available keys: {list(balance.keys())[:10]}"
                )
                return True, "Balance check skipped (no margin field)"

            # Log margin snapshot for diagnostics + store for DataRecorder.
            # On the IBKR path these fields are absent (IBKR doesn't surface
            # used-margin / utilization / total value). utilization_pct is stored
            # as None (not 0) so the DB column is honestly NULL rather than a
            # misleading "0% used" — the gate itself uses `available`.
            margin_used = balance.get("MarginUsedByCurrentPositions", 0)
            margin_pct = balance.get("MarginUtilizationPct")  # None if not surfaced (IBKR)
            total_value = balance.get("TotalValue", 0)
            _util_str = f"{margin_pct:.1f}%" if margin_pct is not None else "n/a"
            logger.info(
                f"ORDER-004: Margin snapshot — "
                f"Available: ${available:,.2f} (via {field_used}), "
                f"Used: ${abs(margin_used):,.2f}, "
                f"Utilization: {_util_str}, "
                f"Account: ${total_value:,.2f}"
            )

            # Store for DataRecorder (read by _record_entry_to_db)
            self._last_margin_snapshot = {
                "available": available,
                "used": margin_used,
                "utilization_pct": margin_pct,
                "total_value": total_value,
            }

            # Calculate required margin for next entry.
            # Each IC needs max(call_spread, put_spread) × $100 × contracts.
            # The per-unit floor is overridable (S2): a defined-risk IC uses
            # `min_buying_power_per_ic`; a NAKED strategy (strangle) overrides
            # `_min_buying_power_per_unit` to a much larger naked-margin floor,
            # because the IC floor (~$5k/contract) grossly underestimates an
            # undefined-risk short.
            per_unit_floor = self._min_buying_power_per_unit()
            required = per_unit_floor * self.contracts_per_entry

            if available < required:
                # In dry-run, log the would-be rejection for diagnostics but
                # don't actually block the entry. The whole point of dry-run
                # is to record what the strategy WOULD fire under ideal
                # conditions, not filter it through today's account margin.
                # Saxo positions are synthetic (DRY_*) so no real money moves.
                # Re-engages automatically when dry_run flips to false.
                if self.dry_run:
                    logger.info(
                        f"ORDER-004 (dry-run): would-be insufficient BP — "
                        f"Available ${available:,.2f} < Required ${required:,.2f} "
                        f"({self.contracts_per_entry}c × ${per_unit_floor:,.0f}). "
                        f"Bypassing margin gate for data collection."
                    )
                    return True, (
                        f"BP bypass (dry-run): ${available:,.2f} < ${required:,.2f}, "
                        f"would reject in live"
                    )
                logger.warning(
                    f"ORDER-004: Insufficient buying power. "
                    f"Available: ${available:,.2f}, Required: ${required:,.2f} "
                    f"({self.contracts_per_entry}c × ${per_unit_floor:,.0f}), "
                    f"Utilization: {_util_str}"
                )
                return False, (
                    f"Insufficient BP: ${available:,.2f} < ${required:,.2f} "
                    f"({self.contracts_per_entry}c, margin {_util_str} used)"
                )

            return True, f"BP OK: ${available:,.2f} (margin {_util_str} used, req ${required:,.0f} at {self.contracts_per_entry}c)"

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

            # GAP-E (F7.3): broker-agnostic SPX price. _read_index_price
            # returns a usable price (mid→last→mark) or None; a None
            # while the schedule says open is the halt heuristic — no
            # broker exposes a trading-halt flag directly.
            # #17/#18: a broker NON-real-time availability flag is treated as
            # no usable data here too — a frozen/unentitled-but-nonzero quote
            # during the session is exactly the degraded-feed/halt case this
            # guards. First char of 6509 in (Z=Frozen, Y=Frozen-Delayed,
            # N=Not-Subscribed) all mean "not real-time" → treat as halt.
            price, avail = self._split_index_read(self._read_index_price(self.underlying_symbol))
            if not price:
                logger.warning(
                    "MKT-005: No SPX price available — possible market halt"
                )
                return True, "No SPX price available"
            avail_first = avail[:1].upper() if isinstance(avail, str) and avail else None
            if avail_first in ("Z", "Y", "N"):
                logger.warning(
                    "MKT-005: SPX quote flagged NOT real-time (availability=%r) "
                    "— treating as no data / possible halt", avail
                )
                return True, f"SPX price not real-time (availability={avail!r})"

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
                adjustment = self.strike_increment * adjustment_direction
                strike += adjustment
                continue

            # Check quote for liquidity (F7.6: broker-agnostic via
            # _read_option_quote — returns normalized {bid, ask, ...}).
            quote = self._read_option_quote(uic)
            if not quote:
                # No quote - try adjusted strike
                adjustment = self.strike_increment * adjustment_direction
                strike += adjustment
                continue

            bid = quote.get("bid") or 0
            ask = quote.get("ask") or 0

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
                adjustment = self.strike_increment * adjustment_direction
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
        max_attempts = int((current_spread - min_spread_width) / self.strike_increment)
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
                long_strike += adjustment_direction * self.strike_increment
                continue

            # Check quote for liquidity (F7.6: broker-agnostic via
            # _read_option_quote — normalized {bid, ask, ...}).
            quote = self._read_option_quote(uic)
            if not quote:
                long_strike += adjustment_direction * self.strike_increment
                continue

            bid = quote.get("bid") or 0
            ask = quote.get("ask") or 0

            # Check if liquid
            if bid > 0 and ask > 0:
                spread_percent = ((ask - bid) / bid) * 100 if bid > 0 else float('inf')
                # Fix #83: Cheap wings (bid <= $0.25) always have wide % spreads
                # but tiny $ impact — accept them as liquid enough
                if spread_percent < MAX_BID_ASK_SPREAD_PERCENT_SKIP or bid <= ORDER_006_CHEAP_OPTION_THRESHOLD:
                    # Found liquid (or acceptably cheap) strike
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
                long_strike += adjustment_direction * self.strike_increment

        # Could not find liquid long wing - return original
        # MKT-010: Still mark as illiquid so HYDRA can use one-sided entry
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
        TIME-001: Verify broker API connectivity at startup.

        A broker balance read is used as a lightweight connectivity
        probe. The Client Portal Web API does not expose a server
        clock, so actual skew is not measured — consider an NTP check
        if precise skew detection is ever needed.
        """
        try:
            # Use a broker balance read as a connectivity probe.
            balance = self._read_account_balance()
            if balance:
                # If successful, we can at least verify our connection works
                self._clock_validated = True

                # Get local time
                local_time = get_us_market_time()
                logger.info(f"TIME-001: Clock validation - Local time: {local_time.strftime('%H:%M:%S')}")

                # Without actual server time, we can only log local time
                self._clock_skew_seconds = 0.0
                logger.info("TIME-001: Clock validation passed (server time comparison not available)")
                return

            logger.warning("TIME-001: Could not validate clock - API unavailable")
            self._clock_validated = False

        except Exception as e:
            logger.error(f"TIME-001: Clock validation error: {e}")
            self._clock_validated = False

    # =========================================================================
    # CONFIG-001: CONFIGURATION VALIDATION
    # =========================================================================

    def _load_instrument_params(self) -> None:
        """Read instrument + expiry parameters from config (modularity-audit item 2).

        Every default equals today's SPX/0DTE literal, so a config without these
        keys produces byte-identical behavior. These fields back the call sites
        that used to hardcode SPX / VIX / SPXW / CBOE / the 5pt strike grid /
        today-expiry — threaded through in the commits that follow.
        """
        cfg = self.strategy_config
        self.underlying_symbol = cfg.get("underlying_symbol", "SPX")
        self.volatility_symbol = cfg.get("volatility_symbol", "VIX")
        self.trading_class = cfg.get("trading_class", "SPXW")
        self.exchange = cfg.get("exchange", "CBOE")
        self.strike_increment = cfg.get("strike_increment", 5)
        self.target_dte = cfg.get("target_dte", 0)
        self._assert_instrument_parameterized()

    def _assert_instrument_parameterized(self) -> None:
        """Fail fast if any instrument field is unset/invalid — a guard so a
        future edit that re-introduces a hardcoded symbol/exchange/grid/expiry
        (instead of threading the config value) trips at startup, not mid-trade.
        """
        missing = [
            n for n in ("underlying_symbol", "volatility_symbol", "trading_class", "exchange")
            if not getattr(self, n, None)
        ]
        if missing:
            raise ConfigError(f"instrument params unset: {missing}")
        if not (isinstance(self.strike_increment, (int, float))
                and not isinstance(self.strike_increment, bool)
                and self.strike_increment > 0):
            raise ConfigError(
                f"strike_increment must be a positive number, got {self.strike_increment!r}")
        if not (isinstance(self.target_dte, int)
                and not isinstance(self.target_dte, bool)
                and self.target_dte >= 0):
            raise ConfigError(f"target_dte must be an int >= 0, got {self.target_dte!r}")

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

        # P7-audit H3: post-Saxo-purge config validation. Only the
        # `strategy` section is required — IBKR credentials are loaded
        # by shared/ib_oauth.load_credentials() (env vars or systemd
        # credentials), NOT from this JSON config. A stale `saxo_api`
        # block in the config (legacy) is now harmless and ignored.
        if "strategy" not in self.config:
            errors.append("Missing required config section: strategy")

        strategy = self.config.get("strategy", {})

        # Legacy UIC fields (e.g. underlying_uic) are vestigial — on
        # IBKR instruments are resolved by conid via _read_option_chain
        # / qualify_contract. If still present, they must at least be
        # well-typed; absence is fine.
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

        # Contracts validation — respect per-config override so variants
        # that intentionally size up (Brandon narrow-spread) aren't blocked
        # by the 1c baseline cap.
        contracts = strategy.get("contracts_per_entry", 1)
        per_order_cap = strategy.get("max_contracts_per_order", MAX_CONTRACTS_PER_ORDER)
        if contracts < 1 or contracts > per_order_cap:
            errors.append(f"Invalid contracts_per_entry: {contracts} (must be 1-{per_order_cap})")

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
            call_long_sold = getattr(entry, 'call_long_sold', False)
            if entry.short_call_price == 0 and entry.long_call_price == 0 and not call_long_sold:
                logger.warning(
                    f"DATA-004: Entry #{entry.entry_number} call side has zero prices "
                    f"(SC=${entry.short_call_price:.2f}, LC=${entry.long_call_price:.2f}) - skipping stop check"
                )
                return False, "Call side prices are zero"
            # If only one leg is zero, that's suspicious — UNLESS the long was sold (LP=$0 is legitimate)
            if not call_long_sold and (entry.short_call_price == 0 or entry.long_call_price == 0):
                logger.warning(
                    f"DATA-004: Entry #{entry.entry_number} call side has partial zero prices "
                    f"(SC=${entry.short_call_price:.2f}, LC=${entry.long_call_price:.2f}) - skipping stop check"
                )
                return False, "Call side has partial zero price"

        if not entry.put_side_stopped:
            put_long_sold = getattr(entry, 'put_long_sold', False)
            if entry.short_put_price == 0 and entry.long_put_price == 0 and not put_long_sold:
                logger.warning(
                    f"DATA-004: Entry #{entry.entry_number} put side has zero prices "
                    f"(SP=${entry.short_put_price:.2f}, LP=${entry.long_put_price:.2f}) - skipping stop check"
                )
                return False, "Put side prices are zero"
            # If only one leg is zero, that's suspicious — UNLESS the long was sold (LP=$0 is legitimate)
            if not put_long_sold and (entry.short_put_price == 0 or entry.long_put_price == 0):
                logger.warning(
                    f"DATA-004: Entry #{entry.entry_number} put side has partial zero prices "
                    f"(SP=${entry.short_put_price:.2f}, LP=${entry.long_put_price:.2f}) - skipping stop check"
                )
                return False, "Put side has partial zero price"

        pnl = entry.unrealized_pnl

        # DATA-003: Check for impossible P&L values
        # Max profit = 100% of credit received (all options expire worthless)
        # Use entry's actual credit, not fixed constant, because credits vary
        max_profit = entry.total_credit if entry.total_credit > 0 else self.max_pnl_per_ic
        # Add 20% buffer for floating point and timing differences
        max_profit_with_buffer = max_profit * 1.2

        if pnl > max_profit_with_buffer:
            logger.error(
                f"DATA-003: Impossible P&L detected for Entry #{entry.entry_number}: "
                f"${pnl:.2f} > max ${max_profit_with_buffer:.2f} (credit=${entry.total_credit:.2f})"
            )
            return False, f"P&L too high: ${pnl:.2f}"

        # L-H3: a loss MORE negative than the floor must NEVER suppress the
        # stop. The DATA-004 zero-price gate above already catches the real
        # data-corruption case ($0 prices → false stop); a large loss with
        # NON-zero prices is a genuine fast/deep loss and is EXACTLY when the
        # stop must fire. The old `return False` here made a deeper/faster loss
        # LESS likely to stop (the confirming MKT-046 tick never completes once
        # pnl dips below the floor). Log the anomaly for investigation but let
        # the stop check PROCEED. The min-loss floor scales with contracts.
        min_loss_floor = self.min_pnl_per_ic * max(1, getattr(entry, "contracts", self.contracts_per_entry))
        if pnl < min_loss_floor:
            logger.warning(
                f"DATA-003: Entry #{entry.entry_number} P&L ${pnl:.2f} is below "
                f"the min-loss floor ${min_loss_floor:.2f} — PROCEEDING with the "
                f"stop check (a large loss must stop faster, not be suppressed)"
            )

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

        # Calculate max possible P&L: use actual total credit collected (known from entries)
        # as the true ceiling, with 10% buffer for rounding. Falls back to MAX_PNL_PER_IC
        # per entry if no credit data available.
        num_entries = max(1, self.daily_state.entries_completed)
        actual_credit = getattr(self.daily_state, 'total_credit_received', 0) or 0
        if actual_credit > 0:
            max_possible = actual_credit * 1.1  # 10% buffer above collected credit
        else:
            max_possible = self.max_pnl_per_ic * num_entries * self.contracts_per_entry
        min_possible = self.min_pnl_per_ic * num_entries * self.contracts_per_entry

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

        # Contract count for title annotation: take the max across stopped entries
        # in case this is a mixed-contract day (after a mid-day config flip).
        _batch_contracts = max(
            (getattr(e, 'contracts', 1) for e, _, _ in entries_stopped),
            default=self.contracts_per_entry,
        )
        self.alert_service.send_alert(
            alert_type=AlertType.STOP_LOSS,
            title=f"Multiple Stops Triggered ({count})",
            message=f"{count} stop losses triggered in rapid succession.\n\n{details_text}\n\nTotal loss: ${total_loss:.2f}",
            priority=AlertPriority.HIGH,
            details={
                "count": count,
                "entries": [e.entry_number for e, _, _ in entries_stopped],
                "total_loss": total_loss
            },
            contracts=_batch_contracts,
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
            # Send this one with per-leg breakdown (MKT-025: short closed, long held)
            if side == "call":
                short_strike = entry.short_call_strike
                long_strike = entry.long_call_strike
                credit = entry.call_spread_credit
                short_label = "SC"
                long_label = "LC"
            else:
                short_strike = entry.short_put_strike
                long_strike = entry.long_put_strike
                credit = entry.put_spread_credit
                short_label = "SP"
                long_label = "LP"

            # Format depends on stop mode (MKT-025 short-only vs both legs)
            if getattr(self, 'short_only_stop', False):
                short_line = f"{short_label} {short_strike:.0f} closed (short only)"
                long_line = f"{long_label} {long_strike:.0f} held → settles EOD"
            else:
                short_line = f"{short_label} {short_strike:.0f} closed"
                long_line = f"{long_label} {long_strike:.0f} closed"

            # MKT-036: Add confirmation context to alert
            conf_line = ""
            alert_conf_seconds = 0
            alert_breach_recoveries = 0
            alert_breach_time = getattr(entry, f'{side}_breach_time', None)
            if alert_breach_time is not None:
                alert_conf_seconds = int((datetime.now() - alert_breach_time).total_seconds())
                alert_breach_recoveries = getattr(entry, f'{side}_breach_count', 0)
                conf_line = f"\nConfirmed: {alert_conf_seconds}s ({alert_breach_recoveries} prior recoveries)"

            stop_msg = (
                f"*Entry #{entry.entry_number}* {side.upper()} stopped\n\n"
                f"{short_line}\n"
                f"{long_line}\n\n"
                f"Credit: ${credit:.0f} | Stop: ${stop_level:.0f}\n"
                f"*Side loss: -${net_loss:.0f}*\n"
                f"SPX: {self.current_price:,.2f}"
                f"{conf_line}"
            )

            self.alert_service.send_alert(
                alert_type=AlertType.STOP_LOSS,
                title="Stop Loss Hit",
                message=stop_msg,
                priority=AlertPriority.HIGH,
                details={
                    "description": f"{self.BOT_NAME} Entry #{entry.entry_number} {side.upper()} side stopped",
                    "entry_number": entry.entry_number,
                    "net_loss": net_loss,
                    "stop_level": stop_level,
                    "confirmation_seconds": alert_conf_seconds,
                    "breach_recoveries": alert_breach_recoveries,
                },
                contracts=entry.contracts,
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
