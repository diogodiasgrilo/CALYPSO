"""GhauriMeanReversionStrategy — Variant F, "0DTE Mean Reversion Credit Spreads".

Source: Jamaal Ghauri (Theta Profits interview, John Einar Sandvand,
2026-08-16; docs/STRATEGY_CANDIDATES.md, "0DTE Mean Reversion Credit
Spreads"). Fades price touching the day's VIX-implied expected-move (EM)
boundary with a ONE-SIDED (never both) 0DTE SPX put or call credit vertical:
mark the upper/lower EM boundary from the opening print; on a fresh touch of
either boundary (no confirmation wait), enter the corresponding fade — a call
credit spread betting on reversal down at the upper boundary, a put credit
spread betting on a bounce at the lower boundary. Short strike ~10-25delta,
width 5-20pt (config). Profit target 50% of credit; stop at 100% of credit
(net loss = 1x credit if untrailed); once profit reaches ~25% of credit, the
stop locks in at (or near) breakeven-or-better. Average hold 20-30 minutes,
always closed same-session — never held overnight, so STATE-004 never
applies to this variant.

STATUS: dry-run-LOCKED (mirrors DoubleCalendarStrategy / StrangleStrategy — a
non-dry_run construction raises ConfigError before any broker I/O). Going
live is a deliberate, later, operator decision after a real dry-run
observation period, not part of this build.

DESIGN — fully self-contained, NOT a partial override of HydraStrategy's
scheduled-entry machinery. An adversarial audit of the first design draft
found that HydraStrategy._initiate_entry is not a thin dispatcher over
entry.put_only/entry.call_only — it independently re-derives entry direction
via HYDRA's own MKT-011/035/038 credit-gate decision tree (entry.put_only/
call_only are WRITTEN inside that method, never READ for dispatch), and that
reusing entry_times/_next_entry_index bookkeeping (even with placeholder
values) breaks real control flow in _skip_missed_entries/_is_entry_time, not
just display. So this class overrides SEVEN methods in full, never calling
super() on any of them:

    _parse_entry_times, _should_attempt_entry, _skip_missed_entries,
    _is_entry_time, _calculate_strikes, _initiate_entry, _check_stop_losses

Every override lives entirely in this file — NOTHING in base_strategy.py or
strategy.py is edited by this build. Zero effect on A/B/C, zero restart of
the live seat (B) required to deploy this variant.

Reused AS-IS from HydraStrategy (verified compatible, no override needed):
  * _execute_put_spread_only / _execute_call_spread_only / their _simulate_*
    dry-run siblings — placement. Both consume entry.{long,short}_{put,call}
    _strike / entry.put_only / entry.call_only / entry.strategy_id and are
    agnostic to how the caller decided direction.
  * _check_stop_with_confirmation — MKT-046 10s anti-spike confirmation,
    internally calls _execute_stop_loss once confirmed. IMPORTANT: it reads
    its stop level via _get_effective_stop_level(entry, side), which reads
    entry.{side}_side_stop directly — NOT the stop_level parameter passed
    in (that parameter is effectively unused past the method's first line).
    So this class ALWAYS writes its freshly-computed stop onto
    entry.{side}_side_stop before calling _check_stop_with_confirmation.
  * _execute_stop_loss — generic debit-vs-credit P&L booking (symmetric; a
    close cheaper than the credit received books a profit correctly).
  * _close_entry_early — take-profit close path (mirrors Brandon's own
    _brandon_check_take_profit usage exactly). Generically per-side: a side
    that was never placed has no uic/position_id set, so it's never included
    in what gets closed — safe for a one-sided entry with no special-casing.
  * _batch_update_entry_prices — fully generic over the 4 canonical leg
    names + getattr/setattr; no Brandon/HYDRA-specific coupling.
  * _get_todays_expiry, _check_market_halt, _has_orphaned_orders,
    _check_buying_power, _register_position, _record_skipped_entry,
    _book_realized_pnl.
  * _handle_monitoring is NOT overridden here (HydraStrategy's own override,
    which layers MKT-033/018/047 on top of the base _check_stop_losses call,
    is inherited unchanged) — this is why MKT-047's EOD safety-flatten
    protects Ghauri positions automatically: it's gated purely on
    self.eod_flatten_enabled / self._eod_flatten_done / active_entries, none
    of which depend on the entry_times/_next_entry_index bookkeeping this
    class deliberately never touches.
  * _reset_for_new_day (STATE-004 + the daily-state reconstruction this
    class's own per-day flags ride on for free, reset alongside it below).

Stop formula, corrected from the audited first draft (which had a live math
bug: stop_level = pct_of_credit * credit computes to BREAKEVEN at
pct_of_credit=1.0, not a 1x-credit loss, since stop_level is an absolute
cost-to-close trigger price, not a loss fraction):
    stop_level = credit * (1 + pct_of_credit)   # loss at trigger = pct_of_credit x credit
Floored at GHAURI_MIN_STOP_LEVEL_PER_CONTRACT x contracts (mirrors
MIN_STOP_LEVEL/MIN_VALID_STOP used elsewhere) — a thin single-contract
10-25delta vertical can easily land under that floor, and the stop-check
loop SKIPS evaluation entirely (not "substitutes the floor") when the stored
stop is below it. No MKT-042 buffer-decay dependency: config_variant_f.json
sets buffer_decay_start_mult/buffer_decay_hours to null so
_get_effective_stop_level's decay branch never activates and simply returns
whatever this class stored — avoiding the "decay re-widens a just-tightened
trail stop" bug class (documented, already fixed once for A2/narrow_spread_stop).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from math import sqrt
from typing import Any, Dict, Optional

from bots.hydra.base_strategy import ConfigError, MEICState
from bots.hydra.brandon import take_profit
from bots.hydra.strategy import HydraStrategy, HydraIronCondorEntry
from shared.alert_service import AlertType, AlertPriority
from shared.delta_strike_selector import select_strike_by_delta
from shared.market_hours import get_us_market_time

logger = logging.getLogger(__name__)

# Mirrors MIN_STOP_LEVEL / MIN_VALID_STOP ($50/contract) used throughout
# strategy.py — a stop below this is not evaluated at all by the shared
# stop-check loop this class hands off to via _check_stop_with_confirmation.
GHAURI_MIN_STOP_LEVEL_PER_CONTRACT = 50.0


@dataclass
class GhauriEntry(HydraIronCondorEntry):
    """HydraIronCondorEntry + trail-to-breakeven state (Variant F only).

    Deliberately NOT round-tripped through the shared save/restore functions
    in strategy.py (both are hand-written, explicit per-field whitelists —
    adding fields there would mean editing shared, live-critical code that
    B/C's real open positions also flow through). CORRECTION (found by
    adversarial post-implementation review, not anticipated by the original
    design): the restart-recovery path does not reconstruct a "GhauriEntry
    with reset fields" — it reconstructs every entry as the plain
    `HydraIronCondorEntry` base class (strategy.py's state-restore code is
    shared, unedited, and has no knowledge of this subclass), so a restart-
    restored Ghauri position isn't a GhauriEntry instance at all. The first
    version of `_check_stop_losses` gated its loop on
    `isinstance(entry, GhauriEntry)`, which meant a restart-restored entry
    was silently excluded from stop-loss/take-profit monitoring FOREVER, not
    just missing its trail state — a CRITICAL bug, fixed by reading/writing
    `peak_profit_pct`/`trail_armed` via `getattr`/`setattr` with defaults
    instead of gating on the subclass. With that fix, a restart genuinely
    does degrade to "trail never armed, falls back to the base stop level"
    (recomputed fresh from credit every tick, still correct and protective)
    — mirroring the precedented `call_breach_time`/`put_breach_time`
    ("conservative reset on restart") pattern elsewhere in this codebase —
    rather than to no monitoring at all. Low-stakes given Ghauri positions
    are same-session, 20-30 minute holds: losing an armed trail is a minor,
    safe degradation now that it's actually what happens.
    """
    peak_profit_pct: float = 0.0
    trail_armed: bool = False


class GhauriMeanReversionStrategy(HydraStrategy):
    """Variant F — see module docstring for the full design rationale."""

    BOT_NAME = "HYDRA"
    # 2026-08-28 audit (cosmetic): suppresses HydraStrategy's "E1-EN: full IC"
    # heartbeat line — meaningless here, entries are EM-boundary-touch
    # triggered, never clock-scheduled. See HydraStrategy's own docstring on
    # this flag for the full rationale.
    _show_ic_schedule_in_heartbeat = False

    def __init__(
        self,
        broker,
        config: Dict[str, Any],
        logger_service: Any,
        dry_run: bool = False,
        alert_service: Optional[Any] = None,
    ):
        """Construct, enforcing the dry-run-only lock (mirrors DoubleCalendarStrategy /
        StrangleStrategy). The entry/exit logic IS fully implemented and runs
        dry-run-only; going live is a deliberate, later operator decision — see the
        module docstring's Non-goals framing in the approved build plan.
        """
        if not dry_run:
            raise ConfigError(
                "GhauriMeanReversionStrategy (Variant F) is dry-run-LOCKED. The "
                "entry/exit logic IS fully implemented, but going live is a "
                "deliberate, later operator decision after a real dry-run "
                "observation period — not a config flip. Set dry_run=true, or do "
                "not select strategy.name='ghauri'."
            )

        strategy_cfg = config.get("strategy", {})
        ghauri_cfg = strategy_cfg.get("ghauri", {})

        # Strike selection
        self.ghauri_target_delta_pct = float(ghauri_cfg.get("target_delta_pct", 0.15))
        band = ghauri_cfg.get("delta_band", [0.05, 0.35])
        self.ghauri_delta_band = (float(band[0]), float(band[1]))
        self.ghauri_delta_max_reads = int(ghauri_cfg.get("delta_max_reads", 6))
        self.ghauri_width_pt = float(ghauri_cfg.get("width_pt", 10.0))
        self.ghauri_strike_search_pts = float(ghauri_cfg.get("strike_search_pts", 150.0))

        # Exit
        self.ghauri_profit_target_pct = float(ghauri_cfg.get("profit_target_pct", 0.50))
        self.ghauri_pct_of_credit = float(ghauri_cfg.get("pct_of_credit", 1.00))
        self.ghauri_trail_arm_pct = float(ghauri_cfg.get("trail_arm_pct", 0.25))
        self.ghauri_trail_lock_pct = float(ghauri_cfg.get("trail_lock_pct", 0.0))

        # Entry window
        cutoff_str = ghauri_cfg.get("entry_cutoff_time", "13:00")
        cutoff_h, cutoff_m = (int(p) for p in cutoff_str.split(":"))
        self.ghauri_entry_cutoff_time = dt_time(cutoff_h, cutoff_m)
        self.ghauri_preferred_window_min = int(ghauri_cfg.get("preferred_window_min", 60))

        # Per-day touch-trigger state (mirrors the reset-at-_reset_for_new_day
        # pattern other self._xxx flags on HydraStrategy use — see
        # _reset_for_new_day override below).
        self._ghauri_upper_boundary: Optional[float] = None
        self._ghauri_lower_boundary: Optional[float] = None
        self._ghauri_upper_fired_today = False
        self._ghauri_lower_fired_today = False
        self._ghauri_pending_fire_side: Optional[str] = None
        self._ghauri_next_entry_number = 1

        super().__init__(
            broker, config, logger_service,
            dry_run=dry_run, alert_service=alert_service,
        )

        # Defense-in-depth (mirrors DoubleCalendarStrategy's re-check after
        # super(), in case dry_run is derived differently downstream).
        if not getattr(self, "dry_run", False):
            raise ConfigError(
                "GhauriMeanReversionStrategy resolved to dry_run=false after "
                "init — refusing to continue (dry-run-LOCKED, see __init__)."
            )

    # =========================================================================
    # Entry triggering — fully self-contained, decoupled from entry_times
    # =========================================================================

    def _parse_entry_times(self):
        """One real, meaningful value — market open — purely so the one
        unconditional base-class startup log (MEICStrategy.__init__ logs
        entry_times at construction) shows something sensible. Otherwise
        inert: _should_attempt_entry/_skip_missed_entries/_is_entry_time are
        all overridden below to never read entry_times/_next_entry_index for
        control flow, so its length/value has no other effect.

        Also replicates the base implementation's MKT-034 vix-gate attribute
        initialization (found missing by a live construction smoke test,
        2026-08-27 — every existing test in this file bypasses the real
        HydraStrategy.__init__ via __new__ or a monkeypatched stub, so this
        gap was never exercised). HydraStrategy.__init__ itself — not just
        the methods this class overrides — reads self.vix_gate_enabled
        unconditionally right after calling _parse_entry_times(), and several
        other inherited-unchanged methods (logging/formatting branches) read
        it too. Since this override never calls super(), it must set these
        side-effect attributes itself or every one of those reads raises
        AttributeError. Ghauri's own entry logic has no VIX cutoff, so the
        gate is always off; still read from config like the base does rather
        than hardcoding, since config_variant_f.json already carries an
        explicit vix_time_shift.enabled=false.
        """
        vts = self.config.get("vix_time_shift", {})
        self.vix_gate_enabled = vts.get("enabled", False)
        self.vix_medium_threshold = vts.get("medium_vix_threshold", 20.0)
        self.vix_high_threshold = vts.get("high_vix_threshold", 23.0)
        self._vix_gate_resolved = False
        self._vix_gate_start_slot = 0

        self.entry_times = [dt_time(9, 30)]
        self._base_entry_count = 1

    def _should_attempt_entry(self, now: datetime) -> bool:
        """Touch-trigger: compute today's EM boundaries once, then fire True
        exactly once per boundary per day, on a fresh touch, before the entry
        cutoff. Never delegates to the base class's clock-schedule logic.
        """
        # Cutoff check runs FIRST, independent of price/boundary data
        # availability — found by adversarial post-implementation review:
        # this used to sit after the current_price/spx_open/vix_open guards,
        # which meant a session-long market-data outage (current_price never
        # populating, or spx_open/vix_open never populating) made this method
        # return False before ever reaching the cutoff check, permanently
        # stalling the DAILY_COMPLETE transition (and therefore
        # _handle_daily_complete's once-per-day _send_daily_summary) for the
        # rest of that day. The base MEIC design's cutoff is wall-clock-only
        # for exactly this robustness reason; this override now matches that.
        cutoff_dt = now.replace(
            hour=self.ghauri_entry_cutoff_time.hour,
            minute=self.ghauri_entry_cutoff_time.minute,
            second=0, microsecond=0,
        )
        if now >= cutoff_dt:
            if self.state != MEICState.DAILY_COMPLETE and not self.daily_state.active_entries:
                self.state = MEICState.DAILY_COMPLETE
                logger.info(
                    "GHAURI: entry cutoff passed with no active entries — "
                    "transitioning to DAILY_COMPLETE"
                )
            return False

        if self.current_price <= 0:
            return False

        if self._ghauri_upper_boundary is None or self._ghauri_lower_boundary is None:
            spx_open = self.market_data.spx_open
            vix_open = self.market_data.vix_open
            if not spx_open or spx_open <= 0 or not vix_open or vix_open <= 0:
                return False  # session open data not available yet this tick
            expected_move = spx_open * (vix_open / 100) / sqrt(252)
            self._ghauri_upper_boundary = spx_open + expected_move
            self._ghauri_lower_boundary = spx_open - expected_move
            logger.info(
                f"GHAURI: EM boundaries set for today — SPX open {spx_open:.2f}, "
                f"VIX open {vix_open:.2f}, EM ±{expected_move:.2f} -> "
                f"upper {self._ghauri_upper_boundary:.2f} / "
                f"lower {self._ghauri_lower_boundary:.2f}"
            )

        spx = self.current_price
        fire_side: Optional[str] = None
        if not self._ghauri_upper_fired_today and spx >= self._ghauri_upper_boundary:
            fire_side = "call"
        elif not self._ghauri_lower_fired_today and spx <= self._ghauri_lower_boundary:
            fire_side = "put"

        if fire_side is None:
            return False

        # Mark fired NOW, before any placement attempt, so a failed/skipped
        # placement can never retry-loop on the same touch — "one touch, one
        # attempt" by design, and the direct fix for the audited finding that
        # a whipsaw touching the same boundary again mid-attempt must not
        # re-trigger a second entry on it.
        if fire_side == "call":
            self._ghauri_upper_fired_today = True
        else:
            self._ghauri_lower_fired_today = True
        self._ghauri_pending_fire_side = fire_side

        elapsed_min = None
        spx_open = self.market_data.spx_open
        if spx_open and spx_open > 0:
            try:
                session_open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
                elapsed_min = (now - session_open_dt).total_seconds() / 60
            except Exception:
                elapsed_min = None
        preferred = (
            elapsed_min is not None and elapsed_min <= self.ghauri_preferred_window_min
        )
        logger.info(
            f"GHAURI: SPX {spx:.2f} touched the "
            f"{'upper' if fire_side == 'call' else 'lower'} EM boundary "
            f"(upper={self._ghauri_upper_boundary:.2f} / "
            f"lower={self._ghauri_lower_boundary:.2f}) -> firing {fire_side} fade"
            + ("" if preferred else " (outside the preferred first-hour window)")
        )
        return True

    def _skip_missed_entries(self, now: datetime) -> None:
        """No-op: there are no fixed clock slots to skip past. The base
        version's dummy-clock window math (proven, by adversarial audit, to
        silently consume real slots and prematurely end the day) is never
        appropriate for an event-triggered strategy — genuinely nothing to do
        here rather than a value to approximate.
        """
        return

    def _is_entry_time(self) -> bool:
        """Never called by this class's own _initiate_entry (it bypasses the
        base retry/calm-wait loop entirely), but overridden defensively in
        case anything inherited calls it: true only while a touch has fired
        and this class is actively attempting to place it.
        """
        return self._ghauri_pending_fire_side is not None

    # =========================================================================
    # Strike selection
    # =========================================================================

    def _calculate_strikes(self, entry: "GhauriEntry") -> bool:
        """Delta-target single-side strike selection. Reads entry.put_only/
        entry.call_only (already set by _initiate_entry from the fired
        boundary) to know which side to compute.
        """
        spx = self.current_price
        if spx <= 0:
            logger.error("GHAURI: cannot calculate strikes - no SPX price")
            return False

        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error("GHAURI: cannot calculate strikes - no expiry available")
            return False

        right = "Call" if entry.call_only else "Put"

        try:
            chain_strikes = self.broker.get_option_chain(
                self.underlying_symbol, expiry,
                trading_class=self.trading_class, exchange=self.exchange,
            )
        except Exception as exc:
            logger.error(f"GHAURI: option chain fetch failed: {exc}")
            return False
        if not chain_strikes:
            logger.error("GHAURI: option chain returned no strikes")
            return False

        lo_bound = spx - self.ghauri_strike_search_pts
        hi_bound = spx + self.ghauri_strike_search_pts
        candidates = sorted(k for k in chain_strikes if lo_bound <= k <= hi_bound)
        if not candidates:
            candidates = sorted(chain_strikes, key=lambda k: abs(k - spx))[:40]

        try:
            conid_map = self.broker.qualify_option_strikes(
                symbol=self.underlying_symbol, expiry=expiry, strikes=candidates,
                trading_class=self.trading_class, exchange=self.exchange,
            )
        except Exception as exc:
            logger.error(f"GHAURI: strike qualification failed: {exc}")
            return False

        right_code = "C" if right == "Call" else "P"
        strike_map = {
            strike: conid
            for (strike, r), conid in conid_map.items()
            if r == right_code
        }
        if not strike_map:
            logger.error(f"GHAURI: no {right} conids resolved near SPX {spx:.0f}")
            return False

        short_strike = select_strike_by_delta(
            self.broker, strike_map, self.ghauri_target_delta_pct, right,
            base=spx, band=self.ghauri_delta_band, max_reads=self.ghauri_delta_max_reads,
        )
        if short_strike is None:
            logger.warning(
                f"GHAURI: no {right} strike found in delta band "
                f"{self.ghauri_delta_band} near target {self.ghauri_target_delta_pct:.2f}"
            )
            return False

        width = self.ghauri_width_pt
        if right == "Call":
            entry.short_call_strike = short_strike
            entry.long_call_strike = short_strike + width
        else:
            entry.short_put_strike = short_strike
            entry.long_put_strike = short_strike - width

        logger.info(
            f"GHAURI: {right} strikes selected — short={short_strike:.0f}, "
            f"width={width:.0f}pt (target delta {self.ghauri_target_delta_pct:.2f}, "
            f"SPX {spx:.2f})"
        )
        return True

    # =========================================================================
    # Entry initiation — fully self-contained, does NOT call super()
    # =========================================================================

    def _initiate_entry(self) -> str:
        """Direction is already decided by _should_attempt_entry (stored in
        _ghauri_pending_fire_side). This deliberately bypasses HydraStrategy's
        936-line _initiate_entry entirely — that method's eventual one-sided-
        vs-full-IC placement decision is made by ITS OWN internal MKT-011/
        035/038 logic (place_call_only/place_put_only local variables), which
        never reads entry.put_only/call_only and has zero awareness of a
        touch-trigger decision a subclass might have already made. Reusing it
        even partially would let HYDRA's own credit-gate/FOMC-callonly logic
        silently override or contradict this strategy's touch-direction
        choice — confirmed as a live risk during the design's adversarial
        audit (MKT-038 FOMC-T+1-callonly code-defaults to True and is not
        gated on entry.put_only/call_only at all).
        """
        fire_side = self._ghauri_pending_fire_side
        if fire_side is None:
            # Should not happen (only called when _should_attempt_entry just
            # returned True), but never place a direction-less entry.
            logger.error("GHAURI: _initiate_entry called with no pending fire side")
            return "Entry skipped - no pending fire side"

        entry_num = self._ghauri_next_entry_number

        is_halted, halt_reason = self._check_market_halt()
        if is_halted:
            logger.warning(f"GHAURI MKT-005: market halt detected - {halt_reason}")
            return f"GHAURI entry #{entry_num} delayed - {halt_reason}"

        if self._has_orphaned_orders():
            logger.error(f"GHAURI: entry #{entry_num} blocked by orphaned orders")
            self._ghauri_next_entry_number += 1
            return f"GHAURI entry #{entry_num} skipped - orphaned orders blocking"

        has_bp, bp_message = self._check_buying_power()
        if not has_bp:
            logger.warning(f"GHAURI ORDER-004: {bp_message}")
            self.daily_state.entries_skipped += 1
            self._ghauri_next_entry_number += 1
            self._record_skipped_entry(entry_num, f"Insufficient margin: {bp_message}", send_alert=False)
            return f"GHAURI entry #{entry_num} skipped - {bp_message}"

        self._entry_in_progress = True
        self.state = MEICState.ENTRY_IN_PROGRESS

        entry = GhauriEntry(entry_number=entry_num)
        entry.entry_time = get_us_market_time()
        entry.strategy_id = f"ghauri_{get_us_market_time().strftime('%Y%m%d')}_entry{entry_num}"
        entry.contracts = self.contracts_per_entry
        entry.call_only = fire_side == "call"
        entry.put_only = fire_side == "put"
        entry.override_reason = "EM_UPPER_TOUCH" if fire_side == "call" else "EM_LOWER_TOUCH"
        self._current_entry = entry

        try:
            if not self._calculate_strikes(entry):
                self.daily_state.entries_skipped += 1
                self._ghauri_next_entry_number += 1
                self._entry_in_progress = False
                self._current_entry = None
                self.state = MEICState.MONITORING if self.daily_state.active_entries else MEICState.WAITING_FIRST_ENTRY
                self._record_skipped_entry(
                    entry_num,
                    f"Could not find a viable {fire_side} strike at the "
                    f"{fire_side.upper()} EM-touch trigger — skipped.",
                    send_alert=True,
                )
                return f"GHAURI entry #{entry_num} skipped - strike selection failed"

            if entry.call_only:
                success = self._simulate_call_spread_only(entry) if self.dry_run else self._execute_call_spread_only(entry)
            else:
                success = self._simulate_put_spread_only(entry) if self.dry_run else self._execute_put_spread_only(entry)

            self._ghauri_next_entry_number += 1
            self._entry_in_progress = False

            if not success:
                self._current_entry = None
                self.state = MEICState.MONITORING if self.daily_state.active_entries else MEICState.WAITING_FIRST_ENTRY
                self._record_skipped_entry(
                    entry_num,
                    f"{fire_side.title()} spread order placement failed at the "
                    f"EM-touch trigger — see logs.",
                    send_alert=True,
                )
                return f"GHAURI entry #{entry_num} skipped - order placement failed"

            self.daily_state.entries.append(entry)
            self.daily_state.one_sided_entries += 1
            self._current_entry = None
            self.state = MEICState.MONITORING

            credit = entry.call_spread_credit if entry.call_only else entry.put_spread_credit
            strike = entry.short_call_strike if entry.call_only else entry.short_put_strike
            self.alert_service.send_alert(
                alert_type=AlertType.POSITION_OPENED,
                title=f"GHAURI Entry #{entry_num} — {fire_side.title()} Credit Spread",
                message=(
                    f"SPX touched the {'upper' if fire_side == 'call' else 'lower'} "
                    f"expected-move boundary — entered {fire_side} credit spread "
                    f"at short strike {strike:.0f} for ${credit:.2f} credit."
                ),
                priority=AlertPriority.MEDIUM,
                details={"entry_number": entry_num, "side": fire_side, "credit": credit},
                contracts=self.contracts_per_entry,
            )
            logger.info(
                f"GHAURI: entry #{entry_num} complete — {fire_side} spread, "
                f"short strike {strike:.0f}, credit ${credit:.2f}"
            )
            return f"GHAURI entry #{entry_num} placed - {fire_side} spread, credit ${credit:.2f}"
        finally:
            self._ghauri_pending_fire_side = None

    # =========================================================================
    # Stop-loss, take-profit, trail-to-breakeven — one self-contained override
    # =========================================================================

    def _min_stop_level(self) -> float:
        return GHAURI_MIN_STOP_LEVEL_PER_CONTRACT * max(int(self.contracts_per_entry), 1)

    def _check_stop_losses(self) -> Optional[str]:
        """Fully self-contained: no shared _calculate_stop_levels_hydra call,
        no MKT-042 decay dependency. Reuses _batch_update_entry_prices (fully
        generic), take_profit.evaluate (pure), _close_entry_early (generic
        per-side close), and _check_stop_with_confirmation (MKT-046 anti-spike
        confirmation + _execute_stop_loss dispatch) — see module docstring for
        why each of these is safe to reuse as-is.

        Note this method is called from HydraStrategy._handle_monitoring (via
        the inherited super()._handle_monitoring() -> self._check_stop_losses()
        chain), which ALSO layers MKT-047 EOD-flatten on top after this
        returns — that machinery is untouched here and applies to Ghauri
        entries automatically (see module docstring).
        """
        self._batch_update_entry_prices()

        for entry in list(self.daily_state.active_entries):
            # NOTE: deliberately no `isinstance(entry, GhauriEntry)` guard here
            # (removed after adversarial post-implementation review found it
            # created a CRITICAL bug). bots/hydra/strategy.py's restart-
            # recovery path (_recover_positions_from_saxo -> state restore,
            # shared/unedited code every HYDRA-lineage strategy goes through
            # on every process start) reconstructs EVERY entry as the base
            # `HydraIronCondorEntry` class, never `GhauriEntry` — so any open
            # Ghauri position that survives a routine systemd restart
            # (Restart=always/RestartSec=30, or a deploy) would silently and
            # permanently stop being stop-loss/take-profit-checked if this
            # loop required the subclass. Instead: read/write the 2 Ghauri-
            # only fields via getattr/setattr, which degrades a restart-
            # restored entry to "trail never armed" (falls back to the still-
            # correct, still-protective base stop below) instead of skipping
            # it from monitoring entirely.
            side = "call" if entry.call_only else "put"
            if getattr(entry, f"{side}_side_stopped", False) or getattr(entry, f"{side}_side_expired", False):
                continue

            credit = entry.call_spread_credit if side == "call" else entry.put_spread_credit
            spread_value = entry.call_spread_value if side == "call" else entry.put_spread_value
            if credit <= 0:
                continue  # not yet priced (placement just happened this tick)

            # --- Take-profit at ghauri_profit_target_pct of credit captured ---
            tp = take_profit.evaluate(
                credit_received=credit, current_value=spread_value,
                threshold=self.ghauri_profit_target_pct,
            )
            if tp.should_close:
                result = self._ghauri_close_for_take_profit(entry, side, tp)
                if result:
                    return result

            # --- Trail-to-breakeven: track peak capture, arm once, never un-arm ---
            peak_profit_pct = getattr(entry, "peak_profit_pct", 0.0)
            trail_armed = getattr(entry, "trail_armed", False)
            captured_pct = (credit - spread_value) / credit
            if captured_pct > peak_profit_pct:
                peak_profit_pct = captured_pct
                setattr(entry, "peak_profit_pct", peak_profit_pct)
            if not trail_armed and peak_profit_pct >= self.ghauri_trail_arm_pct:
                trail_armed = True
                setattr(entry, "trail_armed", True)
                logger.info(
                    f"GHAURI TRAIL-ARM E#{entry.entry_number} {side}: peak capture "
                    f"{peak_profit_pct:.1%} >= arm threshold "
                    f"{self.ghauri_trail_arm_pct:.1%} — stop now locks in "
                    f"{self.ghauri_trail_lock_pct:.1%} of credit as minimum profit"
                )

            # --- Base stop: credit * (1 + pct_of_credit), floored ---
            floor = self._min_stop_level()
            base_stop = max(credit * (1.0 + self.ghauri_pct_of_credit), floor)

            # --- Trail ONLY ever tightens relative to the base stop, never
            #     loosens it: trail_stop = credit * (1 - trail_lock_pct) is
            #     always <= credit <= base_stop by construction, so once armed
            #     the min() below always resolves to the (tighter) trail_stop.
            #     Both branches feeding effective_stop are already
            #     max(..., floor)-clamped, so effective_stop can never fall
            #     below the floor by construction — no separate check needed. ---
            effective_stop = base_stop
            if trail_armed:
                trail_stop = max(credit * (1.0 - self.ghauri_trail_lock_pct), floor)
                effective_stop = min(effective_stop, trail_stop)

            setattr(entry, f"{side}_side_stop", effective_stop)

            result = self._check_stop_with_confirmation(entry, side, spread_value, effective_stop)
            if result:
                return result

        return None

    def _ghauri_close_for_take_profit(self, entry: "GhauriEntry", side: str, tp_decision) -> Optional[str]:
        """Mirrors BrandonHydraStrategy._brandon_check_take_profit's exact
        close pattern: _close_entry_early (generic per-side, live-mode P&L
        booking happens inside it) + a dry-run-only manual _book_realized_pnl
        (matching Brandon's own `if self.dry_run:` branch, since the live
        booking path inside _close_entry_early doesn't fire in dry-run).
        """
        logger.info(f"GHAURI-TP E#{entry.entry_number} {side}: {tp_decision.reason}")
        try:
            legs_closed, legs_failed, _ = self._close_entry_early(entry)
        except Exception as exc:
            logger.error(f"GHAURI-TP E#{entry.entry_number}: close failed ({exc})")
            return None

        side_closed = getattr(entry, f"{side}_side_expired", False)
        if not side_closed:
            logger.critical(
                f"GHAURI-TP E#{entry.entry_number}: close returned {legs_closed} "
                f"legs closed / {legs_failed} failed, but {side} legs are STILL "
                f"OPEN — not marking closed, will retry next tick"
            )
            return None

        setattr(entry, f"{side}_side_stopped", True)
        close_cost = float(entry.call_spread_value if side == "call" else entry.put_spread_value)
        actual_debit_attr = f"actual_{side}_stop_debit"
        if not getattr(entry, actual_debit_attr, 0):
            setattr(entry, actual_debit_attr, close_cost)
        if self.dry_run:
            credit = entry.call_spread_credit if side == "call" else entry.put_spread_credit
            self._book_realized_pnl(credit - close_cost, entry)
        entry.close_reason = "TP"

        self.alert_service.send_alert(
            alert_type=AlertType.PROFIT_TARGET,
            title=f"GHAURI Entry #{entry.entry_number} — Take Profit ({side.title()})",
            message=(
                f"{tp_decision.profit_captured_pct:.1%} of credit captured — "
                f"closed {legs_closed} legs ({legs_failed} failed)."
            ),
            priority=AlertPriority.MEDIUM,
            details={"entry_number": entry.entry_number, "side": side, "captured_pct": tp_decision.profit_captured_pct},
            contracts=self.contracts_per_entry,
        )
        return (
            f"GHAURI-TP E#{entry.entry_number} {side}: closed {legs_closed} legs "
            f"({legs_failed} failed) — {tp_decision.profit_captured_pct:.1%} captured"
        )

    # =========================================================================
    # Daily reset — per-day touch-trigger state
    # =========================================================================

    def _reset_for_new_day(self):
        """Reset the per-day EM-boundary/touch-fired state alongside the
        inherited STATE-004 + daily-state reset. Called via super() so the
        overnight-position broker check + MEICDailyState reconstruction still
        run exactly as they do for every other variant (Ghauri never holds
        overnight by design, so STATE-004 should never actually find anything
        — but the check itself is a universal safety net, not something this
        variant should skip).
        """
        super()._reset_for_new_day()
        self._ghauri_upper_boundary = None
        self._ghauri_lower_boundary = None
        self._ghauri_upper_fired_today = False
        self._ghauri_lower_fired_today = False
        self._ghauri_pending_fire_side = None
        self._ghauri_next_entry_number = 1
