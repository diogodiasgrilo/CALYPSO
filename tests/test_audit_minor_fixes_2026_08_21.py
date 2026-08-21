"""2026-08-21 (full-day execution audit LOW/INFO findings, fixed same day):

FIX 1 — stop-loss "executed" summary line showed the static/undecayed stop
level instead of the actual MKT-042-decayed trigger that caused the breach
(e.g. logged $317.50 when the real firing trigger, shown correctly one line
above in the STOP-DETAIL/MKT-046 output, was $405.76). Display-only — DB,
state file, and P&L math were already correct (a prior 2026-08-20 fix
already recorded the decayed value into trade_stops.trigger_level via a new
``effective_trigger_level`` DB-only parameter; this fix threads that same
value into the human-facing summary line too, via a new
``display_trigger_level`` parameter on the base ``_execute_stop_loss``).

FIX 2 — ``_estimate_entry_credit_ib`` returned unrounded floats, so an
economically-exact credit (e.g. mid 0.15 - mid 0.10 = $0.05/share = $5.00
total) could land as a float artifact like 4.999999999999993 and miss a
credit-gate threshold it should clear by noise alone. Fixed by rounding to
the cent at the source, mirroring the pre-existing rounding already applied
to the MKT-048 fillable-credit stash a few lines below.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bots.hydra.base_strategy import MEICStrategy
import bots.hydra.base_strategy as base_mod
from bots.hydra.strategy import HydraStrategy
from shared.market_hours import get_us_market_time


def _entry(**kw):
    e = base_mod.IronCondorEntry(entry_number=1)
    e.contracts = 7
    for k, v in kw.items():
        setattr(e, k, v)
    return e


# ─────────────── Fix 1: stop-loss display_trigger_level ───────────────

class TestBaseExecuteStopLossDisplayTriggerLevel:
    """Direct test of the new parameter on MEICStrategy._execute_stop_loss
    (base_strategy.py) — the mechanism HydraStrategy's override wires up.
    dry_run=True with no price data on the entry so the function takes the
    simplest theoretical-P&L path (no real close, no threading)."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = True
        s.commission_per_leg = 1.15
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        s._log_stop_loss = MagicMock()
        s._queue_stop_alert = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._flush_batched_alerts = MagicMock()
        return s

    def _entry(self, **kw):
        return _entry(
            put_side_stop=1575.0, call_side_stop=1575.0,
            put_spread_credit=175.0, call_spread_credit=175.0,
            short_put_ask=0, long_put_bid=0, short_put_price=0,
            short_call_ask=0, long_call_bid=0, short_call_price=0,
            actual_put_stop_debit=0.0, actual_call_stop_debit=0.0,
            **kw,
        )

    def test_default_omitted_uses_static_stop_level(self):
        s = self._strat()
        e = self._entry()

        result = MEICStrategy._execute_stop_loss(s, e, "put")

        assert "$1575.00" in result

    def test_explicit_override_shows_in_summary_line(self):
        s = self._strat()
        e = self._entry()

        result = MEICStrategy._execute_stop_loss(
            s, e, "put", display_trigger_level=2950.00,
        )

        assert "$2950.00" in result
        assert "$1575.00" not in result

    def test_override_does_not_change_booked_pnl(self):
        """display_trigger_level is display-only — net_loss/total_realized_pnl
        must still be computed from the static stop_level, exactly as before
        this fix (theoretical net_loss = stop_level - credit = 1575-175=1400)."""
        s = self._strat()
        e = self._entry()

        MEICStrategy._execute_stop_loss(s, e, "put", display_trigger_level=2950.00)

        assert s.daily_state.total_realized_pnl == pytest.approx(-1400.0)


