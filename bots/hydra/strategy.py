"""
strategy.py - HYDRA (Trend Following Hybrid) Strategy Implementation

This module extends the base MEIC strategy with EMA-based trend direction detection
and credit validation. Before each entry, it checks 20 EMA vs 40 EMA on SPX 1-minute
bars. The EMA signal is informational only — entries are full iron condors or put-only via MKT-011.

Trend Detection (informational only, does NOT drive entry type):
- BULLISH (20 EMA > 40 EMA by >0.2%): Logged, stored for analysis
- BEARISH (20 EMA < 40 EMA by >0.2%): Logged, stored for analysis
- NEUTRAL (within 0.2%): Logged, stored for analysis

Risk Management (beyond base MEIC):
- MKT-011: Pre-entry credit gate (put-only if call non-viable, skip if put non-viable)
- MKT-018: Early close on ROC >= 3% (close all positions after entries placed)
- MKT-035: Call-only on down days (SPX drops >= 0.3% below open) with theoretical put stop

The idea comes from Tammy Chambless running MEIC alongside METF (Multiple Entry Trend Following).
For capital-constrained accounts, this hybrid combines both concepts in one bot.

Author: Trading Bot Developer
Date: 2026-02-04

Based on: bots/meic/strategy.py (MEIC v1.2.9)
See docs/HYDRA_STRATEGY_SPECIFICATION.md for full HYDRA details.
See docs/MEIC_STRATEGY_SPECIFICATION.md for base MEIC details.
"""

import json
import logging
import os
import time
import threading
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from enum import Enum

from shared.saxo_client import SaxoClient, BuySell
from shared.alert_service import AlertService, AlertType, AlertPriority
from shared.market_hours import get_us_market_time, is_early_close_day
from shared.technical_indicators import get_current_ema, calculate_atr
from shared.event_calendar import is_fomc_t_plus_one

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
    ENTRY_WINDOW_MINUTES,
    is_fomc_meeting_day,
    # P&L sanity check constants (Fix #39 - one-sided entry validation)
    PNL_SANITY_CHECK_ENABLED,
    MAX_PNL_PER_IC,
    MIN_PNL_PER_IC,
)

# =============================================================================
# HYDRA SPECIFIC FILE PATHS (separate from MEIC)
# =============================================================================

# CRITICAL: HYDRA must use separate state files from MEIC to prevent conflicts
# when both bots run simultaneously. Each bot maintains its own:
# - State file: Tracks entries, P&L, stops for the day
# - Metrics file: Historical performance tracking
# The Position Registry is SHARED (for multi-bot position isolation on same underlying)

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data"
)
HYDRA_STATE_FILE = os.path.join(DATA_DIR, "hydra_state.json")
HYDRA_METRICS_FILE = os.path.join(DATA_DIR, "hydra_metrics.json")
HYDRA_VERSION = "1.22.3"

# MKT-031: Smart Entry Window defaults
DEFAULT_SCOUT_WINDOW_MINUTES = 10
DEFAULT_SCOUT_SCORE_THRESHOLD = 65

# MKT-034: VIX-scaled entry time shifting (DISABLED since v1.10.3 — code preserved)
# When enabled via vix_time_shift.enabled, these slots replace config entry_times.
ALL_ENTRY_SLOTS = [
    dt_time(11, 14, 30),  # Slot 0: VIX < 20 start
    dt_time(11, 44, 30),  # Slot 1: VIX 20-23 start
    dt_time(12, 14, 30),  # Slot 2: VIX >= 23 start (floor)
    dt_time(12, 44, 30),  # Slot 3
    dt_time(13, 14, 30),  # Slot 4
    dt_time(13, 44, 30),  # Slot 5
    dt_time(14, 14, 30),  # Slot 6
]
VIX_GATE_CHECK_SECONDS_BEFORE = 30  # Check VIX 30s before entry
VIX_GATE_FLOOR_SLOT = 2  # Index into ALL_ENTRY_SLOTS that always enters

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
class HydraIronCondorEntry(IronCondorEntry):
    """
    Extended IronCondorEntry that tracks which sides were placed.

    For trend-following entries, only one side may be placed:
    - BULLISH trend: put_only = True (only put spread placed)
    - BEARISH trend: call_only = True (only call spread placed)
    - NEUTRAL: full IC (both sides placed, call_only=False, put_only=False)

    One-sided entries are ONLY allowed for clear trending markets.
    In NEUTRAL markets, if either side is non-viable, the entry is skipped.

    Override reasons (Fix #49):
    - "trend": One-sided due to EMA trend filter (BULLISH/BEARISH)
    - "mkt-011": One-sided due to credit gate in trending market
    - "mkt-010": One-sided due to illiquidity fallback in trending market
    - None: Full IC (no override)
    """
    # Track what was actually placed
    call_only: bool = False   # Only call spread was placed (bearish signal)
    put_only: bool = False    # Only put spread was placed (bullish signal)
    trend_signal: Optional[TrendSignal] = None  # The trend signal at entry time

    # Fix #49: Track why entry became one-sided (for correct logging)
    override_reason: Optional[str] = None  # "trend", "mkt-011", "mkt-010", "mkt-035", "mkt-038", or None

    # Fix #59: Track EMA values at entry time for Trades tab logging
    ema_20_at_entry: Optional[float] = None
    ema_40_at_entry: Optional[float] = None

    # MKT-018: True if closed early by ROC threshold (display only)
    early_closed: bool = False

    # MKT-033: Long leg salvage tracking (sell long after short stopped if profitable)
    call_long_sold: bool = False
    put_long_sold: bool = False
    call_long_sold_revenue: float = 0.0  # Gross revenue (fill_price × 100 × contracts)
    put_long_sold_revenue: float = 0.0   # Gross revenue (fill_price × 100 × contracts)

    # MKT-036: Stop confirmation timer (75s sustained breach before executing stop)
    call_breach_time: Optional[datetime] = None   # When call side first breached stop level
    put_breach_time: Optional[datetime] = None    # When put side first breached stop level
    call_breach_count: int = 0                    # How many times call side breached and recovered
    put_breach_count: int = 0                     # How many times put side breached and recovered

    # Skip tracking: human-readable reason when entry is fully skipped (both sides)
    skip_reason: str = ""  # e.g. "MKT-011: both sides below minimum credit"

    # MKT-041: Cushion recovery exit — close side that nearly stopped then recovered
    call_hit_danger: bool = False   # True if call spread_value reached >= nearstop_pct × stop_level
    put_hit_danger: bool = False    # True if put spread_value reached >= nearstop_pct × stop_level

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
# HYDRA STRATEGY
# =============================================================================

class HydraStrategy(MEICStrategy):
    """
    HYDRA (Trend Following Hybrid) Strategy Implementation.

    Extends MEICStrategy with EMA-based trend detection and credit validation:
    - Before each entry, checks 20 EMA vs 40 EMA on SPX 1-minute bars
    - Signal is informational only — logged and stored but does NOT drive entry type
    - All entries are full ICs, put-only (MKT-011/MKT-032), or call-only (MKT-035/038/040)

    Key Features (HYDRA specific, beyond base MEIC):
    - EMA Trend Signal: Informational only (logged/stored, never drives entry type)
    - Credit Gate (MKT-011): Estimates credit BEFORE placing orders, call min $2.00, put min $2.75
      MKT-029 graduated fallback (call floor $0.75, put floor $2.00).
      Call non-viable → put-only entry (MKT-032/MKT-039, VIX < 15).
      Put non-viable → call-only entry (MKT-040, 89% WR).
    - Progressive Call Tightening (MKT-020): Scans from 3.5x OTM inward for viable credit
    - Progressive Put Tightening (MKT-022): Scans from 4.0x OTM inward for viable credit
    - VIX-Scaled Spread Width (MKT-027): round(VIX × 6.0 / 5) × 5, floor 25pt, cap 110pt
    - Buffer Decay (MKT-042): Stop buffer starts at 2.10x, decays to 1x over 2.0 hours
    - Calm Entry (MKT-043): Delays entry up to 5min when SPX >15pt move in 3min
    - Anti-Whipsaw: Skips entries when intraday range > 1.75x expected move
    - VIX Regime: Adapts entries/buffers based on VIX at open (breakpoints [14, 20, 30])
    - Down-Day Call-Only: E1-E3 convert to call-only when SPX drops >= 0.57% from open
    - FOMC T+1 (MKT-038): All entries forced call-only day after FOMC announcement
    - Early Close (MKT-018): INTENTIONALLY DISABLED. Hold-to-expiry outperforms.
    - Stop formula: total_credit + asymmetric buffer (call $0.35, put $1.55)

    All other functionality (stop losses, position management, reconciliation)
    is inherited from MEICStrategy.

    Version: 1.22.3 (2026-04-09)
    """

    # Bot name for Position Registry - overrides MEIC's hardcoded "MEIC"
    # This ensures HYDRA positions are isolated in the registry
    BOT_NAME = "HYDRA"

    def __init__(
        self,
        saxo_client: SaxoClient,
        config: Dict[str, Any],
        logger_service: Any,
        dry_run: bool = False,
        alert_service: Optional[AlertService] = None
    ):
        """
        Initialize the HYDRA strategy.

        Args:
            saxo_client: Authenticated Saxo API client
            config: Strategy configuration dictionary
            logger_service: Trade logging service
            dry_run: If True, simulate trades without placing real orders
            alert_service: Optional AlertService for Telegram/Email notifications
        """
        # Initialize trend filter config BEFORE calling super().__init__
        # because parent __init__ calls methods that might need these values
        self.trend_config = config.get("trend_filter", {})
        self.trend_enabled = self.trend_config.get("enabled", True)
        self.ema_short_period = self.trend_config.get("ema_short_period", 20)
        self.ema_long_period = self.trend_config.get("ema_long_period", 40)
        self.ema_neutral_threshold = self.trend_config.get("ema_neutral_threshold", 0.002)
        self.recheck_each_entry = self.trend_config.get("recheck_each_entry", True)
        self.chart_bars_count = self.trend_config.get("chart_bars_count", 50)
        self.chart_horizon_minutes = self.trend_config.get("chart_horizon_minutes", 1)

        # Track current trend signal and EMA values for logging
        self._current_trend: Optional[TrendSignal] = None
        self._last_trend_check: Optional[datetime] = None
        self._last_ema_short: float = 0.0
        self._last_ema_long: float = 0.0
        self._last_ema_diff_pct: float = 0.0

        # CRITICAL: Set state/metrics file paths BEFORE calling parent init
        # Parent's __init__ calls _recover_positions_from_saxo() which needs the correct state file
        # and _load_cumulative_metrics() which needs the correct metrics file
        # This prevents conflicts when both MEIC and HYDRA run simultaneously
        self.state_file = HYDRA_STATE_FILE
        self.metrics_file = HYDRA_METRICS_FILE

        # Stop buffer: stop = credit + buffer (Brian's approach)
        # Must be set BEFORE super().__init__() because recovery uses it
        # Config stores in per-contract dollars ($0.10), multiply by 100 for total dollars ($10)
        strategy_cfg = config.get("strategy", {})
        self.call_stop_buffer = strategy_cfg.get("call_stop_buffer", 0.35) * 100
        # MKT-036: Asymmetric put stop buffer (wider buffer avoids false put stops)
        # If not set, falls back to call_stop_buffer for both sides
        put_buf = strategy_cfg.get("put_stop_buffer", None)
        self.put_stop_buffer = put_buf * 100 if put_buf is not None else self.call_stop_buffer
        if self.put_stop_buffer != self.call_stop_buffer:
            logger.info(f"  Asymmetric stop buffer: call=${self.call_stop_buffer/100:.2f}, put=${self.put_stop_buffer/100:.2f}")

        # Price-based stop: trigger when SPX reaches within N points of the short strike.
        # When set, replaces the credit-based spread-value check for ALL entry types
        # (full IC, call-only, put-only). Set to None to use credit-based stop (default).
        # Must be set BEFORE super().__init__() so recovery can use it.
        _price_stop = strategy_cfg.get("price_based_stop_points", None)
        self.price_based_stop_points: Optional[float] = float(_price_stop) if _price_stop is not None else None
        if self.price_based_stop_points is not None:
            logger.info(f"  Price-based stop: ENABLED — trigger {self.price_based_stop_points} pts from short strike")
        else:
            logger.info(f"  Price-based stop: DISABLED — using credit-based stop")

        # MKT-025: short_only_stop needed by _save_state_to_disk() during recovery
        long_salvage = config.get("long_salvage", {})
        self.short_only_stop = long_salvage.get("short_only_stop", False)

        # MKT-035: downday_theoretical_put_credit must be set BEFORE super().__init__()
        # because recovery (_reconstruct_entry_from_positions) uses it to compute call-only
        # stop levels. Without this, the getattr fallback in recovery uses $2.60 instead
        # of the configured value.
        self.downday_theoretical_put_credit = float(strategy_cfg.get("downday_theoretical_put_credit", 2.60)) * 100

        # MKT-035: _conditional_entry_times must be set BEFORE super().__init__()
        # because _parse_entry_times() (called from super) references it
        conditional_strs = strategy_cfg.get("conditional_entry_times", [])
        e6_enabled = strategy_cfg.get("conditional_e6_enabled", False)
        e7_enabled = strategy_cfg.get("conditional_e7_enabled", False)
        all_conditional = [
            dt_time(int(p[0]), int(p[1]))
            for p in (t.split(":") for t in conditional_strs)
        ] if conditional_strs else []
        self._conditional_entry_times = []
        for i, t in enumerate(all_conditional):
            if i == 0 and not e6_enabled:
                logger.info(f"MKT-035: E6 disabled via config (conditional_e6_enabled=False)")
                continue
            if i == 1 and not e7_enabled:
                logger.info(f"MKT-035: E7 disabled via config (conditional_e7_enabled=False)")
                continue
            self._conditional_entry_times.append(t)
        self._conditional_downday_times_set = set(self._conditional_entry_times)

        # Upday conditional put-only entries (mirror of MKT-035 for bullish days)
        upday_e6_enabled = strategy_cfg.get("conditional_upday_e6_enabled", False)
        upday_e7_enabled = strategy_cfg.get("conditional_upday_e7_enabled", False)
        self._conditional_upday_entry_times = []
        for i, t in enumerate(all_conditional):
            if i == 0 and not upday_e6_enabled:
                continue
            if i == 1 and not upday_e7_enabled:
                continue
            self._conditional_upday_entry_times.append(t)
        self._conditional_upday_times_set = set(self._conditional_upday_entry_times)

        # Merge: add any upday times not already covered by downday list into _conditional_entry_times
        # so _parse_entry_times() includes them in entry_times
        _extra_upday = [t for t in self._conditional_upday_entry_times
                        if t not in self._conditional_downday_times_set]
        if _extra_upday:
            self._conditional_entry_times = self._conditional_entry_times + _extra_upday

        self._base_entry_count = 0  # Set in _parse_entry_times after entry_times is built

        # Anti-whipsaw filter: skip entry if SPX intraday range > mult × expected daily move
        # Expected move = SPX_open × VIX_open / 100 / sqrt(252)
        # None = disabled. 1.5 = skip if range > 1.5× expected move.
        _whipsaw = strategy_cfg.get("whipsaw_range_skip_mult", None)
        self.whipsaw_range_skip_mult: Optional[float] = float(_whipsaw) if _whipsaw is not None else None
        if self.whipsaw_range_skip_mult is not None:
            logger.info(f"  Anti-whipsaw filter: ENABLED — skip entry if range > {self.whipsaw_range_skip_mult}× expected move")

        # Day-of-week max entries: MUST be set BEFORE super().__init__() because
        # _parse_entry_times() (called from super) references it
        _dow_max_raw = strategy_cfg.get("dow_max_entries", {})
        self.dow_max_entries = {int(k): int(v) for k, v in _dow_max_raw.items()} if _dow_max_raw else {}

        # Dashboard: server-side P&L history (persists across page refreshes / clients)
        # Each element: {"time": "HH:MM", "pnl": float}
        # Accumulated each heartbeat, one point per minute, reset daily
        # MUST be set BEFORE super().__init__() — recovery restores saved history
        self._pnl_history: list = []

        # Call parent init (this sets up everything else including recovery)
        super().__init__(saxo_client, config, logger_service, dry_run, alert_service)

        logger.info(f"HYDRA using state file: {self.state_file}")
        logger.info(f"HYDRA using metrics file: {self.metrics_file}")

        # DataRecorder: real-time SQLite writes (non-critical, never affects trading)
        self._data_recorder = None
        self._last_stop_time = None  # For cascade gap tracking
        self._last_margin_snapshot = {}  # From _check_buying_power
        try:
            from shared.data_recorder import DataRecorder
            db_path = os.path.join(DATA_DIR, "backtesting.db")
            self._data_recorder = DataRecorder(db_path)
            self._data_recorder.ensure_schema()
            logger.info(f"DataRecorder initialized: {db_path}")
        except Exception as e:
            logger.warning(f"DataRecorder init failed (non-critical): {e}")

        self._bot_start_time = datetime.now()

        logger.info(f"HYDRA Strategy initialized")
        logger.info(f"  Trend filter enabled: {self.trend_enabled}")
        logger.info(f"  EMA periods: {self.ema_short_period}/{self.ema_long_period}")
        logger.info(f"  Neutral threshold: {self.ema_neutral_threshold * 100:.2f}%")
        logger.info(f"  Recheck each entry: {self.recheck_each_entry}")

        # MKT-018: Early close based on Return on Capital (ROC)
        # INTENTIONALLY DISABLED: Backtest showed no ROC configuration beats hold-to-expiry.
        # Code preserved but dormant — set early_close_enabled=true in config to re-enable.
        # See docs/HYDRA_EARLY_CLOSE_ANALYSIS.md for full analysis.
        strategy_config = config.get("strategy", {})
        self.early_close_enabled = bool(strategy_config.get("early_close_enabled", False))
        self.early_close_roc_threshold = float(strategy_config.get("early_close_roc_threshold", 0.03))
        self.early_close_cost_per_position = float(strategy_config.get("early_close_cost_per_position", 5.00))
        self._early_close_triggered = False
        self._early_close_time = None   # ET datetime when early close triggered
        self._early_close_pnl = None    # Net P&L locked in at early close
        logger.info(f"  Early close (MKT-018): {'ENABLED' if self.early_close_enabled else 'DISABLED'} at {self.early_close_roc_threshold*100:.1f}% ROC")

        # MKT-021: Pre-entry ROC gate - skip remaining entries if ROC already
        # exceeds early close threshold. Only active when MKT-018 is enabled.
        # Currently disabled (MKT-018 intentionally off).
        self.min_entries_before_roc_gate = int(strategy_config.get("min_entries_before_roc_gate", 3))
        self._roc_gate_triggered = False
        if self.early_close_enabled:
            logger.info(f"  Pre-entry ROC gate (MKT-021): active after {self.min_entries_before_roc_gate} entries")

        # MKT-020: Progressive call OTM tightening - move short call closer to ATM
        # until credit >= min or OTM floor reached
        self.min_call_otm_distance = int(strategy_config.get("min_call_otm_distance", 25))
        logger.info(f"  Progressive call tightening (MKT-020): min OTM {self.min_call_otm_distance}pt")

        # MKT-022: Progressive put OTM tightening - same as MKT-020 but for puts
        self.min_put_otm_distance = int(strategy_config.get("min_put_otm_distance", 25))
        logger.info(f"  Progressive put tightening (MKT-022): min OTM {self.min_put_otm_distance}pt")

        # MKT-023: Smart hold check before early close
        # Only active when MKT-018 is enabled (currently disabled).
        self.hold_check_enabled = bool(strategy_config.get("hold_check_enabled", True))
        self.hold_check_lean_tolerance = float(strategy_config.get("hold_check_lean_tolerance", 1.0))
        if self.early_close_enabled:
            logger.info(f"  Hold check (MKT-023): {'ENABLED' if self.hold_check_enabled else 'DISABLED'} (lean tolerance {self.hold_check_lean_tolerance}%)")

        # MKT-011 one-sided entry toggle — set false to skip entirely when either side is non-viable
        # When true (default): call non-viable + put viable → put-only entry (v1.7.1 behavior)
        # When false: call non-viable → skip entry entirely (pre-v1.7.1 behavior)
        self.one_sided_entries_enabled = strategy_config.get("one_sided_entries_enabled", True)

        # MKT-032/MKT-039: VIX cutoff for put-only entries — at VIX >= threshold, put-only
        # entries skipped (no call hedge in high volatility). Default 15.0 — put-only
        # stop uses credit+buffer ($1.55 put buffer prevents false stops).
        self.put_only_max_vix = float(strategy_config.get("put_only_max_vix", 15.0))
        if self.one_sided_entries_enabled:
            logger.info(f"  One-sided entries: ENABLED (put-only allowed when VIX < {self.put_only_max_vix})")
        else:
            logger.info(f"  One-sided entries: DISABLED (skip if either side non-viable)")

        # Override min credit from base class $0.50 for HYDRA
        # v1.19.0: Walk-forward backtest optimized: call $2.00, put $2.75.
        # Higher put min forces MKT-022 to scan closer to ATM, landing in sweet spot (42-65pt OTM).
        self.min_viable_credit_per_side = strategy_config.get("min_viable_credit_per_side", 2.0) * 100

        # Separate put minimum credit
        # v1.19.0: Walk-forward backtest optimized at $2.75.
        # MKT-029 graduated fallback: -$0.05, -$0.10 (call floor $0.75, put floor $2.00)
        self.min_viable_credit_put_side = strategy_config.get("min_viable_credit_put_side", 2.75) * 100

        # MKT-029: Configurable credit floors (hard floor after graduated fallback).
        # If not set, falls back to min - $0.10 (legacy behavior).
        _call_floor = strategy_config.get("call_credit_floor", None)
        _put_floor = strategy_config.get("put_credit_floor", None)
        self.call_credit_floor = float(_call_floor) * 100 if _call_floor is not None else self.min_viable_credit_per_side - 10
        self.put_credit_floor = float(_put_floor) * 100 if _put_floor is not None else self.min_viable_credit_put_side - 10
        logger.info(
            f"  Min viable credit - call: ${self.min_viable_credit_per_side / 100:.2f} "
            f"(floor: ${self.call_credit_floor / 100:.2f}), "
            f"put: ${self.min_viable_credit_put_side / 100:.2f} "
            f"(floor: ${self.put_credit_floor / 100:.2f})"
        )

        # MKT-024: Wider starting OTM multipliers
        # Start strike search at N× the VIX-adjusted distance so MKT-020/022
        # scan a wider range. Gives more breathing room on volatile days.
        self.call_starting_otm_multiplier = float(strategy_config.get("call_starting_otm_multiplier", 3.5))
        self.put_starting_otm_multiplier = float(strategy_config.get("put_starting_otm_multiplier", 4.0))
        logger.info(f"  Starting OTM multipliers (MKT-024): call ×{self.call_starting_otm_multiplier}, put ×{self.put_starting_otm_multiplier}")

        # MKT-027: VIX-scaled spread width (continuous formula replaces step function)
        # Pushes long legs further OTM on high-VIX days → cheaper longs → higher net credit → more stop cushion
        self.spread_vix_multiplier = float(strategy_config.get("spread_vix_multiplier", 6.0))
        self.max_spread_width = int(strategy_config.get("max_spread_width", 110))

        # MKT-028: Asymmetric spread widths — put longs cost 7× more than calls due to skew.
        # Wider put spreads push longs further OTM = cheaper.
        # margin = max(call_width, put_width), so wider puts don't require wider calls.
        self.call_min_spread_width = int(strategy_config.get("call_min_spread_width", 25))
        self.put_min_spread_width = int(strategy_config.get("put_min_spread_width", 25))
        logger.info(f"  Spread width (MKT-027/028): VIX × {self.spread_vix_multiplier}, "
                    f"call floor={self.call_min_spread_width}pt, put floor={self.put_min_spread_width}pt, "
                    f"cap={self.max_spread_width}pt")

        # MKT-031: Smart entry windows (top-level config, same as trend_filter)
        smart_entry = config.get("smart_entry", {})
        self.smart_entry_enabled = smart_entry.get("enabled", False)
        self.scout_window_minutes = smart_entry.get("window_minutes", DEFAULT_SCOUT_WINDOW_MINUTES)
        self.scout_score_threshold = smart_entry.get("score_threshold", DEFAULT_SCOUT_SCORE_THRESHOLD)
        self.scout_momentum_threshold = smart_entry.get("momentum_threshold_pct", 0.05)
        logger.info(f"  Smart entry (MKT-031): {'ENABLED' if self.smart_entry_enabled else 'DISABLED'} "
                    f"(window={self.scout_window_minutes}min, threshold={self.scout_score_threshold})")
        if self.vix_gate_enabled:
            schedule_str = ", ".join(t.strftime('%H:%M:%S') for t in self.entry_times)
            logger.info(f"  VIX time shift (MKT-034): ENABLED "
                        f"(medium={self.vix_medium_threshold}, high={self.vix_high_threshold})")
            logger.info(f"  Default schedule: [{schedule_str}]")
        else:
            logger.info(f"  VIX time shift (MKT-034): DISABLED")

        # MKT-025/MKT-033: Short-only stop + long leg salvage (configurable)
        # Note: self.short_only_stop already set before super().__init__() (line ~263)
        long_salvage = config.get("long_salvage", {})
        self.long_salvage_enabled = long_salvage.get("enabled", True)
        self.long_salvage_min_profit = float(long_salvage.get("min_profit", 10.0))
        logger.info(f"  Stop mode: {'SHORT-ONLY + salvage (MKT-025/033)' if self.short_only_stop else 'BOTH LEGS closed'}")
        if self.short_only_stop:
            logger.info(f"  Long salvage (MKT-033): {'ENABLED' if self.long_salvage_enabled else 'DISABLED'} "
                        f"(min_profit=${self.long_salvage_min_profit:.0f})")

        # MKT-035: Call-only on down days — when SPX drops threshold% below
        # today's open, place call spread only (no puts). 20-day data: down days
        # have 71% put stop rate but only 7% call stop rate. +$920 improvement.
        # Note: _conditional_entry_times and _base_entry_count are set BEFORE super().__init__()
        self.downday_callonly_enabled = strategy_config.get("downday_callonly_enabled", True)
        self.downday_threshold_pct = float(strategy_config.get("downday_threshold_pct", 0.003))  # 0.3%
        self.downday_theoretical_put_credit = float(strategy_config.get("downday_theoretical_put_credit", 2.60)) * 100  # $260
        # Base-entry down-day call-only: base entries convert to call-only when SPX drops >= threshold from open.
        # None = disabled (full IC regardless of direction — backtest-confirmed optimal baseline).
        # Set to 0.004 (0.4%) to match backtest-optimized value.
        _base_pct = strategy_config.get("base_entry_downday_callonly_pct", None)
        self.base_entry_downday_callonly_pct = float(_base_pct) if _base_pct is not None else None
        logger.info(
            f"  Down day filter (MKT-035): {'ENABLED' if self.downday_callonly_enabled else 'DISABLED'} "
            f"(threshold: {self.downday_threshold_pct * 100:.1f}%, "
            f"theoretical put: ${self.downday_theoretical_put_credit / 100:.2f}, "
            f"conditional entries: {len(self._conditional_entry_times)})"
        )
        if self.base_entry_downday_callonly_pct is not None:
            logger.info(
                f"  Base-entry down-day call-only: ENABLED "
                f"(threshold: {self.base_entry_downday_callonly_pct * 100:.1f}% drop from open, applies to base entries)"
            )
        else:
            logger.info("  Base-entry down-day call-only: DISABLED")
        # Upday put-only conditional (mirror of MKT-035)
        self.upday_putonly_enabled = bool(
            strategy_config.get("conditional_upday_e6_enabled", False) or
            strategy_config.get("conditional_upday_e7_enabled", False)
        )
        self.upday_threshold_pct = float(strategy_config.get("upday_threshold_pct", 0.0025))  # 0.25%
        self.upday_reference = strategy_config.get("upday_reference", "open")
        logger.info(
            f"  Up day filter: {'ENABLED' if self.upday_putonly_enabled else 'DISABLED'} "
            f"(threshold: +{self.upday_threshold_pct * 100:.2f}%, "
            f"reference: {self.upday_reference}, "
            f"upday slots: {len(self._conditional_upday_entry_times)})"
        )

        # MKT-038: Call-only entries on T+1 after FOMC announcement
        # Research: T+1 is 66.7% down days with 23% more volatility.
        # Force call-only entries to avoid put-side exposure.
        self.fomc_t1_callonly_enabled = strategy_config.get("fomc_t1_callonly_enabled", True)
        logger.info(
            f"  FOMC T+1 filter (MKT-038): {'ENABLED' if self.fomc_t1_callonly_enabled else 'DISABLED'}"
        )

        # MKT-036: Stop confirmation timer — INTENTIONALLY DISABLED.
        # $1.55 put buffer (put_stop_buffer) is the chosen solution instead.
        # Code preserved but dormant. When enabled: requires stop to persist N seconds.
        self.stop_confirmation_enabled = strategy_config.get("stop_confirmation_enabled", False)
        self.stop_confirmation_seconds = int(strategy_config.get("stop_confirmation_seconds", 75))
        logger.info(
            f"  Stop confirmation (MKT-036): {'ENABLED' if self.stop_confirmation_enabled else 'DISABLED'} "
            f"({self.stop_confirmation_seconds}s window)"
        )

        # MKT-043: Calm entry filter — delay entry when SPX is moving fast.
        # Backtest: L3 T15 D5 = Sharpe 2.153, +$2,040 P&L over 938 days.
        self.calm_entry_lookback_min = strategy_config.get("calm_entry_lookback_min", None)
        self.calm_entry_threshold_pts = strategy_config.get("calm_entry_threshold_pts", None)
        self.calm_entry_max_delay_min = strategy_config.get("calm_entry_max_delay_min", None)
        if self.calm_entry_lookback_min is not None:
            logger.info(
                f"  Calm entry (MKT-043): ENABLED "
                f"(lookback {self.calm_entry_lookback_min}min, threshold {self.calm_entry_threshold_pts}pt, "
                f"max delay {self.calm_entry_max_delay_min}min)"
            )
        else:
            logger.info(f"  Calm entry (MKT-043): DISABLED")

        # MKT-042: Time-decaying stop buffer — wider stops early, normal after decay period.
        # Backtest: x2.10 2.0h = Sharpe 2.157, +$10,485 P&L over 938 days.
        self.buffer_decay_start_mult = strategy_config.get("buffer_decay_start_mult", None)
        self.buffer_decay_hours = strategy_config.get("buffer_decay_hours", None)
        if self.buffer_decay_start_mult is not None and self.buffer_decay_hours is not None:
            logger.info(
                f"  Buffer decay (MKT-042): ENABLED "
                f"(start {self.buffer_decay_start_mult:.2f}×, decay to 1× over {self.buffer_decay_hours:.1f}h)"
            )
        else:
            logger.info(f"  Buffer decay (MKT-042): DISABLED")

        # MKT-041: Cushion recovery exit — close side that nearly stopped then recovered.
        # When spread_value reaches >= nearstop_pct × stop_level (danger zone) then
        # drops back to <= recovery_pct × stop_level, close that side.
        # Backtest: N96 R67 = Sharpe 2.182 vs 2.094 baseline (938 days, 1-min data).
        self.cushion_nearstop_pct = strategy_config.get("cushion_nearstop_pct", None)
        self.cushion_recovery_pct = strategy_config.get("cushion_recovery_pct", None)
        if self.cushion_nearstop_pct is not None and self.cushion_recovery_pct is not None:
            logger.info(
                f"  Cushion recovery (MKT-041): ENABLED "
                f"(danger >= {self.cushion_nearstop_pct:.0%} of stop, "
                f"close at <= {self.cushion_recovery_pct:.0%} of stop)"
            )
        else:
            logger.info(f"  Cushion recovery (MKT-041): DISABLED")

        # Skip weekdays: don't trade on specific days (0=Mon..4=Fri)
        self.skip_weekdays = strategy_config.get("skip_weekdays", [])
        if self.skip_weekdays:
            day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
            skip_names = [day_names.get(d, str(d)) for d in self.skip_weekdays]
            logger.info(f"  Skip weekdays: {', '.join(skip_names)}")

        # Day-of-week max entries logging (initialized before super().__init__)
        if self.dow_max_entries:
            day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
            caps = [f"{day_names.get(d, str(d))}={v}e" for d, v in self.dow_max_entries.items()]
            logger.info(f"  Day-of-week entry caps: {', '.join(caps)}")

        # VIX regime: override parameters based on VIX at open
        _vix_regime = strategy_config.get("vix_regime", {})
        self.vix_regime_enabled = _vix_regime.get("enabled", False)
        self.vix_regime_breakpoints = _vix_regime.get("breakpoints", [14.0, 20.0, 30.0])
        self.vix_regime_max_entries = _vix_regime.get("max_entries", [None, None, None, None])
        self.vix_regime_put_stop_buffer = _vix_regime.get("put_stop_buffer", [None, None, None, None])
        self.vix_regime_call_stop_buffer = _vix_regime.get("call_stop_buffer", [None, None, None, None])
        self.vix_regime_min_call_credit = _vix_regime.get("min_call_credit", [None, None, None, None])
        self.vix_regime_min_put_credit = _vix_regime.get("min_put_credit", [None, None, None, None])
        self._vix_regime_applied = False  # set True after first application
        if self.vix_regime_enabled:
            logger.info(
                f"  VIX regime: ENABLED — breakpoints {self.vix_regime_breakpoints}, "
                f"max_entries {self.vix_regime_max_entries}"
            )

        # MKT-031: Scouting state (in-memory, no persistence needed)
        self._scouting_active = False
        self._scouting_window_start = None
        self._scouting_entry_index = -1
        self._last_scout_score = 0
        self._last_scout_details = {}
        self._cached_chart_bars = None
        self._cached_chart_time = None

    # =========================================================================
    # OVERRIDE: Spread width with VIX-scaled formula (MKT-027)
    # =========================================================================

    def _get_vix_adjusted_spread_width(self, vix: float, side: str = "call") -> int:
        """
        MKT-027/MKT-028: VIX-scaled spread width with asymmetric floors.

        MKT-027: Continuous formula pushes long legs further OTM on high-VIX days.
        MKT-028: Separate floors for calls (60pt) and puts (75pt) — put longs
        cost 7× more due to skew, so wider put spreads save more.

        Wider put spreads push longs further OTM = cheaper.
        margin = max(call_width, put_width), so narrower calls don't affect margin.

        Args:
            vix: Current VIX level
            side: "call" or "put" — determines which floor to use
        """
        spread_width = round(vix * self.spread_vix_multiplier / 5) * 5
        if side == "put":
            spread_width = max(self.put_min_spread_width, spread_width)
        else:
            spread_width = max(self.call_min_spread_width, spread_width)
        spread_width = min(spread_width, self.max_spread_width)  # cap
        return spread_width

    # =========================================================================
    # OVERRIDE: Strike calculation with wider starting OTM (MKT-024)
    # =========================================================================

    def _calculate_strikes(self, entry: HydraIronCondorEntry) -> bool:
        """
        Override base MEIC strike calculation to apply MKT-024 wider starting OTM.

        Uses separate multipliers for call and put starting OTM distances.
        At 2× multiplier, the starting distance is doubled — MKT-020/MKT-022
        then scan inward from there to find the widest viable strike at or
        above the minimum credit threshold.

        This gives puts more breathing room on volatile days (put skew means
        $2.75 credit is found much further OTM) while calls still tighten
        to reach $2.00 ($0.75 with MKT-029 fallback floor).

        Args:
            entry: HydraIronCondorEntry to populate with strikes

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

        # Same base OTM calculation as parent MEIC
        base_distance_at_vix15 = 40  # Points OTM for ~8 delta at VIX 15
        delta_adjustment = 8.0 / self.target_delta
        vix_factor = max(0.7, min(2.5, vix / 15.0))
        otm_distance = base_distance_at_vix15 * vix_factor * delta_adjustment
        otm_distance = round(otm_distance / 5) * 5
        otm_distance = max(25, min(120, otm_distance))

        # MKT-024: Apply separate multipliers for wider starting distance
        call_otm = round((otm_distance * self.call_starting_otm_multiplier) / 5) * 5
        call_otm = max(25, min(240, call_otm))  # Extended upper clamp for 2× range
        put_otm = round((otm_distance * self.put_starting_otm_multiplier) / 5) * 5
        put_otm = max(25, min(240, put_otm))

        # MKT-027/028: Asymmetric VIX-adjusted spread widths
        call_spread_width = self._get_vix_adjusted_spread_width(vix, "call")
        put_spread_width = self._get_vix_adjusted_spread_width(vix, "put")

        logger.info(
            f"MKT-024 strike calc: VIX={vix:.1f}, base_otm={otm_distance}pt, "
            f"call_otm={call_otm}pt (×{self.call_starting_otm_multiplier}), "
            f"put_otm={put_otm}pt (×{self.put_starting_otm_multiplier}), "
            f"call_spread={call_spread_width}pt, put_spread={put_spread_width}pt"
        )

        # Call side (above current price) — starts wider than base MEIC
        entry.short_call_strike = rounded_spx + call_otm
        entry.long_call_strike = entry.short_call_strike + call_spread_width

        # Put side (below current price) — starts wider than base MEIC
        entry.short_put_strike = rounded_spx - put_otm
        entry.long_put_strike = entry.short_put_strike - put_spread_width

        # MKT-007: Check liquidity and adjust strikes if needed
        expiry = self._get_todays_expiry()
        if expiry:
            # Check short call liquidity (move closer to ATM if illiquid)
            adjusted_call, call_msg = self._adjust_strike_for_liquidity(
                entry.short_call_strike, "Call", expiry,
                adjustment_direction=-1  # Move closer to ATM (lower for calls)
            )
            if adjusted_call and adjusted_call != entry.short_call_strike:
                entry.short_call_strike = adjusted_call
                entry.long_call_strike = adjusted_call + call_spread_width
                logger.info(f"MKT-007: {call_msg}")

            # Check short put liquidity (move closer to ATM if illiquid)
            adjusted_put, put_msg = self._adjust_strike_for_liquidity(
                entry.short_put_strike, "Put", expiry,
                adjustment_direction=1  # Move closer to ATM (higher for puts)
            )
            if adjusted_put and adjusted_put != entry.short_put_strike:
                entry.short_put_strike = adjusted_put
                entry.long_put_strike = adjusted_put - put_spread_width
                logger.info(f"MKT-007: {put_msg}")

            # MKT-008: Check long wing liquidity and reduce spread width if needed
            adjusted_long_call, call_adjusted = self._adjust_long_wing_for_liquidity(
                entry.long_call_strike, entry.short_call_strike,
                "Call", expiry, is_call=True
            )
            if call_adjusted:
                entry.long_call_strike = adjusted_long_call
                entry.call_wing_illiquid = True

            adjusted_long_put, put_adjusted = self._adjust_long_wing_for_liquidity(
                entry.long_put_strike, entry.short_put_strike,
                "Put", expiry, is_call=False
            )
            if put_adjusted:
                entry.long_put_strike = adjusted_long_put
                entry.put_wing_illiquid = True

        # Fix #44: Check for strike conflicts with existing entries
        self._adjust_for_strike_conflicts(entry)

        # Fix #50/MKT-013: Check for same-strike overlap with existing entries
        self._adjust_for_same_strike_overlap(entry)

        # Fix #66: Re-run Fix #44 after MKT-013
        self._adjust_for_strike_conflicts(entry)

        # MKT-015: Check for long-long strike overlap
        self._adjust_for_long_strike_overlap(entry)

        logger.info(
            f"Strikes calculated for SPX {spx:.2f}: "
            f"Call {entry.short_call_strike}/{entry.long_call_strike}, "
            f"Put {entry.short_put_strike}/{entry.long_put_strike}"
        )

        return True

    # =========================================================================
    # OVERRIDE: Entry time parsing for shifted schedule
    # =========================================================================

    def _parse_entry_times(self):
        """
        Override: MKT-034 VIX-scaled entry time shifting + early close cutoff.

        When vix_time_shift is enabled, uses ALL_ENTRY_SLOTS[:5] as default entry
        times (:14:30/:44:30 offset for execution precision). VIX gate checks at
        :14:00/:44:00 determine E#1 start slot based on VIX level.

        Early close cutoff at 12:30 PM (allows 12:14:30 entry on high VIX days).
        """
        # MKT-034: Read VIX gate config
        vts = self.config.get("vix_time_shift", {})
        self.vix_gate_enabled = vts.get("enabled", False)
        self.vix_medium_threshold = vts.get("medium_vix_threshold", 20.0)
        self.vix_high_threshold = vts.get("high_vix_threshold", 23.0)
        self._vix_gate_resolved = False
        self._vix_gate_start_slot = 0

        if self.vix_gate_enabled:
            # MKT-034: Use pre-defined slots with 30s offset for execution precision
            self.entry_times = list(ALL_ENTRY_SLOTS[:5])
            logger.info(
                f"MKT-034: VIX gate enabled (thresholds: {self.vix_medium_threshold}/{self.vix_high_threshold}), "
                f"default schedule: [{', '.join(t.strftime('%H:%M:%S') for t in self.entry_times)}]"
            )
        else:
            # Legacy: parse from config (same as base)
            entry_time_strs = self.strategy_config.get("entry_times", None)
            if entry_time_strs:
                self.entry_times = [
                    dt_time(int(p[0]), int(p[1]))
                    for p in (t.split(":") for t in entry_time_strs)
                ]
            else:
                # Fallback: 3 base entries (v1.19.0 walk-forward optimized)
                self.entry_times = [
                    dt_time(10, 15), dt_time(10, 45), dt_time(11, 15)
                ]

        # MKT-035: Record base entry count before appending conditional entries
        self._base_entry_count = len(self.entry_times)

        # MKT-035: Append conditional entry times (only fire on down days as call-only)
        if self._conditional_entry_times:
            self.entry_times.extend(self._conditional_entry_times)
            cond_str = ", ".join(t.strftime('%H:%M') for t in self._conditional_entry_times)
            logger.info(f"MKT-035: {len(self._conditional_entry_times)} conditional entries appended: [{cond_str}]")

        # Day-of-week max entries cap (e.g., Fri=2e)
        if self.dow_max_entries:
            today_dow = get_us_market_time().weekday()
            dow_cap = self.dow_max_entries.get(today_dow)
            if dow_cap is not None and self._base_entry_count > dow_cap:
                self.entry_times = self.entry_times[:dow_cap] + self.entry_times[self._base_entry_count:]
                self._base_entry_count = dow_cap
                day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
                logger.info(f"  DoW cap: {day_names.get(today_dow, str(today_dow))} capped to {dow_cap} base entries")

        if is_early_close_day():
            early_cutoff = dt_time(12, 30)  # Allows entries up to 12:15 (12:14:30 with MKT-034 offset)
            first_entry = self.entry_times[0] if self.entry_times else dt_time(10, 15)
            self.entry_times = [t for t in self.entry_times if t < early_cutoff]
            if not self.entry_times:
                self.entry_times = [first_entry]
            # Recalculate base count after cutoff (conditional entries after cutoff are dropped)
            self._base_entry_count = min(self._base_entry_count, len(self.entry_times))
            logger.info(f"HYDRA early close schedule: {[t.strftime('%H:%M:%S') for t in self.entry_times]}")

    # =========================================================================
    # SKIP WEEKDAYS — override idle state to respect DAILY_COMPLETE on restart
    # =========================================================================

    def _handle_idle_state(self) -> str:
        """Override: prevent trading on skip days even after restart."""
        result = super()._handle_idle_state()
        # If base class transitioned to WAITING_FIRST_ENTRY, check skip days
        if self.state == MEICState.WAITING_FIRST_ENTRY and self.skip_weekdays:
            today_dow = get_us_market_time().weekday()
            if today_dow in self.skip_weekdays:
                day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
                logger.info(f"Skip weekday: {day_names.get(today_dow, str(today_dow))} — overriding to DAILY_COMPLETE")
                self.state = MEICState.DAILY_COMPLETE
                return "Skip day — no trading today"
        return result

    # =========================================================================
    # ENTRY GATING (MKT-021 + MKT-031)
    # =========================================================================

    def _should_attempt_entry(self, now: datetime) -> bool:
        """
        Override: MKT-021 ROC gate + MKT-031 smart entry windows.

        MKT-021: After min_entries_before_roc_gate entries placed, if ROC on
        existing positions >= early_close_roc_threshold, skip remaining entries.

        MKT-031: Opens a scouting window before each entry. Scores market
        conditions (post-spike calm + momentum pause). If score >= threshold,
        enter early. Otherwise, enter at scheduled time as usual.
        """
        # Apply VIX regime overrides once per day (after VIX is known)
        self._apply_vix_regime_overrides()

        # MKT-021: Pre-entry ROC gate (only when MKT-018 early close is enabled)
        if self._roc_gate_triggered:
            return False

        if (self.early_close_enabled
            and not self._roc_gate_triggered
            and len(self.daily_state.entries) >= self.min_entries_before_roc_gate
            and len(self.daily_state.active_entries) > 0):

            # Calculate current ROC (same formula as _check_early_close)
            unrealized = self._get_total_saxo_pnl()
            total_pnl = self.daily_state.total_realized_pnl + unrealized
            net_pnl = total_pnl - self.daily_state.total_commission
            active_legs = self._count_active_position_legs()
            close_cost = active_legs * self.early_close_cost_per_position
            capital_deployed = self._calculate_capital_deployed()

            if capital_deployed > 0:
                roc = (net_pnl - close_cost) / capital_deployed
                if roc >= self.early_close_roc_threshold:
                    self._roc_gate_triggered = True
                    remaining = max(0, len(self.entry_times) - self._next_entry_index)
                    logger.warning(
                        f"MKT-021 PRE-ENTRY ROC GATE: ROC {roc*100:.2f}% >= "
                        f"{self.early_close_roc_threshold*100:.1f}% threshold with "
                        f"{len(self.daily_state.entries)} entries placed - "
                        f"skipping {remaining} remaining entries to preserve ROC"
                    )
                    self._log_safety_event(
                        "MKT-021_ROC_GATE",
                        f"ROC {roc*100:.2f}% on {len(self.daily_state.entries)} entries, "
                        f"skipped {remaining} remaining"
                    )
                    self.daily_state.entries_skipped += remaining
                    self._next_entry_index = len(self.entry_times)
                    return False

        # MKT-031: Smart entry windows (+ MKT-034 VIX gate integration)
        if not self.smart_entry_enabled:
            # MKT-034: VIX gate still needs checking even without smart entry
            if self.vix_gate_enabled and not self._vix_gate_resolved:
                scheduled_time = self.entry_times[self._next_entry_index] if self._next_entry_index < len(self.entry_times) else None
                if scheduled_time:
                    vix_result = self._check_vix_gate(now)
                    if vix_result == "blocked":
                        return False
                    # "resolved" or "not_yet" → fall through to base class
            return super()._should_attempt_entry(now)

        if self._next_entry_index >= len(self.entry_times):
            return False

        scheduled_time = self.entry_times[self._next_entry_index]
        scheduled_dt = now.replace(
            hour=scheduled_time.hour, minute=scheduled_time.minute,
            second=scheduled_time.second, microsecond=0
        )
        scout_start = scheduled_dt - timedelta(minutes=self.scout_window_minutes)
        window_end = scheduled_dt + timedelta(minutes=ENTRY_WINDOW_MINUTES)

        # Before scouting window → wait
        if now < scout_start:
            self._deactivate_scouting()
            return False

        # Past retry window → base class handles skip
        if now > window_end:
            self._deactivate_scouting()
            return False

        # At or past scheduled time → enter (standard behavior)
        if now >= scheduled_dt:
            # MKT-034: VIX gate check at scheduled time (E#1 only)
            if self.vix_gate_enabled and not self._vix_gate_resolved:
                vix_result = self._check_vix_gate(now)
                if vix_result == "blocked":
                    self._deactivate_scouting()
                    return False  # Slot popped, next loop iteration uses next slot
                # "resolved" → proceed with entry
            if self._scouting_active:
                logger.info(
                    f"MKT-031: Scout window expired for Entry #{self._next_entry_index + 1} "
                    f"(best score: {self._last_scout_score}). Default entry."
                )
                self._deactivate_scouting()
            return True

        # Within scouting window → score and decide
        if not self._scouting_active:
            self._activate_scouting(now)

        score, details = self._score_entry_conditions(now)
        self._last_scout_score = max(self._last_scout_score, score)
        self._last_scout_details = details

        if score >= self.scout_score_threshold:
            # MKT-034: Early entry must check VIX gate first (Audit Bug #2)
            if self.vix_gate_enabled and not self._vix_gate_resolved:
                vix_result = self._check_vix_gate(now, allow_early=True)
                if vix_result == "resolved":
                    pass  # VIX allows → proceed with early entry below
                else:
                    # "not_yet": VIX too high or unavailable — keep scouting,
                    # don't enter early. Slot NOT popped (scheduled-time check does that).
                    return False

            logger.info(
                f"MKT-031: EARLY ENTRY Entry #{self._next_entry_index + 1} "
                f"Score={score}/{self.scout_score_threshold} "
                f"[spike={details['post_spike']}, momentum={details['momentum']}] "
                f"({(scheduled_dt - now).total_seconds():.0f}s early)"
            )
            self._deactivate_scouting()
            return True

        return False  # Still scouting, not triggered

    def _is_entry_time(self) -> bool:
        """
        Override: extend entry window to include MKT-031 scouting period for retries.

        Without this, if early entry triggers at 11:07 for 11:15 slot, the retry
        loop calls base _is_entry_time() which checks 11:15 <= 11:07 → False →
        retries abort immediately.
        """
        if not self.smart_entry_enabled:
            return super()._is_entry_time()
        if self._next_entry_index >= len(self.entry_times):
            return False
        now = get_us_market_time()
        scheduled_time = self.entry_times[self._next_entry_index]
        scheduled_dt = now.replace(
            hour=scheduled_time.hour, minute=scheduled_time.minute,
            second=scheduled_time.second, microsecond=0
        )
        scout_start = scheduled_dt - timedelta(minutes=self.scout_window_minutes)
        window_end = scheduled_dt + timedelta(minutes=ENTRY_WINDOW_MINUTES)
        return scout_start <= now <= window_end

    def _is_daily_loss_limit_reached(self) -> bool:
        """Disabled for HYDRA — bot always attempts all entries."""
        return False

    # =========================================================================
    # MKT-034: VIX-SCALED ENTRY TIME SHIFTING
    # =========================================================================

    def _check_vix_gate(self, now, allow_early: bool = False) -> str:
        """
        MKT-034: Check VIX level and decide whether to allow E#1 at current slot.

        Args:
            allow_early: If True (MKT-031 scouting), skip the check_time guard
                and return "not_yet" instead of "blocked" when VIX is too high
                (don't pop the slot — let the scheduled-time check do that).

        Returns:
            "blocked"  - VIX too high, slot removed from entry_times
            "resolved" - VIX allows entry, schedule locked
            "not_yet"  - Too early for VIX check / VIX too high during scouting
        """
        if not self.entry_times:
            return "resolved"

        # Trim any entry_times slots whose windows have already ended
        # (handles desync when _skip_missed_entries advances _next_entry_index
        # but doesn't pop from entry_times — e.g., bot restart after slot 0 window)
        while len(self.entry_times) > 1:
            slot = self.entry_times[0]
            slot_dt = now.replace(
                hour=slot.hour, minute=slot.minute,
                second=slot.second, microsecond=0
            )
            window_end = slot_dt + timedelta(minutes=ENTRY_WINDOW_MINUTES)
            if now > window_end:
                self.entry_times.pop(0)
                logger.info(f"MKT-034: Trimming passed slot {slot.strftime('%H:%M:%S')}")
            else:
                break

        current_slot_time = self.entry_times[0]
        # Check time is VIX_GATE_CHECK_SECONDS_BEFORE (30s) before entry
        # :14:30 → check at :14:00, :44:30 → check at :44:00
        check_time = now.replace(
            hour=current_slot_time.hour,
            minute=current_slot_time.minute,
            second=current_slot_time.second,
            microsecond=0
        ) - timedelta(seconds=VIX_GATE_CHECK_SECONDS_BEFORE)

        if now < check_time and not allow_early:
            return "not_yet"

        # Find which slot index this is in ALL_ENTRY_SLOTS
        try:
            slot_index = ALL_ENTRY_SLOTS.index(current_slot_time)
        except ValueError:
            # Custom time not in standard slots — resolve immediately
            self._resolve_vix_gate(0)
            return "resolved"

        # Floor: slot 2+ always enters
        if slot_index >= VIX_GATE_FLOOR_SLOT:
            self._resolve_vix_gate(slot_index)
            return "resolved"

        # Use current VIX from heartbeat (already populated by _update_market_data)
        vix = self.current_vix
        if not vix:  # None or 0.0 (initial state before first fetch)
            logger.warning("MKT-034: VIX unavailable, using default schedule")
            self._resolve_vix_gate(slot_index)
            return "resolved"

        # Determine threshold for this slot
        if slot_index == 0:
            threshold = self.vix_medium_threshold  # 20.0
        elif slot_index == 1:
            threshold = self.vix_high_threshold    # 23.0
        else:
            threshold = self.vix_high_threshold    # Safety fallback

        if vix >= threshold:
            if allow_early:
                # During scouting: VIX too high for early entry, but don't pop
                # the slot — let the scheduled-time check make that decision
                return "not_yet"
            next_slot = ALL_ENTRY_SLOTS[slot_index + 1]
            logger.info(
                f"MKT-034: VIX={vix:.1f} >= {threshold:.1f}, "
                f"skipping {current_slot_time.strftime('%H:%M:%S')}. "
                f"Next check at {next_slot.strftime('%H:%M:%S')}"
            )
            # Remove blocked slot so standard time check won't trigger it
            self.entry_times.pop(0)
            return "blocked"
        else:
            logger.info(
                f"MKT-034: VIX={vix:.1f} < {threshold:.1f}, "
                f"allowing entry at {current_slot_time.strftime('%H:%M:%S')}"
            )
            self._resolve_vix_gate(slot_index)
            return "resolved"

    def _resolve_vix_gate(self, start_slot_index: int):
        """MKT-034: Lock entry schedule to 5 consecutive slots from start_slot_index."""
        self.entry_times = list(ALL_ENTRY_SLOTS[start_slot_index : start_slot_index + 5])
        # Re-apply early close filter if applicable (don't override _parse_entry_times cutoff)
        if is_early_close_day():
            early_cutoff = dt_time(12, 30)
            self.entry_times = [t for t in self.entry_times if t < early_cutoff]
            if not self.entry_times:
                self.entry_times = [ALL_ENTRY_SLOTS[start_slot_index]]
        self._next_entry_index = 0
        self._vix_gate_resolved = True
        self._vix_gate_start_slot = start_slot_index
        schedule_str = ", ".join(t.strftime('%H:%M:%S') for t in self.entry_times)
        logger.info(f"MKT-034: VIX gate resolved -> schedule: [{schedule_str}]")

    # =========================================================================
    # MKT-031: SMART ENTRY WINDOWS — Scouting Lifecycle
    # =========================================================================

    def _activate_scouting(self, now):
        """MKT-031: Begin scouting for next entry."""
        self._scouting_active = True
        self._scouting_window_start = now
        self._scouting_entry_index = self._next_entry_index
        self._last_scout_score = 0
        self._last_scout_details = {}
        self._refresh_chart_data_for_scouting()
        logger.info(
            f"MKT-031: Scouting OPEN for Entry #{self._next_entry_index + 1} "
            f"(threshold: {self.scout_score_threshold})"
        )

    def _deactivate_scouting(self):
        """MKT-031: End scouting."""
        if self._scouting_active:
            self._scouting_active = False
            self._cached_chart_bars = None

    def _refresh_chart_data_for_scouting(self):
        """MKT-031: Fetch 1-min OHLC bars for ATR calculation. Caches result."""
        try:
            chart_data = self.client.get_chart_data(
                uic=self.underlying_uic, asset_type="CfdOnIndex",
                horizon=self.chart_horizon_minutes, count=self.chart_bars_count
            )
            if chart_data and "Data" in chart_data:
                self._cached_chart_bars = chart_data["Data"]
                self._cached_chart_time = get_us_market_time()
        except Exception as e:
            logger.warning(f"MKT-031: Chart data fetch failed: {e}")

    # =========================================================================
    # MKT-031: SMART ENTRY WINDOWS — Scoring Engine
    # =========================================================================

    def _score_entry_conditions(self, now) -> Tuple[int, Dict[str, int]]:
        """
        MKT-031: Score 2 parameters for smart entry timing.

        Parameters:
          1. Post-spike calm (ATR declining from elevated) — 0-70 pts
          2. Momentum pause (price calm over 2 min) — 0-30 pts

        Returns:
            (total_score, {"post_spike": N, "momentum": N})
        """
        # Refresh chart data if stale (> 2 min old)
        if (self._cached_chart_bars and self._cached_chart_time
                and (now - self._cached_chart_time).total_seconds() > 120):
            self._refresh_chart_data_for_scouting()

        spike = self._score_post_spike_calm()
        momentum = self._score_momentum_pause()

        details = {"post_spike": spike, "momentum": momentum}
        return spike + momentum, details

    def _score_post_spike_calm(self) -> int:
        """
        Parameter 1: ATR(3) declining from elevated level (0-70 pts).

        Uses cached 1-min OHLC bars. Compares recent ATR(3) vs prior ATR(3).
        Full points when ATR is declining from a recently elevated level
        (market had a spike but is now consolidating — the "Henry Schwartz pattern").

        Scoring:
          ATR declining 50%+ from elevated peak → 70
          ATR declining 25%+ from elevated peak → 55
          ATR declining 10%+ from elevated peak → 40
          ATR declining but no clear prior spike → 20
          ATR rising or flat → 0

        "Elevated" = previous ATR(3) > 1.5× long-term ATR(14).
        """
        if not self._cached_chart_bars or len(self._cached_chart_bars) < 15:
            return 0

        bars = self._cached_chart_bars

        # Saxo CFD data uses HighBid/LowBid/CloseBid, fallback to High/Low/Close
        highs = [b.get("HighBid") or b.get("High", 0) for b in bars]
        lows = [b.get("LowBid") or b.get("Low", 0) for b in bars]
        closes = [b.get("CloseBid") or b.get("Close", 0) for b in bars]

        # Filter zero prices
        valid = [(h, l, c) for h, l, c in zip(highs, lows, closes) if h > 0 and l > 0 and c > 0]
        if len(valid) < 15:
            return 0

        vh, vl, vc = zip(*valid)

        # Current ATR(3) from most recent 4 bars, prev ATR(3) from 4 bars before that
        current_atr = calculate_atr(list(vh[-4:]), list(vl[-4:]), list(vc[-4:]), period=3)
        prev_atr = calculate_atr(list(vh[-8:-4]), list(vl[-8:-4]), list(vc[-8:-4]), period=3)
        long_atr = calculate_atr(list(vh), list(vl), list(vc), period=min(14, len(valid) - 1))

        if current_atr <= 0 or prev_atr <= 0:
            logger.debug(f"MKT-031 ATR: zero values (current={current_atr:.4f}, prev={prev_atr:.4f})")
            return 0

        is_declining = current_atr < prev_atr
        was_elevated = prev_atr > (long_atr * 1.5) if long_atr > 0 else False
        decline_pct = (prev_atr - current_atr) / prev_atr if is_declining else 0

        logger.debug(
            f"MKT-031 ATR: current={current_atr:.4f}, prev={prev_atr:.4f}, "
            f"long={long_atr:.4f}, declining={is_declining}, "
            f"elevated={was_elevated} (threshold={long_atr * 1.5:.4f}), "
            f"decline={decline_pct*100:.1f}%"
        )

        if is_declining and was_elevated:
            if decline_pct >= 0.50:
                return 70
            elif decline_pct >= 0.25:
                return 55
            else:
                return 40
        elif is_declining:
            return 20
        return 0

    def _score_momentum_pause(self) -> int:
        """
        Parameter 2: |price_change| over 2 min (0-30 pts). In-memory only.

        Uses MarketData.price_history deque. Zero API cost.

        Scoring (default threshold = 0.05%):
          < 0.025% (~$1.50 at SPX 6000) → 30
          < 0.05% (~$3) → 25
          < 0.10% (~$6) → 10
          >= 0.10% → 0
        """
        history = self.market_data.price_history
        if len(history) < 3 or self.current_price <= 0:
            return 0

        cutoff = get_us_market_time() - timedelta(minutes=2)
        oldest = None
        for ts, price in history:
            if ts >= cutoff:
                oldest = price
                break

        if not oldest or oldest <= 0:
            logger.debug(f"MKT-031 Momentum: no price found in 2min window (history={len(history)})")
            return 0

        pct = abs((self.current_price - oldest) / oldest) * 100
        threshold = self.scout_momentum_threshold
        delta = self.current_price - oldest

        logger.debug(
            f"MKT-031 Momentum: price={self.current_price:.2f}, "
            f"2min_ago={oldest:.2f}, delta={delta:+.2f}, "
            f"pct={pct:.4f}% (thresholds: {threshold*0.5:.3f}/{threshold:.3f}/{threshold*2:.3f})"
        )

        if pct < threshold * 0.5:
            return 30   # Very calm (< 0.025%)
        if pct < threshold:
            return 25   # Calm (< 0.05%)
        if pct < threshold * 2:
            return 10   # Mild (< 0.10%)
        return 0

    # =========================================================================
    # MKT-018: EARLY CLOSE BASED ON RETURN ON CAPITAL (ROC)
    # =========================================================================

    def _handle_monitoring(self) -> str:
        """
        Override parent to add MKT-018 early close check after stop loss monitoring.

        Flow: parent handles stops + entry scheduling → then we check ROC threshold.
        Early close only runs AFTER all entries are placed and if positions are open.
        """
        # Run parent monitoring (stop checks, entry scheduling, etc.)
        result = super()._handle_monitoring()

        # MKT-033: Check if any surviving long legs can be sold for profit
        # Only relevant when short_only_stop is enabled (longs stay open after stop)
        if self.short_only_stop:
            self._check_long_salvage()

        # MKT-018: Check early close AFTER parent monitoring completes
        # Only check if:
        # 1. Early close is enabled
        # 2. Not already triggered
        # 3. All entries have been placed (or skipped/failed)
        # 4. There are active positions to close
        # 5. Not in last 15 minutes before close (positions will expire naturally)
        if (self.early_close_enabled
            and not self._early_close_triggered
            and self._next_entry_index >= len(self.entry_times)
            and len(self.daily_state.active_entries) > 0):

            now = get_us_market_time()
            if now.hour < 15 or (now.hour == 15 and now.minute < 45):
                early_close_result = self._check_early_close()
                if early_close_result:
                    return early_close_result

        return result

    def _check_early_close(self) -> Optional[str]:
        """
        MKT-018: Check if Return on Capital threshold is met for early close.

        Calculates: ROC = (net_pnl - close_cost) / capital_deployed
        If ROC >= threshold, triggers _execute_early_close().

        Returns:
            Action string if early close triggered, None otherwise.
        """
        # Calculate current net P&L (same as heartbeat: realized + unrealized - commission)
        unrealized = self._get_total_saxo_pnl()
        total_pnl = self.daily_state.total_realized_pnl + unrealized
        net_pnl = total_pnl - self.daily_state.total_commission

        # Count active position LEGS (not entries)
        active_legs = self._count_active_position_legs()
        close_cost = active_legs * self.early_close_cost_per_position

        # Calculate ROC
        capital_deployed = self._calculate_capital_deployed()
        if capital_deployed <= 0:
            return None

        pnl_after_close = net_pnl - close_cost
        roc = pnl_after_close / capital_deployed

        if roc >= self.early_close_roc_threshold:
            # MKT-023: Check if holding is better than closing now
            if self.hold_check_enabled:
                should_close, hold_details = self._check_hold_vs_close(pnl_after_close)
                if not should_close:
                    wc = hold_details['worst_case_hold_pnl']
                    advantage = wc - pnl_after_close
                    logger.info(
                        f"MKT-023 HOLD CHECK: ROC {roc*100:.2f}% >= threshold, "
                        f"BUT holding is better | close_now=${pnl_after_close:.2f} vs "
                        f"worst_hold=${wc:.2f} (+${advantage:.2f}) | "
                        f"Lean: {hold_details['market_lean']} | "
                        f"Cushion: Call {hold_details['avg_call_cushion']:.0f}% / "
                        f"Put {hold_details['avg_put_cushion']:.0f}%"
                    )
                    self._log_safety_event(
                        "MKT-023_HOLD",
                        f"ROC {roc*100:.2f}% but hold better by ${advantage:.2f} "
                        f"({hold_details['market_lean']})"
                    )
                    return None  # Don't close — hold is better

            logger.warning(
                f"MKT-018 EARLY CLOSE TRIGGERED: ROC {roc*100:.2f}% >= "
                f"{self.early_close_roc_threshold*100:.1f}% threshold | "
                f"net_pnl=${net_pnl:.2f} - close_cost=${close_cost:.2f} = "
                f"${pnl_after_close:.2f} / capital=${capital_deployed:.0f}"
            )
            return self._execute_early_close(roc)

        return None

    def _check_hold_vs_close(self, close_now_pnl: float) -> Tuple[bool, Dict[str, Any]]:
        """
        MKT-023: Compare close-now P&L vs worst-case hold P&L.

        Determines market lean from average cushion per side, then calculates
        what happens if the stressed side gets fully stopped and the safe side
        expires worthless. If holding is better even in that worst case, returns
        should_close=False.

        Args:
            close_now_pnl: Net P&L if we close all positions now (net_pnl - close_cost)

        Returns:
            (should_close, details_dict) where details_dict has diagnostic data.
        """
        details: Dict[str, Any] = {
            'close_now_pnl': close_now_pnl,
        }

        # --- Step A: Collect per-side cushions across active entries ---
        call_cushions: List[float] = []
        put_cushions: List[float] = []
        # (entry, side_name, credit, stop_level)
        active_sides: List[Tuple[Any, str, float, float]] = []

        for entry in self.daily_state.active_entries:
            call_active = not (
                entry.call_side_stopped
                or getattr(entry, 'call_side_expired', False)
                or getattr(entry, 'call_side_skipped', False)
            )
            put_active = not (
                entry.put_side_stopped
                or getattr(entry, 'put_side_expired', False)
                or getattr(entry, 'put_side_skipped', False)
            )

            if call_active and entry.call_side_stop > 0:
                cushion = (entry.call_side_stop - entry.call_spread_value) / entry.call_side_stop * 100
                call_cushions.append(cushion)
                active_sides.append((entry, "call", entry.call_spread_credit, entry.call_side_stop))

            if put_active and entry.put_side_stop > 0:
                cushion = (entry.put_side_stop - entry.put_spread_value) / entry.put_side_stop * 100
                put_cushions.append(cushion)
                active_sides.append((entry, "put", entry.put_spread_credit, entry.put_side_stop))

        # --- Step B: Determine market lean ---
        avg_call = sum(call_cushions) / len(call_cushions) if call_cushions else 0
        avg_put = sum(put_cushions) / len(put_cushions) if put_cushions else 0
        details['avg_call_cushion'] = avg_call
        details['avg_put_cushion'] = avg_put

        # Can't determine lean if only one side exists (all one-sided entries)
        if not call_cushions or not put_cushions:
            details['market_lean'] = 'ONE_SIDED'
            details['reason'] = 'all_one_sided'
            return (True, details)  # Let MKT-018 close

        # No clear lean if cushions are nearly equal
        if abs(avg_call - avg_put) < self.hold_check_lean_tolerance:
            details['market_lean'] = 'EQUAL'
            details['reason'] = 'no_clear_lean'
            return (True, details)  # Let MKT-018 close

        # Lower cushion = stressed
        if avg_call < avg_put:
            stressed_side = "call"
            details['market_lean'] = 'CALLS_STRESSED'
        else:
            stressed_side = "put"
            details['market_lean'] = 'PUTS_STRESSED'

        # --- Step C: Calculate worst-case hold P&L ---
        safe_credit_sum = 0.0
        stressed_credit_sum = 0.0
        stressed_net_sum = 0.0
        stressed_sides_count = 0

        for entry, side, credit, stop_level in active_sides:
            if side == stressed_side:
                # Worst case: this side gets stopped
                # Net = credit collected - cost to close at stop level
                stressed_credit_sum += credit
                stressed_net_sum += (credit - stop_level)
                stressed_sides_count += 1
            else:
                # Safe side expires worthless = keep full credit
                safe_credit_sum += credit

        # Commission for stop closes (2 legs per stopped side)
        stop_close_commission = (
            stressed_sides_count * 2 * self.commission_per_leg * self.contracts_per_entry
        )

        worst_case_hold_pnl = (
            self.daily_state.total_realized_pnl   # Already-realized P&L
            + safe_credit_sum                      # Safe sides expire worthless
            + stressed_net_sum                     # Stressed sides get stopped
            - self.daily_state.total_commission    # Commission already incurred
            - stop_close_commission                # Additional commission for stops
        )

        # Best case: ALL active sides expire worthless (no stops at all)
        all_expire_pnl = (
            self.daily_state.total_realized_pnl
            + safe_credit_sum + stressed_credit_sum  # All credits kept
            - self.daily_state.total_commission
        )

        details['worst_case_hold_pnl'] = worst_case_hold_pnl
        details['all_expire_pnl'] = all_expire_pnl
        details['safe_credit_sum'] = safe_credit_sum
        details['stressed_credit_sum'] = stressed_credit_sum
        details['stressed_net_sum'] = stressed_net_sum
        details['stressed_sides_count'] = stressed_sides_count
        details['stop_close_commission'] = stop_close_commission

        # --- Step D: Decision ---
        # Strictly > : if equal, close (bird-in-hand principle)
        if worst_case_hold_pnl > close_now_pnl:
            details['decision'] = 'HOLD'
            return (False, details)
        else:
            details['decision'] = 'CLOSE'
            return (True, details)

    def _count_active_position_legs(self) -> int:
        """Count individual position legs still open across all active entries."""
        count = 0
        for entry in self.daily_state.active_entries:
            if not entry.call_side_stopped and not entry.call_side_expired and not getattr(entry, 'call_side_skipped', False):
                if entry.short_call_position_id:
                    count += 1
                if entry.long_call_position_id:
                    count += 1
            if not entry.put_side_stopped and not entry.put_side_expired and not getattr(entry, 'put_side_skipped', False):
                if entry.short_put_position_id:
                    count += 1
                if entry.long_put_position_id:
                    count += 1
        return count

    def _execute_early_close(self, roc: float) -> str:
        """
        MKT-018: Close ALL active positions to lock in profit.

        This is an IRREVERSIBLE action — once triggered, all positions are closed
        via market orders and the daily summary is sent immediately.

        Args:
            roc: Return on Capital that triggered the close
        """
        now = get_us_market_time()
        self._early_close_triggered = True
        self._early_close_time = now

        logger.info("=" * 60)
        logger.info("MKT-018: EXECUTING EARLY CLOSE - closing all active positions")
        logger.info("=" * 60)

        # Phase 1: Close each active entry's open legs
        entries_closed = 0
        legs_closed = 0
        legs_failed = 0
        deferred_legs = []  # For async fill lookup

        # Take a snapshot of active entries (list may change as we mark sides)
        active_snapshot = list(self.daily_state.active_entries)

        for entry in active_snapshot:
            entry_legs_closed, entry_legs_failed, entry_deferred = self._close_entry_early(entry)
            legs_closed += entry_legs_closed
            legs_failed += entry_legs_failed
            deferred_legs.extend(entry_deferred)
            if entry_legs_closed > 0 or entry_legs_failed > 0:
                entries_closed += 1

        # Phase 2: Run deferred fill lookup in background thread if any legs had fill_price=None
        if deferred_legs:
            self._spawn_async_early_close_fill_correction(deferred_legs)

        # Phase 3: Unregister positions from registry
        # Only clear position IDs for sides that were successfully closed.
        # If a leg failed to close, keep its position_id/uic so it remains
        # trackable (settlement will handle it as a normal expiration).
        for entry in self.daily_state.entries:
            # Call side: only clear if marked as early-closed (expired flag set by _close_entry_early)
            if entry.call_side_expired or entry.call_side_stopped or getattr(entry, 'call_side_skipped', False):
                for leg in ["short_call", "long_call"]:
                    pos_id = getattr(entry, f"{leg}_position_id", None)
                    if pos_id:
                        try:
                            self.registry.unregister(pos_id)
                        except Exception:
                            pass
                        setattr(entry, f"{leg}_position_id", None)
                        setattr(entry, f"{leg}_uic", 0)
            # Put side: same logic
            if entry.put_side_expired or entry.put_side_stopped or getattr(entry, 'put_side_skipped', False):
                for leg in ["short_put", "long_put"]:
                    pos_id = getattr(entry, f"{leg}_position_id", None)
                    if pos_id:
                        try:
                            self.registry.unregister(pos_id)
                        except Exception:
                            pass
                        setattr(entry, f"{leg}_position_id", None)
                        setattr(entry, f"{leg}_uic", 0)

        # Phase 4: Mark bot state
        self.state = MEICState.DAILY_COMPLETE
        self._settlement_reconciliation_complete = True  # No settlement needed
        self._daily_summary_sent = True  # Prevent duplicate alert in _handle_daily_complete

        # Phase 5: Record the locked-in P&L
        final_net_pnl = self.daily_state.total_realized_pnl - self.daily_state.total_commission
        self._early_close_pnl = final_net_pnl

        # Phase 6: Save state before any logging (crash safety)
        self._save_state_to_disk()

        # Phase 7: Send alert (MEDIUM priority — profit locked in)
        try:
            capital_deployed = self._calculate_capital_deployed()
            self.alert_service.send_alert(
                alert_type=AlertType.PROFIT_TARGET,
                title=f"MKT-018 Early Close: +${final_net_pnl:.2f} ({roc*100:.1f}% ROC)",
                message=(
                    f"Closed all {entries_closed} entries ({legs_closed} legs) at {now.strftime('%I:%M %p ET')}\n"
                    f"ROC: {roc*100:.2f}% (threshold: {self.early_close_roc_threshold*100:.1f}%)\n"
                    f"Net P&L: ${final_net_pnl:.2f} | Capital: ${capital_deployed:,.0f}"
                ),
                priority=AlertPriority.MEDIUM,
            )
        except Exception as e:
            logger.error(f"MKT-018: Alert failed: {e}")

        # Phase 8: Log daily summary IMMEDIATELY
        # No need to wait for settlement — all positions are already closed.
        # Wait for async fill corrections first (base class waits 15s, but early close
        # can have many deferred legs needing 3s sleep + retries each).
        if deferred_legs:
            wait_time = min(3.0 + len(deferred_legs) * 5.0, 60.0)  # Scale with legs, cap at 60s
            self._wait_for_pending_fill_corrections(timeout=wait_time)
        try:
            self.log_daily_summary()
            self.log_account_summary()
            self.log_performance_metrics()
            self.log_position_snapshot()
        except Exception as e:
            logger.error(f"MKT-018: Daily summary logging failed: {e}")

        logger.info("=" * 60)
        logger.info(
            f"MKT-018: EARLY CLOSE COMPLETE | {entries_closed} entries, "
            f"{legs_closed} legs closed, {legs_failed} failed | "
            f"Net P&L: ${final_net_pnl:.2f} | ROC: {roc*100:.2f}%"
        )
        logger.info("=" * 60)

        return (
            f"MKT-018 EARLY CLOSE: +${final_net_pnl:.2f} ({roc*100:.1f}% ROC) - "
            f"all positions closed at {now.strftime('%I:%M %p ET')}"
        )

    def _close_entry_early(self, entry) -> Tuple[int, int, list]:
        """
        Close all open legs of an entry for MKT-018 early close.

        Returns: (legs_closed, legs_failed, deferred_legs)
            deferred_legs: List of (entry, side_name, leg_name, order_id, uic) for async lookup
        """
        legs_closed = 0
        legs_failed = 0
        deferred_legs = []

        sides_to_close = []

        # Check call side
        if (not entry.call_side_stopped and not entry.call_side_expired
            and not getattr(entry, 'call_side_skipped', False) and entry.short_call_position_id):
            sides_to_close.append(("call", [
                ("short_call", entry.short_call_position_id, entry.short_call_uic),
                ("long_call", entry.long_call_position_id, entry.long_call_uic),
            ]))

        # Check put side
        if (not entry.put_side_stopped and not entry.put_side_expired
            and not getattr(entry, 'put_side_skipped', False) and entry.short_put_position_id):
            sides_to_close.append(("put", [
                ("short_put", entry.short_put_position_id, entry.short_put_uic),
                ("long_put", entry.long_put_position_id, entry.long_put_uic),
            ]))

        for side_name, legs in sides_to_close:
            side_close_cost = 0.0
            side_legs_closed = 0

            for leg_name, pos_id, uic in legs:
                if not pos_id:
                    continue

                # Fix #81: Skip closing long legs with $0 bid (worthless, expire naturally)
                # Deeply OTM long legs often have no market - Saxo rejects market orders
                # with 409 Conflict and limit orders at $0.05 can also fail. These legs
                # expire worthless at 4 PM, so closing them wastes API calls for ~$0 value.
                if leg_name.startswith("long") and uic:
                    try:
                        quote = self.client.get_quote(uic, asset_type="StockIndexOption")
                        bid = 0
                        if quote:
                            bid = quote.get("Quote", {}).get("Bid", 0) or quote.get("Bid", 0) or 0
                        if bid <= 0:
                            logger.info(
                                f"  Fix #81: Skipping {leg_name} close for Entry #{entry.entry_number} "
                                f"(bid=${bid:.2f}, expires worthless) — avoiding 409 risk"
                            )
                            # Count as closed (it will expire worthless, no P&L impact)
                            legs_closed += 1
                            side_legs_closed += 1
                            continue
                    except Exception as e:
                        logger.warning(f"  Fix #81: Quote check failed for {leg_name}: {e}, proceeding with close")

                success, fill_price, order_id = self._close_position_with_retry(
                    pos_id, leg_name, uic=uic, entry_number=entry.entry_number
                )
                if success:
                    legs_closed += 1
                    side_legs_closed += 1
                    if fill_price and fill_price > 0:
                        cost = fill_price * 100 * entry.contracts
                        if leg_name.startswith("short"):
                            side_close_cost += cost  # Pay to buy back short
                        else:
                            side_close_cost -= cost  # Receive from selling long
                    else:
                        # Deferred fill lookup needed — capture UIC now before Phase 3 clears it
                        deferred_legs.append((entry, side_name, leg_name, order_id, uic))
                    # Track close commission
                    entry.close_commission += self.commission_per_leg
                    self.daily_state.total_commission += self.commission_per_leg
                else:
                    legs_failed += 1
                    logger.error(f"MKT-018: Failed to close {leg_name} for Entry #{entry.entry_number}")

            # Mark side as early-closed (reuse expired flag for compatibility)
            if side_legs_closed > 0:
                credit = getattr(entry, f"{side_name}_spread_credit", 0)
                setattr(entry, f"{side_name}_side_expired", True)
                entry.early_closed = True

                if credit > 0 and side_close_cost != 0:
                    # Net P&L for this side = credit - net_close_cost
                    # side_close_cost is positive when we spent more buying back short than
                    # we received from selling long (net outflow)
                    self.daily_state.total_realized_pnl += credit
                    self.daily_state.total_realized_pnl -= side_close_cost
                    logger.info(
                        f"  Entry #{entry.entry_number} {side_name} side early-closed: "
                        f"credit=${credit:.2f}, close_cost=${side_close_cost:.2f}, "
                        f"net=${credit - side_close_cost:.2f}"
                    )
                elif credit > 0:
                    # No fill prices yet — use credit only, deferred lookup will correct
                    self.daily_state.total_realized_pnl += credit
                    logger.info(
                        f"  Entry #{entry.entry_number} {side_name} side early-closed: "
                        f"credit=${credit:.2f} (fill prices deferred)"
                    )

            # Mark entry complete if all sides now done
            call_done = entry.call_side_stopped or entry.call_side_expired or getattr(entry, 'call_side_skipped', False)
            put_done = entry.put_side_stopped or entry.put_side_expired or getattr(entry, 'put_side_skipped', False)
            if getattr(entry, 'call_only', False):
                entry.is_complete = call_done
            elif getattr(entry, 'put_only', False):
                entry.is_complete = put_done
            else:
                entry.is_complete = call_done and put_done

        return legs_closed, legs_failed, deferred_legs

    def _spawn_async_early_close_fill_correction(self, deferred_legs: list):
        """
        MKT-018: Spawn background thread to look up actual fill prices for early close legs.

        Same pattern as FIX #75's _spawn_async_fill_correction but handles multiple
        entries/sides at once. Non-blocking — main loop continues immediately.
        """
        def worker():
            try:
                logger.info(
                    f"MKT-018: Deferred fill lookup for {len(deferred_legs)} legs, "
                    f"waiting 3s for Saxo sync..."
                )
                time.sleep(3)

                total_correction = 0.0
                for entry, side_name, leg_name, order_id, uic in deferred_legs:
                    fill_price = None
                    source = None

                    try:
                        # Tier 1: Activities endpoint
                        # Note: uic is captured at close time (5th tuple element) because
                        # Phase 3 of _execute_early_close clears entry UICs to 0
                        if order_id:
                            filled, fill_details = self.client.check_order_filled_by_activity(
                                order_id=order_id,
                                uic=uic,
                                max_retries=3,
                                retry_delay=1.5
                            )
                            if filled and fill_details:
                                fp = fill_details.get("fill_price", 0)
                                if fp and fp > 0:
                                    fill_price = fp
                                    source = "activities"

                        # Tier 2: Closed positions endpoint
                        if fill_price is None and uic:
                            buy_or_sell = "Sell" if leg_name.startswith("short") else "Buy"
                            closed_info = self.client.get_closed_position_price(uic, buy_or_sell=buy_or_sell)
                            if closed_info:
                                cp = closed_info.get("closing_price")
                                if cp and cp > 0:
                                    fill_price = cp
                                    source = "closedpositions"

                        if fill_price is not None:
                            actual_cost = fill_price * 100 * entry.contracts
                            if leg_name.startswith("short"):
                                # We paid to buy back — this is a cost
                                self.daily_state.total_realized_pnl -= actual_cost
                                total_correction -= actual_cost
                            else:
                                # We received from selling — this reduces cost
                                self.daily_state.total_realized_pnl += actual_cost
                                total_correction += actual_cost
                            logger.info(
                                f"MKT-018: Deferred fill for Entry #{entry.entry_number} {leg_name} "
                                f"via {source}: ${fill_price:.2f}"
                            )
                        else:
                            logger.warning(
                                f"MKT-018: No fill price found for Entry #{entry.entry_number} {leg_name}"
                            )
                    except Exception as e:
                        logger.warning(f"MKT-018: Deferred lookup error for {leg_name}: {e}")

                if abs(total_correction) > 0.01:
                    self._save_state_to_disk()
                    logger.info(f"MKT-018: Async fill correction applied: ${total_correction:+.2f}")
                else:
                    logger.info("MKT-018: Async fill lookup complete (no correction needed)")

            except Exception as e:
                logger.warning(f"MKT-018: Async fill correction thread failed: {e}")

        thread = threading.Thread(
            target=worker, daemon=True,
            name="mkt018_fill_correction"
        )
        thread.start()
        self._pending_fill_corrections.append(thread)
        logger.info(f"MKT-018: Spawned async fill correction thread for {len(deferred_legs)} legs")

    # =========================================================================
    # ANTI-WHIPSAW FILTER
    # =========================================================================

    def _check_whipsaw_filter(self) -> Optional[str]:
        """
        Anti-whipsaw: skip entry if SPX intraday range (high - low) exceeds
        whipsaw_range_skip_mult × expected daily move.

        Expected daily move = SPX_open × VIX_open / 100 / sqrt(252)

        Returns skip reason string if should skip, None if OK to enter.
        """
        if self.whipsaw_range_skip_mult is None:
            return None

        spx_open = self.market_data.spx_open
        vix_open = self.market_data.vix_open
        spx_high = self.market_data.spx_high
        spx_low = self.market_data.spx_low

        if not spx_open or spx_open <= 0 or not vix_open or vix_open <= 0:
            return None  # No data, don't block

        if spx_low == float('inf') or spx_low <= 0 or spx_high <= 0:
            return None  # No range data yet

        expected_move = spx_open * (vix_open / 100) / (252 ** 0.5)
        intraday_range = spx_high - spx_low
        threshold = self.whipsaw_range_skip_mult * expected_move

        if intraday_range > threshold:
            reason = (
                f"whipsaw_filter (range={intraday_range:.0f}pt > "
                f"{self.whipsaw_range_skip_mult}×EM={threshold:.0f}pt, "
                f"VIX={vix_open:.1f})"
            )
            logger.warning(f"Anti-whipsaw: SKIPPING entry — {reason}")
            return reason

        return None

    # MKT-035: DOWN DAY FILTER
    # =========================================================================

    def _call_only_stop_label(self, override: str) -> str:
        """Return human-readable stop formula label for call-only Telegram alerts."""
        # All call-only types use unified formula: call + theo put + buffer
        return f"call + ${self.downday_theoretical_put_credit / 100:.2f} theo put"

    def _check_downday_filter(self) -> bool:
        """
        MKT-035: Check if SPX is down more than threshold from today's open.

        Returns True if call-only should be used (bearish day detected).
        Uses market_data.spx_open as the reference price — a down day means
        SPX has dropped below where it opened, not below an intraday high.
        """
        if not self.downday_callonly_enabled:
            return False

        # Use open price as reference
        spx_ref = self.market_data.spx_open
        ref_label = "open"
        if not spx_ref or spx_ref <= 0:
            logger.warning("MKT-035: No SPX open price available, skipping down-day check")
            return False

        current = self.current_price
        if current <= 0:
            return False

        change_pct = (current - spx_ref) / spx_ref
        threshold = -self.downday_threshold_pct

        is_down = change_pct < threshold
        triggered = "TRIGGERED → call-only" if is_down else "not triggered"
        logger.info(
            f"MKT-035: SPX {change_pct * 100:+.2f}% from {ref_label} "
            f"({current:.1f} vs {spx_ref:.1f}), threshold {threshold * 100:.1f}% — {triggered}"
        )
        return is_down

    def _is_conditional_entry(self, entry_num: int) -> bool:
        """Check if this entry number is a conditional slot (downday OR upday)."""
        return entry_num > self._base_entry_count

    def _is_downday_conditional_entry(self, entry_num: int) -> bool:
        """Check if this entry's time slot is enabled for downday call-only (MKT-035)."""
        if entry_num < 1 or entry_num > len(self.entry_times):
            return False
        return self.entry_times[entry_num - 1] in self._conditional_downday_times_set

    def _is_upday_conditional_entry(self, entry_num: int) -> bool:
        """Check if this entry's time slot is enabled for upday put-only."""
        if entry_num < 1 or entry_num > len(self.entry_times):
            return False
        return self.entry_times[entry_num - 1] in self._conditional_upday_times_set

    def _check_upday_filter(self) -> bool:
        """
        Check if SPX is up more than upday_threshold_pct from today's open.

        Returns True if put-only should be used (bullish day detected).
        Reference is always session open (upday_reference="open" or "low" both
        use spx_open — intraday low tracking not yet implemented in live bot).
        """
        if not self.upday_putonly_enabled:
            return False

        spx_ref = self.market_data.spx_open
        if not spx_ref or spx_ref <= 0:
            logger.warning("Upday-035: No SPX open price available, skipping up-day check")
            return False

        current = self.current_price
        if current <= 0:
            return False

        change_pct = (current - spx_ref) / spx_ref
        is_up = change_pct > self.upday_threshold_pct

        triggered = "TRIGGERED → put-only" if is_up else "not triggered"
        logger.info(
            f"Upday-035: SPX {change_pct * 100:+.2f}% from open "
            f"({current:.1f} vs {spx_ref:.1f}), threshold +{self.upday_threshold_pct * 100:.1f}% — {triggered}"
        )
        return is_up

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

            # MKT-031: Cache raw bars for ATR scoring (zero extra API cost)
            self._cached_chart_bars = bars
            self._cached_chart_time = get_us_market_time()

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
    # MKT-011: Credit Gate for HYDRA
    # =========================================================================

    def _check_credit_gate(self, entry: HydraIronCondorEntry) -> Tuple[str, bool, float, float]:
        """
        MKT-011 + MKT-032/MKT-039/MKT-040: Check if estimated credit is above minimum viable threshold.

        Returns viability assessment with estimated credits. When call side
        is non-viable but put side meets threshold, returns "put_only" only
        if VIX < put_only_max_vix (MKT-032). When put side is non-viable
        but call side meets threshold, returns "call_only" (MKT-040).
        At elevated VIX with no viable put, returns "skip".

        Args:
            entry: HydraIronCondorEntry with strikes calculated

        Returns:
            Tuple of (result, estimation_worked, estimated_call, estimated_put):
            - result: "proceed", "put_only", "call_only", or "skip"
            - estimation_worked: True if we got valid quotes, False if estimation failed
            - estimated_call: estimated call credit in cents (0.0 if failed)
            - estimated_put: estimated put credit in cents (0.0 if failed)
        """
        estimated_call, estimated_put = self._estimate_entry_credit(entry)

        # If we couldn't estimate credit, signal that estimation failed
        # MKT-010 will run as fallback
        if estimated_call == 0.0 and estimated_put == 0.0:
            logger.warning(
                f"MKT-011: Could not estimate credit for Entry #{entry.entry_number} - "
                f"falling back to MKT-010 illiquidity check"
            )
            return ("proceed", False, 0.0, 0.0)  # estimation_worked = False

        # Separate thresholds: calls use min_viable_credit_per_side ($2.00),
        # puts use min_viable_credit_put_side ($2.75)
        call_min = self.min_viable_credit_per_side
        put_min = self.min_viable_credit_put_side
        call_viable = estimated_call >= call_min
        put_viable = estimated_put >= put_min

        # MKT-029: Graduated call fallback — try min-$0.05 (if above floor), then floor.
        # Floor from config (call_credit_floor, default $0.75) allows accepting lower
        # credits at far-OTM strikes. Keeps entries wider = safer cushion.
        if not call_viable:
            call_floor = self.call_credit_floor
            # Only include intermediate step (min-$0.05) if it's above the floor
            call_fallbacks = [f for f in [call_min - 5] if f > call_floor] + [call_floor]
            for fallback in call_fallbacks:
                if estimated_call >= fallback:
                    call_viable = True
                    logger.info(
                        f"MKT-029: Call credit ${estimated_call / 100:.2f} accepted at "
                        f"fallback ${fallback / 100:.2f} (primary: ${call_min / 100:.2f}, "
                        f"floor: ${call_floor / 100:.2f})"
                    )
                    self._log_safety_event(
                        "MKT-029_CALL_FALLBACK",
                        f"Entry #{entry.entry_number}: call ${estimated_call / 100:.2f} "
                        f"at fallback ${fallback / 100:.2f}"
                    )
                    break

        # MKT-029: Graduated put fallback — try min-$0.05 (if above floor), then floor.
        if not put_viable:
            put_floor = self.put_credit_floor
            # Only include intermediate step (min-$0.05) if it's above the floor
            put_fallbacks = [f for f in [put_min - 5] if f > put_floor] + [put_floor]
            for fallback in put_fallbacks:
                if estimated_put >= fallback:
                    put_viable = True
                    logger.info(
                        f"MKT-029: Put credit ${estimated_put / 100:.2f} accepted at "
                        f"fallback ${fallback / 100:.2f} (primary: ${put_min / 100:.2f}, "
                        f"floor: ${put_floor / 100:.2f})"
                    )
                    self._log_safety_event(
                        "MKT-029_PUT_FALLBACK",
                        f"Entry #{entry.entry_number}: put ${estimated_put / 100:.2f} "
                        f"at fallback ${fallback / 100:.2f}"
                    )
                    break

        if call_viable and put_viable:
            logger.info(
                f"MKT-011: Credit gate PASSED for Entry #{entry.entry_number}: "
                f"Call ${estimated_call / 100:.2f} (min: ${call_min / 100:.2f}), "
                f"Put ${estimated_put / 100:.2f} (min: ${put_min / 100:.2f})"
            )
            return ("proceed", True, estimated_call, estimated_put)

        if not call_viable and not put_viable:
            logger.warning(
                f"MKT-011: SKIPPING Entry #{entry.entry_number} - both sides non-viable. "
                f"Call ${estimated_call / 100:.2f} (min: ${call_min / 100:.2f}), "
                f"Put ${estimated_put / 100:.2f} (min: ${put_min / 100:.2f})"
            )
            self._log_safety_event(
                "MKT-011_ENTRY_SKIPPED",
                f"Entry #{entry.entry_number} - call ${estimated_call / 100:.2f}, put ${estimated_put / 100:.2f}",
                "Skipped"
            )
            return ("skip", True, estimated_call, estimated_put)

        # One side viable, other not
        if not call_viable:
            # MKT-032/MKT-039: VIX gate for put-only entries
            # At VIX >= threshold, put-only skipped (no call hedge in volatile conditions).
            # At VIX < threshold, put-only viable (credit + $1.55 buffer prevents false stops).
            vix_allows_put_only = self.current_vix < self.put_only_max_vix
            if self.one_sided_entries_enabled and vix_allows_put_only:
                # Call non-viable, put viable, VIX calm → put-only entry (v1.7.1)
                logger.info(
                    f"MKT-011: Entry #{entry.entry_number} call credit non-viable "
                    f"(${estimated_call / 100:.2f} < ${call_min / 100:.2f}) - "
                    f"put ${estimated_put / 100:.2f} viable, VIX {self.current_vix:.1f} < "
                    f"{self.put_only_max_vix} → converting to put-only"
                )
                self._log_safety_event(
                    "MKT-011_PUT_ONLY",
                    f"Entry #{entry.entry_number} - call ${estimated_call / 100:.2f} non-viable, "
                    f"put ${estimated_put / 100:.2f} → put-only (VIX {self.current_vix:.1f})",
                    "Put-Only"
                )
                return ("put_only", True, estimated_call, estimated_put)
            elif self.one_sided_entries_enabled and not vix_allows_put_only:
                # MKT-032: VIX too high for put-only → skip
                logger.warning(
                    f"MKT-032: Entry #{entry.entry_number} call credit non-viable "
                    f"(${estimated_call / 100:.2f} < ${call_min / 100:.2f}) - "
                    f"VIX {self.current_vix:.1f} >= {self.put_only_max_vix} → "
                    f"SKIPPING (put-only too risky at elevated VIX)"
                )
                self._log_safety_event(
                    "MKT-032_VIX_SKIP",
                    f"Entry #{entry.entry_number} - call non-viable, VIX {self.current_vix:.1f} "
                    f">= {self.put_only_max_vix} → skip (no unhedged put-only)",
                    "Skipped"
                )
                return ("skip", True, estimated_call, estimated_put)
            else:
                # One-sided disabled → skip entirely
                logger.warning(
                    f"MKT-011: Entry #{entry.entry_number} call credit non-viable "
                    f"(${estimated_call / 100:.2f} < ${call_min / 100:.2f}) - "
                    f"SKIPPING (one-sided entries disabled)"
                )
                self._log_safety_event(
                    "MKT-011_SKIP",
                    f"Entry #{entry.entry_number} - call non-viable, one-sided disabled",
                    "Skipped"
                )
                return ("skip", True, estimated_call, estimated_put)
        else:
            # MKT-040: Put non-viable, call viable → convert to call-only
            # Data: low-credit call-only entries have 89% WR, +$46 EV per entry.
            # Stop = call_credit + theoretical $2.60 put + call buffer (unified with MKT-035/038).
            if self.one_sided_entries_enabled:
                logger.info(
                    f"MKT-040: Entry #{entry.entry_number} put credit non-viable "
                    f"(${estimated_put / 100:.2f} < ${put_min / 100:.2f}) - "
                    f"call ${estimated_call / 100:.2f} viable → converting to call-only"
                )
                self._log_safety_event(
                    "MKT-040_CALL_ONLY",
                    f"Entry #{entry.entry_number} - put ${estimated_put / 100:.2f} non-viable, "
                    f"call ${estimated_call / 100:.2f} → call-only",
                    "Call-Only"
                )
                return ("call_only", True, estimated_call, estimated_put)
            else:
                logger.warning(
                    f"MKT-011: Entry #{entry.entry_number} put credit non-viable "
                    f"(${estimated_put / 100:.2f} < ${put_min / 100:.2f}) - "
                    f"SKIPPING (one-sided entries disabled)"
                )
                self._log_safety_event(
                    "MKT-011_SKIP",
                    f"Entry #{entry.entry_number} - put non-viable, one-sided disabled",
                    "Skipped"
                )
                return ("skip", True, estimated_call, estimated_put)

    @staticmethod
    def _snap_to_chain_strike(target: float, uic_map: dict, max_snap: int = 15) -> tuple:
        """
        Find the nearest available strike in the option chain.

        Saxo's 0DTE chain uses 5pt intervals near ATM but switches to 25pt
        intervals far OTM (above ~130pt). MKT-020/MKT-022 build candidates at
        5pt steps, so many far-OTM strikes don't exist. This snaps to the
        nearest chain strike within max_snap points.

        Args:
            target: Desired strike price
            uic_map: {strike: uic} mapping from option chain
            max_snap: Maximum points to snap (default 15 — half of 25pt spacing)

        Returns:
            (actual_strike, uic) if found within tolerance, (None, None) otherwise
        """
        if target in uic_map:
            return target, uic_map[target]

        best_strike = None
        best_dist = max_snap + 1
        for strike in uic_map:
            dist = abs(strike - target)
            if dist < best_dist:
                best_dist = dist
                best_strike = strike

        if best_strike is not None and best_dist <= max_snap:
            return best_strike, uic_map[best_strike]

        return None, None

    @staticmethod
    def _snap_long_for_spread(short_strike: float, target_width: int,
                              uic_map: dict, is_call: bool) -> tuple:
        """
        Find the best long leg strike that gives a spread width closest to target.

        After snapping the short leg, the long leg must also exist in the chain
        AND produce a spread width close to the target (±15pt tolerance).
        For calls: long = short + width. For puts: long = short - width.

        Args:
            short_strike: The snapped short strike
            target_width: Desired spread width (e.g., 110)
            uic_map: {strike: uic} mapping from option chain
            is_call: True for calls (long > short), False for puts (long < short)

        Returns:
            (actual_strike, uic) if found, (None, None) otherwise
        """
        ideal_long = short_strike + target_width if is_call else short_strike - target_width

        best_strike = None
        best_dist = 16  # Max tolerance: 15pt
        for strike in uic_map:
            dist = abs(strike - ideal_long)
            if dist < best_dist:
                # Ensure spread is at least min_width (don't snap to tiny spreads)
                actual_width = abs(strike - short_strike)
                if actual_width >= target_width - 15:  # Allow slightly narrower
                    best_dist = dist
                    best_strike = strike

        if best_strike is not None:
            return best_strike, uic_map[best_strike]

        return None, None

    def _apply_progressive_call_tightening(self, entry: HydraIronCondorEntry) -> bool:
        """
        MKT-020: Progressive call OTM tightening for full IC entries.

        When initial call credit is below minimum, moves the short call closer
        to ATM in 5pt steps until credit >= minimum or OTM floor is reached.

        Only applies to NEUTRAL trend entries (full IC candidates).
        One-sided entries are unaffected.

        Uses batch quote API for efficiency: 1 chain fetch + 1 batch quote
        regardless of how many candidate strikes are evaluated.

        Args:
            entry: HydraIronCondorEntry with strikes already calculated

        Returns:
            True if call strikes were tightened, False if no change needed
        """
        if entry.call_only or entry.put_only:
            return False

        spx = round(self.current_price / 5) * 5
        min_otm = self.min_call_otm_distance
        min_credit = self.min_viable_credit_per_side  # In cents (e.g., 200 for $2.00)
        spread_width = self._get_vix_adjusted_spread_width(self.current_vix, "call")

        initial_short_call = entry.short_call_strike
        initial_otm = initial_short_call - spx

        if initial_otm <= min_otm:
            logger.debug(f"MKT-020: Call already at OTM floor ({initial_otm}pt <= {min_otm}pt)")
            return False

        # Build candidate strike pairs: current OTM down to floor, in 5pt steps
        expiry = self._get_todays_expiry()
        if not expiry:
            return False

        candidates = []  # [(otm, short_strike, long_strike), ...]
        otm = initial_otm
        while otm >= min_otm:
            short_s = spx + otm
            long_s = short_s + spread_width
            candidates.append((otm, short_s, long_s))
            otm -= 5

        if not candidates:
            return False

        # Fetch option chain ONCE to get UICs for all candidate strikes
        try:
            chain_response = self.client.get_option_chain(
                option_root_id=self.option_root_uic,
                expiry_dates=[expiry]
            )
        except Exception as e:
            logger.warning(f"MKT-020: Option chain fetch failed: {e}")
            return False

        if not chain_response:
            return False

        option_space = chain_response.get("OptionSpace", [])
        if not option_space:
            return False

        # Build strike -> UIC mapping for calls from the chain
        call_uic_map = {}
        specific_options = option_space[0].get("SpecificOptions", [])
        for opt in specific_options:
            strike = opt.get("StrikePrice", 0)
            put_call = opt.get("PutCall", "")
            if put_call == "Call":
                call_uic_map[strike] = opt.get("Uic")

        # Collect UICs for all candidate strikes, snapping to nearest chain
        # strike when exact 5pt increments don't exist (Saxo uses 25pt spacing
        # far OTM — e.g., 6900, 6925, 6950 instead of every 5pt).
        candidate_uics = []  # [(otm, short_s, long_s, short_uic, long_uic), ...]
        all_uics = []
        seen_pairs = set()  # Avoid duplicate pairs after snapping
        for otm_val, short_s, long_s in candidates:
            actual_short, short_uic = self._snap_to_chain_strike(short_s, call_uic_map)
            if actual_short is not None:
                short_s = actual_short
                otm_val = int(short_s - spx)  # Recalculate actual OTM distance
                # Find long leg that preserves spread width closest to target
                actual_long, long_uic = self._snap_long_for_spread(
                    short_s, spread_width, call_uic_map, is_call=True
                )
            else:
                actual_long, long_uic = None, None
            if actual_long is not None:
                long_s = actual_long
            pair_key = (short_s, long_s)
            if pair_key in seen_pairs:
                continue  # Skip duplicate after snapping
            seen_pairs.add(pair_key)
            candidate_uics.append((otm_val, short_s, long_s, short_uic, long_uic))
            if short_uic:
                all_uics.append(short_uic)
            if long_uic:
                all_uics.append(long_uic)

        if not all_uics:
            logger.warning("MKT-020: No UICs found for candidate call strikes")
            return False

        # Batch fetch quotes for all candidates (1 API call)
        try:
            quotes = self.client.get_quotes_batch(all_uics, asset_type="StockIndexOption")
        except Exception as e:
            logger.warning(f"MKT-020: Batch quote fetch failed: {e}")
            return False

        # Phase 1: Compute credits for all candidates (quotes already batch-fetched)
        evaluated_candidates = []
        for otm_val, short_s, long_s, short_uic, long_uic in candidate_uics:
            if not short_uic or not long_uic:
                continue

            short_quote = quotes.get(short_uic, {})
            long_quote = quotes.get(long_uic, {}) if long_uic else {}

            sq = short_quote.get("Quote", {})
            lq = long_quote.get("Quote", {})
            short_bid = sq.get("Bid", 0) or 0
            short_ask = sq.get("Ask", 0) or 0
            long_bid = lq.get("Bid", 0) or 0
            long_ask = lq.get("Ask", 0) or 0

            if short_bid <= 0 or short_ask <= 0:
                continue  # Short illiquid, skip

            if long_bid <= 0 and long_ask <= 0:
                logger.debug(f"MKT-020: {otm_val}pt OTM → long call has no quote, skipping")
                continue  # Long illiquid, skip — don't treat as $0

            short_mid = (short_bid + short_ask) / 2
            long_mid = (long_bid + long_ask) / 2

            call_credit = (short_mid - long_mid) * 100

            # Enhanced logging: premium curve data for calibration
            logger.debug(
                f"MKT-020: {otm_val}pt OTM → credit ${call_credit:.2f} "
                f"(short ${short_mid:.2f}, long ${long_mid:.2f})"
            )
            evaluated_candidates.append((otm_val, short_s, long_s, call_credit))

        # Phase 2: MKT-029 graduated thresholds — try primary, then fallbacks
        # Lower thresholds let MKT-020 accept wider (further OTM) strikes with
        # slightly below-target credit instead of tightening to narrow strikes.
        # Wider = better cushion = safer. Fallbacks: min-$0.05, min-$0.10.
        call_thresholds = [min_credit, min_credit - 5, self.call_credit_floor]
        for threshold_idx, threshold in enumerate(call_thresholds):
            for otm_val, short_s, long_s, call_credit in evaluated_candidates:
                if call_credit >= threshold:
                    is_fallback = threshold_idx > 0
                    if otm_val < initial_otm:
                        # Tightened — update entry strikes
                        entry.short_call_strike = short_s
                        entry.long_call_strike = long_s
                        if is_fallback:
                            logger.info(
                                f"MKT-029: Call credit ${call_credit:.2f} accepted at "
                                f"fallback ${threshold:.2f} (primary: ${min_credit:.2f})"
                            )
                            self._log_safety_event(
                                "MKT-029_CALL_FALLBACK",
                                f"Entry #{entry.entry_number}: call credit ${call_credit:.2f} "
                                f"at fallback ${threshold:.2f} (primary ${min_credit:.2f})"
                            )
                        logger.info(
                            f"MKT-020: Call tightened {initial_otm}pt → {otm_val}pt OTM "
                            f"(credit: ${call_credit:.2f}, min: ${threshold:.2f})"
                        )
                        self._log_safety_event(
                            "MKT-020_CALL_TIGHTENED",
                            f"Entry #{entry.entry_number}: call OTM {initial_otm}→{otm_val}pt, "
                            f"credit ${call_credit:.2f}"
                        )

                        # Re-run all strike conflict checks on tightened strikes
                        self._adjust_for_strike_conflicts(entry)
                        self._adjust_for_same_strike_overlap(entry)
                        self._adjust_for_strike_conflicts(entry)  # Fix #66 re-run
                        self._adjust_for_long_strike_overlap(entry)

                        # MKT-044: Re-snap after overlap adjustments. The 5pt
                        # shifts can push strikes off the chain in far-OTM zones
                        # where Saxo uses 25pt intervals.
                        sc_snap, _ = self._snap_to_chain_strike(entry.short_call_strike, call_uic_map)
                        lc_snap, _ = self._snap_long_for_spread(
                            sc_snap or entry.short_call_strike, spread_width, call_uic_map, is_call=True
                        )
                        if sc_snap:
                            entry.short_call_strike = sc_snap
                        if lc_snap:
                            entry.long_call_strike = lc_snap
                        return True
                    else:
                        # Current OTM already viable — no tightening needed
                        if is_fallback:
                            logger.info(
                                f"MKT-029: Call credit ${call_credit:.2f} already viable at "
                                f"fallback ${threshold:.2f} (primary: ${min_credit:.2f})"
                            )
                        else:
                            logger.debug(
                                f"MKT-020: Call credit ${call_credit:.2f} already viable "
                                f"at {otm_val}pt OTM"
                            )
                        return False

        # All thresholds exhausted — couldn't find viable credit
        logger.info(
            f"MKT-020: Call credit non-viable even at ${call_thresholds[-1]:.2f} floor. "
            f"MKT-011 will handle (convert to put-only or skip)."
        )
        return False

    def _apply_progressive_put_tightening(self, entry: HydraIronCondorEntry) -> bool:
        """
        MKT-022: Progressive put OTM tightening for full IC entries.

        Mirror of MKT-020 (call tightening) for the put side.
        When initial put credit is below minimum, moves the short put closer
        to ATM in 5pt steps until credit >= minimum or OTM floor is reached.

        Uses put-specific minimum credit (min_viable_credit_put_side, default
        $2.75) — walk-forward backtest optimized.

        Uses batch quote API for efficiency: 1 chain fetch + 1 batch quote
        regardless of how many candidate strikes are evaluated.

        Args:
            entry: HydraIronCondorEntry with strikes already calculated

        Returns:
            True if put strikes were tightened, False if no change needed
        """
        if entry.call_only or entry.put_only:
            return False

        spx = round(self.current_price / 5) * 5
        min_otm = self.min_put_otm_distance
        min_credit = self.min_viable_credit_put_side  # Put-specific: $210 for $2.10
        spread_width = self._get_vix_adjusted_spread_width(self.current_vix, "put")

        initial_short_put = entry.short_put_strike
        initial_otm = spx - initial_short_put  # Put OTM = SPX - short_put

        if initial_otm <= min_otm:
            logger.debug(f"MKT-022: Put already at OTM floor ({initial_otm}pt <= {min_otm}pt)")
            return False

        # Build candidate strike pairs: current OTM down to floor, in 5pt steps
        expiry = self._get_todays_expiry()
        if not expiry:
            return False

        candidates = []  # [(otm, short_strike, long_strike), ...]
        otm = initial_otm
        while otm >= min_otm:
            short_s = spx - otm          # Put: BELOW SPX
            long_s = short_s - spread_width  # Put: FURTHER below
            candidates.append((otm, short_s, long_s))
            otm -= 5

        if not candidates:
            return False

        # Fetch option chain ONCE to get UICs for all candidate strikes
        try:
            chain_response = self.client.get_option_chain(
                option_root_id=self.option_root_uic,
                expiry_dates=[expiry]
            )
        except Exception as e:
            logger.warning(f"MKT-022: Option chain fetch failed: {e}")
            return False

        if not chain_response:
            return False

        option_space = chain_response.get("OptionSpace", [])
        if not option_space:
            return False

        # Build strike -> UIC mapping for puts from the chain
        put_uic_map = {}
        specific_options = option_space[0].get("SpecificOptions", [])
        for opt in specific_options:
            strike = opt.get("StrikePrice", 0)
            put_call = opt.get("PutCall", "")
            if put_call == "Put":
                put_uic_map[strike] = opt.get("Uic")

        # Collect UICs for all candidate strikes, snapping to nearest chain
        # strike when exact 5pt increments don't exist (Saxo uses 25pt spacing
        # far OTM — same issue as MKT-020 calls).
        candidate_uics = []  # [(otm, short_s, long_s, short_uic, long_uic), ...]
        all_uics = []
        seen_pairs = set()  # Avoid duplicate pairs after snapping
        for otm_val, short_s, long_s in candidates:
            actual_short, short_uic = self._snap_to_chain_strike(short_s, put_uic_map)
            if actual_short is not None:
                short_s = actual_short
                otm_val = int(spx - short_s)  # Recalculate actual OTM distance
                # Find long leg that preserves spread width closest to target
                actual_long, long_uic = self._snap_long_for_spread(
                    short_s, spread_width, put_uic_map, is_call=False
                )
            else:
                actual_long, long_uic = None, None
            if actual_long is not None:
                long_s = actual_long
            pair_key = (short_s, long_s)
            if pair_key in seen_pairs:
                continue  # Skip duplicate after snapping
            seen_pairs.add(pair_key)
            candidate_uics.append((otm_val, short_s, long_s, short_uic, long_uic))
            if short_uic:
                all_uics.append(short_uic)
            if long_uic:
                all_uics.append(long_uic)

        if not all_uics:
            logger.warning("MKT-022: No UICs found for candidate put strikes")
            return False

        # Batch fetch quotes for all candidates (1 API call)
        try:
            quotes = self.client.get_quotes_batch(all_uics, asset_type="StockIndexOption")
        except Exception as e:
            logger.warning(f"MKT-022: Batch quote fetch failed: {e}")
            return False

        # Phase 1: Compute credits for all candidates (quotes already batch-fetched)
        evaluated_candidates = []
        for otm_val, short_s, long_s, short_uic, long_uic in candidate_uics:
            if not short_uic or not long_uic:
                continue

            short_quote = quotes.get(short_uic, {})
            long_quote = quotes.get(long_uic, {}) if long_uic else {}

            sq = short_quote.get("Quote", {})
            lq = long_quote.get("Quote", {})
            short_bid = sq.get("Bid", 0) or 0
            short_ask = sq.get("Ask", 0) or 0
            long_bid = lq.get("Bid", 0) or 0
            long_ask = lq.get("Ask", 0) or 0

            if short_bid <= 0 or short_ask <= 0:
                continue  # Short illiquid, skip

            if long_bid <= 0 and long_ask <= 0:
                logger.debug(f"MKT-022: {otm_val}pt OTM → long put has no quote, skipping")
                continue  # Long illiquid, skip — don't treat as $0

            short_mid = (short_bid + short_ask) / 2
            long_mid = (long_bid + long_ask) / 2

            put_credit = (short_mid - long_mid) * 100

            # Enhanced logging: premium curve data for calibration
            logger.debug(
                f"MKT-022: {otm_val}pt OTM → credit ${put_credit:.2f} "
                f"(short ${short_mid:.2f}, long ${long_mid:.2f})"
            )
            evaluated_candidates.append((otm_val, short_s, long_s, put_credit))

        # Phase 2: MKT-029 graduated thresholds — try primary, then fallbacks
        # Lower thresholds let MKT-022 accept wider (further OTM) strikes with
        # slightly below-target credit instead of tightening to narrow strikes.
        # Wider = better cushion = safer. Fallbacks: min-$0.05, min-$0.10.
        put_thresholds = [min_credit, min_credit - 5, self.put_credit_floor]
        for threshold_idx, threshold in enumerate(put_thresholds):
            for otm_val, short_s, long_s, put_credit in evaluated_candidates:
                if put_credit >= threshold:
                    is_fallback = threshold_idx > 0
                    if otm_val < initial_otm:
                        # Tightened — update entry strikes
                        entry.short_put_strike = short_s
                        entry.long_put_strike = long_s
                        if is_fallback:
                            logger.info(
                                f"MKT-029: Put credit ${put_credit:.2f} accepted at "
                                f"fallback ${threshold:.2f} (primary: ${min_credit:.2f})"
                            )
                            self._log_safety_event(
                                "MKT-029_PUT_FALLBACK",
                                f"Entry #{entry.entry_number}: put credit ${put_credit:.2f} "
                                f"at fallback ${threshold:.2f} (primary ${min_credit:.2f})"
                            )
                        logger.info(
                            f"MKT-022: Put tightened {initial_otm}pt → {otm_val}pt OTM "
                            f"(credit: ${put_credit:.2f}, min: ${threshold:.2f})"
                        )
                        self._log_safety_event(
                            "MKT-022_PUT_TIGHTENED",
                            f"Entry #{entry.entry_number}: put OTM {initial_otm}→{otm_val}pt, "
                            f"credit ${put_credit:.2f}"
                        )

                        # Re-run all strike conflict checks on tightened strikes
                        self._adjust_for_strike_conflicts(entry)
                        self._adjust_for_same_strike_overlap(entry)
                        self._adjust_for_strike_conflicts(entry)  # Fix #66 re-run
                        self._adjust_for_long_strike_overlap(entry)

                        # MKT-044: Re-snap after overlap adjustments (same as MKT-020)
                        sp_snap, _ = self._snap_to_chain_strike(entry.short_put_strike, put_uic_map)
                        lp_snap, _ = self._snap_long_for_spread(
                            sp_snap or entry.short_put_strike, spread_width, put_uic_map, is_call=False
                        )
                        if sp_snap:
                            entry.short_put_strike = sp_snap
                        if lp_snap:
                            entry.long_put_strike = lp_snap
                        return True
                    else:
                        # Current OTM already viable — no tightening needed
                        if is_fallback:
                            logger.info(
                                f"MKT-029: Put credit ${put_credit:.2f} already viable at "
                                f"fallback ${threshold:.2f} (primary: ${min_credit:.2f})"
                            )
                        else:
                            logger.debug(
                                f"MKT-022: Put credit ${put_credit:.2f} already viable "
                                f"at {otm_val}pt OTM"
                            )
                        return False

        # All thresholds exhausted — couldn't find viable credit
        logger.info(
            f"MKT-022: Put credit non-viable even at ${put_thresholds[-1]:.2f} floor. "
            f"MKT-011 will handle (convert to call-only or skip)."
        )
        return False

    # =========================================================================
    # =========================================================================
    # Skip tracking + alerting helper
    # =========================================================================

    def _record_skipped_entry(self, entry_num: int, skip_reason: str,
                              alert_details: str = "", send_alert: bool = True):
        """
        Record a skipped entry in daily_state.entries and optionally send Telegram alert.

        Creates a minimal HydraIronCondorEntry with both sides marked as skipped,
        appends it to daily_state.entries so the dashboard can display it, and
        sends a LOW-priority Telegram alert with the skip reason.

        Args:
            entry_num: The entry number (1-7)
            skip_reason: Human-readable skip reason (e.g. "MKT-011: both sides below minimum credit")
            alert_details: Additional context for the Telegram message (estimated credits, VIX, etc.)
            send_alert: Whether to send a Telegram alert (False when caller sends its own alert)
        """
        now = get_us_market_time()
        skipped = HydraIronCondorEntry(entry_number=entry_num)
        skipped.is_complete = True
        skipped.call_side_skipped = True
        skipped.put_side_skipped = True
        skipped.skip_reason = skip_reason
        skipped.entry_time = now
        self.daily_state.entries.append(skipped)

        # Record skip to SQLite (before alert guard — must run even when send_alert=False)
        if self._data_recorder:
            try:
                self._data_recorder.record_skipped_entry({
                    "date": now.strftime('%Y-%m-%d'),
                    "entry_number": entry_num,
                    "skip_time": now.strftime('%Y-%m-%d %H:%M:%S'),
                    "skip_reason": skip_reason,
                    "spx_at_skip": self.current_price,
                    "vix_at_skip": self.current_vix,
                })
            except Exception:
                pass

        if not send_alert:
            return

        # Telegram alert
        time_str = now.strftime('%H:%M ET')
        alert_msg = f"Entry #{entry_num} skipped at {time_str}\nReason: {skip_reason}"
        if alert_details:
            alert_msg += f"\n{alert_details}"

        try:
            self.alert_service.send_alert(
                alert_type=AlertType.ENTRY_SKIPPED,
                title=f"Entry #{entry_num} Skipped",
                message=alert_msg,
                details={"entry_number": entry_num, "reason": skip_reason}
            )
        except Exception as e:
            logger.warning(f"Failed to send skip alert for Entry #{entry_num}: {e}")

    # ========================================================================
    # DataRecorder: Real-time SQLite writes (non-critical, fire-and-forget)
    # ========================================================================

    def _record_heartbeat_to_db(self):
        """Write current heartbeat data to SQLite (called every ~10s)."""
        if not self._data_recorder:
            return
        try:
            now = get_us_market_time()
            timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

            # 1. Market tick
            self._data_recorder.record_tick(
                timestamp=timestamp,
                spx_price=self.current_price,
                vix_level=self.current_vix,
                trend_signal=self._current_trend.value if self._current_trend else "unknown",
                bot_state=self.state.value if hasattr(self.state, 'value') else str(self.state),
                entry_count=self.daily_state.entries_completed,
                active_count=len(self.daily_state.active_entries),
            )

            # 2. Spread snapshots with individual leg prices
            snapshots = []
            for entry in self.daily_state.active_entries:
                call_done = (entry.call_side_stopped
                             or getattr(entry, 'call_side_expired', False)
                             or getattr(entry, 'call_side_skipped', False))
                put_done = (entry.put_side_stopped
                            or getattr(entry, 'put_side_expired', False)
                            or getattr(entry, 'put_side_skipped', False))
                if call_done and put_done:
                    continue

                csv = entry.call_spread_value if not call_done else 0
                psv = entry.put_spread_value if not put_done else 0
                if csv > 0 or psv > 0:
                    snapshots.append({
                        "entry_number": entry.entry_number,
                        "call_spread_value": csv if csv > 0 else None,
                        "put_spread_value": psv if psv > 0 else None,
                        "short_call_price": entry.short_call_price if not call_done else None,
                        "long_call_price": entry.long_call_price if not call_done else None,
                        "short_put_price": entry.short_put_price if not put_done else None,
                        "long_put_price": entry.long_put_price if not put_done else None,
                    })

            if snapshots:
                self._data_recorder.record_spread_snapshots(
                    timestamp=timestamp, snapshots=snapshots
                )
        except Exception as e:
            logger.debug(f"DataRecorder heartbeat failed: {e}")

    def _record_entry_to_db(self, entry):
        """Record entry data to SQLite with execution quality metrics.

        Greeks are fetched in a daemon thread to avoid blocking the main loop.
        """
        if not self._data_recorder:
            return
        try:
            now = get_us_market_time()
            date_str = now.strftime('%Y-%m-%d')

            # Fetch current quotes for bid-ask width (single batch API call, fast)
            call_ba_width = None
            put_ba_width = None
            call_slippage = None
            put_slippage = None
            try:
                quote_uics = [u for u in [entry.short_call_uic, entry.short_put_uic] if u]
                if quote_uics:
                    quotes = self.client.get_quotes_batch(quote_uics, asset_type="StockIndexOption")
                    for side_uic, side_name in [(entry.short_call_uic, "call"), (entry.short_put_uic, "put")]:
                        if side_uic and side_uic in quotes:
                            q = quotes[side_uic].get("Quote", {})
                            bid = q.get("Bid", 0) or 0
                            ask = q.get("Ask", 0) or 0
                            if bid > 0 and ask > 0:
                                width = round(ask - bid, 4)
                                if side_name == "call":
                                    call_ba_width = width
                                else:
                                    put_ba_width = width
            except Exception:
                pass

            # Slippage: fill price vs current mid (approximate)
            if entry.short_call_fill_price and entry.short_call_price:
                call_slippage = round(entry.short_call_fill_price - entry.short_call_price, 4)
            if entry.short_put_fill_price and entry.short_put_price:
                put_slippage = round(entry.short_put_fill_price - entry.short_put_price, 4)

            entry_data = {
                "date": date_str,
                "entry_number": entry.entry_number,
                "entry_time": entry.entry_time.strftime('%Y-%m-%d %H:%M:%S') if entry.entry_time else None,
                "spx_at_entry": self.current_price,
                "vix_at_entry": self.current_vix,
                "expected_move": getattr(self, '_last_expected_move', None),
                "trend_signal": entry.trend_signal.value if entry.trend_signal else None,
                "entry_type": "call_only" if entry.call_only else ("put_only" if entry.put_only else "full_ic"),
                "override_reason": getattr(entry, 'override_reason', None),
                "short_call_strike": entry.short_call_strike,
                "long_call_strike": entry.long_call_strike,
                "short_put_strike": entry.short_put_strike,
                "long_put_strike": entry.long_put_strike,
                "call_credit": entry.call_spread_credit,
                "put_credit": entry.put_spread_credit,
                "total_credit": entry.total_credit,
                "call_spread_width": abs(entry.long_call_strike - entry.short_call_strike) if entry.long_call_strike else 0,
                "put_spread_width": abs(entry.short_put_strike - entry.long_put_strike) if entry.long_put_strike else 0,
                "otm_distance_call": entry.short_call_strike - self.current_price if entry.short_call_strike else None,
                "otm_distance_put": self.current_price - entry.short_put_strike if entry.short_put_strike else None,
                # Execution quality
                "bid_ask_width_call": call_ba_width,
                "bid_ask_width_put": put_ba_width,
                "time_to_fill_ms": getattr(entry, '_fill_time_ms', None),
                "slippage_call": call_slippage,
                "slippage_put": put_slippage,
                "attempts": getattr(entry, '_fill_attempts', 1),
                # Margin snapshot
                "margin_available": self._last_margin_snapshot.get("available"),
                "margin_utilization_pct": self._last_margin_snapshot.get("utilization_pct"),
                "config_version": HYDRA_VERSION,
            }

            # Write entry data immediately (without Greeks)
            self._data_recorder.record_entry(entry_data)

            # Fetch Greeks in daemon thread (avoid blocking main loop)
            # Greeks are purely for analytics — entry is already placed
            import threading
            def _fetch_and_update_greeks():
                try:
                    greeks_data = {}
                    for side, uic in [("call", entry.short_call_uic), ("put", entry.short_put_uic)]:
                        if uic:
                            g = self.client.get_option_greeks(uic, asset_type="StockIndexOption")
                            if g:
                                greeks_data[side] = g
                    if greeks_data:
                        # Update the DB row with Greeks
                        with self._data_recorder._connect() as conn:
                            for side in ("call", "put"):
                                g = greeks_data.get(side, {})
                                if g:
                                    conn.execute(
                                        f"""UPDATE trade_entries SET
                                        delta_{side} = ?, theta_{side} = ?, vega_{side} = ?
                                        WHERE date = ? AND entry_number = ?""",
                                        (g.get("Delta"), g.get("Theta"), g.get("Vega"),
                                         date_str, entry.entry_number)
                                    )
                            conn.commit()
                except Exception as e:
                    logger.debug(f"Greeks fetch failed (non-critical): {e}")

            thread = threading.Thread(target=_fetch_and_update_greeks, daemon=True)
            thread.start()

        except Exception as e:
            logger.debug(f"DataRecorder entry write failed: {e}")

    def _record_stop_to_db(self, entry, side: str, stop_level: float,
                           actual_close_cost: float):
        """Record stop loss data to SQLite with execution quality metrics."""
        if not self._data_recorder:
            return
        try:
            now = get_us_market_time()
            date_str = now.strftime('%Y-%m-%d')

            # Minutes held since entry
            minutes_held = None
            if entry.entry_time:
                minutes_held = (now - entry.entry_time).total_seconds() / 60

            # SPX move since entry
            spx_move = None
            spx_at_entry = getattr(entry, '_spx_at_entry', None)
            if spx_at_entry:
                spx_move = self.current_price - spx_at_entry

            # Cascade gap (seconds since previous stop)
            cascade_gap = None
            if self._last_stop_time:
                cascade_gap = (now - self._last_stop_time).total_seconds()
            self._last_stop_time = now

            # Quoted mid at stop (from current prices on entry)
            if side == "call":
                quoted_mid = entry.call_spread_value if entry.call_spread_value else None
                credit = entry.call_spread_credit
            else:
                quoted_mid = entry.put_spread_value if entry.put_spread_value else None
                credit = entry.put_spread_credit

            slippage = None
            if actual_close_cost and quoted_mid:
                slippage = actual_close_cost - quoted_mid

            self._data_recorder.record_stop({
                "date": date_str,
                "entry_number": entry.entry_number,
                "side": side,
                "stop_time": now.strftime('%H:%M:%S'),
                "spx_at_stop": self.current_price,
                "trigger_level": stop_level,
                "actual_debit": actual_close_cost,
                "net_pnl": -(actual_close_cost - credit) if actual_close_cost and credit else None,
                "quoted_mid_at_stop": quoted_mid,
                "slippage_on_close": slippage,
                "spx_move_since_entry": spx_move,
                "minutes_held": minutes_held,
                "cascade_gap_seconds": cascade_gap,
            })
        except Exception as e:
            logger.debug(f"DataRecorder stop write failed: {e}")

    def _record_daily_summary_to_db(self):
        """Record daily summary to SQLite with economic events and overnight gap."""
        if not self._data_recorder:
            return
        try:
            import json as _json
            from shared.event_calendar import get_economic_events_for_date, is_opex_week

            now = get_us_market_time()
            date_str = now.strftime('%Y-%m-%d')
            summary = self.get_daily_summary()

            events = get_economic_events_for_date(now.date())
            overnight_gap = self._data_recorder.get_yesterday_spx_close(date_str)
            if overnight_gap is not None and self.market_data.spx_open:
                overnight_gap = self.market_data.spx_open - overnight_gap
            else:
                overnight_gap = None

            spx_low = self.market_data.spx_low
            if spx_low == float('inf'):
                spx_low = None
            day_range = None
            if spx_low is not None:
                day_range = self.market_data.spx_high - spx_low

            self._data_recorder.record_daily_summary({
                "date": date_str,
                "spx_open": self.market_data.spx_open,
                "spx_close": self.current_price,
                "spx_high": self.market_data.spx_high,
                "spx_low": spx_low,
                "day_range": day_range,
                "vix_open": self.market_data.vix_open,
                "vix_close": self.current_vix,
                "entries_placed": summary.get("entries_completed", 0),
                "entries_stopped": summary.get("call_stops", 0) + summary.get("put_stops", 0),
                "entries_expired": max(0, summary.get("entries_completed", 0) - (summary.get("call_stops", 0) + summary.get("put_stops", 0))),
                "gross_pnl": summary.get("total_pnl", 0),
                "net_pnl": summary.get("total_pnl", 0) - summary.get("total_commission", 0),
                "commission": summary.get("total_commission", 0),
                "long_salvage_revenue": summary.get("long_salvage_revenue", 0.0),
                "day_of_week": now.strftime('%A'),
                "overnight_gap": overnight_gap,
                "economic_events": _json.dumps(events) if events else None,
                "config_version": HYDRA_VERSION,
                "opex_week": 1 if is_opex_week(now.date()) else 0,
            })

            # Compute MAE/MFE from spread_snapshots
            self._data_recorder.compute_mae_mfe(date_str)

            # WAL checkpoint (prevent unbounded WAL growth)
            self._data_recorder.wal_checkpoint()

        except Exception as e:
            logger.debug(f"DataRecorder daily summary failed: {e}")

    def _get_spx_price_minutes_ago(self, minutes: int) -> float:
        """Get SPX price from approximately N minutes ago using heartbeat price history."""
        target_time = get_us_market_time() - timedelta(minutes=minutes)
        best_price = 0.0
        best_diff = float('inf')
        for ts, price in self.market_data.price_history:
            diff = abs((ts - target_time).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_price = price
        return best_price if best_diff < minutes * 60 * 2 else 0.0  # reject if too far off

    # OVERRIDE: Entry initiation with trend detection
    # =========================================================================

    def _initiate_entry(self) -> str:
        """
        Initiate an entry with trend-based decision making.

        Overrides MEICStrategy._initiate_entry() to:
        1. Check trend signal before entry
        2. Place one-sided spread if trending
        3. Place full IC if neutral
        4. Record skipped entries with reason + send Telegram alert (v1.16.0)

        Returns:
            str: Description of action taken
        """
        entry_num = self._next_entry_index + 1
        logger.info(f"HYDRA: Initiating Entry #{entry_num} of {len(self.entry_times)}")

        # MKT-043: Calm entry filter — delay if SPX moving fast.
        # CRITICAL: refresh prices and check stops during delay to avoid
        # blocking stop monitoring (audit Bug #3).
        if (self.calm_entry_lookback_min is not None
                and self.calm_entry_threshold_pts is not None
                and self.calm_entry_max_delay_min is not None):
            import time as _time
            max_delay_sec = self.calm_entry_max_delay_min * 60
            waited = 0
            _first_log = True
            while waited <= max_delay_sec:
                # Refresh prices so current_price and price_history stay fresh
                self._update_market_data()
                spx_now = self.current_price
                past_price = self._get_spx_price_minutes_ago(self.calm_entry_lookback_min)
                if spx_now > 0 and past_price > 0:
                    move = abs(spx_now - past_price)
                    if move <= self.calm_entry_threshold_pts:
                        if waited > 0:
                            logger.info(
                                f"MKT-043 E#{entry_num}: Market calmed after {waited}s delay "
                                f"(move {move:.1f}pt <= {self.calm_entry_threshold_pts}pt threshold)"
                            )
                        break  # calm enough, proceed
                    else:
                        if _first_log:
                            logger.info(
                                f"MKT-043 E#{entry_num}: SPX moving fast ({move:.1f}pt in {self.calm_entry_lookback_min}min "
                                f"> {self.calm_entry_threshold_pts}pt), waiting for calm..."
                            )
                            _first_log = False
                else:
                    break  # no price data, proceed anyway
                # Check stops for ALL active entries during the wait (Bug #3 fix)
                stop_result = self._check_stop_losses()
                if stop_result:
                    logger.warning(f"MKT-043 E#{entry_num}: Stop triggered during calm wait: {stop_result}")
                # Check if entry window is still open
                if not self._is_entry_time():
                    logger.info(f"MKT-043 E#{entry_num}: Entry window expired during calm wait")
                    break
                _time.sleep(10)
                waited += 10
            else:
                logger.info(
                    f"MKT-043 E#{entry_num}: Max delay {self.calm_entry_max_delay_min}min reached, entering anyway"
                )

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
            self._record_skipped_entry(entry_num, f"Insufficient margin: {bp_message}", send_alert=False)
            # Keep existing HIGH alert for margin (more urgent than generic skip)
            self.alert_service.send_alert(
                alert_type=AlertType.MAX_LOSS,
                title=f"Entry #{entry_num} Skipped - Insufficient Margin",
                message=bp_message,
                priority=AlertPriority.HIGH,
                details={"entry_number": entry_num, "reason": "margin"}
            )
            return f"Entry #{entry_num} skipped - {bp_message}"

        # Anti-whipsaw filter: skip if SPX range > mult × expected daily move
        whipsaw_reason = self._check_whipsaw_filter()
        if whipsaw_reason:
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            self._record_skipped_entry(entry_num, whipsaw_reason, send_alert=True)
            self._log_safety_event("WHIPSAW_SKIP", f"Entry #{entry_num}: {whipsaw_reason}")
            return f"Entry #{entry_num} skipped - {whipsaw_reason}"

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

                # MKT-035 / Upday-035: Check conditional entries BEFORE any strike/API work
                # Conditional entries (6+) fire as call-only on down days or put-only on up days
                is_conditional = self._is_conditional_entry(entry_num)
                _downday_triggered = False
                _upday_triggered = False
                if is_conditional and not self.dry_run:
                    if self._is_downday_conditional_entry(entry_num):
                        _downday_triggered = self._check_downday_filter()
                    if self._is_upday_conditional_entry(entry_num) and not _downday_triggered:
                        _upday_triggered = self._check_upday_filter()
                    if not _downday_triggered and not _upday_triggered:
                        direction = "down" if self._is_downday_conditional_entry(entry_num) else "up"
                        logger.info(
                            f"Conditional Entry #{entry_num} — "
                            f"SPX did not meet {direction}-day threshold, skipping"
                        )
                        self.daily_state.entries_skipped += 1
                        self.daily_state.credit_gate_skips += 1
                        self._entry_in_progress = False
                        self._current_entry = None
                        self.state = MEICState.MONITORING
                        self._next_entry_index += 1
                        self._record_skipped_entry(
                            entry_num, f"Conditional: no {direction}-day trigger"
                        )
                        return f"Entry #{entry_num} skipped - conditional (no trigger)"

                # Create extended entry object
                entry = HydraIronCondorEntry(entry_number=entry_num)
                entry.strategy_id = f"hydra_{get_us_market_time().strftime('%Y%m%d')}_entry{entry_num}"
                entry.trend_signal = trend
                # Fix #52: Set contract count for multi-contract support
                entry.contracts = self.contracts_per_entry
                self._current_entry = entry

                # Calculate strikes
                if not self._calculate_strikes(entry):
                    last_error = "Failed to calculate strikes"
                    continue

                # Determine if this is a conditional entry (MKT-035 E6/E7) before tightening
                # so we can skip put tightening (saves API calls + main loop time)
                credit_gate_handled = False
                place_put_only = False  # v1.7.1: MKT-011 put-only conversion
                place_call_only = False  # MKT-035: call-only on down days
                original_trend = trend  # Save original trend for hybrid logic

                # Check MKT-038 (FOMC T+1) once here so we can skip put tightening
                is_fomc_t1 = (
                    self.fomc_t1_callonly_enabled and not self.dry_run
                    and is_fomc_t_plus_one()
                )

                # Base-entry down-day call-only: evaluate early so we can skip put tightening.
                # Only applies to base entries (not conditional slots), and not when FOMC T+1 already
                # forces call-only (avoids double-logging).
                _base_downday_triggered = False
                if (not is_conditional and not is_fomc_t1
                        and self.base_entry_downday_callonly_pct is not None
                        and not self.dry_run):
                    spx_ref = self.market_data.spx_open
                    if spx_ref and spx_ref > 0 and self.current_price > 0:
                        if (self.current_price - spx_ref) / spx_ref <= -self.base_entry_downday_callonly_pct:
                            _base_downday_triggered = True

                # MKT-020/MKT-022: Progressive OTM tightening
                if not self.dry_run:
                    if not _upday_triggered:
                        # Skip call tightening for upday put-only entries (call side not placed)
                        self._apply_progressive_call_tightening(entry)
                    if not is_conditional and not is_fomc_t1 and not _base_downday_triggered:
                        # Skip put tightening for downday call-only / FOMC T+1 / base-downday entries
                        self._apply_progressive_put_tightening(entry)
                    elif _upday_triggered:
                        # Upday put-only: DO apply put tightening (we're placing the put side)
                        self._apply_progressive_put_tightening(entry)

                if not self.dry_run:
                    if is_conditional and _downday_triggered:
                        # Conditional entry: down day confirmed → force call-only (MKT-035)
                        entry.call_only = True
                        entry.put_only = False
                        entry.put_side_skipped = True
                        entry.override_reason = "mkt-035"
                        place_call_only = True
                        credit_gate_handled = True
                        logger.info(
                            f"MKT-035: Conditional Entry #{entry_num} — down day confirmed, "
                            f"placing CALL spread only"
                        )

                        # Still check call credit viability (with MKT-029 configurable floor)
                        # NOTE: do NOT zero put strikes yet — _estimate_entry_credit needs real
                        # strike values to look up UICs (zeroing causes estimation to fail → skip)
                        _, _, est_call, _ = self._check_credit_gate(entry)
                        call_floor = self.call_credit_floor  # MKT-029 configurable floor ($0.75)
                        if est_call < call_floor:
                            logger.info(
                                f"MKT-035: Entry #{entry_num} call credit "
                                f"${est_call / 100:.2f} below floor — skipping"
                            )
                            self.daily_state.entries_skipped += 1
                            self.daily_state.credit_gate_skips += 1
                            self._entry_in_progress = False
                            self._current_entry = None
                            self.state = MEICState.MONITORING
                            self._next_entry_index += 1
                            self._record_skipped_entry(
                                entry_num,
                                f"MKT-035: call credit non-viable (${est_call / 100:.2f} < ${call_floor / 100:.2f})",
                                f"• Call est: ${est_call / 100:.2f} (floor ${call_floor / 100:.2f}, primary ${self.min_viable_credit_per_side / 100:.2f})"
                            )
                            return f"Entry #{entry_num} skipped - call credit non-viable (MKT-035)"

                    elif is_conditional and _upday_triggered:
                        # Up day confirmed → force put-only (Upday-035)
                        entry.put_only = True
                        entry.call_only = False
                        entry.call_side_skipped = True
                        entry.override_reason = "upday-035"
                        place_put_only = True
                        credit_gate_handled = True
                        logger.info(
                            f"Upday-035: Conditional Entry #{entry_num} — up day confirmed, "
                            f"placing PUT spread only"
                        )

                        # Check put credit viability (MKT-029 configurable floor)
                        _, _, _, est_put = self._check_credit_gate(entry)
                        put_floor = self.put_credit_floor  # MKT-029 configurable floor ($2.07)
                        if est_put < put_floor:
                            logger.info(
                                f"Upday-035: Entry #{entry_num} put credit "
                                f"${est_put / 100:.2f} below floor — skipping"
                            )
                            self.daily_state.entries_skipped += 1
                            self.daily_state.credit_gate_skips += 1
                            self._entry_in_progress = False
                            self._current_entry = None
                            self.state = MEICState.MONITORING
                            self._next_entry_index += 1
                            self._record_skipped_entry(
                                entry_num,
                                f"Upday-035: put credit non-viable (${est_put / 100:.2f} < ${put_floor / 100:.2f})",
                                f"• Put est: ${est_put / 100:.2f} (floor ${put_floor / 100:.2f}, primary ${self.min_viable_credit_put_side / 100:.2f})"
                            )
                            return f"Entry #{entry_num} skipped - put credit non-viable (Upday-035)"

                    else:
                        # Base entries: apply down-day call-only filter if triggered
                        if _base_downday_triggered:
                            spx_ref = self.market_data.spx_open  # already set above; re-read for log
                            move_pct = (self.current_price - spx_ref) / spx_ref * 100
                            entry.call_only = True
                            entry.put_only = False
                            entry.put_side_skipped = True
                            entry.override_reason = "base-downday"
                            place_call_only = True
                            credit_gate_handled = True
                            logger.info(
                                f"Base-Downday: Entry #{entry_num} — SPX {move_pct:+.2f}% vs open "
                                f"(threshold: -{self.base_entry_downday_callonly_pct * 100:.1f}%), "
                                f"placing CALL spread only"
                            )

                            # Check call credit viability (MKT-029 configurable floor)
                            _, _, est_call, _ = self._check_credit_gate(entry)
                            call_floor = self.call_credit_floor  # MKT-029 configurable floor ($0.75)
                            if est_call < call_floor:
                                logger.info(
                                    f"Base-Downday: Entry #{entry_num} call credit "
                                    f"${est_call / 100:.2f} below floor — skipping"
                                )
                                self.daily_state.entries_skipped += 1
                                self.daily_state.credit_gate_skips += 1
                                self._entry_in_progress = False
                                self._current_entry = None
                                self.state = MEICState.MONITORING
                                self._next_entry_index += 1
                                self._record_skipped_entry(
                                    entry_num,
                                    f"Base-Downday: call credit non-viable (${est_call / 100:.2f} < ${call_floor / 100:.2f})",
                                    f"• Call est: ${est_call / 100:.2f} (floor ${call_floor / 100:.2f}, primary ${self.min_viable_credit_per_side / 100:.2f})"
                                )
                                return f"Entry #{entry_num} skipped - call credit non-viable (Base-Downday)"

                # MKT-038: Force call-only on T+1 after FOMC announcement
                if not credit_gate_handled and is_fomc_t1:
                    entry.call_only = True
                    entry.put_only = False
                    entry.put_side_skipped = True
                    entry.override_reason = "mkt-038"
                    place_call_only = True
                    credit_gate_handled = True
                    logger.info(
                        f"MKT-038: Entry #{entry_num} — FOMC T+1 (announcement yesterday), "
                        f"placing CALL spread only"
                    )

                    # Check call credit viability (MKT-029 configurable floor)
                    _, _, est_call, _ = self._check_credit_gate(entry)
                    call_floor = self.call_credit_floor  # MKT-029 configurable floor ($0.75)
                    if est_call < call_floor:
                        logger.info(
                            f"MKT-038: Entry #{entry_num} call credit "
                            f"${est_call / 100:.2f} below floor — skipping"
                        )
                        self.daily_state.entries_skipped += 1
                        self.daily_state.credit_gate_skips += 1
                        self._entry_in_progress = False
                        self._current_entry = None
                        self.state = MEICState.MONITORING
                        self._next_entry_index += 1
                        self._record_skipped_entry(
                            entry_num,
                            f"MKT-038: call credit non-viable on FOMC T+1 (${est_call / 100:.2f} < ${call_floor / 100:.2f})",
                            f"• Call est: ${est_call / 100:.2f} (floor ${call_floor / 100:.2f}, primary ${self.min_viable_credit_per_side / 100:.2f})"
                        )
                        return f"Entry #{entry_num} skipped - call credit non-viable (MKT-038)"

                # MKT-011: Check minimum credit gate (only if MKT-035 didn't already handle)
                if not credit_gate_handled and not self.dry_run:
                    gate_result, estimation_worked, est_call, est_put = self._check_credit_gate(entry)

                    if gate_result == "skip":
                        # Skip: both non-viable, or MKT-032 VIX too high for put-only
                        # Fix #79: Increment skip counters (was missing - all other skip paths have this)
                        self.daily_state.entries_skipped += 1
                        self.daily_state.credit_gate_skips += 1
                        self._entry_in_progress = False
                        self._current_entry = None
                        self.state = MEICState.MONITORING
                        self._next_entry_index += 1
                        # Determine specific skip reason for dashboard/alert
                        if estimation_worked and est_call < self.min_viable_credit_per_side and est_put < self.min_viable_credit_put_side:
                            skip_reason = f"MKT-011: both sides below minimum credit (call ${est_call / 100:.2f}, put ${est_put / 100:.2f})"
                            skip_details = (
                                f"• Call est: ${est_call / 100:.2f} (min ${self.min_viable_credit_per_side / 100:.2f})\n"
                                f"• Put est: ${est_put / 100:.2f} (min ${self.min_viable_credit_put_side / 100:.2f})"
                            )
                        elif self.current_vix and self.current_vix >= self.put_only_max_vix:
                            skip_reason = f"MKT-032: VIX {self.current_vix:.1f} too high for put-only (max {self.put_only_max_vix:.1f})"
                            skip_details = f"• VIX: {self.current_vix:.1f} (max {self.put_only_max_vix:.1f} for put-only)"
                        else:
                            skip_reason = f"MKT-011: credit gate skip (call ${est_call / 100:.2f}, put ${est_put / 100:.2f})"
                            skip_details = (
                                f"• Call est: ${est_call / 100:.2f} (min ${self.min_viable_credit_per_side / 100:.2f})\n"
                                f"• Put est: ${est_put / 100:.2f} (min ${self.min_viable_credit_put_side / 100:.2f})"
                            )
                        self._record_skipped_entry(entry_num, skip_reason, skip_details)
                        return f"Entry #{entry_num} skipped - credit gate (MKT-011/MKT-032)"
                    elif gate_result == "call_only":
                        # MKT-011 retry: Before converting to call-only, try tightening
                        # the put 5pt closer to ATM and re-checking credit. MKT-022 may
                        # have found a borderline strike that moved in the 2s between scans.
                        # Capped at 2 iterations (10pt) to avoid blocking the main loop
                        # (each iteration fetches 4 quotes via REST API).
                        put_retry_succeeded = False
                        current_put_otm = abs(self.current_price - entry.short_put_strike)
                        min_put_floor = self.min_put_otm_distance  # 25pt floor
                        max_retries = 2
                        api_failures = 0

                        for retry_i in range(max_retries):
                            if current_put_otm <= min_put_floor:
                                break  # at floor, can't tighten more

                            # Tighten 5pt closer to ATM
                            entry.short_put_strike += 5
                            entry.long_put_strike += 5
                            current_put_otm = abs(self.current_price - entry.short_put_strike)

                            # Re-estimate credit at new strikes
                            _, est_put_retry = self._estimate_entry_credit(entry)
                            if est_put_retry <= 0:
                                api_failures += 1
                                if api_failures >= 2:
                                    break  # persistent API failure
                                continue  # transient failure, try next 5pt

                            logger.info(
                                f"MKT-011 retry {retry_i + 1}/{max_retries}: Put tightened to "
                                f"{current_put_otm:.0f}pt OTM (SP {entry.short_put_strike:.0f}), "
                                f"credit ${est_put_retry / 100:.2f}"
                            )

                            # Check with MKT-029 fallbacks
                            put_min = self.min_viable_credit_put_side
                            if est_put_retry >= put_min or est_put_retry >= (put_min - 10):
                                put_retry_succeeded = True
                                est_put = est_put_retry
                                logger.info(
                                    f"MKT-011 retry: Put now viable at ${est_put / 100:.2f} "
                                    f"after tightening to {current_put_otm:.0f}pt OTM → proceeding as full IC"
                                )
                                self._log_safety_event(
                                    "MKT-011_RETRY_SUCCESS",
                                    f"Entry #{entry_num}: put viable after retry at {current_put_otm:.0f}pt OTM, "
                                    f"credit ${est_put / 100:.2f}"
                                )
                                break

                        if put_retry_succeeded:
                            # Re-run strike conflict checks after changing put strikes
                            # (same bug class as Fix #44, #50, #66, #67 — new strikes
                            # could collide with existing entries)
                            self._adjust_for_strike_conflicts(entry)
                            self._adjust_for_same_strike_overlap(entry)
                            self._adjust_for_strike_conflicts(entry)
                            self._adjust_for_long_strike_overlap(entry)
                            # Full IC — gate passed after tightening
                            credit_gate_handled = True
                        else:
                            # MKT-040: Put still non-viable after retry → convert to call-only
                            logger.info(
                                f"MKT-040: Entry #{entry_num} put credit non-viable after retry "
                                f"(${est_put / 100:.2f}) → converting to call-only "
                                f"(call ${est_call / 100:.2f})"
                            )
                            entry.call_only = True
                            entry.put_only = False
                            entry.put_side_skipped = True
                            entry.override_reason = "mkt-040"
                            place_call_only = True
                            credit_gate_handled = True
                    elif gate_result == "put_only":
                        # v1.7.1: Call credit non-viable → place put-only entry
                        # Data: 87.5% win rate, +$870 net from 6 qualifying entries
                        logger.info(
                            f"MKT-011: Entry #{entry_num} call credit non-viable "
                            f"(${est_call / 100:.2f}) → converting to put-only "
                            f"(put ${est_put / 100:.2f})"
                        )
                        entry.put_only = True
                        entry.call_side_skipped = True
                        entry.override_reason = "mkt-011"
                        place_put_only = True
                        credit_gate_handled = True
                    elif estimation_worked:
                        # MKT-011 worked and said proceed - skip MKT-010
                        credit_gate_handled = True
                    # else: estimation failed, fall through to MKT-010

                # MKT-010: Fallback ONLY when MKT-011 couldn't estimate credit
                # No one-sided entries allowed (v1.4.0) — skip if any wing illiquid
                if not credit_gate_handled and not self.dry_run:
                    logger.info("MKT-010: Running as fallback (credit estimation failed)")
                    if entry.call_wing_illiquid and not entry.put_wing_illiquid:
                        # Call wing illiquid — no one-sided entries, skip
                        logger.warning(
                            f"MKT-010: Entry #{entry_num} call wing illiquid — "
                            f"SKIPPING (no one-sided entries, trend: {original_trend.value})"
                        )
                        self._log_safety_event(
                            "MKT-010_SKIP",
                            f"Entry #{entry_num} - call illiquid → skip (no one-sided)",
                            "Skipped - No One-Sided"
                        )
                        self.daily_state.entries_skipped += 1
                        self.daily_state.credit_gate_skips += 1
                        self._entry_in_progress = False
                        self._current_entry = None
                        self.state = MEICState.MONITORING
                        self._next_entry_index += 1
                        self._record_skipped_entry(entry_num, "MKT-010: call wings illiquid")
                        return f"Entry #{entry_num} skipped - call illiquid, no one-sided entries"
                    elif entry.put_wing_illiquid and not entry.call_wing_illiquid:
                        # Put wing illiquid — no one-sided entries, skip
                        logger.warning(
                            f"MKT-010: Entry #{entry_num} put wing illiquid — "
                            f"SKIPPING (no one-sided entries, trend: {original_trend.value})"
                        )
                        self._log_safety_event(
                            "MKT-010_SKIP",
                            f"Entry #{entry_num} - put illiquid → skip (no one-sided)",
                            "Skipped - No One-Sided"
                        )
                        self.daily_state.entries_skipped += 1
                        self.daily_state.credit_gate_skips += 1
                        self._entry_in_progress = False
                        self._current_entry = None
                        self.state = MEICState.MONITORING
                        self._next_entry_index += 1
                        self._record_skipped_entry(entry_num, "MKT-010: put wings illiquid")
                        return f"Entry #{entry_num} skipped - put illiquid, no one-sided entries"
                    elif entry.call_wing_illiquid and entry.put_wing_illiquid:
                        # Both wings illiquid — skip entry
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
                        self._record_skipped_entry(entry_num, "MKT-010: both wings illiquid")
                        return f"Entry #{entry_num} skipped - both wings illiquid"

                # Determine entry type and execute
                if place_call_only:
                    logger.info(f"MKT-035: Placing CALL-ONLY entry #{entry_num} (down day)")
                elif place_put_only:
                    logger.info(f"MKT-011: Placing PUT-ONLY entry #{entry_num} (call non-viable)")
                else:
                    if original_trend != TrendSignal.NEUTRAL:
                        logger.info(f"EMA signal: {original_trend.value} (informational only) → placing full iron condor")
                    else:
                        logger.info(f"NEUTRAL → placing full iron condor")

                import time as _time
                _fill_start = _time.monotonic()

                if place_call_only:
                    if self.dry_run:
                        success = self._simulate_call_spread_only(entry)
                    else:
                        success = self._execute_call_spread_only(entry)
                elif place_put_only:
                    if self.dry_run:
                        success = self._simulate_put_spread_only(entry)
                    else:
                        success = self._execute_put_spread_only(entry)
                else:
                    if self.dry_run:
                        success = self._simulate_entry(entry)
                    else:
                        success = self._execute_entry(entry)

                entry._fill_time_ms = int((_time.monotonic() - _fill_start) * 1000)
                entry._fill_attempts = attempt + 1

                if success:
                    entry.entry_time = get_us_market_time()
                    entry.is_complete = True
                    # Fix #59: Capture EMA values at entry time for Trades tab logging
                    entry.ema_20_at_entry = self._last_ema_short
                    entry.ema_40_at_entry = self._last_ema_long
                    self.daily_state.entries.append(entry)
                    self.daily_state.entries_completed += 1
                    self.daily_state.total_credit_received += entry.total_credit

                    # Track commission: 2 legs for one-sided, 4 for full IC
                    num_legs = 2 if (place_put_only or place_call_only) else 4
                    entry.open_commission = num_legs * self.commission_per_leg * self.contracts_per_entry
                    self.daily_state.total_commission += entry.open_commission

                    # Track one-sided entry count
                    if place_put_only or place_call_only:
                        self.daily_state.one_sided_entries += 1

                    # Calculate stop losses
                    self._calculate_stop_levels_hydra(entry)

                    # Log to Google Sheets
                    self._log_entry(entry)

                    # Record to SQLite (with daemon-thread Greeks fetch)
                    entry._spx_at_entry = self.current_price
                    self._record_entry_to_db(entry)

                    # Send entry alert
                    mult = 100 * entry.contracts
                    trend_label = original_trend.value.upper()

                    if place_call_only:
                        # MKT-035/MKT-038/MKT-040: Call-only alert
                        sc_fill = entry.short_call_fill_price
                        lc_fill = entry.long_call_fill_price
                        width = int(entry.long_call_strike - entry.short_call_strike)
                        spx_chg = ((self.current_price - self.market_data.spx_open) / self.market_data.spx_open * 100) if self.market_data.spx_open > 0 else 0
                        cond_tag = " (conditional)" if is_conditional else ""
                        override = getattr(entry, 'override_reason', None) or "mkt-035"
                        override_tag = override.upper()
                        if override == "mkt-038":
                            reason_text = "FOMC T+1 → puts skipped"
                        elif override == "mkt-040":
                            reason_text = "Put credit non-viable → call-only"
                        else:
                            reason_text = "Down day → puts skipped"
                        msg_lines = [
                            f"*Entry #{entry_num}* [{override_tag}] Call-Only{cond_tag}",
                            f"SPX {self.current_price:,.2f} ({spx_chg:+.2f}% from open) | VIX {self.current_vix:.2f}",
                            f"Trend: {trend_label} | {reason_text}",
                            "",
                            f"SC {entry.short_call_strike:.0f} @ ${sc_fill:.2f} (${sc_fill * mult:.0f})",
                            f"LC {entry.long_call_strike:.0f} @ ${lc_fill:.2f} (-${lc_fill * mult:.0f})",
                            f"*Call: ${entry.call_spread_credit:.0f}*",
                            "",
                            f"Comm: ${entry.open_commission:.0f} | Width: {width}pt",
                            f"Stop: ${entry.call_side_stop:.0f} ({self._call_only_stop_label(override)})",
                        ]
                    elif place_put_only:
                        # Put-only alert
                        sp_fill = entry.short_put_fill_price
                        lp_fill = entry.long_put_fill_price
                        width = int(entry.short_put_strike - entry.long_put_strike)
                        msg_lines = [
                            f"*Entry #{entry_num}* [MKT-011] Put-Only",
                            f"SPX {self.current_price:,.2f} | VIX {self.current_vix:.2f}",
                            f"Trend: {trend_label} | Call credit non-viable",
                            "",
                            f"SP {entry.short_put_strike:.0f} @ ${sp_fill:.2f} (${sp_fill * mult:.0f})",
                            f"LP {entry.long_put_strike:.0f} @ ${lp_fill:.2f} (-${lp_fill * mult:.0f})",
                            f"*Put: ${entry.put_spread_credit:.0f}*",
                            "",
                            f"Comm: ${entry.open_commission:.0f} | Width: {width}pt",
                            f"Stop: ${entry.put_side_stop:.0f} (credit + buffer)",
                        ]
                    else:
                        # Full IC alert
                        sc_fill = entry.short_call_fill_price
                        lc_fill = entry.long_call_fill_price
                        sp_fill = entry.short_put_fill_price
                        lp_fill = entry.long_put_fill_price
                        width = int(entry.long_call_strike - entry.short_call_strike)
                        msg_lines = [
                            f"*Entry #{entry_num}* [{trend_label}] Full IC",
                            f"SPX {self.current_price:,.2f} | VIX {self.current_vix:.2f}",
                        ]
                        if self._last_ema_short > 0 and self._last_ema_long > 0:
                            msg_lines.append(f"Trend: {trend_label} (EMA {self._last_ema_short:.0f}/{self._last_ema_long:.0f})")
                        else:
                            msg_lines.append(f"Trend: {trend_label}")
                        msg_lines.append("")
                        msg_lines.append(f"SC {entry.short_call_strike:.0f} @ ${sc_fill:.2f} (${sc_fill * mult:.0f})")
                        msg_lines.append(f"LC {entry.long_call_strike:.0f} @ ${lc_fill:.2f} (-${lc_fill * mult:.0f})")
                        msg_lines.append(f"*Call: ${entry.call_spread_credit:.0f}*")
                        msg_lines.append("")
                        msg_lines.append(f"SP {entry.short_put_strike:.0f} @ ${sp_fill:.2f} (${sp_fill * mult:.0f})")
                        msg_lines.append(f"LP {entry.long_put_strike:.0f} @ ${lp_fill:.2f} (-${lp_fill * mult:.0f})")
                        msg_lines.append(f"*Put: ${entry.put_spread_credit:.0f}*")
                        msg_lines.append("")
                        msg_lines.append(f"*Total: ${entry.total_credit:.0f}* (${entry.call_spread_credit:.0f}C + ${entry.put_spread_credit:.0f}P)")
                        msg_lines.append(f"Comm: ${entry.open_commission:.0f} | Width: {width}pt")
                        if entry.call_side_stop != entry.put_side_stop:
                            msg_lines.append(f"Stop: ${entry.call_side_stop:.0f}C / ${entry.put_side_stop:.0f}P")
                        else:
                            msg_lines.append(f"Stop: ${entry.call_side_stop:.0f}/side")

                    alert_details = {"attempts": attempt + 1} if attempt > 0 else {}
                    self.alert_service.send_alert(
                        alert_type=AlertType.POSITION_OPENED,
                        title="Position Opened",
                        message="\n".join(msg_lines),
                        priority=AlertPriority.MEDIUM,
                        details=alert_details,
                    )

                    self._record_api_result(True)
                    self._next_entry_index += 1
                    self._entry_in_progress = False
                    self._current_entry = None
                    self.state = MEICState.MONITORING

                    self._save_state_to_disk()

                    if place_call_only:
                        override = getattr(entry, 'override_reason', None) or "mkt-035"
                        entry_type = f"Call-Only ({override.upper()})"
                    elif place_put_only:
                        entry_type = "Put-Only (MKT-011)"
                    else:
                        entry_type = f"[{original_trend.value.upper()}]"
                    result_msg = f"Entry #{entry_num} {entry_type} complete - Credit: ${entry.total_credit:.2f}"
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
    # PUT-ONLY ENTRY EXECUTION (v1.7.1 — MKT-011 re-enablement)
    # =========================================================================
    # When call credit is non-viable (< $2.00 with MKT-029 floor $0.75), place only the put
    # spread. Data from Feb 10 - Mar 2: 87.5% win rate, +$870 net from
    # 6 qualifying entries.
    #
    # Leg order (safest): Long Put first (buy protection), then Short Put.
    # Same patterns as _execute_entry(): progressive slippage, rollback,
    # verify fill prices via PositionBase.OpenPrice.
    # =========================================================================

    def _execute_put_spread_only(self, entry: HydraIronCondorEntry) -> bool:
        """
        Execute a put-only entry (MKT-011 conversion).

        Places only the put spread (2 legs) when call credit is non-viable.
        Long put first for safety, then short put.

        Args:
            entry: HydraIronCondorEntry with put_only=True already set

        Returns:
            True if both put legs filled successfully
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
            entry.long_put_fill_price = long_put_result.get("fill_price", 0)
            filled_legs.append(("long_put", entry.long_put_position_id, entry.long_put_uic))
            self._register_position(entry, "long_put")

            # 2. Short Put (now we have the hedge)
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
            entry.short_put_fill_price = short_put_result.get("fill_price", 0)
            short_put_credit = short_put_result.get("credit", 0)
            entry.put_spread_credit = short_put_credit - long_put_debit
            logger.debug(
                f"Put spread: short ${short_put_credit:.2f} - long ${long_put_debit:.2f} "
                f"= net ${entry.put_spread_credit:.2f}"
            )
            filled_legs.append(("short_put", entry.short_put_position_id, entry.short_put_uic))
            self._register_position(entry, "short_put")

            # Call side not placed — zero out
            entry.call_spread_credit = 0
            entry.short_call_fill_price = 0
            entry.long_call_fill_price = 0

            # FIX #70 Part A: Verify fill prices against PositionBase.OpenPrice
            self._verify_entry_fill_prices(entry)

            # Set initial monitoring prices for cushion calculation
            entry.short_put_price = entry.short_put_fill_price
            entry.long_put_price = entry.long_put_fill_price
            entry.short_call_price = 0
            entry.long_call_price = 0

            logger.info(
                f"Entry #{entry.entry_number} put-only complete: "
                f"Put credit ${entry.put_spread_credit:.2f}"
            )

            return True

        except Exception as e:
            logger.error(f"Put-only entry failed at leg {len(filled_legs) + 1}: {e}")

            # Check for naked shorts (short put without long put hedge)
            has_naked_short = False
            naked_short_info = None
            for leg_name, pos_id, uic in filled_legs:
                if leg_name.startswith("short_"):
                    hedge_name = "long_" + leg_name[6:]
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

    def _simulate_put_spread_only(self, entry: HydraIronCondorEntry) -> bool:
        """
        Simulate a put-only entry (dry-run mode).

        Args:
            entry: HydraIronCondorEntry with put_only=True

        Returns:
            True if simulation successful
        """
        spread_width = self._get_vix_adjusted_spread_width(self.current_vix, "put")
        credit_ratio = 0.025  # 2.5% of spread width
        entry.put_spread_credit = spread_width * credit_ratio * 100 * self.contracts_per_entry
        entry.call_spread_credit = 0

        base_id = int(datetime.now().timestamp() * 1000)
        entry.short_put_position_id = f"DRY_{base_id}_SP"
        entry.long_put_position_id = f"DRY_{base_id}_LP"

        logger.info(
            f"[DRY RUN] Simulated Put-Only Entry #{entry.entry_number}: "
            f"Put credit ${entry.put_spread_credit:.2f}"
        )

        return True

    # =========================================================================
    # CALL-ONLY ENTRY EXECUTION (MKT-035 — call-only on down days)
    # =========================================================================
    # When SPX drops >= threshold% below today's open, place only the call
    # spread. 20-day data: down days have 71% put stop rate but only 7% call
    # stop rate — call-only turns -$15 P&L into +$1,215.
    #
    # Leg order (safest): Long Call first (buy protection), then Short Call.
    # Same patterns as _execute_put_spread_only(): progressive slippage,
    # rollback, verify fill prices via PositionBase.OpenPrice.
    # =========================================================================

    def _execute_call_spread_only(self, entry: HydraIronCondorEntry) -> bool:
        """
        Execute a call-only entry (MKT-035 down-day conversion).

        Places only the call spread (2 legs) when down-day filter triggers.
        Long call first for safety, then short call.

        Args:
            entry: HydraIronCondorEntry with call_only=True already set

        Returns:
            True if both call legs filled successfully
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
            entry.long_call_fill_price = long_call_result.get("fill_price", 0)
            filled_legs.append(("long_call", entry.long_call_position_id, entry.long_call_uic))
            self._register_position(entry, "long_call")

            # 2. Short Call (now we have the hedge)
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
            entry.short_call_fill_price = short_call_result.get("fill_price", 0)
            short_call_credit = short_call_result.get("credit", 0)
            entry.call_spread_credit = short_call_credit - long_call_debit
            logger.debug(
                f"Call spread: short ${short_call_credit:.2f} - long ${long_call_debit:.2f} "
                f"= net ${entry.call_spread_credit:.2f}"
            )
            filled_legs.append(("short_call", entry.short_call_position_id, entry.short_call_uic))
            self._register_position(entry, "short_call")

            # Put side not placed — zero out
            entry.put_spread_credit = 0
            entry.short_put_fill_price = 0
            entry.long_put_fill_price = 0

            # FIX #70 Part A: Verify fill prices against PositionBase.OpenPrice
            self._verify_entry_fill_prices(entry)

            # Set initial monitoring prices for cushion calculation
            entry.short_call_price = entry.short_call_fill_price
            entry.long_call_price = entry.long_call_fill_price
            entry.short_put_price = 0
            entry.long_put_price = 0

            logger.info(
                f"Entry #{entry.entry_number} call-only complete: "
                f"Call credit ${entry.call_spread_credit:.2f}"
            )

            return True

        except Exception as e:
            logger.error(f"Call-only entry failed at leg {len(filled_legs) + 1}: {e}")

            # Check for naked shorts (short call without long call hedge)
            has_naked_short = False
            naked_short_info = None
            for leg_name, pos_id, uic in filled_legs:
                if leg_name.startswith("short_"):
                    hedge_name = "long_" + leg_name[6:]
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

    def _simulate_call_spread_only(self, entry: HydraIronCondorEntry) -> bool:
        """
        Simulate a call-only entry (dry-run mode).

        Args:
            entry: HydraIronCondorEntry with call_only=True

        Returns:
            True if simulation successful
        """
        spread_width = self._get_vix_adjusted_spread_width(self.current_vix, "call")
        credit_ratio = 0.010  # 1.0% of spread width (calls have lower premium)
        entry.call_spread_credit = spread_width * credit_ratio * 100 * self.contracts_per_entry
        entry.put_spread_credit = 0

        base_id = int(datetime.now().timestamp() * 1000)
        entry.short_call_position_id = f"DRY_{base_id}_SC"
        entry.long_call_position_id = f"DRY_{base_id}_LC"

        logger.info(
            f"[DRY RUN] Simulated Call-Only Entry #{entry.entry_number}: "
            f"Call credit ${entry.call_spread_credit:.2f}"
        )

        return True

    # =========================================================================
    # STOP LOSS EXECUTION & CALCULATION
    # =========================================================================
    # MKT-025: Short-only stop loss close. When a stop triggers, only close
    # the SHORT leg via market order. The LONG leg stays open and expires
    # worthless at end-of-day settlement (0DTE). This matches Tammy Chambless
    # and Sandvand's approach: "set stops on the short only, not on the spread."
    #
    # Benefits: reduces slippage (1 market order instead of 2), saves $2.50
    # commission per stop (1 leg instead of 2), avoids selling illiquid long
    # wings at terrible fill prices.
    #
    # Tradeoff: we lose the long leg's residual value (it expires worthless
    # instead of being sold for $5-$65). For far-OTM long wings (MKT-024 2×
    # wider starting distance), this is typically $5-$15 for calls and $20-$50
    # for puts.
    # =========================================================================

    def _execute_stop_loss(self, entry, side: str) -> str:
        """
        Execute a stop loss — mode depends on short_only_stop config.

        When short_only_stop=False (default): delegates to base MEIC which
        closes BOTH short and long legs via market order.

        When short_only_stop=True (MKT-025): closes only the SHORT leg.
        The long leg stays open and expires at end-of-day settlement (0DTE).
        MKT-033 salvage may sell the long if it appreciates >= $10.

        Why: Tammy Chambless and Sandvand (1,344+ trades) both recommend
        "stop on short only, not on the spread" to reduce slippage on the
        illiquid long wing. CBOE post-Aug-2023 improvements make market
        orders on the liquid short leg reliable.

        Settlement cleanup: check_after_hours_settlement() detects the
        orphaned long position (in registry but gone from Saxo after expiry),
        unregisters it, and clears the position_id/uic. _process_expired_credits()
        correctly skips stopped sides (no double-counting).

        Args:
            entry: IronCondorEntry (or HydraIronCondorEntry) with stop triggered
            side: "call" or "put"

        Returns:
            str describing action taken
        """
        # MKT-036: Log confirmation context before executing stop
        breach_time = getattr(entry, f'{side}_breach_time', None)
        breach_count = getattr(entry, f'{side}_breach_count', 0)
        if breach_time and self.stop_confirmation_enabled:
            confirmation_seconds = (datetime.now() - breach_time).total_seconds()
            logger.info(
                f"MKT-036: Stop confirmed after {confirmation_seconds:.0f}s, "
                f"{breach_count} prior recoveries"
            )

        # When short_only_stop is disabled, use base MEIC logic (closes both legs)
        if not self.short_only_stop:
            result = super()._execute_stop_loss(entry, side)
            # Record stop to SQLite
            stop_level = entry.call_side_stop if side == "call" else entry.put_side_stop
            actual_debit = entry.actual_call_stop_debit if side == "call" else entry.actual_put_stop_debit
            self._record_stop_to_db(entry, side, stop_level, actual_debit)
            return result

        logger.warning(
            f"MKT-025 STOP TRIGGERED: Entry #{entry.entry_number} {side} side "
            f"(closing SHORT only, long expires at settlement)"
        )

        self.state = MEICState.STOP_TRIGGERED
        stop_time = get_us_market_time().isoformat()

        if side == "call":
            entry.call_side_stopped = True
            entry.call_stop_time = stop_time
            self.daily_state.call_stops_triggered += 1
            # MKT-025: Only close the short leg — long expires at settlement
            positions_to_close = [
                (entry.short_call_position_id, "short_call", entry.short_call_uic),
            ]
            stop_level = entry.call_side_stop
        else:
            entry.put_side_stopped = True
            entry.put_stop_time = stop_time
            self.daily_state.put_stops_triggered += 1
            # MKT-025: Only close the short leg — long expires at settlement
            positions_to_close = [
                (entry.short_put_position_id, "short_put", entry.short_put_uic),
            ]
            stop_level = entry.put_side_stop

        # Check for double stop
        if entry.call_side_stopped and entry.put_side_stopped:
            self.daily_state.double_stops += 1
            logger.warning(f"DOUBLE STOP on Entry #{entry.entry_number}")

        # Track actual fill prices for accurate P&L calculation
        actual_close_cost = 0.0  # Cost to close the short leg only
        fill_prices_captured = True
        deferred_legs = []

        if self.dry_run:
            logger.info(f"[DRY RUN] Would close {side} SHORT of Entry #{entry.entry_number}")
            actual_close_cost = stop_level
        else:
            # Close only the short leg via market order
            for pos_id, leg_name, uic in positions_to_close:
                if pos_id:
                    _, fill_price, order_id = self._close_position_with_retry(
                        pos_id, leg_name, uic=uic, entry_number=entry.entry_number
                    )
                    if fill_price is not None:
                        # Short leg: we BUY to close (costs money)
                        actual_close_cost += fill_price * 100 * entry.contracts
                        logger.info(f"MKT-025: {leg_name} close cost: +${fill_price * 100 * entry.contracts:.2f}")
                    else:
                        fill_prices_captured = False
                        logger.info(f"MKT-025: No immediate fill price for {leg_name}, will use deferred lookup")
                        if order_id:
                            deferred_legs.append((order_id, uic, leg_name))

        # Fix #86: Clear SHORT position IDs and UICs for the stopped side.
        # MKT-025 only closes the short — long stays open for settlement.
        # Only clear the short ID/UIC so reconciliation doesn't flag it as "missing".
        # Long position ID/UIC stay intact for MKT-033 salvage and settlement.
        if side == "call":
            entry.short_call_position_id = None
            entry.short_call_uic = 0
        else:
            entry.short_put_position_id = None
            entry.short_put_uic = 0

        # Calculate net loss
        # MKT-025: close_cost is SHORT only. credit_received is NET of long cost.
        # So net_loss = short_close - (short_premium - long_cost)
        # = short_close - short_premium + long_cost
        # This includes the long leg's original cost (it expires worthless).
        if side == "call":
            credit_received = entry.call_spread_credit
        else:
            credit_received = entry.put_spread_credit

        if fill_prices_captured and not self.dry_run:
            net_loss = actual_close_cost - credit_received
            # Record actual debit for dashboard per-entry P&L
            if side == "call":
                entry.actual_call_stop_debit = actual_close_cost
            else:
                entry.actual_put_stop_debit = actual_close_cost
            logger.info(
                f"MKT-025: Actual P&L for Entry #{entry.entry_number} {side}: "
                f"short_close=${actual_close_cost:.2f} - credit=${credit_received:.2f} = "
                f"net_loss=${net_loss:.2f} (long expires at settlement)"
            )
        else:
            net_loss = stop_level - credit_received
            if not self.dry_run:
                logger.warning(
                    f"MKT-025: Using theoretical P&L (fill prices unavailable): "
                    f"stop_level=${stop_level:.2f} - credit=${credit_received:.2f} = "
                    f"net_loss=${net_loss:.2f} (may be inaccurate!)"
                )

        self.daily_state.total_realized_pnl -= net_loss

        # MKT-025: Commission for 1 close leg only ($2.50 instead of $5.00)
        close_commission = 1 * self.commission_per_leg * self.contracts_per_entry
        entry.close_commission += close_commission
        self.daily_state.total_commission += close_commission

        # Log stop loss to Google Sheets
        self._log_stop_loss(entry, side, stop_level, net_loss)

        # Queue alert
        self._queue_stop_alert(entry, side, stop_level, net_loss)

        # Save state
        self._save_state_to_disk()

        # Spawn background thread for deferred fill price lookup (short leg only)
        if not self.dry_run and deferred_legs:
            self._spawn_async_fill_correction(
                deferred_legs, actual_close_cost, entry, side, credit_received, net_loss
            )

        # Flush batched alerts
        time.sleep(0.1)
        self._flush_batched_alerts()

        # Record stop to SQLite
        self._record_stop_to_db(entry, side, stop_level, actual_close_cost)

        # MKT-033: Immediately try to sell the long leg if profitable
        if self.long_salvage_enabled and not self.dry_run:
            self._try_sell_long_leg(entry, side)

        return (
            f"MKT-025 Stop loss: Entry #{entry.entry_number} {side} "
            f"SHORT closed at ${stop_level:.2f} (long expires at settlement)"
        )

    # =========================================================================
    # MKT-033: LONG LEG SALVAGE (SELL PROFITABLE LONGS AFTER SHORT STOP)
    # =========================================================================

    def _try_sell_long_leg(self, entry, side: str, valid_pos_ids: set = None) -> bool:
        """
        MKT-033: Sell the surviving long leg if profitable after short stop.

        After MKT-025 stops only the short, the long leg normally expires worthless.
        On directional days, the long can appreciate. This method sells it if the
        gain covers round-trip commission ($5) plus max market order slippage ($5).

        Condition: (current_bid - open_price) × 100 × contracts >= min_profit

        P&L accounting: The long's original cost is already deducted from spread
        credit (and thus from the stop loss P&L). Selling the long is pure recovery
        revenue — added directly to total_realized_pnl.

        Args:
            entry: HydraIronCondorEntry with stopped side
            side: "call" or "put" — the side whose short was stopped

        Returns:
            bool: True if long was sold successfully
        """
        if side == "call":
            long_uic = entry.long_call_uic
            long_pos_id = entry.long_call_position_id
            long_open_price = entry.long_call_fill_price
            already_sold = getattr(entry, 'call_long_sold', False)
        else:
            long_uic = entry.long_put_uic
            long_pos_id = entry.long_put_position_id
            long_open_price = entry.long_put_fill_price
            already_sold = getattr(entry, 'put_long_sold', False)

        # Guard: already sold, no position, or no UIC
        if already_sold or not long_pos_id or not long_uic:
            return False

        try:
            # Verify position still exists in Saxo (may have been manually closed)
            if valid_pos_ids is not None:
                pos_exists = str(long_pos_id) in valid_pos_ids
            else:
                positions = self.client.get_positions()
                pos_exists = any(
                    str(p.get("PositionId", "")) == str(long_pos_id)
                    for p in positions
                )
            if not pos_exists:
                logger.info(
                    f"MKT-033 AUTO: Entry #{entry.entry_number} long {side} "
                    f"(pos {long_pos_id}) no longer in Saxo — detecting external close"
                )
                # Look up actual sale price from closedpositions
                closed = self.client.get_closed_position_price(
                    long_uic, buy_or_sell="Sell"
                )
                if closed and closed.get("closing_price", 0) > 0:
                    fill_price = closed["closing_price"]
                    revenue = fill_price * 100 * entry.contracts
                    close_commission = self.commission_per_leg * self.contracts_per_entry

                    self.daily_state.total_realized_pnl += revenue
                    self.daily_state.total_commission += close_commission
                    entry.close_commission += close_commission

                    if side == "call":
                        entry.call_long_sold = True
                        entry.call_long_sold_revenue = revenue
                        entry.long_call_position_id = None
                        entry.long_call_uic = None
                    else:
                        entry.put_long_sold = True
                        entry.put_long_sold_revenue = revenue
                        entry.long_put_position_id = None
                        entry.long_put_uic = None

                    try:
                        self.registry.unregister(long_pos_id)
                    except Exception:
                        pass

                    net_profit = revenue - close_commission
                    logger.info(
                        f"MKT-033 AUTO: Entry #{entry.entry_number} long {side} sold externally "
                        f"@ ${fill_price:.2f} (revenue=${revenue:.2f}, "
                        f"commission=${close_commission:.2f}, net=+${net_profit:.2f})"
                    )
                    self._log_safety_event(
                        "LONG_SOLD_EXTERNAL",
                        f"Entry #{entry.entry_number} long {side} sold externally "
                        f"@ ${fill_price:.2f}, revenue=${revenue:.2f}"
                    )
                else:
                    logger.warning(
                        f"MKT-033 AUTO: Entry #{entry.entry_number} long {side} missing, "
                        f"no closing price found — marking as sold with $0"
                    )
                    if side == "call":
                        entry.call_long_sold = True
                        entry.call_long_sold_revenue = 0.0
                        entry.long_call_position_id = None
                        entry.long_call_uic = None
                    else:
                        entry.put_long_sold = True
                        entry.put_long_sold_revenue = 0.0
                        entry.long_put_position_id = None
                        entry.long_put_uic = None
                    try:
                        self.registry.unregister(long_pos_id)
                    except Exception:
                        pass

                self._save_state_to_disk()
                return False  # Not sold by us, but accounted for

            # Fetch quote for bid price
            quote = self.client.get_quote(long_uic, asset_type="StockIndexOption")
            if not quote:
                logger.debug(f"MKT-033: No quote for Entry #{entry.entry_number} long {side} UIC {long_uic}")
                return False

            bid = quote.get("Quote", {}).get("Bid", 0)
            if not bid or bid <= 0:
                return False

            # Guard: invalid open price (recovery/fill lookup failure) — skip to avoid false profit
            if not long_open_price or long_open_price <= 0:
                logger.warning(
                    f"MKT-033: Skipping Entry #{entry.entry_number} long {side} — "
                    f"invalid open price ${long_open_price}"
                )
                return False

            # Check profitability: appreciation must cover round-trip commission + slippage
            appreciation = (bid - long_open_price) * 100 * entry.contracts
            if appreciation < self.long_salvage_min_profit:
                logger.debug(
                    f"MKT-033: Entry #{entry.entry_number} long {side} below threshold: "
                    f"bid=${bid:.2f} - open=${long_open_price:.2f} = ${appreciation:.2f} "
                    f"< ${self.long_salvage_min_profit:.2f} min"
                )
                return False

            logger.info(
                f"MKT-033 LONG SALVAGE: Entry #{entry.entry_number} long {side} "
                f"bid=${bid:.2f} (open=${long_open_price:.2f}), appreciation=${appreciation:.2f} "
                f">= ${self.long_salvage_min_profit:.2f} threshold — selling via market order"
            )

            # Sell the long via market order
            success, fill_price, order_id = self._close_position_with_retry(
                long_pos_id, f"long_{side}", uic=long_uic, entry_number=entry.entry_number
            )

            if not success:
                logger.warning(f"MKT-033: Failed to sell Entry #{entry.entry_number} long {side}")
                return False

            # Calculate revenue from actual fill (or bid as fallback)
            actual_fill = fill_price if fill_price and fill_price > 0 else bid
            revenue = actual_fill * 100 * entry.contracts

            # Update P&L (revenue is pure recovery — long cost already in spread credit)
            self.daily_state.total_realized_pnl += revenue

            # Commission for closing 1 leg
            close_commission = self.commission_per_leg * self.contracts_per_entry
            entry.close_commission += close_commission
            self.daily_state.total_commission += close_commission

            # Mark entry as long sold
            if side == "call":
                entry.call_long_sold = True
                entry.call_long_sold_revenue = revenue
                entry.long_call_position_id = None
                entry.long_call_uic = None
            else:
                entry.put_long_sold = True
                entry.put_long_sold_revenue = revenue
                entry.long_put_position_id = None
                entry.long_put_uic = None

            # Unregister from position registry
            try:
                self.registry.unregister(long_pos_id)
            except Exception as e:
                logger.debug(f"MKT-033: Registry unregister for {long_pos_id}: {e}")

            net_profit = revenue - close_commission
            logger.info(
                f"MKT-033 SOLD: Entry #{entry.entry_number} long {side} @ ${actual_fill:.2f} "
                f"(revenue=${revenue:.2f}, commission=${close_commission:.2f}, net=+${net_profit:.2f})"
            )

            # Log to Trades tab in Google Sheets
            try:
                if side == "call":
                    long_strike = entry.long_call_strike
                    strike_str = f"C:{long_strike} (long)"
                else:
                    long_strike = entry.long_put_strike
                    strike_str = f"P:{long_strike} (long)"

                self.trade_logger.log_trade(
                    action=f"{self.BOT_NAME} Salvage #{entry.entry_number} ({side.upper()})",
                    strike=strike_str,
                    price=actual_fill,
                    delta=0.0,
                    pnl=net_profit,  # Positive: revenue minus commission
                    saxo_client=self.client,
                    underlying_price=self.current_price,
                    vix=self.current_vix,
                    option_type=f"MKT-033 Long {side.title()}",
                    trade_reason=f"Long Salvage | Open=${long_open_price:.2f} Close=${actual_fill:.2f} Rev=${revenue:.2f}"
                )
            except Exception as e:
                logger.debug(f"MKT-033: Failed to log salvage to Sheets: {e}")

            # Send Telegram alert (MEDIUM priority — same as position closed)
            try:
                self.alert_service.send_alert(
                    alert_type=AlertType.POSITION_CLOSED,
                    title=f"MKT-033 Long Salvage — Entry #{entry.entry_number}",
                    message=(
                        f"Long {side} sold @ ${actual_fill:.2f} "
                        f"(open ${long_open_price:.2f}, +${bid - long_open_price:.2f} appreciation)\n"
                        f"Revenue: ${revenue:.0f} | Commission: ${close_commission:.0f} | "
                        f"Net: +${net_profit:.0f}"
                    ),
                    priority=AlertPriority.MEDIUM,
                    details={
                        "entry_number": entry.entry_number,
                        "side": side,
                        "fill_price": actual_fill,
                        "open_price": long_open_price,
                        "revenue": revenue,
                        "net_profit": net_profit,
                    }
                )
            except Exception:
                pass  # Alert failure shouldn't block trading

            # Log safety event for audit trail
            try:
                self._log_safety_event(
                    "MKT-033_LONG_SALVAGE",
                    f"Entry #{entry.entry_number} long {side} sold @ ${actual_fill:.2f} "
                    f"(open=${long_open_price:.2f}, revenue=${revenue:.2f}, net=+${net_profit:.2f})",
                    f"Salvaged +${net_profit:.2f}"
                )
            except Exception:
                pass  # Logging failure shouldn't block trading

            # Save state
            self._save_state_to_disk()
            return True

        except Exception as e:
            logger.warning(f"MKT-033: Error checking Entry #{entry.entry_number} long {side}: {e}")
            return False

    def _check_long_salvage(self):
        """
        MKT-033: Periodic check for profitable long legs to sell.

        Called from _handle_monitoring() after stop loss checks. Iterates all
        entries with stopped sides and unsold long legs, attempting to sell
        if the appreciation threshold is met.

        Only runs during regular market hours (9:30 AM - 4:00 PM ET).
        """
        if not self.long_salvage_enabled or self.dry_run:
            return

        # Only during regular market hours
        now = get_us_market_time()
        if now.hour < 9 or (now.hour == 9 and now.minute < 30) or now.hour >= 16:
            return

        # Fetch positions once for all long salvage checks (avoid N API calls)
        try:
            all_positions = self.client.get_positions()
            valid_pos_ids = {str(p.get("PositionId", "")) for p in all_positions}
        except Exception as e:
            logger.warning(f"MKT-033: Could not fetch positions: {e}")
            return

        for entry in self.daily_state.entries:
            # Check call side: short stopped, long still open and unsold
            if entry.call_side_stopped and not getattr(entry, 'call_long_sold', False):
                if entry.long_call_position_id and entry.long_call_uic:
                    self._try_sell_long_leg(entry, "call", valid_pos_ids)

            # Check put side: short stopped, long still open and unsold
            if entry.put_side_stopped and not getattr(entry, 'put_long_sold', False):
                if entry.long_put_position_id and entry.long_put_uic:
                    self._try_sell_long_leg(entry, "put", valid_pos_ids)

    def _get_saxo_pnl_for_entry(self, entry, positions=None):
        """MKT-025: Exclude stopped sides' positions from Saxo P&L lookup.

        When MKT-025 stops only the short leg, the long leg remains open on Saxo.
        Its ProfitLossOnTrade would double-count loss already in total_realized_pnl.
        Only include positions for non-stopped sides.
        """
        try:
            if positions is None:
                positions = self.client.get_positions()

            total_pnl = 0.0
            position_ids = []

            # Only include position IDs for non-stopped sides
            if not entry.call_side_stopped:
                if entry.short_call_position_id:
                    position_ids.append(entry.short_call_position_id)
                if entry.long_call_position_id:
                    position_ids.append(entry.long_call_position_id)
            if not entry.put_side_stopped:
                if entry.short_put_position_id:
                    position_ids.append(entry.short_put_position_id)
                if entry.long_put_position_id:
                    position_ids.append(entry.long_put_position_id)

            for pos in positions:
                pos_id = str(pos.get("PositionId", ""))
                if pos_id in position_ids:
                    pos_view = pos.get("PositionView", {})
                    pnl = pos_view.get("ProfitLossOnTrade", 0) or 0
                    total_pnl += pnl

            return total_pnl

        except Exception as e:
            logger.debug(f"Error getting Saxo P&L for Entry #{entry.entry_number}: {e}")
            return entry.unrealized_pnl

    def _calculate_stop_levels_hydra(self, entry: HydraIronCondorEntry):
        """
        Calculate stop loss levels for trend-following entries.

        - Full IC: stop = total_credit + buffer (call $0.35, put $1.55)
        - Put-only (MKT-039): credit + put_stop_buffer ($1.55)
        - Call-only (MKT-040): call_credit + theo $2.60 put + call_stop_buffer ($0.35)

        MKT-020/MKT-022 progressive tightening + credit minimums ($2.00 calls,
        $2.75 puts) reduced skew from 3-7x to 1-3x.

        Args:
            entry: HydraIronCondorEntry to calculate stops for
        """
        MIN_STOP_LEVEL = 50.0

        if entry.call_only:
            # Only call spread placed
            credit = entry.call_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(f"CRITICAL: Low credit ${credit:.2f}, using minimum stop")
                credit = MIN_STOP_LEVEL

            # All call-only entries use theoretical put for stop calculation:
            # stop = call_credit + theoretical_put ($260) + buffer
            # This applies to MKT-035 (conditional), MKT-038 (FOMC T+1),
            # and MKT-040 (put non-viable) — consistent formula for all.
            theoretical_put = self.downday_theoretical_put_credit
            base_stop = credit + theoretical_put
            override = getattr(entry, 'override_reason', None) or "mkt-040"
            logger.info(
                f"{override.upper()}: Call-only stop = call ${credit:.2f} + "
                f"theoretical put ${theoretical_put:.2f} + buffer ${self.call_stop_buffer:.2f} "
                f"= ${base_stop + self.call_stop_buffer:.2f}"
            )

            stop_level = base_stop + self.call_stop_buffer
            stop_level = max(stop_level, MIN_STOP_LEVEL)

            entry.call_side_stop = stop_level
            entry.put_side_stop = 0  # No put side

        elif entry.put_only:
            # Only put spread placed
            credit = entry.put_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(f"CRITICAL: Low credit ${credit:.2f}, using minimum stop")
                credit = MIN_STOP_LEVEL

            # MKT-039: Put-only stop = credit + $1.55 buffer (same pattern as full IC puts).
            # The $1.55 put buffer prevents false stops (walk-forward optimized); the old
            # 2× multiplier was redundant and inflated max loss.
            # Note: Call-only legacy keeps 2× because call buffer is only $0.10.
            base_stop = credit
            stop_level = base_stop + self.put_stop_buffer

            entry.put_side_stop = stop_level
            entry.call_side_stop = 0  # No call side
            logger.info(f"Stop level for put spread: ${stop_level:.2f} (credit ${credit:.2f} + buffer ${self.put_stop_buffer:.2f})")

        else:
            # Full IC — stop = total_credit + asymmetric buffer (call $0.35, put $1.55)
            # MKT-020/MKT-022 tightening + credit minimums ($2.00 calls, $2.75 puts)
            # keep skew manageable.
            total_credit = entry.total_credit

            if total_credit < MIN_STOP_LEVEL:
                logger.critical(f"CRITICAL: Total credit ${total_credit:.2f} very low, using minimum stop")
                total_credit = MIN_STOP_LEVEL

            base_stop = total_credit
            call_stop_level = base_stop + self.call_stop_buffer
            put_stop_level = base_stop + self.put_stop_buffer

            entry.call_side_stop = call_stop_level
            entry.put_side_stop = put_stop_level
            if self.put_stop_buffer != self.call_stop_buffer:
                logger.info(
                    f"Stop level for full IC: call=${call_stop_level:.2f}, put=${put_stop_level:.2f} "
                    f"(total credit ${total_credit:.2f} + call buffer ${self.call_stop_buffer:.2f} / put buffer ${self.put_stop_buffer:.2f})"
                )
            else:
                logger.info(
                    f"Stop level for full IC: ${call_stop_level:.2f} per side "
                    f"(total credit: call=${entry.call_spread_credit:.2f} + put=${entry.put_spread_credit:.2f} = ${total_credit:.2f} + buffer ${self.call_stop_buffer:.2f})"
                )
            # MKT-042: Log initial decayed stop levels if buffer decay is active
            if (self.buffer_decay_start_mult is not None
                    and self.buffer_decay_start_mult > 1.0
                    and self.buffer_decay_hours is not None):
                eff_call = self._get_effective_stop_level(entry, "call")
                eff_put = self._get_effective_stop_level(entry, "put")
                logger.info(
                    f"MKT-042: Buffer decay ACTIVE — effective stops: call=${eff_call:.2f}, put=${eff_put:.2f} "
                    f"({self.buffer_decay_start_mult:.2f}× decaying to 1× over {self.buffer_decay_hours:.1f}h)"
                )

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
            entry: IronCondorEntry (or HydraIronCondorEntry) to validate

        Returns:
            Tuple of (is_valid, message)
        """
        if not PNL_SANITY_CHECK_ENABLED:
            return True, "P&L sanity check disabled"

        is_hydra_entry = isinstance(entry, HydraIronCondorEntry)

        # =====================================================================
        # CALL-ONLY ENTRY: Only validate call side prices
        # =====================================================================
        if is_hydra_entry and entry.call_only:
            # Only check call side - put side was never placed
            if not entry.call_side_stopped:
                call_long_sold = getattr(entry, 'call_long_sold', False)
                if entry.short_call_price == 0 and entry.long_call_price == 0 and not call_long_sold:
                    logger.warning(
                        f"DATA-004: Entry #{entry.entry_number} call side has zero prices "
                        f"(SC=${entry.short_call_price:.2f}, LC=${entry.long_call_price:.2f}) - skipping stop check"
                    )
                    return False, "Call side prices are zero"
                if not call_long_sold and (entry.short_call_price == 0 or entry.long_call_price == 0):
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
        elif is_hydra_entry and entry.put_only:
            # Only check put side - call side was never placed
            if not entry.put_side_stopped:
                put_long_sold = getattr(entry, 'put_long_sold', False)
                if entry.short_put_price == 0 and entry.long_put_price == 0 and not put_long_sold:
                    logger.warning(
                        f"DATA-004: Entry #{entry.entry_number} put side has zero prices "
                        f"(SP=${entry.short_put_price:.2f}, LP=${entry.long_put_price:.2f}) - skipping stop check"
                    )
                    return False, "Put side prices are zero"
                if not put_long_sold and (entry.short_put_price == 0 or entry.long_put_price == 0):
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

    def _batch_update_entry_prices(self):
        """
        Override parent to handle Hydra one-sided entry simulation in dry-run.

        In live mode, the parent's batch approach works correctly for one-sided
        entries because it only collects non-zero UICs (one-sided entries have
        UIC=0 for the non-placed side).

        In dry-run mode, we need to use _simulate_hydra_entry_prices() for
        one-sided entries instead of the parent's _simulate_entry_prices().
        """
        if self.dry_run:
            for entry in self.daily_state.active_entries:
                call_done = entry.call_side_stopped or getattr(entry, 'call_side_expired', False) or getattr(entry, 'call_side_skipped', False)
                put_done = entry.put_side_stopped or getattr(entry, 'put_side_expired', False) or getattr(entry, 'put_side_skipped', False)
                if call_done and put_done:
                    continue
                self._simulate_hydra_entry_prices(entry)
            return
        # Live mode: parent's batch handles one-sided entries naturally
        super()._batch_update_entry_prices()

    def _simulate_hydra_entry_prices(self, entry: IronCondorEntry):
        """
        Simulate option prices for Hydra entries in dry-run mode.

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

        is_hydra_entry = isinstance(entry, HydraIronCondorEntry)

        if is_hydra_entry and entry.call_only:
            # Only simulate call side
            initial_short_price = entry.call_spread_credit / 100  # Per contract
            entry.short_call_price = initial_short_price * decay_factor
            entry.long_call_price = initial_short_price * decay_factor * 0.3

        elif is_hydra_entry and entry.put_only:
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
    # MKT-042: Effective stop level with buffer decay
    # =========================================================================

    def _get_effective_stop_level(self, entry, side: str) -> float:
        """Return effective stop level with MKT-042 buffer decay applied.
        Used by heartbeat and Telegram for accurate cushion display."""
        base_stop = getattr(entry, f'{side}_side_stop', 0)
        if (self.buffer_decay_start_mult is not None
                and self.buffer_decay_hours is not None
                and self.buffer_decay_hours > 0
                and self.buffer_decay_start_mult > 1.0
                and hasattr(entry, 'entry_time') and entry.entry_time is not None):
            try:
                elapsed_h = (get_us_market_time() - entry.entry_time).total_seconds() / 3600
            except (TypeError, AttributeError):
                # entry_time may be a string after state file restore
                return base_stop
            decay_factor = max(0.0, min(1.0, 1.0 - elapsed_h / self.buffer_decay_hours))
            if decay_factor > 0:
                buf = self.call_stop_buffer if side == "call" else self.put_stop_buffer
                extra = buf * (self.buffer_decay_start_mult - 1) * decay_factor
                return base_stop + extra
        return base_stop

    # MKT-036: Stop confirmation timer helper
    # =========================================================================

    def _check_stop_with_confirmation(self, entry, side: str, spread_value: float, stop_level: float) -> Optional[str]:
        """
        MKT-036: Check stop with confirmation timer.
        MKT-042: Time-decaying buffer — wider stop early, normal after decay_hours.

        Instead of executing immediately when spread_value >= stop_level,
        requires the breach to persist for stop_confirmation_seconds (default 75s).
        If spread recovers below stop level during the window, timer resets.

        Returns stop result string if stop executed, or None.
        """
        # MKT-042: Apply time-decaying buffer via shared helper
        stop_level = self._get_effective_stop_level(entry, side)

        if spread_value >= stop_level:
            if not self.stop_confirmation_enabled:
                return self._execute_stop_loss(entry, side)

            breach_time = getattr(entry, f'{side}_breach_time', None)
            now = datetime.now()

            if breach_time is None:
                # First breach — start timer
                setattr(entry, f'{side}_breach_time', now)
                logger.info(
                    f"MKT-036: Entry #{entry.entry_number} {side} breached stop "
                    f"(SV=${spread_value:.0f} >= ${stop_level:.0f}), "
                    f"confirming {self.stop_confirmation_seconds}s..."
                )
                self._save_state_to_disk()  # Save ONCE on first breach
            else:
                elapsed = (now - breach_time).total_seconds()
                if elapsed >= self.stop_confirmation_seconds:
                    # Confirmed — execute stop
                    logger.info(
                        f"MKT-036: Entry #{entry.entry_number} {side} CONFIRMED "
                        f"after {elapsed:.0f}s"
                    )
                    return self._execute_stop_loss(entry, side)
                # else: still confirming — NO disk I/O, just wait for next heartbeat
        else:
            # Spread recovered below stop level — reset timer if active
            breach_time = getattr(entry, f'{side}_breach_time', None)
            if breach_time is not None:
                elapsed = (datetime.now() - breach_time).total_seconds()
                count = getattr(entry, f'{side}_breach_count', 0) + 1
                setattr(entry, f'{side}_breach_count', count)
                setattr(entry, f'{side}_breach_time', None)
                self.daily_state.stops_avoided_mkt036 += 1
                logger.info(
                    f"MKT-036: Entry #{entry.entry_number} {side} RECOVERED after {elapsed:.0f}s "
                    f"(breach #{count}, SV=${spread_value:.0f} < ${stop_level:.0f})"
                )
                self._save_state_to_disk()  # Save ONCE on recovery
                # Log recovery to Sheets
                self._log_safety_event(
                    event_type="MKT-036_RECOVERY",
                    details=(
                        f"Entry #{entry.entry_number} {side}: recovered after {elapsed:.0f}s | "
                        f"SV: ${spread_value:.0f} < ${stop_level:.0f} | Breach #{count}"
                    ),
                    result="Stop avoided"
                )
        return None

    # =========================================================================
    # OVERRIDE: Stop loss checking for one-sided entries
    # =========================================================================

    def _check_stop_losses(self) -> Optional[str]:
        """
        Check all active entries for stop loss triggers.

        Overrides parent to handle one-sided entries correctly.
        Prices are batch-fetched via _batch_update_entry_prices() before the loop.

        Returns:
            str describing stop action taken, or None
        """
        # Batch-fetch ALL option prices in a single API call
        self._batch_update_entry_prices()

        # Price-based stop: fetch SPX price once per loop (refreshed by WebSocket)
        price_stop_pts = self.price_based_stop_points  # None = use credit-based stop
        spx_now = self.current_price if price_stop_pts is not None else 0.0

        for entry in self.daily_state.active_entries:
            # Handle as HydraIronCondorEntry if possible
            is_hydra_entry = isinstance(entry, HydraIronCondorEntry)

            if is_hydra_entry and entry.call_only:
                # Only check call side
                if entry.call_side_stopped:
                    continue

                pnl_valid, pnl_message = self._validate_pnl_sanity(entry)
                if not pnl_valid:
                    logger.error(f"DATA-003: Skipping stop check - {pnl_message}")
                    continue

                if price_stop_pts is not None:
                    # Price-based: stop when SPX >= short_call - N pts
                    if spx_now > 0 and entry.short_call_strike > 0:
                        trigger = entry.short_call_strike - price_stop_pts
                        if spx_now >= trigger:
                            logger.warning(
                                f"PRICE-STOP E#{entry.entry_number} call-only: "
                                f"SPX {spx_now:.2f} >= trigger {trigger:.2f} "
                                f"(short_call={entry.short_call_strike:.0f} - {price_stop_pts}pts)"
                            )
                            result = self._execute_stop_loss(entry, "call")
                            if result:
                                return result
                else:
                    MIN_VALID_STOP = 50.0
                    if entry.call_side_stop < MIN_VALID_STOP:
                        logger.error(f"SAFETY: Invalid call stop ${entry.call_side_stop:.2f}")
                        continue
                    result = self._check_stop_with_confirmation(entry, "call", entry.call_spread_value, entry.call_side_stop)
                    if result:
                        return result
                    # MKT-041: Cushion recovery check (only if stop didn't fire)
                    result = self._check_cushion_recovery(entry, "call", entry.call_spread_value, entry.call_side_stop)
                    if result:
                        return result

            elif is_hydra_entry and entry.put_only:
                # Only check put side
                if entry.put_side_stopped:
                    continue

                pnl_valid, pnl_message = self._validate_pnl_sanity(entry)
                if not pnl_valid:
                    logger.error(f"DATA-003: Skipping stop check - {pnl_message}")
                    continue

                if price_stop_pts is not None:
                    # Price-based: stop when SPX <= short_put + N pts
                    if spx_now > 0 and entry.short_put_strike > 0:
                        trigger = entry.short_put_strike + price_stop_pts
                        if spx_now <= trigger:
                            logger.warning(
                                f"PRICE-STOP E#{entry.entry_number} put-only: "
                                f"SPX {spx_now:.2f} <= trigger {trigger:.2f} "
                                f"(short_put={entry.short_put_strike:.0f} + {price_stop_pts}pts)"
                            )
                            result = self._execute_stop_loss(entry, "put")
                            if result:
                                return result
                else:
                    MIN_VALID_STOP = 50.0
                    if entry.put_side_stop < MIN_VALID_STOP:
                        logger.error(f"SAFETY: Invalid put stop ${entry.put_side_stop:.2f}")
                        continue
                    result = self._check_stop_with_confirmation(entry, "put", entry.put_spread_value, entry.put_side_stop)
                    if result:
                        return result
                    # MKT-041: Cushion recovery check (only if stop didn't fire)
                    result = self._check_cushion_recovery(entry, "put", entry.put_spread_value, entry.put_side_stop)
                    if result:
                        return result

            else:
                # Full IC
                # FIX #47: Check stopped/expired/skipped for completeness
                call_done = entry.call_side_stopped or entry.call_side_expired or entry.call_side_skipped
                put_done = entry.put_side_stopped or entry.put_side_expired or entry.put_side_skipped
                if call_done and put_done:
                    continue

                pnl_valid, pnl_message = self._validate_pnl_sanity(entry)
                if not pnl_valid:
                    logger.error(f"DATA-003: Skipping stop check - {pnl_message}")
                    continue

                MIN_VALID_STOP = 50.0
                if not call_done:
                    if price_stop_pts is not None:
                        # Price-based: stop when SPX >= short_call - N pts
                        if spx_now > 0 and entry.short_call_strike > 0:
                            trigger = entry.short_call_strike - price_stop_pts
                            if spx_now >= trigger:
                                logger.warning(
                                    f"PRICE-STOP E#{entry.entry_number} call (IC): "
                                    f"SPX {spx_now:.2f} >= trigger {trigger:.2f} "
                                    f"(short_call={entry.short_call_strike:.0f} - {price_stop_pts}pts)"
                                )
                                result = self._execute_stop_loss(entry, "call")
                                if result:
                                    return result
                    else:
                        if entry.call_side_stop < MIN_VALID_STOP:
                            logger.error(f"SAFETY: Invalid call stop ${entry.call_side_stop:.2f} for Entry #{entry.entry_number}")
                        else:
                            result = self._check_stop_with_confirmation(entry, "call", entry.call_spread_value, entry.call_side_stop)
                            if result:
                                return result
                            # MKT-041: Cushion recovery check
                            result = self._check_cushion_recovery(entry, "call", entry.call_spread_value, entry.call_side_stop)
                            if result:
                                return result

                if not put_done:
                    if price_stop_pts is not None:
                        # Price-based: stop when SPX <= short_put + N pts
                        if spx_now > 0 and entry.short_put_strike > 0:
                            trigger = entry.short_put_strike + price_stop_pts
                            if spx_now <= trigger:
                                logger.warning(
                                    f"PRICE-STOP E#{entry.entry_number} put (IC): "
                                    f"SPX {spx_now:.2f} <= trigger {trigger:.2f} "
                                    f"(short_put={entry.short_put_strike:.0f} + {price_stop_pts}pts)"
                                )
                                result = self._execute_stop_loss(entry, "put")
                                if result:
                                    return result
                    else:
                        if entry.put_side_stop < MIN_VALID_STOP:
                            logger.error(f"SAFETY: Invalid put stop ${entry.put_side_stop:.2f} for Entry #{entry.entry_number}")
                        else:
                            result = self._check_stop_with_confirmation(entry, "put", entry.put_spread_value, entry.put_side_stop)
                            if result:
                                return result
                            # MKT-041: Cushion recovery check
                            result = self._check_cushion_recovery(entry, "put", entry.put_spread_value, entry.put_side_stop)
                            if result:
                                return result

        return None

    def _check_cushion_recovery(self, entry: HydraIronCondorEntry, side: str,
                                 spread_value: float, stop_level: float) -> Optional[str]:
        """
        MKT-041: Cushion recovery exit — close a side that nearly stopped then recovered.

        If spread_value reaches >= nearstop_pct × stop_level (danger zone), set a flag.
        If the flag is set AND spread_value drops to <= recovery_pct × stop_level, close it.

        Returns:
            str describing close action, or None
        """
        if self.cushion_nearstop_pct is None or self.cushion_recovery_pct is None:
            return None
        if stop_level <= 0 or spread_value <= 0:
            return None

        ratio = spread_value / stop_level

        # Track danger zone entry
        if side == "call":
            if ratio >= self.cushion_nearstop_pct:
                if not entry.call_hit_danger:
                    entry.call_hit_danger = True
                    logger.info(
                        f"MKT-041 E#{entry.entry_number} call DANGER: "
                        f"spread_value ${spread_value:.0f} = {ratio:.1%} of stop ${stop_level:.0f}"
                    )
            # Check recovery (only if previously in danger)
            if entry.call_hit_danger and ratio <= self.cushion_recovery_pct:
                logger.warning(
                    f"MKT-041 E#{entry.entry_number} call RECOVERY EXIT: "
                    f"spread_value ${spread_value:.0f} = {ratio:.1%} of stop ${stop_level:.0f} "
                    f"(recovered from danger zone >= {self.cushion_nearstop_pct:.0%})"
                )
                return self._execute_stop_loss(entry, "call")
        else:  # put
            if ratio >= self.cushion_nearstop_pct:
                if not entry.put_hit_danger:
                    entry.put_hit_danger = True
                    logger.info(
                        f"MKT-041 E#{entry.entry_number} put DANGER: "
                        f"spread_value ${spread_value:.0f} = {ratio:.1%} of stop ${stop_level:.0f}"
                    )
            # Check recovery
            if entry.put_hit_danger and ratio <= self.cushion_recovery_pct:
                logger.warning(
                    f"MKT-041 E#{entry.entry_number} put RECOVERY EXIT: "
                    f"spread_value ${spread_value:.0f} = {ratio:.1%} of stop ${stop_level:.0f} "
                    f"(recovered from danger zone >= {self.cushion_nearstop_pct:.0%})"
                )
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
        # Refresh trend if not checked recently (every 30 seconds)
        # This ensures heartbeat shows current trend without excessive API calls
        now = get_us_market_time()
        if self._last_trend_check is None or (now - self._last_trend_check).total_seconds() >= 30:
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

        # SPX vs open % for heartbeat display
        spx_ref = self.market_data.spx_open
        if spx_ref and spx_ref > 0 and self.current_price > 0:
            change_pct = (self.current_price - spx_ref) / spx_ref * 100
            status['spx_open'] = self.market_data.spx_open
            status['spx_high'] = self.market_data.spx_high
            status['spx_vs_open_pct'] = change_pct
            # Base-entry down-day call-only status
            status['base_downday_threshold'] = -(self.base_entry_downday_callonly_pct * 100) if self.base_entry_downday_callonly_pct else None
            status['base_downday_triggered'] = (
                self.base_entry_downday_callonly_pct is not None
                and change_pct <= -(self.base_entry_downday_callonly_pct * 100)
            )

        # MKT-018: Early close status for heartbeat display
        if self.early_close_enabled:
            if self._early_close_triggered:
                status['early_close_status'] = {
                    'triggered': True,
                    'trigger_time': self._early_close_time.strftime('%I:%M %p ET') if self._early_close_time else 'N/A',
                    'locked_pnl': self._early_close_pnl or 0,
                }
            elif self._next_entry_index >= len(self.entry_times) and len(self.daily_state.active_entries) > 0:
                unrealized = status.get('unrealized_pnl', 0)
                total_pnl = status['realized_pnl'] + unrealized
                net_pnl_val = total_pnl - status.get('total_commission', 0)
                active_legs = self._count_active_position_legs()
                close_cost = active_legs * self.early_close_cost_per_position
                capital = status.get('capital_deployed', 0)
                roc = (net_pnl_val - close_cost) / capital if capital > 0 else 0
                ec_dict: Dict[str, Any] = {
                    'tracking': True,
                    'roc': roc,
                    'threshold': self.early_close_roc_threshold,
                    'close_cost': close_cost,
                    'active_legs': active_legs,
                }
                # MKT-023: Add hold check preview for heartbeat display
                if self.hold_check_enabled:
                    pnl_after_close = net_pnl_val - close_cost
                    should_close, hold_details = self._check_hold_vs_close(pnl_after_close)
                    hold_details['should_close'] = should_close
                    ec_dict['hold_check'] = hold_details
                status['early_close_status'] = ec_dict
            else:
                status['early_close_status'] = {}

        # MKT-031: Continuous scout score for backtesting data
        if self.smart_entry_enabled and self._cached_chart_bars:
            spike_score = self._score_post_spike_calm()
            momentum_score = self._score_momentum_pause()
            total_score = spike_score + momentum_score
            status['scout_score'] = total_score
            status['scout_spike'] = spike_score
            status['scout_momentum'] = momentum_score

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

        # MKT-034: Add VIX gate shift info to heartbeat
        if self.vix_gate_enabled and self._vix_gate_resolved and self._vix_gate_start_slot > 0:
            lines.insert(0, f"  VIX-shift: slot {self._vix_gate_start_slot}")

        # SPX vs open indicator (after trend line, before entries)
        # Shows base entry mode (E1-E{N}) and conditional entry eligibility (E6/E7)
        spx_ref = self.market_data.spx_open
        if spx_ref and spx_ref > 0 and self.current_price > 0:
            change_pct = (self.current_price - spx_ref) / spx_ref * 100
            sign = "+" if change_pct >= 0 else ""
            base_count = self._base_entry_count

            # Base-entry down-day call-only check
            base_downday_active = (
                self.base_entry_downday_callonly_pct is not None
                and change_pct <= -(self.base_entry_downday_callonly_pct * 100)
            )
            base_thr = self.base_entry_downday_callonly_pct * 100 if self.base_entry_downday_callonly_pct else 0
            if base_downday_active:
                base_label = f"E1-E{base_count}: call-only (>{base_thr:.2f}% drop)"
            else:
                base_label = f"E1-E{base_count}: full IC (<{base_thr:.2f}% drop)"

            # Conditional entry eligibility
            cond_parts = []
            # E6 upday put-only (Upday-035)
            if self.upday_putonly_enabled:
                upday_thr = self.upday_threshold_pct * 100
                if change_pct >= upday_thr:
                    cond_parts.append(f"E6: put-only ({sign}{change_pct:.2f}% >= +{upday_thr:.2f}%)")
                else:
                    cond_parts.append(f"E6: pending (+{upday_thr:.2f}%)")
            # E7 down-day call-only (MKT-035) — only if enabled
            if self._conditional_downday_times_set:
                dd_thr = self.downday_threshold_pct * 100
                if change_pct <= -dd_thr:
                    cond_parts.append(f"E7: call-only ({sign}{change_pct:.2f}% <= -{dd_thr:.2f}%)")
                else:
                    cond_parts.append(f"E7: pending (-{dd_thr:.2f}%)")

            cond_label = " | ".join(cond_parts) if cond_parts else ""
            separator = " | " if cond_label else ""

            insert_idx = 1 if lines else 0  # After trend line
            lines.insert(insert_idx,
                f"  {'Down' if change_pct < 0 else 'Up'}-day: SPX {sign}{change_pct:.2f}% vs open "
                f"({self.current_price:.1f} vs {spx_ref:.1f}) | "
                f"{base_label}{separator}{cond_label}"
            )

        return lines

    def build_telegram_snapshot(self) -> str:
        """
        Build a formatted Telegram message showing current HYDRA position snapshot.

        Sent every 30 minutes during market hours after first entry.
        Uses Telegram legacy Markdown: *bold* only (no _ ` [ in message body).

        Returns:
            str: Formatted Markdown message for Telegram
        """
        lines = []

        # Market data header
        spx = self.current_price
        vix = self.current_vix
        lines.append(f"*SPX* {spx:,.2f}  *VIX* {vix:.2f}")

        # Trend line
        trend_str = self._current_trend.value.upper() if self._current_trend else "N/A"
        if self._last_ema_short > 0 and self._last_ema_long > 0:
            lines.append(f"Trend: {trend_str} (EMA {self._last_ema_short:.0f}/{self._last_ema_long:.0f})")
        else:
            lines.append(f"Trend: {trend_str}")

        # Entry count header
        total_entries = len(self.entry_times)
        completed = self.daily_state.entries_completed
        active_count = len(self.daily_state.active_entries)
        lines.append("")
        lines.append(f"━━━ Entries {completed}/{total_entries} | Active {active_count} ━━━")

        # Fetch positions once for P&L calculations
        try:
            positions = self.client.get_positions()
        except Exception:
            positions = []

        # Per-entry details
        for entry in self.daily_state.entries:
            # Determine entry status icon
            call_stopped = entry.call_side_stopped
            put_stopped = entry.put_side_stopped
            call_expired = getattr(entry, 'call_side_expired', False)
            put_expired = getattr(entry, 'put_side_expired', False)
            call_skipped = getattr(entry, 'call_side_skipped', False)
            put_skipped = getattr(entry, 'put_side_skipped', False)

            call_done = call_stopped or call_expired or call_skipped
            put_done = put_stopped or put_expired or put_skipped

            # Check if this was a fully skipped entry (MKT-011)
            if call_skipped and put_skipped:
                lines.append("")
                lines.append(f"⏩ #{entry.entry_number} Skipped (MKT-011)")
                continue

            # Status icon logic:
            # - Green: all opened sides still active (skipped sides don't count against)
            # - Yellow: MKT-036 stop confirmation in progress
            # - Red: any opened side was stopped
            # A "done-bad" side is one that was stopped (actual loss).
            # A "done-ok" side is one that expired or was skipped (no negative impact).
            call_confirming = getattr(entry, 'call_breach_time', None) is not None
            put_confirming = getattr(entry, 'put_breach_time', None) is not None
            any_stopped = call_stopped or put_stopped
            # Check if all OPENED sides are done (exclude skipped sides)
            call_opened = not call_skipped
            put_opened = not put_skipped
            all_opened_done = (not call_opened or call_done) and (not put_opened or put_done)

            both_stopped = call_stopped and put_stopped
            if both_stopped:
                icon = "🔴"  # Both opened sides stopped (worst case)
            elif any_stopped:
                icon = "🟡"  # One side stopped (other active, expired, or skipped)
            elif call_confirming or put_confirming:
                icon = "🟡"  # MKT-036: Confirming stop
            else:
                icon = "🟢"  # All opened sides active or expired/skipped (good)

            # Strikes line — show "SKIP" for sides that weren't placed
            call_strike_str = (
                "SKIP"
                if call_skipped
                else f"{entry.short_call_strike:.0f}/{entry.long_call_strike:.0f}"
            )
            put_strike_str = (
                "SKIP"
                if put_skipped
                else f"{entry.short_put_strike:.0f}/{entry.long_put_strike:.0f}"
            )
            lines.append("")
            lines.append(
                f"{icon} #{entry.entry_number} "
                f"C:{call_strike_str} "
                f"P:{put_strike_str}"
            )

            # Credit, P&L, cushion line
            entry_pnl = self._get_saxo_pnl_for_entry(entry, positions=positions)
            pnl_sign = "+" if entry_pnl >= 0 else ""

            # Cushion percentages (same logic as get_detailed_position_status)
            call_value = entry.call_spread_value if not call_stopped else 0
            put_value = entry.put_spread_value if not put_stopped else 0

            if call_skipped:
                call_str = "SKIP"
            elif call_stopped:
                # MKT-033: Show salvage revenue if long was sold
                if getattr(entry, 'call_long_sold', False):
                    call_str = f"SAL+${getattr(entry, 'call_long_sold_revenue', 0):.0f}"
                else:
                    call_str = "STOP"
            elif call_expired:
                call_str = "EXP"
            elif getattr(entry, 'call_breach_time', None) is not None:
                # MKT-036: Stop confirmation in progress
                elapsed = (datetime.now() - entry.call_breach_time).total_seconds()
                call_str = f"⏳{elapsed:.0f}s"
            else:
                eff_call = self._get_effective_stop_level(entry, "call")
                call_pct = ((eff_call - call_value) / eff_call * 100) if eff_call > 0 else 0
                call_str = f"{call_pct:.0f}%"

            if put_skipped:
                put_str = "SKIP"
            elif put_stopped:
                # MKT-033: Show salvage revenue if long was sold
                if getattr(entry, 'put_long_sold', False):
                    put_str = f"SAL+${getattr(entry, 'put_long_sold_revenue', 0):.0f}"
                else:
                    put_str = "STOP"
            elif put_expired:
                put_str = "EXP"
            elif getattr(entry, 'put_breach_time', None) is not None:
                # MKT-036: Stop confirmation in progress
                elapsed = (datetime.now() - entry.put_breach_time).total_seconds()
                put_str = f"⏳{elapsed:.0f}s"
            else:
                eff_put = self._get_effective_stop_level(entry, "put")
                put_pct = ((eff_put - put_value) / eff_put * 100) if eff_put > 0 else 0
                put_str = f"{put_pct:.0f}%"

            lines.append(
                f"   ${entry.total_credit:.0f} cr | "
                f"{pnl_sign}${entry_pnl:.0f} | "
                f"C:{call_str} P:{put_str}"
            )

        # P&L summary (use already-fetched positions to avoid second API call)
        realized = self.daily_state.total_realized_pnl
        unrealized = sum(
            self._get_saxo_pnl_for_entry(e, positions=positions)
            for e in self.daily_state.active_entries
        )
        commission = self.daily_state.total_commission
        net_pnl = realized + unrealized - commission
        capital = self._calculate_capital_deployed()
        roc = (net_pnl / capital * 100) if capital > 0 else 0

        r_sign = "+" if realized >= 0 else ""
        u_sign = "+" if unrealized >= 0 else ""
        n_sign = "+" if net_pnl >= 0 else ""
        roc_sign = "+" if roc >= 0 else ""

        lines.append("")
        lines.append("━━━ P&L ━━━")
        lines.append(f"Real: {r_sign}${realized:.0f} | Unreal: {u_sign}${unrealized:.0f}")
        lines.append(f"Comm: -${commission:.0f}")
        lines.append(f"*Net: {n_sign}${net_pnl:.0f}* (ROC {roc_sign}{roc:.1f}%)")

        # Bottom info
        call_stops = self.daily_state.call_stops_triggered
        put_stops = self.daily_state.put_stops_triggered
        lines.append("")
        lines.append(f"Capital: ${capital:,.0f} | Stops: {call_stops}C/{put_stops}P")

        # Next entry (only if more pending)
        if self._next_entry_index < len(self.entry_times):
            next_time = self.entry_times[self._next_entry_index]
            lines.append(f"Next: #{self._next_entry_index + 1} @ {next_time.strftime('%I:%M %p')}")

        # Early close tracking (only when active)
        if self.early_close_enabled and self._early_close_triggered:
            ec_pnl = self._early_close_pnl or 0
            ec_time = self._early_close_time.strftime('%I:%M %p') if self._early_close_time else 'N/A'
            lines.append(f"Early Close: TRIGGERED @ {ec_time} (${ec_pnl:.0f})")
        elif (self.early_close_enabled and
              self._next_entry_index >= len(self.entry_times) and
              len(self.daily_state.active_entries) > 0):
            threshold_pct = self.early_close_roc_threshold * 100
            lines.append(f"Early Close: {roc_sign}{roc:.1f}% / {threshold_pct:.0f}% target")

        return "\n".join(lines)

    # =========================================================================
    # TELEGRAM HISTORICAL DATA COMMANDS
    # =========================================================================

    def build_telegram_lastday(self) -> str:
        """
        Build a formatted Telegram message showing the most recent complete trading day.

        The Daily Summary tab only gets rows AFTER settlement (post 4 PM ET),
        so the last row is always the most recent complete day — no special
        weekend/holiday logic needed.

        Returns:
            str: Formatted Markdown message for Telegram
        """
        try:
            last_day = self.trade_logger.get_last_daily_summary()
            if not last_day:
                return "No trading data available yet."

            date_str = last_day.get("Date", "Unknown")

            # Market context
            spx_close = last_day.get("SPX Close", 0) or 0
            vix_close = last_day.get("VIX Close", 0) or 0

            # Activity
            entries = last_day.get("Entries Completed", 0) or 0
            skipped = last_day.get("Entries Skipped", 0) or 0
            full_ics = last_day.get("Full ICs", 0) or 0
            one_sided = last_day.get("One-Sided Entries", 0) or 0

            # Stops
            call_stops = last_day.get("Call Stops", 0) or 0
            put_stops = last_day.get("Put Stops", 0) or 0
            double_stops = last_day.get("Double Stops", 0) or 0

            # P&L
            total_credit = last_day.get("Total Credit ($)", 0) or 0
            expired_credits = last_day.get("Expired Credits ($)", 0) or 0
            stop_debits = last_day.get("Stop Loss Debits ($)", 0) or 0
            long_salvage = last_day.get("Long Salvage ($)", 0) or 0
            commission = last_day.get("Commission ($)", 0) or 0
            daily_pnl = last_day.get("Daily P&L ($)", 0) or 0
            roc = last_day.get("Return on Capital (%)", 0) or 0
            capital = last_day.get("Capital Deployed ($)", 0) or 0

            # Cumulative
            cum_pnl = last_day.get("Cumulative P&L ($)", 0) or 0

            # Early close
            early_close = last_day.get("Early Close", "No")
            early_close_time = last_day.get("Early Close Time", "")

            # Format P&L
            pnl_icon = "\U0001f7e2" if daily_pnl >= 0 else "\U0001f534"
            pnl_str = f"+${daily_pnl:,.0f}" if daily_pnl >= 0 else f"-${abs(daily_pnl):,.0f}"
            cum_str = f"+${cum_pnl:,.0f}" if cum_pnl >= 0 else f"-${abs(cum_pnl):,.0f}"

            lines = [
                f"\U0001f4c5 *HYDRA* | {date_str}",
                "",
                f"SPX {spx_close:,.2f}  |  VIX {vix_close:.2f}",
                "",
                "\u2501\u2501\u2501 Activity \u2501\u2501\u2501",
                f"Entries: {entries} placed, {skipped} skipped",
                f"Full ICs: {full_ics}  |  One-sided: {one_sided}",
                f"Stops: {call_stops}C / {put_stops}P / {double_stops}D",
                "",
                "\u2501\u2501\u2501 P&L Breakdown \u2501\u2501\u2501",
                f"Credit collected: ${total_credit:,.0f}",
                f"Expired (profit): ${expired_credits:,.0f}",
                f"Stop debits: -${abs(stop_debits):,.0f}",
            ]
            # MKT-033: Show long salvage revenue if any
            if long_salvage > 0:
                lines.append(f"Long salvage: +${long_salvage:,.0f}")
            lines += [
                f"Commission: -${abs(commission):,.0f}",
                "",
                f"{pnl_icon} *Net P&L: {pnl_str}*",
                f"ROC: {roc:.1f}%  |  Capital: ${capital:,.0f}",
            ]

            if str(early_close).lower() == "yes" and early_close_time:
                lines.append(f"Early Close: {early_close_time}")

            lines.append("")
            lines.append(f"Cumulative: {cum_str}")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Failed to build /lastday message: %s", e)
            return "Failed to retrieve last day data. Try again shortly."

    def build_telegram_account(self) -> str:
        """
        Build a formatted Telegram message showing lifetime HYDRA performance.

        Reads ALL rows from Daily Summary tab and aggregates.
        Falls back to hydra_metrics.json if Sheets unavailable.

        Returns:
            str: Formatted Markdown message for Telegram
        """
        try:
            all_days = self.trade_logger.get_all_daily_summaries()
            metrics = self.cumulative_metrics or {}

            if not all_days and not metrics:
                return "No trading history available yet."

            if all_days:
                total_days = len(all_days)
                first_date = all_days[0].get("Date", "N/A")
                last_date = all_days[-1].get("Date", "N/A")

                total_entries = sum(
                    (d.get("Entries Completed", 0) or 0) for d in all_days
                )
                total_credit = sum(
                    (d.get("Total Credit ($)", 0) or 0) for d in all_days
                )
                total_commission = sum(
                    (d.get("Commission ($)", 0) or 0) for d in all_days
                )
                total_call_stops = sum(
                    (d.get("Call Stops", 0) or 0) for d in all_days
                )
                total_put_stops = sum(
                    (d.get("Put Stops", 0) or 0) for d in all_days
                )
                total_stops = total_call_stops + total_put_stops
                total_double_stops = sum(
                    (d.get("Double Stops", 0) or 0) for d in all_days
                )

                daily_pnls = [(d.get("Daily P&L ($)", 0) or 0) for d in all_days]
                winning_days = sum(1 for p in daily_pnls if p > 0)
                losing_days = sum(1 for p in daily_pnls if p < 0)
                breakeven_days = total_days - winning_days - losing_days

                cum_pnl = (all_days[-1].get("Cumulative P&L ($)", 0) or 0)
                avg_daily = cum_pnl / total_days if total_days > 0 else 0

                best_day_pnl = max(daily_pnls) if daily_pnls else 0
                worst_day_pnl = min(daily_pnls) if daily_pnls else 0
                best_idx = daily_pnls.index(best_day_pnl)
                worst_idx = daily_pnls.index(worst_day_pnl)
                best_date = all_days[best_idx].get("Date", "N/A")
                worst_date = all_days[worst_idx].get("Date", "N/A")

                win_rate = (winning_days / total_days * 100) if total_days > 0 else 0
                annualized = (all_days[-1].get("Annualized Return (%)", 0) or 0)
                cum_roc = (all_days[-1].get("Cumulative ROC (%)", 0) or 0)
            else:
                # Fallback to metrics file (less detail)
                total_days = (metrics.get("winning_days", 0)
                              + metrics.get("losing_days", 0))
                first_date = "N/A"
                last_date = metrics.get("last_updated", "N/A")
                total_entries = metrics.get("total_entries", 0)
                total_credit = metrics.get("total_credit_collected", 0)
                total_commission = 0
                total_stops = metrics.get("total_stops", 0)
                total_double_stops = metrics.get("double_stops", 0)
                winning_days = metrics.get("winning_days", 0)
                losing_days = metrics.get("losing_days", 0)
                breakeven_days = 0
                cum_pnl = metrics.get("cumulative_pnl", 0)
                avg_daily = cum_pnl / total_days if total_days > 0 else 0
                best_day_pnl = 0
                worst_day_pnl = 0
                best_date = "N/A"
                worst_date = "N/A"
                win_rate = (winning_days / total_days * 100) if total_days > 0 else 0
                annualized = 0
                cum_roc = 0

            def _fmt(val, prefix=""):
                """Format a P&L value with sign (e.g. +$280 or -$740)."""
                if val >= 0:
                    return f"{prefix}+${val:,.0f}"
                else:
                    return f"{prefix}-${abs(val):,.0f}"

            lines = [
                "\U0001f4ca *HYDRA* | Lifetime Performance",
                f"{first_date} to {last_date}",
                "",
                "\u2501\u2501\u2501 Record \u2501\u2501\u2501",
                f"Trading days: {total_days}",
                f"W / L / B: {winning_days} / {losing_days} / {breakeven_days}",
                f"Win rate: {win_rate:.0f}%",
                "",
                "\u2501\u2501\u2501 P&L \u2501\u2501\u2501",
                f"*Cumulative: {_fmt(cum_pnl)}*",
                f"Avg daily: {_fmt(avg_daily)}",
                f"Best day: {_fmt(best_day_pnl)} ({best_date})",
                f"Worst day: {_fmt(worst_day_pnl)} ({worst_date})",
                "",
                "\u2501\u2501\u2501 Activity \u2501\u2501\u2501",
                f"Total entries: {total_entries}",
                f"Total stops: {total_stops} ({total_double_stops} double)",
                f"Credit collected: ${total_credit:,.0f}",
                f"Commission paid: ${total_commission:,.0f}",
            ]

            if cum_roc or annualized:
                lines.append("")
                lines.append("\u2501\u2501\u2501 Returns \u2501\u2501\u2501")
                if cum_roc:
                    lines.append(f"Cumulative ROC: {cum_roc:.1f}%")
                if annualized:
                    lines.append(f"Annualized: {annualized:.0f}%")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Failed to build /account message: %s", e)
            return "Failed to retrieve account data. Try again shortly."

    # =========================================================================
    # TELEGRAM NEW COMMANDS (v1.7.0)
    # =========================================================================

    def build_telegram_status(self) -> str:
        """
        Build a formatted Telegram message showing current HYDRA bot status.

        All data is in-memory — zero I/O, zero API calls.

        Returns:
            str: Formatted Markdown message for Telegram
        """
        # State & mode
        state_str = self.state.value if self.state else "UNKNOWN"
        mode_str = "DRY-RUN" if self.dry_run else "LIVE"

        # Uptime
        uptime = datetime.now() - self._bot_start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes = remainder // 60
        uptime_str = f"{hours}h {minutes}m"

        # Market data
        spx = self.current_price
        vix = self.current_vix

        # Trend
        if self._current_trend:
            trend_str = self._current_trend.value.upper()
        else:
            trend_str = "PENDING"
        if self._last_ema_short > 0 and self._last_ema_long > 0:
            trend_detail = f"{trend_str} (EMA {self._last_ema_short:.0f}/{self._last_ema_long:.0f})"
        else:
            trend_detail = trend_str

        # Entries
        total_scheduled = len(self.entry_times)
        completed = self.daily_state.entries_completed
        skipped = self.daily_state.entries_skipped
        active_count = len(self.daily_state.active_entries)
        call_stops = self.daily_state.call_stops_triggered
        put_stops = self.daily_state.put_stops_triggered

        # P&L
        realized = self.daily_state.total_realized_pnl
        commission = self.daily_state.total_commission
        r_sign = "+" if realized >= 0 else ""

        # Next entry
        if self._next_entry_index < len(self.entry_times):
            next_time = self.entry_times[self._next_entry_index]
            time_fmt = '%I:%M:%S %p' if self.vix_gate_enabled else '%I:%M %p'
            next_str = f"#{self._next_entry_index + 1} @ {next_time.strftime(time_fmt)}"
        else:
            next_str = "All entries placed"

        # Filters
        if self.max_vix_entry >= 999:
            vix_detail = f"No limit (VIX {vix:.1f})"
        else:
            vix_open = "Open" if vix < self.max_vix_entry else "BLOCKED"
            vix_detail = f"{vix_open} ({vix:.1f} {'<' if vix < self.max_vix_entry else '>='} {self.max_vix_entry:.0f})"

        try:
            from shared.event_calendar import is_fomc_meeting_day, is_fomc_announcement_day
            if is_fomc_announcement_day():
                fomc = "ANNOUNCEMENT DAY (entries skipped)"
            elif is_fomc_meeting_day():
                fomc = "Day 1 (no announcement, normal trading)"
            elif is_fomc_t_plus_one():
                fomc = "T+1 (call-only MKT-038)"
            else:
                fomc = "No"
        except Exception:
            fomc = "No"

        # Early close status
        if self._early_close_triggered:
            ec_time = self._early_close_time.strftime('%I:%M %p') if self._early_close_time else "N/A"
            ec_str = f"TRIGGERED @ {ec_time}"
        elif self.early_close_enabled:
            ec_str = f"Armed ({self.early_close_roc_threshold * 100:.1f}% ROC)"
        else:
            ec_str = "Disabled"

        lines = [
            f"\U0001f916 *HYDRA* | Status",
            "",
            f"State: {state_str} ({mode_str})",
            f"Uptime: {uptime_str}",
            "",
            "\u2501\u2501\u2501 Market \u2501\u2501\u2501",
            f"SPX {spx:,.2f}  |  VIX {vix:.2f}",
            f"Trend: {trend_detail}",
            "",
            "\u2501\u2501\u2501 Today \u2501\u2501\u2501",
            f"Entries: {completed}/{total_scheduled} completed  |  {skipped} skipped",
            f"Active: {active_count}  |  Stops: {call_stops}C / {put_stops}P",
            f"Next: {next_str}",
        ]

        # MKT-034: Show VIX time shift status
        if self.vix_gate_enabled:
            if self._vix_gate_resolved:
                if self._vix_gate_start_slot > 0:
                    schedule_str = ", ".join(t.strftime('%H:%M:%S') for t in self.entry_times)
                    lines.append(f"VIX shift: slot {self._vix_gate_start_slot} [{schedule_str}]")
                else:
                    lines.append("VIX shift: default schedule (VIX < 20)")
            else:
                lines.append("VIX shift: pending (checking at next slot)")

        # MKT-031: Show scouting status when active
        if self._scouting_active and self.smart_entry_enabled:
            lines.append(
                f"Scout: #{self._scouting_entry_index + 1} "
                f"score {self._last_scout_score}/{self.scout_score_threshold}"
            )

        # MKT-033: Calculate salvage stats for display
        salvage_count = 0
        salvage_total = 0.0
        for entry in self.daily_state.entries:
            for s in ("call", "put"):
                if getattr(entry, f'{s}_long_sold', False):
                    salvage_count += 1
                    salvage_total += getattr(entry, f'{s}_long_sold_revenue', 0.0)

        lines += [
            "",
            "\u2501\u2501\u2501 P&L \u2501\u2501\u2501",
            f"Realized: {r_sign}${realized:.0f}  |  Commission: -${commission:.0f}",
        ]
        if salvage_count > 0:
            lines.append(f"Salvages: {salvage_count} longs for +${salvage_total:.0f}")
        lines += [
            "",
            "\u2501\u2501\u2501 Filters \u2501\u2501\u2501",
            f"VIX gate: {vix_detail}",
            f"FOMC: {fomc}",
            f"Early close: {ec_str}",
        ]

        return "\n".join(lines)

    def build_telegram_hermes(self) -> str:
        """
        Build a Telegram message with the most recent HERMES daily report.

        Tries today, yesterday, then 2 days back. Returns raw Markdown content
        — the Telegram handler sanitizes before sending.

        Returns:
            str: Raw report content with header, or "not available" message
        """
        now_et = get_us_market_time()

        for days_back in range(3):
            check_date = now_et.date() - timedelta(days=days_back)
            date_str = check_date.strftime("%Y-%m-%d")
            report_path = os.path.join("intel", "hermes", f"{date_str}.md")

            try:
                with open(report_path, "r") as f:
                    content = f.read()
                if content.strip():
                    return f"\U0001f4dd *HYDRA* | HERMES \u2014 {date_str}\n\n{content}"
            except (FileNotFoundError, IOError):
                continue

        return "No recent HERMES report available."

    def build_telegram_apollo(self) -> str:
        """
        Build a Telegram message with the most recent APOLLO morning briefing.

        Tries today, yesterday, then 2 days back. Returns raw Markdown content
        — the Telegram handler sanitizes before sending.

        Returns:
            str: Raw briefing content with header, or "not available" message
        """
        now_et = get_us_market_time()

        for days_back in range(3):
            check_date = now_et.date() - timedelta(days=days_back)
            date_str = check_date.strftime("%Y-%m-%d")
            report_path = os.path.join("intel", "apollo", f"{date_str}.md")

            try:
                with open(report_path, "r") as f:
                    content = f.read()
                if content.strip():
                    return f"\U0001f52d *HYDRA* | APOLLO \u2014 {date_str}\n\n{content}"
            except (FileNotFoundError, IOError):
                continue

        return "No recent APOLLO briefing available."

    def build_telegram_clio(self) -> str:
        """Build a Telegram message with the most recent CLIO weekly analysis.

        Searches intel/clio/ for the latest week_*.md report file.
        Returns raw Markdown content — the Telegram handler sanitizes before sending.
        """
        clio_dir = os.path.join("intel", "clio")
        try:
            files = sorted(
                [f for f in os.listdir(clio_dir) if f.startswith("week_") and f.endswith(".md")],
                reverse=True,
            )
        except (FileNotFoundError, IOError):
            return "No CLIO reports available."

        for filename in files[:3]:
            report_path = os.path.join(clio_dir, filename)
            try:
                with open(report_path, "r") as f:
                    content = f.read()
                if content.strip():
                    week_label = filename.replace("week_", "").replace(".md", "").replace("_", "-")
                    return f"\U0001f4dc *HYDRA* | CLIO \u2014 {week_label}\n\n{content}"
            except (FileNotFoundError, IOError):
                continue

        return "No CLIO weekly analysis available."

    def build_telegram_week(self) -> str:
        """
        Build a formatted Telegram message showing current week's trading summary.

        Reads from Google Sheets Daily Summary tab (timeout-protected).
        Falls back to previous week if current week has no data.

        Returns:
            str: Formatted Markdown message for Telegram
        """
        try:
            all_days = self.trade_logger.get_all_daily_summaries()
            if not all_days:
                return "No trading data available yet."

            now_et = get_us_market_time()
            today = now_et.date()

            # Find Monday of current week
            monday = today - timedelta(days=today.weekday())

            # Try current week, then previous week
            for week_offset in [0, -7]:
                week_monday = monday + timedelta(days=week_offset)
                week_friday = week_monday + timedelta(days=4)

                # Filter days to this week
                week_days = {}
                for d in all_days:
                    date_str = d.get("Date", "")
                    try:
                        d_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                        if week_monday <= d_date <= week_friday:
                            week_days[d_date] = d
                    except (ValueError, TypeError):
                        continue

                if week_days:
                    break
            else:
                return "No trading data available yet."

            # Build header
            week_label = week_monday.strftime("%b %-d")
            lines = [f"\U0001f4c5 *HYDRA* | Week of {week_label}", ""]

            # Per-day lines
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
            total_pnl = 0
            total_entries = 0
            total_stops = 0
            winning = 0
            losing = 0

            for i, day_name in enumerate(day_names):
                day_date = week_monday + timedelta(days=i)
                day_data = week_days.get(day_date)

                if day_data:
                    pnl = day_data.get("Daily P&L ($)", 0) or 0
                    entries = (day_data.get("Entries Completed", 0) or 0)
                    call_s = (day_data.get("Call Stops", 0) or 0)
                    put_s = (day_data.get("Put Stops", 0) or 0)
                    stops = call_s + put_s

                    total_pnl += pnl
                    total_entries += entries
                    total_stops += stops
                    if pnl > 0:
                        winning += 1
                    elif pnl < 0:
                        losing += 1

                    if pnl > 0:
                        icon = "\U0001f7e2"
                        pnl_str = f"+${pnl:,.0f}"
                    elif pnl < 0:
                        icon = "\U0001f534"
                        pnl_str = f"-${abs(pnl):,.0f}"
                    else:
                        icon = "\u26aa"
                        pnl_str = "$0"

                    lines.append(f"{day_name}: {icon} {pnl_str} ({entries} entries, {stops} stops)")
                elif day_date <= today:
                    lines.append(f"{day_name}: \u2014 (no data)")
                else:
                    lines.append(f"{day_name}: \u2014")

            # Week totals
            pnl_sign = "+" if total_pnl >= 0 else ""
            lines.append("")
            lines.append("\u2501\u2501\u2501 Week Total \u2501\u2501\u2501")
            lines.append(f"*Net P&L: {pnl_sign}${total_pnl:,.0f}*")
            lines.append(f"Entries: {total_entries}  |  Stops: {total_stops}")
            total_days = winning + losing
            win_rate = (winning / total_days * 100) if total_days > 0 else 0
            lines.append(f"Win rate: {win_rate:.0f}% ({winning}W / {losing}L)")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Failed to build /week message: %s", e)
            return "Failed to retrieve week data. Try again shortly."

    def build_telegram_entry(self, entry_num: int) -> str:
        """
        Build a formatted Telegram message showing details for a specific entry.

        Args:
            entry_num: 1-based entry number

        Returns:
            str: Formatted Markdown message for Telegram
        """
        entries = self.daily_state.entries
        if not entries:
            return "No entries placed today."

        if entry_num < 1 or entry_num > len(entries):
            return f"Entry #{entry_num} not found. Today has {len(entries)} entries so far."

        entry = entries[entry_num - 1]

        # Entry metadata
        if entry.entry_time:
            time_str = entry.entry_time.strftime('%I:%M %p ET')
        else:
            time_str = "pending"

        # Trend and type
        trend = entry.trend_signal.value.upper() if hasattr(entry, 'trend_signal') and entry.trend_signal else "N/A"
        if getattr(entry, 'call_only', False):
            entry_type = "Call Only"
        elif getattr(entry, 'put_only', False):
            entry_type = "Put Only"
        else:
            entry_type = "Full IC"

        lines = [
            f"\U0001f50d *HYDRA* | Entry #{entry_num}",
            "",
            f"Placed: {time_str}",
            f"Trend: {trend}  |  Type: {entry_type}",
        ]

        # Strikes
        call_width = abs(entry.long_call_strike - entry.short_call_strike)
        put_width = abs(entry.short_put_strike - entry.long_put_strike)

        lines.append("")
        lines.append("\u2501\u2501\u2501 Strikes \u2501\u2501\u2501")

        call_skipped = getattr(entry, 'call_side_skipped', False)
        put_skipped = getattr(entry, 'put_side_skipped', False)

        if not call_skipped:
            lines.append(f"Call: Short {entry.short_call_strike:.0f} / Long {entry.long_call_strike:.0f} ({call_width:.0f}pt)")
        else:
            lines.append("Call: SKIPPED")

        if not put_skipped:
            lines.append(f"Put: Short {entry.short_put_strike:.0f} / Long {entry.long_put_strike:.0f} ({put_width:.0f}pt)")
        else:
            lines.append("Put: SKIPPED")

        # Credits
        lines.append("")
        lines.append("\u2501\u2501\u2501 Credits \u2501\u2501\u2501")
        if not call_skipped:
            lines.append(f"Call: ${entry.call_spread_credit:.0f}")
        if not put_skipped:
            lines.append(f"Put: ${entry.put_spread_credit:.0f}")
        lines.append(f"Total: ${entry.total_credit:.0f}")

        # Fill prices
        lines.append("")
        lines.append("\u2501\u2501\u2501 Fill Prices \u2501\u2501\u2501")
        if not call_skipped:
            sc = f"${entry.short_call_fill_price:.2f}" if entry.short_call_fill_price > 0 else "pending"
            lc = f"${entry.long_call_fill_price:.2f}" if entry.long_call_fill_price > 0 else "pending"
            lines.append(f"SC: {sc}  LC: {lc}")
        if not put_skipped:
            sp = f"${entry.short_put_fill_price:.2f}" if entry.short_put_fill_price > 0 else "pending"
            lp = f"${entry.long_put_fill_price:.2f}" if entry.long_put_fill_price > 0 else "pending"
            lines.append(f"SP: {sp}  LP: {lp}")

        # Status per side
        lines.append("")
        lines.append("\u2501\u2501\u2501 Status \u2501\u2501\u2501")

        for side, label in [("call", "Call"), ("put", "Put")]:
            skipped = getattr(entry, f'{side}_side_skipped', False)
            stopped = getattr(entry, f'{side}_side_stopped', False)
            expired = getattr(entry, f'{side}_side_expired', False)
            base_stop = getattr(entry, f'{side}_side_stop', 0)
            eff_stop = self._get_effective_stop_level(entry, side)
            spread_value = getattr(entry, f'{side}_spread_value', 0)

            if skipped:
                lines.append(f"{label}: SKIPPED")
            elif stopped:
                # MKT-033: Show salvage info if long was sold
                long_sold = getattr(entry, f'{side}_long_sold', False)
                if long_sold:
                    salvage_rev = getattr(entry, f'{side}_long_sold_revenue', 0)
                    lines.append(f"{label}: STOPPED + LONG SALVAGED +${salvage_rev:.0f}")
                else:
                    lines.append(f"{label}: STOPPED (stop @ ${base_stop:.0f})")
            elif expired:
                lines.append(f"{label}: EXPIRED")
            elif eff_stop > 0:
                cushion = (eff_stop - spread_value) / eff_stop * 100 if eff_stop > 0 else 0
                decay_tag = f" [decay→${eff_stop:.0f}]" if eff_stop > base_stop + 1 else ""
                lines.append(f"{label}: {cushion:.0f}% cushion (stop @ ${base_stop:.0f}{decay_tag})")
            else:
                lines.append(f"{label}: \u2014")

        # P&L
        pnl = entry.unrealized_pnl if hasattr(entry, 'unrealized_pnl') else 0
        pnl_sign = "+" if pnl >= 0 else ""
        lines.append("")
        lines.append(f"P&L: {pnl_sign}${pnl:.0f}")

        return "\n".join(lines)

    def build_telegram_stops(self) -> str:
        """
        Build a formatted Telegram message showing stop loss analysis.

        Today's data from in-memory daily_state. Historical from Google Sheets.

        Returns:
            str: Formatted Markdown message for Telegram
        """
        lines = ["\U0001f6d1 *HYDRA* | Stop Analysis"]

        # Today's stops
        call_stops = self.daily_state.call_stops_triggered
        put_stops = self.daily_state.put_stops_triggered
        double_stops = self.daily_state.double_stops

        lines.append("")
        lines.append("\u2501\u2501\u2501 Today \u2501\u2501\u2501")
        lines.append(f"Stops: {call_stops}C / {put_stops}P / {double_stops}D")
        avoided = self.daily_state.stops_avoided_mkt036
        if avoided > 0:
            lines.append(f"MKT-036 recoveries: {avoided} (stops avoided by timer)")

        # Detail per stopped entry
        has_stops_today = False
        for entry in self.daily_state.entries:
            call_stopped = entry.call_side_stopped
            put_stopped = entry.put_side_stopped

            if call_stopped or put_stopped:
                has_stops_today = True
                sides = []
                if call_stopped:
                    sides.append("Call")
                if put_stopped:
                    sides.append("Put")
                sides_str = " + ".join(sides)
                lines.append("")
                lines.append(f"#{entry.entry_number} {sides_str} STOPPED")
                if call_stopped:
                    salvage_str = ""
                    if getattr(entry, 'call_long_sold', False):
                        salvage_str = f"  SALVAGED +${getattr(entry, 'call_long_sold_revenue', 0):.0f}"
                    lines.append(f"  Call credit: ${entry.call_spread_credit:.0f}  |  Stop: ${entry.call_side_stop:.0f}{salvage_str}")
                if put_stopped:
                    salvage_str = ""
                    if getattr(entry, 'put_long_sold', False):
                        salvage_str = f"  SALVAGED +${getattr(entry, 'put_long_sold_revenue', 0):.0f}"
                    lines.append(f"  Put credit: ${entry.put_spread_credit:.0f}  |  Stop: ${entry.put_side_stop:.0f}{salvage_str}")

        if not has_stops_today and (call_stops + put_stops) == 0:
            lines.append("No stops triggered today.")

        # MKT-033: Salvage summary
        salvage_count = 0
        salvage_total = 0.0
        for entry in self.daily_state.entries:
            for s in ("call", "put"):
                if getattr(entry, f'{s}_long_sold', False):
                    salvage_count += 1
                    salvage_total += getattr(entry, f'{s}_long_sold_revenue', 0.0)
        if salvage_count > 0:
            lines.append("")
            lines.append(f"Salvages: {salvage_count} longs sold for +${salvage_total:.0f}")

        # Historical from Sheets
        try:
            all_days = self.trade_logger.get_all_daily_summaries()
            if all_days:
                total_days = len(all_days)
                hist_call = sum((d.get("Call Stops", 0) or 0) for d in all_days)
                hist_put = sum((d.get("Put Stops", 0) or 0) for d in all_days)
                hist_double = sum((d.get("Double Stops", 0) or 0) for d in all_days)
                hist_total = hist_call + hist_put

                lines.append("")
                lines.append(f"\u2501\u2501\u2501 Lifetime ({total_days} days) \u2501\u2501\u2501")
                lines.append(f"Total: {hist_call}C / {hist_put}P / {hist_double}D")

                if hist_total > 0:
                    avg_per_day = hist_total / total_days
                    call_pct = hist_call / hist_total * 100
                    put_pct = hist_put / hist_total * 100
                    lines.append(f"Avg stops/day: {avg_per_day:.1f}")
                    lines.append(f"Call: {hist_call} ({call_pct:.0f}%)  |  Put: {hist_put} ({put_pct:.0f}%)")
        except Exception as e:
            logger.error("Failed to get historical stop data: %s", e)
            lines.append("")
            lines.append("Historical data temporarily unavailable.")

        return "\n".join(lines)

    def build_telegram_config(self) -> str:
        """
        Build a formatted Telegram message showing current HYDRA configuration.

        All data from in-memory attributes — zero I/O.

        Returns:
            str: Formatted Markdown message for Telegram
        """
        # Entry schedule
        time_fmt = '%I:%M:%S' if self.vix_gate_enabled else '%I:%M'
        schedule = ", ".join(t.strftime(time_fmt) for t in self.entry_times)
        contracts = self.strategy_config.get("contracts_per_entry", 1)
        mode = "DRY-RUN" if self.dry_run else "LIVE"

        # Credits (convert cents to dollars)
        min_credit_call = self.min_viable_credit_per_side / 100
        min_credit_put = self.min_viable_credit_put_side / 100

        # Stop buffer
        stop_buffer_dollars = self.strategy_config.get("call_stop_buffer", 0.10)
        put_buffer_dollars = self.strategy_config.get("put_stop_buffer", stop_buffer_dollars)
        if put_buffer_dollars != stop_buffer_dollars:
            stop_buffer_str = f"call +${stop_buffer_dollars:.2f} / put +${put_buffer_dollars:.2f}"
        else:
            stop_buffer_str = f"+${stop_buffer_dollars:.2f}"

        # Early close
        if self.early_close_enabled:
            ec_str = f"{self.early_close_roc_threshold * 100:.1f}% ROC"
        else:
            ec_str = "Disabled"

        # Hold check
        hold_str = "Enabled" if self.hold_check_enabled else "Disabled"

        lines = [
            f"\u2699\ufe0f *HYDRA* | Config (v{HYDRA_VERSION})",
            "",
            "\u2501\u2501\u2501 Entries \u2501\u2501\u2501",
            f"Schedule: {schedule}",
            f"Contracts: {contracts}  |  Mode: {mode}",
            "",
            "\u2501\u2501\u2501 Strikes \u2501\u2501\u2501",
            f"Starting OTM: Call {self.call_starting_otm_multiplier}x  |  Put {self.put_starting_otm_multiplier}x",
            f"Spread floors: Call {self.call_min_spread_width}pt  |  Put {self.put_min_spread_width}pt",
            f"Spread cap: {self.max_spread_width}pt (VIX x {self.spread_vix_multiplier})",
            "",
            "\u2501\u2501\u2501 Credits \u2501\u2501\u2501",
            f"Min credit: Call ${min_credit_call:.2f}  |  Put ${min_credit_put:.2f}",
            "",
            "\u2501\u2501\u2501 Risk \u2501\u2501\u2501",
            f"Max VIX: {'No limit' if self.max_vix_entry >= 999 else f'{self.max_vix_entry:.0f}'}",
            f"Stop: credit {stop_buffer_str}",
            "",
            "\u2501\u2501\u2501 Exits \u2501\u2501\u2501",
            f"Early close: {ec_str}",
            f"Hold check: {hold_str}",
            "",
            "\u2501\u2501\u2501 Smart Entry (MKT-031) \u2501\u2501\u2501",
            f"Enabled: {'Yes' if self.smart_entry_enabled else 'No'}",
        ]

        if self.smart_entry_enabled:
            lines.append(f"Window: {self.scout_window_minutes}min | Threshold: {self.scout_score_threshold}")

        # MKT-035: Down day filter
        lines.extend([
            "",
            "\u2501\u2501\u2501 Down Day (MKT-035) \u2501\u2501\u2501",
            f"Enabled: {'Yes' if self.downday_callonly_enabled else 'No'}",
            f"Threshold: {self.downday_threshold_pct * 100:.1f}% below open",
            f"Theo put: ${self.downday_theoretical_put_credit / 100:.2f}",
        ])
        if self._conditional_entry_times:
            cond_str = ", ".join(t.strftime('%H:%M') for t in self._conditional_entry_times)
            lines.append(f"Conditional: {cond_str} (call-only on down days)")

        # MKT-036: Stop confirmation timer
        lines.extend([
            "",
            "\u2501\u2501\u2501 Stop Confirmation (MKT-036) \u2501\u2501\u2501",
            f"Enabled: {'Yes' if self.stop_confirmation_enabled else 'No'}",
            f"Window: {self.stop_confirmation_seconds}s",
        ])

        if self.vix_gate_enabled:
            lines.extend([
                "",
                "\u2501\u2501\u2501 VIX Time Shift (MKT-034) \u2501\u2501\u2501",
                f"Thresholds: {self.vix_medium_threshold:.0f} / {self.vix_high_threshold:.0f}",
                f"Floor: slot 2 (12:14:30)",
            ])

        return "\n".join(lines)

    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """
        Get dashboard metrics with HYDRA specific fields.

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
            if isinstance(entry, HydraIronCondorEntry) and entry.is_one_sided:
                one_sided += 1
            else:
                full_ics += 1

            # Count trend signals from actual trend_signal field
            if isinstance(entry, HydraIronCondorEntry) and entry.trend_signal:
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
        metrics['stops_avoided_mkt036'] = self.daily_state.stops_avoided_mkt036

        return metrics

    def get_daily_summary(self) -> Dict:
        """
        Get daily summary with HYDRA specific fields.

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
            if isinstance(entry, HydraIronCondorEntry) and entry.is_one_sided:
                one_sided += 1
            else:
                full_ics += 1

            # Count trend signals from actual trend_signal field
            if isinstance(entry, HydraIronCondorEntry) and entry.trend_signal:
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

        # MKT-033: Long leg salvage revenue
        long_salvage_revenue = 0.0
        for entry in self.daily_state.entries:
            if isinstance(entry, HydraIronCondorEntry):
                long_salvage_revenue += getattr(entry, 'call_long_sold_revenue', 0.0)
                long_salvage_revenue += getattr(entry, 'put_long_sold_revenue', 0.0)
        summary['long_salvage_revenue'] = long_salvage_revenue

        # MKT-036: Stop confirmation stats
        summary['stops_avoided_mkt036'] = self.daily_state.stops_avoided_mkt036

        return summary

    # =========================================================================
    # OVERRIDE: Logging for trend-following entries
    # =========================================================================

    def log_account_summary(self):
        """
        Log HYDRA account summary to Google Sheets dashboard.

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
                # HYDRA specific: Trend data (Fix #62)
                "current_trend": metrics.get("current_trend", "NEUTRAL"),
                "ema_20": metrics.get("ema_20"),
                "ema_40": metrics.get("ema_40"),
                # Risk
                "daily_loss_percent": metrics["pnl_percent"],
                "circuit_breaker": metrics["circuit_breaker_open"],
                # State
                "state": metrics["state"],
                # MKT-018: Early close status
                "early_close": "TRIGGERED" if self._early_close_triggered else (
                    "Tracking" if (self._next_entry_index >= len(self.entry_times) and len(self.daily_state.active_entries) > 0) else "Waiting"
                ),
                # MKT-033: Long salvage
                "long_salvage_count": sum(
                    (1 if getattr(e, 'call_long_sold', False) else 0) +
                    (1 if getattr(e, 'put_long_sold', False) else 0)
                    for e in self.daily_state.entries
                ),
                "long_salvage_revenue": sum(
                    getattr(e, 'call_long_sold_revenue', 0.0) +
                    getattr(e, 'put_long_sold_revenue', 0.0)
                    for e in self.daily_state.entries
                ),
            })
        except Exception as e:
            logger.error(f"Failed to log HYDRA account summary: {e}")

    def log_performance_metrics(self):
        """
        Log HYDRA performance metrics to Google Sheets.

        Overrides parent to include HYDRA-specific fields:
        - full_ics / one_sided_entries counts
        - trend_overrides / credit_gate_skips counts

        Fix #69: Parent's log_performance_metrics() builds a NEW dict that
        doesn't include HYDRA-specific keys from get_dashboard_metrics().
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
                    # HYDRA specific: entry type counts
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
                    # HYDRA specific: trend stats
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
                    # MKT-018: Early close tracking
                    "early_close_triggered": self._early_close_triggered,
                    "early_close_time": self._early_close_time.strftime('%H:%M') if self._early_close_time else "",
                    # MKT-033: Long salvage
                    "long_salvage_revenue": sum(
                        getattr(e, 'call_long_sold_revenue', 0.0) +
                        getattr(e, 'put_long_sold_revenue', 0.0)
                        for e in self.daily_state.entries
                    ),
                },
                saxo_client=self.client
            )
        except Exception as e:
            logger.error(f"Failed to log HYDRA performance metrics: {e}")

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
                is_hydra = isinstance(entry, HydraIronCondorEntry)
                trend_signal = entry.trend_signal.value.upper() if is_hydra and entry.trend_signal else "NEUTRAL"

                # Call side
                call_skipped = getattr(entry, 'call_side_skipped', False)
                if not call_skipped:
                    is_early_closed = getattr(entry, 'early_closed', False)
                    if entry.call_side_stopped:
                        status = "STOPPED"
                        current_value = entry.call_side_stop
                        pnl = -(entry.call_side_stop - entry.call_spread_credit)
                    elif getattr(entry, 'call_side_expired', False):
                        status = "EARLY_CLOSED" if is_early_closed else "EXPIRED"
                        current_value = 0
                        pnl = entry.call_spread_credit
                    else:
                        status = "ACTIVE"
                        current_value = entry.call_spread_value if entry.call_spread_value else 0
                        pnl = entry.call_spread_credit - current_value

                    eff_stop = self._get_effective_stop_level(entry, "call")
                    stop_level = eff_stop if eff_stop else 0
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
                    is_early_closed = getattr(entry, 'early_closed', False)
                    if entry.put_side_stopped:
                        status = "STOPPED"
                        current_value = entry.put_side_stop
                        pnl = -(entry.put_side_stop - entry.put_spread_credit)
                    elif getattr(entry, 'put_side_expired', False):
                        status = "EARLY_CLOSED" if is_early_closed else "EXPIRED"
                        current_value = 0
                        pnl = entry.put_spread_credit
                    else:
                        status = "ACTIVE"
                        current_value = entry.put_spread_value if entry.put_spread_value else 0
                        pnl = entry.put_spread_credit - current_value

                    eff_stop = self._get_effective_stop_level(entry, "put")
                    stop_level = eff_stop if eff_stop else 0
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
            logger.error(f"Failed to log HYDRA position snapshot: {e}")

    def _log_entry(self, entry):
        """
        Log entry to Google Sheets with trend info.

        Overrides parent to:
        - Use "HYDRA" instead of "MEIC"
        - Include trend signal in the action
        - Handle one-sided entries (show only placed side's strikes)

        Fix #49: Use override_reason to determine correct tag:
        - MKT-011: Credit gate triggered override
        - MKT-010: Illiquidity fallback triggered override
        - Trend: EMA trend filter determined one-sided
        """
        try:
            # Determine entry type and format strikes accordingly
            is_hydra_entry = isinstance(entry, HydraIronCondorEntry)

            if is_hydra_entry and entry.call_only:
                # Call spread only
                strike_str = f"C:{entry.short_call_strike}/{entry.long_call_strike}"
                entry_type = "Call Spread"
                # Fix #49: Use override_reason for correct tag
                override_reason = getattr(entry, 'override_reason', None)
                if override_reason == "mkt-035":
                    trend_tag = "[MKT-035]"
                elif override_reason == "mkt-038":
                    trend_tag = "[MKT-038]"
                elif override_reason == "mkt-011":
                    trend_tag = "[MKT-011]"
                elif override_reason == "mkt-010":
                    trend_tag = "[MKT-010]"
                elif override_reason == "mkt-040":
                    trend_tag = "[MKT-040]"
                elif override_reason == "base-downday":
                    trend_tag = "[BASE-DOWNDAY]"
                else:
                    trend_tag = "[BEARISH]"
            elif is_hydra_entry and entry.put_only:
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

            # Per-side credits: only include credit for sides that were actually placed
            log_call_credit = entry.call_spread_credit if not getattr(entry, 'call_side_skipped', False) else None
            log_put_credit = entry.put_spread_credit if not getattr(entry, 'put_side_skipped', False) else None

            self.trade_logger.log_trade(
                action=f"HYDRA Entry #{entry.entry_number} {trend_tag}",
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
                trade_reason=f"Entry | Trend: {trend_tag} | Credit: ${entry.total_credit:.2f}{ema_info}",
                call_credit=log_call_credit,
                put_credit=log_put_credit,
            )

            logger.info(
                f"Entry #{entry.entry_number} {trend_tag} logged to Sheets: "
                f"SPX={self.current_price:.2f}, Credit=${entry.total_credit:.2f}, "
                f"Type={entry_type}, Strikes: {strike_str}"
            )
        except Exception as e:
            logger.error(f"Failed to log entry: {e}")

    # =========================================================================
    # STATE FILE OVERRIDES (HYDRA uses separate state file from MEIC)
    # =========================================================================
    # CRITICAL: These overrides ensure HYDRA uses its own state file
    # (hydra_state.json) instead of sharing with MEIC (meic_state.json).
    # This is necessary when both bots may run simultaneously.

    def _save_state_to_disk(self):
        """
        Save current daily state to disk for crash recovery.

        OVERRIDE: Uses HYDRA_STATE_FILE instead of MEIC's STATE_FILE.
        Also saves trend-following specific fields (call_only, put_only, trend_signal).
        """
        try:
            state_data = {
                "bot_type": "hydra",  # Identify this as HYDRA state
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
                # Fix #55/#56/#57: HYDRA specific counters
                "one_sided_entries": self.daily_state.one_sided_entries,
                "trend_overrides": self.daily_state.trend_overrides,
                "credit_gate_skips": self.daily_state.credit_gate_skips,
                # MKT-036: Stop confirmation avoided counter
                "stops_avoided_mkt036": self.daily_state.stops_avoided_mkt036,
                # MKT-018: Early close state
                # getattr: these are set post-super().__init__(); state save during
                # base-class recovery may fire before they exist.
                "early_close_triggered": getattr(self, '_early_close_triggered', False),
                "early_close_time": getattr(self, '_early_close_time', None) and self._early_close_time.isoformat() if getattr(self, '_early_close_time', None) else None,
                "early_close_pnl": getattr(self, '_early_close_pnl', None),
                # MKT-021: Pre-entry ROC gate state
                "roc_gate_triggered": getattr(self, '_roc_gate_triggered', False),
                # MKT-034: VIX gate state
                "vix_gate_resolved": getattr(self, '_vix_gate_resolved', False),
                "vix_gate_start_slot": getattr(self, '_vix_gate_start_slot', 0),
                # Dashboard: entry schedule for pending slot display
                "entry_schedule": {
                    "base": [t.strftime('%H:%M') for t in self.entry_times[:self._base_entry_count]],
                    "conditional": [t.strftime('%H:%M') for t in self._conditional_entry_times],
                },
                # Dashboard: config flags for banner display
                # getattr: fomc_t1_callonly_enabled is set post-super(); if state save is
                # triggered during base-class recovery it may not exist yet.
                "fomc_t1_callonly_enabled": getattr(self, 'fomc_t1_callonly_enabled', True),
                "downday_callonly_enabled": getattr(self, 'downday_callonly_enabled', True),
                "entries": []
            }

            # Serialize each entry with HYDRA-specific fields
            for entry in self.daily_state.entries:
                is_hydra_entry = isinstance(entry, HydraIronCondorEntry)
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
                    "total_credit": entry.total_credit,
                    "call_spread_credit": entry.call_spread_credit,
                    "put_spread_credit": entry.put_spread_credit,
                    # Stops (base + effective with MKT-042 decay applied)
                    "call_side_stop": entry.call_side_stop,
                    "put_side_stop": entry.put_side_stop,
                    "effective_call_stop": self._get_effective_stop_level(entry, "call"),
                    "effective_put_stop": self._get_effective_stop_level(entry, "put"),
                    # Actual stop debit (for dashboard per-entry P&L accuracy)
                    "actual_call_stop_debit": entry.actual_call_stop_debit,
                    "actual_put_stop_debit": entry.actual_put_stop_debit,
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
                    # HYDRA specific: trend-following fields
                    # FIX #43: Use getattr to handle both HydraIronCondorEntry and
                    # dynamically-added attributes on IronCondorEntry (from recovery)
                    "call_only": getattr(entry, 'call_only', False),
                    "put_only": getattr(entry, 'put_only', False),
                    "trend_signal": getattr(entry, 'trend_signal', None).value if getattr(entry, 'trend_signal', None) else None,
                    # Fix #49: Track override reason for correct logging after recovery
                    "override_reason": getattr(entry, 'override_reason', None),
                    # Skip tracking: reason when entry is fully skipped
                    "skip_reason": getattr(entry, 'skip_reason', ""),
                    # Fill prices (for /entry display after restart)
                    "short_call_fill_price": entry.short_call_fill_price,
                    "long_call_fill_price": entry.long_call_fill_price,
                    "short_put_fill_price": entry.short_put_fill_price,
                    "long_put_fill_price": entry.long_put_fill_price,
                    # Fix #52: Contract count for multi-contract support
                    "contracts": entry.contracts,
                    # Fix #59: EMA values at entry time for Trades tab logging
                    "ema_20_at_entry": getattr(entry, 'ema_20_at_entry', None),
                    "ema_40_at_entry": getattr(entry, 'ema_40_at_entry', None),
                    # MKT-018: Early close marker
                    "early_closed": getattr(entry, 'early_closed', False),
                    # MKT-033: Long leg salvage tracking
                    "call_long_sold": getattr(entry, 'call_long_sold', False),
                    "put_long_sold": getattr(entry, 'put_long_sold', False),
                    "call_long_sold_revenue": getattr(entry, 'call_long_sold_revenue', 0.0),
                    "put_long_sold_revenue": getattr(entry, 'put_long_sold_revenue', 0.0),
                    # Stop timestamps (for dashboard stop markers)
                    "call_stop_time": entry.call_stop_time,
                    "put_stop_time": entry.put_stop_time,
                    # MKT-036: Stop confirmation timer
                    "call_breach_time": entry.call_breach_time.isoformat() if getattr(entry, 'call_breach_time', None) else None,
                    "put_breach_time": entry.put_breach_time.isoformat() if getattr(entry, 'put_breach_time', None) else None,
                    "call_breach_count": getattr(entry, 'call_breach_count', 0),
                    "put_breach_count": getattr(entry, 'put_breach_count', 0),
                    # MKT-041: Cushion recovery danger flags
                    "call_hit_danger": getattr(entry, 'call_hit_danger', False),
                    "put_hit_danger": getattr(entry, 'put_hit_danger', False),
                    # Dashboard: live spread values for cushion display
                    "call_spread_value": entry.call_spread_value if not entry.call_side_stopped else 0,
                    "put_spread_value": entry.put_spread_value if not entry.put_side_stopped else 0,
                    # Dashboard: surviving long leg value after MKT-025 stop (long stays open)
                    # Value = long_price * 100 * contracts (what we'd get if sold now)
                    # Fix #85: Only populate when short_only_stop=True (MKT-025 mode).
                    # When both legs are closed on stop, long sale proceeds are already
                    # included in actual_stop_debit — showing long_value would double-count.
                    "call_long_value": (entry.long_call_price * 100 * entry.contracts
                                        if self.short_only_stop and entry.call_side_stopped
                                        and not getattr(entry, 'call_long_sold', False) and entry.long_call_uic
                                        else 0),
                    "put_long_value": (entry.long_put_price * 100 * entry.contracts
                                       if self.short_only_stop and entry.put_side_stopped
                                       and not getattr(entry, 'put_long_sold', False) and entry.long_put_uic
                                       else 0),
                }
                state_data["entries"].append(entry_data)

            # Dashboard: accumulate P&L history (one point per minute)
            # Only record when there are placed entries (skip pre-market zeros)
            active_entries = [e for e in self.daily_state.entries if e.entry_time]
            if active_entries or self._pnl_history:
                now = get_us_market_time()
                time_key = now.strftime("%H:%M")
                # Compute net P&L: realized + unrealized (active sides) + surviving longs - commission
                net_pnl = self.daily_state.total_realized_pnl - self.daily_state.total_commission
                for entry in active_entries:
                    call_active = (not entry.call_side_stopped and not entry.call_side_skipped
                                   and not entry.call_side_expired)
                    put_active = (not entry.put_side_stopped and not entry.put_side_skipped
                                  and not entry.put_side_expired)
                    if call_active:
                        net_pnl += entry.call_spread_credit - (entry.call_spread_value or 0)
                    if put_active:
                        net_pnl += entry.put_spread_credit - (entry.put_spread_value or 0)
                    # Surviving long legs after MKT-025 stop
                    if entry.call_side_stopped and not getattr(entry, 'call_long_sold', False) and entry.long_call_uic:
                        net_pnl += entry.long_call_price * 100 * entry.contracts
                    if entry.put_side_stopped and not getattr(entry, 'put_long_sold', False) and entry.long_put_uic:
                        net_pnl += entry.long_put_price * 100 * entry.contracts

                # Append or update current minute
                if self._pnl_history and self._pnl_history[-1]["time"] == time_key:
                    self._pnl_history[-1]["pnl"] = round(net_pnl, 2)
                else:
                    self._pnl_history.append({"time": time_key, "pnl": round(net_pnl, 2)})

            state_data["pnl_history"] = self._pnl_history

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
            logger.debug(f"HYDRA state saved to {self.state_file}")

        except Exception as e:
            logger.error(f"Failed to save HYDRA state: {e}")

    def _register_position(self, entry: IronCondorEntry, leg_name: str):
        """
        Register a position leg with the Position Registry using HYDRA bot name.

        Override from MEIC to use "HYDRA" instead of "MEIC" for proper isolation
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
                bot_name="HYDRA",  # Use HYDRA instead of MEIC for isolation
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

        OVERRIDE: Uses BOT_NAME ("HYDRA") instead of hardcoded "MEIC" in parent class.

        Returns:
            Error message if inconsistent, None if OK
        """
        from bots.meic.strategy import MEICState

        active_entries = len(self.daily_state.active_entries)
        my_positions = self.registry.get_positions(self.BOT_NAME)  # Use HYDRA, not MEIC

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

        OVERRIDE: Uses BOT_NAME ("HYDRA") instead of hardcoded "MEIC" in parent class.

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
            my_registry_positions = self.registry.get_positions(self.BOT_NAME)  # Use HYDRA, not MEIC
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

        OVERRIDE: Uses BOT_NAME ("HYDRA") instead of hardcoded "MEIC" in parent class.
        """
        from bots.meic.strategy import MEICDailyState

        logger.info("Resetting for new trading day")

        # STATE-004: Check for overnight 0DTE positions (should NEVER happen)
        try:
            my_position_ids = self.registry.get_positions(self.BOT_NAME)  # Use HYDRA, not MEIC
        except Exception as e:
            logger.error(f"Registry error checking for overnight positions: {e}")
            my_position_ids = set()
        if my_position_ids:
            # FIX #82: Registry has positions, but they may be stale (already settled on Saxo).
            # Verify against Saxo before halting - 0DTE options always settle same day.
            try:
                actual_positions = self.client.get_positions()
                actual_position_ids = {str(p.get("PositionId")) for p in actual_positions}
                still_open = my_position_ids & actual_position_ids

                if not still_open:
                    # Positions are gone from Saxo — registry is stale, clean it up
                    logger.info(
                        f"FIX #82: Registry had {len(my_position_ids)} stale position IDs "
                        f"but Saxo confirms 0 still open — cleaning up registry"
                    )
                    for pos_id in my_position_ids:
                        try:
                            self.registry.unregister(pos_id)
                        except Exception as e:
                            logger.error(f"Registry error unregistering stale {pos_id}: {e}")
                    # Fall through to normal reset below
                else:
                    # Positions genuinely still open on Saxo — this is a real problem
                    error_msg = (
                        f"CRITICAL: {len(still_open)} {self.BOT_NAME} positions still open on Saxo overnight! "
                        f"0DTE should expire same day. IDs: {list(still_open)}"
                    )
                    logger.critical(error_msg)
                    self.alert_service.send_alert(
                        alert_type=AlertType.CRITICAL_INTERVENTION,
                        title=f"{self.BOT_NAME} Overnight Position Detected!",
                        message=error_msg,
                        priority=AlertPriority.CRITICAL,
                        details={"position_ids": list(still_open)}
                    )
                    # Halt trading - manual intervention required
                    self._critical_intervention_required = True
                    self._critical_intervention_reason = "Overnight 0DTE positions detected - investigate immediately"
                    return  # Don't reset state, need to handle existing positions
            except Exception as e:
                # Can't verify — be conservative and halt
                error_msg = (
                    f"CRITICAL: {len(my_position_ids)} {self.BOT_NAME} positions in registry and "
                    f"Saxo verification failed ({e}) — halting for safety"
                )
                logger.critical(error_msg)
                self.alert_service.send_alert(
                    alert_type=AlertType.CRITICAL_INTERVENTION,
                    title=f"{self.BOT_NAME} Overnight Position Check Failed!",
                    message=error_msg,
                    priority=AlertPriority.CRITICAL,
                    details={"position_ids": list(my_position_ids), "error": str(e)}
                )
                self._critical_intervention_required = True
                self._critical_intervention_reason = f"Overnight position verification failed: {e}"
                return

        self.daily_state = MEICDailyState()
        self.daily_state.date = get_us_market_time().strftime("%Y-%m-%d")

        self._next_entry_index = 0
        self._daily_summary_sent = False
        self._circuit_breaker_open = False
        self._consecutive_failures = 0
        self._api_results_window.clear()
        self._early_close_triggered = False  # MKT-018: Reset early close
        self._roc_gate_triggered = False  # MKT-021: Reset ROC gate
        self._vix_gate_resolved = False  # MKT-034: Reset VIX gate
        self._vix_gate_start_slot = 0
        if self.vix_gate_enabled:
            self.entry_times = list(ALL_ENTRY_SLOTS[:5])  # MKT-034: Reset to default schedule
        else:
            # Non-MKT-034: Re-parse entry times from config for new day
            self._parse_entry_times()
        # Re-apply early close filter for new day (both paths)
        if is_early_close_day():
            early_cutoff = dt_time(12, 30)
            first_entry = self.entry_times[0] if self.entry_times else dt_time(10, 15)
            self.entry_times = [t for t in self.entry_times if t < early_cutoff]
            if not self.entry_times:
                self.entry_times = [first_entry]
            logger.info(f"HYDRA early close day schedule: {[t.strftime('%H:%M:%S') for t in self.entry_times]}")
        self._early_close_time = None
        self._early_close_pnl = None
        self._pnl_history = []  # Reset dashboard P&L curve for new day

        # P3: Reset intraday market data tracking
        self.market_data.reset_daily_tracking()

        # P2: Clear WebSocket price cache
        self._ws_price_cache.clear()

        # Reset reconciliation timer
        self._last_reconciliation_time = None

        # POS-004: Reset settlement reconciliation flag for new day
        self._settlement_reconciliation_complete = False

        # Skip weekdays: if today is a skip day, go straight to DAILY_COMPLETE
        today_dow = get_us_market_time().weekday()
        if today_dow in self.skip_weekdays:
            day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
            logger.info(f"Skip weekday: {day_names.get(today_dow, str(today_dow))} — no trading today")
            self.state = MEICState.DAILY_COMPLETE
        else:
            self.state = MEICState.IDLE

        # VIX regime: reset applied flag so it re-applies with today's VIX
        self._vix_regime_applied = False

        # Save clean state to disk
        self._save_state_to_disk()

    def _apply_vix_regime_overrides(self):
        """Apply VIX regime parameter overrides based on current VIX level."""
        if not self.vix_regime_enabled or self._vix_regime_applied:
            return
        if self.current_vix <= 0:
            return  # VIX not available yet

        vix = self.current_vix
        # Determine regime bin
        regime = len(self.vix_regime_breakpoints)  # default: above all breakpoints
        for i, bp in enumerate(self.vix_regime_breakpoints):
            if vix < bp:
                regime = i
                break

        # Apply max_entries cap
        max_entries = self.vix_regime_max_entries
        if regime < len(max_entries) and max_entries[regime] is not None:
            cap = max_entries[regime]
            if self._base_entry_count > cap:
                # Truncate base entries, keep conditional entries
                base_times = self.entry_times[:self._base_entry_count]
                cond_times = self.entry_times[self._base_entry_count:]
                self.entry_times = base_times[:cap] + cond_times
                self._base_entry_count = cap
                logger.info(f"VIX regime: VIX={vix:.1f}, regime={regime}, capped to {cap} base entries")

        # Apply stop buffer overrides (config values are per-contract dollars, multiply by 100)
        psb = self.vix_regime_put_stop_buffer
        if regime < len(psb) and psb[regime] is not None:
            old = self.put_stop_buffer
            self.put_stop_buffer = psb[regime] * 100
            logger.info(f"VIX regime: put_stop_buffer ${old/100:.2f} → ${self.put_stop_buffer/100:.2f}")
        csb = self.vix_regime_call_stop_buffer
        if regime < len(csb) and csb[regime] is not None:
            old = self.call_stop_buffer
            self.call_stop_buffer = csb[regime] * 100
            logger.info(f"VIX regime: call_stop_buffer ${old/100:.2f} → ${self.call_stop_buffer/100:.2f}")

        # Apply credit gate overrides (config values are per-contract dollars, multiply by 100)
        mcc = self.vix_regime_min_call_credit
        if regime < len(mcc) and mcc[regime] is not None:
            old = self.min_viable_credit_per_side
            self.min_viable_credit_per_side = mcc[regime] * 100
            self.call_credit_floor = self.min_viable_credit_per_side - 10
            logger.info(f"VIX regime: min_call_credit ${old/100:.2f} → ${self.min_viable_credit_per_side/100:.2f}")
        mpc = self.vix_regime_min_put_credit
        if regime < len(mpc) and mpc[regime] is not None:
            old = self.min_viable_credit_put_side
            self.min_viable_credit_put_side = mpc[regime] * 100
            self.put_credit_floor = self.min_viable_credit_put_side - 10
            logger.info(f"VIX regime: min_put_credit ${old/100:.2f} → ${self.min_viable_credit_put_side/100:.2f}")

        self._vix_regime_applied = True
        logger.info(f"VIX regime applied: VIX={vix:.1f}, regime={regime}/{len(self.vix_regime_breakpoints)}")

    def _process_expired_credits(self) -> float:
        """
        FIX #77: Process entries with un-finalized sides as expired.

        Iterates through daily_state.entries and marks any side that:
        - Had positions (strike > 0)
        - Has no position IDs (positions gone from Saxo)
        - Is NOT already stopped, expired, or skipped

        ...as EXPIRED, adding its credit to total_realized_pnl.

        Returns:
            float: Total expired credit added to realized P&L
        """
        expired_call_credit = 0.0
        expired_put_credit = 0.0

        for entry in self.daily_state.entries:
            # Check call side
            call_had_positions = entry.short_call_strike > 0 or entry.long_call_strike > 0
            call_positions_gone = not entry.short_call_position_id and not entry.long_call_position_id

            if call_had_positions and call_positions_gone:
                if not entry.call_side_stopped and not entry.call_side_expired and not entry.call_side_skipped:
                    entry.call_side_expired = True
                    credit = entry.call_spread_credit
                    if credit > 0:
                        expired_call_credit += credit
                        logger.info(
                            f"  Entry #{entry.entry_number} call side EXPIRED worthless: "
                            f"+${credit:.2f} profit (credit kept)"
                        )

            # Check put side
            put_had_positions = entry.short_put_strike > 0 or entry.long_put_strike > 0
            put_positions_gone = not entry.short_put_position_id and not entry.long_put_position_id

            if put_had_positions and put_positions_gone:
                if not entry.put_side_stopped and not entry.put_side_expired and not entry.put_side_skipped:
                    entry.put_side_expired = True
                    credit = entry.put_spread_credit
                    if credit > 0:
                        expired_put_credit += credit
                        logger.info(
                            f"  Entry #{entry.entry_number} put side EXPIRED worthless: "
                            f"+${credit:.2f} profit (credit kept)"
                        )

            # Mark complete if both sides done
            if entry.call_only:
                if entry.call_side_stopped or entry.call_side_expired:
                    entry.is_complete = True
            elif entry.put_only:
                if entry.put_side_stopped or entry.put_side_expired:
                    entry.is_complete = True
            else:
                call_done = entry.call_side_stopped or entry.call_side_expired or entry.call_side_skipped or not call_had_positions
                put_done = entry.put_side_stopped or entry.put_side_expired or entry.put_side_skipped or not put_had_positions
                if call_done and put_done:
                    entry.is_complete = True

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

        # Fix #87: Verify expired P&L against Saxo's actual settlement values.
        # _process_expired_credits assumes full credit kept (ClosePrice=$0), but
        # options near ATM can settle at non-zero values. Query Saxo's historical
        # closedpositions report for actual P&L and correct if different.
        if not self.dry_run:
            self._verify_settlement_pnl_from_saxo()

        return total_expired_credit

    def _verify_settlement_pnl_from_saxo(self):
        """
        Fix #87: Verify total P&L against Saxo's closedpositions report.

        The bot calculates P&L from: stop close costs + assumed expired credits.
        But expired options may settle at non-zero (near-ATM at settlement).
        Saxo's /cs/v1/reports/closedPositions has actual PnLAccountCurrency
        for every closed position including settlements.

        If Saxo's total differs from our calculated total, apply a correction
        to total_realized_pnl. This runs once at settlement time.
        """
        try:
            from shared.market_hours import get_us_market_time
            today = get_us_market_time().strftime("%Y-%m-%d")

            response = self.client._make_request(
                "GET",
                f"/cs/v1/reports/closedPositions/{self.client.client_key}/{today}/{today}"
            )

            if not response or "Data" not in response:
                logger.warning("Fix #87: Could not fetch closedpositions report — using assumed P&L")
                return

            # PnLAccountCurrency = NET P&L per position (includes commission)
            # Sum = total net P&L for the day from Saxo's perspective
            saxo_net_pnl = 0.0
            positions_found = 0
            for cp in response["Data"]:
                pnl = cp.get("PnLAccountCurrency", 0) or 0
                saxo_net_pnl += pnl
                positions_found += 1

            if positions_found == 0:
                logger.info("Fix #87: No closed positions in Saxo report — skipping verification")
                return

            # Our net P&L = total_realized_pnl (gross) - total_commission
            our_net_pnl = self.daily_state.total_realized_pnl - self.daily_state.total_commission

            diff = saxo_net_pnl - our_net_pnl
            if abs(diff) < 1.0:
                logger.info(
                    f"Fix #87: P&L verified — Saxo ${saxo_net_pnl:.2f} net matches "
                    f"bot ${our_net_pnl:.2f} net ({positions_found} positions)"
                )
                return

            # Apply correction to total_realized_pnl (gross).
            # Since diff = saxo_net - our_net, and our_net = gross - commission,
            # corrected_gross = gross + diff, so corrected_net = gross + diff - commission = saxo_net.
            logger.warning(
                f"Fix #87: P&L CORRECTION — Saxo reports ${saxo_net_pnl:.2f} net, "
                f"bot calculated ${our_net_pnl:.2f} net (diff: ${diff:+.2f}). "
                f"Adjusting total_realized_pnl by ${diff:+.2f}"
            )
            self.daily_state.total_realized_pnl += diff
            logger.info(
                f"Fix #87: Corrected total_realized_pnl: ${self.daily_state.total_realized_pnl:.2f} "
                f"(net after commission: ${self.daily_state.total_realized_pnl - self.daily_state.total_commission:.2f})"
            )

        except Exception as e:
            logger.warning(f"Fix #87: Settlement P&L verification failed: {e} — using assumed P&L")

    def _reconcile_positions(self):
        """Override: After base reconciliation, detect manually closed longs.

        When a long leg disappears from Saxo (manually sold by the user),
        the base class clears position_id and UIC. This override checks
        Saxo's closedpositions API to capture the actual sale revenue,
        replicating what MKT-033 would have recorded.
        """
        # Snapshot which long legs have UICs BEFORE base reconciliation clears them
        pre_longs = {}
        for entry in self.daily_state.entries:
            if not entry.entry_time:
                continue
            for side in ("call", "put"):
                sold = getattr(entry, f"{side}_long_sold", False)
                uic = getattr(entry, f"long_{side}_uic", None)
                pos_id = getattr(entry, f"long_{side}_position_id", None)
                if uic and pos_id and not sold:
                    pre_longs[(entry.entry_number, side)] = {
                        "uic": uic,
                        "pos_id": pos_id,
                    }

        # Run base reconciliation (clears position_id + UIC for missing legs)
        super()._reconcile_positions()

        # Check which long legs just disappeared
        for (entry_num, side), info in pre_longs.items():
            entry = next(
                (e for e in self.daily_state.entries if e.entry_number == entry_num),
                None,
            )
            if entry is None:
                continue

            uic_now = getattr(entry, f"long_{side}_uic", None)
            if uic_now is not None:
                continue  # Still present, not missing

            # Long leg was just cleared by base reconciliation — look up close price
            already_sold = getattr(entry, f"{side}_long_sold", False)
            if already_sold:
                continue  # Already accounted for

            logger.info(
                f"MKT-033 AUTO: Entry #{entry_num} long {side} (UIC {info['uic']}) "
                f"missing from Saxo — checking closedpositions for sale revenue"
            )

            try:
                # Long positions are "Buy" direction; selling them is recorded as "Sell"
                closed = self.client.get_closed_position_price(
                    info["uic"], buy_or_sell="Sell"
                )
                if closed and closed.get("closing_price", 0) > 0:
                    fill_price = closed["closing_price"]
                    revenue = fill_price * 100 * self.contracts_per_entry
                    close_commission = self.commission_per_leg * self.contracts_per_entry

                    # Record exactly as MKT-033 does
                    self.daily_state.total_realized_pnl += revenue
                    self.daily_state.total_commission += close_commission
                    entry.close_commission += close_commission

                    setattr(entry, f"{side}_long_sold", True)
                    setattr(entry, f"{side}_long_sold_revenue", revenue)

                    net_profit = revenue - close_commission
                    logger.info(
                        f"MKT-033 AUTO: Entry #{entry_num} long {side} sold externally "
                        f"@ ${fill_price:.2f} (revenue=${revenue:.2f}, "
                        f"commission=${close_commission:.2f}, net=+${net_profit:.2f})"
                    )
                    self._log_safety_event(
                        "LONG_SOLD_EXTERNAL",
                        f"Entry #{entry_num} long {side} sold externally @ ${fill_price:.2f}, "
                        f"revenue=${revenue:.2f}"
                    )
                else:
                    logger.warning(
                        f"MKT-033 AUTO: Entry #{entry_num} long {side} missing but "
                        f"no closing price found — may have expired worthless"
                    )
                    # Mark as sold with $0 revenue to prevent repeated lookups
                    setattr(entry, f"{side}_long_sold", True)
                    setattr(entry, f"{side}_long_sold_revenue", 0.0)
            except Exception as e:
                logger.error(
                    f"MKT-033 AUTO: Error looking up close price for Entry #{entry_num} "
                    f"long {side}: {e}"
                )

        # Save state with any new salvage data
        self._save_state_to_disk()

    def check_after_hours_settlement(self) -> bool:
        """
        POS-004: Check if 0DTE positions have been settled after market close.

        OVERRIDE: Uses BOT_NAME ("HYDRA") instead of hardcoded "MEIC" in parent class.

        Called on every heartbeat after market close until all positions
        are confirmed settled. This handles the fact that Saxo settles 0DTE
        options sometime between 4:00 PM and 7:00 PM EST.

        Returns:
            True if all positions are settled (or were already confirmed settled)
            False if positions still exist on Saxo (settlement pending)
        """
        # Already confirmed settled for today - but check if new positions appeared
        # FIX #82: The flag gets set at midnight when registry is empty (pre-market).
        # If trading happens during the day, registry gets new positions. We must
        # reset the flag so post-market settlement actually processes them.
        if self._settlement_reconciliation_complete:
            my_position_ids = self.registry.get_positions(self.BOT_NAME)
            if my_position_ids:
                logger.info(
                    f"FIX #82: Settlement was marked complete but registry has "
                    f"{len(my_position_ids)} positions - resetting flag for proper settlement"
                )
                self._settlement_reconciliation_complete = False
                # Fall through to normal settlement logic below
            else:
                return True

        # Check how many positions we think we have in registry
        my_position_ids = self.registry.get_positions(self.BOT_NAME)  # Use HYDRA, not MEIC

        if not my_position_ids:
            # FIX #77: Registry empty — but entries may have un-finalized surviving sides
            # that need expired credit processing (e.g., post-restart with partial ICs).
            # Previously returned True immediately, skipping expired credit processing.
            expired_credit = self._process_expired_credits()
            if expired_credit > 0:
                logger.info(f"FIX #77: Processed ${expired_credit:.2f} expired credits from surviving sides (registry was empty)")
                # Fix #84: Add final P&L history point after settlement
                final_net_pnl = self.daily_state.total_realized_pnl - self.daily_state.total_commission
                now = get_us_market_time()
                time_key = now.strftime("%H:%M")
                self._pnl_history.append({"time": time_key, "pnl": round(final_net_pnl, 2)})
                logger.info(f"Fix #84: Final P&L history point: ${final_net_pnl:.2f} at {time_key}")
                self._save_state_to_disk()
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

                # FIX #43 / FIX #77: Process expired positions and add credit to realized P&L.
                # Extracted to _process_expired_credits() helper to share with empty-registry path.
                self._process_expired_credits()

                # Save updated state
                self._save_state_to_disk()

            if still_open:
                logger.info(f"POS-004: {len(still_open)} positions still open on Saxo - awaiting settlement")
                return False
            else:
                # All positions settled
                logger.info(f"POS-004: All {self.BOT_NAME} positions confirmed settled - reconciliation complete")
                self._settlement_reconciliation_complete = True

                # Fix #84: Add final P&L history point after settlement so dashboard
                # shows post-settlement P&L (not stale pre-settlement snapshot)
                final_net_pnl = self.daily_state.total_realized_pnl - self.daily_state.total_commission
                now = get_us_market_time()
                time_key = now.strftime("%H:%M")
                self._pnl_history.append({"time": time_key, "pnl": round(final_net_pnl, 2)})
                logger.info(f"Fix #84: Final P&L history point: ${final_net_pnl:.2f} at {time_key}")
                self._save_state_to_disk()

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

        OVERRIDE: Uses BOT_NAME ("HYDRA") instead of hardcoded "MEIC" in parent class.

        Args:
            event_type: Type of safety event (e.g., "CIRCUIT_BREAKER_OPEN", "NAKED_SHORT_DETECTED")
            details: Human-readable description of the event
            result: Outcome of the event (default: "Acknowledged")
        """
        try:
            self.trade_logger.log_safety_event({
                "timestamp": get_us_market_time().strftime("%Y-%m-%d %H:%M:%S"),
                "event_type": event_type,
                "bot": self.BOT_NAME,  # Use HYDRA, not MEIC
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
    ) -> Optional[HydraIronCondorEntry]:
        """
        Reconstruct a HydraIronCondorEntry from Saxo position data.

        OVERRIDE (Fix #40, 2026-02-05): Parent class creates IronCondorEntry objects
        which don't have call_only/put_only fields. For HYDRA, we must create
        HydraIronCondorEntry objects and set the one-sided flags based on which legs
        exist. Without this, recovery of one-sided entries triggers false stops.

        Args:
            entry_number: The entry number (1-N, based on configured entry_times)
            positions: List of parsed position dicts for this entry

        Returns:
            Reconstructed HydraIronCondorEntry or None if invalid
        """
        # Create HydraIronCondorEntry instead of IronCondorEntry
        entry = HydraIronCondorEntry(entry_number=entry_number)
        entry.strategy_id = f"hydra_{get_us_market_time().strftime('%Y%m%d')}_{entry_number:03d}"
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
            # Partial entry - determine if it's a one-sided HYDRA entry or a stopped entry
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

            # HYDRA SPECIFIC: Determine if this is a one-sided entry (by design)
            # or if a side was stopped out
            # If we have exactly call side OR put side, it's likely a one-sided HYDRA entry
            # FIX #47: Use "skipped" instead of "stopped" for sides that were never opened
            if has_call_side and not has_put_side:
                # Only call spread found in Saxo. Two possibilities:
                #   (a) Designed call-only entry (MKT-035/038/040) — put side was never opened
                #   (b) Full IC where put side was stopped intraday — only call remains
                # We cannot determine which from positions alone. Tentatively set call_only=True
                # so stop monitoring watches the right side. State file restoration (lines ~8017-8031)
                # will overwrite call_only/put_side_skipped with the authoritative values.
                entry.call_only = True
                entry.put_only = False
                entry.call_side_stopped = False
                entry.put_side_skipped = True  # Tentative — state file may change to put_side_stopped
                logger.info(
                    f"Entry #{entry_number}: Only call side found in Saxo — "
                    f"tentatively CALL-ONLY (state file will correct if put was stopped intraday)"
                )
            elif has_put_side and not has_call_side:
                # Only put spread found. Could be designed put-only OR full IC with stopped call.
                # Tentative classification; state file restoration is authoritative.
                entry.call_only = False
                entry.put_only = True
                entry.call_side_skipped = True  # Tentative — state file may change to call_side_stopped
                entry.put_side_stopped = False
                logger.info(
                    f"Entry #{entry_number}: Only put side found in Saxo — "
                    f"tentatively PUT-ONLY (state file will correct if call was stopped intraday)"
                )
            else:
                # Mixed partial - probably a stopped entry (not skipped)
                entry.call_side_stopped = not has_call_side
                entry.put_side_stopped = not has_put_side

        entry.is_complete = has_all_legs

        # CRITICAL SAFETY CHECK: Prevent zero stop levels
        MIN_STOP_LEVEL = 50.0

        # One-sided entry stop levels (must match _calculate_stop_levels_hydra behavior).
        # Call-only: call_credit + theoretical_put ($250) + buffer (consistent for all call-only types).
        # Put-only: credit + $1.55 buffer (MKT-039 — $1.55 buffer prevents false stops, walk-forward optimized).
        if entry.call_only:
            credit = entry.call_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(
                    f"Recovery CRITICAL: Entry #{entry.entry_number} (call-only) has low credit "
                    f"(${credit:.2f}). Using minimum stop level ${MIN_STOP_LEVEL:.2f}."
                )
                credit = MIN_STOP_LEVEL

            # All call-only entries: call_credit + theoretical_put + buffer
            # Use getattr with default — recovery may run before HYDRA config is loaded
            theoretical_put = getattr(self, 'downday_theoretical_put_credit', 260.0)
            call_stop_buffer = getattr(self, 'call_stop_buffer', 35.0)
            base_stop = credit + theoretical_put
            override = getattr(entry, 'override_reason', None) or "mkt-040"
            logger.info(
                f"Recovery: {override.upper()} call-only stop = call ${credit:.2f} + "
                f"theoretical put ${theoretical_put:.2f} + buffer ${call_stop_buffer:.2f}"
            )

            stop_level = base_stop + call_stop_buffer
            stop_level = max(stop_level, MIN_STOP_LEVEL)

            entry.call_side_stop = stop_level
            entry.put_side_stop = 0  # No put side to monitor
            logger.info(f"Recovery: Call-only stop = ${stop_level:.2f}")

        elif entry.put_only:
            credit = entry.put_spread_credit
            if credit < MIN_STOP_LEVEL:
                logger.critical(
                    f"Recovery CRITICAL: Entry #{entry.entry_number} (put-only) has low credit "
                    f"(${credit:.2f}). Using minimum stop level ${MIN_STOP_LEVEL:.2f}."
                )
                credit = MIN_STOP_LEVEL

            # MKT-039: Put-only stop = credit + $1.55 buffer (matches _calculate_stop_levels_hydra)
            base_stop = credit
            stop_level = base_stop + self.put_stop_buffer

            entry.put_side_stop = stop_level
            entry.call_side_stop = 0  # No call side to monitor
            logger.info(f"Recovery: Put-only stop = ${stop_level:.2f} (credit ${credit:.2f} + buffer ${self.put_stop_buffer:.2f})")
        else:
            # Full IC — stop = total_credit + buffer (asymmetric: call uses call_stop_buffer, put uses put_stop_buffer)
            total_credit = entry.total_credit

            if total_credit < MIN_STOP_LEVEL:
                logger.critical(
                    f"Recovery CRITICAL: Entry #{entry.entry_number} total credit too low "
                    f"(${total_credit:.2f}). Using minimum."
                )
                total_credit = MIN_STOP_LEVEL

            base_stop = total_credit
            call_stop_level = base_stop + self.call_stop_buffer
            put_stop_level = base_stop + self.put_stop_buffer

            entry.call_side_stop = call_stop_level
            entry.put_side_stop = put_stop_level
            logger.info(
                f"Recovery: Full IC stop = call ${call_stop_level:.2f} / put ${put_stop_level:.2f} "
                f"(total credit ${total_credit:.2f} + call buf ${self.call_stop_buffer:.2f} / put buf ${self.put_stop_buffer:.2f})"
            )

        return entry

    def _recover_from_state_file_uics(self, all_positions: List[Dict]) -> Dict[int, List[Dict]]:
        """
        Override to use HYDRA bot name in registry during UIC-based recovery.

        This is a fallback recovery method when registry-based recovery fails.
        Uses UICs stored in the state file to match positions and re-registers
        them with the correct bot name (HYDRA instead of MEIC).

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
                                    strategy_id = entry_data.get("strategy_id", f"hydra_{today}_entry{entry_num}")
                                    try:
                                        self.registry.register(
                                            position_id=pos_id,
                                            bot_name=self.BOT_NAME,  # Use HYDRA
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
            # Fix #55/#56/#57: Restore HYDRA specific counters
            self.daily_state.one_sided_entries = saved_state.get("one_sided_entries", 0)
            self.daily_state.trend_overrides = saved_state.get("trend_overrides", 0)
            self.daily_state.credit_gate_skips = saved_state.get("credit_gate_skips", 0)
            # MKT-036: Restore stop confirmation avoided counter
            self.daily_state.stops_avoided_mkt036 = saved_state.get("stops_avoided_mkt036", 0)

            # MKT-018: Restore early close state
            self._early_close_triggered = saved_state.get("early_close_triggered", False)
            ec_time_str = saved_state.get("early_close_time")
            if ec_time_str:
                try:
                    from datetime import datetime as dt_cls
                    self._early_close_time = dt_cls.fromisoformat(ec_time_str)
                except (ValueError, TypeError):
                    self._early_close_time = None
            self._early_close_pnl = saved_state.get("early_close_pnl")
            # MKT-021: Restore ROC gate state
            self._roc_gate_triggered = saved_state.get("roc_gate_triggered", False)
            # MKT-034: Restore VIX gate state
            vix_gate_resolved = saved_state.get("vix_gate_resolved", False)
            if vix_gate_resolved and self.vix_gate_enabled:
                saved_slot = saved_state.get("vix_gate_start_slot", 0)
                self._resolve_vix_gate(saved_slot)
                logger.info(f"MKT-034: Restored VIX gate state (slot {saved_slot})")

            # Restore P&L history for dashboard persistence
            self._pnl_history = saved_state.get("pnl_history", [])
            logger.info(f"Restored {len(self._pnl_history)} P&L history points from state file")

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

            # FIX #77: Restore ALL entries from state file, not just "fully done" ones.
            # Previously, entries with surviving sides (e.g., IC with call stopped but put
            # still live) were silently dropped. This caused post-restart settlement to miss
            # $660 in expired credits on Feb 17, logging -$1400 net instead of -$740 net.
            # Now we restore all entries; settlement will process un-finalized sides as expired.
            stopped_entries_restored = 0
            surviving_entries_restored = 0
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

                # Reconstruct the entry from saved state (ALL entries, not just done ones)
                entry_num = entry_data.get("entry_number")
                restored_entry = HydraIronCondorEntry(entry_number=entry_num)

                # Parse entry_time if it's a string
                entry_time_str = entry_data.get("entry_time")
                if entry_time_str:
                    if isinstance(entry_time_str, str):
                        try:
                            restored_entry.entry_time = datetime.fromisoformat(entry_time_str)
                        except ValueError:
                            restored_entry.entry_time = None
                    else:
                        restored_entry.entry_time = entry_time_str

                restored_entry.strategy_id = entry_data.get("strategy_id", f"hydra_{today.replace('-', '')}_{entry_num:03d}")
                restored_entry.short_call_strike = entry_data.get("short_call_strike", 0)
                restored_entry.long_call_strike = entry_data.get("long_call_strike", 0)
                restored_entry.short_put_strike = entry_data.get("short_put_strike", 0)
                restored_entry.long_put_strike = entry_data.get("long_put_strike", 0)
                restored_entry.call_spread_credit = entry_data.get("call_spread_credit", 0)
                restored_entry.put_spread_credit = entry_data.get("put_spread_credit", 0)
                restored_entry.call_side_stop = entry_data.get("call_side_stop", 0)
                restored_entry.put_side_stop = entry_data.get("put_side_stop", 0)
                # FIX #47: Restore all status flags (stopped/expired/skipped)
                restored_entry.call_side_stopped = call_stopped
                restored_entry.put_side_stopped = put_stopped
                restored_entry.call_side_expired = call_expired
                restored_entry.put_side_expired = put_expired
                restored_entry.call_side_skipped = call_skipped
                restored_entry.put_side_skipped = put_skipped
                # Fix #61: Restore merge flags
                restored_entry.call_side_merged = entry_data.get("call_side_merged", False)
                restored_entry.put_side_merged = entry_data.get("put_side_merged", False)
                restored_entry.open_commission = entry_data.get("open_commission", 0)
                restored_entry.close_commission = entry_data.get("close_commission", 0)
                restored_entry.call_only = call_only
                restored_entry.put_only = put_only
                # Fix #52: Restore contract count (default to current config if not saved)
                restored_entry.contracts = entry_data.get("contracts", self.contracts_per_entry)

                if entry_data.get("trend_signal"):
                    try:
                        restored_entry.trend_signal = TrendSignal(entry_data["trend_signal"])
                    except ValueError:
                        pass

                # Fix #49: Restore override_reason for correct logging
                restored_entry.override_reason = entry_data.get("override_reason", None)
                # Fix #59: Restore EMA values for Trades tab logging
                restored_entry.ema_20_at_entry = entry_data.get("ema_20_at_entry", None)
                restored_entry.ema_40_at_entry = entry_data.get("ema_40_at_entry", None)
                # MKT-018: Restore early_closed marker
                restored_entry.early_closed = entry_data.get("early_closed", False)
                # MKT-033: Restore long salvage flags
                restored_entry.call_long_sold = entry_data.get("call_long_sold", False)
                restored_entry.put_long_sold = entry_data.get("put_long_sold", False)
                restored_entry.call_long_sold_revenue = entry_data.get("call_long_sold_revenue", 0.0)
                restored_entry.put_long_sold_revenue = entry_data.get("put_long_sold_revenue", 0.0)
                # MKT-036: Restore breach counts (NOT breach_time — conservative reset on restart)
                restored_entry.call_breach_count = entry_data.get("call_breach_count", 0)
                restored_entry.put_breach_count = entry_data.get("put_breach_count", 0)
                # MKT-041: Restore cushion recovery danger flags
                restored_entry.call_hit_danger = entry_data.get("call_hit_danger", False)
                restored_entry.put_hit_danger = entry_data.get("put_hit_danger", False)
                # Restore stop timestamps (for dashboard stop markers)
                restored_entry.call_stop_time = entry_data.get("call_stop_time", "")
                restored_entry.put_stop_time = entry_data.get("put_stop_time", "")
                # Fill prices (for /entry display after restart)
                restored_entry.short_call_fill_price = entry_data.get("short_call_fill_price", 0)
                restored_entry.long_call_fill_price = entry_data.get("long_call_fill_price", 0)
                restored_entry.short_put_fill_price = entry_data.get("short_put_fill_price", 0)
                restored_entry.long_put_fill_price = entry_data.get("long_put_fill_price", 0)
                # Actual stop debit (for dashboard per-entry P&L accuracy)
                restored_entry.actual_call_stop_debit = entry_data.get("actual_call_stop_debit", 0.0)
                restored_entry.actual_put_stop_debit = entry_data.get("actual_put_stop_debit", 0.0)
                # v1.16.0: Restore skip reason for dashboard display
                restored_entry.skip_reason = entry_data.get("skip_reason", "")

                if is_fully_done:
                    restored_entry.is_complete = True
                    stopped_entries_restored += 1
                else:
                    # FIX #77: Entry has surviving sides — restore but don't mark complete.
                    # Settlement will process these sides as expired after market close.
                    surviving_entries_restored += 1

                self.daily_state.entries.append(restored_entry)

            # Update next_entry_index: use saved value if higher than entry-based calc
            # (MKT-021 ROC gate or MKT-011 skips may advance it beyond completed entries)
            saved_next_idx = saved_state.get("next_entry_index", 0)
            entry_based_idx = 0
            if self.daily_state.entries:
                max_entry_num = max(e.entry_number for e in self.daily_state.entries)
                entry_based_idx = max_entry_num  # Next entry is the one after max
            self._next_entry_index = max(entry_based_idx, saved_next_idx)

            logger.info(f"FIX #41: Loaded state file history - P&L: ${self.daily_state.total_realized_pnl:.2f}, "
                       f"entries_completed: {self.daily_state.entries_completed}, "
                       f"stopped_entries_restored: {stopped_entries_restored}, "
                       f"surviving_entries_restored: {surviving_entries_restored}, "
                       f"next_entry_index: {self._next_entry_index}")

            return True

        except Exception as e:
            logger.warning(f"Could not load state file history: {e}")
            return False

    def _recover_positions_from_saxo(self) -> bool:
        """
        Override to use HYDRA bot name in registry queries and logging.

        This is the main recovery method that queries Saxo API for positions
        and uses the Position Registry to identify which belong to HYDRA.

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

            # Step 3: Get HYDRA positions from registry (using class constant)
            my_position_ids = self.registry.get_positions(self.BOT_NAME)
            if not my_position_ids:
                logger.info(f"No {self.BOT_NAME} positions in registry")
                # FIX #41: Still load historical data from state file
                self._load_state_file_history()
                self.daily_state.date = today
                return False

            logger.info(f"Found {len(my_position_ids)} {self.BOT_NAME} positions in registry")

            # Step 4: Filter Saxo positions to just HYDRA positions
            hydra_positions = []
            for pos in all_positions:
                pos_id = str(pos.get("PositionId"))
                if pos_id in my_position_ids:
                    hydra_positions.append(pos)

            if not hydra_positions:
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

            logger.info(f"Matched {len(hydra_positions)} positions to {self.BOT_NAME} in Saxo")

            # Step 5: Group positions by entry number using registry metadata
            entries_by_number = self._group_positions_by_entry(hydra_positions, my_position_ids)

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
            preserved_stops_avoided_mkt036 = 0
            preserved_entry_credits = {}
            preserved_stopped_entries = []  # FIX #43: Fully stopped entries (no live positions)
            preserved_market_ohlc = {}
            preserved_pnl_history = []  # Dashboard P&L curve
            preserved_early_close_triggered = False  # MKT-018
            preserved_early_close_time = None  # MKT-018
            preserved_early_close_pnl = None  # MKT-018
            preserved_roc_gate_triggered = False  # MKT-021
            preserved_vix_gate_resolved = False  # MKT-034
            preserved_vix_gate_start_slot = 0  # MKT-034
            preserved_next_entry_index = 0
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
                            preserved_stops_avoided_mkt036 = saved_state.get("stops_avoided_mkt036", 0)
                            preserved_market_ohlc = saved_state.get("market_data_ohlc", {})
                            preserved_pnl_history = saved_state.get("pnl_history", [])
                            # MKT-018: Preserve early close state
                            preserved_early_close_triggered = saved_state.get("early_close_triggered", False)
                            ec_time_str = saved_state.get("early_close_time")
                            if ec_time_str:
                                try:
                                    from datetime import datetime as dt_cls
                                    preserved_early_close_time = dt_cls.fromisoformat(ec_time_str)
                                except (ValueError, TypeError):
                                    pass
                            preserved_early_close_pnl = saved_state.get("early_close_pnl")
                            # MKT-021: Preserve ROC gate state
                            preserved_roc_gate_triggered = saved_state.get("roc_gate_triggered", False)
                            # MKT-034: Preserve VIX gate state
                            preserved_vix_gate_resolved = saved_state.get("vix_gate_resolved", False)
                            preserved_vix_gate_start_slot = saved_state.get("vix_gate_start_slot", 0)
                            preserved_next_entry_index = saved_state.get("next_entry_index", 0)
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
                                        # HYDRA specific fields (Fix #40)
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
                                        # MKT-018: Early close marker
                                        "early_closed": entry_data.get("early_closed", False),
                                        # Entry time and fill prices (for /entry display)
                                        "entry_time": entry_data.get("entry_time"),
                                        "short_call_fill_price": entry_data.get("short_call_fill_price", 0),
                                        "long_call_fill_price": entry_data.get("long_call_fill_price", 0),
                                        "short_put_fill_price": entry_data.get("short_put_fill_price", 0),
                                        "long_put_fill_price": entry_data.get("long_put_fill_price", 0),
                                        # MKT-033: Long salvage flags
                                        "call_long_sold": entry_data.get("call_long_sold", False),
                                        "put_long_sold": entry_data.get("put_long_sold", False),
                                        "call_long_sold_revenue": entry_data.get("call_long_sold_revenue", 0.0),
                                        "put_long_sold_revenue": entry_data.get("put_long_sold_revenue", 0.0),
                                        # Actual stop debit (for dashboard per-entry P&L accuracy)
                                        "actual_call_stop_debit": entry_data.get("actual_call_stop_debit", 0.0),
                                        "actual_put_stop_debit": entry_data.get("actual_put_stop_debit", 0.0),
                                        # MKT-036: Breach counts (NOT breach_time — reset on restart)
                                        "call_breach_count": entry_data.get("call_breach_count", 0),
                                        "put_breach_count": entry_data.get("put_breach_count", 0),
                                        # MKT-041: Cushion recovery danger flags
                                        "call_hit_danger": entry_data.get("call_hit_danger", False),
                                        "put_hit_danger": entry_data.get("put_hit_danger", False),
                                        # Stop timestamps (for dashboard stop markers)
                                        "call_stop_time": entry_data.get("call_stop_time", ""),
                                        "put_stop_time": entry_data.get("put_stop_time", ""),
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
                    # MKT-018: Restore early_closed marker
                    entry.early_closed = saved.get("early_closed", False)
                    # MKT-033: Restore long salvage flags
                    entry.call_long_sold = saved.get("call_long_sold", False)
                    entry.put_long_sold = saved.get("put_long_sold", False)
                    entry.call_long_sold_revenue = saved.get("call_long_sold_revenue", 0.0)
                    entry.put_long_sold_revenue = saved.get("put_long_sold_revenue", 0.0)
                    # Actual stop debit (for dashboard per-entry P&L accuracy)
                    entry.actual_call_stop_debit = saved.get("actual_call_stop_debit", 0.0)
                    entry.actual_put_stop_debit = saved.get("actual_put_stop_debit", 0.0)
                    # Restore stop timestamps (for dashboard stop markers)
                    entry.call_stop_time = saved.get("call_stop_time", "")
                    entry.put_stop_time = saved.get("put_stop_time", "")
                    # MKT-041: Restore cushion recovery danger flags
                    entry.call_hit_danger = saved.get("call_hit_danger", False)
                    entry.put_hit_danger = saved.get("put_hit_danger", False)

                    # Restore entry_time and fill prices (for /entry display)
                    entry_time_str = saved.get("entry_time")
                    if entry_time_str and not entry.entry_time:
                        if isinstance(entry_time_str, str):
                            try:
                                entry.entry_time = datetime.fromisoformat(entry_time_str)
                            except ValueError:
                                pass
                        else:
                            entry.entry_time = entry_time_str
                    if saved.get("short_call_fill_price", 0) > 0 and entry.short_call_fill_price == 0:
                        entry.short_call_fill_price = saved["short_call_fill_price"]
                    if saved.get("long_call_fill_price", 0) > 0 and entry.long_call_fill_price == 0:
                        entry.long_call_fill_price = saved["long_call_fill_price"]
                    if saved.get("short_put_fill_price", 0) > 0 and entry.short_put_fill_price == 0:
                        entry.short_put_fill_price = saved["short_put_fill_price"]
                    if saved.get("long_put_fill_price", 0) > 0 and entry.long_put_fill_price == 0:
                        entry.long_put_fill_price = saved["long_put_fill_price"]

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
                    # Only restore missing long put for entries where the put side was actually active.
                    # Skip call-only entries (put_side_skipped=True): the state file may have a
                    # stale long_put_strike from before the entry type was finalized, and restoring
                    # it would trigger a spurious "Restored missing long put" warning.
                    if not entry.put_side_stopped and not entry.put_side_skipped and entry.long_put_strike == 0:
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
                    # Reconstruct HydraIronCondorEntry from saved state data
                    stopped_entry = HydraIronCondorEntry(entry_number=entry_num)
                    entry_time_str = stopped_entry_data.get("entry_time")
                    if entry_time_str and isinstance(entry_time_str, str):
                        try:
                            stopped_entry.entry_time = datetime.fromisoformat(entry_time_str)
                        except ValueError:
                            stopped_entry.entry_time = None
                    else:
                        stopped_entry.entry_time = entry_time_str
                    stopped_entry.strategy_id = stopped_entry_data.get("strategy_id", f"hydra_{today.replace('-', '')}_{entry_num:03d}")

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

                    # HYDRA specific: One-sided entry flags
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
                    # MKT-018: Restore early_closed marker
                    stopped_entry.early_closed = stopped_entry_data.get("early_closed", False)
                    # MKT-033: Long salvage flags (PRE-EXISTING BUG FIX — missing from this path)
                    stopped_entry.call_long_sold = stopped_entry_data.get("call_long_sold", False)
                    stopped_entry.put_long_sold = stopped_entry_data.get("put_long_sold", False)
                    stopped_entry.call_long_sold_revenue = stopped_entry_data.get("call_long_sold_revenue", 0.0)
                    stopped_entry.put_long_sold_revenue = stopped_entry_data.get("put_long_sold_revenue", 0.0)
                    # MKT-036: Restore breach counts (NOT breach_time — conservative reset on restart)
                    stopped_entry.call_breach_count = stopped_entry_data.get("call_breach_count", 0)
                    stopped_entry.put_breach_count = stopped_entry_data.get("put_breach_count", 0)
                    # MKT-041: Restore cushion recovery danger flags
                    stopped_entry.call_hit_danger = stopped_entry_data.get("call_hit_danger", False)
                    stopped_entry.put_hit_danger = stopped_entry_data.get("put_hit_danger", False)
                    # Fill prices (for /entry display after restart)
                    stopped_entry.short_call_fill_price = stopped_entry_data.get("short_call_fill_price", 0)
                    stopped_entry.long_call_fill_price = stopped_entry_data.get("long_call_fill_price", 0)
                    stopped_entry.short_put_fill_price = stopped_entry_data.get("short_put_fill_price", 0)
                    stopped_entry.long_put_fill_price = stopped_entry_data.get("long_put_fill_price", 0)
                    # Actual stop debit (for dashboard per-entry P&L accuracy)
                    stopped_entry.actual_call_stop_debit = stopped_entry_data.get("actual_call_stop_debit", 0.0)
                    stopped_entry.actual_put_stop_debit = stopped_entry_data.get("actual_put_stop_debit", 0.0)
                    # Stop timestamps (for dashboard stop markers)
                    stopped_entry.call_stop_time = stopped_entry_data.get("call_stop_time", "")
                    stopped_entry.put_stop_time = stopped_entry_data.get("put_stop_time", "")

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
            self.daily_state.stops_avoided_mkt036 = preserved_stops_avoided_mkt036

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

            # Restore P&L history for dashboard persistence
            self._pnl_history = preserved_pnl_history

            # Determine next entry index
            if recovered_entries:
                max_entry_num = max(e.entry_number for e in recovered_entries)
                self._next_entry_index = max(max_entry_num, preserved_next_entry_index)
            else:
                self._next_entry_index = preserved_next_entry_index

            # MKT-018: Restore early close state
            self._early_close_triggered = preserved_early_close_triggered
            self._early_close_time = preserved_early_close_time
            self._early_close_pnl = preserved_early_close_pnl
            # MKT-021: Restore ROC gate state
            self._roc_gate_triggered = preserved_roc_gate_triggered
            # MKT-034: Restore VIX gate state
            if preserved_vix_gate_resolved and self.vix_gate_enabled:
                self._resolve_vix_gate(preserved_vix_gate_start_slot)
                # _resolve_vix_gate resets _next_entry_index to 0 — restore correct value
                if recovered_entries:
                    self._next_entry_index = max(max_entry_num, preserved_next_entry_index)
                else:
                    self._next_entry_index = preserved_next_entry_index

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
