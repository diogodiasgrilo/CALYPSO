"""Failed-entry unwind P&L must reconcile — it belongs to the DAY, not an entry.

THE INCIDENT (variant B, live paper seat, 2026-09-24). Entry #6 legged into the
Iran-headline melt-up, got 3 of 4 legs filled, then GUARD-FLOOR refused the
short put and the attempt was abandoned. ORDER-010 unwound the 3 filled legs
for **+$5,995 of real money** (verified leg-by-leg against IBKR fill prices).

The day total took it. No entry did:

    total_realized_pnl        = +2,600.00
    sum(entry.realized_pnl)   = -3,395.00      (the day's two real stops)
    DRIFT                     = +5,995.00

The booking site *looked* correct — it called
``_book_realized_pnl(leg_pnl, entry)``. But every one of the five
``_unwind_partial_entry`` call sites ``return False`` immediately afterwards,
so that entry object is discarded and the retry builds a fresh one. The
per-entry write went to an object nobody kept.

WHY AGGREGATE-ONLY IS THE RIGHT ANSWER, not "attach it to the retry".
A failed attempt never becomes a position. Forcing its P&L onto whichever
entry eventually occupies that slot would hand slot_edge a +$5,995 entry that
never happened — corrupting the very per-entry attribution this machinery
exists to provide. The day is the honest owner, and
``_unattributed_overlay_pnl()`` is the existing, tested channel for exactly
that (Brandon already used it for aggregate-only overlay hedges).

THE CONTROL THAT MATTERS: ``test_brandon_override_EXTENDS_base``. Brandon
overrides ``_unattributed_overlay_pnl`` and variant B *is* Brandon — so a
Brandon override that returned only its own component would silently re-open
this bug on the one seat that has money on it, while every base-class test
stayed green.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.base_strategy import MEICStrategy, MEICDailyState  # noqa: E402
from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402


def _today():
    return get_us_market_time().strftime("%Y-%m-%d")


class _Nil(list):
    """Shape-agnostic stand-in for strategy fields irrelevant to this test."""
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _NIL
    def __getitem__(self, k):
        try:
            return list.__getitem__(self, k)
        except Exception:
            return _NIL
    def __float__(self): return 0.0
    def __int__(self): return 0
    def __bool__(self): return False
    def __add__(self, o): return o
    def __radd__(self, o): return o
    def __sub__(self, o): return o
    def __rsub__(self, o): return o
    def __mul__(self, o): return 0
    def __rmul__(self, o): return 0
    def __truediv__(self, o): return 0
    def __lt__(self, o): return True
    def __le__(self, o): return True
    def __gt__(self, o): return False
    def __ge__(self, o): return False
    def get(self, k, d=None): return d
    def keys(self): return []
    def items(self): return []
    def values(self): return []
    def strftime(self, fmt): return ""
    def isoformat(self): return ""
    def copy(self): return _NIL


_NIL = _Nil()

def _leg(fill_price):
    return SimpleNamespace(fill_price=fill_price)


def _entry(contracts=7):
    return SimpleNamespace(
        entry_number=6,
        contracts=contracts,
        realized_pnl=0.0,
        legs={
            "short_call": _leg(13.40),
            "long_call": _leg(3.20),
            # The REAL blended open: 5 @ $0.30 + 2 @ $0.25 = $0.285714.
            # Production logs it rounded to "0.29" but computes on the
            # blend — which is why the live line reads -$25.00, not -$28.
            "long_put": _leg(2.0 / 7.0),
        },
    )


class _Concrete(MEICStrategy):
    """MEICStrategy is an ABC; this fills the three abstract slots so the REAL
    _unwind_partial_entry / _book_realized_pnl bodies can be exercised."""
    def _calculate_strikes(self, *a, **k):  # pragma: no cover - unused
        raise NotImplementedError

    def _check_stop_losses(self, *a, **k):  # pragma: no cover - unused
        raise NotImplementedError

    def _initiate_entry(self, *a, **k):  # pragma: no cover - unused
        raise NotImplementedError


def _strategy(close_prices):
    """Minimal strategy carrying only what _unwind_partial_entry touches."""
    s = _Concrete.__new__(_Concrete)
    s.dry_run = False
    s.daily_state = MEICDailyState(date="2026-09-24")
    s.commission_per_leg = 1.15
    s.registry = SimpleNamespace(unregister=MagicMock())
    calls = []

    def _close(instrument_id, side, quantity):
        calls.append((instrument_id, side, quantity))
        return {"filled": True, "fill_price": close_prices[instrument_id],
                "order_id": f"ord{instrument_id}"}

    s._close_leg_order = _close
    s._close_calls = calls
    return s


# --------------------------------------------------------------------------
class TestDailyStateField:
    def test_defaults_to_zero(self):
        assert MEICDailyState().failed_entry_unattributed_pnl == 0.0

    def test_a_fresh_day_starts_clean(self):
        """It lives on daily_state precisely so the new day resets it."""
        d = MEICDailyState(date="2026-09-24")
        d.failed_entry_unattributed_pnl = 5995.0
        assert MEICDailyState(date="2026-09-25").failed_entry_unattributed_pnl == 0.0


# --------------------------------------------------------------------------
class TestUnwindBooking:
    """Replays 2026-09-24 Entry #6 through the real booking site."""

    def _run(self):
        s = _strategy({101: 8.20, 102: 6.60, 103: 0.25})
        e = _entry()
        _Concrete._unwind_partial_entry(
            s,
            [("short_call", None, 101), ("long_call", None, 102), ("long_put", None, 103)],
            e,
        )
        return s, e

    def test_reproduces_the_real_5995(self):
        s, _ = self._run()
        # short 13.40->8.20 = +3640 | long 3.20->6.60 = +2380 | put .2857->.25 = -25
        assert round(s.daily_state.total_realized_pnl, 2) == 5995.00

    def test_the_money_is_recorded_as_unattributed(self):
        s, _ = self._run()
        assert round(s.daily_state.failed_entry_unattributed_pnl, 2) == 5995.00

    def test_the_discarded_entry_is_NOT_credited(self):
        """The old behaviour wrote here — onto an object the caller throws away."""
        _, e = self._run()
        assert e.realized_pnl == 0.0

    def test_reconcile_identity_holds(self):
        """The exact check the settlement RECONCILE guard performs.

        Day's two real stops summed -3,395 on their entries; the unwind is
        unattributed. Identity: per_entry + unattributed == total.
        """
        s, _ = self._run()
        s.daily_state.total_realized_pnl += -3395.00      # E#4 + E#5 call stops
        per_entry_sum = -3395.00
        unattributed = _Concrete._unattributed_overlay_pnl(s)
        assert round(s.daily_state.total_realized_pnl, 2) == 2600.00   # the live number
        drift = (per_entry_sum + unattributed) - s.daily_state.total_realized_pnl
        assert abs(drift) < 0.01, f"RECONCILE would still report drift ${drift:.2f}"

    def test_a_losing_unwind_is_signed_correctly(self):
        s = _strategy({101: 15.00, 102: 2.00, 103: 0.10})
        _Concrete._unwind_partial_entry(
            s,
            [("short_call", None, 101), ("long_call", None, 102), ("long_put", None, 103)],
            _entry(),
        )
        # short 13.40->15.00 = -1120 | long 3.20->2.00 = -840 | put .2857->.10 = -130
        assert round(s.daily_state.failed_entry_unattributed_pnl, 2) == -2090.00
        assert round(s.daily_state.total_realized_pnl, 2) == -2090.00


