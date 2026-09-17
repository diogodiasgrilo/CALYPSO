"""Deployed capital must follow the strategy's capital_basis in the DB reader too.

Defect D2, SECOND LOCATION. Phase 3 made ``base_strategy._entry_margin``
basis-aware, but ``dashboard/backend/services/db_reader.py`` kept its own
private copy of the defined-risk assumption, hardcoded in SQL:

    SUM(MAX(call_spread_width, put_spread_width) * 100 * contracts)

A naked strangle has no spread width, so for G every entry contributed 0:

    variant   entries   sum(call_width)   sum(put_width)   capital_deployed
       g        26            0.0              0.0              $0.00
       b       297          865.0           1520.0       $1,404,000.00

``roi_pct`` and ``avg_capital_per_day`` are derived from that number, so both
came back 0 and G's dashboard showed "—" for Return on Margin and Peak Margin
per Day — the exact symptom originally reported.

This is why backfilling ``daily_returns`` alone did NOT fix the display: the
dashboard never reads ``daily_returns`` for these cards. It recomputes capital
from ``trade_entries`` in SQL.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.backend.services.db_reader import BacktestingDBReader  # noqa: E402
from dashboard.backend.services import variant_readers as VR  # noqa: E402


def _make_db(path: Path, *, width: float, contracts: int, n: int, pnl: float) -> None:
    """A tiny DB shaped like the real one. ``width=0`` models a wingless
    strategy; ``width>0`` a defined-risk one."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE daily_summaries (date TEXT PRIMARY KEY, net_pnl REAL, "
        "entries_placed INTEGER, entries_stopped INTEGER)"
    )
    con.execute(
        "CREATE TABLE trade_entries (date TEXT, entry_number INTEGER, "
        "call_spread_width REAL, put_spread_width REAL, contracts INTEGER, "
        "total_credit REAL)"
    )
    con.execute(
        "CREATE TABLE trade_stops (date TEXT, entry_number INTEGER, side TEXT, "
        "net_pnl REAL, stop_time TEXT)"
    )
    for i in range(n):
        d = f"2026-09-{i + 1:02d}"
        con.execute("INSERT INTO daily_summaries VALUES (?,?,1,0)", (d, pnl))
        con.execute("INSERT INTO trade_entries VALUES (?,1,?,?,?,100.0)",
                    (d, width, width, contracts))
    con.commit()
    con.close()


def _overrides(reader: BacktestingDBReader) -> dict:
    return asyncio.run(reader.get_cumulative_overrides())


def test_wingless_strategy_reports_real_capital(tmp_path):
    """The whole defect: width 0 must NOT mean capital 0 under broker_margin."""
    db = tmp_path / "g.db"
    _make_db(db, width=0.0, contracts=1, n=4, pnl=100.0)

    defined = _overrides(BacktestingDBReader(db, capital_basis="defined_risk"))
    assert defined["capital_deployed"] == 0.0, (
        "sanity: the OLD behaviour must still produce 0 for a wingless strategy, "
        "otherwise this test proves nothing about the fix"
    )
    assert defined["roi_pct"] == 0.0
    assert defined["avg_capital_per_day"] == 0.0

    margin = _overrides(BacktestingDBReader(
        db, capital_basis="broker_margin", broker_margin_per_contract=30_000.0))
    assert margin["capital_deployed"] == pytest.approx(4 * 30_000.0)
    assert margin["roi_pct"] > 0, "return on margin must be computable"
    assert margin["avg_capital_per_day"] == pytest.approx(30_000.0)


def test_broker_margin_scales_with_contracts(tmp_path):
    db = tmp_path / "g2.db"
    _make_db(db, width=0.0, contracts=3, n=2, pnl=10.0)
    r = _overrides(BacktestingDBReader(
        db, capital_basis="broker_margin", broker_margin_per_contract=30_000.0))
    assert r["capital_deployed"] == pytest.approx(2 * 3 * 30_000.0)


