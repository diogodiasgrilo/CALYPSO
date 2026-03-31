"""
HYDRA Backtest Engine

Simulates HYDRA's exact entry logic against historical ThetaData:
  - VIX-adjusted OTM distance (base_distance_at_vix15=40, ~8-delta)
  - Spread width formula (VIX × multiplier, per-side floors)
  - Progressive tightening scan (MKT-020 calls / MKT-022 puts)
  - Credit gate (MKT-011) with MKT-029 graduated fallback
  - Put-only entries when call non-viable + VIX < threshold (MKT-032/039)
  - Call-only entries when put non-viable after retries (MKT-040)
  - E6/E7 conditional down-day call-only entries (MKT-035)
  - FOMC T+1 forced call-only (MKT-038)
  - Per-side stop monitoring at each 5-min interval
  - Settlement (expiry) as full profit at 4 PM
"""
from __future__ import annotations

import math
from copy import copy, deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .downloader import load_index_day, get_spxw_trading_days


def _load_chain(expiry: date, opts_dir: Path) -> pd.DataFrame:
    """Load chain data from a specific directory (supports 5min/1min folders)."""
    from .downloader import _date_str
    path = opts_dir / f"SPXW_{_date_str(expiry)}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _load_greeks(expiry: date, grk_dir: Path) -> pd.DataFrame:
    """Load Greeks data from a specific directory (supports 5min/1min folders)."""
    from .downloader import _date_str
    path = grk_dir / f"SPXW_{_date_str(expiry)}_greeks.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class EntryResult:
    entry_num: int
    entry_time_ms: int
    entry_type: str = ""     # "full_ic" | "call_only" | "put_only" | "skipped"
    skip_reason: str = ""

    # Strikes (0 if side not placed)
    short_call: float = 0
    long_call: float = 0
    short_put: float = 0
    long_put: float = 0

    # Credits collected (in dollars)
    call_credit: float = 0.0
    put_credit: float = 0.0

    # Stop levels (in dollars)
    call_stop: float = 0.0
    put_stop: float = 0.0

    # Outcomes
    call_outcome: str = ""   # "expired" | "stopped" | "skipped"
    put_outcome: str = ""
    call_exit_ms: int = 0
    put_exit_ms: int = 0
    call_close_cost: float = 0.0  # dollars paid to close (0 if expired)
    put_close_cost: float = 0.0

    # P&L
    gross_pnl: float = 0.0
    commission: float = 0.0
    net_pnl: float = 0.0

    # Context
    spx_at_entry: float = 0.0
    vix_at_entry: float = 0.0
    call_spread_width: int = 0
    put_spread_width: int = 0


@dataclass
class DayResult:
    date: date
    entries: List[EntryResult] = field(default_factory=list)

    @property
    def gross_pnl(self) -> float:
        return sum(e.gross_pnl for e in self.entries)

    @property
    def commission(self) -> float:
        return sum(e.commission for e in self.entries)

    @property
    def net_pnl(self) -> float:
        return sum(e.net_pnl for e in self.entries)

    @property
    def entries_placed(self) -> int:
        return sum(1 for e in self.entries if e.entry_type != "skipped")

    @property
    def entries_skipped(self) -> int:
        return sum(1 for e in self.entries if e.entry_type == "skipped")

    @property
    def stops_hit(self) -> int:
        return sum(
            (1 if e.call_outcome == "stopped" else 0) +
            (1 if e.put_outcome == "stopped" else 0)
            for e in self.entries
        )


# ── Chain lookup helpers ────────────────────────────────────────────────────

def _build_chain_lookup(chain_df: pd.DataFrame) -> Dict:
    """
    Build a fast lookup: {(strike, right, ms_of_day): (bid, ask, mid)}
    """
    if chain_df.empty:
        return {}
    lookup = {}
    for row in chain_df.itertuples(index=False):
        lookup[(row.strike, row.right, row.ms_of_day)] = (row.bid, row.ask, row.mid)
    return lookup


def _get_bid(lookup: Dict, strike: float, right: str, ms: int) -> float:
    """Get bid price for a specific strike/right/time. Returns 0 if not found."""
    val = lookup.get((strike, right, ms))
    if val is None:
        return 0.0
    bid, ask, mid = val
    return bid


def _get_ask(lookup: Dict, strike: float, right: str, ms: int) -> float:
    """Get ask price for a specific strike/right/time. Returns 0 if not found."""
    val = lookup.get((strike, right, ms))
    if val is None:
        return 0.0
    bid, ask, mid = val
    return ask


def _get_spread_open_credit(lookup: Dict, short_strike: float, long_strike: float,
                             right: str, ms: int) -> float:
    """
    Credit received when opening a spread (selling short, buying long).
    Uses bid for short leg and ask for long leg — the realistic fill prices.
    Returns dollars (× 100 multiplier).
    Long ask of 0 is allowed (deep OTM long is essentially free).
    """
    short_bid = _get_bid(lookup, short_strike, right, ms)
    if short_bid == 0:
        return 0.0  # no bid on short → can't collect credit
    long_ask = _get_ask(lookup, long_strike, right, ms)
    # long_ask == 0 is fine: deep OTM long costs nothing
    return max(0.0, (short_bid - long_ask) * 100)


def _get_spread_close_cost(lookup: Dict, short_strike: float, long_strike: float,
                            right: str, ms: int) -> float:
    """
    Cost to close a spread (buying back short, selling long).
    Uses ask for short leg and bid for long leg — the realistic fill prices.
    Returns dollars (× 100 multiplier).

    When long_bid == 0 the long is worthless (far OTM, no market bid) but you
    still need to buy back the short.  Return short_ask * 100 so stop monitoring
    can fire normally.  Only return 0.0 when short_ask == 0 (no quote at all).
    """
    short_ask = _get_ask(lookup, short_strike, right, ms)
    if short_ask == 0:
        return 0.0
    long_bid = _get_bid(lookup, long_strike, right, ms)
    # long_bid == 0 → long is worthless; close cost = just buying back the short
    return max(0.0, (short_ask - long_bid) * 100)


def _nearest_ms(chain_df: pd.DataFrame, target_ms: int) -> int:
    """Find the nearest available ms_of_day in the chain data."""
    if chain_df.empty:
        return target_ms
    avail = chain_df["ms_of_day"].unique()
    if len(avail) == 0:
        return target_ms
    idx = np.argmin(np.abs(avail - target_ms))
    return int(avail[idx])


def _get_index_price(index_df: pd.DataFrame, target_ms: int) -> float:
    """Get the index price at or just before target_ms."""
    if index_df.empty:
        return 0.0
    before = index_df[index_df["ms_of_day"] <= target_ms]
    if before.empty:
        return float(index_df.iloc[0]["price"])
    row = before.iloc[-1]
    return float(row["price"])


def _compute_ema(prices: pd.Series, period: int) -> pd.Series:
    return prices.ewm(span=period, adjust=False).mean()


# ── Strike selection ────────────────────────────────────────────────────────

def _calc_otm_distance(vix: float, target_delta: float) -> int:
    """
    VIX-adjusted OTM distance for ~target_delta options.
    Mirrors live HYDRA formula exactly.
    """
    base_distance_at_vix15 = 40
    delta_adjustment = 8.0 / target_delta
    vix_factor = max(0.7, min(2.5, vix / 15.0))
    otm = base_distance_at_vix15 * vix_factor * delta_adjustment
    otm = round(otm / 5) * 5
    return max(25, min(120, int(otm)))


