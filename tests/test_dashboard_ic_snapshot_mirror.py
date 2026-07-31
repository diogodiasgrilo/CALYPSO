"""Full-mirror IC snapshot enrichment (2026-06).

Pins that ``GET /api/strategies/{id}/snapshot`` for an AVAILABLE IC variant now
carries the FULL-MIRROR enrichment so a non-primary IC selection renders the
SAME panels as the primary WS view:

  * ``ohlc``        — the variant's OWN today 1-min SPX bars (market_ohlc_1min),
                      in the {timestamp,open,high,low,close,vix} shape SPXChart
                      consumes (matches /api/market/ohlc).
  * ``cumulative``  — the variant's OWN lifetime metrics (DB-canonical override
                      of its hydra_metrics.json), in the DailyPnLCard shape.
  * ``performance`` — the variant's OWN daily P&L array + advanced stats, in the
                      /api/metrics/performance shape PerformanceMetrics computes
                      ratios from.

And that all three reads are MISSING-FILE-TOLERANT (no DB / no metrics → empty
structures, never a 500), and that the reads come from the VARIANT'S OWN files
(not the canonical-C ones).

Runs in the dashboard env (FastAPI); skips cleanly where it isn't installed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.config import settings  # noqa: E402
from dashboard.backend.services.market_status import get_today_et  # noqa: E402

TODAY = get_today_et()


def _seed_ic_db(path: Path) -> None:
    """Seed a minimal backtesting.db with the tables the IC snapshot readers
    touch: market_ohlc_1min (today), daily_summaries (3 days), trade_entries +
    trade_stops (for the cumulative override)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE market_ohlc_1min (
            timestamp TEXT, open REAL, high REAL, low REAL, close REAL, vix REAL
        );
        CREATE TABLE market_ticks (
            timestamp TEXT, spx_price REAL, vix_level REAL
        );
        CREATE TABLE daily_summaries (
            date TEXT PRIMARY KEY, net_pnl REAL
        );
        CREATE TABLE trade_entries (
            date TEXT, entry_number INTEGER, total_credit REAL,
            call_spread_width REAL, put_spread_width REAL, contracts INTEGER
        );
        CREATE TABLE trade_stops (
            date TEXT, entry_number INTEGER, side TEXT, net_pnl REAL
        );
        """
    )
    conn.executemany(
        "INSERT INTO market_ohlc_1min (timestamp, open, high, low, close, vix) VALUES (?,?,?,?,?,?)",
        [
            (f"{TODAY} 10:45:00", 5000.0, 5005.0, 4998.0, 5002.0, 14.0),
            (f"{TODAY} 10:46:00", 5002.0, 5008.0, 5001.0, 5006.0, 14.1),
            (f"{TODAY} 10:47:00", 5006.0, 5007.0, 5003.0, 5004.0, 14.0),
        ],
    )
    conn.executemany(
        "INSERT INTO daily_summaries (date, net_pnl) VALUES (?,?)",
        [("2026-06-12", 120.0), ("2026-06-13", -50.0), ("2026-06-15", 80.0)],
    )
    conn.executemany(
        "INSERT INTO trade_entries (date, entry_number, total_credit, call_spread_width, put_spread_width, contracts) VALUES (?,?,?,?,?,?)",
        [
            ("2026-06-12", 1, 250.0, 5.0, 5.0, 10),
            ("2026-06-13", 1, 180.0, 5.0, 5.0, 10),
            ("2026-06-15", 1, 210.0, 5.0, 5.0, 10),
        ],
    )
    conn.execute(
        "INSERT INTO trade_stops (date, entry_number, side, net_pnl) VALUES (?,?,?,?)",
        ("2026-06-13", 1, "put", -200.0),
    )
    conn.commit()
    conn.close()


def _seed_state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "date": TODAY,
                "state": "MONITORING",
                "entries": [
                    {
                        "entry_number": 1,
                        "entry_time": f"{TODAY}T10:45:03-04:00",
                        "short_call_strike": 5100,
                        "long_call_strike": 5105,
                        "short_put_strike": 4900,
                        "long_put_strike": 4895,
                        "call_spread_credit": 120,
                        "put_spread_credit": 130,
                        "call_side_stop": 3.0,
                        "put_side_stop": 4.0,
                        "call_spread_value": 1.0,
                        "put_spread_value": 1.2,
                    }
                ],
                "total_credit_received": 250.0,
                "total_realized_pnl": 0.0,
                "total_commission": 5.0,
                "pnl_history": [{"time": "10:46", "pnl": 30.0}],
                "market_data_ohlc": {"spx_open": 5000.0, "vix_open": 14.0},
            }
        )
    )


def _seed_metrics(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cumulative_pnl": 999.0,  # stale — DB override should win
                "winning_days": 1,
                "losing_days": 1,
                "double_stops": 0,
            }
        )
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    # No dashboard accounts in this isolated temp auth DB → require_session is
    # a no-op (dev-mode), so requests below need no auth header. Isolated from
    # any real /opt/calypso auth DB.
    monkeypatch.setattr(settings, "dashboard_auth_db", tmp_path / "dashboard_auth.db", raising=False)

    # Variant A (an IC variant) gets a full, OWN data tree under tmp_path.
    va = tmp_path / "variant_a"
    va.mkdir()
    _seed_state(va / "hydra_state.json")
    _seed_ic_db(va / "backtesting.db")
    _seed_metrics(va / "hydra_metrics.json")

    monkeypatch.setattr(settings, "variant_a_state_file", va / "hydra_state.json", raising=False)
    monkeypatch.setattr(settings, "variant_a_backtesting_db", va / "backtesting.db", raising=False)
    monkeypatch.setattr(settings, "variant_a_metrics_file", va / "hydra_metrics.json", raising=False)
    monkeypatch.setattr(settings, "variant_a_baseline_date", "", raising=False)
    # The SPX candle chart reads the SHARED main market-data DB (SPX is the same
    # index for every strategy); in production market_data_db == variant A's DB.
    monkeypatch.setattr(settings, "market_data_db", va / "backtesting.db", raising=False)

    # Variant B: a state file ONLY (no DB, no metrics) → enrichment must degrade
    # to empty structures, never 500.
    vb = tmp_path / "variant_b"
    vb.mkdir()
    _seed_state(vb / "hydra_state.json")
    monkeypatch.setattr(settings, "variant_b_state_file", vb / "hydra_state.json", raising=False)
    monkeypatch.setattr(settings, "variant_b_backtesting_db", vb / "backtesting.db", raising=False)
    monkeypatch.setattr(settings, "variant_b_metrics_file", vb / "hydra_metrics.json", raising=False)
    monkeypatch.setattr(settings, "variant_b_baseline_date", "", raising=False)

    from dashboard.backend.main import app
    with TestClient(app) as c:
        yield c


class TestICSnapshotMirror:
    def test_available_ic_variant_has_mirror_keys(self, client):
        r = client.get("/api/strategies/a/snapshot")
        assert r.status_code == 200
        env = r.json()
        assert env["data_kind"] == "ic_state"
        body = env["body"]
        assert body["available"] is True

        # All three new keys present.
        assert "ohlc" in body
        assert "cumulative" in body
        assert "performance" in body

    def test_ohlc_is_the_shared_spx_bars_in_spxchart_shape(self, client):
        # SPX is the same index for every strategy → bars come from the SHARED main
        # market-data DB (dense), not each variant's throttled DB (2026-06-18 fix).
        body = client.get("/api/strategies/a/snapshot").json()["body"]
        ohlc = body["ohlc"]
        assert isinstance(ohlc, list) and len(ohlc) == 3
        bar = ohlc[0]
        # SPXChart / market_ohlc_1min row shape.
        for key in ("timestamp", "open", "high", "low", "close"):
            assert key in bar
        assert bar["timestamp"].startswith(TODAY)
        assert bar["close"] == 5002.0

    def test_cumulative_is_db_canonical_override_of_metrics_file(self, client):
        body = client.get("/api/strategies/a/snapshot").json()["body"]
        cum = body["cumulative"]
        # DB override wins over the stale metrics-file 999.0.
        # daily_summaries: 120 - 50 + 80 = 150.
        assert cum["cumulative_pnl"] == pytest.approx(150.0)
        # Win/loss days from DB (2 positive, 1 negative).
        assert cum["winning_days"] == 2
        assert cum["losing_days"] == 1
        # Capital efficiency present (DailyPnLCard reads roi_pct + avg_capital_per_day).
        assert "roi_pct" in cum
        assert "avg_capital_per_day" in cum
        assert "cumulative_baseline_date" in cum

    def test_performance_is_daily_pnls_plus_advanced(self, client):
        body = client.get("/api/strategies/a/snapshot").json()["body"]
        perf = body["performance"]
        # /api/metrics/performance shape: a daily_pnls array (PerformanceMetrics
        # computes the ratios client-side).
        assert perf["daily_pnls"] == [120.0, -50.0, 80.0]
        # Advanced stats computed by variants_router._compute_advanced_stats.
        adv = perf["advanced"]
        assert set(adv) >= {"sharpe", "max_drawdown", "best_day", "worst_day"}
        assert adv["best_day"] == pytest.approx(120.0)
        assert adv["worst_day"] == pytest.approx(-50.0)

    def test_missing_db_and_metrics_degrade_to_empty_not_500(self, client):
        # Variant B has a state file but NO db / metrics.
        r = client.get("/api/strategies/b/snapshot")
        assert r.status_code == 200
        body = r.json()["body"]
        assert body["available"] is True
        # OHLC is the SHARED SPX (main market DB), so B shows the candle chart even
        # with no OWN db — only its cumulative/performance degrade to empty.
        assert len(body["ohlc"]) == 3
        # cumulative degrades to the baseline-echo-only dict (no DB override).
        assert isinstance(body["cumulative"], dict)
        assert body["performance"]["daily_pnls"] == []

    def test_unavailable_variant_has_no_500(self, client, tmp_path, monkeypatch):
        # Point A at a missing state file → available:false, still 200.
        monkeypatch.setattr(
            settings, "variant_a_state_file",
            tmp_path / "nope" / "hydra_state.json", raising=False,
        )
        r = client.get("/api/strategies/a/snapshot")
        assert r.status_code == 200
        assert r.json()["body"]["available"] is False
