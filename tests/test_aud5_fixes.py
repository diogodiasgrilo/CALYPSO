"""Regression tests for the AUD5 post-cutover go-live audit fixes.

Covers the confirmed code findings in docs/migration/AUD5_FINDINGS.md:

- GL-2 : ORDER-004 buying-power gate must NOT raise (and must not silently
         fail open) when IBKR leaves margin utilization unset (margin_pct=None).
- C-1a : index current_price/current_vix only adopt a quote whose 6509 flag is
         real-time/usable (block Z/Y/N), consistent with MarketData.update_spx.
- C-1b : market-halt detection treats Y/N (frozen-delayed / not-subscribed)
         like Z (frozen) — all "not real-time" → halt.
- C-1c : _option_quote_is_realtime exists + batch surfaces the availability flag.
- C-2  : an empty position snapshot shrinks the Positions sheet to header-only
         instead of leaving the prior snapshot's stale rows.
- C-3  : HydraStrategy.log_performance_metrics passes `period` through so the
         post-settlement write can use a throttle-exempt label.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy
import bots.hydra.base_strategy as base_mod
from shared.logger_service import GoogleSheetsLogger


def _bare() -> HydraStrategy:
    """A HydraStrategy with no __init__ — set only the attrs a method needs."""
    return HydraStrategy.__new__(HydraStrategy)


class _ZeroDict(dict):
    """dict that yields 0 for any missing key (keeps metric math finite)."""

    def __missing__(self, key):  # noqa: D401
        return 0


# ─── GL-2: ORDER-004 margin_pct=None must not crash the BP gate ───────────
class TestOrder004MarginNone:
    """base_strategy._check_buying_power on the IBKR path (margin_pct=None)."""

    def _strategy(self, available, dry_run=False):
        s = _bare()
        # IBKR balance: only MarginAvailableForTrading; NO MarginUtilizationPct.
        s._read_account_balance = MagicMock(
            return_value={"MarginAvailableForTrading": available}
        )
        s.min_buying_power_per_ic = 5000
        s.contracts_per_entry = 1
        s.dry_run = dry_run
        s._last_margin_snapshot = {}
        return s

    def test_bp_ok_path_no_crash_when_margin_pct_none(self):
        # available >> required → success return (the line that crashed pre-fix).
        ok, msg = self._strategy(available=100000)._check_buying_power()
        assert ok is True
        assert "BP OK" in msg          # the real success msg, NOT "skipped (error"
        assert "n/a" in msg            # margin rendered as n/a, not a TypeError

    def test_insufficient_bp_path_no_crash_when_margin_pct_none(self):
        ok, msg = self._strategy(available=10, dry_run=False)._check_buying_power()
        assert ok is False
        assert "Insufficient BP" in msg
        assert "n/a" in msg

    def test_explicit_margin_pct_still_formatted(self):
        s = _bare()
        s._read_account_balance = MagicMock(return_value={
            "MarginAvailableForTrading": 100000,
            "MarginUtilizationPct": 12.5,
        })
        s.min_buying_power_per_ic = 5000
        s.contracts_per_entry = 1
        s.dry_run = False
        s._last_margin_snapshot = {}
        ok, msg = s._check_buying_power()
        assert ok is True
        assert "12.5%" in msg


# ─── C-1a: index current_price/current_vix availability gate ──────────────
class TestIndexAvailabilityGate:
    def _strategy(self, avail):
        s = _bare()
        s.market_data = MagicMock()
        s._read_index_price = MagicMock(
            side_effect=lambda sym: (6000.0 if sym == "SPX" else 15.0, avail)
        )
        s.current_price = -1.0
        s.current_vix = -1.0
        return s

    def test_realtime_updates_current(self):
        s = self._strategy("R")
        s._update_market_data()
        assert s.current_price == 6000.0
        assert s.current_vix == 15.0

    def test_delayed_still_updates_current(self):
        # 'D' is delayed-but-usable (update_spx logs and proceeds) — adopted.
        s = self._strategy("D")
        s._update_market_data()
        assert s.current_price == 6000.0
        assert s.current_vix == 15.0

    @pytest.mark.parametrize("avail", ["Z", "Y", "N", "ZP", "Yp", "Np"])
    def test_non_realtime_does_not_update_current(self, avail):
        s = self._strategy(avail)
        s._update_market_data()
        assert s.current_price == -1.0   # stale/unentitled quote NOT adopted
        assert s.current_vix == -1.0


# ─── C-1b: market-halt detection on Y/N ───────────────────────────────────
class TestMarketHaltAvailability:
    def _strategy(self, price, avail):
        s = _bare()
        s._read_index_price = MagicMock(return_value=(price, avail))
        return s

    def test_realtime_not_halted(self):
        s = self._strategy(6000.0, "R")
        with patch.object(base_mod, "is_market_open", return_value=True):
            halted, reason = s._check_market_halt()
        assert halted is False

    @pytest.mark.parametrize("avail", ["Z", "Y", "N", "Yp", "Np"])
    def test_non_realtime_treated_as_halt(self, avail):
        s = self._strategy(6000.0, avail)
        with patch.object(base_mod, "is_market_open", return_value=True):
            halted, reason = s._check_market_halt()
        assert halted is True
        assert "not real-time" in reason


# ─── C-1c: _option_quote_is_realtime + batch availability ─────────────────
class TestOptionQuoteIsRealtime:
    def test_semantics(self):
        s = _bare()
        assert s._option_quote_is_realtime(None) is False        # no quote
        assert s._option_quote_is_realtime({}) is False          # empty quote
        # quote present but no availability flag → pass (IBKR batch may omit it)
        assert s._option_quote_is_realtime({"bid": 1.0}) is True
        assert s._option_quote_is_realtime({"availability": None}) is True
        assert s._option_quote_is_realtime({"availability": "R"}) is True
        assert s._option_quote_is_realtime({"availability": "RpB"}) is True
        for bad in ("D", "Z", "Y", "N", "DP", "Zp", "Yp", "Np"):
            assert s._option_quote_is_realtime({"availability": bad}) is False, bad

    def test_batch_surfaces_availability(self):
        s = _bare()
        broker = MagicMock()
        broker.get_quotes_batch.return_value = [
            {"conid": 1, "bid": 1.0, "availability": "R"},
            {"conid": 2, "bid": 2.0, "availability": "Z"},
            {"conid": 3, "bid": 3.0},   # no flag → None
        ]
        s.broker = broker
        out = s._read_option_quotes_batch([1, 2, 3])
        assert out[1]["availability"] == "R"
        assert out[2]["availability"] == "Z"
        assert out[3]["availability"] is None
        # and the gate agrees:
        assert s._option_quote_is_realtime(out[1]) is True
        assert s._option_quote_is_realtime(out[2]) is False
        assert s._option_quote_is_realtime(out[3]) is True


# ─── C-2: empty Positions snapshot shrinks to header-only ─────────────────
class TestEmptyPositionsShrink:
    def _logger(self):
        lg = GoogleSheetsLogger.__new__(GoogleSheetsLogger)
        lg.enabled = True
        lg.strategy_type = "hydra"
        lg._last_pos_snapshot_at = 0.0
        lg._pos_snapshot_min_interval = 0.0
        lg._sheets_call_with_timeout = MagicMock()
        ws = MagicMock()
        ws.row_count = 50
        lg.worksheets = {"Positions": ws}
        return lg, ws

    def test_empty_positions_resizes_to_header_only_and_skips_update(self):
        lg, ws = self._logger()
        lg.log_position_snapshot([])
        calls = lg._sheets_call_with_timeout.call_args_list
        # resize(worksheet.resize, 1, 17) — shrink to header
        assert any(c.args[0] is ws.resize and c.args[1] == 1 for c in calls), calls
        # update is NOT called when there are no data rows
        assert not any(c.args[0] is ws.update for c in calls), calls

    def test_nonempty_positions_resizes_and_updates(self):
        lg, ws = self._logger()
        pos = [{"entry_number": 1, "leg_type": "call", "strike": 6000,
                "expiry": "2026-06-03", "side": "short", "status": "ACTIVE"}]
        lg.log_position_snapshot(pos)
        calls = lg._sheets_call_with_timeout.call_args_list
        assert any(c.args[0] is ws.resize and c.args[1] == 2 for c in calls), calls
        assert any(c.args[0] is ws.update for c in calls), calls


# ─── C-3: settlement metrics period passthrough ───────────────────────────
class TestPerformanceMetricsPeriodPassthrough:
    def _strategy(self):
        s = _bare()
        s.trade_logger = MagicMock()
        s.get_dashboard_metrics = MagicMock(return_value=_ZeroDict())
        s.cumulative_metrics = {}
        ds = MagicMock()
        ds.total_commission = 0
        ds.entries = []
        s.daily_state = ds
        s._calculate_capital_deployed = MagicMock(return_value=0)
        s._calculate_max_loss_with_stops = MagicMock(return_value=0)
        s._calculate_max_loss_catastrophic = MagicMock(return_value=0)
        s._early_close_triggered = False
        s._early_close_time = None
        s.broker = MagicMock()
        return s

    def test_default_period_is_intraday(self):
        s = self._strategy()
        s.log_performance_metrics()
        _, kwargs = s.trade_logger.log_performance_metrics.call_args
        assert kwargs["period"] == "Intraday"

    def test_settlement_period_passed_through(self):
        s = self._strategy()
        s.log_performance_metrics(period="End of Day")
        _, kwargs = s.trade_logger.log_performance_metrics.call_args
        assert kwargs["period"] == "End of Day"


# ─── POS-004: settlement net-zero-merge classification ────────────────────
class TestPOS004SettlementMergeClassification:
    """AUD5/POS-004: a conid whose EXPECTED net is 0 (opposing legs merged)
    must NOT be classified 'settled' off a net-0 broker reading — doing so
    cleared a genuinely-open 0DTE short."""

    _classify = staticmethod(HydraStrategy._classify_settlement_conids)

    def test_normal_single_leg_settles(self):
        settled, still_open, merged = self._classify({100: -1}, {100: 0})
        assert settled == {100} and still_open == set() and merged == set()

    def test_open_leg_not_settled(self):
        settled, still_open, merged = self._classify({100: -1}, {100: -1})
        assert settled == set() and still_open == {100} and merged == set()

    def test_same_sign_merge_settles_cleanly(self):
        # two shorts merged on one conid (-2) → still a non-zero expectation
        settled, still_open, merged = self._classify({100: -2}, {100: 0})
        assert settled == {100} and merged == set()

    def test_opposing_merge_net_zero_is_ambiguous_not_settled(self):
        # THE BUG: short -1 (E#2) + long +1 (E#3) on the SAME conid → expected 0;
        # both still open → broker also 0. Must land in merged_net_zero, never
        # in settled (which would clear the live short), nor block forever.
        settled, still_open, merged = self._classify({100: 0}, {100: 0})
        assert 100 not in settled
        assert 100 not in still_open
        assert merged == {100}

    def test_mixed_set(self):
        expected = {10: -1, 20: 0, 30: -1}   # 10 expired, 20 net-zero merge, 30 open
        actual = {10: 0, 20: 0, 30: -1}
        settled, still_open, merged = self._classify(expected, actual)
        assert settled == {10}
        assert still_open == {30}
        assert merged == {20}


# ─── 2026-06-03: qty=0 zombie-position filter (STATE-004 false-halt) ───────
class TestZombiePositionFilter:
    """_read_open_positions must drop qty=0 'zombie' rows — IBKR paper leaves an
    expired 0DTE in the position list with quantity 0 for ~a day, which
    false-triggered the STATE-004 overnight-0DTE halt and froze the bot for the
    whole day. Real (non-zero) positions are still returned so genuine overnight
    positions still halt."""

    _ZOMBIE = {"conid": 886490515, "position": 0, "assetClass": "OPT",
               "ticker": "SPXW", "putOrCall": "C", "strike": 7615,
               "lastTradingDay": "20260602"}
    _REAL = {"conid": 111, "position": -10, "assetClass": "OPT",
             "ticker": "SPXW", "putOrCall": "P", "strike": 7570,
             "lastTradingDay": "20260603"}
    _STK = {"conid": 222, "position": 100, "assetClass": "STK", "ticker": "SPY"}

    def _strategy(self, rows):
        s = _bare()
        broker = MagicMock()
        broker.get_positions.return_value = rows
        s.broker = broker
        return s

    def test_zombie_filtered_real_kept(self):
        out = self._strategy([self._ZOMBIE, self._REAL, self._STK])._read_open_positions()
        assert [p["instrument_id"] for p in out] == [111]
        assert out[0]["quantity"] == -10

    def test_only_zombie_returns_empty(self):
        # the actual 06-03 incident: a single qty-0 expired 0DTE row → no halt
        assert self._strategy([self._ZOMBIE])._read_open_positions() == []

    def test_real_overnight_position_still_detected(self):
        # safety net intact: a genuine non-zero overnight position is still returned
        out = self._strategy([self._REAL])._read_open_positions()
        assert len(out) == 1 and out[0]["instrument_id"] == 111
