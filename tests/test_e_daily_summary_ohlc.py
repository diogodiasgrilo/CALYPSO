"""2026-07-14: Strategy E (SPY double calendar) writes an IC-shaped, vestigial
`daily_summaries` row via the shared writer, but its SPY underlying is never read
through the index (`sec_type=IND`) path, so `market_data.spx_open`/`spx_high` stay
at their 0.0 reset defaults → the row had `spx_open=0.0`, `spx_high=0.0` garbage.
E now backfills the OHLC from its own recorded SPY `market_ticks`. Pins the
DataRecorder helper + the E-only override (incl. the no-op when OHLC is already
populated, which guarantees A/B/C/D are unaffected — they never call this)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.data_recorder import DataRecorder  # noqa: E402
from bots.hydra.spy_double_calendar_strategy import SpyDoubleCalendarStrategy  # noqa: E402
from bots.hydra.strategy import HydraStrategy  # noqa: E402


class TestGetSpxOhlcForDate:
    def _ticks(self, rec, rows):
        rec.ensure_schema()
        with rec._connect() as conn:
            for ts, px in rows:
                conn.execute(
                    "INSERT INTO market_ticks (timestamp, spx_price) VALUES (?, ?)",
                    (ts, px))

    def test_ohlc_from_ticks(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "bt.db"))
        self._ticks(rec, [("2026-07-13 09:30:01", 753.0),
                          ("2026-07-13 12:00:00", 757.0),
                          ("2026-07-13 14:00:00", 751.0),
                          ("2026-07-13 16:00:00", 755.0)])
        # open=first, high=max, low=min, close=last
        assert rec.get_spx_ohlc_for_date("2026-07-13") == (753.0, 757.0, 751.0, 755.0)

    def test_ignores_zero_ticks_and_other_days(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "bt.db"))
        self._ticks(rec, [("2026-07-13 09:30:00", 0.0),     # zero → excluded
                          ("2026-07-13 10:00:00", 754.0),
                          ("2026-07-12 16:00:00", 999.0)])   # other day
        assert rec.get_spx_ohlc_for_date("2026-07-13") == (754.0, 754.0, 754.0, 754.0)

    def test_empty_returns_nones(self, tmp_path):
        rec = DataRecorder(str(tmp_path / "bt.db"))
        rec.ensure_schema()  # table exists but empty
        assert rec.get_spx_ohlc_for_date("2026-07-13") == (None, None, None, None)


class TestEDailySummaryOHLCBackfill:
    def _e(self):
        return SpyDoubleCalendarStrategy.__new__(SpyDoubleCalendarStrategy)

    def test_backfills_ohlc_from_ticks_when_unset(self):
        # The 07-13 garbage shape: spx_open=0.0, spx_high=0.0 → backfill from ticks.
        s = self._e()
        s.market_data = SimpleNamespace(spx_open=0.0, spx_high=0.0, spx_low=float("inf"))
        s._data_recorder = MagicMock()
        s._data_recorder.get_spx_ohlc_for_date.return_value = (753.0, 757.0, 751.0, 755.0)
        with patch.object(HydraStrategy, "_record_daily_summary_to_db") as sup:
            s._record_daily_summary_to_db()
        assert s.market_data.spx_open == 753.0
        assert s.market_data.spx_high == 757.0
        assert s.market_data.spx_low == 751.0
        sup.assert_called_once()  # still delegates to the shared writer

    def test_noop_when_ohlc_already_populated(self):
        s = self._e()
        s.market_data = SimpleNamespace(spx_open=750.0, spx_high=760.0, spx_low=748.0)
        s._data_recorder = MagicMock()
        with patch.object(HydraStrategy, "_record_daily_summary_to_db") as sup:
            s._record_daily_summary_to_db()
        s._data_recorder.get_spx_ohlc_for_date.assert_not_called()
        assert s.market_data.spx_open == 750.0
        sup.assert_called_once()

    def test_no_ticks_leaves_defaults_and_still_writes(self):
        # No ticks (helper returns Nones) → OHLC untouched, writer still runs.
        s = self._e()
        s.market_data = SimpleNamespace(spx_open=0.0, spx_high=0.0, spx_low=float("inf"))
        s._data_recorder = MagicMock()
        s._data_recorder.get_spx_ohlc_for_date.return_value = (None, None, None, None)
        with patch.object(HydraStrategy, "_record_daily_summary_to_db") as sup:
            s._record_daily_summary_to_db()
        assert s.market_data.spx_open == 0.0  # unchanged
        sup.assert_called_once()
