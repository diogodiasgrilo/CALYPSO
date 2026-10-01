"""One failing settlement stage must not silence the rest.

Until 2026-10-01 every stage after the daily-summary write shared a single
try/except, so the first to raise skipped all the others. The chain is:
per-entry realized P&L, MAE/MFE, **the WAL checkpoint**, the metrics self-heal,
and the broker reconcile — the only non-circular P&L check in the codebase.

Three are load-bearing beyond telemetry. The WAL checkpoint prevents unbounded
WAL growth, so skipping it compounds operationally. The self-heal is the drift
guard. The broker reconcile is the independent arm. None should be hostage to
an unrelated failure upstream.

This is the same shape as the health-check unit the day before, where a gating
first step stopped the second from ever running — and the report went quiet
precisely on the days something was wrong.
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _s():
    return HydraStrategy.__new__(HydraStrategy)


class TestOneStageCannotSilenceTheOthers:
    def test_a_raising_stage_does_not_propagate(self, caplog):
        s = _s()
        def boom(*a):
            raise RuntimeError("stage exploded")
        with caplog.at_level(logging.WARNING):
            out = s._settlement_stage("per_entry_realized_pnl", boom, "2026-10-01")
        assert out is None
        assert any("SETTLEMENT-STAGE" in str(r.msg) for r in caplog.records), (
            "a settlement stage failed silently — that is how a whole day's "
            "telemetry disappears without anyone noticing")

    def test_the_failure_names_the_stage(self, caplog):
        """'settlement failed' is not actionable; 'wal_checkpoint failed' is."""
        s = _s()
        with caplog.at_level(logging.WARNING):
            s._settlement_stage("wal_checkpoint", lambda: 1 / 0)
        joined = " ".join(str(r.msg) % r.args if r.args else str(r.msg)
                          for r in caplog.records)
        assert "wal_checkpoint" in joined, joined

    def test_it_carries_a_traceback(self, caplog):
        s = _s()
        with caplog.at_level(logging.WARNING):
            s._settlement_stage("x", lambda: 1 / 0)
        assert any(r.exc_info for r in caplog.records), (
            "no traceback — the next reader gets a message without the line "
            "that raised")

    def test_a_successful_stage_returns_its_value_and_logs_nothing(self, caplog):
        s = _s()
        with caplog.at_level(logging.WARNING):
            out = s._settlement_stage("ok", lambda a: a * 2, 21)
        assert out == 42, "the helper changed a stage's return value"
        assert not caplog.records, "a clean stage logged a warning"

    def test_args_are_passed_through(self):
        s = _s()
        seen = {}
        s._settlement_stage("x", lambda d: seen.setdefault("d", d), "2026-10-01")
        assert seen["d"] == "2026-10-01"


class TestEveryStageIsIsolated:
    STAGES = ["per_entry_realized_pnl", "mae_mfe", "wal_checkpoint",
              "archive_pre_epoch", "metrics_self_heal", "broker_reconcile"]

    def test_all_six_stages_route_through_the_helper(self):
        """If a stage is called directly again, it regains the power to
        silence everything after it."""
        import inspect
        src = inspect.getsource(HydraStrategy._record_daily_summary_to_db)
        for name in self.STAGES:
            assert f'"{name}"' in src, (
                f"stage {name!r} no longer goes through _settlement_stage — a "
                f"raise there would skip every stage after it")

    def test_no_stage_is_still_called_bare(self):
        import inspect
        src = inspect.getsource(HydraStrategy._record_daily_summary_to_db)
        for bare in ("self._record_entry_realized_pnl(date_str)",
                     "self._data_recorder.wal_checkpoint()",
                     "self._reconcile_cumulative_metrics_from_db(date_str)",
                     "self._reconcile_pnl_against_broker(date_str)"):
            assert bare not in src, f"bare call restored: {bare}"


class TestTheOuterHandlerIsLoud:
    def test_it_is_not_debug(self):
        import inspect
        src = inspect.getsource(HydraStrategy._record_daily_summary_to_db)
        tail = src[src.rindex("except Exception"):]
        calls = [l for l in tail.splitlines()
                 if "logger.debug(" in l and not l.strip().startswith("#")]
        assert not calls, (
            f"the settlement failure handler is back at DEBUG: {calls}. The bot "
            f"runs at INFO, so a total settlement failure would be silent.")

    def test_it_says_no_row_was_written(self):
        import inspect
        src = inspect.getsource(HydraStrategy._record_daily_summary_to_db)
        tail = src[src.rindex("except Exception"):]
        assert "no daily_summary" in tail and "logger.warning" in tail


class TestThePayloadEnrichersStillCannotRaise:
    """The other half of the risk, pinned.

    `_classify_day_type` and `_realized_volatility` are evaluated INSIDE the
    `record_daily_summary(...)` payload, which is upstream of every isolated
    stage. A raise there still skips the write and everything after it, so the
    only protection is that they cannot raise. Fuzzed over 324 hostile inputs
    on 2026-10-01; these pin the worst of them so a future edit cannot quietly
    remove the internal guards.
    """

    HOSTILE = [None, "not-market-data", 0]

    @pytest.mark.parametrize("md", HOSTILE)
    def test_classify_day_type_survives_hostile_market_data(self, md):
        s = _s()
        s.market_data = md
        s.fomc_announcement_today = False
        s._resolve_spx_close = lambda: None
        assert isinstance(s._classify_day_type([]), str)

    def test_classify_day_type_survives_a_raising_close_resolver(self):
        s = _s()
        s.market_data = None
        s.fomc_announcement_today = False
        def boom():
            raise RuntimeError("close exploded")
        s._resolve_spx_close = boom
        assert isinstance(s._classify_day_type([]), str)

    def test_realized_volatility_survives_a_raising_recorder(self):
        class Bad:
            def get_spx_price_series_for_date(self, d):
                raise RuntimeError("db exploded")
        s = _s()
        s._data_recorder = Bad()
        assert s._realized_volatility() is None

    def test_realized_volatility_survives_garbage_prices(self):
        from types import SimpleNamespace
        s = _s()
        s._data_recorder = SimpleNamespace(
            get_spx_price_series_for_date=lambda d: ["a"] * 50)
        assert s._realized_volatility() is None
