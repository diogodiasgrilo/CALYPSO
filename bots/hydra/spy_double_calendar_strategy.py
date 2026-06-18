"""SpyDoubleCalendarStrategy — "SPY Double Calendar" (Strategy E), dry-run-LOCKED.

Source strategy: an OptionsKit "calendar / double calendar" video (YouTube
GuI-hH_jhlg) — the creator's "ultimate strategy" (>80% win rate). Open a SPY
**double calendar** for a net DEBIT: a CALL calendar ABOVE spot + a PUT calendar
BELOW spot. Each leg sells a near-dated option and buys a longer-dated option at
the SAME strike. Unlike Strategy D (the "DC Time Machine"), E does NOT transform
into a risk-free iron condor — it is a MANAGED double calendar:

  * Strikes are EXPECTED-MOVE based: call strike ≈ spot + em_fraction×EM,
    put strike ≈ spot − em_fraction×EM (em_fraction 1.0 = ±1 standard deviation).
  * Expiries: short leg ~35 DTE; long leg = short + ~1 week (same strike per leg).
  * Entry only in LOW IV (a proxy gate on VIX, since we have no IV-rank history) —
    calendars are positive vega and want IV to mean-revert UP.
  * Management is LADDERED early profit-taking (scale out across contracts as the
    return-on-risk rises) plus a TRADING-day TIME-EXIT before the short expiry.
    There is NO hard stop — the defined risk IS the debit paid.

STATUS: dry-run-LOCKED SCAFFOLD. The dry-run lifecycle is implemented (entry +
laddered profit-take + time-exit + settlement backstop), simulated from real
mids; NO real orders are placed. The class refuses any non-dry_run construction.

DESIGN — reuse D's shared base, override only the economics:
  * Inherits ``CalendarStrategyBase`` (the shared two-expiry data layer, dry-run
    simulation primitives, sidecar persistence, multi-day lifecycle, and the
    per-expiry settlement plumbing — all lifted verbatim from D). Strategy D
    (``DoubleCalendarStrategy``) stays byte-identical; E provides its OWN versions
    of the methods that live in D (it does NOT inherit D's transformer / delta-
    target / threshold logic) and SETS on itself any attribute the inherited base
    methods read (e.g. ``self.dc_wing_width = 0.0`` for the base ``_dc_simulate_entry``
    wing stamp; ``self.dc_require_realtime_quotes`` for ``_dc_refresh_marks``).
  * Dry-run LOCK in __init__ (mirrors D / StrangleStrategy): a non-dry-run
    construction raises ConfigError BEFORE any broker I/O; ``self._dc_loaded=False``
    is set BEFORE ``super().__init__`` (which can call ``_save_state_to_disk`` during
    recovery); a defense-in-depth re-check runs after super.
  * BOT_NAME="SPYDC" and requires_protective_wings=False so the shared safety path
    never treats E's debit/calendar legs as a naked-short emergency.

GO-LIVE GATES (NOT a dry-run concern — documented so they are not forgotten):
  * E is a SECOND multi-day debit strategy on the shared paper account. It inherits
    D's three coexistence MUST-FIXes as go-live gates (scope C's STATE-004 overnight
    guard + orphan sweep to per-variant conids; budget per-variant buying power).
  * SPY options are AMERICAN / physically-settled (unlike SPXW). Early assignment of
    the short near leg (esp. around ex-dividend for calls) must be handled before the
    short expiry — the time-exit ("never hold through expiry") is the first line of
    defense, but a real-order path needs explicit assignment + dividend handling.
  * True IV-RANK (vs a 1-yr median) is the correct low-IV gate. The VIX-threshold
    gate here is a dry-run proxy; a go-live refinement needs an IV-history source.
In dry_run (the only mode this class permits) E places ZERO real orders.
"""

from __future__ import annotations

import logging
import os
from datetime import time
from typing import List, Optional, Tuple

from bots.hydra.base_strategy import ConfigError, MEICState
from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: F401
from bots.hydra.calendar_chain import (
    generate_candidate_expiries,
    pick_calendar_expiries,
    trading_days_until,
)
from bots.hydra.calendar_strategy_base import CalendarStrategyBase
from bots.hydra.strategy import HydraStrategy  # noqa: F401  (re-exported for back-compat)
from shared.market_hours import get_us_market_time

