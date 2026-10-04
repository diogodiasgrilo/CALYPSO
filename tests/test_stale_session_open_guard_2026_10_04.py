"""The session's first tick must be corroborated before it sets the open.

THE BUG (measured 2026-10-04 over 24 trading days, 3 occurrences). IBKR can
serve the PRIOR DAY'S CLOSE as the first regular-session print, flagged
6509='R' (real-time) — so the existing Z/Y/N freshness gate does not catch it.
Because that one tick is both the FIRST and an extreme, it set `spx_open` AND
an extreme in one go:

    2026-10-02  variant G  7666.45 at 09:30:03, next tick 7735.76 (+69pt)
                -> day_range 53 -> 88, day_type chop -> TREND, rv 8.5 -> 16.4
    2026-09-10  variant A  +41.6pt stale open -> day_type chop -> TREND

It is not only an analysis problem: `_check_whipsaw_filter` is a LIVE entry-skip
path on B and reads `spx_high - spx_low`, while `_expected_daily_move()` is built
from `spx_open`. For a stale-LOW open both errors push the same way — toward
falsely SKIPPING entries.

The value cannot be screened alone (it IS the prior close, so there is nothing
to compare against at arrival). A second independent read is the only
discriminator, which is what these tests pin.
"""
import pytest

from bots.hydra.base_strategy import MarketData


def _md():
    m = MarketData()
    # Force the regular-session OHLC gate; otherwise this suite only passes
    # when it happens to run between 09:30 and 16:00 ET.
    m._is_regular_session_or_later = lambda: True
    return m


def _feed(m, prices):
    for p in prices:
        m.update_spx(p, "R")
    return m


class TestTheRealIncidents:
    def test_G_2026_10_02_stale_open_is_rejected(self):
        """The actual tick sequence from G's log."""
        m = _feed(_md(), [7666.45, 7735.76, 7734.87, 7733.07, 7754.47, 7700.51])
        assert m.spx_open == 7735.76, "the stale print became the open"
        assert m.spx_low == 7700.51, "the stale print polluted the low"
        rng = m.spx_high - m.spx_low
        assert rng == pytest.approx(53.96, abs=0.01), (
            "range %.2f — the fleet's true range that day was ~53.4; the "
            "corrupted value was 88.0" % rng)

    def test_A_2026_09_10_stale_open_is_rejected(self):
        m = _feed(_md(), [7636.36, 7594.78, 7600.0])
        assert m.spx_open == 7594.78

    def test_the_rejected_tick_never_reaches_high_or_low(self):
        """A stale HIGH print must not set spx_high either."""
        m = _feed(_md(), [7800.0, 7700.0, 7710.0])
        assert m.spx_open == 7700.0
        assert m.spx_high == 7710.0, "the stale high print leaked into spx_high"


class TestNormalOpensAreUnharmed:
    def test_first_tick_is_kept_when_corroborated(self):
        m = _feed(_md(), [7731.07, 7731.51, 7754.06, 7700.51])
        assert m.spx_open == 7731.07, "a good first tick must still be the open"

    def test_a_corroborated_first_tick_counts_toward_extremes(self):
        """It was withheld from hi/lo while pending — it must be folded back."""
        m = _feed(_md(), [7700.0, 7700.5, 7750.0])
        assert m.spx_low == 7700.0, (
            "the corroborated first tick was dropped from the low entirely")
        assert m.spx_open == 7700.0

    def test_a_single_tick_session_captures_no_open_rather_than_a_bad_one(self):
        """Better NULL than stale: unverifiable means no open."""
        m = _feed(_md(), [7666.45])
        assert m.spx_open == 0.0
        assert m.spx_low == float("inf"), "an uncorroborated tick set an extreme"

    def test_a_move_just_inside_tolerance_is_accepted(self):
        m = _feed(_md(), [7700.0, 7700.0 * 1.002])      # 0.20% < 0.30%
        assert m.spx_open == 7700.0

    def test_a_move_just_outside_tolerance_is_rejected(self):
        m = _feed(_md(), [7700.0, 7700.0 * 1.004])      # 0.40% > 0.30%
        assert m.spx_open == pytest.approx(7700.0 * 1.004)


class TestPlumbing:
    def test_tolerance_is_config_overridable_in_percent(self):
        m = _md()
        m.strategy_config = {"session_open_corroboration_pct": 1.0}
        assert m._open_corroboration_tol() == pytest.approx(0.01)
        _feed(m, [7666.45, 7735.76])     # 0.90% now INSIDE a 1.0% tolerance
        assert m.spx_open == 7666.45

    @pytest.mark.parametrize("junk", ["x", None, 0, -5, {}])
    def test_junk_tolerance_falls_back_to_the_default(self, junk):
        m = _md()
        m.strategy_config = {"session_open_corroboration_pct": junk}
        assert m._open_corroboration_tol() == MarketData._OPEN_CORROBORATION_TOL

    def test_tolerance_without_any_config_attribute(self):
        assert _md()._open_corroboration_tol() == pytest.approx(0.0030)

    def test_daily_reset_clears_a_pending_candidate(self):
        """Otherwise yesterday's unconfirmed candidate corroborates today's open."""
        m = _feed(_md(), [7666.45])
        assert m._pending_open_price == 7666.45
        m.reset_daily_tracking()
        assert m._pending_open_price is None

    def test_pre_market_ticks_do_not_consume_the_candidate_slot(self):
        """OHLC capture is gated to the regular session; pre-market must not arm it."""
        m = MarketData()
        m._is_regular_session_or_later = lambda: False
        m.update_spx(7666.45, "R")
        assert m._pending_open_price is None
        assert m.spx_price == 7666.45, "the live price should still update"
