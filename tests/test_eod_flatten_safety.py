"""MKT-047 EOD safety flatten + near-expiry MARKET-order escalation.

Context (2026-06-17, FOMC day): variant C's E#1 put breached its credit+buffer
stop at 15:57, but EMERGENCY-001's marketable-limit closes "did not fill" and the
0DTE options then expired ("Order is already expired" ×5) → full put-spread max
loss. Two fixes, both exercised here:

  1. ``_check_eod_flatten`` / ``_execute_eod_flatten`` (strategy.py): force-close
     every open 0DTE short at a cutoff (15:50, earlier on FOMC days) BEFORE the
     un-closable final-minutes window. Idempotent per day, disabled-able.
  2. ``_place_marketable_close`` (base_strategy.py): inside N minutes of the actual
     close, escalate straight to a true MARKET order instead of a crossing limit.
"""

import datetime
from datetime import time as dtime
from unittest.mock import MagicMock, patch

from bots.hydra.strategy import HydraStrategy
import bots.hydra.strategy as strat_mod
import bots.hydra.base_strategy as base_mod


def _et(h, m):
    """A bare ET-ish datetime — the flatten gate only reads hour/minute/date."""
    return datetime.datetime(2026, 6, 17, h, m, 0)


# ─────────────────────────── _check_eod_flatten gating ───────────────────────
class TestEodFlattenGate:
    def _strat(self, *, enabled=True, done=False, time_et="15:50",
               fomc_et="15:40", with_position=True):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_enabled = enabled
        s._eod_flatten_done = done
        s.eod_flatten_time_et = time_et
        s.eod_flatten_time_fomc_et = fomc_et
        s.daily_state = base_mod.MEICDailyState()
        if with_position:
            e = base_mod.IronCondorEntry(entry_number=1)
            e.contracts = 7
            e.put_only = True
            e.call_side_skipped = True
            e.short_put_uic = 111
            e.long_put_uic = 222
            e.short_put_position_id = None
            e.long_put_position_id = None
            e.is_complete = False
            s.daily_state.entries = [e]
        # Mock the executor so the gate tests assert only the trigger decision.
        s._execute_eod_flatten = MagicMock(return_value="FLATTENED")
        return s

    def _run(self, s, now, *, fomc=False):
        with patch.object(strat_mod, "get_us_market_time", return_value=now), \
             patch("shared.event_calendar.is_fomc_announcement_day", return_value=fomc):
            return s._check_eod_flatten()

    def test_before_cutoff_no_flatten(self):
        s = self._strat()
        assert self._run(s, _et(15, 45)) is None
        s._execute_eod_flatten.assert_not_called()

    def test_at_cutoff_flattens(self):
        s = self._strat()
        assert self._run(s, _et(15, 50)) == "FLATTENED"
        s._execute_eod_flatten.assert_called_once()

    def test_after_cutoff_flattens(self):
        s = self._strat()
        assert self._run(s, _et(15, 58)) == "FLATTENED"

    def test_fomc_uses_earlier_cutoff(self):
        # 15:42 — past the 15:40 FOMC cutoff but BEFORE the 15:50 normal cutoff.
        s_fomc = self._strat()
        assert self._run(s_fomc, _et(15, 42), fomc=True) == "FLATTENED"
        s_normal = self._strat()
        assert self._run(s_normal, _et(15, 42), fomc=False) is None

    def test_idempotent_latch(self):
        s = self._strat(done=True)
        assert self._run(s, _et(15, 55)) is None
        s._execute_eod_flatten.assert_not_called()

    def test_disabled(self):
        s = self._strat(enabled=False)
        assert self._run(s, _et(15, 55)) is None

    def test_no_open_positions(self):
        s = self._strat(with_position=False)
        assert self._run(s, _et(15, 55)) is None
        s._execute_eod_flatten.assert_not_called()

    def test_calendar_strategy_never_flattens(self):
        # Multi-day calendars (D/E) set requires_protective_wings=False — MKT-047
        # (a 0DTE expiry-window flatten) must NEVER fire on them, even past the
        # cutoff with an open position. 2026-06-18: it wrongly closed D's calendar
        # at 15:50; on a transform day it would destroy the multi-day hold.
        s = self._strat()
        s.requires_protective_wings = False
        assert self._run(s, _et(15, 58)) is None
        s._execute_eod_flatten.assert_not_called()


