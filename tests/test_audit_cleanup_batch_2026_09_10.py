"""
Three verified items from the 2026-09-10 audit, shipped together.

1. UNWIND ORDER — close SHORT legs first. An unwind runs because an entry failed
   part-way, so what is held is an arbitrary subset of the four legs, and the
   dangerous subset is always one containing a short without its protective
   long. Closing longs first strips protection off shorts that are still open;
   if a later short close then fails, the account is left holding a NAKED short.

2. SLOT ANALYZER — `CANONICAL_SLOTS` was missing "12:45". B has run a 7-slot
   grid ending at 12:45 since the 2026-07-24 swap, so every 12:45 entry was
   silently bucketed as "other" and dropped from per-slot scoring. Not cosmetic:
   12:45 is B's BEST live-era slot (+$293/trade, closest strikes at 26.5pt, and
   the only slot the hedge's 12:30 cutoff made unhedgeable) — the slot most
   worth measuring was the one being discarded.

3. AGENT TIMERS — HERMES (19:00 ET) and HOMER (19:30 ET) both fired roughly
   THREE HOURS before settlement completed, every day. Measured
   SETTLEMENT_COMPLETE times from the bots' own logs, variants B and C:
       2026-09-03 21:45   2026-09-04 22:32/22:37
       2026-09-08 22:30   2026-09-09 22:15
   Worst observed 22:37 ET. Both timer descriptions already said
   "post-settlement" — the schedules simply never matched the intent, so the
   daily analysis and the committed trading journal were built on unsettled
   numbers.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]

from bots.hydra.slot_edge import CANONICAL_SLOTS, slot_for  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Unwind closes shorts first
# ---------------------------------------------------------------------------

def _unwind_strat():
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.commission_per_leg = 1.15
    s.daily_state = SimpleNamespace(total_commission=0.0, total_realized_pnl=0.0)
    s.registry = MagicMock()
    s._cancel_order = MagicMock()
    s._book_realized_pnl = lambda amt, entry=None: None
    s.closed_order = []

    def _close(instrument_id, side, quantity):
        s.closed_order.append((instrument_id, side))
        return {"filled": True, "order_id": f"o{instrument_id}", "fill_price": 1.0}

    s._close_leg_order = _close
    return s


def _entry(contracts=7):
    legs = {n: SimpleNamespace(fill_price=1.0)
            for n in ("short_call", "long_call", "short_put", "long_put")}
    return SimpleNamespace(entry_number=3, contracts=contracts, legs=legs,
                           realized_pnl=0.0)


class TestUnwindClosesShortsFirst:
    def test_longs_listed_first_are_still_closed_last(self):
        """The ordering must come from the leg TYPE, not from input order."""
        s = _unwind_strat()
        HydraStrategy._unwind_partial_entry(s, [
            ("long_call", "p1", 101),
            ("long_put", "p2", 102),
            ("short_call", "p3", 103),
        ], _entry())
        assert s.closed_order[0] == (103, "BUY"), "short must be closed first"

    def test_all_shorts_precede_all_longs(self):
        s = _unwind_strat()
        HydraStrategy._unwind_partial_entry(s, [
            ("long_call", "p1", 101),
            ("short_put", "p2", 102),
            ("long_put", "p3", 103),
            ("short_call", "p4", 104),
        ], _entry())
        kinds = ["short" if cid in (102, 104) else "long"
                 for cid, _ in s.closed_order]
        assert kinds == ["short", "short", "long", "long"]

    def test_every_leg_is_still_closed(self):
        """Ordering only — nothing may be dropped."""
        s = _unwind_strat()
        legs = [("long_call", "p1", 101), ("short_put", "p2", 102),
                ("long_put", "p3", 103), ("short_call", "p4", 104)]
        HydraStrategy._unwind_partial_entry(s, legs, _entry())
        assert sorted(c for c, _ in s.closed_order) == [101, 102, 103, 104]

    def test_sort_is_stable_within_each_group(self):
        """Legs of the same kind keep their original relative order, so this
        change cannot reshuffle anything it was not meant to."""
        s = _unwind_strat()
        HydraStrategy._unwind_partial_entry(s, [
            ("short_put", "p1", 201),
            ("short_call", "p2", 202),
            ("long_put", "p3", 203),
            ("long_call", "p4", 204),
        ], _entry())
        assert [c for c, _ in s.closed_order] == [201, 202, 203, 204]

    def test_sides_are_still_correct(self):
        """short -> BUY to close, long -> SELL. Reordering must not disturb it."""
        s = _unwind_strat()
        HydraStrategy._unwind_partial_entry(s, [
            ("long_call", "p1", 101), ("short_call", "p2", 102),
        ], _entry())
        assert dict(s.closed_order) == {102: "BUY", 101: "SELL"}

    def test_shorts_only_is_unchanged(self):
        s = _unwind_strat()
        HydraStrategy._unwind_partial_entry(s, [
            ("short_call", "p1", 101), ("short_put", "p2", 102),
        ], _entry())
        assert [c for c, _ in s.closed_order] == [101, 102]

    def test_legs_without_a_conid_are_still_skipped(self):
        """P7-audit H1: the gate is the conid, not the position id."""
        s = _unwind_strat()
        HydraStrategy._unwind_partial_entry(s, [
            ("short_call", "p1", None), ("long_call", "p2", 102),
        ], _entry())
        assert [c for c, _ in s.closed_order] == [102]


# ---------------------------------------------------------------------------
# 2. The slot analyzer sees 12:45
# ---------------------------------------------------------------------------

class TestSlotAnalyzerCoversTheWholeGrid:
    B_GRID = ["09:45", "10:15", "10:45", "11:15", "11:45", "12:15", "12:45"]

    def test_1245_is_a_canonical_slot(self):
        assert "12:45" in CANONICAL_SLOTS

    @pytest.mark.parametrize("hhmm", B_GRID)
    def test_every_slot_b_actually_trades_maps_to_itself(self, hhmm):
        """The regression: 12:45 previously fell through to "other"."""
        assert slot_for(f"2026-09-10 {hhmm}:03") == hhmm

    def test_1245_is_not_absorbed_into_1215(self):
        """12:15 and 12:45 are 30 min apart and the tolerance is 12 min, so they
        must stay distinct rather than one swallowing the other."""
        assert slot_for("2026-09-10 12:45:00") == "12:45"
        assert slot_for("2026-09-10 12:15:00") == "12:15"

    def test_a_time_in_no_slot_is_still_other(self):
        """The narrowing must not turn "other" into a catch-all that never fires."""
        assert slot_for("2026-09-10 15:30:00") == "other"

    def test_e6_still_resolves(self):
        assert slot_for("2026-09-10 14:00:00") == "E6 14:00"

    def test_bad_input_is_still_other(self):
        for bad in (None, "", "junk", "99:99"):
            assert slot_for(bad) == "other"


# ---------------------------------------------------------------------------
# 3. Agent timers fire after settlement
# ---------------------------------------------------------------------------

#: Worst SETTLEMENT_COMPLETE observed in the bots' own logs, in ET minutes.
#: 2026-09-04 22:37. 0DTE SPX is PM-settled and IBKR's position feed clears
#: hours after the 16:00 close, so "after the close" is nowhere near enough.
WORST_SETTLEMENT_ET_MIN = 22 * 60 + 37


def _timer_minutes(name: str) -> int:
    text = (REPO / "deploy" / name).read_text()
    m = re.search(r"^OnCalendar=.*?(\d{2}):(\d{2}):\d{2}\s+America/New_York",
                  text, re.M)
    assert m, f"no OnCalendar line found in {name}"
    return int(m.group(1)) * 60 + int(m.group(2))


class TestAgentTimersRunAfterSettlement:
    @pytest.mark.parametrize("timer", ["hermes.timer", "homer.timer"])
    def test_it_fires_after_the_worst_observed_settlement(self, timer):
        assert _timer_minutes(timer) > WORST_SETTLEMENT_ET_MIN, (
            f"{timer} would analyse an unsettled day"
        )

    @pytest.mark.parametrize("timer", ["hermes.timer", "homer.timer"])
    def test_it_keeps_a_real_margin_not_a_hairline(self, timer):
        assert _timer_minutes(timer) - WORST_SETTLEMENT_ET_MIN >= 20

    def test_homer_still_runs_after_hermes(self):
        """HOMER writes the journal; HERMES analyses the day. The original
        30-minute ordering must survive the move."""
        assert _timer_minutes("homer.timer") - _timer_minutes("hermes.timer") == 30

    @pytest.mark.parametrize("timer", ["hermes.timer", "homer.timer"])
    def test_it_still_completes_before_midnight(self, timer):
        """HOMER detects missing trading days by calendar date, so a run that
        lands on the NEXT date would look like a skipped day."""
        assert _timer_minutes(timer) <= 23 * 60 + 30

    @pytest.mark.parametrize("timer", ["hermes.timer", "homer.timer"])
    def test_the_description_matches_the_schedule(self, timer):
        """Both files claimed "post-settlement" for months while scheduled three
        hours early. Keep the stated hour and the OnCalendar hour in step."""
        text = (REPO / "deploy" / timer).read_text()
        hour = _timer_minutes(timer) // 60
        display = hour if hour <= 12 else hour - 12
        assert f"{display}" in text.split("Description=")[1].split("\n")[0]
