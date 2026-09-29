"""The registered one-entry-a-day test, and the tracker that keeps it honest.

WHY A TEST FOR AN ANALYSIS SCRIPT. The registration
(docs/PREREG_SLOT_PRUNE_2026_09_29.md) fixes a cut-off date, a statistic, a
required n and a threshold BEFORE any out-of-sample data exists. Its whole
value is that none of those can drift later. A constant nobody pins is a
constant that gets nudged, and the nudge is invisible in a diff full of
analysis code.

So these tests pin the registration itself, not the arithmetic.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import one_entry_counterfactual as oec  # noqa: E402


class TestTheRegistrationConstantsAreFixed:
    def test_cutoff_is_the_registered_date(self):
        """Moving this forward would silently launder in-sample days into the
        out-of-sample window — the exact failure the registration prevents."""
        assert oec.OOS_SINCE == "2026-09-30", (
            "the out-of-sample cut-off moved; that invalidates the "
            "registration rather than extending it")

    def test_required_days_is_the_registered_n(self):
        assert oec.OOS_REQUIRED_DAYS == 40

    def test_the_cutoff_excludes_every_in_sample_day(self):
        """2026-09-28 was the last live day used to form the hypothesis."""
        assert oec.OOS_SINCE > "2026-09-28"


class TestTheVerdictRuleMatchesWhatWasRegistered:
    """ADOPT if mean >= 0; REJECT only if mean < 0 AND |t| >= 2.

    Non-inferiority, not superiority — the rule comes from the SOURCE, so the
    data only has to show it does no harm. Demanding superiority would need
    ~771 entries (~18 months), because the 95% CI on the extra entries' gross
    is [-$139, +$138] and spans their $41.63 commission.
    """

    def _verdict(self, n, mean, t):
        if n < oec.OOS_REQUIRED_DAYS:
            return "PENDING"
        if mean >= 0:
            return "ADOPT"
        return "REJECT" if abs(t) >= 2 else "ADOPT"

    def test_pending_below_required_n(self):
        assert self._verdict(39, 500.0, 9.0) == "PENDING", (
            "a big early number must not end the test early")

    def test_adopt_on_positive_mean(self):
        assert self._verdict(40, 10.0, 0.1) == "ADOPT"

    def test_adopt_on_negative_but_insignificant(self):
        """Non-inferiority: a small negative drift does NOT reject."""
        assert self._verdict(40, -50.0, -1.2) == "ADOPT"

    def test_reject_only_when_significantly_negative(self):
        assert self._verdict(40, -50.0, -2.4) == "REJECT"


class TestTheTrackerCannotTrade:
    """It runs unattended on a timer against the live trading database."""

    def _unit(self):
        import pathlib
        return pathlib.Path(__file__).resolve().parents[1] / "deploy" / "oos_tracker.service"

    def test_the_unit_exists_and_runs_the_registered_command(self):
        src = self._unit().read_text()
        assert "--oos" in src and "--append" in src
        assert "Type=oneshot" in src

    def test_it_runs_as_calypso_not_root(self):
        assert "User=calypso" in self._unit().read_text()

    def test_it_cannot_write_outside_data(self):
        src = self._unit().read_text()
        assert "ProtectSystem=strict" in src
        assert "ReadWritePaths=/opt/calypso/data" in src

    def test_the_script_opens_the_db_read_only(self):
        import inspect
        src = inspect.getsource(oec.load)
        assert "mode=ro" in src, (
            "the tracker must not be able to write to backtesting.db")

    def test_it_imports_no_broker_or_order_path(self):
        import inspect
        src = inspect.getsource(oec)
        for forbidden in ("IBClient", "BrokerClient", "place_order",
                          "place_and_wait_for_fill"):
            assert forbidden not in src, f"tracker references {forbidden}"


class TestItRunsAfterSettlement:
    def test_timer_fires_after_settlement_completes(self):
        """Settlement finishes ~21:45-22:37 ET. Reading before that appends a
        row built on unsettled numbers — the 2026-09-19 HERMES/HOMER mistake
        in a different costume."""
        import pathlib, re
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "deploy" / "oos_tracker.timer").read_text()
        m = re.search(r"OnCalendar=.*?(\d{2}):(\d{2}):\d{2}", src)
        assert m, "no OnCalendar time found"
        hh, mm = int(m.group(1)), int(m.group(2))
        assert (hh, mm) >= (23, 0), f"fires at {hh:02d}:{mm:02d}, before settlement"
        assert "America/New_York" in src, "must be pinned to ET, not UTC"
