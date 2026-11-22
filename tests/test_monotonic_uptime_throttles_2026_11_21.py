"""The last two `monotonic() - 0.0` throttles — a fresh boot swallowed the first.

Found 2026-11-20 by a CI runner, in a logging fix, and recorded then as two
remaining sites. This closes them.

THE DEFECT. `time.monotonic()` on Linux is **seconds since boot**, not since
the epoch. So a throttle written as

    last = getattr(self, "_last_x_at", 0.0)
    if now - last < WINDOW:
        return                      # "we did this recently"

compares against uptime. On a machine that booted 30 seconds ago, `now - 0.0`
is 30 — inside any window of a minute or two — so the guard reports "done
recently" for something that has NEVER run, and swallows the FIRST occurrence.
On a long-running host the same code is harmless: the VM here reads
~22,000,000s of uptime, so the subtraction always clears the window and every
occurrence fires. That asymmetry is why this cannot be found in production and
took a fresh CI container to expose.

THE TWO SITES.

`_entry_checkpoint` (bots/hydra/base_strategy.py) is the safety-adjacent one.
It runs a stop check at each quiescent rung of entry placement, closing a blind
window measured at 78% of a stop's 54-second warning time down to 24%
(2026-10-25). Its throttle skipping the FIRST checkpoint of a process means a
bot restarted into a moving market has no checkpoint protection for its first
15 seconds of placement — the precise scenario the mechanism was built for.

`_alert_rate_penalty` (bots/hydra/strategy.py) swallowed the first HIGH operator
alert for an IBKR 429 penalty box when the process started within 120s of a
boot — which is when a restart storm makes a 429 most likely, so the dedup
suppressed the one alert most worth having.

THE FIX is a `None` sentinel: "never happened" must not be representable as a
timestamp, because every timestamp is a real point on the monotonic clock.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


# --------------------------------------------------------------------------
# 1. The stop-check throttle
# --------------------------------------------------------------------------

class _Rig:
    """Minimal stand-in exercising the REAL _entry_checkpoint."""

    ENTRY_CHECKPOINT_MIN_GAP_S = 15.0

    def __init__(self):
        from bots.hydra.base_strategy import MEICStrategy
        self._fn = MEICStrategy._entry_checkpoint
        self.daily_state = types.SimpleNamespace(active_entries=[{"entry_number": 1}])
        self.state = "MONITORING"
        self.checks = 0

    def _check_stop_losses(self):
        self.checks += 1
        return None

    def run(self, where="rung"):
        self._fn(self, where)


@pytest.fixture()
def rig():
    return _Rig()


def _at_uptime(monkeypatch, seconds: float):
    """Pin time.monotonic to a given machine uptime."""
    import bots.hydra.base_strategy as bs
    monkeypatch.setattr(bs.time, "monotonic", lambda: seconds)


class TestTheFirstCheckpointAfterBoot:
    def test_it_runs_2_seconds_after_boot(self, rig, monkeypatch):
        """THE REGRESSION. Uptime 2s is inside the 15s window, so a 0.0 default
        reads as 'checked 2 seconds ago' and skips a check that never ran."""
        _at_uptime(monkeypatch, 2.0)
        rig.run()
        assert rig.checks == 1, "first checkpoint after boot was swallowed"

    @pytest.mark.parametrize("uptime", [0.0, 0.5, 5.0, 14.9])
    def test_it_runs_at_any_sub_window_uptime(self, rig, monkeypatch, uptime):
        _at_uptime(monkeypatch, uptime)
        rig.run()
        assert rig.checks == 1

    def test_it_also_runs_on_a_long_uptime_host(self, rig, monkeypatch):
        """The VM's real condition — must be unchanged by the fix."""
        _at_uptime(monkeypatch, 22_029_949.0)
        rig.run()
        assert rig.checks == 1

    def test_the_throttle_STILL_throttles(self, rig, monkeypatch):
        """The fix must not turn a throttle into a no-op: the gate it protects
        is ~83% saturated."""
        _at_uptime(monkeypatch, 1000.0)
        rig.run()
        assert rig.checks == 1
        _at_uptime(monkeypatch, 1005.0)        # 5s later, inside 15s
        rig.run()
        assert rig.checks == 1, "throttle stopped throttling"

    def test_it_runs_again_once_the_window_passes(self, rig, monkeypatch):
        _at_uptime(monkeypatch, 1000.0)
        rig.run()
        _at_uptime(monkeypatch, 1015.1)
        rig.run()
        assert rig.checks == 2

    def test_no_open_entries_means_no_check(self, rig, monkeypatch):
        """Unchanged precondition — nothing to protect, nothing to spend."""
        _at_uptime(monkeypatch, 2.0)
        rig.daily_state.active_entries = []
        rig.run()
        assert rig.checks == 0

    def test_reentry_is_still_refused(self, rig, monkeypatch):
        _at_uptime(monkeypatch, 2.0)
        rig._in_entry_checkpoint = True
        rig.run()
        assert rig.checks == 0


