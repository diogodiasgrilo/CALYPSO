"""
L-M3: the external-close path must never re-book a side another path already
booked (2026-09-10).

THE INCIDENT. On 2026-09-04, variant B (the live seat) entry #4's call side
expired worthless and settlement booked +$140.00 at 16:00:24 ET. After a later
restart, `_reconcile_recovered_entries_with_broker` reached
`_handle_position_discrepancies`, found the conid gone, and tried to book the
SAME side a second time:
    22:37:24  CRITICAL | L-M3: E#4 call short vanished but its close price is
              unreadable

It only failed to double-book because IBKR's `/iserver/account/trades` returns
ZERO rows on this paper account, so the close price came back None. That
accident is the only thing that saved the day's P&L.

WHY IT WOULD HAVE BEEN WORSE THAN A DUPLICATE. `get_closed_position_price`
applies no opening-vs-closing filter — it takes ANY same-side execution at the
conid inside a days=1 window. Conid 907878374 had a real BUY that day: the
Brandon butterfly hedge's own long leg, 7/7 @ $2.00. On a Brandon variant this
is SYSTEMATIC, not incidental — the butterfly is pinned at the threatened short
strike by construction. So the second booking would have been
    $140 - ($2.00 x 100 x 7) = -$1,260
turning a real -$1,225 day into -$2,485.

THE ROOT CAUSE was two booking paths with DISJOINT idempotency flags. The
external path gated on exactly one flag, `{side}_side_pnl_booked_external`,
which no other close path sets — and which was not even persisted to
hydra_state.json, so a restart resurrected it as False. Settlement, meanwhile,
gated on `*_side_expired` / `*_side_skipped` / `*_side_pivot_closed` /
`*_genuine_stop` and never looked at the external flag.

Neither could see the other, and the day still "reconciled" because the
in-process check compares two numbers that both descend from
`_book_realized_pnl` (see test_broker_pnl_reconcile_2026_09_10.py).

THREE VECTORS, all closed here:
  1. settlement books an expiry -> restart -> the external path books it again
     (the actual 09-04 incident)
  2. the external path books -> restart -> the guard flag was never persisted,
     so it books the SAME side again (previously unnoticed)
  3. the external path books -> settlement re-books the full credit on top,
     whenever close_reason was already TP/BREACH so `*_genuine_stop` is False
     (the mirror image, previously unnoticed)
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402

CONID = 907878374          # the real conid from the 2026-09-04 incident
CREDIT = 140.0             # what the call side actually kept
BUTTERFLY_PX = 2.00        # the hedge's long leg that would have been mistaken
                           # for the short's closing buy


def _entry(**over):
    """An entry whose short_call leg is the vanished conid."""
    e = SimpleNamespace(
        entry_number=4,
        contracts=7,
        short_call_uic=CONID,
        long_call_uic=None,
        short_put_uic=None,
        long_put_uic=None,
        call_spread_credit=CREDIT,
        put_spread_credit=0.0,
        call_side_stopped=False,
        put_side_stopped=False,
        call_side_expired=False,
        put_side_expired=False,
        call_side_skipped=False,
        put_side_skipped=False,
        call_side_pivot_closed=False,
        put_side_pivot_closed=False,
        close_reason="",
        realized_pnl=0.0,
    )
    for k, v in over.items():
        setattr(e, k, v)
    return e


def _strat(entry, close_px=BUTTERFLY_PX):
    """A strategy whose broker WOULD return a close price — i.e. a live
    account, where the dead-endpoint accident no longer protects us."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.contracts_per_entry = 7
    s.daily_state = SimpleNamespace(entries=[entry], total_realized_pnl=0.0)
    s._read_closed_position_price = MagicMock(
        return_value={"closing_price": close_px} if close_px is not None else None)
    s.booked = []
    s._book_realized_pnl = lambda amt, ent=None: s.booked.append(amt)
    return s


def _run(s):
    """Drive the reconcile path: the conid is expected but the broker has 0."""
    HydraStrategy._handle_position_discrepancies(s, {CONID: (-7, 0)})


