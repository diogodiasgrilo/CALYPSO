"""Second full sweep — a different lens, because a repeated lens reproduces
its own results.

Pass 1 checked ENTRY rules (instrument, core quantity, proxy filters,
instrument-dependent constants, whose parameters). This pass checks:

* **EXIT and management rules** — where pass 1 barely looked.
* **Which store each dashboard widget reads**, systematically, rather than one
  banner at a time. That is the generalisation of the FOMC-banner defect: a
  widget reading the PRIMARY seat's data while a different variant is selected.
* **A/B/C's dashboard**, which had never been audited at all (their strategy
  fidelity is out of scope — they are the MEIC/Brandon lineage, not single-video
  builds — but they share the iron-condor page with F and G).

FINDINGS THIS PASS
------------------
**F's exits are faithful**, confirmed against the article rather than assumed:
*"Once the position reaches around a 25% profit, Jamaal will often move his stop
toward breakeven or even lock in a small gain of around 10%"* — F arms at 25%
and locks at breakeven, the conservative end of his stated range. The article
also independently confirms the expected move is *"derived from the options
market itself"* (the 2026-09-23 straddle fix) and the 1 PM cutoff.

**The log feed was showing the wrong strategy's log.** The dashboard process
tails ONE log — the primary's. ``LiveLogFeed`` took no props and rendered headed
"Live Log" on every variant's page, so selecting G showed B's lines under a
heading that implied they were G's. Worse, ``IronCondorDashboard``'s module
docstring asserted a note was shown off-primary. There was no such note.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COMPONENTS = ROOT / "dashboard" / "frontend" / "src" / "components"
LOGFEED = COMPONENTS / "logs" / "LiveLogFeed.tsx"
ICDASH = COMPONENTS / "dashboard" / "IronCondorDashboard.tsx"
CFG = ROOT / "bots" / "hydra" / "config"


class TestTheLogFeedNamesWhoseLogItIs:
    """The dashboard tails ONE process. Anything else it shows must say so."""

    def test_it_accepts_the_viewed_strategy(self):
        assert "viewingStrategy" in LOGFEED.read_text()

    def test_it_says_the_lines_are_NOT_the_viewed_strategys(self):
        src = LOGFEED.read_text()
        assert "the live seat's process, not" in src

    def test_the_caveat_only_appears_off_primary(self):
        """On the primary view the lines ARE that strategy's, so the note would
        be noise."""
        assert "{viewingStrategy && (" in LOGFEED.read_text()

    def test_the_dashboard_passes_the_selection(self):
        assert "viewingStrategy={selected?.display_name" in ICDASH.read_text()

    def test_the_docstring_no_longer_claims_a_note_that_did_not_exist(self):
        """It asserted one from the start. Documentation describing a safeguard
        that was never built is worse than no documentation."""
        src = ICDASH.read_text()
        assert '"log lives on the variant\'s own process" note off-primary' not in src
        assert "it did not" in src or "did\n * not" in src


class TestEveryPrimaryStoreWidgetIsEitherScopedOrLabelled:
    """The generalisation of the FOMC-banner defect, checked once rather than
    discovered one widget at a time.

    A component reading the global/primary store is fine IF it (a) also accepts
    a prop so the selected view can scope it, or (b) is genuinely process-global
    and says so. This pins the classification so a NEW widget cannot quietly
    join the list unexamined.
    """

    #: Widgets that read the primary store and are SCOPED by props from the
    #: selected view (they fall back to the store only on the primary path).
    SCOPED = {
        "entries/EntryTimeline.tsx", "entries/EntryGrid.tsx",
        "market/SPXChart.tsx", "market/PositionHeatmap.tsx",
        "pnl/PnLCurve.tsx", "pnl/DailyPnLCard.tsx",
        "dashboard/IronCondorDashboard.tsx",
    }
    #: Genuinely process- or app-global, and correct to read the primary.
    GLOBAL = {
        "auth/LoginGate.tsx", "layout/StatusBar.tsx", "layout/Header.tsx",
        "agents/AgentStatusPanel.tsx", "shared/CommandPalette.tsx",
        "shared/ToastContainer.tsx", "shared/StrategySwitcher.tsx",
        "pnl/PerformanceMetrics.tsx",
    }
    #: Reads the primary but shows another strategy's page — must LABEL itself.
    LABELLED = {"logs/LiveLogFeed.tsx", "market/MarketContextBanner.tsx"}

    def _readers(self):
        out = set()
        for f in COMPONENTS.rglob("*.tsx"):
            if "useHydraStore" in f.read_text():
                out.add(str(f.relative_to(COMPONENTS)))
        return out

    def test_every_reader_is_classified(self):
        """A widget nobody has classified is a widget nobody has checked."""
        known = self.SCOPED | self.GLOBAL | self.LABELLED
        unclassified = self._readers() - known
        assert not unclassified, (
            f"new primary-store readers are unclassified: {sorted(unclassified)}. "
            f"Decide whether each is scoped by props, genuinely global, or must "
            f"label whose data it shows — that decision is what the FOMC banner "
            f"and the log feed both got wrong.")

    def test_the_classification_has_not_gone_stale(self):
        missing = (self.SCOPED | self.LABELLED) - self._readers()
        assert not missing, f"no longer read the primary store: {sorted(missing)}"

    @pytest.mark.parametrize("rel", sorted(LABELLED))
    def test_the_labelled_ones_actually_label(self, rel):
        src = (COMPONENTS / rel).read_text()
        assert ("viewingStrategy" in src or "useSelectedSnapshotStore" in src), (
            f"{rel} shows primary data on another strategy's page without "
            f"naming the source")

    @pytest.mark.parametrize("rel", sorted(SCOPED))
    def test_the_scoped_ones_accept_a_prop(self, rel):
        src = (COMPONENTS / rel).read_text()
        assert re.search(r"Props\b|\bprops\b|\{\s*\w+:", src), rel


class TestFsExitRulesMatchTheArticle:
    """Pass 1 checked F's entry. These are the exits, confirmed against the
    written source rather than assumed."""

    G = json.loads((CFG / "config_variant_f.json").read_text())["strategy"]["ghauri"]

    def test_the_trail_arms_at_the_stated_25_percent(self):
        """*"Once the position reaches around a 25% profit…"*"""
        assert self.G["trail_arm_pct"] == 0.25

    def test_the_lock_sits_inside_his_stated_range(self):
        """*"…move his stop toward breakeven or even lock in a small gain of
        around 10%."* Breakeven (0.0) is the conservative end of that range;
        anything above 10% would be inventing a tighter rule than he states."""
        assert 0.0 <= self.G["trail_lock_pct"] <= 0.10

    def test_the_targets_are_his(self):
        assert self.G["profit_target_pct"] == 0.50
        assert self.G["pct_of_credit"] == 1.00

    def test_the_cutoff_is_his_1pm(self):
        assert self.G["entry_cutoff_time"] == "13:00"

    def test_the_first_hour_PREFERENCE_is_telemetry_not_a_gate(self):
        """He *prefers* the first 30-60 minutes but states the 1 PM cutoff as
        the rule. F gates on the rule and only LOGS the preference, which is the
        right discrimination — turning a preference into a veto would invent a
        constraint he does not impose."""
        src = (ROOT / "bots" / "hydra" / "ghauri_strategy.py").read_text()
        assert "outside the preferred first-hour window" in src
        assert "preferred_window_min" in src
