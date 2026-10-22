"""MKT-011C — a failed credit estimate let the bot attempt an unplaceable order.

`019f188` (2026-09-18) fixed the MEASURED-zero half of this: a credit that
computed to exactly $0.00 was being read as "could not tell, proceed". What it
deliberately left alone was the genuine could-not-measure case, which still
falls through to MKT-010 — correct when the inputs merely degraded.

It is NOT correct when the thing that failed was CONID RESOLUTION. Placement
keys on the same conids the estimate could not get, so the order cannot be
placed whatever MKT-010 says about liquidity.

Live seat (`bl`), 2026-10-21, broker degraded with 75s ReadTimeouts:

    13:17:36  qualify_option_strikes failed: ReadTimeout (read timeout=75.0)
    13:17:36  Could not resolve call conids - estimating put side only
    13:17:36  Could not resolve put conids
    13:17:36  MKT-011: Could not estimate credit for Entry #3 - falling back
    13:17:36  MKT-010: Running as fallback (credit estimation failed)
    13:17:36  NEUTRAL -> placing full iron condor
    13:17:37  Placing Long Call at 7790.0          <-- with no conid
    13:18:52  no conid for Call 7790.0 2026-10-21
    13:18:52  Entry failed at leg 1: Long Call order failed

Two costs, and declining carries no trade-off because the attempt was already
doomed:

1. An order placement attempted against an unresolved contract.
2. ~76 further seconds blocking the thread the STOP MONITOR shares —
   `run_strategy_check()` runs entries and monitoring sequentially. Measured on
   `bl`'s own `market_ticks`: a single **569-second gap** (13:14:56 -> 13:24:25)
   with no tick, no stop check and no MKT-046 progression, while entry #1 was
   open at 72% call cushion.

Scale of the starvation, measured over the fleet since 2026-09-01 (gaps >120s
inside RTH): `b` 96 gaps / 410 min, `bl` 17 / 98 min. Separating gaps that
start within 120s of one of that variant's own entry slots from the rest shows
entry attempts are the cause and that the cost is bimodal, not a slow drift:

    bl  at a slot   median  29s  p90  66s  p99 553s  max 569s
    bl  elsewhere   median  28s  p90  38s  p99  64s  max 374s

A healthy entry attempt is indistinguishable from baseline; only the degraded
tail starves the monitor.

⚠️ The exposure is LATENT, not realised: across 73 blind windows >180s and 63
stops on `b`+`bl`, **no stop fired inside one or within 180s after one**. At
~2.9% of RTH blind, ~1.6 coincidences were expected by chance, so observing
zero is unremarkable rather than reassuring. That is why this fix is the narrow
one (don't place what cannot be placed) and not a re-architecture of the entry
path.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.strategy import HydraStrategy  # noqa: E402

GATE = HydraStrategy._check_credit_gate
BUILD = HydraStrategy._build_credit_gate_skip_message


def _entry(reason, ok=False):
    e = SimpleNamespace(entry_number=3)
    e._credit_estimate_ok = ok
    e._credit_estimate_fail_reason = reason
    return e


class _Stub:
    """Only what the estimation-failure branch of the gate touches."""
    min_viable_credit_per_side = 10.0
    min_viable_credit_put_side = 15.0
    one_sided_entries_enabled = False
    call_credit_floor = 5.0
    put_credit_floor = 5.0
    max_vix_put_only = 15.0
    current_vix = 14.0
    mkt011_fillability_gate_enabled = False

    def _estimate_entry_credit(self, entry):
        return (0.0, 0.0)

    def _log_safety_event(self, *a, **k):
        pass


class TestTheGateDecision:
    def test_unresolved_conids_SKIPS_instead_of_proceeding(self):
        result, worked, call, put, side = GATE(_Stub(), _entry("unresolved_conids"))
        assert result == "skip", "an unplaceable entry must not reach placement"
        assert side == "unresolved_conids"
        assert worked is False, "estimation genuinely did not work"

    def test_any_OTHER_estimation_failure_still_falls_through_to_MKT010(self):
        """The deliberately-unchanged half. Degraded quotes can still fill, so
        MKT-010 keeps its say — narrowing that would be a new risk envelope,
        not a fix."""
        for reason in (None, "no_expiry", "something_new"):
            result, worked, _c, _p, _s = GATE(_Stub(), _entry(reason))
            assert result == "proceed", f"reason={reason!r} must still fall through"
            assert worked is False

    def test_a_SUCCESSFUL_estimate_is_untouched_by_this_branch(self):
        """Negative control on the guard itself: the flag, not the reason, is
        what gates entry into this branch at all."""
        e = _entry("unresolved_conids", ok=True)
        result, _w, _c, _p, _s = GATE(_Stub(), e)
        assert result != "skip" or _s != "unresolved_conids", (
            "a working estimate must not be diverted by a stale reason field")

    def test_missing_attribute_degrades_to_the_OLD_behaviour(self):
        """An entry object built before this field existed (state-file restore,
        or any un-updated construction site) must not crash — getattr default."""
        e = SimpleNamespace(entry_number=3)
        e._credit_estimate_ok = False
        result, _w, _c, _p, _s = GATE(_Stub(), e)
        assert result == "proceed"


class TestTheSkipMessageDoesNotINVENTAMeasurement:
    """The builder's own docstring records this exact mistake once already: it
    used to re-derive from raw numbers and claimed 'both spreads below minimum'
    for an entry whose put spread was fine. Reusing "both" here would repeat it
    — nothing was priced, so there is no $0.00 to compare to a minimum."""

    def test_it_says_the_lookup_failed_not_that_credit_was_too_low(self):
        reason, details = BUILD(_Stub(), "unresolved_conids", 0.0, 0.0)
        low = (reason + details).lower()
        assert "resolve" in low or "lookup" in low
        assert "never measured" in low or "not a credit rejection" in low

    def test_it_does_NOT_quote_a_credit_against_a_minimum(self):
        reason, details = BUILD(_Stub(), "unresolved_conids", 0.0, 0.0)
        text = reason + details
        assert "need ≥" not in text, "asserts a threshold comparison never made"
        assert "min $" not in text

    def test_the_real_both_nonviable_message_is_UNCHANGED(self):
        """Control: the genuine credit rejection still reads as one."""
        reason, details = BUILD(_Stub(), "both", 5.0, 7.0)
        assert "below our minimum credit" in reason
        assert "need ≥" in reason

    def test_negative_control_reusing_both_would_have_LIED(self):
        """Proves the assertions above are not vacuous by producing the message
        the discarded implementation would have emitted."""
        reason, _d = BUILD(_Stub(), "both", 0.0, 0.0)
        assert "$0.00" in reason and "need ≥" in reason, (
            "the overloaded discriminator did claim a $0.00 measurement")


class TestTheEstimatorRecordsWHYItFailed:
    def test_both_conid_failure_returns_set_the_reason(self):
        import inspect
        from bots.hydra.base_strategy import MEICStrategy
        src = inspect.getsource(MEICStrategy._estimate_entry_credit_ib)
        assert src.count('_credit_estimate_fail_reason = "unresolved_conids"') == 2, (
            "both the call-side and put-side unresolvable returns must record it")
        assert "_credit_estimate_fail_reason = None" in src, "must default per call"

    def test_the_default_is_reset_per_estimate_not_sticky(self):
        """A reason left over from a previous entry would skip a placeable one."""
        import inspect
        from bots.hydra.base_strategy import MEICStrategy
        src = inspect.getsource(MEICStrategy._estimate_entry_credit_ib)
        i_ok = src.index("_credit_estimate_ok = False")
        i_reason = src.index("_credit_estimate_fail_reason = None")
        i_first_set = src.index('_credit_estimate_fail_reason = "unresolved_conids"')
        assert i_reason < i_first_set, "reset must precede any set"
        assert abs(i_reason - i_ok) < 700, "reset belongs with the ok-flag stash"