# ---------------------------------------------------------------------------
# Vector 1 — the actual 2026-09-04 incident
# ---------------------------------------------------------------------------

class TestAlreadyExpiredIsNotRebooked:
    def test_an_expired_side_is_NOT_booked_again(self):
        """THE regression test. Settlement already booked this side's expiry;
        the reconcile path must not touch it — even though the broker now
        happily returns a price, as a live account would."""
        e = _entry(call_side_expired=True, close_reason="EXPIRED", realized_pnl=CREDIT)
        s = _strat(e)
        _run(s)
        assert s.booked == []

    def test_the_2026_09_04_day_total_stays_correct(self):
        """End-to-end with the incident's real numbers. Unguarded, this books
        140 - (2.00 x 100 x 7) = -1260, turning -1,225 into -2,485."""
        e = _entry(call_side_expired=True, close_reason="EXPIRED")
        s = _strat(e)
        s.daily_state.total_realized_pnl = -1225.00
        s._book_realized_pnl = lambda amt, ent=None: (
            s.booked.append(amt),
            setattr(s.daily_state, "total_realized_pnl",
                    s.daily_state.total_realized_pnl + amt))
        _run(s)
        assert s.daily_state.total_realized_pnl == pytest.approx(-1225.00)

    def test_the_close_price_is_not_even_fetched(self):
        """Cheaper AND safer: no call means no chance of acting on the
        butterfly's execution."""
        s = _strat(_entry(call_side_expired=True, close_reason="EXPIRED"))
        _run(s)
        assert s._read_closed_position_price.call_count == 0

    def test_the_leg_is_still_cleaned_up(self):
        """The guard must not cost us the state cleanup this path exists for."""
        e = _entry(call_side_expired=True, close_reason="EXPIRED")
        s = _strat(e)
        _run(s)
        assert e.short_call_uic is None
        assert e.call_side_stopped is True


# ---------------------------------------------------------------------------
# Every clause of the guard predicate needs its own test, or a mutation that
# deletes just that clause would pass.
# ---------------------------------------------------------------------------

class TestEveryGuardClause:
    @pytest.mark.parametrize("flag", [
        "call_side_expired",        # mutation: drop the *_side_expired clause
        "call_side_skipped",        # mutation: drop the *_side_skipped clause
        "call_side_pivot_closed",   # mutation: drop the *_side_pivot_closed clause
        "call_side_pnl_booked_external",  # mutation: drop the external-flag clause
    ])
    def test_each_disposition_flag_alone_blocks_rebooking(self, flag):
        s = _strat(_entry(**{flag: True}))
        _run(s)
        assert s.booked == [], f"{flag} alone must block re-booking"

    def test_a_genuine_prior_stop_blocks_rebooking(self):
        """A real HYDRA stop booked its P&L at stop time. Mutation: drop the
        `prior_stopped and close_reason not in (TP, BREACH)` clause."""
        s = _strat(_entry(call_side_stopped=True, close_reason="STOP"))
        _run(s)
        assert s.booked == []

    def test_the_guard_reads_stopped_BEFORE_the_setattr(self):
        """Order-of-operations mutation: hoisting
        `setattr(entry, f"{side}_side_stopped", True)` above the guard makes
        prior_stopped ALWAYS True, which would silently block every legitimate
        external booking. This test fails if that happens, because a fresh
        never-closed side must still book."""
        s = _strat(_entry())          # nothing set: prior_stopped is False
        _run(s)
        assert s.booked != [], "hoisting the setattr would break all booking"


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL. A guard that blocks legitimate bookings is a WORSE bug than
# the one being fixed, because it silently loses real money.
# ---------------------------------------------------------------------------

