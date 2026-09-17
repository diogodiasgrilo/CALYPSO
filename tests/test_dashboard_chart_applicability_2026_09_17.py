"""Analytics must only render charts that are MEANINGFUL for the strategy.

Defect D11. ``pages/Analytics.tsx`` is 1,334 lines with zero taxonomy
references, so every strategy got the same sixteen 0DTE iron-condor charts.
E — a multi-day net-debit SPY calendar — rendered "Rolling Win Rate (10-day)"
flat at 0%, "Avg P&L by Day of Week" for a position opened Monday and closed
Thursday, and a distribution dominated by its 47 no-trade days.

A confident axis of zeros is worse than a blank card: a blank reads as "no data
yet", a populated axis reads as a result.

Two layers:

  1. PAGE level — ``capabilities.analytics`` gates the whole page. The backend
     had computed this all along and the frontend ignored it.
  2. CHART level — within the strategies that CAN drive Analytics, each chart
     declares the taxonomy facts it depends on.

``_capabilities`` itself had to be corrected first: it gated on
``structure_family == "iron_condor"``, which declared G (``"strangle"``)
incapable of History and Analytics even though its data is IC-shaped and both
pages render it correctly. Honouring the old flags would have REMOVED two
working pages from G.

And the two capabilities were then SPLIT, because the pages read different
tables:

  history    reads ``daily_summaries`` (date, net P&L, SPX, VIX) — every
             strategy writes it, so a P&L calendar is always meaningful and the
             flag is always True. Only the IC-specific COLUMNS drop out.
  analytics  reads ``trade_entries`` / ``trade_stops`` deeply, so it gates on
             ``data_kind == "ic_state"``.

One flag for both would either delete a working P&L calendar from the calendars
or hand them sixteen zeroed 0DTE charts.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402
from dashboard.backend.routers import strategies as S  # noqa: E402

APPLICABILITY = ROOT / "dashboard" / "frontend" / "src" / "lib" / "chartApplicability.ts"
ANALYTICS = ROOT / "dashboard" / "frontend" / "src" / "pages" / "Analytics.tsx"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — the page-level capability, and the correction it needed
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "vid, expected",
    [("a", True), ("b", True), ("c", True), ("f", True), ("g", True),
     ("d", False), ("e", False)],
)
def test_analytics_capability_follows_data_kind(vid, expected):
    """ANALYTICS only. `history` deliberately does NOT share this gate — it
    reads daily_summaries, which every strategy writes. See
    test_history_and_analytics_capabilities_differ."""
    caps = S._capabilities(tax.meta(vid))
    assert caps["analytics"] is expected


def test_strangle_keeps_history_and_analytics():
    """The regression the correction exists to prevent. G's structure_family is
    "strangle", so the OLD iron_condor gate declared it incapable — yet its data
    is ic_state and both pages render it correctly today."""

    g = tax.meta("g")
    assert g.structure_family == "strangle", "taxonomy changed; re-check this test"
    assert S._data_kind(g) == "ic_state"
    assert S._capabilities(g)["analytics"] is True, (
        "G would lose two working pages — the gate must be data_kind, not "
        "structure_family."
    )


def test_capability_gate_is_data_kind_not_structure_family():
    """Check the CODE, not the prose. A naive substring search over the whole
    source matches the docstring that explains the old gate — which would ban
    documenting the very bug this guards against."""
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(S._capabilities))).body[0]
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body   # drop the docstring
    code = "\n".join(ast.unparse(n) for n in body)

    assert "_data_kind(m)" in code, "the capability no longer consults data_kind"
    assert "structure_family" not in code, (
        "the iron_condor gate is back in the CODE — it silently disables G's "
        "History and Analytics."
    )


def test_dte_class_is_exposed():
    """A chart bucketed by day-of-week or entry slot needs the holding horizon.
    It lived in the taxonomy all along but was never sent to the frontend."""
    row = S._strategy_meta_dict(tax.meta("e"))
    assert row["dte_class"] == "multi_day"
    assert S._strategy_meta_dict(tax.meta("b"))["dte_class"] == "0DTE"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — per-chart applicability, evaluated against the REAL taxonomy
# ─────────────────────────────────────────────────────────────────────────────

def _parse_specs() -> dict[str, list[tuple[str, set[str]]]]:
    """Read ANALYTICS_CHARTS out of the TS module so the test cannot drift from
    the source of truth by being hand-copied."""
    src = APPLICABILITY.read_text()
    block = src[src.index("export const ANALYTICS_CHARTS"): src.index("/** The facts a strategy supplies")]
    out: dict[str, list[tuple[str, set[str]]]] = {}
    tab = None
    for line in block.splitlines():
        m = re.match(r"\s*(\w+):\s*\[", line)
        if m:
            tab = m.group(1)
            out[tab] = []
            continue
        m = re.search(r'\{\s*title:\s*"([^"]+)"(?:,\s*requires:\s*\{([^}]*)\})?', line)
        if m and tab:
            reqs = set(re.findall(r"(\w+):\s*true", m.group(2) or ""))
            out[tab].append((m.group(1), reqs))
    return out


#: Each fact, and the taxonomy field its TS expression MUST derive from.
#: Without this the Python mirror below is the only thing under test, so
#: replacing a TS fact with a constant (`bothSides: true`) changes real
#: behaviour while every test still passes — a mutant that survived once.
FACT_SOURCE_FIELD = {
    "credit": "pnl_shape",
    "sameDaySession": "dte_class",
    "bothSides": "sides",
    "definedWings": "capital_basis",
    "ironCondorEntryTypes": "family",
    "trendSignal": "data_kind",
}


def test_each_fact_derives_from_its_taxonomy_field():
    """Pin the TS implementation, not just the mirror."""
    src = APPLICABILITY.read_text()
    body = src[src.index("export function factsFor"): src.index("/** Human explanation")]
    # The real (non-null) branch is the second `return {` in the function.
    real = body[body.index("return {", body.index("}", body.index("if (!s)")) ):]
    for fact, field in FACT_SOURCE_FIELD.items():
        m = re.search(rf"{fact}:\s*([^,\n]+)", real)
        assert m, f"factsFor no longer defines {fact}"
        expr = m.group(1)
        assert f"s.{field}" in expr, (
            f"{fact} is computed as `{expr.strip()}` — it must derive from "
            f"s.{field}, not a constant or another field, or the chart filter "
            f"silently stops responding to the taxonomy."
        )


def _facts(vid: str) -> dict[str, bool]:
    """Mirror of factsFor() in the TS module, driven by the real taxonomy.

    Kept in step with the TS by ``test_each_fact_derives_from_its_taxonomy_field``
    above, which pins the expressions themselves."""
    m = tax.meta(vid)
    return {
        "credit": m.pnl_shape != "debit",
        "sameDaySession": m.dte_class != "multi_day",
        "bothSides": m.sides != "one_sided",
        "definedWings": m.capital_basis != "broker_margin",
        "ironCondorEntryTypes": m.structure_family == "iron_condor" and m.sides != "one_sided",
        "trendSignal": S._data_kind(m) == "ic_state",
    }


def _hidden(vid: str) -> set[str]:
    specs = _parse_specs()
    facts = _facts(vid)
    return {
        title
        for charts in specs.values()
        for title, reqs in charts
        if not all(facts[r] for r in reqs)
    }


def test_the_registry_parses():
    specs = _parse_specs()
    assert set(specs) == {"performance", "entries", "stops", "market"}
    assert sum(len(v) for v in specs.values()) == 16


def test_iron_condors_see_every_chart():
    """A/B/C are the shape the charts were designed for — hiding anything from
    them would be a regression, not a fix."""
    for vid in ("a", "b", "c"):
        assert _hidden(vid) == set(), f"{vid} lost charts: {_hidden(vid)}"


def test_one_sided_strategy_hides_the_two_sided_charts():
    """F places a one-sided vertical; a call-vs-put ratio describes a side it
    never trades."""
    hidden = _hidden("f")
    assert "Call vs Put Stop Ratio" in hidden
    assert "Entry Type Breakdown" in hidden
    # ...but it is still a credit 0DTE strategy, so the rest must survive.
    assert "Avg Credit by Time Slot" not in hidden
    assert "Survival Rate by OTM Distance" not in hidden


def test_wingless_strategy_hides_the_width_chart():
    """G has no spread width, so OTM-distance bucketing has no denominator."""
    hidden = _hidden("g")
    assert "Survival Rate by OTM Distance" in hidden
    assert "Entry Type Breakdown" in hidden
    # A strangle has two real sides, so the call-vs-put comparison IS meaningful.
    assert "Call vs Put Stop Ratio" not in hidden


def test_multiday_calendar_hides_every_intraday_chart():
    """Even though the page-level gate stops D/E reaching Analytics today, the
    chart-level facts must still be right — the page gate is one flag away from
    changing, and a calendar analytics view is on the roadmap."""
    hidden = _hidden("e")
    for title in (
        "Avg P&L by Day of Week",
        "Avg Credit by Time Slot",
        "Avg P&L by Entry Number",
        "Stop Rate by Time Slot",
    ):
        assert title in hidden, f"{title} should not apply to a multi-day calendar"


def test_universal_charts_are_never_hidden():
    """P&L over time, its distribution, and the market-regime charts apply to
    any strategy that has daily P&L at all."""
    universal = {
        "Cumulative P&L", "Daily P&L Distribution", "VIX vs Daily P&L",
        "Day Range vs P&L", "Avg P&L by Market Direction",
    }
    for vid in tax.available_ids():
        assert not (universal & _hidden(vid)), (
            f"{vid} hid a universal chart: {universal & _hidden(vid)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# The wiring
# ─────────────────────────────────────────────────────────────────────────────

def test_chart_titles_match_the_rendered_cards():
    """The registry keys on the ChartCard title. A typo silently hides nothing
    (or everything), so every declared title must exist in the page."""
    page = ANALYTICS.read_text()
    for charts in _parse_specs().values():
        for title, _ in charts:
            assert f'<ChartCard title="{title}"' in page, (
                f'no ChartCard titled "{title}" — the registry has drifted from '
                f"the page and filters nothing."
            )


def test_analytics_consults_applicability_and_capabilities():
    page = ANALYTICS.read_text()
    assert "applicableCharts" in page and "hiddenReasons" in page
    assert "capabilities?.analytics === false" in page, (
        "the page no longer honours the capability flag, so a calendar renders "
        "0DTE charts again."
    )


def test_hidden_charts_are_named_not_silently_dropped():
    """A reader must be able to tell 'does not apply' from 'is broken'."""
    page = ANALYTICS.read_text()
    assert "not shown for" in page
    src = APPLICABILITY.read_text()
    assert "const REASONS" in src
    for key in ("credit", "sameDaySession", "bothSides", "definedWings",
                "ironCondorEntryTypes", "trendSignal"):
        assert f"{key}:" in src.split("const REASONS")[1].split("}")[0], (
            f"no human reason for {key} — the footnote would print 'undefined'."
        )


def test_absent_meta_hides_nothing():
    """Meta not loaded must render the FULL grid, never a filtered one — an
    unknown strategy losing real charts would be a worse bug than the one being
    fixed."""
    src = APPLICABILITY.read_text()
    block = src[src.index("export function factsFor"): src.index("/** Human explanation")]
    assert "if (!s)" in block
    # every fact defaults true in the null branch
    null_branch = block[block.index("if (!s)"): block.index("return {", block.index("if (!s)") + 40)]
    assert "false" not in null_branch


# ─────────────────────────────────────────────────────────────────────────────
# History reads DIFFERENT data from Analytics, so it gets its own answer
# ─────────────────────────────────────────────────────────────────────────────

HISTORY = ROOT / "dashboard" / "frontend" / "src" / "pages" / "History.tsx"
SUMMARY_TABLE = (ROOT / "dashboard" / "frontend" / "src" / "components" / "history"
                 / "DailySummaryTable.tsx")


@pytest.mark.parametrize("vid", ["a", "b", "c", "d", "e", "f", "g"])
def test_history_applies_to_every_strategy(vid):
    """History reads daily_summaries — date, net P&L, SPX, VIX — which every
    strategy writes, calendars included (D has 48 rows, E has 52). Gating it off
    would delete a working P&L calendar; only the IC-specific COLUMNS go."""
    assert S._capabilities(tax.meta(vid))["history"] is True


def test_history_and_analytics_capabilities_differ():
    """They read different tables, so one flag cannot serve both without either
    deleting a useful page or serving zeroed charts."""
    e = S._capabilities(tax.meta("e"))
    assert e["history"] is True and e["analytics"] is False


def test_entry_and_stop_columns_are_intraday_only():
    """D13: a calendar books P&L on a close day that opened nothing, so
    "0 entries" beside a real P&L reads as broken. Drop the column instead."""
    src = SUMMARY_TABLE.read_text()
    block = src[src.index("const COLUMNS"): src.index("];", src.index("const COLUMNS"))]
    for key in ("entries_placed", "entries_stopped"):
        row = next(l for l in block.splitlines() if key in l)
        assert "intradayOnly: true" in row, f"{key} is not marked intraday-only"
    for key in ("date", "net_pnl", "spx_close", "vix_open"):
        row = next(l for l in block.splitlines() if f'"{key}"' in l)
        assert "intradayOnly" not in row, (
            f"{key} is universal — marking it intraday-only would blank a real "
            f"column for the calendars."
        )


def test_history_passes_the_horizon_from_the_taxonomy():
    page = HISTORY.read_text()
    assert 'strategy?.dte_class !== "multi_day"' in page, (
        "History no longer derives the horizon from dte_class, so the "
        "intraday-only columns cannot be dropped for a calendar."
    )
    assert "intraday={intraday}" in page


def test_missing_vix_renders_as_a_dash_not_zero():
    """D12: market-holiday rows carry a falsy VIX. `?? 0` printed a literal
    0.0 — an impossible reading presented as data."""
    src = SUMMARY_TABLE.read_text()
    assert "day.vix_open ? day.vix_open.toFixed(1)" in src, (
        "a falsy VIX is being coerced to a number again"
    )


def test_table_defaults_to_intraday():
    """Every existing caller omits the prop and must be unchanged."""
    src = SUMMARY_TABLE.read_text()
    assert "intraday = true" in src
