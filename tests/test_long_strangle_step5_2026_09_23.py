"""Strategy H, Playbook Step 5 — the exits. Profit target, expiry, and no stop.

Step 5's exit criterion is "stop / target / EOD paths book the right P&L and are
unit-tested, including the noise/false-trigger guard."

**H has no stop**, and that is the substance of the step rather than a gap in it:
max loss is the debit, known before the position opens. What replaces a stop is a
percent-of-debit profit target, and what replaces the breach-persistence window is
quote quality — see ``TestWhyThereIsNoPersistenceWindow``, which is the one design
decision here that inverts the playbook's own instruction on purpose.

Two exits in this file would have failed SILENTLY if inherited:

* **Settlement.** The base books "the full credit kept" for a side finishing OTM.
  For H that field is never set, so the base would book **$0.00** and record a
  strangle that expired worthless as **break-even** rather than a total loss of
  the premium. Same class as G's S-HIGH-2, sign reversed.
* **The DATA-004 sanity guard.** It rejects a side whose legs are "partially
  zero"; H's shorts are permanently zero against a priced long, so it rejected
  every tick — discarding the very tick the target needs.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.base_strategy import ConfigError, MEICDailyState  # noqa: E402
from bots.hydra.long_strangle_entry import LongStrangleEntry  # noqa: E402
from bots.hydra.long_strangle_strategy import LongStrangleStrategy  # noqa: E402
from shared.market_hours import get_us_market_time  # noqa: E402

SPOT = 7765.0


def _strat(ls_cfg=None, contracts=2):
    s = LongStrangleStrategy.__new__(LongStrangleStrategy)
    s.current_price = SPOT
    s.current_vix = 14.5
    s.dry_run = True
    s.contracts_per_entry = contracts
    s.commission_per_leg = 1.15
    cfg = {"profit_target_pct_of_debit": 50.0}
    cfg.update(ls_cfg or {})
    s.strategy_config = {"long_strangle": cfg}
    s.ls_recorder = MagicMock()
    s._batch_update_entry_prices = MagicMock()
    # The REAL daily state, not a stub: `active_entries` is a property whose
    # done-side logic is precisely what "a closed entry leaves the active set"
    # is asserting, and a stubbed version would assert nothing.
    s.daily_state = MEICDailyState(date="2026-09-23")
    return s


def _open(strat, *, call_px=1.00, put_px=1.45, contracts=2):
    """An open H position: $245/contract paid, both legs currently at cost."""
    e = LongStrangleEntry(entry_number=1)
    e.contracts = contracts
    e.long_call_strike, e.long_put_strike = 7785.0, 7745.0
    e.long_call_uic, e.long_put_uic = 111, 222
    e.long_call_price, e.long_put_price = call_px, put_px
    e.call_debit = call_px * 100 * contracts
    e.put_debit = put_px * 100 * contracts
    e.open_commission = 2 * 1.15 * contracts
    e.entry_time = get_us_market_time() - timedelta(minutes=42)
    strat.daily_state.entries.append(e)
    return e


def _mark(entry, call_px, put_px):
    entry.long_call_price, entry.long_put_price = call_px, put_px
    return entry


# ======================================================================
# There is no stop loss, and that is the design
# ======================================================================

class TestThereIsNoStopLoss:
    def test_a_total_loss_never_triggers_an_exit(self):
        """Both legs going to zero IS the maximum loss, and it is the position's
        designed worst case — not a condition to react to."""
        s = _strat()
        e = _mark(_open(s), 0.01, 0.01)     # −98% of the debit
        assert s._check_stop_losses() is None
        assert e.call_side_expired is False

    def test_the_loss_is_bounded_by_the_debit_no_matter_the_mark(self):
        s = _strat()
        e = _mark(_open(s), 0.0, 0.0)
        assert e.unrealized_pnl == pytest.approx(-e.total_debit)
        assert e.max_loss == e.total_debit


# ======================================================================
# The sanity guard — G's S-CRIT-1, mirrored
# ======================================================================

class TestTheSanityGuardValidatesTheLongLegs:
    def test_a_priced_long_pair_is_valid_despite_zero_shorts(self):
        """The base's DATA-004 check would call this "partial zero prices" and
        reject every tick. H's shorts are absent by construction, not missing."""
        s = _strat()
        e = _open(s)
        assert e.short_call_price == 0 and e.short_put_price == 0
        ok, _ = s._validate_pnl_sanity(e)
        assert ok is True

    def test_an_unpriced_long_is_rejected(self):
        s = _strat()
        e = _mark(_open(s), 0.0, 1.45)
        ok, msg = s._validate_pnl_sanity(e)
        assert ok is False and "call" in msg

    def test_an_already_closed_side_is_not_revalidated(self):
        s = _strat()
        e = _mark(_open(s), 0.0, 0.0)
        e.call_side_expired = e.put_side_expired = True
        assert s._validate_pnl_sanity(e)[0] is True

    def test_an_unpriced_tick_is_skipped_without_exiting(self):
        s = _strat()
        e = _mark(_open(s), 0.0, 0.0)
        assert s._check_stop_losses() is None
        s.ls_recorder.record_snapshot.assert_not_called()


