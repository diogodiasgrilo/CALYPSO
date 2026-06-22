"""MKT-048 (2026-06-22): fillability gate.

Root cause it fixes — the 2026-06-22 variant-C Entry#1 wall: MKT-011 decides
side viability on MID prices, but a real iron-condor side fills as long@ask /
short@bid. So a side can clear the mid-credit threshold yet be UNFILLABLE as a
credit spread (its ``short_bid − long_ask`` is a debit). It then fails at leg 3
when the placement net-credit floor refuses to leg into a debit — AFTER buying
the protective long: 3 retries, long-leg bleed, a HIGH watchdog alert, and
put-only anyway. The gate veto routes that side one-sided UP FRONT instead.

Two layers under test:
  1. ``_estimate_entry_credit_ib`` STASHES each side's per-share fillable credit
     (``short_bid − long_ask``) on the entry — ``None`` when a leg is unquoted.
  2. ``_check_credit_gate`` reads the stash and flips a mid-viable-but-unfillable
     side non-viable, so the existing one-sided routing books it cleanly.

FAIL-OPEN is the safety contract: a veto fires ONLY on a confirmed debit
(fillable not None AND < floor); a missing / crossed quote (None) never vetoes,
and the whole gate is gated behind ``mkt011_fillability_gate_enabled``.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — the gate veto (_check_credit_gate)
# ─────────────────────────────────────────────────────────────────────────────

def _gate_strat(*, vix=17.4, put_only_max_vix=25.0, one_sided=True,
                gate_enabled=True, min_net_credit=0.05,
                call_min=10.0, put_min=15.0):
    """A HydraStrategy stubbed down to exactly what _check_credit_gate reads.

    call_min / put_min are in the gate's per-contract-total units (× $0.01),
    i.e. call_min=10 means a $0.10/share threshold.
    """
    s = HydraStrategy.__new__(HydraStrategy)
    s.min_viable_credit_per_side = call_min
    s.min_viable_credit_put_side = put_min
    # MKT-029 fallback floors: set BELOW the thresholds so a mid-viable side is
    # never re-touched, and a non-viable side isn't rescued in these tests.
    s.call_credit_floor = call_min - 5
    s.put_credit_floor = put_min - 5
    s.current_vix = vix
    s.put_only_max_vix = put_only_max_vix
    s.one_sided_entries_enabled = one_sided
    s.mkt011_fillability_gate_enabled = gate_enabled
    s.min_net_credit_per_contract = min_net_credit
    s._log_safety_event = MagicMock()
    return s


def _with_estimate(s, mid_call, mid_put, fill_call, fill_put):
    """Wire _estimate_entry_credit to return the MID tuple and stash the
    per-share fillable credits, exactly as _estimate_entry_credit_ib does."""
    def _est(entry):
        entry._fillable_call_ps = fill_call
        entry._fillable_put_ps = fill_put
        return (mid_call, mid_put)
    s._estimate_entry_credit = _est


def _entry(n=1):
    return types.SimpleNamespace(entry_number=n)


class TestGateVeto:
    def test_call_mid_viable_but_unfillable_routes_put_only(self):
        # The 2026-06-22 C Entry#1 case: call mid $0.15 (>= $0.10), but its
        # fillable credit short_bid−long_ask = −$0.10 < $0.05 floor → veto call,
        # put viable + VIX calm → put-only (no leg-3 bleed).
        s = _gate_strat(vix=17.4, put_only_max_vix=25.0)
        _with_estimate(s, mid_call=15.0, mid_put=35.0, fill_call=-0.10, fill_put=0.20)
        result, worked, est_c, est_p = s._check_credit_gate(_entry())
        assert result == "put_only"
        assert worked is True
        s._log_safety_event.assert_called()  # MKT-048 event emitted

    def test_both_fillable_ok_proceeds(self):
        s = _gate_strat()
        _with_estimate(s, mid_call=20.0, mid_put=40.0, fill_call=0.10, fill_put=0.25)
        result, worked, *_ = s._check_credit_gate(_entry())
        assert result == "proceed"

    def test_put_unfillable_routes_call_only(self):
        s = _gate_strat()
        _with_estimate(s, mid_call=20.0, mid_put=40.0, fill_call=0.10, fill_put=-0.05)
        result, *_ = s._check_credit_gate(_entry())
        assert result == "call_only"

    def test_both_unfillable_skips(self):
        s = _gate_strat()
        _with_estimate(s, mid_call=20.0, mid_put=40.0, fill_call=-0.20, fill_put=-0.05)
        result, *_ = s._check_credit_gate(_entry())
        assert result == "skip"

    def test_call_unfillable_high_vix_skips_no_unhedged_put_only(self):
        # Call unfillable, put fillable, but VIX above the put-only ceiling →
        # MKT-032 skip (no naked put-only in volatile conditions).
        s = _gate_strat(vix=30.0, put_only_max_vix=25.0)
        _with_estimate(s, mid_call=15.0, mid_put=35.0, fill_call=-0.10, fill_put=0.20)
        result, *_ = s._check_credit_gate(_entry())
        assert result == "skip"

    def test_fillable_exactly_at_floor_not_vetoed(self):
        # Veto is strict "< floor"; clearing it EXACTLY is fillable → proceed.
        s = _gate_strat(min_net_credit=0.05)
        _with_estimate(s, mid_call=20.0, mid_put=40.0, fill_call=0.05, fill_put=0.05)
        result, *_ = s._check_credit_gate(_entry())
        assert result == "proceed"


class TestGateFailOpen:
    def test_unquoted_fillable_none_never_vetoes(self):
        # A side with no usable quote (None) must NOT be vetoed — fail open and
        # let the placement guard-floor / GUARD-INVERT backstops handle it.
        s = _gate_strat()
        _with_estimate(s, mid_call=15.0, mid_put=35.0, fill_call=None, fill_put=None)
        result, *_ = s._check_credit_gate(_entry())
        assert result == "proceed"

    def test_disabled_knob_skips_the_veto_entirely(self):
        # With the gate off, a confirmed debit fillable is ignored (pure mid
        # gating, pre-MKT-048 behavior).
        s = _gate_strat(gate_enabled=False)
        _with_estimate(s, mid_call=15.0, mid_put=35.0, fill_call=-0.50, fill_put=-0.50)
        result, *_ = s._check_credit_gate(_entry())
        assert result == "proceed"

    def test_veto_does_not_touch_a_mid_nonviable_side(self):
        # If the call is already mid-non-viable (and not rescued), the veto is a
        # no-op on it; routing is unchanged from pure mid gating.
        s = _gate_strat(call_min=30.0)  # call mid 15 < 30, non-viable
        _with_estimate(s, mid_call=15.0, mid_put=40.0, fill_call=0.10, fill_put=0.25)
        result, *_ = s._check_credit_gate(_entry())
        # call non-viable (mid), put viable & fillable → put-only
        assert result == "put_only"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — the stash math (_estimate_entry_credit_ib)
# ─────────────────────────────────────────────────────────────────────────────

def _estimator_strat():
    s = HydraStrategy.__new__(HydraStrategy)
    s._get_todays_expiry = lambda: "2026-06-22"
    return s


def _est_entry():
    return types.SimpleNamespace(
        entry_number=1,
        short_call_strike=7550.0, long_call_strike=7555.0,
        short_put_strike=7410.0, long_put_strike=7405.0,
    )


def _wire_quotes(s, quote_by_conid, chain_map=None):
    """chain_map: strike→conid for both calls and puts (default maps each of the
    4 entry strikes to a distinct conid)."""
    if chain_map is None:
        chain_map = {7550.0: 1, 7555.0: 2, 7410.0: 3, 7405.0: 4}
    call_map = {k: v for k, v in chain_map.items() if k >= 7500}
    put_map = {k: v for k, v in chain_map.items() if k < 7500}
    s._read_option_chain = lambda expiry, wanted: (call_map, put_map)
    s._read_option_quotes_batch = lambda ids: {cid: quote_by_conid.get(cid) for cid in ids}


class TestStashMath:
    def test_stash_is_short_bid_minus_long_ask(self):
        s = _estimator_strat()
        # call: short(7550) bid 0.35, long(7555) ask 0.45 → fillable −0.10
        # put : short(7410) bid 2.40, long(7405) ask 2.10 → fillable +0.30
        _wire_quotes(s, {
            1: {"bid": 0.35, "ask": 0.65, "mid": 0.50},
            2: {"bid": 0.25, "ask": 0.45, "mid": 0.35},
            3: {"bid": 2.40, "ask": 2.70, "mid": 2.55},
            4: {"bid": 1.90, "ask": 2.10, "mid": 2.00},
        })
        e = _est_entry()
        s._estimate_entry_credit_ib(e)
        assert e._fillable_call_ps == 0.35 - 0.45
        assert e._fillable_put_ps == 2.40 - 2.10

    def test_unquoted_leg_leaves_stash_none(self):
        s = _estimator_strat()
        # long call (conid 2) returns NO quote row → call side fillable None.
        _wire_quotes(s, {
            1: {"bid": 0.35, "ask": 0.65, "mid": 0.50},
            2: None,
            3: {"bid": 2.40, "ask": 2.70, "mid": 2.55},
            4: {"bid": 1.90, "ask": 2.10, "mid": 2.00},
        })
        e = _est_entry()
        s._estimate_entry_credit_ib(e)
        assert e._fillable_call_ps is None      # fail open
        assert e._fillable_put_ps == 2.40 - 2.10

    def test_missing_bidask_field_leaves_stash_none(self):
        s = _estimator_strat()
        # short call quoted but with NO bid field → call fillable None.
        _wire_quotes(s, {
            1: {"ask": 0.65, "mid": 0.50},   # no 'bid'
            2: {"bid": 0.25, "ask": 0.45, "mid": 0.35},
            3: {"bid": 2.40, "ask": 2.70, "mid": 2.55},
            4: {"bid": 1.90, "ask": 2.10, "mid": 2.00},
        })
        e = _est_entry()
        s._estimate_entry_credit_ib(e)
        assert e._fillable_call_ps is None
        assert e._fillable_put_ps is not None

    def test_stash_reset_to_none_each_call(self):
        # A prior call's stash must never leak into a no-expiry early return.
        s = _estimator_strat()
        e = _est_entry()
        e._fillable_call_ps = 99.0
        e._fillable_put_ps = 99.0
        s._get_todays_expiry = lambda: None     # early return path
        s._estimate_entry_credit_ib(e)
        assert e._fillable_call_ps is None
        assert e._fillable_put_ps is None
