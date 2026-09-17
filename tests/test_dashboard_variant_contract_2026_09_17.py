"""PHASE 0 — the contract every strategy's dashboard view must satisfy.

Written 2026-09-17, after the dashboard was shown to investors with several
strategies rendering empty or showing metrics whose denominator does not exist.
Plan: docs/DASHBOARD_REBUILD_PLAN.md. Audit: docs/DASHBOARD_VARIANT_AUDIT_2026_09_17.md.

WHY THIS FILE EXISTS BEFORE ANY FIX. Without a test that fails today and names
each defect, "fixed" is an opinion. Every defect below is marked
``xfail(strict=True)``:

  * TODAY  — the assertion fails, pytest reports XFAIL, the suite stays green.
  * FIXED  — the assertion passes, pytest reports XPASS, and because the marker
             is STRICT that is a BUILD FAILURE telling you to delete the marker.

So this file cannot rot into a permanently-red wall that everyone learns to
ignore, and it cannot silently keep passing a defect that was quietly fixed.

EVERY CHECK IS STATIC (source / AST / taxonomy). None needs the VM, a database
or a live broker, so they run in the normal suite on every commit.

The defect IDs (D1…D8) match the plan document exactly.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402

ROUTERS = ROOT / "dashboard" / "backend" / "routers"


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _endpoint_params(router: str, path: str) -> list[str] | None:
    """Parameter names of the handler registered at `path`, or None if absent."""
    tree = ast.parse((ROUTERS / f"{router}.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if not isinstance(d, ast.Call):
                continue
            if getattr(d.func, "attr", None) not in ("get", "post"):
                continue
            if d.args and isinstance(d.args[0], ast.Constant) and d.args[0].value == path:
                return [a.arg for a in node.args.args]
    return None


def _is_scoped(router: str, path: str) -> bool:
    params = _endpoint_params(router, path) or []
    return any(p in ("strategy_id", "variant_id", "vid") for p in params)


# ─────────────────────────────────────────────────────────────────────────────
# The contract that ALREADY holds — these must stay green, they are the floor
# ─────────────────────────────────────────────────────────────────────────────

class TestWhatAlreadyWorks:
    """Guard rails. If one of these breaks, a 'fix' regressed the shared model."""

    def test_every_variant_is_in_the_taxonomy(self):
        assert set(tax.available_ids()) >= {"a", "b", "c", "d", "e", "f", "g"}

    def test_every_variant_has_a_group_and_a_pnl_shape(self):
        for vid in tax.available_ids():
            g = tax.group(vid)
            assert g.pnl_shape in ("credit", "debit"), vid
            assert g.id, vid

    def test_the_endpoints_fixed_in_july_are_still_scoped(self):
        """4b3d6a0 scoped these. A regression here re-breaks the History page."""
        assert _is_scoped("hydra", "/entries")
        assert _is_scoped("market", "/replay_pnl")
        assert _is_scoped("metrics", "/daily")

    def test_the_per_strategy_snapshot_is_scoped(self):
        assert _is_scoped("strategies", "/{strategy_id}/snapshot")


# ─────────────────────────────────────────────────────────────────────────────
# D1 — total_trades is a dead field
# ─────────────────────────────────────────────────────────────────────────────

def test_D1_total_trades_is_either_populated_or_gone():
    """Measured on the VM: every variant reports total_trades=0 while
    total_entries is 352 / 84 / 26. Anything derived from trade count — win rate
    per trade, average per trade — is therefore broken on EVERY strategy,
    including B. Fix by removing the field (preferred) or populating it.

    FIXED 2026-09-17 by removal (Phase 7). Guard rail from here on: it must never
    be reintroduced as a declared-but-never-incremented field.

    NOTE the separate `total_trades` in shared/logger_service.py is a DIFFERENT
    dict — the Google-Sheets daily-summary payload, alongside trades_today /
    winning_trades — and Sheets is disabled on all 7 variants, so that path is
    dead. Deliberately left alone rather than renamed into a spreadsheet schema
    nobody is writing to."""
    src = (ROOT / "bots" / "hydra" / "base_strategy.py").read_text()
    declared = '"total_trades": 0' in src
    incremented = any(
        tok in src for tok in ('total_trades"] +=', "total_trades'] +=", "total_trades += ")
    )
    assert (not declared) or incremented, (
        "total_trades is declared but never incremented — a field that has read 0 "
        "forever is a trap; remove it or populate it from total_entries"
    )


# ─────────────────────────────────────────────────────────────────────────────
# D2 / D3 — capital is modelled as spread width, which G does not have
# ─────────────────────────────────────────────────────────────────────────────

def test_D2_capital_model_dispatches_on_capital_basis():
    """`if entry.spread_width <= 0: continue` skipped EVERY entry of a naked
    strangle, so G had 0 daily_returns rows against 26 entries — no
    return-on-capital, no average capital per day. Return on spread width is not
    missing for G; it is UNDEFINED.

    FIXED 2026-09-17 (Phase 3) via an overridable per-entry hook rather than a
    literal basis check inside the sweep — the sweep carries several hard-won
    timestamp fixes and should not be re-entered. This asserts the PROPERTY
    (capital varies by basis, and the sweep asks the hook) rather than the
    spelling, which is what the original version of this test got wrong.
    """
    from bots.hydra.base_strategy import MEICStrategy
    from bots.hydra.strangle_strategy import StrangleStrategy

    assert hasattr(MEICStrategy, "_entry_margin"), "no per-entry capital hook"
    sweep = inspect.getsource(MEICStrategy._calculate_capital_deployed)
    assert "_entry_margin(entry)" in sweep, "the sweep does not use the hook"
    assert "spread_width <= 0" not in sweep, (
        "the sweep still skips wingless entries inline — a broker-margin "
        "strategy would report capital 0 again"
    )
    assert "_entry_margin" in StrangleStrategy.__dict__, (
        "the one broker_margin strategy does not override the hook, so it "
        "silently reverts to defined-risk width"
    )


def test_D3_sortino_distinguishes_no_data_from_zero():
    """G's daily_returns is empty, so the Sortino computation averages nothing
    and yields a number. Missing data must read as None/absent, never as 0.0 —
    a fabricated 0.0 is indistinguishable from a real result.

    FIXED 2026-09-17 (Phase 3). Guard rail from here on."""
    from bots.hydra.base_strategy import MEICStrategy

    src = inspect.getsource(MEICStrategy._calculate_sortino_ratio)
    assert "return None" in src, (
        "with <2 return rows this returns 0.0, which renders as a real value"
    )


# ─────────────────────────────────────────────────────────────────────────────
# D4 / D5 — endpoints that silently answer for the live seat
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("router,path", [("hydra", "/summary"), ("metrics", "/cumulative")])
def test_D4_previous_day_endpoints_are_variant_scoped(router, path):
    """These feed the summary and cumulative cards. Unscoped, they answered for
    the LIVE SEAT no matter which strategy was picked — which is precisely why
    only B appeared to show the previous day. The July fix (4b3d6a0) scoped
    /hydra/entries and /market/replay_pnl and stopped there; Phase 1 completed it.

    FIXED 2026-09-17. Kept as a GUARD RAIL — a regression here silently restores
    "every strategy shows the live seat's numbers"."""
    assert _is_scoped(router, path), f"{router}{path} takes no strategy_id"


@pytest.mark.parametrize(
    "router,path",
    [("hydra", "/state"), ("hydra", "/bot-config"), ("metrics", "/range")],
)
def test_D5_live_state_endpoints_are_variant_scoped(router, path):
    # FIXED in Phase 1 (2026-09-17). Guard rail from here on.
    assert _is_scoped(router, path), f"{router}{path} takes no strategy_id"


# ─────────────────────────────────────────────────────────────────────────────
# D6 — the calendar page can only ever be D
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="D6: routers/dc.py hardcodes variant_d")
def test_D6_calendar_router_is_not_hardcoded_to_D():
    """E is a double calendar with its own dc_calendar.db, but /api/dc/status
    reads settings.variant_d_state_file and variant_d_baseline_date. E's
    calendar view can never show E's own data."""
    src = (ROUTERS / "dc.py").read_text()
    hardcoded = [ln.strip() for ln in src.splitlines() if "variant_d" in ln]
    assert not hardcoded, f"dc.py hardcodes variant_d: {hardcoded}"


