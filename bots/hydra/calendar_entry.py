"""CalendarEntry — the two-expiration, net-DEBIT position model for Strategy D.

Phase-1 foundation for the "DC Time Machine" (double calendar -> risk-free iron
condor). This is the data model every other D surface (sim, transformer, DB,
Telegram, dashboard) depends on.

DESIGN — subclass IronCondorEntry, override only the economics
A double calendar's four legs map EXACTLY onto the canonical leg names:
  - call calendar = short_call (near expiry) + long_call (far expiry) at the SAME strike
  - put  calendar = short_put  (near expiry) + long_put  (far expiry) at the SAME strike
and AFTER the transformer the longs move to wing strikes on the NEAR expiry, so
the position becomes a genuine same-expiry iron condor. So CalendarEntry
SUBCLASSES IronCondorEntry to reuse all the *structural* plumbing untouched —
the Leg/LegSet bridge, ``active_entries`` recognition, state save/load, per-tick
price updates, and (conid, quantity) reconciliation (calendar legs have DIFFERENT
conids per expiry, so keying is actually cleaner than for an IC). It then
OVERRIDES every *economic* property (total_credit / spread_width /
call_spread_value / put_spread_value / unrealized_pnl) with phase-aware,
debit-rooted math, so the iron-condor credit-vertical formulas (which would
mis-handle a net debit and degenerate at width=0 on same-strike legs) NEVER run
for a calendar. The legs carry their own ``expiry`` (added to ``Leg``).

The 0DTE iron-condor variants (A/B/C) never construct a CalendarEntry, so their
behavior is byte-identical.

NOTE (Phase 5): cross-restart persistence of ``dc_phase`` + per-leg ``expiry``
is NOT wired here — the base state serializer uses a fixed IC schema. The
in-memory model below is complete; restart-safe (de)serialization lands in
Phase 5 along with the multi-day recovery path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from bots.hydra.base_strategy import IronCondorEntry


class DCPhase(Enum):
    """Per-position lifecycle phase for the DC Time Machine.

    Unlike the 0DTE iron-condor family (one phase, born-and-dies same session),
    a DC position moves CALENDAR -> TRANSFORMED -> CLOSED across multiple days.
    """

    CALENDAR = "calendar"        # net-debit double calendar, pre-transformation
    TRANSFORMED = "transformed"  # transformed into a (possibly risk-free) iron condor
    CLOSED = "closed"            # liquidated (pre-transform stop / EOD) or settled


@dataclass
class CalendarEntry(IronCondorEntry):
    """A double-calendar position (Strategy D), held across days.

    Inherits IronCondorEntry's structural surface (legs, status flags, contracts,
    entry bookkeeping) and overrides the economics. All new fields have defaults
    so the dataclass remains constructible as ``CalendarEntry(entry_number=N)``.
    """

    # Lifecycle phase + structure discriminator (read via getattr by DB/Telegram/
    # dashboard so a calendar is never labeled an iron condor).
    dc_phase: DCPhase = DCPhase.CALENDAR
    structure: str = "double_calendar"

    # Net DEBIT paid to open the double calendar (dollars, already ×100×contracts;
    # positive = cash out). The iron-condor family is credit-only and has no
    # analogue — this is the calendar's cost basis.
    net_debit: float = 0.0

    # Same opening cost priced at MID (agg=0) and FULL TOUCH (agg=1), zero extra
    # slippage — record-only, never used by a trading decision (2026-09-07). They
    # exist so the entry debit can be re-priced at any aggressiveness offline:
    #     net_debit(a) = mid_net_debit + a * (touch_net_debit - mid_net_debit)
    # Before this, only the post-haircut number was stored, so the 2026-09-06
    # forensic could not answer "what would D have done at a different fill
    # model?" — the bid/ask had already been discarded. 0.0 means "not captured"
    # (pre-2026-09-07 entries, or a partial quote at entry time).
    mid_net_debit: float = 0.0
    touch_net_debit: float = 0.0

    # Opening liquidation P&L (dollars) captured at fill — unrealized_pnl on the
    # FIRST mark, which is structurally negative by the full round-trip bid/ask
    # spread (the dry-run marks at liquidation, not mid). Decision TRIGGERS
    # (D's transform/stop, E's profit ladder) measure MOVEMENT from this opening
    # mark, so a fresh unmoved calendar reads 0% (as it does on a real broker's
    # P&L) instead of a false ~-20% that was stopping D at birth (2026-07-02
    # calendar audit). The REALIZED P&L at close still uses the honest
    # liquidation value — anchoring changes only WHEN we act, not the EV.
    opening_pnl: float = 0.0

    # Commission bookkeeping (2026-09-09). The risk-free invariant is a CASH
    # claim — "this position cannot lose money" — so it must charge every fee
    # the position pays, not just its structural cost. Before this, the
    # threshold was net_debit + wing*100*n with NO commission term at all, and
    # the transform's own 4 legs booked zero commission anywhere.
    #
    # Impact on the real record: dctm_20260818_001 cleared the old gate by
    # $11.50. The 8 legs it actually pays (4 to open + 4 to transform) cost
    # $9.20 at commission_per_leg=1.15, leaving a true margin of $2.30.
    commission_per_leg: float = 0.0   # rate, stamped at open so the threshold
                                      # can never be computed without it
    transform_commission: float = 0.0  # the transformer's own 4 legs

    # Transformer outcome (set when the transformer fires; Phase 4).
    transform_credit: float = 0.0   # net cash from selling longs - buying wings (dollars)
    wing_width: float = 0.0         # IC wing width in POINTS, set at transform
    is_risk_free: bool = False      # transform_credit >= net_debit + wing_width*100*contracts
    transformed_at: str = ""        # ET ISO timestamp of the transformer fill

    # ------------------------------------------------------------------
    # Expiry helpers (the calendar's defining dimension)
    # ------------------------------------------------------------------

    @property
    def short_expiry(self) -> Optional[str]:
        """Near (short-leg) expiry — the expiry that settles first / the eventual
        iron-condor expiry. Read from the short_call leg (both short legs share it)."""
        leg = self.legs.get("short_call")
        return leg.expiry if leg else None

    @property
    def long_expiry(self) -> Optional[str]:
        """Far (long-leg) expiry — the back-dated leg bought in the calendar phase.
        Becomes irrelevant once the longs are sold off at transformation."""
        leg = self.legs.get("long_call")
        return leg.expiry if leg else None

    # ------------------------------------------------------------------
    # Economic overrides — phase-aware, debit-rooted
    # (the IC credit-vertical math is WRONG for a calendar and never runs)
    # ------------------------------------------------------------------

    @property
    def calendar_value(self) -> float:
        """Current liquidation value of the OPEN double calendar (dollars).

        You are net-long the far-dated legs, so each side's value to unwind is
        (long_price - short_price): sell the long back, buy the short back. Summed
        over the call and put calendars, ×100×contracts. Typically positive (the
        longer-dated long is dearer) and rises as the near short decays faster —
        the calendar's theta/vega edge.
        """
        call_val = (self.long_call_price - self.short_call_price)
        put_val = (self.long_put_price - self.short_put_price)
        return (call_val + put_val) * 100 * self.contracts

    @property
    def total_credit(self) -> float:
        """No credit is collected in the calendar phase (it opens for a DEBIT).
        Once transformed, the remaining position is a credit iron condor whose
        per-side credits live in call/put_spread_credit (handled by super)."""
        if self.dc_phase == DCPhase.CALENDAR:
            return 0.0
        return super().total_credit

    @property
    def spread_width(self) -> float:
        """Risk width in points. A calendar has same-strike legs (IC width would
        be 0); the meaningful width is the wing width of the IC it transforms
        into. Post-transform, the legs ARE a real vertical so super() is correct."""
        if self.dc_phase == DCPhase.CALENDAR:
            return self.wing_width
        return super().spread_width

    @property
    def call_spread_value(self) -> float:
        """Cost-to-close of the call side. CALENDAR: the call calendar's debit
        value (long - short); the IC [0, width] clamp would degenerate at
        width=0, so it must not run. TRANSFORMED: a real vertical -> super()."""
        if self.dc_phase == DCPhase.CALENDAR:
            return (self.long_call_price - self.short_call_price) * 100 * self.contracts
        return super().call_spread_value

    @property
    def put_spread_value(self) -> float:
        """Cost-to-close of the put side. See call_spread_value."""
        if self.dc_phase == DCPhase.CALENDAR:
            return (self.long_put_price - self.short_put_price) * 100 * self.contracts
        return super().put_spread_value

    @property
    def unrealized_pnl(self) -> float:
        """Mark-to-market P&L (dollars), phase-aware.

        CALENDAR: current calendar liquidation value minus the debit paid.
        TRANSFORMED: the locked transformer economics (transform_credit -
        net_debit) minus the current cost to close the remaining iron condor. If
        the risk-free gate held (transform_credit >= net_debit + wing), even the
        worst-case IC cost (= wing) leaves this >= 0. (The exact held-to-expiry
        settlement accounting is finalized in Phase 4/5.)
        CLOSED: 0 (realized P&L is booked on the daily state, not here).
        """
        if self.dc_phase == DCPhase.CALENDAR:
            return self.calendar_value - self.net_debit
        if self.dc_phase == DCPhase.TRANSFORMED:
            ic_cost = super().call_spread_value + super().put_spread_value
            return (self.transform_credit - self.net_debit) - ic_cost
        return 0.0

    @property
    def pnl_move_pct(self) -> float:
        """Return-on-debit MOVEMENT since the opening mark — the honest basis for
        the decision triggers (D's transform/stop, E's profit ladder). Zero on a
        fresh unmoved calendar because opening_pnl IS the birth liquidation mark,
        so the full round-trip spread is netted out of the trigger (but NOT out of
        the realized P&L at close). Falls back to absolute pnl/debit when
        net_debit or opening_pnl is unset (pre-2026-07-02 restored trades)."""
        nd = self.net_debit
        if not nd:
            return 0.0
        return (self.unrealized_pnl - self.opening_pnl) / nd

    # ------------------------------------------------------------------
    # Risk-free invariant
    # ------------------------------------------------------------------

    #: Legs the transformer itself trades: sell 2 back-dated longs, buy 2 wings.
    TRANSFORM_LEG_COUNT = 4

    def risk_free_threshold(self) -> float:
        """Minimum transformer credit (dollars) for the transformed IC to be
        genuinely risk-free — structural cost PLUS every commission the
        position pays.

            net_debit + wing_width*100*n          <- structural worst case
          + open_commission                       <- 4 legs, already paid
          + TRANSFORM_LEG_COUNT * rate * n        <- 4 legs, about to be paid

        WHY THE FEES BELONG HERE (2026-09-09). The invariant this gate asserts
        is a CASH claim: `_dc_settle_transformed` logs "max loss $0". But the
        worst-case IC value at expiry is EXACTLY wing*100*n and the old
        threshold was EXACTLY net_debit + wing*100*n, so worst-case realized
        was exactly $0 BEFORE fees — meaning any omitted cost makes the real
        outcome negative. The gate had zero margin by construction, and then
        omitted 8 legs of commission on top.

        Measured impact on D's only settled winner, dctm_20260818_001:
        transform_credit $1,456.50 vs old threshold $1,445.00 = cleared by
        $11.50. Commissions are $9.20 (8 legs x $1.15 x 1 contract). True
        margin: $2.30.

        SPXW is European cash-settled, so a held-to-expiry IC pays no closing
        commission — hence 8 legs, not 12. If a future change closes the IC
        early instead of letting it settle, add close_commission here.

        Fees default to 0.0, which reproduces the pre-2026-09-09 threshold
        exactly for any entry that never stamped them (all historical rows).
        """
        structural = self.net_debit + self.wing_width * 100 * self.contracts
        fees = (
            self.open_commission
            + self.TRANSFORM_LEG_COUNT * self.commission_per_leg * self.contracts
        )
        return structural + fees

    def evaluate_risk_free(self) -> bool:
        """Set + return is_risk_free from the realized transform_credit vs the
        commission-inclusive threshold.

        HONEST LIMITATION — this is NOT an independent verification. The
        transformer gates on the same inequality against the same numbers, so
        reaching here implies True. It is retained because (a) it is the single
        place `is_risk_free` is set, and (b) once a real-order path exists this
        becomes the post-fill re-check, recomputed from actual fill prices
        rather than the estimate, and must then be able to return False.
        Do not read a True here as evidence the position is risk-free; read it
        as "the estimate cleared the estimate".
        """
        self.is_risk_free = self.transform_credit >= self.risk_free_threshold()
        return self.is_risk_free
