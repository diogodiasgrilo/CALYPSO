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
from shared.ib_client import DEFAULT_ORDER_ANSWERS
from ibind import QuestionType


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


# ─── 2026-06-03: confirm IBKR Size-Limit precaution (live order path) ──────
class TestOrderSizeLimitConfirmed:
    """The order-reply defaults must CONFIRM IBKR's 'size exceeds the Size Limit
    of N' precaution. The bot trades contracts_per_entry (10c) which exceeds
    IBKR's default account Size Limit (5); answering this prompt False refused
    the bot's own orders ('A question was not given a positive reply') and
    blocked ALL live entries. Order size is bounded by config + the ORDER-004
    BP gate, so confirming the precaution is correct."""

    def test_size_limit_prompts_confirmed(self):
        assert DEFAULT_ORDER_ANSWERS.get(QuestionType.ORDER_SIZE_LIMIT) is True
        assert DEFAULT_ORDER_ANSWERS.get(QuestionType.SIZE_MODIFICATION_LIMIT) is True

    def test_safety_prompts_still_refused(self):
        # we did NOT loosen the genuinely-protective refusals
        assert DEFAULT_ORDER_ANSWERS.get(QuestionType.MISSING_MARKET_DATA) is False
        assert DEFAULT_ORDER_ANSWERS.get(QuestionType.CLOSE_POSITION) is False


# ─── 2026-06-03: phantom daily-summary guard (down-at-open → stale write) ──
class TestPhantomSummaryGuard:
    """A bot down at the 9:30 open that starts mid-session never fires the
    new-day reset, so it can reach the close still holding the PRIOR day's
    daily_state (entries + session-open) with no live price captured for
    today. Writing the summary then records yesterday's entries/P&L under
    today's date with spx_close=0 — the 06-03 phantom row. The guard
    (_daily_summary_is_stale) must veto that write."""

    _stale = staticmethod(HydraStrategy._daily_summary_is_stale)

    def test_prior_day_state_is_stale(self):
        # in-memory state belongs to a different calendar day → veto
        assert self._stale("2026-06-02", "2026-06-03", 7556.0) is True

    def test_zero_close_is_stale(self):
        # no live SPX captured today (the phantom had spx_close=0) → veto
        assert self._stale("2026-06-03", "2026-06-03", 0) is True
        assert self._stale("2026-06-03", "2026-06-03", None) is True

    def test_exact_phantom_signature_is_stale(self):
        # the literal 06-03 phantom: yesterday's date + 0 close
        assert self._stale("2026-06-02", "2026-06-03", 0.0) is True

    def test_healthy_today_summary_writes(self):
        # normal close: today's state + a real live price → allowed
        assert self._stale("2026-06-03", "2026-06-03", 7556.82) is False

    def test_empty_state_date_allowed(self):
        # fresh recovery (date not yet set) is handled by _handle_idle, not vetoed
        assert self._stale("", "2026-06-03", 7556.82) is False


