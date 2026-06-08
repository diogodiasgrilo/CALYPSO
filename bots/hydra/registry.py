"""Strategy registry — the "pick a strategy by name" extension point.

Modularity-audit item 4a. Replaces the hardcoded ``if brandon.enabled / else``
branch in ``main.py`` with a name → class lookup, so a new strategy is added by
registering it here (one line) rather than editing the bot's entrypoint.

Lazy, import-path based: a strategy's module is imported only when it is selected
(preserving the pre-existing behavior where Brandon — which pulls in the Polygon
GEX stack — is imported only when enabled). Names map to ``"module.path:ClassName"``.

Backward compatibility (zero behavior change): ``resolve_strategy_name`` honors the
existing ``strategy.brandon.enabled`` boolean first (→ ``"brandon"``), then an explicit
``strategy.name`` config key, then defaults to ``"hydra"`` — exactly what ``main.py``
did before. Every registered strategy is constructed with the same signature the
entrypoint always used: ``Cls(broker, config, trade_logger, dry_run=dry_run)``.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict

# name → "module.path:ClassName". Add a row to register a new strategy.
_REGISTRY: Dict[str, str] = {
    "hydra": "bots.hydra.strategy:HydraStrategy",
    "brandon": "bots.hydra.brandon.strategy:BrandonHydraStrategy",
    # 0DTE SPX short strangle (undefined-risk). Selected via strategy.name =
    # "strangle"; dry-run only until paper-verified + an explicit operator flip.
    "strangle": "bots.hydra.strangle_strategy:StrangleStrategy",
}

# Default when neither brandon.enabled nor strategy.name is set — preserves the
# pre-registry entrypoint behavior.
DEFAULT_STRATEGY = "hydra"


class StrategyNotRegistered(ValueError):
    """Raised when a config selects a strategy name that isn't in the registry."""


def available_strategies() -> list[str]:
    """Registered strategy names (sorted)."""
    return sorted(_REGISTRY)


def resolve_strategy_name(config: Dict[str, Any]) -> str:
    """Resolve the selected strategy name from config, preserving legacy precedence.

    1. ``strategy.brandon.enabled == True`` → ``"brandon"`` (legacy flag, wins).
    2. else ``strategy.name`` if set.
    3. else ``DEFAULT_STRATEGY`` (``"hydra"``).
    """
    scfg = config.get("strategy") or {}
    if (scfg.get("brandon") or {}).get("enabled", False):
        return "brandon"
    return scfg.get("name") or DEFAULT_STRATEGY


def resolve_strategy_class(name: str):
    """Import and return the strategy class registered under ``name`` (lazy)."""
    path = _REGISTRY.get(name)
    if path is None:
        raise StrategyNotRegistered(
            f"unknown strategy {name!r}; available: {available_strategies()}"
        )
    module_path, _, cls_name = path.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


def build_strategy(config: Dict[str, Any], broker, trade_logger, *, dry_run: bool = False):
    """Construct the strategy selected by ``config`` — the entrypoint's single call.

    Equivalent to the old ``main.py`` branch: resolves the name, lazily imports the
    class, and constructs it with the canonical
    ``(broker, config, trade_logger, dry_run=dry_run)`` signature.
    """
    name = resolve_strategy_name(config)
    cls = resolve_strategy_class(name)
    return cls(broker, config, trade_logger, dry_run=dry_run)