def _find_target_delta_otm(
    greeks_df: pd.DataFrame,
    spx: float,
    ms: int,
    side: str,          # "call" or "put"
    target_delta: float,  # e.g. 8.0 → looks for |delta| closest to 0.08
) -> Optional[int]:
    """
    Find OTM distance (in points) for the strike whose |delta| is closest
    to target_delta/100 at the given entry timestamp.

    Returns None if Greeks data is missing or unusable for this side/time.
    Caller falls back to _calc_otm_distance when None is returned.
    """
    right = "C" if side == "call" else "P"
    target = target_delta / 100.0

    # Find nearest available timestamp in Greeks data
    available_ms = greeks_df["ms_of_day"].unique()
    if len(available_ms) == 0:
        return None
    nearest_ms = int(available_ms[np.argmin(np.abs(available_ms - ms))])

    # Filter: right side, correct timestamp, OTM strikes only
    mask = (greeks_df["right"] == right) & (greeks_df["ms_of_day"] == nearest_ms)
    if side == "call":
        mask &= greeks_df["strike"] > spx
    else:
        mask &= greeks_df["strike"] < spx
    sub = greeks_df.loc[mask].copy()

    if sub.empty:
        return None

    # Drop rows with missing/zero delta
    sub = sub[sub["delta"].notna() & (sub["delta"] != 0)]
    if sub.empty:
        return None

    # Find strike with |delta| closest to target
    sub["delta_dist"] = (sub["delta"].abs() - target).abs()
    best = sub.loc[sub["delta_dist"].idxmin()]
    otm = int(round(abs(float(best["strike"]) - spx) / 5) * 5)
    return max(25, min(120, otm))


def _calc_spread_width(vix: float, side: str, cfg: BacktestConfig) -> int:
    """MKT-027/028: VIX-scaled spread width with per-side floors and cap."""
    width = round(vix * cfg.spread_vix_multiplier / 5) * 5
    if side == "put":
        width = max(cfg.put_min_spread_width, width)
    else:
        width = max(cfg.call_min_spread_width, width)
    return min(width, cfg.max_spread_width)


def _scan_for_viable_strike(
    lookup: Dict,
    spx_rounded: float,
    side: str,           # "call" or "put"
    spread_width: int,
    starting_otm: int,
    min_otm: int,
    min_credit: float,   # in dollars
    ms: int,
) -> Tuple[Optional[float], Optional[float], float]:
    """
    MKT-020/022: Progressive OTM tightening.
    Scan from starting_otm inward in 5pt steps until credit >= min_credit.
    Returns (short_strike, long_strike, credit_dollars) or (None, None, 0).
    """
    right = "C" if side == "call" else "P"
    otm = starting_otm
    while otm >= min_otm:
        if side == "call":
            short_s = spx_rounded + otm
            long_s = short_s + spread_width
        else:
            short_s = spx_rounded - otm
            long_s = short_s - spread_width

        credit = _get_spread_open_credit(lookup, short_s, long_s, right, ms)
        if credit >= min_credit:
            return short_s, long_s, credit
        otm -= 5

    return None, None, 0.0


# ── Per-entry simulation ────────────────────────────────────────────────────

_USE_CFG_EARLY_EXIT = object()  # sentinel: "use cfg.early_exit_time_ms()"


