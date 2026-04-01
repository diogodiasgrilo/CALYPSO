"""
HYDRA Backtest Configuration

Every configurable parameter in the live HYDRA strategy is exposed here.
Change values to run what-if scenarios against historical data.
"""
from dataclasses import dataclass, field
from datetime import date, time
from typing import Dict, List, Optional


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
    conditional_entry_times: List[str] = field(default_factory=lambda: ["14:00", "13:15"])
    downday_threshold_pct: float = 0.3       # 0.3% drop triggers conditional entries
    downday_reference: str = "open"          # reference price for threshold: "open" or "high"
    downday_theoretical_put_credit: float = 175.0   # $1.75 × 100 — used in call-only stop (sweep optimal 2026-03-24)
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
    # Skip ALL entries on FOMC announcement day (MKT-008)
    fomc_announcement_skip: bool = False  # 1-min test: skip costs -$5,855 P&L, -0.096 Sharpe (2026-03-29)

    # ── Strike selection ─────────────────────────────────────────────────────
    target_delta: float = 8.0               # ~8-delta OTM target
    call_starting_otm_multiplier: float = 3.5  # MKT-024: scan from 3.5× base OTM
    put_starting_otm_multiplier: float = 4.0   # MKT-024: scan from 4.0× base OTM
    min_call_otm_distance: int = 25         # MKT-020: floor for progressive scan (pt)
    min_put_otm_distance: int = 25          # MKT-022: floor for progressive scan (pt)

    # ── Spread widths ────────────────────────────────────────────────────────
    # VIX-scaled: formula round(VIX × mult / 5) × 5, floor 25pt, cap 100pt
    # mult=4.0 confirmed optimal after fixing engine bug (long_bid=0 stop skip).
    # Corrected results vs fixed 50pt: Sharpe +0.45, P&L +$25k, MaxDD -$6.4k
    spread_vix_multiplier: float = 4.0
    call_min_spread_width: int = 25
    put_min_spread_width: int = 25
    max_spread_width: int = 110

    # ── Credit gate (MKT-011) ─────────────────────────────────────────────────
    min_call_credit: float = 0.60           # primary minimum per call side ($)
    min_put_credit: float = 2.50            # primary minimum per put side ($)
    call_credit_floor: float = 0.50         # MKT-029 hard floor after fallbacks
    put_credit_floor: float = 2.15          # MKT-029 hard floor after fallbacks (min_put_credit - $0.10)
    one_sided_entries_enabled: bool = True  # if False, skip all put-only and call-only entries
    put_only_max_vix: float = 25.0          # MKT-032: only place put-only if VIX < this
    max_vix_entry: Optional[float] = None   # Skip ALL entries (full IC + one-sided) if VIX >= this. None = no gate.
    put_tighten_retries: int = 0            # MKT-040: dead code — MKT-029 fallback floor handles everything (sweep confirmed 2026-03-27)
    put_tighten_step: int = 5              # points to tighten per retry (unused when retries=0)
    call_tighten_retries: int = 0          # dead code — same reason as put (sweep confirmed 2026-03-27)
    call_tighten_step: int = 5             # points to tighten per retry (unused when retries=0)

    # ── Stop formula ─────────────────────────────────────────────────────────
    # Full IC:    call_side_stop = total_credit + call_stop_buffer
    #             put_side_stop  = total_credit + put_stop_buffer
    # Put-only:   put_side_stop  = put_credit + put_stop_buffer
    # Call-only:  call_side_stop = call_credit + theo_put + call_stop_buffer
    call_stop_buffer: float = 10.0         # call side buffer in dollars ($0.10 × 100)
    put_stop_buffer: float = 500.0         # put side buffer in dollars ($5.00 × 100)
    min_stop_level: float = 50.0           # safety floor — never stop below $50

    # ── Costs ────────────────────────────────────────────────────────────────
    commission_per_leg: float = 2.50        # $ per leg (Saxo) — $2.50 to open, $2.50 to close; expires worthless = $2.50 only
    contracts: int = 1
    stop_slippage_per_leg: float = 5.0      # $0.05/leg slippage on stop-loss market orders (based on Mar 31 live data)

    # ── Data / cache ─────────────────────────────────────────────────────────
    cache_dir: str = "backtest/data/cache"
    theta_host: str = "http://127.0.0.1:25510"
    # Data resolution: "5min" uses options/ and greeks/ folders (default)
    #                  "1min" uses options_1min/ and greeks_1min/ folders
    data_resolution: str = "5min"

    # ── Early exit ────────────────────────────────────────────────────────────
    # If set, close all surviving open positions at this time (HH:MM ET).
    # Positions already stopped are unaffected. Closing commissions apply.
    # None = hold to 4 PM expiry (default, no closing commission).
    early_exit_time: Optional[str] = None  # e.g. "13:00", "14:30", "15:00"

    # ── Price-based stop (alternative to spread-value stop) ──────────────────
    # If set, stop triggers when SPX reaches within this many points of the
    # short strike (on the ITM side), instead of using spread-value vs credit.
    # price_stop_inward=True  (default): fires N pts BEFORE the short strike
    #   call: spx >= short_call - N   put: spx <= short_put + N   (matches live bot)
    # price_stop_inward=False (legacy):  fires N pts PAST   the short strike
    #   call: spx >= short_call + N   put: spx <= short_put - N
    # None = use standard credit-based stop (current behaviour).
    price_based_stop_points: Optional[float] = None
    price_stop_inward: bool = True  # True = matches live bot direction

    # ── VIX-conditional early exit ────────────────────────────────────────────
    # If set, early_exit_time only triggers when VIX at open >= this threshold.
    # On calm days (VIX below threshold) positions hold to 4 PM as normal.
    # None = always apply early_exit_time regardless of VIX (current behaviour).
    # Example: vix_early_exit_threshold=20.0, early_exit_time="12:00" means
    #   "exit at noon only on high-VIX days; hold to 4PM when VIX is calm."
    vix_early_exit_threshold: Optional[float] = None

    # ── Movement-triggered entries (E1-E5) ────────────────────────────────────
    # If set, each base slot fires as soon as SPX moves >= this % in either
    # direction from the previous entry's SPX price (or session open for E1).
    # Scheduled time becomes a hard fallback — the slot fires no later than
    # its scheduled time regardless of movement.  E6/E7 are unaffected.
    # None = disabled (time-based only, current behaviour).
    movement_entry_pct: Optional[float] = None  # e.g. 0.3 = fire next slot when SPX moves 0.3%

    # ── Call-side upward-move filter (E1-E5 base entries) ────────────────────
    # If set, the call spread is only placed on E1-E5 when SPX is already UP
    # at least this % from the session open at entry time.  If the market
    # hasn't moved up enough, the entry becomes put-only.
    # None = disabled (always attempt the call side, current behaviour).
    callside_min_upday_pct: Optional[float] = None  # e.g. 0.2 = only calls if SPX up 0.2%+

    # ── Directional filter for E1-E5 base entries ────────────────────────────
    # Down-day call-only: if SPX is down >= this % from open at entry time,
    # force the base entry to call-only (no puts).  Mirrors MKT-035 logic but
    # applies to all 5 base entries instead of only E6/E7.
    # None = disabled (full IC regardless of direction, current behaviour).
    # UNIT NOTE: stored here in PERCENTAGE units (e.g. 0.40 = 0.4%).
    # The live bot config uses DECIMAL FRACTION units (e.g. 0.004 = 0.4%).
    # Do NOT copy this value directly to bots/hydra/config/config.json.
    base_entry_downday_callonly_pct: Optional[float] = None  # e.g. 0.40 (= 0.4%)
    base_entry_downday_reference: str = "open"  # "open" or "high" — reference price for base entry downday filter

    # Up-day put-only: if SPX is up >= this % from open at entry time,
    # force the base entry to put-only (no calls).  Mirrors Upday-035 logic.
    # None = disabled.
    base_entry_upday_putonly_pct: Optional[float] = None  # e.g. 0.2

    # ── Net-return threshold exit ─────────────────────────────────────────────
    # If set, close ALL surviving open positions the first time the day's
    # net P&L / total credit collected >= this fraction.  Entries scheduled
    # after the exit bar are skipped.  Closing commissions apply.
    # None = disabled (hold to 4 PM or until stopped).
    # Example: 0.50 = exit when net P&L reaches 50 % of collected credit.
    net_return_exit_pct: Optional[float] = None

    # ── Protection features ───────────────────────────────────────────────────
    # Daily loss limit: skip remaining entries if day's realized P&L <= this.
    # None = disabled. Example: -1000 = stop entering after -$1000 loss.
    daily_loss_limit: Optional[float] = None

    # Cap spread close cost at stop to spread_width × 100 (max theoretical value).
    # Prevents slippage from inflating stop losses beyond physical spread width.
    spread_value_cap_at_stop: bool = False

    # VIX spike gate: skip entry if VIX at entry time > VIX at open + this many points.
    # None = disabled. Example: 5.0 = skip if VIX jumped 5+ pts since open.
    vix_spike_skip_points: Optional[float] = None

    # Anti-whipsaw: skip entry if SPX intraday range (high-low) from open to entry
    # exceeds this multiple of the expected move (VIX-based: SPX × VIX/100 / sqrt(252)).
    # None = disabled. Example: 1.5 = skip if range > 1.5× expected daily move.
    whipsaw_range_skip_mult: Optional[float] = 1.50  # 1-min fine-grain optimal 2026-03-29 (Sharpe 3.282)

    # ── VIX-regime adaptive parameters ──────────────────────────────────────
    # Override parameters based on VIX at open.  breakpoints define thresholds;
    # override lists have len(breakpoints)+1 entries (one per regime bin).
    # None in an override list = use the base config value for that regime.
    # Regime bins: [<bp[0]], [bp[0]..bp[1]), [bp[1]..bp[2]), [>=bp[-1]]
    vix_regime_enabled: bool = False
    vix_regime_breakpoints: List[float] = field(default_factory=lambda: [14.0, 20.0, 30.0])
    vix_regime_max_entries: List[Optional[int]] = field(default_factory=lambda: [None, None, None, None])
    vix_regime_put_stop_buffer: List[Optional[float]] = field(default_factory=lambda: [None, None, None, None])
    vix_regime_call_stop_buffer: List[Optional[float]] = field(default_factory=lambda: [None, None, None, None])
    vix_regime_min_put_credit: List[Optional[float]] = field(default_factory=lambda: [None, None, None, None])
    vix_regime_min_call_credit: List[Optional[float]] = field(default_factory=lambda: [None, None, None, None])

    # ── Day-of-week filter ──────────────────────────────────────────────────
    # skip_weekdays: skip these days entirely (0=Mon .. 4=Fri).
    # dow_max_entries: cap base entry count per weekday, e.g. {0: 2} = max 2 Mon.
    skip_weekdays: List[int] = field(default_factory=list)
    dow_max_entries: Dict[int, int] = field(default_factory=dict)

    # ── Replacement entries after stops ─────────────────────────────────────
    # After a side is stopped, re-enter the same side further OTM.
    replacement_entry_enabled: bool = False
    replacement_entry_max_per_day: int = 2          # max replacements per day
    replacement_entry_delay_minutes: int = 5        # wait N min after stop
    replacement_entry_extra_otm: int = 10           # place N pts further OTM
    replacement_entry_cutoff: str = "14:00"         # no re-entry after this time

    # ── Trailing stop / profit lock ─────────────────────────────────────────
    # When a side's spread value (cost-to-close) decays to trigger_decay × credit,
    # tighten that side's stop to credit + trailing buffer (instead of original buffer).
    # Lower buffer = tighter stop = locks in more profit but risks early stop-out.
    trailing_stop_enabled: bool = False
    trailing_stop_trigger_decay: float = 0.50       # trigger at 50% of per-side credit
    trailing_stop_call_buffer: float = 10.0         # $ — tightened call stop buffer (vs base $35)
    trailing_stop_put_buffer: float = 50.0          # $ — tightened put stop buffer (vs base $155)

    # ── Real Greeks mode ─────────────────────────────────────────────────────
    # When True (strict mode): use actual per-strike delta from ThetaData Greeks
    # files to determine the 8-delta OTM distance instead of the VIX formula.
    # Days without a Greeks cache file are SKIPPED entirely (not approximated).
    # When False (default): use VIX-formula approximation for all days.
    # Greeks files live in: cache/greeks/SPXW_YYYYMMDD_greeks.parquet
    use_real_greeks: bool = False

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

    def replacement_cutoff_ms(self) -> int:
        """Convert replacement_entry_cutoff to ms-of-day."""
        h, m = map(int, self.replacement_entry_cutoff.split(":"))
        return (h * 3600 + m * 60) * 1000

    def replacement_delay_ms(self) -> int:
        """Convert replacement_entry_delay_minutes to ms."""
        return self.replacement_entry_delay_minutes * 60 * 1000


