"""
MEIC Position Data Classes.

This module defines the position tracking data structures for MEIC:
- IronCondorEntry: Single iron condor with 4 legs
- MEICDailyState: Day's trading state with all entries

Extracted from strategy.py for better code organization.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


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
