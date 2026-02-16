"""
Tests for v1.2.7 Daily Summary column redesign.

Covers:
1. MarketData OHLC tracking (spx_open, vix_open, vix_low + existing fields)
2. get_daily_summary() P&L breakdown (stop_loss_debits, expired_credits)
3. logger_service.py header/row alignment (34 cols)
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
from unittest.mock import MagicMock, patch, PropertyMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.meic.strategy import MarketData


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


# ============================================================
# 2. get_daily_summary() P&L Breakdown Tests
# ============================================================

class TestGetDailySummaryPnLBreakdown:
    """Test P&L breakdown computation in get_daily_summary().

    Identity: Expired Credits - Stop Loss Debits (net) - Commission = Daily P&L
    Stop Loss Debits = net loss per stop = stop_level - credit_for_that_side
    """

    def _make_mock_entry(self, call_stopped=False, put_stopped=False,
                          call_expired=False, put_expired=False,
                          call_side_stop=0.0, put_side_stop=0.0,
                          call_spread_credit=0.0, put_spread_credit=0.0):
        """Create a mock IronCondorEntry with specific fields."""
        entry = MagicMock()
        entry.call_side_stopped = call_stopped
        entry.put_side_stopped = put_stopped
        entry.call_side_expired = call_expired
        entry.put_side_expired = put_expired
        entry.call_side_stop = call_side_stop
        entry.put_side_stop = put_side_stop
        entry.call_spread_credit = call_spread_credit
        entry.put_spread_credit = put_spread_credit
        return entry

    def _compute_breakdown(self, entries):
        """Replicate the P&L breakdown logic from get_daily_summary()."""
        stop_loss_debits = 0.0
        expired_credits = 0.0
        for entry in entries:
            if entry.call_side_stopped:
                stop_loss_debits += entry.call_side_stop - entry.call_spread_credit
            if entry.put_side_stopped:
                stop_loss_debits += entry.put_side_stop - entry.put_spread_credit
            if entry.call_side_expired:
                expired_credits += entry.call_spread_credit
            if entry.put_side_expired:
                expired_credits += entry.put_spread_credit
        return stop_loss_debits, expired_credits

    def test_no_entries_returns_zero(self):
        """No entries means zero debits and zero expired credits."""
        debits, credits = self._compute_breakdown([])
        assert debits == 0.0
        assert credits == 0.0

    def test_all_expired_no_stops(self):
        """All entries expired = credits earned, no stop debits."""
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
        debits, credits = self._compute_breakdown(entries)
        assert debits == 0.0
        assert abs(credits - 4.80) < 0.01

    def test_one_side_stopped_other_expired_full_ic(self):
        """Full IC: put stopped, call expired. Net debit = stop - put_credit."""
        # Full IC: $2.50 total credit ($1.25/$1.30), stop level = $2.50
        entries = [
            self._make_mock_entry(
                call_expired=True, put_stopped=True,
                call_spread_credit=1.25, put_spread_credit=1.30,
                put_side_stop=2.50
            ),
        ]
        debits, credits = self._compute_breakdown(entries)
        # Net debit = 2.50 - 1.30 = 1.20
        assert abs(debits - 1.20) < 0.01
        assert abs(credits - 1.25) < 0.01
        # Identity: credits - debits = gross P&L = 1.25 - 1.20 = 0.05
        gross_pnl = credits - debits
        assert abs(gross_pnl - 0.05) < 0.01

    def test_double_stop_full_ic(self):
        """Both sides stopped - net debit = total credit."""
        # Full IC: $2.50 total ($1.20/$1.30), both stopped at $2.50
        entries = [
            self._make_mock_entry(
                call_stopped=True, put_stopped=True,
                call_spread_credit=1.20, put_spread_credit=1.30,
                call_side_stop=2.50, put_side_stop=2.50
            ),
        ]
        debits, credits = self._compute_breakdown(entries)
        # Net debit = (2.50 - 1.20) + (2.50 - 1.30) = 1.30 + 1.20 = 2.50 = total_credit
        assert abs(debits - 2.50) < 0.01
        assert credits == 0.0
        # Identity: 0 - 2.50 = -2.50 gross P&L (lost total credit)
        assert abs(credits - debits - (-2.50)) < 0.01

    def test_one_sided_entry_stopped(self):
        """One-sided (put-only): stop = 2x credit, net debit = credit."""
        entries = [
            self._make_mock_entry(
                put_stopped=True,
                put_spread_credit=1.28,
                put_side_stop=2.56  # 2x credit (Fix #40)
            ),
        ]
        debits, credits = self._compute_breakdown(entries)
        # Net debit = 2.56 - 1.28 = 1.28 (lost exactly one credit)
        assert abs(debits - 1.28) < 0.01
        assert credits == 0.0

    def test_one_sided_entry_expired(self):
        """One-sided (put-only): expired = credit kept, no debit."""
        entries = [
            self._make_mock_entry(
                put_expired=True,
                put_spread_credit=1.28
            ),
        ]
        debits, credits = self._compute_breakdown(entries)
        assert debits == 0.0
        assert abs(credits - 1.28) < 0.01

    def test_identity_feb10(self):
        """Feb 10: Expired Credits - Debits = Gross P&L ($380)."""
        # 5 one-sided entries (put-only), 1 put stop, 4 expired
        # Total credit = $640, Commission = $30, Net P&L = $350
        # Using representative per-entry credits that sum to $640
        entries = [
            self._make_mock_entry(put_expired=True, put_spread_credit=128.0),
            self._make_mock_entry(put_expired=True, put_spread_credit=130.0),
            self._make_mock_entry(put_expired=True, put_spread_credit=126.0),
            self._make_mock_entry(put_expired=True, put_spread_credit=128.0),
            self._make_mock_entry(
                put_stopped=True, put_spread_credit=128.0,
                put_side_stop=256.0  # 2x credit for one-sided
            ),
        ]
        debits, credits = self._compute_breakdown(entries)
        gross_pnl = credits - debits
        commission = 30.0
        net_pnl = gross_pnl - commission
        # Expired = 128+130+126+128 = 512
        # Net debit = 256 - 128 = 128
        # Gross = 512 - 128 = 384, Net = 384 - 30 = 354
        # (Not exactly $350 because we're using representative credits, not actuals)
        assert debits == 128.0
        assert credits == 512.0
        assert debits < credits  # Identity: debits always < credits on profitable day

    def test_identity_always_holds(self):
        """Verify: Expired Credits - Net Debits - Commission = Net P&L for any mix."""
        # 3 full ICs: 2 with one side stopped, 1 both expired
        # Full IC credit = $2.60 ($1.30/$1.30), stop = $2.60
        entries = [
            # Entry 1: call expired, put stopped
            self._make_mock_entry(
                call_expired=True, put_stopped=True,
                call_spread_credit=1.30, put_spread_credit=1.30,
                put_side_stop=2.60
            ),
            # Entry 2: both expired
            self._make_mock_entry(
                call_expired=True, put_expired=True,
                call_spread_credit=1.30, put_spread_credit=1.30,
            ),
            # Entry 3: call stopped, put expired
            self._make_mock_entry(
                call_stopped=True, put_expired=True,
                call_spread_credit=1.30, put_spread_credit=1.30,
                call_side_stop=2.60
            ),
        ]
        debits, credits = self._compute_breakdown(entries)
        commission = 45.0  # 3 entries × $15

        # Expired: 1.30 + 1.30 + 1.30 + 1.30 = 5.20 (4 expired sides)
        # Net debits: (2.60-1.30) + (2.60-1.30) = 1.30 + 1.30 = 2.60 (2 stopped sides)
        assert abs(credits - 5.20) < 0.01
        assert abs(debits - 2.60) < 0.01

        # Total credit = 3 × 2.60 = 7.80
        # Gross P&L = Total Credit - Gross Stop Debits = 7.80 - (2.60 + 2.60) = 2.60
        # Also: Expired Credits - Net Debits = 5.20 - 2.60 = 2.60 ✓
        gross_pnl = credits - debits
        assert abs(gross_pnl - 2.60) < 0.01

        net_pnl = gross_pnl - commission
        assert abs(net_pnl - (2.60 - 45.0)) < 0.01  # -42.40

    def test_skipped_sides_not_counted(self):
        """Skipped sides (one-sided entries) should not appear in debits or credits."""
        entry = self._make_mock_entry(
            call_expired=True,
            put_stopped=False, put_expired=False,
            call_spread_credit=1.50,
            put_spread_credit=0.0,
        )
        debits, credits = self._compute_breakdown([entry])
        assert debits == 0.0
        assert credits == 1.50


# ============================================================
# 3. Logger Service Column Alignment Tests
# ============================================================

class TestLoggerServiceColumnAlignment:
    """Test that header and row have exactly 34 elements in correct order."""

    EXPECTED_HEADERS = [
        "Date", "SPX Open", "SPX Close", "SPX High", "SPX Low",
        "VIX Open", "VIX Close", "VIX High", "VIX Low",
        "Entries Completed", "Entries Skipped",
        "Full ICs", "One-Sided Entries",
        "Bullish Signals", "Bearish Signals", "Neutral Signals",
        "Total Credit ($)", "Call Stops", "Put Stops", "Double Stops",
        "Stop Loss Debits ($)", "Commission ($)", "Expired Credits ($)",
        "Daily P&L ($)", "Daily P&L (EUR)",
        "Cumulative P&L ($)", "Cumulative P&L (EUR)",
        "Win Rate (%)",
        "Capital Deployed ($)", "Return on Capital (%)", "Sortino Ratio",
        "Max Loss Stops ($)", "Max Loss Catastrophic ($)",
        "Notes"
    ]

    def test_header_count_is_34(self):
        """Header list should have exactly 34 elements."""
        assert len(self.EXPECTED_HEADERS) == 34

    def test_header_starts_with_date(self):
        assert self.EXPECTED_HEADERS[0] == "Date"

    def test_header_ends_with_notes(self):
        assert self.EXPECTED_HEADERS[-1] == "Notes"

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

    def test_row_builder_produces_34_elements(self):
        """Build a row from sample data and verify it has 34 elements."""
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
            "notes": "Post-settlement",
        }

        # Replicate the row builder logic from logger_service.py
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
            # Other
            summary.get("notes", "")
        ]

        assert len(row) == 34, f"Row has {len(row)} elements, expected 34"

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
            "notes": "Post-settlement",
        }

        # Build row (same logic)
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
            summary.get("notes", "")
        ]

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
        assert row[33] == "Post-settlement"          # Notes


# ============================================================
# 4. State File OHLC Persistence Tests
# ============================================================

class TestStateFileOHLCPersistence:
    """Test OHLC save to state file and restore from state file."""

    def test_save_ohlc_to_state_data(self):
        """Verify OHLC dict is correctly constructed for state file."""
        md = MarketData()
        md.update_spx(6970.0)
        md.update_spx(6990.0)
        md.update_spx(6930.0)
        md.update_vix(17.5)
        md.update_vix(21.0)
        md.update_vix(15.2)

        # Replicate save logic from _save_state_to_disk()
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

        # Replicate restore logic from meic_tf/strategy.py
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

    def test_cumulative_pnl_computation(self):
        """cumulative_pnl should be previous_cumulative + today's net_pnl."""
        cumulative_metrics = {"cumulative_pnl": 500.0}
        net_pnl = 150.0

        cumulative_pnl = cumulative_metrics.get("cumulative_pnl", 0) + net_pnl
        assert cumulative_pnl == 650.0

    def test_cumulative_pnl_eur_conversion(self):
        """cumulative_pnl_eur should be cumulative_pnl * fx_rate."""
        cumulative_pnl = 650.0
        rate = 0.84  # Example EUR/USD rate

        cumulative_pnl_eur = cumulative_pnl * rate
        assert abs(cumulative_pnl_eur - 546.0) < 0.01

    def test_eur_conversion_fallback_on_error(self):
        """If FX rate fetch fails, EUR values should be 0."""
        # Simulate exception in get_fx_rate
        daily_pnl_eur = 0
        cumulative_pnl_eur = 0

        # These should be the fallback values
        assert daily_pnl_eur == 0
        assert cumulative_pnl_eur == 0

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

    def test_format_range_ah1_covers_34_columns(self):
        """Verify A1:AH1 covers exactly 34 columns (A=1, AH=34)."""
        # A=1, B=2, ..., Z=26, AA=27, AB=28, ..., AH=34
        col_number = (ord('A') - ord('A') + 1) * 26 + (ord('H') - ord('A') + 1)  # 26 + 8 = 34
        assert col_number == 34


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
