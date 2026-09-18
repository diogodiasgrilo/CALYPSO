"""A credit of exactly $0.00 was read as "I could not measure the credit".

Found 2026-09-18 by asking why variant B's entry #7 placed when entries #1-#6 had
declined the identical trade all morning. Cost on the live seat: **-$137.20**.

THE DEFECT. ``_check_credit_gate`` opened with:

    if estimated_call == 0.0 and estimated_put == 0.0:
        return ("proceed", False, 0.0, 0.0, "")      # → MKT-010 fallback

``_estimate_entry_credit_ib`` produces ``(0.0, 0.0)`` for FOUR different situations:

    1. no expiry resolvable                        — a failure
    2. conids unresolvable                         — a failure
    3. a leg is unquoted                           — a DECISION: its own comment says
                                                     "treating this side as NON-VIABLE"
    4. round((short_mid - long_mid) * 100, 2) == 0 — a MEASUREMENT: worth nothing

Only the first two are failures. Case 4 is the estimator doing its job perfectly and
answering "this trade pays you nothing" — and the gate turned that answer into a green
light, because the fallback route skips the thresholds, the MKT-029 ladder AND the
MKT-048 fillability veto that sit below it.

The codebase contradicted itself: the estimator writes 0.0 to mean *do not trade this
side*; the gate read the same value as *could not tell, proceed*. Two functions, opposite
meanings for one number, and the disagreement resolved in favour of trading.

WHY IT FIRED THAT DAY, and why "rare edge case" understates it. Options are quoted in
$0.05 ticks. At VIX 15.4 the 8-delta strikes sat ~65pt out, far enough that ADJACENT
strikes carried identical quotes — short call 7685 at $0.10/$0.15 and long call 7690 at
$0.10/$0.15, both mid $0.125. Subtract: exactly 0.0. Same on the put side. So the
trapdoor opens precisely when the spread is worthless, i.e. exactly when declining
matters most. On a normal-vol day the spread has real value and this never triggers.

NOT A REGRESSION. The branch traces to ``0e73b42`` — the original MEIC→HYDRA rename
(v1.5.0), years before any of the 2026-09 work.

THE FIX is to tell the four cases apart: the estimator stashes ``_credit_estimate_ok``,
mirroring the ``_fillable_*_ps`` stash it already performs a few lines below for
MKT-048 — same function, same consumer, same reason. A genuine zero now falls through to
the normal path, where it is below every threshold and every MKT-029 floor, and the entry
is skipped exactly as #1-#6 were.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _gate(*, call_min=10.0, put_min=15.0, one_sided=False, vix=15.4):
    """Stubbed to exactly what _check_credit_gate reads. Mirrors the harness in
    test_mkt048_fillability_gate.py; floors sit below the thresholds so MKT-029 cannot
    rescue a side these tests intend to be non-viable."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.min_viable_credit_per_side = call_min
    s.min_viable_credit_put_side = put_min
    s.call_credit_floor = call_min - 5
    s.put_credit_floor = put_min - 5
    s.current_vix = vix
    s.put_only_max_vix = 25.0
    s.one_sided_entries_enabled = one_sided
    s.mkt011_fillability_gate_enabled = True
    s.min_net_credit_per_contract = 0.05
    s._log_safety_event = MagicMock()
    return s


def _estimate(s, call, put, *, ok, fill_call=0.10, fill_put=0.10):
    def _est(entry):
        entry._fillable_call_ps = fill_call
        entry._fillable_put_ps = fill_put
        entry._credit_estimate_ok = ok
        return (call, put)
    s._estimate_entry_credit = _est


def _entry(n=7):
    return types.SimpleNamespace(entry_number=n)


# ══════════════════════════════════════════════════════════════════════════════
# THE DEFECT
# ══════════════════════════════════════════════════════════════════════════════

