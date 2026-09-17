"""`time.monotonic()`'s zero point is MACHINE BOOT, not process start.

So a "last happened at" timestamp defaulting to 0.0 does NOT mean "never" — it
means "at boot". On a host that has been up for LESS than the interval being
tested, `monotonic() - 0.0` is a small number, and every guard of the shape

    if now - last < INTERVAL:   # too soon, skip
        ...

fires backwards on the very first call.

This is invisible on any long-running machine — a dev laptop with 17.8 days of
uptime makes 0.0 look like the distant past — and was found only when CI ran the
suite inside a container 33 seconds old. It broke three tests across two files
and, more importantly, three pieces of PRODUCTION behaviour:

    shared/alert_service.py     lazy Pub/Sub reinit suppressed for the first
                                minute of uptime — exactly when a bot starting
                                alongside its VM would need it
    shared/claude_client.py     `wait = 0.0 + 35 - monotonic()` SLEEPS up to 35s
                                before the first agent API call
    brandon/strategy.py         the FIRST overlay watch line per (entry, side)
                                silently suppressed
    shared/logger_service.py    the first position/metrics/summary write skipped

`broker_service._health_cache_at` uses the same default and is NOT a bug: it is
guarded by `self._health_cache is not None`, so the stale branch cannot fire
before the cache is populated. Left alone deliberately.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: (module path, attribute) pairs that hold a monotonic-compared timestamp.
MONOTONIC_TIMESTAMPS = [
    ("shared/alert_service.py", "_last_init_attempt"),
    ("shared/claude_client.py", "_last_call_at"),
    ("shared/logger_service.py", "_last_pos_snapshot_at"),
    ("shared/logger_service.py", "_last_metrics_write_at"),
    ("shared/logger_service.py", "_last_summary_write_at"),
]


@pytest.mark.parametrize("rel, attr", MONOTONIC_TIMESTAMPS)
def test_never_happened_is_negative_infinity(rel, attr):
    src = (ROOT / rel).read_text()
    m = re.search(rf"{re.escape(attr)}\s*(?::\s*float\s*)?=\s*([^\s#]+)", src)
    assert m, f"{attr} not found in {rel}"
    assert "-inf" in m.group(1), (
        f"{rel}:{attr} defaults to {m.group(1)} — on a host up for less than "
        f"the interval it is compared against, that reads as 'just now' and the "
        f"guard fires backwards on the first call."
    )


def test_overlay_watch_throttle_default_is_not_zero():
    """This one is a `.get(key, default)` rather than an attribute, and it is
    the instance that was PROVEN to fail: pinning monotonic to 33 produced zero
    watch lines where 1 was expected."""
    src = (ROOT / "bots" / "hydra" / "brandon" / "strategy.py").read_text()
    m = re.search(r"_brandon_overlay_watch_logged_at\.get\(\s*\n?\s*key,\s*([^)]+)\)", src)
    assert m, "the overlay watch throttle lookup changed shape"
    assert "-inf" in m.group(1), (
        f"defaults to {m.group(1).strip()} — suppresses the FIRST watch line "
        f"for every (entry, side) on a freshly booted host."
    )


def test_first_overlay_watch_line_survives_on_a_fresh_host():
    """The behavioural proof, at the exact monotonic value CI reported (33.27)."""
    import importlib
    import logging
    from unittest.mock import patch

    mod = importlib.import_module("tests.test_brandon_overlay_confirmation_2026_08_25")
    import bots.hydra.brandon.strategy as bs

    with patch.object(bs.time, "monotonic", lambda: 33.0):
        inst = mod._make_overlay_instance(
            brandon_overlay_debit_spread_enabled=False,
            brandon_overlay_butterfly_cutoff_hour=23,
            brandon_overlay_butterfly_cutoff_minute=59,
            current_price=6822,
        )
        entry = mod._entry()
        seen = []
        handler = logging.Handler()
        handler.emit = lambda r: seen.append(r.getMessage())
        root = logging.getLogger()
        root.addHandler(handler)
        prev = root.level
        root.setLevel(logging.INFO)
        try:
            inst._brandon_check_overlay(entry)
        finally:
            root.removeHandler(handler)
            root.setLevel(prev)

    watch = [m for m in seen if "BRANDON-OVERLAY-WATCH" in m]
    assert len(watch) == 1, (
        "the first overlay watch line is suppressed on a host up 33 seconds — "
        "the exact condition under which CI failed."
    )


def test_broker_health_cache_zero_default_is_deliberately_left_alone():
    """Same 0.0 default, NOT a bug: the stale branch is unreachable until the
    cache is populated. Pinned so nobody 'fixes' it by reflex, and so the
    reasoning survives."""
    src = (ROOT / "shared" / "broker_service.py").read_text()
    assert "_health_cache_at: float = 0.0" in src
    assert "self._health_cache is not None" in src, (
        "the guard that makes broker_service's 0.0 default safe is gone — it "
        "now has the same bug as the others."
    )
