"""H reported a false CRITICAL on every restart that followed a skip (2026-11-11).

THE CHAIN. The shared `_record_skipped_entry` deliberately appends a minimal
`HydraIronCondorEntry` to `daily_state.entries` so the dashboard can display a
skip. H's restart recovery upgrades anything that is not a `LongStrangleEntry`
back into one by reading its cost basis out of `ls_entries` — and a SKIP is
recorded in `ls_skipped`, so no `ls_entries` row exists, by design. The
placeholder therefore hit the "no row" branch and logged:

    CRITICAL  LS: entry #1 restored with NO ls_entries row — its debit is
              unknown, so its P&L is not computable.

MEASURED. H has opened exactly two real positions, 2026-09-24 and 2026-09-25.
The CRITICAL fired on three restarts — 09-30, 10-01 and 11-10 — each following
a skip. All three were false. The 11-10 instance is fully reconstructed from the
pre-restart snapshot: date=2026-11-10, entries=1, entry #1 at 09:45:33 (the
skew-veto timestamp) with long_call_strike=0.0 and long_put_strike=0.0.

SECOND, LATENT DEFECT in the same function: the `ls_entries` lookup keyed on
TODAY rather than on the date the restored entries belong to. Harmless on a
same-day restart, which is why it never showed, but a restart crossing midnight
with a live position would have reported the same false CRITICAL for a real
entry.
"""
import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bots.hydra.long_strangle_strategy import LongStrangleStrategy, LongStrangleEntry
from bots.hydra.strategy import HydraIronCondorEntry


def _placeholder(n=1, reason="skew 39.5% > 35.0% tolerance"):
    """Exactly what `_record_skipped_entry` appends."""
    e = HydraIronCondorEntry(entry_number=n)
    e.is_complete = True
    e.call_side_skipped = True
    e.put_side_skipped = True
    e.skip_reason = reason
    return e


def _real_entry_without_cost_basis(n=1):
    """A genuine entry the base restore handed back as the wrong class."""
    e = HydraIronCondorEntry(entry_number=n)
    e.is_complete = True
    return e


# --------------------------------------------------- the discriminator itself

def test_the_skip_placeholder_is_recognised():
    assert LongStrangleStrategy._is_skip_placeholder(_placeholder()) is True


def test_a_real_entry_is_NOT_mistaken_for_a_placeholder():
    assert LongStrangleStrategy._is_skip_placeholder(
        _real_entry_without_cost_basis()) is False


def test_a_long_strangle_entry_is_not_a_placeholder():
    assert LongStrangleStrategy._is_skip_placeholder(
        LongStrangleEntry(entry_number=1)) is False


@pytest.mark.parametrize("call_sk,put_sk,reason,expected,why", [
    (True,  True,  "skew", True,  "the placeholder"),
    (True,  False, "skew", False, "ONE-SIDED IC entry — must not be eaten"),
    (False, True,  "skew", False, "ONE-SIDED IC entry — must not be eaten"),
    (True,  True,  "",     False, "both flags but no reason: not the placeholder"),
    (False, False, "skew", False, "a reason alone is not enough"),
])
def test_requires_both_sides_AND_a_reason(call_sk, put_sk, reason, expected, why):
    """Either test alone misreads a one-sided entry. Pin the conjunction."""
    e = HydraIronCondorEntry(entry_number=1)
    e.call_side_skipped, e.put_side_skipped, e.skip_reason = call_sk, put_sk, reason
    assert LongStrangleStrategy._is_skip_placeholder(e) is expected, why


def test_missing_attributes_are_not_a_placeholder():
    class _Bare:
        entry_number = 1
    assert LongStrangleStrategy._is_skip_placeholder(_Bare()) is False


# ------------------------------------------------- the restore, end to end

class _Recorder:
    def __init__(self, rows_by_date):
        self.rows_by_date = rows_by_date
        self.asked = []

    def fetch_entries(self, date):
        self.asked.append(date)
        return self.rows_by_date.get(date, [])


class _DailyState:
    def __init__(self, entries, date="2026-11-10"):
        self.entries = entries
        self.date = date


def _rig(entries, *, rows=None, state_date="2026-11-10"):
    s = object.__new__(LongStrangleStrategy)
    s.daily_state = _DailyState(entries, state_date)
    s.ls_recorder = _Recorder(rows or {})
    return s


