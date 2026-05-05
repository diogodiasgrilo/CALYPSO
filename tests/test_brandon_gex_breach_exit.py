"""Tests for bots.hydra.brandon.gex_breach_exit."""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.gex_breach_exit import (
    BreachState,
    evaluate_breach,
)
from bots.hydra.brandon.gex_provider import GEXCluster


T0 = datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc)


def _wall(low, high, gex):
    return GEXCluster(strike_low=low, strike_high=high, total_gex=gex)


class TestNoSignal:
    def test_no_walls_returns_no_breach(self):
        d, s = evaluate_breach(
            side="call",
            spot_now=6850,
            decel_walls=(),
            state=BreachState(),
            now=T0,
        )
        assert d.would_close is False
        assert "no decel walls" in d.reason

    def test_invalid_side(self):
        d, _ = evaluate_breach(
            side="bogus",
            spot_now=6850,
            decel_walls=(_wall(6900, 6910, 1e10),),
            state=BreachState(),
            now=T0,
        )
        assert d.would_close is False
        assert "invalid side" in d.reason


class TestCallSideBreach:
    def test_within_wall_no_breach(self):
        # Wall at 6900-6910, spot at 6890 → no breach
        d, s = evaluate_breach(
            side="call", spot_now=6890,
            decel_walls=(_wall(6900, 6910, 1e10),),
            state=BreachState(),
            now=T0,
        )
        assert d.would_close is False
        assert d.is_first_breach is False
        assert s.first_breach_at is None

    def test_first_breach_records_state(self):
        # SPX above wall edge for the first time
        d, s = evaluate_breach(
            side="call", spot_now=6915,
            decel_walls=(_wall(6900, 6910, 1e10),),
            state=BreachState(),
            now=T0,
        )
        assert d.would_close is False
        assert d.is_first_breach is True
        assert s.first_breach_at == T0

    def test_sustained_breach_confirms(self):
        # 90s after first breach, still breached → confirm
        s0 = BreachState(first_breach_at=T0)
        d, s = evaluate_breach(
            side="call", spot_now=6920,
            decel_walls=(_wall(6900, 6910, 1e10),),
            state=s0,
            now=T0 + timedelta(seconds=90),
            confirmation_seconds=90,
        )
        assert d.would_close is True
        assert s.confirmed is True

    def test_breach_pending_below_confirmation(self):
        s0 = BreachState(first_breach_at=T0)
        d, s = evaluate_breach(
            side="call", spot_now=6920,
            decel_walls=(_wall(6900, 6910, 1e10),),
            state=s0,
            now=T0 + timedelta(seconds=30),
            confirmation_seconds=90,
        )
        assert d.would_close is False
        assert "pending" in d.reason
        assert s.first_breach_at == T0
        assert s.confirmed is False

    def test_recovery_resets_state(self):
        # Was breached, now back inside → state resets, no would_close
        s0 = BreachState(first_breach_at=T0)
        d, s = evaluate_breach(
            side="call", spot_now=6905,
            decel_walls=(_wall(6900, 6910, 1e10),),
            state=s0,
            now=T0 + timedelta(seconds=30),
        )
        assert d.would_close is False
        assert "recovered" in d.reason
        assert s.first_breach_at is None

    def test_picks_outermost_wall_when_multiple(self):
        # Two decel walls; spot above the closer one but below the outer.
        # Should NOT trigger because outer wall still holds.
        d, s = evaluate_breach(
            side="call", spot_now=6915,
            decel_walls=(
                _wall(6900, 6910, 1e10),
                _wall(6925, 6935, 1e10),
            ),
            state=BreachState(),
            now=T0,
        )
        assert d.would_close is False
        assert d.is_first_breach is False

    def test_confirmed_state_persists(self):
        s0 = BreachState(first_breach_at=T0, confirmed=True)
        # Even if SPX recovered, confirmed stays
        d, s = evaluate_breach(
            side="call", spot_now=6850,  # back below
            decel_walls=(_wall(6900, 6910, 1e10),),
            state=s0,
            now=T0 + timedelta(minutes=5),
        )
        assert d.would_close is True
        assert s.confirmed is True


class TestPutSideBreach:
    def test_within_wall_no_breach(self):
        d, s = evaluate_breach(
            side="put", spot_now=6710,
            decel_walls=(_wall(6695, 6705, 1e10),),
            state=BreachState(),
            now=T0,
        )
        assert d.would_close is False

    def test_first_breach_below_wall(self):
        d, s = evaluate_breach(
            side="put", spot_now=6690,
            decel_walls=(_wall(6695, 6705, 1e10),),
            state=BreachState(),
            now=T0,
        )
        assert d.is_first_breach is True

    def test_picks_outermost_wall_below(self):
        # Two decel walls below; spot below the closer but above the lower one.
        d, s = evaluate_breach(
            side="put", spot_now=6680,
            decel_walls=(
                _wall(6695, 6705, 1e10),
                _wall(6650, 6660, 1e10),
            ),
            state=BreachState(),
            now=T0,
        )
        assert d.would_close is False
        assert d.is_first_breach is False

    def test_sustained_breach_confirms(self):
        s0 = BreachState(first_breach_at=T0)
        d, s = evaluate_breach(
            side="put", spot_now=6680,
            decel_walls=(_wall(6695, 6705, 1e10),),
            state=s0,
            now=T0 + timedelta(seconds=120),
            confirmation_seconds=90,
        )
        assert d.would_close is True
        assert s.confirmed is True


class TestStateTransitions:
    def test_breach_flap_breach_again(self):
        # Breach → recover → breach again → state restarts cleanly
        s = BreachState()

        # First breach at T0
        _, s = evaluate_breach(
            side="call", spot_now=6920,
            decel_walls=(_wall(6900, 6910, 1e10),), state=s, now=T0,
        )
        assert s.first_breach_at == T0

        # Recovery at T0 + 30s
        _, s = evaluate_breach(
            side="call", spot_now=6905,
            decel_walls=(_wall(6900, 6910, 1e10),), state=s,
            now=T0 + timedelta(seconds=30),
        )
        assert s.first_breach_at is None

        # Second breach at T0 + 60s — should restart, not confirm yet
        d, s = evaluate_breach(
            side="call", spot_now=6920,
            decel_walls=(_wall(6900, 6910, 1e10),), state=s,
            now=T0 + timedelta(seconds=60),
            confirmation_seconds=90,
        )
        assert d.would_close is False
        assert d.is_first_breach is True
        assert s.first_breach_at == T0 + timedelta(seconds=60)

    def test_state_is_frozen(self):
        s = BreachState(first_breach_at=T0)
        with pytest.raises(Exception):
            s.first_breach_at = None  # type: ignore