def _simulate_entry(
    entry_num: int,
    entry_ms: int,
    is_conditional: bool,
    is_fomc_t1: bool,
    spx_open: float,        # session open price (for conditional threshold)
    chain_df: pd.DataFrame,
    lookup: Dict,
    spx_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    cfg: BacktestConfig,
    monitor_times: List[int],
    is_upday_conditional: bool = False,  # upday put-only trigger enabled for this slot
    day_early_exit_ms=_USE_CFG_EARLY_EXIT,  # override from simulate_day for VIX-gated exit
    greeks_df: Optional[pd.DataFrame] = None,  # real Greeks data (None = use VIX formula)
    force_entry_type: Optional[str] = None,  # "call_only"|"put_only" for replacement entries
    extra_min_otm: int = 0,  # added to min OTM distances (replacement entries)
) -> EntryResult:

    result = EntryResult(entry_num=entry_num, entry_time_ms=entry_ms)

    # ── Skip if entry fires at or after early exit time ──────────────────
    # day_early_exit_ms overrides cfg when simulate_day applies VIX-gated logic.
    # _USE_CFG_EARLY_EXIT sentinel means "fall back to config value".
    early_ms = (cfg.early_exit_time_ms()
                if day_early_exit_ms is _USE_CFG_EARLY_EXIT
                else day_early_exit_ms)
    if early_ms and entry_ms >= early_ms:
        result.entry_type = "skipped"
        result.skip_reason = "entry_at_or_after_early_exit"
        return result

    # ── Get market data at entry time ────────────────────────────────────
    actual_ms = _nearest_ms(chain_df, entry_ms)
    spx = _get_index_price(spx_df, entry_ms)
    vix = _get_index_price(vix_df, entry_ms)

    if spx <= 0 or vix <= 0:
        result.entry_type = "skipped"
        result.skip_reason = "no_market_data"
        return result

    # ── Global VIX gate ───────────────────────────────────────────────────
    max_vix = getattr(cfg, "max_vix_entry", None)
    if max_vix is not None and vix >= max_vix:
        result.entry_type = "skipped"
        result.skip_reason = f"vix_gate ({vix:.1f} >= {max_vix})"
        return result

    result.spx_at_entry = spx
    result.vix_at_entry = vix

    # Round SPX to nearest 5 (SPX strikes in 5pt increments)
    spx_rounded = round(spx / 5) * 5

    # ── Determine entry mode ─────────────────────────────────────────────
    # MKT-035: Conditional down-day entries → call-only if SPX dropped enough
    # MKT-038: FOMC T+1 → force call-only for ALL entries
    # Upday conditional: put-only if SPX rose enough (mirror of MKT-035)
    force_call_only = False
    force_put_only = False
    market_open_ms = 34_200_000  # 9:30 AM ET in ms

    # ── Forced entry type (replacement entries bypass all directional logic)
    if force_entry_type == "call_only":
        force_call_only = True
        result.entry_type = "call_only"
        result.skip_reason = "replacement"
    elif force_entry_type == "put_only":
        force_put_only = True
        result.entry_type = "put_only"
        result.skip_reason = "replacement"
    elif is_fomc_t1 and cfg.fomc_t1_callonly_enabled:
        force_call_only = True
        result.entry_type = "call_only"
        result.skip_reason = "mkt-038"
    elif is_conditional or is_upday_conditional:
        # ── Down-day reference (open or intraday high) ────────────────────
        if is_conditional:
            if getattr(cfg, "downday_reference", "open") == "high":
                mask = (
                    (spx_df["ms_of_day"] >= market_open_ms) &
                    (spx_df["ms_of_day"] <= entry_ms) &
                    (spx_df["price"] > 0)
                )
                down_ref = float(spx_df.loc[mask, "price"].max()) if mask.any() else spx_open
                if down_ref <= 0:
                    down_ref = spx_open
            else:
                down_ref = spx_open
            drop_pct = (down_ref - spx) / down_ref * 100 if down_ref > 0 else 0
        else:
            drop_pct = 0.0

        # ── Up-day reference (open or intraday low) ───────────────────────
        if is_upday_conditional:
            if getattr(cfg, "upday_reference", "open") == "low":
                mask = (
                    (spx_df["ms_of_day"] >= market_open_ms) &
                    (spx_df["ms_of_day"] <= entry_ms) &
                    (spx_df["price"] > 0)
                )
                up_ref = float(spx_df.loc[mask, "price"].min()) if mask.any() else spx_open
                if up_ref <= 0:
                    up_ref = spx_open
            else:
                up_ref = spx_open
            rise_pct = (spx - up_ref) / up_ref * 100 if up_ref > 0 else 0
        else:
            rise_pct = 0.0

        # ── Decide which trigger fires (down-day takes priority) ──────────
        if is_conditional and drop_pct >= cfg.downday_threshold_pct:
            force_call_only = True
            result.entry_type = "call_only"
            result.skip_reason = "mkt-035"
        elif is_upday_conditional and rise_pct >= getattr(cfg, "upday_threshold_pct", 0.3):
            force_put_only = True
            result.entry_type = "put_only"
            result.skip_reason = "upday-035"
        else:
            result.entry_type = "skipped"
            result.skip_reason = (
                f"conditional_no_trigger (drop={drop_pct:.2f}% rise={rise_pct:.2f}%)"
            )
            return result

    # ── Directional filter for E1-E5 base entries ────────────────────────
    # Only fires when neither force flag is set (i.e. FOMC T+1 / MKT-035 /
    # Upday-035 haven't already decided the entry type).
    # Down >= threshold → call-only; Up >= threshold → put-only; else full IC.
    # callside_min_upday_pct is a simpler legacy check (up-only); superseded
    # by the combined base_entry_*_pct params when those are set.
    if not force_put_only and not force_call_only and spx_open > 0:
        # Base entry reference: "open" (default) or "high" (intraday high from open to entry)
        base_ref = getattr(cfg, "base_entry_downday_reference", "open")
        if base_ref == "high":
            mask = (
                (spx_df["ms_of_day"] >= market_open_ms) &
                (spx_df["ms_of_day"] <= entry_ms) &
                (spx_df["price"] > 0)
            )
            base_ref_price = float(spx_df.loc[mask, "price"].max()) if mask.any() else spx_open
            if base_ref_price <= 0:
                base_ref_price = spx_open
        else:
            base_ref_price = spx_open
        move_pct = (spx - base_ref_price) / base_ref_price * 100  # positive = up, negative = down

        down_thresh = getattr(cfg, "base_entry_downday_callonly_pct", None)
        up_thresh   = getattr(cfg, "base_entry_upday_putonly_pct", None)

        if down_thresh is not None and move_pct <= -down_thresh:
            force_call_only = True
        elif up_thresh is not None and move_pct >= up_thresh:
            force_put_only = True
        else:
            # Legacy single-direction filter (callside_min_upday_pct)
            legacy_up = getattr(cfg, "callside_min_upday_pct", None)
            if legacy_up is not None and move_pct < legacy_up:
                force_put_only = True

    # ── Strike calculation ───────────────────────────────────────────────
    call_spread_width = _calc_spread_width(vix, "call", cfg)
    put_spread_width = _calc_spread_width(vix, "put", cfg)

    # OTM base distance: real delta lookup when Greeks available, else VIX formula
    if greeks_df is not None and not greeks_df.empty:
        call_otm_base = _find_target_delta_otm(greeks_df, spx, actual_ms, "call", cfg.target_delta)
        put_otm_base  = _find_target_delta_otm(greeks_df, spx, actual_ms, "put",  cfg.target_delta)
        # Fall back to VIX formula per-side if Greeks lookup returned None
        vix_otm = _calc_otm_distance(vix, cfg.target_delta)
        if call_otm_base is None:
            call_otm_base = vix_otm
        if put_otm_base is None:
            put_otm_base = vix_otm
    else:
        vix_otm = _calc_otm_distance(vix, cfg.target_delta)
        call_otm_base = vix_otm
        put_otm_base  = vix_otm

    call_starting_otm = int(round((call_otm_base * cfg.call_starting_otm_multiplier) / 5) * 5)
    call_starting_otm = max(25, min(240, call_starting_otm))
    put_starting_otm = int(round((put_otm_base * cfg.put_starting_otm_multiplier) / 5) * 5)
    put_starting_otm = max(25, min(240, put_starting_otm))

    result.call_spread_width = call_spread_width
    result.put_spread_width = put_spread_width

    # Effective min OTM distances (increased for replacement entries)
    eff_min_call = cfg.min_call_otm_distance + extra_min_otm
    eff_min_put = cfg.min_put_otm_distance + extra_min_otm

    # ── Credit gate + progressive tightening (MKT-011/020/022) ──────────
    call_short = call_long = None
    call_credit = 0.0
    put_short = put_long = None
    put_credit = 0.0

    if force_put_only:
        # Forced put-only (upday conditional) — only scan put side
        put_starting_otm_original = put_starting_otm
        put_short, put_long, put_credit = _scan_for_viable_strike(
            lookup, spx_rounded, "put", put_spread_width,
            put_starting_otm, eff_min_put,
            cfg.put_credit_floor * 100, actual_ms
        )

    elif not force_call_only:
        # Scan for viable call strike
        call_short, call_long, call_credit = _scan_for_viable_strike(
            lookup, spx_rounded, "call", call_spread_width,
            call_starting_otm, eff_min_call,
            cfg.min_call_credit * 100, actual_ms
        )
        # Call tightening retries (mirror of MKT-040 for put side)
        if call_short is None and getattr(cfg, 'call_tighten_retries', 0) > 0:
            call_starting_otm_retry = call_starting_otm
            for _ in range(cfg.call_tighten_retries):
                call_starting_otm_retry = max(eff_min_call, call_starting_otm_retry - cfg.call_tighten_step)
                call_short, call_long, call_credit = _scan_for_viable_strike(
                    lookup, spx_rounded, "call", call_spread_width,
                    call_starting_otm_retry, eff_min_call,
                    cfg.min_call_credit * 100, actual_ms
                )
                if call_short is not None:
                    break
        # With MKT-029 fallback (graduated floor)
        if call_short is None and cfg.call_credit_floor > 0:
            call_short, call_long, call_credit = _scan_for_viable_strike(
                lookup, spx_rounded, "call", call_spread_width,
                call_starting_otm, eff_min_call,
                cfg.call_credit_floor * 100, actual_ms
            )

        # Scan for viable put strike
        put_starting_otm_original = put_starting_otm
        put_short, put_long, put_credit = _scan_for_viable_strike(
            lookup, spx_rounded, "put", put_spread_width,
            put_starting_otm, eff_min_put,
            cfg.min_put_credit * 100, actual_ms
        )
        # MKT-040: put non-viable → tighten retries (5pt closer each time) before calling it call-only
        if put_short is None:
            for _ in range(cfg.put_tighten_retries):
                put_starting_otm = max(eff_min_put, put_starting_otm - cfg.put_tighten_step)
                put_short, put_long, put_credit = _scan_for_viable_strike(
                    lookup, spx_rounded, "put", put_spread_width,
                    put_starting_otm, eff_min_put,
                    cfg.min_put_credit * 100, actual_ms
                )
                if put_short is not None:
                    break
        # MKT-029 put fallback floor: scan full original range with lower credit threshold
        if put_short is None and cfg.put_credit_floor > 0:
            put_short, put_long, put_credit = _scan_for_viable_strike(
                lookup, spx_rounded, "put", put_spread_width,
                put_starting_otm_original, eff_min_put,
                cfg.put_credit_floor * 100, actual_ms
            )

    elif force_call_only:
        # Forced call-only — only scan call side
        call_short, call_long, call_credit = _scan_for_viable_strike(
            lookup, spx_rounded, "call", call_spread_width,
            call_starting_otm, eff_min_call,
            cfg.call_credit_floor * 100, actual_ms
        )

    # ── Determine entry type based on what's viable ───────────────────────
    if force_put_only:
        if put_short is None:
            result.entry_type = "skipped"
            result.skip_reason = "upday-035_no_put_credit"
            return result
        # For put-only stop: put_credit + theoretical_call + buffer (mirrors call-only formula)
        put_stop = put_credit + getattr(cfg, "upday_theoretical_call_credit", 0.0) + cfg.put_stop_buffer
        put_stop = max(put_stop, cfg.min_stop_level)
        result.entry_type = "put_only"
        result.short_put = put_short
        result.long_put = put_long
        result.put_credit = put_credit
        result.put_stop = put_stop
        result.call_outcome = "skipped"

    elif force_call_only:
        if call_short is None:
            result.entry_type = "skipped"
            result.skip_reason = f"mkt-011_call_only_no_credit (forced: {result.skip_reason})"
            return result
        result.entry_type = "call_only"
        # For call-only stop: call_credit + theoretical_put + buffer
        call_stop = call_credit + cfg.downday_theoretical_put_credit + cfg.call_stop_buffer
        call_stop = max(call_stop, cfg.min_stop_level)
        result.short_call = call_short
        result.long_call = call_long
        result.call_credit = call_credit
        result.call_stop = call_stop
        result.put_outcome = "skipped"

    elif call_short is not None and put_short is not None:
        result.entry_type = "full_ic"
        total_credit = call_credit + put_credit
        call_stop = total_credit + cfg.call_stop_buffer
        put_stop = total_credit + cfg.put_stop_buffer
        call_stop = max(call_stop, cfg.min_stop_level)
        put_stop = max(put_stop, cfg.min_stop_level)
        result.short_call = call_short
        result.long_call = call_long
        result.short_put = put_short
        result.long_put = put_long
        result.call_credit = call_credit
        result.put_credit = put_credit
        result.call_stop = call_stop
        result.put_stop = put_stop

    elif call_short is None and put_short is not None:
        # MKT-032/039: put-only if VIX < threshold and one-sided entries enabled
        if not cfg.one_sided_entries_enabled or vix >= cfg.put_only_max_vix:
            result.entry_type = "skipped"
            result.skip_reason = f"mkt-011_call_non_viable_vix_too_high ({vix:.1f} >= {cfg.put_only_max_vix})"
            return result
        result.entry_type = "put_only"
        # For put-only stop: put_credit + theoretical_call + buffer (mirrors call-only formula)
        put_stop = put_credit + getattr(cfg, "upday_theoretical_call_credit", 0.0) + cfg.put_stop_buffer
        put_stop = max(put_stop, cfg.min_stop_level)
        result.short_put = put_short
        result.long_put = put_long
        result.put_credit = put_credit
        result.put_stop = put_stop
        result.call_outcome = "skipped"

    elif call_short is not None and put_short is None:
        # MKT-040: call-only when put non-viable (only if one-sided entries enabled)
        if not cfg.one_sided_entries_enabled:
            result.entry_type = "skipped"
            result.skip_reason = "one_sided_disabled_put_non_viable"
            return result
        call_stop = call_credit + cfg.downday_theoretical_put_credit + cfg.call_stop_buffer
        call_stop = max(call_stop, cfg.min_stop_level)
        result.entry_type = "call_only"
        result.skip_reason = "mkt-040"
        result.short_call = call_short
        result.long_call = call_long
        result.call_credit = call_credit
        result.call_stop = call_stop
        result.put_outcome = "skipped"

    else:
        result.entry_type = "skipped"
        result.skip_reason = "mkt-011_both_non_viable"
        return result

    # ── Stop monitoring + early exit ──────────────────────────────────────
    call_stopped = False
    put_stopped = False
    # Track which sides are active
    call_active = result.entry_type in ("full_ic", "call_only")
    put_active = result.entry_type in ("full_ic", "put_only")

    price_stop_pts = getattr(cfg, "price_based_stop_points", None)
    price_stop_inward = getattr(cfg, "price_stop_inward", True)

    # Trailing stop state (only for credit-based stops)
    _trailing_en = getattr(cfg, "trailing_stop_enabled", False) and price_stop_pts is None
    _trailing_trig = getattr(cfg, "trailing_stop_trigger_decay", 0.50)
    _trail_call_buf = getattr(cfg, "trailing_stop_call_buffer", 10.0)
    _trail_put_buf = getattr(cfg, "trailing_stop_put_buffer", 50.0)
    _call_trail = False  # has call trailing been triggered?
    _put_trail = False   # has put trailing been triggered?

    # early_ms already computed above (used to skip entries at/after exit time)
    for monitor_ms in monitor_times:
        if monitor_ms <= entry_ms:
            continue  # don't check before entry

        # Get SPX price once per bar when using price-based stops
        spx_now = _get_index_price(spx_df, monitor_ms) if price_stop_pts is not None else 0.0

        # Stop checks: use ask-based close cost (realistic fill price)
        slip = getattr(cfg, "stop_slippage_per_leg", 0.0) * 2  # 2 legs, value already in dollars
        # Spread value cap: close cost can't exceed spread width × 100
        cap_at_stop = getattr(cfg, "spread_value_cap_at_stop", False)
        call_cap = result.call_spread_width * 100 if cap_at_stop else float('inf')
        put_cap = result.put_spread_width * 100 if cap_at_stop else float('inf')
        if call_active and not call_stopped:
            if price_stop_pts is not None:
                if spx_now > 0 and spx_now >= result.short_call - (price_stop_pts if price_stop_inward else -price_stop_pts):
                    cv = _get_spread_close_cost(lookup, result.short_call, result.long_call, "C", monitor_ms)
                    if cv > 0:
                        call_stopped = True
                        result.call_outcome = "stopped"
                        result.call_exit_ms = monitor_ms
                        result.call_close_cost = min(cv + slip, call_cap)
            else:
                cv = _get_spread_close_cost(lookup, result.short_call, result.long_call, "C", monitor_ms)
                # ── Trailing stop: tighten call side when decay threshold reached
                if _trailing_en and not _call_trail and cv > 0 and result.call_credit > 0:
                    if cv <= result.call_credit * _trailing_trig:
                        _call_trail = True
                        if result.entry_type == "full_ic":
                            result.call_stop = (result.call_credit + result.put_credit) + _trail_call_buf
                        else:  # call_only
                            result.call_stop = result.call_credit + cfg.downday_theoretical_put_credit + _trail_call_buf
                        result.call_stop = max(result.call_stop, cfg.min_stop_level)
                # ── Stop check (may use tightened level)
                if cv > 0 and cv >= result.call_stop:
                    call_stopped = True
                    result.call_outcome = "stopped"
                    result.call_exit_ms = monitor_ms
                    result.call_close_cost = min(cv + slip, call_cap)

        if put_active and not put_stopped:
            if price_stop_pts is not None:
                if spx_now > 0 and spx_now <= result.short_put + (price_stop_pts if price_stop_inward else -price_stop_pts):
                    pv = _get_spread_close_cost(lookup, result.short_put, result.long_put, "P", monitor_ms)
                    if pv > 0:
                        put_stopped = True
                        result.put_outcome = "stopped"
                        result.put_exit_ms = monitor_ms
                        result.put_close_cost = min(pv + slip, put_cap)
            else:
                pv = _get_spread_close_cost(lookup, result.short_put, result.long_put, "P", monitor_ms)
                # ── Trailing stop: tighten put side when decay threshold reached
                if _trailing_en and not _put_trail and pv > 0 and result.put_credit > 0:
                    if pv <= result.put_credit * _trailing_trig:
                        _put_trail = True
                        if result.entry_type == "full_ic":
                            result.put_stop = (result.call_credit + result.put_credit) + _trail_put_buf
                        else:  # put_only
                            result.put_stop = result.put_credit + getattr(cfg, "upday_theoretical_call_credit", 0.0) + _trail_put_buf
                        result.put_stop = max(result.put_stop, cfg.min_stop_level)
                # ── Stop check (may use tightened level)
                if pv > 0 and pv >= result.put_stop:
                    put_stopped = True
                    result.put_outcome = "stopped"
                    result.put_exit_ms = monitor_ms
                    result.put_close_cost = min(pv + slip, put_cap)

        # Early exit: close any remaining open sides at this bar
        if early_ms and monitor_ms >= early_ms:
            if call_active and not call_stopped:
                cv = _get_spread_close_cost(lookup, result.short_call, result.long_call, "C", monitor_ms)
                result.call_outcome = "early_exit"
                result.call_exit_ms = monitor_ms
                result.call_close_cost = cv  # 0.0 if quote missing (treated as worthless)
                call_stopped = True
            if put_active and not put_stopped:
                pv = _get_spread_close_cost(lookup, result.short_put, result.long_put, "P", monitor_ms)
                result.put_outcome = "early_exit"
                result.put_exit_ms = monitor_ms
                result.put_close_cost = pv
                put_stopped = True
            break  # don't monitor past early exit time

        if (not call_active or call_stopped) and (not put_active or put_stopped):
            break  # both sides resolved

    # ── Settlement (4 PM) ─────────────────────────────────────────────────
    # Get SPX settlement price from last available bar (represents 4 PM close).
    # SPX options (0DTE) settle to the closing price of the index.
    # If spread expires ITM, the intrinsic value is the settlement cost.
    # No commission at expiry — cash settlement is automatic, no transaction.
    spx_settle = _get_index_price(spx_df, monitor_times[-1]) if monitor_times else 0.0

    if call_active and not call_stopped:
        result.call_outcome = "expired"
        if spx_settle > 0 and result.short_call > 0 and spx_settle > result.short_call:
            itm_amt = min(spx_settle - result.short_call, result.long_call - result.short_call)
            intrinsic = round(itm_amt * 100, 2)
            if price_stop_pts is not None:
                # Price-based stop: intrinsic is the correct cost (no credit-level cap).
                # Stop should have fired at short_call + price_stop_pts; if settlement is
                # deeper ITM, the live bot was stopped earlier — intrinsic ≤ spread_width×100.
                result.call_close_cost = intrinsic
            else:
                # Credit-based stop: cap at stop level (live bot would have stopped before expiry).
                result.call_close_cost = min(intrinsic, result.call_stop)
        else:
            result.call_close_cost = 0.0

    if put_active and not put_stopped:
        result.put_outcome = "expired"
        if spx_settle > 0 and result.short_put > 0 and spx_settle < result.short_put:
            itm_amt = min(result.short_put - spx_settle, result.short_put - result.long_put)
            intrinsic = round(itm_amt * 100, 2)
            if price_stop_pts is not None:
                result.put_close_cost = intrinsic
            else:
                result.put_close_cost = min(intrinsic, result.put_stop)
        else:
            result.put_close_cost = 0.0

    # ── P&L ───────────────────────────────────────────────────────────────
    # Opening commission: always charged ($2.50/leg × legs placed)
    # Closing commission: only when actively closed (stopped or early_exit)
    # Expiry: no closing commission — legs expire worthless, no transaction needed
    legs_placed = 0
    legs_closed = 0
    gross = 0.0

    if call_active:
        legs_placed += 2
        gross += result.call_credit - result.call_close_cost
        if result.call_outcome in ("stopped", "early_exit"):
            legs_closed += 2

    if put_active:
        legs_placed += 2
        gross += result.put_credit - result.put_close_cost
        if result.put_outcome in ("stopped", "early_exit"):
            legs_closed += 2

    commission = cfg.commission_per_leg * (legs_placed + legs_closed) * cfg.contracts
    result.gross_pnl = gross * cfg.contracts
    result.commission = commission
    result.net_pnl = result.gross_pnl - commission

    return result


