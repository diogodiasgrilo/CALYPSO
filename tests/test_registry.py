"""Strategy registry tests (modularity-audit item 4a).

Pins that name-resolution preserves the legacy entrypoint precedence
(brandon.enabled wins → strategy.name → "hydra") and that build_strategy
constructs with the canonical (broker, config, trade_logger, dry_run) signature.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bots.hydra.registry as reg
from bots.hydra.registry import (
    StrategyNotRegistered,
    available_strategies,
    build_strategy,
    resolve_strategy_class,
    resolve_strategy_name,
)


class TestResolveName:
    def test_empty_config_defaults_to_hydra(self):
        assert resolve_strategy_name({}) == "hydra"
        assert resolve_strategy_name({"strategy": {}}) == "hydra"

    def test_brandon_enabled_selects_brandon(self):
        assert resolve_strategy_name({"strategy": {"brandon": {"enabled": True}}}) == "brandon"

    def test_brandon_disabled_falls_through(self):
        assert resolve_strategy_name({"strategy": {"brandon": {"enabled": False}}}) == "hydra"

    def test_explicit_registered_name(self):
        # A name that IS a registered key is honored.
        assert resolve_strategy_name({"strategy": {"name": "strangle"}}) == "strangle"

    def test_double_calendar_name_resolves(self):
        # Strategy D — registered name selects it (with brandon disabled so the
        # brandon.enabled precedence doesn't win first).
        cfg = {"strategy": {"name": "double_calendar", "brandon": {"enabled": False}}}
        assert resolve_strategy_name(cfg) == "double_calendar"

    def test_spy_double_calendar_name_resolves(self):
        # Strategy E — registered name selects it (brandon disabled so the
        # brandon.enabled precedence doesn't win first).
        cfg = {"strategy": {"name": "spy_double_calendar", "brandon": {"enabled": False}}}
        assert resolve_strategy_name(cfg) == "spy_double_calendar"

    def test_descriptive_legacy_name_falls_through_to_default(self):
        # Regression: the real A config carries a human-readable label here that
        # the pre-registry entrypoint ignored. It must NOT crash with
        # StrategyNotRegistered — fall through to the default "hydra".
        cfg = {"strategy": {"name": "HYDRA (Trend Following Hybrid)"}}
        assert resolve_strategy_name(cfg) == "hydra"

    def test_unregistered_name_falls_through_to_default(self):
        assert resolve_strategy_name({"strategy": {"name": "typo_strat"}}) == "hydra"

    def test_brandon_flag_wins_over_name(self):
        # Legacy precedence: the brandon.enabled boolean is checked first.
        cfg = {"strategy": {"name": "strangle", "brandon": {"enabled": True}}}
        assert resolve_strategy_name(cfg) == "brandon"


class TestResolveClass:
    def test_hydra_resolves(self):
        assert resolve_strategy_class("hydra").__name__ == "HydraStrategy"

    def test_brandon_resolves(self):
        assert resolve_strategy_class("brandon").__name__ == "BrandonHydraStrategy"

    def test_strangle_resolves(self):
        assert resolve_strategy_class("strangle").__name__ == "StrangleStrategy"

    def test_double_calendar_resolves(self):
        assert resolve_strategy_class("double_calendar").__name__ == "DoubleCalendarStrategy"

    def test_spy_double_calendar_resolves(self):
        assert resolve_strategy_class("spy_double_calendar").__name__ == "SpyDoubleCalendarStrategy"

    def test_unknown_raises(self):
        with pytest.raises(StrategyNotRegistered):
            resolve_strategy_class("nope")

    def test_available_lists_registered(self):
        assert available_strategies() == [
            "brandon", "double_calendar", "hydra", "spy_double_calendar", "strangle"
        ]


class TestBuildStrategy:
    def test_constructs_with_canonical_signature(self, monkeypatch):
        captured = {}

        class Fake:
            def __init__(self, broker, config, trade_logger, dry_run=False):
                captured.update(broker=broker, config=config,
                                logger=trade_logger, dry_run=dry_run)

        monkeypatch.setattr(reg, "resolve_strategy_class", lambda name: Fake)
        out = build_strategy({"x": 1}, "BROKER", "LOGGER", dry_run=True)
        assert isinstance(out, Fake)
        assert captured == {"broker": "BROKER", "config": {"x": 1},
                            "logger": "LOGGER", "dry_run": True}

    def test_routes_brandon_flag_to_brandon_class(self, monkeypatch):
        seen = {}

        def fake_resolve(name):
            seen["name"] = name
            return lambda *a, **k: object()

        monkeypatch.setattr(reg, "resolve_strategy_class", fake_resolve)
        build_strategy({"strategy": {"brandon": {"enabled": True}}}, None, None)
        assert seen["name"] == "brandon"


class TestStrategyContract:
    """Item 4b — MEICStrategy is an abc with the template-method hooks; the
    concrete strategies implement all of them (else they'd be uninstantiable)."""

    HOOKS = frozenset({"_calculate_strikes", "_initiate_entry", "_check_stop_losses"})

    def test_base_is_abstract_with_the_three_hooks(self):
        from bots.hydra.base_strategy import MEICStrategy
        assert MEICStrategy.__abstractmethods__ == self.HOOKS

    def test_concrete_strategies_implement_all_hooks(self):
        from bots.hydra.brandon.strategy import BrandonHydraStrategy
        from bots.hydra.strategy import HydraStrategy
        assert HydraStrategy.__abstractmethods__ == frozenset()
        assert BrandonHydraStrategy.__abstractmethods__ == frozenset()

    def test_base_cannot_be_instantiated(self):
        from bots.hydra.base_strategy import MEICStrategy
        with pytest.raises(TypeError):
            MEICStrategy.__new__(MEICStrategy)
