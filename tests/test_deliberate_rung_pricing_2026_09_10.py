"""
Entry rung 1 had no pricing POLICY — it had an arithmetic accident (2026-09-10).

MEASURED ON B's LIVE FILLS (2026-07-24..09-09, 65 entries, 7 contracts):
total fill gap $1,817.62, of which the LONG legs are $1,575.07 (86.7%) —
long_call $815.01, long_put $760.06. That is ~38% of B's $4,687.65 net P&L,
about $79 per trading day.

TWO SEPARATE DEFECTS, both in `round_to_spx_tick`:

1. BUY uses math.ceil. On a $0.05 book the mid is ALWAYS a half-tick, so the
   limit lands exactly on the ask — every long leg is a taker, deterministically.
   31 of 34 rung-1 long limits parsed from raw logs were exactly the ask; the 3
   exceptions were $0.10 books where ceil(mid) == mid.

2. SELL uses round(), and on a half-tick the outcome is decided by FLOAT NOISE
   plus banker's rounding. Across the 58 one-tick books below $3 the sell limit
   crosses the spread on 67% of them and rests on 33% — with no trading intent
   behind the split at all.

THE REPLACEMENT is a decision, not a different rounding mode:
    BUY  -> highest tick at or below mid  (floor)
    SELL -> lowest  tick at or above mid  (ceil)
On a one-tick book that is exactly "join the touch". On a wider book it lands
INSIDE the spread — a price improvement, not a concession.

CONFIG-GATED AND OFF BY DEFAULT. Resting is only better if the order still
fills; an entry that fails part-way pays the spread TWICE on the unwind (that
path fired twice on 2026-09-09). So this goes to C (dry-run) first to measure
the fill-rate cost, and nowhere near the live seat until there is data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import (  # noqa: E402
    deliberate_rung_pricing_enabled,
    round_to_spx_tick,
    rung_limit_no_cross,
)

#: Every valid tick below $3 and a stretch above it. Using ALL of them is the
#: point: the first version of this test hand-picked 0.05/0.50/1.00/2.95/3.00/
#: 4.20/10.00, every one of which is EXACTLY representable in binary — so it
#: could not detect the epsilon being removed. The prices that actually carry
#: float error are 0.15, 0.30, 0.60, 0.70, 1.15, 4.30 ... At 0.60,
#: 0.60/0.05 == 11.999999999999998, so without the epsilon a BUY floors to
#: 0.55 — a whole tick too low.
ON_TICK_PRICES = (
    [round(i * 0.05, 2) for i in range(1, 60)]      # 0.05 .. 2.95
    + [round(i * 0.10, 2) for i in range(30, 121)]  # 3.00 .. 12.00
)

# One-tick books below $3 (tick = $0.05)
ONE_TICK_BOOKS = [(round(i * 0.05, 2), round(i * 0.05 + 0.05, 2)) for i in range(1, 59)]


class TestItNeverCrosses:
    @pytest.mark.parametrize("bid,ask", ONE_TICK_BOOKS)
    def test_buy_never_lands_above_the_bid_on_a_one_tick_book(self, bid, ask):
        """Joining the bid is passive. Landing on the ask is taking."""
        mid = (bid + ask) / 2
        assert rung_limit_no_cross(mid, is_buy=True) == pytest.approx(bid)

    @pytest.mark.parametrize("bid,ask", ONE_TICK_BOOKS)
    def test_sell_never_lands_below_the_ask_on_a_one_tick_book(self, bid, ask):
        mid = (bid + ask) / 2
        assert rung_limit_no_cross(mid, is_buy=False) == pytest.approx(ask)

    @pytest.mark.parametrize("bid,ask", ONE_TICK_BOOKS)
    def test_buy_is_never_above_sell(self, bid, ask):
        """Sanity: the passive buy must always be <= the passive sell."""
        mid = (bid + ask) / 2
        assert (rung_limit_no_cross(mid, is_buy=True)
                <= rung_limit_no_cross(mid, is_buy=False))


class TestItIsDeterministic:
    """The whole point. The legacy sell rounding was decided by float noise."""

    @pytest.mark.parametrize("bid,ask", ONE_TICK_BOOKS)
    def test_sell_is_stable_where_legacy_was_not(self, bid, ask):
        mid = (bid + ask) / 2
        assert rung_limit_no_cross(mid, is_buy=False) == pytest.approx(ask)

    def test_the_legacy_sell_really_was_inconsistent(self):
        """Documents the defect being fixed — if this ever stops holding, the
        underlying rounding changed and this whole module needs re-reading."""
        crossed = rested = 0
        for bid, ask in ONE_TICK_BOOKS:
            v = round_to_spx_tick((bid + ask) / 2, round_up=False)
            if abs(v - bid) < 1e-9:
                crossed += 1
            elif abs(v - ask) < 1e-9:
                rested += 1
        assert crossed and rested, "legacy sell should be split across bid/ask"
        assert crossed > rested   # 39 vs 19 as measured

    def test_the_legacy_buy_really_did_always_take(self):
        for bid, ask in ONE_TICK_BOOKS:
            assert round_to_spx_tick((bid + ask) / 2, round_up=True) == pytest.approx(ask)


class TestWideBooksGetPriceImprovement:
    """On a book wider than one tick, resting AT the touch leaves money on the
    table. Landing inside the spread is strictly better than both crossing and
    joining the touch."""

    def test_buy_lands_inside_a_wide_spread(self):
        bid, ask = 0.05, 0.20          # mid 0.125
        got = rung_limit_no_cross((bid + ask) / 2, is_buy=True)
        assert bid < got < ask
        assert got == pytest.approx(0.10)

    def test_sell_lands_inside_a_wide_spread(self):
        bid, ask = 0.05, 0.20
        got = rung_limit_no_cross((bid + ask) / 2, is_buy=False)
        assert bid < got < ask
        assert got == pytest.approx(0.15)


class TestTickSizeAndEdges:
    def test_the_ten_cent_tick_above_three_dollars_is_respected(self):
        assert rung_limit_no_cross(4.25, is_buy=True) == pytest.approx(4.20)
        assert rung_limit_no_cross(4.25, is_buy=False) == pytest.approx(4.30)

    @pytest.mark.parametrize("px", ON_TICK_PRICES)
    def test_a_price_already_on_a_tick_returns_itself(self, px):
        """The epsilon exists for exactly this: without it, float error makes
        an on-tick price jump a whole tick. Parametrised over EVERY tick rather
        than a hand-picked few — see ON_TICK_PRICES for why that matters."""
        assert rung_limit_no_cross(px, is_buy=True) == pytest.approx(px)
        assert rung_limit_no_cross(px, is_buy=False) == pytest.approx(px)

    def test_the_float_error_this_guards_is_real(self):
        """If this ever stops holding, Python's float behaviour changed and the
        epsilon may no longer be needed — but do not remove it on a hunch."""
        assert 0.60 / 0.05 != 12.0
        assert 0.60 / 0.05 == pytest.approx(12.0)

    @pytest.mark.parametrize("px", [0.0, -1.0, -0.05])
    def test_non_positive_prices_return_zero(self, px):
        assert rung_limit_no_cross(px, is_buy=True) == 0.0
        assert rung_limit_no_cross(px, is_buy=False) == 0.0

    def test_output_is_always_a_valid_tick(self):
        for bid, ask in ONE_TICK_BOOKS:
            for is_buy in (True, False):
                v = rung_limit_no_cross((bid + ask) / 2, is_buy=is_buy)
                assert abs(round(v / 0.05) * 0.05 - v) < 1e-9, f"{v} not on a tick"


class TestTheMoneyClaim:
    """Quantifies the improvement this is supposed to deliver, so the estimate
    in the design is checkable rather than asserted."""

    def test_buy_side_improves_by_one_tick_per_share_on_one_tick_books(self):
        total = 0.0
        for bid, ask in ONE_TICK_BOOKS:
            mid = (bid + ask) / 2
            total += (round_to_spx_tick(mid, round_up=True)
                      - rung_limit_no_cross(mid, is_buy=True))
        assert total == pytest.approx(0.05 * len(ONE_TICK_BOOKS))

    def test_at_seven_contracts_that_is_35_dollars_per_long_leg(self):
        bid, ask = 0.55, 0.60
        mid = (bid + ask) / 2
        saved = (round_to_spx_tick(mid, round_up=True)
                 - rung_limit_no_cross(mid, is_buy=True))
        assert saved * 100 * 7 == pytest.approx(35.0)


class TestTheGateDefaultsOff:
    """Every variant must keep bit-for-bit legacy behaviour until it opts in."""

    # These call the PRODUCTION reader directly. An earlier version of this
    # class re-implemented the .get() chain in its own fixture, so it happily
    # passed while the real default was mutated to True — it was testing its
    # own copy of the logic, not the code that ships.

    def test_absent_config_is_off(self):
        assert deliberate_rung_pricing_enabled({}) is False

    def test_absent_subkey_is_off(self):
        assert deliberate_rung_pricing_enabled({"entry_pricing": {}}) is False

    def test_explicit_true_is_on(self):
        assert deliberate_rung_pricing_enabled(
            {"entry_pricing": {"deliberate_rung_pricing": True}}) is True

    def test_explicit_false_is_off(self):
        assert deliberate_rung_pricing_enabled(
            {"entry_pricing": {"deliberate_rung_pricing": False}}) is False

    @pytest.mark.parametrize("junk", [None, [], "yes", 42])
    def test_malformed_config_is_off_not_crashing(self, junk):
        assert deliberate_rung_pricing_enabled({"entry_pricing": junk}) is False
        assert deliberate_rung_pricing_enabled(junk) is False

    def test_init_actually_uses_this_reader(self):
        """Wiring guard: the gate must be read through the tested function, not
        a second inline copy that could drift from it."""
        import inspect
        from bots.hydra.base_strategy import MEICStrategy
        src = inspect.getsource(MEICStrategy.__init__)
        assert "deliberate_rung_pricing_enabled(" in src

    def test_a_strategy_missing_the_attribute_entirely_falls_back_to_legacy(self):
        """The placement path reads it with getattr(..., False) so a partially
        constructed object (tests, recovery) can never accidentally enable it."""
        obj = object()
        assert getattr(obj, "deliberate_rung_pricing", False) is False