# ======================================================================
# The profit target
# ======================================================================

class TestTheProfitTarget:
    def test_fifty_percent_of_the_debit_closes_the_position(self):
        s = _strat()
        e = _mark(_open(s), 1.50, 2.18)          # $736 value on a $490 debit
        msg = s._check_stop_losses()
        assert msg is not None and "closed" in msg
        assert e.close_reason == "profit_target_50"

    def test_just_below_the_target_holds(self):
        s = _strat()
        e = _mark(_open(s), 1.20, 2.00)          # +30.6%
        assert s._check_stop_losses() is None
        assert not getattr(e, "close_reason", "")

    def test_the_realized_pnl_is_value_minus_debit(self):
        s = _strat()
        e = _mark(_open(s), 2.00, 2.00)          # $800 value on a $490 debit
        s._check_stop_losses()
        assert e.realized_pnl == pytest.approx(310.0)
        assert s.daily_state.total_realized_pnl == pytest.approx(310.0)

    def test_pnl_is_booked_GROSS_of_commission_like_every_other_close_path(self):
        """Inventing a net-of-commission realized P&L here would make H's numbers
        quietly incomparable with A–G's."""
        s = _strat()
        e = _mark(_open(s), 2.00, 2.00)
        s._check_stop_losses()
        assert e.realized_pnl == pytest.approx(310.0)          # not 310 − 4.60
        assert s.daily_state.total_commission == pytest.approx(2 * 1.15 * 2)

    def test_a_closed_entry_leaves_the_active_set(self):
        s = _strat()
        e = _mark(_open(s), 2.00, 2.00)
        s._check_stop_losses()
        assert e.call_side_expired and e.put_side_expired
        assert e not in s.daily_state.active_entries

    def test_the_exit_is_recorded_with_its_percentage(self):
        """+50% / +100% are the units this strategy's targets are written in."""
        s = _strat()
        _mark(_open(s), 2.00, 2.00)
        s._check_stop_losses()
        kwargs = s.ls_recorder.record_exit.call_args.kwargs
        assert kwargs["exit_reason"] == "profit_target_50"
        assert kwargs["realized_pnl"] == pytest.approx(310.0)
        assert kwargs["minutes_held"] == pytest.approx(42.0, abs=0.5)

    def test_only_long_strangle_entries_are_managed_here(self):
        """The base's IC entries must not be routed through a debit-shaped exit
        if one ever shares the state file after a recovery."""
        s = _strat()
        from bots.hydra.strategy import HydraIronCondorEntry
        ic = HydraIronCondorEntry(entry_number=9)
        ic.short_call_strike, ic.long_call_strike = 7800.0, 7805.0
        ic.short_call_price, ic.long_call_price = 1.0, 0.5
        s.daily_state.entries.append(ic)
        assert s._check_stop_losses() is None
        assert not getattr(ic, "close_reason", "")