# ── Net-return threshold exit ───────────────────────────────────────────────

def _apply_return_threshold(
    entries: List[EntryResult],
    lookup: Dict,
    monitor_times: List[int],
    cfg: BacktestConfig,
) -> List[EntryResult]:
    """
    Post-process entries to apply net-return-threshold early exit.

    Scans 5-min bars chronologically.  At each bar, computes what net P&L
    would be if every surviving open side were closed there (using ask-based
    close costs, same as stop monitoring).  When:

        net_pnl_at_bar / total_credit_collected_so_far  >=  net_return_exit_pct

    that bar becomes the exit time:
      - Entries placed before exit bar whose sides are still open → "early_exit"
      - Entries placed at or after exit bar → "skipped"

    Per-entry stops that fired before the exit bar are kept as-is.
    Stops that would have fired after the exit bar are superseded by
    the earlier "early_exit".

    Commission rules (unchanged):
      - Opening legs: always charged
      - Closing legs: charged for "stopped" and "early_exit", not for expiry
    """
    threshold = getattr(cfg, "net_return_exit_pct", None)
    if threshold is None or threshold <= 0:
        return entries

    placed = [e for e in entries if e.entry_type != "skipped"]
    if not placed:
        return entries

    exit_ms: Optional[int] = None

    # ── Phase 1: find the first bar where the return threshold is crossed ──
    for bar_ms in monitor_times:
        # Only entries already placed before this bar
        active = [e for e in placed if e.entry_time_ms < bar_ms]
        if not active:
            continue

        total_credit = 0.0
        total_net_pnl = 0.0

        for e in active:
            call_active = e.entry_type in ("full_ic", "call_only")
            put_active  = e.entry_type in ("full_ic", "put_only")

            legs_placed = (2 if call_active else 0) + (2 if put_active else 0)
            entry_credit = (
                (e.call_credit if call_active else 0.0) +
                (e.put_credit  if put_active  else 0.0)
            ) * cfg.contracts
            total_credit += entry_credit

            gross = 0.0
            hyp_close_legs = 0

            if call_active:
                already_closed = (
                    e.call_outcome in ("stopped", "early_exit")
                    and e.call_exit_ms > 0
                    and e.call_exit_ms <= bar_ms
                )
                if already_closed:
                    gross += e.call_credit - e.call_close_cost
                    hyp_close_legs += 2
                else:
                    # Still open (outcome may be "expired" — that's at 4 PM, not yet)
                    cv = _get_spread_close_cost(
                        lookup, e.short_call, e.long_call, "C", bar_ms
                    )
                    gross += e.call_credit - cv
                    hyp_close_legs += 2

            if put_active:
                already_closed = (
                    e.put_outcome in ("stopped", "early_exit")
                    and e.put_exit_ms > 0
                    and e.put_exit_ms <= bar_ms
                )
                if already_closed:
                    gross += e.put_credit - e.put_close_cost
                    hyp_close_legs += 2
                else:
                    pv = _get_spread_close_cost(
                        lookup, e.short_put, e.long_put, "P", bar_ms
                    )
                    gross += e.put_credit - pv
                    hyp_close_legs += 2

            commission = (
                cfg.commission_per_leg * (legs_placed + hyp_close_legs) * cfg.contracts
            )
            total_net_pnl += gross * cfg.contracts - commission

        if total_credit > 0 and total_net_pnl / total_credit >= threshold:
            exit_ms = bar_ms
            break

    if exit_ms is None:
        return entries  # threshold never reached; hold everything to expiry/stop

    # ── Phase 2: post-process — apply the exit at exit_ms ─────────────────
    for e in entries:
        if e.entry_type == "skipped":
            continue

        call_active = e.entry_type in ("full_ic", "call_only")
        put_active  = e.entry_type in ("full_ic", "put_only")

        # Entries that would have been placed at or after exit_ms: skip them
        if e.entry_time_ms >= exit_ms:
            e.entry_type      = "skipped"
            e.skip_reason     = f"net_return_threshold_{threshold:.0%}"
            e.short_call      = e.long_call = e.short_put = e.long_put = 0.0
            e.call_credit     = e.put_credit  = 0.0
            e.call_stop       = e.put_stop    = 0.0
            e.call_outcome    = e.put_outcome = ""
            e.call_close_cost = e.put_close_cost = 0.0
            e.gross_pnl = e.commission = e.net_pnl = 0.0
            continue

        # Entries placed before exit_ms: fix outcomes for sides still open
        legs_placed = (2 if call_active else 0) + (2 if put_active else 0)
        gross       = 0.0
        legs_closed = 0

        if call_active:
            resolved_before = (
                e.call_outcome in ("stopped", "early_exit")
                and e.call_exit_ms > 0
                and e.call_exit_ms <= exit_ms
            )
            if resolved_before:
                gross += e.call_credit - e.call_close_cost
                legs_closed += 2
            else:
                cv = _get_spread_close_cost(
                    lookup, e.short_call, e.long_call, "C", exit_ms
                )
                e.call_outcome    = "early_exit"
                e.call_exit_ms    = exit_ms
                e.call_close_cost = cv
                gross += e.call_credit - cv
                legs_closed += 2

        if put_active:
            resolved_before = (
                e.put_outcome in ("stopped", "early_exit")
                and e.put_exit_ms > 0
                and e.put_exit_ms <= exit_ms
            )
            if resolved_before:
                gross += e.put_credit - e.put_close_cost
                legs_closed += 2
            else:
                pv = _get_spread_close_cost(
                    lookup, e.short_put, e.long_put, "P", exit_ms
                )
                e.put_outcome    = "early_exit"
                e.put_exit_ms    = exit_ms
                e.put_close_cost = pv
                gross += e.put_credit - pv
                legs_closed += 2

        commission  = (
            cfg.commission_per_leg * (legs_placed + legs_closed) * cfg.contracts
        )
        e.gross_pnl = gross * cfg.contracts
        e.commission = commission
        e.net_pnl   = e.gross_pnl - commission

    return entries


