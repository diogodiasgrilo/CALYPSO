"""
HYDRA Backtest Configuration

Every configurable parameter in the live HYDRA strategy is exposed here.
Change values to run what-if scenarios against historical data.
"""
from dataclasses import dataclass, field
from datetime import date, time
from typing import List, Optional


@dataclass
class BacktestConfig:
    # ── Date range ──────────────────────────────────────────────────────────
    start_date: date = field(default_factory=lambda: date(2022, 5, 16))
    end_date: date = field(default_factory=lambda: date.today())

    # ── Entry schedule ───────────────────────────────────────────────────────
    # Base entries (always attempted as full ICs)
    entry_times: List[str] = field(default_factory=lambda: [
        "10:15", "10:45", "11:15", "11:45", "12:15"
    ])

    # ── Conditional E6/E7 entries (MKT-035) ─────────────────────────────────
    # Fire as call-only when SPX drops >= downday_threshold_pct below session open
    conditional_e6_enabled: bool = False
    conditional_e7_enabled: bool = False
    conditional_entry_times: List[str] = field(default_factory=lambda: ["12:45", "13:15"])
    downday_threshold_pct: float = 0.3       # 0.3% drop triggers conditional entries
    downday_reference: str = "open"          # reference price for threshold: "open" or "high"
    downday_theoretical_put_credit: float = 1000.0  # $10.00 × 100 — used in call-only stop (walk-forward optimized)
    upday_theoretical_call_credit: float = 0.0   # added to put-only stop level (mirrors downday_theoretical_put_credit)

    # ── Conditional E6/E7 up-day put-only entries ────────────────────────────
    # Fire as put-only when SPX rises >= upday_threshold_pct above reference
    conditional_upday_e6_enabled: bool = False
    conditional_upday_e7_enabled: bool = False
    upday_threshold_pct: float = 0.3         # % SPX rise to trigger up-day put-only
    upday_reference: str = "open"            # reference price: "open" or "low" (intraday low)

    # ── FOMC T+1 call-only (MKT-038) ─────────────────────────────────────────
    # On the day after FOMC, force all entries to call-only
    fomc_t1_callonly_enabled: bool = True

    # ── Strike selection ─────────────────────────────────────────────────────
    target_delta: float = 8.0               # ~8-delta OTM target
    call_starting_otm_multiplier: float = 3.5  # MKT-024: scan from 3.5× base OTM
    put_starting_otm_multiplier: float = 4.0   # MKT-024: scan from 4.0× base OTM
    min_call_otm_distance: int = 25         # MKT-020: floor for progressive scan (pt)
    min_put_otm_distance: int = 25          # MKT-022: floor for progressive scan (pt)

    # ── Spread widths ────────────────────────────────────────────────────────
    # VM uses fixed 50pt for both sides (VIX-scaling reverted after Feb 10-27 testing)
    # floor = cap = 50 pins spread at exactly 50pt regardless of VIX
    spread_vix_multiplier: float = 3.5      # only relevant if min/max differ
    call_min_spread_width: int = 50
    put_min_spread_width: int = 50
    max_spread_width: int = 50

    # ── Credit gate (MKT-011) ─────────────────────────────────────────────────
    min_call_credit: float = 0.60           # primary minimum per call side ($)
    min_put_credit: float = 2.50            # primary minimum per put side ($)
    call_credit_floor: float = 0.50         # MKT-029 hard floor after fallbacks
    put_credit_floor: float = 2.15          # MKT-029 hard floor after fallbacks (min_put_credit - $0.10)
    one_sided_entries_enabled: bool = True  # if False, skip all put-only and call-only entries
    put_only_max_vix: float = 25.0          # MKT-032: only place put-only if VIX < this
    put_tighten_retries: int = 2            # MKT-040: retries before going call-only
    put_tighten_step: int = 5              # points to tighten per retry

    # ── Stop formula ─────────────────────────────────────────────────────────
    # Full IC:    call_side_stop = total_credit + stop_buffer
    #             put_side_stop  = total_credit + put_stop_buffer
    # Put-only:   put_side_stop  = put_credit + put_stop_buffer
    # Call-only:  call_side_stop = call_credit + theo_put + stop_buffer
    stop_buffer: float = 10.0              # call side buffer in dollars ($0.10 × 100)
    put_stop_buffer: float = 500.0         # put side buffer in dollars ($5.00 × 100)
    min_stop_level: float = 50.0           # safety floor — never stop below $50

    # ── Costs ────────────────────────────────────────────────────────────────
    commission_per_leg: float = 2.50        # $ per leg (Saxo) — $2.50 to open, $2.50 to close; expires worthless = $2.50 only
    contracts: int = 1

    # ── Data / cache ─────────────────────────────────────────────────────────
    cache_dir: str = "backtest/data/cache"
    theta_host: str = "http://127.0.0.1:25510"

    # ── Early exit ────────────────────────────────────────────────────────────
    # If set, close all surviving open positions at this time (HH:MM ET).
    # Positions already stopped are unaffected. Closing commissions apply.
    # None = hold to 4 PM expiry (default, no closing commission).
    early_exit_time: Optional[str] = None  # e.g. "13:00", "14:30", "15:00"

    # ── Net-return threshold exit ─────────────────────────────────────────────
    # If set, close ALL surviving open positions the first time the day's
    # net P&L / total credit collected >= this fraction.  Entries scheduled
    # after the exit bar are skipped.  Closing commissions apply.
    # None = disabled (hold to 4 PM or until stopped).
    # Example: 0.50 = exit when net P&L reaches 50 % of collected credit.
    net_return_exit_pct: Optional[float] = None

    # ── Simulation behaviour ─────────────────────────────────────────────────
    # Interval (ms) between stop checks. 300000 = 5-min (matches data resolution).
    monitor_interval_ms: int = 300000

    # ── FOMC dates (for MKT-038) ─────────────────────────────────────────────
    # The engine will auto-load from shared/event_calendar.py if available,
    # otherwise falls back to this list. Add T+1 dates (day AFTER announcement).
    fomc_t1_dates: List[date] = field(default_factory=list)

    # ── Computed properties (not user-facing) ────────────────────────────────
    @property
    def commission_full_ic(self) -> float:
        return self.commission_per_leg * 4 * self.contracts

    @property
    def commission_one_sided(self) -> float:
        return self.commission_per_leg * 2 * self.contracts

    def early_exit_time_ms(self) -> Optional[int]:
        """Convert early_exit_time string to ms-of-day. Returns None if not set."""
        if not self.early_exit_time:
            return None
        h, m = map(int, self.early_exit_time.split(":"))
        return (h * 3600 + m * 60) * 1000

    def entry_times_as_ms(self) -> List[int]:
        """Convert entry time strings to ms-of-day (ThetaData format)."""
        result = []
        for t_str in self.entry_times:
            h, m = map(int, t_str.split(":"))
            result.append((h * 3600 + m * 60) * 1000)
        return result

    def conditional_times_as_ms(self) -> List[int]:
        result = []
        for t_str in self.conditional_entry_times:
            h, m = map(int, t_str.split(":"))
            result.append((h * 3600 + m * 60) * 1000)
        return result


