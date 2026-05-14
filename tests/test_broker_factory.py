"""Tests for shared/broker/factory.py — Phase B.4.

Verifies the BROKER= env switch:
  • Default (no env var set) → SaxoBrokerAdapter
  • BROKER=saxo → SaxoBrokerAdapter (case-insensitive, whitespace-tolerant)
  • BROKER=ibkr / BROKER=ib → IBBrokerAdapter
  • Invalid BROKER value → BrokerError with actionable text
  • Saxo path requires a pre-built saxo_client (factory does NOT
    construct it — auth flow too bot-specific)
  • IB path can construct the IBClient itself from env vars
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.broker import (
    BrokerError,
    BrokerInterface,
    IBBrokerAdapter,
    SaxoBrokerAdapter,
    build_broker,
)


# ─── Selection logic ────────────────────────────────────────────────────────


class TestBrokerSelection:
    def test_default_is_saxo(self, monkeypatch):
        monkeypatch.delenv("BROKER", raising=False)
        saxo = MagicMock()
        broker = build_broker(saxo_client=saxo)
        assert isinstance(broker, SaxoBrokerAdapter)

    def test_explicit_saxo_env_selects_saxo(self, monkeypatch):
        monkeypatch.setenv("BROKER", "saxo")
        broker = build_broker(saxo_client=MagicMock())
        assert isinstance(broker, SaxoBrokerAdapter)

    def test_ibkr_env_selects_ibkr_adapter(self, monkeypatch):
        monkeypatch.setenv("BROKER", "ibkr")
        broker = build_broker(ib_client=MagicMock())
        assert isinstance(broker, IBBrokerAdapter)

    def test_ib_alias_also_selects_ibkr(self, monkeypatch):
        """BROKER=ib (legacy / shorthand) maps to the same IBBrokerAdapter."""
        monkeypatch.setenv("BROKER", "ib")
        broker = build_broker(ib_client=MagicMock())
        assert isinstance(broker, IBBrokerAdapter)

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("BROKER", "IBKR")
        broker = build_broker(ib_client=MagicMock())
        assert isinstance(broker, IBBrokerAdapter)

        monkeypatch.setenv("BROKER", "Saxo")
        broker = build_broker(saxo_client=MagicMock())
        assert isinstance(broker, SaxoBrokerAdapter)

    def test_whitespace_tolerant(self, monkeypatch):
        monkeypatch.setenv("BROKER", "  ibkr  ")
        broker = build_broker(ib_client=MagicMock())
        assert isinstance(broker, IBBrokerAdapter)

    def test_invalid_broker_raises(self, monkeypatch):
        monkeypatch.setenv("BROKER", "nasdaq_direct")
        with pytest.raises(BrokerError, match="not recognized"):
            build_broker(saxo_client=MagicMock(), ib_client=MagicMock())

    def test_custom_env_var(self, monkeypatch):
        monkeypatch.setenv("CALYPSO_BROKER_CHOICE", "ibkr")
        broker = build_broker(env_var="CALYPSO_BROKER_CHOICE", ib_client=MagicMock())
        assert isinstance(broker, IBBrokerAdapter)


# ─── Saxo path ──────────────────────────────────────────────────────────────


class TestSaxoPath:
    def test_requires_saxo_client(self, monkeypatch):
        monkeypatch.setenv("BROKER", "saxo")
        with pytest.raises(BrokerError, match="saxo_client must be supplied"):
            build_broker()

    def test_wraps_provided_saxo_client(self, monkeypatch):
        monkeypatch.setenv("BROKER", "saxo")
        saxo = MagicMock()
        broker = build_broker(saxo_client=saxo)
        # The adapter's escape hatch returns the original client
        assert broker.saxo is saxo


# ─── IB path ────────────────────────────────────────────────────────────────


class TestIBPath:
    def test_wraps_provided_ib_client(self, monkeypatch):
        monkeypatch.setenv("BROKER", "ibkr")
        ib = MagicMock()
        broker = build_broker(ib_client=ib)
        assert broker.ib is ib

    def test_constructs_ib_client_from_env(self, monkeypatch):
        """When BROKER=ibkr and no ib_client supplied, factory builds one
        via load_credentials() + IBConfig + IBClient."""
        monkeypatch.setenv("BROKER", "ibkr")
        with patch("shared.ib_oauth.load_credentials") as mock_load, \
             patch("shared.ib_client.IBClient") as mock_ib_class:
            mock_load.return_value = MagicMock(environment="paper")
            mock_ib_instance = MagicMock()
            mock_ib_class.return_value = mock_ib_instance
            broker = build_broker(ib_environment="paper")
            assert isinstance(broker, IBBrokerAdapter)
            mock_load.assert_called_once_with("paper")
            mock_ib_class.assert_called_once()

    def test_ib_environment_passthrough(self, monkeypatch):
        """ib_environment kwarg flows into load_credentials."""
        monkeypatch.setenv("BROKER", "ibkr")
        with patch("shared.ib_oauth.load_credentials") as mock_load, \
             patch("shared.ib_client.IBClient"):
            mock_load.return_value = MagicMock(environment="live")
            build_broker(ib_environment="live")
            mock_load.assert_called_once_with("live")


# ─── Return type contract ───────────────────────────────────────────────────


class TestReturnContract:
    def test_returns_broker_interface_instance(self, monkeypatch):
        monkeypatch.setenv("BROKER", "saxo")
        broker = build_broker(saxo_client=MagicMock())
        assert isinstance(broker, BrokerInterface)
