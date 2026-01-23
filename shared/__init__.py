"""
Shared infrastructure modules for CALYPSO trading bots.

This package contains common utilities used by all trading strategies:
- saxo_client: Saxo Bank API client for trading operations
- logger_service: Google Sheets and local file logging
- config_loader: Smart config loading (cloud vs local)
- market_hours: US market hours utilities
- secret_manager: GCP Secret Manager interface
- external_price_feed: Yahoo Finance fallback for prices
- token_coordinator: OAuth token refresh coordination across bots
- event_calendar: FOMC/economic calendar for trading blackouts
- technical_indicators: Technical analysis calculations

================================================================================
CRITICAL IMPLEMENTATION NOTES - READ BEFORE MODIFYING SAXO API CODE
================================================================================
Full documentation: docs/SAXO_API_PATTERNS.md

1. ORDER FILL PRICES (2026-01-23)
   ------------------------------
   NEVER use quoted bid/ask prices for P&L calculation!
   Market orders fill at actual prices which may differ from quotes.

   WRONG: credit = quoted_bid - quoted_ask
   RIGHT: credit = fill_details.get("FilledPrice") - ...

   Fill price fields (check in order):
   - fill_details.get("fill_price")    # Normalized field
   - fill_details.get("FilledPrice")   # From /activities/ endpoint
   - fill_details.get("Price")         # From order details

   See: docs/SAXO_API_PATTERNS.md Section 2

2. "UNKNOWN" ORDER STATUS (2026-01-23)
   ------------------------------------
   Market orders fill instantly and DISAPPEAR from /orders/ endpoint.
   Status "Unknown" usually means FILLED, not an error.

   WRONG: Keep polling get_order_status() waiting for "Filled"
   RIGHT: Check check_order_filled_by_activity() immediately

   See: docs/SAXO_API_PATTERNS.md Section 6

3. VIX DATA FETCHING (2026-01-23)
   -------------------------------
   VIX is a stock INDEX, not a tradable instrument.
   It has NO bid/ask prices - only LastTraded in PriceInfoDetails.

   WebSocket FieldGroups MUST include "PriceInfoDetails":
   ["DisplayAndFormat", "Quote", "PriceInfo", "PriceInfoDetails"]
                                              ^^^^^^^^^^^^^^^^
                                              Required for VIX!

   See: saxo_client.py start_price_streaming() around line 2967

4. ASSET TYPE MAPPING (2026-01-23)
   --------------------------------
   Wrong asset type = 404 errors or missing data.

   | Instrument    | AssetType          | Notes                    |
   |---------------|--------------------|--------------------------|
   | SPXW options  | StockIndexOption   | NOT StockOption!         |
   | SPY options   | StockOption        | Regular stock options    |
   | VIX.I spot    | StockIndex         | For VIX level monitoring |
   | US500.I (CFD) | CfdOnIndex         | For SPX price tracking   |

   See: docs/SAXO_API_PATTERNS.md Section 4

5. WEBSOCKET TOKEN REFRESH (2026-01-23)
   -------------------------------------
   ALWAYS call authenticate() BEFORE starting WebSocket connection.
   If another bot refreshed the shared token while sleeping, your
   in-memory token is stale and will cause 401 Unauthorized.

   See: saxo_client.py start_price_streaming() around line 2927
================================================================================
"""

from shared.saxo_client import SaxoClient, BuySell, OrderType, AssetType
from shared.logger_service import TradeLoggerService, setup_logging, TradeRecord
from shared.config_loader import ConfigLoader, get_config_loader
from shared.market_hours import (
    is_market_open,
    get_market_status_message,
    calculate_sleep_duration,
)
from shared.secret_manager import is_running_on_gcp
from shared.external_price_feed import ExternalPriceFeed

__all__ = [
    # Saxo Client
    'SaxoClient', 'BuySell', 'OrderType', 'AssetType',
    # Logging
    'TradeLoggerService', 'setup_logging', 'TradeRecord',
    # Config
    'ConfigLoader', 'get_config_loader',
    # Market Hours
    'is_market_open', 'get_market_status_message', 'calculate_sleep_duration',
    # Cloud
    'is_running_on_gcp',
    # Price Feed
    'ExternalPriceFeed',
]
