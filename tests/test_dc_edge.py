"""Tests for bots/hydra/dc_edge.py — the calendar (D/E) dry-run edge reader.

Builds REAL sqlite dc_calendar.db files (matching dc_recorder's schema) so the
SQL JOIN (outcomes ⨝ entries) is exercised end-to-end, not mocked. Covers:
sample-size verdict tiers, transform segmentation (incl. the MULTI-DAY case the
date-keyed join used to miss), COMMISSION-NET P&L, scale-free return-on-debit,
drawdown, profit factor, t-vs-z, float-noise rounding, db_status, and fail-soft.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.dc_edge import (  # noqa: E402
    analyze_calendar_edge,
    format_edge_report,
    _is_transformed,
    _max_drawdown,
    _round_trip_commission,
    _summarize,
    _t_crit,
)


# ── fixture builder ───────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE dc_calendar_entries (
    date TEXT, entry_number INTEGER, strategy_id TEXT, structure TEXT,
    entry_time TEXT, spx_at_entry REAL,
    short_expiry TEXT, long_expiry TEXT, short_dte INTEGER, long_dte INTEGER,
    call_strike REAL, put_strike REAL, net_debit REAL, contracts INTEGER,
    short_call_uic INTEGER, long_call_uic INTEGER,
    short_put_uic INTEGER, long_put_uic INTEGER,
    PRIMARY KEY (date, entry_number)
);
CREATE TABLE dc_transformations (
    date TEXT, entry_number INTEGER, strategy_id TEXT, transform_time TEXT,
    transform_credit REAL, net_debit REAL, wing_width REAL, is_risk_free INTEGER,
    wing_call_strike REAL, wing_put_strike REAL,
    call_spread_credit REAL, put_spread_credit REAL,
    PRIMARY KEY (date, entry_number)
);
CREATE TABLE dc_outcomes (
    entry_date TEXT, close_date TEXT, entry_number INTEGER, strategy_id TEXT,
    terminal_state TEXT, realized_pnl REAL, spx_at_close REAL,
    close_commission REAL, transform_credit REAL, net_debit REAL,
    PRIMARY KEY (entry_date, entry_number)
);
"""


def _build_db(path, outcomes, *, entries_contracts=True):
    """outcomes: list of dicts (realized_pnl, net_debit required). close_commission
    defaults to 0.0 so net==gross unless a test sets it explicitly."""
    con = sqlite3.connect(str(path))
    con.executescript(_SCHEMA)
    for i, o in enumerate(outcomes, start=1):
        ed = o.get("entry_date", f"2026-06-{(i % 28) + 1:02d}")
        en = o.get("entry_number", i)
        con.execute(
            "INSERT INTO dc_outcomes (entry_date, close_date, entry_number, strategy_id, "
            "terminal_state, realized_pnl, spx_at_close, close_commission, transform_credit, net_debit) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ed, o.get("close_date", ed), en, "d", o.get("terminal_state", "stop"),
             o["realized_pnl"], o.get("spx_at_close", 6000.0), o.get("close_commission", 0.0),
             o.get("transform_credit", 0.0), o["net_debit"]),
        )
        if entries_contracts:
            con.execute(
                "INSERT INTO dc_calendar_entries (date, entry_number, contracts, net_debit) "
                "VALUES (?,?,?,?)",
                (ed, en, o.get("contracts", 1), o["net_debit"]),
            )
    con.commit()
    con.close()
    return str(path)


# ── verdict tiers ─────────────────────────────────────────────────────────────

