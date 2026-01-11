# Market Data Access Issue - Saxo Bank Simulation Environment

## Official Saxo Bank Policy (CONFIRMED)

According to official Saxo Bank documentation:

> **"Saxo Bank only offers market data for Forex instruments to demo accounts on the simulation environment."**
>
> Source: [Why do I get "NoAccess" instead of prices?](https://openapi.help.saxo/hc/en-us/articles/4405160773661)

This is **NOT a bug or configuration issue** - it's an intentional limitation of simulation accounts.

## Problem Summary

The Saxo Bank simulation (paper trading) environment returns `NoAccess` for US equity and index price data:

```json
{
    "PriceTypeAsk": "NoAccess",
    "PriceTypeBid": "NoAccess",
    "Quote": {
        "ErrorCode": "None",
        "PriceSource": "NYSE_ARCA"
    }
}
```

## What Works

- **FX Spot prices** (e.g., EURUSD): ✅ Full access with real-time quotes
- **Account balance queries**: ✅ Working
- **Order placement**: ✅ Working (but cannot verify prices)

## What Doesn't Work

- **US Stocks/ETFs** (e.g., SPY - UIC 36590): ❌ NoAccess
- **US Indices** (e.g., VIX.I - UIC 10606): ❌ NoAccess

## Root Cause

Saxo Bank simulation accounts require **market data entitlements** for US equities and indices. By default, only FX spot data is included. This is a common limitation across broker simulation environments to prevent market data fees during paper trading.

## Verified Attempts

We tested multiple approaches:

1. ✅ **Correct API endpoint**: `/trade/v1/infoprices/list` with `AccountKey`
2. ✅ **All field groups**: `DisplayAndFormat,Quote,PriceInfo,PriceInfoDetails,Greeks,InstrumentPriceDetails`
3. ✅ **Correct UIC codes**:
   - SPY: 36590 (verified as "SPDR S&P 500 ETF Trust")
   - VIX.I: 10606 (verified as "CBOE Volatility Index")
4. ✅ **With and without AccountKey**: Both show NoAccess

**Conclusion**: The issue is not with the API implementation but with account-level market data permissions.

## Solutions

### Option 1: Use Live Account with Market Data Enabled (Production)

Switch from `"environment": "sim"` to `"environment": "live"` in [config.json](config.json):
1. Change `"environment": "live"` in config
2. Enable market data in SaxoTrader GO:
   - Log in to [saxotrader.com](https://saxotrader.com)
   - Click My Profile icon → Other Tab → Open API Access
   - Click Enable and Accept terms
3. **WARNING**: This uses real money. Only use if you understand the risks.

### Option 2: External Price Feed for Simulation (Recommended for Development)

Use external free data sources for SPY/VIX prices while using Saxo for order execution:
- **Yahoo Finance API**: Free, 15-min delayed data
- **Alpha Vantage**: Free tier with 5 API calls/minute
- **Polygon.io**: Free tier for end-of-day data

The bot will:
- Fetch SPY/VIX prices from external source
- Use Saxo API for all trading operations (orders, positions, balance)
- Log warnings that external prices are being used

This allows full development and testing without a live account.

## Current Implementation Status

The bot now:
1. ✅ Detects NoAccess responses
2. ✅ Logs clear warnings about market data entitlements
3. ✅ Uses correct SPY UIC (36590) and VIX UIC (10606)
4. ⚠️ Cannot trade without price data

## Testing Performed

Run `python test_spy_price.py` to verify market data access:
- Tests all available field groups
- Compares with/without AccountKey
- Shows exactly what data is accessible

## Next Steps

Choose one of the solutions above based on your needs:
- **For live trading**: Request market data access or use live account
- **For development**: Implement Option 3 with simulated prices
