"""
Shared infrastructure modules for CALYPSO trading bots.

This package contains common utilities used by all trading strategies:
- saxo_client: Saxo Bank API client for trading operations
  (includes AssetType enum: STOCK, STOCK_OPTION, STOCK_INDEX_OPTION, CFD_ON_STOCK, CFD_ON_INDEX, ETF, FUTURES)
- logger_service: Google Sheets and local file logging
- config_loader: Smart config loading (cloud vs local)
- market_hours: US market hours utilities (get_us_market_time, US_EASTERN, etc.)
- secret_manager: GCP Secret Manager interface
- external_price_feed: Yahoo Finance fallback for prices
- token_coordinator: OAuth token refresh coordination across bots (used by Token Keeper service)
- event_calendar: FOMC/economic calendar for trading blackouts
- technical_indicators: Technical analysis calculations
- alert_service: SMS/Email alerting via Google Cloud Pub/Sub
- position_registry: Multi-bot position ownership tracking (for same underlying)

Last Updated: 2026-01-29 (Delta Neutral v2.0.2 - safety extension, 1.5% target, 1.33x floor)

ALERT SYSTEM (2026-01-26)
================================================================================
Architecture: Bot -> AlertService -> Pub/Sub -> Cloud Function -> Twilio/Gmail

Key design: Alerts are sent AFTER actions complete with ACTUAL results.
The bot publishes to Pub/Sub (~50ms non-blocking) and continues immediately.
Cloud Function delivers SMS/email asynchronously in the background.

Timezone: All timestamps use US Eastern Time (ET) - the exchange timezone.
          Handles EST ↔ EDT transitions automatically via pytz.

Alert Priorities (ALL levels get WhatsApp + Email):
    CRITICAL: WhatsApp + Email (circuit breaker, emergency exit, naked positions, ITM risk close)
    HIGH: WhatsApp + Email (stop loss, max loss, position issues, vigilant mode entry)
    MEDIUM: WhatsApp + Email (position opened, profit target, rolls, recenters)
    LOW: WhatsApp + Email (informational, startup/shutdown, vigilant mode exit)

Alert Responsibilities by Bot:
    Iron Fly:           AlertService only (no market monitor, no gap alerts - 0DTE only)
    Delta Neutral:      AlertService + MarketStatusMonitor + SPY gap alerts + ITM monitoring alerts
    Rolling Put Diag:   AlertService + QQQ gap alerts

Delta Neutral ITM Monitoring Alerts (Updated 2026-01-28):
    - VIGILANT_ENTERED (HIGH): 60-75% of original cushion consumed (adaptive threshold)
    - VIGILANT_EXITED (LOW): Cushion consumption drops below 60% (back to safe zone)
    - ITM_RISK_CLOSE (CRITICAL): Shorts emergency closed at 0.1% from strike (absolute safety floor)
    - ROLL_COMPLETED (MEDIUM): Weekly shorts rolled (scheduled, challenged at 75% cushion, or emergency)
    - RECENTER (MEDIUM): Long straddle recentered to new ATM strike

MarketStatusMonitor (runs ONLY on Delta Neutral to avoid duplicates):
    - Market opening countdown (1h, 30m, 15m before open)
    - Market open notification (at 9:30 AM ET)
    - Market close notification (at 4:00 PM ET or early close)
    - Holiday notifications (weekday market closures)

Pre-Market Gap Alerts (WARNING 2-3%, CRITICAL 3%+):
    - Delta Neutral: SPY gaps (once per day max)
    - Rolling Put Diagonal: QQQ gaps (once per day max)

Usage:
    from shared import AlertService, AlertType, AlertPriority, MarketStatusMonitor

    alert_service = AlertService(config, "IRON_FLY")
    alert_service.circuit_breaker("5 consecutive failures", 5)
    alert_service.position_opened("Iron Fly @ 6020", -245.50)
    alert_service.premarket_gap("SPY", -2.5, 600.00, 585.00, "Check positions")

    # Market status monitor (use only on ONE bot to avoid duplicates)
    monitor = MarketStatusMonitor(alert_service)
    monitor.check_and_alert()  # Call periodically from main loop

See: docs/ALERTING_SETUP.md for full deployment guide
================================================================================

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

6. EXTENDED HOURS PRICE FETCHING (2026-01-26)
   -------------------------------------------
   Saxo extended hours: 7:00 AM - 5:00 PM ET on trading days.
   NEVER try to fetch prices before 7:00 AM ET - Saxo has no data.

   | Session      | Time (ET)         | Notes              |
   |--------------|-------------------|--------------------|
   | Pre-Market   | 7:00 AM - 9:30 AM | Limit orders only  |
   | Regular      | 9:30 AM - 4:00 PM | Full trading       |
   | After-Hours  | 4:00 PM - 5:00 PM | Limit orders only  |

   Use is_saxo_price_available() before fetching:
   if is_saxo_price_available():  # True only 7:00 AM - 5:00 PM ET
       quote = client.get_quote(uic)

   See: docs/SAXO_API_PATTERNS.md Section 10

7. WEBSOCKET BINARY PARSING (2026-01-26)
   --------------------------------------
   Saxo WebSocket sends BINARY frames, NOT plain JSON text!
   Previous code tried message.decode('utf-8') which silently failed,
   causing stale cached prices and unnecessary REST API fallbacks.

   Binary frame format (per Saxo documentation):
   | 8 bytes | 2 bytes  | 1 byte    | N bytes | 1 byte  | 4 bytes | N bytes |
   | Msg ID  | Reserved | RefID Len | RefID   | Format  | Size    | Payload |

   WRONG: json.loads(message.decode('utf-8'))  # Fails silently on binary!
   RIGHT: struct.unpack() to parse binary, then json.loads() on payload

   The fix is in saxo_client.py _decode_binary_ws_message() method.
   This enables proper WebSocket caching for get_quote(), get_spy_price(),
   get_vix_price() - eliminating rate limit concerns for 1-second monitoring.

   See: docs/SAXO_API_PATTERNS.md Section 5

8. WEBSOCKET RELIABILITY (2026-01-28)
   ------------------------------------
   10 critical fixes for production WebSocket streaming (CONN-007 to CONN-016):

   Fix #1 (CONN-007): Clear cache on disconnect
   - _clear_cache() called in all disconnect paths
   - Prevents using stale data after reconnection

   Fix #2 (CONN-008): Timestamp-based staleness detection
   - Cache format: {'timestamp': datetime, 'data': quote_data}
   - Rejects data older than 60 seconds, forces REST fallback

   Fix #3 (CONN-014): Limit order $0 price bug
   - Changed: `if limit_price:` to `if limit_price is None or limit_price <= 0:`
   - Python truthiness treats 0.0 as False

   Fix #4 (CONN-015): Never use $0.00 fallback price
   - If quote invalid AND leg_price is $0, skip to retry
   - Prevents placing orders at $0.00

   Fix #5 (CONN-009): WebSocket health monitoring
   - is_websocket_healthy() checks thread alive, last message < 60s
   - get_quote() forces REST fallback if unhealthy

   Fix #6 (CONN-010): Heartbeat timeout detection
   - Track _last_heartbeat_time (Saxo sends every ~15s)
   - Connection is zombie if no heartbeat in 60+ seconds

   Fix #8 (CONN-012): Thread-safe cache locking
   - _price_cache_lock mutex protects all cache operations
   - Prevents race conditions between threads

   Fix #10 (CONN-011): Binary parser bounds checking
   - Validates message length at each parsing step
   - Returns None on parse error instead of crashing

   See: docs/DELTA_NEUTRAL_EDGE_CASES.md for all 10 fixes
================================================================================
"""