# --------------------------------------------------------------------------
class TestUnattributedAccessor:
    def test_base_surfaces_the_accumulator(self):
        s = SimpleNamespace(daily_state=MEICDailyState())
        s.daily_state.failed_entry_unattributed_pnl = 1234.5
        assert MEICStrategy._unattributed_overlay_pnl(s) == 1234.5

    def test_base_tolerates_a_legacy_daily_state(self):
        """A state object from before this field existed must not explode."""
        s = SimpleNamespace(daily_state=SimpleNamespace())
        assert MEICStrategy._unattributed_overlay_pnl(s) == 0.0

    def test_brandon_override_EXTENDS_base(self):
        """CONTROL. Variant B is Brandon; an override that replaced instead of
        extended would re-open this bug on the live seat alone."""
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.daily_state = MEICDailyState()
        s.daily_state.failed_entry_unattributed_pnl = 5995.0
        s._brandon_unattributed_overlay = -400.0
        assert s._unattributed_overlay_pnl() == 5595.0

    def test_brandon_extends_base_on_the_derivation_fallback(self):
        """The no-stored-accumulator path must carry the base component too."""
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        s.daily_state = MEICDailyState()
        s.daily_state.failed_entry_unattributed_pnl = 5995.0
        s._brandon_unattributed_overlay = None
        s._brandon_hedge_settlements = None
        assert s._unattributed_overlay_pnl() == 5995.0


