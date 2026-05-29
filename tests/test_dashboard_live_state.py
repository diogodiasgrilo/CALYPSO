"""Dashboard live_state regression — today's SPX/VIX must come from the bot's
tracked market_data_ohlc (always written to state), not the per-entry
spx_at_entry/vix_at_entry the bot does NOT write (audit FP6)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.backend.services.live_state import LiveStateProvider


def _provider(state):
    p = LiveStateProvider.__new__(LiveStateProvider)
    p._db_reader = None  # no DB fallback → exercise the OHLC path only
    p._get_today_state = lambda: state
    return p


def test_today_summary_spx_vix_from_market_data_ohlc():
    state = {
        "total_realized_pnl": 100.0,
        "total_commission": 5.0,
        "entries": [{"entry_number": 1}],   # non-empty to pass the gate
        "pnl_history": [],
        "market_data_ohlc": {
            "spx_open": 7591.85, "spx_high": 7592.47, "spx_low": 7571.34,
            "vix_open": 15.24, "vix_high": 15.55, "vix_low": 15.24,
        },
    }
    s = _provider(state).get_today_summary()
    assert s is not None
    assert s["spx_open"] == 7591.85
    assert s["spx_high"] == 7592.47
    assert s["spx_low"] == 7571.34
    assert s["vix_open"] == 15.24
    # No pnl_history + no DB → close falls back to open (not None/0).
    assert s["spx_close"] == 7591.85
    assert s["vix_close"] == 15.24


def test_today_summary_none_without_entries():
    # 0-entry day (e.g. today's dry-run) → no row, as designed.
    assert _provider({"entries": [], "market_data_ohlc": {}}).get_today_summary() is None