# ── VIX regime helpers ─────────────────────────────────────────────────────

def _get_vix_regime(vix: float, breakpoints: List[float]) -> int:
    """Return regime index: 0 = below first bp, len(breakpoints) = above last."""
    for i, bp in enumerate(breakpoints):
        if vix < bp:
            return i
    return len(breakpoints)


def _apply_vix_regime(cfg: BacktestConfig, vix: float) -> BacktestConfig:
    """Return a copy of cfg with VIX-regime overrides applied. No-op if disabled."""
    if not getattr(cfg, "vix_regime_enabled", False):
        return cfg

    breakpoints = cfg.vix_regime_breakpoints
    regime = _get_vix_regime(vix, breakpoints)
    day_cfg = deepcopy(cfg)  # deepcopy to avoid shared List mutation across days

    _overrides = {
        "put_stop_buffer":  getattr(cfg, "vix_regime_put_stop_buffer", []),
        "call_stop_buffer": getattr(cfg, "vix_regime_call_stop_buffer", []),
        "min_put_credit":   getattr(cfg, "vix_regime_min_put_credit", []),
        "min_call_credit":  getattr(cfg, "vix_regime_min_call_credit", []),
    }
    for attr, values in _overrides.items():
        if regime < len(values) and values[regime] is not None:
            setattr(day_cfg, attr, values[regime])

    return day_cfg


