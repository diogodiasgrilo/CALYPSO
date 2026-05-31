"""
Tests for v1.2.7 Daily Summary column redesign.

Covers:
1. MarketData OHLC tracking (spx_open, vix_open, vix_low + existing fields),
   including the pre-market session gate that excludes extended-hours quotes.
2. get_daily_summary() P&L breakdown (stop_loss_debits, expired_credits) —
   the REAL FIX #78 derivation: stop_loss_debits = max(0, expired_credits -
   total_realized_pnl), exercised via the production method.
3. logger_service.py HYDRA header/row alignment (42 cols, A..AP).
4. State file OHLC persistence and restoration
5. log_daily_summary() sheets_summary construction

Run: .venv/bin/python -m pytest tests/test_daily_summary_v127.py -v
"""

import os
import sys
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.base_strategy import MarketData, MEICStrategy


@pytest.fixture(autouse=True)
def _force_regular_session(monkeypatch):
    """Make the intraday-OHLC tests deterministic at any wall-clock hour.

    MarketData.update_spx/update_vix capture open/high/low ONLY during the
    regular session (>= 9:30 ET, gated by _is_regular_session_or_later). These
    tests exercise that capture, so they implicitly assume RTH — without this
    they pass when run after 09:30 ET but fail between 00:00 and 09:30 ET (a
    time-dependent, flaky suite). Forcing the gate True here keeps the tests
    hermetic; the gate's production logic is unchanged.
    """
    monkeypatch.setattr(
        MarketData, "_is_regular_session_or_later", staticmethod(lambda: True)
    )


# ============================================================
# 1. MarketData OHLC Tracking Tests
# ============================================================

class TestMarketDataOHLC:
    """Test MarketData open/high/low/close tracking for SPX and VIX."""

    def test_spx_open_set_on_first_update(self):
        """SPX open should capture the first valid price."""
        md = MarketData()
        assert md.spx_open == 0.0
        md.update_spx(6950.0)
        assert md.spx_open == 6950.0

    def test_spx_open_not_overwritten_by_later_updates(self):
        """SPX open should NOT change after first price."""
        md = MarketData()
        md.update_spx(6950.0)
        md.update_spx(6960.0)
        md.update_spx(6940.0)
        assert md.spx_open == 6950.0

    def test_spx_open_ignores_zero_price(self):
        """SPX open should not be set by a 0 price."""
        md = MarketData()
        md.update_spx(0.0)
        assert md.spx_open == 0.0  # Still unset
        md.update_spx(6950.0)
        assert md.spx_open == 6950.0

    def test_spx_high_tracks_maximum(self):
        """SPX high should track the maximum price seen."""
        md = MarketData()
        md.update_spx(6950.0)
        md.update_spx(6980.0)
        md.update_spx(6960.0)
        assert md.spx_high == 6980.0

    def test_spx_low_tracks_minimum(self):
        """SPX low should track the minimum price seen."""
        md = MarketData()
        md.update_spx(6950.0)
        md.update_spx(6920.0)
        md.update_spx(6940.0)
        assert md.spx_low == 6920.0

    def test_spx_low_default_is_inf(self):
        """SPX low should default to inf (no updates yet)."""
        md = MarketData()
        assert md.spx_low == float('inf')

    def test_vix_open_set_on_first_update(self):
        """VIX open should capture the first valid VIX value."""
        md = MarketData()
        assert md.vix_open == 0.0
        md.update_vix(17.5)
        assert md.vix_open == 17.5

    def test_vix_open_not_overwritten_by_later_updates(self):
        """VIX open should NOT change after first value."""
        md = MarketData()
        md.update_vix(17.5)
        md.update_vix(18.0)
        md.update_vix(16.8)
        assert md.vix_open == 17.5

    def test_vix_open_ignores_zero(self):
        """VIX open should not be set by a 0 value."""
        md = MarketData()
        md.update_vix(0.0)
        assert md.vix_open == 0.0  # Still unset
        md.update_vix(17.5)
        assert md.vix_open == 17.5

    def test_vix_high_tracks_maximum(self):
        """VIX high should track the maximum VIX seen."""
        md = MarketData()
        md.update_vix(17.5)
        md.update_vix(21.0)
        md.update_vix(19.0)
        assert md.vix_high == 21.0

    def test_vix_low_tracks_minimum(self):
        """VIX low should track the minimum VIX seen."""
        md = MarketData()
        md.update_vix(17.5)
        md.update_vix(15.2)
        md.update_vix(16.8)
        assert md.vix_low == 15.2

    def test_vix_low_default_is_inf(self):
        """VIX low should default to inf (no updates yet)."""
        md = MarketData()
        assert md.vix_low == float('inf')

    def test_vix_samples_populated(self):
        """VIX samples list should grow with each update."""
        md = MarketData()
        md.update_vix(17.5)
        md.update_vix(18.0)
        md.update_vix(16.5)
        assert len(md.vix_samples) == 3
        assert md.vix_samples == [17.5, 18.0, 16.5]

    def test_reset_daily_tracking_clears_all_ohlc(self):
        """Reset should clear ALL OHLC fields to defaults."""
        md = MarketData()
        # Populate all fields
        md.update_spx(6950.0)
        md.update_spx(6980.0)
        md.update_spx(6920.0)
        md.update_vix(17.5)
        md.update_vix(21.0)
        md.update_vix(15.2)

        # Verify populated
        assert md.spx_open == 6950.0
        assert md.spx_high == 6980.0
        assert md.spx_low == 6920.0
        assert md.vix_open == 17.5
        assert md.vix_high == 21.0
        assert md.vix_low == 15.2

        # Reset
        md.reset_daily_tracking()

        # Verify all cleared
        assert md.spx_open == 0.0
        assert md.spx_high == 0.0
        assert md.spx_low == float('inf')
        assert md.vix_open == 0.0
        assert md.vix_high == 0.0
        assert md.vix_low == float('inf')
        assert len(md.vix_samples) == 0

    def test_single_update_sets_open_high_low_equal(self):
        """After one SPX update, open=high=low=that price."""
        md = MarketData()
        md.update_spx(6950.0)
        assert md.spx_open == 6950.0
        assert md.spx_high == 6950.0
        assert md.spx_low == 6950.0

    def test_single_vix_update_sets_open_high_low_equal(self):
        """After one VIX update, open=high=low=that value."""
        md = MarketData()
        md.update_vix(17.5)
        assert md.vix_open == 17.5
        assert md.vix_high == 17.5
        assert md.vix_low == 17.5

    def test_negative_prices_ignored(self):
        """Negative prices should be ignored (price > 0 guard)."""
        md = MarketData()
        md.update_spx(-100.0)
        assert md.spx_open == 0.0
        assert md.spx_high == 0.0
        assert md.spx_low == float('inf')

        md.update_vix(-5.0)
        assert md.vix_open == 0.0
        assert md.vix_high == 0.0
        assert md.vix_low == float('inf')

    def test_ohlc_after_restore_then_updates(self):
        """Simulate state restore followed by live price updates."""
        md = MarketData()
        # Simulate restored state (as if loaded from state file)
        md.spx_open = 6970.55
        md.spx_high = 6985.81
        md.spx_low = 6937.67
        md.vix_open = 17.35
        md.vix_high = 17.97
        md.vix_low = 17.14

        # Live updates after restart
        md.update_spx(6960.0)  # Between existing high/low
        assert md.spx_open == 6970.55  # Open unchanged (non-zero)
        assert md.spx_high == 6985.81  # High unchanged (6960 < 6985.81)
        assert md.spx_low == 6937.67   # Low unchanged (6960 > 6937.67)

        # New high
        md.update_spx(6990.0)
        assert md.spx_high == 6990.0  # Updated

        # New low
        md.update_spx(6930.0)
        assert md.spx_low == 6930.0  # Updated

        # VIX same pattern
        md.update_vix(17.50)  # Between existing high/low
        assert md.vix_open == 17.35  # Unchanged
        assert md.vix_high == 17.97  # Unchanged
        assert md.vix_low == 17.14   # Unchanged

        # New VIX high
        md.update_vix(22.0)
        assert md.vix_high == 22.0

        # New VIX low
        md.update_vix(16.0)
        assert md.vix_low == 16.0

    def test_premarket_tick_updates_live_price_but_not_ohlc(self, monkeypatch):
        """Pre-market tick must set live spx_price/vix but NOT capture OHLC.

        Overrides the autouse RTH fixture to force the session gate False,
        exercising the early-return branch in update_spx/update_vix
        (base_strategy.py ~698-699 / ~723-724). This is the entire reason the
        gate exists: pre-market Saxo extended-hours quotes must not contaminate
        the session open/high/low anchor.
        """
        monkeypatch.setattr(
            MarketData, "_is_regular_session_or_later", staticmethod(lambda: False)
        )
        md = MarketData()
        md.update_spx(6950.0)
        # Live price is updated (flash-crash tracking works pre-market)...
        assert md.spx_price == 6950.0
        assert len(md.price_history) == 1
        # ...but OHLC anchors are untouched.
        assert md.spx_open == 0.0
        assert md.spx_high == 0.0
        assert md.spx_low == float('inf')

        md.update_vix(17.5)
        assert md.vix == 17.5
        assert md.vix_open == 0.0
        assert md.vix_high == 0.0
        assert md.vix_low == float('inf')
        assert len(md.vix_samples) == 0

    def test_first_rth_tick_captures_open_after_premarket(self, monkeypatch):
        """After pre-market ticks (no capture), first RTH tick sets the open.

        Verifies the gate flips capture on at 9:30 ET: the session open must be
        the first regular-session print, not the earlier pre-market print.
        """
        # Pre-market: gate False, no capture.
        monkeypatch.setattr(
            MarketData, "_is_regular_session_or_later", staticmethod(lambda: False)
        )
        md = MarketData()
        md.update_spx(6900.0)   # 4am print - must NOT become the open
        md.update_vix(16.0)
        assert md.spx_open == 0.0
        assert md.vix_open == 0.0

        # Regular session: gate True, capture begins.
        monkeypatch.setattr(
            MarketData, "_is_regular_session_or_later", staticmethod(lambda: True)
        )
        md.update_spx(6950.0)
        md.update_vix(17.5)
        assert md.spx_open == 6950.0   # open is the first RTH tick, not 6900
        assert md.spx_high == 6950.0
        assert md.spx_low == 6950.0
        assert md.vix_open == 17.5
        assert md.vix_samples == [17.5]