logger = logging.getLogger(__name__)


class SpyDoubleCalendarStrategy(CalendarStrategyBase):
    """Strategy E — managed SPY double calendar (no transformer, no hard stop).

    Walks CALENDAR → CLOSED only (E never transforms). Inherits all of
    ``CalendarStrategyBase``'s plumbing; overrides the expiry picker, strike
    selection (expected-move based), entry, pre-entry gates, the per-tick
    manager (laddered profit-take + time-exit), the monitoring cadence, the
    debit-based max loss, and the settlement booking.
    """

    BOT_NAME = "SPYDC"
    # Debit double calendar — the long-dated legs are the protection, so the base
    # naked-short emergency path must stay off (same as D / StrangleStrategy).
    requires_protective_wings = False
    # E only ever holds an OPEN double calendar (it never transforms). TRANSFORMED
    # is unreachable, but keep both phases "open" so the inherited carry-forward /
    # settlement predicates behave identically should a stray phase ever appear.

    def __init__(self, *args, **kwargs):
        """Construct, enforcing the dry-run-only lock (mirrors D).

        E is a dry-run-LOCKED scaffold (a SPY double calendar): the dry-run
        lifecycle is implemented, but a real-order run next to the live variants
        requires the coexistence MUST-FIXes + SPY American-assignment / dividend
        handling, so a live (real-order) construction is refused BEFORE
        ``super().__init__`` runs any broker I/O. ``build_strategy`` always passes
        ``dry_run`` as a kwarg.
        """
        if not kwargs.get("dry_run", False):
            raise ConfigError(
                "SpyDoubleCalendarStrategy (Strategy E) is dry-run-LOCKED. This is a "
                "SCAFFOLD: a SPY double calendar (net-debit call calendar above spot "
                "+ put calendar below spot, managed via laddered profit-taking + a "
                "trading-day time-exit, NO hard stop). The dry-run lifecycle IS "
                "implemented (simulated from real mids; no real orders), but running "
                "it with real paper orders next to the live variants requires the "
                "coexistence MUST-FIXes (scope STATE-004 + orphan sweep to per-variant "
                "conids; budget buying power) AND SPY-specific American early-assignment "
                "+ dividend handling. Going live is a gated build, NOT a config flip. "
                "Set dry_run=true, or do not select strategy.name='spy_double_calendar'."
            )
        # Sidecar-load guard, set BEFORE super().__init__ (which calls
        # _recover_positions_from_saxo AND may call _save_state_to_disk during
        # recovery). _dc_save_sidecar must NOT clobber the real sidecar with an
        # empty list before _dc_load_sidecar has read it on startup.
        self._dc_loaded = False
        super().__init__(*args, **kwargs)
        # Defense-in-depth: re-check after super in case dry_run is derived
        # differently downstream (it must equal the kwarg).
        if not getattr(self, "dry_run", False):
            raise ConfigError(
                "SpyDoubleCalendarStrategy resolved to dry_run=false after init — "
                "refusing to arm a coexistence-unsafe SPY double-calendar scaffold."
            )

        # E-specific knobs (config.strategy.spy_double_calendar.*).
        cfg = getattr(self, "strategy_config", {}) or {}
        e = cfg.get("spy_double_calendar", {}) or {}
        self.spy_dc_short_dte_target = int(e.get("short_dte_target", 35))
        self.spy_dc_short_dte_tolerance = int(e.get("short_dte_tolerance", 5))
        self.spy_dc_long_gap_days = int(e.get("long_gap_days", 7))
        self.spy_dc_long_gap_tolerance = int(e.get("long_gap_tolerance", 3))
        self.spy_dc_em_fraction = float(e.get("em_fraction", 1.0))
        self.spy_dc_max_vix_entry = float(e.get("max_vix_entry", 22.0))
        # Laddered profit-taking: a list of {"ror": fraction, "frac": fraction}
        # steps. ``ror`` = unrealized_pnl / net_debit (return-on-risk). ``frac`` =
        # the share of the ORIGINAL contracts to have closed once that ror is met
        # (cumulative across steps). Sorted ascending by ror so the cumulative
        # target-closed fraction is monotonic.
        ladder = e.get(
            "profit_ladder",
            [
                {"ror": 0.20, "frac": 0.25},
                {"ror": 0.50, "frac": 0.25},
                {"ror": 1.00, "frac": 0.25},
                {"ror": 2.00, "frac": 0.25},
            ],
        )
        self.spy_dc_profit_ladder = sorted(
            ({"ror": float(s["ror"]), "frac": float(s["frac"])} for s in ladder),
            key=lambda s: s["ror"],
        )
        self.spy_dc_time_exit_days_before = int(e.get("time_exit_days_before", 2))
        self.spy_dc_max_concurrent = int(e.get("max_concurrent", 1))
        self.spy_dc_max_deployed_debit = float(e.get("max_deployed_debit", 5000.0))
        # Realistic dry-run fill model (2026-06-17, shared with D): price every
        # simulated fill across the bid/ask spread, not the mid. aggressiveness 1.0
        # = full touch (buy@ask / sell@bid). Read by the inherited _dc_fill_price,
        # so E's entry debit, liquidation marks, and laddered profit-takes are all
        # honest about the spread instead of mid-optimistic.
        _fm = cfg.get("dry_run_fill_model", {}) or {}
        self._dc_fill_agg = float(_fm.get("aggressiveness", 1.0))
        self._dc_fill_slippage = float(_fm.get("extra_slippage_per_leg", 0.0))

        # ── Attributes the INHERITED base methods read (so D is untouched) ──
        # E never transforms, so there is no IC wing. _dc_simulate_entry stamps
        # entry.wing_width = self.dc_wing_width at OPEN; 0.0 keeps the calendar's
        # spread_width/capital at the debit basis (CalendarEntry.spread_width
        # returns wing_width in the CALENDAR phase, but _calculate_capital_deployed
        # / _calculate_max_loss use net_debit, so wing_width=0 is harmless display).
        self.dc_wing_width = 0.0
        # _dc_refresh_marks gates value-driven decisions on real-time quotes when
        # this is True. Default OFF (config-overridable) — same rationale as D: the
        # ladder/time-exit work regardless, and an over-eager gate could freeze E if
        # non-0DTE SPY snapshots don't carry the 6509 'R' flag.
        self.dc_require_realtime_quotes = bool(e.get("require_realtime_quotes", False))
        # _dc_em_otm_distance reads dc_delta_otm_fraction to scale the VIX-EM to a
        # ~target-delta OTM distance. E wants the FULL ±em_fraction×EM (a ±1 SD
        # strike), so set the fraction to em_fraction (1.0 = the raw 1-SD EM).
        self.dc_delta_otm_fraction = self.spy_dc_em_fraction
        # get_detailed_position_status (inherited) reads this dict; E has no stop
        # so it stays empty, but the attribute must exist.
        self._dc_stop_breach = {}
        # EOD cutoff is never used by E (no EOD close), but _dc_past_eod_cutoff
        # exists on the base; set a sane value so an accidental call can't crash.
        self.dc_eod_cutoff = time(15, 55)
        # Track contracts already closed per entry (for the cumulative ladder).
        self._spy_dc_closed_contracts = {}

        logger.info(
            "SPYDC initialized (DRY-RUN, LOCKED). SPY double calendar: short ~%dDTE (±%d), "
            "long +%dd (±%d), strikes ±%.2f×EM, low-IV gate VIX<=%.1f, ladder=%s, "
            "time-exit %d trading day(s) before short expiry, NO hard stop. "
            "Lifecycle LIVE (simulated); no real orders.",
            self.spy_dc_short_dte_target, self.spy_dc_short_dte_tolerance,
            self.spy_dc_long_gap_days, self.spy_dc_long_gap_tolerance,
            self.spy_dc_em_fraction, self.spy_dc_max_vix_entry,
            [(s["ror"], s["frac"]) for s in self.spy_dc_profit_ladder],
            self.spy_dc_time_exit_days_before,
        )

        # E's OWN (isolated) calendar DB — reuse D's DCDataRecorder + schema with a
        # distinct ``structure`` so rows self-identify. Derived from the variant
        # state-file dir (data/variant_e/dc_calendar.db). Non-critical.
        self._dc_recorder = None
        try:
            from bots.hydra.dc_recorder import DCDataRecorder
            dc_db = os.path.join(os.path.dirname(self.state_file), "dc_calendar.db")
            self._dc_recorder = DCDataRecorder(dc_db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DCDataRecorder init failed (non-critical): %s", exc)

    # ------------------------------------------------------------------
    # Calendar-aware risk + monitoring overrides
    # ------------------------------------------------------------------

    def _calculate_max_loss_with_stops(self) -> float:
        """E has NO hard stop — defined risk IS the debit. So the max loss with
        "stops working" is just the full open debit (there is no stop fraction to
        apply). Risk-free TRANSFORMED legs (never reached for E) are excluded by
        _dc_open_debit_at_risk's CALENDAR filter."""
        return self._dc_open_debit_at_risk()

    def get_monitoring_mode(self) -> str:
        """Vigilant (2s) when any open calendar is NEAR a ladder threshold or
        within the time-exit window; else normal. E has no stop-confirm window
        (no hard stop), so vigilance keys off the ladder + time-exit instead."""
        if not self.daily_state.active_entries:
            self._current_monitoring_mode = "normal"
            return "normal"
        today = get_us_market_time().strftime("%Y-%m-%d")
        first_step = self.spy_dc_profit_ladder[0]["ror"] if self.spy_dc_profit_ladder else None
        for entry in self.daily_state.active_entries:
            if not isinstance(entry, CalendarEntry):
                continue
            # Near the time-exit window? (within time_exit + 1 trading days)
            short_exp = getattr(entry, "short_expiry", None)
            if short_exp:
                td = trading_days_until(today, short_exp)
                if td <= self.spy_dc_time_exit_days_before + 1:
                    self._current_monitoring_mode = "vigilant"
                    return "vigilant"
            debit = float(getattr(entry, "net_debit", 0.0) or 0.0)
            if debit <= 0 or first_step is None:
                continue
            ror = entry.unrealized_pnl / debit
            if ror >= 0.75 * first_step:  # approaching the first ladder rung
                self._current_monitoring_mode = "vigilant"
                return "vigilant"
        self._current_monitoring_mode = "normal"
        return "normal"

    # ------------------------------------------------------------------
    # Two-expiry selection — absolute-DTE short + a ~1-week long gap
    # ------------------------------------------------------------------

    def _pick_expiries(self) -> Optional[Tuple[str, list]]:
        """Choose the short expiry (~short_dte_target ± tolerance) and an ORDERED
        list of long candidates (short + long_gap_days ± tolerance, nearest gap
        first). PURE / fast — no per-candidate broker calls; the both-expiry strike
        requirement is enforced later by _calculate_strikes (which only accepts a
        strike listed on BOTH expiries). Returns (short_expiry, [long_candidates])
        or None.

        NOTE: the inherited base plumbing (probe / settle-due) calls
        ``_dc_pick_expiries`` by name, so it is aliased to this method below.
        """
        today_iso = get_us_market_time().strftime("%Y-%m-%d")
        short_min = self.spy_dc_short_dte_target - self.spy_dc_short_dte_tolerance
        short_max = self.spy_dc_short_dte_target + self.spy_dc_short_dte_tolerance
        gap_min = self.spy_dc_long_gap_days - self.spy_dc_long_gap_tolerance
        gap_max = self.spy_dc_long_gap_days + self.spy_dc_long_gap_tolerance
        horizon = short_max + gap_max + 3
        candidates = generate_candidate_expiries(today_iso, horizon)
        # Reuse D's pure picker: nearest-the-target short (prefer_friday False — E
        # uses an absolute-DTE target, not a following-week-Friday convention), then
        # the smallest valid long gap. Backtracks across shorts.
        picked = pick_calendar_expiries(
            candidates, today_iso,
            short_min, short_max, gap_min, gap_max,
            prefer_friday=False,
        )
        if picked is None:
            logger.warning(
                "[SPYDC-EXPIRIES] no viable short/long window: short %d-%dDTE, long gap +%d-%dd (candidates=%d)",
                short_min, short_max, gap_min, gap_max, len(candidates),
            )
            return None
        short_exp, _ = picked
        from datetime import date as _date
        t = _date.fromisoformat(today_iso)
        sd = (_date.fromisoformat(short_exp) - t).days
        longs = sorted(
            [c for c in candidates
             if gap_min <= (_date.fromisoformat(c) - t).days - sd <= gap_max],
            key=lambda c: _date.fromisoformat(c),
        )
        logger.info("[SPYDC-EXPIRIES] short=%s (%dDTE), long candidates=%s", short_exp, sd, longs)
        return short_exp, longs

    # The inherited base probe (_dc_probe_two_expiry_data) + settle helpers call
    # _dc_pick_expiries by name; alias E's picker so they work unchanged.
    _dc_pick_expiries = _pick_expiries

    # ------------------------------------------------------------------
    # Strike selection — expected-move (±1×EM) call + put calendar strikes,
    # snapped to the SPY $1 grid and listed on BOTH expiries.
    # ------------------------------------------------------------------

    def _calculate_strikes(self, entry) -> bool:
        """Pick the two expiries and the ±em_fraction×EM call/put strikes (same
        strike per leg), SNAPPED to the intersection of strikes listed on BOTH
        expiries, and stamp them onto the entry.

        Fix (2026-06-18): the old code rounded the EM target to a fixed $1 grid and
        then required that exact strike on both expiries — but SPY monthlies use a
        $5 far-OTM grid while weeklies use $1, so the strike was frequently unlisted
        on one leg and the entry skipped every day. We now fetch each expiry's
        actual strike list, take the short∩long intersection, and snap each EM
        target to the NEAREST strike present on both — guaranteeing the legs resolve
        (a listed-grid strike near ±EM beats a perfect-EM strike that doesn't exist)."""
        spot = self.current_price
        if not spot or spot <= 0:
            logger.error("[SPYDC] no SPY price — cannot calculate strikes")
            return False
        picked = self._pick_expiries()
        if not picked:
            return False
        short_exp, longs = picked
        # ±em_fraction×EM (dc_delta_otm_fraction == em_fraction → the raw scaled EM).
        em = self._dc_em_otm_distance(spot, short_exp)
        call_target, put_target = spot + em, spot - em
        short_strikes = self._dc_strikes_for_expiry(short_exp)
        if not short_strikes:
            logger.warning("[SPYDC] no strikes listed for short %s — skipping", short_exp)
            return False
        for long_exp in longs[:3]:  # try the nearest few long candidates
            common = short_strikes & self._dc_strikes_for_expiry(long_exp)
            # Snap each EM target to the NEAREST strike listed on BOTH expiries,
            # keeping the call OTM-above-spot and the put OTM-below-spot.
            call_strike = min((s for s in common if s > spot),
                              key=lambda s: abs(s - call_target), default=None)
            put_strike = min((s for s in common if 0 < s < spot),
                             key=lambda s: abs(s - put_target), default=None)
            if call_strike is None or put_strike is None or call_strike <= put_strike:
                logger.info(
                    "[SPYDC] long %s — no both-sides strike in the short∩long grid "
                    "(common=%d) near ±EM; trying next", long_exp, len(common),
                )
                continue
            legs = self._dc_resolve_calendar_legs(call_strike, put_strike, short_exp, long_exp)
            if legs:
                entry.short_call_strike = entry.long_call_strike = float(call_strike)
                entry.short_put_strike = entry.long_put_strike = float(put_strike)
                entry.legs["short_call"].expiry = short_exp
                entry.legs["long_call"].expiry = long_exp
                entry.legs["short_put"].expiry = short_exp
                entry.legs["long_put"].expiry = long_exp
                logger.info(
                    "[SPYDC] strikes Kc=%.0f Kp=%.0f (spot %.2f, EM ±%.0f, snapped to short∩long) "
                    "| short=%s long=%s", call_strike, put_strike, spot, em, short_exp, long_exp,
                )
                return True
            logger.info(
                "[SPYDC] long %s — intersection Kc=%.0f/Kp=%.0f failed to resolve; trying next",
                long_exp, call_strike, put_strike,
            )
        logger.warning(
            "[SPYDC] no both-expiry strike pair near ±%.2f×EM (short %s + longs %s)",
            self.spy_dc_em_fraction, short_exp, longs[:3],
        )
        return False

    # ------------------------------------------------------------------
    # Pre-entry gates + entry (dry-run simulated)
    # ------------------------------------------------------------------

    def _pre_entry_gates(self, entry_num: int) -> Optional[str]:
        """Minimal pre-entry gates: concurrent-calendar cap, per-variant BP budget,
        orphaned orders, market halt, buying power, and the LOW-IV entry gate
        (VIX proxy). Deliberately simpler than HYDRA's IC gates (no VIX regime, no
        credit gate — those are credit-IC concepts)."""
        # Concurrent-calendar cap (E holds a multi-day debit; don't stack).
        open_cals = sum(1 for e in self.daily_state.entries if self._dc_entry_is_open(e))
        if open_cals >= self.spy_dc_max_concurrent:
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            return (
                f"Entry #{entry_num} skipped - {open_cals} calendar(s) already open "
                f"(max {self.spy_dc_max_concurrent})"
            )
        # Per-variant BP budget.
        budget = self.spy_dc_max_deployed_debit
        if budget > 0:
            deployed = sum(
                float(getattr(e, "net_debit", 0.0) or 0.0)
                for e in self.daily_state.entries if self._dc_entry_is_open(e)
            )
            if deployed >= budget:
                self.daily_state.entries_skipped += 1
                self._next_entry_index += 1
                return (
                    f"Entry #{entry_num} skipped - deployed debit ${deployed:.0f} "
                    f">= BP budget ${budget:.0f}"
                )
        # LOW-IV entry gate: calendars are positive-vega; enter only when IV is at
        # the low end (VIX proxy — true IV-rank is a go-live refinement).
        vix = self.current_vix
        if vix and vix > self.spy_dc_max_vix_entry:
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            return (
                f"Entry #{entry_num} skipped - VIX {vix:.1f} > low-IV gate "
                f"{self.spy_dc_max_vix_entry:.1f} (calendars want low IV)"
            )
        if self._has_orphaned_orders():
            self._next_entry_index += 1
            return f"Entry #{entry_num} skipped - orphaned orders blocking"
        is_halted, halt_reason = self._check_market_halt()
        if is_halted:
            return f"Entry #{entry_num} delayed - {halt_reason}"  # delay, don't advance
        has_bp, bp_message = self._check_buying_power()
        if not has_bp:
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            return f"Entry #{entry_num} skipped - {bp_message}"
        return None

    def _initiate_entry(self) -> str:
        """Open the next scheduled SPY double calendar (dry-run simulated).

        E is dry-run-LOCKED, so the simulated path always runs — no real order
        ever reaches the broker.
        """
        entry_num = self._next_entry_index + 1
        logger.info("[SPYDC] initiating entry #%s", entry_num)

        gate = self._pre_entry_gates(entry_num)
        if gate is not None:
            return gate

        self._entry_in_progress = True
        self.state = MEICState.ENTRY_IN_PROGRESS
        try:
            entry = CalendarEntry(entry_number=entry_num)
            entry.structure = "double_calendar_spy"  # self-identification in the shared DB
            entry.contracts = self.contracts_per_entry
            entry.strategy_id = f"spydc_{get_us_market_time().strftime('%Y%m%d')}_{entry_num:03d}"

            if not self._calculate_strikes(entry):
                self.daily_state.entries_skipped += 1
                self._next_entry_index += 1
                return f"Entry #{entry_num} skipped - strike/expiry selection failed"

            if not self._dc_simulate_entry(entry):
                self.daily_state.entries_failed += 1
                self._next_entry_index += 1
                return f"Entry #{entry_num} failed - could not price/simulate calendar"

            # _dc_simulate_entry stamps structure back to its default; re-assert E's.
            entry.structure = "double_calendar_spy"
            # Record the original contract count for the cumulative ladder.
            entry.original_contracts = entry.contracts
            self._spy_dc_closed_contracts[entry.entry_number] = 0

            entry.entry_time = get_us_market_time()
            self.daily_state.entries.append(entry)
            self.daily_state.entries_completed += 1
            entry.open_commission = 4 * self.commission_per_leg * self.contracts_per_entry
            self.daily_state.total_commission += entry.open_commission

            self._save_state_to_disk()  # persist before returning (crash-window guard)
            if getattr(self, "_dc_recorder", None):
                self._dc_recorder.record_calendar_entry(
                    entry, self.current_price, entry.entry_time.strftime("%Y-%m-%d")
                )
            self._next_entry_index += 1
            return (
                f"Entry #{entry_num} placed: SPY Double Calendar "
                f"Kc {entry.short_call_strike:.0f} / Kp {entry.short_put_strike:.0f}, "
                f"debit ${entry.net_debit:.2f} (DRY)"
            )
        finally:
            self._entry_in_progress = False
            self.state = MEICState.MONITORING

    # ------------------------------------------------------------------
    # Management — laddered profit-take + trading-day time-exit (no stop)
    # ------------------------------------------------------------------

    def _check_stop_losses(self):
        """Manage every open double calendar (CALENDAR phase). Returns a summary
        of actions taken this tick, or None. (Named for the base-loop contract;
        E has no actual stop — it does laddered profit-takes + a time-exit.)"""
        actions = []
        for entry in list(self.daily_state.active_entries):
            if not isinstance(entry, CalendarEntry):
                continue
            if entry.dc_phase == DCPhase.CALENDAR:
                act = self._manage_calendar(entry)
                if act:
                    actions.append(act)
        return "; ".join(actions) if actions else None

    def _manage_calendar(self, entry) -> Optional[str]:
        """One open calendar's intraday decision: TIME-EXIT first (time-based, runs
        regardless of mark freshness — never hold through expiry), then LADDERED
        profit-taking (value-driven, only on a fully fresh mark)."""
        # 1. Time-exit: within N trading days of the short expiry -> close ALL
        #    remaining contracts (managed close, not a loss). Time-based, so it
        #    runs even on a stale mark — this is the guarantee we never hold to
        #    expiry (and the SPY-assignment safety net).
        short_exp = getattr(entry, "short_expiry", None)
        if short_exp:
            today = get_us_market_time().strftime("%Y-%m-%d")
            td = trading_days_until(today, short_exp)
            if td <= self.spy_dc_time_exit_days_before:
                remaining = entry.contracts or 0
                if remaining > 0:
                    self._dc_close_calendar(
                        entry,
                        reason=f"time-exit ({td} trading day(s) to short expiry {short_exp})",
                        loss=False,
                    )
                    self._spy_dc_closed_contracts.pop(entry.entry_number, None)
                    return f"TIME-EXIT E#{entry.entry_number}"

        # 2. Laddered profit-taking — value-driven, only on a fully fresh mark.
        fresh = self._dc_refresh_marks(entry)
        if not fresh or entry.net_debit <= 0:
            return None
        return self._spy_dc_take_profit_ladder(entry)

    def _spy_dc_take_profit_ladder(self, entry) -> Optional[str]:
        """Laddered scale-out. ror = unrealized_pnl / net_debit. The cumulative
        target-closed fraction = sum of each met step's ``frac``; the target close
        count = round(target_frac × original_contracts). Partial-close the delta
        above what's already closed; book proportional realized P&L and decrement
        ``entry.contracts``. When contracts reach 0 -> CLOSED.

        With the default single contract, the first met step closes the whole
        position (round(0.25 × 1) = 0, but the highest met step's cumulative frac
        eventually rounds to >=1 — see _ladder_target_close_count, which floors a
        full-exit at the top met rung for 1-lots so a met threshold always acts)."""
        original = int(getattr(entry, "original_contracts", entry.contracts) or entry.contracts)
        if original <= 0 or entry.net_debit <= 0:
            return None
        ror = entry.unrealized_pnl / entry.net_debit
        target_closed = self._ladder_target_close_count(ror, original)
        already = self._spy_dc_closed_contracts.get(entry.entry_number, 0)
        to_close = target_closed - already
        if to_close <= 0:
            return None
        to_close = min(to_close, entry.contracts)
        if to_close <= 0:
            return None
        self._spy_dc_partial_close(entry, to_close, ror)
        self._spy_dc_closed_contracts[entry.entry_number] = already + to_close
        if entry.contracts <= 0:
            return f"PROFIT-CLOSE E#{entry.entry_number} (ror {ror*100:.0f}%)"
        return f"PROFIT-SCALE E#{entry.entry_number} -{to_close}c (ror {ror*100:.0f}%)"

    def _ladder_target_close_count(self, ror: float, original: int) -> int:
        """Cumulative number of (original) contracts that SHOULD be closed at this
        ror. = round(sum(frac for met steps) × original), capped at original. For a
        single contract any met step closes the whole position (the highest met
        rung's cumulative frac is treated as a full exit)."""
        met = [s for s in self.spy_dc_profit_ladder if ror >= s["ror"]]
        if not met:
            return 0
        target_frac = sum(s["frac"] for s in met)
        count = int(round(target_frac * original))
        # 1-lot (or any case where rounding loses the exit): a met threshold must
        # close at least one contract. The cleanest is: once ANY rung is met, close
        # proportionally but never less than enough to honor the highest met rung —
        # for a 1-lot that means full exit at the first met rung.
        count = max(count, 1 if original == 1 else count)
        return min(count, original)

    def _spy_dc_partial_close(self, entry, n_close: int, ror: float) -> None:
        """Book a proportional partial close of ``n_close`` contracts: realize the
        per-contract P&L for that slice into the daily state, charge proportional
        close commission, decrement entry.contracts, and (if the position is now
        flat) mark it CLOSED + clear leg conids. Dry-run only — no broker order.

        Uses the CURRENT mark (caller already refreshed). Realized P&L for the
        slice = unrealized_pnl × (n_close / contracts_before) — the per-contract
        liquidation value of the calendar at this tick."""
        contracts_before = entry.contracts or 0
        if contracts_before <= 0 or n_close <= 0:
            return
        n_close = min(n_close, contracts_before)
        slice_pnl = entry.unrealized_pnl * (n_close / contracts_before)
        close_comm = 4 * self.commission_per_leg * n_close
        self.daily_state.total_realized_pnl += slice_pnl
        self.daily_state.total_commission += close_comm
        entry.close_commission = getattr(entry, "close_commission", 0.0) + close_comm
        entry.contracts = contracts_before - n_close
        logger.info(
            "[SPYDC-PROFIT] E#%s scaled out %dc (ror %.0f%%): realized $%.2f, %dc remaining",
            entry.entry_number, n_close, ror * 100, slice_pnl, entry.contracts,
        )
        if entry.contracts <= 0:
            entry.dc_phase = DCPhase.CLOSED
            entry.call_side_pivot_closed = entry.put_side_pivot_closed = True
            self._dc_clear_leg_conids(entry)
            logger.info(
                "[SPYDC-PROFIT] E#%s fully closed via profit ladder (final ror %.0f%%)",
                entry.entry_number, ror * 100,
            )
            if getattr(self, "_dc_recorder", None):
                entry_date = entry.entry_time.strftime("%Y-%m-%d") if entry.entry_time else ""
                self._dc_recorder.record_outcome(
                    entry, "profit_ladder", slice_pnl, getattr(self, "current_price", None),
                    entry_date, get_us_market_time().strftime("%Y-%m-%d"),
                )
        # Persist the (partial or full) close now — crash-window guard.
        self._save_state_to_disk()

    # ------------------------------------------------------------------
    # Per-expiry settlement (CALENDAR-leftover backstop only — E never transforms)
    # ------------------------------------------------------------------

    def _dc_settle_entry(self, entry, spx: float) -> None:
        """Book terminal P&L for a position that reached its short expiry and mark
        CLOSED. E NEVER transforms, so there is only the CALENDAR-leftover branch
        (the time-exit should have closed it before expiry; this is the backstop).
        Liquidate at the current mark."""
        self._dc_refresh_marks(entry)
        realized = entry.unrealized_pnl  # calendar mark - debit
        self.daily_state.total_realized_pnl += realized
        entry.dc_phase = DCPhase.CLOSED
        entry.call_side_expired = entry.put_side_expired = True  # settled at expiry
        self._dc_clear_leg_conids(entry)
        self._spy_dc_closed_contracts.pop(entry.entry_number, None)
        logger.info(
            "[SPYDC-SETTLE] E#%s (CALENDAR-leftover) settled at SPY %.2f: P&L $%.2f (debit $%.2f)",
            entry.entry_number, spx, realized, entry.net_debit,
        )
        if getattr(self, "_dc_recorder", None):
            entry_date = entry.entry_time.strftime("%Y-%m-%d") if entry.entry_time else ""
            self._dc_recorder.record_outcome(
                entry, "calendar_leftover", realized, spx, entry_date,
                get_us_market_time().strftime("%Y-%m-%d"),
            )