# ── Preset configs ────────────────────────────────────────────────────────────

def live_config() -> BacktestConfig:
    """HYDRA optimized parameters (converged 2026-03-29, 1-min data, 933 days).

    Confirmed optimal values (2026-03-27 full sweep — real Greeks, honest engine, 902 days):
      - entry_times: 3 entries at 30min [10:15, 10:45, 11:15]  (Sharpe 1.452, best of 94 combos tested)
        Peak margin $30K — fits $35K account with zero breach days (5 entries breached 41%)
      - spread_vix_multiplier: 4.0  (Sharpe 1.098 vs 0.643 fixed-50pt)
      - call_stop_buffer: 20.0  ($0.20, Sharpe 1.220 vs 1.170 at $0.10)
      - conditional_e7_enabled: False  (baseline Sharpe 1.164 vs 1.098 with E7+E6up ON)
      - conditional_upday_e6_enabled: False  (E6/E7 hurt Sharpe AND P&L)
      - base_entry_downday_callonly_pct: 0.60  (Sharpe 1.098, best among 8 values)
      - downday_theoretical_put_credit: 150.0  ($1.50, Sharpe 1.125 vs 1.098 at $1.75)
      - min_call_credit: 1.25  (Sharpe 1.160, confirmed optimal among 7 values)
      - min_put_credit: 2.25  (Sharpe 1.160, confirmed optimal among 7 values)
      - price_based_stop_points: None  (credit-based stop, confirmed 2026-03-24)
    """
    return BacktestConfig(
        entry_times=["10:15", "10:45", "11:15"],  # 3 entries, 30min interval (sweep optimal 2026-03-27, was 5 entries)
        conditional_e6_enabled=False,
        conditional_e7_enabled=False,         # DISABLED (2026-03-27 sweep: baseline beats E7+E6up by +0.066 Sharpe)
        conditional_upday_e6_enabled=True,    # ENABLED on 1-min (2026-03-28: Sharpe 2.003 vs 1.988 OFF, MaxDD $7,855)
        conditional_upday_e7_enabled=False,
        downday_threshold_pct=0.3,
        upday_threshold_pct=0.25,             # re-swept with $2.00/$2.75 gates: 0.25% best Sharpe (2.445)
        fomc_t1_callonly_enabled=True,
        call_starting_otm_multiplier=3.5,
        put_starting_otm_multiplier=4.0,
        spread_vix_multiplier=6.0,            # reconvergence 2026-03-31 (was 5.3, Sharpe 2.360)
        call_min_spread_width=25,
        put_min_spread_width=25,
        max_spread_width=110,                 # fine-grain sweep optimal: plateau 110-200 all ~2.28-2.38 Sharpe, pick min (least margin)
        min_call_credit=2.00,                 # re-swept with VIX regime: +$7,696 P&L vs $1.35 (Sharpe 2.436)
        min_put_credit=2.75,                  # re-swept with VIX regime: best MaxDD $6,115, Win 52.9%
        call_credit_floor=0.75,               # 1-min edge sweep optimal 2026-03-28 (was $0.85, Sharpe 1.988)
        put_credit_floor=2.00,                # reconvergence 2026-03-31 (was $2.07, Sharpe 2.360)
        call_stop_buffer=35.0,                # $0.35 × 100, convergence round 4 (was $0.26, +0.056 Sharpe)
        put_stop_buffer=155.0,                # $1.55 × 100, 1-min confirmed 2026-03-28
        one_sided_entries_enabled=True,
        put_only_max_vix=15.0,                # 1-min retest optimal 2026-03-28 (was 25.0, Sharpe 2.042)
        price_based_stop_points=None,         # credit-based stop (confirmed on 1-min)
        downday_theoretical_put_credit=260.0, # $2.60 × 100, convergence round 4 (was $2.90, +0.029 Sharpe)
        base_entry_downday_callonly_pct=0.57, # 1-min fine-grain optimal 2026-03-29 (was 0.60%, Sharpe 2.371)
        fomc_announcement_skip=False,       # 1-min test: skip costs -$5,855 P&L, -0.096 Sharpe (2026-03-29)
        whipsaw_range_skip_mult=1.75,       # reconvergence 2026-03-31 (was 1.50, Sharpe 2.360)
    )


def tight_stops_config() -> BacktestConfig:
    """Tighter stops — smaller buffer, see how P&L changes."""
    cfg = live_config()
    cfg.call_stop_buffer = 10.0
    cfg.put_stop_buffer = 200.0   # $2.00 buffer instead of $5.00
    return cfg


def wide_stops_config() -> BacktestConfig:
    """Wider stops — hold through more adverse moves."""
    cfg = live_config()
    cfg.call_stop_buffer = 10.0
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