# ============================================================
# 2. get_daily_summary() P&L Breakdown Tests
# ============================================================

class TestGetDailySummaryPnLBreakdown:
    """Test the REAL P&L breakdown computed by MEICStrategy.get_daily_summary().

    Production (FIX #78, base_strategy.py:4025-4034) derives the breakdown from
    the actual realized P&L instead of theoretical stop trigger levels, because
    market-order slippage means actual_close_cost != stop_level:

        expired_credits   = sum(call_spread_credit / put_spread_credit
                                 over sides flagged *_side_expired)
        stop_loss_debits  = max(0.0, expired_credits - total_realized_pnl)

    These tests call the real get_daily_summary via a lightweight stub so a
    regression in that derivation is actually caught (the previous version of
    this class re-implemented an abandoned theoretical-stop formula and tested
    nothing in production).
    """

    def _make_mock_entry(self, call_expired=False, put_expired=False,
                          call_spread_credit=0.0, put_spread_credit=0.0):
        """Create a mock IronCondorEntry exposing the fields get_daily_summary reads.

        get_daily_summary only inspects *_side_expired and *_spread_credit when
        accumulating expired_credits (it derives debits from realized P&L, not
        from per-entry stop levels), so those are the only fields modelled here.
        """
        entry = MagicMock()
        entry.call_side_expired = call_expired
        entry.put_side_expired = put_expired
        entry.call_spread_credit = call_spread_credit
        entry.put_spread_credit = put_spread_credit
        return entry

    def _compute_breakdown(self, entries, total_realized_pnl=0.0):
        """Call the REAL MEICStrategy.get_daily_summary breakdown logic.

        Builds a minimal stub exposing exactly the attributes/methods the
        method touches, then invokes the unbound production method against it.
        Returns (stop_loss_debits, expired_credits) read from the real result.
        """
        daily_state = SimpleNamespace(
            entries=list(entries),
            total_realized_pnl=total_realized_pnl,
            date="2026-02-10",
            entries_completed=len(entries),
            entries_failed=0,
            entries_skipped=0,
            total_credit_received=0.0,
            call_stops_triggered=0,
            put_stops_triggered=0,
            double_stops=0,
            circuit_breaker_opens=0,
            total_commission=0.0,
        )
        stub = SimpleNamespace(
            daily_state=daily_state,
            _get_total_saxo_pnl=lambda: 0.0,
            _check_pnl_sanity=lambda *a, **k: True,
        )
        result = MEICStrategy.get_daily_summary(stub)
        return result["stop_loss_debits"], result["expired_credits"]

    def test_no_entries_returns_zero(self):
        """No entries means zero debits and zero expired credits."""
        debits, credits = self._compute_breakdown([], total_realized_pnl=0.0)
        assert debits == 0.0
        assert credits == 0.0

    def test_all_expired_no_stops(self):
        """All entries expired = credits earned, debits clamp to 0 when realized >= credits."""
        entries = [
            self._make_mock_entry(
                call_expired=True, put_expired=True,
                call_spread_credit=1.25, put_spread_credit=1.30
            ),
            self._make_mock_entry(
                call_expired=True, put_expired=True,
                call_spread_credit=1.10, put_spread_credit=1.15
            ),
        ]
        # All expired with no slippage: realized P&L == total expired credit.
        debits, credits = self._compute_breakdown(entries, total_realized_pnl=4.80)
        assert debits == 0.0
        assert abs(credits - 4.80) < 0.01

    def test_one_side_stopped_other_expired_full_ic(self):
        """Full IC: put stopped, call expired. Debit derived from realized P&L."""
        # Full IC: $2.50 total credit ($1.25/$1.30). Call side expires (keep $1.25),
        # put side stopped with slippage -> realized P&L = +0.05 (gross).
        entries = [
            self._make_mock_entry(
                call_expired=True,
                call_spread_credit=1.25, put_spread_credit=1.30,
            ),
        ]
        # Only the call side is flagged expired -> expired_credits = 1.25.
        # Realized P&L of +0.05 implies stop_loss_debits = 1.25 - 0.05 = 1.20.
        debits, credits = self._compute_breakdown(entries, total_realized_pnl=0.05)
        assert abs(debits - 1.20) < 0.01
        assert abs(credits - 1.25) < 0.01
        # Identity: expired_credits - stop_loss_debits == realized (gross) P&L.
        assert abs((credits - debits) - 0.05) < 0.01

    def test_double_stop_full_ic(self):
        """Both sides stopped (no expirations) - debit = abs(realized loss)."""
        # Full IC: $2.50 total ($1.20/$1.30), both stopped -> lost the credit.
        entries = [
            self._make_mock_entry(
                call_spread_credit=1.20, put_spread_credit=1.30,
            ),
        ]
        # No side flagged expired -> expired_credits = 0; realized P&L = -2.50.
        debits, credits = self._compute_breakdown(entries, total_realized_pnl=-2.50)
        # stop_loss_debits = max(0, 0 - (-2.50)) = 2.50
        assert abs(debits - 2.50) < 0.01
        assert credits == 0.0
        # Identity: 0 - 2.50 == -2.50 gross P&L (lost total credit)
        assert abs((credits - debits) - (-2.50)) < 0.01

    def test_one_sided_entry_stopped(self):
        """One-sided (put-only) stopped: debit = abs(realized loss)."""
        entries = [
            self._make_mock_entry(put_spread_credit=1.28),  # not expired
        ]
        # Put-only stop at 2x credit (Fix #40) -> net realized loss = -1.28.
        debits, credits = self._compute_breakdown(entries, total_realized_pnl=-1.28)
        assert abs(debits - 1.28) < 0.01
        assert credits == 0.0

    def test_one_sided_entry_expired(self):
        """One-sided (put-only): expired = credit kept, no debit."""
        entries = [
            self._make_mock_entry(put_expired=True, put_spread_credit=1.28),
        ]
        # Expired and kept -> realized P&L == credit, debits clamp to 0.
        debits, credits = self._compute_breakdown(entries, total_realized_pnl=1.28)
        assert debits == 0.0
        assert abs(credits - 1.28) < 0.01

    def test_identity_feb10(self):
        """Feb 10: Expired Credits - Debits = realized (gross) P&L."""
        # 5 one-sided entries (put-only): 4 expired, 1 stopped.
        # Expired credits = 128+130+126+128 = 512.
        # Stopped entry lost net ~128 (2x credit stop) -> realized = 512 - 128 = 384.
        entries = [
            self._make_mock_entry(put_expired=True, put_spread_credit=128.0),
            self._make_mock_entry(put_expired=True, put_spread_credit=130.0),
            self._make_mock_entry(put_expired=True, put_spread_credit=126.0),
            self._make_mock_entry(put_expired=True, put_spread_credit=128.0),
            self._make_mock_entry(put_spread_credit=128.0),  # stopped (not expired)
        ]
        debits, credits = self._compute_breakdown(entries, total_realized_pnl=384.0)
        # expired_credits = 512, stop_loss_debits = max(0, 512 - 384) = 128
        assert credits == 512.0
        assert debits == 128.0
        assert debits < credits  # debits always < credits on a profitable day

    def test_identity_always_holds(self):
        """Verify: Expired Credits - stop_loss_debits == realized P&L for any mix."""
        # 3 full ICs ($1.30/$1.30 each): entry1 put stopped/call expired,
        # entry2 both expired, entry3 call stopped/put expired.
        entries = [
            self._make_mock_entry(
                call_expired=True,  # put side stopped (not flagged expired)
                call_spread_credit=1.30, put_spread_credit=1.30,
            ),
            self._make_mock_entry(
                call_expired=True, put_expired=True,
                call_spread_credit=1.30, put_spread_credit=1.30,
            ),
            self._make_mock_entry(
                put_expired=True,  # call side stopped (not flagged expired)
                call_spread_credit=1.30, put_spread_credit=1.30,
            ),
        ]
        # Expired sides: 1.30*4 = 5.20. Two stops lost net 1.30 each -> realized:
        # total credit 7.80 - gross stop debits 2.60 = 5.20... realized (gross) = 2.60.
        debits, credits = self._compute_breakdown(entries, total_realized_pnl=2.60)
        assert abs(credits - 5.20) < 0.01
        # stop_loss_debits = max(0, 5.20 - 2.60) = 2.60
        assert abs(debits - 2.60) < 0.01
        # Identity: expired_credits - stop_loss_debits == realized (gross) P&L.
        assert abs((credits - debits) - 2.60) < 0.01

    def test_debits_clamp_at_zero(self):
        """stop_loss_debits never goes negative even if realized > expired credits.

        Long-salvage revenue (MKT-033) can push realized P&L above the summed
        expired credits; production clamps with max(0.0, ...). Guard that.
        """
        entries = [
            self._make_mock_entry(put_expired=True, put_spread_credit=100.0),
        ]
        debits, credits = self._compute_breakdown(entries, total_realized_pnl=150.0)
        assert credits == 100.0
        assert debits == 0.0  # max(0, 100 - 150) clamps, not -50

    def test_skipped_sides_not_counted(self):
        """Skipped sides (one-sided entries) should not appear in expired credits."""
        entry = self._make_mock_entry(
            call_expired=True,
            call_spread_credit=1.50,
            put_spread_credit=0.0,  # skipped side, not flagged expired
        )
        debits, credits = self._compute_breakdown([entry], total_realized_pnl=1.50)
        assert debits == 0.0
        assert credits == 1.50


