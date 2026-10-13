"""ORDER-DBL stopped the LEG retry; the ENTRY retry walked around it.

Live seat `bl`, 2026-10-12, entry #7:

    15:16:28 CRITICAL _place_leg_order(926793533 BUY x7 LMT) TRANSPORT FAILURE
                      on entry place — order may be LIVE but unconfirmed
    15:16:28 CRITICAL ORDER-DBL: aborting Call 7815.0 ... will NOT retry under
                      a new cOID; reconcile the possibly-open order
    15:16:28 ERROR    Entry failed at leg 1: Long Call order failed
    15:16:28 INFO     Entry #7 retry 2/3        <-- re-places the SAME leg
    15:33:04 WARNING  conid 926793533: expected 7, broker shows 14

`ORDER-DBL` behaved perfectly: exactly one cOID was ever used for that leg and
it returned without re-placing. What it could not do was stop its CALLER. The
entry retry loop in `strategy.py` re-ran the whole entry about a minute later
under fresh cOIDs, and the abandoned-but-live order from attempt 1 plus attempt
2's order left the broker holding twice what the bot tracked.

The guard's own message says *"reconcile the possibly-open order"* — and there
was no reconciler for it. ORDER-008 (`_orphaned_orders`) is the right machinery
and even BLOCKS trading, but its only writer, `_add_orphaned_order`, is keyed on
`order_id` and documented "cancel failed". A transport-failure orphan has **no
order_id** — the response never arrived, only the cOID exists — so the one case
where an order is most likely to be silently live was the one case ORDER-008
could not represent. The pre-entry ORDER-008 gate also runs ONCE per slot,
before the retry loop, so it could not have caught this even if populated.

That instance was benign: the doubled leg was the protective LONG call on a 0DTE
that cash-settled. ⚠️ Three of those four mitigations are luck about which leg
transport-failed. The same path on a SHORT leg leaves an untracked naked short
with no stop on it — the shape reached by a different route in
`naked_short_from_failed_stop_close` (2026-10-08).

⚠️ WHAT THESE TESTS DO NOT SETTLE: whether the broker truly held 14. IBKR's own
realized figure for that conid was **+$6.51**, which is exactly seven contracts'
round-trip (7 x $0.0093 x 100), while the position read said fourteen — and
`/iserver/account/trades` returns zero rows on this paper account, so the
evidence to decide is gone. `TestPos003DumpsRawRows` exists so the next
occurrence is decidable instead of arguable.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402

COID = "hydra_20261012_entry7_LC_3483f00"


def _strategy(open_orders=None, cancel_ok=True):
    """Duck-typed strategy exercising the REAL methods under test."""
    s = HydraStrategy.__new__(HydraStrategy)
    s._ambiguous_placement = None
    s._orphaned_orders = []
    s.alert_service = MagicMock()
    s._get_open_orders = lambda: list(open_orders or [])
    s._cancel_order = lambda oid: cancel_ok
    s.cancelled = []
    return s


class TestTheFlagIsSetWhateverTheLookupSays:
    """Job 1, and the only one that must not depend on a network call."""

    def test_flag_set_when_the_order_is_not_found(self):
        s = _strategy(open_orders=[])
        s._note_ambiguous_placement(
            coid=COID, conid=926793533, side="BUY", quantity=7,
            leg_description="Call 7815.0")
        assert s._ambiguous_placement is not None
        assert s._ambiguous_placement["coid"] == COID
        assert s._ambiguous_placement["resolved"] == "not_found"

    def test_flag_set_even_when_the_lookup_raises(self):
        """A flag that depended on the broker answering would be absent in
        exactly the situation that creates it — a broker that just failed."""
        s = _strategy()
        def boom():
            raise RuntimeError("broker down")
        s._get_open_orders = boom
        s._note_ambiguous_placement(
            coid=COID, conid=1, side="BUY", quantity=7, leg_description="L")
        assert s._ambiguous_placement is not None
        assert s._ambiguous_placement["coid"] == COID

    @pytest.mark.parametrize("key", ["cOID", "coid", "order_ref", "orderRef"])
    def test_every_ibkr_coid_spelling_is_matched(self, key):
        """IBKR spells it differently per endpoint; missing one means we fail to
        find (and so fail to cancel) a live order."""
        s = _strategy(open_orders=[{key: COID, "orderId": "439587347"}])
        s._note_ambiguous_placement(
            coid=COID, conid=1, side="BUY", quantity=7, leg_description="L")
        assert s._ambiguous_placement["resolved"] == "cancelled:439587347"


class TestBestEffortCancel:
    """Job 2: we abandoned the leg, so a working order for it is unwanted."""

    def test_a_live_order_is_cancelled(self):
        s = _strategy(open_orders=[{"cOID": COID, "orderId": "439587347"}])
        s._note_ambiguous_placement(
            coid=COID, conid=1, side="BUY", quantity=7, leg_description="L")
        assert s._ambiguous_placement["resolved"] == "cancelled:439587347"
        assert s._orphaned_orders == [], "a cancelled order is not an orphan"

    def test_a_failed_cancel_becomes_an_ORDER_008_orphan(self):
        """This is the hand-off that was impossible before: ORDER-008 needs an
        order_id, and resolving the cOID is what finally supplies one."""
        s = _strategy(open_orders=[{"cOID": COID, "orderId": "439587347"}],
                      cancel_ok=False)
        s._note_ambiguous_placement(
            coid=COID, conid=1, side="BUY", quantity=7, leg_description="L")
        assert s._orphaned_orders == ["439587347"]
        assert s._ambiguous_placement["resolved"] == "orphaned:439587347"
        # and ORDER-008's gate must now actually block
        assert s._has_orphaned_orders() is True

    def test_an_unrelated_live_order_is_left_alone(self):
        """Cancelling someone else's working order would be far worse than the
        bug being fixed."""
        s = _strategy(open_orders=[{"cOID": "some_other_entry", "orderId": "999"}])
        s._note_ambiguous_placement(
            coid=COID, conid=1, side="BUY", quantity=7, leg_description="L")
        assert s._ambiguous_placement["resolved"] == "not_found"
        assert s._orphaned_orders == []

    def test_a_live_order_with_no_id_is_reported_not_silently_dropped(self):
        s = _strategy(open_orders=[{"cOID": COID}])
        s._note_ambiguous_placement(
            coid=COID, conid=1, side="BUY", quantity=7, leg_description="L")
        assert s._ambiguous_placement["resolved"] == "live_no_order_id"

    def test_non_dict_rows_do_not_crash_the_sweep(self):
        s = _strategy(open_orders=[None, "junk", 42, {"cOID": COID, "orderId": "7"}])
        s._note_ambiguous_placement(
            coid=COID, conid=1, side="BUY", quantity=7, leg_description="L")
        assert s._ambiguous_placement["resolved"] == "cancelled:7"


class TestTheRetryLoopHonoursTheFlag:
    """The part that actually prevents the duplicate. Source-asserted, because
    the retry loop sits inside a ~400-line entry method that cannot be driven
    without a broker — so these are necessary, not sufficient (the behavioural
    proof is the flag tests above plus the ordering test below)."""

    @pytest.fixture(scope="class")
    def src(self):
        return open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()

    def test_the_loop_checks_the_flag(self, src):
        assert '_ambiguous_placement", None)' in src or \
               'getattr(self, "_ambiguous_placement", None)' in src

    def test_the_check_precedes_the_retry_announcement(self, src):
        """If the flag were read AFTER `retry N/3` was logged and the sleep
        taken, the log would claim a retry that never happened — and a future
        edit could easily slot the placement call in between."""
        announce = 'retry {attempt + 1}/'
        assert announce in src, "the retry announcement itself is gone"
        flag_at = src.find('getattr(self, "_ambiguous_placement", None)')
        assert flag_at != -1, (
            "the retry loop no longer reads _ambiguous_placement — an entry "
            "whose leg placement could not be confirmed will be re-placed "
            "under a fresh cOID, which is the 2026-10-12 duplicate")
        assert flag_at < src.index(announce), (
            "the ambiguity check must come before the retry is announced")

    def test_the_blocked_path_breaks_rather_than_continues(self, src):
        marker = 'NOT retrying — a prior leg'
        assert marker in src, (
            "the blocked-retry path is gone; nothing stops the entry loop "
            "re-placing a possibly-live leg")
        window = src[src.index(marker):][:1400]
        assert "break" in window, "must leave the retry loop, not skip one attempt"
        assert "ORDER_DBL_RETRY_BLOCKED" in window, "must record a safety event"

    def test_the_flag_is_cleared_per_entry_not_left_sticky(self, src):
        """A sticky flag would let one transport blip silently end the trading
        day — a new failure mode, not a fix."""
        assert "self._ambiguous_placement = None" in src
        clear_at = src.index("self._ambiguous_placement = None")
        loop_at = src.index("for attempt in range(ENTRY_MAX_RETRIES)")
        assert clear_at < loop_at, "the clear must happen before the retry loop"


class TestPos003DumpsRawRows:
    """So the next occurrence is decidable rather than arguable."""

    @pytest.fixture(scope="class")
    def src(self):
        return open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()

    def test_raw_rows_are_logged_on_a_quantity_mismatch(self, src):
        assert "POS-003-RAW" in src

    def test_the_dump_cannot_break_the_alert_path(self, src):
        """Diagnostics must never take down the thing they are diagnosing."""
        i = src.index("POS-003-RAW")
        window = src[max(0, i - 900):i + 900]
        assert "try:" in window and "except Exception" in window

    def test_the_summing_assumption_is_still_what_it_claims(self):
        """The dump only matters because `_actual_position_quantities` SUMS per
        conid. This pins the behaviour the diagnostic is about: two rows at one
        conid really do aggregate, so a duplicate row IS indistinguishable from
        a doubled position in the aggregate number alone."""
        rows = [{"instrument_id": 926793533, "quantity": 7},
                {"instrument_id": 926793533, "quantity": 7}]
        out = HydraStrategy._actual_position_quantities(rows)
        assert out == {926793533: 14}


class TestTheOrphanRegistrationGapIsRealAndClosed:
    """Documents, executably, why a separate field was needed."""

    def test_add_orphaned_order_still_requires_an_order_id(self):
        """If this ever accepts a cOID directly, `_note_ambiguous_placement`
        can be simplified — until then the two-step resolve is necessary."""
        import inspect
        sig = inspect.signature(MEICStrategy._add_orphaned_order)
        assert list(sig.parameters) == ["self", "order_id"]

    def test_a_fresh_strategy_starts_with_no_ambiguity(self):
        s = _strategy()
        assert s._ambiguous_placement is None
        assert s._has_orphaned_orders() is False
