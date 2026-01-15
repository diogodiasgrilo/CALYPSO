"""
Delta Neutral Strategy Bot (Brian's Strategy)

SPY Long Straddle + Weekly Short Strangles with 5-Point Recentering

Strategy Overview:
1. Buy ATM Long Straddle (90-120 DTE) when VIX < 18
2. Sell weekly Short Strangles at 1.5-2x expected move
3. If SPY moves 5 points from initial strike, recenter
4. Roll weekly shorts on Friday
5. Exit entire trade when 30-60 DTE remains on Longs
"""

from bots.delta_neutral.strategy import DeltaNeutralStrategy, StrategyState

__all__ = ['DeltaNeutralStrategy', 'StrategyState']
