"""The anti-spam gate could not see a caller slower than its own window.

Layer 1 collapses byte-identical repeats inside `_DEDUP_WINDOWS_S`, which is
keyed on PRIORITY: CRITICAL = 300s. POS-003 reconciles every
RECONCILIATION_INTERVAL_MINUTES = 60. An hourly repeat therefore always lands
OUTSIDE the window, so on 2026-10-19 an unresolved orphan sent four identical
CRITICALs straight through a gate whose own comment promised "no matter what
loop or retry storm fires upstream, the inbox can never be flooded again".

Fix: a per-TYPE window override for types that report a PERSISTING CONDITION,
long enough (4h) to exceed any periodic re-evaluation in the system.

⚠️ THIS IS NOT THE 2026-10-06 TOKEN-BUCKET SCAR, and the tests below pin the
difference. Layer 2 drops by TYPE regardless of content — that is how three
real "Stop Loss Hit" alerts were lost on the days they mattered most. Layer 1
is CONTENT-keyed: the first always sends, a different event fingerprints
differently and sends at once, and collapsed repeats are counted and rolled up.
Widening layer 1 delays no first notification and loses no event.

Source-level latches (strategy.py:_recon_latch_key) remain the PRECISE
mechanism — they can see a condition clear and return, which fingerprinting
cannot. This is the backstop for a call site that forgot to latch.
"""

from __future__ import annotations

import os
import sys
import threading

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import shared.alert_service as alert_mod  # noqa: E402
from shared.alert_service import AlertService, AlertType, AlertPriority  # noqa: E402

HOUR = 3600.0


def _svc():
    s = AlertService.__new__(AlertService)
    # The fingerprint is namespaced by bot_name, so variants never collide.
    s.bot_name = "HYDRA_BL"
    s._gate_lock = threading.Lock()
    s._dedup_last = {}
    s._dedup_suppressed = {}
    s._type_buckets = {}
    s._email_times = []
    return s


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(alert_mod.time, "monotonic", lambda: t["now"])
    return t


def _gate(s, atype, prio, title="Orphan position — untracked broker leg",
          details=None):
    allow, _email, note = s._apply_alert_gate(
        atype, prio, title, details or {"orphans": {"929372783": 1}}, True)
    return allow, note


class TestTheIncident:
    def test_four_hourly_CRITICAL_INTERVENTIONs_now_pass_only_ONCE(self, clock):
        """The exact 2026-10-19 sequence: 12:32, 13:32, 14:32, 15:32 ET."""
        s = _svc()
        allowed = 0
        for _ in range(4):
            if _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)[0]:
                allowed += 1
            clock["now"] += HOUR
        assert allowed == 1

    def test_negative_control_without_the_override_all_FOUR_pass(self, clock):
        """Reinstates the defect: per-priority window only (300s < 3600s)."""
        s = _svc()
        s._PERSISTING_CONDITION_TYPES = frozenset()  # the pre-fix behaviour
        allowed = 0
        for _ in range(4):
            if _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)[0]:
                allowed += 1
            clock["now"] += HOUR
        assert allowed == 4, "the defect must reproduce, or this suite is vacuous"

    def test_the_FIRST_alert_always_sends_immediately(self, clock):
        s = _svc()
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)[0]

    def test_it_re_arms_after_the_window(self, clock):
        s = _svc()
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)[0]
        clock["now"] += 4 * HOUR + 1
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)[0]

    def test_a_DIFFERENT_condition_sends_at_once(self, clock):
        """A genuinely new event fingerprints differently and is never blocked."""
        s = _svc()
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)[0]
        clock["now"] += HOUR
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL,
                     details={"orphans": {"555555555": -7}})[0]

    def test_suppressed_repeats_are_COUNTED_and_rolled_up(self, clock):
        """Collapsed is not silent — the count surfaces on the next send."""
        s = _svc()
        _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)
        for _ in range(3):
            clock["now"] += HOUR
            allow, note = _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)
            assert not allow and "suppressed" in note
        clock["now"] += 4 * HOUR + 1
        allow, note = _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL)
        assert allow and "+3 similar suppressed" in note


