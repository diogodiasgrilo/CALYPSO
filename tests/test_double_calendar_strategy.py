"""DoubleCalendarStrategy (Strategy D — "DC Time Machine") scaffold tests.

Pins the SAFETY-critical scaffold behavior: the dry-run lock refuses any
non-dry-run construction (D's entry/transformer logic is stubbed and a
real-order run would need the coexistence MUST-FIXes), the undefined-risk
contract flag, the BOT_NAME, the DCPhase enum, and the multi-day open-position
predicate that the lifecycle overrides depend on.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from bots.hydra.base_strategy import ConfigError
from bots.hydra.double_calendar_strategy import DCPhase, DoubleCalendarStrategy
from bots.hydra.strategy import HydraStrategy


class TestDryRunLock:
    """The scaffold must refuse non-dry-run construction BEFORE any broker I/O —
    same guard pattern as StrangleStrategy."""

    def _patch_super_init(self, monkeypatch):
        # Lightweight super().__init__ that only sets dry_run (avoids the heavy
        # real HydraStrategy construction; we're testing D's gate, not the base).
        def fake_init(self, *a, **k):
            self.dry_run = k.get("dry_run", False)
        monkeypatch.setattr(HydraStrategy, "__init__", fake_init)

    def test_refuses_non_dry_run(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        with pytest.raises(ConfigError):
            DoubleCalendarStrategy(None, {}, None, dry_run=False)

    def test_refuses_when_dry_run_kwarg_absent(self, monkeypatch):
        # build_strategy always passes dry_run; absence must also be refused
        # (the lock reads kwargs.get("dry_run", False) BEFORE super()).
        self._patch_super_init(monkeypatch)
        with pytest.raises(ConfigError):
            DoubleCalendarStrategy(None, {}, None)

    def test_allows_dry_run(self, monkeypatch):
        self._patch_super_init(monkeypatch)
        s = DoubleCalendarStrategy(None, {}, None, dry_run=True)
        assert isinstance(s, DoubleCalendarStrategy)
        assert s.dry_run is True


class TestContract:
    def test_is_undefined_risk(self):
        # Debit calendar / transformer manages its own structure — the base
        # naked-short emergency path must stay off.
        assert DoubleCalendarStrategy.requires_protective_wings is False

    def test_bot_name(self):
        assert DoubleCalendarStrategy.BOT_NAME == "DCTM"

    def test_dcphase_values(self):
        assert {p.value for p in DCPhase} == {"calendar", "transformed", "closed"}


class TestMultiDayPredicate:
    """_dc_entry_is_open drives the lifecycle carve-outs (carry-forward across
    the daily reset; settlement treating a held position as normal)."""

    def _inst(self):
        # Build without __init__ — the predicate only reads entry.dc_phase.
        return DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)

    def test_open_phases_are_open(self):
        inst = self._inst()
        assert inst._dc_entry_is_open(SimpleNamespace(dc_phase=DCPhase.CALENDAR)) is True
        assert inst._dc_entry_is_open(SimpleNamespace(dc_phase=DCPhase.TRANSFORMED)) is True

    def test_closed_or_unset_is_not_open(self):
        inst = self._inst()
        assert inst._dc_entry_is_open(SimpleNamespace(dc_phase=DCPhase.CLOSED)) is False
        # A recovered/plain entry with no dc_phase must NOT be treated as open
        # (documents the known restart-fragility gap; predicate is conservative).
        assert inst._dc_entry_is_open(SimpleNamespace(dc_phase=None)) is False
        assert inst._dc_entry_is_open(SimpleNamespace()) is False