# ─── 2026-06-04: one-sided LIVE entry execution (the 0.0-strike leg failure) ──
class TestOneSidedLiveEntry:
    """The LIVE _execute_entry placed all 4 legs unconditionally, so any
    one-sided entry (put-only / call-only / Brandon GEX-skip / E6 conditional)
    tried to place the skipped side's leg at strike 0.0 and aborted at leg 1
    ('Placing Long Call at 0.0'). The dry-run _simulate_entry gates on `if
    strike`, so A/B (dry-run) were fine — C, the only LIVE variant, was the only
    one affected. _execute_entry must place only the ACTIVE side(s)."""

    def _strategy(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = False
        placed: list = []

        def _place(strike, put_call, buy_sell, expiry, external_ref,
                   emergency_mode=False, paired_long_fill_per_share=None, **kwargs):
            placed.append((put_call, strike))
            return {"position_id": f"P_{external_ref}", "uic": 999,
                    "debit": 1.0, "credit": 2.0, "fill_price": 1.5, "mid_at_fill": 1.5}

        s._place_option_order = _place
        s._get_todays_expiry = lambda: "2026-06-04"
        s._register_position = lambda entry, leg: None
        s._verify_entry_fill_prices = lambda entry: None
        return s, placed

    def _entry(self, **kw):
        e = base_mod.IronCondorEntry(entry_number=2)
        e.strategy_id = "C2"
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def test_put_only_skips_call_legs(self):
        # Brandon GEX-skip: call strikes zeroed + call_side_skipped set → put-only
        s, placed = self._strategy()
        e = self._entry(short_call_strike=0.0, long_call_strike=0.0,
                        short_put_strike=7540, long_put_strike=7535,
                        call_side_skipped=True)
        assert s._execute_entry(e) is True
        # only the put spread was placed — NO "Call 0.0" leg
        assert all(pc == "Put" for pc, _ in placed)
        assert len(placed) == 2
        assert e.call_spread_credit == 0.0

    def test_call_only_skips_put_legs(self):
        s, placed = self._strategy()
        e = self._entry(short_call_strike=7600, long_call_strike=7605,
                        short_put_strike=0.0, long_put_strike=0.0,
                        put_side_skipped=True)
        assert s._execute_entry(e) is True
        assert all(pc == "Call" for pc, _ in placed)
        assert len(placed) == 2

    def test_full_ic_places_all_four(self):
        s, placed = self._strategy()
        e = self._entry(short_call_strike=7600, long_call_strike=7605,
                        short_put_strike=7540, long_put_strike=7535)
        assert s._execute_entry(e) is True
        assert len(placed) == 4
        assert sum(1 for pc, _ in placed if pc == "Call") == 2
        assert sum(1 for pc, _ in placed if pc == "Put") == 2

    def test_no_active_side_aborts_without_orders(self):
        # both sides zeroed → refuse to place anything (no 0.0 leg)
        s, placed = self._strategy()
        e = self._entry(short_call_strike=0.0, long_call_strike=0.0,
                        short_put_strike=0.0, long_put_strike=0.0)
        assert s._execute_entry(e) is False
        assert placed == []

    def test_no_leg_is_ever_placed_at_zero_strike(self):
        # the exact 06-04 signature: never a strike-0 order on a one-sided entry
        s, placed = self._strategy()
        e = self._entry(short_call_strike=0.0, long_call_strike=0.0,
                        short_put_strike=7540, long_put_strike=7535,
                        call_side_skipped=True)
        s._execute_entry(e)
        assert all(strike != 0.0 for _, strike in placed)


# ─── 2026-06-04: live CLOSE paths gated on pos_id (None on IBKR) → 0 legs ──────
class TestLiveCloseGating:
    """_close_entry_early (Brandon TP + GEX-breach close) and
    _count_active_position_legs gated on *_position_id, which is ALWAYS None on
    IBKR — so they closed/counted 0 legs on every LIVE position and orphaned it
    (the 06-04 'closed 0 legs' bug). They must gate on the conid (*_uic). The
    dry-run path (truthy DRY_* position_ids) must keep working."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = False
        s.commission_per_leg = 1.15
        s.daily_state = base_mod.MEICDailyState()
        s._read_option_quote = lambda uic: {"bid": 0.5}  # >0 so Fix #81 closes the long
        s._record_stop_to_db = lambda *a, **k: None
        closed = []

        def _close(pos_id, leg_name, uic=None, entry_number=None, contracts=None):
            closed.append((leg_name, pos_id, uic))
            return (True, 0.10, "oid")
        s._close_position_with_retry = _close
        return s, closed

    def _entry(self, **kw):
        e = base_mod.IronCondorEntry(entry_number=1)
        e.contracts = 10
        e.close_commission = 0.0
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def test_close_entry_early_live_put_only_closes_put_legs(self):
        # live put-only: put conids set, ALL position_ids None (IBKR)
        s, closed = self._strat()
        e = self._entry(call_side_skipped=True, put_only=True,
                        short_call_strike=0.0, long_call_strike=0.0,
                        short_put_strike=7550, long_put_strike=7545,
                        short_put_uic=111, long_put_uic=222,
                        short_call_uic=None, long_call_uic=None,
                        short_put_position_id=None, long_put_position_id=None,
                        put_spread_credit=400.0)
        legs_closed, legs_failed, _ = s._close_entry_early(e)
        assert legs_closed >= 1          # was 0 before the fix → orphaned position
        assert legs_failed == 0
        assert all(ln.endswith("put") for ln, _, _ in closed)   # never a call-side close
        assert {u for _, _, u in closed} == {111, 222}          # closed BY CONID
        assert e.put_side_expired is True                        # set iff legs closed

    def test_close_entry_early_live_full_ic_closes_all_four(self):
        s, closed = self._strat()
        e = self._entry(short_call_strike=7600, long_call_strike=7605,
                        short_put_strike=7550, long_put_strike=7545,
                        short_call_uic=1, long_call_uic=2, short_put_uic=3, long_put_uic=4,
                        short_call_position_id=None, long_call_position_id=None,
                        short_put_position_id=None, long_put_position_id=None,
                        call_spread_credit=200.0, put_spread_credit=200.0)
        legs_closed, _, _ = s._close_entry_early(e)
        assert legs_closed == 4          # full IC: both sides closed live

    def test_count_active_position_legs_counts_on_uic(self):
        s, _ = self._strat()
        e = self._entry(put_only=True, call_side_skipped=True,
                        short_put_strike=7550, long_put_strike=7545,
                        short_put_uic=111, long_put_uic=222,
                        short_put_position_id=None, long_put_position_id=None,
                        is_complete=True)
        s.daily_state.entries = [e]
        assert s._count_active_position_legs() == 2   # was 0 before the fix


# ─── 2026-06-04: recovery dropped a live open entry from active_entries ────────
class TestRecoveryActiveEntries:
    """active_entries.has_any_position checked *_position_id (None on IBKR), so a
    recovered LIVE open entry was dropped from monitoring after a mid-day restart.
    Must be conid-aware."""

    def test_recovered_live_open_entry_is_active(self):
        st = base_mod.MEICDailyState()
        e = base_mod.IronCondorEntry(entry_number=1)
        e.put_only = True
        e.call_side_skipped = True
        e.short_put_strike = 7550
        e.long_put_strike = 7545
        e.short_put_uic = 111            # live conid present
        e.long_put_uic = 222
        e.short_put_position_id = None   # IBKR: always None
        e.long_put_position_id = None
        e.is_complete = False            # worst case: not restored on recovery
        st.entries = [e]
        assert len(st.active_entries) == 1   # was 0 before the fix → unmonitored

    def test_fully_done_entry_not_active(self):
        st = base_mod.MEICDailyState()
        e = base_mod.IronCondorEntry(entry_number=1)
        e.put_only = True
        e.call_side_skipped = True
        e.put_side_stopped = True        # put side done → not active
        e.short_put_uic = 111
        e.short_put_position_id = None
        st.entries = [e]
        assert len(st.active_entries) == 0


# ─── 2026-06-04: settlement re-books a spuriously-stopped (0-leg-TP) side ──────
class TestSettlementRebook:
    """A Brandon TP/BREACH that closed 0 legs spuriously set *_side_stopped +
    close_reason='TP' WITHOUT booking P&L; the position then expires at
    settlement. _process_expired_credits must re-book that side (else a full
    credit is silently dropped). A GENUINE HYDRA stop (booked at stop time,
    close_reason != TP/BREACH) must NOT be re-booked."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = True            # skips the IBKR settlement-verify tail
        s.broker = None
        s.daily_state = base_mod.MEICDailyState()
        s._side_positions_gone = lambda entry, side, op: True   # expired/cleared
        s._settlement_booked_pnl = lambda entry, side, lvl: (400.0, True)
        s._settlement_spx_level = lambda: None
        s._read_open_positions = lambda *a, **k: []
        return s

    def _put_entry(self, **kw):
        e = base_mod.IronCondorEntry(entry_number=1)
        # call_only/put_only are declared on the live HYDRA entry subclass; set
        # them on the base test entry (_process_expired_credits reads them directly).
        e.call_only = False
        e.put_only = True
        e.call_side_skipped = True
        e.short_put_strike = 7550
        e.long_put_strike = 7545
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def test_spurious_tp_stop_is_rebooked(self):
        s = self._strat()
        e = self._put_entry(put_side_stopped=True, close_reason="TP")  # 0-leg-TP artifact
        s.daily_state.entries = [e]
        booked = s._process_expired_credits()
        assert booked == 400.0          # was 0 (skipped) before the fix
        assert e.put_side_expired is True

    def test_genuine_hydra_stop_not_rebooked(self):
        s = self._strat()
        e = self._put_entry(put_side_stopped=True, close_reason="STOP")  # genuine, already booked
        s.daily_state.entries = [e]
        booked = s._process_expired_credits()
        assert booked == 0.0            # not double-booked
        assert e.put_side_expired is False


class TestDailySummarySheetIdempotent:
    """The Daily Summary worksheet write must be IDEMPOTENT BY DATE. The bot can
    legitimately (re)write a day's summary more than once — a mid-day restart, a
    multi-pass settlement, or a manual correction — and the old blind append_row
    left a SECOND row for the day (the duplicate Jun-2 row that motivated this).
    log_daily_summary now removes any existing row(s) for the date, then appends,
    so there is exactly one row per day (and it self-heals pre-existing dups).
    Mirrors the DB path where daily_summaries.date is the PRIMARY KEY."""

    def _logger(self, existing_col_a):
        lg = GoogleSheetsLogger.__new__(GoogleSheetsLogger)
        lg.enabled = True
        lg.strategy_type = "hydra"
        lg.SHEETS_API_TIMEOUT = 10
        ws = MagicMock()
        ws.col_values.return_value = list(existing_col_a)  # column A incl. header
        lg.worksheets = {"Daily Summary": ws}
        return lg, ws

    def test_existing_date_is_replaced_not_duplicated(self):
        # Sheet already has a 2026-06-04 row (the exact re-write scenario).
        lg, ws = self._logger(["Date", "2026-06-03", "2026-06-04"])
        ok = lg.log_daily_summary({"date": "2026-06-04", "daily_pnl": 377.0})
        assert ok is True
        ws.delete_rows.assert_called_once_with(3)   # the stale 2026-06-04 row
        assert ws.append_row.call_count == 1        # then the fresh one
        assert ws.append_row.call_args[0][0][0] == "2026-06-04"

    def test_new_date_simply_appends(self):
        lg, ws = self._logger(["Date", "2026-06-03"])
        ok = lg.log_daily_summary({"date": "2026-06-04", "daily_pnl": 377.0})
        assert ok is True
        ws.delete_rows.assert_not_called()          # nothing to remove
        assert ws.append_row.call_count == 1

    def test_self_heals_preexisting_duplicates(self):
        # Two stale 2026-06-04 rows already present → both removed, one appended.
        lg, ws = self._logger(["Date", "2026-06-04", "2026-06-03", "2026-06-04"])
        ok = lg.log_daily_summary({"date": "2026-06-04", "daily_pnl": 377.0})
        assert ok is True
        # deleted bottom-up so indices stay valid: row 4 then row 2
        assert [c.args[0] for c in ws.delete_rows.call_args_list] == [4, 2]
        assert ws.append_row.call_count == 1

    def test_read_timeout_falls_back_to_plain_append(self):
        # col_values times out → wrapper returns None → skip dedup, still append.
        lg, ws = self._logger(["Date", "2026-06-04"])
        ws.col_values.side_effect = None
        with patch.object(lg, "_sheets_call_with_timeout") as wrapped:
            # col_values → None (timeout); append_row → truthy result
            wrapped.side_effect = [None, {"updates": {}}]
            ok = lg.log_daily_summary({"date": "2026-06-04", "daily_pnl": 377.0})
        assert ok is True
        # exactly two wrapper calls: the read, then the append (no delete attempted)
        assert wrapped.call_count == 2
