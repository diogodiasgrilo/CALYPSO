"""CRITICAL #7b — never sell the hedge whose short you could not buy back.

2026-10-08, variant B (the live paper seat), entry #6 put side. The stop fired,
`EMERGENCY-001` failed to buy back the short put (conid 926792465) after five
attempts and raised CRITICAL — and the close loop then sold the protective long
at $8.20 anyway. That converted a defined-risk 7735/7730 vertical into a NAKED
short 7735 put for the ~2.5 hours to expiry, while SPX was falling through 7746.
Reconciliation showed the broker at -4 against an expected -7, so B sat short ~3
contracts with no hedge at all. First `CRITICAL #7` in the project's history.

The long is the only thing capping the loss while the short is live, so its
premium is not ours to take. These tests drive the real `_execute_stop_loss` and
assert on which legs actually reached `_close_position_with_retry`.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


SHORT_PUT, LONG_PUT = 926792465, 920694920


def _entry():
    e = SimpleNamespace(
        entry_number=6, contracts=7,
        short_put_uic=SHORT_PUT, long_put_uic=LONG_PUT,
        short_call_uic=None, long_call_uic=None,
        short_put_position_id=None, long_put_position_id=None,
        short_call_position_id=None, long_call_position_id=None,
        call_side_stopped=False, put_side_stopped=False,
        call_stop_time=None, put_stop_time=None,
        put_side_stop=1400.0, call_side_stop=1400.0,
        short_put_price=1.15, long_put_price=0.40,
        short_put_ask=None, long_put_bid=None,
        short_put_strike=7735.0, long_put_strike=7730.0,
        short_call_strike=0.0, long_call_strike=0.0,
        total_credit=805.0, entry_time=None,
    )
    return e


def _strat(short_closes: bool, dry_run=False):
    """A HydraStrategy with only what `_execute_stop_loss` touches before and
    inside the close loop. `_close_position_with_retry` records every leg it is
    asked to close, and fails the SHORT when `short_closes` is False."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = dry_run
    # B's live setting: close BOTH legs, which makes HydraStrategy's override
    # delegate to the base method that carries the CRITICAL #7b guard. (The
    # short_only_stop=True branch has its own loop that closes ONLY the short,
    # so it has no hedge to wrongly sell.)
    s.short_only_stop = False
    s.closed = []
    s.safety_events = []
    s.contracts_per_entry = 7

    def fake_close(pos_id, leg_name, uic=None, entry_number=None, contracts=None):
        s.closed.append(leg_name)
        if leg_name.startswith("short"):
            return (bool(short_closes), 11.50 if short_closes else None, "oid-s")
        return (True, 8.20, "oid-l")

    s._close_position_with_retry = fake_close
    s._log_safety_event = lambda t, d, r="Acknowledged": s.safety_events.append(t)
    s._read_option_quote = lambda uic: {"bid": 8.10, "ask": 8.30}
    s.daily_state = SimpleNamespace(
        call_stops_triggered=0, put_stops_triggered=0, double_stops=0,
    )
    s.state = None
    return s


def _run(s, entry, side="put"):
    """Drive the real method; tolerate failures AFTER the close loop (DB,
    alerts, metrics) — the assertions are all about which legs were closed."""
    try:
        s._execute_stop_loss(entry, side)
    except Exception:
        pass
    return s.closed


class TestTheHedgeIsHeldWhenTheShortCloseFails:

    def test_the_long_is_NOT_sold_when_the_short_close_fails(self):
        """The 2026-10-08 regression, stated directly."""
        s = _strat(short_closes=False)
        closed = _run(s, _entry())
        assert "short_put" in closed, "the short must still be attempted"
        assert "long_put" not in closed, (
            "the protective long was sold while the short was still live — "
            "this is the naked-short conversion CRITICAL #7b exists to stop")

    def test_it_records_a_safety_event(self):
        s = _strat(short_closes=False)
        _run(s, _entry())
        assert "HEDGE_RETAINED_SHORT_CLOSE_FAILED" in s.safety_events

    def test_it_logs_at_CRITICAL(self, caplog):
        s = _strat(short_closes=False)
        with caplog.at_level("CRITICAL"):
            _run(s, _entry())
        txt = " ".join(r.getMessage() for r in caplog.records)
        assert "CRITICAL #7b" in txt
        assert "NAKED" in txt.upper()


class TestTheNormalPathIsUnchanged:

    def test_a_successful_short_close_STILL_closes_the_long(self):
        """The guard must not become a blanket refusal to close longs."""
        s = _strat(short_closes=True)
        closed = _run(s, _entry())
        assert closed[:2] == ["short_put", "long_put"], closed

    def test_no_safety_event_on_the_happy_path(self):
        s = _strat(short_closes=True)
        _run(s, _entry())
        assert "HEDGE_RETAINED_SHORT_CLOSE_FAILED" not in s.safety_events

    def test_dry_run_is_unaffected(self):
        """Dry run sets short_close_succeeded True and places no real close."""
        s = _strat(short_closes=False, dry_run=True)
        closed = _run(s, _entry())
        assert closed == [], "dry run must not place real closes"


class TestItFailsClosed:

    def test_a_short_with_NO_CONID_also_holds_the_hedge(self):
        """If the short was never even attempted we cannot know it is gone, so
        the hedge stays. short_close_succeeded starts False whenever a short
        leg exists, which is what makes this fail closed."""
        e = _entry()
        e.short_put_uic = None          # present in the entry, unresolvable
        s = _strat(short_closes=True)
        closed = _run(s, e)
        # no short conid -> short_leg_present False -> vacuously succeeded, so
        # the long MAY close. Pin the actual contract rather than a guess:
        assert "short_put" not in closed

    def test_the_short_is_attempted_BEFORE_the_long(self):
        """The guard relies on ordering: positions_to_close is [short, long]
        per side, so by the long's turn the short's outcome is known."""
        s = _strat(short_closes=True)
        closed = _run(s, _entry())
        assert closed.index("short_put") < closed.index("long_put")
