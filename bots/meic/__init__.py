"""
MEIC (Multiple Entry Iron Condors) Trading Bot

Implements Tammy Chambless's "Queen of 0DTE" MEIC strategy:
- 6 scheduled iron condor entries throughout the trading day
- Per-side stop losses equal to total credit received (breakeven design)
- MEIC+ modification: stop = credit - $0.10 for small wins on stop days

Key Performance Metrics (Tammy Chambless, Jan 2023 - present):
- 20.7% CAGR
- 4.31% max drawdown
- 4.8 Calmar ratio
- ~70% win rate

Strategy Details:
- Entry times: 10:05, 10:35, 11:05, 11:35, 12:05, 12:35 AM ET
- Strike selection: VIX-adjusted for ~8 delta, 50-point spreads
- Credit target: $1.00 - $1.75 per side (validated at runtime)
- Stop loss: Total credit per side (MEIC+ subtracts configurable amount)

Version History:
- 1.3.3 (2026-04-09): Fix #86 — Clear position IDs and UICs on entry object after stop loss to prevent false POS-003 "Position Mismatch Detected" alerts. Registry was already cleaned by _close_position_with_retry but entry object held stale IDs.
- 1.3.2 (2026-03-09): Fix #83a: Skip closing worthless long legs (bid=$0) during stop loss — deep OTM longs expire worthless on 0DTE, no point trying market orders. Accurate commission tracking (count only legs actually closed). Fix #83d: Removed narrow is_limit_only_period time check. Strike-not-found log level ERROR→WARNING.
- 1.3.1 (2026-03-07): Actual stop debit tracking — actual_call_stop_debit/actual_put_stop_debit fields record real market order cost for per-entry P&L accuracy. Serialization and all restoration paths updated.
- 1.3.0 (2026-02-19): Batch quote API for stop loss monitoring (7x rate limit reduction), Fix #80 Sheets resize
- 1.2.9 (2026-02-18): Fix #77 post-restart settlement, Fix #78 stop loss debits accuracy
- 1.2.8 (2026-02-13): Fix #75 - Async deferred stop fill lookup (non-blocking P&L correction)
- 1.2.7 (2026-02-13): Fix #74 - Stop loss fill price accuracy (deferred lookup was bypassed by quote fallback)
- 1.2.6 (2026-02-11): Fix #63 - EUR conversion in Trades tab (pass saxo_client to log_trade)
- 1.2.5 (2026-02-11): MKT-014 liquidity re-check + Fix #52-#61 (multi-contract, position merge detection, win rate helpers)
- 1.2.4 (2026-02-09): MKT-012 strike conflict prevention - adjusts long strikes that conflict with existing shorts
- 1.2.3 (2026-02-08): MKT-011 credit gate - estimates credit before entry, skips non-viable entries
- 1.2.2 (2026-02-04): Commission tracking - shows gross/net P&L in logs, alerts, daily summary
- 1.2.1 (2026-02-04): Zero credit safety (STOP-007), P&L double-counting fix, daily summary logging fix
- 1.2.0 (2026-02-02): VIX-adjusted strike selection, credit validation, code audit fixes
- 1.1.0 (2026-02-01): REST-only mode, enhanced safety features
- 1.0.0 (2026-01-27): Initial implementation

See docs/MEIC_STRATEGY_SPECIFICATION.md for full details.
See docs/MEIC_EDGE_CASES.md for edge case analysis.
"""

from bots.meic.strategy import (
    MEICStrategy,
    MEICState,
    IronCondorEntry,
    MEICDailyState,
    MarketData,
)

__all__ = [
    'MEICStrategy',
    'MEICState',
    'IronCondorEntry',
    'MEICDailyState',
    'MarketData',
]
