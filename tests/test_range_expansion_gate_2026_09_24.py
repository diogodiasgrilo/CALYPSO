"""The source's THIRD entry condition, which H shipped without.

Found by the operator's question *"did you do thorough online research to close
all of them?"* — I had not. Reading the Theta Profits article (rather than only
the video) turned up an entry rule listed beside the IV filter and the cost cap:

> *"Seeks range expansion — a period of narrow daily candle ranges that begins
> to widen."*

For a long strangle that is the thesis stated on the chart: the position needs
MOVEMENT, and a market coming out of a quiet stretch is where movement tends to
start. The same article also CONFIRMED the +100%/+50% rule H already had —
*"If implied volatility starts low and is expanding, he may aim toward the 100%
target. If volatility is already somewhat elevated… he is more likely to take
profits around 50%"* — which had been recorded as an open interpretive gap.

FAIL-OPEN, AND WHY IT DIFFERS FROM THE IV GATE
-----------------------------------------------
The source gives IV an explicit number to clear ("below about 35%"), so an
unmeasurable IV is a genuine unknown and skipping is the conservative reading.
He gives range expansion NO threshold — "narrow", "begins to widen" — so every
number here is ours. A filter built entirely from our own thresholds must not be
able to silently veto every session; that is exactly how H's first live day
produced one skip and no data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.long_strangle_chain import range_expansion_signal  # noqa: E402

H = json.loads(
    (ROOT / "bots" / "hydra" / "config" / "config_variant_h.json").read_text()
)["strategy"]["long_strangle"]

#: A long quiet stretch, then one wide day. The pattern he describes.
QUIET = [80.0 + (i % 5) for i in range(50)] + [20.0 + (i % 3) for i in range(10)]


class TestTheSignalReadsBothHalvesOfThePhrase:

    def test_compression_THEN_widening_fires(self):
        fires, _ = range_expansion_signal(QUIET + [30.0])
        assert fires is True

    def test_quiet_WITHOUT_widening_does_not_fire(self):
        """'Narrow ranges' alone is the setup, not the signal."""
        fires, _ = range_expansion_signal(QUIET + [21.0])
        assert fires is False

    def test_widening_WITHOUT_prior_compression_does_not_fire(self):
        """A market already wide that gets wider is not 'coming out of' a quiet
        stretch — it never had one."""
        always_wide = [80.0 + (i % 7) for i in range(60)]
        fires, _ = range_expansion_signal(always_wide + [120.0])
        assert fires is False

    def test_a_uniformly_calm_market_does_not_fire(self):
        """The negative control that matters most: with no baseline to compress
        AGAINST, 'narrow' is meaningless and the gate must not fire on every
        quiet day."""
        flat = [20.0 + (i % 3) for i in range(60)]
        fires, _ = range_expansion_signal(flat + [22.0])
        assert fires is False

    def test_compression_is_RELATIVE_so_it_scales_with_price(self):
        """Measured against the market's own recent normal, so the rule means
        the same thing at SPY 400 and SPY 800."""
        small = [8.0 + (i % 5) for i in range(50)] + [2.0 + (i % 2) for i in range(10)]
        big = [r * 100 for r in small]
        assert (range_expansion_signal(small + [3.0])[0]
                is range_expansion_signal(big + [300.0])[0] is True)

    def test_the_detail_string_shows_the_numbers_that_decided_it(self):
        _, detail = range_expansion_signal(QUIET + [30.0])
        assert "narrow median" in detail and "baseline" in detail and "latest" in detail


class TestItRefusesToJudgeWithoutHistory:

    @pytest.mark.parametrize("n", [0, 1, 2, 4])
    def test_too_few_days_reports_insufficient(self, n):
        fires, detail = range_expansion_signal([20.0] * n)
        assert fires is False and "insufficient" in detail

    def test_degenerate_ranges_are_not_a_signal(self):
        fires, detail = range_expansion_signal([0.0] * 60 + [0.0])
        assert fires is False
        assert "insufficient" in detail or "degenerate" in detail


class TestTheGateFailsOPENRatherThanVetoingOnIgnorance:

    def _strat(self, ranges, enabled=True):
        from bots.hydra.long_strangle_strategy import LongStrangleStrategy
        s = LongStrangleStrategy.__new__(LongStrangleStrategy)
        s._ls_config = lambda: {
            "range_expansion_filter_enabled": enabled,
            "range_expansion_narrow_days": 10,
            "range_expansion_baseline_days": 60,
            "range_expansion_compression_max": 0.90,
            "range_expansion_mult": 1.10,
        }
        s._daily_ranges = lambda lookback=80: ranges
        return s

    def _entry(self):
        from bots.hydra.long_strangle_entry import LongStrangleEntry
        return LongStrangleEntry(entry_number=1)

    def test_the_signal_present_lets_the_entry_through(self):
        assert self._strat(QUIET + [30.0])._range_expansion_gate(self._entry()) is None

    def test_the_signal_absent_skips_and_says_why(self):
        r = self._strat(QUIET + [21.0])._range_expansion_gate(self._entry())
        assert r is not None and "no range expansion" in r

    def test_NO_HISTORY_PROCEEDS(self):
        """The whole point of the asymmetry. 'Cannot measure the pattern' is not
        'the pattern is absent', and a filter made entirely of our own numbers
        must not veto every session on its own ignorance."""
        assert self._strat([])._range_expansion_gate(self._entry()) is None
        assert self._strat([20.0, 21.0])._range_expansion_gate(self._entry()) is None

    def test_disabled_is_a_no_op(self):
        assert self._strat(QUIET + [21.0], enabled=False)._range_expansion_gate(
            self._entry()) is None

    def test_the_reading_is_recorded_on_the_entry(self):
        """So a later analysis can score the filter instead of guessing at it."""
        e = self._entry()
        self._strat(QUIET + [30.0])._range_expansion_gate(e)
        assert getattr(e, "ls_range_expansion", None)


class TestTheShippedConfig:

    def test_the_filter_is_on(self):
        assert H["range_expansion_filter_enabled"] is True

    def test_the_two_halves_are_both_configured(self):
        assert 0 < H["range_expansion_compression_max"] <= 1.0
        assert H["range_expansion_mult"] > 1.0

    def test_the_narrow_window_is_shorter_than_the_baseline(self):
        assert H["range_expansion_narrow_days"] < H["range_expansion_baseline_days"], (
            "compression is measured against a LONGER baseline; inverted, the "
            "comparison is meaningless")

    def test_the_provenance_and_the_fail_open_choice_are_recorded(self):
        raw = (ROOT / "bots" / "hydra" / "config" / "config_variant_h.json").read_text()
        assert "_comment_range_expansion" in raw
        assert "FAILS OPEN" in raw