# ─────────────────────────────────────────────────────────────────────────────
# D7 — the taxonomy cannot express a one-sided strategy or an undefined risk
# ─────────────────────────────────────────────────────────────────────────────

def test_D7_taxonomy_can_express_capital_basis_and_sides():
    """The renderer needs two facts the taxonomy cannot currently state:

      capital_basis  defined_risk | net_debit | broker_margin
      sides          one_sided    | two_sided

    Without them G (naked strangle, broker margin) is indistinguishable from an
    iron condor, and F (one-sided vertical) renders a side it never trades —
    which is why F's first recorded entry carried a phantom short_call of 0.0.
    """
    fields = set(tax.StrategyMeta.__dataclass_fields__)
    assert {"capital_basis", "sides"} <= fields, f"StrategyMeta has: {sorted(fields)}"


def test_D7_G_declares_broker_margin_capital():
    """G's group is already `undefined_risk_0dte` and comparable=False, so the
    taxonomy knows it is different — but pnl_shape is `credit`, identical to the
    iron condors, and pnl_shape is what drives the renderer AND the capital
    model. The missing axis is the capital basis, not the P&L shape: G genuinely
    belongs on the credit axis."""
    assert getattr(tax.meta("g"), "capital_basis", None) == "broker_margin"


def test_D7_F_declares_itself_one_sided():
    assert getattr(tax.meta("f"), "sides", None) == "one_sided"


