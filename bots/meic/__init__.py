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
- Strike selection: 5-15 delta, 50-60 point spreads
- Credit target: $1.00 - $1.75 per side
- Stop loss: Total credit per side (MEIC+ subtracts $0.10)

See docs/MEIC_STRATEGY_SPECIFICATION.md for full details.
See docs/MEIC_EDGE_CASES.md for edge case analysis.

Last Updated: 2026-01-27 (Initial implementation)
"""

from bots.meic.strategy import MEICStrategy, MEICState

__all__ = ['MEICStrategy', 'MEICState']
