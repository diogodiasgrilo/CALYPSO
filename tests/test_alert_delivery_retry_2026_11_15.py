"""Total delivery failure must RAISE; partial failure must NOT.

THE DEFECT (established 2026-11-15 by probe, not inference).
`cloud_functions/alert_processor/main.py:process_alert` wrapped its entire body
in `except Exception` and returned normally on every input. functions_framework
therefore always answered the Pub/Sub push 2xx, Pub/Sub acked, and
`deliveryAttempt` never left 1 -- so the subscription's retry policy (10s-600s
backoff) and its `maxDeliveryAttempts=50` dead-letter policy could NEVER engage
on an application failure. An alert lost to a Telegram or Gmail outage was
dropped with nothing but a log line. Measured: one alert published with Gmail
forced to reject the recipient (553 5.1.3) produced exactly ONE invocation, no
retry at the 10s minimum backoff nor in the 150s after, DLQ still empty.

WHY ONLY *TOTAL* FAILURE RAISES. A blanket re-raise would be a duplicate-alert
bug: the handler delivers Telegram and email in one invocation with no
per-channel state, so if Telegram succeeded and email failed, the retry would
redeliver the whole message and send Telegram AGAIN -- the duplicate-notification
defect of 2026-11-11. Raising only when every REQUESTED channel failed means
there is nothing to duplicate, because nothing was delivered.

WHY THE RAISE SITS OUTSIDE THE try/except. Raising inside it would be caught by
the very catch-all being fixed and silently acked. That placement is load-bearing
and `test_raise_is_outside_the_try` pins it, because it is exactly the kind of
thing a later tidy-up would "simplify" back into the bug.

WHY THESE TESTS EXECUTE THE REAL HANDLER. Per the standing lesson that
source-grep tests are blind (21 of them once passed against a gutted method),
every behavioural test here calls the real `process_alert` with a real
base64/JSON CloudEvent payload and real monkeypatched delivery functions. Only
ONE test reads source, and it guards a statement's POSITION, which behaviour
cannot observe from outside.
"""

import base64
import json
import logging
import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import the Cloud Function with its GCP-only dep stubbed (the pattern already
# established by tests/test_phase7_comms_naming.py -- not a second mechanism).
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cf():
    ff = types.ModuleType("functions_framework")
    ff.cloud_event = lambda f: f
    sys.modules.setdefault("functions_framework", ff)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                           / "cloud_functions" / "alert_processor"))
    import main as cf_main  # noqa: WPS433
    # Guard against importing some OTHER main.py off sys.path.
    assert hasattr(cf_main, "process_alert"), cf_main.__file__
    assert "alert_processor" in cf_main.__file__
    return cf_main


def _event(delivery, alert_type="bot_started"):
    """A real CloudEvent-shaped object with a real base64/JSON payload."""
    alert = {"bot_name": "TESTBOT", "alert_type": alert_type,
             "priority": "low", "title": "t", "message": "m",
             "timestamp": "2026-11-15T12:00:00-05:00", "delivery": delivery}
    raw = base64.b64encode(json.dumps(alert).encode()).decode()
    ev = types.SimpleNamespace()
    ev.data = {"message": {"data": raw}}
    return ev


@pytest.fixture
def wired(cf, monkeypatch):
    """Monkeypatch the two delivery functions and the config; record calls."""
    calls = {"telegram": 0, "email": 0}
    outcome = {"telegram": True, "email": True}

    def fake_tg(message, chat_id=None):
        calls["telegram"] += 1
        return outcome["telegram"]

    def fake_email(to_email, subject, html, text):
        calls["email"] += 1
        return outcome["email"]

    monkeypatch.setattr(cf, "send_telegram", fake_tg)
    monkeypatch.setattr(cf, "send_email", fake_email)
    monkeypatch.setattr(cf, "get_alert_config", lambda: {
        "email": "ops@example.invalid", "telegram_chat_id": "123"})
    return calls, outcome


# ===========================================================================
# 1. The predicate: total failure raises
# ===========================================================================

