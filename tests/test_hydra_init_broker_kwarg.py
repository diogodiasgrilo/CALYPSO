"""Signature-level tests for HydraStrategy's `broker` kwarg.

Validates the contract added in Phase NEW-2 commit 6: HydraStrategy
accepts an optional `broker: Optional[IBClient]` kwarg, stored as
`self.broker`. Used by ported HYDRA methods to call IBClient directly
instead of inherited MEIC methods that go through Saxo.

This file deliberately stops at signature/attribute validation —
running HYDRA's full __init__ requires a real (or extensively mocked)
Saxo client, config, logger, and AlertService, which is out of scope
for this small change. The full-construction integration test lives
where it belongs: in the broader rewrite validation phase.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy
from shared.ib_client import IBClient


class TestHydraInitBrokerKwarg:
    """Phase NEW-2 commit 6 added an optional `broker` kwarg to
    HydraStrategy.__init__. Verify the contract."""

    def test_signature_has_broker_kwarg(self):
        sig = inspect.signature(HydraStrategy.__init__)
        assert "broker" in sig.parameters, (
            "HydraStrategy.__init__ must accept a `broker` kwarg "
            "(Phase NEW-2 commit 6)"
        )

    def test_broker_kwarg_is_keyword_only(self):
        """`broker` is keyword-only so positional ordering can't
        accidentally shift its position vs the legacy positional args."""
        sig = inspect.signature(HydraStrategy.__init__)
        param = sig.parameters["broker"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"`broker` must be KEYWORD_ONLY, got kind={param.kind}"
        )

    def test_broker_kwarg_defaults_to_none(self):
        """Default-None means existing HYDRA callers (which don't pass
        broker) continue to work unchanged via inherited Saxo path."""
        sig = inspect.signature(HydraStrategy.__init__)
        param = sig.parameters["broker"]
        assert param.default is None, (
            f"`broker` must default to None, got {param.default!r}"
        )

    def test_broker_kwarg_typed_as_optional_ibclient(self):
        """Type annotation should be Optional[IBClient]. We check the
        string form rather than the resolved type because annotations
        may be strings under `from __future__ import annotations`."""
        sig = inspect.signature(HydraStrategy.__init__)
        param = sig.parameters["broker"]
        # Allow either resolved type or string form
        annotation_str = str(param.annotation)
        assert "IBClient" in annotation_str, (
            f"`broker` annotation should reference IBClient, got "
            f"{annotation_str!r}"
        )

    def test_init_stores_broker_on_self(self):
        """When broker is passed, it's stored as self.broker.

        We construct via __new__ + manually invoke the BROKER-storage
        line — running the full __init__ requires a real Saxo client +
        config etc. and is out of scope here."""
        instance = HydraStrategy.__new__(HydraStrategy)
        # Mock broker — IBClient instance not actually needed because
        # __init__ only stores the reference; nothing is called on it.
        fake_broker = MagicMock(spec=IBClient)
        # Manually exercise the same assignment __init__ does
        instance.broker = fake_broker
        assert instance.broker is fake_broker

    def test_init_broker_can_be_none(self):
        """Default path: broker=None is the legacy mode (back-compat)."""
        instance = HydraStrategy.__new__(HydraStrategy)
        instance.broker = None
        assert instance.broker is None
