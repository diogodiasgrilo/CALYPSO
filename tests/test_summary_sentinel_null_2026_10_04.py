"""A session that captured no open must record NULL, not a price of zero.

`spx_open` / `spx_high` / `vix_open` were written RAW into daily_summaries, so a
day on which nothing was captured recorded its **0.0 sentinel as if it were a
price**. 81 rows across the fleet carry it, and the dates give the cause away —
2026-04-03, 05-25, 06-19, 07-03, 09-07 are Good Friday, Memorial Day,
Juneteenth, July-4th-observed and Labor Day. Market holidays: no session, so
nothing to capture.

`spx_low` already had this discipline (inf -> None) and `day_range` already
guarded on it. The other three were missed.

It matters more as of 2026-10-04: the stale-open corroboration guard added the
same day deliberately leaves `spx_open` at 0.0 when a session's first tick is
never corroborated, so the number of days that CAN hit this went up. This test
exists because that widening was mine.
"""
import sqlite3
import datetime

import pytest

from bots.hydra.strategy import HydraStrategy
from bots.hydra.base_strategy import MarketData
from shared.data_recorder import DataRecorder
from shared.market_hours import get_us_market_time


class _DailyState:
    def __init__(self, date_str):
        self.date = date_str
        self.entries = []


def _rig(tmp_path, market_data):
    """A HydraStrategy with __init__ bypassed and only the summary path wired."""
    s = HydraStrategy.__new__(HydraStrategy)
    rec = DataRecorder(str(tmp_path / "s.db"))
    assert rec.ensure_schema(), "schema init failed — the rig, not the code"
    s._data_recorder = rec
    s.market_data = market_data
    s.current_vix = 15.5
    s.current_price = 7700.0
    s.contracts_per_entry = 7
    today = get_us_market_time().strftime("%Y-%m-%d")
    s.daily_state = _DailyState(today)
    s._resolve_spx_close = lambda: 7700.0
    s._daily_summary_is_stale = lambda *a, **k: False
    s.get_daily_summary = lambda: {
        "entries_completed": 0, "total_pnl": 0.0, "total_commission": 0.0}
    s._cumulative_tracking_pnl = lambda summary, v: v
    s._classify_day_type = lambda *a, **k: "chop"
    s._realized_volatility = lambda *a, **k: None
    s._classification_moment = lambda: get_us_market_time()
    return s, today


def _row(tmp_path, date_str):
    c = sqlite3.connect(str(tmp_path / "s.db"))
    r = c.execute("SELECT spx_open, spx_high, spx_low, vix_open, day_range "
                  "FROM daily_summaries WHERE date=?", (date_str,)).fetchone()
    c.close()
    return r


def test_a_session_with_no_data_records_nulls_not_zeroes(tmp_path):
    """THE FIX. The market-holiday shape: nothing was ever captured."""
    md = MarketData()                      # pristine: open/high 0.0, low inf
    s, today = _rig(tmp_path, md)
    s._record_daily_summary_to_db()
    spx_open, spx_high, spx_low, vix_open, day_range = _row(tmp_path, today)
    assert spx_open is None, "a 0.0 sentinel was recorded as a price"
    assert spx_high is None, "a 0.0 sentinel was recorded as a price"
    assert spx_low is None
    assert vix_open is None
    assert day_range is None


def test_a_real_session_still_records_its_values(tmp_path):
    md = MarketData()
    md._is_regular_session_or_later = lambda: True
    for px in (7731.07, 7731.51, 7754.06, 7700.51):
        md.update_spx(px, "R")
    md.update_vix(15.2)
    s, today = _rig(tmp_path, md)
    s._record_daily_summary_to_db()
    spx_open, spx_high, spx_low, vix_open, day_range = _row(tmp_path, today)
    assert spx_open == 7731.07
    assert spx_high == 7754.06
    assert spx_low == 7700.51
    assert vix_open == 15.2
    assert day_range == pytest.approx(53.55)


def test_the_uncorroborated_single_tick_session_records_nulls(tmp_path):
    """The case the 2026-10-04 corroboration guard newly creates.

    One regular-hours tick, never corroborated -> the guard deliberately holds
    it, so nothing is captured. That must land as NULL, not as 0.0.
    """
    md = MarketData()
    md._is_regular_session_or_later = lambda: True
    md.update_spx(7666.45, "R")            # candidate, never corroborated
    s, today = _rig(tmp_path, md)
    s._record_daily_summary_to_db()
    spx_open, spx_high, spx_low, _, day_range = _row(tmp_path, today)
    assert (spx_open, spx_high, spx_low, day_range) == (None, None, None, None)


def test_a_partial_session_keeps_what_it_has(tmp_path):
    """SPX captured but VIX never arrived: NULL the VIX only."""
    md = MarketData()
    md._is_regular_session_or_later = lambda: True
    md.update_spx(7700.0, "R")
    md.update_spx(7700.5, "R")
    s, today = _rig(tmp_path, md)
    s._record_daily_summary_to_db()
    spx_open, spx_high, _, vix_open, _ = _row(tmp_path, today)
    assert spx_open == 7700.0, "a good SPX open must survive"
    assert vix_open is None, "VIX never arrived — must be NULL"
