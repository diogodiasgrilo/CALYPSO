"""
Delta Neutral Strategy Bot (Brian Terry's Strategy from Theta Profits)

SPY Long Straddle + Weekly Short Strangles with 5-Point Recentering

Strategy Overview:
1. Buy ATM Long Straddle (target 120 DTE) when VIX < 18
2. Sell weekly Short Strangles targeting 1% NET return on long straddle cost
   - Uses expected move from ATM straddle pricing
   - Caps strikes at 1.5x expected move for safety
   - Prefers widest (safest) strikes that meet return target
3. If SPY moves ±5 points from initial strike, recenter long straddle
4. Roll weekly shorts on Friday to next week's expiry
5. Exit entire position when long straddle reaches 60 DTE

Key Logic (2026-01-23):
- Proactive Restart Check: Before opening/rolling shorts, check if new shorts
  would outlive the longs hitting 60 DTE. If so, close everything NOW and
  start fresh with new 120 DTE longs + new shorts. This prevents wasting
  theta on shorts that would be abandoned at the 60 DTE exit.

Configuration:
- weekly_target_return_percent: 1.0 (target 1% NET weekly return)
- exit_dte_max: 60 (close everything when longs reach this DTE)
- short_strangle_max_multiplier: 1.5 (cap for strike distance)
- recenter_threshold_points: 5.0 (recenter when SPY moves ±$5)

Updated: 2026-01-25
"""

from bots.delta_neutral.strategy import DeltaNeutralStrategy, StrategyState

__all__ = ['DeltaNeutralStrategy', 'StrategyState']
