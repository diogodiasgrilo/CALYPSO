"""D and E share a page, and only D transforms.

From the line-by-line audit of the calendar dashboard (B2/B3) — the one the
operator asked for after only H's page had ever had one.

The page is built around D's defining move: a double calendar that becomes a
structurally **risk-free iron condor** once it shows 5–10% profit. **E never
transforms** — there is literally no transform code in
``spy_double_calendar_strategy.py``; it is a managed calendar with laddered
profit-taking and a time exit.

So selecting E rendered ``Transformed: 0`` and ``Risk-Free: 0``, permanently, for
a concept E does not have. A zero reads as *failing* at something, not as *not
attempting* it — the same reason H was given its own page rather than being
folded into the iron-condor view, where "expired worthless" would have been
printed as profit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402
import dashboard.backend.routers.strategies as sr  # noqa: E402

CFG = ROOT / "bots" / "hydra" / "config"
PAGE = ROOT / "dashboard" / "frontend" / "src" / "components" / "dashboard" / "CalendarDashboard.tsx"


def _flag(vid: str):
    sr.settings.__dict__[f"variant_{vid}_config_file"] = str(CFG / f"config_variant_{vid}.json")
    return sr._header_chrome(vid, tax.STRATEGIES[vid])["transforms_to_condor"]


class TestTheCodeBacksTheClaim:
    """The finding rests on E genuinely having no transform path, so that is
    asserted rather than taken from a docstring."""

    def test_E_has_no_transform_code_at_all(self):
        src = (ROOT / "bots" / "hydra" / "spy_double_calendar_strategy.py").read_text()
        assert "_dc_attempt_transform" not in src
        assert "DCPhase.TRANSFORMED" not in src

    def test_D_does_have_one(self):
        src = (ROOT / "bots" / "hydra" / "double_calendar_strategy.py").read_text()
        assert "_dc_attempt_transform" in src


class TestTheCapabilityIsPublished:

    def test_D_transforms(self):
        assert _flag("d") is True

    def test_E_does_not(self):
        assert _flag("e") is False

    def test_they_disagree_so_a_shared_page_needs_the_flag(self):
        assert _flag("d") != _flag("e")

    def test_a_variant_without_the_key_publishes_None_not_False(self):
        """`None` must keep the historical behaviour (show the metrics); only an
        explicit `false` hides them, so an older config is never silently
        stripped of its transform cards."""
        assert _flag("h") is None

    def test_each_config_records_the_reasoning(self):
        for vid in ("d", "e"):
            raw = (CFG / f"config_variant_{vid}.json").read_text()
            assert "_comment_transforms_to_condor" in raw


class TestThePageHidesWhatDoesNotApply:

    def test_the_transform_cards_are_conditional(self):
        assert "transforms !== false" in PAGE.read_text()

    def test_it_says_what_E_does_INSTEAD_rather_than_leaving_a_hole(self):
        """Removing a card and leaving blank space answers nothing. The slot
        states E's actual management model."""
        src = PAGE.read_text()
        assert "Laddered profit-taking + time exit" in src
        assert "does not transform" in src

    def test_undefined_keeps_showing_them(self):
        """`transforms !== false` rather than `transforms === true`: an older
        payload with no flag must be unchanged."""
        src = PAGE.read_text()
        assert "transforms === true" not in src

    def test_the_reason_is_recorded_where_the_defect_was(self):
        src = PAGE.read_text()
        assert "E never transforms" in src
