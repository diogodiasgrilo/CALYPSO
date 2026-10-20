"""One orphan sent FOUR identical CRITICAL emails in four hours (2026-10-19).

`_reconcile_orphan_sweep` alerted unconditionally on every detection. But an
orphan is **deliberately not auto-closed** ("could be a manual position —
MANUAL REVIEW"), so the condition PERSISTS by design and the hourly sweep
re-detects it forever. On 2026-10-19 conid 929372783 (+1 LONG, created by the
A2 over-close since fixed in 460ae3f3) alerted at 12:32, 13:32, 14:32 and
15:32 ET. It stopped only because the contract — a 0DTE SPXW call — expired
that afternoon, realizing +$1.92. Had it been anything longer-dated it would
have emailed ~6x/day indefinitely.

CRITICAL is the ONLY priority that sends email (`_should_send_email`), so an
unchanged "MANUAL REVIEW" repeating on the hour is exactly how an operator is
trained to ignore the one tier that matters.

⚠️ The cadence was NOT a runaway loop: RECONCILIATION_INTERVAL_MINUTES = 60 and
the timestamps are exactly hourly. An earlier pass over this incident reported
"18 alerts" from `grep -c orphan`, which counts log lines and the
BROKER-RECONCILE leg listing too. The true alert count is 4. The gate works;
the de-dup was simply absent.

Fix: alert once per distinct orphan SET, re-arming when the set changes, when
it clears, or on a new trading day. The LOG line stays unconditional — it is
the forensic trail that reconstructed this incident.
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


def _make(orphans):
    """A HydraStrategy stubbed to exactly the surface the sweep touches."""
    s = HydraStrategy.__new__(HydraStrategy)
    s.BOT_NAME = "HYDRA_BL"
    s.contracts_per_entry = 7
    s.alert_service = MagicMock()
    s._recon_detect_orphans = lambda e, a: dict(orphans)
    s._recon_suppress_recent_closes = lambda o, now: dict(o)
    return s


@pytest.fixture
def at(monkeypatch):
    """Control the clock the sweep reads."""
    def _set(day="2026-10-19", h=12, m=32):
        y, mo, d = (int(x) for x in day.split("-"))
        monkeypatch.setattr(
            strat_mod, "get_us_market_time", lambda: _dt.datetime(y, mo, d, h, m))
    return _set


ORPHAN = {929372783: 1}
SWEEP_HOURS = (12, 13, 14, 15)  # the four real 2026-10-19 reconciliations


class TestTheIncident:
    def test_the_first_detection_alerts(self, at):
        at(h=12)
        s = _make(ORPHAN)
        assert s._reconcile_orphan_sweep({}, {}) == 1
        assert s.alert_service.send_alert.call_count == 1

    def test_four_hourly_sweeps_send_ONE_alert_not_four(self, at):
        """The regression: 2026-10-19's exact sequence."""
        s = _make(ORPHAN)
        for h in SWEEP_HOURS:
            at(h=h)
            assert s._reconcile_orphan_sweep({}, {}) == 1  # still reported
        assert s.alert_service.send_alert.call_count == 1

    def test_the_alert_that_IS_sent_is_still_CRITICAL(self, at):
        at(h=12)
        s = _make(ORPHAN)
        s._reconcile_orphan_sweep({}, {})
        kw = s.alert_service.send_alert.call_args.kwargs
        assert kw["priority"] is strat_mod.AlertPriority.CRITICAL
        assert "929372783" in str(kw["details"])

    def test_the_LOG_still_fires_every_sweep(self, at, caplog):
        """De-dup must not thin the forensic trail that solved this."""
        import logging
        s = _make(ORPHAN)
        with caplog.at_level(logging.INFO):
            for h in SWEEP_HOURS:
                at(h=h)
                s._reconcile_orphan_sweep({}, {})
        crit = [r for r in caplog.records
                if r.levelno == logging.CRITICAL and "ORPHAN broker position" in r.message]
        assert len(crit) == 4, "every detection must still be logged CRITICAL"
        assert s.alert_service.send_alert.call_count == 1


class TestItReArms:
    def test_a_CHANGED_orphan_set_alerts_again(self, at):
        at(h=12)
        s = _make(ORPHAN)
        s._reconcile_orphan_sweep({}, {})
        at(h=13)
        s._recon_detect_orphans = lambda e, a: {929372783: 1, 111111111: -2}
        s._reconcile_orphan_sweep({}, {})
        assert s.alert_service.send_alert.call_count == 2

    def test_a_changed_QUANTITY_at_the_same_conid_alerts_again(self, at):
        """-1 becoming -5 is new information even though the conid is the same."""
        at(h=12)
        s = _make({929372783: -1})
        s._reconcile_orphan_sweep({}, {})
        at(h=13)
        s._recon_detect_orphans = lambda e, a: {929372783: -5}
        s._reconcile_orphan_sweep({}, {})
        assert s.alert_service.send_alert.call_count == 2

    def test_a_NEW_TRADING_DAY_resurfaces_a_persistent_orphan(self, at):
        at(day="2026-10-19", h=15)
        s = _make(ORPHAN)
        s._reconcile_orphan_sweep({}, {})
        at(day="2026-10-20", h=12)
        s._reconcile_orphan_sweep({}, {})
        assert s.alert_service.send_alert.call_count == 2, "once per day, not once ever"

    def test_clearing_then_RETURNING_alerts_again(self, at):
        at(h=12)
        s = _make(ORPHAN)
        s._reconcile_orphan_sweep({}, {})
        at(h=13)
        s._recon_detect_orphans = lambda e, a: {}
        assert s._reconcile_orphan_sweep({}, {}) == 0
        at(h=14)
        s._recon_detect_orphans = lambda e, a: dict(ORPHAN)
        s._reconcile_orphan_sweep({}, {})
        assert s.alert_service.send_alert.call_count == 2

    def test_a_FAILED_send_does_not_latch(self, at):
        """A swallowed delivery error must not silently consume the only alert."""
        at(h=12)
        s = _make(ORPHAN)
        s.alert_service.send_alert.side_effect = RuntimeError("pubsub down")
        s._reconcile_orphan_sweep({}, {})
        s.alert_service.send_alert.side_effect = None
        at(h=13)
        s._reconcile_orphan_sweep({}, {})
        assert s.alert_service.send_alert.call_count == 2

    def test_the_daily_reset_clears_the_latch(self):
        """_reset_for_new_day is the second, explicit re-arm mechanism."""
        import inspect
        src = inspect.getsource(HydraStrategy._reset_for_new_day)
        assert "_orphan_alert_latch = None" in src


class TestNegativeControl:
    def test_reintroducing_the_defect_reproduces_FOUR_alerts(self, at):
        """Proves the suite above is not vacuous.

        Faithfully reinstates the old behaviour by making the latch unstorable,
        so the de-dup comparison can never match — exactly what the code did
        before. If this does not produce 4, the tests above prove nothing.
        """
        class Undeduped(HydraStrategy):
            _orphan_alert_latch = property(lambda self: None, lambda self, v: None)

        s = Undeduped.__new__(Undeduped)
        s.BOT_NAME = "HYDRA_BL"
        s.contracts_per_entry = 7
        s.alert_service = MagicMock()
        s._recon_detect_orphans = lambda e, a: dict(ORPHAN)
        s._recon_suppress_recent_closes = lambda o, now: dict(o)

        for h in SWEEP_HOURS:
            at(h=h)
            s._reconcile_orphan_sweep({}, {})
        assert s.alert_service.send_alert.call_count == 4, (
            "the defect must reproduce, or the de-dup tests are vacuous")
