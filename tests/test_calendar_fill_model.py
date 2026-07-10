"""Realistic dry-run fill model for the calendar strategies (D + E), 2026-06-17.

Before this, every simulated fill (entry debit, transform credit, mark-to-market,
liquidation) used the bid/ask MIDPOINT — which made D's transform look risk-free
far cheaper and faster than crossing 4–6 real spreads would allow ("too good to be
true"). _dc_fill_price now crosses the spread: BUY toward the ask, SELL toward the
bid, scaled by `aggressiveness` (1.0 = full touch). These tests pin the helper and
its effect on the entry debit (↑) and the transform credit (↓, can defer).
"""

from unittest.mock import MagicMock, patch

from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy
from bots.hydra.calendar_entry import CalendarEntry, DCPhase
import bots.hydra.calendar_strategy_base as base_mod


def _strat(*, agg=1.0, slip=0.0):
    s = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
    s._dc_fill_agg = agg
    s._dc_fill_slippage = slip
    return s


def _q(mid, bid=None, ask=None):
    return {"mid": mid, "raw": {"bid": bid, "ask": ask}}


# ───────────────────────── _dc_fill_price ─────────────────────────
class TestFillPrice:
    def test_full_touch_buy_is_ask_sell_is_bid(self):
        s = _strat(agg=1.0)
        q = _q(10.0, bid=9.0, ask=11.0)
        assert s._dc_fill_price(q, "buy") == 11.0    # full touch buy = ask
        assert s._dc_fill_price(q, "sell") == 9.0    # full touch sell = bid

    def test_half_aggressiveness_is_halfway_to_touch(self):
        s = _strat(agg=0.5)
        q = _q(10.0, bid=9.0, ask=11.0)
        assert s._dc_fill_price(q, "buy") == 10.5
        assert s._dc_fill_price(q, "sell") == 9.5

    def test_zero_aggressiveness_is_the_mid(self):
        s = _strat(agg=0.0)
        q = _q(10.0, bid=9.0, ask=11.0)
        assert s._dc_fill_price(q, "buy") == 10.0
        assert s._dc_fill_price(q, "sell") == 10.0

    def test_extra_slippage_widens_both_sides(self):
        s = _strat(agg=1.0, slip=0.25)
        q = _q(10.0, bid=9.0, ask=11.0)
        assert s._dc_fill_price(q, "buy") == 11.25
        assert s._dc_fill_price(q, "sell") == 8.75

    def test_missing_bid_ask_falls_back_to_mid(self):
        s = _strat(agg=1.0)
        q = _q(10.0)  # no bid/ask
        assert s._dc_fill_price(q, "buy") == 10.0
        assert s._dc_fill_price(q, "sell") == 10.0

    def test_no_mid_returns_none(self):
        s = _strat()
        assert s._dc_fill_price(_q(0.0, 1, 2), "buy") is None
        assert s._dc_fill_price({"mid": None, "raw": {}}, "sell") is None

    def test_sell_never_negative(self):
        s = _strat(agg=1.0, slip=5.0)
        assert s._dc_fill_price(_q(1.0, bid=0.5, ask=1.5), "sell") == 0.0  # floored at 0