class TestHydraExecuteStopLossPassesDecayedDisplayLevel:
    """End-to-end through the REAL (unmocked) HydraStrategy._execute_stop_loss
    override + the REAL base _execute_stop_loss — reproduces the actual
    2026-08-21 audit finding on variant A: with MKT-042 decay active, the
    final summary line must show the decayed trigger, not the static base
    level."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = True
        s.short_only_stop = False
        s.stop_confirmation_enabled = False
        s.narrow_spread_stop_enabled = False  # A2 off — credit+buffer decay path
        s.buffer_decay_start_mult = 2.5
        s.buffer_decay_hours = 4.0
        s.call_stop_buffer = 0.75
        s.put_stop_buffer = 1.75
        s.commission_per_leg = 1.15
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        s._log_stop_loss = MagicMock()
        s._queue_stop_alert = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._flush_batched_alerts = MagicMock()
        s._record_stop_to_db = MagicMock()
        return s

    def _entry(self, **kw):
        return _entry(
            put_side_stop=1575.0, call_side_stop=1575.0,
            put_spread_credit=175.0, call_spread_credit=175.0,
            short_put_ask=0, long_put_bid=0, short_put_price=0,
            short_call_ask=0, long_call_bid=0, short_call_price=0,
            actual_put_stop_debit=0.0, actual_call_stop_debit=0.0,
            long_call_strike=7695, short_call_strike=7690,
            long_put_strike=7690, short_put_strike=7695,
            entry_time=get_us_market_time() - timedelta(hours=1),  # decay active
            **kw,
        )

    def test_summary_line_shows_decayed_trigger_not_static(self):
        s = self._strat()
        e = self._entry()

        result = s._execute_stop_loss(e, "put")

        effective = s._get_effective_stop_level(e, "put")
        assert effective != pytest.approx(e.put_side_stop), (
            "test setup bug: decay isn't actually active, this test proves nothing"
        )
        assert f"${effective:.2f}" in result
        assert f"${e.put_side_stop:.2f}" not in result

    def test_no_decay_configured_summary_line_matches_static(self):
        """When decay is off, effective == static, so this is a no-behavior-
        change check — the summary line still correctly shows that (equal)
        value either way."""
        s = self._strat()
        s.buffer_decay_start_mult = None
        e = self._entry()

        result = s._execute_stop_loss(e, "put")

        assert f"${e.put_side_stop:.2f}" in result

    def test_db_write_still_receives_same_effective_trigger_level(self):
        """Regression guard: this fix must not disturb the existing
        2026-08-20 DB-recording fix it's built alongside."""
        s = self._strat()
        e = self._entry()

        s._execute_stop_loss(e, "put")

        effective = s._get_effective_stop_level(e, "put")
        kwargs = s._record_stop_to_db.call_args.kwargs
        assert kwargs["effective_trigger_level"] == pytest.approx(effective)


class TestHydraMkt025ShortOnlyDisplayLevel:
    """The sibling MKT-025 short-only-stop branch (currently dormant —
    short_only_stop=false on every tracked config) had the identical bug in
    its own, separately-implemented final summary line — fixed for
    completeness, matching the round-2-review precedent already applied to
    this exact function's DB-write path."""

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = True
        s.short_only_stop = True
        s.stop_confirmation_enabled = False
        s.narrow_spread_stop_enabled = False
        s.buffer_decay_start_mult = 2.5
        s.buffer_decay_hours = 4.0
        s.call_stop_buffer = 0.75
        s.put_stop_buffer = 1.75
        s.long_salvage_enabled = False
        s.commission_per_leg = 1.15
        s.daily_state = base_mod.MEICDailyState()
        s.daily_state.total_realized_pnl = 0.0
        s.daily_state.total_commission = 0.0
        s._log_stop_loss = MagicMock()
        s._queue_stop_alert = MagicMock()
        s._save_state_to_disk = MagicMock()
        s._flush_batched_alerts = MagicMock()
        s._record_stop_to_db = MagicMock()
        return s

    def _entry(self, **kw):
        return _entry(
            put_side_stop=1575.0, call_side_stop=1575.0,
            put_spread_credit=175.0, call_spread_credit=175.0,
            short_put_ask=0, short_put_price=0,
            short_call_ask=0, short_call_price=0,
            entry_time=get_us_market_time() - timedelta(hours=1),
            **kw,
        )

    def test_mkt025_summary_line_shows_decayed_trigger(self):
        s = self._strat()
        e = self._entry()

        result = s._execute_stop_loss(e, "put")

        effective = s._get_effective_stop_level(e, "put")
        assert effective != pytest.approx(e.put_side_stop)
        assert f"${effective:.2f}" in result
        assert f"${e.put_side_stop:.2f}" not in result
        assert result.startswith("MKT-025 Stop loss:")


