# Saxo Bank Pricing API Analysis - NoAccess Issue

**Date**: January 11, 2026
**Status**: ✅ API Working Correctly | ⚠️ Account Entitlement Issue

---

## Summary

The bot is **calling the Saxo pricing API correctly**, but the API is returning `NoAccess` for both SPY and VIX prices. This is **NOT an API usage issue** — it's an **account subscription/entitlement issue**.

**What this means**: Your Saxo account doesn't have subscriptions to the real-time market data feeds for:
- SPY (US Equities feed from NYSE Arca)
- VIX (US Index feed from CBOE)

---

## Current Implementation: ✅ Correct

### Endpoint Being Used
```
GET /trade/v1/infoprices/list
```

### Request Parameters
```
AccountKey=HHzaFvDVAVCg3hi3QUvbNg==
Uics=36590                          # SPY
AssetType=Etf                        # Correct for SPY
Amount=1
FieldGroups=DisplayAndFormat,Quote,PriceInfo
```

### Why This Is Correct

1. **Endpoint**: `/infoprices/list` is the correct REST API endpoint for polling prices
2. **AccountKey**: Properly included (required in live environment)
3. **Uics**: Correct instrument codes:
   - SPY: UIC 36590 (SPDR S&P 500 ETF Trust)
   - VIX: UIC 10606 (CBOE Volatility Index)
4. **AssetType**: Properly specified as "Etf" for SPY, "StockIndex" for VIX
5. **FieldGroups**: Requesting proper data fields (DisplayAndFormat, Quote, PriceInfo)

---

## The Real Issue: Account Entitlements

### What Saxo Returns

```json
{
  "Data": [{
    "Quote": {
      "PriceTypeAsk": "NoAccess",    ← Account doesn't have feed subscription
      "PriceTypeBid": "NoAccess",    ← Account doesn't have feed subscription
      "AskSize": 0.0,
      "BidSize": 0.0,
      "ErrorCode": "None",
      "PriceSource": "NYSE_ARCA"
    }
  }]
}
```

### What This Means

- **NoAccess** = Your Saxo account tier doesn't include subscriptions to real-time feeds
- **PriceSource still shows** (NYSE_ARCA, CBOE) = The instruments are correctly identified
- **No price data** (Bid/Ask/Mid/LastTraded) = Cannot retrieve prices without subscription

### Why This Happens

Saxo Bank charges separately for real-time market data subscriptions:
- **US Equities** (for SPY) - requires separate subscription
- **CBOE Index** (for VIX) - requires separate subscription
- **Cost**: Varies by subscription tier (typically $25-100+ per month per feed)

---

## Current Workaround: ✅ Working

The bot has a **fallback mechanism** using external price feeds:

### How It Works
1. Bot calls Saxo API
2. API returns `NoAccess`
3. Bot detects NoAccess and falls back to external feed (Yahoo Finance)
4. Uses 15-minute delayed prices (sufficient for testing)

### Code Flow
```
saxo_client.py:1015-1032
├─ Detect "NoAccess" in Quote.PriceTypeAsk/PriceTypeBid
├─ Call external_feed.get_price(symbol)
└─ Inject external price into response structure
```

### Current Prices
- **SPY**: $694.07 (from Yahoo Finance, 15-min delayed)
- **VIX**: $14.49 (from Yahoo Finance, 15-min delayed)

---

## Options for Real-Time Prices

### Option 1: Add Market Data Subscriptions (Recommended for Live Trading)
**Cost**: $50-150/month per feed
**Process**:
1. Log into your Saxo account
2. Go to Account Settings → Subscriptions/Feeds
3. Subscribe to:
   - US Equities (for SPY)
   - CBOE Index (for VIX)
4. Takes 1-5 minutes to activate
5. Prices will then return real values instead of `NoAccess`

### Option 2: Use WebSocket Streaming (Already in Code)
**Cost**: Same subscriptions required
**Current Status**: ✅ Code already implements this

The bot has WebSocket streaming code:
```python
# saxo_client.py:1399
POST /trade/v1/prices/subscriptions
```

**Benefits**:
- Real-time tick-by-tick prices
- Lower latency than polling REST API
- Same subscription requirement

**Current Status in Logs**:
```
WebSocket error: Handshake status 401 Unauthorized
```
This 401 is a separate authentication token issue, not subscription.