# ── Preset configs ────────────────────────────────────────────────────────────

def live_config() -> BacktestConfig:
    """Exact parameters running on HYDRA as of 2026-03-23 (synced from VM config.json)."""
    return BacktestConfig(
        entry_times=["10:15", "10:45", "11:15", "11:45", "12:15"],
        conditional_e6_enabled=False,
        conditional_e7_enabled=True,          # VM: E7 downday call-only enabled
        conditional_upday_e6_enabled=True,    # VM: E6 upday put-only enabled
        conditional_upday_e7_enabled=False,
        downday_threshold_pct=0.3,
        upday_threshold_pct=0.40,
        fomc_t1_callonly_enabled=True,
        call_starting_otm_multiplier=3.5,
        put_starting_otm_multiplier=4.0,
        spread_vix_multiplier=3.5,
        call_min_spread_width=50,
        put_min_spread_width=50,
        max_spread_width=50,
        min_call_credit=1.25,                 # VM: min_viable_credit_per_side=1.25
        min_put_credit=2.25,                  # VM: min_viable_credit_put_side=2.25
        put_credit_floor=2.15,                # VM: dynamic floor = min - $0.10
        stop_buffer=10.0,
        put_stop_buffer=100.0,                # VM: put_stop_buffer=1.0 × 100
        one_sided_entries_enabled=True,
        put_only_max_vix=25.0,
        downday_theoretical_put_credit=1000.0,  # VM: 10.0 × 100 (walk-forward optimized)
    )


def tight_stops_config() -> BacktestConfig:
    """Tighter stops — smaller buffer, see how P&L changes."""
    cfg = live_config()
    cfg.stop_buffer = 10.0
    cfg.put_stop_buffer = 200.0   # $2.00 buffer instead of $5.00
    return cfg


def wide_stops_config() -> BacktestConfig:
    """Wider stops — hold through more adverse moves."""
    cfg = live_config()
    cfg.stop_buffer = 10.0
    cfg.put_stop_buffer = 1000.0  # $10.00 buffer
    return cfg


def higher_credit_gate_config() -> BacktestConfig:
    """Higher credit minimums — only enter on premium days."""
    cfg = live_config()
    cfg.min_call_credit = 0.75
    cfg.min_put_credit = 3.00
    return cfg


def e6_e7_enabled_config() -> BacktestConfig:
    """Enable conditional down-day E6/E7 entries."""
    cfg = live_config()
    cfg.conditional_e6_enabled = True
    cfg.conditional_e7_enabled = True
    return cfg