# ───────────────── entry debit (↑ vs mid) ─────────────────
class TestEntryDebitSpread:
    def _entry(self):
        e = CalendarEntry(entry_number=1)
        e.short_call_strike = 7580.0
        e.short_put_strike = 7450.0
        e.legs["short_call"].expiry = "2026-06-26"
        e.legs["long_call"].expiry = "2026-06-29"
        e.legs["short_put"].expiry = "2026-06-26"
        e.legs["long_put"].expiry = "2026-06-29"
        return e

    def _strat_for_entry(self, quotes):
        s = _strat(agg=1.0)
        s.contracts_per_entry = 1
        s.dc_wing_width = 5
        s._dc_resolve_calendar_legs = MagicMock(
            return_value={"short_call": 1, "long_call": 2, "short_put": 3, "long_put": 4}
        )
        s._dc_read_leg_quotes = MagicMock(return_value=quotes)
        return s

    def test_debit_pays_ask_on_longs_bid_on_shorts(self):
        # mid debit = (8+7 - 5-4)*100 = $600. Realistic = pay ask on longs, get bid
        # on shorts = (8.4+7.4 - 4.8-3.8)*100 = $720 — HIGHER (you cross the spread).
        quotes = {
            "short_call": _q(5.0, bid=4.8, ask=5.2),
            "long_call": _q(8.0, bid=7.6, ask=8.4),
            "short_put": _q(4.0, bid=3.8, ask=4.2),
            "long_put": _q(7.0, bid=6.6, ask=7.4),
        }
        s = self._strat_for_entry(quotes)
        e = self._entry()
        e.short_expiry  # property — ensure legs set
        with patch.object(base_mod, "get_us_market_time") as t:
            t.return_value = MagicMock(timestamp=lambda: 1.0)
            ok = s._dc_simulate_entry(e)
        assert ok is True
        assert round(e.net_debit, 2) == 720.0          # not the $600 mid
        assert e.net_debit > 600.0

    def test_mid_pricing_when_agg_zero_matches_old_behavior(self):
        quotes = {
            "short_call": _q(5.0, bid=4.8, ask=5.2),
            "long_call": _q(8.0, bid=7.6, ask=8.4),
            "short_put": _q(4.0, bid=3.8, ask=4.2),
            "long_put": _q(7.0, bid=6.6, ask=7.4),
        }
        s = self._strat_for_entry(quotes)
        s._dc_fill_agg = 0.0   # mid
        e = self._entry()
        with patch.object(base_mod, "get_us_market_time") as t:
            t.return_value = MagicMock(timestamp=lambda: 1.0)
            s._dc_simulate_entry(e)
        assert round(e.net_debit, 2) == 600.0          # exact old mid behavior


# ───────────────── transform credit (↓ vs mid, can defer) ─────────────────
class TestTransformCreditSpread:
    def _ready_entry(self):
        e = CalendarEntry(entry_number=1)
        e.dc_phase = DCPhase.CALENDAR
        e.net_debit = 1085.0
        e.contracts = 1
        e.short_call_strike = 7580.0
        e.short_put_strike = 7450.0
        e.legs["long_call"].uic = 2
        e.legs["long_put"].uic = 4
        e.legs["short_call"].uic = 1
        e.legs["short_put"].uic = 3
        e.legs["short_call"].expiry = "2026-06-26"
        return e

    def _strat_for_transform(self, long_q, wing_q, short_q=None):
        s = _strat(agg=1.0)
        s.contracts_per_entry = 1
        s.dc_wing_width = 5
        s._get_option_uic = MagicMock(side_effect=[101, 102])  # wing call, wing put
        # _dc_read_leg_quotes is called for the longs, the wings, and (only if the
        # transform FIRES) the shorts for the display IC credit.
        effects = [long_q, wing_q]
        if short_q is not None:
            effects.append(short_q)
        s._dc_read_leg_quotes = MagicMock(side_effect=effects)
        s._dc_recorder = None
        return s

    def test_credit_sells_longs_at_bid_buys_wings_at_ask(self):
        # Far longs worth ~$13/$12 mid; wings ~$1 mid. Mid credit = (13+12-1-1)*100
        # = $2300. Realistic = sell longs at BID, buy wings at ASK = (12.5+11.5
        # -1.2-1.2)*100 = $2160 — LOWER (the spread cost the mid hid).
        long_q = {"long_call": _q(13.0, bid=12.5, ask=13.5),
                  "long_put": _q(12.0, bid=11.5, ask=12.5)}
        wing_q = {"wing_call": _q(1.0, bid=0.8, ask=1.2),
                  "wing_put": _q(1.0, bid=0.8, ask=1.2)}
        short_q = {"short_call": _q(5.0, bid=4.8, ask=5.2),
                   "short_put": _q(4.0, bid=3.8, ask=4.2)}
        s = self._strat_for_transform(long_q, wing_q, short_q)
        e = self._ready_entry()
        with patch.object(__import__("bots.hydra.double_calendar_strategy", fromlist=["x"]),
                          "get_us_market_time") as t:
            t.return_value = MagicMock(isoformat=lambda: "2026-06-17T10:47:00")
            fired = s._dc_attempt_transform(e)
        assert fired is True
        assert round(e.transform_credit, 2) == 2160.0     # not the $2300 mid
        assert e.transform_credit < 2300.0

    def test_spread_can_make_transform_defer(self):
        # Wide spreads: realistic credit falls BELOW debit+wing ($1585) → NOT
        # risk-free → transform defers (holds) instead of locking a phantom gain.
        long_q = {"long_call": _q(9.0, bid=7.5, ask=10.5),
                  "long_put": _q(8.5, bid=7.0, ask=10.0)}
        wing_q = {"wing_call": _q(1.0, bid=0.2, ask=1.8),
                  "wing_put": _q(1.0, bid=0.2, ask=1.8)}
        s = self._strat_for_transform(long_q, wing_q)
        e = self._ready_entry()  # debit 1085, threshold 1085 + 5*100 = 1585
        fired = s._dc_attempt_transform(e)
        # realistic credit = (7.5+7.0 - 1.8-1.8)*100 = $1090 < $1585 → defer
        assert fired is False
        assert e.dc_phase == DCPhase.CALENDAR          # still holding, not transformed