# ============================================================
# 3. Logger Service Column Alignment Tests
# ============================================================

class TestLoggerServiceColumnAlignment:
    """Test that header and row have exactly 42 elements in correct order.

    Mirrors the production HYDRA "Daily Summary" layout in
    shared/logger_service.py (header at lines ~508-538, row builder at
    ~2129-2184, worksheet created with cols=42). The 34-column layout these
    tests previously asserted is stale: Phase 2 added Early Close (2),
    cumulative-ROC metrics (4), Long Salvage (1) and Contracts (1) for 42 total.
    NOTE: EXPECTED_HEADERS / the row builder below are hand-copied from
    logger_service.py and must be kept in sync with it (see cross-file note:
    the long-term fix is a shared module-level header constant + row builder).
    """

    EXPECTED_HEADERS = [
        # Market Context (9 cols)
        "Date", "SPX Open", "SPX Close", "SPX High", "SPX Low",
        "VIX Open", "VIX Close", "VIX High", "VIX Low",
        # Bot Activity (7 cols)
        "Entries Completed", "Entries Skipped",
        "Full ICs", "One-Sided Entries",
        "Bullish Signals", "Bearish Signals", "Neutral Signals",
        # Position Outcomes (4 cols)
        "Total Credit ($)", "Call Stops", "Put Stops", "Double Stops",
        # P&L Breakdown (7 cols)
        "Stop Loss Debits ($)", "Commission ($)", "Expired Credits ($)",
        "Daily P&L ($)", "Daily P&L (EUR)",
        "Cumulative P&L ($)", "Cumulative P&L (EUR)",
        # Performance & Risk (6 cols)
        "Win Rate (%)",
        "Capital Deployed ($)", "Return on Capital (%)", "Sortino Ratio",
        "Max Loss Stops ($)", "Max Loss Catastrophic ($)",
        # MKT-018 Early Close (2 cols)
        "Early Close", "Early Close Time",
        # Other
        "Notes",
        # Cumulative ROC metrics (4 cols)
        "Avg Capital Deployed ($)", "Cumulative ROC (%)",
        "Avg Daily ROC (%)", "Annualized Return (%)",
        # MKT-033 Long salvage (1 col)
        "Long Salvage ($)",
        # Phase 2 D-4 Contracts per entry (1 col)
        "Contracts",
    ]

    def test_header_count_is_42(self):
        """Header list should have exactly 42 elements."""
        assert len(self.EXPECTED_HEADERS) == 42

    def test_header_starts_with_date(self):
        assert self.EXPECTED_HEADERS[0] == "Date"

    def test_header_ends_with_contracts(self):
        assert self.EXPECTED_HEADERS[-1] == "Contracts"

    def test_market_context_group(self):
        """First 9 columns are Market Context."""
        market_ctx = self.EXPECTED_HEADERS[0:9]
        assert market_ctx == [
            "Date", "SPX Open", "SPX Close", "SPX High", "SPX Low",
            "VIX Open", "VIX Close", "VIX High", "VIX Low",
        ]

    def test_bot_activity_group(self):
        """Columns 10-16 are Bot Activity."""
        bot_activity = self.EXPECTED_HEADERS[9:16]
        assert bot_activity == [
            "Entries Completed", "Entries Skipped",
            "Full ICs", "One-Sided Entries",
            "Bullish Signals", "Bearish Signals", "Neutral Signals",
        ]

    def test_position_outcomes_group(self):
        """Columns 17-20 are Position Outcomes."""
        outcomes = self.EXPECTED_HEADERS[16:20]
        assert outcomes == [
            "Total Credit ($)", "Call Stops", "Put Stops", "Double Stops",
        ]

    def test_pnl_breakdown_group(self):
        """Columns 21-27 are P&L Breakdown."""
        pnl = self.EXPECTED_HEADERS[20:27]
        assert pnl == [
            "Stop Loss Debits ($)", "Commission ($)", "Expired Credits ($)",
            "Daily P&L ($)", "Daily P&L (EUR)",
            "Cumulative P&L ($)", "Cumulative P&L (EUR)",
        ]

    def test_performance_risk_group(self):
        """Columns 28-33 are Performance & Risk."""
        perf = self.EXPECTED_HEADERS[27:33]
        assert perf == [
            "Win Rate (%)",
            "Capital Deployed ($)", "Return on Capital (%)", "Sortino Ratio",
            "Max Loss Stops ($)", "Max Loss Catastrophic ($)",
        ]

    def test_early_close_group(self):
        """Columns 34-35 are MKT-018 Early Close."""
        early_close = self.EXPECTED_HEADERS[33:35]
        assert early_close == ["Early Close", "Early Close Time"]

    def test_notes_then_roc_and_salvage_tail(self):
        """Columns 36-42: Notes, ROC metrics (4), Long Salvage, Contracts."""
        tail = self.EXPECTED_HEADERS[35:42]
        assert tail == [
            "Notes",
            "Avg Capital Deployed ($)", "Cumulative ROC (%)",
            "Avg Daily ROC (%)", "Annualized Return (%)",
            "Long Salvage ($)",
            "Contracts",
        ]

    def test_row_builder_produces_42_elements(self):
        """Build a row from sample data and verify it has 42 elements."""
        summary = {
            "date": "2026-02-14",
            "spx_open": 6950.55,
            "spx_close": 6943.87,
            "underlying_close": 6943.87,
            "spx_high": 6985.81,
            "spx_low": 6937.67,
            "vix_open": 17.35,
            "vix_close": 17.81,
            "vix": 17.81,
            "vix_high": 17.97,
            "vix_low": 17.14,
            "entries_completed": 5,
            "entries_skipped": 0,
            "full_ics": 3,
            "one_sided_entries": 2,
            "bullish_signals": 2,
            "bearish_signals": 0,
            "neutral_signals": 3,
            "total_credit": 850.50,
            "call_stops": 1,
            "put_stops": 2,
            "double_stops": 0,
            "stop_loss_debits": 260.00,
            "total_commission": 30.00,
            "expired_credits": 520.00,
            "daily_pnl": 350.00,
            "daily_pnl_eur": 294.27,
            "cumulative_pnl": 350.00,
            "cumulative_pnl_eur": 294.27,
            "capital_deployed": 12500.00,
            "return_on_capital": 2.8,
            "sortino_ratio": 1.5,
            "max_loss_stops": 850.50,
            "max_loss_catastrophic": 11649.50,
            "early_close": "No",
            "early_close_time": "",
            "notes": "Post-settlement",
            "avg_capital_deployed": 12000.00,
            "cumulative_roc": 5.5,
            "avg_daily_roc": 0.2750,
            "annualized_return": 42.3,
            "long_salvage_revenue": 0.0,
            "contracts_per_entry": 2,
        }

        # Replicate the row builder logic from logger_service.py (HYDRA, 42 cols)
        entries_completed = summary.get("entries_completed", 0)
        full_ics = summary.get("full_ics", 0)
        one_sided = summary.get("one_sided_entries", 0)
        call_stops = summary.get("call_stops", 0)
        put_stops = summary.get("put_stops", 0)
        double_stops = summary.get("double_stops", 0)
        bullish_count = summary.get("bullish_signals", 0)
        bearish_count = summary.get("bearish_signals", 0)
        neutral_count = summary.get("neutral_signals", 0)

        total_entries = entries_completed
        if total_entries > 0:
            wins = total_entries - double_stops - (call_stops + put_stops - 2 * double_stops)
            win_rate = (wins / total_entries) * 100 if total_entries > 0 else 0
        else:
            win_rate = 0

        daily_pnl = summary.get('daily_pnl', summary.get('total_pnl', 0))

        row = [
            # Market Context (9 cols)
            summary.get("date", datetime.now().strftime("%Y-%m-%d")),
            f"{summary.get('spx_open', 0):.2f}",
            f"{summary.get('spx_close', summary.get('underlying_close', 0)):.2f}",
            f"{summary.get('spx_high', 0):.2f}",
            f"{summary.get('spx_low', 0):.2f}",
            f"{summary.get('vix_open', 0):.2f}",
            f"{summary.get('vix_close', summary.get('vix', 0)):.2f}",
            f"{summary.get('vix_high', 0):.2f}",
            f"{summary.get('vix_low', 0):.2f}",
            # Bot Activity (7 cols)
            str(entries_completed),
            str(summary.get('entries_skipped', 0)),
            str(full_ics),
            str(one_sided),
            str(bullish_count),
            str(bearish_count),
            str(neutral_count),
            # Position Outcomes (4 cols)
            f"{summary.get('total_credit', 0):.2f}",
            str(call_stops),
            str(put_stops),
            str(double_stops),
            # P&L Breakdown (7 cols)
            f"{summary.get('stop_loss_debits', 0):.2f}",
            f"{summary.get('total_commission', 0):.2f}",
            f"{summary.get('expired_credits', 0):.2f}",
            f"{daily_pnl:.2f}",
            f"{summary.get('daily_pnl_eur', 0):.2f}",
            f"{summary.get('cumulative_pnl', 0):.2f}",
            f"{summary.get('cumulative_pnl_eur', 0):.2f}",
            # Performance & Risk (6 cols)
            f"{win_rate:.1f}",
            f"{summary.get('capital_deployed', 0):.2f}",
            f"{summary.get('return_on_capital', 0):.2f}",
            f"{summary.get('sortino_ratio', 0):.2f}",
            f"{summary.get('max_loss_stops', 0):.2f}",
            f"{summary.get('max_loss_catastrophic', 0):.2f}",
            # MKT-018 Early Close (2 cols)
            summary.get("early_close", "No"),
            summary.get("early_close_time", ""),
            # Other
            summary.get("notes", ""),
            # Cumulative ROC metrics (4 cols)
            f"{summary.get('avg_capital_deployed', 0):.2f}",
            f"{summary.get('cumulative_roc', 0):.2f}",
            f"{summary.get('avg_daily_roc', 0):.4f}",
            f"{summary.get('annualized_return', 0):.1f}",
            # MKT-033 Long salvage (1 col)
            f"{summary.get('long_salvage_revenue', 0):.2f}",
            # Phase 2 D-4 Contracts per entry (1 col)
            str(summary.get("contracts_per_entry") or 1),
        ]

        assert len(row) == 42, f"Row has {len(row)} elements, expected 42"
        assert len(row) == len(self.EXPECTED_HEADERS)

    def test_row_values_match_header_positions(self):
        """Verify that row values match their expected header positions."""
        summary = {
            "date": "2026-02-10",
            "spx_open": 6970.55,
            "spx_close": 6943.87,
            "spx_high": 6985.81,
            "spx_low": 6937.67,
            "vix_open": 17.35,
            "vix_close": 17.81,
            "vix_high": 17.97,
            "vix_low": 17.14,
            "entries_completed": 5,
            "entries_skipped": 0,
            "full_ics": 3,
            "one_sided_entries": 2,
            "bullish_signals": 2,
            "bearish_signals": 0,
            "neutral_signals": 3,
            "total_credit": 850.50,
            "call_stops": 1,
            "put_stops": 2,
            "double_stops": 0,
            "stop_loss_debits": 260.00,
            "total_commission": 30.00,
            "expired_credits": 520.00,
            "daily_pnl": 350.00,
            "daily_pnl_eur": 294.27,
            "cumulative_pnl": 350.00,
            "cumulative_pnl_eur": 294.27,
            "capital_deployed": 12500.00,
            "return_on_capital": 2.8,
            "sortino_ratio": 1.5,
            "max_loss_stops": 850.50,
            "max_loss_catastrophic": 11649.50,
            "early_close": "No",
            "early_close_time": "",
            "notes": "Post-settlement",
            "avg_capital_deployed": 12000.00,
            "cumulative_roc": 5.5,
            "avg_daily_roc": 0.2750,
            "annualized_return": 42.3,
            "long_salvage_revenue": 0.0,
            "contracts_per_entry": 2,
        }

        # Build row (same logic, HYDRA 42-col layout)
        entries_completed = summary.get("entries_completed", 0)
        full_ics = summary.get("full_ics", 0)
        one_sided = summary.get("one_sided_entries", 0)
        call_stops = summary.get("call_stops", 0)
        put_stops = summary.get("put_stops", 0)
        double_stops = summary.get("double_stops", 0)
        bullish_count = summary.get("bullish_signals", 0)
        bearish_count = summary.get("bearish_signals", 0)
        neutral_count = summary.get("neutral_signals", 0)
        win_rate = 40.0  # 2/5 = 40%
        daily_pnl = summary.get('daily_pnl', 0)

        row = [
            summary.get("date"),
            f"{summary.get('spx_open', 0):.2f}",
            f"{summary.get('spx_close', 0):.2f}",
            f"{summary.get('spx_high', 0):.2f}",
            f"{summary.get('spx_low', 0):.2f}",
            f"{summary.get('vix_open', 0):.2f}",
            f"{summary.get('vix_close', 0):.2f}",
            f"{summary.get('vix_high', 0):.2f}",
            f"{summary.get('vix_low', 0):.2f}",
            str(entries_completed),
            str(summary.get('entries_skipped', 0)),
            str(full_ics),
            str(one_sided),
            str(bullish_count),
            str(bearish_count),
            str(neutral_count),
            f"{summary.get('total_credit', 0):.2f}",
            str(call_stops),
            str(put_stops),
            str(double_stops),
            f"{summary.get('stop_loss_debits', 0):.2f}",
            f"{summary.get('total_commission', 0):.2f}",
            f"{summary.get('expired_credits', 0):.2f}",
            f"{daily_pnl:.2f}",
            f"{summary.get('daily_pnl_eur', 0):.2f}",
            f"{summary.get('cumulative_pnl', 0):.2f}",
            f"{summary.get('cumulative_pnl_eur', 0):.2f}",
            f"{win_rate:.1f}",
            f"{summary.get('capital_deployed', 0):.2f}",
            f"{summary.get('return_on_capital', 0):.2f}",
            f"{summary.get('sortino_ratio', 0):.2f}",
            f"{summary.get('max_loss_stops', 0):.2f}",
            f"{summary.get('max_loss_catastrophic', 0):.2f}",
            summary.get("early_close", "No"),
            summary.get("early_close_time", ""),
            summary.get("notes", ""),
            f"{summary.get('avg_capital_deployed', 0):.2f}",
            f"{summary.get('cumulative_roc', 0):.2f}",
            f"{summary.get('avg_daily_roc', 0):.4f}",
            f"{summary.get('annualized_return', 0):.1f}",
            f"{summary.get('long_salvage_revenue', 0):.2f}",
            str(summary.get("contracts_per_entry") or 1),
        ]

        # Row length must equal the header length (alignment guard).
        assert len(row) == len(self.EXPECTED_HEADERS) == 42

        # Spot-check specific positions
        assert row[0] == "2026-02-10"               # Date
        assert row[1] == "6970.55"                   # SPX Open
        assert row[2] == "6943.87"                   # SPX Close
        assert row[5] == "17.35"                     # VIX Open
        assert row[6] == "17.81"                     # VIX Close
        assert row[9] == "5"                         # Entries Completed
        assert row[13] == "2"                        # Bullish Signals
        assert row[16] == "850.50"                   # Total Credit
        assert row[20] == "260.00"                   # Stop Loss Debits
        assert row[21] == "30.00"                    # Commission
        assert row[22] == "520.00"                   # Expired Credits
        assert row[23] == "350.00"                   # Daily P&L
        assert row[25] == "350.00"                   # Cumulative P&L
        assert row[26] == "294.27"                   # Cumulative P&L EUR
        assert row[33] == "No"                       # Early Close
        assert row[34] == ""                         # Early Close Time
        assert row[35] == "Post-settlement"          # Notes
        assert row[36] == "12000.00"                 # Avg Capital Deployed
        assert row[37] == "5.50"                     # Cumulative ROC
        assert row[38] == "0.2750"                   # Avg Daily ROC
        assert row[39] == "42.3"                     # Annualized Return
        assert row[40] == "0.00"                     # Long Salvage
        assert row[41] == "2"                        # Contracts


