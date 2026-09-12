"""Direct-Telegram bypass for CRITICAL/HIGH alerts (2026-08-04, golden-loop
pass — see feedback_golden_loop.md).

Normal alert flow (Bot -> AlertService -> Pub/Sub -> Cloud Function ->
Telegram/Email) depends entirely on Pub/Sub. If Pub/Sub itself has an
outage, a CRITICAL/HIGH alert (naked position, emergency-close failure,
circuit breaker) goes completely dark even after the 2026-08-04 retry +
dead-letter fix — the dead-letter file (data/failed_alerts.jsonl) is a
passive record, not a notification anyone is actively watching.

shared/telegram_direct.py adds a Pub/Sub-INDEPENDENT last-resort send,
wired into AlertService.send_alert right after _write_dead_letter fires,
for CRITICAL/HIGH only. This file covers:
  - send_telegram_direct(): success, non-200-then-retry-plaintext, total
    failure, missing/malformed credentials — all without ever raising.
  - get_telegram_credentials(): valid JSON, malformed JSON, no secret.
  - AlertService wiring: bypass fires only when Pub/Sub is exhausted AND
    priority is CRITICAL/HIGH; never fires on a successful publish; never
    fires for MEDIUM/LOW; credentials are cached across repeated triggers.

Golden-loop negative control: after writing the wiring tests below, the
bypass call in shared/alert_service.py was temporarily commented out and
this file re-run to confirm test_critical_alert_triggers_telegram_bypass
and test_high_alert_triggers_telegram_bypass went RED, before restoring it
and confirming GREEN again — see the version-history entry for the exact
before/after output. This is not a hypothetical; it was actually run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import alert_service as alert_service_module  # noqa: E402
from shared.alert_service import AlertPriority, AlertService, AlertType  # noqa: E402
from shared import secret_manager  # noqa: E402
from shared import telegram_direct  # noqa: E402


# ─── send_telegram_direct ──────────────────────────────────────────────────

def _creds():
    return {"bot_token": "TESTTOKEN", "chat_id": "12345"}


def test_send_telegram_direct_success_first_attempt():
    resp = MagicMock(status_code=200)
    with patch("requests.post", return_value=resp) as post:
        result = telegram_direct.send_telegram_direct(
            "body text", title="Title", credentials=_creds()
        )
    assert result is True
    assert post.call_count == 1
    url, kwargs = post.call_args[0][0], post.call_args[1]
    assert url == "https://api.telegram.org/botTESTTOKEN/sendMessage"
    assert kwargs["json"]["chat_id"] == "12345"
    assert kwargs["json"]["parse_mode"] == "Markdown"
    assert "Title" in kwargs["json"]["text"] and "body text" in kwargs["json"]["text"]
    assert kwargs["timeout"] == telegram_direct.TELEGRAM_API_TIMEOUT_S


def test_send_telegram_direct_retries_plaintext_on_non_200():
    markdown_fail = MagicMock(status_code=400)
    plaintext_ok = MagicMock(status_code=200)
    with patch("requests.post", side_effect=[markdown_fail, plaintext_ok]) as post:
        result = telegram_direct.send_telegram_direct("body", credentials=_creds())
    assert result is True
    assert post.call_count == 2
    second_kwargs = post.call_args_list[1][1]
    assert "parse_mode" not in second_kwargs["json"]


def test_send_telegram_direct_both_attempts_fail_returns_false():
    resp = MagicMock(status_code=400)
    with patch("requests.post", return_value=resp) as post:
        result = telegram_direct.send_telegram_direct("body", credentials=_creds())
    assert result is False
    assert post.call_count == 2


def test_send_telegram_direct_network_exception_does_not_raise():
    with patch("requests.post", side_effect=ConnectionError()):
        result = telegram_direct.send_telegram_direct("body", credentials=_creds())
    assert result is False


def test_send_telegram_direct_redacts_token_from_exception_log(caplog):
    """Round-1 review (2026-08-04): every Telegram API call embeds the bot
    token in the URL, so a `requests` connection exception can stringify the
    token straight into the log line — the exact risk
    bots/hydra/telegram_commands.py's _TokenRedactingFilter was built to
    close for that module (see its docstring), which doesn't cover this
    module. Confirm the token never reaches the log here either."""
    import logging

    token = "TESTTOKEN"
    exc = ConnectionError(
        f"Failed to establish a new connection to api.telegram.org/bot{token}/sendMessage"
    )
    with patch("requests.post", side_effect=exc):
        with caplog.at_level(logging.WARNING, logger="shared.telegram_direct"):
            result = telegram_direct.send_telegram_direct(
                "body", credentials={"bot_token": token, "chat_id": "12345"}
            )
    assert result is False
    full_log = " ".join(r.message for r in caplog.records)
    assert token not in full_log, f"bot token leaked into log: {full_log!r}"
    assert "REDACTED" in full_log


def test_send_telegram_direct_missing_credentials_makes_no_http_call():
    with patch("requests.post") as post:
        result = telegram_direct.send_telegram_direct("body", credentials=None)
        # credentials=None with no GCP -> get_telegram_credentials() returns None
    assert result is False
    post.assert_not_called()


def test_send_telegram_direct_incomplete_credentials_makes_no_http_call():
    with patch("requests.post") as post:
        result = telegram_direct.send_telegram_direct(
            "body", credentials={"bot_token": "", "chat_id": "12345"}
        )
    assert result is False
    post.assert_not_called()


def test_send_telegram_direct_fetches_fresh_when_no_credentials_passed(monkeypatch):
    monkeypatch.setattr(telegram_direct, "get_telegram_credentials", lambda: _creds())
    resp = MagicMock(status_code=200)
    with patch("requests.post", return_value=resp) as post:
        result = telegram_direct.send_telegram_direct("body")
    assert result is True
    assert post.call_count == 1


# ─── get_telegram_credentials ──────────────────────────────────────────────

def test_get_telegram_credentials_valid_json(monkeypatch):
    monkeypatch.setattr(
        secret_manager, "get_secret",
        lambda name, version="latest": json.dumps(_creds()),
    )
    result = secret_manager.get_telegram_credentials()
    assert result == _creds()


def test_get_telegram_credentials_malformed_json_returns_none(monkeypatch):
    monkeypatch.setattr(
        secret_manager, "get_secret", lambda name, version="latest": "not valid json{{{"
    )
    assert secret_manager.get_telegram_credentials() is None


def test_get_telegram_credentials_no_secret_returns_none(monkeypatch):
    monkeypatch.setattr(secret_manager, "get_secret", lambda name, version="latest": None)
    assert secret_manager.get_telegram_credentials() is None


def test_secret_names_has_telegram_entry():
    assert secret_manager.SECRET_NAMES["telegram_credentials"] == "calypso-telegram-credentials"


# ─── AlertService wiring ────────────────────────────────────────────────────

def _live_svc(monkeypatch) -> AlertService:
    monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
    monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: True)
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    svc._initialized = True
    svc._publisher = MagicMock()
    svc._topic_path = "projects/test/topics/calypso-alerts"
    return svc


def _mock_future(exc: Exception = None):
    future = MagicMock()
    if exc is not None:
        future.result.side_effect = exc
    else:
        future.result.return_value = "mid-1"
    return future


@pytest.fixture(autouse=True)
def _isolate_dead_letter_file(tmp_path, monkeypatch):
    monkeypatch.setattr(alert_service_module, "FAILED_ALERTS_PATH", str(tmp_path / "failed_alerts.jsonl"))
    monkeypatch.setattr(alert_service_module.time, "sleep", lambda _s: None)


def _read_dead_letters() -> list:
    path = alert_service_module.FAILED_ALERTS_PATH
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_critical_alert_triggers_telegram_bypass(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
    ]
    with patch("shared.secret_manager.get_telegram_credentials", return_value=_creds()), \
         patch("shared.telegram_direct.send_telegram_direct", return_value=True) as bypass:
        result = svc.send_alert(
            AlertType.NAKED_POSITION, "Naked Position", "untracked short",
            priority=AlertPriority.CRITICAL,
        )
    assert result is False  # Pub/Sub itself still failed
    bypass.assert_called_once()
    args, kwargs = bypass.call_args
    assert args[0] == "untracked short" or kwargs.get("message") == "untracked short"
    assert kwargs.get("title") == "Naked Position"


def test_high_alert_triggers_telegram_bypass(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
    ]
    with patch("shared.secret_manager.get_telegram_credentials", return_value=_creds()), \
         patch("shared.telegram_direct.send_telegram_direct", return_value=True) as bypass:
        svc.send_alert(
            AlertType.STOP_LOSS, "Stop Loss", "closed", priority=AlertPriority.HIGH,
        )
    bypass.assert_called_once()
    args, kwargs = bypass.call_args
    assert args[0] == "closed" or kwargs.get("message") == "closed"
    assert kwargs.get("title") == "Stop Loss"


def test_medium_alert_does_not_trigger_bypass(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [_mock_future(exc=TimeoutError())]
    with patch("shared.telegram_direct.send_telegram_direct") as bypass:
        svc.send_alert(
            AlertType.POSITION_OPENED, "Opened", "entry", priority=AlertPriority.MEDIUM,
        )
    bypass.assert_not_called()


def test_low_alert_does_not_trigger_bypass(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [_mock_future(exc=TimeoutError())]
    with patch("shared.telegram_direct.send_telegram_direct") as bypass:
        svc.send_alert(
            AlertType.BOT_STOPPED, "Bot Stopped", "shutdown", priority=AlertPriority.LOW,
        )
    bypass.assert_not_called()


def test_successful_publish_never_triggers_bypass(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [_mock_future()]
    with patch("shared.telegram_direct.send_telegram_direct") as bypass:
        result = svc.send_alert(
            AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
            priority=AlertPriority.CRITICAL,
        )
    assert result is True
    bypass.assert_not_called()


def test_retry_success_on_second_pubsub_attempt_never_triggers_bypass(monkeypatch):
    """CRITICAL gets a Pub/Sub retry BEFORE the bypass is even considered —
    confirms the bypass is truly last-resort, not a parallel/racing path."""
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(),
    ]
    with patch("shared.telegram_direct.send_telegram_direct") as bypass:
        result = svc.send_alert(
            AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
            priority=AlertPriority.CRITICAL,
        )
    assert result is True
    bypass.assert_not_called()


def test_bypass_failure_is_logged_distinctly(monkeypatch, caplog):
    import logging

    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
    ]
    with patch("shared.secret_manager.get_telegram_credentials", return_value=_creds()), \
         patch("shared.telegram_direct.send_telegram_direct", return_value=False) as bypass:
        with caplog.at_level(logging.WARNING, logger="shared.alert_service"):
            svc.send_alert(
                AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
                priority=AlertPriority.CRITICAL,
            )
    bypass.assert_called_once()  # confirms this exercised send_telegram_direct's
                                  # False path, not the no-credentials short-circuit
    assert any("ALSO failed" in r.message for r in caplog.records), caplog.text
    assert not any("delivered (Pub/Sub unavailable)" in r.message for r in caplog.records)


def test_bypass_success_is_logged_distinctly(monkeypatch, caplog):
    import logging

    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
    ]
    with patch("shared.secret_manager.get_telegram_credentials", return_value=_creds()), \
         patch("shared.telegram_direct.send_telegram_direct", return_value=True):
        with caplog.at_level(logging.WARNING, logger="shared.alert_service"):
            svc.send_alert(
                AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
                priority=AlertPriority.CRITICAL,
            )
    assert any("delivered (Pub/Sub unavailable)" in r.message for r in caplog.records), caplog.text
    assert not any("ALSO failed" in r.message for r in caplog.records)


def test_bypass_exception_does_not_crash_send_alert(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
    ]
    with patch("shared.secret_manager.get_telegram_credentials", return_value=_creds()), \
         patch("shared.telegram_direct.send_telegram_direct", side_effect=RuntimeError("boom")):
        result = svc.send_alert(  # must not raise
            AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
            priority=AlertPriority.CRITICAL,
        )
    assert result is False


def test_no_credentials_short_circuits_without_calling_send_telegram_direct(monkeypatch, caplog):
    """Round-1 review (2026-08-04) caught a real bug: the original wiring
    passed a still-None cache straight into send_telegram_direct, which
    would then fetch credentials AGAIN internally — doubling the Secret
    Manager timeout exposure on exactly the failure path the worst-case
    shutdown accounting cares about. Fixed to short-circuit: if credentials
    are unavailable, don't call send_telegram_direct (and its own internal
    fetch) at all."""
    import logging

    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
    ]
    with patch("shared.secret_manager.get_telegram_credentials", return_value=None) as get_creds, \
         patch("shared.telegram_direct.send_telegram_direct") as bypass:
        with caplog.at_level(logging.ERROR, logger="shared.alert_service"):
            svc.send_alert(
                AlertType.CIRCUIT_BREAKER, "Circuit Breaker", "halted",
                priority=AlertPriority.CRITICAL,
            )
    get_creds.assert_called_once()
    bypass.assert_not_called()
    assert any("no credentials" in r.message for r in caplog.records), caplog.text


def test_telegram_credentials_cached_across_repeated_bypass_triggers(monkeypatch):
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
    ]
    with patch(
        "shared.secret_manager.get_telegram_credentials", return_value=_creds()
    ) as get_creds, patch("shared.telegram_direct.send_telegram_direct", return_value=True):
        svc.send_alert(AlertType.CIRCUIT_BREAKER, "CB1", "x", priority=AlertPriority.CRITICAL)
        svc.send_alert(AlertType.CIRCUIT_BREAKER, "CB2", "y", priority=AlertPriority.CRITICAL)
    assert get_creds.call_count == 1, "credentials must be cached, not re-fetched every trigger"


def test_telegram_credentials_refetched_after_a_failed_fetch(monkeypatch):
    """A failed fetch (returns None) must NOT be cached as permanent —
    confirms the None-sentinel design from the plan."""
    svc = _live_svc(monkeypatch)
    svc._publisher.publish.side_effect = [
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
        _mock_future(exc=TimeoutError()), _mock_future(exc=TimeoutError()),
    ]
    with patch(
        "shared.secret_manager.get_telegram_credentials", side_effect=[None, _creds()]
    ) as get_creds, patch("shared.telegram_direct.send_telegram_direct", return_value=True):
        svc.send_alert(AlertType.CIRCUIT_BREAKER, "CB1", "x", priority=AlertPriority.CRITICAL)
        svc.send_alert(AlertType.CIRCUIT_BREAKER, "CB2", "y", priority=AlertPriority.CRITICAL)
    assert get_creds.call_count == 2, "a failed fetch must be retried on the next trigger"


# ─── Round-3 review finding: Pub/Sub never initializing at all ────────────
# The publish-exception path above (Pub/Sub client constructed fine, but a
# specific publish() call fails) already dead-letters + bypasses. Round 3
# caught that a MORE severe case — the Pub/Sub client never constructing in
# the first place (not on GCP, or a sustained outage the lazy-reinit hasn't
# recovered from) — used to return early from a completely different branch
# in send_alert, before ever reaching either the dead-letter write or the
# bypass. That's exactly the "Pub/Sub itself is down" scenario this whole
# feature exists for, so it needed the same treatment.

def test_uninitialized_pubsub_writes_dead_letter_and_bypasses_for_critical(monkeypatch):
    monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
    # False (not the usual True) — keeps the lazy-reinit block from firing
    # too, isolating this test to the "never initialized" branch under test.
    monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: False)
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")
    assert svc._initialized is False

    with patch("shared.secret_manager.get_telegram_credentials", return_value=_creds()), \
         patch("shared.telegram_direct.send_telegram_direct", return_value=True) as bypass:
        result = svc.send_alert(
            AlertType.NAKED_POSITION, "Naked Position", "untracked short",
            priority=AlertPriority.CRITICAL,
        )
    assert result is False
    bypass.assert_called_once()
    records = _read_dead_letters()
    assert len(records) == 1
    assert records[0]["alert_type"] == "naked_position"


def test_uninitialized_pubsub_medium_alert_dead_letters_but_does_not_bypass(monkeypatch):
    monkeypatch.delenv("ALERT_DRY_RUN", raising=False)
    monkeypatch.setattr(alert_service_module, "is_running_on_gcp", lambda: False)
    svc = AlertService({"alerts": {"enabled": True}}, "HYDRA_B")

    with patch("shared.telegram_direct.send_telegram_direct") as bypass:
        result = svc.send_alert(
            AlertType.POSITION_OPENED, "Opened", "entry", priority=AlertPriority.MEDIUM,
        )
    assert result is False
    bypass.assert_not_called()
    assert len(_read_dead_letters()) == 1
