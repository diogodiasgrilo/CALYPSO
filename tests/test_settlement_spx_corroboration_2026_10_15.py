"""Settlement must not evaluate ITM against a prior session's price.

Live seat `bl`, 2026-10-14. `_settlement_spx_level()` returned **7805.45** at
21:45 ET when the day's real close was **7720.97** (7805.45 sits beside
10-13's close of 7804.40 — a day-stale value). Four call sides with shorts at
7745/7765/7770/7780 were 25–75pt OTM and expired worthless; each was booked as
a ~$3,400 ITM loss:

    Entry #1 call side settled ITM (SPX=7805.45 vs short 7780, 5.00pt
    intrinsic): booking $-3395.00 ... — NOT a worthless expiry

The day booked **-$12,710.40**. IBKR's own independent realized figure for the
same day was **-$1,324.87**, and the true outcome was roughly **+$1,500**.

Nothing suppressed it, because the method documents that it ignores freshness
ON PURPOSE: just after 16:00 a 'Z' (Frozen) flag IS the settlement reference.
That reasoning is sound for a read taken minutes after the close and false hours
later, when IBKR's frozen value may belong to a different session.

⚠️ THE PROPERTY THAT MAKES THIS WORSE THAN ITS SIZE: the ITM check only runs
when positions reach expiry, which is exactly what a credit strategy wants. A
stale HIGH price therefore converts a maximum-credit day into a near-maximum
recorded loss. The bug fires on good days.

The fix keeps the live read but corroborates it against the day's last recorded
intraday tick, reusing the threshold `_resolve_spx_close` already established
(a legitimate settlement price sits within ~0.05% of the recorded close; the
10-14 divergence was 1.094%).
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.strategy import HydraStrategy  # noqa: E402

TRUE_CLOSE = 7720.97
STALE = 7805.45          # what IBKR actually served at 21:45 on 2026-10-14


def _strategy(live, disk, *, current_price=0.0, raise_live=False):
    s = HydraStrategy.__new__(HydraStrategy)
    s.underlying_symbol = "SPX"
    s.current_price = current_price
    s.spx_price = 0.0

    def _read(_sym):
        if raise_live:
            raise RuntimeError("broker down")
        return (live, True)
    s._read_index_price = _read
    s._data_recorder = (
        SimpleNamespace(get_last_spx_for_date=lambda d: disk) if disk is not None else None
    )
    return s


class TestTheIncident:
    def test_a_day_stale_live_read_is_rejected_for_the_recorded_close(self):
        s = _strategy(live=STALE, disk=TRUE_CLOSE)
        assert s._settlement_spx_level() == pytest.approx(TRUE_CLOSE)

    def test_and_that_makes_the_call_sides_worthless_again(self):
        """The whole point — the booking, not the price."""
        s = _strategy(live=STALE, disk=TRUE_CLOSE)
        level = s._settlement_spx_level()
        s.requires_protective_wings = True
        for short_k, long_k, credit in ((7780, 7785, 105.0), (7765, 7770, 95.0),
                                        (7770, 7775, 65.0), (7745, 7750, 175.0)):
            entry = SimpleNamespace(
                entry_number=1, contracts=7,
                call_spread_credit=credit, short_call_strike=short_k,
                long_call_strike=long_k,
                put_spread_credit=0.0, short_put_strike=0.0, long_put_strike=0.0)
            booked, worthless = s._settlement_booked_pnl(entry, "call", level)
            assert worthless is True, f"short {short_k} still booked ITM at {level}"
            assert booked == pytest.approx(credit), "must keep the full credit"

    def test_negative_control_the_stale_price_really_did_book_a_loss(self):
        """Proves the test above is not vacuous: with the stale level, the same
        entry books a ~-$3,400 loss. If this stops reproducing, the fixture no
        longer models the incident."""
        s = _strategy(live=STALE, disk=None)      # no recorder -> no corroboration
        level = s._settlement_spx_level()
        assert level == pytest.approx(STALE)
        s.requires_protective_wings = True
        entry = SimpleNamespace(
            entry_number=1, contracts=7, call_spread_credit=105.0,
            short_call_strike=7780, long_call_strike=7785,
            put_spread_credit=0.0, short_put_strike=0.0, long_put_strike=0.0)
        booked, worthless = s._settlement_booked_pnl(entry, "call", level)
        assert worthless is False
        assert booked == pytest.approx(105.0 - 5 * 100 * 7)   # -$3,395


class TestTheGuardIsNarrow:
    """It must not reject a LEGITIMATE settlement read."""

    def test_a_normal_close_read_is_used_unchanged(self):
        """A real settlement price sits within ~0.05% of the recorded close."""
        s = _strategy(live=7721.40, disk=TRUE_CLOSE)
        assert s._settlement_spx_level() == pytest.approx(7721.40)

    @pytest.mark.parametrize("pct", [0.0, 0.2, 0.5, 0.9])
    def test_divergences_under_one_percent_pass_through(self, pct):
        live = TRUE_CLOSE * (1 + pct / 100)
        s = _strategy(live=live, disk=TRUE_CLOSE)
        assert s._settlement_spx_level() == pytest.approx(live)

    @pytest.mark.parametrize("pct", [1.01, 1.094, 3.0, -1.5, -5.0])
    def test_divergences_over_one_percent_are_rejected(self, pct):
        live = TRUE_CLOSE * (1 + pct / 100)
        s = _strategy(live=live, disk=TRUE_CLOSE)
        assert s._settlement_spx_level() == pytest.approx(TRUE_CLOSE)

    def test_a_genuine_limit_move_is_a_known_accepted_cost(self):
        """If SPX truly moved >1% between the last recorded tick and the read,
        the recorded close wins. That is deliberate: the recorded close is the
        better settlement proxy anyway (SPXW is PM-cash-settled on the close),
        and no live read taken hours later adds information."""
        s = _strategy(live=TRUE_CLOSE * 1.05, disk=TRUE_CLOSE)
        assert s._settlement_spx_level() == pytest.approx(TRUE_CLOSE)


class TestFallbacks:
    def test_no_live_read_prefers_the_recorded_close_over_current_price(self):
        """current_price is a strategy attribute that can be stale by DAYS; the
        recorded close cannot."""
        s = _strategy(live=None, disk=TRUE_CLOSE, current_price=7805.45)
        assert s._settlement_spx_level() == pytest.approx(TRUE_CLOSE)

    def test_live_read_raising_still_yields_the_recorded_close(self):
        s = _strategy(live=None, disk=TRUE_CLOSE, raise_live=True)
        assert s._settlement_spx_level() == pytest.approx(TRUE_CLOSE)

    def test_no_live_and_no_disk_falls_back_to_current_price(self):
        s = _strategy(live=None, disk=None, current_price=7700.0)
        assert s._settlement_spx_level() == pytest.approx(7700.0)

    def test_nothing_at_all_returns_None_so_the_caller_assumes_worthless(self):
        """Returning None is the safe legacy path — never crash settlement."""
        s = _strategy(live=None, disk=None, current_price=0.0)
        assert s._settlement_spx_level() is None

    def test_a_recorder_that_raises_does_not_break_settlement(self):
        s = _strategy(live=7721.0, disk=TRUE_CLOSE)
        def boom(_d):
            raise RuntimeError("db locked")
        s._data_recorder = SimpleNamespace(get_last_spx_for_date=boom)
        assert s._settlement_spx_level() == pytest.approx(7721.0)

    def test_a_zero_or_none_disk_close_does_not_reject_a_good_live_read(self):
        for disk in (0.0, None):
            s = _strategy(live=7721.0, disk=disk)
            assert s._settlement_spx_level() == pytest.approx(7721.0)
