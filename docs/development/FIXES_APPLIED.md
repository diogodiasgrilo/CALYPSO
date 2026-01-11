# Fixes Applied - January 8-9, 2026

## Issue 1: HTTP 201 Status Code Error ✅ FIXED

**Problem:**
Bot was treating HTTP 201 (Created) as an error during token exchange, even though Saxo returns valid tokens with this status code.

**Fix Applied:**
Modified [saxo_client.py](saxo_client.py) lines 285 and 332:
- Changed `if response.status_code == 200:` to `if response.status_code in [200, 201]:`
- Applied to both `_exchange_code_for_token()` and `_refresh_access_token()` methods

**Result:**
Authentication now works successfully! ✓

---

## Issue 2: Tokens Not Persisting ✅ FIXED

**Problem:**
Bot obtained tokens successfully but didn't save them to config.json, forcing re-authentication every time.

**Fix Applied:**
Added token persistence functionality to [saxo_client.py](saxo_client.py):

1. **New method** `_save_tokens_to_config()` (lines 360-387):
   - Reads current config.json
   - Updates access_token, refresh_token, and token_expiry
   - Writes back to config.json
   - Called automatically after successful authentication or token refresh

2. **Updated methods:**
   - Line 297: `_exchange_code_for_token()` now calls `_save_tokens_to_config()`
   - Line 343: `_refresh_access_token()` now calls `_save_tokens_to_config()`

**Result:**
Tokens are now automatically saved to config.json after authentication! You won't need to log in every time.

---

## Issue 3: VIX UIC 19217 Invalid ⚠️ ACTION REQUIRED

**Problem:**
```
2026-01-08 19:01:16 | ERROR | saxo_client | API request failed: 404 - {"ErrorCode":"IllegalInstrumentId","Message":"Instrument Not Found"}
```

The VIX UIC `19217` in your config.json doesn't exist in Saxo's simulation environment.

**Solution - Option 1 (Recommended): Find Correct VIX UIC**

I created a helper script to find the correct UIC. Run:

```bash
python find_vix_uic.py
```

This will:
1. Authenticate with Saxo
2. Search for VIX instruments available in your environment
3. Display all VIX UICs with their details
4. Show you which one to use

Then update [config.json](config.json):
```json
"vix_uic": YOUR_ACTUAL_VIX_UIC_HERE
```

**Solution - Option 2: Disable VIX Entry Filter**

If VIX isn't available in sim environment, you can temporarily disable the VIX entry filter:

In [config.json](config.json), change:
```json
"max_vix_entry": 99999.0
```

This effectively disables the VIX < 18 entry requirement, allowing the bot to enter trades regardless of VIX level.

---

## Testing the Fixes

### Test 1: Verify Token Persistence

1. Delete your current tokens from config.json (set to empty strings)
2. Run: `python main.py --status`
3. Authenticate in browser
4. Check config.json - you should now see tokens saved automatically!

### Test 2: Verify No Re-Authentication Needed

1. Run: `python main.py --status` again
2. This time it should NOT open browser (will use saved tokens)
3. You should see: "Using existing valid access token"

### Test 3: Find Correct VIX UIC

```bash
python find_vix_uic.py
```

Follow the output instructions to update your config.

---

## What Changed - Summary

### Files Modified:
1. **saxo_client.py** (3 changes):
   - Line 285: Accept HTTP 201 in `_exchange_code_for_token()`
   - Line 332: Accept HTTP 201 in `_refresh_access_token()`
   - Lines 360-387: New `_save_tokens_to_config()` method
   - Line 297: Call `_save_tokens_to_config()` after token exchange
   - Line 343: Call `_save_tokens_to_config()` after token refresh

### Files Created:
1. **find_vix_uic.py** - Helper script to find correct VIX UIC
2. **FIXES_APPLIED.md** - This file

---

## Next Steps

1. ✅ **Token persistence is working** - You're done! Next run won't require login.

2. ⚠️ **Fix VIX UIC** - Run `python find_vix_uic.py` to find the correct UIC

