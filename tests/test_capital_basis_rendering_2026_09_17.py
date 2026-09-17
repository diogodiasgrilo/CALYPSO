"""The view must not show a metric whose denominator does not exist.

Dashboard rebuild Phase 4 (2026-09-17). The single sentence this rebuild exists
to make true, enforced on the frontend side.

BACKGROUND. `PNL_SHAPES.credit.capitalLabel` reads "Capital deployed (width
notional)". A naked strangle is `credit` shaped — it sells premium exactly like
an iron condor — but it has NO spread width, so that label was false for G by
construction, and the ROI computed against it was undefined rather than merely
missing.

The shape config existed but was never wired: `capitalLabel`, `hasCreditFields`
and `structureName` had ZERO consumers, and only `accentForStrategy` was
imported anywhere. So Phase 4 both adds the capital-basis axis AND actually
connects it to the two cards that render capital.

These are source-level checks because the frontend has no test runner in this
repo's suite; `tsc -b` is the compile-level gate and is run in CI/manually.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FE = ROOT / "dashboard" / "frontend" / "src"
SHAPE = (FE / "lib" / "pnlShape.ts").read_text()
META = (FE / "hooks" / "useStrategyMeta.ts").read_text()
CARD = (FE / "components" / "pnl" / "DailyPnLCard.tsx").read_text()
BANNER = (FE / "components" / "market" / "MarketContextBanner.tsx").read_text()

import shared.strategy_taxonomy as tax  # noqa: E402


class TestTheCapitalBasisAxisExists:
    def test_every_backend_basis_has_a_frontend_config(self):
        """A basis the renderer cannot describe falls back to iron-condor
        wording — which is exactly the bug."""
        for vid in tax.available_ids():
            basis = tax.meta(vid).capital_basis
            assert f"{basis}: {{" in SHAPE, f"{vid}: no CAPITAL_BASES entry for {basis!r}"

    def test_the_types_carry_it(self):
        assert "CapitalBasis" in META and "capital_basis: CapitalBasis" in META
        assert "sides: Sides" in META

    def test_undefined_risk_is_marked_unbounded(self):
        """boundedLoss:false is the one that must never be wrong — printing any
        max-loss number for a naked position is a lie."""
        i = SHAPE.index("broker_margin: {")
        assert "boundedLoss: false" in SHAPE[i:i + 400]

    def test_defined_risk_and_net_debit_remain_bounded(self):
        for basis in ("defined_risk", "net_debit"):
            i = SHAPE.index(f"{basis}: {{")
            assert "boundedLoss: true" in SHAPE[i:i + 400], basis

    def test_the_margin_basis_does_not_say_width(self):
        i = SHAPE.index("broker_margin: {")
        block = SHAPE[i:i + 400].lower()
        assert "width" not in block, "a strangle's capital is margin, never width"
        assert "margin" in block


class TestTheCardsActuallyUseIt:
    """The shape config sat unused for its whole existence. Wiring is the fix."""

    @pytest.mark.parametrize("src,name", [(CARD, "DailyPnLCard"), (BANNER, "MarketContextBanner")])
    def test_labels_come_from_the_basis_not_a_literal(self, src, name):
        assert "capitalBasisConfig" in src, f"{name} does not consult the basis"
        assert "useSelectedStrategy" in src, f"{name} cannot know which strategy"

    @pytest.mark.parametrize("src,name", [(CARD, "DailyPnLCard"), (BANNER, "MarketContextBanner")])
    def test_the_hardcoded_iron_condor_labels_are_gone(self, src, name):
        """Hardcoded 'ROI (on capital)' / 'Capital / Day' as RENDERED text is
        what mislabelled G. They may survive only as fallback defaults."""
        assert '<div className="label-upper mb-1">Capital / Day</div>' not in src, name
        assert '<StatCell label="Capital / Day">' not in src, name
        assert '<StatCell label="ROI (on capital)">' not in src, name


class TestAbsentDataRendersAsAbsent:
    """G had ZERO capital history before Phase 3 — a rendered $0 / 0.00% would
    be a fabricated number, indistinguishable from a real flat result."""

    @pytest.mark.parametrize("src,name", [(CARD, "DailyPnLCard"), (BANNER, "MarketContextBanner")])
    def test_it_checks_for_history_before_rendering_a_number(self, src, name):
        assert "avg_capital_per_day != null" in src, name
        assert "—" in src, f"{name} has no absent-data placeholder"

    def test_zero_is_treated_as_no_history_not_as_zero_capital(self):
        """`> 0`, not just non-null: the old code path wrote 0.0, and a strategy
        that genuinely deployed $0 of capital does not exist."""
        for src in (CARD, BANNER):
            i = src.index("avg_capital_per_day != null")
            assert "> 0" in src[i:i + 120]
