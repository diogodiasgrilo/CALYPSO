"""The off-day (pre-market) view must be per-strategy, not live-seat-only.

Written 2026-09-17 after a Playwright audit of all 42 dashboard surfaces
(docs/DASHBOARD_VISUAL_AUDIT_2026_09_17.md) found the ORIGINAL complaint —
"only B shows the previous day, the others are empty charts" — still live after
the D4/D5 endpoint scoping.

The endpoints were fixed; the VIEW was not. Two distinct defects:

  V1  ``PolledICView`` had no off-day branch at all, so every non-primary
      strategy rendered the full layout unconditionally and drew an empty
      chart, an empty P&L curve and an empty grid pre-market. The previous-day
      cards (``MarketContextBanner`` / ``OffDaySummaryCards``) were reachable
      ONLY from ``PrimaryICView``.

  V2  Those cards read ``best_day`` / ``worst_day`` / ``avg_pnl`` from the
      WebSocket store, which holds the LIVE SEAT's stats only. Every strategy
      therefore displayed B's +$11,852 best day and B's +$258.53 average —
      including G, whose entire lifetime P&L is +$836. Same cross-wiring class
      as the 2026-07-14 fix (4b3d6a0), surviving in the component layer.

  V3  ``OffDaySummaryCards`` fetched ``/api/metrics/daily`` and
      ``/api/hydra/entries`` with NO ``strategy_id``, so even once reachable it
      would have shown the live seat's last day under another strategy's name.

Static checks (source + AST) plus a behavioural check of the new backend
reader, so the whole file runs in the normal suite with no VM.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FRONTEND = ROOT / "dashboard" / "frontend" / "src"
IC_DASHBOARD = FRONTEND / "components" / "dashboard" / "IronCondorDashboard.tsx"
BANNER = FRONTEND / "components" / "market" / "MarketContextBanner.tsx"
STRATEGIES_ROUTER = ROOT / "dashboard" / "backend" / "routers" / "strategies.py"


def _polled_view_source() -> str:
    """Just the PolledICView function body — asserting against the whole file
    would pass on PrimaryICView's copy of the same code and prove nothing."""
    src = IC_DASHBOARD.read_text()
    start = src.index("function PolledICView")
    end = src.index("interface IronCondorDashboardProps")
    return src[start:end]


# ─────────────────────────────────────────────────────────────────────────────
# V1 — the polled view must make the same off-day decision as the primary
# ─────────────────────────────────────────────────────────────────────────────

def test_polled_view_has_an_offday_branch():
    body = _polled_view_source()
    assert "showFullLayout" in body, (
        "PolledICView renders the full layout unconditionally — pre-market every "
        "non-primary strategy draws an empty chart and an empty grid."
    )


def test_polled_view_renders_the_previous_day_cards():
    body = _polled_view_source()
    for component in ("MarketContextBanner", "OffDaySummaryCards"):
        assert f"<{component}" in body, (
            f"{component} is not rendered by PolledICView, so no strategy other "
            f"than the live seat can show a previous day."
        )


def test_polled_offday_decision_matches_the_primary():
    """Both views must gate on the same three signals. If they drift, one of the
    two paths silently gets a different layout again — which is the whole bug."""
    src = IC_DASHBOARD.read_text()
    for view in ("PrimaryICView", "PolledICView"):
        start = src.index(f"function {view}")
        end = src.index("\n}", src.index("showFullLayout", start))
        chunk = src[start:end]
        assert "isLive" in chunk and "hasEntries" in chunk and "hasChartData" in chunk, (
            f"{view} no longer gates the layout on all three of isLive / "
            f"hasEntries / hasChartData — the two views have drifted apart."
        )


# ─────────────────────────────────────────────────────────────────────────────
# V2 — best/worst/avg must come from the SELECTED strategy
# ─────────────────────────────────────────────────────────────────────────────

def test_offday_cards_accept_scoped_comparisons():
    src = BANNER.read_text()
    assert "comparisons?: ComparisonsLike" in src, (
        "MarketContextBanner cannot be given a strategy's own comparisons, so it "
        "falls back to the WS store = the live seat's best/worst day."
    )
    assert "comparisonsProp ?? storeComparisons" in src, (
        "The prop must take precedence over the store; the store is the live "
        "seat's and is correct only for the primary."
    )


def test_polled_view_passes_comparisons_through():
    body = _polled_view_source()
    assert body.count("comparisons={body.comparisons}") >= 2, (
        "Both off-day cards must receive the snapshot's own comparisons; "
        "whichever one is missed shows the live seat's numbers."
    )


