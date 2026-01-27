"""
MEIC Models Package.

This package contains data models for the MEIC trading strategy:
- MEICState: Strategy state machine
- IronCondorEntry: Single iron condor position
- MEICDailyState: Daily trading state
- MarketData: Market data tracking

These models are standalone definitions that can be imported directly
without importing the entire strategy module. This is useful for:
- Testing
- Avoiding circular imports
- Type hints in other modules

For production use, strategy.py contains the canonical definitions.
Both are kept in sync.
"""

from .states import MEICState
from .positions import IronCondorEntry, MEICDailyState
from .market_data import MarketData

__all__ = [
    "MEICState",
    "IronCondorEntry",
    "MEICDailyState",
    "MarketData",
]
