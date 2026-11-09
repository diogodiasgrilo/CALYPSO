"""DATA-002: the fleet traded on DELAYED data for a week and never stopped.

From 2026-11-02 every variant received SPX and VIX with IBKR field 6509 = "D".
IBKR documents that as 15-20 MINUTES behind. The bot logged
"proceeding but data may lag real-time" ~20,000 times a day and then did this:

    if first == "D":
        logger.warning(...)
    self.spx_price = price
    self.last_spx_update = get_us_market_time()   # <- stamped FRESH

So delayed data was accepted AND marked current, walking past the staleness
gate that already existed for FROZEN quotes. Measured cost on the live seat
over five sessions: 24 entries failed mid-leg (19 on a short leg, because
limits were computed from stale prices), $736 of commission burned, one
`critical_intervention` halt, and a week of unusable data.

The guard blocks OPENING positions and deliberately leaves EXITS alone: a
stale price is a bad reason to take on risk and a terrible reason to be unable
to shed it.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402


class _MD:
    def __init__(self, spx=None, vix=None):
        self.last_spx_availability = spx
        self.last_vix_availability = vix


def _strat(spx=None, vix=None):
    class _C(MEICStrategy):
        def _calculate_strikes(self, *a, **k): ...
        def _check_stop_losses(self, *a, **k): ...
        def _initiate_entry(self, *a, **k): ...
    s = _C.__new__(_C)
    s.market_data = _MD(spx, vix)
    return s


def _reason(spx=None, vix=None):
    return MEICStrategy._market_data_degraded(_strat(spx, vix))[0]


class TestItDetectsTheIncident:
    def test_the_exact_2026_11_02_condition_is_caught(self):
        """SPX and VIX both 'D' — what ran for five sessions."""
        r = _reason(spx="D", vix="D")
        assert r and "DELAYED" in r and "SPX" in r

    def test_ibkr_delay_magnitude_is_stated_not_implied(self):
        assert "15-20 min" in _reason(spx="D")

    def test_either_feed_alone_is_enough(self):
        assert _reason(spx="D", vix="R") is not None
        assert _reason(spx="R", vix="D") is not None

    def test_frozen_is_also_refused(self):
        assert "FROZEN" in _reason(spx="Z", vix="R")

    def test_an_unrecognised_code_is_refused_not_ignored(self):
        """Fail CLOSED on a code we have never seen, since it is not 'R'."""
        r = _reason(spx="X9", vix="R")
        assert r is not None and "NOT real-time" in r


class TestItDoesNotOverBlock:
    def test_real_time_passes(self):
        assert _reason(spx="R", vix="R") is None

    def test_the_RP_RB_compound_codes_pass(self):
        """6509 can carry a structure suffix: 'RP' is still REAL-TIME."""
        assert _reason(spx="RP", vix="RB") is None

    def test_unknown_availability_fails_OPEN(self):
        """A feed reporting nothing is the freshness gate's job, not this one.

        Treating 'unreported' as degraded would halt trading on a missing
        field rather than on bad data.
        """
        assert _reason(spx=None, vix=None) is None
        assert _reason(spx="R", vix=None) is None

    def test_lowercase_is_handled(self):
        assert _reason(spx="r", vix="r") is None
        assert _reason(spx="d", vix="r") is not None


class TestTheWiring:
    def test_the_gate_runs_BEFORE_the_other_entry_gates(self):
        """Every gate below reasons about prices; stale prices poison them all."""
        src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
        i_data = src.index("DATA-002: a feed we trade on is not real-time")
        i_whip = src.index("whipsaw_reason = self._check_whipsaw_filter()")
        assert i_data < i_whip

    def test_the_alert_is_CRITICAL_so_it_emails(self):
        """_should_send_email is a pure priority check: CRITICAL only."""
        from shared.alert_service import DEFAULT_PRIORITIES, AlertPriority, AlertType
        assert DEFAULT_PRIORITIES[AlertType.CRITICAL_INTERVENTION] == AlertPriority.CRITICAL
        src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
        seg = src[src.index("DATA-002: a feed we trade on is not real-time"):][:3000]
        assert "AlertPriority.CRITICAL" in seg

    def test_the_alert_is_deduped_per_episode(self):
        """20,000 skips a day must not be 20,000 emails."""
        src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
        seg = src[src.index("DATA-002: a feed we trade on is not real-time"):][:3000]
        assert "_md_alert_episode" in seg

    def test_exits_are_untouched(self):
        """The guard must appear ONLY on the entry path."""
        src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
        assert src.count("_market_data_degraded()") == 1

    def test_the_dashboard_can_say_why(self):
        src = open(os.path.join(ROOT, "bots/hydra/base_strategy.py")).read()
        for k in ("market_data_degraded", "market_data_reason",
                  "market_data_degraded_since"):
            assert f'"{k}"' in src
