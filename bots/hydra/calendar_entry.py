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

    # ------------------------------------------------------------------
    # Risk-free invariant
    # ------------------------------------------------------------------

    def risk_free_threshold(self) -> float:
        """Minimum transformer credit (dollars) for the transformed IC to be
        structurally risk-free: net_debit + wing_width * 100 * contracts."""
        return self.net_debit + self.wing_width * 100 * self.contracts

    def evaluate_risk_free(self) -> bool:
        """Set + return is_risk_free from the realized transform_credit vs the
        threshold. Call after the transformer fills with measured numbers."""
        self.is_risk_free = self.transform_credit >= self.risk_free_threshold()
        return self.is_risk_free