# ─────────────────────────── _execute_eod_flatten ────────────────────────────
class TestEodFlattenExecute:
    def _strat(self, close_ret=(2, 0, [])):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_enabled = True
        s._eod_flatten_done = False
        s.contracts_per_entry = 7
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._close_entry_early = MagicMock(return_value=close_ret)
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = -3185.0
        s.daily_state.total_commission = 16.1
        e = base_mod.IronCondorEntry(entry_number=1)
        e.contracts = 7
        e.short_put_uic = 111
        e.short_put_position_id = None
        e.is_complete = False
        s.daily_state.entries = [e]
        return s

    def _run(self, s, now=None):
        now = now or _et(15, 50)
        with patch.object(strat_mod, "get_us_market_time", return_value=now):
            return s._execute_eod_flatten(cutoff_label="15:50", is_fomc=False)

    def test_closes_every_active_entry(self):
        s = self._strat()
        msg = self._run(s)
        s._close_entry_early.assert_called_once()       # the one open entry
        assert "MKT-047 EOD FLATTEN" in msg
        assert s._eod_flatten_done is True              # one-shot latch set
        s._save_state_to_disk.assert_called_once()

    def test_sends_alert(self):
        s = self._strat()
        self._run(s)
        s.alert_service.send_alert.assert_called_once()

    def test_failed_leg_raises_alert_priority(self):
        # A leg that fails to close → HIGH-priority alert (operator visibility).
        s = self._strat(close_ret=(1, 1, []))
        self._run(s)
        _, kwargs = s.alert_service.send_alert.call_args
        assert kwargs["priority"] == strat_mod.AlertPriority.HIGH

    def test_clean_close_is_medium_priority(self):
        s = self._strat(close_ret=(2, 0, []))
        self._run(s)
        _, kwargs = s.alert_service.send_alert.call_args
        assert kwargs["priority"] == strat_mod.AlertPriority.MEDIUM

    def test_alert_failure_never_raises(self):
        s = self._strat()
        s.alert_service.send_alert.side_effect = RuntimeError("pubsub down")
        # Must still complete + return a summary (alert is best-effort).
        assert "MKT-047" in self._run(s)

    def test_no_alert_when_nothing_closed(self):
        # Nothing actually closed (all legs gone / every entry skipped) → NO email.
        # 2026-07-07: a "0 entr(ies) closed" flatten alert is pure noise.
        s = self._strat(close_ret=(0, 0, []))
        self._run(s)
        s.alert_service.send_alert.assert_not_called()

    def test_alert_has_no_misleading_pnl(self):
        # The action alert must NOT carry a day-P&L — pre-settlement it is just the
        # commission and reads as a phantom loss (variant C 07-07 "$-64.40" while the
        # real OTM-settled day was ~+$566). The real number is the DAILY_SUMMARY.
        s = self._strat(close_ret=(2, 0, []))
        self._run(s)
        _, kwargs = s.alert_service.send_alert.call_args
        assert "Net P&L" not in kwargs["message"]
        assert "Force-closed" in kwargs["message"]


