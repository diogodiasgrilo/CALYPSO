"""Brandon take-profit: worthless-leg staleness fix + near-expiry hold-if-safe.

Grounded in 2026-06-15 variant C: E#1 (call 7590 / put 7450) had its put decay
to $0 (110pt OTM), which the old `value==0` guard mistook for a stale quote and
permanently blocked TP. With the new rules, the $0 far-OTM value is trusted, but
near close a comfortably-OTM IC is held to expiry (100%) instead of TP'd at 80%.
"""
import inspect

from bots.hydra.brandon.strategy import BrandonHydraStrategy as B


def test_hold_safe_cushion_default_is_data_derived_50pt():
    # 2026-06-23: the hold-if-safe cushion default was raised 25 -> 50pt, the
    # EMPIRICAL break-even from 84 days of SPX paths (riding to expiry is +EV only
    # below a ~3-7% reversal rate; 25pt reverses 26.5% of the time, 50pt ~4-5%).
    # See docs/HYDRA_HOLD_IF_SAFE_ANALYSIS.md. Guard against a silent revert.
    src = inspect.getsource(B.__init__)
    assert 'tp.get("hold_safe_cushion_pts", 50.0)' in src


# ---- staleness fix: trust a $0 only when genuinely worthless (far OTM) --------

def test_positive_value_is_trustworthy():
    assert B._tp_value_trustworthy(35.0, None, 20.0) is True


def test_zero_value_far_otm_is_trustworthy():
    # E#1's put: $0 but 110pt OTM -> genuinely worthless -> trust -> TP can eval
    assert B._tp_value_trustworthy(0.0, 110.0, 20.0) is True


def test_zero_value_near_money_is_stale():
    # $0 with the short only 5pt OTM is implausible -> treat as stale -> defer
    assert B._tp_value_trustworthy(0.0, 5.0, 20.0) is False


def test_zero_value_at_threshold_is_trusted():
    assert B._tp_value_trustworthy(0.0, 20.0, 20.0) is True


def test_zero_value_no_spot_is_untrusted():
    assert B._tp_value_trustworthy(0.0, None, 20.0) is False


# ---- near-expiry hold-if-safe ------------------------------------------------

def test_hold_disabled_when_window_zero():
    assert B._tp_hold_to_expiry(10.0, 0.0, 100.0, 100.0, 25.0) is False


def test_hold_when_near_close_and_safe():
    # E#1 today at ~15:10: ~50m to close, call 30pt OTM, put 110pt OTM
    assert B._tp_hold_to_expiry(50.0, 60.0, 30.0, 110.0, 25.0) is True


def test_no_hold_when_far_from_close():
    assert B._tp_hold_to_expiry(90.0, 60.0, 100.0, 100.0, 25.0) is False


def test_no_hold_when_a_short_is_threatened():
    # near close but the call is only 10pt OTM (< cushion) -> TP should fire
    assert B._tp_hold_to_expiry(20.0, 60.0, 10.0, 100.0, 25.0) is False


def test_hold_ignores_a_closed_side_with_none_otm():
    # call already closed (None), put comfortably OTM -> still hold
    assert B._tp_hold_to_expiry(20.0, 60.0, None, 100.0, 25.0) is True


def test_no_hold_when_minutes_unknown():
    assert B._tp_hold_to_expiry(None, 60.0, 100.0, 100.0, 25.0) is False


def test_no_hold_when_no_spot_known():
    # both otm None (no spot) -> can't confirm safe -> let TP run
    assert B._tp_hold_to_expiry(20.0, 60.0, None, None, 25.0) is False
