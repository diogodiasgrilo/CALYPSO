"""
Two fixes that both became more urgent because of changes made earlier today
(2026-09-10).

1. MKT-033 booked NOTHING when it could not read a closing price.
   `_try_sell_long_leg` detects that a long leg was sold externally. When the
   closing execution was unreadable it marked the leg sold, set
   `*_long_sold_revenue = 0.0`, and — the actual defect — never called
   `_book_realized_pnl` at all. The long's value was simply deleted from
   realized P&L. A long option someone paid for is essentially never worth
   exactly zero.

   WHY IT GOT MORE URGENT TODAY: the opening-vs-closing filter added to
   `get_closed_position_price` deliberately returns None whenever it cannot tell
   a closing execution from an opening one. That is the right behaviour, but it
   means THIS branch is now reached more often than before. Fixing the lookup
   without fixing its fallback would have traded a wrong number for a missing
   one.

   The fix estimates from the live bid — the price the long could actually have
   been sold at — books it, and labels it an ESTIMATE. It fails CLOSED on a
   stale/non-real-time quote, because an estimate off a delayed quote is not
   better than no estimate.

2. Every IbkrClient was retained for the life of the process.
   ibind's `auto_register_shutdown` defaults to True and, on EVERY construction,
   `RestClient.register_shutdown_handler()`:
     - calls `atexit.register(_close_handler)` where the handler closes over
       `self`, so atexit pins the client (and its requests Session and
       connection pool) forever; and
     - captures the CURRENT SIGINT/SIGTERM handlers and installs its own that
       chains to them — so each new client captures the PREVIOUS client's
       handler, and one SIGTERM walks a chain N deep after N reconnects, in the
       process that owns the IBKR session.
   calypso-broker re-auths daily and on every session fault, so a broker up for
   weeks holds weeks' worth. We need none of it: `disconnect()` already calls
   `close()` explicitly, main.py installs its own signal handlers, and the
   strategy processes exit via `os._exit()` (2026-09-05 hard-exit fix) which
   bypasses atexit entirely — so the accumulation bought nothing at all.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from shared.ib_client import IBClient  # noqa: E402

LONG_UIC = 555001


def _entry(contracts=7):
    return SimpleNamespace(
        entry_number=2, contracts=contracts,
        long_call_uic=LONG_UIC, long_call_position_id="p1",
        long_put_uic=None, long_put_position_id=None,
        call_long_sold=False, put_long_sold=False,
        call_long_sold_revenue=None, put_long_sold_revenue=None,
        long_call_fill_price=1.00, long_put_fill_price=1.00,
        close_commission=0.0, realized_pnl=0.0,
    )


def _strat(quote, closed=None, realtime=True):
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.commission_per_leg = 1.15
    s.contracts_per_entry = 7
    s.daily_state = SimpleNamespace(total_commission=0.0, total_realized_pnl=0.0)
    s.registry = MagicMock()
    s._save_state_to_disk = MagicMock()
    s._log_safety_event = MagicMock()
    s._position_is_open = lambda *a, **k: False          # long vanished
    s._read_closed_position_price = lambda *a, **k: closed
    s._read_option_quote = lambda uic: quote
    s._option_quote_is_realtime = lambda q: realtime
    s.booked = []
    s._book_realized_pnl = lambda amt, entry=None: (
        s.booked.append(amt),
        setattr(entry, "realized_pnl", (entry.realized_pnl or 0) + amt)
        if entry is not None else None,
    )
    return s


def _run(s, entry):
    return HydraStrategy._try_sell_long_leg(s, entry, "call", open_positions=[])


class TestMkt033EstimatesInsteadOfDeletingTheValue:
    def test_it_books_an_estimate_from_the_live_bid(self):
        """bid 0.80 x 100 x 7 = $560, instead of the old $0."""
        e = _entry()
        s = _strat(quote={"bid": 0.80, "ask": 0.90})
        _run(s, e)
        assert s.booked == [pytest.approx(560.0)]
        assert e.call_long_sold_revenue == pytest.approx(560.0)

    def test_the_old_behaviour_booked_nothing_at_all(self):
        """Guards the actual regression: not '$0 was booked' but 'nothing was'."""
        e = _entry()
        s = _strat(quote={"bid": 0.80})
        _run(s, e)
        assert s.booked, "must book something — the long was SOLD, not vaporised"

    def test_commission_is_charged_on_the_estimated_close(self):
        e = _entry()
        s = _strat(quote={"bid": 0.80})
        _run(s, e)
        assert s.daily_state.total_commission == pytest.approx(1.15 * 7)
        assert e.close_commission == pytest.approx(1.15 * 7)

    def test_it_scales_with_the_entry_contract_count(self):
        e = _entry(contracts=10)
        s = _strat(quote={"bid": 0.80})
        _run(s, e)
        assert s.booked == [pytest.approx(800.0)]

    def test_the_leg_is_still_marked_sold_and_cleared(self):
        """The bookkeeping fix must not disturb the state cleanup."""
        e = _entry()
        s = _strat(quote={"bid": 0.80})
        _run(s, e)
        assert e.call_long_sold is True
        assert e.long_call_uic is None

    def test_a_safety_event_records_that_it_was_an_estimate(self):
        e = _entry()
        s = _strat(quote={"bid": 0.80})
        _run(s, e)
        names = [c.args[0] for c in s._log_safety_event.call_args_list]
        assert "LONG_SOLD_EXTERNAL_ESTIMATED" in names


class TestItFailsClosedRatherThanGuessing:
    def test_a_non_realtime_quote_books_nothing(self):
        """An estimate off a delayed quote is not better than no estimate."""
        e = _entry()
        s = _strat(quote={"bid": 0.80}, realtime=False)
        _run(s, e)
        assert s.booked == []
        assert e.call_long_sold_revenue == 0.0

    def test_no_quote_at_all_books_nothing(self):
        e = _entry()
        s = _strat(quote=None)
        _run(s, e)
        assert s.booked == []
        assert e.call_long_sold_revenue == 0.0

    @pytest.mark.parametrize("bid", [0, 0.0, None, -1.0])
    def test_a_non_positive_bid_books_nothing(self, bid):
        e = _entry()
        s = _strat(quote={"bid": bid})
        _run(s, e)
        assert s.booked == []

    def test_a_raising_quote_lookup_does_not_break_the_cleanup(self):
        e = _entry()
        s = _strat(quote=None)
        s._read_option_quote = MagicMock(side_effect=RuntimeError("broker down"))
        _run(s, e)
        assert e.call_long_sold is True      # state cleanup still happened
        assert s.booked == []

    def test_a_real_closing_execution_still_wins_over_the_estimate(self):
        """The estimate is a FALLBACK. A readable execution must take priority."""
        e = _entry()
        s = _strat(quote={"bid": 0.80}, closed={"closing_price": 1.50})
        _run(s, e)
        assert s.booked == [pytest.approx(1.50 * 100 * 7)]


class TestIbkrClientLifecycle:
    def test_construction_disables_ibinds_shutdown_registration(self):
        src = inspect.getsource(IBClient.connect)
        assert "auto_register_shutdown=False" in src, (
            "ibind would otherwise atexit-pin every client AND chain a new "
            "SIGTERM handler onto the previous one at every reconnect"
        )

    def test_disconnect_drops_the_client_reference(self):
        src = inspect.getsource(IBClient.disconnect)
        assert "self._client = None" in src

    def test_require_connected_treats_a_none_client_as_disconnected(self):
        """What makes dropping the reference safe: no AttributeError leaks out."""
        from shared.ib_client import IBClientError
        import threading
        c = IBClient.__new__(IBClient)
        c._call_lock = threading.RLock()
        c._connected = True
        c._client = None
        with pytest.raises(IBClientError, match="not connected"):
            IBClient._require_connected(c)

    def test_ibind_really_does_pin_clients_by_default(self):
        """Documents the upstream behaviour being worked around. If this stops
        holding, ibind changed and the workaround should be re-examined rather
        than carried forever."""
        import ibind.base.rest_client as rc
        src = inspect.getsource(rc.RestClient.register_shutdown_handler)
        assert "atexit.register" in src
        assert "signal.signal" in src