# ============================================================
# 4. State File OHLC Persistence Tests
# ============================================================

class TestStateFileOHLCPersistence:
    """Test OHLC save-to / restore-from state file.

    These mirror the production guards in bots/hydra/strategy.py:
      - save:    _save_state_to_disk() lines ~9713-9720 (spx_low/vix_low ==
                 float('inf') -> 0.0 so the state JSON stays serializable;
                 an un-guarded inf would make json.dumps raise and the day's
                 state would silently fail to persist)
      - restore: _initialize_from_state_file() lines ~10885-10896 (low > 0 ->
                 set, otherwise keep the float('inf') default)
    The save/restore arithmetic is hand-copied here, not called from the
    strategy (constructing a full HydraStrategy pulls in IBKR/Saxo deps); the
    copies MUST be kept in sync with those source lines. See cross-file note for
    the durable fix (extract the OHLC pack/unpack into testable helpers)."""

    def test_save_ohlc_to_state_data(self):
        """Verify OHLC dict is correctly constructed for state file."""
        md = MarketData()
        md.update_spx(6970.0)
        md.update_spx(6990.0)
        md.update_spx(6930.0)
        md.update_vix(17.5)
        md.update_vix(21.0)
        md.update_vix(15.2)

        # Mirror save logic from strategy._save_state_to_disk() (~9713-9720)
        ohlc_dict = {
            "spx_open": md.spx_open,
            "spx_high": md.spx_high,
            "spx_low": md.spx_low if md.spx_low != float('inf') else 0.0,
            "vix_open": md.vix_open,
            "vix_high": md.vix_high,
            "vix_low": md.vix_low if md.vix_low != float('inf') else 0.0,
        }

        assert ohlc_dict["spx_open"] == 6970.0
        assert ohlc_dict["spx_high"] == 6990.0
        assert ohlc_dict["spx_low"] == 6930.0
        assert ohlc_dict["vix_open"] == 17.5
        assert ohlc_dict["vix_high"] == 21.0
        assert ohlc_dict["vix_low"] == 15.2

    def test_save_ohlc_with_no_updates_gives_zero_not_inf(self):
        """If no prices received, low should save as 0.0 (not float('inf'))."""
        md = MarketData()  # No updates

        ohlc_dict = {
            "spx_low": md.spx_low if md.spx_low != float('inf') else 0.0,
            "vix_low": md.vix_low if md.vix_low != float('inf') else 0.0,
        }

        assert ohlc_dict["spx_low"] == 0.0  # Not inf
        assert ohlc_dict["vix_low"] == 0.0  # Not inf

    def test_save_ohlc_is_json_serializable(self):
        """Verify the OHLC dict can be serialized to JSON (no inf/nan)."""
        md = MarketData()
        # With no updates, lows are inf - save logic should handle this
        ohlc_dict = {
            "spx_open": md.spx_open,
            "spx_high": md.spx_high,
            "spx_low": md.spx_low if md.spx_low != float('inf') else 0.0,
            "vix_open": md.vix_open,
            "vix_high": md.vix_high,
            "vix_low": md.vix_low if md.vix_low != float('inf') else 0.0,
        }

        # This would raise ValueError if inf/nan present
        json_str = json.dumps(ohlc_dict)
        assert json_str  # Not empty

        # Round-trip
        parsed = json.loads(json_str)
        assert parsed == ohlc_dict

    def test_restore_ohlc_from_state_file(self):
        """Simulate restoring OHLC from a state file dict."""
        md = MarketData()  # Fresh (all defaults)

        ohlc = {
            "spx_open": 6970.55,
            "spx_high": 6985.81,
            "spx_low": 6937.67,
            "vix_open": 17.35,
            "vix_high": 17.97,
            "vix_low": 17.14,
        }

        # Mirror restore logic from strategy._initialize_from_state_file() (~10885-10896)
        if ohlc:
            md.spx_open = ohlc.get("spx_open", 0.0)
            md.spx_high = ohlc.get("spx_high", 0.0)
            spx_low = ohlc.get("spx_low", 0.0)
            if spx_low > 0:
                md.spx_low = spx_low
            md.vix_open = ohlc.get("vix_open", 0.0)
            md.vix_high = ohlc.get("vix_high", 0.0)
            vix_low = ohlc.get("vix_low", 0.0)
            if vix_low > 0:
                md.vix_low = vix_low

        assert md.spx_open == 6970.55
        assert md.spx_high == 6985.81
        assert md.spx_low == 6937.67
        assert md.vix_open == 17.35
        assert md.vix_high == 17.97
        assert md.vix_low == 17.14

    def test_restore_with_zero_low_keeps_inf_default(self):
        """If state file has low=0.0 (no data), restore should keep inf."""
        md = MarketData()

        ohlc = {
            "spx_open": 0.0,
            "spx_high": 0.0,
            "spx_low": 0.0,  # Was inf, saved as 0.0
            "vix_open": 0.0,
            "vix_high": 0.0,
            "vix_low": 0.0,  # Was inf, saved as 0.0
        }

        if ohlc:
            md.spx_open = ohlc.get("spx_open", 0.0)
            md.spx_high = ohlc.get("spx_high", 0.0)
            spx_low = ohlc.get("spx_low", 0.0)
            if spx_low > 0:
                md.spx_low = spx_low
            md.vix_open = ohlc.get("vix_open", 0.0)
            md.vix_high = ohlc.get("vix_high", 0.0)
            vix_low = ohlc.get("vix_low", 0.0)
            if vix_low > 0:
                md.vix_low = vix_low

        # Low should remain inf (0.0 not restored)
        assert md.spx_low == float('inf')
        assert md.vix_low == float('inf')

    def test_restore_then_update_preserves_open(self):
        """After restore, new price updates should not overwrite open."""
        md = MarketData()

        # Restore
        md.spx_open = 6970.55
        md.spx_high = 6985.81
        md.spx_low = 6937.67

        # New updates
        md.update_spx(6960.0)  # Between high and low

        assert md.spx_open == 6970.55  # Preserved (not 0.0, so guard passes)
        assert md.spx_high == 6985.81  # Preserved (6960 < 6985.81)
        assert md.spx_low == 6937.67   # Preserved (6960 > 6937.67)

    def test_full_save_restore_roundtrip(self):
        """Full roundtrip: populate -> save -> fresh MD -> restore -> verify."""
        # Populate
        md1 = MarketData()
        md1.update_spx(6970.0)
        md1.update_spx(6990.0)
        md1.update_spx(6930.0)
        md1.update_spx(6950.0)
        md1.update_vix(17.5)
        md1.update_vix(21.0)
        md1.update_vix(15.2)
        md1.update_vix(18.0)

        # Save
        ohlc_dict = {
            "spx_open": md1.spx_open,
            "spx_high": md1.spx_high,
            "spx_low": md1.spx_low if md1.spx_low != float('inf') else 0.0,
            "vix_open": md1.vix_open,
            "vix_high": md1.vix_high,
            "vix_low": md1.vix_low if md1.vix_low != float('inf') else 0.0,
        }

        # Serialize/deserialize (simulates file I/O)
        json_str = json.dumps(ohlc_dict)
        ohlc_restored = json.loads(json_str)

        # Restore to fresh MarketData
        md2 = MarketData()
        md2.spx_open = ohlc_restored.get("spx_open", 0.0)
        md2.spx_high = ohlc_restored.get("spx_high", 0.0)
        spx_low = ohlc_restored.get("spx_low", 0.0)
        if spx_low > 0:
            md2.spx_low = spx_low
        md2.vix_open = ohlc_restored.get("vix_open", 0.0)
        md2.vix_high = ohlc_restored.get("vix_high", 0.0)
        vix_low = ohlc_restored.get("vix_low", 0.0)
        if vix_low > 0:
            md2.vix_low = vix_low

        # Verify roundtrip
        assert md2.spx_open == md1.spx_open == 6970.0
        assert md2.spx_high == md1.spx_high == 6990.0
        assert md2.spx_low == md1.spx_low == 6930.0
        assert md2.vix_open == md1.vix_open == 17.5
        assert md2.vix_high == md1.vix_high == 21.0
        assert md2.vix_low == md1.vix_low == 15.2

    def test_restore_with_missing_ohlc_key(self):
        """If state file has no market_data_ohlc key, MarketData stays default."""
        md = MarketData()
        saved_state = {"entries": [], "total_realized_pnl": 100}  # No market_data_ohlc

        ohlc = saved_state.get("market_data_ohlc", {})
        if ohlc:
            md.spx_open = ohlc.get("spx_open", 0.0)
            # ... etc

        # Defaults unchanged
        assert md.spx_open == 0.0
        assert md.spx_high == 0.0
        assert md.spx_low == float('inf')
        assert md.vix_open == 0.0
        assert md.vix_high == 0.0
        assert md.vix_low == float('inf')


