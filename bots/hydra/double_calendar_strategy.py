"""DoubleCalendarStrategy — "DC Time Machine" (Strategy D) SCAFFOLD, dry-run-LOCKED.

Source strategy: Steve Burnich's "DC Time Machine" (YouTube JtGW1wNFNIY). Open a
SPX **double calendar** for a net DEBIT (sell a shorter-dated option, buy a
longer-dated option at the same strike, on both a call and a put side ~30-40δ),
then after a small intraday profit fire a "transformer" that closes the long
back-dated legs and buys protective wings against the remaining shorts — turning
the position into an iron condor whose collected credit exceeds the original
debit by at least the wing width (structurally risk-free). The condor is then
held to expiry. Full spec: /tmp/strategy_D_dc_time_machine.md.

WHY THIS IS A SCAFFOLD, NOT A FINISHED STRATEGY
HYDRA is architecturally pinned to "a position is born and dies in one 0DTE
session." Strategy D is the opposite: TWO simultaneous expirations, a net-DEBIT
entry, positions held 6-15 CALENDAR DAYS, and a mid-life restructuring order.
Implementing the entry / transformer / debit-P&L / two-stage-settlement logic is
a multi-week build (see the prior feasibility analysis). This file delivers the
SAFE SHELL so D can be registered and run dry-run NOW without endangering the
live variants — the strategy-defining hooks are explicit stubs.

WHAT IS REAL HERE (safety, done):
  * Dry-run LOCK in __init__ (mirrors StrangleStrategy): a non-dry-run
    construction raises ConfigError BEFORE any broker I/O. D cannot place real
    paper orders until a deliberate operator flip AND the coexistence MUST-FIXes
    ship (see below).
  * BOT_NAME="DCTM" and requires_protective_wings=False so the shared safety path
    never treats D's debit/calendar legs as a naked-short emergency.
  * Multi-day lifecycle overrides: _reset_for_new_day no longer wipes D's open
    multi-day positions at the daily reset, and check_after_hours_settlement
    treats a legitimately-held position as NORMAL (not "settlement pending
    forever", which would jam the daily-summary gate).
  * The three abstract hooks are OVERRIDDEN to inert stubs so D never silently
    runs HYDRA's iron-condor entry logic and masquerades as an IC.

WHAT IS STUBBED (the multi-week build — clearly marked TODO):
  * _calculate_strikes / _initiate_entry / _check_stop_losses — the actual
    double-calendar entry, the transformer, the 20%-debit pre-transform stop,
    the EOD-day-1 close, and the held-to-expiry condor settlement.
  * An expiry-aware leg model + a debit-rooted P&L path (HydraIronCondorEntry is
    a single-expiry credit IC and must NOT be reused for the calendar phase).

COEXISTENCE WITH THE LIVE VARIANTS (A/B/C) — READ BEFORE FLIPPING dry_run=false
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

import logging
from datetime import date, time
from typing import Dict, Optional, Tuple

from bots.hydra.base_strategy import ConfigError, MEICState
# DCPhase + the two-expiry CalendarEntry model live in calendar_entry (Phase 1
# foundation). Re-exported here so callers/tests can import them from the
# strategy module.
from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: F401
from bots.hydra.calendar_chain import generate_candidate_expiries, pick_calendar_expiries
from bots.hydra.strategy import HydraStrategy
from shared.market_hours import get_us_market_time

logger = logging.getLogger(__name__)

# Canonical-leg-name -> short tag for synthetic dry-run position ids.
_DC_LEG_ABBR = {"short_call": "SC", "long_call": "LC", "short_put": "SP", "long_put": "LP"}


class DoubleCalendarStrategy(HydraStrategy):
    """Strategy D — double calendar -> risk-free iron condor (DC Time Machine).

    SCAFFOLD: the safety shell and multi-day lifecycle overrides are real; the
    strategy-defining hooks are inert stubs pending the full build. Inherits
    HYDRA's scheduling / monitoring / state / IBKR / Telegram / DB machinery so
    only the strategy-specific pieces remain to be written.
    """

    BOT_NAME = "DCTM"
    # Debit calendar + transformer manage their own structure; the shorts are not
    # an "unhedged naked short" emergency the base path should auto-close (the
    # long back-dated leg is the protection in the calendar phase, the wing in the
    # condor phase). Same flag StrangleStrategy uses for the same reason.
    requires_protective_wings = False

    def __init__(self, *args, **kwargs):
        """Construct, enforcing the dry-run-only lock (mirrors StrangleStrategy).

        The entry/transformer/accounting logic is NOT implemented and the
        coexistence MUST-FIXes are NOT in place, so a live (real-order)
        construction is refused BEFORE super().__init__ runs any broker I/O.
        ``build_strategy`` always passes ``dry_run`` as a kwarg.
        """
        if not kwargs.get("dry_run", False):
            raise ConfigError(
                "DoubleCalendarStrategy (Strategy D) is dry-run-LOCKED. Its entry / "
                "transformer / debit-P&L / multi-day-settlement logic is a SCAFFOLD "
                "stub, and running it with real paper orders next to the live "
                "variants requires the coexistence MUST-FIXes (scope STATE-004 + "
                "orphan sweep to per-variant conids; budget buying power) — see this "
                "module's docstring. Set dry_run=true, or do not select "
                "strategy.name='double_calendar'."
            )
        super().__init__(*args, **kwargs)
        # Defense-in-depth: re-check after super in case dry_run is derived
        # differently downstream (it must equal the kwarg).
        if not getattr(self, "dry_run", False):
            raise ConfigError(
                "DoubleCalendarStrategy resolved to dry_run=false after init — "
                "refusing to arm an unimplemented, coexistence-unsafe strategy."
            )

        # DC-specific knobs (config.strategy.double_calendar.*). Read here so the
        # config surface is wired even though the entry logic that consumes them
        # is still stubbed.
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

        logger.info(
            "DCTM scaffold initialized (DRY-RUN ONLY). Short DTE %d-%d, long +%d-%d, "
            "target Δ%.2f %s, profit-trigger %.1f%%, pre-transform stop %.0f%%, wing %.0fpt. "
            "Entry/transformer/stop logic is STUBBED — D will idle (skip entries) until built.",
            self.dc_short_dte_min, self.dc_short_dte_max,
            self.dc_long_extra_dte_min, self.dc_long_extra_dte_max,
            self.dc_target_delta, list(self.dc_delta_band),
            self.dc_profit_trigger_pct * 100, self.dc_pre_transform_stop_pct * 100,
            self.dc_wing_width,
        )

    # ------------------------------------------------------------------
    # Multi-day lifecycle overrides (REAL — the part that matters today)
    # ------------------------------------------------------------------

    def _dc_entry_is_open(self, entry) -> bool:
        """True if ``entry`` is a still-live multi-day DC position (calendar or
        transformed-but-not-yet-settled). Used to carry positions across the
        daily reset and to keep settlement from treating a held position as a
        same-day-0DTE failure."""
        phase = getattr(entry, "dc_phase", None)
        return phase in (DCPhase.CALENDAR, DCPhase.TRANSFORMED)

    def _reset_for_new_day(self):
        """Daily reset that PRESERVES D's open multi-day positions.

        The base reset rebuilds ``daily_state`` from scratch, which would discard
        any double calendar that legitimately spans days. In DRY-RUN (the only
        mode this class permits) there are NO real broker positions, so the base
        STATE-004 overnight-position halt does not trigger — the only fix needed
        is to re-attach D's still-open multi-day entries after the base reset.

        REAL-ORDER PATH (NOT YET ENABLED): once D places real paper orders, the
        shared account holds D's legs overnight and the base STATE-004 guard WILL
        halt (it reads the whole account, unscoped). Do NOT flip dry_run=false
        until STATE-004 / the orphan sweep are scoped to per-variant ownership and
        buying power is budgeted (MUST-FIX #1/#2/#3 — see module docstring).
        """
        carried = [e for e in self.daily_state.entries if self._dc_entry_is_open(e)]
        super()._reset_for_new_day()  # dry-run: account flat -> no STATE-004 halt
        if carried:
            self.daily_state.entries.extend(carried)
            logger.info(
                "[DCTM-CARRY] carried %d open multi-day position(s) across the new-day "
                "reset (not wiped, not flagged as an overnight 0DTE fault).",
                len(carried),
            )
            # TODO(full build): carry the cumulative cost basis / realized-P&L of
            # the surviving entries across the reset too — the base rebuild zeroes
            # the per-day counters, which is correct for 0DTE but loses multi-day
            # debit accounting. Needs the debit-rooted P&L path.

    def check_after_hours_settlement(self) -> bool:
        """Treat a legitimately-held multi-day position as NORMAL after the close.

        The base method assumes any tracked position should settle the same
        session and, until it does, returns False (keeping the daily-summary gate
        open). A DC position held across days would jam that gate forever. Here:
        if D is holding open multi-day positions, report settled-for-today so the
        daily summary proceeds; otherwise defer to the base 0DTE-style logic.
        """
        open_md = [e for e in self.daily_state.entries if self._dc_entry_is_open(e)]
        if not open_md:
            return super().check_after_hours_settlement()
        logger.info(
            "[DCTM-HOLD] %d multi-day position(s) held past the close — normal for a "
            "calendar, not pending settlement; daily summary may proceed.",
            len(open_md),
        )
        # TODO(full build): per-expiry settlement — when a position's SHORT expiry
        # actually arrives, book that leg/condor at the SPXW PM close; until then
        # hold. Requires the debit-rooted, two-stage settlement path.
        return True

    # ------------------------------------------------------------------
    # Two-expiry data layer (Phase 2) — pick expiries, resolve per-expiry
    # conids, read per-expiry quotes + IV. Thin wrappers over the existing
    # broker-data methods called with EXPLICIT (non-0DTE) expiries; the pure
    # expiry-selection logic lives in calendar_chain.
    # ------------------------------------------------------------------

    def _dc_pick_expiries(self) -> Optional[Tuple[str, str]]:
        """Choose (short_expiry, long_expiry) for today from the SPXW trading-day
        candidates and the configured DTE windows. Returns None if no viable pair."""
        today_iso = get_us_market_time().strftime("%Y-%m-%d")
        horizon = self.dc_short_dte_max + self.dc_long_extra_dte_max + 3  # small buffer
        candidates = generate_candidate_expiries(today_iso, horizon)
        picked = pick_calendar_expiries(
            candidates, today_iso,
            self.dc_short_dte_min, self.dc_short_dte_max,
            self.dc_long_extra_dte_min, self.dc_long_extra_dte_max,
            prefer_friday=self.dc_prefer_friday,
        )
        if picked is None:
            logger.warning(
                "[DCTM-EXPIRIES] no viable pair: short %d-%d DTE, long +%d-%d (candidates=%d)",
                self.dc_short_dte_min, self.dc_short_dte_max,
                self.dc_long_extra_dte_min, self.dc_long_extra_dte_max, len(candidates),
            )
            return None
        short_exp, long_exp = picked
        t = date.fromisoformat(today_iso)
        logger.info(
            "[DCTM-EXPIRIES] short=%s (%dDTE) long=%s (+%dd)",
            short_exp, (date.fromisoformat(short_exp) - t).days,
            long_exp, (date.fromisoformat(long_exp) - date.fromisoformat(short_exp)).days,
        )
        return picked

    def _dc_resolve_calendar_legs(
        self, call_strike: float, put_strike: float,
        short_expiry: str, long_expiry: str,
    ) -> Optional[Dict[str, int]]:
        """Resolve the 4 calendar-leg conids across the two expirations. The call
        calendar shares ``call_strike`` (short near / long far); the put calendar
        shares ``put_strike``. Returns the {leg_name: conid} map or None if any
        leg fails to resolve (off-grid strike / unlisted expiry)."""
        legs = {
            "short_call": self._get_option_uic(call_strike, "Call", short_expiry),
            "long_call":  self._get_option_uic(call_strike, "Call", long_expiry),
            "short_put":  self._get_option_uic(put_strike, "Put", short_expiry),
            "long_put":   self._get_option_uic(put_strike, "Put", long_expiry),
        }
        missing = [k for k, v in legs.items() if not v]
        if missing:
            logger.warning(
                "[DCTM-LEGS] unresolved conids %s (C %s / P %s, short %s / long %s)",
                missing, call_strike, put_strike, short_expiry, long_expiry,
            )
            return None
        return legs

    def _dc_read_iv(self, conid: int) -> Optional[float]:
        """Per-option implied vol from the broker greeks snapshot. Returns None
        (NOT 0.0) on a missing/None/non-positive IV so a flaky read reads as
        'no signal', never 'IV=0'."""
        try:
            g = self.broker.get_option_greeks(conid) or {}
        except Exception as exc:
            logger.warning("[DCTM-IV] greeks read failed for conid %s: %s", conid, exc)
            return None
        try:
            iv = float(g.get("iv"))
        except (TypeError, ValueError):
            return None
        return iv if iv > 0 else None

    def _dc_front_back_iv(
        self, strike: float, right: str, short_expiry: str, long_expiry: str,
    ) -> Optional[Tuple[float, float]]:
        """(front_iv, back_iv) for the same strike on the near vs far expiry — the
        term-structure signal that IS the calendar's edge (front contracting
        faster than back is favorable). None if either IV is unavailable."""
        front_conid = self._get_option_uic(strike, right, short_expiry)
        back_conid = self._get_option_uic(strike, right, long_expiry)
        if not front_conid or not back_conid:
            return None
        front_iv = self._dc_read_iv(front_conid)
        back_iv = self._dc_read_iv(back_conid)
        if front_iv is None or back_iv is None:
            logger.info(
                "[DCTM-IV] term-structure unavailable (front=%s back=%s) — no signal",
                front_iv, back_iv,
            )
            return None
        return front_iv, back_iv

    def _dc_read_leg_quotes(self, leg_conids: Dict[str, int]) -> Dict[str, dict]:
        """Current mid (+ raw quote) per resolved leg. Reuses the crossed-quote-
        guarded _read_option_quote / _quote_mid so a crossed book yields mid=0,
        not a nonsense price."""
        out: Dict[str, dict] = {}
        for name, conid in leg_conids.items():
            q = self._read_option_quote(conid) or {}
            out[name] = {"mid": self._quote_mid(q), "raw": q}
        return out

    def _dc_probe_two_expiry_data(self) -> bool:
        """LIVE diagnostic — run on the VM in market hours. Picks the two
        expiries, resolves an ~ATM call+put on BOTH, and reads quotes + per-expiry
        IV. This verifies non-0DTE SPXW entitlement + snapshot warmup — the one
        Phase-2 item that cannot be checked offline. Returns True iff both
        expirations produced a populated mid AND IV at the ATM strike."""
        picked = self._dc_pick_expiries()
        if not picked:
            return False
        short_exp, long_exp = picked
        spx = self.current_price
        if not spx or spx <= 0:
            logger.warning("[DCTM-PROBE] no SPX price — cannot probe")
            return False
        inc = getattr(self, "strike_increment", 5) or 5
        atm = round(spx / inc) * inc
        legs = self._dc_resolve_calendar_legs(atm, atm, short_exp, long_exp)
        if not legs:
            return False
        quotes = self._dc_read_leg_quotes(legs)
        iv_call = self._dc_front_back_iv(atm, "Call", short_exp, long_exp)
        ok = (
            quotes["short_call"]["mid"] > 0 and quotes["long_call"]["mid"] > 0
            and iv_call is not None
        )
        logger.info(
            "[DCTM-PROBE] ATM %s | short_call mid=%.2f long_call mid=%.2f | front/back IV=%s | %s",
            atm, quotes["short_call"]["mid"], quotes["long_call"]["mid"],
            iv_call, "OK" if ok else "INCOMPLETE (entitlement/warmup?)",
        )
        return ok

    # ------------------------------------------------------------------
    # Entry + dry-run simulation (Phase 3)
    # Pick the 30-40delta two-expiry strikes, open the net-DEBIT double calendar,
    # and SIMULATE the fills from REAL mids (dry-run: no broker order, synthetic
    # DRY ids). Overridden (not inherited) so D NEVER runs HYDRA's IC entry.
    # ------------------------------------------------------------------

    def _min_buying_power_per_unit(self) -> float:
        """A double calendar's defined risk is the net DEBIT paid (a long calendar
        cannot lose more than the debit), FAR below the iron-condor defined-risk
        floor. Use a conservative per-contract debit estimate (config-driven)."""
        cfg = getattr(self, "strategy_config", {}) or {}
        return float(cfg.get("min_buying_power_per_calendar", 2000.0))

    def _dc_delta_target_strike(self, spx: float, right: str, expiry: str) -> Optional[float]:
        """OTM strike whose option |delta| is closest to dc_target_delta within
        dc_delta_band, on the SHORT expiry. Scans on-grid strikes outward from
        spot (calls up, puts down), reading delta per candidate. Returns the
        strike or None if none lands in the band."""
        inc = getattr(self, "strike_increment", 5) or 5
        lo, hi = self.dc_delta_band
        base = round(spx / inc) * inc
        best = None  # (abs(delta - target), strike)
        for step in range(1, 41):  # bounded scan so a bad chain can't loop forever
            strike = base + step * inc if right == "Call" else base - step * inc
            if strike <= 0:
                break
            conid = self._get_option_uic(strike, right, expiry)
            if not conid:
                continue
            try:
                delta = abs(float((self.broker.get_option_greeks(conid) or {}).get("delta")))
            except Exception:
                continue
            if delta <= 0:
                continue
            if lo <= delta <= hi:
                score = abs(delta - self.dc_target_delta)
                if best is None or score < best[0]:
                    best = (score, strike)
            elif delta < lo and best is not None:
                break  # past the band (deltas fall as we go further OTM)
        return best[1] if best else None

    def _calculate_strikes(self, entry) -> bool:
        """Pick the two expiries and the 30-40delta short strikes, and stamp them
        onto the calendar entry. A calendar's short+long of a side share a STRIKE
        but differ in EXPIRY."""
        spx = self.current_price
        if not spx or spx <= 0:
            logger.error("[DCTM] no SPX price — cannot calculate strikes")
            return False
        picked = self._dc_pick_expiries()
        if not picked:
            return False
        short_exp, long_exp = picked
        kc = self._dc_delta_target_strike(spx, "Call", short_exp)
        kp = self._dc_delta_target_strike(spx, "Put", short_exp)
        if not kc or not kp:
            logger.warning(
                "[DCTM] no ~%.2fdelta strike in band %s (call=%s put=%s)",
                self.dc_target_delta, list(self.dc_delta_band), kc, kp,
            )
            return False
        entry.short_call_strike = entry.long_call_strike = kc
        entry.short_put_strike = entry.long_put_strike = kp
        entry.legs["short_call"].expiry = short_exp
        entry.legs["long_call"].expiry = long_exp
        entry.legs["short_put"].expiry = short_exp
        entry.legs["long_put"].expiry = long_exp
        logger.info("[DCTM] strikes Kc=%s Kp=%s | short=%s long=%s", kc, kp, short_exp, long_exp)
        return True

    def _dc_simulate_entry(self, entry) -> bool:
        """Dry-run open of the net-DEBIT double calendar from REAL mids (no broker
        order). Resolves the 4 conids across 2 expiries, prices the net debit
        (buy longs - sell shorts), and stamps fills + synthetic DRY ids."""
        short_exp, long_exp = entry.short_expiry, entry.long_expiry
        leg_conids = self._dc_resolve_calendar_legs(
            entry.short_call_strike, entry.short_put_strike, short_exp, long_exp
        )
        if not leg_conids:
            return False
        quotes = self._dc_read_leg_quotes(leg_conids)
        mids = {k: quotes[k]["mid"] for k in leg_conids}
        if any(m is None or m <= 0 for m in mids.values()):
            logger.warning("[DCTM] incomplete mids %s — cannot price calendar", mids)
            return False
        n = self.contracts_per_entry
        # Net DEBIT = cost of longs (bought) - credit from shorts (sold).
        net_debit = (
            mids["long_call"] + mids["long_put"] - mids["short_call"] - mids["short_put"]
        ) * 100 * n
        base_id = int(get_us_market_time().timestamp() * 1000)
        for name, conid in leg_conids.items():
            leg = entry.legs[name]
            leg.uic = conid
            leg.fill_price = mids[name]
            leg.price = mids[name]
            leg.position_id = f"DRY_{base_id}_{_DC_LEG_ABBR[name]}"
        entry.net_debit = net_debit
        entry.dc_phase = DCPhase.CALENDAR
        entry.contracts = n
        entry.is_complete = True
        logger.info(
            "[DCTM-OPEN] entry #%s DEBIT $%.2f | Kc=%s Kp=%s | short=%s long=%s | %dc",
            entry.entry_number, net_debit, entry.short_call_strike,
            entry.short_put_strike, short_exp, long_exp, n,
        )
        return True

    def _dc_pre_entry_gates(self, entry_num: int) -> Optional[str]:
        """Minimal pre-entry gates (reuse the shared helpers): orphaned orders,
        market halt, buying power. Deliberately simpler than HYDRA's IC gates (no
        VIX regime, no MKT-011 credit gate — those are credit-IC concepts)."""
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
        ever reaches the broker. DB recording is Phase 6; the transformer / 20%
        stop / EOD close (_check_stop_losses) is Phase 4.
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
            # TODO(Phase 6): _record_entry_to_db with the calendar schema (the IC
            # recorder would mis-store a debit calendar as a credit IC).
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
        """Manage every open double calendar (CALENDAR phase). Returns a summary
        of actions taken this tick, or None."""
        actions = []
        for entry in list(self.daily_state.active_entries):
            if not isinstance(entry, CalendarEntry):
                continue
            if entry.dc_phase == DCPhase.CALENDAR:
                act = self._dc_manage_calendar(entry)
                if act:
                    actions.append(act)
            # TRANSFORMED positions hold to expiry — settled in Phase 5.
        return "; ".join(actions) if actions else None

    def _dc_refresh_marks(self, entry) -> None:
        """Refresh each open leg's price from the current quote so unrealized_pnl
        is current before a transform/stop/EOD decision."""
        conids = {
            name: entry.legs[name].uic
            for name in ("short_call", "long_call", "short_put", "long_put")
            if entry.legs[name].uic
        }
        if not conids:
            return
        for name, q in self._dc_read_leg_quotes(conids).items():
            if q["mid"] and q["mid"] > 0:
                entry.legs[name].price = q["mid"]

    def _dc_past_eod_cutoff(self) -> bool:
        return get_us_market_time().time() >= self.dc_eod_cutoff

    def _dc_manage_calendar(self, entry) -> Optional[str]:
        """One open calendar's intraday decision (priority: transform -> stop -> EOD)."""
        self._dc_refresh_marks(entry)
        if entry.net_debit <= 0:
            return None
        pnl_pct = entry.unrealized_pnl / entry.net_debit

        # 1. profit trigger -> attempt the (risk-free-gated) transformer
        if pnl_pct >= self.dc_profit_trigger_pct:
            if self._dc_attempt_transform(entry):
                return f"TRANSFORM E#{entry.entry_number}"
            # gate failed (not yet risk-free) -> fall through to stop/EOD checks

        # 2. pre-transform hard stop (~20% of debit)
        if pnl_pct <= -self.dc_pre_transform_stop_pct:
            self._dc_close_calendar(entry, reason="20%-debit stop", loss=True)
            return f"STOP E#{entry.entry_number}"

        # 3. EOD-day-1 close if still un-transformed
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

        # Sell the back-dated longs; buy the wings on the short expiry.
        lq = self._dc_read_leg_quotes({"long_call": entry.long_call_uic, "long_put": entry.long_put_uic})
        wq = self._dc_read_leg_quotes({"wing_call": wing_call_conid, "wing_put": wing_put_conid})
        lc, lp = lq["long_call"]["mid"], lq["long_put"]["mid"]
        wc, wp = wq["wing_call"]["mid"], wq["wing_put"]["mid"]
        if any(m is None or m <= 0 for m in (lc, lp, wc, wp)):
            logger.info("[DCTM] transform deferred — incomplete mids (lc=%s lp=%s wc=%s wp=%s)", lc, lp, wc, wp)
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
        return True

    def _dc_close_calendar(self, entry, reason: str, loss: bool) -> None:
        """Liquidate an un-transformed calendar (dry-run): book realized P&L from
        the current mark, mark CLOSED, and set side-done flags so active_entries
        drops it. ``loss`` distinguishes the 20%-stop from the managed EOD close."""
        self._dc_refresh_marks(entry)
        pnl = entry.unrealized_pnl  # CALENDAR phase: calendar_value - net_debit
        n = entry.contracts or self.contracts_per_entry
        close_comm = 4 * self.commission_per_leg * n
        self.daily_state.total_realized_pnl += pnl
        self.daily_state.total_commission += close_comm
        entry.close_commission = close_comm
        entry.dc_phase = DCPhase.CLOSED
        if loss:
            entry.call_side_stopped = entry.put_side_stopped = True
        else:
            entry.call_side_pivot_closed = entry.put_side_pivot_closed = True
        tag = "DCTM-STOP" if loss else "DCTM-EOD-CLOSE"
        logger.warning(
            "[%s] E#%s closed (%s): P&L $%.2f on debit $%.2f",
            tag, entry.entry_number, reason, pnl, entry.net_debit,
        )