# --------------------------------------------------------------------------
class TestSurvivesRestart:
    """The accumulator explains a total that IS persisted, so it must persist too.

    Otherwise a restart between the ORDER-010 booking and the daily summary
    restores total_realized_pnl with nothing to explain the unattributed part,
    and RECONCILE reports the restored total as drift — the same failure mode
    Brandon hit in 2026-09-10 and fixed by persisting its own accumulator.
    """

    class _Harness(HydraStrategy):
        """Supplies a benign, shape-agnostic default for the many unrelated
        fields ``_save_state_to_disk`` touches, so the REAL save and load
        bodies run end-to-end instead of being mocked away.

        The sentinel subclasses ``list`` so it iterates, slices, is falsy and
        serialises as ``[]``; the numeric dunders keep arithmetic sites happy.
        Nothing under test reads it — the assertions are on one explicit float.
        """
        def __getattr__(self, name):  # only called when normally absent
            if name.startswith("__"):
                raise AttributeError(name)
            return _NIL

    def _harness(self, tmp_path):
        from bots.hydra.base_strategy import MEICDailyState, MEICState
        s = self._Harness.__new__(self._Harness)
        s.daily_state = MEICDailyState(date=_today())
        s.state = MEICState.MONITORING
        s.state_file = str(tmp_path / "hydra_state.json")
        return s

    def test_round_trip_preserves_the_unattributed_total(self, tmp_path):
        """SCOPE, stated honestly: the sentinel harness trips a later,
        unrelated comparison inside ``_load_state_file_history`` (logged as
        "Could not load state file history: '>' not supported...") and that
        body catches it. The two restores asserted below run BEFORE that point
        — which is exactly why both assertions are meaningful and why control
        runs that delete either line turn this red. This test does NOT cover
        the remainder of the load body; it is not evidence about it.
        """
        from bots.hydra.base_strategy import MEICDailyState
        import json

        saver = self._harness(tmp_path)
        saver.daily_state.total_realized_pnl = 2600.0
        saver.daily_state.failed_entry_unattributed_pnl = 5995.0
        HydraStrategy._save_state_to_disk(saver)

        assert os.path.exists(saver.state_file), "save did not write (see logged error)"
        written = json.load(open(saver.state_file))
        assert written["failed_entry_unattributed_pnl"] == 5995.0

        loader = self._harness(tmp_path)
        loader.daily_state = MEICDailyState(date=_today())
        HydraStrategy._load_state_file_history(loader)
        assert loader.daily_state.total_realized_pnl == 2600.0
        assert loader.daily_state.failed_entry_unattributed_pnl == 5995.0

    def test_a_state_file_predating_the_field_loads_as_zero(self, tmp_path):
        """Back-compat: yesterday's file has no such key."""
        from bots.hydra.base_strategy import MEICDailyState
        import json

        saver = self._harness(tmp_path)
        saver.daily_state.total_realized_pnl = 100.0
        HydraStrategy._save_state_to_disk(saver)
        d = json.load(open(saver.state_file))
        d.pop("failed_entry_unattributed_pnl", None)
        json.dump(d, open(saver.state_file, "w"))

        loader = self._harness(tmp_path)
        loader.daily_state = MEICDailyState(date=_today())
        HydraStrategy._load_state_file_history(loader)
        assert loader.daily_state.failed_entry_unattributed_pnl == 0.0
