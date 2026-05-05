"""Tests for bots.hydra.brandon.take_profit."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.brandon.take_profit import evaluate, evaluate_iron_condor


class TestEvaluate:
    def test_fires_when_value_at_threshold_boundary(self):
        # $100 credit, 80% threshold → trigger value $20. SV exactly $20 should fire.
        d = evaluate(credit_received=100.0, current_value=20.0, threshold=0.80)
        assert d.should_close is True
        assert d.threshold_value == pytest.approx(20.0)
        assert d.profit_captured_pct == pytest.approx(0.80)

    def test_fires_when_value_well_below_threshold(self):
        d = evaluate(credit_received=150.0, current_value=10.0, threshold=0.80)
        assert d.should_close is True
        assert d.profit_captured_pct == pytest.approx(140.0 / 150.0)

    def test_holds_when_value_just_above_threshold(self):
        d = evaluate(credit_received=100.0, current_value=20.01, threshold=0.80)
        assert d.should_close is False
        assert "holding" in d.reason

    def test_holds_when_freshly_opened(self):
        # Just sold, mark roughly equal to credit received → 0% captured.
        d = evaluate(credit_received=100.0, current_value=98.0, threshold=0.80)
        assert d.should_close is False
        assert d.profit_captured_pct == pytest.approx(0.02)

    def test_fires_when_value_zero(self):
        # Total decay — definitely close.
        d = evaluate(credit_received=100.0, current_value=0.0, threshold=0.80)
        assert d.should_close is True
        assert d.profit_captured_pct == pytest.approx(1.0)

    def test_threshold_at_85_percent(self):
        # Brandon mentions 80–85%. Verify the upper end works.
        d = evaluate(credit_received=200.0, current_value=30.0, threshold=0.85)
        assert d.threshold_value == pytest.approx(30.0)
        assert d.should_close is True

    def test_higher_threshold_holds_longer(self):
        # Same SV ($25) on $100 credit: 80% TP fires, 85% TP holds.
        assert evaluate(100.0, 25.0, 0.80).should_close is False  # SV > $20
        assert evaluate(100.0, 14.0, 0.85).should_close is True   # SV <= $15
        assert evaluate(100.0, 16.0, 0.85).should_close is False  # SV > $15

    def test_no_credit_never_closes(self):
        d = evaluate(credit_received=0.0, current_value=10.0)
        assert d.should_close is False
        assert "no credit" in d.reason

    def test_negative_credit_never_closes(self):
        d = evaluate(credit_received=-50.0, current_value=10.0)
        assert d.should_close is False

    def test_negative_value_never_closes(self):
        # Negative cost-to-close is a data bug; refuse to act on it.
        d = evaluate(credit_received=100.0, current_value=-5.0)
        assert d.should_close is False
        assert "invalid" in d.reason

    @pytest.mark.parametrize("bad_threshold", [0.0, 1.0, -0.1, 1.5, 2.0])
    def test_invalid_threshold_never_closes(self, bad_threshold):
        d = evaluate(credit_received=100.0, current_value=0.0, threshold=bad_threshold)
        assert d.should_close is False

    def test_decision_is_immutable(self):
        d = evaluate(100.0, 20.0)
        with pytest.raises((AttributeError, Exception)):
            d.should_close = False  # type: ignore


class TestEvaluateIronCondor:
    def test_full_ic_at_threshold(self):
        # $60 call credit + $90 put credit = $150. 80% TP trigger = $30.
        # SV: $10 call + $20 put = $30 → fires.
        d = evaluate_iron_condor(
            call_credit=60.0,
            put_credit=90.0,
            call_value=10.0,
            put_value=20.0,
        )
        assert d.should_close is True
        assert d.threshold_value == pytest.approx(30.0)

    def test_one_side_already_closed(self):
        # Call side closed (expired worthless): pass 0 for both. Put still active.
        # Effectively a put-only TP eval on $90 credit / $20 value.
        d = evaluate_iron_condor(
            call_credit=0.0,
            put_credit=90.0,
            call_value=0.0,
            put_value=15.0,
        )
        assert d.should_close is True
        assert d.profit_captured_pct == pytest.approx(0.8333, abs=0.001)

    def test_both_sides_threatened_holds(self):
        # Adverse intraday move: SVs are 60% of credits combined → no TP.
        d = evaluate_iron_condor(
            call_credit=80.0,
            put_credit=80.0,
            call_value=50.0,
            put_value=46.0,
        )
        assert d.should_close is False

    def test_zero_total_credit_never_fires(self):
        d = evaluate_iron_condor(0.0, 0.0, 0.0, 0.0)
        assert d.should_close is False

    def test_aggregate_can_fire_when_one_side_underwater(self):
        # Call side losing ($30 on $20 credit), put side recovering hard ($5 on $80 credit).
        # Total: $100 credit, $35 SV → 65% captured → still holding at 80% threshold.
        d = evaluate_iron_condor(
            call_credit=20.0,
            put_credit=80.0,
            call_value=30.0,
            put_value=5.0,
        )
        assert d.should_close is False
        # But raise threshold sweep — at 65% threshold (i.e., capture >=65%), we'd close:
        d2 = evaluate_iron_condor(20.0, 80.0, 30.0, 5.0, threshold=0.65)
        assert d2.should_close is True