# ── Replacement entry generator ────────────────────────────────────────────

def _generate_replacements(
    entries: List[EntryResult],
    cfg: BacktestConfig,
    chain_df: pd.DataFrame,
    lookup: Dict,
    spx_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    monitor_times: List[int],
    spx_open: float,
    is_fomc_t1: bool,
    day_early_exit_ms,
    greeks_df: Optional[pd.DataFrame],
) -> List[EntryResult]:
    """Generate replacement entries for sides that were stopped before the cutoff."""
    max_repl = cfg.replacement_entry_max_per_day
    delay_ms = cfg.replacement_delay_ms()
    extra_otm = cfg.replacement_entry_extra_otm
    cutoff_ms = cfg.replacement_cutoff_ms()

    # Collect all stopped sides before cutoff, sorted by stop time
    stopped: List[Tuple[str, int, int]] = []  # (force_type, stop_ms, orig_entry_num)
    for e in entries:
        if e.entry_type == "skipped":
            continue
        if e.entry_type in ("full_ic", "call_only") and e.call_outcome == "stopped":
            if e.call_exit_ms > 0 and e.call_exit_ms < cutoff_ms:
                stopped.append(("call_only", e.call_exit_ms, e.entry_num))
        if e.entry_type in ("full_ic", "put_only") and e.put_outcome == "stopped":
            if e.put_exit_ms > 0 and e.put_exit_ms < cutoff_ms:
                stopped.append(("put_only", e.put_exit_ms, e.entry_num))

    stopped.sort(key=lambda x: x[1])

    replacements: List[EntryResult] = []
    for force_type, stop_ms, orig_num in stopped:
        if len(replacements) >= max_repl:
            break
        repl_ms = stop_ms + delay_ms
        if repl_ms >= cutoff_ms:
            continue

        res = _simulate_entry(
            entry_num=20 + orig_num,
            entry_ms=repl_ms,
            is_conditional=False,
            is_fomc_t1=is_fomc_t1,
            spx_open=spx_open,
            chain_df=chain_df,
            lookup=lookup,
            spx_df=spx_df,
            vix_df=vix_df,
            cfg=cfg,
            monitor_times=monitor_times,
            day_early_exit_ms=day_early_exit_ms,
            greeks_df=greeks_df,
            force_entry_type=force_type,
            extra_min_otm=extra_otm,
        )
        if res.entry_type != "skipped":
            replacements.append(res)

    return replacements


# ── Per-day simulation ─────────────────────────────────────────────────────

