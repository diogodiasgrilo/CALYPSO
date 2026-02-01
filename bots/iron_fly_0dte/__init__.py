"""
0DTE Iron Fly Strategy Bot (Doc Severson's Strategy)

SPX 0DTE Iron Butterfly with Opening Range and VIX filters.

Strategy Overview:
1. Wait for 10:00 AM EST (after 30-min opening range)
2. VIX filter: abort if VIX > 20 or spiking > 5%
3. Opening range filter: price must be within 9:30-10:00 high/low
4. Sell ATM straddle, buy wings at expected move distance
5. Take profit: Dynamic (30% of credit + commission) with $25 minimum floor
6. Stop loss: when SPX touches either wing strike (market order)
7. Max hold time: 60 minutes (11:00 AM rule)

P&L Tracking (Updated 2026-02-01):
- Uses actual fill prices from Saxo activities endpoint (not quoted bid/ask)
- Tracks commission: $5 per leg round-trip ($20 total per trade)
- Shows both Gross P&L and Net P&L (after commission) in logs/alerts
- Profit target factors in commission to ensure actual net profit

Safety Features:
- Circuit breaker with sliding window failure detection
- Critical intervention flag for unrecoverable errors
- Partial fill auto-unwind with retry logic
- Stop loss retry escalation (5 retries per leg)
- Daily circuit breaker escalation (halt after 3 opens)
- Flash crash velocity detection (2% in 5 minutes)
- Market halt detection from error messages
- Position reconciliation with broker on startup
- Pending order check and auto-cancel on startup
- Position metadata persistence for crash recovery
- Multiple iron fly detection and auto-selection

Edge Cases: 63 analyzed, 59 LOW / 3 MEDIUM / 1 HIGH by design (see docs/IRON_FLY_EDGE_CASES.md)
Last Updated: 2026-02-01 (Commission tracking, fill price retry, activities sync delay handling)
"""

from bots.iron_fly_0dte.strategy import IronFlyStrategy, IronFlyState

__all__ = ['IronFlyStrategy', 'IronFlyState']