# ───────────────── near-expiry MARKET escalation (_place_marketable_close) ─────
class TestNearExpiryMarketEscalation:
    def _strat(self, *, market_minutes=6.0):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_market_minutes = market_minutes
        s._read_option_quote = MagicMock(return_value={"bid": 1.0, "ask": 1.2})
        s._close_leg_order = MagicMock(return_value={"success": True, "filled": True})  # MARKET
        s._place_leg_order = MagicMock(return_value={"success": True, "filled": True})  # LMT
        return s

    def _call(self, s, now, *, close_time=dtime(16, 0)):
        with patch.object(base_mod, "get_us_market_time", return_value=now), \
             patch.object(base_mod, "get_market_close_time", return_value=close_time):
            return s._place_marketable_close(uic=1, side="BUY", quantity=7, attempt_num=1)

    def test_final_window_uses_market_order(self):
        # 15:56 → 4 min to the 16:00 close (≤ 6) → escalate to MARKET.
        s = self._strat(market_minutes=6.0)
        self._call(s, _et(15, 56))
        s._close_leg_order.assert_called_once()     # MARKET path
        s._place_leg_order.assert_not_called()      # NOT the crossing limit

    def test_outside_window_uses_marketable_limit(self):
        # 15:00 → 60 min to close (> 6) → normal crossing limit.
        s = self._strat(market_minutes=6.0)
        self._call(s, _et(15, 0))
        s._place_leg_order.assert_called_once()      # LMT path
        s._close_leg_order.assert_not_called()
        _, kwargs = s._place_leg_order.call_args
        assert kwargs["order_type"] == "LMT"

    def test_escalation_disabled_when_minutes_zero(self):
        s = self._strat(market_minutes=0.0)
        self._call(s, _et(15, 59))                   # 1 min to close, but disabled
        s._place_leg_order.assert_called_once()      # still the limit path
        s._close_leg_order.assert_not_called()

    def test_early_close_day_window_relative_to_1pm(self):
        # On a 1:00 PM early-close day, 12:56 is 4 min from THAT close → MARKET.
        s = self._strat(market_minutes=6.0)
        self._call(s, _et(12, 56), close_time=dtime(13, 0))
        s._close_leg_order.assert_called_once()
        s._place_leg_order.assert_not_called()

    def test_missing_attr_defaults_to_no_escalation(self):
        # A strategy without the knob (e.g. an unmigrated subclass) → no escalation.
        s = self._strat()
        del s.eod_flatten_market_minutes
        self._call(s, _et(15, 59))
        s._place_leg_order.assert_called_once()      # safe default: limit path