class TestTheScarIsNotReopened:
    """2026-10-06: the TYPE-based token bucket dropped three real Stop Loss
    alerts. Nothing here may widen a discrete completed action."""

    @pytest.mark.parametrize("atype", [
        AlertType.STOP_LOSS, AlertType.MAX_LOSS, AlertType.EMERGENCY_CLOSE,
        AlertType.POSITION_OPENED, AlertType.PROFIT_TARGET,
        AlertType.ENTRY_EXECUTION_FAILED, AlertType.ROLL_FAILED,
    ])
    def test_discrete_actions_keep_their_priority_window(self, atype):
        assert atype not in AlertService._PERSISTING_CONDITION_TYPES

    def test_an_hourly_STOP_LOSS_still_sends_every_time(self, clock):
        s = _svc()
        allowed = 0
        for _ in range(4):
            if _gate(s, AlertType.STOP_LOSS, AlertPriority.HIGH, title="Stop Loss Hit")[0]:
                allowed += 1
            clock["now"] += HOUR
        assert allowed == 4, "a discrete money event must never be collapsed hourly"

    @pytest.mark.parametrize("atype", [
        AlertType.NAKED_POSITION, AlertType.DAILY_HALT,
        AlertType.CIRCUIT_BREAKER, AlertType.EMERGENCY_EXIT,
    ])
    def test_active_emergencies_are_deliberately_EXCLUDED(self, atype):
        """These persist too, but repeated prompting is the point."""
        assert atype not in AlertService._PERSISTING_CONDITION_TYPES

    def test_a_persisting_NAKED_POSITION_still_repeats_hourly(self, clock):
        s = _svc()
        allowed = 0
        for _ in range(4):
            if _gate(s, AlertType.NAKED_POSITION, AlertPriority.CRITICAL,
                     title="Naked short position")[0]:
                allowed += 1
            clock["now"] += HOUR
        assert allowed == 4, "a naked short must keep reaching the operator"


class TestWindowSizing:
    def test_the_window_exceeds_the_reconciliation_cadence(self):
        """The sizing constraint that motivated the whole fix."""
        from bots.hydra.base_strategy import RECONCILIATION_INTERVAL_MINUTES
        assert (AlertService._PERSISTING_CONDITION_WINDOW_S
                > RECONCILIATION_INTERVAL_MINUTES * 60), (
            "a window shorter than the slowest periodic loop cannot collapse it")

    def test_it_is_longer_than_every_priority_window(self):
        assert AlertService._PERSISTING_CONDITION_WINDOW_S > max(
            AlertService._DEDUP_WINDOWS_S.values())


class TestTheFingerprintExtension:
    """The 4h window is only safe because a changed condition now fingerprints
    differently. These pin that property and its scope."""

    def test_the_SAME_condition_still_collapses(self, clock):
        """The digest must not be so sensitive it defeats dedup entirely."""
        s = _svc()
        d = {"orphans": {"929372783": 1}}
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL, details=d)[0]
        clock["now"] += HOUR
        assert not _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL,
                         details=dict(d))[0]

    def test_a_drifting_dollar_amount_does_NOT_defeat_the_collapse(self, clock):
        """Volatile tokens are stripped from the blob as they are from titles."""
        s = _svc()
        assert _gate(s, AlertType.DATA_QUALITY, AlertPriority.HIGH, title="Stale quote",
                     details={"conid": 1, "pnl": "$1,200.50"})[0]
        clock["now"] += HOUR
        assert not _gate(s, AlertType.DATA_QUALITY, AlertPriority.HIGH, title="Stale quote",
                         details={"conid": 1, "pnl": "$1,999.99"})[0]

    def test_a_changed_QUANTITY_at_the_same_conid_is_a_new_condition(self, clock):
        s = _svc()
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL,
                     details={"orphans": {"929372783": 1}})[0]
        clock["now"] += HOUR
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL,
                     details={"orphans": {"929372783": -7}})[0]

    def test_NON_persisting_types_fingerprint_EXACTLY_as_before(self):
        """Scope guarantee: the digest must touch nothing else.

        Recomputes the pre-change formula and requires byte equality for a
        type outside the set, and inequality for one inside it (so this test
        cannot pass by the extension simply never running).
        """
        s = _svc()
        details = {"entry_number": 2, "side": "call", "orphans": {"1": 1}}

        def legacy(atype, title):
            norm = s._VOLATILE_TOKEN_RE.sub('#', title.lower())
            norm = __import__("re").sub(r'\s+', ' ', norm).strip()
            tag = f"|e{details['entry_number']}|s{details['side']}"
            return f'{s.bot_name}|{atype.value}|{norm}{tag}'

        for atype in (AlertType.STOP_LOSS, AlertType.POSITION_OPENED,
                      AlertType.NAKED_POSITION, AlertType.EMERGENCY_EXIT):
            assert s._alert_fingerprint(atype, "X", details) == legacy(atype, "X"), atype

        inside = AlertType.CRITICAL_INTERVENTION
        assert s._alert_fingerprint(inside, "X", details) != legacy(inside, "X")

    def test_unserializable_details_do_not_raise(self, clock):
        """Fingerprinting must never break alerting; json.dumps has a fallback."""
        class Weird:
            def __repr__(self): return "<weird>"
        s = _svc()
        assert _gate(s, AlertType.CRITICAL_INTERVENTION, AlertPriority.CRITICAL,
                     details={"obj": Weird(), "s": {1, 2}})[0]