class TestTotalFailureRaises:

    def test_telegram_only_failed_raises(self, cf, wired):
        calls, outcome = wired
        outcome["telegram"] = False
        with pytest.raises(cf.AlertDeliveryFailed):
            cf.process_alert(_event({"telegram": True, "email": False}))
        assert calls == {"telegram": 1, "email": 0}

    def test_both_requested_both_failed_raises(self, cf, wired):
        calls, outcome = wired
        outcome["telegram"] = False
        outcome["email"] = False
        with pytest.raises(cf.AlertDeliveryFailed):
            cf.process_alert(_event({"telegram": True, "email": True}))
        assert calls == {"telegram": 1, "email": 1}

    def test_email_only_failed_raises(self, cf, wired):
        calls, outcome = wired
        outcome["email"] = False
        with pytest.raises(cf.AlertDeliveryFailed):
            cf.process_alert(_event({"telegram": False, "email": True}))
        assert calls == {"telegram": 0, "email": 1}

    def test_legacy_sms_key_still_routes_telegram_and_raises(self, cf, wired):
        """The legacy `sms` key must keep working -- and keep retrying."""
        calls, outcome = wired
        outcome["telegram"] = False
        with pytest.raises(cf.AlertDeliveryFailed):
            cf.process_alert(_event({"sms": True, "email": False}))
        assert calls["telegram"] == 1

    def test_exception_inside_a_channel_counts_as_failure(self, cf, monkeypatch):
        """A raising delivery fn is caught per-channel -> False -> total fail."""
        def boom(*a, **k):
            raise RuntimeError("telegram exploded")
        monkeypatch.setattr(cf, "send_telegram", boom)
        monkeypatch.setattr(cf, "get_alert_config", lambda: {"email": ""})
        with pytest.raises(cf.AlertDeliveryFailed):
            cf.process_alert(_event({"telegram": True, "email": False}))

    def test_message_names_the_failed_channels(self, cf, wired):
        calls, outcome = wired
        outcome["telegram"] = False
        outcome["email"] = False
        with pytest.raises(cf.AlertDeliveryFailed) as ei:
            cf.process_alert(_event({"telegram": True, "email": True}))
        assert "telegram" in str(ei.value) and "email" in str(ei.value)


# ===========================================================================
# 2. THE INVARIANT THAT PREVENTS DUPLICATE ALERTS
# ===========================================================================

class TestPartialFailureMustNotRaise:
    """If ANY requested channel delivered, a retry would re-send it."""

    def test_telegram_ok_email_failed_does_not_raise(self, cf, wired):
        calls, outcome = wired
        outcome["email"] = False
        cf.process_alert(_event({"telegram": True, "email": True}))
        assert calls == {"telegram": 1, "email": 1}

    def test_telegram_failed_email_ok_does_not_raise(self, cf, wired):
        calls, outcome = wired
        outcome["telegram"] = False
        cf.process_alert(_event({"telegram": True, "email": True}))
        assert calls == {"telegram": 1, "email": 1}

    def test_all_succeeded_does_not_raise(self, cf, wired):
        cf.process_alert(_event({"telegram": True, "email": True}))

    def test_nothing_requested_does_not_raise(self, cf, wired):
        """No channel attempted is not a failure -- there is nothing to retry."""
        calls, _ = wired
        cf.process_alert(_event({"telegram": False, "email": False}))
        assert calls == {"telegram": 0, "email": 0}


# ===========================================================================
# 3. Non-retriable inputs must NOT raise (a retry cannot fix malformed data)
# ===========================================================================

class TestNonRetriableInputsDoNotRaise:

    def test_empty_message_does_not_raise(self, cf, wired):
        ev = types.SimpleNamespace()
        ev.data = {"message": {"data": ""}}
        cf.process_alert(ev)

    def test_bad_json_does_not_raise(self, cf, wired):
        ev = types.SimpleNamespace()
        ev.data = {"message": {"data": base64.b64encode(b"{nope").decode()}}
        cf.process_alert(ev)

    def test_unexpected_internal_error_does_not_raise(self, cf, monkeypatch):
        """An unexpected bug stays acked -- scope is DELIVERY failure only."""
        def boom():
            raise RuntimeError("secret manager down")
        monkeypatch.setattr(cf, "get_alert_config", boom)
        cf.process_alert(_event({"telegram": True, "email": True}))

    def test_malformed_event_object_does_not_raise(self, cf, wired):
        ev = types.SimpleNamespace()
        ev.data = "not-a-dict"
        cf.process_alert(ev)