_REAL_ROW = {
    "entry_number": 1, "call_debit": 120.0, "put_debit": 95.0, "contracts": 1,
    "call_strike": 772.0, "put_strike": 767.0,
    "long_call_uic": 111, "long_put_uic": 222,
    "em_source": "straddle", "expected_move": 5.3, "skew_gap_pct": 12.0,
}


def test_a_skipped_entry_produces_NO_critical(caplog):
    """THE DEFECT. 11-10's exact state: one skip placeholder, no ls_entries row."""
    s = _rig([_placeholder()])
    with caplog.at_level("CRITICAL"):
        LongStrangleStrategy._restore_long_strangle_entries(s)
    assert "NO ls_entries row" not in caplog.text


def test_a_skipped_entry_is_left_in_place_for_the_dashboard():
    """It must not be dropped — the dashboard displays skips."""
    ph = _placeholder()
    s = _rig([ph])
    LongStrangleStrategy._restore_long_strangle_entries(s)
    assert s.daily_state.entries == [ph]


def test_a_skip_does_not_even_hit_the_database():
    """No stale entries means no reason to query at all."""
    s = _rig([_placeholder()])
    LongStrangleStrategy._restore_long_strangle_entries(s)
    assert s.ls_recorder.asked == [], "queried the DB for a skip placeholder"


def test_a_GENUINE_missing_row_STILL_criticals(caplog):
    """The negative control. The alarm must survive the fix."""
    s = _rig([_real_entry_without_cost_basis()])
    with caplog.at_level("CRITICAL"):
        LongStrangleStrategy._restore_long_strangle_entries(s)
    assert "NO ls_entries row" in caplog.text


def test_a_real_entry_is_still_upgraded_with_its_debits():
    s = _rig([_real_entry_without_cost_basis()],
             rows={"2026-11-10": [_REAL_ROW]})
    LongStrangleStrategy._restore_long_strangle_entries(s)
    got = s.daily_state.entries[0]
    assert isinstance(got, LongStrangleEntry)
    assert got.call_debit == 120.0 and got.put_debit == 95.0
    assert got.long_call_strike == 772.0


def test_a_skip_beside_a_real_entry_does_not_block_the_real_one(caplog):
    """The mixed day: entry #1 skipped, entry #2 genuine."""
    row2 = dict(_REAL_ROW, entry_number=2)
    s = _rig([_placeholder(1), _real_entry_without_cost_basis(2)],
             rows={"2026-11-10": [row2]})
    with caplog.at_level("CRITICAL"):
        LongStrangleStrategy._restore_long_strangle_entries(s)
    assert "NO ls_entries row" not in caplog.text
    assert isinstance(s.daily_state.entries[1], LongStrangleEntry)
    assert s.daily_state.entries[1].call_debit == 120.0
    assert LongStrangleStrategy._is_skip_placeholder(s.daily_state.entries[0])


# ------------------------------------------------- the latent date-key defect

def test_lookup_uses_the_STATE_date_not_today():
    """A restart crossing midnight must not look up the wrong day."""
    s = _rig([_real_entry_without_cost_basis()],
             rows={"2026-11-10": [_REAL_ROW]}, state_date="2026-11-10")
    with patch("bots.hydra.long_strangle_strategy.get_us_market_time") as now:
        now.return_value.strftime.return_value = "2026-11-11"   # the NEXT day
        LongStrangleStrategy._restore_long_strangle_entries(s)
    assert s.ls_recorder.asked == ["2026-11-10"], (
        f"keyed on the wrong date: {s.ls_recorder.asked}")
    assert isinstance(s.daily_state.entries[0], LongStrangleEntry)


def test_falls_back_to_today_when_the_state_carries_no_date():
    s = _rig([_real_entry_without_cost_basis()], rows={})
    s.daily_state.date = None
    with patch("bots.hydra.long_strangle_strategy.get_us_market_time") as now:
        now.return_value.strftime.return_value = "2026-11-11"
        LongStrangleStrategy._restore_long_strangle_entries(s)
    assert s.ls_recorder.asked == ["2026-11-11"]


def test_no_entries_is_a_no_op():
    s = _rig([])
    LongStrangleStrategy._restore_long_strangle_entries(s)
    assert s.ls_recorder.asked == []