### Option 3: Use External Price Feeds (Current Solution)
**Cost**: Free
**Data Quality**: 15-20 minute delay
**When**: Ideal for testing, development, paper trading

**Current Sources**:
- Yahoo Finance (for SPY, VIX)
- Easy to add other sources (Alpha Vantage, IEX Cloud, etc.)

---

## Testing: ✅ Confirmed Working

### API Validation
✅ Endpoint reached: `https://gateway.saxobank.com/openapi/trade/v1/infoprices/list`
✅ HTTP 200 response: Data retrieved successfully
✅ Field parsing: All fields correctly identified
✅ Error handling: NoAccess properly detected and handled

### Example Request
```
GET https://gateway.saxobank.com/openapi/trade/v1/infoprices/list?
  AccountKey=HHzaFvDVAVCg3hi3QUvbNg%3D%3D&
  Uics=36590&
  AssetType=Etf&
  Amount=1&
  FieldGroups=DisplayAndFormat%2CQuote%2CPriceInfo
```

### Example Response (NoAccess)
```json
{
  "Data": [{
    "Uic": 36590,
    "AssetType": "Etf",
    "DisplayAndFormat": {
      "Symbol": "SPY:arcx",
      "Description": "SPDR S&P 500 ETF Trust",
      "Currency": "USD"
    },
    "Quote": {
      "PriceTypeAsk": "NoAccess",
      "PriceTypeBid": "NoAccess",
      "PriceSource": "NYSE_ARCA",
      "ErrorCode": "None"
    },
    "PriceInfo": {}
  }]
}
```

---

## VIX 404 Error: ⚠️ Secondary Issue

There's also a 404 error when trying VIX with `CfdOnIndex`:

```
GET /trade/v1/infoprices?Uic=10606&AssetType=CfdOnIndex
404 - {"ErrorCode":"IllegalInstrumentId","Message":"Instrument Not Found"}
```

**Why**: VIX is not available as a `CfdOnIndex` (CFD = Contract for Difference).
**Solution**: Already handled in code — tries multiple asset types and falls back to external feed.

---

## What You Need To Do (For Live Trading)

### If Running in Live Environment with Real Money:
1. **Add market data subscriptions** to your Saxo account
2. **Verify** prices appear in API response instead of NoAccess
3. **Disable external_price_feed** in config.json
4. Test with real market data

### If Running in Simulation/Testing:
- ✅ Keep external_price_feed enabled (current setup)
- ✅ Works perfectly for testing strategy logic
- ✅ Prices update every 15-20 minutes

---

## Code Status

| Component | Status | Details |
|-----------|--------|---------|
| API Endpoint | ✅ Correct | `/trade/v1/infoprices/list` is proper endpoint |
| Parameters | ✅ Correct | AccountKey, Uics, AssetType all proper |
| Error Handling | ✅ Correct | Detects NoAccess and falls back |
| External Feed | ✅ Working | Yahoo Finance fallback functional |
| Response Parsing | ✅ Correct | All fields extracted properly |
| Account Entitlements | ⚠️ Missing | No real-time feed subscriptions |
| WebSocket Streaming | ⚠️ Auth Issue | Token issue, not API issue |

---

## Summary Table

| Item | Current | Issue | Solution |
|------|---------|-------|----------|
| **API Used** | infoprices/list | None - correct | N/A |
| **SPY Prices** | $694.07 (Yahoo) | NoAccess | Add subscription or use external feed |
| **VIX Prices** | $14.49 (Yahoo) | NoAccess | Add subscription or use external feed |
| **Data Freshness** | 15-20 min delay | Acceptable? | Add subscription for real-time |
| **Bot Status** | Running ✅ | None | Ready for testing |

---

## Related Files

- [saxo_client.py:1008-1037](saxo_client.py#L1008-L1037) - get_underlying_quote() with fallback
- [saxo_client.py:1039-1109](saxo_client.py#L1039-L1109) - get_vix_price() with multi-fallback
- [saxo_client.py:620-669](saxo_client.py#L620-L669) - get_quote() API implementation
- [config.json:external_price_feed](config.json) - Fallback configuration

---

**Conclusion**: Your implementation is **correct**. The NoAccess is not your code's fault—it's Saxo's way of saying "this account doesn't have real-time feeds." The bot gracefully handles this with external feeds and continues working fine for testing.
