"""A variant whose RULES changed starts its lifetime record over.

Operator, on variant E: *"restart the P&L count from when it becomes true to the
video, and the online dashboard needs to be updated too."*

E's low-IV gate moved on 2026-09-24 from an absolute ``VIX <= 22`` to the VIX
percentile its source actually describes. That gate decides **which days the
strategy trades at all**, so days either side of the cutover were produced by
different strategies and a blended lifetime average answers neither question.

WHY THIS COULD NOT BE DONE BY EDITING THE METRICS FILE
-------------------------------------------------------
``_reconcile_cumulative_metrics_from_db`` re-derives ``cumulative_pnl`` from the
**full** ``daily_summaries`` table on every settlement — that is its job, and it
is the root-cause fix for the 2026-07-20 metrics drift. A hand-zeroed
``hydra_metrics.json`` is therefore silently undone on the next close. Filtering
the source is the only reset that survives its own self-heal, which is what
these tests pin.

Nothing is deleted: pre-epoch rows stay in the database and the retired totals
move to a ``pre_epoch`` block in the metrics file.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.strategy import HydraStrategy  # noqa: E402

EPOCH = "2026-09-24"


def _strat(epoch=EPOCH, metrics=None):
    s = HydraStrategy.__new__(HydraStrategy)
    s.strategy_config = {"metrics_epoch_date": epoch} if epoch else {}
    s.cumulative_metrics = metrics if metrics is not None else {
        "cumulative_pnl": -109.0, "total_entries": 12, "total_stops": 3,
        "total_credit_collected": 0.0, "double_stops": 0,
        "winning_days": 5, "losing_days": 7,
        "daily_returns": [
            {"date": "2026-09-20", "net_pnl": -50.0, "capital_deployed": 500},
            {"date": "2026-09-23", "net_pnl": -59.0, "capital_deployed": 500},
            {"date": "2026-09-25", "net_pnl": 30.0, "capital_deployed": 500},
        ],
    }
    s._saved = []
    s._save_cumulative_metrics = lambda trading_date=None: s._saved.append(trading_date)
    return s


class TestTheEpochIsReadSafely:

    def test_it_reads_the_configured_date(self):
        assert _strat()._metrics_epoch_date() == EPOCH

    def test_no_epoch_means_count_everything(self):
        assert _strat(epoch=None)._metrics_epoch_date() is None

    @pytest.mark.parametrize("bad", ["not-a-date", "24/09/2026", "2026-13-01", "   "])
    def test_a_malformed_date_is_IGNORED_not_raised(self, bad):
        """This runs inside the settlement path. A typo must cost the epoch, not
        the night's reconciliation."""
        assert _strat(epoch=bad)._metrics_epoch_date() is None


class TestTheArchivePreservesTheEarlierEra:

    def test_the_lifetime_counters_reset(self):
        s = _strat(); s._archive_pre_epoch_metrics()
        cm = s.cumulative_metrics
        assert cm["cumulative_pnl"] == 0.0
        assert cm["total_entries"] == 0 and cm["total_stops"] == 0
        assert cm["winning_days"] == 0 and cm["losing_days"] == 0

    def test_the_earlier_era_is_KEPT_not_deleted(self):
        """A record that simply vanishes invites the question of what else did."""
        s = _strat(); s._archive_pre_epoch_metrics()
        pe = s.cumulative_metrics["pre_epoch"]
        assert pe["cumulative_pnl"] == -109.0
        assert pe["days"] == 2 and pe["retired_on"] == EPOCH
        assert [r["date"] for r in pe["daily_returns"]] == ["2026-09-20", "2026-09-23"]

    def test_only_post_epoch_days_remain_live(self):
        s = _strat(); s._archive_pre_epoch_metrics()
        assert [r["date"] for r in s.cumulative_metrics["daily_returns"]] == ["2026-09-25"]

    def test_it_is_idempotent_across_restarts(self):
        """Run twice, the second must not archive the ALREADY-ZEROED counters
        over the real ones — which would erase the history it exists to keep."""
        s = _strat()
        s._archive_pre_epoch_metrics()
        first = json.dumps(s.cumulative_metrics["pre_epoch"], sort_keys=True)
        s._archive_pre_epoch_metrics()
        assert json.dumps(s.cumulative_metrics["pre_epoch"], sort_keys=True) == first
        assert s.cumulative_metrics["pre_epoch"]["cumulative_pnl"] == -109.0

    def test_no_epoch_archives_nothing(self):
        s = _strat(epoch=None); s._archive_pre_epoch_metrics()
        assert "pre_epoch" not in s.cumulative_metrics
        assert s.cumulative_metrics["cumulative_pnl"] == -109.0

    def test_a_fresh_variant_with_no_history_is_left_alone(self):
        s = _strat(metrics={"cumulative_pnl": 0.0, "daily_returns": []})
        s._archive_pre_epoch_metrics()
        assert "pre_epoch" not in s.cumulative_metrics

    def test_the_archive_is_persisted(self):
        s = _strat(); s._archive_pre_epoch_metrics()
        assert s._saved == [EPOCH]


