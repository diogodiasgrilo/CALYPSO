"""LongStrangleEntry economics — every inherited credit formula must be inverted.

Step 2's exit criterion is "the model's economic properties (P&L, value, width)
are correct and unit-tested in isolation; A/B/C's use of IronCondorEntry is
untouched." These are that test.

The failure this guards against is specific and silent. `IronCondorEntry` assumes
premium was COLLECTED: `unrealized_pnl` = credit − cost-to-close, and both legs
expiring worthless is the MAXIMUM PROFIT. For a long strangle every one of those
inverts — expiring worthless is the maximum LOSS. An inherited formula left in
place here would not raise; it would return a plausible number with the wrong
sign, and every downstream consumer would believe it.

The arithmetic is checked against hand-computed values rather than against the
implementation, so a future refactor that "simplifies" a formula into the base's
shape fails here rather than in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.long_strangle_entry import LongStrangleEntry  # noqa: E402
from bots.hydra.strategy import HydraIronCondorEntry  # noqa: E402


def _entry(call_px=2.00, put_px=1.50, call_debit=1400.0, put_debit=1050.0, contracts=7):
    """A 7-contract long strangle: call bought at 2.00 ($1,400), put at 1.50 ($1,050)."""
    e = LongStrangleEntry(entry_number=1)
    e.contracts = contracts
    e.long_call_strike = 7825.0
    e.long_put_strike = 7725.0
    e.long_call_price = call_px
    e.long_put_price = put_px
    e.call_debit = call_debit
    e.put_debit = put_debit
    return e


class TestWhatWasPaid:
    def test_total_debit_sums_both_legs(self):
        assert _entry().total_debit == pytest.approx(2450.0)

    def test_max_loss_is_the_debit(self):
        """Bounded by construction — the reason H needs no stop machinery."""
        assert _entry().max_loss == pytest.approx(2450.0)

    def test_total_credit_is_zero_not_a_negative_debit(self):
        """THE trap. A negative 'credit' would let the inherited `credit − value`
        formula return a plausible number that is wrong by 2 × value. Zero is
        truthful: nothing was sold."""
        e = _entry()
        assert e.total_credit == 0.0
        assert e.total_credit != -e.total_debit


class TestWhatItIsWorth:
    def test_leg_values_are_price_x_100_x_contracts(self):
        e = _entry()
        assert e.call_leg_value == pytest.approx(2.00 * 100 * 7)
        assert e.put_leg_value == pytest.approx(1.50 * 100 * 7)

    def test_current_value_is_both_legs(self):
        assert _entry().current_value == pytest.approx(2450.0)

    def test_spread_value_properties_return_the_LONG_leg(self):
        """Inherited names, inverted meaning: the base subtracts a short leg that
        does not exist here."""
        e = _entry()
        assert e.call_spread_value == e.call_leg_value
        assert e.put_spread_value == e.put_leg_value

    def test_value_is_NOT_clamped_to_a_width(self):
        """The base clamps spread value to [0, width] because a short vertical's
        cost-to-close is capped by its width. A long option has no such cap —
        clamping would truncate exactly the large-move payoff this strategy exists
        to capture. A 40.00 call on 7 contracts is worth $28,000, not a width."""
        e = _entry(call_px=40.00)
        assert e.call_spread_value == pytest.approx(28000.0)

    def test_spread_width_is_zero_not_the_strike(self):
        """The base's max(call_width, put_width) with short_* at 0.0 returns the
        LONG STRIKE (7825.0) — a four-digit number that looks like a width and
        would wildly overstate capital. 0.0 makes a width-based consumer fail
        visibly instead."""
        e = _entry()
        assert e.spread_width == 0.0
        assert e.spread_width != e.long_call_strike


class TestThePnlInversion:
    def test_pnl_is_value_minus_debit(self):
        """Entered at 2450 debit, now worth 3000 → +550."""
        e = _entry(call_px=2.50, put_px=1.7857142857)
        assert e.unrealized_pnl == pytest.approx(e.current_value - 2450.0)

    def test_worthless_at_expiry_is_the_MAXIMUM_LOSS(self):
        """The single most important assertion in this file. The base class's
        identical situation — both sides expiring worthless — is its maximum
        PROFIT. Here it is the maximum loss, the full debit."""
        e = _entry(call_px=0.0, put_px=0.0)
        assert e.unrealized_pnl == pytest.approx(-2450.0)
        assert e.unrealized_pnl == pytest.approx(-e.max_loss)

    def test_a_big_move_pays_far_more_than_the_debit(self):
        """Long gamma: the payoff is unbounded on the upside, which is the whole
        thesis. 10.00 on the call alone is $7,000 against a $2,450 debit."""
        e = _entry(call_px=10.00, put_px=0.0)
        assert e.unrealized_pnl == pytest.approx(7000.0 - 2450.0)
        assert e.unrealized_pnl > e.max_loss

    def test_the_inherited_credit_formula_reports_a_LOSS_where_there_is_a_PROFIT(self):
        """Pins the reason Step 2 exists, in the starkest available form.

        A 2450 debit now worth 3500 is a **+$1,050 profit**. The base's formula,
        ``credit − value`` with a truthful zero credit, returns **−$3,500** — not
        a near miss but the opposite sign, and a number larger than the maximum
        possible loss. Nothing would raise; every consumer downstream would simply
        believe it."""
        e = _entry(call_px=2.50, put_px=2.50)
        correct = e.unrealized_pnl
        inherited_would_give = e.total_credit - e.current_value

        assert correct == pytest.approx(1050.0)          # a profit
        assert inherited_would_give == pytest.approx(-3500.0)  # reported as a loss
        assert correct > 0 > inherited_would_give
        # ...and the bogus figure even exceeds the position's maximum loss.
        assert abs(inherited_would_give) > e.max_loss


class TestPercentOfDebit:
    def test_fifty_percent_target(self):
        """The source's normal exit: +50% of premium paid."""
        e = _entry(call_px=2.50, put_px=2.75)  # value 3675 on a 2450 debit
        assert e.pnl_pct_of_debit == pytest.approx(50.0, abs=0.01)

    def test_hundred_percent_target(self):
        e = _entry(call_px=5.00, put_px=2.00)  # value 4900 = 2 × debit
        assert e.pnl_pct_of_debit == pytest.approx(100.0, abs=0.01)

    def test_total_loss_is_minus_one_hundred_percent(self):
        e = _entry(call_px=0.0, put_px=0.0)
        assert e.pnl_pct_of_debit == pytest.approx(-100.0)

    def test_zero_debit_does_not_divide_by_zero(self):
        """A ZeroDivisionError inside the monitoring loop would be far worse than
        a flat zero."""
        e = _entry(call_debit=0.0, put_debit=0.0)
        assert e.pnl_pct_of_debit == 0.0


class TestTheBaseIsUntouched:
    def test_a_plain_iron_condor_entry_still_uses_credit_math(self):
        """A/B/C/F/G must be completely unaffected by variant H existing."""
        ic = HydraIronCondorEntry(entry_number=1)
        ic.contracts = 7
        ic.call_spread_credit = 300.0
        ic.put_spread_credit = 200.0
        assert ic.total_credit == pytest.approx(500.0)

    def test_the_base_still_computes_width_from_the_wings(self):
        ic = HydraIronCondorEntry(entry_number=1)
        ic.short_call_strike, ic.long_call_strike = 7800.0, 7805.0
        ic.short_put_strike, ic.long_put_strike = 7700.0, 7695.0
        assert ic.spread_width == pytest.approx(5.0)

    def test_long_strangle_entry_is_still_a_hydra_entry(self):
        """Subclassing (not replacing) keeps the Leg bridge, active_entries, state
        save/load and (conid, quantity) reconciliation working unchanged."""
        assert issubclass(LongStrangleEntry, HydraIronCondorEntry)