# ─────────────── Fix 2: _estimate_entry_credit_ib rounding ───────────────

class TestEstimateEntryCreditIbRounding:
    """Direct test of the credit-rounding fix. Mocks _read_option_chain +
    _read_option_quotes_batch + _quote_mid to control the exact mid prices
    that produce a float-precision artifact, matching the real
    2026-08-21 incident (variant C, put credit computed as
    4.999999999999993 instead of a clean 5.0)."""

    def _strat(self, *, call_mid=(0.0, 0.0), put_mid=(0.15, 0.10)):
        """call_mid/put_mid = (short_mid, long_mid) per side."""
        s = HydraStrategy.__new__(HydraStrategy)
        s._get_todays_expiry = MagicMock(return_value="2026-08-21")
        # Two fake conids per side so _read_option_chain's lookups resolve.
        call_map = {7715.0: "sc", 7720.0: "lc"}
        put_map = {7590.0: "sp", 7585.0: "lp"}
        s._read_option_chain = MagicMock(return_value=(call_map, put_map))
        mids = {"sc": call_mid[0], "lc": call_mid[1], "sp": put_mid[0], "lp": put_mid[1]}
        # Every conid must appear in the quotes dict (else _quoted() is False).
        s._read_option_quotes_batch = MagicMock(
            return_value={cid: {"bid": mids[cid], "ask": mids[cid]} for cid in mids}
        )
        s._quote_mid = MagicMock(side_effect=lambda q: q["bid"] if q else 0.0)
        return s

    def _entry(self):
        return _entry(
            short_call_strike=7715.0, long_call_strike=7720.0,
            short_put_strike=7590.0, long_put_strike=7585.0,
            put_only=False, call_only=False,
        )

    def test_float_artifact_credit_rounds_to_clean_cents(self):
        # 0.15 - 0.10 in binary float = 0.04999999999999999 -> *100 =
        # 4.999999999999993, reproducing the exact real-incident artifact.
        raw_artifact = (0.15 - 0.10) * 100
        assert raw_artifact != 5.0, "float artifact didn't reproduce — test premise invalid"

        s = self._strat(call_mid=(0.10, 0.10), put_mid=(0.15, 0.10))
        e = self._entry()

        est_call, est_put = s._estimate_entry_credit_ib(e)

        assert est_put == pytest.approx(5.0)
        assert est_put == 5.0  # exact — proves it's genuinely rounded, not just close

    def test_clean_values_unaffected(self):
        s = self._strat(call_mid=(0.20, 0.05), put_mid=(0.30, 0.10))
        e = self._entry()

        est_call, est_put = s._estimate_entry_credit_ib(e)

        assert est_call == pytest.approx(15.0)
        assert est_put == pytest.approx(20.0)

    def test_rounded_credit_correctly_clears_a_threshold_it_would_have_missed(self):
        """The actual real-world consequence: a credit gate comparing
        est_put >= 5.0 must now pass instead of spuriously failing."""
        s = self._strat(call_mid=(0.10, 0.10), put_mid=(0.15, 0.10))
        e = self._entry()

        _, est_put = s._estimate_entry_credit_ib(e)

        assert est_put >= 5.0  # would have been 4.999999999999993 < 5.0 pre-fix
