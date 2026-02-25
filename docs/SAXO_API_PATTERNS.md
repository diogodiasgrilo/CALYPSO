# Saxo Bank OpenAPI - Critical Patterns and Gotchas

**Last Updated:** 2026-01-31
**Purpose:** Document proven patterns for Saxo API integration to avoid repeating mistakes.

---

## Table of Contents

1. [Order Placement and Verification](#1-order-placement-and-verification)
2. [Extracting Fill Prices](#2-extracting-fill-prices)
3. [Price Data Extraction](#3-price-data-extraction)
4. [Asset Type Mapping](#4-asset-type-mapping)
5. [WebSocket Streaming](#5-websocket-streaming)
6. [Order Status Handling](#6-order-status-handling)
7. [Position Detection](#7-position-detection)
8. [Common Mistakes](#8-common-mistakes)
9. [Chart API for Historical OHLC Data](#9-chart-api-for-historical-ohlc-data)
10. [Extended Hours Trading](#10-extended-hours-trading-pre-market--after-hours)
11. [WebSocket Reliability Fixes (2026-01-28)](#11-websocket-reliability-fixes-2026-01-28)

---

## 1. Order Placement and Verification

### The Problem
Market orders fill almost instantly but the order ID "disappears" from `/orders/` endpoint.
You CANNOT rely on order status alone - must check activities/positions.

### Proven Pattern

```python
def place_and_verify_order(self, order_params):
    """
    Place order and verify fill. Returns (success, fill_details).

    CRITICAL: Market orders fill instantly and disappear from /orders/.
    Must verify via /activities/ or /positions/ endpoint.
    """
    # Step 1: Place order
    order_id = self.place_order(order_params)
    if not order_id:
        return False, None

    # Step 2: Try to get status (may return "Unknown" for filled market orders)
    status = self.get_order_status(order_id)

    # Step 3: If not clearly filled, check activities
    if status not in ["Filled", "FinalFill"]:
        filled, fill_details = self.check_order_filled_by_activity(order_id, uic)
        if filled:
            return True, fill_details

    # Step 4: Final fallback - check if position exists
    positions = self.get_positions(asset_type=asset_type)
    # ... verify position matches expected

    return success, fill_details
```

### Key Methods in saxo_client.py

| Method | Purpose | When to Use |
|--------|---------|-------------|
| `place_order()` | Submit order to Saxo | Initial order placement |
| `get_order_status()` | Check order state | First check after placement |
| `check_order_filled_by_activity()` | Verify via activity log | When status is Unknown/missing |
| `get_positions()` | Check actual positions | Final verification |
| `_verify_order_fill()` | Combined verification | Recommended wrapper |

---

## 2. Extracting Fill Prices

### The Problem (Fixed 2026-01-23)
P&L was wildly incorrect because we used **quoted bid/ask prices** instead of **actual fill prices**.

**Wrong:** Calculate credit from quoted prices at order time
**Right:** Extract actual fill prices from activity/order response

### Fill Price Extraction Pattern

```python
def get_fill_price(fill_detail: Optional[Dict], fallback: float) -> float:
    """
    Extract fill price from Saxo fill_details response.

    CRITICAL (Fix #76, 2026-02-17): "FilledPrice" does NOT exist in Saxo's API!
    Correct fields: AveragePrice, ExecutionPrice. "Price" is only on LIMIT orders.

    Priority order:
    1. "AveragePrice" - Actual execution price from activities (CORRECT)
    2. "ExecutionPrice" - Alternative field name (CORRECT)
    3. "Price" - Only on LIMIT orders (submitted price, NOT execution)
    4. fallback - Quoted price (last resort)

    For OPEN positions: PositionBase.OpenPrice is authoritative.
    For CLOSED positions: /port/v1/closedpositions → ClosingPrice is authoritative.
    """
    if fill_detail:
        price = (
            fill_detail.get("AveragePrice") or
            fill_detail.get("ExecutionPrice") or
            fill_detail.get("Price")
        )
        if price and price > 0:
            return float(price)
    return fallback
```

### Where Fill Details Come From

**Activities Endpoint Response (`/cs/v1/audit/orderactivities`):**
```json
{
    "OrderId": "123456",
    "FilledAmount": 1.0,
    "AveragePrice": 12.50,       // <-- ACTUAL FILL PRICE (use this!)
    "Status": "FinalFill",
    "ActivityTime": "2026-01-23T15:00:00Z"
}
```

**CRITICAL (Fix #76, 2026-02-17):** The field `"FilledPrice"` does NOT exist in Saxo's API.
Previous code used `activity.get("FilledPrice")` which always returned None, causing 17 days of
fill_price=0 errors. Correct fields are `"AveragePrice"` or `"ExecutionPrice"`.
`"Price"` only exists on LIMIT orders (the submitted limit price, not execution price).
Correct extraction:
```python
fill_price = activity.get("AveragePrice") or activity.get("ExecutionPrice") or activity.get("Price", 0)
fill_amount = activity.get("FilledAmount") or activity.get("Amount", 0)
```

**Activities Endpoint Sync Delay (Fixed 2026-02-02):** The activities endpoint may have a
delay (~3-10 seconds) before fill data appears. Solution: Retry with configurable delay.

**Current implementation (saxo_client.py):**
- 4 retries × 1.5s = 6s total in `check_order_filled_by_activity()`
- Iron Fly adds its own 3-attempt loop on top = ~18s worst case
- Falls back to `PositionBase.OpenPrice` if activities have no price

```python
# check_order_filled_by_activity() - 4 retries × 1.5s = 6s
for attempt in range(1, max_retries + 1):  # max_retries=4, retry_delay=1.5
    filled, fill_details = check_order_filled_by_activity(order_id, uic)
    if filled and fill_details.get("fill_price", 0) > 0:
        return True, fill_details  # Got actual fill price
    time.sleep(retry_delay)  # 1.5s between retries
# Falls back to position lookup for PositionBase.OpenPrice
```

**Order Details Response (`/port/v1/orders/{clientKey}/{orderId}`):**
```json
{
    "OrderId": "123456",
    "Price": 12.50,              // <-- ACTUAL FILL PRICE
    "Status": "Filled",
    "FilledAmount": 1.0
}
```

### P&L Calculation Pattern

```python
# WRONG - Using quoted prices
sc_bid = get_option_quote(short_call_uic)["Bid"]  # What we EXPECTED
sp_bid = get_option_quote(short_put_uic)["Bid"]
credit = (sc_bid + sp_bid - lc_ask - lp_ask) * 100

# RIGHT - Using actual fill prices
actual_sc = get_fill_price(fill_details.get("short_call"), sc_bid)  # What we ACTUALLY GOT
actual_sp = get_fill_price(fill_details.get("short_put"), sp_bid)
actual_lc = get_fill_price(fill_details.get("long_call"), lc_ask)
actual_lp = get_fill_price(fill_details.get("long_put"), lp_ask)
credit = (actual_sc + actual_sp - actual_lc - actual_lp) * 100
```

### Position Fill Price Extraction (CRITICAL FIX 2026-02-02)

When the activities endpoint returns `FilledPrice=0` (sync delay), fall back to position lookup.

**CRITICAL:** Use `PositionBase.OpenPrice`, NOT `PositionView.AverageOpenPrice`!

| Field | Location | Works For | Notes |
|-------|----------|-----------|-------|
| `OpenPrice` | `PositionBase` | BOTH long AND short | **ALWAYS USE THIS** |
| `AverageOpenPrice` | `PositionView` | **NEITHER** | Always returns 0, do NOT use |

**Investigation (2026-02-02):** We tested all 5 positions on the live account:
- Long positions (Amount=+2): `PositionBase.OpenPrice` = 21.525, 28.4 ✓
- Short positions (Amount=-2): `PositionBase.OpenPrice` = 1.77, 0.385 ✓
- **ALL positions:** `PositionView.AverageOpenPrice` = 0 ✗

```python
# WRONG - AverageOpenPrice is ALWAYS zero
avg_price = pos.get("PositionView", {}).get("AverageOpenPrice", 0)  # Returns 0!

# RIGHT - OpenPrice works for both long and short positions
open_price = pos.get("PositionBase", {}).get("OpenPrice", 0)  # Returns actual fill price
```

**Position Lookup as Fill Price Fallback:**
```python
# When activities endpoint has no price, check position
for pos in get_positions():
    if pos.get("PositionBase", {}).get("Uic") == uic:
        open_price = pos.get("PositionBase", {}).get("OpenPrice", 0)
        if open_price > 0:
            return True, {"fill_price": open_price, "source": "position_check"}
```

---

## 3. Price Data Extraction

### The Problem
Different asset types return price data in different fields. VIX especially is tricky.

### Asset-Specific Price Fields

| Asset Type | Primary Field | Fallback Fields | Notes |
|------------|---------------|-----------------|-------|
| Stock/ETF | `Quote.Mid` | `Quote.Bid/Ask`, `LastTraded` | Most reliable |
| CFD | `Quote.Mid` | `Quote.Bid/Ask` | Like stocks |
| Stock Index (VIX.I) | `PriceInfoDetails.LastTraded` | None | **NO bid/ask!** |
| Options | `Quote.Bid/Ask` | `LastTraded` | Wide spreads common |

### VIX Price Extraction (CRITICAL)

```python
def extract_vix_price(data: Dict) -> Optional[float]:
    """
    VIX is a stock INDEX, not a tradable instrument.
    It has NO bid/ask spread - only LastTraded in PriceInfoDetails.

    CRITICAL: WebSocket subscription MUST include "PriceInfoDetails" field group!
    """
    # Try PriceInfoDetails.LastTraded first (correct for VIX)
    price_info_details = data.get("PriceInfoDetails", {})
    if price_info_details:
        last_traded = price_info_details.get("LastTraded")
        if last_traded and last_traded > 0:
            return float(last_traded)

    # Fallback to PriceInfo.LastTraded
    price_info = data.get("PriceInfo", {})
    if price_info:
        last_traded = price_info.get("LastTraded")
        if last_traded and last_traded > 0:
            return float(last_traded)

    return None  # Will trigger Yahoo Finance fallback
```

### Generic Price Extraction

```python
def extract_price(data: Dict, asset_type: str) -> Optional[float]:
    """
    Extract price from Saxo price response based on asset type.
    """
    # For indices like VIX, use LastTraded
    if asset_type in ["StockIndex", "CfdOnIndex"]:
        return extract_vix_price(data)

    # For tradable instruments, prefer mid price
    quote = data.get("Quote", {})
    if quote:
        # Try mid first
        mid = quote.get("Mid")
        if mid and mid > 0:
            return float(mid)

        # Calculate from bid/ask
        bid = quote.get("Bid", 0)
        ask = quote.get("Ask", 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2

    # Fallback to LastTraded
    price_info = data.get("PriceInfo", {})
    last_traded = price_info.get("LastTraded")
    if last_traded and last_traded > 0:
        return float(last_traded)

    return None
```

---

## 4. Asset Type Mapping

### The Problem
Saxo requires correct AssetType for ALL API calls. Wrong type = 404 or wrong data.

### Asset Type Reference

| Instrument | Saxo AssetType | UIC | Notes |
|------------|---------------|-----|-------|
| SPX price (CFD) | `CfdOnIndex` | 4913 | Use for SPX spot price |
| SPXW options | `StockIndexOption` | 128 | 0DTE options |
| VIX spot | `StockIndex` | 10606 | For VIX level monitoring |
| VIX options | `StockOption` | 117 | Not commonly used |
| SPY stock | `Stock` | varies | Delta Neutral underlying |
| SPY options | `StockOption` | varies | Delta Neutral options |

### Code Pattern

```python
# WRONG - Hardcoded asset type
positions = client.get_positions(asset_type="StockOption")  # Misses SPX options!

# RIGHT - Use correct asset type for instrument
if symbol.startswith("SPXW") or symbol.startswith("SPX:"):
    asset_type = "StockIndexOption"
elif symbol.endswith(".I"):  # Index like VIX.I
    asset_type = "StockIndex"
else:
    asset_type = "StockOption"

positions = client.get_positions(asset_type=asset_type)
```

---

## 5. WebSocket Streaming

### The Problem
WebSocket subscriptions must include correct FieldGroups or data will be missing. Additionally, Saxo sends **binary frames**, not plain JSON text.

### CRITICAL: Binary WebSocket Format (Fixed 2026-01-26)

Saxo Bank sends WebSocket messages as **binary frames**, not plain JSON text. Previous code tried to decode binary as UTF-8 which silently failed, causing stale cached prices.

**Documentation:** https://www.developer.saxo/openapi/learn/plain-websocket-streaming

**Binary Frame Format:**
```
| 8 bytes | 2 bytes  | 1 byte    | N bytes | 1 byte  | 4 bytes | N bytes |
| Msg ID  | Reserved | RefID Len | RefID   | Format  | Size    | Payload |
| uint64  |          | uint8     | ASCII   | 0=JSON  | int32   | JSON    |
| little  |          |           |         | 1=Proto | little  |         |
```

**Correct Parsing (in `saxo_client.py`):**
```python
import struct

def _decode_binary_ws_message(self, raw: bytes):
    """Decode Saxo Bank binary WebSocket message format."""
    pos = 0
    while pos < len(raw):
        # Message ID (8 bytes, uint64 little-endian)
        msg_id = struct.unpack_from('<Q', raw, pos)[0]
        pos += 8

        # Reserved (2 bytes, skip)
        pos += 2

        # Reference ID length (1 byte)
        ref_id_len = struct.unpack_from('B', raw, pos)[0]
        pos += 1

        # Reference ID (variable length ASCII)
        ref_id = raw[pos:pos + ref_id_len].decode('ascii')
        pos += ref_id_len

        # Payload format (1 byte: 0=JSON, 1=Protobuf)
        payload_format = struct.unpack_from('B', raw, pos)[0]
        pos += 1

        # Payload size (4 bytes, int32 little-endian)
        payload_size = struct.unpack_from('<i', raw, pos)[0]
        pos += 4

        # Extract and parse JSON payload
        payload_data = raw[pos:pos + payload_size]
        pos += payload_size

        if payload_format == 0:  # JSON
            msg = json.loads(payload_data.decode('utf-8'))
            yield {'refid': ref_id, 'msgId': msg_id, 'msg': msg}
```

**Wrong (Previous Code):**
```python
# WRONG - Binary frames cannot be decoded as UTF-8 text
def on_message(ws, message):
    data = json.loads(message.decode('utf-8'))  # FAILS SILENTLY!
```

### Cache Architecture

WebSocket updates populate `self._price_cache` which is used by:
- `get_quote()` - Checks cache first, falls back to REST API
- `get_spy_price()` - Checks cache first, falls back to REST, then Yahoo
- `get_vix_price()` - Checks cache first, falls back to REST, then Yahoo

This eliminates API rate limit concerns for frequent price checks (e.g., 1-second ITM monitoring).

### Required FieldGroups by Use Case

```python
# For general price streaming (stocks, ETFs, CFDs)
STANDARD_FIELD_GROUPS = ["DisplayAndFormat", "Quote", "PriceInfo"]

# For VIX and stock indices (CRITICAL - must include PriceInfoDetails!)
INDEX_FIELD_GROUPS = ["DisplayAndFormat", "Quote", "PriceInfo", "PriceInfoDetails"]

# For options (need greeks, theoretical prices)
OPTION_FIELD_GROUPS = ["DisplayAndFormat", "Quote", "PriceInfo", "Greeks", "InstrumentPriceDetails"]
```

### Token Refresh Before WebSocket (CONN-008 Fix)

```python
def start_price_streaming(self):
    """
    Start WebSocket price streaming.

    CRITICAL: Must refresh token BEFORE creating WebSocket connection!
    If another bot refreshed the shared token while this bot was sleeping,
    the in-memory token will be stale and cause 401 Unauthorized.
    """
    # CRITICAL FIX: Ensure token is fresh BEFORE starting WebSocket
    if not self.authenticate():
        logger.warning("Token refresh failed before WebSocket connection")

    # Now safe to start WebSocket with fresh token
    self._start_websocket()
```

---

## 6. Order Status Handling

### The Problem
Market orders fill so fast they "disappear" - status becomes "Unknown" or order not found.

### Order Status State Machine

```
Placed → Working → Filled/PartialFill/Rejected/Cancelled/Unknown
              ↓
        (For market orders, often jumps straight to Unknown)
```

### Handling "Unknown" Status (Fixed 2026-01-23)

```python
def verify_order_completion(self, order_id, uic, asset_type):
    """
    Verify order is complete. Handle the "Unknown" status edge case.
    """
    status = self.get_order_status(order_id)

    if status == "Filled" or status == "FinalFill":
        return True, "Filled via status check"

    elif status == "Unknown":
        # CRITICAL: "Unknown" often means order filled and disappeared
        # Must check activities to verify
        filled, fill_details = self.check_order_filled_by_activity(order_id, uic)
        if filled:
            return True, fill_details

        # Also check if position exists
        positions = self.get_positions(asset_type=asset_type)
        # ... verify position

    elif status in ["Rejected", "Cancelled"]:
        return False, f"Order {status}"

    return False, f"Unknown state: {status}"
```

---

## 7. Position Detection

### The Problem
Position detection must handle multiple iron flies, different expiries, and partial positions.

### Position Matching Pattern

```python
def find_matching_position(self, expected_uic, expected_direction, positions):
    """
    Find a position matching our expected leg.

    CRITICAL: Must match on BOTH UIC and direction (long/short).
    """
    for pos in positions:
        pos_uic = pos.get("Uic")
        pos_amount = pos.get("Amount", 0)

        # Direction: positive = long, negative = short
        pos_direction = "long" if pos_amount > 0 else "short"

        if pos_uic == expected_uic and pos_direction == expected_direction:
            return pos

    return None
```

### Multiple Iron Fly Detection (POS-004)

```python
def detect_multiple_iron_flies(self, positions):
    """
    Handle case where multiple iron flies exist (e.g., from different days).

    Strategy: Group by expiry, match short call/put pairs, select closest to ATM.
    """
    # Group by expiry
    by_expiry = defaultdict(list)
    for pos in positions:
        expiry = pos.get("Expiry", "unknown")
        by_expiry[expiry].append(pos)

    # For each expiry, try to identify complete iron fly
    iron_flies = []
    for expiry, legs in by_expiry.items():
        # Need 4 legs: long call, long put, short call, short put
        if len(legs) >= 4:
            # Match by strike proximity
            iron_fly = self._match_iron_fly_legs(legs)
            if iron_fly:
                iron_flies.append(iron_fly)

    # Select the one closest to current price
    if len(iron_flies) > 1:
        return self._select_closest_iron_fly(iron_flies, current_price)

    return iron_flies[0] if iron_flies else None
```

---

## 8. Common Mistakes

### Mistake 1: Using Quoted Prices for P&L
**Wrong:** `credit = quoted_bid - quoted_ask`
**Right:** `credit = actual_fill_price_short - actual_fill_price_long`

**Impact:** P&L can be off by $20-50 per trade.

### Mistake 2: Missing PriceInfoDetails for VIX
**Wrong:** `FieldGroups = ["Quote", "PriceInfo"]`
**Right:** `FieldGroups = ["Quote", "PriceInfo", "PriceInfoDetails"]`

**Impact:** VIX always falls back to Yahoo Finance.

### Mistake 3: Not Handling "Unknown" Order Status
**Wrong:** Keep polling for status change
**Right:** Check activities endpoint immediately

**Impact:** Close orders appear stuck, logs show "Unknown" forever.

### Mistake 4: Wrong Asset Type for SPX Options
**Wrong:** `asset_type = "StockOption"`
**Right:** `asset_type = "StockIndexOption"`

**Impact:** Options not found, orders rejected.

### Mistake 5: Stale Token on WebSocket Connect
**Wrong:** Use `self.access_token` directly
**Right:** Call `authenticate()` first to refresh from coordinator

**Impact:** WebSocket 401 errors after sleeping.

### Mistake 6: Assuming Order ID Persists
**Wrong:** Keep checking `/orders/{order_id}` for status
**Right:** Check `/activities/` or `/positions/` for market orders

**Impact:** Filled orders appear as "not found".

### Mistake 7: Decoding WebSocket as Text (Fixed 2026-01-26)
**Wrong:** `json.loads(message.decode('utf-8'))` - treats binary as text
**Right:** Use `struct.unpack()` to parse binary frame format

**Impact:** WebSocket cache never updates, stale prices, unnecessary REST API calls.

### Mistake 8: Using DELETE Endpoint to Close SPX Positions (Fixed 2026-02-03)
**Wrong:** `DELETE /trade/v2/positions/{position_id}` - returns 404 for SPX options
**Right:** Use `place_emergency_order()` with `to_open_close="ToClose"`

**Impact:** Stop losses fail silently, positions remain open while bot thinks they're closed.

**Root Cause:** The `DELETE /trade/v2/positions/{id}` endpoint returns 404 "File or directory not found" for StockIndexOption (SPX) positions. This endpoint may work for other asset types but does NOT work for SPX options.

**Correct Pattern for Closing SPX Option Positions:**
```python
# To close a SHORT position (you sold it): BUY to close
result = client.place_emergency_order(
    uic=position_uic,
    asset_type="StockIndexOption",
    buy_sell=BuySell.BUY,  # Buy back the short
    amount=position_amount,
    order_type=OrderType.MARKET,
    to_open_close="ToClose"
)

# To close a LONG position (you bought it): SELL to close
result = client.place_emergency_order(
    uic=position_uic,
    asset_type="StockIndexOption",
    buy_sell=BuySell.SELL,  # Sell the long
    amount=position_amount,
    order_type=OrderType.MARKET,
    to_open_close="ToClose"
)
```

**Affected Bots:**
- MEIC: Fixed in commit d71a248 (2026-02-03)
- Iron Fly: Already used place_emergency_order (not affected)
- Delta Neutral: Uses SPY (StockOption), may need verification

---

## 9. Chart API for Historical OHLC Data

### The Problem (Fixed 2026-01-21)
The Rolling Put Diagonal bot uses the Chart API to fetch daily OHLC data for calculating technical indicators (EMA, MACD, CCI). On Jan 20-21, 2026, the Chart API returned **404 errors** for QQQ (UIC 4328771), causing EMA to become $0.00 and blocking all entries.

**Root Cause:** The code was using the deprecated `/chart/v1/charts` endpoint instead of `/chart/v3/charts`. This was fixed in commit `d4fa997` on Jan 21, 2026.

### Chart API Endpoint
```
GET /chart/v3/charts?Uic={uic}&AssetType={asset_type}&Horizon={horizon}&Count={count}&FieldGroups=ChartInfo,Data
```

**Parameters:**
| Parameter | Description | Example Values |
|-----------|-------------|----------------|
| Uic | Instrument ID | 4328771 (QQQ), 36590 (SPY) |
| AssetType | Instrument type | `Etf`, `Stock`, `CfdOnIndex` |
| Horizon | Bar size in minutes | 1440 (daily), 60 (hourly), 5 (5-min) |
| Count | Number of bars | 50 (default), max 1200 |

### Key Findings

**1. Chart API v1 is DEPRECATED (use v3):**
- Jan 20-21: Returned 404 because code used `/chart/v1/charts`
- Jan 21 15:25: Fixed to use `/chart/v3/charts` (commit d4fa997)
- Jan 22: Worked correctly with v3 endpoint
- **Lesson:** Always use `/chart/v3/charts`, not v1

**2. Quote API works when Chart API fails:**
- Quote endpoint (`/trade/v1/infoprices/list`) returned valid QQQ prices ($611.89)
- Only the Chart endpoint (`/chart/v3/charts`) failed

**3. Only Rolling Put Diagonal uses Chart API:**
- Iron Fly: No chart data needed (no technical indicators)
- Delta Neutral: No chart data needed (no technical indicators)
- Rolling Put Diagonal: Requires EMA for entry filter

### Deprecated Endpoint 404 Error Pattern

```
Error Log (Jan 20-21 - using /chart/v1/charts):
API request failed: 404 - File or directory not found
Failed to get chart data for UIC 4328771: 'max_consecutive_errors'
Insufficient chart data for indicators
QQQ: $611.89 | EMA9: $0.00  <-- Quote API works, Chart API fails

Working Log (Jan 22 - using /chart/v3/charts):
QQQ: $621.32 | EMA9: $619.28  <-- Both work with v3 endpoint
```

**The Fix (commit d4fa997):**
```diff
- endpoint = f"/chart/v1/charts"
+ endpoint = f"/chart/v3/charts"
```

### Extended AssetTypes Consideration

Saxo introduced "Extended AssetTypes" which split `Stock` into:
- `Stock` - Pure equities
- `Etf` - Exchange-Traded Funds
- `Etc` - Exchange-Traded Commodities
- `Etn` - Exchange-Traded Notes

**Impact:** Some older apps/endpoints may not support `AssetType=Etf`. Try `AssetType=Stock` as fallback.

### Sources

- [Saxo Chart v3 API Reference](https://developer.saxobank.com/openapi/referencedocs/chart/v3/charts)
- [Extended AssetTypes Documentation](https://www.developer.saxo/openapi/learn/extended-assettypes)
- [Historical Prices FAQ](https://openapi.help.saxo/hc/en-us/articles/4405260778653-How-can-I-get-historical-prices)
- [Daily Closing Prices FAQ](https://openapi.help.saxo/hc/en-us/articles/4417053702801-How-can-I-get-historical-daily-closing-prices)

---

## 10. Extended Hours Trading (Pre-Market & After-Hours)

### The Problem (Fixed 2026-01-26)
Bots were attempting to fetch pre-market prices from Saxo at times when the extended hours session hadn't started yet (e.g., 4:30 AM UTC = before 7:00 AM ET). Saxo does not provide extended hours data outside of specific windows.

### Saxo Extended Hours Schedule

| Session | Time (ET) | Time (UTC in Winter) | Notes |
|---------|-----------|---------------------|-------|
| **Pre-Market** | 7:00 AM - 9:30 AM | 12:00 PM - 2:30 PM | Limit orders only |
| **Regular** | 9:30 AM - 4:00 PM | 2:30 PM - 9:00 PM | Full trading |
| **After-Hours** | 4:00 PM - 5:00 PM | 9:00 PM - 10:00 PM | Limit orders only |

**Source:** [Saxo Extended Trading Hours](https://www.help.saxo/hc/en-us/articles/7574076258589-Extended-trading-hours)

### Key Points

1. **Extended hours is auto-enabled** on all Saxo accounts
2. **Only limit orders** are supported during extended hours (no market orders)
3. **Price data available** during extended hours for US stocks, ETFs, and single stock CFDs
4. **Before 7:00 AM ET**: Saxo returns stale data or no prices - do NOT attempt to fetch

### Implementation Pattern

```python
from shared.market_hours import (
    is_pre_market,           # True if 7:00-9:30 AM ET on trading day
    is_after_hours,          # True if 4:00-5:00 PM ET on trading day
    is_saxo_price_available, # True if 7:00 AM - 5:00 PM ET on trading day
    get_trading_session,     # Returns "pre_market", "regular", "after_hours", "closed"
)

def fetch_premarket_price():
    """Only fetch prices when Saxo can provide them."""

    # CRITICAL: Check if Saxo has prices available
    if not is_saxo_price_available():
        logger.info("Saxo prices not available yet (before 7:00 AM ET)")
        return None

    # Now safe to fetch
    quote = client.get_quote(uic, asset_type="Etf")
    if quote:
        return quote.get("Mid") or ((quote.get("Bid", 0) + quote.get("Ask", 0)) / 2)
    return None
```

### Error Pattern (What to Avoid)

```python
# WRONG - Fetches at any time, causing failed requests before 7 AM
is_premarket = now.hour < 9 or (now.hour == 9 and now.minute < 30)
if is_premarket:
    quote = client.get_quote(uic)  # May fail before 7 AM!

# RIGHT - Only fetch during Saxo's extended hours window
if is_saxo_price_available():  # True only between 7 AM - 5 PM ET
    quote = client.get_quote(uic)  # Saxo has prices available
```

### Helper Functions in `shared/market_hours.py`

| Function | Returns | Description |
|----------|---------|-------------|
| `is_pre_market()` | `bool` | True if 7:00-9:30 AM ET on trading day |
| `is_after_hours()` | `bool` | True if 4:00-5:00 PM ET on trading day |
| `is_extended_hours()` | `bool` | True if pre-market OR after-hours |
| `is_saxo_price_available()` | `bool` | True if 7:00 AM - 5:00 PM ET (when Saxo has data) |
| `get_trading_session()` | `str` | Returns "pre_market", "regular", "after_hours", or "closed" |
| `get_extended_hours_status_message()` | `str` | Human-readable status with session info |

### Bot-Specific Handling

| Bot | Pre-Market Behavior |
|-----|---------------------|
| **Iron Fly 0DTE** | Sleeps until 9:30 AM - no pre-market price fetching needed |
| **Delta Neutral** | Fetches SPY price if `is_saxo_price_available()`, logs gap analysis |
| **Rolling Put Diagonal** | Fetches QQQ price if `is_saxo_price_available()`, logs gap analysis |

---

## 11. WebSocket Reliability Fixes (2026-01-28)

### Background

On 2026-01-27, production trading experienced multiple order failures due to WebSocket streaming issues that went undetected. This section documents the 10 critical fixes implemented to prevent these issues.

**Root Cause Analysis:** The WebSocket cache was returning stale data after disconnects, and the system had no way to detect when the WebSocket was unhealthy or when cached data was too old.

### Fix #1: Cache Invalidation on Disconnect (CONN-007)

**Problem:** When WebSocket disconnected, cached prices remained in `_price_cache`. Bot continued using stale data after reconnection.

**Fix:** Clear cache in all disconnect paths:
```python
def _on_ws_close(self, ws, close_status_code, close_msg):
    self._clear_cache()  # NEW: Prevent stale data usage

def _on_ws_error(self, ws, error):
    self._clear_cache()  # NEW: Prevent stale data usage
```

### Fix #2: Timestamp-Based Staleness Detection (CONN-008)

**Problem:** Cached data could be arbitrarily old with no way to detect it.

**Fix:** Each cache entry now includes a timestamp:
```python
# Cache format changed from:
self._price_cache[uic] = quote_data

# To:
self._price_cache[uic] = {'timestamp': datetime.now(), 'data': quote_data}

# get_quote() now checks:
cache_age = (datetime.now() - entry['timestamp']).total_seconds()
if cache_age > 60:  # Max 60 seconds
    # Force REST fallback
```

### Fix #3: Limit Order $0 Price Bug (CONN-014)

**Problem:** Python truthiness: `if limit_price:` evaluates False when `limit_price=0.0`.

**Fix:** Explicit None/zero check:
```python
# OLD (buggy):
if order_type == OrderType.LIMIT and limit_price:
    # Never executed when limit_price=0.0!

# NEW (fixed):
if limit_price is None or limit_price <= 0:
    raise ValueError("Limit price must be positive")
```

### Fix #4: Never Use $0.00 Fallback Price (CONN-015)

**Problem:** When quote failed AND `leg_price` was $0, bot placed order at $0.00.

**Fix:** Skip to retry if both are invalid:
```python
if quote is None or not self._validate_quote(quote):
    if leg_price and leg_price > 0:
        price = leg_price  # Use fallback
    else:
        logger.warning("DATA-004: Both quote and leg_price invalid, skipping to retry")
        continue  # Don't place order at $0
```

### Fix #5: WebSocket Health Monitoring (CONN-009)

**Problem:** No way to detect if WebSocket thread died silently.

**Fix:** New health check method:
```python
def is_websocket_healthy(self) -> bool:
    """Check if WebSocket is alive and receiving data."""
    if not self._ws_thread or not self._ws_thread.is_alive():
        return False
    if self._last_message_time:
        age = (datetime.now() - self._last_message_time).total_seconds()
        if age > 60:  # No message in 60s = unhealthy
            return False
    return True
```

### Fix #6: Heartbeat Timeout Detection (CONN-010)

**Problem:** Saxo sends heartbeats every ~15 seconds. Zombie connections showed no heartbeat.

**Fix:** Track heartbeat timestamps:
```python
# On heartbeat received:
self._last_heartbeat_time = datetime.now()

# In health check:
if self._last_heartbeat_time:
    age = (datetime.now() - self._last_heartbeat_time).total_seconds()
    if age > 60:  # Saxo sends every 15s, so 60s = zombie
        return False
```

### Fix #8: Thread-Safe Cache Locking (CONN-012)

**Problem:** Race condition between WebSocket callback thread and main trading thread.

**Fix:** Mutex for all cache operations:
```python
self._price_cache_lock = threading.Lock()

# All cache read/write:
with self._price_cache_lock:
    self._price_cache[uic] = data

with self._price_cache_lock:
    return self._price_cache.get(uic)
```

### Fix #10: Binary Parser Bounds Checking (CONN-011)

**Problem:** Malformed message could cause array index out of bounds.

**Fix:** Validate lengths at each step:
```python
def _decode_binary_ws_message(self, raw):
    if len(raw) < 12:  # Minimum header size
        return None
    # Check bounds before each unpack...
    if pos + ref_id_len > len(raw):
        return None
```

### Impact

These fixes apply to ALL bots using `SaxoClient`:
- **Delta Neutral:** ITM monitoring now reliable at 1-second intervals
- **Iron Fly:** Price updates for P&L tracking now work correctly
- **Rolling Put Diagonal:** Entry price checks use fresh data

### Testing

22 unit tests in `scripts/test_websocket_fixes.py` validate all fixes:
```bash
python scripts/test_websocket_fixes.py
```

### Reference

Full edge case documentation: [DELTA_NEUTRAL_EDGE_CASES.md](./DELTA_NEUTRAL_EDGE_CASES.md) (CONN-007 through CONN-016)

---

## Quick Reference: File Locations

| Pattern | File | Line(s) |
|---------|------|---------|
| Fill price extraction | `bots/iron_fly_0dte/strategy.py` | ~2986-3052 |
| VIX price extraction | `shared/saxo_client.py` | ~1200-1250 |
| Order verification | `shared/saxo_client.py` | ~800-900 |
| WebSocket setup | `shared/saxo_client.py` | ~2927-2970 |
| Unknown status handling | `bots/iron_fly_0dte/strategy.py` | ~2497-2516 |
| Position detection | `shared/saxo_client.py` | ~1600-1700 |
| Extended hours helpers | `shared/market_hours.py` | ~430-570 |
| Pre-market price fetch (Delta Neutral) | `bots/delta_neutral/main.py` | ~523-590 |
| Pre-market price fetch (Rolling Put Diagonal) | `bots/rolling_put_diagonal/main.py` | ~478-540 |

---

## Related Documentation

- [IRON_FLY_CODE_AUDIT.md](./IRON_FLY_CODE_AUDIT.md) - Full code audit with line references
- [IRON_FLY_EDGE_CASES.md](./IRON_FLY_EDGE_CASES.md) - 63 edge cases and handling
- [CLAUDE.md](../CLAUDE.md) - Project overview and VM commands

---

**Document Version:** 1.4
**Created:** 2026-01-23
**Updated:** 2026-01-28 - Added Section 11: WebSocket Reliability Fixes (10 critical fixes)
**Previous Update:** 2026-01-26 - Added WebSocket binary parsing documentation (Section 5), Mistake 7
**Author:** Claude (learned from production bugs)
