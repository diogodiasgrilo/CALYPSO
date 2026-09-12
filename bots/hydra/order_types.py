"""Order enums for the standalone HYDRA bot.

HYDRA-owned copies of the order-direction / order-type enums it needs,
so `bots/hydra/` has no dependency on `shared/saxo_client.py`. The enum
*values* are kept stable strings — call sites compare members
(`BuySell.BUY`), never the raw value, and the broker adapter
(`IBClient`) translates members to its own wire format.
"""
from __future__ import annotations

from enum import Enum


class BuySell(Enum):
    """Order direction."""
    BUY = "Buy"
    SELL = "Sell"


class OrderType(Enum):
    """Order type."""
    MARKET = "Market"
    LIMIT = "Limit"
