"""The Strategy H data probe must be incapable of touching the order subsystem.

This probe runs against the LIVE paper account during market hours, alongside
variant B, so the assertions that matter are about what it CANNOT do.

Two distinct hazards, both learned the hard way in this repo:

1. **Placement.** Handled by a hard allowlist rather than a convention, so
   "this cannot place an order" is checkable by reading eight lines.
2. **The `orders` circuit breaker.** `what_if_order` places nothing, yet it lives
   on the `orders` family and therefore shares a retry/breaker budget with real
   placement — which is how the 2026-09-19 combo probe opened the breaker for
   ~34s. A read-only probe must stay off that family entirely, so `what_if_order`
   is excluded even though it is not a write.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.probe_long_strangle_data import _PERMITTED, rpc  # noqa: E402

SRC = (ROOT / "scripts" / "probe_long_strangle_data.py").read_text()


class TestItCannotTouchOrders:
    @pytest.mark.parametrize("method", [
        "place_order", "place_and_wait_for_fill", "place_iron_condor",
        "place_vertical_spread", "modify_order", "cancel_order",
    ])
    def test_every_write_method_is_refused(self, method):
        assert method not in _PERMITTED
        with pytest.raises(RuntimeError, match="read-only"):
            rpc(method)

    def test_what_if_order_is_excluded_despite_placing_nothing(self):
        """Not a write, but on the `orders` family — the 2026-09-19 lesson."""
        assert "what_if_order" not in _PERMITTED
        with pytest.raises(RuntimeError, match="read-only"):
            rpc("what_if_order")

    def test_the_allowlist_is_reads_only(self):
        assert _PERMITTED == frozenset({
            "qualify_contract", "qualify_option_strikes", "get_option_chain",
            "get_quote", "get_quotes_batch",
        })

    def test_the_allowlist_cannot_be_mutated_at_runtime(self):
        assert isinstance(_PERMITTED, frozenset)


class TestItHandlesAClosedMarket:
    def test_it_aborts_rather_than_reporting_nonsense(self):
        """Run outside RTH the quotes are absent; the probe must say so and exit,
        not compute an expected move from stale or missing prices."""
        assert "market may be closed" in SRC
        assert "needs LIVE quotes" in SRC


class TestItRePollsFreshConids:
    def test_it_does_not_believe_a_single_empty_quote(self):
        """The 2026-09-22 paper-smoke failure: IBKR serves a metadata-only row on
        the first poll of an unseen conid, and one read made the smoke abort on a
        quote that was really there."""
        assert "def _quote(" in SRC
        assert "metadata-only" in SRC

    def test_a_crossed_book_yields_no_mid(self):
        """(bid+ask)/2 on a crossed market is nonsense — the same guard
        `_parse_quote_row` applies (P7-audit L9)."""
        from scripts.probe_long_strangle_data import _mid
        assert _mid({"bid": 12.0, "ask": 11.0}) is None
        assert _mid({"bid": 11.0, "ask": 12.0}) == pytest.approx(11.5)
        assert _mid({"bid": None, "ask": 12.0}) is None


class TestItAnswersTheThreeFlaggedAssumptions:
    @pytest.mark.parametrize("marker", ["Q1", "Q2", "Q3"])
    def test_each_question_is_actually_asked(self, marker):
        assert marker in SRC

    def test_it_refuses_to_pass_off_vix_as_option_iv(self):
        """The trap the chain module flags: VIX is a 30-day INDEX vol, not the IV
        of the 0DTE options being bought. If the probe finds no per-option IV, it
        must say the filter is unbuildable rather than quietly substituting."""
        assert "NOT an option-IV percentile" in SRC
        assert "Do NOT silently substitute VIX" in SRC

    def test_it_decides_nothing_on_its_own(self):
        """A probe gathers inputs. The flagged assumptions stay flagged until a
        human writes a result into the spec."""
        assert "Nothing here decides anything" in SRC
