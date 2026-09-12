"""
The one P&L check in this codebase that is NOT circular (2026-09-10).

WHY. Every other reconciliation compares two numbers descended from the SAME
accumulator: `_book_realized_pnl` increments `daily_state.total_realized_pnl`
and `entry.realized_pnl` in one statement pair, and `daily_summaries.gross_pnl`
derives from that same total. They cannot disagree by construction, so they can
only catch a booking site that forgot `entry=`. A WRONG booked amount, a MISSING
booking and a DOUBLE booking are all invisible to them — and all three have
bitten this codebase.

IBKR computes its own realized P&L (`raw_ledger.USD.realizedpnl`). That shares
no code path with ours, so it can catch all three.

EXECUTIONS WERE THE FIRST CHOICE AND DO NOT WORK: probed 2026-09-10,
/iserver/account/trades returned ZERO records over a 7-day window containing
dozens of real paper fills, with no error. The ledger is what this paper account
actually exposes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _s(broker_realized=None, gross=1000.0, commission=100.0, dry_run=False,
       raise_on_call=False):
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = dry_run
    s.daily_state = SimpleNamespace(total_realized_pnl=gross,
                                    total_commission=commission)
    ledger = {} if broker_realized is None else {
        "realizedpnl": broker_realized, "netliquidationvalue": 1009439.8}
    s.broker = MagicMock()
    if raise_on_call:
        s.broker.get_balance.side_effect = RuntimeError("broker down")
    else:
        s.broker.get_balance.return_value = {"raw_ledger": {"USD": ledger}}
    return s


class TestIndependentCheck:
    def test_reports_drift_against_both_gross_and_net(self):
        """Both, because it is NOT established whether IBKR's realizedpnl is
        net of commission. Reporting one would silently pick a convention."""
        r = HydraStrategy._reconcile_pnl_against_broker(
            _s(broker_realized=900.0, gross=1000.0, commission=100.0), "2026-09-10")
        assert r["broker_realized_pnl"] == 900.0
        assert r["drift_vs_gross"] == pytest.approx(100.0)
        assert r["drift_vs_net"] == pytest.approx(0.0)

    def test_it_would_catch_a_MISSING_booking(self):
        """The failure the circular checks cannot see: our total is short by a
        whole trade, and both sides of the in-process identity are short
        together, so only the broker notices."""
        r = HydraStrategy._reconcile_pnl_against_broker(
            _s(broker_realized=1500.0, gross=1000.0, commission=0.0), "2026-09-10")
        assert r["drift_vs_gross"] == pytest.approx(-500.0)

    def test_it_would_catch_a_DOUBLE_booking(self):
        r = HydraStrategy._reconcile_pnl_against_broker(
            _s(broker_realized=500.0, gross=1000.0, commission=0.0), "2026-09-10")
        assert r["drift_vs_gross"] == pytest.approx(500.0)

    def test_a_clean_day_shows_zero_drift(self):
        r = HydraStrategy._reconcile_pnl_against_broker(
            _s(broker_realized=1000.0, gross=1000.0, commission=0.0), "2026-09-10")
        assert r["drift_vs_gross"] == 0.0


class TestScopeAndSafety:
    def test_DRY_RUN_variants_are_skipped(self):
        """A dry-run variant places no orders, so the broker's realized P&L
        reflects OTHER variants. Comparing a simulated P&L against it would
        alarm on every single close."""
        s = _s(broker_realized=900.0, dry_run=True)
        r = HydraStrategy._reconcile_pnl_against_broker(s, "2026-09-10")
        assert r == {"skipped": "dry_run"}
        assert s.broker.get_balance.call_count == 0

    def test_a_missing_realizedpnl_field_is_skipped_not_treated_as_zero(self):
        """Absent must not read as 'IBKR says you made $0', which would report
        a drift equal to the entire day's P&L."""
        r = HydraStrategy._reconcile_pnl_against_broker(
            _s(broker_realized=None), "2026-09-10")
        # The REASON matters: dropping this guard makes float(None) raise and
        # land in the error handler, which an `is None` assertion cannot tell
        # apart. An earlier version of this test passed against exactly that.
        assert r == {"skipped": "no_realizedpnl_field"}

    def test_a_broker_failure_never_disturbs_settlement(self):
        """It is a diagnostic. It must not be able to break the close."""
        r = HydraStrategy._reconcile_pnl_against_broker(
            _s(raise_on_call=True), "2026-09-10")
        assert r["skipped"] == "error" and "broker down" in r["error"]

    def test_a_zero_broker_realized_is_still_REPORTED(self):
        """0.0 is a legitimate value (a flat day) and must be distinguished
        from the missing-field case above — a truthiness check would conflate
        them and silently skip real flat days."""
        r = HydraStrategy._reconcile_pnl_against_broker(
            _s(broker_realized=0.0, gross=0.0, commission=0.0), "2026-09-10")
        assert r is not None and r["broker_realized_pnl"] == 0.0


class TestWiredIntoSettlement:
    def test_settlement_calls_it(self):
        import inspect
        src = inspect.getsource(HydraStrategy._record_daily_summary_to_db)
        assert "_reconcile_pnl_against_broker" in src

    def test_it_runs_AFTER_the_in_process_reconcile(self):
        """It reads daily_state, so it must run once the day's P&L is final."""
        import inspect
        src = inspect.getsource(HydraStrategy._record_daily_summary_to_db)
        assert (src.index("_reconcile_cumulative_metrics_from_db")
                < src.index("_reconcile_pnl_against_broker"))
