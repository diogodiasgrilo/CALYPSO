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
from unittest.mock import MagicMock

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


class TestResolveCalendarLegs:
    """Phase 2: resolve 4 conids across two expiries via the existing
    _get_option_uic (called with EXPLICIT non-0DTE expiries)."""

    def _inst(self, uic_map):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        # Shadow the bound method with a fake keyed on (strike, right, expiry).
        inst._get_option_uic = lambda strike, right, expiry: uic_map.get((strike, right, expiry))
        return inst

    def test_resolves_all_four(self):
        m = {
            (5000.0, "Call", "2026-06-26"): 11,
            (5000.0, "Call", "2026-06-29"): 12,
            (4900.0, "Put", "2026-06-26"): 21,
            (4900.0, "Put", "2026-06-29"): 22,
        }
        inst = self._inst(m)
        legs = inst._dc_resolve_calendar_legs(5000.0, 4900.0, "2026-06-26", "2026-06-29")
        assert legs == {"short_call": 11, "long_call": 12, "short_put": 21, "long_put": 22}

    def test_none_when_a_leg_unresolved(self):
        m = {
            (5000.0, "Call", "2026-06-26"): 11,
            (5000.0, "Call", "2026-06-29"): 12,
            (4900.0, "Put", "2026-06-26"): 21,
            # long_put missing -> None
        }
        inst = self._inst(m)
        assert inst._dc_resolve_calendar_legs(5000.0, 4900.0, "2026-06-26", "2026-06-29") is None


class TestReadIV:
    def _inst(self, greeks_return=None, raise_exc=False):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst.broker = MagicMock()
        if raise_exc:
            inst.broker.get_option_greeks.side_effect = RuntimeError("boom")
        else:
            inst.broker.get_option_greeks.return_value = greeks_return
        return inst

    def test_valid_iv(self):
        assert self._inst({"iv": 0.18})._dc_read_iv(1) == 0.18

    def test_zero_iv_is_none(self):
        assert self._inst({"iv": 0.0})._dc_read_iv(1) is None

    def test_missing_iv_is_none(self):
        assert self._inst({"iv": None})._dc_read_iv(1) is None
        assert self._inst({})._dc_read_iv(1) is None

    def test_broker_error_is_none(self):
        assert self._inst(raise_exc=True)._dc_read_iv(1) is None


class TestFrontBackIV:
    def _inst(self, conids, iv_by_conid):
        inst = DoubleCalendarStrategy.__new__(DoubleCalendarStrategy)
        inst._get_option_uic = lambda strike, right, expiry: conids.get(expiry)
        inst.broker = MagicMock()
        inst.broker.get_option_greeks.side_effect = lambda c: {"iv": iv_by_conid.get(c)}
        return inst

    def test_returns_front_back_pair(self):
        inst = self._inst(
            conids={"2026-06-26": 1, "2026-06-29": 2},
            iv_by_conid={1: 0.25, 2: 0.20},
        )
        assert inst._dc_front_back_iv(5000.0, "Call", "2026-06-26", "2026-06-29") == (0.25, 0.20)

    def test_none_when_back_iv_missing(self):
        inst = self._inst(
            conids={"2026-06-26": 1, "2026-06-29": 2},
            iv_by_conid={1: 0.25, 2: None},
        )
        assert inst._dc_front_back_iv(5000.0, "Call", "2026-06-26", "2026-06-29") is None

    def test_none_when_conid_unresolved(self):
        inst = self._inst(conids={"2026-06-26": 1}, iv_by_conid={1: 0.25})
        # long expiry has no conid -> None before any greeks read
        assert inst._dc_front_back_iv(5000.0, "Call", "2026-06-26", "2026-06-29") is None
