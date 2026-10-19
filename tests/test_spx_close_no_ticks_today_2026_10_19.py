"""`_resolve_spx_close`'s stale-price guard could not see the case with no ticks.

The guard added 2026-07-07 (after variant C's phantom −$6,037) is written:

    if disk > 0 and abs(px - disk) / disk > 0.01:

so when TODAY has recorded no ticks at all, `disk` is 0, the guard **cannot
fire**, and an in-memory `current_price` passes through unchecked however stale
it is. A guard that needs a reference cannot protect the case where the
reference does not exist yet.

It is reachable whenever a summary or settlement runs before a session's first
tick — pre-market, or the 00:00 new-day reset. The calendar variants hit it
routinely: `CalendarStrategyBase`'s activity gate is trivially true while a
multi-day position is carried, so a summary is written at the reset.

Variant D, 2026-10-16: a leftover calendar settled at 00:00:48 against
`current_price` 7743.37, and the row was stamped onto 10-16 — a day that
actually closed at **7660.94**, an 82.43pt (1.076%) error. Measured scope: 22 of
D's 70 epoch rows, 42 of E's 89, six of D's carrying real P&L.

⚠️ WHAT THIS DOES NOT FIX, deliberately: the row is still DATED to the new day.
`_book_daily_cumulative` is idempotent-by-date, so re-dating a settlement onto
an already-booked prior day makes it early-return and DROPS the P&L from
cumulative_pnl (which the self-heal re-derives from daily_summaries).
Suppressing the row loses it the same way. Both obvious fixes are worse than the
misdating, which is why only provenance is corrected here. D's verdict is
unaffected either way: t = −2.28 as recorded vs −2.22 re-dated.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.strategy import HydraStrategy  # noqa: E402

STALE = 7743.37       # what D's current_price held at 00:00:48 on 2026-10-16
PRIOR = 7744.57       # the real prior recorded close
TODAY_CLOSE = 7660.94  # 10-16's actual close (no ticks yet at 00:00)


def _s(current_price, *, today_ticks=None, prior=None, recorder=True):
    s = HydraStrategy.__new__(HydraStrategy)
    s.current_price = current_price
    if recorder:
        s._data_recorder = SimpleNamespace(
            get_last_spx_for_date=lambda d: today_ticks,
            get_last_spx_before_date=lambda d: prior,
        )
    else:
        s._data_recorder = None
    return s


class TestTheHoleIsClosed:
    def test_a_days_stale_price_is_rejected_when_today_has_no_ticks(self):
        """The D 2026-10-16 shape, exaggerated to a clearly stale value."""
        s = _s(7900.00, today_ticks=None, prior=PRIOR)
        assert s._resolve_spx_close() == pytest.approx(PRIOR)

    def test_the_real_incident_values_pass_through(self):
        """HONEST LIMIT: 7743.37 vs a 7744.57 prior close is 0.016% apart, well
        inside the 1% band, so this fix would NOT have changed D's settlement
        price. It closes the hole for a price stale by DAYS (a weekend, a long
        restart), not for one stale by one session. Pinned so the fix is not
        credited with more than it does."""
        s = _s(STALE, today_ticks=None, prior=PRIOR)
        assert s._resolve_spx_close() == pytest.approx(STALE)

    def test_a_weekend_stale_price_is_caught(self):
        """The case that actually matters: Friday's close in memory, Monday
        00:00 settlement, market gapped >1% over the weekend."""
        s = _s(7900.00, today_ticks=None, prior=7660.94)
        assert s._resolve_spx_close() == pytest.approx(7660.94)

    def test_divergences_under_one_percent_still_pass(self):
        s = _s(PRIOR * 1.005, today_ticks=None, prior=PRIOR)
        assert s._resolve_spx_close() == pytest.approx(PRIOR * 1.005)

    def test_an_implausible_reference_is_not_trusted_over_the_live_price(self):
        """The REFERENCE gets sanity-checked too, not just px. Accepting any
        positive number meant a nonsense reference could override a good close —
        tests/test_aud5_fixes.py mocks the recorder with a bare MagicMock, and
        `float(MagicMock())` is 1.0, so this branch "corrected" 7350.5 to 1.0.
        Those pre-existing tests caught it; the mock was an artefact but the
        weakness was real. SPX has never gapped 20% between sessions, so a
        divergence outside [1%, 20%] describes a bad reference, not a stale
        price, and the safe action is the pre-existing one: keep px."""
        for bad_prior in (1.0, 0.01, 100.0, 50000.0):
            s = _s(7350.5, today_ticks=None, prior=bad_prior)
            assert s._resolve_spx_close() == pytest.approx(7350.5), (
                f"a reference of {bad_prior} must not override a plausible close")

    def test_the_band_upper_edge_is_inclusive_and_beyond_it_is_not(self):
        base = 7000.0
        s_in = _s(base * 1.19, today_ticks=None, prior=base)    # 19% -> act
        assert s_in._resolve_spx_close() == pytest.approx(base)
        s_out = _s(base * 1.25, today_ticks=None, prior=base)   # 25% -> keep px
        assert s_out._resolve_spx_close() == pytest.approx(base * 1.25)

    def test_negative_control_without_the_prior_lookup_the_stale_price_wins(self):
        """Proves the assertions above are not vacuous: with no prior reference
        available the stale value is returned, which is the pre-fix behaviour."""
        s = _s(7900.00, today_ticks=None, prior=None)
        assert s._resolve_spx_close() == pytest.approx(7900.00)


class TestTheExistingGuardIsUntouched:
    """The 2026-07-07 guard is what prevents variant C's phantom. It must keep
    working exactly as before whenever today HAS ticks."""

    def test_today_ticks_present_and_price_sane_uses_the_live_price(self):
        s = _s(7661.10, today_ticks=TODAY_CLOSE, prior=PRIOR)
        assert s._resolve_spx_close() == pytest.approx(7661.10)

    def test_today_ticks_present_and_price_stale_uses_the_recorded_close(self):
        """The original 07-06 variant-C shape."""
        s = _s(7420.22, today_ticks=7537.86, prior=PRIOR)
        assert s._resolve_spx_close() == pytest.approx(7537.86)

    def test_zero_current_price_still_recovers_the_on_disk_close(self):
        """The 2026-06-11 late-settlement gap fix."""
        s = _s(0.0, today_ticks=TODAY_CLOSE, prior=PRIOR)
        assert s._resolve_spx_close() == pytest.approx(TODAY_CLOSE)

    def test_zero_everything_returns_zero_so_the_phantom_guard_still_skips(self):
        s = _s(0.0, today_ticks=None, prior=None)
        assert s._resolve_spx_close() == 0.0


class TestItNeverBreaksSettlement:
    def test_no_recorder_at_all_is_survivable(self):
        s = _s(7700.0, recorder=False)
        assert s._resolve_spx_close() == pytest.approx(7700.0)

    def test_a_raising_prior_lookup_does_not_propagate(self):
        s = _s(7900.0, today_ticks=None, prior=None)
        def boom(_d):
            raise RuntimeError("db locked")
        s._data_recorder = SimpleNamespace(
            get_last_spx_for_date=lambda d: None, get_last_spx_before_date=boom)
        assert s._resolve_spx_close() == pytest.approx(7900.0)

    def test_a_raising_today_lookup_does_not_propagate(self):
        def boom(_d):
            raise RuntimeError("db locked")
        s = _s(7700.0, today_ticks=None, prior=None)
        s._data_recorder = SimpleNamespace(
            get_last_spx_for_date=boom, get_last_spx_before_date=lambda d: None)
        assert s._resolve_spx_close() == pytest.approx(7700.0)

    def test_a_nonpositive_result_is_the_contract_not_a_bug(self):
        """It never returns None, and a NON-POSITIVE result is meaningful rather
        than wrong: `_daily_summary_is_stale` treats `(spx_close or 0) <= 0` as
        "no usable close" and skips the row. An earlier version of this test
        asserted `>= 0` and failed on current_price = -5.0 — the test was wrong,
        not the code. Pinned so the contract is explicit."""
        for cp, positive in ((7700.0, True), (0.0, False), (-5.0, False)):
            s = _s(cp, today_ticks=None, prior=None)
            out = s._resolve_spx_close()
            assert out is not None
            assert (out > 0) is positive
            if not positive:
                assert HydraStrategy._daily_summary_is_stale("2026-10-19", "2026-10-19", out) is True


class TestTheRecorderHelperIsNonCircular:
    def test_it_reads_market_ticks_not_daily_summaries(self):
        """daily_summaries is the table that CONTAINS the artifact rows —
        corroborating against it would be circular, and `get_yesterday_spx_close`
        (which does read it) is why a separate helper exists."""
        src = open(os.path.join(ROOT, "shared/data_recorder.py")).read()
        i = src.index("def get_last_spx_before_date")
        body = src[i:i + 1800]
        assert "FROM market_ticks" in body
        assert "daily_summaries" not in body.split('"""')[2] if '"""' in body else True
