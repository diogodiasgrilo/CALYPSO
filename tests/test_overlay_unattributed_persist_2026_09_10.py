"""
Persist the aggregate-only overlay total so it survives the restart that
CAUSES it (2026-09-10).

THE BUG. Brandon overlay P&L is normally attributed to the entry it defended.
When that entry is absent from `daily_state.entries` at settle — a post-close or
cross-day restart — it is booked to the day AGGREGATE only, and
`daily_summaries.unattributed_overlay_pnl` exists to record the gap so the
reconciliation still closes.

But `_unattributed_overlay_pnl()` DERIVED that number from
`_brandon_hedge_settlements`, which is NOT persisted. So the very restart that
causes an aggregate-only booking is the one that loses the record of it — the
scalar failed in precisely the situation it was built for.

MEASURED: on 2026-07-07 variant B booked -$2,532.58 to the aggregate, the column
was written 0.0, and slot_edge reported the day as a $2,533 unexplained
attribution miss for two months until a manual back-fill on 2026-09-10. The
column had NEVER held a non-zero value in B's entire history.

FIX: accumulate at the booking site, where the fact is known, and persist it in
hydra_state.json alongside `_brandon_overlay_booked` — the guard it belongs
with. Reset it on the new-day reset, since it is a PER-DAY total.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _s(unattributed=None, entries=()):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    if unattributed is not None:
        s._brandon_unattributed_overlay = unattributed
    s.daily_state = SimpleNamespace(entries=list(entries))
    s._brandon_hedge_settlements = []
    return s


class TestPersistedAccumulator:
    def test_the_stored_value_is_returned_verbatim(self):
        assert BrandonHydraStrategy._unattributed_overlay_pnl(
            _s(unattributed=-2532.58)) == pytest.approx(-2532.58)

    def test_a_stored_ZERO_must_not_fall_through_to_the_derivation(self):
        """0.0 is FALSY, so `if stored:` would fall through to the old
        derivation — and resurrect a stale in-process settlement that the daily
        reset had correctly zeroed.

        Constructed so the two branches DISAGREE: stored is 0.0 (correct, post
        reset) while a stale settlement still sits in memory. A truthiness check
        returns -1960.0 here and manufactures drift on a clean day. An earlier
        version of this test used an empty settlement list, so both branches
        returned 0.0 and it passed against the bug."""
        s = _s(unattributed=0.0)
        s._brandon_hedge_settlements = [
            SimpleNamespace(entry_number=4, total_pnl=-1960.0)]   # stale
        s.daily_state.entries = []
        assert BrandonHydraStrategy._unattributed_overlay_pnl(s) == 0.0

    def test_it_no_longer_depends_on_in_process_settlements(self):
        """THE BUG: previously this returned 0.0 after a restart because
        _brandon_hedge_settlements is not persisted. The stored value must win
        even with no settlements in memory."""
        s = _s(unattributed=-2532.58)
        s._brandon_hedge_settlements = []          # wiped by the restart
        s.daily_state.entries = []
        assert BrandonHydraStrategy._unattributed_overlay_pnl(s) == pytest.approx(-2532.58)

    def test_falls_back_to_the_old_derivation_when_the_field_is_absent(self):
        """Back-compat for an object built before the field existed."""
        s = _s()                                    # no accumulator attribute
        s._brandon_hedge_settlements = [
            SimpleNamespace(entry_number=4, total_pnl=-1960.0)]
        s.daily_state.entries = []                  # entry 4 absent -> unattributed
        assert BrandonHydraStrategy._unattributed_overlay_pnl(s) == pytest.approx(-1960.0)

    def test_the_old_derivation_still_excludes_ATTRIBUTED_settlements(self):
        s = _s()
        s._brandon_hedge_settlements = [
            SimpleNamespace(entry_number=4, total_pnl=-1960.0)]
        s.daily_state.entries = [SimpleNamespace(entry_number=4)]   # present
        assert BrandonHydraStrategy._unattributed_overlay_pnl(s) == 0.0


class TestPersistenceRoundTrip:
    def test_the_state_writer_emits_the_field(self):
        import inspect
        src = inspect.getsource(HydraStrategy._save_state_to_disk)
        assert '"brandon_unattributed_overlay"' in src

    def test_the_loader_restores_it(self):
        import inspect
        src = inspect.getsource(HydraStrategy._load_state_file_history)
        assert "brandon_unattributed_overlay" in src

    def test_it_is_persisted_NEXT_TO_the_guard_it_belongs_with(self):
        """They must be written in the same save. A guard restored without its
        amount is the 2026-07-18 failure mode in reverse."""
        import inspect
        src = inspect.getsource(HydraStrategy._save_state_to_disk)
        assert (src.index('"brandon_overlay_booked"')
                < src.index('"brandon_unattributed_overlay"'))


class TestDailyReset:
    def test_the_reset_clears_it_with_the_guard(self):
        """It is a PER-DAY total consumed by that day's summary row. Left
        running it would carry yesterday's overlay into today and manufacture
        drift on a clean day."""
        import inspect
        src = inspect.getsource(BrandonHydraStrategy._reset_for_new_day)
        assert "_brandon_unattributed_overlay = 0.0" in src
        assert (src.index("_brandon_overlay_booked.clear()")
                < src.index("_brandon_unattributed_overlay = 0.0"))
