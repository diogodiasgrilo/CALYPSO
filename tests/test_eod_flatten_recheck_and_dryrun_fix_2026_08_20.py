"""2026-08-20 (full-day execution audit findings, real production incidents):

FIX 1 — dry-run zero-cost booking bug (variants A & C). MKT-047's EOD-flatten
shares `_close_entry_early` -> `_book_early_close_side_pnl` with MKT-018. In
dry-run, `_close_position_with_retry` (SAFETY-DRY-04) always returns
`(True, None, None)` — no simulated fill price — so `side_close_cost` stays 0
and the side's FULL credit gets booked as if it closed for free. Real
incident: variant A's day flipped from a reported +$24.85 to an estimated
true -$40/-$55; variant C overstated by ~$490 (+$619.50 reported vs ~+$129.50
true). `_eod_flatten_dry_run_correct` mirrors the fallback Brandon's own
TP/GEX-breach handlers already use (fall back to the pre-close spread-value
MARK) — this test file pins that fix.

FIX 2 — MKT-047's single point-in-time OTM check. The primary flatten decides
once, at the 15:50 ET cutoff, whether each side is safe to ride to expiry.
Real 2026-08-20 near-miss on variant B (live paper money): two short puts
were 11pt OTM at that single check — left to ride — but SPX drifted and the
cushion shrank to 0.84pt (near-ATM) at 15:57:30 before recovering to 1.4pt by
the close. `_check_eod_flatten_recheck` re-watches every side left riding on
every subsequent tick until close — this file pins that fix.
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from bots.hydra.strategy import HydraStrategy
import bots.hydra.strategy as strat_mod
import bots.hydra.base_strategy as base_mod
from bots.hydra.base_strategy import MEICStrategy


def _et(h, m, s=0):
    return datetime.datetime(2026, 8, 20, h, m, s)


def _entry(**kw):
    e = base_mod.IronCondorEntry(entry_number=1)
    e.contracts = 7
    e.close_commission = 0.0
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def _put_prices_for_value(value: float, contracts: int = 7):
    """(short_put_price, long_put_price) whose put_spread_value == value,
    given IronCondorEntry.put_spread_value = (short - long) * 100 * contracts.
    long_put_price pinned at 0 for simplicity."""
    return value / (100 * contracts), 0.0


# ─────────────────────── Fix 1: dry-run cost correction ───────────────────────

class TestEodFlattenDryRunCorrectUnit:
    """Direct unit tests of _eod_flatten_dry_run_correct."""

    def _strat(self, *, dry_run=True):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = dry_run
        s.daily_state = base_mod.MEICDailyState()
        return s

    def test_subtracts_mark_and_sets_debit(self):
        s = self._strat(dry_run=True)
        short_p, long_p = _put_prices_for_value(90.0)
        e = _entry(put_spread_credit=135.0, short_put_price=short_p, long_put_price=long_p,
                   short_put_strike=7640, long_put_strike=7630,
                   actual_put_stop_debit=0.0)
        mark = e.put_spread_value
        s.daily_state.total_realized_pnl = 135.0  # already booked as raw credit

        s._eod_flatten_dry_run_correct(e, "put")

        assert s.daily_state.total_realized_pnl == pytest.approx(135.0 - mark)
        assert e.actual_put_stop_debit == pytest.approx(mark)

    def test_noop_when_not_dry_run(self):
        s = self._strat(dry_run=False)
        short_p, long_p = _put_prices_for_value(90.0)
        e = _entry(put_spread_credit=135.0, short_put_price=short_p, long_put_price=long_p,
                   short_put_strike=7640, long_put_strike=7630,
                   actual_put_stop_debit=0.0)
        s.daily_state.total_realized_pnl = 135.0

        s._eod_flatten_dry_run_correct(e, "put")

        assert s.daily_state.total_realized_pnl == 135.0  # unchanged
        assert e.actual_put_stop_debit == 0.0

    def test_noop_when_real_fill_cost_already_known(self):
        # A live close (or an already-corrected dry-run close) must never be
        # double-corrected.
        s = self._strat(dry_run=True)
        short_p, long_p = _put_prices_for_value(90.0)
        e = _entry(put_spread_credit=135.0, short_put_price=short_p, long_put_price=long_p,
                   short_put_strike=7640, long_put_strike=7630,
                   actual_put_stop_debit=475.0)  # real fill already captured
        s.daily_state.total_realized_pnl = 135.0 - 475.0  # already correct

        s._eod_flatten_dry_run_correct(e, "put")

        assert s.daily_state.total_realized_pnl == pytest.approx(135.0 - 475.0)  # unchanged
        assert e.actual_put_stop_debit == 475.0  # not overwritten

    def test_noop_when_no_mark_available(self):
        s = self._strat(dry_run=True)
        e = _entry(put_spread_credit=135.0, short_put_price=0.0, long_put_price=0.0,
                   short_put_strike=7640, long_put_strike=7630,
                   actual_put_stop_debit=0.0)
        s.daily_state.total_realized_pnl = 135.0

        s._eod_flatten_dry_run_correct(e, "put")

        assert s.daily_state.total_realized_pnl == 135.0  # left as-is
        assert e.actual_put_stop_debit == 0.0


class TestEodFlattenExecuteDryRunCorrectionIntegration:
    """End-to-end through _execute_eod_flatten with the REAL _close_entry_early
    (not mocked) — reproduces the actual 2026-08-19/20 variant A/C incident."""

    def _strat(self, *, dry_run=True, short_fill=None, long_fill=0.01):
        """short_fill=None simulates SAFETY-DRY-04's (True, None, None) shape
        (dry-run / no fill captured). A real close ALWAYS has the short and
        long legs fill at different prices (that's what a spread means) — a
        mock returning the same price for both would make
        _close_entry_early's `side_close_cost += cost` (short, buy back) and
        `-= cost` (long, sell) cancel to exactly 0, silently masquerading as
        the dry-run "no fill" case regardless of what this test is trying to
        exercise. long_fill defaults to a small nonzero value for exactly
        this reason (0.0 is also falsy in the `if fill_price and ...` check
        the production code uses, so a genuinely-zero long would be treated
        as "unresolved", not "confirmed worthless" — a separate, real
        subtlety this test deliberately avoids rather than conflates with)."""
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = dry_run
        s.commission_per_leg = 1.15
        s.contracts_per_entry = 7
        s.eod_flatten_enabled = True
        s._eod_flatten_done = False
        s.eod_flatten_skip_otm_pts = 10.0
        s.current_price = 7635.0  # near the put strike -> at-risk, must close
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._record_stop_to_db = MagicMock()
        s._read_option_quote = lambda uic: {"bid": 0.5}

        def _close_side_effect(pos_id, leg_name, uic=None, entry_number=None, contracts=None):
            if short_fill is None:
                return (True, None, None)  # SAFETY-DRY-04 shape
            if leg_name.startswith("short"):
                return (True, short_fill, "oid")
            return (True, long_fill, "oid")

        s._close_position_with_retry = MagicMock(side_effect=_close_side_effect)
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        return s

    def _at_risk_put_entry(self, mark=90.0):
        # short put 7640, spot 7635 -> 5pt OTM, inside the 10pt cushion -> at risk
        short_p, long_p = _put_prices_for_value(mark)
        return _entry(
            call_side_skipped=True, put_only=True,
            short_call_strike=0.0, long_call_strike=0.0,
            short_put_strike=7640, long_put_strike=7630,
            short_put_uic=111, long_put_uic=222,
            short_put_position_id=None, long_put_position_id=None,
            put_spread_credit=135.0,
            short_put_price=short_p, long_put_price=long_p,  # live mark at close time
            actual_put_stop_debit=0.0,
        )

    def _run(self, s, entry):
        s.daily_state.entries = [entry]
        with patch.object(strat_mod, "get_us_market_time", return_value=_et(15, 50)):
            return s._execute_eod_flatten(cutoff_label="15:50", is_fomc=False)

    def test_dry_run_books_net_not_raw_credit(self):
        s = self._strat(dry_run=True, short_fill=None)  # SAFETY-DRY-04 shape
        e = self._at_risk_put_entry(mark=90.0)
        mark = e.put_spread_value

        self._run(s, e)

        # BUGGY (pre-fix) behavior would leave this at 135.0 (raw credit).
        assert s.daily_state.total_realized_pnl == pytest.approx(135.0 - mark)
        assert e.actual_put_stop_debit == pytest.approx(mark)
        assert e.put_side_expired is True

    def test_live_mode_uses_real_fill_unaffected_by_correction(self):
        # A REAL close with real (distinct short/long) fill prices must NOT
        # be touched by the dry-run correction — it's already correct from
        # the real fills. net cost = short buy-back (0.10*100*7=$70) minus
        # long sale proceeds (0.01*100*7=$7) = $63.
        s = self._strat(dry_run=False, short_fill=0.10, long_fill=0.01)
        e = self._at_risk_put_entry(mark=90.0)

        self._run(s, e)

        assert s.daily_state.total_realized_pnl == pytest.approx(135.0 - 63.0)
        assert e.actual_put_stop_debit == pytest.approx(63.0)  # the REAL net fill cost, not the mark (90)

    def test_already_closed_side_not_re_corrected_on_second_call(self):
        # Idempotency: calling _execute_eod_flatten again (e.g. a same-day
        # restart re-running the primary sweep) must not re-subtract.
        s = self._strat(dry_run=True, short_fill=None)
        e = self._at_risk_put_entry(mark=90.0)
        mark = e.put_spread_value
        self._run(s, e)
        first_total = s.daily_state.total_realized_pnl
        assert first_total == pytest.approx(135.0 - mark)

        s._eod_flatten_done = False  # simulate a restart resetting the one-shot latch
        self._run(s, e)

        assert s.daily_state.total_realized_pnl == pytest.approx(first_total)  # unchanged


# ─────────────────────── Fix 2: continuous re-check ───────────────────────

class TestEodFlattenRecheckGating:
    """Gating behavior with _close_entry_early mocked out — mirrors
    TestEodFlattenExecute's style for pure gate-logic tests."""

    def _strat(self, *, eod_flatten_done=True, hour=15, minute=57,
               active_entry=True):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_enabled = True
        s._eod_flatten_done = eod_flatten_done
        s.eod_flatten_skip_otm_pts = 10.0
        s.current_price = 7639.16  # 0.84pt OTM of a 7640 put -> at risk
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._close_entry_early = MagicMock(return_value=(0, 0, []))
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        if active_entry:
            e = _entry(call_side_skipped=True, put_only=True,
                       short_put_strike=7640, short_put_uic=111,
                       put_spread_credit=135.0)
            s.daily_state.entries = [e]
        self._now = _et(hour, minute)
        return s

    def _run(self, s):
        with patch.object(strat_mod, "get_us_market_time", return_value=self._now):
            return s._check_eod_flatten_recheck()

    def test_noop_before_primary_flatten_has_run(self):
        s = self._strat(eod_flatten_done=False)
        assert self._run(s) is None
        s._close_entry_early.assert_not_called()

    def test_noop_after_market_close(self):
        s = self._strat(hour=16, minute=1)
        assert self._run(s) is None
        s._close_entry_early.assert_not_called()

    def test_noop_when_no_active_entries(self):
        s = self._strat(active_entry=False)
        assert self._run(s) is None
        s._close_entry_early.assert_not_called()

    def test_calendar_variant_never_recheck(self):
        s = self._strat()
        s.requires_protective_wings = False
        assert self._run(s) is None
        s._close_entry_early.assert_not_called()

    def test_noop_when_close_returns_zero_legs(self):
        # _close_entry_early gets called (side is at-risk) but reports
        # nothing actually closed (e.g. it was already closed) -> silent.
        s = self._strat()
        assert self._run(s) is None
        s._save_state_to_disk.assert_not_called()
        s.alert_service.send_alert.assert_not_called()


class TestEodFlattenRecheckIntegration:
    """End-to-end with the REAL _close_entry_early — proves a genuinely
    drifted side gets force-closed and correctly corrected."""

    def _strat(self, *, fill_price=None, dry_run=True):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = dry_run
        s.commission_per_leg = 1.15
        s.contracts_per_entry = 7
        s.eod_flatten_enabled = True
        s._eod_flatten_done = True  # primary sweep already fired
        s.eod_flatten_skip_otm_pts = 10.0
        s.current_price = 7639.16  # 0.84pt OTM -> drifted within the 10pt cushion
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._record_stop_to_db = MagicMock()
        s._read_option_quote = lambda uic: {"bid": 0.5}
        s._close_position_with_retry = MagicMock(
            return_value=(True, fill_price, "oid") if fill_price is not None
            else (True, None, None)
        )
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        return s

    def _drifted_put_entry(self, mark=140.0):
        # Was 11pt OTM at the 15:50 primary check (safely skipped then, so
        # never closed); now at 7639.16 spot it's 0.84pt OTM -> at risk.
        short_p, long_p = _put_prices_for_value(mark)
        return _entry(
            call_side_skipped=True, put_only=True,
            short_put_strike=7640, long_put_strike=7630,
            short_put_uic=111, long_put_uic=222,
            short_put_position_id=None, long_put_position_id=None,
            put_spread_credit=175.0,
            short_put_price=short_p, long_put_price=long_p,
            actual_put_stop_debit=0.0,
        )

    def _run(self, s, entry, now=None):
        s.daily_state.entries = [entry]
        with patch.object(strat_mod, "get_us_market_time", return_value=now or _et(15, 57, 30)):
            return s._check_eod_flatten_recheck()

    def test_drifted_side_gets_force_closed_and_dry_run_corrected(self):
        s = self._strat(dry_run=True, fill_price=None)
        e = self._drifted_put_entry(mark=140.0)
        mark = e.put_spread_value

        result = self._run(s, e)

        assert result is not None
        assert "MKT-047-RECHECK" in result
        assert e.put_side_expired is True
        assert e.close_reason == "EOD_FLATTEN"
        # Dry-run correction fired: net = credit - mark, not raw credit.
        assert s.daily_state.total_realized_pnl == pytest.approx(175.0 - mark)
        assert e.actual_put_stop_debit == pytest.approx(mark)
        s._save_state_to_disk.assert_called_once()
        s.alert_service.send_alert.assert_called_once()

    def test_side_already_safe_again_is_left_alone(self):
        # Recovered back outside the cushion (e.g. bounced) -> no action.
        s = self._strat()
        s.current_price = 7655.0  # 15pt OTM, back outside the 10pt cushion
        e = self._drifted_put_entry()

        result = self._run(s, e)

        assert result is None
        assert e.put_side_expired is False
        s._save_state_to_disk.assert_not_called()
        s.alert_service.send_alert.assert_not_called()

    def test_side_already_closed_by_primary_sweep_not_touched_again(self):
        s = self._strat()
        e = self._drifted_put_entry()
        e.put_side_expired = True  # already closed by the primary 15:50 sweep
        e.actual_put_stop_debit = 140.0  # already correctly booked once

        result = self._run(s, e)

        assert result is None  # active_entries excludes a fully-closed put-only entry
        assert s.daily_state.total_realized_pnl == 0.0  # not re-corrected
        s._close_position_with_retry.assert_not_called()

    def test_failed_close_enters_cooldown_and_is_not_retried_immediately(self):
        s = self._strat()
        s._close_position_with_retry = MagicMock(return_value=(False, None, None))
        e = self._drifted_put_entry()

        result1 = self._run(s, e, now=_et(15, 57, 30))
        assert result1 is not None
        assert e.put_side_expired is False  # still open, still monitored
        first_call_count = s._close_position_with_retry.call_count
        assert first_call_count >= 1

        # Immediately after (well within the 90s cooldown): must NOT retry.
        s.alert_service.send_alert.reset_mock()
        result2 = self._run(s, e, now=_et(15, 57, 40))
        assert result2 is None
        assert s._close_position_with_retry.call_count == first_call_count  # no new attempt

    def test_failed_close_retried_after_cooldown_expires(self):
        s = self._strat()
        s._close_position_with_retry = MagicMock(return_value=(False, None, None))
        e = self._drifted_put_entry()

        self._run(s, e, now=_et(15, 57, 30))
        first_call_count = s._close_position_with_retry.call_count

        # 91s later — past the 90s cooldown — must retry.
        self._run(s, e, now=_et(15, 59, 1))
        assert s._close_position_with_retry.call_count > first_call_count

    def test_clear_cooldown_on_eventual_success(self):
        s = self._strat()
        s._close_position_with_retry = MagicMock(return_value=(False, None, None))
        e = self._drifted_put_entry()
        self._run(s, e, now=_et(15, 57, 30))
        assert (1, "put") in s._eod_recheck_failed_store()

        # Now let the retry succeed.
        s._close_position_with_retry = MagicMock(return_value=(True, None, None))
        self._run(s, e, now=_et(15, 59, 1))

        assert (1, "put") not in s._eod_recheck_failed_store()


class TestEodFlattenRecheckWiredIntoHandleMonitoring:
    """The standalone-function tests above prove _check_eod_flatten_recheck
    is correct in isolation — this proves _handle_monitoring actually CALLS
    it. Without this, every test above could pass while the real bot never
    invokes the fix at all."""

    def _strat(self, *, eod_flatten_done=True, active_entries=1):
        s = HydraStrategy.__new__(HydraStrategy)
        s.short_only_stop = False
        s.early_close_enabled = False
        s.eod_flatten_enabled = True
        s._eod_flatten_done = eod_flatten_done
        s.daily_state = base_mod.MEICDailyState()
        if active_entries:
            s.daily_state.entries = [_entry(is_complete=True) for _ in range(active_entries)]
        s._check_eod_flatten_recheck = MagicMock(return_value=None)
        s._check_eod_flatten = MagicMock(return_value=None)  # the PRIMARY sweep — not under test here
        return s

    def test_recheck_called_when_primary_flatten_already_done(self):
        s = self._strat(eod_flatten_done=True, active_entries=1)
        with patch.object(MEICStrategy, "_handle_monitoring", return_value="ok"):
            s._handle_monitoring()
        s._check_eod_flatten_recheck.assert_called_once()

    def test_recheck_not_called_before_primary_flatten_has_run(self):
        s = self._strat(eod_flatten_done=False, active_entries=1)
        with patch.object(MEICStrategy, "_handle_monitoring", return_value="ok"):
            s._handle_monitoring()
        s._check_eod_flatten_recheck.assert_not_called()

    def test_recheck_not_called_with_no_active_entries(self):
        s = self._strat(eod_flatten_done=True, active_entries=0)
        with patch.object(MEICStrategy, "_handle_monitoring", return_value="ok"):
            s._handle_monitoring()
        s._check_eod_flatten_recheck.assert_not_called()

    def test_recheck_result_is_returned_when_it_fires(self):
        s = self._strat(eod_flatten_done=True, active_entries=1)
        s._check_eod_flatten_recheck = MagicMock(return_value="MKT-047-RECHECK: closed something")
        with patch.object(MEICStrategy, "_handle_monitoring", return_value="ok"):
            result = s._handle_monitoring()
        assert result == "MKT-047-RECHECK: closed something"


# ────────────── Round-1 review remediation (2026-08-20, same day) ──────────────
# Round-1 adversarial review of the two fixes above found 2 HIGH + 2 real
# MEDIUM/LOW findings before deploy. This section pins each remediation.

class TestEodFlattenRecheckCooldownClearsOnObservedSafe:
    """HIGH #1: pre-remediation, a failed close's 90s cooldown only cleared on
    a LATER SUCCESSFUL close (see test_clear_cooldown_on_eventual_success
    above) — never merely on the side being observed OTM-safe again. A side
    that fails, drifts safe, then drifts BACK at-risk within the original 90s
    window stayed silently unprotected until the stale cooldown expired,
    defeating the whole point of a continuous re-check. Fix: clear the
    cooldown the moment `_eod_flatten_can_skip_side` is next True, not only
    on a successful `_close_entry_early`."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = True
        s.commission_per_leg = 1.15
        s.contracts_per_entry = 7
        s.eod_flatten_enabled = True
        s._eod_flatten_done = True
        s.eod_flatten_skip_otm_pts = 10.0
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._record_stop_to_db = MagicMock()
        s._read_option_quote = lambda uic: {"bid": 0.5}
        s._close_position_with_retry = MagicMock(return_value=(False, None, None))
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        return s

    def _put_entry(self, mark=140.0):
        short_p, long_p = _put_prices_for_value(mark)
        return _entry(
            call_side_skipped=True, put_only=True,
            short_put_strike=7640, long_put_strike=7630,
            short_put_uic=111, long_put_uic=222,
            short_put_position_id=None, long_put_position_id=None,
            put_spread_credit=175.0,
            short_put_price=short_p, long_put_price=long_p,
            actual_put_stop_debit=0.0,
        )

    def _run(self, s, entry, now, price):
        s.current_price = price
        s.daily_state.entries = [entry]
        with patch.object(strat_mod, "get_us_market_time", return_value=now):
            return s._check_eod_flatten_recheck()

    def test_redrift_within_original_cooldown_window_is_retried_immediately(self):
        s = self._strat()
        e = self._put_entry()

        # t=15:57:30: 0.84pt OTM -> at risk. Close attempt fails -> cooldown starts.
        self._run(s, e, _et(15, 57, 30), price=7639.16)
        assert (1, "put") in s._eod_recheck_failed_store()
        attempts_after_failure = s._close_position_with_retry.call_count
        assert attempts_after_failure >= 1

        # t=15:57:40 (10s later): bounces back to 15pt OTM -> safe. No retry
        # attempted (side is skippable), but the observed-safe state must
        # clear the stale cooldown entry.
        self._run(s, e, _et(15, 57, 40), price=7655.0)
        assert (1, "put") not in s._eod_recheck_failed_store()
        assert s._close_position_with_retry.call_count == attempts_after_failure

        # t=15:57:50 (only 20s after the ORIGINAL failure — well inside the
        # 90s cooldown window): drifts back to 0.84pt OTM -> at risk again.
        # Must be retried NOW, not blocked by the stale cooldown.
        self._run(s, e, _et(15, 57, 50), price=7639.16)
        assert s._close_position_with_retry.call_count > attempts_after_failure


class TestEodFlattenRecheckTickTimeBudget:
    """HIGH #2: pre-remediation, the per-entry loop had no wall-clock budget,
    so N stuck legs in the same tick could compound serially (each retry
    stack up to ~2.7min) and starve every OTHER entry's safety check for the
    rest of the ~10min re-check window. Fix: a 60s per-tick budget — any
    entry not reached within it is deferred to the next tick."""

    def _strat(self, n_entries=2):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_enabled = True
        s._eod_flatten_done = True
        s.eod_flatten_skip_otm_pts = 10.0
        s.current_price = 7639.16  # 0.84pt OTM of a 7640 put -> at risk
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._close_entry_early = MagicMock(return_value=(0, 0, []))
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        s.daily_state.entries = [
            _entry(entry_number=i, call_side_skipped=True, put_only=True,
                   short_put_strike=7640, short_put_uic=111,
                   put_spread_credit=135.0)
            for i in range(1, n_entries + 1)
        ]
        return s

    def _run(self, s, monotonic_values, now=None):
        with patch.object(strat_mod, "get_us_market_time", return_value=now or _et(15, 57, 30)), \
             patch.object(strat_mod.time, "monotonic", side_effect=monotonic_values):
            return s._check_eod_flatten_recheck()

    def test_second_entry_deferred_when_budget_exhausted(self):
        s = self._strat(n_entries=2)
        # start=1000.0; entry1 check passes (0s elapsed); entry2 check trips
        # (65s elapsed >= the 60s budget) -> loop breaks before entry2.
        self._run(s, monotonic_values=[1000.0, 1000.0, 1065.0])

        assert s._close_entry_early.call_count == 1
        processed = s._close_entry_early.call_args_list[0][0][0]
        assert processed.entry_number == 1

    def test_both_entries_processed_when_within_budget(self):
        s = self._strat(n_entries=2)
        self._run(s, monotonic_values=[1000.0, 1000.0, 1005.0])

        assert s._close_entry_early.call_count == 2


class TestEodFlattenRecheckStarvationFairness:
    """Round-2 review finding (confirmed by independent verification against
    the live code): the fixed entry-iteration order meant a single entry
    that blows the tick budget on EVERY tick (e.g. a stuck leg whose price
    keeps oscillating across the cushion boundary, repeatedly re-armed by
    the cooldown-clear-on-safe fix above) could perpetually occupy the FRONT
    of every tick's budget, starving every later entry of its own recheck
    coverage for a large fraction of the ~10min window — exactly the
    failure mode the tick budget itself exists to prevent, reopened across
    ticks instead of within one. Fix: rotate the iteration start point to
    just past wherever the previous tick left off."""

    def _strat(self, n_entries=3):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_enabled = True
        s._eod_flatten_done = True
        s.eod_flatten_skip_otm_pts = 10.0
        s.current_price = 7639.16  # 0.84pt OTM of a 7640 put -> at risk
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._close_entry_early = MagicMock(return_value=(0, 0, []))
        s._eod_recheck_next_start_idx = 0
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        s.daily_state.entries = [
            _entry(entry_number=i, call_side_skipped=True, put_only=True,
                   short_put_strike=7640, short_put_uic=111,
                   put_spread_credit=135.0)
            for i in range(1, n_entries + 1)
        ]
        return s

    def _run(self, s, monotonic_values, now=None):
        with patch.object(strat_mod, "get_us_market_time", return_value=now or _et(15, 57, 30)), \
             patch.object(strat_mod.time, "monotonic", side_effect=monotonic_values):
            return s._check_eod_flatten_recheck()

    def test_stuck_early_entry_does_not_perpetually_block_later_entries(self):
        s = self._strat(n_entries=3)

        # Tick 1: entry #1 alone consumes the whole 60s budget (simulated
        # via the monotonic sequence — realistic if E1's own close attempt
        # blocks for a long time). start=1000.0; E1's check passes (0s
        # elapsed, gets processed); E2's check trips (65s elapsed) -> only
        # E1 reached this tick.
        self._run(s, monotonic_values=[1000.0, 1000.0, 1065.0])
        assert s._close_entry_early.call_count == 1
        assert s._close_entry_early.call_args_list[0][0][0].entry_number == 1
        assert s._eod_recheck_next_start_idx == 1

        # Tick 2: WITHOUT the fix, iteration restarts from entry #1 again,
        # so the very next _close_entry_early call would (again) be on
        # entry #1 -- re-blocking entries #2/#3 for a SECOND consecutive
        # tick. WITH the fix, tick 2 starts from entry #2 -- the earliest
        # entry NOT yet reached last tick -- so it (and #3) get their own
        # recheck coverage immediately instead of waiting behind the stuck
        # entry #1 again.
        self._run(s, monotonic_values=[2000.0, 2000.0, 2005.0, 2010.0])
        assert s._close_entry_early.call_count == 4
        second_tick_entry_numbers = [
            c[0][0].entry_number for c in s._close_entry_early.call_args_list[1:]
        ]
        assert second_tick_entry_numbers == [2, 3, 1]


class TestEodFlattenRecheckEarlyCloseAwareBound:
    """MEDIUM: pre-remediation, the recheck's market-closed gate was a
    hardcoded `now.hour >= 16`, ignoring early-close days (1:00pm ET). Fix:
    compute minutes-to-close from `get_market_close_time(now)`, matching the
    existing pattern in `_place_marketable_close`."""

    def _strat(self, hour, minute):
        s = HydraStrategy.__new__(HydraStrategy)
        s.eod_flatten_enabled = True
        s._eod_flatten_done = True
        s.eod_flatten_skip_otm_pts = 10.0
        s.current_price = 7639.16  # at-risk IF the function proceeds past the close gate
        s.registry = MagicMock()
        s.alert_service = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._spawn_async_early_close_fill_correction = MagicMock()
        s._close_entry_early = MagicMock(return_value=(0, 0, []))
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        s.daily_state.entries = [_entry(call_side_skipped=True, put_only=True,
                                         short_put_strike=7640, short_put_uic=111,
                                         put_spread_credit=135.0)]
        self._now = _et(hour, minute)
        return s

    def _run(self, s):
        with patch.object(strat_mod, "get_us_market_time", return_value=self._now), \
             patch.object(strat_mod, "get_market_close_time", return_value=datetime.time(13, 0)):
            return s._check_eod_flatten_recheck()

    def test_stops_recheck_after_early_close_even_though_hour_lt_16(self):
        # 2:30pm ET on a simulated 1:00pm early-close day. The old hardcoded
        # `now.hour >= 16` bound would keep re-checking for 3 more hours
        # after the market actually closed.
        s = self._strat(hour=14, minute=30)
        result = self._run(s)
        assert result is None
        s._close_entry_early.assert_not_called()

    def test_still_active_before_early_close(self):
        s = self._strat(hour=12, minute=55)  # 5min before the 1pm early close
        self._run(s)
        s._close_entry_early.assert_called_once()


class TestEodSideIsLiveShortPivotClosed:
    """MEDIUM/LOW (confirmed by 2 independent round-1 reviewers): a side
    closed by the directional-pivot feature (`*_side_pivot_closed`) wasn't
    excluded from `_eod_side_is_live_short`, mirroring a pre-existing gap
    already in `_close_entry_early`/`_eod_flatten_can_skip_side`. Currently
    unreachable (`directional_pivot.enabled=false` everywhere), but a real,
    independently-testable gap — cheap to close."""

    def test_pivot_closed_side_not_treated_as_live(self):
        e = _entry(put_side_pivot_closed=True, put_side_stopped=False,
                    put_side_expired=False, put_side_skipped=False,
                    short_put_uic=111)
        assert HydraStrategy._eod_side_is_live_short(e, "put") is False

    def test_non_pivot_closed_side_is_live(self):
        e = _entry(put_side_pivot_closed=False, put_side_stopped=False,
                    put_side_expired=False, put_side_skipped=False,
                    short_put_uic=111)
        assert HydraStrategy._eod_side_is_live_short(e, "put") is True
