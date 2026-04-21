"""Regression test for Phase 2 T-1: AlertService accepts `contracts` kwarg
and auto-prefixes [{N}c] on the title when > 1.

Run: python scripts/test_alert_service_contracts.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Enable dry-run mode so send_alert doesn't require Pub/Sub credentials
os.environ["ALERT_DRY_RUN"] = "true"


def _build_service():
    from shared.alert_service import AlertService
    # Minimal enabled config — dry-run so no real Pub/Sub
    svc = AlertService(
        config={"alerts": {"enabled": True, "email": "test@example.com"}},
        bot_name="TEST_BOT",
    )
    return svc


def test_backwards_compat_default_1c():
    """Old callers that don't pass `contracts` kwarg still work — no title change."""
    print("\n[T1-1] Legacy caller without contracts kwarg: no title mutation")
    from shared.alert_service import AlertType, AlertPriority
    svc = _build_service()
    captured = {}
    original_log = svc.__class__.__dict__  # no-op; we'll just call and check return

    # Patch json.dumps so we can capture the payload
    import shared.alert_service as mod
    original_log_info = mod.logger.info
    logs = []
    def capture(msg, *a, **k): logs.append(msg)
    mod.logger.info = capture
    try:
        ok = svc.send_alert(
            alert_type=AlertType.POSITION_OPENED,
            title="Position Opened",
            message="something happened",
            priority=AlertPriority.MEDIUM,
        )
        assert ok  # dry-run returns True
        combined = "\n".join(logs)
        # Legacy behavior: no [Nc] prefix at 1c (default)
        assert "[1c]" not in combined, f"should not prefix 1c: {combined}"
        # But service-side log line should still mention "Position Opened"
        assert "Position Opened" in combined
        print("  \u2713 contracts defaults to 1 and does NOT prefix the title")
    finally:
        mod.logger.info = original_log_info


def test_contracts_2_prefixes_title():
    """When contracts=2, title gets [2c] prefix automatically."""
    print("\n[T1-2] contracts=2 adds [2c] prefix to title")
    from shared.alert_service import AlertType, AlertPriority
    import shared.alert_service as mod
    svc = _build_service()
    logs = []
    orig = mod.logger.info
    mod.logger.info = lambda m, *a, **k: logs.append(m)
    try:
        svc.send_alert(
            alert_type=AlertType.POSITION_OPENED,
            title="Position Opened",
            message="two-contract entry",
            priority=AlertPriority.MEDIUM,
            contracts=2,
        )
        combined = "\n".join(logs)
        assert "[2c] Position Opened" in combined, (
            f"expected '[2c] Position Opened' in logs, got:\n{combined}"
        )
        print("  \u2713 title auto-prefixed with [2c]")
    finally:
        mod.logger.info = orig


def test_contracts_none_treated_as_1():
    """Null-safe pattern: contracts=None falls back to 1 (no prefix)."""
    print("\n[T1-3] contracts=None falls back to 1 (no prefix)")
    from shared.alert_service import AlertType, AlertPriority
    import shared.alert_service as mod
    svc = _build_service()
    logs = []
    orig = mod.logger.info
    mod.logger.info = lambda m, *a, **k: logs.append(m)
    try:
        # type-ignore: intentionally testing the null-safety of the service
        svc.send_alert(
            alert_type=AlertType.POSITION_OPENED,
            title="Position Opened",
            message="x",
            priority=AlertPriority.MEDIUM,
            contracts=None,  # type: ignore
        )
        combined = "\n".join(logs)
        assert "[" not in combined.split("Position Opened")[0][-20:], (
            f"contracts=None should not produce a prefix: {combined}"
        )
        print("  \u2713 contracts=None coerced to 1, no prefix added")
    finally:
        mod.logger.info = orig


def test_contracts_0_treated_as_1():
    """Invalid contracts=0 falls back to 1 (no prefix)."""
    print("\n[T1-4] contracts=0 falls back to 1 (no prefix)")
    from shared.alert_service import AlertType, AlertPriority
    import shared.alert_service as mod
    svc = _build_service()
    logs = []
    orig = mod.logger.info
    mod.logger.info = lambda m, *a, **k: logs.append(m)
    try:
        svc.send_alert(
            alert_type=AlertType.POSITION_OPENED,
            title="Position Opened",
            message="x",
            priority=AlertPriority.MEDIUM,
            contracts=0,
        )
        combined = "\n".join(logs)
        assert "[0c]" not in combined
        print("  \u2713 contracts=0 coerced to 1, no [0c] prefix")
    finally:
        mod.logger.info = orig


def test_details_enrichment():
    """`contracts` is added to details dict for structured logging."""
    print("\n[T1-5] details payload includes 'contracts' field")
    from shared.alert_service import AlertType, AlertPriority
    import shared.alert_service as mod
    svc = _build_service()
    logs = []
    orig = mod.logger.info
    mod.logger.info = lambda m, *a, **k: logs.append(str(m))
    try:
        svc.send_alert(
            alert_type=AlertType.POSITION_OPENED,
            title="Position Opened",
            message="x",
            priority=AlertPriority.MEDIUM,
            details={"entry_number": 1},
            contracts=2,
        )
        combined = "\n".join(logs)
        # Dry-run dumps the payload; should include contracts=2
        assert '"contracts": 2' in combined, (
            f"expected 'contracts: 2' in dry-run payload: {combined[:500]}"
        )
        print("  \u2713 payload details includes contracts=2")
    finally:
        mod.logger.info = orig


def test_existing_prefix_not_duplicated():
    """If title already has [2c] prefix, don't double-prefix it."""
    print("\n[T1-6] Pre-prefixed title is not re-prefixed")
    from shared.alert_service import AlertType, AlertPriority
    import shared.alert_service as mod
    svc = _build_service()
    logs = []
    orig = mod.logger.info
    mod.logger.info = lambda m, *a, **k: logs.append(m)
    try:
        svc.send_alert(
            alert_type=AlertType.POSITION_OPENED,
            title="[2c] Position Opened",  # already prefixed
            message="x",
            priority=AlertPriority.MEDIUM,
            contracts=2,
        )
        combined = "\n".join(logs)
        # Should not have [2c] [2c] double-prefix
        assert "[2c] [2c]" not in combined, f"double-prefix detected: {combined}"
        print("  \u2713 pre-prefixed title preserved, no double [2c] [2c]")
    finally:
        mod.logger.info = orig


def main():
    tests = [
        test_backwards_compat_default_1c,
        test_contracts_2_prefixes_title,
        test_contracts_none_treated_as_1,
        test_contracts_0_treated_as_1,
        test_details_enrichment,
        test_existing_prefix_not_duplicated,
    ]
    fails = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  \u2717 FAIL: {e}")
            fails += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            fails += 1
    print("\n" + "=" * 60)
    if fails == 0:
        print(f"ALL {len(tests)} T-1 ALERT-SERVICE TESTS PASSED")
        return 0
    print(f"{fails} / {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
