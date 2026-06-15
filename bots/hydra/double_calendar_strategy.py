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
from datetime import date
from typing import Dict, Optional, Tuple

from bots.hydra.base_strategy import ConfigError
# DCPhase + the two-expiry CalendarEntry model live in calendar_entry (Phase 1
# foundation). Re-exported here so callers/tests can import them from the
# strategy module. CalendarEntry is consumed by the entry logic in Phase 3.
from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: F401
from bots.hydra.calendar_chain import generate_candidate_expiries, pick_calendar_expiries
from bots.hydra.strategy import HydraStrategy
from shared.market_hours import get_us_market_time

logger = logging.getLogger(__name__)


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
    # Strategy-defining hooks — STUBBED (the multi-week build)
    # Overridden (not inherited) so D NEVER runs HYDRA's iron-condor logic.
    # ------------------------------------------------------------------

    def _calculate_strikes(self, entry) -> bool:
        """STUB. The real implementation picks, for both a call and a put side:
        the short expiry (DTE in [short_dte_min, short_dte_max]), the long expiry
        (short + [long_extra_dte_min, long_extra_dte_max]), and the short strike
        at ~target_delta within delta_band — then snaps to the chain. Returns
        viability. Until built, no entry is viable.
        """
        logger.debug("DCTM: _calculate_strikes is a scaffold stub — no strikes selected.")
        return False

    def _initiate_entry(self) -> str:
        """STUB. The real implementation builds the net-DEBIT double calendar
        (4 legs across 2 expiries), books the debit, and arms the transformer.

        Until built, advance the entry index (so the scheduler doesn't re-fire
        the same slot every tick) and return a clear scaffold-skip message. No
        position is opened, so D stays flat and cannot touch the shared account.
        """
        entry_num = self._next_entry_index + 1
        self._next_entry_index += 1
        # Counter parity with the strangle skip paths so the per-variant DB /
        # dashboard reflect the skip rather than a silent no-op.
        self.daily_state.entries_skipped += 1
        msg = (
            f"Entry #{entry_num} skipped — DCTM (Strategy D) entry logic is a "
            f"scaffold stub (double-calendar open + transformer not yet built)."
        )
        logger.info("[DCTM-SKIP] %s", msg)
        return msg

    def _check_stop_losses(self):
        """STUB. The real implementation monitors each open calendar intraday for:
        the ~profit_trigger_pct -> attempt the transformer (close longs + buy wings,
        fire only if transform credit >= debit + wing width), the
        pre_transform_stop_pct hard exit, and the EOD-day-1 close-if-no-transform.
        No positions exist in the scaffold, so this is a no-op.
        """
        return None