def simulate_day(
    trading_date: date,
    cfg: BacktestConfig,
    cache_dir: Path,
    fomc_t1_dates: set,
) -> Optional[DayResult]:

    # ── Day-of-week filter (skip before loading any data) ─────────────
    if trading_date.weekday() in getattr(cfg, "skip_weekdays", []):
        return None

    # Load data — use 1-min or 5-min subfolder based on config
    resolution = getattr(cfg, "data_resolution", "5min")
    if resolution == "1min":
        opts_dir = cache_dir / "options_1min"
        grk_dir = cache_dir / "greeks_1min"
    else:
        opts_dir = cache_dir / "options"
        grk_dir = cache_dir / "greeks"

    chain_df = _load_chain(trading_date, opts_dir)
    if chain_df.empty:
        return None

    # Real Greeks mode (strict): skip day entirely if no Greeks file cached
    greeks_df: Optional[pd.DataFrame] = None
    if getattr(cfg, "use_real_greeks", False):
        greeks_df = _load_greeks(trading_date, grk_dir)
        if greeks_df.empty:
            return None  # strict mode — no approximation fallback

    spx_df = load_index_day("SPX", trading_date, cache_dir)
    vix_df = load_index_day("VIX", trading_date, cache_dir)
    if spx_df.empty or vix_df.empty:
        return None

    lookup = _build_chain_lookup(chain_df)
    monitor_times = sorted(chain_df["ms_of_day"].unique().tolist())

    # Session open price (first valid 1-min SPX bar at/after 9:30)
    spx_open_row = spx_df[spx_df["price"] > 0]
    spx_open = float(spx_open_row.iloc[0]["price"]) if not spx_open_row.empty else 0.0

    is_fomc_t1 = trading_date in fomc_t1_dates

    day = DayResult(date=trading_date)

    # ── VIX-conditional early exit: decide effective exit time for today ──
    # If vix_early_exit_threshold is set, only apply early_exit_time on days
    # when VIX at the open is >= threshold.  On calm days, hold to 4 PM.
    vix_threshold = getattr(cfg, "vix_early_exit_threshold", None)
    if vix_threshold is not None:
        # Use VIX at 9:45 AM (first bar after open volatility settles) as day VIX
        open_vix_ms = 9 * 3600000 + 45 * 60000  # 9:45 AM in ms
        day_vix = _get_index_price(vix_df, open_vix_ms)
        if day_vix <= 0:
            # Fallback: first valid VIX bar of the day
            vix_rows = vix_df[vix_df["price"] > 0]
            day_vix = float(vix_rows.iloc[0]["price"]) if not vix_rows.empty else 0.0
        day_early_exit_ms = cfg.early_exit_time_ms() if day_vix >= vix_threshold else None
    else:
        day_early_exit_ms = _USE_CFG_EARLY_EXIT  # use cfg as-is (no VIX gate)

    # ── Protection gate helpers ──────────────────────────────────────────
    market_open_ms = 9 * 3600000 + 30 * 60000  # 9:30 AM

    # VIX at open for spike detection
    vix_at_open = _get_index_price(vix_df, market_open_ms + 15 * 60000)  # 9:45 AM
    if vix_at_open <= 0:
        vix_rows = vix_df[vix_df["price"] > 0]
        vix_at_open = float(vix_rows.iloc[0]["price"]) if not vix_rows.empty else 0.0

    # Expected daily move for whipsaw filter
    expected_move = spx_open * (vix_at_open / 100) / (252 ** 0.5) if spx_open > 0 and vix_at_open > 0 else 0

    # ── VIX regime: apply per-regime config overrides ─────────────────
    cfg = _apply_vix_regime(cfg, vix_at_open)
    # Re-read protection params from (possibly overridden) cfg
    daily_loss_limit = getattr(cfg, "daily_loss_limit", None)
    vix_spike_pts = getattr(cfg, "vix_spike_skip_points", None)
    whipsaw_mult = getattr(cfg, "whipsaw_range_skip_mult", None)

    def _should_skip_entry(entry_ms: int) -> Optional[str]:
        """Check protection gates. Returns skip reason or None."""
        # Daily loss limit — ONLY count REALIZED losses (entries stopped BEFORE this entry time).
        # Entries still open at entry_ms have unknown outcomes — no lookahead bias.
        if daily_loss_limit is not None:
            realized_pnl = 0.0
            for e in day.entries:
                if e.entry_type == "skipped":
                    continue
                # Check if this entry is fully resolved before current entry_ms
                call_resolved = (e.call_outcome == "stopped" and e.call_exit_ms > 0 and e.call_exit_ms < entry_ms)
                put_resolved = (e.put_outcome == "stopped" and e.put_exit_ms > 0 and e.put_exit_ms < entry_ms)
                call_active = e.entry_type in ("full_ic", "call_only")
                put_active = e.entry_type in ("full_ic", "put_only")

                # Only count P&L from sides that are DONE (stopped before now)
                # Sides still open or expired later are unknown at this point
                if call_active and call_resolved:
                    realized_pnl += e.call_credit - e.call_close_cost
                if put_active and put_resolved:
                    realized_pnl += e.put_credit - e.put_close_cost
                # Commission: count opening commission for all placed entries,
                # closing commission only for sides stopped before now
                if e.entry_type != "skipped":
                    legs_placed = 2 if e.entry_type in ("call_only", "put_only") else 4
                    legs_closed = 0
                    if call_active and call_resolved: legs_closed += 2
                    if put_active and put_resolved: legs_closed += 2
                    realized_pnl -= (legs_placed + legs_closed) * getattr(cfg, "commission_per_leg", 2.50) * getattr(cfg, "contracts", 1)

            if realized_pnl <= daily_loss_limit:
                return f"daily_loss_limit (realized {realized_pnl:.0f} <= {daily_loss_limit:.0f})"

        # VIX spike gate
        if vix_spike_pts is not None:
            vix_now = _get_index_price(vix_df, entry_ms)
            if vix_now > 0 and vix_at_open > 0 and (vix_now - vix_at_open) >= vix_spike_pts:
                return f"vix_spike ({vix_now:.1f} vs open {vix_at_open:.1f}, +{vix_now-vix_at_open:.1f}pts)"

        # Anti-whipsaw filter
        if whipsaw_mult is not None and expected_move > 0:
            mask = (
                (spx_df["ms_of_day"] >= market_open_ms) &
                (spx_df["ms_of_day"] <= entry_ms) &
                (spx_df["price"] > 0)
            )
            if mask.any():
                prices = spx_df.loc[mask, "price"]
                intraday_range = float(prices.max()) - float(prices.min())
                if intraday_range > whipsaw_mult * expected_move:
                    return f"whipsaw (range={intraday_range:.0f} > {whipsaw_mult}×EM={whipsaw_mult*expected_move:.0f})"

        return None

    # ── Base entries (E1-E5) ─────────────────────────────────────────────
    entry_ms_list = cfg.entry_times_as_ms()

    # ── Max entries cap (VIX regime + day-of-week) ────────────────────
    _max_e = len(entry_ms_list)
    if getattr(cfg, "vix_regime_enabled", False):
        _regime = _get_vix_regime(vix_at_open, cfg.vix_regime_breakpoints)
        _rm = cfg.vix_regime_max_entries
        if _regime < len(_rm) and _rm[_regime] is not None:
            _max_e = min(_max_e, _rm[_regime])
    _dow_max = getattr(cfg, "dow_max_entries", {}).get(trading_date.weekday())
    if _dow_max is not None:
        _max_e = min(_max_e, _dow_max)
    if _max_e < len(entry_ms_list):
        entry_ms_list = entry_ms_list[:_max_e]

    movement_pct = getattr(cfg, "movement_entry_pct", None)

    if movement_pct is None:
        # Standard time-based entries
        for i, entry_ms in enumerate(entry_ms_list, 1):
            skip_reason = _should_skip_entry(entry_ms)
            if skip_reason:
                skip_res = EntryResult(entry_num=i, entry_time_ms=entry_ms,
                                       entry_type="skipped", skip_reason=skip_reason)
                day.entries.append(skip_res)
                continue
            res = _simulate_entry(
                entry_num=i,
                entry_ms=entry_ms,
                is_conditional=False,
                is_fomc_t1=is_fomc_t1,
                spx_open=spx_open,
                chain_df=chain_df,
                lookup=lookup,
                spx_df=spx_df,
                vix_df=vix_df,
                cfg=cfg,
                monitor_times=monitor_times,
                day_early_exit_ms=day_early_exit_ms,
                greeks_df=greeks_df,
            )
            day.entries.append(res)
    else:
        # Movement-triggered entries: each slot fires when SPX moves >= movement_pct
        # from the previous entry's SPX price.  Scheduled time is a hard fallback.
        last_ref_spx = spx_open
        next_slot = 0
        fired_at_ms = {}  # slot_idx → actual bar_ms when fired

        for bar_ms in monitor_times:
            if next_slot >= len(entry_ms_list):
                break
            scheduled_ms = entry_ms_list[next_slot]
            bar_spx = _get_index_price(spx_df, bar_ms)
            if bar_spx <= 0:
                continue
            move_pct = (abs(bar_spx - last_ref_spx) / last_ref_spx * 100
                        if last_ref_spx > 0 else 0.0)
            if bar_ms >= scheduled_ms or move_pct >= movement_pct:
                fired_at_ms[next_slot] = bar_ms
                res = _simulate_entry(
                    entry_num=next_slot + 1,
                    entry_ms=bar_ms,           # actual trigger time (may be earlier than scheduled)
                    is_conditional=False,
                    is_fomc_t1=is_fomc_t1,
                    spx_open=spx_open,
                    chain_df=chain_df,
                    lookup=lookup,
                    spx_df=spx_df,
                    vix_df=vix_df,
                    cfg=cfg,
                    monitor_times=monitor_times,
                    day_early_exit_ms=day_early_exit_ms,
                    greeks_df=greeks_df,
                )
                day.entries.append(res)
                last_ref_spx = bar_spx         # update reference for next slot
                next_slot += 1

    # ── Conditional entries (E6/E7) ─────────────────────────────────────
    cond_times = cfg.conditional_times_as_ms()
    cond_down = [cfg.conditional_e6_enabled, cfg.conditional_e7_enabled]
    cond_up = [
        getattr(cfg, "conditional_upday_e6_enabled", False),
        getattr(cfg, "conditional_upday_e7_enabled", False),
    ]
    for i, (cond_ms, down_en, up_en) in enumerate(zip(cond_times, cond_down, cond_up), 6):
        if not down_en and not up_en:
            continue
        skip_reason = _should_skip_entry(cond_ms)
        if skip_reason:
            skip_res = EntryResult(entry_num=i, entry_time_ms=cond_ms,
                                   entry_type="skipped", skip_reason=skip_reason)
            day.entries.append(skip_res)
            continue
        res = _simulate_entry(
            entry_num=i,
            entry_ms=cond_ms,
            is_conditional=down_en,
            is_upday_conditional=up_en,
            is_fomc_t1=is_fomc_t1,
            spx_open=spx_open,
            chain_df=chain_df,
            lookup=lookup,
            spx_df=spx_df,
            vix_df=vix_df,
            cfg=cfg,
            monitor_times=monitor_times,
            day_early_exit_ms=day_early_exit_ms,
            greeks_df=greeks_df,
        )
        day.entries.append(res)

    # ── Replacement entries (re-enter after early stops) ──────────────
    if getattr(cfg, "replacement_entry_enabled", False):
        replacements = _generate_replacements(
            day.entries, cfg, chain_df, lookup, spx_df, vix_df,
            monitor_times, spx_open, is_fomc_t1, day_early_exit_ms, greeks_df,
        )
        day.entries.extend(replacements)

    # ── Net-return threshold exit (post-processing pass) ─────────────────
    if getattr(cfg, "net_return_exit_pct", None):
        day.entries = _apply_return_threshold(
            day.entries, lookup, monitor_times, cfg
        )

    return day


