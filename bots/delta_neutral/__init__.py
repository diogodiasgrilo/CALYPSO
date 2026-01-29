"""
Delta Neutral Strategy Bot (Brian Terry's Strategy from Theta Profits)

Version: 2.0.2
Last Updated: 2026-01-29

SPY Long Straddle + Weekly Short Strangles with 5-Point Recentering

Strategy Overview:
1. Buy ATM Long Straddle (target 120 DTE) when VIX < 18
2. Sell weekly Short Strangles targeting 1.5% NET return on long straddle cost
   - Scans from 2.0x down to 1.33x expected move looking for target return
   - Uses 1.33x as safety floor (ensures roll trigger lands at 1.0x expected move)
   - Safety extension: If floor gives zero/negative, extends scan to 1.0x
3. If SPY moves ±5 points from initial strike, recenter long straddle
4. Roll weekly shorts on Friday to next week's expiry
5. Exit entire position when long straddle reaches 60 DTE

Key Logic:
- Proactive Restart Check (2026-01-23): Before opening/rolling shorts, check if
  new shorts would outlive the longs hitting 60 DTE. If so, close everything NOW
  and start fresh with new 120 DTE longs + new shorts.
- Safety Extension (2026-01-29): If 1.33x floor strikes give zero/negative return,
  scan extends from 1.33x down to 1.0x looking for first positive return.

Configuration (see config/config.json for full list):
- weekly_target_return_percent: 1.5 (target 1.5% NET weekly return on straddle cost)
- short_strangle_multiplier_min: 1.33 (safety floor - roll trigger at 1.0x when breached)
- short_strangle_multiplier_max: 2.0 (scan starting point - widest/safest strikes)
- exit_dte_max: 60 (close entire position when longs reach this DTE)
- recenter_threshold_points: 5.0 (recenter when SPY moves ±$5)

Strike Selection Priority:
1. Target return (1.5%) at multiplier >= 1.33x (optimal - widest safe strikes)
2. Positive return at 1.33x floor (safe - ensures roll trigger at 1.0x EM)
3. Positive return at 1.0x-1.33x range via safety extension (less safe but profitable)
4. Skip entry if no positive return found even at 1.0x

Version History:
- 2.0.2 (2026-01-29): Safety extension for low-return scenarios
  - If 1.33x floor gives zero/negative return, extends scan to 1.0x
  - Target return changed from 1.0% to 1.5% (optimal EV based on IV>RV analysis)
  - Ensures bot always finds positive return opportunity or skips entry
- 2.0.1 (2026-01-28): REST-only mode for reliability
  - Disabled WebSocket streaming (USE_WEBSOCKET_STREAMING=False)
  - All price fetching now uses REST API directly
  - VIGILANT mode changed from 1s to 2s (30 calls/min, under rate limits)
  - WebSocket code preserved for future use if 4+ bots need rate limit relief
- 2.0.0 (2026-01-28): Adaptive roll trigger + WebSocket fixes
  - Adaptive cushion-based roll trigger (75% consumed = roll, 60% = vigilant)
  - Immediate next-week shorts entry after scheduled debit skip
  - entry_underlying_price tracking on StranglePosition for cushion calculation
  - Cushion % consumed visible in terminal status, heartbeat, and Google Sheets
  - 10 critical fixes for WebSocket price streaming (kept but disabled)
- 1.0.0 (2026-01-23): Initial production release
  - Proactive restart check logic
  - 1% weekly target return mode
  - Config key standardization
"""

__version__ = "2.0.2"

from bots.delta_neutral.strategy import DeltaNeutralStrategy, StrategyState

__all__ = ['DeltaNeutralStrategy', 'StrategyState', '__version__']
