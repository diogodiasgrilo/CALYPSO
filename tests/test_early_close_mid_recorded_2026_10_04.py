"""An MKT-047 flatten must record its quoted mid, including when it is 0.0.

THE BUG. `_record_stop_to_db` did:

    quoted_mid = entry.call_spread_value if entry.call_spread_value else None

`if X else None` conflates 0.0 with missing — and 0.0 is the most common value
at an EOD flatten, because a deep-OTM side really is worthless there. So
`quoted_mid_at_stop` and `slippage_on_close` came back NULL on **8 of 27**
live-era `early_close` rows while `stop_loss` was **0 of 17** complete (a stop
never fires at a spread value of 0). MKT-047 is the dominant exit path, and exit
slippage is an open money question, so the hole was in the measurement itself.

Verified against 2026-10-02: both NULL rows were the deep-OTM call sides.

These hit a REAL DataRecorder and SELECT the row back, rather than asserting on
source text — a source-grep test here would pass against a gutted writer.
"""
import sqlite3
import datetime

import pytest

from bots.hydra.strategy import HydraStrategy
from shared.data_recorder import DataRecorder
from shared.market_hours import get_us_market_time


class _Entry:
    def __init__(self, call_value, put_value):
        self.entry_number = 1
        # tz-AWARE, like production: _record_stop_to_db subtracts this from
        # get_us_market_time(), and a naive value raises
        # "can't subtract offset-naive and offset-aware datetimes" — which the
        # method swallows to DEBUG, losing the whole row silently.
        self.entry_time = get_us_market_time() - datetime.timedelta(minutes=90)
        self.call_spread_value = call_value
        self.put_spread_value = put_value
        self.call_spread_credit = 300.0
        self.put_spread_credit = 250.0
        self.contracts = 7
        self._spx_at_entry = 7700.0
        self.call_breach_count = 0
        self.put_breach_count = 0
        self.call_long_sold = False
        self.put_long_sold = False
        self.call_long_sold_revenue = 0.0
        self.put_long_sold_revenue = 0.0


def _strategy(db_path):
    s = HydraStrategy.__new__(HydraStrategy)
    rec = DataRecorder(str(db_path))
    # ensure_schema() is NOT called by __init__ — and record_stop routes through
    # _safe_write, which swallows the resulting "no such table" into a warning.
    # Without this line the writer silently no-ops and a sloppier test would
    # read that as "no row written" rather than "the test rig is broken".
    assert rec.ensure_schema(), "schema init failed — the rig, not the code"
    s._data_recorder = rec
    s.current_price = 7721.0
    s._last_stop_time = None
    return s


def _row(db_path, side):
    c = sqlite3.connect(str(db_path))
    r = c.execute(
        "SELECT quoted_mid_at_stop, slippage_on_close, actual_debit, exit_reason "
        "FROM trade_stops WHERE side=?", (side,)).fetchone()
    c.close()
    return r


def test_a_worthless_side_records_mid_zero_not_null(tmp_path):
    """THE FIX. Deep-OTM call worth exactly 0.0 at the flatten."""
    db = tmp_path / "b.db"
    s = _strategy(db)
    s._record_stop_to_db(_Entry(call_value=0.0, put_value=70.0), "call",
                         stop_level=1400.0, actual_close_cost=35.0,
                         exit_reason="early_close")
    mid, slip, debit, reason = _row(db, "call")
    assert reason == "early_close"
    assert mid == 0.0, "a 0.0 spread value must record as 0.0, not NULL"
    assert slip == 35.0, "slippage = 35.0 close - 0.0 mid; got %r" % slip


def test_a_nonzero_side_still_records(tmp_path):
    db = tmp_path / "b.db"
    s = _strategy(db)
    s._record_stop_to_db(_Entry(call_value=0.0, put_value=70.0), "put",
                         stop_level=1400.0, actual_close_cost=105.0,
                         exit_reason="early_close")
    mid, slip, _, _ = _row(db, "put")
    assert mid == 70.0
    assert slip == pytest.approx(35.0)


def test_a_genuinely_missing_value_is_still_null(tmp_path):
    """None must stay NULL — the fix must not invent a 0.0 where there is no quote."""
    db = tmp_path / "b.db"
    s = _strategy(db)
    s._record_stop_to_db(_Entry(call_value=None, put_value=None), "call",
                         stop_level=1400.0, actual_close_cost=35.0,
                         exit_reason="early_close")
    mid, slip, _, _ = _row(db, "call")
    assert mid is None, "a missing quote must remain NULL, not become 0.0"
    assert slip is None


def test_zero_close_against_zero_mid_is_zero_slippage_not_null(tmp_path):
    """Both operands 0.0: zero slippage is a RESULT, not an absence of one."""
    db = tmp_path / "b.db"
    s = _strategy(db)
    s._record_stop_to_db(_Entry(call_value=0.0, put_value=0.0), "call",
                         stop_level=1400.0, actual_close_cost=0.0,
                         exit_reason="early_close")
    mid, slip, _, _ = _row(db, "call")
    assert mid == 0.0
    assert slip == 0.0, "0.0 - 0.0 must record 0.0, got %r" % slip


def test_a_real_stop_loss_is_unaffected(tmp_path):
    """The stop_loss path was already complete (0 of 17 NULL) — keep it that way."""
    db = tmp_path / "b.db"
    s = _strategy(db)
    s._record_stop_to_db(_Entry(call_value=1505.0, put_value=0.0), "call",
                         stop_level=1400.0, actual_close_cost=2260.0,
                         exit_reason="stop_loss")
    mid, slip, _, reason = _row(db, "call")
    assert reason == "stop_loss"
    assert mid == 1505.0
    assert slip == pytest.approx(755.0)