def test_a_measured_zero_credit_is_skipped_not_proceeded():
    """THE BUG, in one assertion.

    Estimation SUCCEEDED and said the spread is worth $0.00. That is an answer. The gate
    must run its thresholds on it and skip — not treat it as a failure to answer and wave
    the entry through to a laxer fallback.
    """
    s = _gate()
    _estimate(s, 0.0, 0.0, ok=True)
    result, worked, est_c, est_p, _ = s._check_credit_gate(_entry())
    assert result == "skip", (
        f"a spread measured at exactly $0.00 returned {result!r} — this is the "
        f"2026-09-18 entry #7 path that cost -$137.20"
    )
    assert worked is True, "a successful measurement must report estimation_worked=True"


def test_a_genuine_estimation_failure_still_falls_back():
    """CONTROL, and the half that must NOT change. When the estimator really could not
    price the spread — no expiry, unresolvable conids, an exception — MKT-010 remains the
    fallback. Losing this would turn every transient data gap into a missed trade."""
    s = _gate()
    _estimate(s, 0.0, 0.0, ok=False)
    result, worked, _, _, _ = s._check_credit_gate(_entry())
    assert result == "proceed"
    assert worked is False, "a real failure must still report estimation_worked=False"


def test_the_incident_quotes_reproduce_the_skip():
    """The actual 2026-09-18 12:45 ET book, from the ORDER-DIAG lines.

    short call 7685 bid $0.10 / ask $0.15 → mid $0.125
    long  call 7690 bid $0.10 / ask $0.15 → mid $0.125   ⇒ credit exactly 0.0

    Adjacent strikes quoting identically is not exotic at 65pt OTM on a $0.05 tick — it
    is what low volatility looks like.
    """
    s = _gate()
    _estimate(s, 0.0, 0.0, ok=True, fill_call=-0.025, fill_put=-0.025)
    result, _, _, _, nonviable = s._check_credit_gate(_entry())
    assert result == "skip"
    assert nonviable == "both"


@pytest.mark.parametrize("call,put", [(0.0, 5.0), (5.0, 0.0), (5.0, 5.0)])
def test_a_nonzero_estimate_is_unaffected(call, put):
    """BLAST-RADIUS CONTROL. The change may only affect the both-sides-exactly-zero case.
    Anything with a number on either side must behave exactly as before."""
    s = _gate()
    _estimate(s, call, put, ok=True)
    result, worked, _, _, _ = s._check_credit_gate(_entry())
    assert worked is True
    assert result in ("skip", "proceed", "put_only", "call_only", "retry_put")


def test_a_viable_entry_still_proceeds():
    """INSTRUMENT CONTROL. A gate that skips everything is not a gate."""
    s = _gate()
    _estimate(s, 50.0, 60.0, ok=True, fill_call=0.50, fill_put=0.60)
    result, worked, _, _, _ = s._check_credit_gate(_entry())
    assert result == "proceed" and worked is True


# ══════════════════════════════════════════════════════════════════════════════
# The estimator's half of the contract
# ══════════════════════════════════════════════════════════════════════════════

def _estimator(*, expiry="2026-09-18", chain=None, quotes=None, raise_on_chain=False):
    # HydraStrategy, not MEICStrategy: the base is abstract (_calculate_strikes,
    # _check_stop_losses, _initiate_entry). The concrete subclass inherits
    # _estimate_entry_credit_ib unchanged, so this exercises the same code.
    s = HydraStrategy.__new__(HydraStrategy)
    s._get_todays_expiry = lambda: expiry

    def _chain(exp, wanted):
        if raise_on_chain:
            raise RuntimeError("broker down")
        return chain if chain is not None else ({}, {})
    s._read_option_chain = _chain
    s._read_option_quotes_batch = lambda ids: (quotes or {})
    s._quote_mid = lambda q: (q or {}).get("mid", 0.0) or 0.0
    return s