class TestTheSelfHealRespectsTheEpoch:
    """The half that makes the reset survive. Without it the next settlement
    re-derives cumulative_pnl from every row and the restart is undone."""

    _n = 0

    def _db(self, tmp_path):
        # A fresh file per call: several tests build the DB twice (epoch vs no
        # epoch) inside one tmp_path, and reusing the name re-runs CREATE TABLE.
        type(self)._n += 1
        p = tmp_path / f"backtesting_{type(self)._n}.db"
        con = sqlite3.connect(p)
        with con:
            con.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL)")
            con.executemany("INSERT INTO daily_summaries VALUES (?,?)", [
                ("2026-09-20", -50.0), ("2026-09-23", -59.0),   # pre-epoch
                ("2026-09-25", 30.0), ("2026-09-26", 12.0),     # post-epoch
            ])
            con.execute("CREATE TABLE trade_stops (date TEXT, entry_number INT, "
                        "side TEXT, exit_reason TEXT, net_pnl REAL)")
            con.executemany("INSERT INTO trade_stops VALUES (?,?,?,?,?)", [
                ("2026-09-20", 1, "call", "stop_loss", -10.0),   # pre-epoch
                ("2026-09-25", 1, "call", "stop_loss", -10.0),   # post-epoch
            ])
        con.close()
        return str(p)

    def _run(self, tmp_path, epoch):
        s = _strat(epoch=epoch)
        s._data_recorder = type("R", (), {"db_path": self._db(tmp_path)})()
        s._reconcile_cumulative_metrics_from_db("2026-09-26")
        return s.cumulative_metrics

    def test_with_an_epoch_only_later_days_are_summed(self, tmp_path):
        cm = self._run(tmp_path, EPOCH)
        assert cm["cumulative_pnl"] == pytest.approx(42.0)    # 30 + 12

    def test_WITHOUT_an_epoch_the_whole_history_is_summed(self, tmp_path):
        """The negative control: proves the filter is what changes the number,
        not some other edit."""
        cm = self._run(tmp_path, None)
        assert cm["cumulative_pnl"] == pytest.approx(-67.0)   # -50 -59 +30 +12

    def test_the_stop_counters_respect_the_same_cutover(self, tmp_path):
        """Counting stops from an era whose P&L is excluded would describe two
        different strategies in one row."""
        assert self._run(tmp_path, EPOCH)["total_stops"] == 1
        assert self._run(tmp_path, None)["total_stops"] == 2

    def test_win_loss_counts_cover_the_SAME_era_as_the_pnl(self, tmp_path):
        cm = self._run(tmp_path, EPOCH)
        assert cm["winning_days"] + cm["losing_days"] == len(cm["daily_returns"])
        assert all(r["date"] >= EPOCH for r in cm["daily_returns"])


class TestTheDashboardAgreesWithTheBot:
    """Two surfaces reporting one lifetime number must not disagree about which
    days it covers."""

    def test_E_s_dashboard_baseline_matches_its_bot_epoch(self):
        from dashboard.backend.config import Settings
        cfg = json.loads(
            (ROOT / "bots" / "hydra" / "config" / "config_variant_e.json").read_text())
        bot_epoch = cfg["strategy"]["metrics_epoch_date"]
        assert Settings().variant_e_baseline_date == bot_epoch, (
            "the dashboard rebases its own cumulative figures; if it and the bot "
            "disagree they will report different lifetime P&L for the same variant")

    def test_the_api_publishes_the_epoch(self):
        import dashboard.backend.routers.strategies as sr
        import shared.strategy_taxonomy as tax
        sr.settings.variant_e_config_file = str(
            ROOT / "bots" / "hydra" / "config" / "config_variant_e.json")
        assert sr._header_chrome("e", tax.STRATEGIES["e"])["metrics_epoch_date"] == EPOCH

    def test_a_variant_without_an_epoch_publishes_none(self):
        import dashboard.backend.routers.strategies as sr
        import shared.strategy_taxonomy as tax
        sr.settings.variant_g_config_file = str(
            ROOT / "bots" / "hydra" / "config" / "config_variant_g.json")
        assert sr._header_chrome("g", tax.STRATEGIES["g"])["metrics_epoch_date"] is None

    def test_the_header_actually_renders_it(self):
        """A restarted record that is not SHOWN reads as data loss."""
        src = (ROOT / "dashboard" / "frontend" / "src" / "components" / "layout"
               / "Header.tsx").read_text()
        assert "metricsEpoch" in src
        assert "Record restarted" in src


class TestTheEpochLookupCannotDisableTheSelfHeal:
    """A regression I introduced and caught in the same session.

    ``_metrics_epoch_date`` first read ``self.strategy_config`` by attribute.
    ``_reconcile_cumulative_metrics_from_db`` wraps everything in a broad
    ``except Exception`` and logs at DEBUG, so on any instance without that
    attribute the missing-attribute error turned the ENTIRE self-heal into a
    silent no-op — the 2026-07-20 drift guard would simply stop running, and
    nothing in the logs would say so. Six existing tests went red and said so;
    in production it would have been invisible.
    """

    def test_a_strategy_with_no_strategy_config_reports_no_epoch(self):
        s = HydraStrategy.__new__(HydraStrategy)
        assert s._metrics_epoch_date() is None

    def test_the_self_heal_still_runs_without_a_strategy_config(self, tmp_path):
        """The property that actually matters: the drift guard keeps working."""
        db = tmp_path / "bt.db"
        con = sqlite3.connect(db)
        with con:
            con.execute("CREATE TABLE daily_summaries (date TEXT, net_pnl REAL)")
            con.execute("INSERT INTO daily_summaries VALUES ('2026-09-25', 30.0)")
            con.execute("CREATE TABLE trade_stops (date TEXT, entry_number INT, "
                        "side TEXT, exit_reason TEXT, net_pnl REAL)")
        con.close()

        s = HydraStrategy.__new__(HydraStrategy)          # NO strategy_config
        s._data_recorder = type("R", (), {"db_path": str(db)})()
        s.cumulative_metrics = {"cumulative_pnl": 999.0, "daily_returns": []}
        s._save_cumulative_metrics = lambda trading_date=None: None
        s._reconcile_cumulative_metrics_from_db("2026-09-25")
        assert s.cumulative_metrics["cumulative_pnl"] == pytest.approx(30.0), (
            "the self-heal silently no-opped — the drift guard is not running")
