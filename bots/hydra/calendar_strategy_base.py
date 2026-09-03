"""CalendarStrategyBase — shared calendar plumbing for the calendar strategies.

This base class holds the generic, two-expiry "calendar" plumbing that is NOT
specific to the DC Time Machine's transformer / delta-target / threshold logic:
multi-day lifecycle overrides (reset / settlement / daily-summary), the
two-expiry data layer (leg resolution, IV / quote reads, probe), the dry-run
simulation primitives (mids → net debit, mark refresh, EOD cutoff), the
calendar-aware risk/capital helpers, and the sidecar persistence + per-expiry
settlement machinery.

It was lifted verbatim from ``DoubleCalendarStrategy`` on 2026-06-16 (Commit A of
extracting a shared base so a sibling calendar strategy can inherit it). The
method BODIES were moved cut-and-paste with NO behavioral change — Strategy D
(``DoubleCalendarStrategy``) remains byte-identical and its full test suite
passes unchanged. Both D (``DoubleCalendarStrategy``) and E
(``SpyDoubleCalendarStrategy``, BOT_NAME ``SPYDC``) now subclass this base. The
variant-specific economics (the constructor + its knob reads, expiry/strike
picking, transformer, stop/profit-take manager, settlement booking, monitoring
mode) stay in each subclass.

NOTE: log-tag string literals in this SHARED module use a neutral ``[CAL-*]``
prefix (CAL = calendar) precisely because this code runs for BOTH D and E (e.g.
when E inherits ``_reset_for_new_day`` or ``check_after_hours_settlement``). A
``[CAL-*]`` prefix here would mis-attribute E's calendar plumbing to D's "DC
Time Machine" name. Strategy-specific files keep their own distinctive tags
(D's ``double_calendar_strategy.py`` legitimately uses ``[CAL-*]``). Nothing
parses these tags (verified 2026-06-22), so the prefix is display-only.

``CalendarStrategyBase`` deliberately defines NO ``__init__`` — it inherits
``HydraStrategy``'s, and each concrete calendar strategy keeps its own
dry-run-locked constructor. ``HydraStrategy`` already concretizes the MEIC
abstract methods, so this class is instantiable on its own (though it is meant
to be subclassed).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time
from typing import Dict, List, Optional, Tuple

from bots.hydra.base_strategy import ConfigError, MEICState  # noqa: F401
from bots.hydra.leg import LEG_NAMES
from bots.hydra.calendar_entry import CalendarEntry, DCPhase  # noqa: F401
from bots.hydra.strategy import HydraStrategy
from shared.market_hours import get_us_market_time

logger = logging.getLogger(__name__)

# Canonical-leg-name -> short tag for synthetic dry-run position ids.
_DC_LEG_ABBR = {"short_call": "SC", "long_call": "LC", "short_put": "SP", "long_put": "LP"}


class CalendarStrategyBase(HydraStrategy):
    """Shared two-expiry calendar plumbing for the calendar strategies (D and E).

    Holds the generic calendar machinery (multi-day lifecycle, two-expiry data
    layer, dry-run simulation primitives, sidecar persistence, per-expiry
    settlement) lifted from ``DoubleCalendarStrategy``. The variant-specific
    economics (transformer, delta-target strike picking, thresholds, profit-take /
    stop manager, monitoring mode) live in each subclass.
    """

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
        """Daily reset that PRESERVES a calendar variant's open multi-day positions.

        The base reset rebuilds ``daily_state`` from scratch, which would discard
        any double calendar that legitimately spans days. In DRY-RUN (the only
        mode the calendar strategies permit today) there are NO real broker
        positions, so the base STATE-004 overnight-position halt does not trigger —
        the only fix needed is to re-attach the still-open multi-day entries after
        the base reset.

        REAL-ORDER PATH (NOT YET ENABLED): once a calendar variant places real
        paper orders, the shared account holds its legs overnight and the base
        STATE-004 guard WILL halt (it reads the whole account, unscoped). Do NOT
        flip dry_run=false until STATE-004 / the orphan sweep are scoped to
        per-variant ownership and buying power is budgeted (MUST-FIX #1/#2/#3 — see
        the subclass module docstring).
        """
        carried = [e for e in self.daily_state.entries if self._dc_entry_is_open(e)]
        super()._reset_for_new_day()  # dry-run: account flat -> no STATE-004 halt
        # BUG (found 2026-08-18): the base reset above ends with its own
        # _save_state_to_disk() call, made while daily_state.entries is still
        # EMPTY (the carry re-append below hasn't run yet) — which, via this
        # class's _save_state_to_disk() override, also writes dc_open_trades
        # .json EMPTY. That's a harmless same-day blip IF a later heartbeat's
        # save re-persists the (correctly repopulated in-memory) carried
        # entries first — but if the process restarts in the window before
        # that happens, _dc_load_sidecar() on boot reads the empty file and
        # the position is reconstructed as nothing: permanently dropped from
        # tracking, no further monitoring, ever. Confirmed exactly this
        # sequence on 2026-08-17->18: dc_open_trades.json's mtime lands on
        # the same reset that logged [CAL-CARRY] for it. re_save_needed
        # below forces a second save AFTER the entries are correct again, so
        # the on-disk sidecar is never left empty while a position is truly
        # still open.
        re_save_needed = bool(carried)
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
                    "[CAL-CARRY] carried %d open multi-day position(s) across the new-day "
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

        # Intraday anti-spike state must NOT carry a stale timestamp across the
        # overnight gap (2026-07-20). D's -20% stop confirm window keys on
        # _dc_stop_breach[entry] = first-breach time and only fires once the breach
        # PERSISTS dc_stop_confirm_seconds. A breach recorded near yesterday's close
        # (that neither stopped nor recovered on a fresh tick before marks went
        # stale) would leave a stale timestamp; on the first day-2 breaching tick
        # (now - breaches[en]) is ~hours >> the confirm window, firing an INSTANT
        # stop that BYPASSES the anti-spike gate. This is newly reachable now that
        # eod_close_if_no_transform=false lets a calendar span days (with same-day
        # close the EOD _dc_close_calendar popped the key the same session). The
        # market is closed overnight, so any in-flight confirm window is moot — start
        # each day fresh; a genuine day-2 breach simply re-confirms. Also clears any
        # orphaned key for an entry that settled (kept D pinned in 2s-vigilant).
        breach = getattr(self, "_dc_stop_breach", None)
        if breach:
            logger.info(
                "[CAL-CARRY] cleared %d stale stop-breach timer(s) at the new-day "
                "reset (fresh anti-spike confirmation each session).",
                len(breach),
            )
            breach.clear()

        if re_save_needed:
            # Re-persist now that daily_state.entries actually contains the
            # carried position again — see the comment above super()._reset_
            # for_new_day() for why the base reset's own save can't be
            # trusted to have captured it.
            self._save_state_to_disk()

    def _had_trading_activity_today(self) -> bool:
        """BUG (found 2026-08-18, production incident on D): the base check's
        `len(daily_state.entries) > 0` condition is always true for as long
        as ANY multi-day position is held, since a carried entry re-attaches
        to daily_state.entries on every reset (see _reset_for_new_day above)
        — it can never distinguish "holding a position, nothing new today"
        from "something actually happened today". Observed: at 00:00:36 ET
        on 2026-08-18, main.py's had_trading_activity check (backed by this
        method) read True purely because D's Aug-13 carried calendar was in
        daily_state.entries, seconds after the midnight reset and before any
        trading could possibly occur — triggering a phantom EOD daily-summary
        send that booked the position's stale unrealized mark from the reset
        moment into hydra_metrics.json as a "realized" result for a day that
        hadn't started, corrupting D's cumulative track record.

        Require genuinely-new evidence instead: a real close/settlement
        booked today (total_realized_pnl != 0, unchanged from the base), or
        at least one entry actually OPENED today (by entry_time's date) —
        excludes carried-only entries from counting as "today's activity"."""
        if self.daily_state.entries_completed > 0 or self.daily_state.total_realized_pnl != 0:
            return True
        today = get_us_market_time().date()
        return any(
            getattr(e, "entry_time", None) is not None and e.entry_time.date() == today
            for e in self.daily_state.entries
        )

    def _calculate_capital_deployed(self) -> float:
        """Capital at risk for a calendar variant = sum of OPEN calendars' net
        debit (pre-transform the max loss IS the debit; post-risk-free-transform
        it's ~0). The base method uses spread_width × 100 × contracts — for a
        same-strike calendar that is just the wing notional (e.g. $500), LESS than
        the debit actually paid ($1035), so the heartbeat Capital/Return were
        computed off the wrong base."""
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

    def _calculate_max_loss_catastrophic(self) -> float:
        """The calendar variant's worst case if the stop fails = the full debit
        paid (a long calendar cannot lose more than its debit). Risk-free
        TRANSFORMED legs excluded."""
        return self._dc_open_debit_at_risk()

    def get_detailed_position_status(self) -> List[str]:
        """Calendar-native per-entry heartbeat lines. A calendar variant holds
        net-DEBIT double calendars, so the inherited IC-style line
        (Credit/cushion/SV) is garbage for it ('Credit $0', '-469% cushion', an SV
        that ignores the debit). Render the calendar's real economics: phase,
        strikes, both expiries, debit, current liquidation value, and unrealized
        P&L as % of debit."""
        lines: List[str] = []
        for entry in self.daily_state.active_entries:
            if not isinstance(entry, CalendarEntry):
                continue  # a calendar variant only holds calendars; skip anything unexpected
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
        open). A calendar position held across days would jam that gate forever.
        Here: if the strategy is holding open multi-day positions, report
        settled-for-today so the daily summary proceeds.

        When there are NO open multi-day positions, settlement is simply done —
        `_dc_settle_due` (called just above) is this class's own, complete
        settlement mechanism for anything that needed it (the transformed-IC /
        leftover-calendar PM-close case). There is deliberately no fallback to
        `super().check_after_hours_settlement()` here (found missing, 2026-08-28
        audit): the base HydraStrategy version eventually reaches
        `_process_expired_credits()`, which iterates `self.daily_state.entries`
        assuming HydraIronCondorEntry-shaped objects (`entry.call_only` etc.) —
        attributes a `CalendarEntry` never has (it subclasses the plain
        `IronCondorEntry`, not `HydraIronCondorEntry`). The instant a calendar
        variant's only tracked entry settles (leaving `daily_state.entries`
        non-empty but `open_md` empty), every off-hours heartbeat hit that
        AttributeError — caught by main.py's broad try/except so it never took
        the bot down, but it fired every ~15min all night and logged a
        misleading "positions still open" line when nothing was open.
        """
        # Phase 5: settle any position whose SHORT expiry has arrived (the
        # transformed IC / leftover calendar settles at the SPXW PM close), then
        # treat the rest as normally-held multi-day positions.
        today = get_us_market_time().strftime("%Y-%m-%d")
        self._dc_settle_due(today)

        open_md = [e for e in self.daily_state.entries if self._dc_entry_is_open(e)]
        if open_md:
            logger.info(
                "[CAL-HOLD] %d multi-day position(s) held past the close — normal for a "
                "calendar, not pending settlement; daily summary may proceed.",
                len(open_md),
            )
        return True

    def get_daily_summary(self) -> dict:
        """Daily summary with the IC credit-vertical breakdown zeroed for a
        calendar variant.

        The base summary derives ``expired_credits`` from each entry's
        call/put_spread_credit and ``stop_loss_debits`` from the credit-kept
        identity — both meaningless for a net-DEBIT calendar (the calendar strategy
        books realized P&L directly into total_realized_pnl at
        transform/stop/settlement). Left as-is, a settled CalendarEntry's
        transform-time credit would surface as a bogus expired-credit. Zero those
        two IC-specific fields and report the calendar's realized P&L from
        total_realized_pnl; net_pnl/total_pnl are already debit-correct (they
        derive from total_realized_pnl + unrealized)."""
        summary = super().get_daily_summary()
        summary["expired_credits"] = 0.0
        summary["stop_loss_debits"] = 0.0
        summary["realized_pnl"] = self.daily_state.total_realized_pnl
        return summary

    def _cumulative_tracking_pnl(self, summary: dict, net_pnl: float) -> float:
        """A carried calendar's net_pnl re-includes the still-open position's
        live unrealized mark on every day it's held (see MEICStrategy's
        docstring for the full mechanism) — book $0 into the LIFETIME cumulative
        tracking on a pure hold day; the real close P&L only on the day it
        actually closes, since total_realized_pnl is 0 until a genuine
        close/stop/settlement posts to it. The live unrealized mark itself is
        still shown correctly elsewhere (alerts, the dashboard's intraday P&L
        curve) — only the LIFETIME tracking is re-scoped to realized-only here.
        """
        return self.daily_state.total_realized_pnl - self.daily_state.total_commission

    # ------------------------------------------------------------------
    # Two-expiry data layer (Phase 2) — pick expiries, resolve per-expiry
    # conids, read per-expiry quotes + IV. Thin wrappers over the existing
    # broker-data methods called with EXPLICIT (non-0DTE) expiries; the pure
    # expiry-selection logic lives in calendar_chain.
    # ------------------------------------------------------------------

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
                "[CAL-LEGS] unresolved conids %s (C %s / P %s, short %s / long %s)",
                missing, call_strike, put_strike, short_expiry, long_expiry,
            )
            return None
        return legs

    def _dc_strikes_for_expiry(self, expiry_iso: str) -> set:
        """The set of listed strikes (floats) for ONE expiry. Used to snap a
        calendar's strikes to the INTERSECTION of strikes available on BOTH the
        short and long expiries — monthlies and weeklies don't share the same
        far-OTM grid (e.g. a $1-grid expected-move strike on the weekly isn't on
        the $5-grid monthly), so a fixed-grid round is often unlisted on one leg
        and the whole entry skips (the 06-17/06-18 SPY skips). Empty set on any
        failure → the caller falls back / skips that long."""
        from datetime import date as _date
        try:
            chain = self.broker.get_option_chain(
                self.underlying_symbol, _date.fromisoformat(expiry_iso),
                trading_class=self.trading_class, exchange=self.exchange,
            )
        except Exception as exc:  # noqa: BLE001 — never let a chain fetch crash entry
            logger.warning("[CAL-STRIKES] chain fetch failed for %s: %s", expiry_iso, exc)
            return set()
        raw = chain if isinstance(chain, list) else (
            chain.get("strikes", []) if isinstance(chain, dict) else []
        )
        out = set()
        for s in raw:
            try:
                out.add(round(float(s), 2))
            except (TypeError, ValueError):
                continue
        return out

    def _dc_read_iv(self, conid: int) -> Optional[float]:
        """Per-option implied vol from the broker greeks snapshot. Returns None
        (NOT 0.0) on a missing/None/non-positive IV so a flaky read reads as
        'no signal', never 'IV=0'."""
        try:
            g = self.broker.get_option_greeks(conid) or {}
        except Exception as exc:
            logger.warning("[CAL-IV] greeks read failed for conid %s: %s", conid, exc)
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
                "[CAL-IV] term-structure unavailable (front=%s back=%s) — no signal",
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

    def _resolve_dc_fill_model(self, cfg: dict, sub: dict) -> None:
        """Resolve the dry-run fill model → set self._dc_fill_agg / _dc_fill_slippage.

        Tolerant of BOTH the config LOCATION and FORMAT an operator might reasonably
        use (2026-07-20 fix):
          * location — the strategy SUB-block (``strategy.<variant>.dry_run_fill_model``,
            where the DTE/wing/trigger knobs live) is checked FIRST, then the strategy
            level (``strategy.dry_run_fill_model``). The sub-block wins if both are set.
          * format — a bare SCALAR (interpreted as the aggressiveness) OR a dict
            ``{"aggressiveness", "extra_slippage_per_leg"}``.

        ``aggressiveness``: 0.0 = mid, 1.0 = full touch (buy@ask / sell@bid). Defaults
        to 1.0 (the honest worst case) when unset. ALWAYS logged so the resolved value
        can never be silently wrong again — the prior inline read only accepted a dict
        at the strategy level, so a scalar written under the sub-block (as D's
        ``double_calendar.dry_run_fill_model: 0.5`` was) was silently ignored and D ran
        at full-touch 1.0 despite the configured 0.5.
        """
        raw = sub.get("dry_run_fill_model")
        if raw is None:
            raw = cfg.get("dry_run_fill_model")
        if isinstance(raw, dict):
            agg = float(raw.get("aggressiveness", 1.0))
            slip = float(raw.get("extra_slippage_per_leg", 0.0))
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            agg = float(raw)
            slip = 0.0
        else:
            agg = 1.0
            slip = 0.0
        self._dc_fill_agg = agg
        self._dc_fill_slippage = slip
        logger.info(
            "[CAL] dry-run fill model: aggressiveness=%.2f (0=mid, 1=touch), "
            "slippage=$%.2f/leg",
            agg, slip,
        )

    def _dc_fill_price(self, qrow: dict, action: str) -> Optional[float]:
        """Realistic dry-run fill for ONE leg — model the bid/ask spread instead of
        the optimistic MID (2026-06-17). A real marketable order BUYS toward the ASK
        and SELLS toward the BID; ``aggressiveness`` (config dry_run_fill_model,
        default 1.0 = full touch) scales how far across the half-spread, and
        ``extra_slippage_per_leg`` adds a fixed per-leg buffer for wide/fast markets.

        Without this, entry debits / transform credits / liquidations were all priced
        at the mid, which made D's transform look risk-free far cheaper and faster
        than it would be after crossing 4–6 real spreads (the "too good to be true"
        artifact). Falls back to the mid when a side is missing. ``action`` is
        ``"buy"`` or ``"sell"``. Returns None if there is no usable mid.
        """
        mid = qrow.get("mid")
        if mid is None or mid <= 0:
            return None
        raw = qrow.get("raw") or {}
        bid, ask = raw.get("bid"), raw.get("ask")
        agg = float(getattr(self, "_dc_fill_agg", 1.0))
        slip = float(getattr(self, "_dc_fill_slippage", 0.0))
        if action == "buy":
            px = mid + agg * (ask - mid) if (ask and ask >= mid) else mid
            px += slip
        else:  # sell
            px = mid - agg * (mid - bid) if (bid and 0 < bid <= mid) else mid
            px -= slip
        return max(0.0, px)

    def _dc_quote_is_realtime(self, q: dict) -> bool:
        """True unless the broker DEFINITIVELY flags the quote as delayed/frozen.
        Wraps HYDRA's _option_quote_is_realtime (IBKR field 6509); defaults to
        True when the check is unavailable/errors so a missing entitlement signal
        never freezes the calendar strategy's stop/transform entirely — it only
        rejects a quote we can positively confirm is non-real-time."""
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
            logger.warning("[CAL-PROBE] no long candidate for short %s", short_exp)
            return False
        long_exp = longs[0]
        spx = self.current_price
        if not spx or spx <= 0:
            logger.warning("[CAL-PROBE] no SPX price — cannot probe")
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
            "[CAL-PROBE] ATM %s | short_call mid=%.2f long_call mid=%.2f | front/back IV=%s | %s",
            atm, quotes["short_call"]["mid"], quotes["long_call"]["mid"],
            iv_call, "OK" if ok else "INCOMPLETE (entitlement/warmup?)",
        )
        return ok

    # ------------------------------------------------------------------
    # Entry + dry-run simulation (Phase 3)
    # Pick the 30-40delta two-expiry strikes, open the net-DEBIT double calendar,
    # and SIMULATE the fills from REAL mids (dry-run: no broker order, synthetic
    # DRY ids). Overridden (not inherited) so a calendar variant NEVER runs
    # HYDRA's IC entry.
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
            logger.warning("[CAL] incomplete mids %s — cannot price calendar", mids)
            return False
        n = self.contracts_per_entry
        # Realistic OPEN fills (2026-06-17): BUY the longs toward the ask, SELL the
        # shorts toward the bid — you pay the spread opening, just like a real
        # marketable order (full-touch by default). open_fill = the cost basis;
        # mark_fill = the immediate LIQUIDATION value (you'd pay the spread again to
        # close), so the position starts down the round-trip cost, not flat-at-mid.
        open_fill = {
            "long_call": self._dc_fill_price(quotes["long_call"], "buy"),
            "long_put": self._dc_fill_price(quotes["long_put"], "buy"),
            "short_call": self._dc_fill_price(quotes["short_call"], "sell"),
            "short_put": self._dc_fill_price(quotes["short_put"], "sell"),
        }
        mark_fill = {
            "long_call": self._dc_fill_price(quotes["long_call"], "sell"),
            "long_put": self._dc_fill_price(quotes["long_put"], "sell"),
            "short_call": self._dc_fill_price(quotes["short_call"], "buy"),
            "short_put": self._dc_fill_price(quotes["short_put"], "buy"),
        }
        if any(p is None or p <= 0 for p in open_fill.values()):
            logger.warning("[CAL] incomplete fills %s — cannot price calendar", open_fill)
            return False
        # Net DEBIT = cost of longs (bought at ask) - credit from shorts (sold at bid).
        net_debit = (
            open_fill["long_call"] + open_fill["long_put"]
            - open_fill["short_call"] - open_fill["short_put"]
        ) * 100 * n
        # A double calendar is a net-DEBIT structure by definition. An inverted
        # term structure (shorts richer than longs) yields net_debit <= 0 — that
        # is NOT this strategy, and it would also break pnl_pct = pnl/net_debit
        # and leave the position unmanaged. Skip it rather than open an anomaly.
        if net_debit <= 0:
            logger.warning(
                "[CAL] skipping entry #%s — net credit/zero (%.2f); a calendar must be a net debit",
                entry.entry_number, net_debit,
            )
            return False
        base_id = int(get_us_market_time().timestamp() * 1000)
        for name, conid in leg_conids.items():
            leg = entry.legs[name]
            leg.uic = conid
            leg.fill_price = open_fill[name]   # entry cost basis (spread-crossed)
            leg.price = mark_fill[name]        # immediate liquidation mark (round-trip cost)
            leg.position_id = f"DRY_{base_id}_{_DC_LEG_ABBR[name]}"
        entry.net_debit = net_debit
        entry.dc_phase = DCPhase.CALENDAR
        entry.contracts = n
        # Capture the OPENING liquidation mark now (legs' `price` are the
        # round-trip-cost marks set above), so decision triggers measure movement
        # FROM here — a fresh calendar reads 0% move, not the false ~-20% birth
        # spread that stopped D out same-day (2026-07-02 calendar audit).
        entry.opening_pnl = entry.unrealized_pnl
        # Stamp the (config) wing width at OPEN so spread_width / capital_deployed
        # are non-zero in the CALENDAR phase (it's the planned IC wing, known
        # upfront; the transformer uses the same value).
        entry.wing_width = self.dc_wing_width
        entry.is_complete = True
        logger.info(
            "[CAL-OPEN] entry #%s DEBIT $%.2f | Kc=%s Kp=%s | short=%s long=%s | %dc",
            entry.entry_number, net_debit, entry.short_call_strike,
            entry.short_put_strike, short_exp, long_exp, n,
        )
        return True

    # ------------------------------------------------------------------
    # Transformer + risk controls (Phase 4) — calendar refresh + EOD cutoff
    # plumbing shared with the subclass's transformer / stop manager.
    # ------------------------------------------------------------------

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
        quotes = self._dc_read_leg_quotes(conids)
        require_rt = getattr(self, "dc_require_realtime_quotes", True)
        # Price each re-quoted leg at its LIQUIDATION fill (2026-06-17): to close NOW
        # you SELL the longs (toward bid) and BUY BACK the shorts (toward ask), so
        # calendar_value/unrealized_pnl and every value-driven decision (profit-take
        # ladder, %-stop, transform trigger, EOD close P&L) stay honest about the
        # spread instead of mid-optimistic. Compute candidates first, commit
        # all-or-nothing after the sanity guard.
        candidate: dict = {}
        for name in conids:
            qrow = quotes.get(name) or {}
            mid = qrow.get("mid")
            rt_ok = qrow.get("realtime", True) or not require_rt
            if mid and mid > 0 and rt_ok:
                action = "buy" if name.startswith("short") else "sell"
                candidate[name] = self._dc_fill_price(qrow, action) or mid
        # A missing/crossed/stale leg → not fresh; leave the prior price so a frozen
        # leg can't trip the % stop on bad data.
        if len(candidate) != len(conids):
            return False
        # MARK-SANITY GUARD (2026-07-10): a fast tape can hand back a stale/crossed
        # leg mid that is individually POSITIVE but STRUCTURALLY IMPOSSIBLE — the
        # further-dated LONG priced BELOW the same-strike near-dated SHORT, a
        # calendar-arbitrage violation that drives the calendar value NEGATIVE. On
        # 07-10 D's long-call mid dropped to 15.0 vs the short-call 24.0 → a -$2,550
        # calendar value (-356% of a defined-risk debit) → a phantom -20% stop booked
        # at -$797. Reject the whole tick (KEEP the prior good marks, return
        # not-fresh) so no stop/transform/close acts on it; the next clean tick
        # resumes. A REAL adverse move keeps long >= short, so genuine stops fire.
        #
        # ONLY VALID PRE-TRANSFORM (2026-09-03 fix): "further-dated long >= near
        # short" only holds while the two legs have DIFFERENT expiries (the
        # CALENDAR phase this guard was built for). Once TRANSFORMED, both call
        # legs share the SAME expiry and both put legs share the SAME expiry —
        # the position is now a same-expiry debit vertical, where the long strike
        # being FURTHER OTM than the short strike legitimately prices it LOWER,
        # every single tick. Applying this check unconditionally rejected 1,138
        # consecutive real, correct marks for D's transformed dctm_20260901_001
        # across the full 2026-09-02 session (found in a routine daily audit) —
        # the position's live P&L was effectively frozen all day, not because of
        # bad data, but because this guard didn't know the position had already
        # transformed.
        _CAL_ARB_EPS = 0.10
        if (getattr(entry, "dc_phase", None) == DCPhase.CALENDAR
                and all(k in quotes for k in ("long_call", "short_call", "long_put", "short_put"))):
            call_side = (quotes["long_call"].get("mid") or 0) - (quotes["short_call"].get("mid") or 0)
            put_side = (quotes["long_put"].get("mid") or 0) - (quotes["short_put"].get("mid") or 0)
            if call_side < -_CAL_ARB_EPS or put_side < -_CAL_ARB_EPS:
                logger.warning(
                    "[CAL] REJECTED impossible mark for E#%s — a calendar side is "
                    "negative (call long-short=%.2f, put=%.2f): the further-dated "
                    "long cannot be worth less than the near short. Keeping prior "
                    "marks (crossed/stale quote on a fast tape).",
                    entry.entry_number, call_side, put_side,
                )
                return False
        for name, px in candidate.items():
            entry.legs[name].price = px
        return True

    def _dc_past_eod_cutoff(self) -> bool:
        return get_us_market_time().time() >= self.dc_eod_cutoff

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
        # Route through _book_realized_pnl(entry=...) so entry.realized_pnl is
        # populated (per-entry attribution), not just the day aggregate. Booking
        # directly to total_realized_pnl left entry.realized_pnl at 0.0 and tripped
        # the settlement RECONCILE drift guard (2026-07-06 D: sum $0 != total -$340);
        # it also blocked per-entry/slot analytics on the calendars.
        self._book_realized_pnl(pnl, entry=entry)
        self.daily_state.total_commission += close_comm
        entry.close_commission = close_comm
        entry.dc_phase = DCPhase.CLOSED
        if loss:
            entry.call_side_stopped = entry.put_side_stopped = True
        else:
            entry.call_side_pivot_closed = entry.put_side_pivot_closed = True
        self._dc_clear_leg_conids(entry)
        tag = "CAL-STOP" if loss else "CAL-EOD-CLOSE"
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
    # A calendar variant owns its persistence via a SIDECAR (the Brandon-hedge
    # precedent) so the 0DTE base save/load stays byte-identical: super() writes
    # the base state file unchanged; the sidecar carries the multi-day fields
    # (dc_phase, expiries, debit, transform) the fixed IC schema can't, and lets a
    # calendar opened on a PRIOR day be re-adopted (the base date!=today guard
    # drops it).
    # ------------------------------------------------------------------

    def _dc_state_path(self) -> str:
        """Sidecar path next to the variant state file (data/variant_<letter>/...,
        d for D, e for E)."""
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
            "opening_pnl": getattr(e, "opening_pnl", 0.0),
            "transform_credit": e.transform_credit,
            "wing_width": e.wing_width,
            "is_risk_free": e.is_risk_free,
            "transformed_at": e.transformed_at,
            "is_complete": e.is_complete,
            "call_spread_credit": e.call_spread_credit,
            "put_spread_credit": e.put_spread_credit,
            # Live MTM (computed from the CURRENT leg prices, phase-aware) so the
            # sidecar-backed dashboard/Telegram view shows a real-time mark — esp.
            # for TRANSFORMED positions, which hold to expiry and would otherwise
            # render a frozen transform-time value. Output-only; deserialize
            # recomputes from the legs and ignores these keys.
            "unrealized_pnl": round(e.unrealized_pnl, 2),
            "pnl_pct": (round(100.0 * e.unrealized_pnl / e.net_debit, 1)
                        if e.net_debit else None),
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
        e.opening_pnl = float(d.get("opening_pnl", 0.0))
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
            logger.error("[CAL] sidecar save failed: %s", exc)

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
            logger.error("[CAL] sidecar read failed: %s", exc)
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
                logger.error("[CAL] could not rebuild calendar from sidecar: %s", exc)
        if adopted:
            logger.info("[CAL-RECOVER] re-adopted %d multi-day calendar(s) from sidecar", adopted)
        return adopted > 0

    def _save_state_to_disk(self):
        """Base state file (unchanged) + the calendar sidecar with the multi-day fields."""
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
            logger.warning("[CAL-SETTLE] %d position(s) due but SPX unreadable — deferring", len(due))
            return False
        for e in due:
            self._dc_settle_entry(e, spx)
        # Persist BOTH the base state file (realized P&L now in total_realized_pnl)
        # and the sidecar (settled calendars removed) — not just the sidecar.
        self._save_state_to_disk()
        return True

    def _record_heartbeat_to_db(self):
        """Record the market tick (generic SPX/VIX) + the calendar variant's
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
                logger.debug("CAL heartbeat tick failed: %s", e)
        if getattr(self, "_dc_recorder", None):
            for entry in self.daily_state.active_entries:
                if isinstance(entry, CalendarEntry):
                    try:
                        self._dc_recorder.record_snapshot(entry, timestamp)
                    except Exception as e:
                        logger.debug("CAL snapshot failed: %s", e)
