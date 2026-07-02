"""DoubleCalendarStrategy — "DC Time Machine" (Strategy D), dry-run-LOCKED.

Source strategy: Steve Burnich's "DC Time Machine" (YouTube JtGW1wNFNIY). Open a
SPX **double calendar** for a net DEBIT (sell a shorter-dated option, buy a
longer-dated option at the same strike, on both a call and a put side ~30-40δ),
then after a small intraday profit fire a "transformer" that closes the long
back-dated legs and buys protective wings against the remaining shorts — turning
the position into an iron condor whose collected credit exceeds the original
debit by at least the wing width (structurally risk-free). The condor is then
held to expiry. Full spec: /tmp/strategy_D_dc_time_machine.md.

STATUS: the full dry-run strategy is IMPLEMENTED (Phases 1-7) and dry-run-LOCKED
(no real orders). It picks two expiries + 30-40δ strikes, opens the net-debit
calendar from real mids, transforms to a risk-free IC (or 20%-debit-stops /
EOD-closes), survives restarts (sidecar persistence), settles at the short
expiry, records to its own DB, and renders via Telegram /calendars + /api/dc/status.

DESIGN — reuse structure, override economics:
  * CalendarEntry (calendar_entry.py) SUBCLASSES IronCondorEntry to reuse the
    structural plumbing (Leg bridge, active_entries, state save/load,
    (conid,quantity) reconciliation) but OVERRIDES every economic property so the
    IC credit-vertical math NEVER runs for a debit calendar. A double calendar's
    4 legs map onto (short_call,long_call,short_put,long_put): short+long of a
    side share the STRIKE, differ in EXPIRY; post-transform the longs move to wing
    strikes on the short expiry → a real same-expiry IC.
  * Dry-run LOCK in __init__ (mirrors StrangleStrategy): a non-dry-run
    construction raises ConfigError BEFORE any broker I/O.
  * BOT_NAME="DCTM" and requires_protective_wings=False so the shared safety path
    never treats D's debit/calendar legs as a naked-short emergency.
  * Multi-day lifecycle overrides: _reset_for_new_day carries open positions
    across the daily reset; check_after_hours_settlement settles at the short
    expiry and treats a held position as NORMAL. ZERO edits to the fix-scarred
    base save/load/settlement — D persists via a SIDECAR (dc_open_trades.json).

COEXISTENCE WITH THE LIVE VARIANTS (A/B/C) — READ BEFORE FLIPPING dry_run=false
>>> CANONICAL GO-LIVE RUNBOOK: docs/migration/D_GOLIVE_RUNBOOK.md <<<
Read that runbook IN FULL before flipping dry_run=false, building any real-order
path, removing the dry-run lock, or touching a coexistence guard. It is the single
source of truth for taking D live — every step, the arm-gate, the flip / rollback /
emergency-flatten procedures, and the readiness gate. Supporting analysis:
docs/migration/D_GOLIVE_SCOPE_AND_AUDIT.md (scope + audit + NO-GO verdict) and
docs/migration/D_MVL_PHASE1_PLAN.md (the MVL-D first-live-step plan).
A/B/C only coexist safely today because they are all 0DTE and the shared IBKR
paper account is FLAT overnight. A REAL-ORDER multi-day D holding positions
overnight in that shared account would be SEEN by C's account-wide guards. Before
this strategy is ever run with dry_run=false next to a live C, ship:
  MUST-FIX #1: scope C's STATE-004 overnight guard (strategy.py:_reset_for_new_day)
               to C's OWN conids, or D's overnight legs halt C every morning.
  MUST-FIX #2: scope C's orphan sweep (strategy.py:_reconcile_orphan_sweep) the
               same way, or C floods CRITICAL "orphan position" alerts.
  MUST-FIX #3: budget per-variant buying power (D's debit ties up the shared
               account BP that C draws from).
In dry_run (the only mode this class permits today) D places ZERO real orders,
so the account stays flat and NONE of those vectors touch C.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time
from typing import Dict, List, Optional, Tuple

from bots.hydra.base_strategy import ConfigError, MEICState
from bots.hydra.leg import LEG_NAMES
# DCPhase + the two-expiry CalendarEntry model live in calendar_entry (Phase 1
# foundation). Re-exported here so callers/tests can import them from the
# strategy module.
from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: F401
from bots.hydra.calendar_chain import generate_candidate_expiries, pick_calendar_expiries
from bots.hydra.calendar_strategy_base import CalendarStrategyBase
from bots.hydra.strategy import HydraStrategy  # noqa: F401  (re-exported for back-compat)
from shared.market_hours import get_us_market_time

logger = logging.getLogger(__name__)

# Canonical-leg-name -> short tag for synthetic dry-run position ids.
_DC_LEG_ABBR = {"short_call": "SC", "long_call": "LC", "short_put": "SP", "long_put": "LP"}


class DoubleCalendarStrategy(CalendarStrategyBase):
    """Strategy D — double calendar -> risk-free iron condor (DC Time Machine).

    The full strategy runs in SIMULATION (dry-run-LOCKED): entry, transform, 20%
    stop, and settlement are implemented and exercised against live two-expiration
    quotes, but NO real orders are placed. What does NOT exist yet is the real-order
    execution path + the coexistence guards (STATE-004 / orphan / BP scoping) — by
    deliberate safety design, not omission. Building those and flipping dry_run=false
    is a gated, multi-week effort: read docs/migration/D_GOLIVE_RUNBOOK.md first.
    Inherits HYDRA's scheduling / monitoring / state / IBKR / Telegram / DB machinery.
    """

    BOT_NAME = "DCTM"
    # Debit calendar + transformer manage their own structure; the shorts are not
    # an "unhedged naked short" emergency the base path should auto-close (the
    # long back-dated leg is the protection in the calendar phase, the wing in the
    # condor phase). Same flag StrangleStrategy uses for the same reason.
    requires_protective_wings = False

    def __init__(self, *args, **kwargs):
        """Construct, enforcing the dry-run-only lock (mirrors StrangleStrategy).

        The entry/transformer/accounting logic IS implemented (Phases 1-7) and runs
        dry-run-only, but the coexistence MUST-FIXes are NOT in place, so a live
        (real-order) construction is refused BEFORE super().__init__ runs any broker
        I/O. ``build_strategy`` always passes ``dry_run`` as a kwarg.
        """
        if not kwargs.get("dry_run", False):
            raise ConfigError(
                "DoubleCalendarStrategy (Strategy D) is dry-run-LOCKED. The entry / "
                "transformer / debit-P&L / multi-day-settlement logic IS implemented "
                "(Phases 1-7), but running it with real paper orders next to the live "
                "variants requires the coexistence MUST-FIXes (scope STATE-004 + "
                "orphan sweep to per-variant conids; budget buying power). Going live "
                "is a gated, multi-week build, NOT a config flip — follow the canonical "
                "runbook docs/migration/D_GOLIVE_RUNBOOK.md. Set dry_run=true, or do "
                "not select strategy.name='double_calendar'."
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
                "DoubleCalendarStrategy resolved to dry_run=false after init — "
                "refusing to arm a coexistence-unsafe strategy. "
                "See docs/migration/D_GOLIVE_RUNBOOK.md before attempting go-live."
            )

        # DC-specific knobs (config.strategy.double_calendar.*). Read here to wire
        # the config surface for the entry logic that consumes them.
        cfg = getattr(self, "strategy_config", {}) or {}
        dc = cfg.get("double_calendar", {}) or {}
        self.dc_short_dte_min = int(dc.get("short_dte_min", 6))
        self.dc_short_dte_max = int(dc.get("short_dte_max", 15))
        self.dc_long_extra_dte_min = int(dc.get("long_extra_dte_min", 1))
        self.dc_long_extra_dte_max = int(dc.get("long_extra_dte_max", 4))
        self.dc_target_delta = float(dc.get("target_delta", 0.35))
        self.dc_delta_band = tuple(dc.get("delta_band", [0.30, 0.40]))
        self.dc_profit_trigger_pct = float(dc.get("profit_trigger_pct", 0.075))
        self.dc_pre_transform_stop_pct = float(dc.get("pre_transform_stop_pct", 0.20))
        self.dc_wing_width = float(dc.get("wing_width", 5))
        self.dc_eod_close_if_no_transform = bool(dc.get("eod_close_if_no_transform", True))
        # Prefer the following-week Friday weekly for the short expiry (Burnich's
        # setup); else the earliest in-window candidate. Phase 2.
        self.dc_prefer_friday = bool(dc.get("prefer_friday", True))
        # EOD-day-1 close cutoff (ET) for an un-transformed calendar. Phase 4.
        cutoff = str(dc.get("eod_cutoff_et", "15:55"))
        try:
            hh, mm = (int(x) for x in cutoff.split(":")[:2])
            self.dc_eod_cutoff = time(hh, mm)
        except (ValueError, TypeError):
            self.dc_eod_cutoff = time(15, 55)
        # Bounded strike-scan knobs (2026-06-15 latency fix): center the scan on a
        # VIX-EM estimate of the target-delta OTM distance and read greeks for only
        # a small window of strikes (capped), instead of scanning ~40 cold conids.
        self.dc_delta_otm_fraction = float(dc.get("delta_otm_fraction", 0.40))
        self.dc_delta_window = int(dc.get("delta_window", 8))
        self.dc_delta_max_reads = int(dc.get("delta_max_reads", 6))
        # Stop hardening (2026-06-16). A double calendar's mark is built from 4
        # independent option mids, so a single noisy tick can spike past the -20%
        # stop and revert (live: E#1 closed at a realized -6.3% on a -20%
        # trigger). (1) require the breach to PERSIST stop_confirm_seconds before
        # closing (MKT-046 analogue); (2) gate value-driven decisions on
        # real-time quotes; (3) cap concurrent open calendars so the daily entry
        # can't stack multi-day debit/BP.
        self.dc_stop_confirm_seconds = float(dc.get("stop_confirm_seconds", 20.0))
        # Real-time quote gate is OPT-IN (default off): the breach-persistence
        # window above is the primary noise defense and works regardless, whereas
        # an over-eager realtime gate could FREEZE D entirely if non-0DTE SPXW
        # snapshots don't reliably carry the 6509 'R' flag. Enable once non-0DTE
        # real-time entitlement is confirmed on the VM.
        self.dc_require_realtime_quotes = bool(dc.get("require_realtime_quotes", False))
        self.dc_max_concurrent = int(dc.get("max_concurrent", 1))
        # Per-variant buying-power budget (MUST-FIX #3): cap the total net debit D
        # deploys across OPEN calendars so it can't starve the shared account's BP
        # (the LIVE variant C draws on the same account). 0 disables.
        self.dc_max_deployed_debit = float(dc.get("max_deployed_debit", 5000.0))
        # Realistic dry-run fill model (2026-06-17): price every simulated fill
        # across the bid/ask spread instead of the mid. aggressiveness 1.0 = full
        # touch (buy@ask / sell@bid) — the honest worst case for a marketable order;
        # 0.0 = the old mid pricing. Applied at entry, transform, mark + close.
        _fm = cfg.get("dry_run_fill_model", {}) or {}
        self._dc_fill_agg = float(_fm.get("aggressiveness", 1.0))
        self._dc_fill_slippage = float(_fm.get("extra_slippage_per_leg", 0.0))
        # entry_number -> first-breach time, for the stop-confirmation window.
        self._dc_stop_breach: Dict[int, datetime] = {}

        logger.info(
            "DCTM initialized (DRY-RUN, LOCKED). Short DTE %d-%d, long +%d-%d, "
            "target Δ%.2f %s, profit-trigger %.1f%%, pre-transform stop %.0f%%, wing %.0fpt. "
            "Entry/transformer/stop/settlement logic LIVE (simulated); no real orders.",
            self.dc_short_dte_min, self.dc_short_dte_max,
            self.dc_long_extra_dte_min, self.dc_long_extra_dte_max,
            self.dc_target_delta, list(self.dc_delta_band),
            self.dc_profit_trigger_pct * 100, self.dc_pre_transform_stop_pct * 100,
            self.dc_wing_width,
        )

        # Phase 6: D's calendar-shaped DB tables in D's OWN (isolated) DB file —
        # separate from the shared DataRecorder (which the base init already set
        # up for market_ticks). Non-critical; never blocks trading.
        self._dc_recorder = None
        try:
            from bots.hydra.dc_recorder import DCDataRecorder
            # SEPARATE DB file (not the shared backtesting.db the base DataRecorder
            # uses for market_ticks) so D's calendar tables never share a SQLite
            # file/connection with the base recorder — full isolation, no
            # two-connections-on-one-file concern.
            dc_db = os.path.join(os.path.dirname(self.state_file), "dc_calendar.db")
            self._dc_recorder = DCDataRecorder(dc_db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DCDataRecorder init failed (non-critical): %s", exc)

    # ------------------------------------------------------------------
    # Multi-day lifecycle overrides (REAL — the part that matters today)
    # ------------------------------------------------------------------

    def _calculate_max_loss_with_stops(self) -> float:
        """D's max loss WITH the 20%-debit stop working = stop_pct × open debit.
        The base IC math (stop_level − credit per side) is meaningless for a
        net-debit calendar and produced phantom numbers in the heartbeat."""
        return self._dc_open_debit_at_risk() * self.dc_pre_transform_stop_pct

    def get_monitoring_mode(self) -> str:
        """Calendar-aware vigilance. The base keys off call/put_side_stop (which D
        never sets → always 'normal' → ~12.5s checks). Go vigilant (2s) when a
        stop breach is being CONFIRMED, or when any open calendar's P&L is within
        75% of the stop or transform trigger — so the confirm window resolves and
        the transformer fires promptly."""
        if not self.daily_state.active_entries:
            self._current_monitoring_mode = "normal"
            return "normal"
        if self._dc_stop_breach:  # mid-confirmation -> watch closely
            self._current_monitoring_mode = "vigilant"
            return "vigilant"
        for entry in self.daily_state.active_entries:
            if not isinstance(entry, CalendarEntry):
                continue
            debit = float(getattr(entry, "net_debit", 0.0) or 0.0)
            if debit <= 0:
                continue
            pnl_pct = entry.unrealized_pnl / debit
            if (pnl_pct <= -0.75 * self.dc_pre_transform_stop_pct
                    or pnl_pct >= 0.75 * self.dc_profit_trigger_pct):
                self._current_monitoring_mode = "vigilant"
                return "vigilant"
        self._current_monitoring_mode = "normal"
        return "normal"

    # ------------------------------------------------------------------
    # Two-expiry data layer (Phase 2) — pick expiries, resolve per-expiry
    # conids, read per-expiry quotes + IV. Thin wrappers over the existing
    # broker-data methods called with EXPLICIT (non-0DTE) expiries; the pure
    # expiry-selection logic lives in calendar_chain.
    # ------------------------------------------------------------------

    def _dc_pick_expiries(self) -> Optional[Tuple[str, list]]:
        """Choose the short expiry + an ORDERED list of candidate long expiries
        (smallest gap first) for today. PURE / fast — NO per-candidate broker
        calls. (The prior ATM listed-filter cost ~50s live AND was month-granular,
        so it couldn't even detect gap weekdays.) Day-granular listing + the
        both-expiry strike requirement are enforced later by _dc_delta_target_strike,
        which batch-reads the chain on BOTH expiries and only accepts a strike
        listed on both. _calculate_strikes then tries the long candidates in order.
        Returns (short_expiry, [long_candidates]) or None."""
        today_iso = get_us_market_time().strftime("%Y-%m-%d")
        t = date.fromisoformat(today_iso)
        horizon = self.dc_short_dte_max + self.dc_long_extra_dte_max + 3
        candidates = generate_candidate_expiries(today_iso, horizon)
        picked = pick_calendar_expiries(
            candidates, today_iso,
            self.dc_short_dte_min, self.dc_short_dte_max,
            self.dc_long_extra_dte_min, self.dc_long_extra_dte_max,
            prefer_friday=self.dc_prefer_friday,
        )
        if picked is None:
            logger.warning(
                "[DCTM-EXPIRIES] no viable short/long window: short %d-%d DTE, long +%d-%d (candidates=%d)",
                self.dc_short_dte_min, self.dc_short_dte_max,
                self.dc_long_extra_dte_min, self.dc_long_extra_dte_max, len(candidates),
            )
            return None
        short_exp, _ = picked
        sd = (date.fromisoformat(short_exp) - t).days
        longs = sorted(
            [c for c in candidates
             if self.dc_long_extra_dte_min <= (date.fromisoformat(c) - t).days - sd <= self.dc_long_extra_dte_max],
            key=lambda c: date.fromisoformat(c),
        )
        logger.info("[DCTM-EXPIRIES] short=%s (%dDTE), long candidates=%s", short_exp, sd, longs)
        return short_exp, longs

    # ------------------------------------------------------------------
    # Entry + dry-run simulation (Phase 3)
    # Pick the 30-40delta two-expiry strikes, open the net-DEBIT double calendar,
    # and SIMULATE the fills from REAL mids (dry-run: no broker order, synthetic
    # DRY ids). Overridden (not inherited) so D NEVER runs HYDRA's IC entry.
    # ------------------------------------------------------------------

    def _dc_pick_delta_strike(
        self, right: str, base: float, window: List[float],
        short_map: Dict[float, int], long_map: Dict[float, int],
    ) -> Optional[float]:
        """Pick the ~dc_target_delta OTM strike LISTED ON BOTH expiries, reading as
        FEW greeks as possible.

        SEEDED MONOTONIC STEP-SEARCH (fixes the residual ~67s scan). Inputs are the
        already-fetched conid maps for the short + long expiry (the caller batches
        ONE chain read per expiry). |delta| is monotonic in OTM distance, and the EM
        ``base`` is calibrated to ≈ target Δ, so we seed at the both-expiry strike
        nearest ``base`` and step in the direction that moves delta toward target,
        stopping the moment we cross the band. That's typically 1-4 cold greeks reads
        per side instead of ~10-40 — the difference between a ~30s and a ~4-min entry
        burst on the shared broker session. ``short_map`` supplies the conid we read
        greeks on; requiring the strike in ``long_map`` too is the gap-day guard."""
        both = sorted(k for k in window if k in short_map and k in long_map)
        if not both:
            logger.info("[DCTM] no %s strike near %.0f listed on BOTH expiries", right, base)
            return None
        # OTM order: |delta| DECREASES along increasing index for both rights
        # (calls -> higher strike; puts -> lower strike).
        ordered = both if right == "Call" else list(reversed(both))
        lo, hi = self.dc_delta_band
        target = self.dc_target_delta
        cap = int(getattr(self, "dc_delta_max_reads", 6))
        cache: Dict[float, Optional[float]] = {}

        def delta_at(k: float) -> Optional[float]:
            if k in cache:
                return cache[k]
            try:
                d = abs(float((self.broker.get_option_greeks(short_map[k]) or {}).get("delta")))
            except Exception:
                d = None
            cache[k] = d if (d and d > 0) else None
            return cache[k]

        # Seed at the both-expiry strike nearest the EM base (≈ target Δ).
        i = min(range(len(ordered)), key=lambda j: abs(ordered[j] - base))
        best = None  # (abs(delta-target), strike)
        while 0 <= i < len(ordered) and len(cache) < cap:
            k = ordered[i]
            d = delta_at(k)
            if d is None:
                i += 1  # bad/empty read — march OTM, never revisit
                continue
            in_band = lo <= d <= hi
            if in_band:
                score = abs(d - target)
                if best is None or score < best[0]:
                    best = (score, k)
            nxt = i + 1 if d > target else i - 1  # delta decreases with index
            if best is not None and not in_band:
                break  # had an in-band hit, now stepped out the far side — done
            if 0 <= nxt < len(ordered) and ordered[nxt] in cache:
                break  # target sits between two read strikes — refined, done
            i = nxt
        if best is None:
            logger.info(
                "[DCTM] no %s strike in Δ band %s near %.0f (read %d)",
                right, list(self.dc_delta_band), base, len(cache),
            )
        return best[1] if best else None

    def _calculate_strikes(self, entry) -> bool:
        """Pick the two expiries and the 30-40delta short strikes (listed on BOTH
        expiries), and stamp them onto the calendar entry. Short+long of a side
        share a STRIKE but differ in EXPIRY. Tries the long candidates in order so
        a thin long expiry doesn't skip the entry when another long works.

        Fetches each expiry's chain ONCE (one _read_option_chain per expiry returns
        BOTH the call and put maps over a combined call+put window), so the broker
        work per long candidate is 2 chain reads + the few greeks reads the seeded
        step-search needs — not the old 4 chain reads + ~40 cold greeks."""
        spx = self.current_price
        if not spx or spx <= 0:
            logger.error("[DCTM] no SPX price — cannot calculate strikes")
            return False
        picked = self._dc_pick_expiries()
        if not picked:
            return False
        short_exp, longs = picked
        inc = getattr(self, "strike_increment", 5) or 5
        win = int(getattr(self, "dc_delta_window", 8))
        est = self._dc_em_otm_distance(spx, short_exp)  # OTM distance depends on DTE, not side
        call_base = round((spx + est) / inc) * inc
        put_base = round((spx - est) / inc) * inc
        call_win = [call_base + s * inc for s in range(-win, win + 1) if call_base + s * inc > 0]
        put_win = [put_base + s * inc for s in range(-win, win + 1) if put_base + s * inc > 0]
        combined = sorted(set(call_win) | set(put_win))  # one chain read covers both sides
        for long_exp in longs[:2]:  # try the nearest 2 long candidates
            s_call, s_put = self._read_option_chain(short_exp, combined)
            l_call, l_put = self._read_option_chain(long_exp, combined)
            kc = self._dc_pick_delta_strike("Call", call_base, call_win, s_call, l_call)
            kp = self._dc_pick_delta_strike("Put", put_base, put_win, s_put, l_put)
            if kc and kp:
                entry.short_call_strike = entry.long_call_strike = kc
                entry.short_put_strike = entry.long_put_strike = kp
                entry.legs["short_call"].expiry = short_exp
                entry.legs["long_call"].expiry = long_exp
                entry.legs["short_put"].expiry = short_exp
                entry.legs["long_put"].expiry = long_exp
                logger.info("[DCTM] strikes Kc=%s Kp=%s | short=%s long=%s", kc, kp, short_exp, long_exp)
                return True
            logger.info("[DCTM] long %s yielded no both-expiry strike (call=%s put=%s) — trying next", long_exp, kc, kp)
        logger.warning(
            "[DCTM] no ~%.2fΔ strikes listed on both short %s + any long %s",
            self.dc_target_delta, short_exp, longs[:2],
        )
        return False

    def _dc_pre_entry_gates(self, entry_num: int) -> Optional[str]:
        """Minimal pre-entry gates (reuse the shared helpers): orphaned orders,
        market halt, buying power. Deliberately simpler than HYDRA's IC gates (no
        VIX regime, no MKT-011 credit gate — those are credit-IC concepts)."""
        # Concurrent-calendar cap: D holds a multi-day debit position, so opening
        # a fresh daily calendar while one is still open would stack debit + tie
        # up more of the shared account's BP. Default 1 (one calendar at a time).
        open_cals = sum(1 for e in self.daily_state.entries if self._dc_entry_is_open(e))
        if open_cals >= getattr(self, "dc_max_concurrent", 1):
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            return (
                f"Entry #{entry_num} skipped - {open_cals} calendar(s) already open "
                f"(max {self.dc_max_concurrent})"
            )
        # Per-variant BP budget: don't open a new calendar if the debit already
        # deployed across open calendars is at/over the configured budget.
        budget = getattr(self, "dc_max_deployed_debit", 0.0)
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
        """Open the next scheduled double calendar (dry-run simulated).

        D is dry-run-LOCKED, so the simulated path always runs — no real order
        ever reaches the broker. DB recording (Phase 6) and the transformer / 20%
        stop / EOD close (_check_stop_losses, Phase 4) are implemented.
        """
        entry_num = self._next_entry_index + 1
        logger.info("[DCTM] initiating entry #%s", entry_num)

        gate = self._dc_pre_entry_gates(entry_num)
        if gate is not None:
            return gate

        self._entry_in_progress = True
        self.state = MEICState.ENTRY_IN_PROGRESS
        try:
            entry = CalendarEntry(entry_number=entry_num)
            entry.contracts = self.contracts_per_entry
            entry.strategy_id = f"dctm_{get_us_market_time().strftime('%Y%m%d')}_{entry_num:03d}"

            if not self._calculate_strikes(entry):
                self.daily_state.entries_skipped += 1
                self._next_entry_index += 1
                return f"Entry #{entry_num} skipped - strike/expiry selection failed"

            if not self._dc_simulate_entry(entry):
                self.daily_state.entries_failed += 1
                self._next_entry_index += 1
                return f"Entry #{entry_num} failed - could not price/simulate calendar"

            entry.entry_time = get_us_market_time()
            self.daily_state.entries.append(entry)
            self.daily_state.entries_completed += 1
            # 4-leg open commission (display only; debit P&L is separate).
            entry.open_commission = 4 * self.commission_per_leg * self.contracts_per_entry
            self.daily_state.total_commission += entry.open_commission

            self._save_state_to_disk()  # persist before returning (crash-window guard)
            if getattr(self, "_dc_recorder", None):
                self._dc_recorder.record_calendar_entry(
                    entry, self.current_price, entry.entry_time.strftime("%Y-%m-%d")
                )
            self._next_entry_index += 1
            return (
                f"Entry #{entry_num} placed: DC Time Machine "
                f"Kc {entry.short_call_strike:.0f} / Kp {entry.short_put_strike:.0f}, "
                f"debit ${entry.net_debit:.2f} (DRY)"
            )
        finally:
            self._entry_in_progress = False
            self.state = MEICState.MONITORING

    # ------------------------------------------------------------------
    # Transformer + risk controls (Phase 4) — the DC Time Machine's mechanic.
    # Per open calendar each tick: ~profit_trigger -> attempt the transformer
    # (close longs + buy wings, FIRE ONLY IF transform_credit >= debit + wing =
    # risk-free); else 20%-of-debit hard stop; else EOD-day-1 close. A TRANSFORMED
    # position holds to expiry (settled in Phase 5).
    # ------------------------------------------------------------------

    def _check_stop_losses(self):
        """Manage every open double calendar. CALENDAR-phase entries run the
        transform/stop/EOD logic; TRANSFORMED (held-to-expiry) entries take NO
        action but are re-marked each tick so the heartbeat + dashboard show a
        LIVE mark converging to the locked profit, not the frozen transform-time
        snapshot (2026-06-17 fix — transformed marks were never refreshed, so
        ``unrealized_pnl`` stayed pinned at the transform-time leg prices).
        Returns a summary of actions taken this tick, or None."""
        actions = []
        remarked = False
        for entry in list(self.daily_state.active_entries):
            if not isinstance(entry, CalendarEntry):
                continue
            if entry.dc_phase == DCPhase.CALENDAR:
                act = self._dc_manage_calendar(entry)
                if act:
                    actions.append(act)
            elif entry.dc_phase == DCPhase.TRANSFORMED:
                # Held to expiry — no management action; just refresh the leg
                # mids so the MTM (entry.unrealized_pnl) tracks live, not frozen.
                self._dc_refresh_marks(entry)
                remarked = True
        if remarked:
            # Persist the refreshed leg prices + computed MTM so the sidecar-backed
            # dashboard/Telegram view is live too (the heartbeat reads it in-memory).
            self._dc_save_sidecar()
        return "; ".join(actions) if actions else None

    def _dc_manage_calendar(self, entry) -> Optional[str]:
        """One open calendar's intraday decision (priority: transform -> stop -> EOD)."""
        fresh = self._dc_refresh_marks(entry)
        if entry.net_debit <= 0:
            return None

        # The value-driven decisions (transform, % stop) act ONLY on a fully fresh
        # mark this tick — a single stale leg could otherwise fire a false stop or
        # a mistimed transform. EOD close is TIME-based and runs regardless.
        if fresh:
            # MOVEMENT since the opening mark (not absolute pnl/debit): a fresh
            # calendar reads 0% here instead of the false ~-20% birth spread that
            # was firing the stop same-day (2026-07-02 audit). Burnich's 7.5%
            # transform pop and 20% stop are both moves-from-entry, exactly as a
            # real broker's P&L shows them. Realized P&L at close stays honest.
            pnl_pct = entry.pnl_move_pct
            en = entry.entry_number
            breaches = self._dc_stop_breach

            # 1. profit trigger -> attempt the (risk-free-gated) transformer
            if pnl_pct >= self.dc_profit_trigger_pct:
                if self._dc_attempt_transform(entry):
                    breaches.pop(en, None)
                    return f"TRANSFORM E#{entry.entry_number}"
                # gate failed (not yet risk-free) -> fall through to stop/EOD checks

            # 2. pre-transform hard stop (~20% of debit) — CONFIRM-BEFORE-CLOSE.
            # The calendar mark is noisy (4 independent mids); require the breach
            # to persist dc_stop_confirm_seconds before closing, and clear it on
            # recovery (MKT-046 analogue). Without this a single noisy tick fired
            # a real stop (2026-06-16).
            now = get_us_market_time()
            if pnl_pct <= -self.dc_pre_transform_stop_pct:
                if en not in breaches:
                    breaches[en] = now
                    logger.warning(
                        "[DCTM] E#%s breached -%.0f%% stop (pnl %.1f%%) — confirming %.0fs...",
                        en, self.dc_pre_transform_stop_pct * 100, pnl_pct * 100,
                        self.dc_stop_confirm_seconds,
                    )
                elif (now - breaches[en]).total_seconds() >= self.dc_stop_confirm_seconds:
                    self._dc_close_calendar(entry, reason="20%-debit stop", loss=True)
                    return f"STOP E#{entry.entry_number}"
                # else: still inside the confirm window — hold
            elif en in breaches:
                logger.info(
                    "[DCTM] E#%s stop breach recovered (pnl %.1f%%) — false stop avoided",
                    en, pnl_pct * 100,
                )
                breaches.pop(en, None)
        else:
            logger.debug(
                "[DCTM] E#%s marks not fresh this tick — skipping transform/stop",
                entry.entry_number,
            )

        # 3. EOD-day-1 close if still un-transformed (time-based, runs regardless)
        if self.dc_eod_close_if_no_transform and self._dc_past_eod_cutoff():
            self._dc_close_calendar(entry, reason="EOD no-transform", loss=False)
            return f"EOD-CLOSE E#{entry.entry_number}"
        return None

    def _dc_attempt_transform(self, entry) -> bool:
        """The transformer (dry-run): close the two back-dated long legs and buy
        protective wings on the SHORT expiry, turning the calendar into an iron
        condor. Fires ONLY if the realized transform credit clears
        net_debit + wing_width (i.e. structurally risk-free); otherwise holds.
        Returns True iff the transform fired."""
        n = entry.contracts or self.contracts_per_entry
        short_exp = entry.short_expiry
        kc, kp = entry.short_call_strike, entry.short_put_strike
        wing = self.dc_wing_width

        wing_call_conid = self._get_option_uic(kc + wing, "Call", short_exp)
        wing_put_conid = self._get_option_uic(kp - wing, "Put", short_exp)
        if not wing_call_conid or not wing_put_conid:
            logger.info(
                "[DCTM] transform deferred — wing conids unresolved (C %s / P %s @ %s)",
                kc + wing, kp - wing, short_exp,
            )
            return False

        # Sell the back-dated longs (toward BID); buy the wings (toward ASK) — the
        # REALISTIC transform fills (2026-06-17), not the mid. Crossing the spread
        # on these 4 legs is exactly the cost an optimistic mid hid, and it's what
        # makes the risk-free gate honest (the mid made the transform look risk-free
        # cheaper + faster than real fills would allow).
        lq = self._dc_read_leg_quotes({"long_call": entry.long_call_uic, "long_put": entry.long_put_uic})
        wq = self._dc_read_leg_quotes({"wing_call": wing_call_conid, "wing_put": wing_put_conid})
        lc = self._dc_fill_price(lq["long_call"], "sell")
        lp = self._dc_fill_price(lq["long_put"], "sell")
        wc = self._dc_fill_price(wq["wing_call"], "buy")
        wp = self._dc_fill_price(wq["wing_put"], "buy")
        if any(m is None or m <= 0 for m in (lc, lp, wc, wp)):
            logger.info("[DCTM] transform deferred — incomplete fills (lc=%s lp=%s wc=%s wp=%s)", lc, lp, wc, wp)
            return False

        transform_credit = (lc + lp - wc - wp) * 100 * n
        threshold = entry.net_debit + wing * 100 * n
        if transform_credit < threshold:
            logger.info(
                "[DCTM] transform NOT risk-free yet: credit $%.2f < debit+wing $%.2f — holding",
                transform_credit, threshold,
            )
            return False

        # FIRE: the longs become wings on the short expiry → a same-expiry IC.
        lc_leg, lp_leg = entry.legs["long_call"], entry.legs["long_put"]
        lc_leg.strike, lc_leg.expiry, lc_leg.uic = kc + wing, short_exp, wing_call_conid
        lc_leg.price = lc_leg.fill_price = wc
        lp_leg.strike, lp_leg.expiry, lp_leg.uic = kp - wing, short_exp, wing_put_conid
        lp_leg.price = lp_leg.fill_price = wp

        # Resulting IC credit (display/total_credit): short premium - wing cost.
        sq = self._dc_read_leg_quotes({"short_call": entry.short_call_uic, "short_put": entry.short_put_uic})
        sc = sq["short_call"]["mid"] or entry.short_call_price
        sp = sq["short_put"]["mid"] or entry.short_put_price
        entry.legs["short_call"].price = sc
        entry.legs["short_put"].price = sp
        entry.call_spread_credit = max(0.0, sc - wc) * 100 * n
        entry.put_spread_credit = max(0.0, sp - wp) * 100 * n

        entry.transform_credit = transform_credit
        entry.wing_width = wing
        entry.transformed_at = get_us_market_time().isoformat()
        entry.dc_phase = DCPhase.TRANSFORMED
        entry.evaluate_risk_free()  # gate == risk-free condition, so this is True

        logger.info(
            "[DCTM-TRANSFORM] E#%s credit $%.2f >= debit+wing $%.2f → IC C %s/%s P %s/%s (exp %s)",
            entry.entry_number, transform_credit, threshold,
            kc, kc + wing, kp, kp - wing, short_exp,
        )
        logger.info(
            "[DCTM-RISKFREE] E#%s risk-free achieved (max loss $0): debit $%.2f, transform credit $%.2f, wing %.0fpt",
            entry.entry_number, entry.net_debit, transform_credit, wing,
        )
        if getattr(self, "_dc_recorder", None):
            self._dc_recorder.record_transformation(entry, get_us_market_time().strftime("%Y-%m-%d"))
        return True

    # ── Per-expiry settlement ──────────────────────────────────────────

    def _dc_settle_entry(self, entry, spx: float) -> None:
        """Book terminal P&L for a position at its short expiry and mark CLOSED.

        TRANSFORMED (held iron condor): realized = transform_credit - net_debit -
        IC intrinsic at S* (each side's intrinsic capped at the wing). Risk-free
        when the transform gate held, so this is >= 0 in the worst case.
        CALENDAR (defensive — EOD-day-1 close should prevent reaching expiry):
        liquidate at the current mark.
        """
        n = entry.contracts or self.contracts_per_entry
        if entry.dc_phase == DCPhase.TRANSFORMED:
            wing = entry.wing_width
            call_intr = self._clamp(spx - entry.short_call_strike, 0.0, wing)
            put_intr = self._clamp(entry.short_put_strike - spx, 0.0, wing)
            ic_cost = (call_intr + put_intr) * 100 * n
            realized = entry.transform_credit - entry.net_debit - ic_cost
            tag = "TRANSFORMED"
        else:
            self._dc_refresh_marks(entry)
            realized = entry.unrealized_pnl  # calendar mark - debit
            tag = "CALENDAR-leftover"
            # Stamp the close-side commission so this leftover-close path records
            # it in dc_outcomes (and the edge reader charges it) like any other
            # close — instead of $0. Mirrors _dc_close_calendar (4 legs). Only the
            # CALENDAR branch: a TRANSFORMED IC cash-settles at expiry (no closing
            # trade) and is excluded from the edge verdict anyway. (2026-06-23 audit.)
            close_comm = 4 * self.commission_per_leg * n
            self.daily_state.total_commission += close_comm
            entry.close_commission = getattr(entry, "close_commission", 0.0) + close_comm
        self.daily_state.total_realized_pnl += realized
        entry.dc_phase = DCPhase.CLOSED
        entry.call_side_expired = entry.put_side_expired = True  # settled at expiry
        self._dc_clear_leg_conids(entry)
        logger.info(
            "[DCTM-SETTLE] E#%s (%s) settled at SPX %.2f: P&L $%.2f (debit $%.2f, transform $%.2f)",
            entry.entry_number, tag, spx, realized, entry.net_debit, entry.transform_credit,
        )
        if getattr(self, "_dc_recorder", None):
            entry_date = entry.entry_time.strftime("%Y-%m-%d") if entry.entry_time else ""
            term = "transformed_settled" if tag == "TRANSFORMED" else "calendar_leftover"
            self._dc_recorder.record_outcome(
                entry, term, realized, spx, entry_date,
                get_us_market_time().strftime("%Y-%m-%d"),
            )