# ─────────────────────── MKT-047 OTM-skip (2026-06-25) ────────────────────────
class TestEodFlattenOtmSkip:
    """A comfortably-OTM 0DTE short cash-settles worthless — don't pay to close it
    in the un-closable window. Final-10-min reversal study: ~0% touch at >= 20pt."""

    def _strat(self, *, spot=7348.0, skip_pts=25.0):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_enabled = True
        s._eod_flatten_done = False
        s.contracts_per_entry = 7
        s.eod_flatten_skip_otm_pts = skip_pts
        s.current_price = spot
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._close_entry_early = MagicMock(return_value=(2, 0, []))
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        return s

    def _put_only(self, *, short_put=7295.0):
        e = base_mod.IronCondorEntry(entry_number=2)
        e.contracts = 7
        e.short_put_strike = short_put
        e.short_put_uic = 111
        e.is_complete = False
        e.call_side_skipped = True  # put-only
        return e

    def test_can_skip_comfortably_otm(self):
        s = self._strat(spot=7348.0)  # short put 7295 → 53pt OTM
        assert s._eod_flatten_can_skip(self._put_only()) is True

    def test_cannot_skip_near_money(self):
        s = self._strat(spot=7310.0)  # 15pt OTM (< 25)
        assert s._eod_flatten_can_skip(self._put_only()) is False

    def test_full_ic_needs_both_sides_safe(self):
        s = self._strat(spot=7400.0)
        e = base_mod.IronCondorEntry(entry_number=1)
        e.contracts = 7
        e.short_call_strike, e.short_put_strike = 7460.0, 7340.0  # both 60pt OTM
        e.short_call_uic, e.short_put_uic = 1, 2
        e.is_complete = False
        assert s._eod_flatten_can_skip(e) is True
        s.current_price = 7445.0  # call 7460 now 15pt OTM
        assert s._eod_flatten_can_skip(e) is False

    def test_disabled_when_pts_zero(self):
        assert self._strat(skip_pts=0.0)._eod_flatten_can_skip(self._put_only()) is False

    def test_no_spot_is_conservative_close(self):
        s = self._strat()
        s.current_price = None
        assert s._eod_flatten_can_skip(self._put_only()) is False

    def test_no_alive_short_does_not_skip(self):
        s = self._strat()
        e = self._put_only()
        e.put_side_expired = True  # no alive short left
        assert s._eod_flatten_can_skip(e) is False

    def test_flatten_skips_comfortably_otm_entry(self):
        s = self._strat(spot=7348.0)
        s.daily_state.entries = [self._put_only(short_put=7295.0)]
        with patch.object(strat_mod, "get_us_market_time", return_value=_et(15, 50)):
            s._execute_eod_flatten(cutoff_label="15:50", is_fomc=False)
        s._close_entry_early.assert_not_called()  # 53pt OTM → ride to worthless expiry

    def test_flatten_closes_near_money_entry(self):
        s = self._strat(spot=7305.0)  # short put 7295 → 10pt OTM
        s.daily_state.entries = [self._put_only(short_put=7295.0)]
        with patch.object(strat_mod, "get_us_market_time", return_value=_et(15, 50)):
            s._execute_eod_flatten(cutoff_label="15:50", is_fomc=False)
        s._close_entry_early.assert_called_once()  # within cushion → closed