def _ic_entry(**kw):
    e = types.SimpleNamespace(
        entry_number=7, short_call_strike=7685.0, long_call_strike=7690.0,
        short_put_strike=7550.0, long_put_strike=7545.0,
    )
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def test_estimator_flags_success_even_when_the_credit_is_zero():
    """THE OTHER HALF OF THE FIX. The flag says "I computed this", NOT "the answer was
    non-zero" — conflating those is the original bug wearing a new hat."""
    q = {"sc": {"mid": 0.125, "bid": 0.10, "ask": 0.15},
         "lc": {"mid": 0.125, "bid": 0.10, "ask": 0.15},
         "sp": {"mid": 0.175, "bid": 0.15, "ask": 0.20},
         "lp": {"mid": 0.175, "bid": 0.15, "ask": 0.20}}
    s = _estimator(chain=({7685.0: "sc", 7690.0: "lc"}, {7550.0: "sp", 7545.0: "lp"}),
                   quotes=q)
    entry = _ic_entry()
    call, put = s._estimate_entry_credit_ib(entry)
    assert (call, put) == (0.0, 0.0), "the incident's book must price to exactly zero"
    assert entry._credit_estimate_ok is True, (
        "a completed computation that happens to equal zero must NOT be reported as a "
        "failure — that is the whole defect"
    )


def test_estimator_flags_failure_when_there_is_no_expiry():
    s = _estimator(expiry=None)
    entry = _ic_entry()
    assert s._estimate_entry_credit_ib(entry) == (0.0, 0.0)
    assert entry._credit_estimate_ok is False


def test_estimator_flags_failure_when_conids_do_not_resolve():
    s = _estimator(chain=({}, {}))
    entry = _ic_entry()
    assert s._estimate_entry_credit_ib(entry) == (0.0, 0.0)
    assert entry._credit_estimate_ok is False


def test_estimator_flags_failure_on_an_exception():
    """The broad `except` must leave the flag False, or a broker outage becomes a
    green light — the same shape as the bug being fixed."""
    s = _estimator(raise_on_chain=True)
    entry = _ic_entry()
    assert s._estimate_entry_credit_ib(entry) == (0.0, 0.0)
    assert entry._credit_estimate_ok is False


def test_the_flag_is_reset_on_every_call():
    """A stale True from a previous entry would wave through the very case this fixes.
    The reset sits with the _fillable_*_ps resets for exactly that reason."""
    s = _estimator(expiry=None)
    entry = _ic_entry()
    entry._credit_estimate_ok = True          # pretend a previous estimate succeeded
    s._estimate_entry_credit_ib(entry)
    assert entry._credit_estimate_ok is False


# ══════════════════════════════════════════════════════════════════════════════
# Second-order effects, checked rather than assumed
# ══════════════════════════════════════════════════════════════════════════════

def test_the_realized_credit_guard_goes_inert_on_a_zero_estimate():
    """``_mkt011_est_call`` now stores 0.0 where it used to store None (estimation_worked
    flipped True). Rule 2 divides by the estimate, so this checks it does not — it is
    guarded by `est_dollars_pc > 0` and goes inert, rather than dividing by zero or
    inventing a violation."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.strategy_config = {"realized_credit_guard": {
        "action": "alert_only", "min_realized_credit_per_contract": -999.0,
        "credit_realized_fraction": 0.5}}
    entry = types.SimpleNamespace(
        entry_number=7, contracts=7, short_call_uic="x", call_side_skipped=False,
        call_spread_credit=1.0, short_put_uic=None, put_side_skipped=True,
    )
    s._alert_service = MagicMock()
    # est_call_pc=0.0 must not raise and must not fabricate a Rule-2 violation.
    assert s._validate_realized_credit(
        entry, filled_legs=[], est_call_pc=0.0, est_put_pc=0.0) is True


def test_the_gate_no_longer_branches_on_the_raw_zero_tuple():
    """Pins the SHAPE of the fix. If someone reinstates the `== 0.0` test alongside the
    flag, the defect returns while every test above still passes."""
    from tests.conftest import strip_comments
    src = strip_comments((Path(__file__).resolve().parents[1]
                          / "bots" / "hydra" / "strategy.py").read_text(), "py")
    body = src[src.index("def _check_credit_gate"):]
    body = body[: body.index("\n    def ", 10)]
    assert "_credit_estimate_ok" in body, "the gate no longer reads the estimator's flag"
    assert "estimated_call == 0.0 and estimated_put == 0.0" not in body, (
        "the gate branches on the raw (0.0, 0.0) tuple again — a measured zero would "
        "once more be read as a failure to measure"
    )