class TestVerdictTiers:
    def test_empty_db_is_insufficient(self, tmp_path):
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", []))
        assert r["total_outcomes"] == 0
        assert r["db_status"] == "ok"
        assert r["verdict"]["code"] == "INSUFFICIENT_DATA"

    def test_missing_db_distinguished_from_empty(self, tmp_path):
        r = analyze_calendar_edge(str(tmp_path / "nope.db"))
        assert r["total_outcomes"] == 0
        assert r["db_status"] == "not_found"          # audit F9: not the same as empty
        assert r["verdict"]["code"] == "INSUFFICIENT_DATA"
        assert "not found" in format_edge_report(r).lower()

    def test_single_outcome_is_insufficient(self, tmp_path):
        # the real-VM shape today: one -$230 stop on a $1140 debit, no commission set
        db = _build_db(tmp_path / "e.db",
                       [{"realized_pnl": -229.999999999999, "net_debit": 1140.0,
                         "terminal_state": "stop", "contracts": 1}])
        r = analyze_calendar_edge(db)
        seg = r["segments"]["calendar_mvl"]
        assert r["total_outcomes"] == 1
        assert r["verdict"]["code"] == "INSUFFICIENT_DATA"
        assert seg["worst"] == -230.0  # float noise → cents
        assert seg["losses"] == 1 and seg["wins"] == 0
        assert seg["return_on_debit_net_pct"] == pytest.approx(-20.175, abs=0.01)

    def test_preliminary_positive(self, tmp_path):
        outs = [{"realized_pnl": 50.0 + (i % 4), "net_debit": 1000.0} for i in range(12)]
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs))
        assert r["verdict"]["code"] == "PRELIMINARY_POSITIVE"

    def test_preliminary_negative(self, tmp_path):
        outs = [{"realized_pnl": -50.0 - (i % 4), "net_debit": 1000.0} for i in range(12)]
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs))
        assert r["verdict"]["code"] == "PRELIMINARY_NEGATIVE"

    def test_edge_positive_when_ci_clears_zero(self, tmp_path):
        outs = [{"realized_pnl": 100.0 + (i % 5), "net_debit": 1000.0} for i in range(40)]
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs))
        assert r["verdict"]["code"] == "EDGE_POSITIVE"
        assert r["segments"]["calendar_mvl"]["return_on_debit_net_ci95_pct"][0] > 0

    def test_edge_negative_when_ci_below_zero(self, tmp_path):
        outs = [{"realized_pnl": -100.0 - (i % 5), "net_debit": 1000.0} for i in range(40)]
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs))
        assert r["verdict"]["code"] == "EDGE_NEGATIVE"

    def test_inconclusive_when_ci_straddles_zero(self, tmp_path):
        outs = [{"realized_pnl": 500.0 if i % 2 == 0 else -500.0, "net_debit": 1000.0}
                for i in range(40)]
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs))
        assert r["verdict"]["code"] == "INCONCLUSIVE"
        ci = r["segments"]["calendar_mvl"]["return_on_debit_net_ci95_pct"]
        assert ci[0] < 0 < ci[1]

    def test_degenerate_zero_variance_not_confident(self, tmp_path):
        # 40 IDENTICAL positive returns → zero variance must NOT become EDGE_POSITIVE (audit F7)
        outs = [{"realized_pnl": 100.0, "net_debit": 1000.0} for _ in range(40)]
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs))
        assert r["verdict"]["code"] == "DEGENERATE_SAMPLE"

    def test_custom_thresholds(self, tmp_path):
        outs = [{"realized_pnl": 100.0 + (i % 3), "net_debit": 1000.0} for i in range(8)]
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs),
                                  min_preliminary=3, min_confident=5)
        assert r["verdict"]["code"] == "EDGE_POSITIVE"


# ── transform segmentation (audit F1: must work for MULTI-DAY transforms) ──────

