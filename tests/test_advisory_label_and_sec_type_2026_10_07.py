"""Two cosmetic-but-real defects found in the 2026-10-07 pre-market sweep.

1. The Brandon breach ADVISORY hardcoded "credit+buffer stop is primary". That
   was WRONG on the live seat B from the 2026-07-24 swap onward — B runs
   `narrow_spread_stop.enabled=true`, so the A2 40%-of-width trigger is the
   acting stop — and it said so in ~2,200 WARNING lines (825 on 2026-10-05
   alone, 1,214 on 09-24), because it also fired on EVERY ~11s tick for as
   long as a breach persisted, burying every other warning in the file.

2. `_read_index_price` hardcoded `sec_type="IND"` for both the underlying and
   the volatility symbol. Variants E and H trade SPY (an ETF), so the spot read
   asked IBKR for a non-existent "SPY index"; it resolved the ETF only by
   falling through to the first search candidate, logging
   `qualify_contract(SPY, IND): 3 underlying candidates ... picking first`.
   E's own docstring says that read "never resolves a SPY spot" and it carries
   an OHLC-backfill workaround for it.

Both fixes default to today's behaviour so the SPX variants are unchanged.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.brandon.strategy import BrandonHydraStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _brandon(enabled=False, pct=0.40, shadow=False):
    s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
    s.narrow_spread_stop_enabled = enabled
    s.narrow_spread_stop_pct = pct
    s.narrow_spread_stop_shadow = shadow
    s._brandon_advisory_log = {}
    s.brandon_breach_exit_advisory = True
    return s


_ENTRY = SimpleNamespace(entry_number=3)
_DEC = SimpleNamespace(reason="spot 7845 beyond decel wall 7840")


class TestTheActingStopIsNamedNotAssumed:

    def test_credit_buffer_when_the_override_is_off(self):
        assert _brandon(enabled=False).acting_stop_label() == "credit+buffer"

    def test_pct_of_width_when_the_override_is_on(self):
        """B's live shape: enabled=true, pct=0.4."""
        label = _brandon(enabled=True, pct=0.40).acting_stop_label()
        assert label == "A2 %-of-width (40% of width)"
        assert "credit+buffer" not in label

    def test_the_pct_is_read_not_hardcoded(self):
        assert _brandon(enabled=True, pct=0.25).acting_stop_label() == \
            "A2 %-of-width (25% of width)"

    def test_shadow_alone_is_NOT_the_acting_stop(self):
        """C's live shape: enabled=false + shadow=true. A shadow never acts, so
        the acting stop is still credit+buffer — the message is correct on C,
        which is exactly why the fix had to be conditional rather than a blanket
        text change."""
        assert _brandon(enabled=False, shadow=True).acting_stop_label() == "credit+buffer"

    def test_a_missing_attribute_degrades_to_credit_buffer(self):
        s = BrandonHydraStrategy.__new__(BrandonHydraStrategy)
        assert s.acting_stop_label() == "credit+buffer"


