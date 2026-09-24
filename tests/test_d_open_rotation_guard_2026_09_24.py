"""D's 20% stop must not act on an opening-rotation mark.

MEASURED, not theorised. On 2026-09-24, during a live market-open health check,
D's carried calendar marked ``value $50`` on an $868 debit **six seconds after
the open** — −86.7% — tripping the 20% stop. Eighteen seconds later the SAME
position marked ``$570`` (−34.3%):

    09:30:11  E#1 breached -20% stop (pnl -86.7%) — confirming 20s...
    09:30:12  debit $868 | value $50  | P&L $-818 (-94.2%)  ⏳stop-confirm
    09:30:29  debit $868 | value $570 | P&L $-298 (-34.3%)  ⏳stop-confirm
    09:30:47  [CAL-STOP] E#1 closed (20%-debit stop): P&L $-267.50

The 20-second confirmation window did its job — it turned an $818 book into
$267.50. But the close still acted on a number drawn from the opening rotation,
when four independent option mids are each stale or spread-wide.

**This is MKT-046 one level up.** That filter asks *"did the breach persist?"*;
this one asks *"was the quote worth reading at all?"*. A persistence window
cannot rescue a decision when every tick inside it comes from the same
unreliable minutes.

⚠️ It is NOT free. A genuine overnight gap against a carried calendar is real,
and this delays the stop by the arming window. Five minutes is the compromise:
long enough for the rotation to settle, short enough that a true gap is not
ridden meaningfully longer. D's own ENTRY is 10:00, so it only ever covers a
position carried in from a prior session.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.calendar_entry import CalendarEntry  # noqa: E402
from bots.hydra.double_calendar_strategy import DoubleCalendarStrategy  # noqa: E402

D_DC = json.loads(
    (ROOT / "bots" / "hydra" / "config" / "config_variant_d.json").read_text()
)["strategy"]["double_calendar"]


def _strat(delay=5.0):
    s = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
    s.dc_stop_arm_delay_min = delay
    return s


def _at(h, m, sec=0):
    return datetime(2026, 9, 24, h, m, sec)


ENTRY = CalendarEntry(entry_number=1)


class TestTheGuardCoversTheOpeningRotation:

    def test_the_exact_moment_it_fired_is_now_disarmed(self):
        """09:30:11 — six seconds after the open, where the -86.7% came from."""
        assert _strat()._dc_stop_disarmed_at_open(_at(9, 30, 11), ENTRY) is True

    def test_still_disarmed_at_the_close_it_actually_took(self):
        """09:30:47, the tick the -$267.50 was booked on."""
        assert _strat()._dc_stop_disarmed_at_open(_at(9, 30, 47), ENTRY) is True

    def test_armed_once_the_window_passes(self):
        assert _strat()._dc_stop_disarmed_at_open(_at(9, 35, 1), ENTRY) is False

    def test_armed_for_the_whole_rest_of_the_session(self):
        for t in (_at(10, 0), _at(12, 30), _at(15, 55)):
            assert _strat()._dc_stop_disarmed_at_open(t, ENTRY) is False

    def test_it_does_not_disarm_BEFORE_the_open(self):
        """Pre-market elapsed is negative; that must not read as 'inside the
        window' or the stop would be off all night."""
        assert _strat()._dc_stop_disarmed_at_open(_at(4, 0), ENTRY) is False

    def test_zero_disables_it_entirely(self):
        assert _strat(delay=0)._dc_stop_disarmed_at_open(_at(9, 30, 11), ENTRY) is False

    def test_a_clock_failure_arms_rather_than_disarms(self):
        """Fail toward the stop being LIVE. An exception here must not silently
        switch off a risk control."""
        class Bad:
            def replace(self, **k):
                raise ValueError("no")
        assert _strat()._dc_stop_disarmed_at_open(Bad(), ENTRY) is False


class TestItOnlyTouchesTheStop:

    def test_the_transform_is_NOT_gated_by_it(self):
        """The transformer has its own credit gate — a bad mark cannot produce a
        bad transform, it just fails the threshold. Gating it too would delay a
        profitable action for no reason."""
        src = (ROOT / "bots" / "hydra" / "double_calendar_strategy.py").read_text()
        i_transform = src.index("# 1. profit trigger -> attempt the")
        i_guard = src.index("_dc_stop_disarmed_at_open(now, entry)")
        assert i_transform < i_guard, "the guard must sit after the transform branch"

    def test_an_in_flight_breach_is_CLEARED_at_the_boundary(self):
        """Otherwise a breach recorded during the rotation would carry across
        and confirm the instant the window ends — the guard would delay the
        close by five minutes instead of preventing it."""
        src = (ROOT / "bots" / "hydra" / "double_calendar_strategy.py").read_text()
        i = src.index("_dc_stop_disarmed_at_open(now, entry)")
        assert "breaches.pop(en, None)" in src[i:i + 400]

    def test_E_has_no_pre_transform_stop_to_guard(self):
        """E never transforms and carries no such stop, so this is D-only."""
        src = (ROOT / "bots" / "hydra" / "spy_double_calendar_strategy.py").read_text()
        assert "dc_pre_transform_stop_pct" not in src


class TestTheShippedConfig:

    def test_the_delay_is_enabled(self):
        assert D_DC["stop_arm_delay_min"] > 0

    def test_it_ends_before_D_would_ever_enter(self):
        """It must only ever cover a CARRIED position, never delay a stop on a
        position opened the same day."""
        entry_h, entry_m = (int(x) for x in D_DC["entry_time_et"].split(":"))
        arms_at_min = 9 * 60 + 30 + D_DC["stop_arm_delay_min"]
        assert arms_at_min < entry_h * 60 + entry_m

    def test_it_is_short_enough_to_not_ride_a_real_gap(self):
        assert D_DC["stop_arm_delay_min"] <= 15

    def test_the_measured_evidence_is_recorded_with_the_knob(self):
        raw = (ROOT / "bots" / "hydra" / "config" / "config_variant_d.json").read_text()
        assert "_comment_stop_arm_delay_min" in raw
        assert "$50" in raw and "$570" in raw, "the numbers are the argument"
