"""A stop loss must never be dropped for being frequent.

MEASURED across variant B's retained logs (2026-10-06): the per-type token
bucket had dropped **3 "Stop Loss Hit" alerts** — alongside 40 Position
Snapshots (correctly) and 4 Position Opened. The bucket is capacity 3 refilling
1 per 10 minutes, and on 2026-10-05 B stopped twice inside 73 seconds and a
third time later. So the operator was NOT told about a realized loss, by
design, on exactly the days that mattered.

THE PRINCIPLE these tests pin: a type whose every occurrence is a DISCRETE
COMPLETED ACTION with money attached must never be rate-limited — each is a
different real event and no later alert carries the same information. A type
reporting a PERSISTING CONDITION (delta_breach, wing_breach, api_error) may be
bucketed, because the condition is still true next tick and the next alert says
the same thing. That is the storm layer 2 exists for.

Content-dedup (layer 1) still applies to everything, which is what makes the
exemption safe: the 84x/hr stuck-close flood of 2026-06-12 fired IDENTICAL
alerts, and dedup catches those without the bucket.
"""
import pytest

from shared.alert_service import AlertService, AlertType, AlertPriority


def _svc():
    s = AlertService.__new__(AlertService)
    import threading
    s._gate_lock = threading.Lock()
    s._dedup_last = {}
    s._dedup_suppressed = {}
    s._type_buckets = {}
    s._email_times = []
    # _alert_fingerprint mixes in the bot name, so the rig needs it — found by
    # the first run rather than assumed.
    s.bot_name = "TESTBOT"
    return s


def _gate(s, atype, priority, title, details=None, email=False):
    return s._apply_alert_gate(atype, priority, title, details or {}, email)


class TestDiscreteMoneyEventsSurviveABurst:
    def test_six_distinct_stop_losses_all_get_through(self):
        """THE BUG. Bucket is 3 — the 4th onward were silently dropped."""
        s = _svc()
        allowed = 0
        for i in range(6):
            ok, _, note = _gate(s, AlertType.STOP_LOSS, AlertPriority.HIGH,
                                f"Stop Loss Hit — Entry #{i} call")
            if ok:
                allowed += 1
            else:
                assert "burst" not in note, (
                    f"stop #{i} dropped by the rate bucket: {note}")
        assert allowed == 6, f"only {allowed}/6 distinct stop alerts survived"

    @pytest.mark.parametrize("atype", [
        AlertType.STOP_LOSS, AlertType.MAX_LOSS, AlertType.EMERGENCY_CLOSE,
        AlertType.ENTRY_EXECUTION_FAILED, AlertType.ROLL_FAILED,
    ])
    def test_every_exempt_type_survives_a_burst(self, atype):
        s = _svc()
        for i in range(6):
            ok, _, note = _gate(s, atype, AlertPriority.HIGH, f"{atype.value} #{i}")
            assert ok, f"{atype.value} #{i} blocked: {note}"


class TestTheProtectionIsStillThere:
    def test_IDENTICAL_stop_losses_are_still_deduped(self):
        """The real spam vector — the 2026-06-12 flood fired identical alerts.
        Exempting the bucket must NOT exempt content-dedup."""
        s = _svc()
        ok1, _, _ = _gate(s, AlertType.STOP_LOSS, AlertPriority.HIGH, "Stop Loss Hit")
        ok2, _, note = _gate(s, AlertType.STOP_LOSS, AlertPriority.HIGH, "Stop Loss Hit")
        assert ok1 is True
        assert ok2 is False and "duplicate" in note, note

    def test_a_PERSISTING_CONDITION_is_still_bucketed(self):
        """delta_breach re-fires with a changing number every tick — exactly
        what the bucket is for. It must still be limited."""
        s = _svc()
        results = [_gate(s, AlertType.DELTA_BREACH, AlertPriority.HIGH,
                         f"Delta breach {i}")[0] for i in range(6)]
        assert results.count(True) <= 3, (
            "a persisting-condition type escaped the bucket: %s" % results)
        assert results.count(False) >= 1

    def test_critical_types_are_unaffected(self):
        s = _svc()
        for i in range(6):
            ok, _, note = _gate(s, AlertType.NAKED_POSITION, AlertPriority.CRITICAL,
                                f"Naked position #{i}")
            assert ok, note

    def test_low_priority_noise_is_still_bucketed(self):
        """40 Position Snapshots were dropped and that was CORRECT."""
        s = _svc()
        results = [_gate(s, AlertType.POSITION_SNAPSHOT, AlertPriority.LOW,
                         f"Snapshot {i}")[0] for i in range(8)]
        assert results.count(False) >= 1, "low-priority spam escaped the bucket"


def test_the_exempt_set_contains_no_condition_types():
    """Guards the principle itself: a persisting-condition type must never be
    added here, or the bucket stops protecting against its one real storm."""
    conditions = {AlertType.DELTA_BREACH, AlertType.WING_BREACH, AlertType.API_ERROR}
    assert not (AlertService._NEVER_RATE_LIMIT & conditions), (
        "a persisting-condition type was exempted from the rate bucket")