class TestTheAdvisoryLogsOncePerEpisode:

    def test_first_tick_warns_and_names_the_acting_stop(self, caplog):
        s = _brandon(enabled=True, pct=0.40)
        with caplog.at_level("WARNING"):
            s._log_breach_advisory(_ENTRY, "call", _DEC, datetime(2026, 10, 7, 10, 0, 0))
        msgs = [r.getMessage() for r in caplog.records]
        assert len(msgs) == 1
        assert "ADVISORY would-close" in msgs[0]
        assert "A2 %-of-width (40% of width) is the acting stop" in msgs[0]
        # the defect this replaces
        assert "credit+buffer stop is primary" not in msgs[0]

    def test_subsequent_ticks_are_SUPPRESSED(self, caplog):
        """The whole point: 825 lines in a day became one."""
        s = _brandon(enabled=True)
        t0 = datetime(2026, 10, 7, 10, 0, 0)
        with caplog.at_level("WARNING"):
            for i in range(60):            # ~11 minutes of ~11s ticks
                s._log_breach_advisory(_ENTRY, "call", _DEC, t0 + timedelta(seconds=11 * i))
        warns = [r for r in caplog.records if r.levelname == "WARNING"]
        # 60 ticks spanning 649s -> 1 first line + 2 roll-ups at 300s cadence
        assert len(warns) == 3, [r.getMessage() for r in warns]
        assert s._brandon_advisory_log[(3, "call")]["ticks"] == 60

    def test_the_rollup_reports_the_suppressed_tick_count(self, caplog):
        s = _brandon(enabled=True)
        t0 = datetime(2026, 10, 7, 10, 0, 0)
        with caplog.at_level("WARNING"):
            s._log_breach_advisory(_ENTRY, "call", _DEC, t0)
            s._log_breach_advisory(_ENTRY, "call", _DEC, t0 + timedelta(seconds=10))
            s._log_breach_advisory(_ENTRY, "call", _DEC,
                                   t0 + timedelta(seconds=s.ADVISORY_REPEAT_SECONDS + 1))
        msgs = [r.getMessage() for r in caplog.records]
        assert len(msgs) == 2
        assert "still would-close after 3 ticks" in msgs[1]

    def test_each_side_is_tracked_SEPARATELY(self, caplog):
        s = _brandon(enabled=True)
        t0 = datetime(2026, 10, 7, 10, 0, 0)
        with caplog.at_level("WARNING"):
            s._log_breach_advisory(_ENTRY, "call", _DEC, t0)
            s._log_breach_advisory(_ENTRY, "put", _DEC, t0)
        assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 2

    def test_a_NEW_episode_warns_again_after_the_breach_clears(self, caplog):
        """Suppression must not be permanent — a second breach is news."""
        s = _brandon(enabled=True)
        t0 = datetime(2026, 10, 7, 10, 0, 0)
        with caplog.at_level("WARNING"):
            s._log_breach_advisory(_ENTRY, "call", _DEC, t0)
            s._log_breach_advisory(_ENTRY, "call", _DEC, t0 + timedelta(seconds=11))
            s._close_breach_advisory(_ENTRY, "call")          # breach recovered
            s._log_breach_advisory(_ENTRY, "call", _DEC, t0 + timedelta(seconds=60))
        warns = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(warns) == 2
        assert all("ADVISORY would-close" in m for m in warns)

    def test_closing_an_episode_clears_the_record(self):
        s = _brandon(enabled=True)
        s._log_breach_advisory(_ENTRY, "call", _DEC, datetime(2026, 10, 7, 10, 0, 0))
        assert (3, "call") in s._brandon_advisory_log
        s._close_breach_advisory(_ENTRY, "call")
        assert (3, "call") not in s._brandon_advisory_log

    def test_closing_an_episode_that_never_opened_is_a_noop(self):
        _brandon()._close_breach_advisory(_ENTRY, "put")      # must not raise

    def test_a_bad_clock_does_not_kill_monitoring(self, caplog):
        """A tz-naive/aware mix must not propagate out of a logging helper."""
        s = _brandon(enabled=True)
        s._log_breach_advisory(_ENTRY, "call", _DEC, datetime(2026, 10, 7, 10, 0, 0))
        s._log_breach_advisory(_ENTRY, "call", _DEC, "not-a-datetime")
        assert s._brandon_advisory_log[(3, "call")]["ticks"] == 2


# --------------------------------------------------------------------------
# item 2 — per-symbol sec_type
# --------------------------------------------------------------------------
class _Broker:
    def __init__(self):
        self.calls = []

    def qualify_contract(self, symbol, sec_type=None, exchange=None):
        self.calls.append({"symbol": symbol, "sec_type": sec_type, "exchange": exchange})
        return 12345

    def get_quote(self, conid):
        return {"mid": 660.25, "availability": "R"}


def _strat(underlying="SPX", sec_type=None, exchange="CBOE"):
    s = HydraStrategy.__new__(HydraStrategy)
    s.underlying_symbol = underlying
    s.volatility_symbol = "VIX"
    s.exchange = exchange
    if sec_type is not None:
        s.underlying_sec_type = sec_type
    s.broker = _Broker()
    return s


