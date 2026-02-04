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
- Entry times: 10:00, 10:30, 11:00, 11:30, 12:00, 12:30 AM ET
- Strike selection: VIX-adjusted for ~8 delta, 50-point spreads
- Credit target: $1.00 - $1.75 per side (validated at runtime)
- Stop loss: Total credit per side (MEIC+ subtracts configurable amount)

Version History:
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