# ──────────────── MKT-047 PER-SIDE OTM-skip (2026-07-06 C leak fix) ────────────
class TestEodFlattenPerSideSkip:
    """The per-ENTRY gate closed BOTH sides when EITHER was within the cushion. On
    2026-07-06 C's short calls were ~18pt OTM (< cushion) while the short puts were
    72-77pt OTM — the whole-entry gate flattened the safe puts too, buying them back
    for a needless debit + commission (~$215 of profit given back → $0.35 net). The
    per-SIDE gate flattens only the at-risk side and rides the safe side to expiry."""

    def _strat(self, *, spot=7542.0, skip_pts=20.0):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_enabled = True
        s._eod_flatten_done = False
        s.contracts_per_entry = 7
        s.eod_flatten_skip_otm_pts = skip_pts
        s.current_price = spot
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        return s

    def _full_ic(self):
        # Today's shape: short call 18pt OTM (spot 7542), short put 72pt OTM.
        e = base_mod.IronCondorEntry(entry_number=1)
        e.contracts = 7
        e.short_call_strike, e.long_call_strike = 7560.0, 7565.0
        e.short_put_strike, e.long_put_strike = 7470.0, 7465.0
        e.short_call_uic, e.long_call_uic = 1, 2
        e.short_put_uic, e.long_put_uic = 3, 4
        e.call_spread_credit, e.put_spread_credit = 70.0, 105.0
        e.is_complete = False
        return e

    # ── the per-side gate itself ──
    def test_side_skip_far_otm_put_only(self):
        s = self._strat(spot=7542.0)          # put 7470 → 72pt OTM
        assert s._eod_flatten_can_skip_side(self._full_ic(), "put") is True

    def test_side_no_skip_near_money_call(self):
        s = self._strat(spot=7542.0)          # call 7560 → 18pt OTM (< 20)
        assert s._eod_flatten_can_skip_side(self._full_ic(), "call") is False

    def test_side_skip_at_exact_threshold(self):
        s = self._strat(spot=7540.0)          # call 7560 → exactly 20pt OTM
        assert s._eod_flatten_can_skip_side(self._full_ic(), "call") is True

    def test_side_skip_disabled_when_pts_zero(self):
        s = self._strat(skip_pts=0.0)
        assert s._eod_flatten_can_skip_side(self._full_ic(), "put") is False

    def test_side_no_spot_is_conservative_close(self):
        s = self._strat()
        s.current_price = None
        assert s._eod_flatten_can_skip_side(self._full_ic(), "put") is False

    def test_side_not_alive_short_does_not_skip(self):
        s = self._strat(spot=7542.0)
        e = self._full_ic()
        e.put_side_expired = True             # already settled → not an alive short
        assert s._eod_flatten_can_skip_side(e, "put") is False

    def test_side_unreadable_strike_is_conservative_close(self):
        s = self._strat(spot=7542.0)
        e = self._full_ic()
        e.short_put_strike = None
        assert s._eod_flatten_can_skip_side(e, "put") is False

    # ── _execute_eod_flatten passes the right skip_sides ──
    def test_flatten_partial_skips_safe_put_side(self):
        s = self._strat(spot=7542.0)
        s._close_entry_early = MagicMock(return_value=(2, 0, []))
        s.daily_state.entries = [self._full_ic()]
        with patch.object(strat_mod, "get_us_market_time", return_value=_et(15, 50)):
            s._execute_eod_flatten(cutoff_label="15:50", is_fomc=False)
        # whole-entry gate fails (call 18pt < 20) but the safe put side is skipped
        s._close_entry_early.assert_called_once()
        _, kwargs = s._close_entry_early.call_args
        assert kwargs["skip_sides"] == {"put"}

    def test_flatten_no_partial_skip_when_both_near_money(self):
        s = self._strat(spot=7500.0)          # call 60pt, put 30pt — both >= 20
        # both >= 20 → whole-entry skip fires; nothing closed
        s._close_entry_early = MagicMock(return_value=(0, 0, []))
        s.daily_state.entries = [self._full_ic()]
        with patch.object(strat_mod, "get_us_market_time", return_value=_et(15, 50)):
            s._execute_eod_flatten(cutoff_label="15:50", is_fomc=False)
        s._close_entry_early.assert_not_called()

    def test_flatten_closes_both_when_both_at_risk(self):
        s = self._strat(spot=7485.0)          # call 75pt OTM, put 15pt OTM (< 20)
        e = self._full_ic()
        e.short_call_strike = 7495.0          # 10pt OTM → also at-risk
        s._close_entry_early = MagicMock(return_value=(4, 0, []))
        s.daily_state.entries = [e]
        with patch.object(strat_mod, "get_us_market_time", return_value=_et(15, 50)):
            s._execute_eod_flatten(cutoff_label="15:50", is_fomc=False)
        _, kwargs = s._close_entry_early.call_args
        assert kwargs["skip_sides"] == set()  # neither side safe → flatten both

    # ── _close_entry_early actually LEAVES the skipped side open ──
    def test_close_entry_early_leaves_skipped_side_untouched(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.commission_per_leg = 0.65
        s.daily_state = base_mod.MEICDailyState()
        s._close_position_with_retry = MagicMock(return_value=(True, 0.10, "oid"))
        s._read_option_quote = MagicMock(return_value={"bid": 0.5, "ask": 0.6})
        s._book_early_close_side_pnl = MagicMock()
        s._record_stop_to_db = MagicMock()
        e = self._full_ic()
        with patch.object(strat_mod, "get_us_market_time", return_value=_et(15, 50)):
            closed, failed, _ = s._close_entry_early(e, skip_sides={"put"})
        touched = [c.args[1] for c in s._close_position_with_retry.call_args_list]
        assert set(touched) == {"short_call", "long_call"}   # only the at-risk side
        assert "short_put" not in touched and "long_put" not in touched
        assert e.put_side_expired is False   # put rides to worthless expiry / settlement
        assert e.call_side_expired is True   # call side flattened
        assert e.is_complete is False        # not complete until the put settles