# ─────────────────────────────────────────────────────────────────────────────
# D8 — nothing can render a pre-market view
# ─────────────────────────────────────────────────────────────────────────────

def test_D8_snapshot_exposes_a_previous_session():
    """Pre-market the snapshot returns the freshly-reset day, so entries / ohlc /
    spx_open are empty for EVERY variant including B. B only looks correct
    because the main page reads the unscoped endpoints in D4/D5. Every strategy
    needs an explicit, clearly-labelled last-completed-session block."""
    src = (ROUTERS / "strategies.py").read_text()
    assert "previous_session" in src, (
        "no previous-session fallback — pre-market is empty for all 7 variants"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The cross-cutting invariant the whole rebuild is FOR
# ─────────────────────────────────────────────────────────────────────────────

def test_every_strategy_can_state_what_its_capital_means():
    """The single sentence this rebuild exists to make true: a dashboard must
    never show a metric whose denominator does not exist for that strategy.

    Each variant must be able to say what its capital basis is, so the view can
    choose cards that are meaningful instead of rendering return-on-width for a
    position that has no width."""
    expected = {
        "a": "defined_risk", "b": "defined_risk", "c": "defined_risk",
        "f": "defined_risk",
        "d": "net_debit", "e": "net_debit",
        "g": "broker_margin",
    }
    actual = {v: getattr(tax.meta(v), "capital_basis", None) for v in expected}
    assert actual == expected, f"got {actual}"


class TestPhase2GuardRails:
    """Added when capital_basis / sides landed (Phase 2, 2026-09-17).

    These stop the taxonomy regressing to the state that caused the whole
    rebuild: a strategy whose view cannot know what its own capital means.
    """

    def test_every_strategy_declares_a_known_capital_basis(self):
        valid = {"defined_risk", "net_debit", "broker_margin"}
        for vid in tax.available_ids():
            assert tax.meta(vid).capital_basis in valid, vid

    def test_every_strategy_declares_known_sides(self):
        for vid in tax.available_ids():
            assert tax.meta(vid).sides in {"one_sided", "two_sided"}, vid

    def test_capital_basis_is_INDEPENDENT_of_pnl_shape(self):
        """The design decision this rebuild turns on. G and B share a pnl_shape
        (both sell premium, both belong on the credit axis) but have different
        capital bases. If these two fields ever move together, someone has
        collapsed them back and G will render as an iron condor again."""
        b, g = tax.meta("b"), tax.meta("g")
        assert b.pnl_shape == g.pnl_shape == "credit"
        assert b.capital_basis != g.capital_basis

    def test_the_meta_endpoint_actually_ships_them(self):
        """A taxonomy field the API does not expose cannot reach the renderer."""
        src = (ROUTERS / "strategies.py").read_text()
        assert '"capital_basis": m.capital_basis' in src
        assert '"sides": m.sides' in src


class TestPhase5PreviousSession:
    """D8 fixed 2026-09-17. Pre-market a bot has reset, so the live fields are
    legitimately empty — but blank is indistinguishable from broken, which is the
    impression the dashboard actually gave."""

    SRC = (ROUTERS / "strategies.py").read_text()

    def test_it_reads_the_variants_OWN_database(self):
        """Reading the canonical DB here would reintroduce the very bug Phase 1
        fixed: every strategy showing the live seat's last session."""
        i = self.SRC.index("def _read_previous_session")
        body = self.SRC[i:i + 2600]
        assert 'f"variant_{vid}_backtesting_db"' in body
        assert "canonical_db_reader" not in body

    def test_it_never_returns_todays_row_as_the_previous_session(self):
        i = self.SRC.index("def _read_previous_session")
        assert 'd == today' in self.SRC[i:i + 2600]

    def test_it_is_a_SEPARATE_block_not_merged_into_the_live_fields(self):
        """Back-filling yesterday's numbers into today's fields would be far
        worse than a blank chart — it would be silently wrong."""
        i = self.SRC.index('"previous_session":')
        assert '_read_previous_session(vid,' in self.SRC[i:i + 160]

    def test_missing_data_degrades_to_empty_not_fabricated(self):
        i = self.SRC.index("def _read_previous_session")
        body = self.SRC[i:i + 2600]
        assert body.count("return {}") >= 3, "must degrade to {} on every failure path"

    def test_it_cannot_500_the_snapshot(self):
        i = self.SRC.index("def _read_previous_session")
        assert "except Exception" in self.SRC[i:i + 2600]

    def test_calendars_are_deliberately_excluded(self):
        """D/E hold MULTI-DAY positions that do not reset daily, and their
        snapshot already carries recent_outcomes. Forcing a 0DTE concept onto
        them would repeat the mistake this whole rebuild is correcting."""
        i = self.SRC.index("def _dc_snapshot")
        j = self.SRC.index("def ", i + 10)
        assert "previous_session" not in self.SRC[i:j]
