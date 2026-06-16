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
                "DoubleCalendarStrategy (Strategy D) is dry-run-LOCKED. The entry / "
                "transformer / debit-P&L / multi-day-settlement logic IS implemented "
                "(Phases 1-7), but running it with real paper orders next to the live "
                "variants requires the coexistence MUST-FIXes (scope STATE-004 + "
                "orphan sweep to per-variant conids; budget buying power) — see this "
                "module's docstring. Set dry_run=true, or do not select "
                "strategy.name='double_calendar'."
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
            # Re-attach only entries NOT already present. The base reset normally
            # rebuilds daily_state (entries=[]) so we re-add all carried; but if it
            # EARLY-RETURNS (broker outage / STATE-004) it leaves daily_state
            # untouched (carried still in it) — a blind extend() would DUPLICATE.
            # Identity check (`is`) avoids dataclass __eq__ matching a different entry.
            existing = self.daily_state.entries
            readopted = 0
            for e in carried:
                if not any(e is x for x in existing):
                    existing.append(e)
                    readopted += 1
            if readopted:
                logger.info(
                    "[DCTM-CARRY] carried %d open multi-day position(s) across the new-day "
                    "reset (not wiped, not flagged as an overnight 0DTE fault).",
                    readopted,
                )
            # Multi-day P&L accounting (verified 2026-06-16): each carried entry
            # keeps its OWN cost basis (net_debit, transform_credit) as fields, so
            # _dc_settle_entry books the terminal P&L (transform_credit − net_debit
            # − IC intrinsic, or mark − debit) into the SETTLEMENT day's
            # total_realized_pnl correctly — there is nothing to carry in the
            # per-day counter. Open positions surface as UNREALIZED in
            # net_pnl/total_pnl; lifetime totals accumulate in the metrics file via
            # log_daily_summary. The per-day total_realized_pnl=0 reset is correct.

    def _calculate_capital_deployed(self) -> float:
        """Capital at risk for D = sum of OPEN calendars' net debit (pre-transform
        the max loss IS the debit; post-risk-free-transform it's ~0). The base
        method uses spread_width × 100 × contracts — for D's same-strike calendar
        that is just the wing notional (e.g. $500), LESS than the debit actually
        paid ($1035), so the heartbeat Capital/Return were computed off the wrong
        base."""
        total = 0.0
        for e in self.daily_state.entries:
            if self._dc_entry_is_open(e):
                total += float(getattr(e, "net_debit", 0.0) or 0.0)
        return total

    def _dc_open_debit_at_risk(self) -> float:
        """Net debit across OPEN pre-transform (CALENDAR) calendars — the capital
        genuinely at risk. TRANSFORMED legs are (gated) risk-free, so excluded."""
        total = 0.0
        for e in self.daily_state.entries:
            if self._dc_entry_is_open(e) and getattr(e, "dc_phase", None) == DCPhase.CALENDAR:
                total += float(getattr(e, "net_debit", 0.0) or 0.0)
        return total

    def _calculate_max_loss_with_stops(self) -> float:
        """D's max loss WITH the 20%-debit stop working = stop_pct × open debit.
        The base IC math (stop_level − credit per side) is meaningless for a
        net-debit calendar and produced phantom numbers in the heartbeat."""
        return self._dc_open_debit_at_risk() * self.dc_pre_transform_stop_pct

    def _calculate_max_loss_catastrophic(self) -> float:
        """D's worst case if the stop fails = the full debit paid (a long calendar
        cannot lose more than its debit). Risk-free TRANSFORMED legs excluded."""
        return self._dc_open_debit_at_risk()

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

    def get_detailed_position_status(self) -> List[str]:
        """Calendar-native per-entry heartbeat lines. D holds net-DEBIT double
        calendars, so the inherited IC-style line (Credit/cushion/SV) is garbage
        for D ('Credit $0', '-469% cushion', an SV that ignores the debit). Render
        the calendar's real economics: phase, strikes, both expiries, debit,
        current liquidation value, and unrealized P&L as % of debit."""
        lines: List[str] = []
        for entry in self.daily_state.active_entries:
            if not isinstance(entry, CalendarEntry):
                continue  # D only holds calendars; skip anything unexpected
            phase = getattr(entry.dc_phase, "value", str(entry.dc_phase))
            debit = float(getattr(entry, "net_debit", 0.0) or 0.0)
            value = entry.calendar_value
            upnl = entry.unrealized_pnl
            pct = (upnl / debit * 100.0) if debit > 0 else 0.0
            confirming = " ⏳stop-confirm" if entry.entry_number in self._dc_stop_breach else ""
            lines.append(
                f"  Entry #{entry.entry_number} [{phase}]: "
                f"Kc={entry.short_call_strike:.0f} Kp={entry.short_put_strike:.0f} | "
                f"short {getattr(entry, 'short_expiry', '?')} / long {getattr(entry, 'long_expiry', '?')} | "
                f"debit ${debit:.0f} | value ${value:.0f} | "
                f"P&L ${upnl:+.0f} ({pct:+.1f}%) | {entry.contracts}c{confirming}"
            )
        return lines

    def check_after_hours_settlement(self) -> bool:
        """Treat a legitimately-held multi-day position as NORMAL after the close.

        The base method assumes any tracked position should settle the same
        session and, until it does, returns False (keeping the daily-summary gate
        open). A DC position held across days would jam that gate forever. Here:
        if D is holding open multi-day positions, report settled-for-today so the
        daily summary proceeds; otherwise defer to the base 0DTE-style logic.
        """
        # Phase 5: settle any position whose SHORT expiry has arrived (the
        # transformed IC / leftover calendar settles at the SPXW PM close), then
        # treat the rest as normally-held multi-day positions.
        today = get_us_market_time().strftime("%Y-%m-%d")
        self._dc_settle_due(today)

        open_md = [e for e in self.daily_state.entries if self._dc_entry_is_open(e)]
        if not open_md:
            return super().check_after_hours_settlement()
        logger.info(
            "[DCTM-HOLD] %d multi-day position(s) held past the close — normal for a "
            "calendar, not pending settlement; daily summary may proceed.",
            len(open_md),
        )
        return True

    def get_daily_summary(self) -> dict:
        """Daily summary with the IC credit-vertical breakdown zeroed for D.

        The base summary derives ``expired_credits`` from each entry's
        call/put_spread_credit and ``stop_loss_debits`` from the credit-kept
        identity — both meaningless for a net-DEBIT calendar (D books realized
        P&L directly into total_realized_pnl at transform/stop/settlement). Left
        as-is, a settled CalendarEntry's transform-time credit would surface as a
        bogus expired-credit. Zero those two IC-specific fields and report D's
        realized P&L from total_realized_pnl; net_pnl/total_pnl are already
        debit-correct (they derive from total_realized_pnl + unrealized)."""
        summary = super().get_daily_summary()
        summary["expired_credits"] = 0.0
        summary["stop_loss_debits"] = 0.0
        summary["realized_pnl"] = self.daily_state.total_realized_pnl
        return summary

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
            out[name] = {"mid": self._quote_mid(q), "raw": q, "realtime": self._dc_quote_is_realtime(q)}
        return out

    def _dc_quote_is_realtime(self, q: dict) -> bool:
        """True unless the broker DEFINITIVELY flags the quote as delayed/frozen.
        Wraps HYDRA's _option_quote_is_realtime (IBKR field 6509); defaults to
        True when the check is unavailable/errors so a missing entitlement signal
        never freezes D's stop/transform entirely — it only rejects a quote we
        can positively confirm is non-real-time."""
        try:
            checker = getattr(self, "_option_quote_is_realtime", None)
            return bool(checker(q)) if checker else True
        except Exception:
            return True

    def _dc_probe_two_expiry_data(self) -> bool:
        """LIVE diagnostic — run on the VM in market hours. Picks the two
        expiries, resolves an ~ATM call+put on BOTH, and reads quotes + per-expiry
        IV. This verifies non-0DTE SPXW entitlement + snapshot warmup — the one
        Phase-2 item that cannot be checked offline. Returns True iff both
        expirations produced a populated mid AND IV at the ATM strike."""
        picked = self._dc_pick_expiries()
        if not picked:
            return False
        short_exp, longs = picked  # _dc_pick_expiries returns (short, [long candidates])
        if not longs:
            logger.warning("[DCTM-PROBE] no long candidate for short %s", short_exp)
            return False
        long_exp = longs[0]
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

    def _dc_em_otm_distance(self, spx: float, short_expiry: str) -> float:
        """Estimated OTM distance (points) for a ~dc_target_delta strike, from the
        VIX-implied expected move: EM_1sd = spx*(VIX/100)*sqrt(DTE/365), scaled by
        dc_delta_otm_fraction (~0.40 ≈ 35Δ in BS terms). Used to CENTER the bounded
        scan so we read greeks for only a handful of strikes, not ~40."""
        vix = self.current_vix if (self.current_vix and self.current_vix > 0) else 16.0
        try:
            dte = max(1, (date.fromisoformat(short_expiry) - get_us_market_time().date()).days)
        except Exception:
            dte = 10
        em_1sd = spx * (vix / 100.0) * ((dte / 365.0) ** 0.5)
        inc = getattr(self, "strike_increment", 5) or 5
        return max(inc, em_1sd * self.dc_delta_otm_fraction)

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
        # A double calendar is a net-DEBIT structure by definition. An inverted
        # term structure (shorts richer than longs) yields net_debit <= 0 — that
        # is NOT this strategy, and it would also break pnl_pct = pnl/net_debit
        # and leave the position unmanaged. Skip it rather than open an anomaly.
        if net_debit <= 0:
            logger.warning(
                "[DCTM] skipping entry #%s — net credit/zero (%.2f); a calendar must be a net debit",
                entry.entry_number, net_debit,
            )
            return False
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
        # Stamp the (config) wing width at OPEN so spread_width / capital_deployed
        # are non-zero in the CALENDAR phase (it's the planned IC wing, known
        # upfront; the transformer uses the same value).
        entry.wing_width = self.dc_wing_width
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

    def _dc_refresh_marks(self, entry) -> bool:
        """Refresh each open leg's price from the current quote. Returns True only
        if EVERY open leg got a fresh positive mid THIS tick — a missing/crossed
        mid leaves the prior (stale) price in place, so callers must not act on a
        non-fresh mark (a frozen leg could otherwise trip the % stop on bad data)."""
        conids = {
            name: entry.legs[name].uic
            for name in ("short_call", "long_call", "short_put", "long_put")
            if entry.legs[name].uic
        }
        if not conids:
            return False
        fresh = True
        quotes = self._dc_read_leg_quotes(conids)
        require_rt = getattr(self, "dc_require_realtime_quotes", True)
        for name in conids:
            qrow = quotes.get(name) or {}
            mid = qrow.get("mid")
            rt_ok = qrow.get("realtime", True) or not require_rt
            # A positive mid from a real-time quote updates the price; a missing/
            # crossed mid OR a confirmed-stale quote leaves the prior price and
            # marks the tick not-fresh (so value-driven stop/transform skip it).
            if mid and mid > 0 and rt_ok:
                entry.legs[name].price = mid
            else:
                fresh = False
        return fresh

    def _dc_past_eod_cutoff(self) -> bool:
        return get_us_market_time().time() >= self.dc_eod_cutoff

    def _dc_manage_calendar(self, entry) -> Optional[str]:
        """One open calendar's intraday decision (priority: transform -> stop -> EOD)."""
        fresh = self._dc_refresh_marks(entry)
        if entry.net_debit <= 0:
            return None

        # The value-driven decisions (transform, % stop) act ONLY on a fully fresh
        # mark this tick — a single stale leg could otherwise fire a false stop or
        # a mistimed transform. EOD close is TIME-based and runs regardless.
        if fresh:
            pnl_pct = entry.unrealized_pnl / entry.net_debit
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
        if getattr(self, "_dc_recorder", None):
            self._dc_recorder.record_transformation(entry, get_us_market_time().strftime("%Y-%m-%d"))
        return True

    @staticmethod
    def _dc_clear_leg_conids(entry) -> None:
        """Null the leg conids/ids of a CLOSED entry so the base account-wide
        reconciliation never re-counts an already-settled calendar as a tracked
        ('expected') position."""
        for name in LEG_NAMES:
            entry.legs[name].uic = None
            entry.legs[name].position_id = None

    def _dc_close_calendar(self, entry, reason: str, loss: bool) -> None:
        """Liquidate an un-transformed calendar (dry-run): book realized P&L from
        the current mark, mark CLOSED, and set side-done flags so active_entries
        drops it. ``loss`` distinguishes the 20%-stop from the managed EOD close.

        Caller MUST have refreshed marks this tick (_dc_manage_calendar does at
        the top). We deliberately do NOT re-refresh here: re-fetching the noisy
        4-mid calendar value would book a DIFFERENT P&L than the one that
        triggered the close (2026-06-16: a -20% trigger booked at -6.3%)."""
        self._dc_stop_breach.pop(entry.entry_number, None)
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
        self._dc_clear_leg_conids(entry)
        tag = "DCTM-STOP" if loss else "DCTM-EOD-CLOSE"
        logger.warning(
            "[%s] E#%s closed (%s): P&L $%.2f on debit $%.2f",
            tag, entry.entry_number, reason, pnl, entry.net_debit,
        )
        if getattr(self, "_dc_recorder", None):
            entry_date = entry.entry_time.strftime("%Y-%m-%d") if entry.entry_time else ""
            self._dc_recorder.record_outcome(
                entry, "stop" if loss else "eod_close", pnl,
                getattr(self, "current_price", None), entry_date,
                get_us_market_time().strftime("%Y-%m-%d"),
            )
        # Crash-window guard: persist the CLOSE now (base state + sidecar drops the
        # now-CLOSED calendar) so a crash before the next heartbeat can't re-adopt
        # it as open from a stale sidecar. Mirrors _dc_settle_due / _initiate_entry.
        self._save_state_to_disk()

    # ------------------------------------------------------------------
    # Multi-day persistence + per-expiry settlement (Phase 5)
    # D owns its persistence via a SIDECAR (the Brandon-hedge precedent) so the
    # 0DTE base save/load stays byte-identical: super() writes the base state
    # file unchanged; the sidecar carries the multi-day fields (dc_phase,
    # expiries, debit, transform) the fixed IC schema can't, and lets a calendar
    # opened on a PRIOR day be re-adopted (the base date!=today guard drops it).
    # ------------------------------------------------------------------

    def _dc_state_path(self) -> str:
        """Sidecar path next to the variant state file (data/variant_d/...)."""
        return os.path.join(os.path.dirname(self.state_file), "dc_open_trades.json")

    def _dc_serialize_entry(self, e) -> dict:
        """Serialize a CalendarEntry to a sidecar dict (all multi-day fields)."""
        return {
            "entry_number": e.entry_number,
            "strategy_id": getattr(e, "strategy_id", ""),
            "structure": getattr(e, "structure", "double_calendar"),
            "dc_phase": e.dc_phase.value,
            "contracts": e.contracts,
            "entry_time": e.entry_time.isoformat() if e.entry_time else None,
            "net_debit": e.net_debit,
            "transform_credit": e.transform_credit,
            "wing_width": e.wing_width,
            "is_risk_free": e.is_risk_free,
            "transformed_at": e.transformed_at,
            "is_complete": e.is_complete,
            "call_spread_credit": e.call_spread_credit,
            "put_spread_credit": e.put_spread_credit,
            "flags": {
                f: getattr(e, f, False) for f in (
                    "call_side_stopped", "put_side_stopped",
                    "call_side_pivot_closed", "put_side_pivot_closed",
                    "call_side_expired", "put_side_expired",
                )
            },
            "legs": {
                name: {
                    "strike": e.legs[name].strike,
                    "uic": e.legs[name].uic,
                    "expiry": e.legs[name].expiry,
                    "fill_price": e.legs[name].fill_price,
                    "price": e.legs[name].price,
                    "position_id": e.legs[name].position_id,
                }
                for name in LEG_NAMES
            },
        }

    def _dc_deserialize_entry(self, d: dict):
        """Rebuild a CalendarEntry from a sidecar dict."""
        e = CalendarEntry(entry_number=int(d["entry_number"]))
        e.strategy_id = d.get("strategy_id", "")
        e.structure = d.get("structure", "double_calendar")
        e.dc_phase = DCPhase(d.get("dc_phase", "calendar"))
        e.contracts = int(d.get("contracts", 1))
        et = d.get("entry_time")
        e.entry_time = datetime.fromisoformat(et) if et else None
        e.net_debit = float(d.get("net_debit", 0.0))
        e.transform_credit = float(d.get("transform_credit", 0.0))
        e.wing_width = float(d.get("wing_width", 0.0))
        e.is_risk_free = bool(d.get("is_risk_free", False))
        e.transformed_at = d.get("transformed_at", "")
        e.is_complete = bool(d.get("is_complete", False))
        e.call_spread_credit = float(d.get("call_spread_credit", 0.0))
        e.put_spread_credit = float(d.get("put_spread_credit", 0.0))
        for f, v in (d.get("flags") or {}).items():
            setattr(e, f, bool(v))
        for name, lr in (d.get("legs") or {}).items():
            if name not in e.legs:
                continue
            leg = e.legs[name]
            leg.strike = lr.get("strike", 0.0)
            leg.uic = lr.get("uic")
            leg.expiry = lr.get("expiry")
            leg.fill_price = lr.get("fill_price", 0.0)
            leg.price = lr.get("price", 0.0)
            leg.position_id = lr.get("position_id")
        return e

    def _dc_save_sidecar(self) -> None:
        """Persist all OPEN (non-CLOSED) calendars to the sidecar.

        Guarded by _dc_loaded: during startup the base recovery/reset may call
        _save_state_to_disk BEFORE _dc_load_sidecar has run; writing then would
        clobber the real sidecar with an empty list and lose the open calendar.
        """
        if not getattr(self, "_dc_loaded", False):
            return
        records = [
            self._dc_serialize_entry(e)
            for e in self.daily_state.entries
            if isinstance(e, CalendarEntry) and e.dc_phase != DCPhase.CLOSED
        ]
        try:
            self._atomic_write_json(self._dc_state_path(), records)
        except Exception as exc:
            logger.error("[DCTM] sidecar save failed: %s", exc)

    def _dc_load_sidecar(self) -> bool:
        """Re-adopt open multi-day calendars from the sidecar (survives restarts
        AND prior-day opens, which the base date!=today guard drops). Replaces any
        base-loaded IC-shaped version of the same trade. Returns True if any
        calendar was adopted."""
        # Mark loaded FIRST so subsequent _dc_save_sidecar calls are armed even
        # when there's no file yet (a fresh start with no open calendars).
        self._dc_loaded = True
        path = self._dc_state_path()
        if not os.path.exists(path):
            return False
        try:
            with open(path) as f:
                records = json.load(f)
        except Exception as exc:
            logger.error("[DCTM] sidecar read failed: %s", exc)
            return False
        if not records:
            return False
        sidecar_ids = {r.get("strategy_id") for r in records}
        # Drop any base-loaded (IronCondorEntry, dc_phase-less) version of these.
        self.daily_state.entries = [
            e for e in self.daily_state.entries
            if getattr(e, "strategy_id", None) not in sidecar_ids
        ]
        adopted = 0
        for r in records:
            try:
                self.daily_state.entries.append(self._dc_deserialize_entry(r))
                adopted += 1
            except Exception as exc:
                logger.error("[DCTM] could not rebuild calendar from sidecar: %s", exc)
        if adopted:
            logger.info("[DCTM-RECOVER] re-adopted %d multi-day calendar(s) from sidecar", adopted)
        return adopted > 0

    def _save_state_to_disk(self):
        """Base state file (unchanged) + the D sidecar with the multi-day fields."""
        super()._save_state_to_disk()
        self._dc_save_sidecar()

    def _recover_positions_from_saxo(self) -> bool:
        """Base today-only recovery, then re-adopt multi-day calendars from the
        sidecar (the base load drops a prior-day calendar via its date!=today
        guard). Called from the inherited __init__; sidecar load needs no dc_*
        config, so init ordering is safe."""
        base = super()._recover_positions_from_saxo()
        adopted = self._dc_load_sidecar()
        return base or adopted

    # ── Per-expiry settlement ──────────────────────────────────────────

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(x, hi))

    def _dc_settlement_spx(self) -> Optional[float]:
        """SPX level to settle against — HYDRA's close-resolution: current_price,
        or the day's last recorded SPX tick after-hours (via _resolve_spx_close),
        so a LATE settlement doesn't mark against a current_price that has decayed
        to 0 post-close. None if genuinely unreadable (defer settlement). NOTE: this
        is the recorded close, NOT the official SPXW SOQ/PM-settlement print (not
        on this feed) — a documented dry-run fidelity limitation."""
        try:
            px = self._resolve_spx_close()
        except Exception:
            px = getattr(self, "current_price", 0) or 0
        return px if px and px > 0 else None

    def _dc_settle_due(self, today: str) -> bool:
        """Settle every open position whose SHORT expiry has arrived (ISO compare).
        Returns True if anything settled."""
        due = [
            e for e in self.daily_state.entries
            if self._dc_entry_is_open(e) and (e.short_expiry or "") and e.short_expiry <= today
        ]
        if not due:
            return False
        spx = self._dc_settlement_spx()
        if spx is None:
            logger.warning("[DCTM-SETTLE] %d position(s) due but SPX unreadable — deferring", len(due))
            return False
        for e in due:
            self._dc_settle_entry(e, spx)
        # Persist BOTH the base state file (realized P&L now in total_realized_pnl)
        # and the sidecar (settled calendars removed) — not just the sidecar.
        self._save_state_to_disk()
        return True

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

    def _record_heartbeat_to_db(self):
        """Record the market tick (generic SPX/VIX, useful for D) + D's calendar
        snapshots. OVERRIDE: does NOT write the base IC-shaped spread_snapshots —
        call/put_spread_value in IC-named columns would mis-describe a debit
        calendar — routing per-entry marks to dc_calendar_snapshots instead."""
        rec = getattr(self, "_data_recorder", None)
        now = get_us_market_time()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        if rec:
            try:
                rec.record_tick(
                    timestamp=timestamp,
                    spx_price=self.current_price,
                    vix_level=self.current_vix,
                    trend_signal=self._current_trend.value if self._current_trend else "unknown",
                    bot_state=self.state.value if hasattr(self.state, "value") else str(self.state),
                    entry_count=self.daily_state.entries_completed,
                    active_count=len(self.daily_state.active_entries),
                )
            except Exception as e:
                logger.debug("DCTM heartbeat tick failed: %s", e)
        if getattr(self, "_dc_recorder", None):
            for entry in self.daily_state.active_entries:
                if isinstance(entry, CalendarEntry):
                    try:
                        self._dc_recorder.record_snapshot(entry, timestamp)
                    except Exception as e:
                        logger.debug("DCTM snapshot failed: %s", e)
