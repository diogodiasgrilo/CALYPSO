"""A str alert_type silently bypassed the anti-spam gate — 2026-11-16.

``scripts/audit_telemetry_health.py`` passed ``alert_type="data_quality"`` as a
STRING where ``send_alert`` expects the ``AlertType`` enum. Every gate downstream
reads ``alert_type.value`` or tests membership in a set of enum members, so the
string raised ``AttributeError`` inside the gate, which logged:

    Alert gate error (failing open, alert sent): AttributeError:
    'str' object has no attribute 'value'

THAT LOG LINE WAS FALSE, and the fix is partly about that. The gate did fail
open — but the send then reached the Pub/Sub payload build, hit ``.value`` on the
same string, and raised AGAIN, out of ``send_alert`` entirely. Verified against
the pre-fix code: ``RAISED: AttributeError``, nothing published. The audit script
catches that into ``(alert failed, non-fatal: ...)`` printed to a systemd unit
nobody reads, so **this check's ``--alert`` had never delivered a single
alert** — not "delivered without dedup", which is what the gate's own log line
had everyone believing, this file's first draft included.

The gating sibling, ``scripts/audit_fleet_correctness.py``, passes the enum and
was never affected.

TWO HALVES, and the second is the one that matters:
  1. the call site now passes ``AlertType.DATA_QUALITY``;
  2. ``send_alert`` COERCES a str at the boundary — because fixing only the call
     site would leave the next one free to reintroduce the same silent bypass,
     and no test could see it. The gate is a chokepoint; the normalisation
     belongs there.

``test_control_the_raw_gate_really_does_break_on_a_str`` reproduces the original
defect directly against the gate, so these tests cannot pass vacuously.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.alert_service import AlertService, AlertPriority, AlertType  # noqa: E402


@pytest.fixture(autouse=True)
def _dry_run_env(monkeypatch):
    # Makes a "published" alert observable as a True return with no live
    # Pub/Sub channel. The anti-spam gate still runs before the short-circuit,
    # which is the whole point of this file.
    monkeypatch.setenv("ALERT_DRY_RUN", "true")
    yield


def _svc(name="TELEMETRY") -> AlertService:
    return AlertService({"alerts": {"enabled": True}}, name)


# ---------------------------------------------------------------------------
# The defect, reproduced
# ---------------------------------------------------------------------------

def test_control_the_raw_gate_really_does_break_on_a_str():
    """CONTROL — the exact failure, one level below the fix. The fingerprint the
    dedup gate is built on cannot be computed from a string, which is why the
    gate threw and failed open."""
    svc = _svc()
    with pytest.raises(AttributeError):
        svc._alert_fingerprint("data_quality", "title", None)
    # ...and works for the enum the call site now passes.
    assert svc._alert_fingerprint(AlertType.DATA_QUALITY, "title", None)


# ---------------------------------------------------------------------------
# The fix: coercion at the boundary restores the gate
# ---------------------------------------------------------------------------

def test_a_str_alert_type_is_coerced_so_dedup_applies():
    """THE invariant. 20 identical alerts sent with the STRING must collapse to
    one, exactly as the enum does. Before the fix every one of them was
    delivered."""
    svc = _svc()
    results = [
        svc.send_alert(alert_type="data_quality", title="Telemetry health: 3 finding(s)",
                       message="same body every night")
        for _ in range(20)
    ]
    assert results[0] is True
    assert sum(results) == 1, "a str alert_type still bypasses the dedup gate"


def test_the_enum_path_is_unchanged():
    """The fix must not alter the behaviour every other call site already has."""
    svc = _svc()
    results = [
        svc.send_alert(alert_type=AlertType.DATA_QUALITY, title="enum title",
                       message="same body")
        for _ in range(20)
    ]
    assert results[0] is True
    assert sum(results) == 1


def test_a_str_is_coerced_by_value():
    svc = _svc()
    seen = {}
    orig = svc._alert_fingerprint

    def spy(alert_type, *a, **k):
        seen["type"] = alert_type
        return orig(alert_type, *a, **k)

    svc._alert_fingerprint = spy  # type: ignore[method-assign]
    svc.send_alert(alert_type="data_quality", title="t", message="m")
    assert seen["type"] is AlertType.DATA_QUALITY


def test_a_str_is_coerced_by_member_name_too():
    """Both spellings a caller might reach for."""
    svc = _svc()
    seen = {}
    orig = svc._alert_fingerprint

    def spy(alert_type, *a, **k):
        seen["type"] = alert_type
        return orig(alert_type, *a, **k)

    svc._alert_fingerprint = spy  # type: ignore[method-assign]
    svc.send_alert(alert_type="DATA_QUALITY", title="t", message="m")
    assert seen["type"] is AlertType.DATA_QUALITY


def test_coercion_warns_so_the_call_site_gets_fixed(caplog):
    svc = _svc()
    with caplog.at_level("WARNING"):
        svc.send_alert(alert_type="data_quality", title="t", message="m")
    assert any("as a str" in r.message for r in caplog.records)


def test_the_enum_path_does_not_warn(caplog):
    svc = _svc()
    with caplog.at_level("WARNING"):
        svc.send_alert(alert_type=AlertType.DATA_QUALITY, title="t", message="m")
    assert not any("as a str" in r.message for r in caplog.records)


def test_an_unmappable_str_is_delivered_as_unclassified(caplog):
    """An alert_type this enum does not know is a programming error, but losing
    the MESSAGE over it is the one outcome an alerting path must never have —
    and losing it is exactly what happened before, two steps past a log line
    claiming the opposite. It now coerces to a real member so every `.value`
    downstream works, and says so at WARNING."""
    svc = _svc()
    with caplog.at_level("WARNING"):
        ok = svc.send_alert(alert_type="not_a_real_type", title="t", message="m",
                            priority=AlertPriority.HIGH)
    assert ok is True
    assert any("no matching AlertType member" in r.message for r in caplog.records)
    assert any("unclassified" in r.message for r in caplog.records)


def test_the_unmappable_fallback_is_itself_gated():
    """Coercing to a real member also restores dedup for this path — the
    fallback must not become a hole in the chokepoint."""
    svc = _svc()
    results = [
        svc.send_alert(alert_type="not_a_real_type", title="identical", message="m",
                       priority=AlertPriority.HIGH)
        for _ in range(10)
    ]
    assert results[0] is True
    assert sum(results) == 1


def test_control_the_pre_fix_publish_path_also_broke_on_a_str():
    """CONTROL — the SECOND failure, the one the gate's log line hid. Even with
    the gate bypassed, the payload build could not read `.value` off a string,
    so the alert was never published. This is why the fix is a coercion at the
    boundary rather than a more forgiving gate."""
    from shared.alert_service import AlertType as _AT
    assert not hasattr("data_quality", "value")
    assert _AT.UNCLASSIFIED.value == "unclassified"


def test_distinct_titles_still_get_through_under_a_coerced_str():
    """Coercion must not over-suppress: different content is different."""
    svc = _svc()
    a = svc.send_alert(alert_type="data_quality", title="finding A", message="m")
    b = svc.send_alert(alert_type="data_quality", title="finding B", message="m")
    assert a is True and b is True


# ---------------------------------------------------------------------------
# The call site
# ---------------------------------------------------------------------------

def test_the_audit_script_passes_the_enum_not_a_string():
    """A source-level check, and deliberately labelled as one: the behavioural
    guarantee is the coercion above (which holds even if this regresses). This
    only keeps the nightly WARNING from coming back."""
    src = Path(__file__).resolve().parents[1] / "scripts" / "audit_telemetry_health.py"
    tree = ast.parse(src.read_text())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and any(kw.arg == "alert_type" for kw in n.keywords)
    ]
    assert calls, "no send_alert(alert_type=...) call found — did the alert move?"
    for call in calls:
        kw = next(k for k in call.keywords if k.arg == "alert_type")
        assert isinstance(kw.value, ast.Attribute), (
            f"alert_type is passed as {ast.dump(kw.value)[:60]} — pass AlertType.X"
        )
        assert kw.value.attr == "DATA_QUALITY"