class TestTheElevatedTarget:
    """The source raises the target to +100% when IV is "expanding from a low
    base". That is TWO conditions, and reading it as one was the easy mistake:
    a low percentile alone is a cheap-premium day, which is the ENTRY filter,
    not the exit rule. He wants the bigger target when vol is cheap AND rising —
    the case where a long position gets paid twice, by the move and by the vol
    expansion."""

    def _s(self, **over):
        cfg = {"profit_target_pct_of_debit_iv_expanding": 100.0,
               "iv_percentile_max": 35.0}
        cfg.update(over)
        return _strat(ls_cfg=cfg)

    def test_cheap_AND_rising_raises_it_to_one_hundred(self):
        s = self._s()
        s.current_vix = 14.0                      # above the prior close...
        # 79 expensive days then a cheap 13.0 most recent: today is both LOW in
        # the distribution and ABOVE yesterday. Length matters — under the
        # 60-day sample floor the target correctly falls back to the base, which
        # is pinned in tests/test_iv_percentile_sample_floor_2026_09_23.py.
        s._vix_history_for_percentile = lambda: [20.0 + (i % 11) for i in range(79)] + [13.0]
        assert s._profit_target_pct() == pytest.approx(100.0)   # ...and low pct

    def test_cheap_but_FALLING_keeps_the_ordinary_target(self):
        """Vol collapsing toward a low is not vol expanding from one. Buying
        premium into a decline is the opposite of the setup he describes."""
        s = self._s()
        s.current_vix = 10.0
        s._vix_history_for_percentile = lambda: [12.0, 13.0, 14.0, 20.0, 25.0]
        assert s._profit_target_pct() == pytest.approx(50.0)

    def test_rising_but_EXPENSIVE_keeps_the_ordinary_target(self):
        """"From a low base" is doing real work — a rise off an already-high
        level is not the setup."""
        s = self._s()
        s.current_vix = 30.0
        s._vix_history_for_percentile = lambda: [10.0, 11.0, 12.0, 13.0, 20.0]
        assert s._profit_target_pct() == pytest.approx(50.0)

    def test_no_history_falls_back_to_the_ordinary_target(self):
        """Unknown is not "expanding". The ENTRY filter fails closed on unknown;
        the EXIT must fail to the ordinary target, because refusing to exit is
        not a safe default for a position already open."""
        s = self._s()
        s._vix_history_for_percentile = lambda: []
        assert s._profit_target_pct() == pytest.approx(50.0)

    def test_an_elevated_target_below_the_base_is_ignored(self):
        s = self._s(profit_target_pct_of_debit_iv_expanding=25.0)
        s.current_vix = 14.0
        s._vix_history_for_percentile = lambda: [20.0, 22.0, 25.0, 30.0, 13.0]
        assert s._profit_target_pct() == pytest.approx(50.0)


