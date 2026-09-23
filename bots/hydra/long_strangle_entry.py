"""LongStrangleEntry — the debit-shaped economics for variant H. Playbook Step 2.

Step 2 exists only when "the strategy's economics don't fit ``IronCondorEntry``".
They do not fit here, and the reason is not a rounding detail — **every economic
property on the base runs credit-vertical math, and a long strangle inverts all
of it.**

WHAT INVERTS
------------
``IronCondorEntry`` is built on one identity: *premium was COLLECTED, and P&L is
what is left of it after paying to close.* Hence ``unrealized_pnl`` = credit −
cost-to-close, ``call_side_expired`` documented as *"PROFIT — kept credit"*, and
``call_spread_value`` clamped to ``[0, width]`` because a short vertical's
cost-to-close cannot exceed its width.

A long strangle collects nothing. Premium is **paid**, P&L is ``value − debit``,
expiring worthless is the **maximum loss** rather than the maximum profit, and
there is no width to clamp to because there are no spreads — just two bought
options.

**No sign trick rescues the inherited formula.** Setting ``total_credit`` to
``−debit`` and reusing ``credit − value`` gives ``−debit − value``; the correct
answer is ``value − debit``. The two differ by ``2 × value``. The formula itself
has to be replaced, which is what this class does and why Step 2 is not optional
for variant H.

FIELD MAPPING — the mirror of variant G
---------------------------------------
G (short strangle) holds two NAKED SHORTS, so it populates ``short_*`` and leaves
``long_*`` permanently 0.0. H is the mirror: it holds two LONGS, so it populates
``long_call_strike`` / ``long_put_strike`` (and the matching ``long_*_price``) and
leaves ``short_*`` at 0.0.

⚠️ That mapping is deliberate and it has a known consequence, recorded here so
Steps 4–5 do not rediscover it the hard way. G needed **S-CRIT-1** because the
base's ``_validate_pnl_sanity`` demanded both legs of a side be priced, and G's
``long_*_price`` is permanently 0.0 — so the guard rejected every tick and G's
stop never fired. H has the same shape with the sides swapped: base code that
gates on ``short_*`` will see zeros. **H needs no stop** (max loss is the debit),
so the specific S-CRIT-1 failure cannot bite the same way — but any base path
that treats ``short_*`` as "is there a position here" must be checked in Step 5.

WHAT THIS CLASS DOES **NOT** DO
-------------------------------
It carries economics only. No entry logic, no strike selection, no exits — those
are Steps 3–5. Subclassing ``HydraIronCondorEntry`` (rather than writing a fresh
dataclass) is the playbook's instruction and keeps the Leg bridge,
``active_entries``, state save/load and ``(conid, quantity)`` reconciliation
working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from bots.hydra.strategy import HydraIronCondorEntry


@dataclass
class LongStrangleEntry(HydraIronCondorEntry):
    """A 0DTE long strangle: one bought call + one bought put, no wings, no shorts.

    Economics are debit-shaped throughout. Every credit-shaped property inherited
    from ``IronCondorEntry`` is overridden below; each override says what the base
    did and why it is wrong here, because a silent sign inversion in this file
    would mis-state P&L everywhere downstream without failing anything loudly.
    """

    #: Premium PAID for the long call, in dollars, already × 100 × contracts.
    call_debit: float = 0.0
    #: Premium PAID for the long put, in dollars, already × 100 × contracts.
    put_debit: float = 0.0

    # ------------------------------------------------------------------
    # What was paid
    # ------------------------------------------------------------------

    @property
    def total_debit(self) -> float:
        """Total premium paid — and therefore the **maximum possible loss**.

        There is no equivalent on the base class: an iron condor's max loss is
        ``width − credit`` and depends on the wings. Here it is simply what was
        spent, known before the position is even open.
        """
        return self.call_debit + self.put_debit

    @property
    def max_loss(self) -> float:
        """Bounded by construction. This is why variant H needs no stop-loss
        machinery at all — no GUARD-FLOOR, no %-of-width stop, no buffer decay."""
        return self.total_debit

    @property
    def total_credit(self) -> float:
        """**0.0 — nothing was sold.** Deliberately truthful rather than a signed
        stand-in for the debit.

        The base returns ``call_spread_credit + put_spread_credit``. Returning a
        negative "credit" here would be worse than useless: it would let the
        inherited ``credit − value`` formula produce a plausible-looking number
        that is wrong by ``2 × value``. Any consumer that needs the money paid
        must ask for ``total_debit`` and will get a clear zero if it asks the
        wrong question.
        """
        return 0.0

    # ------------------------------------------------------------------
    # What it is worth now
    # ------------------------------------------------------------------

    @property
    def call_leg_value(self) -> float:
        """Current market value of the long call (what it could be sold for)."""
        return self.long_call_price * 100 * self.contracts

    @property
    def put_leg_value(self) -> float:
        """Current market value of the long put."""
        return self.long_put_price * 100 * self.contracts

    @property
    def call_spread_value(self) -> float:
        """The long call's value. **The name is inherited, not descriptive** —
        there is no call *spread* in a strangle.

        The base computes ``(short_call_price − long_call_price) × 100 ×
        contracts`` and clamps it to ``[0, width]``, because a short vertical's
        cost-to-close is capped by its width. Neither half applies: there is no
        short leg to subtract, and **a long option's value has no width cap** —
        clamping it would silently truncate exactly the large-move payoff this
        strategy exists to capture.
        """
        return self.call_leg_value

    @property
    def put_spread_value(self) -> float:
        """The long put's value. See ``call_spread_value`` for why the inherited
        name, subtraction and ``[0, width]`` clamp are all wrong here."""
        return self.put_leg_value

    @property
    def current_value(self) -> float:
        """What the whole position could be closed for right now."""
        return self.call_leg_value + self.put_leg_value

    @property
    def spread_width(self) -> float:
        """**0.0 — a strangle has no spreads.**

        The base returns ``max(call_width, put_width)`` from the gaps between
        short and long strikes, for capital/margin sizing. With ``short_*`` at 0.0
        that arithmetic yields the long strike itself (e.g. 7825.0) — a number
        that looks like a width, is not one, and would badly overstate capital if
        anything sized on it. Returning 0.0 makes a width-based consumer fail
        visibly instead of quietly using a four-digit "width".
        """
        return 0.0

    # ------------------------------------------------------------------
    # P&L — the inversion
    # ------------------------------------------------------------------

    @property
    def unrealized_pnl(self) -> float:
        """``value − debit``. The base computes ``credit − value``.

        This single line is the reason Step 2 could not be skipped. Both legs
        expiring worthless gives ``0 − debit`` = the full loss, where the base's
        identical situation ("expired worthless") is its maximum *profit*.
        """
        return self.current_value - self.total_debit

    @property
    def pnl_pct_of_debit(self) -> float:
        """P&L as a percentage of premium paid — the unit the strategy's exits are
        actually expressed in (+50% normally, +100% when IV expands from a low).

        Returns 0.0 on a zero debit rather than dividing by it: an entry with no
        premium paid has no meaningful percentage, and a ``ZeroDivisionError``
        inside a monitoring loop would be a far worse outcome than a flat zero.
        """
        debit = self.total_debit
        if not debit:
            return 0.0
        return self.unrealized_pnl / debit * 100.0

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (
            f"LongStrangleEntry(#{self.entry_number} "
            f"C{self.long_call_strike:g}/P{self.long_put_strike:g} "
            f"debit=${self.total_debit:,.2f} value=${self.current_value:,.2f} "
            f"pnl=${self.unrealized_pnl:,.2f} ({self.pnl_pct_of_debit:+.1f}%))"
        )
