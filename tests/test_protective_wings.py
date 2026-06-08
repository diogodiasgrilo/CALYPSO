"""Pluggable naked-short safety (modularity-audit item 6a / plan step S1).

requires_protective_wings gates the naked-short emergency path so an
undefined-risk strategy (e.g. a short strangle) holds naked shorts by design
without _handle_naked_short emergency-closing them. The iron-condor family
keeps the flag True → behavior unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy


class TestDefault:
    def test_ic_family_requires_wings_by_default(self):
        # The flag is a class attr → True for HydraStrategy (and Brandon).
        assert HydraStrategy.requires_protective_wings is True
        from bots.hydra.brandon.strategy import BrandonHydraStrategy
        assert BrandonHydraStrategy.requires_protective_wings is True


class TestHandlerGate:
    def _strategy(self, *, requires_wings):
        s = HydraStrategy.__new__(HydraStrategy)
        s.requires_protective_wings = requires_wings
        s.dry_run = False  # so the dry-run gate doesn't mask the wings gate
        s.alert_service = MagicMock()
        return s

    def test_no_wings_skips_emergency_close(self):
        s = self._strategy(requires_wings=False)
        # Should early-return: no alert, no close attempt.
        s._handle_naked_short(("short_call", "POS", 12345))
        s.alert_service.send_alert.assert_not_called()

    def test_with_wings_proceeds_past_the_gate(self):
        # With wings required, the gate does NOT early-return — execution
        # reaches the CRITICAL "NAKED SHORT DETECTED" alert. (We don't drive the
        # full close path here; that the alert fired proves the gate passed.)
        s = self._strategy(requires_wings=True)
        s._log_safety_event = MagicMock()
        try:
            s._handle_naked_short(("short_call", "POS", 12345))
        except Exception:
            pass  # downstream close path may raise on this bare __new__ stub
        assert s.alert_service.send_alert.called
        first = s.alert_service.send_alert.call_args_list[0]
        assert first.kwargs["title"] == "NAKED SHORT DETECTED"
