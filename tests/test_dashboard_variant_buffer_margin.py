"""Comparison-page buffer MARGIN + SETTLING disposition (2026-06-12).

The variant comparison panel used to show buffer UTILIZATION (cost/stop, high =
near stop) — the inverse of the main dashboard's cushion (stop-cost)/stop, so a
safe day read red and a near-stop day read green. And a LIVE 0DTE that had
already expired (awaiting IBKR's hours-late settlement) showed "LIVE" post-close.
These tests pin the corrected behaviour: margin (100 = safe, 0 = at stop) +
SETTLING after the close.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The dashboard backend runs in its own venv (FastAPI). Skip cleanly in the main
# suite where it isn't installed; this runs in the dashboard env (and on the VM).
pytest.importorskip("fastapi")

from dashboard.backend.routers.variants import (  # noqa: E402
    _compute_buffer_margin,
    _min_buffer_margin_pct,
    _entry_disposition,
)


def _active_entry(**kw):
    """A monitoring entry with both sides placed and not finalized."""
    e = {
        "short_call_strike": 7505.0, "long_call_strike": 7510.0,
        "short_put_strike": 7300.0, "long_put_strike": 7295.0,
        "call_side_stop": 1400.0, "put_side_stop": 2100.0,
        "call_spread_value": 100.0, "put_spread_value": 50.0,
    }
    e.update(kw)
    return e


# ─── _compute_buffer_margin: MARGIN, not utilization ──────────────────────
class TestComputeBufferMargin:
    def test_low_cost_is_high_margin(self):
        # cost 100 vs stop 1400 → margin (1400-100)/1400 = 92.9% (safe)
        b = _compute_buffer_margin(_active_entry())
        assert b["call_pct"] == pytest.approx(92.9, abs=0.1)
        assert b["call_value"] == 100.0  # raw cost retained for the caption

    def test_near_stop_is_low_margin(self):
        # cost 1330 vs stop 1400 → margin 5% (near stop)
        b = _compute_buffer_margin(_active_entry(call_spread_value=1330.0))
        assert b["call_pct"] == pytest.approx(5.0, abs=0.1)

    def test_at_or_past_stop_clamps_to_zero(self):
        b = _compute_buffer_margin(_active_entry(call_spread_value=1500.0))
        assert b["call_pct"] == 0.0  # 0% margin = at/over the stop

    def test_full_margin_when_cost_zero(self):
        b = _compute_buffer_margin(_active_entry(call_spread_value=0.0))
        assert b["call_pct"] == 100.0

    def test_done_side_is_none(self):
        # a finalized side returns None (UI shows a placeholder, not 0/100)
        b = _compute_buffer_margin(_active_entry(call_side_expired=True))
        assert b["call_pct"] is None


# ─── _min_buffer_margin_pct: smallest margin reached today (inverse of peak) ──
class TestMinBufferMarginPct:
    def test_safe_day_high_margin(self):
        # only a small live cost → high min-margin
        m = _min_buffer_margin_pct([_active_entry()], db_path=None)
        assert m["call_pct"] == pytest.approx(92.9, abs=0.2)

    def test_near_stop_low_margin(self):
        m = _min_buffer_margin_pct([_active_entry(call_spread_value=1330.0)], db_path=None)
        assert m["call_pct"] == pytest.approx(5.0, abs=0.5)

    def test_real_stop_is_zero_margin(self):
        e = _active_entry(call_side_stopped=True, close_reason="STOP",
                          call_spread_value=None)
        m = _min_buffer_margin_pct([e], db_path=None)
        assert m["call_pct"] == 0.0  # a real stop = 0% margin (at the stop)


# ─── _entry_disposition: SETTLING after the close, LIVE during the session ──
class TestDispositionSettling:
    def test_live_during_session(self):
        assert _entry_disposition(_active_entry(), after_close=False) == "LIVE"

    def test_settling_after_close(self):
        # 0DTE has expired, awaiting (late) settlement → SETTLING, not LIVE
        assert _entry_disposition(_active_entry(), after_close=True) == "SETTLING"

    def test_close_reason_wins_over_after_close(self):
        e = _active_entry(close_reason="TP")
        assert _entry_disposition(e, after_close=True) == "TP"

    def test_expired_both_sides(self):
        e = _active_entry(call_side_expired=True, put_side_expired=True)
        assert _entry_disposition(e, after_close=True) == "EXPIRED"
