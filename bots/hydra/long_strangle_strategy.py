"""LongStrangleStrategy — a 0DTE SPX **LONG** strangle (variant H). Complete but unrun.

The structural mirror of ``StrangleStrategy`` (variant G): the same two legs, the
opposite sign. G **sells** an OTM call and an OTM put; this **buys** them. That one
difference inverts almost everything that matters:

  - **NET DEBIT, not credit.** Premium is paid, not collected.
  - **LONG gamma, SHORT theta.** It profits from a large move in either direction
    and bleeds when the market sits still — the exact opposite of A/B/C/F/G, all
    of which are premium sellers, and of D/E, which are net debit but theta-POSITIVE.
  - **Max loss is the debit, by construction.** There is no stop-loss, because
    there is nothing to stop out of: the worst case is both legs expiring
    worthless. No GUARD-FLOOR, no A2 %-of-width, no MKT-046 anti-spike, no buffer
    decay. This is the only strategy in the fleet whose risk is bounded without
    any machinery at all.
  - **Naked-short exposure is structurally impossible.** A partial fill leaves a
    LONG option, never an unhedged short. The entry-execution failure that cost
    variant B $159.50 on 2026-09-22 cannot occur in this structure.

WHY IT EXISTS. Not because its source's numbers are believed — they are not; see
``docs/LONG_STRANGLE_STRATEGY_SPECIFICATION.md`` §1, where 80% winners at
+50–100% against losers at −100% works out to roughly +40% expected per trade,
which would be the best documented edge in retail options. It exists to MEASURE
something the fleet currently cannot answer: **does long gamma hedge the
short-gamma book's bad days?** 2026-09-21 (SPX +1.1%, B's worst live session at
−$441/contract) and 2026-09-22 (a 20-point range, B positive) are the two sides
of that question.

BUILD STATUS — **Steps 1–5 + 7 of ``docs/NEW_STRATEGY_PLAYBOOK.md``.** The strategy is
functionally complete in dry-run: expected-move strike selection, sizing-for-zero, a
``_simulate_entry`` booking synthetic DRY fills, a percent-of-debit profit target, and
settlement at intrinsic — all persisted to the isolated ``long_strangle.db``.

**It remains dry-run-LOCKED, and it has never run even in dry-run.** Steps 8–10
(observability, hardening, the go-live audit) are outstanding. Three independent locks
keep a real order off the wire: ``__init__`` refuses a non-dry-run construction,
``_execute_entry`` refuses rather than falling through to the base's 4-leg iron-condor
placement (which would SELL two short legs this strategy does not have), and
``_close_long_strangle`` refuses a live close.

Every exit here is the sign-mirror of the credit family and none of them could be
inherited. The two that would have failed SILENTLY are worth naming at the top of the
file: the base's settlement booking would have recorded a strangle expiring worthless
as **break-even** rather than a total loss of the premium (see
``_settlement_booked_pnl``), and the base's ``credit + buffer`` stop would have produced
an arbitrary trigger nobody chose (see ``_calculate_stop_levels_hydra``).

**Go-live gate and the NO-GO reasoning:**
``docs/migration/H_GOLIVE_SCOPE_AND_AUDIT.md`` (HG-1..HG-10). The next step for this
strategy is **not** a flip — it is to run the dry run at all, which it never has.

Spec + build-weight decision: ``docs/LONG_STRANGLE_STRATEGY_SPECIFICATION.md``
(MEDIUM build — skip Step 6, include a small Step 2 model and a Step 7 isolated DB,
because ``IronCondorEntry``'s P&L is credit-shaped in every branch and
``trade_entries`` has no debit columns).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple

from bots.hydra.base_strategy import ConfigError, MEICState
from bots.hydra.long_strangle_chain import (
    expected_move_from_straddle,
    expected_move_from_vix,
    iv_percentile,
    premiums_are_balanced,
    select_strangle_strikes,
    size_for_zero,
    snap_to_chain,
)
from bots.hydra.long_strangle_entry import LongStrangleEntry
from bots.hydra.ls_recorder import LongStrangleDataRecorder
from bots.hydra.strategy import DATA_DIR, HYDRA_VARIANT_ID, HydraStrategy
from shared.event_calendar import is_fomc_t_plus_one
from shared.market_hours import get_us_market_time

logger = logging.getLogger(__name__)

#: Every strike is snapped to a listed one within this many points, or the entry
#: is skipped. Same tolerance ``_read_option_chain`` uses — half the widest
#: far-OTM SPXW spacing. A looser cap would let 7825 quietly resolve to 7700.
MAX_SNAP_DISTANCE = 25.0


class LongStrangleStrategy(HydraStrategy):
    """0DTE SPX long strangle: buy an OTM call + an OTM put at the expected move.

    Inherits HYDRA's scheduling / monitoring / state machinery. Like variant G it
    sets ``requires_protective_wings = False`` — but for the opposite reason. G
    sets it because its unhedged SHORTS are the intended position and must not be
    auto-closed as a naked-short emergency. Here there are no shorts at all: both
    legs are long, so the base's naked-short guard has nothing to act on either
    way. The flag is set so the shared safety path does not mistake a two-leg
    structure for half of a broken iron condor.
    """

    BOT_NAME = "LONGSTRANGLE"

    #: No shorts anywhere in this structure — see the class docstring for why this
    #: is the same flag as G's but not the same reasoning.
    requires_protective_wings = False

    #: The base heartbeat renders an "E1-EN: full IC" schedule line written for a
    #: 4-leg iron condor, which misdescribes a 2-leg structure. Cosmetic/log-only,
    #: mirroring StrangleStrategy and GhauriMeanReversion.
    _show_ic_schedule_in_heartbeat = False

    def __init__(self, *args, **kwargs):
        """Construct, then enforce the dry-run-only gate.

        Locked for a different reason from G's. G is locked because it carries
        UNDEFINED risk and arming it is a deliberate operator decision. This is
        locked because it has **never run** — entry and exits are written and
        unit-tested, but no tick of this code has touched a live chain, and
        Steps 8–10 (observability, hardening, the go-live audit) are outstanding.
        Its risk being bounded by construction is not a reason to arm code that
        has never executed.

        The kwarg is checked BEFORE ``super().__init__`` so an illegal live
        construction never reaches the base init's broker I/O. ``build_strategy``
        always passes ``dry_run`` as a kwarg.
        """
        if not kwargs.get("dry_run", False):
            raise ConfigError(
                "LongStrangleStrategy is dry-run-LOCKED: entry and exits are "
                "written but THIS CODE HAS NEVER RUN — not one tick against a "
                "live chain, verified on the VM 2026-09-23 (no unit, no data "
                "dir, inactive). The go-live gate HG-1..HG-10 and the reasons "
                "for the NO-GO are in "
                "docs/migration/H_GOLIVE_SCOPE_AND_AUDIT.md. Set dry_run=true, "
                "or do not select strategy.name='long_strangle'."
            )
        super().__init__(*args, **kwargs)
        # Defense-in-depth: re-check after super in case dry_run is derived
        # differently downstream (it should equal the kwarg).
        if not getattr(self, "dry_run", False):
            raise ConfigError(
                "LongStrangleStrategy resolved to dry_run=false after init — "
                "refusing to arm a strategy with zero observations. See "
                "docs/migration/H_GOLIVE_SCOPE_AND_AUDIT.md."
            )

        # Step 7's isolated DB, wired here in Step 4 because this is where rows
        # start existing. It is NOT the base's DataRecorder: `trade_entries` has
        # no column that can hold a debit, so writing H there would record a
        # strangle that cost nothing (see ls_recorder.py's module docstring).
        self.ls_recorder: Optional[LongStrangleDataRecorder] = None
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            self.ls_recorder = LongStrangleDataRecorder(
                os.path.join(DATA_DIR, "long_strangle.db")
            )
        except Exception as e:  # pragma: no cover - defensive; recorder is optional
            logger.warning("LongStrangle recorder unavailable (non-critical): %s", e)

        logger.info(
            "LongStrangleStrategy (variant H) constructed — Steps 1-5 + 7: entry, "
            "profit target and settlement wired; dry-run-LOCKED, never yet run."
        )

    # ==================================================================
    # Step 4 — config access
    # ==================================================================

    def _ls_config(self) -> dict:
        """The ``strategy.long_strangle`` block, or an empty dict.

        Every Step 4 knob lives under this one key so a reader can see the whole
        strategy-specific surface in one place, and so nothing here can collide
        with an iron-condor knob of the same name.
        """
        cfg = getattr(self, "strategy_config", {}) or {}
        return cfg.get("long_strangle", {}) or {}

    # ==================================================================
    # Step 4 — capital
    # ==================================================================

    def _min_buying_power_per_unit(self) -> float:
        """Capital per contract is the DEBIT PAID, not a margin requirement.

        Long options are **fully paid** — there is nothing to margin. The base's
        floor is width-derived (``max(call_width, put_width) x $100``), which for
        the IC family is correct and for H is meaningless: ``spread_width`` is
        0.0 by construction (Step 2), and the base would instead derive a 60-75pt
        IC width and demand $6,000-7,500 per contract for a position that costs a
        few hundred dollars. That would not fail loudly; it would just skip every
        entry on a small account and look like "no signal".

        The floor used instead is ``sizing_for_zero_max_loss`` — the most the
        strategy will ever commit to one entry, because sizing-for-zero caps
        total debit at exactly that number (see ``_size_for_zero``). Overridable
        via ``min_buying_power_per_long_strangle`` if an operator wants a wider
        margin of safety than the loss limit itself.

        Note the gate multiplies this by ``contracts_per_entry``, so at >1
        contract it demands MORE than the strategy can actually spend. That is
        deliberate: the floor is checked before strikes exist, so the true debit
        is unknowable at that point, and a capital gate should err high.
        """
        cfg = self._ls_config()
        default = float(cfg.get("sizing_for_zero_max_loss", 500.0) or 500.0)
        return float(cfg.get("min_buying_power_per_long_strangle", default))

    # ==================================================================
    # Step 4 — the expected move (which IS the strike choice)
    # ==================================================================

    def _chain_strikes(self, expiry: str) -> List[float]:
        """The listed strike grid for ``expiry``, or ``[]``.

        Goes to the broker's chain directly rather than through
        ``_read_option_chain``, which resolves conids for a *candidate* set — but
        the candidates are what we are trying to compute. One cheap call.
        """
        try:
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        except (ValueError, TypeError) as e:
            logger.warning("LS: bad expiry %r: %s", expiry, e)
            return []
        try:
            strikes = self.broker.get_option_chain(
                self.underlying_symbol, expiry_date,
                trading_class=self.trading_class, exchange=self.exchange,
            )
        except Exception as e:
            logger.warning("LS: chain fetch failed: %s", e)
            return []
        return [float(s) for s in (strikes or []) if s]

    def _expected_move(self, spot: float, expiry: str,
                       strikes: List[float]) -> Tuple[float, str]:
        """``(expected_move, source)`` — the number the strikes are built from.

        Config-driven via ``long_strangle.expected_move_source``. The two
        definitions **disagree by roughly 3x** on a quiet day (2026-09-22:
        straddle ~22pt, VIX-implied ~71pt), and the expected move IS the strike
        choice, so this is not a tuning knob — it selects which strategy runs.

        **There is deliberately NO FALLBACK between them.** If the configured
        source cannot be computed — a missing ATM quote, a VIX of zero — this
        returns ``(0.0, source)`` and the caller skips the entry. Quietly
        substituting the other definition would place a 71pt-wide strangle while
        the config, the logs and the recorded ``em_source`` all said "straddle",
        and the resulting data would be two strategies blended into one series
        with no way to separate them afterwards.
        """
        source = str(self._ls_config().get("expected_move_source", "straddle")).lower()

        if source == "vix":
            vix = float(getattr(self, "current_vix", 0.0) or 0.0)
            mult = float(self._ls_config().get("expected_move_multiplier", 1.0))
            em = expected_move_from_vix(spot, vix, mult)
            if em <= 0:
                logger.warning("LS: VIX-implied expected move unavailable (VIX=%.2f)", vix)
            return em, "vix"

        if source != "straddle":
            logger.error(
                "LS: unknown expected_move_source %r — refusing to guess. "
                "Valid values: 'straddle', 'vix'.", source
            )
            return 0.0, source

        # Straddle: price the ATM call and put on the live chain.
        atm = snap_to_chain(spot, strikes, MAX_SNAP_DISTANCE)
        if atm is None:
            logger.warning("LS: no ATM strike within %.0fpt of spot %.2f",
                           MAX_SNAP_DISTANCE, spot)
            return 0.0, "straddle"
        call_uic, put_uic = self._resolve_pair(expiry, atm, atm)
        call_px, put_px = self._price_pair(call_uic, put_uic)
        em = expected_move_from_straddle(call_px, put_px)
        if em <= 0:
            logger.warning(
                "LS: ATM straddle at %.0f not priceable (call %.2f / put %.2f) — "
                "NOT substituting the VIX formula; skipping instead",
                atm, call_px, put_px,
            )
        else:
            logger.info("LS: expected move %.1fpt from the %.0f straddle "
                        "(call %.2f + put %.2f)", em, atm, call_px, put_px)
        return em, "straddle"

    def _resolve_pair(self, expiry: str, call_strike: float,
                      put_strike: float) -> Tuple[Optional[int], Optional[int]]:
        """``(call_conid, put_conid)`` in ONE chain round-trip.

        ``_get_option_uic`` resolves a SINGLE right and internally does a full
        ``_read_option_chain`` — a chain fetch plus a qualify — so calling it
        per leg costs two broker round-trips each. ``_read_option_chain``
        already returns both maps, so one call resolves both rights.

        This is a Step 9 (hardening) change, and on this fleet it is a
        **correctness** concern rather than a tidy-up: every strategy proxies
        through the ONE shared ``calypso-broker`` session, so a read-heavy entry
        burst on a dry-run variant adds latency to **live B**. The playbook is
        explicit that D had to be bounded before it could safely run beside a
        live variant.
        """
        call_map, put_map = self._read_option_chain(
            expiry, [float(call_strike), float(put_strike)])
        return call_map.get(float(call_strike)), put_map.get(float(put_strike))

    def _price_pair(self, call_uic, put_uic) -> Tuple[float, float]:
        """``(call_mid, put_mid)`` in ONE batch quote, in option points.

        Routes through ``_quote_mid``, which carries the L-M7 crossed-quote
        guard (never averages a bid>ask book) and the mid → last → mark
        fallback. The mirror of G's ``_estimate_short_premium``; the arithmetic
        is identical because a mid is a mid — only what we do with it differs in
        sign. Batched for the same shared-broker reason as ``_resolve_pair``.
        """
        ids = [u for u in (call_uic, put_uic) if u]
        if not ids:
            return 0.0, 0.0
        quotes = self._read_option_quotes_batch(ids) or {}
        return (float(self._quote_mid(quotes.get(call_uic)) or 0.0),
                float(self._quote_mid(quotes.get(put_uic)) or 0.0))

    # ==================================================================
    # Step 4 — strike selection
    # ==================================================================

    def _calculate_strikes(self, entry) -> bool:
        """Pick both long strikes at ``spot ± expected_move``, and price them.

        Also resolves conids and leg prices here rather than in
        ``_simulate_entry``, because the skew check and sizing-for-zero both need
        the premiums — quoting once and reusing is both cheaper and consistent
        (two quote rounds could disagree and produce a debit that never existed).

        Returns False on any refusal; the caller records the reason.
        """
        spot = self.current_price
        if spot <= 0:
            logger.error("LS: cannot calculate strikes — no SPX price")
            entry.ls_skip_reason = "no SPX price"
            return False

        expiry = self._get_todays_expiry()
        if not expiry:
            logger.error("LS: no expiry available")
            entry.ls_skip_reason = "no expiry"
            return False
        entry.expiry = expiry

        strikes = self._chain_strikes(expiry)
        if not strikes:
            entry.ls_skip_reason = "empty option chain"
            return False

        em, em_source = self._expected_move(spot, expiry, strikes)
        entry.ls_em_source = em_source
        entry.ls_expected_move = em
        if em <= 0:
            entry.ls_skip_reason = f"expected move unavailable ({em_source})"
            return False

        call_k, put_k = select_strangle_strikes(spot, em, strikes, MAX_SNAP_DISTANCE)
        if call_k is None or put_k is None:
            # One leg is a directional bet, which is not this strategy.
            logger.warning(
                "LS: chain could not supply both strikes within %.0fpt of "
                "%.2f ± %.1f (call=%s put=%s)",
                MAX_SNAP_DISTANCE, spot, em, call_k, put_k,
            )
            entry.ls_skip_reason = "chain could not supply both strikes"
            return False

        # STEP 9 FINDING. `select_strangle_strikes` refuses a non-positive
        # expected move because "spot +/- 0" is an ATM straddle — a materially
        # different and far more expensive position. It does NOT catch the same
        # thing happening through the SNAP: with 5pt strikes near the money, any
        # expected move under ~2.5pt rounds BOTH legs onto the same strike, and
        # a long strangle silently becomes a long straddle. That is reachable —
        # the ATM straddle collapses late in a quiet session, which is exactly
        # when this strategy is least likely to be watched.
        #
        # Refuse rather than adjust. Widening to the next strike out would
        # invent a position the expected move did not ask for; skipping records
        # the reason and leaves the decision visible.
        if call_k <= put_k:
            logger.warning(
                "LS: expected move %.1fpt collapsed both legs onto %.0f — that is "
                "an ATM straddle, not a strangle. Skipping.", em, call_k,
            )
            entry.ls_skip_reason = (
                f"expected move {em:.1f}pt too small — both legs snap to {call_k:.0f} "
                f"(a straddle, not a strangle)"
            )
            return False

        # H holds two LONGS. short_* stays 0.0 — the mirror of G (Step 2).
        entry.long_call_strike, entry.long_put_strike = call_k, put_k
        entry.short_call_strike = entry.short_put_strike = 0.0

        call_uic, put_uic = self._resolve_pair(expiry, call_k, put_k)
        if not call_uic or not put_uic:
            logger.warning("LS: conid resolution failed (call=%s put=%s)",
                           call_uic, put_uic)
            entry.ls_skip_reason = "conid resolution failed"
            return False
        entry.long_call_uic, entry.long_put_uic = call_uic, put_uic

        call_px, put_px = self._price_pair(call_uic, put_uic)
        if call_px <= 0 or put_px <= 0:
            # A zero premium here would become a zero debit, an infinite
            # sizing-for-zero count, and a position that appears free.
            logger.warning("LS: leg not priceable (call %.2f / put %.2f)",
                           call_px, put_px)
            entry.ls_skip_reason = "leg not priceable"
            return False
        entry.long_call_price, entry.long_put_price = call_px, put_px

        # The source's skew check. Equidistant strikes are NOT equally priced
        # (put skew); a large imbalance means the "strangle" is a directional
        # position wearing two legs.
        tol = float(self._ls_config().get("skew_tolerance_pct", 35.0))
        gap = abs(call_px - put_px) / max(call_px, put_px) * 100.0
        entry.ls_skew_gap_pct = gap
        if not premiums_are_balanced(call_px, put_px, tol):
            logger.info(
                "LS: skew veto — call %.2f vs put %.2f is %.1f%% apart "
                "(tolerance %.1f%%)", call_px, put_px, gap, tol,
            )
            entry.ls_skip_reason = (
                f"skew {gap:.1f}% > {tol:.1f}% tolerance"
            )
            return False

        # THE SOURCE'S COST CAP — his rule, added 2026-09-23 after re-reading the
        # article. He will not pay more than ~$1.15/share for a SPY strangle
        # (~$1.30-1.40 on QQQ). It is a QUALITY filter, distinct from
        # sizing-for-zero: that one decides HOW MANY to buy, this one refuses to
        # OVERPAY for the premium in the first place.
        #
        # ⚠️ THE PORT IS OURS, NOT HIS. He trades SPY and QQQ; we chose SPX to
        # match the fleet. $1.15 on a ~$775 SPY is 0.1483% of spot, so the same
        # fraction on SPX is ~$11.50/share ≈ $1,150/contract. Expressed as a
        # percentage so it tracks the index instead of going stale.
        #
        # At 1 contract with a $500 loss limit, sizing-for-zero binds first and
        # this cap never fires. It is implemented anyway because the two rules
        # are independent, and raising the loss limit later must not silently
        # remove his.
        cap_pct = float(self._ls_config().get("max_debit_pct_of_spot", 0.0))
        debit_ps = call_px + put_px
        if cap_pct > 0:
            cap_ps = spot * cap_pct / 100.0
            if debit_ps > cap_ps:
                logger.info(
                    "LS: debit $%.2f/share exceeds the source's cost cap $%.2f "
                    "(%.4f%% of spot %.0f) — too expensive, skipping",
                    debit_ps, cap_ps, cap_pct, spot,
                )
                entry.ls_skip_reason = (
                    f"debit ${debit_ps:.2f}/sh over the ${cap_ps:.2f} cost cap "
                    f"({cap_pct:.4f}% of spot)"
                )
                return False

        logger.info(
            "LS strikes: C %.0f (%.2f) / P %.0f (%.2f) — spot %.2f ± %.1fpt "
            "(%s), skew %.1f%%, debit $%.2f/sh",
            call_k, call_px, put_k, put_px, spot, em, em_source, gap, debit_ps,
        )
        return True

    # ==================================================================
    # Step 4 — the IV-percentile filter, deliberately OFF
    # ==================================================================

    def _iv_percentile_gate(self, entry) -> Optional[str]:
        """The source's "IV percentile below ~35%" filter. **Default: disabled.**

        Not disabled out of laziness — disabled because **this repo has no honest
        input for it** (spec assumption 2). Nothing stores option-IV history;
        ``market_ticks`` keeps VIX, which is a 30-day *index* vol and NOT the IV
        of the specific 0DTE options being bought. The Step 3 probe's job is to
        establish what series actually exists.

        So the gate is built and wired, and it **fails closed**: enabling it
        without naming a series skips every entry with an explicit reason, rather
        than passing everything and looking like the filter is working. A
        premature flip is therefore immediately visible in the logs instead of
        silently doing nothing.

        Returns a skip reason, or None to proceed.
        """
        cfg = self._ls_config()
        if not cfg.get("iv_percentile_filter_enabled", False):
            return None

        source = str(cfg.get("iv_percentile_source", "") or "").strip().lower()
        if not source:
            return ("iv_percentile filter enabled but no iv_percentile_source is "
                    "wired — see spec assumption 2 (probe Q2)")
        if source != "vix":
            return f"iv_percentile_source {source!r} is not implemented"

        # VIX percentile, named as such. It is a proxy and the recorded reason
        # says so, so no later analysis can mistake it for an option-IV
        # percentile.
        history = self._vix_history_for_percentile()
        pct = iv_percentile(float(getattr(self, "current_vix", 0.0) or 0.0), history)
        entry.ls_iv_percentile = pct
        if pct is None:
            return "VIX-percentile proxy unavailable (empty history)"
        max_pct = float(cfg.get("iv_percentile_max", 35.0))
        if pct > max_pct:
            return (f"VIX-percentile proxy {pct:.0f}% > {max_pct:.0f}% max "
                    f"(NOT an option-IV percentile)")
        return None

    def _vix_history_for_percentile(self) -> List[float]:
        """One VIX close per prior trading day, oldest first, for the percentile.

        WIRED 2026-09-23. This returned ``[]`` — "unknown" — on the reasoning
        that the repo stores no option-IV history. **That was too absolutist.**
        The source is a retail trader reading "IV percentile" off a broker
        platform, and for SPX that number is derived from index-option implied
        vol — which is what VIX *is*. ``market_ticks`` has carried a VIX level
        on every heartbeat since 2026-05-05, so the filter the source specifies
        is computable, and running without it was the larger infidelity.

        WHAT IT IS NOT, said plainly so no later reader over-reads it: VIX is
        the **30-day** implied vol of SPX options, not the IV of the specific
        0DTE contracts being bought. The two move together but are not the same
        point on the term structure. Every skip this produces says so in its
        reason string.

        ⚠️ **The sample has never seen a high-vol regime.** As of wiring it spans
        ~93 trading days at VIX 13.95-22.59, so a "35th percentile" here is
        ranked against a calm-only history and means something different from
        the same number over a full year. It widens as the record grows; it does
        not become a different measure.

        Reads read-only, one row per day (the last tick of each session), and
        returns ``[]`` on any failure — the gate then treats that as "unknown"
        and skips, which is the fail-closed half of the design.

        ⚠️ IT DOES NOT READ H'S OWN DATABASE, and that was a real bug caught on
        H's first live tick (2026-09-23 09:49 ET): a brand-new variant's
        ``backtesting.db`` is EMPTY, so the gate skipped every entry and H would
        never have traded until it had accumulated its own 93 days — defeating
        the point of the filter. **VIX is a market-wide value, not a per-variant
        one**, so the history is read from whichever variant has been recording
        longest. See ``_vix_history_db``.
        """
        import sqlite3
        db = self._vix_history_db()
        if not db:
            return []
        lookback = int(self._ls_config().get("iv_percentile_lookback_days", 252))
        today = get_us_market_time().strftime("%Y-%m-%d")
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                # One value per PRIOR day: today's own ticks would rank the
                # current reading against itself and drag the percentile toward
                # the middle on every tick.
                rows = con.execute(
                    "SELECT vix_level FROM ("
                    "  SELECT date(timestamp) AS d, vix_level,"
                    "         ROW_NUMBER() OVER (PARTITION BY date(timestamp)"
                    "                            ORDER BY timestamp DESC) AS rn"
                    "  FROM market_ticks WHERE vix_level > 0 AND date(timestamp) < ?"
                    ") WHERE rn = 1 ORDER BY d DESC LIMIT ?",
                    (today, lookback),
                ).fetchall()
                return [float(r[0]) for r in reversed(rows) if r and r[0]]
            finally:
                con.close()
        except Exception as e:          # noqa: BLE001 — a filter must not break entry
            logger.warning("LS: VIX history unavailable for the IV percentile: %s", e)
            return []

    def _vix_history_db(self) -> Optional[str]:
        """Which ``backtesting.db`` to read VIX history from.

        **Not H's own.** VIX is market-wide, and a fresh variant's database is
        empty — which is exactly how the IV filter skipped H's very first entry.

        Resolution order, each step existing for a reason:

        1. ``iv_percentile_source_db`` from config, if an operator pinned one.
        2. The **live seat's** database, found through the taxonomy rather than
           hardcoded — CLAUDE.md is explicit that no surface should hardcode
           which variant is live, and that seat has moved once already (C→B).
        3. Variant A's root database, the longest-running recorder.
        4. H's own, which will be right eventually and is a harmless last resort.

        Returns the first path that exists, or None.
        """
        cfg_db = str(self._ls_config().get("iv_percentile_source_db", "") or "").strip()
        candidates = [cfg_db] if cfg_db else []

        root = os.path.dirname(DATA_DIR) if HYDRA_VARIANT_ID else DATA_DIR
        try:
            from shared import strategy_taxonomy as _tax
            for vid in _tax.available_ids():
                if _tax.STRATEGIES[vid].status == "live":
                    candidates.append(
                        os.path.join(root, f"variant_{vid}", "backtesting.db"))
        except Exception as e:  # pragma: no cover - taxonomy is always importable
            logger.debug("LS: taxonomy lookup for the VIX source failed: %s", e)

        candidates.append(os.path.join(root, "backtesting.db"))   # variant A
        candidates.append(os.path.join(DATA_DIR, "backtesting.db"))

        for c in candidates:
            if c and os.path.exists(c):
                return c
        return None

    # ==================================================================
    # Step 4 — sizing for zero
    # ==================================================================

    def _size_for_zero(self, entry) -> int:
        """Contracts, assuming the entire debit goes to zero.

        The source's rule, and the only one that makes sense for a position whose
        maximum loss is its cost: choose what you can afford to lose outright,
        then buy that many. ``size_for_zero`` floors at 0 rather than 1 — a
        caller that cannot afford one contract must place nothing, because
        rounding up would breach the very limit the rule exists to enforce.

        The fleet-wide safety caps (``contracts_per_entry``,
        ``max_contracts_per_order``) are applied as a MIN, so they can only ever
        reduce the count, never raise it above what the loss limit allows.
        """
        debit_per_contract = (entry.long_call_price + entry.long_put_price) * 100.0
        if debit_per_contract <= 0:
            return 0
        max_loss = float(self._ls_config().get("sizing_for_zero_max_loss", 500.0))
        affordable = size_for_zero(max_loss, debit_per_contract)

        # A cap that is PRESENT is honoured even at 0 — `or affordable` here
        # would turn a deliberate 0 into "no cap", which is the wrong direction
        # for a safety limit.
        caps = [int(self.contracts_per_entry or 0)]
        order_cap = getattr(self, "max_contracts_per_order", None)
        if order_cap is not None:
            caps.append(int(order_cap))

        capped = max(min(affordable, *caps) if caps else affordable, 0)
        logger.info(
            "LS sizing-for-zero: $%.0f limit / $%.2f per contract = %d, "
            "capped to %d by the fleet contract caps",
            max_loss, debit_per_contract, affordable, capped,
        )
        return capped

    # ==================================================================
    # Step 4 — pre-entry gates
    # ==================================================================

    def _long_strangle_pre_entry_gates(self, entry_num: int) -> Optional[str]:
        """Shared pre-entry gates; a skip/delay string if any blocks, else None.

        Mirrors G's ``_strangle_pre_entry_gates`` — the same helpers in the same
        order, so the gate LOGIC stays single-sourced — minus the IC-specific
        credit/conditional branches, which have no meaning for a debit structure.

        Two of these gates are worth noting because they read oddly for a long
        strategy and are kept on purpose:

        * **Orphaned orders** still blocks. H cannot create a naked short, but it
          shares an account with variants that can, and opening new positions
          while the account state is unreconciled is wrong regardless of sign.
        * **Whipsaw is DISABLED for H** (``whipsaw_range_skip_mult: null``), and
          this is the most consequential config choice in the variant. The
          filter skips an entry when the intraday range exceeds 1.75x the
          expected move. For a premium SELLER that is protective — a wild day is
          when a short position gets hurt. **H is the opposite: a wide range is
          the day it exists for**, so the inherited gate would systematically
          skip its best sessions and we would be measuring "the source's
          strategy minus its winners". The source specifies no such filter.

          It was briefly kept "so the gating matches the fleet and the data is
          comparable" — that was wrong. Comparability is worth nothing if the
          thing being compared has had its thesis filtered out.

        * **FOMC T+1 is also disabled** for the same reason: the day after an
          announcement is frequently a large-move day, and the source specifies
          no FOMC handling at all. A/B/C black it out because unresolved
          post-Fed drift hurts a short position; that reasoning does not
          transfer to a long one.

        The gate CALLS stay in place rather than being deleted, so the logic
        remains single-sourced with the rest of the fleet and both are one
        config value away from returning.
        """
        if self._has_orphaned_orders():
            logger.error("LS entry #%d blocked by orphaned orders", entry_num)
            self._next_entry_index += 1
            return f"Entry #{entry_num} skipped - orphaned orders blocking"

        is_halted, halt_reason = self._check_market_halt()
        if is_halted:
            # A halt is a DELAY (retry later), not a skip — do NOT advance.
            return f"Entry #{entry_num} delayed - {halt_reason}"

        has_bp, bp_message = self._check_buying_power()
        if not has_bp:
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            self._record_skipped_entry(entry_num, f"Insufficient capital: {bp_message}",
                                       send_alert=False)
            return f"Entry #{entry_num} skipped - {bp_message}"

        whipsaw_reason = self._check_whipsaw_filter()
        if whipsaw_reason:
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            self._record_skipped_entry(entry_num, whipsaw_reason, send_alert=True)
            return f"Entry #{entry_num} skipped - {whipsaw_reason}"

        if (self.fomc_t1_skip_enabled and is_fomc_t_plus_one()
                and not self._force_normal_day()):
            self.daily_state.entries_skipped += 1
            self._next_entry_index += 1
            self._record_skipped_entry(entry_num, "FOMC T+1 blackout", send_alert=True)
            return f"Entry #{entry_num} skipped - FOMC T+1 blackout"

        return None

    # ==================================================================
    # Step 4 — orchestration
    # ==================================================================

    def _initiate_entry(self) -> str:
        """Long-strangle entry: gates → strikes → IV gate → size → simulate → book.

        Simpler than the IC path by construction: there is no credit gate (nothing
        is sold), no one-sided conversion (one leg is a directional bet, not this
        strategy), no conditional E6, and no stop levels to compute.
        """
        entry_num = self._next_entry_index + 1
        logger.info("LONGSTRANGLE: initiating entry #%d", entry_num)

        gate = self._long_strangle_pre_entry_gates(entry_num)
        if gate is not None:
            return gate

        self._entry_in_progress = True
        self.state = MEICState.ENTRY_IN_PROGRESS
        try:
            entry = LongStrangleEntry(entry_number=entry_num)
            # Structure discriminator so logging/analytics never label this an
            # Iron Condor with phantom 0.0 wings (item 6, as G does).
            entry.structure = "long_strangle"
            entry.contracts = self.contracts_per_entry
            entry.strategy_id = (
                f"long_strangle_{get_us_market_time().strftime('%Y%m%d')}_{entry_num:03d}"
            )

            if not self._calculate_strikes(entry):
                return self._skip(entry, entry_num,
                                  getattr(entry, "ls_skip_reason", "strike selection failed"))

            iv_skip = self._iv_percentile_gate(entry)
            if iv_skip:
                return self._skip(entry, entry_num, iv_skip)

            contracts = self._size_for_zero(entry)
            if contracts <= 0:
                return self._skip(
                    entry, entry_num,
                    "sizing-for-zero allows 0 contracts (debit exceeds the loss limit)",
                )
            entry.contracts = contracts

            if not self.dry_run:  # pragma: no cover - unreachable while locked
                raise ConfigError("LongStrangleStrategy cannot place live orders")
            if not self._simulate_entry(entry):
                self.daily_state.entries_failed += 1
                self._record_failed_entry(entry_num, "long strangle: simulation failed",
                                          used_retry_loop=False)
                self._next_entry_index += 1
                return f"Entry #{entry_num} failed - simulation unsuccessful"

            entry.entry_time = get_us_market_time()
            entry.is_complete = True
            self.daily_state.entries.append(entry)
            self.daily_state.entries_completed += 1
            # NOT total_credit_received — nothing was collected. The daily
            # credit accumulator stays at zero for H on purpose; the money paid
            # is in ls_entries.total_debit.
            entry.open_commission = 2 * self.commission_per_leg * entry.contracts
            self.daily_state.total_commission += entry.open_commission

            self._calculate_stop_levels_hydra(entry)   # documented no-op
            self._save_state_to_disk()                 # persist before logging
            self._log_entry(entry)
            entry._spx_at_entry = self.current_price
            self._record_entry_to_db(entry)
            self._next_entry_index += 1

            return (
                f"Entry #{entry_num} placed: LONG STRANGLE "
                f"C {entry.long_call_strike:.0f} / P {entry.long_put_strike:.0f}, "
                f"debit ${entry.total_debit:.2f} ({entry.contracts}c)"
            )
        finally:
            self._entry_in_progress = False
            self.state = MEICState.MONITORING

    def _skip(self, entry, entry_num: int, reason: str) -> str:
        """Book a skip once, to both the shared counters and ``ls_skipped``.

        The second half is the part that matters: ``ls_skipped`` carries the
        proposed strikes, the debit, the expected move and its source — enough to
        score the decision later. The GEX work on variant B had to be retro-fitted
        for exactly this and could never recover its first 95 vetoes.
        """
        self.daily_state.entries_skipped += 1
        self._next_entry_index += 1
        self._record_skipped_entry(entry_num, reason, send_alert=False)
        if self.ls_recorder:
            now = get_us_market_time()
            debit = ((getattr(entry, "long_call_price", 0.0)
                      + getattr(entry, "long_put_price", 0.0))
                     * 100.0 * (entry.contracts or 1))
            self.ls_recorder.record_skip(
                now.strftime("%Y-%m-%d"), entry_num, now.strftime("%H:%M:%S"), reason,
                spx=self.current_price, vix=getattr(self, "current_vix", 0.0),
                proposed_call_strike=getattr(entry, "long_call_strike", 0.0),
                proposed_put_strike=getattr(entry, "long_put_strike", 0.0),
                proposed_debit=debit,
                em_source=getattr(entry, "ls_em_source", ""),
                expected_move=getattr(entry, "ls_expected_move", 0.0),
                iv_percentile=getattr(entry, "ls_iv_percentile", None),
                skew_gap_pct=getattr(entry, "ls_skew_gap_pct", 0.0),
            )
        logger.info("LONGSTRANGLE entry #%d skipped - %s", entry_num, reason)
        return f"Entry #{entry_num} skipped - {reason}"

    # ==================================================================
    # Step 4 — dry-run simulation
    # ==================================================================

    def _simulate_entry(self, entry) -> bool:
        """Book synthetic DRY fills for the two LONG legs. No order is placed.

        Conids and mid prices were already resolved in ``_calculate_strikes``, so
        this only converts them into the debit and assigns synthetic ids. The
        base IC simulation cannot be reused: it books a *credit* from a four-leg
        structure and would record this position as having collected money.
        """
        if not entry.long_call_uic or not entry.long_put_uic:
            logger.error("[DRY RUN] LS: missing conid for entry #%d", entry.entry_number)
            return False
        if entry.long_call_price <= 0 or entry.long_put_price <= 0:
            logger.error("[DRY RUN] LS: unpriced leg for entry #%d", entry.entry_number)
            return False

        n = entry.contracts
        entry.call_debit = entry.long_call_price * 100 * n
        entry.put_debit = entry.long_put_price * 100 * n
        entry.long_call_fill_price = entry.long_call_price
        entry.long_put_fill_price = entry.long_put_price

        base_id = int(datetime.now().timestamp() * 1000)
        entry.long_call_position_id = f"DRY_{base_id}_LC"
        entry.long_put_position_id = f"DRY_{base_id}_LP"
        entry.is_complete = True

        logger.info(
            "[DRY RUN] Simulated LONG STRANGLE #%d: C %.0f ($%.2f) / P %.0f ($%.2f), "
            "total debit $%.2f = max loss, %dc",
            entry.entry_number, entry.long_call_strike, entry.call_debit,
            entry.long_put_strike, entry.put_debit, entry.total_debit, n,
        )
        return True

    def _execute_entry(self, entry) -> bool:
        """**Refuses.** There is no live placement path for variant H.

        Not merely absent — actively refused. Leaving this inherited would hand H
        the base's four-leg iron-condor placement, which sells two short legs H
        does not have and has never sized for. The dry-run lock in ``__init__``
        should make this unreachable; this is the second lock, because "the other
        guard will catch it" is how a strategy ends up selling naked options.
        """
        raise ConfigError(
            "LongStrangleStrategy has no live entry path (Step 4 is dry-run only). "
            "The inherited IC placement would SELL two short legs this strategy "
            "does not have."
        )

    # ==================================================================
    # Step 4 — stops are disarmed, not inherited
    # ==================================================================

    def _calculate_stop_levels_hydra(self, entry) -> None:
        """Deliberate no-op: **a long strangle has no stop loss.**

        Max loss is the debit, known before the position opens, so there is
        nothing to stop out of — no GUARD-FLOOR, no A2 %-of-width, no MKT-046
        anti-spike, no buffer decay.

        The base computes ``credit + buffer``. With ``total_credit`` truthfully
        0.0 (Step 2) that collapses to the MIN_STOP_LEVEL floor plus a buffer —
        a small, arbitrary dollar figure with no relationship to anything, which
        the monitoring loop would then treat as a real trigger. Setting the stops
        explicitly unreachable is the difference between "no stop" and "a stop
        nobody chose".

        The exits live in ``_check_stop_losses`` (the profit target) and
        ``_settlement_booked_pnl`` (expiry). Neither reads these levels; they are
        set unreachable so that no base path can.

        RESOLVED IN STEP 5: the base's DATA-004 sanity guard USED to reject H on
        every tick — it rejects a side whose two legs are "partially zero", and
        H's ``short_*_price`` is permanently 0.0 against a priced ``long_*``.
        That was harmless as a second lock but it discarded the very tick the
        profit target needs, and logged a warning calling an intentionally-absent
        leg "suspicious". ``_validate_pnl_sanity`` now validates the LONG legs
        only — the exact mirror of G's **S-CRIT-1**, where the same guard was the
        bug that kept G's stop from ever firing.
        """
        entry.call_side_stop = float("inf")
        entry.put_side_stop = float("inf")
        logger.info(
            "LS entry #%d: no stop levels — max loss is the $%.2f debit, by "
            "construction", entry.entry_number, entry.total_debit,
        )

    # ==================================================================
    # Step 4 — recording
    # ==================================================================

    def _record_entry_to_db(self, entry) -> None:
        """Route H's entries to the isolated DB, NEVER to ``trade_entries``.

        The base writes ``call_credit`` / ``put_credit`` / ``total_credit``, all
        of which are truthfully 0.0 here — so the shared-shape table would record
        a strangle that cost nothing, and every consumer would read that zero as
        fact. Overriding rather than simply not calling it means no inherited
        call site can leak one in either.
        """
        if not self.ls_recorder:
            return
        now = get_us_market_time()
        self.ls_recorder.record_entry(
            entry, date=now.strftime("%Y-%m-%d"),
            spx_at_entry=self.current_price,
            vix_at_entry=float(getattr(self, "current_vix", 0.0) or 0.0),
            em_source=getattr(entry, "ls_em_source", ""),
            expected_move=getattr(entry, "ls_expected_move", 0.0),
            skew_gap_pct=getattr(entry, "ls_skew_gap_pct", 0.0),
        )

    # ==================================================================
    # Step 5 — the exits
    # ==================================================================

    def _validate_pnl_sanity(self, entry) -> Tuple[bool, str]:
        """Validate the LONG legs only. The exact mirror of G's **S-CRIT-1**,
        for the opposite reason.

        The base's DATA-004 check rejects a side whose two legs are "partially
        zero". H's ``short_*_price`` is permanently 0.0 against a priced
        ``long_*_price``, so the base guard returns False on **every tick** and
        the per-tick manager skips the entry entirely.

        For G that was the bug: its stop never fired, leaving an undefined-risk
        naked short unmanaged. Here it is not dangerous — there is no stop to
        miss — but it is still wrong in two ways. It discards the tick Step 5's
        profit target needs, and it logs a warning calling an intentionally
        absent leg "suspicious".

        A side is data-valid iff its LONG is priced. There is deliberately no
        minimum-loss suppression (mirroring G's L-H3 reasoning in the opposite
        direction): this guard exists to reject unusable data, never to delay an
        exit.
        """
        if not getattr(entry, "call_side_expired", False) and entry.long_call_strike:
            if not entry.long_call_price:
                return False, "Long strangle call leg is unpriced (skip this tick)"
        if not getattr(entry, "put_side_expired", False) and entry.long_put_strike:
            if not entry.long_put_price:
                return False, "Long strangle put leg is unpriced (skip this tick)"
        return True, "ok (long strangle long-only sanity)"

    def _profit_target_pct(self) -> float:
        """The exit threshold, as a percentage of the debit paid.

        The source takes **+50%** normally and **+100%** when IV is expanding
        from a low base. The elevated target therefore depends on the same
        IV-percentile series Step 4's filter needs and this repo does not have —
        so it is **unreachable until ``iv_percentile_source`` is wired**, and the
        target is 50% until then.

        That is stated rather than silently defaulted, because "we take +100% in
        expanding IV" reads as an implemented rule in the config file, and today
        it is not one.
        """
        cfg = self._ls_config()
        base = float(cfg.get("profit_target_pct_of_debit", 50.0))
        elevated = float(cfg.get("profit_target_pct_of_debit_iv_expanding", base))
        if elevated <= base:
            return base

        hist = self._vix_history_for_percentile()
        pct = iv_percentile(float(getattr(self, "current_vix", 0.0) or 0.0), hist)
        if pct is None:
            return base

        # "EXPANDING from a LOW base" is TWO conditions, and reading it as one
        # was the easy mistake: a low percentile alone is a cheap-premium day,
        # which is the ENTRY filter, not the exit rule. The source wants the
        # extra target when vol is cheap AND rising — the case where a long
        # position gets paid twice, by the move and by the vol expansion.
        low = pct <= float(cfg.get("iv_percentile_max", 35.0))
        rising = bool(hist) and float(getattr(self, "current_vix", 0.0) or 0.0) > hist[-1]
        if low and rising:
            logger.info(
                "LS: IV cheap (%.0fth pct) AND expanding (VIX %.2f > prior close "
                "%.2f) — target raised to %+.0f%%",
                pct, float(getattr(self, "current_vix", 0.0) or 0.0), hist[-1], elevated,
            )
            return elevated
        return base

    def _check_stop_losses(self) -> Optional[str]:
        """H's per-tick manager. **There is no stop loss — this is the target.**

        WHY THERE IS NO BREACH-PERSISTENCE WINDOW, AND WHY THAT IS NOT AN OMISSION
        -------------------------------------------------------------------------
        The playbook's Step 5 asks for a persistence window on any stop reading a
        noisy multi-leg mark, the MKT-046 analogue: a single stale tick must not
        fire a false stop. **Applied here it would be actively harmful.**

        A stop triggers on an ADVERSE spike, so waiting to confirm protects you —
        if it reverts, you keep the position. A long strangle's profit target
        triggers on a FAVOURABLE spike, and reverting is exactly what those
        spikes do. Waiting 10 seconds to confirm +50% systematically gives back
        the move the strategy exists to capture. The sign of the position inverts
        the sign of the guard.

        So the target fires on the first VALID tick, and the protection against a
        phantom target is **quote quality rather than elapsed time**:
        ``_quote_mid``'s crossed-book guard (L-M7) plus the long-legs-only sanity
        check above. ``profit_target_confirm_seconds`` exists at a default of 0
        so the dry run can measure the other setting rather than assume this one.

        Every valid tick is written to ``ls_snapshots`` BEFORE the target is
        evaluated, so the mark that triggered an exit is recoverable and the
        peak-versus-exit gap is measurable — the question the source's win-rate
        claim actually rests on.
        """
        self._batch_update_entry_prices()
        target = self._profit_target_pct()
        now = get_us_market_time()

        for entry in list(self.daily_state.active_entries):
            if not isinstance(entry, LongStrangleEntry):
                continue
            valid, message = self._validate_pnl_sanity(entry)
            if not valid:
                logger.debug("LS #%d: %s", entry.entry_number, message)
                continue

            if self.ls_recorder:
                self.ls_recorder.record_snapshot(
                    entry, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                    spx=self.current_price,
                )

            pct = entry.pnl_pct_of_debit
            if pct < target:
                entry._ls_target_first_seen = None
                continue

            confirm = float(self._ls_config().get(
                "profit_target_confirm_seconds", 0.0))
            if confirm > 0:
                first = getattr(entry, "_ls_target_first_seen", None)
                if first is None:
                    entry._ls_target_first_seen = now
                    logger.info(
                        "LS #%d: target %+.0f%% touched at %+.1f%% — holding "
                        "%.0fs for confirmation (a NON-default setting; see "
                        "_check_stop_losses on why 0 is the default)",
                        entry.entry_number, target, pct, confirm,
                    )
                    continue
                if (now - first).total_seconds() < confirm:
                    continue

            logger.info(
                "LS #%d: profit target hit — %+.1f%% of a $%.2f debit "
                "(target %+.0f%%)", entry.entry_number, pct,
                entry.total_debit, target,
            )
            return self._close_long_strangle(
                entry, f"profit_target_{target:.0f}", now,
            )
        return None

    def _close_long_strangle(self, entry, reason: str, now=None) -> str:
        """SELL both longs at the current mark and book the result.

        Dry-run only — the third lock. ``__init__`` and ``_execute_entry`` should
        both make a live call here impossible; this refuses anyway, on the same
        principle.

        P&L is booked GROSS of commission and the commission is added to the
        day's separate total, which is the convention every other close path in
        this codebase uses. Inventing a net-of-commission realized P&L here would
        make H's numbers quietly incomparable with A-G's.
        """
        if not self.dry_run:  # pragma: no cover - unreachable while locked
            raise ConfigError(
                "LongStrangleStrategy has no live close path. "
                "Gate: docs/migration/H_GOLIVE_SCOPE_AND_AUDIT.md (HG-5)."
            )
        now = now or get_us_market_time()
        proceeds = entry.current_value
        realized = proceeds - entry.total_debit
        close_commission = 2 * self.commission_per_leg * entry.contracts

        # *_side_expired is the flag `active_entries` and the settlement sweep
        # both read: the sweep explicitly skips an already-expired side, so
        # marking both here is what prevents settlement re-booking this exit.
        entry.call_side_expired = True
        entry.put_side_expired = True
        entry.close_reason = reason
        entry.close_time = now.isoformat()

        self._book_realized_pnl(realized, entry)
        self.daily_state.total_commission += close_commission

        minutes_held = 0.0
        if getattr(entry, "entry_time", None):
            minutes_held = max(0.0, (now - entry.entry_time).total_seconds() / 60.0)
        if self.ls_recorder:
            self.ls_recorder.record_exit(
                entry, now.strftime("%Y-%m-%d"), exit_reason=reason,
                realized_pnl=realized, commissions=entry.open_commission + close_commission,
                spx_at_exit=self.current_price, minutes_held=minutes_held,
                exit_time=now.strftime("%H:%M:%S"),
            )

        logger.info(
            "LS #%d CLOSED (%s): sold for $%.2f against a $%.2f debit = "
            "$%+.2f (%+.1f%%), held %.0f min",
            entry.entry_number, reason, proceeds, entry.total_debit,
            realized, entry.pnl_pct_of_debit, minutes_held,
        )
        return (
            f"Entry #{entry.entry_number} closed ({reason}): "
            f"{realized:+.2f} on a ${entry.total_debit:.2f} debit"
        )

    # ==================================================================
    # Step 5 — expiry
    # ==================================================================

    def _settlement_booked_pnl(self, entry, side: str,
                               settlement_level) -> Tuple[float, bool]:
        """Settlement P&L for one LONG leg: ``intrinsic − debit``.

        The base books "the full credit kept" for a side that finished OTM. For H
        that is ``call_spread_credit``, an inherited field nothing ever sets — so
        the base would book **$0.00** and record a long strangle that expired
        worthless as **break-even**. The true answer is a loss of the whole
        premium paid for that leg. This is the same class of mis-record S-HIGH-2
        fixed for G, with the sign reversed.

        The ``worthless`` flag is returned False unconditionally: in the base's
        vocabulary it means "kept the credit", which never applies here, and it
        only gates a log line that would otherwise read backwards.
        """
        # getattr, not attribute access: the base's restart-recovery path
        # reconstructs entries as HydraIronCondorEntry, which has no debit at
        # all. _restore_long_strangle_entries repairs that from ls_entries, but
        # an entry whose row is missing reaches here with no cost basis — and an
        # AttributeError inside the settlement sweep would take down settlement
        # for the whole day, for every entry.
        debit = getattr(entry, "call_debit" if side == "call" else "put_debit", None)
        if debit is None:
            logger.critical(
                "LS #%s %s side: NO COST BASIS (entry was restored without an "
                "ls_entries row) — booking $0.00 because the P&L is genuinely "
                "unknown, NOT because it is zero. Reconcile against the IBKR "
                "settlement report.", getattr(entry, "entry_number", "?"), side,
            )
            return 0.0, False

        if side == "call":
            strike = entry.long_call_strike
            intrinsic_pts = ((settlement_level - strike)
                             if settlement_level is not None else None)
        else:
            strike = entry.long_put_strike
            intrinsic_pts = ((strike - settlement_level)
                             if settlement_level is not None else None)

        if intrinsic_pts is None:
            # Unreachable on the normal path: requires_protective_wings=False
            # makes the base read the settlement level even in dry-run, and it
            # DEFERS booking when that read fails (S-HIGH-3). If it happens
            # anyway (no broker), book the WORST case rather than nothing —
            # booking 0.0 here would silently lose the entire debit, which is
            # exactly the failure this override exists to prevent.
            logger.critical(
                "LS #%d %s side: settlement SPX unreadable — booking the "
                "worst case (−$%.2f, total loss of the premium paid). RECONCILE "
                "against the IBKR settlement report.",
                entry.entry_number, side, debit,
            )
            return -debit, False

        if strike <= 0:
            return 0.0, False

        value = max(0.0, intrinsic_pts) * 100 * max(int(getattr(entry, "contracts", 1)), 1)
        booked = value - debit
        logger.info(
            "LS #%d %s leg settled: SPX %.2f vs strike %.0f → intrinsic $%.2f "
            "against a $%.2f debit = %+.2f",
            entry.entry_number, side, settlement_level, strike, value, debit, booked,
        )
        return booked, False

    def _simulate_hydra_entry_prices(self, entry) -> None:
        """The dry-run price fallback, made deliberate rather than accidental.

        The base runs this when the real-quote batch returns nothing. Its full-IC
        branch derives every leg from ``total_credit / (140 × contracts)`` — which
        for H is a truthful 0.0, so it silently marks both longs at **zero**.

        That outcome is, as it happens, the RIGHT one: a zeroed long is rejected
        by ``_validate_pnl_sanity``, so the tick is skipped, no target can fire on
        a fabricated mark, and settlement books from the SPX level rather than
        from these prices. The alternative — holding the last good mark — is
        worse, because a stale +50% would exit at a price that no longer exists.

        It is overridden anyway for two reasons. Arriving at a safe failure by
        accident is not the same as choosing it, and the base path is silent
        where an operator should see that quotes are down.
        """
        entry.long_call_price = 0.0
        entry.long_put_price = 0.0
        logger.warning(
            "LS #%d: quote batch unavailable — marking both legs UNPRICED so "
            "this tick is skipped. No exit can fire on a fabricated mark; "
            "settlement is unaffected (it reads the SPX level).",
            entry.entry_number,
        )

    # ==================================================================
    # Step 5 — restart recovery, because the shared state file cannot
    #          carry a debit
    # ==================================================================

    def _load_state_file_history(self) -> bool:
        """Restore as the base does, then **put the debit back**.

        THE PROBLEM. The shared state file serialises only credit-shaped fields
        (``total_credit``, ``call_spread_credit``, ``put_spread_credit``) and its
        restore path hardcodes ``HydraIronCondorEntry``. A mid-day restart
        therefore hands variant H back entries that are the wrong class AND have
        no cost basis, with two consequences:

        * the per-tick manager skips them (they are not ``LongStrangleEntry``),
          so the profit target can never fire again for that position; and
        * ``_settlement_booked_pnl`` reads ``entry.call_debit``, which a base
          entry does not have — an ``AttributeError`` inside the settlement
          sweep.

        THE FIX, AND WHY IT IS NOT A SIDECAR. The playbook's Step 6 answer to
        "the base state schema can't hold your fields" is a sidecar JSON, and it
        also says single-day strategies skip Step 6. Both are satisfiable here
        because **H already has its own database**: ``ls_entries`` holds the
        strikes, conids and both debits, keyed by ``(date, entry_number)``, and
        the row is written before the position is ever monitored. So recovery is
        a read from H's own store — no new file, and **zero edits to the shared
        save/load that variant B trades on live**.

        A row that cannot be found is reported CRITICAL rather than patched over
        with a guess: an entry with an unknown cost basis has no computable P&L,
        and inventing one would be worse than saying so.
        """
        ok = super()._load_state_file_history()
        try:
            self._restore_long_strangle_entries()
        except Exception as e:  # pragma: no cover - recovery must not block start
            logger.error("LS: restart recovery failed (non-fatal): %s", e)
        return ok

    def _restore_long_strangle_entries(self) -> None:
        """Upgrade restored base entries back to ``LongStrangleEntry`` + debits."""
        entries = getattr(self.daily_state, "entries", None)
        if not entries:
            return
        stale = [e for e in entries if not isinstance(e, LongStrangleEntry)]
        if not stale:
            return
        if not self.ls_recorder:
            logger.critical(
                "LS: %d entries restored WITHOUT a cost basis and no recorder to "
                "recover it from. Their P&L is not computable — reconcile manually.",
                len(stale),
            )
            return

        rows = {r.get("entry_number"): r
                for r in self.ls_recorder.fetch_entries(
                    get_us_market_time().strftime("%Y-%m-%d"))}

        for i, old in enumerate(entries):
            if isinstance(old, LongStrangleEntry):
                continue
            row = rows.get(old.entry_number)
            if not row:
                logger.critical(
                    "LS: entry #%s restored with NO ls_entries row — its debit is "
                    "unknown, so its P&L is not computable. Reconcile against the "
                    "IBKR statement.", old.entry_number,
                )
                continue

            new = LongStrangleEntry(entry_number=old.entry_number)
            # Carry every field the base restore did populate, so flags, ids and
            # timestamps survive the class change unchanged.
            for name, value in vars(old).items():
                try:
                    setattr(new, name, value)
                except AttributeError:  # pragma: no cover - read-only property
                    pass
            new.call_debit = float(row.get("call_debit") or 0.0)
            new.put_debit = float(row.get("put_debit") or 0.0)
            new.contracts = int(row.get("contracts") or new.contracts or 1)
            new.long_call_strike = float(row.get("call_strike") or new.long_call_strike)
            new.long_put_strike = float(row.get("put_strike") or new.long_put_strike)
            new.long_call_uic = row.get("long_call_uic") or new.long_call_uic
            new.long_put_uic = row.get("long_put_uic") or new.long_put_uic
            new.ls_em_source = row.get("em_source") or ""
            new.ls_expected_move = float(row.get("expected_move") or 0.0)
            new.ls_skew_gap_pct = float(row.get("skew_gap_pct") or 0.0)
            entries[i] = new
            logger.info(
                "LS: recovered entry #%d from ls_entries — C %.0f / P %.0f, "
                "$%.2f debit, %dc",
                new.entry_number, new.long_call_strike, new.long_put_strike,
                new.total_debit, new.contracts,
            )

    # ==================================================================
    # Step 9 — hardening: the EOD flatten, declined on purpose
    # ==================================================================

    def _check_eod_flatten(self) -> Optional[str]:
        """**Never.** H rides to cash settlement, and that is a decision.

        MKT-047 force-closes every open 0DTE SHORT near the cutoff so a late
        breach cannot ride to maximum loss in the un-closable final minutes.
        **H has no shorts and no such tail** — its worst case is the debit, which
        was already paid — so the rule it exists to enforce does not apply.

        It was already being skipped before this override, but only by
        ACCIDENT: the base gates on ``requires_protective_wings``, which H sets
        to False for a completely different reason (it has no shorts for the
        naked-short guard to act on) and which the CALENDARS set to skip this
        for a third reason again (they hold for days). Three unrelated
        rationales resolving to one flag is exactly the kind of coincidence that
        breaks silently when someone changes one of them.

        THE TRADE-OFF, STATED. Closing at 15:50 would capture whatever extrinsic
        value is left; holding to settlement forfeits it and books intrinsic.
        At 0DTE with minutes to run that difference is small, SPXW is
        cash-settled so there is no assignment risk, and hold-to-expiry is what
        the source describes. The cost of holding is variance — the P&L is set
        by the 4pm print rather than by where the position could have been sold
        ten minutes earlier — not a systematic loss. If the dry run shows
        meaningful extrinsic being given up, this is the method to change.
        """
        return None
