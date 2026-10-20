"""POS-003's position-mismatch alert had the same unlatched shape as the orphan.

Found 2026-10-20 while auditing the alert paths after the orphan incident.
`_check_hourly_reconciliation` alerted on every detection of a quantity
mismatch, and `_handle_position_discrepancies` explicitly "STILL REFUSES TO
GUESS" — ties and quantities no subset can explain fall through to MANUAL
REVIEW. So an unresolvable mismatch persists and would re-alert every hour,
forever, exactly as the orphan did.

It is less severe than the orphan only because its priority is HIGH, and
email is CRITICAL-only since 2026-08-28 — so it floods Telegram, not the
inbox. The bug class is identical.

⚠️ The shared anti-spam gate cannot catch either one: its CRITICAL
content-dedup window is 300s against RECONCILIATION_INTERVAL_MINUTES = 60.
Hourly repeats always land outside the window, so the gate's claim that "no
matter what loop or retry storm fires upstream, the inbox can never be flooded
again" holds for FAST loops only.

THE LOAD-BEARING INVARIANT, pinned below: the latch suppresses the
NOTIFICATION, never the RECOVERY. `_handle_position_discrepancies` must still
run on every suppressed cycle, or de-dup would stop the bot repairing itself.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import bots.hydra.strategy as strat_mod  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402

CONID = 929372783
MISMATCH = {CONID: (7, 14)}  # the real 2026-10-12 shape: expected 7, broker 14


def _make(discrepant):
    s = HydraStrategy.__new__(HydraStrategy)
    # Reconciliation is skipped outright in dry-run (DRY_* ids never exist at
    # the broker), so POS-003 only ever runs on the LIVE seat.
    s.dry_run = False
    s.BOT_NAME = "HYDRA_BL"
    s.contracts_per_entry = 7
    s.alert_service = MagicMock()
    s._handle_position_discrepancies = MagicMock()
    s._save_state_to_disk = MagicMock()
    s._reconcile_orphan_sweep = MagicMock(return_value=0)
    s._expected_position_quantities = lambda: {CONID: 7}
    s._read_open_positions = lambda: [{"instrument_id": CONID, "quantity": 14}]
    s._actual_position_quantities = lambda pos: {CONID: 14}
    s._recon_diff_quantities = staticmethod(lambda e, a: dict(discrepant))
    s._recon_detect_orphans = lambda e, a: {}
    s._recon_suppress_recent_closes = lambda o, now: dict(o)
    s._recon_should_defer = lambda has, confirm: False
    s._recon_recheck_at = None
    s._last_reconciliation_time = None
    return s


@pytest.fixture
def at(monkeypatch):
    def _set(day="2026-10-19", h=12, m=32):
        y, mo, d = (int(x) for x in day.split("-"))
        now = _dt.datetime(y, mo, d, h, m)
        monkeypatch.setattr(strat_mod, "get_us_market_time", lambda: now)
        # imported INSIDE _check_hourly_reconciliation from base_strategy —
        # patching the strategy module's name would have no effect.
        import bots.hydra.base_strategy as base_mod
        monkeypatch.setattr(base_mod, "is_market_open", lambda: True)
        return now
    return _set


def _sweep(s):
    """Drive one reconciliation, bypassing the hourly gate."""
    s._last_reconciliation_time = None
    s._check_hourly_reconciliation()


SWEEP_HOURS = (12, 13, 14, 15)


class TestTheBugClass:
    def test_first_mismatch_alerts(self, at):
        at(h=12)
        s = _make(MISMATCH)
        _sweep(s)
        assert s.alert_service.send_alert.call_count == 1

    def test_four_hourly_sweeps_send_ONE_alert(self, at):
        s = _make(MISMATCH)
        for h in SWEEP_HOURS:
            at(h=h)
            _sweep(s)
        assert s.alert_service.send_alert.call_count == 1

    def test_RECOVERY_still_runs_on_every_suppressed_cycle(self, at):
        """The load-bearing invariant: de-dup must not stop self-repair."""
        s = _make(MISMATCH)
        for h in SWEEP_HOURS:
            at(h=h)
            _sweep(s)
        assert s._handle_position_discrepancies.call_count == 4, (
            "resolution must be attempted every cycle, alert or not")
        assert s.alert_service.send_alert.call_count == 1


class TestItReArms:
    def test_a_changed_quantity_alerts_again(self, at):
        at(h=12)
        s = _make(MISMATCH)
        _sweep(s)
        at(h=13)
        s._recon_diff_quantities = staticmethod(lambda e, a: {CONID: (7, 21)})
        _sweep(s)
        assert s.alert_service.send_alert.call_count == 2

    def test_a_new_trading_day_resurfaces_it(self, at):
        at(day="2026-10-19", h=15)
        s = _make(MISMATCH)
        _sweep(s)
        at(day="2026-10-20", h=12)
        _sweep(s)
        assert s.alert_service.send_alert.call_count == 2

    def test_clearing_then_returning_alerts_again(self, at):
        at(h=12)
        s = _make(MISMATCH)
        _sweep(s)
        at(h=13)
        s._recon_diff_quantities = staticmethod(lambda e, a: {})
        _sweep(s)
        at(h=14)
        s._recon_diff_quantities = staticmethod(lambda e, a: dict(MISMATCH))
        _sweep(s)
        assert s.alert_service.send_alert.call_count == 2

    def test_a_failed_send_does_not_latch(self, at):
        at(h=12)
        s = _make(MISMATCH)
        s.alert_service.send_alert.side_effect = RuntimeError("pubsub down")
        _sweep(s)
        s.alert_service.send_alert.side_effect = None
        at(h=13)
        _sweep(s)
        assert s.alert_service.send_alert.call_count == 2


class TestNegativeControl:
    def test_reintroducing_the_defect_reproduces_FOUR_alerts(self, at):
        """Proves the suite is not vacuous."""
        class Undeduped(HydraStrategy):
            _mismatch_alert_latch = property(lambda self: None, lambda self, v: None)

        s = _make(MISMATCH)
        s.__class__ = Undeduped
        for h in SWEEP_HOURS:
            at(h=h)
            _sweep(s)
        assert s.alert_service.send_alert.call_count == 4, (
            "the defect must reproduce, or the de-dup tests prove nothing")