class TestTransformSegmentation:
    def test_transformed_outcomes_excluded_from_verdict(self, tmp_path):
        # 30 profitable transformed + 1 losing plain calendar → verdict reads the
        # SINGLE plain calendar (insufficient), NOT rescued to EDGE_POSITIVE.
        outs = [{"entry_date": f"2026-05-{i + 1:02d}", "entry_number": 1, "realized_pnl": 80.0,
                 "net_debit": 100.0, "terminal_state": "settled", "transform_credit": 60.0}
                for i in range(30)]
        outs.append({"entry_date": "2026-06-01", "entry_number": 9, "realized_pnl": -230.0,
                     "net_debit": 1140.0, "terminal_state": "stop", "transform_credit": 0.0})
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs))
        assert r["total_outcomes"] == 31
        assert r["segments"]["transformed_untrusted"]["n"] == 30
        assert r["segments"]["calendar_mvl"]["n"] == 1
        assert r["verdict"]["code"] == "INSUFFICIENT_DATA"

    def test_multiday_transform_excluded_by_credit(self, tmp_path):
        # entry day != close day (a real multi-day transform) — the OLD date-keyed
        # join would have missed this; transform_credit>0 catches it (audit F1).
        db = _build_db(tmp_path / "e.db",
                       [{"entry_date": "2026-06-10", "close_date": "2026-06-14",
                         "realized_pnl": 75.0, "net_debit": 100.0, "transform_credit": 70.0}])
        r = analyze_calendar_edge(db)
        assert r["segments"]["transformed_untrusted"]["n"] == 1
        assert r["segments"]["calendar_mvl"]["n"] == 0

    def test_plain_multiday_calendar_is_trusted(self, tmp_path):
        db = _build_db(tmp_path / "e.db",
                       [{"entry_date": "2026-06-10", "close_date": "2026-06-14",
                         "realized_pnl": -50.0, "net_debit": 1000.0, "transform_credit": 0.0}])
        r = analyze_calendar_edge(db)
        assert r["segments"]["calendar_mvl"]["n"] == 1
        assert r["segments"]["transformed_untrusted"]["n"] == 0

    def test_is_transformed_keys_on_credit_only(self):
        assert _is_transformed({"transform_credit": 55.0}) is True
        assert _is_transformed({"transform_credit": 0.0}) is False
        assert _is_transformed({"transform_credit": None}) is False
        assert _is_transformed({}) is False


# ── commission-net (audit F2) ─────────────────────────────────────────────────

class TestCommissionNet:
    def test_round_trip_is_double_close_side(self):
        assert _round_trip_commission({"close_commission": 4.6}) == 9.2  # open≈close
        assert _round_trip_commission({"close_commission": None}) == 0.0

    def test_net_below_gross(self, tmp_path):
        s = _summarize([{"realized_pnl": 100.0, "net_debit": 1000.0,
                         "close_commission": 10.0, "contracts": 1}])
        assert s["ev_dollars_gross"] == 100.0
        assert s["ev_dollars_net"] == 80.0           # 100 − 2×10
        assert s["commission_total"] == 20.0
        assert s["return_on_debit_gross_pct"] == pytest.approx(10.0, abs=0.001)
        assert s["return_on_debit_net_pct"] == pytest.approx(8.0, abs=0.001)

    def test_commission_flips_marginal_win_to_loss(self):
        # gross +$15 but $10/side commission → net −$5 → a real LOSS to the operator
        s = _summarize([{"realized_pnl": 15.0, "net_debit": 1000.0,
                         "close_commission": 10.0, "contracts": 1}])
        assert s["wins"] == 0 and s["losses"] == 1
        assert s["worst"] == -5.0


# ── statistics ────────────────────────────────────────────────────────────────

