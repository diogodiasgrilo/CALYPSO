"""An entry that never opened a position ties up NO capital.

Found on the morning check, 2026-09-18, in real data rather than a test.

G's `daily_returns` row for 2026-09-17 read:

    net_pnl: 0.0        capital_deployed: 60000.0

It placed nothing that day — both entries were skipped for the FOMC T+1
blackout — yet the settlement booked $60,000 of "deployed capital". Two skipped
entries x the $30,000 naked-margin floor.

WHY ONLY G. `_entry_margin`'s base (`defined_risk`) implementation filters these
BY ACCIDENT: a skipped entry has no spread width, and `width <= 0` returns 0.0
which the sweep skips. The `broker_margin` override returns a flat per-contract
floor and has no such accident, so it counted them.

That inflates the ROI denominator on every no-trade day and gets worse the more
days a strategy sits out — exactly backwards for a strategy whose whole design
is to trade selectively.

The guard therefore lives in the SWEEP, not in `_entry_margin`: it is a
basis-independent fact, and putting it where each basis must remember it is how
this happened.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402


def _entry(**kw):
    base = dict(
        call_side_skipped=False, put_side_skipped=False, execution_failed=False,
        spread_width=25.0, contracts=1,
        entry_time="2026-09-17 10:45:00", close_time=None,
        call_stop_time=None, put_stop_time=None,
        short_call_position_id="x", short_put_position_id="y",
        call_side_stopped=False, put_side_stopped=False,
        call_side_expired=False, put_side_expired=False,
    )
    base.update(kw)
    return NS(**base)


@pytest.mark.parametrize("kw, opened", [
    ({}, True),
    ({"call_side_skipped": True, "put_side_skipped": True}, False),
    ({"call_side_skipped": True}, True),    # one-sided is a REAL position
    ({"put_side_skipped": True}, True),
    ({"execution_failed": True}, False),    # accepted but never filled
])
def test_never_opened_truth_table(kw, opened):
    assert MEICStrategy._entry_never_opened(_entry(**kw)) is (not opened)


def test_sweep_checks_placement_before_asking_for_margin():
    """Pinned structurally: the check must sit in the sweep, so a future
    `_entry_margin` override cannot omit it the way broker_margin did."""
    import inspect

    src = inspect.getsource(MEICStrategy._calculate_capital_deployed)
    assert "_entry_never_opened(entry)" in src, (
        "the placement check left the sweep — any capital basis that returns a "
        "flat figure will count skipped entries again."
    )
    i_check = src.index("_entry_never_opened(entry)")
    i_margin = src.index("self._entry_margin(entry)")
    assert i_check < i_margin, "the check must run BEFORE margin is computed"


def test_a_fully_skipped_day_deploys_no_capital():
    """The exact 2026-09-17 shape: two entries, both sides skipped."""
    inst = NS(
        daily_state=NS(entries=[
            _entry(call_side_skipped=True, put_side_skipped=True, spread_width=0.0),
            _entry(call_side_skipped=True, put_side_skipped=True, spread_width=0.0),
        ]),
        # A broker_margin basis: a flat floor per contract, no width involved.
        _entry_margin=lambda e: 30_000.0 * (e.contracts or 1),
        _entry_never_opened=MEICStrategy._entry_never_opened,
    )
    cap = MEICStrategy._calculate_capital_deployed(inst)
    assert cap == 0.0, (
        f"a day that opened nothing reported ${cap:,.0f} of deployed capital"
    )


def test_a_real_entry_still_counts():
    """The control — a fix that returned 0 for everything would also pass the
    test above."""
    inst = NS(
        daily_state=NS(entries=[_entry(spread_width=0.0)]),
        _entry_margin=lambda e: 30_000.0 * (e.contracts or 1),
        _entry_never_opened=MEICStrategy._entry_never_opened,
    )
    assert MEICStrategy._calculate_capital_deployed(inst) == 30_000.0


def test_all_skipped_day_does_not_fall_through_to_the_summed_estimate():
    """The flaw the existing suite caught in the first version of this fix.

    When every entry is filtered as never-opened, `intervals` is empty — and the
    sweep's fallback for that case SUMS every entry's width notional, which
    would report capital for a day that deployed none. The fallback exists for a
    different situation (entries whose timestamps are unusable), so the two must
    be told apart.
    """
    inst = NS(
        daily_state=NS(entries=[
            # Width RECORDED but both sides skipped — a defined-risk variant on
            # a blackout day. The old fallback would have summed these.
            _entry(call_side_skipped=True, put_side_skipped=True, spread_width=25.0),
            _entry(call_side_skipped=True, put_side_skipped=True, spread_width=25.0),
        ]),
        _entry_margin=lambda e: (e.spread_width or 0) * 100.0 * (e.contracts or 1),
        _entry_never_opened=MEICStrategy._entry_never_opened,
    )
    assert MEICStrategy._calculate_capital_deployed(inst) == 0.0, (
        "an all-skipped day fell through to the summed-width estimate"
    )


def test_missing_timestamps_still_use_the_summed_estimate():
    """The control: the fallback must still work for the case it was built for
    — a real entry whose timestamps are unusable."""
    e = _entry(entry_time=None, spread_width=25.0, contracts=2)
    inst = NS(
        daily_state=NS(entries=[e]),
        _entry_margin=lambda x: (x.spread_width or 0) * 100.0 * (x.contracts or 1),
        _entry_never_opened=MEICStrategy._entry_never_opened,
    )
    assert MEICStrategy._calculate_capital_deployed(inst) == 25.0 * 100 * 2
