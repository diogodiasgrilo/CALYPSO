"""Instrument parameterization — config reads + fail-fast assertion (item 2, commit 6).

Pins that the new instrument knobs default to today's SPX/0DTE literals (so an
absent config is byte-identical to pre-refactor behavior) and that the startup
assertion rejects unset/invalid values. _load_instrument_params is exercised in
isolation via __new__ + an injected strategy_config (the full __init__ is heavy).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import ConfigError
from bots.hydra.strategy import HydraStrategy


def _strat(cfg: dict) -> HydraStrategy:
    s = HydraStrategy.__new__(HydraStrategy)
    s.strategy_config = cfg
    return s


class TestInstrumentDefaults:
    def test_defaults_equal_todays_literals(self):
        s = _strat({})
        s._load_instrument_params()
        assert s.underlying_symbol == "SPX"
        assert s.volatility_symbol == "VIX"
        assert s.trading_class == "SPXW"
        assert s.exchange == "CBOE"
        assert s.strike_increment == 5
        assert s.target_dte == 0

    def test_config_overrides_apply(self):
        s = _strat({
            "underlying_symbol": "NDX", "volatility_symbol": "VXN",
            "trading_class": "NDXP", "exchange": "NASDAQ",
            "strike_increment": 10, "target_dte": 30,
        })
        s._load_instrument_params()
        assert s.underlying_symbol == "NDX"
        assert s.volatility_symbol == "VXN"
        assert s.trading_class == "NDXP"
        assert s.exchange == "NASDAQ"
        assert s.strike_increment == 10
        assert s.target_dte == 30


class TestAssertion:
    @pytest.mark.parametrize("key", ["underlying_symbol", "volatility_symbol", "trading_class", "exchange"])
    def test_empty_symbol_raises(self, key):
        with pytest.raises(ConfigError):
            _strat({key: ""})._load_instrument_params()

    @pytest.mark.parametrize("bad", [0, -5, "5", None, True])
    def test_bad_strike_increment_raises(self, bad):
        with pytest.raises(ConfigError):
            _strat({"strike_increment": bad})._load_instrument_params()

    @pytest.mark.parametrize("bad", [-1, 1.5, "0", True])
    def test_bad_target_dte_raises(self, bad):
        with pytest.raises(ConfigError):
            _strat({"target_dte": bad})._load_instrument_params()

    def test_valid_config_does_not_raise(self):
        _strat({"strike_increment": 25, "target_dte": 45})._load_instrument_params()

    def test_config_error_is_a_value_error(self):
        # Existing broad ValueError config handling must still catch it.
        with pytest.raises(ValueError):
            _strat({"underlying_symbol": ""})._load_instrument_params()


class TestUnderlyingSymbolThreading:
    """Commit 7 — the override path: a non-SPX underlying threads to the broker
    (not a hardcoded "SPX"). The default path is covered by the existing
    TestReadOptionChain suite."""

    def test_read_option_chain_uses_underlying_symbol(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.underlying_symbol = "NDX"  # override; class default is "SPX"
        broker = MagicMock()
        broker.get_option_chain.return_value = [6735.0]
        broker.qualify_option_strikes.return_value = {(6735.0, "C"): 1}
        s.broker = broker

        s._read_option_chain("2026-05-21", [6735.0])

        assert broker.get_option_chain.call_args[0][0] == "NDX"
        assert broker.qualify_option_strikes.call_args.kwargs["symbol"] == "NDX"

    def test_trading_class_and_exchange_thread_to_broker(self):
        # Commit 9: the live option-chain path threads trading_class + exchange.
        s = HydraStrategy.__new__(HydraStrategy)
        s.underlying_symbol = "NDX"
        s.trading_class = "NDXP"
        s.exchange = "NASDAQ"
        broker = MagicMock()
        broker.get_option_chain.return_value = [6735.0]
        broker.qualify_option_strikes.return_value = {(6735.0, "C"): 1}
        s.broker = broker

        s._read_option_chain("2026-05-21", [6735.0])

        oc_kwargs = broker.get_option_chain.call_args.kwargs
        assert oc_kwargs["trading_class"] == "NDXP"
        assert oc_kwargs["exchange"] == "NASDAQ"
        qs_kwargs = broker.qualify_option_strikes.call_args.kwargs
        assert qs_kwargs["trading_class"] == "NDXP"
        assert qs_kwargs["exchange"] == "NASDAQ"

    def test_class_default_underlying_is_spx(self):
        # A __new__-built strategy that never ran _load_instrument_params still
        # reads SPX (the canonical fallback) — pins the no-AttributeError contract.
        s = HydraStrategy.__new__(HydraStrategy)
        assert s.underlying_symbol == "SPX"


class TestVolatilitySymbolThreading:
    """Commit 8 — the VIX index read uses self.volatility_symbol, not a literal."""

    def test_update_market_data_reads_configured_vol_symbol(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.underlying_symbol = "SPX"
        s.volatility_symbol = "VXN"  # override; class default is "VIX"
        s.market_data = MagicMock()
        s.current_price = 0.0
        s.current_vix = 0.0
        seen = []

        def fake_read(sym):
            seen.append(sym)
            return (6000.0 if sym == "SPX" else 15.0, "R")

        s._read_index_price = fake_read
        s._update_market_data()
        assert "VXN" in seen  # vol leg used the configured symbol, not "VIX"
        assert "SPX" in seen

    def test_class_default_volatility_is_vix(self):
        s = HydraStrategy.__new__(HydraStrategy)
        assert s.volatility_symbol == "VIX"