from shared.saxo_client import SaxoClient, BuySell, OrderType, AssetType
from shared.logger_service import TradeLoggerService, setup_logging, TradeRecord
from shared.config_loader import ConfigLoader, get_config_loader
from shared.market_hours import (
    is_market_open,
    get_market_status_message,
    calculate_sleep_duration,
    is_pre_market,
    is_after_hours,
    is_extended_hours,
    is_saxo_price_available,
    get_trading_session,
    is_early_close_day,
    get_early_close_reason,
    get_market_close_time,
    is_market_holiday,
    get_holiday_name,
    get_us_market_time,
    US_EASTERN,
)
from shared.secret_manager import is_running_on_gcp
from shared.external_price_feed import ExternalPriceFeed
from shared.alert_service import AlertService, AlertType, AlertPriority
from shared.market_status_monitor import MarketStatusMonitor
from shared.event_calendar import (
    get_fomc_dates,
    get_fomc_announcement_dates,
    is_fomc_meeting_day,
    is_fomc_announcement_day,
    get_next_fomc_date,
    is_fomc_approaching,
    FOMC_DATES_2026,
)
from shared.position_registry import PositionRegistry
from shared.token_coordinator import TokenCoordinator, get_token_coordinator

__all__ = [
    # Saxo Client
    'SaxoClient', 'BuySell', 'OrderType', 'AssetType',
    # Logging
    'TradeLoggerService', 'setup_logging', 'TradeRecord',
    # Config
    'ConfigLoader', 'get_config_loader',
    # Market Hours (Regular)
    'is_market_open', 'get_market_status_message', 'calculate_sleep_duration',
    # Market Hours (Extended - Saxo: 7:00 AM - 5:00 PM ET)
    'is_pre_market', 'is_after_hours', 'is_extended_hours',
    'is_saxo_price_available', 'get_trading_session',
    # Market Hours (Early Close / Holidays)
    'is_early_close_day', 'get_early_close_reason', 'get_market_close_time',
    'is_market_holiday', 'get_holiday_name',
    # Market Hours (Timezone)
    'get_us_market_time', 'US_EASTERN',
    # Cloud
    'is_running_on_gcp',
    # Price Feed
    'ExternalPriceFeed',
    # Alerts
    'AlertService', 'AlertType', 'AlertPriority',
    # Market Status Monitor (for countdown/open/close/holiday alerts)
    'MarketStatusMonitor',
    # Event Calendar (FOMC dates - single source of truth)
    'get_fomc_dates', 'get_fomc_announcement_dates', 'is_fomc_meeting_day',
    'is_fomc_announcement_day', 'get_next_fomc_date', 'is_fomc_approaching', 'FOMC_DATES_2026',
    # Position Registry (for multi-bot same-underlying support)
    'PositionRegistry',
    # Token Coordinator (for multi-bot token sharing, used by Token Keeper service)
    'TokenCoordinator', 'get_token_coordinator',
]