def test_defined_risk_is_unchanged(tmp_path):
    """A/B/C/F must compute exactly what they always did — this fix must not
    move the live seat's reported capital."""
    db = tmp_path / "b.db"
    _make_db(db, width=5.0, contracts=7, n=3, pnl=100.0)
    r = _overrides(BacktestingDBReader(db))            # default basis
    assert r["capital_deployed"] == pytest.approx(3 * 5.0 * 100 * 7)
    explicit = _overrides(BacktestingDBReader(db, capital_basis="defined_risk"))
    assert explicit["capital_deployed"] == r["capital_deployed"]


def test_default_basis_is_defined_risk():
    """Constructing a reader without the argument must behave as before, so no
    existing caller silently changes meaning."""
    r = BacktestingDBReader(Path("/nonexistent.db"))
    assert r.capital_basis == "defined_risk"
    assert "call_spread_width" in r._capital_sql()


def test_margin_expression_used_only_for_broker_margin():
    margin = BacktestingDBReader(Path("/x.db"), capital_basis="broker_margin",
                                 broker_margin_per_contract=12_345.0)
    assert "12345" in margin._capital_sql().replace(".0", "")
    assert "spread_width" not in margin._capital_sql()


def test_net_debit_falls_through_unchanged():
    """D/E keep the historical expression here on purpose — their own views use
    dc_db_reader. Pinned so the fall-through is a decision, not an oversight."""
    debit = BacktestingDBReader(Path("/x.db"), capital_basis="net_debit")
    plain = BacktestingDBReader(Path("/x.db"))
    assert debit._capital_sql() == plain._capital_sql()


# ─────────────────────────────────────────────────────────────────────────────
# The wiring: the factory must actually hand the basis to the reader
# ─────────────────────────────────────────────────────────────────────────────

def test_factory_resolves_basis_from_the_taxonomy():
    assert VR._capital_basis_for("g") == "broker_margin"
    for vid in ("a", "b", "c", "f"):
        assert VR._capital_basis_for(vid) == "defined_risk"


def test_factory_degrades_on_unknown_id():
    """A bad query param must not raise — it falls back to the historical
    default, matching resolve_for's degradation contract."""
    assert VR._capital_basis_for("zz") == "defined_risk"
    assert VR._broker_margin_for("zz") > 0


def test_reader_for_g_carries_the_margin_basis():
    reader, _ = VR.reader_for("g")
    assert reader.capital_basis == "broker_margin", (
        "the G reader still computes capital with the defined-risk formula, so "
        "its Return on Margin and Peak Margin cards stay blank"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Every per-variant construction site must come through the ONE factory
# ─────────────────────────────────────────────────────────────────────────────

def test_per_variant_readers_are_basis_aware():
    """variants._db_readers is built at import; if it constructs bare readers,
    every /api/variants payload reports a wingless strategy's capital as 0."""
    from dashboard.backend.routers import variants as V

    assert V._db_readers["g"].capital_basis == "broker_margin"
    for vid in ("a", "b", "c", "f"):
        assert V._db_readers[vid].capital_basis == "defined_risk"


def test_snapshot_cumulative_uses_the_factory():
    """_read_variant_cumulative built its own reader, which is why G's cards
    stayed blank even after db_reader itself was fixed. Pin the call site."""
    import inspect
    from dashboard.backend.routers import strategies as S

    src = inspect.getsource(S._read_variant_cumulative)
    assert "db_reader_for_variant" in src, (
        "_read_variant_cumulative constructs a bare BacktestingDBReader again — "
        "it will silently use the defined-risk formula for every strategy."
    )


def test_factory_is_the_single_source_of_the_rule():
    from dashboard.backend.services import variant_readers as VR

    assert VR.db_reader_for_variant("g").capital_basis == "broker_margin"
    assert VR.db_reader_for_variant("b").capital_basis == "defined_risk"
    # Unknown id degrades rather than raising.
    assert VR.db_reader_for_variant("zz").capital_basis == "defined_risk"