# ============================================================
# 5. Sheets Summary Construction Tests
# ============================================================

class TestSheetsSummaryConstruction:
    """Test the sheets_summary dict construction in log_daily_summary()."""

    def test_inf_low_converted_to_zero_in_sheets(self):
        """float('inf') for spx_low/vix_low should become 0.0 in sheets_summary."""
        md = MarketData()  # No updates - lows are inf

        spx_low = md.spx_low if md.spx_low != float('inf') else 0.0
        vix_low = md.vix_low if md.vix_low != float('inf') else 0.0

        assert spx_low == 0.0
        assert vix_low == 0.0

    def test_normal_low_passes_through(self):
        """Normal spx_low/vix_low values pass through unchanged."""
        md = MarketData()
        md.update_spx(6950.0)
        md.update_vix(17.5)

        spx_low = md.spx_low if md.spx_low != float('inf') else 0.0
        vix_low = md.vix_low if md.vix_low != float('inf') else 0.0

        assert spx_low == 6950.0
        assert vix_low == 17.5

    # NOTE: The previous test_cumulative_pnl_computation /
    # test_cumulative_pnl_eur_conversion / test_eur_conversion_fallback_on_error
    # were removed: they asserted hand-computed arithmetic (650==650, 546==546,
    # 0==0) with no production code involved, so they could never catch a
    # regression. The real cumulative-P&L roll-up lives in
    # base_strategy._send_daily_summary (~line 4092) and the EUR conversion /
    # fail-to-0 fallback in log_daily_summary (~lines 4711-4719); exercising
    # those requires a full strategy + alert_service / _read_fx_rate harness and
    # belongs in an integration test (see cross-file note), not a tautology here.

    def test_sheets_summary_contains_all_new_keys(self):
        """Verify sheets_summary has all new OHLC and P&L breakdown keys."""
        # Simulate what log_daily_summary() produces
        md = MarketData()
        md.update_spx(6950.0)
        md.update_spx(6980.0)
        md.update_spx(6930.0)
        md.update_vix(17.5)
        md.update_vix(21.0)
        md.update_vix(15.2)

        sheets_summary = {
            "spx_open": md.spx_open,
            "spx_close": 6955.0,
            "spx_high": md.spx_high,
            "spx_low": md.spx_low if md.spx_low != float('inf') else 0.0,
            "vix_open": md.vix_open,
            "vix_close": 18.5,
            "vix_high": md.vix_high,
            "vix_low": md.vix_low if md.vix_low != float('inf') else 0.0,
            "stop_loss_debits": 260.0,
            "expired_credits": 520.0,
            "cumulative_pnl_eur": 294.27,
        }

        # All new keys present
        new_keys = [
            "spx_open", "spx_high", "spx_low",
            "vix_open", "vix_high", "vix_low",
            "stop_loss_debits", "expired_credits",
            "cumulative_pnl_eur",
        ]
        for key in new_keys:
            assert key in sheets_summary, f"Missing key: {key}"

    @staticmethod
    def _col_letter_to_number(letters: str) -> int:
        """Convert a spreadsheet column label (e.g. 'AH') to a 1-based index."""
        n = 0
        for ch in letters:
            n = n * 26 + (ord(ch) - ord('A') + 1)
        return n

    def test_a1_to_ap1_covers_42_columns(self):
        """The HYDRA daily-summary header occupies 42 columns -> A..AP.

        A=1, B=2, ..., Z=26, AA=27, ..., AP=42. The 34-col / A1:AH1 layout
        these tests previously assumed is stale (Phase 2 added 8 columns).
        """
        expected_cols = len(TestLoggerServiceColumnAlignment.EXPECTED_HEADERS)
        assert expected_cols == 42
        # Column index of the last header equals the header count.
        assert self._col_letter_to_number("AP") == expected_cols
        assert self._col_letter_to_number("A") == 1

    def test_production_bold_format_range_is_a1_aj1(self):
        """Document the production bold-format range used in logger_service.py.

        Production formats the header row with range "A1:AJ1" (AJ=36). NOTE:
        this is narrower than the 42-column header, so the last 6 header cells
        (AK..AP: Cumulative ROC, Avg Daily ROC, Annualized Return, Long Salvage,
        Contracts and one ROC col) are NOT bolded. This is a cosmetic-only
        production gap (Sheets formatting, no data impact); recorded so the
        constant stays visible if the layout changes. See cross-file note.
        """
        expected_cols = len(TestLoggerServiceColumnAlignment.EXPECTED_HEADERS)
        production_format_range = "A1:AJ1"  # shared/logger_service.py:549
        end_col = production_format_range.split(":")[1].rstrip("1")
        assert self._col_letter_to_number(end_col) == 36
        # The bold range currently under-covers the 42-col header (cosmetic).
        assert self._col_letter_to_number(end_col) < expected_cols


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
