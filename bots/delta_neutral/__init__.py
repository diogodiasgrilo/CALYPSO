"""
Delta Neutral Strategy Bot (Brian Terry's Strategy from Theta Profits)

Version: 2.0.0
Last Updated: 2026-01-28

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
- weekly_strangle_multiplier_max: 2.0 (max multiplier for strike distance)
- recenter_threshold_points: 5.0 (recenter when SPY moves ±$5)

Version History:
- 2.0.0 (2026-01-28): Major WebSocket reliability fixes
  - 10 critical fixes for WebSocket price streaming
  - Cache invalidation on disconnect
  - Timestamp-based staleness detection (60s max)
  - Thread-safe cache access with locking
  - Binary message parser bounds checking
  - Limit order $0 price validation fix
  - WebSocket health monitoring
  - Heartbeat timeout detection
- 1.0.0 (2026-01-23): Initial production release
  - Proactive restart check logic
  - 1% weekly target return mode
  - Config key standardization
"""

__version__ = "2.0.0"

from bots.delta_neutral.strategy import DeltaNeutralStrategy, StrategyState

__all__ = ['DeltaNeutralStrategy', 'StrategyState', '__version__']
