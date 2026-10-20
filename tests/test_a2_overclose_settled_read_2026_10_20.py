"""A2's over-close correction traded off a position read taken 1s after its own fill.

LIVE INCIDENT, seat `bl`, 2026-10-19 12:19-12:21 (entry #1, Short Call 7755):

    12:20:32  Cancelling working order ... (1/7 filled) before next rung
    12:21:31  GUARD-FLOOR-UNFILLABLE: abandoning at rung 3/5 — the net-credit
              floor $0.45 is ABOVE the offer $0.40
    12:21:31  ORDER-010: only filled 1/7 after all 5 rungs — flattening the
              partial to avoid an untracked naked leg
    12:21:33  ORDER-010: booked $2.30 round-trip commission for the flattened
              1-contract partial            <-- the flatten FILLED here
    12:21:34  A2 OVER-CLOSE: broker went +0 -> -1 but we asked for +0, so -1
              contract(s) traded beyond the request. Correcting with BUY 1.
    12:21:38  A2: over-close on Call 7755.0 corrected (BUY 1 filled).
    12:32:33  POS-003/I-M4: 1 ORPHAN broker position at untracked conid
              929372783 qty 1 (LONG)        <-- and again 13:32, 14:32, 15:32

The flatten had already returned the position to 0. The "correction" bought a
second contract, leaving **+1 LONG** — an untracked position with no stop on it,
which fired four CRITICAL alerts and was left to expire (SPX closed 7691 vs the
7755 strike, so it cost the premium paid, ~$40, plus commission).

ROOT CAUSE — both inputs stale in the same direction:

  * `qty_before` was captured at 12:19:43, before any rung ran. It was
    genuinely 0 then.
  * `qty_after`, read at 12:21:34, had caught up with the 12:20:32 SELL but not
    with the 12:21:33 BUY.

So `actual_delta` read as "the book moved 1 against a request of 0" when nothing
had moved — the feed had merely caught up with an earlier fill.

⚠️ That falsifies the guard's stated reasoning, which says lag "can never invent
contracts we did not trade" and treats "moved MORE than we asked" as
trustworthy. That holds only while `qty_before` is accurate. When it is also
stale, a catch-up is indistinguishable from an over-fill — and the safety net
OPENED a position, the one thing its own comment says it must never do.

THE FIX is narrow: `require_settled=True` at the post-flatten site only, because
only there has a corrective trade of ours just landed. The immediate pass defers
instead of trading; the deferred pass re-evaluates the same inputs against a
settled book. For this incident that yields actual_delta 0 and no trade. A
genuine double-fill (the 2026-09-25 shape the mechanism was built for — BUY 7
twice, ending LONG 7) persists across the settle window and is still corrected.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

# MEICStrategy is abstract (_calculate_strikes/_check_stop_losses/_initiate_entry),
# so drive the method through the concrete subclass that inherits it.
from bots.hydra.strategy import HydraStrategy  # noqa: E402

CONID = 929372783          # Call 7755.0


def _s(qty_after):
    s = HydraStrategy.__new__(HydraStrategy)
    # qty_after comes from a STRICT position read (`_read_open_positions`), not
    # from _net_qty_at_conid directly — and the method fails SAFE if that read
    # raises ("NOT assuming an over-close"), so the stub has to live at the read
    # layer or the test proves nothing.
    s._read_open_positions = lambda **kw: [
        {"instrument_id": CONID, "quantity": qty_after}]
    s.alert_service = MagicMock()
    s.deferred = []
    s._defer_fill_check = lambda uic, side, qb, iq, ln: s.deferred.append(
        (uic, side, qb, iq, ln))
    s.traded = []
    s._place_leg_order = lambda **kw: (
        s.traded.append((kw.get("side"), kw.get("quantity"))) or {"filled": True})
    return s


class TestTheIncident:
    def test_the_post_flatten_read_defers_instead_of_trading(self):
        """The exact 10-19 numbers: qty_before 0, intended 0, broker reads -1."""
        s = _s(qty_after=-1)
        s._correct_over_fill(CONID, "SELL", 0, 0, "Call 7755.0",
                             require_settled=True)
        assert s.traded == [], "must NOT trade on a read taken after our own fill"
        assert len(s.deferred) == 1, "must park it for a settled re-check"
        assert s.deferred[0][0] == CONID

    def test_negative_control_without_require_settled_it_buys_and_opens_the_orphan(self):
        """Reproduces the bug, proving the test above is not vacuous: the old
        behaviour issues BUY 1 against a position that is already flat."""
        s = _s(qty_after=-1)
        s._correct_over_fill(CONID, "SELL", 0, 0, "Call 7755.0")
        assert s.traded == [("BUY", 1)], (
            "the pre-fix path buys 1 — which is what left +1 LONG")

    def test_the_deferred_pass_on_a_SETTLED_book_correctly_does_nothing(self):
        """Once the flatten's BUY has propagated the book reads 0, so the same
        inputs yield no excess and no trade. This is why deferring is correct
        rather than merely cautious."""
        s = _s(qty_after=0)
        s._correct_over_fill(CONID, "SELL", 0, 0, "Call 7755.0",
                             allow_defer=False)
        assert s.traded == []
        assert s.deferred == []


class TestTheRealOverFillIsStillCorrected:
    """The mechanism exists for 2026-09-25: a stop bought 7 twice and ended
    LONG 7. That must still be caught."""

    def test_a_genuine_double_fill_is_corrected_on_the_settled_pass(self):
        s = _s(qty_after=+7)                       # was short 7, bought 14
        s._correct_over_fill(CONID, "BUY", -7, 7, "short_call",
                             allow_defer=False)
        assert s.traded == [("SELL", 7)], s.traded

    def test_a_genuine_double_fill_is_still_detected_on_the_immediate_pass(self):
        """Sites that have NOT just issued a corrective trade keep correcting
        immediately — only the post-flatten site opts into deferring."""
        s = _s(qty_after=+7)
        s._correct_over_fill(CONID, "BUY", -7, 7, "short_call")
        assert s.traded == [("SELL", 7)]

    def test_require_settled_only_delays_it_it_does_not_disable_it(self):
        s = _s(qty_after=+7)
        s._correct_over_fill(CONID, "BUY", -7, 7, "short_call",
                             require_settled=True)
        assert s.traded == []
        assert len(s.deferred) == 1, "deferred, not dropped"


class TestOnlyThePostFlattenSiteOptsIn:
    @pytest.fixture(scope="class")
    def src(self):
        return open(os.path.join(ROOT, "bots/hydra/base_strategy.py")).read()

    def test_exactly_one_call_site_passes_require_settled(self, src):
        assert src.count("require_settled=True") == 1

    def test_it_is_the_post_flatten_site(self, src):
        i = src.index("require_settled=True")
        window = src[max(0, i - 1200):i]
        assert "filled then flattened" in window, (
            "require_settled must be on the post-flatten check, which is the "
            "only site where a corrective trade of ours has just landed")

    def test_the_deferred_recheck_still_passes_allow_defer_false(self, src):
        """Otherwise a deferred entry could re-park itself forever."""
        assert "allow_defer=False" in src
