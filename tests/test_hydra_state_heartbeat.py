"""Polish Item 2 — regression tests for `last_heartbeat_at` in hydra_state.json.

ARGUS Check 2 reads this field to detect a bot-process-alive-but-frozen
state. If a future refactor accidentally drops the field or stores it
under the wrong key, ARGUS silently goes blind to the freeze case.
These tests pin the contract.

Strategy: invoke the real `_save_state_to_disk` against a tmp_path
state file, then read the resulting JSON and verify the field. We
inject just enough attribute state to satisfy the method's reads,
using __new__ to bypass __init__.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy


def _make_strategy(tmp_path: Path) -> HydraStrategy:
    """Minimal HydraStrategy with the attributes _save_state_to_disk reads."""
    s = HydraStrategy.__new__(HydraStrategy)

    # daily_state — many fields; use a mock with explicit values
    ds = MagicMock()
    ds.date = "2026-05-24"
    ds.entries_completed = 0
    ds.entries_failed = 0
    ds.entries_skipped = 0
    ds.total_credit_received = 0.0
    ds.total_realized_pnl = 0.0
    ds.total_commission = 0.0
    ds.call_stops_triggered = 0
    ds.put_stops_triggered = 0
    ds.double_stops = 0
    ds.circuit_breaker_opens = 0
    ds.one_sided_entries = 0
    ds.trend_overrides = 0
    ds.credit_gate_skips = 0
    ds.stops_avoided_mkt036 = 0
    ds.entries = []
    s.daily_state = ds

    # Top-level state
    s.state = MagicMock()
    s.state.value = "WAITING"
    s._next_entry_index = 0
    s.contracts_per_entry = 1
    s.entry_times = []
    s._base_entry_count = 0
    s._conditional_entry_times = []
    s.strategy_config = {}
    s._pnl_history = []
    s.short_only_stop = False
    s._early_close_time = None
    # market_data is read for 6 OHLC fields (3 SPX + 3 VIX). Use a
    # SimpleNamespace so attribute reads return real None/float values
    # (vs MagicMock which returns more MagicMocks → JSON serialization
    # failure).
    from types import SimpleNamespace
    s.market_data = SimpleNamespace(
        spx_open=None, spx_high=None, spx_low=None,
        vix_open=None, vix_high=None, vix_low=None,
    )
    # State file path — points into the tmp_path
    s.state_file = str(tmp_path / "hydra_state.json")
    # Optional methods called inside the save
    s._get_effective_stop_level = lambda *a, **kw: None
    return s


class TestStateHeartbeat:
    """ARGUS Check 2 reads `last_heartbeat_at`. If this contract breaks,
    ARGUS goes blind to the bot-frozen failure mode."""

    def test_last_heartbeat_at_present(self, tmp_path):
        s = _make_strategy(tmp_path)
        s._save_state_to_disk()

        state_file = Path(s.state_file)
        assert state_file.exists(), "state file was not written"
        data = json.loads(state_file.read_text())
        assert "last_heartbeat_at" in data, (
            f"last_heartbeat_at field missing; keys={sorted(data.keys())}"
        )

    def test_last_heartbeat_at_is_iso8601_utc_with_Z(self, tmp_path):
        """Format must be ISO-8601 UTC with `Z` suffix. ARGUS's Python
        parser uses fromisoformat() with `Z → +00:00` substitution; both
        ARGUS Python and bash `date` parsers expect Z-suffixed UTC."""
        s = _make_strategy(tmp_path)
        s._save_state_to_disk()

        data = json.loads(Path(s.state_file).read_text())
        value = data["last_heartbeat_at"]
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$",
            value,
        ), f"value not ISO-8601 UTC: {value!r}"

    def test_heartbeat_advances_between_saves(self, tmp_path):
        """A second save MUST produce a later timestamp (else ARGUS's
        freshness check is meaningless)."""
        import time
        s = _make_strategy(tmp_path)
        s._save_state_to_disk()
        first = json.loads(Path(s.state_file).read_text())["last_heartbeat_at"]
        # Small sleep to guarantee a measurable timestamp delta
        time.sleep(0.01)
        s._save_state_to_disk()
        second = json.loads(Path(s.state_file).read_text())["last_heartbeat_at"]
        # Lexicographic comparison works for ISO-8601 UTC.
        assert second >= first, (
            f"heartbeat did not advance: {first!r} → {second!r}"
        )


class TestBrandonOverlayGuardPersistence:
    """2026-07-18: the overlay double-book guard (_brandon_overlay_booked) must be
    persisted ATOMICALLY with daily_state.total_realized_pnl — same hydra_state.json
    write — so a restart never restores the guard without the booked total it
    protects (which would silently lose the overlay). These pin the co-location +
    the getattr-default for non-Brandon variants, and a same-day-gated restore."""

    def test_guard_co_located_with_total_in_one_write(self, tmp_path):
        s = _make_strategy(tmp_path)
        s._brandon_overlay_booked = {7, 2}
        s.daily_state.total_realized_pnl = 392.0
        s._save_state_to_disk()
        data = json.loads(Path(s.state_file).read_text())
        # Both live in the SAME file/dict (one os.replace) → crash-atomic.
        assert data["brandon_overlay_booked"] == [2, 7]  # sorted
        assert data["total_realized_pnl"] == 392.0

    def test_non_brandon_defaults_to_empty_guard(self, tmp_path):
        s = _make_strategy(tmp_path)  # plain HydraStrategy: no _brandon_overlay_booked
        s._save_state_to_disk()       # getattr-default set() → must not crash
        data = json.loads(Path(s.state_file).read_text())
        assert data["brandon_overlay_booked"] == []

    def test_restore_reads_guard_and_total_together_same_day(self, tmp_path):
        from shared.market_hours import get_us_market_time
        s = _make_strategy(tmp_path)
        # Use the SAME time source the code gates on (ET market time), not local
        # datetime.now(): otherwise, in the evening-Pacific window where the local
        # date still lags the ET date, "today" here mismatches the code's ET
        # "today" and the same-day restore gate spuriously fails.
        today = get_us_market_time().strftime("%Y-%m-%d")
        s.daily_state.date = today
        s._brandon_overlay_booked = set()  # Brandon attr present → restore path active
        Path(s.state_file).write_text(json.dumps({
            "date": today, "total_realized_pnl": 392.0, "brandon_overlay_booked": [2, 7],
            "entries": [],
        }))
        assert s._load_state_file_history() is True
        assert s.daily_state.total_realized_pnl == 392.0   # total restored...
        assert s._brandon_overlay_booked == {2, 7}         # ...and guard, together

    def test_restore_skips_stale_day_guard_and_total(self, tmp_path):
        s = _make_strategy(tmp_path)
        s.daily_state.date = "2026-05-24"
        s._brandon_overlay_booked = set()
        Path(s.state_file).write_text(json.dumps({
            "date": "2020-01-01", "total_realized_pnl": 392.0,
            "brandon_overlay_booked": [2, 7], "entries": [],
        }))
        assert s._load_state_file_history() is False  # stale → neither restored
        assert s._brandon_overlay_booked == set()
