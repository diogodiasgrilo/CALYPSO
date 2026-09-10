"""
`get_closed_position_price` must not mistake an OPENING execution for a CLOSING
one (2026-09-10).

WHY THIS IS URGENT NOW. The function matched on (conid, side) alone and then
took the MOST RECENT match. That was harmless only because
/iserver/account/trades returned nothing on this account — and that is fixed in
the same batch as this change (we were never sending `accountId`). The moment
the endpoint starts answering, this function goes live-fire.

WHY (conid, side) IS SYSTEMATICALLY WRONG ON A BRANDON VARIANT. The butterfly
hedge's long leg is pinned at the threatened short's OWN strike by construction.
So the hedge's OPENING buy has the same conid and the same side as the short's
CLOSING buy. On 2026-09-04 that execution was real: Call 7740, 7/7 @ $2.00.

AND NOTE WHAT DOES NOT FIX IT. "Only consider executions after the leg opened"
sounds like the answer and is not: the hedge is placed LATER than the leg it
defends, so it passes that test cleanly. `not_before` is kept because it is
strictly additive, but the mechanism that actually saves us is REFUSING TO
GUESS when two executions are indistinguishable.

A wrong close price is far worse than none. No price leaves the P&L unbooked
and logs loudly; a wrong price books a plausible number that no downstream check
can catch, because the in-process reconcile is circular by construction.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import IBClient, _to_epoch_ms  # noqa: E402

CONID = 907878374
OPEN_T = datetime(2026, 9, 4, 10, 15, tzinfo=timezone.utc)   # the IC leg opens


def _ms(dt):
    return dt.timestamp() * 1000.0


def _exec(price, size=7, at=None, side="B", **extra):
    at = at or (OPEN_T + timedelta(hours=1))
    rec = {
        "conid": CONID, "side": side, "price": price, "size": size,
        "trade_time": at.isoformat(), "trade_time_r": _ms(at),
        "execution_id": f"e{price}",
    }
    rec.update(extra)
    return rec


def _client(rows):
    c = IBClient.__new__(IBClient)
    c._require_connected = lambda: None
    c._account_id = "DU1"
    c._client = MagicMock()

    def _ib_call(family, fn, *a, **kw):
        return rows if fn is c._client.trades else []

    c._ib_call = _ib_call
    return c


def _get(rows, **kw):
    kw.setdefault("buy_or_sell", "Buy")
    return IBClient.get_closed_position_price(_client(rows), CONID, **kw)


# ---------------------------------------------------------------------------
# The 2026-09-04 shape
# ---------------------------------------------------------------------------

class TestTheButterflyIsNotMistakenForTheClose:
    def test_two_indistinguishable_buys_REFUSE(self):
        """The short's close and the butterfly's open, same conid, same side,
        same size. Nothing can tell them apart, so report nothing."""
        rows = [
            _exec(1.10, at=OPEN_T + timedelta(hours=2)),   # plausibly the close
            _exec(2.00, at=OPEN_T + timedelta(hours=4)),   # the butterfly open
        ]
        assert _get(rows, not_before=OPEN_T, expect_quantity=7) is None

    def test_recency_would_have_picked_the_WRONG_one(self):
        """This is the specific trap. The hedge is placed LATER, so
        'most recent wins' selects the butterfly. Pin that we do not do that:
        if the refusal were replaced by `matches[0]` after the recency sort,
        this returns 2.00."""
        rows = [
            _exec(1.10, at=OPEN_T + timedelta(hours=2)),
            _exec(2.00, at=OPEN_T + timedelta(hours=4)),
        ]
        got = _get(rows, not_before=OPEN_T)
        assert got is None or got["closing_price"] != 2.00

    def test_not_before_alone_does_NOT_save_us(self):
        """Both executions post-date the leg's open, so the timestamp filter
        keeps both. Documents why the refusal is the real mechanism."""
        rows = [
            _exec(1.10, at=OPEN_T + timedelta(hours=2)),
            _exec(2.00, at=OPEN_T + timedelta(hours=4)),
        ]
        assert _get(rows, not_before=OPEN_T) is None


class TestTheHappyPathStillWorks:
    def test_a_single_unambiguous_execution_is_returned(self):
        assert _get([_exec(1.10)], not_before=OPEN_T)["closing_price"] == 1.10

    def test_single_execution_without_any_hints(self):
        """Back-compat: no hints, one match -> still answers."""
        assert _get([_exec(1.10)])["closing_price"] == 1.10

    def test_the_returned_shape_is_unchanged(self):
        got = _get([_exec(1.10, size=7)])
        assert got["closing_price"] == 1.10
        assert got["amount"] == 7
        assert got["buy_or_sell"] == "Buy"
        assert got["conid"] == CONID
        assert "raw" in got

    def test_the_wrong_side_is_still_ignored(self):
        assert _get([_exec(1.10, side="S")]) is None


class TestNotBefore:
    def test_an_execution_predating_the_open_is_dropped(self):
        """It cannot be this leg's close — the leg did not exist yet."""
        rows = [_exec(9.99, at=OPEN_T - timedelta(hours=1))]
        assert _get(rows, not_before=OPEN_T) is None

    def test_it_narrows_two_candidates_to_one(self):
        rows = [
            _exec(9.99, at=OPEN_T - timedelta(hours=1)),  # pre-open, dropped
            _exec(1.10, at=OPEN_T + timedelta(hours=2)),
        ]
        assert _get(rows, not_before=OPEN_T)["closing_price"] == 1.10

    def test_an_unparseable_cutoff_does_not_silently_disable_the_filter(self):
        """_to_epoch_ms returns None for garbage. That must mean 'no cutoff',
        never 'cutoff of zero' — and it must not crash."""
        assert _to_epoch_ms("not a timestamp") is None
        assert _get([_exec(1.10)], not_before="not a timestamp")["closing_price"] == 1.10

    @pytest.mark.parametrize("form", [
        OPEN_T,                            # datetime
        OPEN_T.isoformat(),                # ISO string
        OPEN_T.timestamp(),                # epoch seconds
        OPEN_T.timestamp() * 1000.0,       # epoch millis
    ])
    def test_every_timestamp_form_is_accepted(self, form):
        rows = [_exec(9.99, at=OPEN_T - timedelta(hours=1)), _exec(1.10)]
        assert _get(rows, not_before=form)["closing_price"] == 1.10