class TestWhyThereIsNoPersistenceWindow:
    """The one place Step 5's own instruction is deliberately inverted.

    The playbook asks for a breach-persistence window so a single stale tick
    cannot fire a false stop. A stop triggers on an ADVERSE spike, so waiting to
    confirm protects you. A long strangle's target triggers on a FAVOURABLE
    spike, and reverting is what those spikes do — waiting systematically gives
    back the move the strategy exists to capture. The sign of the position
    inverts the sign of the guard.
    """

    def test_the_target_fires_on_the_FIRST_valid_tick(self):
        s = _strat()
        _mark(_open(s), 2.00, 2.00)
        assert s._check_stop_losses() is not None     # no second tick needed

    def test_the_reasoning_is_recorded_in_the_code_not_just_here(self):
        src = (ROOT / "bots" / "hydra" / "long_strangle_strategy.py").read_text()
        assert "WHY THERE IS NO BREACH-PERSISTENCE WINDOW" in src
        assert "quote quality rather than elapsed time" in src

    def test_a_crossed_book_cannot_manufacture_a_target(self):
        """`_quote_mid` returns 0.0 on a crossed market (L-M7), which the
        long-legs sanity guard then rejects — quote quality is the guard that
        replaces elapsed time."""
        s = _strat()
        e = _mark(_open(s), 0.0, 2.00)               # call unquotable
        assert s._check_stop_losses() is None
        assert not getattr(e, "close_reason", "")

    def test_the_knob_exists_so_the_dry_run_can_MEASURE_the_other_setting(self):
        """Non-default on purpose: the claim that persistence is wrong here is
        reasoned, not measured. The knob makes it falsifiable rather than
        permanent."""
        s = _strat(ls_cfg={"profit_target_confirm_seconds": 10.0})
        e = _mark(_open(s), 2.00, 2.00)
        assert s._check_stop_losses() is None            # first touch: held
        assert not getattr(e, "close_reason", "")
        e._ls_target_first_seen = e._ls_target_first_seen - timedelta(seconds=11)
        assert s._check_stop_losses() is not None        # confirmed
        assert e.close_reason == "profit_target_50"

    def test_a_touch_that_reverts_resets_the_confirmation_clock(self):
        s = _strat(ls_cfg={"profit_target_confirm_seconds": 10.0})
        e = _mark(_open(s), 2.00, 2.00)
        s._check_stop_losses()
        assert e._ls_target_first_seen is not None
        _mark(e, 1.00, 1.00)                             # gave the spike back
        s._check_stop_losses()
        assert e._ls_target_first_seen is None

    def test_every_valid_tick_is_snapshotted_before_the_target_is_evaluated(self):
        """So the mark that triggered an exit is recoverable, and the
        peak-versus-exit gap — the question the source's win-rate claim rests on
        — is measurable."""
        s = _strat()
        _mark(_open(s), 2.00, 2.00)
        s._check_stop_losses()
        assert s.ls_recorder.record_snapshot.called
        assert s.ls_recorder.record_snapshot.call_count == 1


# ======================================================================
# Expiry
# ======================================================================

class TestSettlement:
    def test_expiring_worthless_books_the_FULL_DEBIT_AS_A_LOSS(self):
        """The whole point of the override. The base books "the credit kept" —
        which for H is an unset field, so it would book $0.00 and record a total
        loss as break-even."""
        s = _strat()
        e = _open(s)
        call, _ = s._settlement_booked_pnl(e, "call", 7760.0)   # below 7785
        put, _ = s._settlement_booked_pnl(e, "put", 7760.0)     # above 7745
        assert call == pytest.approx(-e.call_debit)
        assert put == pytest.approx(-e.put_debit)
        assert call + put == pytest.approx(-e.total_debit)

    def test_an_ITM_call_settles_at_intrinsic_minus_its_debit(self):
        s = _strat()
        e = _open(s)                                   # call strike 7785, 2c
        booked, _ = s._settlement_booked_pnl(e, "call", 7800.0)
        assert booked == pytest.approx(15 * 100 * 2 - e.call_debit)   # +$2,800

    def test_an_ITM_put_settles_at_intrinsic_minus_its_debit(self):
        s = _strat()
        e = _open(s)                                   # put strike 7745, 2c
        booked, _ = s._settlement_booked_pnl(e, "put", 7700.0)
        assert booked == pytest.approx(45 * 100 * 2 - e.put_debit)    # +$8,710

    def test_the_winning_leg_is_NOT_capped_by_any_width(self):
        """A long option's payoff has no cap. The base clamps intrinsic to the
        spread width — which for H would be `spread_width`'s truthful 0.0 or,
        worse, a four-digit strike masquerading as one."""
        s = _strat()
        e = _open(s)
        booked, _ = s._settlement_booked_pnl(e, "call", 8000.0)
        assert booked == pytest.approx(215 * 100 * 2 - e.call_debit)

    def test_worthless_is_always_False_because_it_means_kept_the_credit(self):
        s = _strat()
        e = _open(s)
        assert s._settlement_booked_pnl(e, "call", 7760.0)[1] is False
        assert s._settlement_booked_pnl(e, "call", 7800.0)[1] is False

    def test_an_unreadable_settlement_books_the_WORST_case_not_zero(self):
        """Unreachable on the normal path (the base defers), but booking 0.0
        would silently lose the whole debit — exactly the failure this override
        exists to prevent."""
        s = _strat()
        e = _open(s)
        booked, _ = s._settlement_booked_pnl(e, "call", None)
        assert booked == pytest.approx(-e.call_debit)

    def test_a_side_that_was_never_opened_books_nothing(self):
        s = _strat()
        e = _open(s)
        e.long_call_strike = 0.0
        assert s._settlement_booked_pnl(e, "call", 7800.0)[0] == 0.0

    def test_a_target_close_is_not_re_booked_at_settlement(self):
        """`_close_long_strangle` sets *_side_expired, which is the flag the
        settlement sweep checks before re-booking a side."""
        s = _strat()
        e = _mark(_open(s), 2.00, 2.00)
        s._check_stop_losses()
        assert e.call_side_expired and e.put_side_expired
        assert e.realized_pnl == pytest.approx(310.0)