class TestTheUnderlyingSecTypeIsPerSymbol:

    def test_SPX_variants_are_unchanged_and_default_to_IND(self):
        """A/B/C/G/D carry no `underlying_sec_type`; behaviour must be identical."""
        s = _strat("SPX")                      # attribute deliberately absent
        price, avail = s._read_index_price("SPX")
        assert (price, avail) == (660.25, "R")
        assert s.broker.calls[0]["sec_type"] == "IND"

    def test_an_ETF_underlying_resolves_as_STK(self):
        s = _strat("SPY", sec_type="STK", exchange="SMART")
        s._read_index_price("SPY")
        assert s.broker.calls[0]["sec_type"] == "STK"
        assert s.broker.calls[0]["exchange"] == "SMART"

    def test_VIX_STAYS_IND_even_when_the_underlying_is_STK(self):
        """The reason this had to be per-symbol rather than one config key: VIX
        is a real cash index on every variant, including E and H. A blanket
        `sec_type` would have broken the VIX read while fixing the spot read."""
        s = _strat("SPY", sec_type="STK", exchange="SMART")
        s._read_index_price("VIX")
        assert s.broker.calls[0]["sec_type"] == "IND"

    def test_both_reads_in_sequence_get_their_own_type(self):
        s = _strat("SPY", sec_type="STK", exchange="SMART")
        s._read_index_price("SPY")
        s._read_index_price("VIX")
        assert [c["sec_type"] for c in s.broker.calls] == ["STK", "IND"]

    def test_an_unknown_symbol_is_treated_as_an_index(self):
        s = _strat("SPY", sec_type="STK")
        s._read_index_price("RUT")
        assert s.broker.calls[0]["sec_type"] == "IND"

    @pytest.mark.parametrize("raw,expected", [
        ("stk", "STK"), ("Stk", "STK"), ("IND", "IND"), ("", "IND"),
    ])
    def test_the_config_value_is_normalised(self, raw, expected):
        """Exercises the REAL loader (`_load_instrument_params`) on a concrete
        subclass — not an inline re-implementation of the parse, which would
        test nothing but itself."""
        s = HydraStrategy.__new__(HydraStrategy)
        s.strategy_config = {"underlying_symbol": "SPY", "underlying_sec_type": raw}
        s._load_instrument_params()
        assert s.underlying_sec_type == expected

    def test_a_config_WITHOUT_the_key_defaults_to_IND(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.strategy_config = {"underlying_symbol": "SPX"}
        s._load_instrument_params()
        assert s.underlying_sec_type == "IND"

    def test_the_loader_leaves_the_volatility_symbol_alone(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.strategy_config = {"underlying_symbol": "SPY", "underlying_sec_type": "STK"}
        s._load_instrument_params()
        assert s.volatility_symbol == "VIX"
        assert s.underlying_sec_type == "STK"

    def test_the_LEGACY_sec_type_key_is_honoured(self):
        """Variant E has carried `"sec_type": "STK"` since it was written and
        NOTHING read it — the key looked meaningful and was inert. Honouring it
        as an alias means E is correct without a config edit."""
        s = HydraStrategy.__new__(HydraStrategy)
        s.strategy_config = {"underlying_symbol": "SPY", "sec_type": "STK"}
        s._load_instrument_params()
        assert s.underlying_sec_type == "STK"

    def test_the_explicit_key_WINS_over_the_legacy_alias(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.strategy_config = {"underlying_symbol": "SPY",
                             "sec_type": "IND", "underlying_sec_type": "STK"}
        s._load_instrument_params()
        assert s.underlying_sec_type == "STK"

    def test_every_shipped_variant_config_resolves_sensibly(self):
        """The real configs, not a fixture: SPY variants STK, SPX variants IND."""
        import json
        expected = {"b": "IND", "c": "IND", "d": "IND", "e": "STK",
                    "f": "IND", "g": "IND", "h": "STK"}
        for vid, want in expected.items():
            cfg = json.load(open(ROOT / ("bots/hydra/config/config_variant_%s.json" % vid)))
            s = HydraStrategy.__new__(HydraStrategy)
            s.strategy_config = cfg.get("strategy", {})
            s._load_instrument_params()
            assert s.underlying_sec_type == want, (
                "%s: underlying %s resolved %s, expected %s"
                % (vid, s.underlying_symbol, s.underlying_sec_type, want))
            # the invariant that actually matters
            if s.underlying_symbol == "SPY":
                assert s.underlying_sec_type == "STK", "%s trades SPY as an index" % vid
