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
        seg = src[src.index("DATA-002: a feed we trade on is not real-time"):][:6000]
        assert "AlertPriority.CRITICAL" in seg

    def test_the_alert_is_deduped_per_episode(self):
        """20,000 skips a day must not be 20,000 emails."""
        src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
        seg = src[src.index("DATA-002: a feed we trade on is not real-time"):][:6000]
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


class TestOnlyTheLiveSeatNotifies:
    """Nine variants, one shared feed — and the first version emailed per variant.

    Measured on 2026-11-09, the morning DATA-002 shipped: variants A and C
    both alerted at their 10:45 slot about the SAME delayed feed, and the
    remaining seven would have followed at theirs. Up to nine CRITICAL emails
    for one fact.

    They sent despite `alerts.enabled=False` because CRITICAL bypasses that
    gate deliberately — so a safety property became a spam multiplier. One
    bounced outright: variant C still carries the `your@email.com` placeholder.

    Every variant must still BLOCK (a shadow recording 15-minute-old prices is
    producing garbage data), but only the seat holding real orders notifies.
    """

    @staticmethod
    def _segment():
        src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
        i = src.index("DATA-002: a feed we trade on is not real-time")
        return src[i:i + 6000]

    def test_dry_run_variants_do_not_alert(self):
        """⚠️ Match the CALL, not the kwarg.

        The first version asserted on `seg.index("send_alert")`, which matched
        `_record_skipped_entry(..., send_alert=False)` — a kwarg that appears
        BEFORE the gate — so it failed against correct code.
        """
        seg = self._segment()
        assert "if self.dry_run:" in seg, "the dry-run gate is gone"
        gate = seg.index("if self.dry_run:")
        send = seg.index("self.alert_service.send_alert")
        assert gate < send, "the dry-run gate must precede the send"

    def test_the_block_itself_is_NOT_gated_on_dry_run(self):
        """Blocking is for everyone; only the notification is scoped."""
        seg = self._segment()
        assert "if self.dry_run:" in seg, "the dry-run gate is gone"
        assert seg.index("entries_skipped += 1") < seg.index("if self.dry_run:"), (
            "a dry-run variant must still refuse to open on stale data")

    def test_the_safety_event_is_still_recorded_for_every_variant(self):
        """The record must survive even where the email does not."""
        seg = self._segment()
        assert "if self.dry_run:" in seg, "the dry-run gate is gone"
        assert seg.index("DATA-002_DEGRADED_FEED") < seg.index("if self.dry_run:")

    def test_per_episode_dedup_still_applies_to_the_live_seat(self):
        seg = self._segment()
        assert "_md_alert_episode" in seg


class TestTheFlagTracksOBSERVATION_NotEntryAttempts:
    """The first version only set the flag when an entry was attempted.

    Measured on the day it shipped: SPX was delayed from the 09:30 open, but
    the live seat's first slot is 12:15 — so for nearly three hours the
    dashboard showed a healthy bot on a known-bad feed. That is precisely what
    the banner exists to prevent, so the flag now refreshes every tick.
    """

    @staticmethod
    def _rig(spx=None, vix=None, in_session=True):
        class _MD2(_MD):
            @staticmethod
            def _is_regular_session_or_later():
                return in_session
        s = _strat(spx, vix)
        s.market_data = _MD2(spx, vix)
        return s

    def test_degraded_is_flagged_without_any_entry_attempt(self):
        s = self._rig(spx="D", vix="R")
        MEICStrategy._refresh_market_data_health(s)
        assert s._md_degraded_reason and "DELAYED" in s._md_degraded_reason
        assert s._md_degraded_since, "the operator needs a start time"

    def test_healthy_data_clears_a_previous_degradation(self):
        s = self._rig(spx="R", vix="R")
        s._md_degraded_reason = "stale"
        s._md_degraded_since = "2026-11-09 09:30:00"
        MEICStrategy._refresh_market_data_health(s)
        assert s._md_degraded_reason is None and s._md_degraded_since is None

    def test_OUTSIDE_the_session_it_does_not_evaluate(self):
        """Pre-market FROZEN is normal; a banner that is always on is unread."""
        s = self._rig(spx="Z", vix="Z", in_session=False)
        MEICStrategy._refresh_market_data_health(s)
        assert getattr(s, "_md_degraded_reason", None) is None

    def test_outside_the_session_it_PRESERVES_an_existing_verdict(self):
        """After the close the operator must still see the day ran degraded."""
        s = self._rig(spx="R", vix="R", in_session=False)
        s._md_degraded_reason = "SPX feed is DELAYED"
        MEICStrategy._refresh_market_data_health(s)
        assert s._md_degraded_reason == "SPX feed is DELAYED"

    def test_the_since_timestamp_is_not_rewritten_on_every_tick(self):
        """'since' must be the FIRST sighting, not the latest tick.

        ⚠️ The first version called refresh twice in a row and compared the
        results. Both ran inside the same second, so the formatted timestamp
        was identical whether or not the code rewrote it — the test passed
        against the defect. Caught by its own mutation control.

        Seeding a distinctly OLD value removes the clock from the question.
        """
        s = self._rig(spx="D", vix="R")
        s._md_degraded_since = "2026-01-01 09:31:00"      # unmistakably earlier
        MEICStrategy._refresh_market_data_health(s)
        assert s._md_degraded_since == "2026-01-01 09:31:00", (
            "'since' was overwritten — the operator loses when it started")

    def test_it_never_raises_into_the_tick(self):
        """Telemetry must not be able to break the trading loop."""
        class _Boom:
            @staticmethod
            def _is_regular_session_or_later():
                raise RuntimeError("boom")
        s = _strat("D", "R")
        s.market_data = _Boom()
        MEICStrategy._refresh_market_data_health(s)   # must not raise

    def test_it_is_called_from_the_per_tick_index_read(self):
        src = open(os.path.join(ROOT, "bots/hydra/base_strategy.py")).read()
        assert "self._refresh_market_data_health()" in src

    def test_the_daily_reset_clears_it(self):
        import ast
        src = open(os.path.join(ROOT, "bots/hydra/strategy.py")).read()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_reset_for_new_day")
        seg = ast.get_source_segment(src, fn)
        for k in ("_md_degraded_reason", "_md_degraded_since", "_md_alert_episode"):
            assert k in seg, f"{k} survives into the next session"