# ===========================================================================
# 4. The raise must ESCAPE the catch-all -- structure + behaviour
# ===========================================================================

class TestRaiseEscapesTheCatchAll:

    def test_raise_is_outside_the_try(self, cf):
        """Position is load-bearing: inside the try it would be swallowed.

        Behaviour cannot see this from outside, which is why it is checked in
        source -- and it is backed by the pytest.raises tests above, so it is
        not a blind grep.
        """
        src = Path(cf.__file__).read_text(encoding="utf-8")
        body = src[src.index("def process_alert("):]
        body = body[:body.index("\n# For local testing")]
        last_catch_all = body.rindex("except Exception as e:")
        raise_at = body.index("raise AlertDeliveryFailed(")
        assert raise_at > last_catch_all, (
            "the raise moved INSIDE the try/except -- it will be swallowed")

    def test_swallowing_version_is_what_we_are_avoiding(self, cf):
        """Negative control for the structural claim.

        Proves the mechanism rather than asserting it: a catch-all around the
        raise really does convert it to a silent return, so the position test
        above is guarding something real.
        """
        def swallowed():
            try:
                raise cf.AlertDeliveryFailed("telegram")
            except Exception:
                return "acked"
        assert swallowed() == "acked"

    def test_early_return_path_is_clean(self, cf, wired):
        """A missing `data` key returns without raising.

        NOTE on what this does NOT prove: moving the flag init to the top of
        the try is an EQUIVALENT mutation -- an early `return` exits before the
        post-try check, so no unbound name is reachable that way. The init sits
        outside the try to stay robust to a LATER edit that puts a raising
        statement before it; that hazard is the one actually covered, by
        `test_bad_json_does_not_raise` (verified: moving the init below the
        decode makes that test NameError).
        """
        ev = types.SimpleNamespace()
        ev.data = {"message": {}}
        cf.process_alert(ev)

    def test_exception_is_a_runtime_error(self, cf):
        assert issubclass(cf.AlertDeliveryFailed, RuntimeError)


# ===========================================================================
# 5. Observability: the unconfigured channel, and no logging regression
# ===========================================================================

class TestObservability:

    def test_email_requested_but_unconfigured_warns(self, cf, monkeypatch, caplog):
        monkeypatch.setattr(cf, "get_alert_config", lambda: {"email": ""})
        monkeypatch.setattr(cf, "send_telegram", lambda *a, **k: True)
        with caplog.at_level(logging.WARNING):
            cf.process_alert(_event({"telegram": True, "email": True}))
        assert any("no recipient configured" in r.message for r in caplog.records)

    def test_unconfigured_email_alone_is_not_a_delivery_failure(self, cf, monkeypatch):
        """Nothing was attempted, so there is nothing to retry."""
        monkeypatch.setattr(cf, "get_alert_config", lambda: {"email": ""})
        cf.process_alert(_event({"telegram": False, "email": True}))

    def test_total_failure_logs_before_raising(self, cf, wired, caplog):
        calls, outcome = wired
        outcome["telegram"] = False
        with caplog.at_level(logging.ERROR):
            with pytest.raises(cf.AlertDeliveryFailed):
                cf.process_alert(_event({"telegram": True, "email": False}))
        assert any("ALERT DELIVERY FAILED" in r.message for r in caplog.records)

    def test_processed_line_still_logged_on_total_failure(self, cf, wired, caplog):
        """Do not regress the 2026-11-15 INFO-logging fix."""
        calls, outcome = wired
        outcome["telegram"] = False
        with caplog.at_level(logging.INFO):
            with pytest.raises(cf.AlertDeliveryFailed):
                cf.process_alert(_event({"telegram": True, "email": False}))
        assert any("Alert processed" in r.message for r in caplog.records)

    def test_no_double_send_within_one_invocation(self, cf, wired):
        """The retry happens at Pub/Sub level, never by re-sending in-process."""
        calls, outcome = wired
        outcome["telegram"] = False
        outcome["email"] = False
        with pytest.raises(cf.AlertDeliveryFailed):
            cf.process_alert(_event({"telegram": True, "email": True}))
        assert calls == {"telegram": 1, "email": 1}
