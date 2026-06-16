"""Phase 7 — comms naming / grouping (display names, group-scoped /compare).

These tests pin the THREE invariants the audit froze, plus the new additive
behavior:

  FROZEN (audit AUD-1-H1):
    * the alert-wire ``bot_name`` (the anti-spam dedup PARTITION KEY + the
      monitor-log column) is byte-identical to before for a given variant;
    * the dedup fingerprint string is byte-identical to before — the rename is
      DISPLAY-ONLY (rides in NEW additive payload fields).

  ADDITIVE:
    * ``send_alert`` attaches ``display_name`` + ``group_label`` resolved from
      the taxonomy for the running variant, WITHOUT touching ``bot_name``;
    * the Cloud Function formatters PREFER those fields and FALL BACK to
      ``bot_name`` when absent (older payloads / other bots);
    * ``/compare`` is GROUP-SCOPED: bare = the IC group {b,c}; ``calendars`` =
      the calendar group {d,e} with a debit-native renderer that NEVER emits IC
      credit/buffer/spread-width fields.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import strategy_taxonomy as tax  # noqa: E402
from shared.alert_service import AlertService, AlertType, AlertPriority  # noqa: E402


# ===========================================================================
# Helpers: capture the actual published payload (where display fields live)
# ===========================================================================

def _svc_with_capture(bot_name: str):
    """An AlertService wired to a fake Pub/Sub publisher that records the
    JSON payload of each publish, so the test can assert on the wire shape."""
    svc = AlertService({"alerts": {"enabled": True}}, bot_name)
    captured = {}

    class _FakePublisher:
        def publish(self, topic, data):
            captured["payload"] = json.loads(data.decode("utf-8"))

            class _F:
                def result(self, timeout=None):
                    return "fake-msg-id"

            return _F()

    svc._publisher = _FakePublisher()
    svc._topic_path = "fake-topic"
    svc._initialized = True
    svc._dry_run = False
    return svc, captured


# ===========================================================================
# FROZEN: bot_name + fingerprint are byte-identical (display-only rename)
# ===========================================================================

# Reference values captured BEFORE the Phase-7 change. These are the
# anti-spam partition keys; if a refactor ever shifts them, dedup state
# merges/resets across variants — so they must never move.
_FROZEN_FINGERPRINT_C = (
    "HYDRA_C|stop_loss|stop at # (#)|e2|sshort"
)


def test_bot_name_on_wire_is_unchanged_per_variant(monkeypatch):
    """The payload ``bot_name`` for each variant is the variant-aware name the
    AlertService was constructed with — NOT a display name."""
    for variant_env, bot_name in [
        (None, "HYDRA"),
        ("b", "HYDRA_B"),
        ("c", "HYDRA_C"),
        ("d", "DCTM_D"),
        ("e", "SPYDC_E"),
    ]:
        if variant_env is None:
            monkeypatch.delenv("HYDRA_VARIANT_ID", raising=False)
        else:
            monkeypatch.setenv("HYDRA_VARIANT_ID", variant_env)
        svc, captured = _svc_with_capture(bot_name)
        svc.send_alert(AlertType.STOP_LOSS, "Stop at $1.00", "msg",
                       priority=AlertPriority.HIGH)
        assert captured["payload"]["bot_name"] == bot_name


def test_fingerprint_string_is_byte_identical_to_before(monkeypatch):
    """The dedup fingerprint is computed the same way as before the rename —
    keyed on the FROZEN ``bot_name``, ignoring volatile $/% tokens, keeping
    entry/side. A drift here would re-partition the anti-spam namespace."""
    monkeypatch.setenv("HYDRA_VARIANT_ID", "c")
    svc, _ = _svc_with_capture("HYDRA_C")
    fp = svc._alert_fingerprint(
        AlertType.STOP_LOSS, "Stop at $3,380.50 (12.5%)",
        {"entry_number": 2, "side": "short"},
    )
    assert fp == _FROZEN_FINGERPRINT_C
    # And it still keys on bot_name (the partition guarantee).
    assert fp.startswith("HYDRA_C|")


# ===========================================================================
# ADDITIVE: display_name + group_label on the wire
# ===========================================================================

def test_payload_carries_display_name_and_group_label(monkeypatch):
    monkeypatch.setenv("HYDRA_VARIANT_ID", "c")
    svc, captured = _svc_with_capture("HYDRA_C")
    svc.send_alert(AlertType.STOP_LOSS, "Stop", "msg", priority=AlertPriority.HIGH)
    p = captured["payload"]
    assert p["display_name"] == "Brandon Narrow (live)"
    assert p["group_label"] == "0DTE Iron Condor"
    # bot_name unchanged alongside the additive fields.
    assert p["bot_name"] == "HYDRA_C"


def test_payload_display_name_for_calendar_variant(monkeypatch):
    monkeypatch.setenv("HYDRA_VARIANT_ID", "e")
    svc, captured = _svc_with_capture("SPYDC_E")
    svc.send_alert(AlertType.STOP_LOSS, "Stop", "msg", priority=AlertPriority.HIGH)
    p = captured["payload"]
    assert p["display_name"] == "SPY Double Calendar"
    assert p["group_label"] == "Multi-day Calendar"
    assert p["bot_name"] == "SPYDC_E"


def test_display_fields_omitted_for_non_hydra_bot(monkeypatch):
    """A non-HYDRA AlertService (e.g. legacy IRON_FLY, HYDRA_VARIANT_ID unset)
    must NOT be mislabeled 'HYDRA Baseline' — the fields are left off so the CF
    falls back to bot_name."""
    monkeypatch.delenv("HYDRA_VARIANT_ID", raising=False)
    svc, captured = _svc_with_capture("IRON_FLY")
    svc.send_alert(AlertType.STOP_LOSS, "Stop", "msg", priority=AlertPriority.HIGH)
    p = captured["payload"]
    assert "display_name" not in p
    assert "group_label" not in p
    assert p["bot_name"] == "IRON_FLY"


# ===========================================================================
# Cloud Function: prefer display fields, fall back to bot_name
# ===========================================================================

@pytest.fixture(scope="module")
def cf():
    """Import the Cloud Function with its GCP-only deps stubbed."""
    ff = types.ModuleType("functions_framework")
    ff.cloud_event = lambda f: f
    sys.modules.setdefault("functions_framework", ff)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                          / "cloud_functions" / "alert_processor"))
    import main as cf_main  # noqa: WPS433
    return cf_main


def test_cf_email_subject_prefers_display_name_keeps_letter(cf):
    alert = {"bot_name": "SPYDC_E", "display_name": "SPY Double Calendar",
             "group_label": "Multi-day Calendar", "priority": "high",
             "title": "Stop Loss Hit"}
    assert cf.format_email_subject(alert) == "[HIGH] SPY Double Calendar (E): Stop Loss Hit"


def test_cf_email_subject_falls_back_to_bot_name(cf):
    alert = {"bot_name": "HYDRA_C", "priority": "high", "title": "Stop Loss Hit"}
    assert cf.format_email_subject(alert) == "[HIGH] HYDRA_C: Stop Loss Hit"


def test_cf_variant_letter_extraction(cf):
    assert cf._variant_letter("HYDRA") == "A"
    assert cf._variant_letter("HYDRA_C") == "C"
    assert cf._variant_letter("DCTM_D") == "D"
    assert cf._variant_letter("SPYDC_E") == "E"
    assert cf._variant_letter("IRON_FLY") == ""  # no false letter suffix


def test_cf_telegram_header_prefers_display_label(cf):
    alert = {"bot_name": "SPYDC_E", "display_name": "SPY Double Calendar",
             "group_label": "Multi-day Calendar", "priority": "high", "title": "x"}
    head = cf.format_telegram_message(alert).splitlines()[0]
    assert "SPY Double Calendar (E)" in head
    assert "Multi-day Calendar" in head


def test_cf_telegram_header_falls_back(cf):
    alert = {"bot_name": "HYDRA_C", "priority": "high", "title": "x"}
    head = cf.format_telegram_message(alert).splitlines()[0]
    # md-escaped underscore; bot_name preserved
    assert "HYDRA" in head and "C" in head


# ===========================================================================
# /compare group-scoping + calendar-native renderer
# ===========================================================================

def _make_calendar_variant(data_root, vid, records):
    vdir = os.path.join(data_root, f"variant_{vid}")
    os.makedirs(vdir, exist_ok=True)
    with open(os.path.join(vdir, "dc_open_trades.json"), "w") as f:
        json.dump(records, f)
    # minimal outcomes DB
    con = sqlite3.connect(os.path.join(vdir, "dc_calendar.db"))
    con.execute(
        "CREATE TABLE dc_outcomes (entry_date TEXT, close_date TEXT, "
        "entry_number INT, terminal_state TEXT, realized_pnl REAL)"
    )
    con.commit()
    con.close()


def _make_ic_variant(data_root, vid):
    vdir = os.path.join(data_root, f"variant_{vid}")
    os.makedirs(vdir, exist_ok=True)
    with open(os.path.join(vdir, "hydra_state.json"), "w") as f:
        json.dump({"date": "2026-07-01", "entries": []}, f)


@pytest.fixture
def strat(tmp_path, monkeypatch):
    """A bare HydraStrategy-shaped object exposing the discovery/render methods
    with state_file pointed at a temp data root."""
    from bots.hydra.strategy import HydraStrategy
    monkeypatch.delenv("HYDRA_VARIANT_ID", raising=False)  # poller = variant A
    data_root = tmp_path / "data"
    data_root.mkdir()
    obj = types.SimpleNamespace(state_file=str(data_root / "hydra_state.json"))
    # Bind the unbound methods so they operate on our fake object.
    obj._discover_variant_ids = HydraStrategy._discover_variant_ids.__get__(obj)
    obj._format_calendar_comparison = HydraStrategy._format_calendar_comparison.__get__(obj)
    obj._data_root = str(data_root)
    return obj


def test_discover_is_group_scoped(strat):
    _make_ic_variant(strat._data_root, "b")
    _make_ic_variant(strat._data_root, "c")
    _make_calendar_variant(strat._data_root, "d", [])
    _make_calendar_variant(strat._data_root, "e", [])
    # IC group: only b, c (NOT d/e — the credit-vs-debit separation, by data)
    assert strat._discover_variant_ids("ic_0dte") == ["b", "c"]
    # default (variant A's group) is the IC group → same {b,c}
    assert strat._discover_variant_ids() == ["b", "c"]
    # calendar group: only d, e
    assert strat._discover_variant_ids("calendar_multiday") == ["d", "e"]


def test_compare_group_resolver():
    from bots.hydra.strategy import HydraStrategy
    r = HydraStrategy._resolve_compare_group
    assert r("calendars") == "calendar_multiday"
    assert r("Multi-day Calendar") == "calendar_multiday"
    assert r("ic_0dte") == "ic_0dte"
    assert r("0dte") == "ic_0dte"
    assert r("garbage") is None
    assert r("") is None


def test_calendar_renderer_excludes_ic_fields(strat):
    _make_calendar_variant(strat._data_root, "d", [{
        "entry_number": 1, "strategy_id": "d", "dc_phase": "open",
        "is_risk_free": False, "contracts": 1, "net_debit": 900,
        "transform_credit": None,
        "legs": {
            "short_call": {"strike": 5300, "expiry": "2026-07-18"},
            "long_call": {"strike": 5300, "expiry": "2026-07-25"},
            "short_put": {"strike": 5300, "expiry": "2026-07-18"},
        },
    }])
    _make_calendar_variant(strat._data_root, "e", [])
    out = strat._format_calendar_comparison("calendar_multiday")
    # Debit-native: net_debit is shown.
    assert "debit" in out.lower()
    assert "$900" in out
    # Enumerates ALL calendar-group members (D and E), not just hardcoded D.
    assert "DC Time Machine (D)" in out
    assert "SPY Double Calendar (E)" in out
    # NEVER leaks IC credit/buffer/spread-width vocabulary.
    low = out.lower()
    for bad in ("total credit", "credit collected", "buffer", "spread width",
                "call stop", "put stop", "call_stop", "put_stop"):
        assert bad not in low, f"calendar render leaked IC field: {bad!r}"


# ===========================================================================
# Telegram headers + /help are taxonomy-driven (display-only)
# ===========================================================================

def test_poller_header_is_taxonomy_driven(monkeypatch):
    from bots.hydra.telegram_commands import _poller_header
    monkeypatch.delenv("HYDRA_VARIANT_ID", raising=False)  # poller = A
    assert _poller_header() == "HYDRA Baseline (A)"


def test_help_lists_strategies_grouped(monkeypatch):
    """/help enumerates every taxonomy strategy under its comparability group
    with display name + letter, and keeps all command names."""
    import bots.hydra.telegram_commands as tc
    monkeypatch.delenv("HYDRA_VARIANT_ID", raising=False)
    sent = {}
    handler = tc.TelegramCommandHandler.__new__(tc.TelegramCommandHandler)
    handler._send_message = lambda chat_id, text: sent.__setitem__("text", text)
    handler._handle_help("123")
    txt = sent["text"]
    # Group labels + member display names present.
    assert "0DTE Iron Condor" in txt
    assert "Multi-day Calendar" in txt
    assert "HYDRA Baseline (A)" in txt
    assert "SPY Double Calendar (E)" in txt
    # All existing command names retained.
    for cmd in ("/status", "/snapshot", "/entry", "/lastday", "/week", "/account",
                "/stops", "/config", "/set", "/hermes", "/apollo", "/clio",
                "/compare", "/calendars", "/restart", "/stop", "/help"):
        assert cmd in txt, f"/help dropped {cmd}"