# ── Full backtest ───────────────────────────────────────────────────────────

def run_backtest(cfg: BacktestConfig, verbose: bool = True) -> List[DayResult]:
    cache_dir = Path(cfg.cache_dir)
    trading_days = get_spxw_trading_days(cfg.start_date, cfg.end_date, cache_dir)

    # Build FOMC date sets (announcement days + T+1 days)
    fomc_t1_dates = set(cfg.fomc_t1_dates)
    fomc_announcement_dates = set()
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from shared.event_calendar import get_fomc_announcement_dates
        years = range(cfg.start_date.year, cfg.end_date.year + 1)
        for fomc_date in [d for yr in years for d in get_fomc_announcement_dates(yr)]:
            if isinstance(fomc_date, date):
                fomc_announcement_dates.add(fomc_date)
                t1 = fomc_date + timedelta(days=1)
                # Skip weekends (if FOMC on Friday, T+1 = Monday)
                while t1.weekday() >= 5:
                    t1 += timedelta(days=1)
                fomc_t1_dates.add(t1)
    except ImportError:
        pass  # Use whatever was in cfg

    results = []
    n_total = len(trading_days)
    if verbose:
        print(f"\nRunning backtest: {cfg.start_date} → {cfg.end_date} ({n_total} days)\n")

    for i, d in enumerate(trading_days, 1):
        # FOMC announcement day skip (MKT-008)
        if getattr(cfg, "fomc_announcement_skip", False) and d in fomc_announcement_dates:
            continue
        day_result = simulate_day(d, cfg, cache_dir, fomc_t1_dates)
        if day_result is None:
            continue
        results.append(day_result)

        if verbose and (i % 50 == 0 or i == n_total):
            cum_net = sum(r.net_pnl for r in results)
            print(f"  [{i}/{n_total}] {d}  cumulative net P&L: ${cum_net:+.0f}")

    return results


# ── Results summary ─────────────────────────────────────────────────────────

def summarize(results: List[DayResult]) -> pd.DataFrame:
    """Convert list of DayResult to a tidy daily DataFrame."""
    rows = []
    for day in results:
        for e in day.entries:
            rows.append({
                "date": day.date,
                "entry_num": e.entry_num,
                "entry_type": e.entry_type,
                "skip_reason": e.skip_reason,
                "spx": e.spx_at_entry,
                "vix": e.vix_at_entry,
                "short_call": e.short_call,
                "long_call": e.long_call,
                "short_put": e.short_put,
                "long_put": e.long_put,
                "call_credit": e.call_credit,
                "put_credit": e.put_credit,
                "call_stop": e.call_stop,
                "put_stop": e.put_stop,
                "call_outcome": e.call_outcome,
                "put_outcome": e.put_outcome,
                "call_close_cost": e.call_close_cost,
                "put_close_cost": e.put_close_cost,
                "gross_pnl": e.gross_pnl,
                "commission": e.commission,
                "net_pnl": e.net_pnl,
            })
    return pd.DataFrame(rows)


def print_stats(results: List[DayResult]):
    """Print a performance summary."""
    if not results:
        print("No results.")
        return

    daily_net = [r.net_pnl for r in results]
    total_net = sum(daily_net)
    winning_days = sum(1 for x in daily_net if x > 0)
    losing_days = sum(1 for x in daily_net if x < 0)
    flat_days = len(daily_net) - winning_days - losing_days
    win_rate = winning_days / len(daily_net) * 100 if daily_net else 0

    all_entries = [e for r in results for e in r.entries]
    placed = [e for e in all_entries if e.entry_type != "skipped"]
    full_ics = [e for e in placed if e.entry_type == "full_ic"]
    call_onlys = [e for e in placed if e.entry_type == "call_only"]
    put_onlys = [e for e in placed if e.entry_type == "put_only"]
    skipped = [e for e in all_entries if e.entry_type == "skipped"]

    call_stops = sum(1 for e in placed if e.call_outcome == "stopped")
    put_stops = sum(1 for e in placed if e.put_outcome == "stopped")
    total_stops = call_stops + put_stops
    stop_rate = total_stops / (len(placed) * 2) * 100 if placed else 0

    # Sharpe (annualized, assuming ~252 trading days/year)
    if len(daily_net) > 1:
        arr = pd.Series(daily_net)
        sharpe = arr.mean() / arr.std() * math.sqrt(252) if arr.std() > 0 else 0
    else:
        sharpe = 0

    # Max drawdown
    cumulative = pd.Series(daily_net).cumsum()
    rolling_max = cumulative.cummax()
    drawdown = cumulative - rolling_max
    max_dd = float(drawdown.min())

    avg_credit = sum(e.call_credit + e.put_credit for e in placed) / len(placed) if placed else 0
    avg_net_per_day = total_net / len(results) if results else 0
    avg_net_per_entry = total_net / len(placed) if placed else 0

    print(f"\n{'='*60}")
    print(f"  HYDRA BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"  Period:         {results[0].date} → {results[-1].date}")
    print(f"  Trading days:   {len(results)}")
    print(f"")
    print(f"  ── P&L ──────────────────────────────────────────────────")
    print(f"  Total net P&L:  ${total_net:+,.0f}")
    print(f"  Avg net/day:    ${avg_net_per_day:+.0f}")
    print(f"  Avg net/entry:  ${avg_net_per_entry:+.0f}")
    print(f"  Max drawdown:   ${max_dd:,.0f}")
    print(f"  Sharpe ratio:   {sharpe:.2f}")
    print(f"")
    print(f"  ── Win/Loss ─────────────────────────────────────────────")
    print(f"  Win rate:       {win_rate:.1f}%  ({winning_days}W / {losing_days}L / {flat_days}F)")
    print(f"")
    print(f"  ── Entries ──────────────────────────────────────────────")
    print(f"  Total placed:   {len(placed)}  (avg {len(placed)/len(results):.1f}/day)")
    print(f"  Full ICs:       {len(full_ics)}")
    print(f"  Call-only:      {len(call_onlys)}")
    print(f"  Put-only:       {len(put_onlys)}")
    print(f"  Skipped:        {len(skipped)}")
    print(f"  Avg credit:     ${avg_credit:.0f}")
    print(f"")
    print(f"  ── Stops ────────────────────────────────────────────────")
    print(f"  Call stops:     {call_stops}")
    print(f"  Put stops:      {put_stops}")
    print(f"  Stop rate:      {stop_rate:.1f}% of sides placed")
    print(f"{'='*60}\n")
