"""
0DTE Iron Fly Strategy Bot (Doc Severson's Strategy)

SPX 0DTE Iron Butterfly with Opening Range and VIX filters

Strategy Overview:
1. Wait for 10:00 AM EST (after 30-min opening range)
2. VIX filter: abort if VIX > 20 or spiking > 5%
3. Opening range filter: price must be within 9:30-10:00 high/low
4. Sell ATM straddle, buy wings at expected move distance
5. Take profit: $50-$100 per contract (limit order)
6. Stop loss: when SPX touches either wing strike (market order)
7. Max hold time: 18 minutes to 1 hour
"""

from bots.iron_fly_0dte.strategy import IronFlyStrategy, IronFlyState

__all__ = ['IronFlyStrategy', 'IronFlyState']