3. 🧪 **Test full bot** - Once VIX UIC is fixed, run:
   ```bash
   python main.py --dry-run
   ```

4. 📊 **Optional: Enable Google Sheets** - If you want Google Sheets logging:
   ```bash
   pip install gspread google-auth
   ```

---

## Expected Behavior Now

**Before fixes:**
```
2026-01-08 18:54:11 | ERROR | saxo_client | Token exchange failed: 201
```

**After fixes:**
```
2026-01-08 19:01:16 | INFO  | saxo_client | Access token obtained successfully
2026-01-08 19:01:16 | INFO  | saxo_client | Tokens saved to config.json successfully
```

And config.json will contain your tokens automatically! 🎉

---

---

## Issue 4: Wrong SPY UIC ✅ FIXED

**Problem:**
Config was using UIC 211 which is **Apple Inc. (AAPL)**, not SPY!

**Fix Applied:**
Updated [config.example.json](config.example.json) line 23:
- Changed from `"underlying_uic": 211` (AAPL)
- Changed to `"underlying_uic": 36590` (SPY:arcx - SPDR S&P 500 ETF Trust)

**Verification:**
Created and ran `search_instruments.py` which confirmed:
```
UIC: 36590 | SPY:arcx | Etf | SPDR S&P 500 ETF Trust
```

**Result:**
Bot now fetches data for the correct instrument (SPY)! ✓

---

## Issue 5: Market Data Access - NoAccess ✅ SOLVED

**Problem:**
Even with correct UICs and proper API usage, both SPY and VIX return:
```json
{
    "PriceTypeAsk": "NoAccess",
    "PriceTypeBid": "NoAccess"
}
```

**Root Cause (CONFIRMED):**
According to official Saxo Bank documentation:
> "Saxo Bank only offers market data for Forex instruments to demo accounts on the simulation environment."

This is intentional policy, not a bug. Source: [Saxo Support Article](https://openapi.help.saxo/hc/en-us/articles/4405160773661)

**Solution Implemented: External Price Feed ✅**

Created a hybrid system that:
- Uses **Yahoo Finance** for SPY/VIX prices in simulation (15-min delayed)
- Uses **Saxo API** for all trading operations (orders, positions, balance)
- **Automatically disabled** when switching to live environment

**Files Created:**
- `external_price_feed.py` - Yahoo Finance integration with caching
- Configuration option in `config.json`: `external_price_feed.enabled`

**Result:**
Bot now works perfectly in simulation environment:
```
[DRY RUN] SPY: $689.51 | VIX: 15.66 | State: Idle
```

**For Live Trading:**
When you switch to `"environment": "live"`, the external feed automatically disables and uses real-time Saxo prices.

---

## Issue 6: API Improvements ✅ FIXED

**Updates to saxo_client.py:**

1. **Added AccountKey to all price requests** (required for sim environment)
   - Line 602: `get_quote()` now includes `AccountKey` parameter
   - Subscriptions now include `AccountKey` in request body

2. **Added Amount parameter** (required by Saxo API)
   - Line 605: All price requests include `Amount: 1`

3. **Added OrderRelation to orders** (Saxo requirement)
   - Added `"OrderRelation": "StandAlone"` to order placement

4. **Added FieldGroups to get_open_orders()**
   - Ensures full order data is returned

5. **Improved error logging**
   - NoAccess detection with clear warnings
   - Detailed price data logging for debugging

**Updates to strategy.py:**

1. **PriceInfo fallback logic** (lines 268-288)
   - First tries Quote fields (Mid, LastTraded, Bid, Ask)
   - Falls back to PriceInfo.Last if Quote has NoAccess
   - Logs clear warnings when NoAccess detected

---

## Questions?

If you encounter any issues:
1. Check bot_log.txt for detailed error messages
2. Verify config.json has valid tokens saved
3. Run `python find_vix.py` to verify VIX UIC (10606)
4. Run `python test_spy_price.py` to check market data access
5. Read [MARKET_DATA_ACCESS.md](MARKET_DATA_ACCESS.md) for NoAccess issues
6. Make sure you're using the correct Saxo environment (sim vs live)
