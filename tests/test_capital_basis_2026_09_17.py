"""Capital is basis-dependent (dashboard rebuild Phase 3, defects D2/D3).

THE DEFECT. `_calculate_capital_deployed` computed margin inline as
`spread_width x 100 x contracts` and did `if entry.spread_width <= 0: continue`.
A naked strangle's legs are permanently wingless, so EVERY entry was skipped, G
reported capital 0, no `daily_returns` row was ever written, and
return-on-capital came out UNDEFINED rather than merely missing — measured on
the live VM: 0 rows against 26 entries across 13 traded days, where B had 73.

Return on spread width is not "missing" for a position with no spread. It does
not exist. So capital dispatches on the taxonomy's `capital_basis`, and the
sweep that computes PEAK CONCURRENT margin — which carries several hard-won
timestamp fixes — is left untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import MEICStrategy  # noqa: E402
from bots.hydra.strangle_strategy import StrangleStrategy  # noqa: E402
import shared.strategy_taxonomy as tax  # noqa: E402


def _entry(**kw):
    base = dict(spread_width=0, contracts=1, margin_requirement=None)
    base.update(kw)
    return SimpleNamespace(**base)


class TestDefinedRiskIsUnchanged:
    """A/B/C/F must compute exactly what they computed before."""

    def test_width_times_100_times_contracts(self):
        e = _entry(spread_width=5, contracts=7)
        assert MEICStrategy._entry_margin(object(), e) == 5 * 100 * 7

    def test_a_single_contract(self):
        assert MEICStrategy._entry_margin(object(), _entry(spread_width=10, contracts=1)) == 1000.0

    def test_no_width_means_no_measurable_capital_not_a_guess(self):
        """0.0 tells the sweep to skip. It must never invent a number."""
        assert MEICStrategy._entry_margin(object(), _entry(spread_width=0)) == 0.0

    @pytest.mark.parametrize("bad", [None, 0, -5])
    def test_missing_or_nonsense_width_is_zero(self, bad):
        assert MEICStrategy._entry_margin(object(), _entry(spread_width=bad)) == 0.0


class TestTheStrangleUsesBrokerMargin:
    """G: taxonomy capital_basis == 'broker_margin'."""

    @staticmethod
    def _g(floor=30000.0):
        s = StrangleStrategy.__new__(StrangleStrategy)
        s.strategy_config = {"min_buying_power_per_strangle": floor}
        return s

    def test_it_does_NOT_fall_through_to_the_wingless_base(self):
        """The whole defect in one assertion: a wingless entry must still
        report capital, or no daily_returns row is ever written."""
        assert self._g()._entry_margin(_entry(spread_width=0, contracts=1)) > 0

    def test_it_uses_the_configured_naked_floor(self):
        """The SAME number the entry gate sized itself with — so the reported
        return is a return on capital the strategy actually required, not a
        second invented definition."""
        assert self._g(30000.0)._entry_margin(_entry(contracts=1)) == 30000.0
        assert self._g(30000.0)._entry_margin(_entry(contracts=3)) == 90000.0

    def test_a_real_broker_figure_WINS_over_the_floor(self):
        e = _entry(contracts=1, margin_requirement=41250.0)
        assert self._g()._entry_margin(e) == 41250.0

    @pytest.mark.parametrize("junk", [None, 0, -1, "", "abc"])
    def test_a_junk_broker_figure_falls_back_to_the_floor(self, junk):
        """A 0 or unparseable margin must not zero out capital — that would
        reproduce the original bug through a different door."""
        e = _entry(contracts=1, margin_requirement=junk)
        assert self._g(30000.0)._entry_margin(e) == 30000.0

    def test_it_ignores_spread_width_entirely(self):
        """A strangle has no wings; if width ever leaks in, the number is wrong."""
        wide = self._g()._entry_margin(_entry(spread_width=999, contracts=1))
        none = self._g()._entry_margin(_entry(spread_width=0, contracts=1))
        assert wide == none


class TestItMatchesTheTaxonomy:
    def test_the_only_broker_margin_strategy_overrides_entry_margin(self):
        """A declared basis with no implementation silently reverts to
        defined-risk — the exact failure this phase fixes."""
        for vid in tax.available_ids():
            if tax.meta(vid).capital_basis != "broker_margin":
                continue
            assert vid == "g"
            assert "_entry_margin" in StrangleStrategy.__dict__

    def test_calendars_override_capital_wholesale_instead(self):
        """D/E are net_debit and override _calculate_capital_deployed itself,
        so they never reach _entry_margin. Pinned so a later 'tidy-up' does not
        route them through a width-based path."""
        from bots.hydra.calendar_strategy_base import CalendarStrategyBase
        assert "_calculate_capital_deployed" in CalendarStrategyBase.__dict__
        for vid in ("d", "e"):
            assert tax.meta(vid).capital_basis == "net_debit"


class TestSortinoDoesNotInventANumber:
    """D3: it returned 0.0 with <2 return rows, which renders as a real ratio
    and is indistinguishable from a genuinely flat strategy. G had ZERO rows."""

    @staticmethod
    def _sortino(rows, pnl=0.0, capital=0.0):
        """MEICStrategy is abstract, so call the unbound method with a stand-in
        self carrying only what it reads."""
        fake_self = SimpleNamespace(cumulative_metrics={"daily_returns": rows})
        return MEICStrategy._calculate_sortino_ratio(fake_self, pnl, capital)

    def test_no_history_returns_None_not_zero(self):
        assert self._sortino([]) is None

    def test_one_day_is_still_not_enough(self):
        assert self._sortino([{"return_pct": 0.01}]) is None

    def test_two_days_produces_a_real_number(self):
        v = self._sortino([{"return_pct": 0.01}, {"return_pct": -0.02}])
        assert v is not None and isinstance(v, float)

    def test_no_losing_days_still_caps_rather_than_None(self):
        assert self._sortino([{"return_pct": 0.01}, {"return_pct": 0.02}]) == 99.99

    def test_the_sheets_consumer_tolerates_None(self):
        """logger_service formats this with :.2f — None would raise TypeError.
        Dead path today (Sheets disabled on all 7) but a latent crash regardless."""
        src = (Path(__file__).resolve().parents[1] / "shared" / "logger_service.py").read_text()
        i = src.index("sortino_ratio']:.2f")
        assert "is not None" in src[i - 200:i + 200]