# ───────── mark-sanity guard: reject impossible (crossed) calendar marks ─────────
class TestRefreshMarkSanityGuard:
    """2026-07-10: a crossed/stale leg quote made D's long-dated call (mid 15.0)
    worth LESS than the same-strike near short (24.0) → calendar value -$2,550
    (-356% of a defined-risk debit) → a phantom -20% stop booked at -$797. The guard
    rejects a tick where a calendar side goes negative, keeping the prior good marks."""

    def _entry(self):
        e = CalendarEntry(entry_number=1)
        for name, uic in (("short_call", 1), ("long_call", 2), ("short_put", 3), ("long_put", 4)):
            e.legs[name].uic = uic
            e.legs[name].price = 5.0  # prior GOOD mark
        e.dc_phase = DCPhase.CALENDAR
        return e

    def _strat(self, quotes):
        s = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        s._dc_fill_agg = 0.5
        s._dc_fill_slippage = 0.0
        s.dc_require_realtime_quotes = False
        s._dc_read_leg_quotes = MagicMock(return_value=quotes)
        return s

    def _rt(self, mid, bid, ask):
        return {"mid": mid, "raw": {"bid": bid, "ask": ask}, "realtime": True}

    def test_valid_calendar_marks_commit_and_fresh(self):
        q = {
            "short_call": self._rt(23.9, 23.7, 24.1),
            "long_call":  self._rt(28.3, 28.1, 28.5),  # long > short → valid
            "short_put":  self._rt(27.4, 27.2, 27.6),
            "long_put":   self._rt(31.8, 31.6, 32.0),
        }
        e = self._entry()
        assert self._strat(q)._dc_refresh_marks(e) is True
        assert e.legs["long_call"].price != 5.0  # prices updated

    def test_impossible_mark_rejected_keeps_prior(self):
        # The exact 07-10 spike: long_call 15.0 < short_call 24.0 (calendar-arb
        # violation) → reject the tick, keep prior good marks, return not-fresh.
        q = {
            "short_call": self._rt(24.0, 23.8, 24.2),
            "long_call":  self._rt(15.0, 14.8, 15.2),  # BAD: long < short
            "short_put":  self._rt(44.5, 44.3, 44.7),  # BAD: spiked
            "long_put":   self._rt(28.0, 27.8, 28.2),
        }
        e = self._entry()
        assert self._strat(q)._dc_refresh_marks(e) is False
        assert all(e.legs[n].price == 5.0 for n in ("short_call", "long_call", "short_put", "long_put"))

    def test_real_adverse_move_still_fresh(self):
        # A REAL adverse move keeps long >= short (both drop) → valid → fresh, so
        # genuine stops still fire; the guard only rejects arb-impossible marks.
        q = {
            "short_call": self._rt(5.0, 4.8, 5.2),
            "long_call":  self._rt(6.0, 5.8, 6.2),
            "short_put":  self._rt(5.0, 4.8, 5.2),
            "long_put":   self._rt(6.0, 5.8, 6.2),
        }
        e = self._entry()
        assert self._strat(q)._dc_refresh_marks(e) is True