class TestLegitimateBookingsStillHappen:
    def test_a_genuinely_vanished_unbooked_side_IS_booked(self):
        """credit 140 - (2.00 x 100 x 7) = -1260."""
        s = _strat(_entry())
        _run(s)
        assert s.booked == [pytest.approx(CREDIT - BUTTERFLY_PX * 100 * 7)]

    def test_the_06_04_TP_orphan_is_STILL_bookable(self):
        """A Brandon TP/BREACH that closed 0 legs sets *_side_stopped but books
        NOTHING. It must remain bookable — this is why the clause is
        `prior_stopped and close_reason not in (TP, BREACH)` rather than a bare
        `prior_stopped`. Mutation: simplify to bare prior_stopped -> this fails."""
        for reason in ("TP", "BREACH"):
            s = _strat(_entry(call_side_stopped=True, close_reason=reason))
            _run(s)
            assert s.booked != [], f"close_reason={reason} must stay bookable"

    def test_unreadable_price_still_books_nothing_and_stays_loud(self):
        """Pre-existing behaviour, guarded against regression."""
        s = _strat(_entry(), close_px=None)
        _run(s)
        assert s.booked == []


# ---------------------------------------------------------------------------
# Vector 2 — the guard flag must survive a restart, or it guards nothing.
# ---------------------------------------------------------------------------

class TestTheFlagIsPersisted:
    """The 09-04 incident crossed a restart. `*_side_expired` survived it
    (it is a declared dataclass field AND is written to hydra_state.json), but
    `*_side_pnl_booked_external` was set by setattr and written NOWHERE — so a
    restart resurrected it as False."""

    def _saved_entry_dict(self):
        import inspect
        return inspect.getsource(HydraStrategy._save_state_to_disk)

    def test_save_writes_both_sides_of_the_external_flag(self):
        src = self._saved_entry_dict()
        assert '"call_side_pnl_booked_external"' in src
        assert '"put_side_pnl_booked_external"' in src

    def test_load_restores_both_sides_of_the_external_flag(self):
        import inspect
        src = inspect.getsource(HydraStrategy._load_state_file_history)
        assert "call_side_pnl_booked_external" in src
        assert "put_side_pnl_booked_external" in src

    def test_a_restored_flag_blocks_rebooking(self):
        """What the persistence is FOR: after a restart, the side the external
        path already booked must not be booked a second time."""
        s = _strat(_entry(call_side_pnl_booked_external=True))
        _run(s)
        assert s.booked == []


# ---------------------------------------------------------------------------
# Vector 3 — settlement must not re-book what the external path booked.
# ---------------------------------------------------------------------------

class TestSettlementRespectsTheExternalFlag:
    def test_settlement_guard_excludes_booked_external_on_both_sides(self):
        """Without this, a side booked externally while close_reason was already
        TP/BREACH (so *_genuine_stop is False) gets its FULL CREDIT re-booked by
        settlement on top of the real close debit."""
        import inspect
        src = inspect.getsource(HydraStrategy)
        # the settlement expiry guard, both sides
        assert 'not getattr(entry, "call_side_pnl_booked_external", False)' in src
        assert 'not getattr(entry, "put_side_pnl_booked_external", False)' in src


# ---------------------------------------------------------------------------
# The put side is a separate code path with its own flags — cover it too.
# ---------------------------------------------------------------------------

class TestPutSideIsGuardedIdentically:
    def _put_entry(self, **over):
        e = _entry(short_call_uic=None, short_put_uic=CONID,
                   call_spread_credit=0.0, put_spread_credit=CREDIT)
        for k, v in over.items():
            setattr(e, k, v)
        return e

    def test_an_expired_put_side_is_not_rebooked(self):
        s = _strat(self._put_entry(put_side_expired=True, close_reason="EXPIRED"))
        _run(s)
        assert s.booked == []

    def test_a_genuinely_vanished_put_side_IS_booked(self):
        s = _strat(self._put_entry())
        _run(s)
        assert s.booked == [pytest.approx(CREDIT - BUTTERFLY_PX * 100 * 7)]

    def test_the_call_flag_does_not_block_the_put_side(self):
        """A cross-wired guard (using one side's flag for the other) would make
        this fail — and would silently stop booking half of all external
        closes."""
        s = _strat(self._put_entry(call_side_expired=True))
        _run(s)
        assert s.booked != []