# --------------------------------------------------------------------------
# 2. The rate-penalty operator alert
# --------------------------------------------------------------------------

class TestTheFirstRatePenaltyAlertAfterBoot:
    def _rig(self):
        from bots.hydra.strategy import HydraStrategy
        sent = []

        class R:
            BOT_NAME = "HYDRA_TEST"
            _alert_rate_penalty = HydraStrategy._alert_rate_penalty

            class alert_service:
                @staticmethod
                def send_alert(**kw):
                    sent.append(kw)

        return R(), sent

    def _at(self, monkeypatch, seconds):
        import bots.hydra.strategy as st
        monkeypatch.setattr(st.time, "monotonic", lambda: seconds)

    def test_the_first_alert_fires_30_seconds_after_boot(self, monkeypatch):
        """THE REGRESSION. A 429 during a restart storm is the single most
        likely time for this, and it was the one case suppressed."""
        r, sent = self._rig()
        self._at(monkeypatch, 30.0)
        r._alert_rate_penalty("chain", RuntimeError("429"))
        assert len(sent) == 1, "first rate-penalty alert after boot was swallowed"

    def test_it_fires_on_a_long_uptime_host_too(self, monkeypatch):
        r, sent = self._rig()
        self._at(monkeypatch, 22_029_949.0)
        r._alert_rate_penalty("chain", RuntimeError("429"))
        assert len(sent) == 1

    def test_the_dedup_STILL_dedups(self, monkeypatch):
        """Without this the fix would reintroduce the alert storm the cooldown
        was added to stop (hundreds of identical HIGH alerts per window)."""
        r, sent = self._rig()
        self._at(monkeypatch, 5000.0)
        r._alert_rate_penalty("chain", RuntimeError("429"))
        self._at(monkeypatch, 5060.0)          # 60s later, inside 120s
        r._alert_rate_penalty("chain", RuntimeError("429"))
        assert len(sent) == 1, "dedup stopped deduping"

    def test_it_fires_again_after_the_cooldown(self, monkeypatch):
        r, sent = self._rig()
        self._at(monkeypatch, 5000.0)
        r._alert_rate_penalty("chain", RuntimeError("429"))
        self._at(monkeypatch, 5121.0)
        r._alert_rate_penalty("chain", RuntimeError("429"))
        assert len(sent) == 2


# --------------------------------------------------------------------------
# 3. The class is closed — no `monotonic()` throttle may default to a number
# --------------------------------------------------------------------------

class TestTheClassIsClosed:
    SCAN = ["bots/hydra/base_strategy.py", "bots/hydra/strategy.py",
            "bots/hydra/brandon/strategy.py", "shared/ib_client.py"]

    def test_no_throttle_defaults_to_a_numeric_timestamp(self):
        """A `getattr(self, "_last_*", <number>)` feeding a monotonic
        subtraction is the defect. Require the None sentinel instead."""
        import re
        pat = re.compile(r'getattr\(\s*self,\s*"(_last_[a-z0-9_]*)"\s*,\s*([^)]+)\)')
        offenders = []
        for rel in self.SCAN:
            p = os.path.join(ROOT, rel)
            if not os.path.exists(p):
                continue
            for n, line in enumerate(open(p).read().splitlines(), 1):
                m = pat.search(line)
                if not m:
                    continue
                default = m.group(2).strip()
                if default != "None":
                    offenders.append(f"{rel}:{n} {m.group(1)} default={default}")
        assert not offenders, (
            "monotonic throttle defaulting to a number (uptime bug): " + str(offenders)
        )

    def test_CONTROL_the_scan_catches_a_planted_numeric_default(self, tmp_path):
        """So the test above cannot pass by matching nothing."""
        import re
        pat = re.compile(r'getattr\(\s*self,\s*"(_last_[a-z0-9_]*)"\s*,\s*([^)]+)\)')
        bad = 'last = getattr(self, "_last_thing_at", 0.0)'
        m = pat.search(bad)
        assert m and m.group(2).strip() == "0.0"