def test_snapshot_carries_per_variant_comparisons():
    """The backend must actually supply what the frontend now reads."""
    tree = ast.parse(STRATEGIES_ROUTER.read_text())
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_read_variant_comparisons" in names, (
        "No per-variant comparisons reader — the frontend prop would always be "
        "undefined and silently fall back to the live seat's store values."
    )
    ic = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_ic_snapshot")
    keys = {k.value for n in ast.walk(ic) if isinstance(n, ast.Dict)
            for k in n.keys if isinstance(k, ast.Constant)}
    assert "comparisons" in keys, "_ic_snapshot does not emit a 'comparisons' key."


# ─────────────────────────────────────────────────────────────────────────────
# V3 — the card's own fetches must be strategy-scoped
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("endpoint", ["/api/metrics/daily", "/api/hydra/entries"])
def test_offday_card_fetches_are_scoped(endpoint):
    src = BANNER.read_text()
    # Anchor on the FETCH, not the first textual mention — the endpoint is also
    # named in a type comment, and matching that made this test pass vacuously.
    idx = src.index(f"fetch(`{endpoint}")
    window = src[idx: idx + 200]
    assert "sidParam" in window, (
        f"{endpoint} is fetched without a strategy_id, so the off-day card shows "
        f"the live seat's last day under whichever strategy is selected."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Behavioural — the reader really does return THIS variant's numbers
# ─────────────────────────────────────────────────────────────────────────────

def _make_db(path: Path, rows: list[tuple[str, float]]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE daily_summaries (date TEXT PRIMARY KEY, net_pnl REAL, "
        "entries_placed INTEGER, entries_stopped INTEGER, total_credit REAL)"
    )
    # get_comparison_stats sub-selects avg_credit from trade_entries; without the
    # table the WHOLE query errors and the reader returns None, which would look
    # like "the variant has no data" rather than "the fixture is incomplete".
    con.execute(
        "CREATE TABLE trade_entries (date TEXT, total_credit REAL)"
    )
    con.executemany(
        "INSERT INTO daily_summaries VALUES (?,?,1,0,0)", rows
    )
    con.executemany("INSERT INTO trade_entries VALUES (?,?)", rows)
    con.commit()
    con.close()


def test_comparisons_reader_returns_the_variant_it_was_asked_for(tmp_path, monkeypatch):
    """Two variants with deliberately different extremes. Reading one must never
    return the other's — the exact failure the visual audit caught, where every
    strategy showed the live seat's +$11,852 best day."""
    from dashboard.backend.config import settings
    from dashboard.backend.routers import strategies as S

    db_a = tmp_path / "a.db"
    db_g = tmp_path / "g.db"
    _make_db(db_a, [("2026-09-01", 1475.0), ("2026-09-02", -1985.0)])
    _make_db(db_g, [("2026-09-01", 435.4), ("2026-09-02", -708.8)])

    monkeypatch.setattr(settings, "variant_a_backtesting_db", db_a, raising=False)
    monkeypatch.setattr(settings, "variant_g_backtesting_db", db_g, raising=False)

    a = S._read_variant_comparisons("a")
    g = S._read_variant_comparisons("g")

    assert a.get("best_day") == pytest.approx(1475.0)
    assert g.get("best_day") == pytest.approx(435.4)
    assert a.get("worst_day") == pytest.approx(-1985.0)
    assert g.get("worst_day") == pytest.approx(-708.8)
    # The point of the whole fix: they must differ.
    assert a["best_day"] != g["best_day"]


def test_comparisons_reader_is_missing_db_tolerant(monkeypatch):
    """A dry-run variant with no DB yet must degrade to {}, never raise — the
    sibling _read_variant_* helpers all promise this."""
    from dashboard.backend.config import settings
    from dashboard.backend.routers import strategies as S

    monkeypatch.setattr(settings, "variant_a_backtesting_db", Path("/nope/missing.db"),
                        raising=False)
    assert S._read_variant_comparisons("a") == {}
    # An id with no settings entry at all.
    assert S._read_variant_comparisons("zz") == {}


# ─────────────────────────────────────────────────────────────────────────────
# Regression guard — the conditional hook that the audit's linter surfaced
# ─────────────────────────────────────────────────────────────────────────────

def test_selected_strategy_hook_runs_before_the_early_returns():
    """``useSelectedStrategy`` used to be called AFTER ``if (!market) return null``,
    a rules-of-hooks violation that changed the hook count when market status
    arrived or flipped at the open/close. Benign only because nothing else hooked
    in between — adding any hook above it would have made it a crash."""
    src = BANNER.read_text()
    start = src.index("export function MarketContextBanner")
    body = src[start: src.index("// Determine context", start)]
    hook_at = body.index("useSelectedStrategy(")
    first_return = body.index("if (!market) return null;")
    assert hook_at < first_return, (
        "useSelectedStrategy is called after an early return — React Hooks must "
        "run in the same order on every render."
    )
