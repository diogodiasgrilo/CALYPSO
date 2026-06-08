"""Instrument parameterization — config reads + fail-fast assertion (item 2, commit 6).

Pins that the new instrument knobs default to today's SPX/0DTE literals (so an
absent config is byte-identical to pre-refactor behavior) and that the startup
assertion rejects unset/invalid values. _load_instrument_params is exercised in
isolation via __new__ + an injected strategy_config (the full __init__ is heavy).
"""
from __future__ import annotations

import sys
from pathlib import Path

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