class TestQuantityHint:
    def test_it_breaks_a_tie_when_exactly_one_matches(self):
        rows = [_exec(1.10, size=7), _exec(2.00, size=3)]
        assert _get(rows, expect_quantity=7)["closing_price"] == 1.10

    def test_it_does_NOT_break_a_tie_when_several_match(self):
        """Two 7-lots are still indistinguishable — the hint must not create
        false confidence."""
        rows = [_exec(1.10, size=7), _exec(2.00, size=7)]
        assert _get(rows, expect_quantity=7) is None

    def test_sign_is_ignored(self):
        """A closing buy may be reported as -7 or 7 depending on convention."""
        rows = [_exec(1.10, size=-7), _exec(2.00, size=3)]
        assert _get(rows, expect_quantity=7)["closing_price"] == 1.10


class TestOpenCloseMarkerWhenIBKRSuppliesOne:
    """The field name is doc-sourced, not observed — the endpoint returned
    nothing until the accountId fix. So probe several spellings and treat
    absence as 'unknown', never as a default."""

    @pytest.mark.parametrize("key", ["open_close", "openClose", "open_close_indicator"])
    def test_a_closing_marker_wins_outright(self, key):
        rows = [_exec(1.10, **{key: "C"}), _exec(2.00, **{key: "O"})]
        assert _get(rows)["closing_price"] == 1.10

    @pytest.mark.parametrize("val", ["C", "c", "Close", "CLOSING"])
    def test_closing_spellings(self, val):
        rows = [_exec(1.10, open_close=val), _exec(2.00, open_close="O")]
        assert _get(rows)["closing_price"] == 1.10

    def test_all_opening_means_refuse(self):
        """If IBKR says every match OPENED a position, none of them closed
        ours — reporting one would be strictly wrong."""
        assert _get([_exec(2.00, open_close="O")]) is None

    def test_an_absent_marker_is_not_treated_as_closing(self):
        """Records without the field must not be silently promoted."""
        rows = [_exec(1.10, open_close="C"), _exec(2.00)]
        assert _get(rows)["closing_price"] == 1.10

    def test_marker_beats_recency(self):
        rows = [
            _exec(1.10, at=OPEN_T + timedelta(hours=1), open_close="C"),
            _exec(2.00, at=OPEN_T + timedelta(hours=9), open_close="O"),
        ]
        assert _get(rows)["closing_price"] == 1.10


class TestDegenerateInputs:
    def test_no_rows(self):
        assert _get([]) is None

    def test_non_dict_rows_are_skipped(self):
        assert _get(["junk", None, _exec(1.10)])["closing_price"] == 1.10

    def test_a_zero_or_negative_price_is_rejected(self):
        assert _get([_exec(0.0)]) is None
        assert _get([_exec(-1.0)]) is None

    def test_an_unparseable_size_does_not_crash_the_quantity_hint(self):
        rows = [_exec(1.10, size="x"), _exec(2.00, size=3)]
        assert _get(rows, expect_quantity=7) is None   # ambiguous -> refuse