# ======================================================================
# The third lock
# ======================================================================

class TestTheQuoteOutageFallback:
    """The base's dry-run fallback derives every leg from
    ``total_credit / (140 × contracts)``. H's credit is a truthful 0.0, so it
    marks both longs at ZERO — which is the right outcome, and was arrived at by
    accident until it was made deliberate."""

    def test_both_legs_are_marked_unpriced(self):
        s = _strat()
        e = _mark(_open(s), 2.00, 2.00)
        s._simulate_hydra_entry_prices(e)
        assert e.long_call_price == 0.0 and e.long_put_price == 0.0

    def test_no_target_can_fire_on_a_fabricated_mark(self):
        """A zeroed long is rejected by the sanity guard, so the tick is skipped
        rather than exiting at a price that was invented."""
        s = _strat()
        e = _mark(_open(s), 2.00, 2.00)          # would be +63%, a live target
        s._simulate_hydra_entry_prices(e)
        assert s._check_stop_losses() is None
        assert not getattr(e, "close_reason", "")

    def test_settlement_is_unaffected_because_it_reads_SPX(self):
        s = _strat()
        e = _open(s)
        s._simulate_hydra_entry_prices(e)
        booked, _ = s._settlement_booked_pnl(e, "call", 7800.0)
        assert booked == pytest.approx(15 * 100 * 2 - e.call_debit)

    def test_stale_marks_are_NOT_preferred_to_zeroed_ones(self):
        """Holding the last good mark is the worse failure: a stale +50% would
        exit at a price that no longer exists."""
        src = (ROOT / "bots" / "hydra" / "long_strangle_strategy.py").read_text()
        assert "a stale +50% would exit at a price that no longer exists" in src


class TestTheCloseRefusesOutsideDryRun:
    def test_a_live_close_raises(self):
        """Third independent lock, after __init__ and _execute_entry."""
        s = _strat()
        e = _open(s)
        s.dry_run = False
        with pytest.raises(ConfigError, match="no live close path"):
            s._close_long_strangle(e, "profit_target_50")

    def test_the_close_touches_no_broker_method(self):
        s = _strat()
        s.broker = MagicMock()
        s._place_option_order = MagicMock()
        e = _mark(_open(s), 2.00, 2.00)
        s._close_long_strangle(e, "profit_target_50")
        s._place_option_order.assert_not_called()
        s.broker.place_order.assert_not_called()

    def test_a_missing_recorder_does_not_break_the_close(self):
        s = _strat()
        s.ls_recorder = None
        e = _mark(_open(s), 2.00, 2.00)
        assert "closed" in s._close_long_strangle(e, "profit_target_50")


# ======================================================================
# Restart recovery — the shared state file cannot carry a debit
# ======================================================================