class TestStatistics:
    def test_t_crit_uses_t_not_z_and_is_conservative(self):
        assert _t_crit(29) == 2.045          # t(29), not z=1.96
        assert _t_crit(35) == _t_crit(30)    # untabulated → largest df<=35 (conservative)
        assert _t_crit(200) == 1.960         # huge df → z
        assert _t_crit(1) > 12               # tiny df → very wide

    def test_max_drawdown_in_close_order(self):
        assert _max_drawdown([100.0, -300.0, 50.0]) == 300.0
        assert _max_drawdown([]) is None
        assert _max_drawdown([100.0, 50.0]) == 0.0

    def test_profit_factor_no_losses_is_inf(self):
        s = _summarize([{"realized_pnl": 10.0, "net_debit": 100.0, "contracts": 1},
                        {"realized_pnl": 20.0, "net_debit": 100.0, "contracts": 1}])
        assert s["profit_factor"] == float("inf")

    def test_profit_factor_no_wins_is_zero(self):
        s = _summarize([{"realized_pnl": -10.0, "net_debit": 100.0, "contracts": 1}])
        assert s["profit_factor"] == 0.0

    def test_profit_factor_all_scratch_is_none(self):
        s = _summarize([{"realized_pnl": 0.0, "net_debit": 100.0, "contracts": 1}])
        assert s["profit_factor"] is None

    def test_return_on_debit_scale_free(self):
        s = _summarize([{"realized_pnl": -100.0, "net_debit": 1000.0, "contracts": 1},
                        {"realized_pnl": -50.0, "net_debit": 500.0, "contracts": 1}])
        assert s["return_on_debit_net_pct"] == pytest.approx(-10.0, abs=0.001)

    def test_per_contract_normalization(self):
        s = _summarize([{"realized_pnl": 200.0, "net_debit": 1000.0, "contracts": 2}])
        assert s["ev_per_contract_net"] == 100.0

    def test_win_loss_scratch_classification(self):
        s = _summarize([
            {"realized_pnl": 100.0, "net_debit": 1000.0, "contracts": 1},
            {"realized_pnl": -50.0, "net_debit": 1000.0, "contracts": 1},
            {"realized_pnl": 0.0, "net_debit": 1000.0, "contracts": 1},
        ])
        assert (s["wins"], s["losses"], s["scratches"]) == (1, 1, 1)
        assert s["win_rate"] == pytest.approx(1 / 3, abs=0.001)

    def test_terminal_states_counted(self):
        s = _summarize([
            {"realized_pnl": 10.0, "net_debit": 100.0, "contracts": 1, "terminal_state": "stop"},
            {"realized_pnl": 10.0, "net_debit": 100.0, "contracts": 1, "terminal_state": "eod"},
            {"realized_pnl": 10.0, "net_debit": 100.0, "contracts": 1, "terminal_state": "eod"},
        ])
        assert s["terminal_states"] == {"stop": 1, "eod": 2}

    def test_zero_debit_row_excluded_from_return_on_debit(self):
        s = _summarize([{"realized_pnl": 10.0, "net_debit": 0.0, "contracts": 1}])
        assert s["return_on_debit_net_pct"] is None   # no divisible debit
        assert s["ev_dollars_net"] == 10.0            # but dollar EV still computed

    def test_verdict_gates_on_usable_series_not_row_count(self, tmp_path):
        # 30 rows but all zero-debit → 0 usable returns → must be INSUFFICIENT (audit F3),
        # not a confident verdict over "30 trades".
        outs = [{"realized_pnl": 5.0, "net_debit": 0.0} for _ in range(30)]
        r = analyze_calendar_edge(_build_db(tmp_path / "e.db", outs))
        assert r["verdict"]["code"] == "INSUFFICIENT_DATA"
        assert r["verdict"]["sample_used"] == 0


# ── rendering ─────────────────────────────────────────────────────────────────

class TestRendering:
    def test_report_is_string_and_mentions_verdict(self, tmp_path):
        db = _build_db(tmp_path / "e.db",
                       [{"realized_pnl": -230.0, "net_debit": 1140.0, "contracts": 1}])
        out = format_edge_report(analyze_calendar_edge(db), title="Strategy D")
        assert isinstance(out, str)
        assert "INSUFFICIENT_DATA" in out and "Strategy D" in out
        assert "COMMISSION-NET" in out

    def test_report_renders_transformed_caveat(self, tmp_path):
        db = _build_db(tmp_path / "e.db",
                       [{"realized_pnl": 80.0, "net_debit": 100.0, "transform_credit": 60.0}])
        assert "UNTRUSTED" in format_edge_report(analyze_calendar_edge(db))
