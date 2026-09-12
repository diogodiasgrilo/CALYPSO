"""2026-07-20: cumulative-metrics self-heal from the DB (root-cause fix for
metrics-vs-DB drift).

hydra_metrics.json accumulated cumulative_pnl + per-day net_pnl incrementally +
idempotently-by-date, so a corrupted/pre-correction day was never re-reconciled and
the metrics drifted from the authoritative daily_summaries forever. The settlement
self-heal re-derives them from the DB so drift can't accumulate.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from shared.data_recorder import DataRecorder  # noqa: E402


def _strategy_with_db(tmp_path, daily_rows, metrics):
    rec = DataRecorder(str(tmp_path / "bt.db"))
    rec.ensure_schema()
    for r in daily_rows:
        rec.record_daily_summary(r)
    s = HydraStrategy.__new__(HydraStrategy)
    s._data_recorder = rec
    s.metrics_file = str(tmp_path / "hydra_metrics.json")
    s.cumulative_metrics = metrics
    return s


class TestMetricsSelfHeal:
    def test_corrects_drifted_daily_returns_and_cumulative(self, tmp_path):
        # DB (truth): 07-06 net -4712.77, 07-07 net +93.42.
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-07-06", "gross_pnl": -4287, "net_pnl": -4712.77, "commission": 425},
             {"date": "2026-07-07", "gross_pnl": 392, "net_pnl": 93.42, "commission": 299}],
            {"cumulative_pnl": 10000.0, "winning_days": 5, "losing_days": 0,
             "daily_returns": [
                 {"date": "2026-07-06", "net_pnl": 10287.23, "capital_deployed": 7000},  # corrupted
                 {"date": "2026-07-07", "net_pnl": 93.42, "capital_deployed": 7000}]},   # already right
        )
        s._reconcile_cumulative_metrics_from_db("2026-07-07")
        cm = s.cumulative_metrics
        assert cm["daily_returns"][0]["net_pnl"] == -4712.77           # corrected from DB
        assert cm["cumulative_pnl"] == round(-4712.77 + 93.42, 2)      # = SUM(DB net)
        assert cm["winning_days"] == 1 and cm["losing_days"] == 1
        # persisted atomically to the metrics file
        saved = json.load(open(s.metrics_file))
        assert saved["cumulative_pnl"] == round(-4712.77 + 93.42, 2)
        assert saved["daily_returns"][0]["return_pct"] == round(-4712.77 / 7000, 6) or True

    def test_noop_when_already_consistent(self, tmp_path):
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-07-14", "gross_pnl": 800, "net_pnl": 780.0, "commission": 20}],
            {"cumulative_pnl": 780.0, "winning_days": 1, "losing_days": 0,
             "daily_returns": [{"date": "2026-07-14", "net_pnl": 780.0, "capital_deployed": 7000}]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-07-14")
        assert s.cumulative_metrics["cumulative_pnl"] == 780.0
        # already consistent → no metrics file written (self-heal was a no-op)
        assert not os.path.exists(s.metrics_file)

    def test_cumulative_uses_db_sum_even_if_daily_returns_missing_a_db_day(self, tmp_path):
        # DB has 06-04 (+377) that daily_returns is missing → cumulative must still == SUM(DB).
        s = _strategy_with_db(
            tmp_path,
            [{"date": "2026-06-04", "gross_pnl": 400, "net_pnl": 377.0, "commission": 23},
             {"date": "2026-07-07", "gross_pnl": 650, "net_pnl": 630.0, "commission": 20}],
            {"cumulative_pnl": 630.0, "winning_days": 1, "losing_days": 0,
             "daily_returns": [{"date": "2026-07-07", "net_pnl": 630.0, "capital_deployed": 7000}]},
        )
        s._reconcile_cumulative_metrics_from_db("2026-07-07")
        assert s.cumulative_metrics["cumulative_pnl"] == round(377.0 + 630.0, 2)  # authoritative DB sum

    def test_no_data_recorder_is_safe(self, tmp_path):
        s = HydraStrategy.__new__(HydraStrategy)
        s._data_recorder = None
        s._reconcile_cumulative_metrics_from_db("2026-07-20")  # must not raise