class TestRestartRecovery:
    """The shared state file serialises only credit-shaped fields and its
    restore path hardcodes ``HydraIronCondorEntry``. Without the repair below, a
    mid-day restart hands H back an entry of the wrong class with **no cost
    basis** — unmanageable (no percentage of a zero debit) and, before the guard
    in ``_settlement_booked_pnl``, an AttributeError inside the settlement sweep
    that would take down settlement for every entry that day.
    """

    def _restored_as_base(self, s):
        """What the base restore actually produces: right strikes, no debit."""
        from bots.hydra.strategy import HydraIronCondorEntry
        e = HydraIronCondorEntry(entry_number=1)
        e.long_call_strike, e.long_put_strike = 7785.0, 7745.0
        e.long_call_price, e.long_put_price = 1.60, 2.10
        e.contracts = 2
        s.daily_state.entries.append(e)
        return e

    def _row(self, **over):
        row = {"entry_number": 1, "call_debit": 200.0, "put_debit": 290.0,
               "contracts": 2, "call_strike": 7785.0, "put_strike": 7745.0,
               "long_call_uic": 111, "long_put_uic": 222,
               "em_source": "straddle", "expected_move": 22.0,
               "skew_gap_pct": 16.7}
        row.update(over)
        return row

    def test_the_debit_comes_back_from_H_s_OWN_database(self):
        """Not a sidecar: ls_entries already holds the cost basis, and the row is
        written before the position is ever monitored. Zero edits to the shared
        save/load that variant B trades on live."""
        s = _strat()
        self._restored_as_base(s)
        s.ls_recorder.fetch_entries.return_value = [self._row()]
        s._restore_long_strangle_entries()

        e = s.daily_state.entries[0]
        assert isinstance(e, LongStrangleEntry)
        assert e.total_debit == pytest.approx(490.0)
        assert e.ls_em_source == "straddle"

    def test_a_recovered_entry_is_manageable_again(self):
        """Before the repair the per-tick manager skipped it — it was not a
        LongStrangleEntry — so the profit target could never fire again."""
        s = _strat()
        self._restored_as_base(s)
        s.ls_recorder.fetch_entries.return_value = [self._row()]
        s._restore_long_strangle_entries()
        _mark(s.daily_state.entries[0], 2.00, 2.00)
        assert s._check_stop_losses() is not None

    def test_flags_and_ids_survive_the_class_change(self):
        s = _strat()
        old = self._restored_as_base(s)
        old.call_side_stopped = True
        old.strategy_id = "long_strangle_20260923_001"
        s.ls_recorder.fetch_entries.return_value = [self._row()]
        s._restore_long_strangle_entries()
        e = s.daily_state.entries[0]
        assert e.call_side_stopped is True
        assert e.strategy_id == "long_strangle_20260923_001"

    def test_a_missing_row_is_reported_CRITICAL_not_patched_over(self):
        """An entry with an unknown cost basis has no computable P&L. Inventing
        one would be worse than saying so."""
        s = _strat()
        self._restored_as_base(s)
        s.ls_recorder.fetch_entries.return_value = []
        s._restore_long_strangle_entries()
        assert not isinstance(s.daily_state.entries[0], LongStrangleEntry)

    def test_settlement_refuses_rather_than_crashing_on_a_costless_entry(self):
        """The guard that matters: an AttributeError here would take down
        settlement for EVERY entry that day, not just this one."""
        s = _strat()
        e = self._restored_as_base(s)
        booked, worthless = s._settlement_booked_pnl(e, "call", 7800.0)
        assert booked == 0.0 and worthless is False

    def test_an_already_correct_entry_is_left_alone(self):
        s = _strat()
        e = _open(s)
        s._restore_long_strangle_entries()
        assert s.daily_state.entries[0] is e
        s.ls_recorder.fetch_entries.assert_not_called()

    def test_recovery_never_blocks_startup(self):
        s = _strat()
        self._restored_as_base(s)
        s.ls_recorder.fetch_entries.side_effect = RuntimeError("db gone")
        with pytest.raises(RuntimeError):
            s._restore_long_strangle_entries()          # raises here...
        # ...but the caller swallows it, which is the property that matters.
        src = (ROOT / "bots" / "hydra" / "long_strangle_strategy.py").read_text()
        assert "LS: restart recovery failed (non-fatal)" in src


# ======================================================================
# Step 9 — the EOD flatten is declined on purpose, not by accident
# ======================================================================

class TestTheEODFlattenIsDeclinedDeliberately:
    """MKT-047 force-closes open 0DTE SHORTS near the cutoff so a late breach
    cannot ride to max loss in the un-closable final minutes. H has no shorts
    and no such tail — its worst case is the debit, already paid.

    It was already being skipped, but only by ACCIDENT: the base gates on
    `requires_protective_wings`, which H sets for a different reason and the
    calendars set for a third. Three unrelated rationales resolving to one flag
    is precisely what breaks silently when someone changes one of them.
    """

    def test_it_never_flattens(self):
        s = _strat()
        _mark(_open(s), 2.00, 2.00)
        assert s._check_eod_flatten() is None

    def test_it_does_not_depend_on_the_calendars_flag_to_do_so(self):
        """Flip the flag the base actually gates on: H must still decline."""
        s = _strat()
        s.requires_protective_wings = True
        _mark(_open(s), 2.00, 2.00)
        assert s._check_eod_flatten() is None

    def test_the_trade_off_is_recorded_rather_than_left_implicit(self):
        """Holding forfeits remaining extrinsic. That is a choice with a cost,
        and the cost is named so a dry-run result can overturn it."""
        src = (ROOT / "bots" / "hydra" / "long_strangle_strategy.py").read_text()
        assert "THE TRADE-OFF, STATED" in src
        # Matched without spanning a line wrap.
        assert "capture whatever extrinsic" in src
        assert "meaningful extrinsic being given up" in src

    def test_settlement_still_books_the_position(self):
        """Declining to flatten is only safe because expiry books at intrinsic."""
        s = _strat()
        e = _open(s)
        booked, _ = s._settlement_booked_pnl(e, "call", 7800.0)
        assert booked == pytest.approx(15 * 100 * 2 - e.call_debit)


class TestTheRecorderExistsBeforeTheBaseInitRuns:
    """Caught on H's first real restart, 2026-09-23 09:55 ET:

        ERROR | LS: restart recovery failed (non-fatal):
                'LongStrangleStrategy' object has no attribute 'ls_recorder'

    The base `__init__` calls `_load_state_file_history` →
    `_restore_long_strangle_entries`, which reads `self.ls_recorder` to recover
    a restarted position's cost basis. The recorder was created AFTER
    `super().__init__()`, so the attribute did not exist yet and recovery never
    ran. **The Step 5 mechanism was dead on arrival** — and it took an actual
    restart to reveal it, because the unit tests constructed the object with
    `__new__` and set the attribute by hand.
    """

    def test_the_recorder_is_assigned_before_super_init(self):
        """Source-order check. A behavioural test cannot catch this: every other
        test bypasses `__init__` entirely via `__new__`, which is exactly why
        the bug survived to production."""
        src = (ROOT / "bots" / "hydra" / "long_strangle_strategy.py").read_text()
        body = src.split("def __init__", 1)[1].split("\n    def ", 1)[0]
        # The exact CALL, not the docstring's prose mention of it — the first
        # version of this test matched the docstring and passed vacuously.
        call = "super().__init__(*args, **kwargs)"
        assign = "self.ls_recorder: Optional[LongStrangleDataRecorder] = None"
        assert assign in body and call in body
        assert body.index(assign) < body.index(call), \
            "the recorder must be created BEFORE super().__init__ — recovery " \
            "runs inside it"

    def test_recovery_survives_a_missing_attribute_loudly(self, caplog):
        """Belt-and-braces: an ordering regression must produce a CRITICAL, not
        an AttributeError swallowed by the caller's except."""
        import logging
        s = _strat()
        del s.ls_recorder
        from bots.hydra.strategy import HydraIronCondorEntry
        e = HydraIronCondorEntry(entry_number=1)
        e.long_call_strike = 7785.0
        s.daily_state.entries.append(e)
        with caplog.at_level(logging.CRITICAL):
            s._restore_long_strangle_entries()          # must not raise
        assert any("no recorder to recover it from" in r.message
                   for r in caplog.records)

    def test_the_ordering_reason_is_recorded_in_the_code(self):
        src = (ROOT / "bots" / "hydra" / "long_strangle_strategy.py").read_text()
        assert "dead on arrival" in src
        assert "CREATED **BEFORE** ``super().__init__()``" in src
