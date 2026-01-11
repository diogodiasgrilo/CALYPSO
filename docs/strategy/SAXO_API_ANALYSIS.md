# Saxo OpenAPI Deep Dive Analysis

## Executive Summary

After comprehensive analysis of Saxo Bank's OpenAPI documentation, I've identified **critical issues** in how the bot currently accesses market data and options chains. The bot is using incorrect endpoints and missing required workflow steps.

---

## 1. Market Data Access - How It SHOULD Work

### Three Price Endpoints Available:

#### A. **InfoPrices** (GET - Request/Response)
- **Use case**: Occasional price checks, non-trading scenarios
- **Endpoint**: `GET /trade/v1/infoprices` (single) or `GET /trade/v1/infoprices/list` (multiple)
- **Advantages**: Simple request/response, no WebSocket needed
- **Limitations**: Not tradable prices, fewer fields, no commissions

**Required Parameters**:
```python
{
    "AccountKey": "string",
    "Uic": 36590,
    "AssetType": "Etf",  # or "StockOption", "StockIndex"
    "FieldGroups": "Quote,PriceInfo"  # Quote is always included
}
```

**What Bot Currently Does**: ✅ **CORRECT** - Uses this endpoint properly

---

#### B. **Prices** (POST - Streaming Subscription)
- **Use case**: Trading applications requiring real-time updates
- **Endpoint**: `POST /trade/v1/prices/subscriptions`
- **Advantages**: Real-time streaming, tradable prices, commission data
- **Limitations**: Requires WebSocket connection

**Required Parameters**:
```python
{
    "ContextId": "unique_context_id",
    "ReferenceId": "unique_ref_id",
    "Arguments": {
        "Uic": 36590,
        "AssetType": "Etf"
    }
}
```

**What Bot Currently Does**: ✅ Uses WebSocket subscriptions (working)

---

### NoAccess Error - Root Cause

When you see `"PriceTypeAsk": "NoAccess"` or `"PriceTypeBid": "NoAccess"`, it means:

**Your account lacks market data feed subscriptions for that instrument.**

From Saxo documentation:
> "To resolve NoAccess: contact Saxo to activate appropriate data feeds for desired instruments."

**Price Status Types**:
| Status | Meaning | Action |
|--------|---------|--------|
| **NoAccess** | No subscription rights | Contact Saxo support |
| **NoMarket** | Temporarily unavailable | Retry later |
| **Pending** | Price arriving soon | Common for options, wait |
| **Indicative** | Reference price (usually tradable) | Generally usable |
| **OldIndicative** | Last price when market closed | Historical only |

**What Bot Currently Does**: ✅ Falls back to Yahoo Finance external feed (15-min delayed)

---

## 2. Options Chain Access - CRITICAL ISSUES FOUND

### The Correct Workflow (What Bot Should Do):

#### **Step 1: Find OptionRootId**

You **cannot** directly query options chains with just the underlying UIC (like SPY = 36590).

**Correct Endpoint**:
```
GET /ref/v1/instruments/details?Uics=36590
```

**Response Contains**:
```json
{
  "Data": [{
    "Uic": 36590,
    "Symbol": "SPY",
    "RelatedOptionRootsEnhanced": [
      {
        "AssetType": "StockOption",
        "OptionRootId": 309,  // ← THIS IS WHAT YOU NEED
        "Description": "SPY Options"
      }
    ]
  }]
}
```

**What Bot Currently Does**: ❌ **SKIPS THIS STEP** - Goes directly to contractoptionspaces with UnderlyingUic

---

#### **Step 2: Get Options Chain with OptionRootId**

**Current Bot Code** ([saxo_client.py:677-687](saxo_client.py#L677-L687)):
```python
def get_option_chain(self, underlying_uic: int, expiry_date: Optional[str] = None):
    endpoint = "/ref/v1/instruments/contractoptionspaces"
    params = {
        "UnderlyingUic": underlying_uic,  # ❌ WRONG - Not a valid parameter
        "AssetType": "StockOption"
    }
    response = self._make_request("GET", endpoint, params=params)
```

**PROBLEM**: This endpoint requires OptionRootId **in the URL path**, not as a query parameter.

**Correct Implementation**:
```python
def get_option_chain(self, option_root_id: int, expiry_dates: Optional[List[str]] = None):
    # OptionRootId goes in the PATH, not params
    endpoint = f"/ref/v1/instruments/contractoptionspaces/{option_root_id}"

    params = {}

    # Optional: Filter by specific expiry dates
    if expiry_dates:
        params["OptionSpaceSegment"] = "SpecificDates"
        params["ExpiryDates"] = expiry_dates  # ["2024-02-16", "2024-03-15"]

    response = self._make_request("GET", endpoint, params=params)
    return response
```

---

#### **Step 3: Parse Response Structure**

**Response Structure**:
```json
{
  "OptionSpace": [
    {
      "Expiry": "2024-02-16T00:00:00Z",
      "DisplayExpiry": "16Feb24",
      "DisplayDaysToExpiry": 45,
      "SpecificOptions": [
        {
          "Strike": 450.0,
          "PutCall": "Call",
          "Uic": 4061136,  // ← This is the tradable option UIC
          "Symbol": "SPY 16FEB24 450 C"
        },
        {
          "Strike": 450.0,
          "PutCall": "Put",
          "Uic": 4061137,
          "Symbol": "SPY 16FEB24 450 P"
        },
        {
          "Strike": 455.0,
          "PutCall": "Call",
          "Uic": 4061138
        }
        // ... more strikes
      ]
    }
    // ... more expiries
  ]
}
```

**What Bot Currently Expects** ([saxo_client.py:700-702](saxo_client.py#L700-L702)):
```python
def get_option_expirations(self, underlying_uic: int):
    option_chain = self.get_option_chain(underlying_uic)
    if option_chain and "Data" in option_chain:  # ❌ WRONG - Response has "OptionSpace", not "Data"
        return option_chain["Data"]
```

**Correct Parsing**:
```python
def get_option_expirations(self, option_root_id: int):
    option_chain = self.get_option_chain(option_root_id)
    if option_chain and "OptionSpace" in option_chain:  # ✅ CORRECT
        return option_chain["OptionSpace"]
```

---

### Why Bot Gets 404 Errors

Looking at bot logs:
```
ERROR | saxo_client | API request failed: 404 - {"ErrorCode":"IllegalInstrumentId","Message":"Instrument Not Found"}
```

**Root Cause**: The endpoint `/ref/v1/instruments/contractoptionspaces` expects:
```
GET /ref/v1/instruments/contractoptionspaces/309
```

But bot is calling:
```
GET /ref/v1/instruments/contractoptionspaces?UnderlyingUic=36590&AssetType=StockOption
```

This returns 404 because:
1. Missing OptionRootId in path
2. Using invalid query parameters
3. Saxo doesn't know which option root you want (SPY might have multiple option types)

---

## 3. Order Placement - How to Trade Options

### Correct Order Structure for StockOption

**Endpoint**: `POST /trade/v2/orders`

**Request Body**:
```json
{
  "AccountKey": "HHzaFvDVAVCg3hi3QUvbNg==",
  "Uic": 4061136,           // ← Option UIC from contractoptionspaces
  "AssetType": "StockOption",
  "Amount": 1,              // Number of contracts (usually 1)
  "BuySell": "Buy",         // "Buy" or "Sell"
  "OrderType": "Limit",     // "Market", "Limit", "Stop", "StopLimit"
  "OrderPrice": 2.50,       // Required for Limit orders
  "ToOpenClose": "Open",    // "Open" = establish position, "Close" = exit position
  "ManualOrder": true,
  "OrderDuration": {
    "DurationType": "DayOrder"  // "DayOrder", "GoodTillCancel", "GoodTillDate"
  }
}
```

**Required Fields**:
- ✅ `AccountKey`
- ✅ `Uic` (the option's UIC, not underlying's)
- ✅ `AssetType` (must be "StockOption")
- ✅ `BuySell`
- ✅ `Amount`
- ✅ `OrderType`
- ✅ `OrderDuration`
- ✅ `OrderPrice` (required for Limit orders)

**Optional but Important**:
- `ToOpenClose`: Specifies whether opening or closing position (options-specific)
- `ManualOrder`: Set to true for non-algorithmic orders

**What Bot Currently Does**: Unknown - needs review of order placement code

---

## 4. Complete Workflow Example

### Scenario: Buy SPY ATM Straddle 90 DTE

```python
# Step 1: Get SPY's OptionRootId
response = client._make_request("GET", "/ref/v1/instruments/details", params={"Uics": 36590})
option_root = response["Data"][0]["RelatedOptionRootsEnhanced"][0]
option_root_id = option_root["OptionRootId"]  # e.g., 309

# Step 2: Get all available expiries and strikes
options_chain = client._make_request(
    "GET",
    f"/ref/v1/instruments/contractoptionspaces/{option_root_id}"
)

# Step 3: Find expiry ~90 days out
today = datetime.now().date()
target_expiry = None

for expiry in options_chain["OptionSpace"]:
    expiry_date = datetime.fromisoformat(expiry["Expiry"][:10]).date()
    dte = (expiry_date - today).days

    if 85 <= dte <= 95:  # 90 DTE ± 5 days
        target_expiry = expiry
        break

# Step 4: Find ATM strike
spy_price = 450.00  # Current SPY price
atm_strike = None
min_diff = float('inf')

for option in target_expiry["SpecificOptions"]:
    strike = option["Strike"]
    diff = abs(strike - spy_price)

    if diff < min_diff and option["PutCall"] == "Call":
        min_diff = diff
        call_uic = option["Uic"]
        atm_strike = strike

# Find matching Put
for option in target_expiry["SpecificOptions"]:
    if option["Strike"] == atm_strike and option["PutCall"] == "Put":
        put_uic = option["Uic"]
        break

# Step 5: Get prices for call and put
call_quote = client.get_quote(call_uic, asset_type="StockOption")
put_quote = client.get_quote(put_uic, asset_type="StockOption")

call_price = call_quote["Quote"]["Ask"]
put_price = put_quote["Quote"]["Ask"]

# Step 6: Place orders (if not dry-run)
call_order = {
    "AccountKey": "HHzaFvDVAVCg3hi3QUvbNg==",
    "Uic": call_uic,
    "AssetType": "StockOption",
    "Amount": 1,
    "BuySell": "Buy",
    "OrderType": "Limit",
    "OrderPrice": call_price,
    "ToOpenClose": "Open",
    "ManualOrder": true,
    "OrderDuration": {"DurationType": "DayOrder"}
}

put_order = {
    "AccountKey": "HHzaFvDVAVCg3hi3QUvbNg==",
    "Uic": put_uic,
    "AssetType": "StockOption",
    "Amount": 1,
    "BuySell": "Buy",
    "OrderType": "Limit",
    "OrderPrice": put_price,
    "ToOpenClose": "Open",
    "ManualOrder": true,
    "OrderDuration": {"DurationType": "DayOrder"}
}

# Submit orders
call_response = client._make_request("POST", "/trade/v2/orders", json=call_order)
put_response = client._make_request("POST", "/trade/v2/orders", json=put_order)
```

---

## 5. Code Changes Required

### File: [saxo_client.py](saxo_client.py)

#### A. Add method to get OptionRootId

**Location**: After `get_quote()` method (~line 661)

```python
def get_option_root_id(self, underlying_uic: int) -> Optional[int]:
    """
    Get the OptionRootId for an underlying instrument.

    This is required before fetching options chains.

    Args:
        underlying_uic: UIC of the underlying (e.g., 36590 for SPY)

    Returns:
        int: OptionRootId, or None if not found
    """
    endpoint = "/ref/v1/instruments/details"
    params = {"Uics": underlying_uic}

    response = self._make_request("GET", endpoint, params=params)

    if not response or "Data" not in response or len(response["Data"]) == 0:
        logger.error(f"No instrument details found for UIC {underlying_uic}")
        return None

    instrument = response["Data"][0]
    related_options = instrument.get("RelatedOptionRootsEnhanced", [])

    # Look for StockOption type
    for option_root in related_options:
        if option_root.get("AssetType") == "StockOption":
            option_root_id = option_root.get("OptionRootId")
            logger.info(f"Found OptionRootId {option_root_id} for UIC {underlying_uic}")
            return option_root_id

    logger.error(f"No StockOption root found for UIC {underlying_uic}")
    return None
```

---

#### B. Fix get_option_chain() method

**Current Code** ([saxo_client.py:662-687](saxo_client.py#L662-L687)):
```python
def get_option_chain(
    self,
    underlying_uic: int,
    expiry_date: Optional[str] = None
) -> Optional[Dict]:
    endpoint = "/ref/v1/instruments/contractoptionspaces"
    params = {
        "UnderlyingUic": underlying_uic,
        "AssetType": "StockOption"
    }

    response = self._make_request("GET", endpoint, params=params)
    if response:
        logger.debug(f"Got option chain for underlying UIC {underlying_uic}")
        return response
    return None
```

**Fixed Code**:
```python
def get_option_chain(
    self,
    option_root_id: int,
    expiry_dates: Optional[List[str]] = None,
    option_space_segment: str = "AllDates"
) -> Optional[Dict]:
    """
    Get option chain for an OptionRootId.

    Args:
        option_root_id: Option root ID (get from get_option_root_id())
        expiry_dates: Optional list of specific expiry dates ["2024-02-16", ...]
        option_space_segment: "AllDates" (default) or "SpecificDates"

    Returns:
        dict: OptionSpace array with expiries and strikes
    """
    # OptionRootId goes in the URL path
    endpoint = f"/ref/v1/instruments/contractoptionspaces/{option_root_id}"

    params = {}

    if expiry_dates:
        params["OptionSpaceSegment"] = "SpecificDates"
        params["ExpiryDates"] = expiry_dates
    elif option_space_segment:
        params["OptionSpaceSegment"] = option_space_segment

    response = self._make_request("GET", endpoint, params=params)

    if response:
        logger.debug(f"Got option chain for OptionRootId {option_root_id}")
        return response

    logger.error(f"Failed to get option chain for OptionRootId {option_root_id}")
    return None
```

---

#### C. Fix get_option_expirations() method

**Current Code** ([saxo_client.py:689-702](saxo_client.py#L689-L702)):
```python
def get_option_expirations(self, underlying_uic: int) -> Optional[List[Dict]]:
    option_chain = self.get_option_chain(underlying_uic)
    if option_chain and "Data" in option_chain:
        return option_chain["Data"]
    return None
```

**Fixed Code**:
```python
def get_option_expirations(self, underlying_uic: int) -> Optional[List[Dict]]:
    """
    Get available option expiration dates for an underlying.

    Args:
        underlying_uic: UIC of the underlying instrument (e.g., 36590 for SPY)

    Returns:
        list: OptionSpace array with expiries
    """
    # Step 1: Get OptionRootId
    option_root_id = self.get_option_root_id(underlying_uic)
    if not option_root_id:
        logger.error(f"Could not find OptionRootId for UIC {underlying_uic}")
        return None

    # Step 2: Get option chain
    option_chain = self.get_option_chain(option_root_id)

    # Step 3: Extract OptionSpace (not "Data")
    if option_chain and "OptionSpace" in option_chain:
        return option_chain["OptionSpace"]

    logger.error(f"No OptionSpace found in response for OptionRootId {option_root_id}")
    return None
```

---

#### D. Fix find_atm_options() method

**Current Code** ([saxo_client.py:749-782](saxo_client.py#L749-L782)) expects:
```python
strikes = target_expiration.get("Strikes", [])  # ❌ Wrong field name

for strike_data in strikes:
    strike_price = strike_data.get("Strike", 0)
    # ...
    atm_strike = strike_data

return {
    "call": {
        "uic": atm_strike.get("CallUic"),  # ❌ Wrong - should be option["Uic"]
        "strike": atm_strike.get("Strike")
    }
}
```

**Fixed Code**:
```python
def find_atm_options(
    self,
    underlying_uic: int,
    underlying_price: float,
    target_dte_min: int,
    target_dte_max: int
) -> Optional[Dict[str, Dict]]:
    """Find ATM call and put options."""

    expirations = self.get_option_expirations(underlying_uic)
    if not expirations:
        logger.error("Failed to get option expirations")
        return None

    # Find expiration within target DTE range
    today = datetime.now().date()
    target_expiration = None

    for exp_data in expirations:
        exp_date_str = exp_data.get("Expiry")
        if not exp_date_str:
            continue

        exp_date = datetime.fromisoformat(exp_date_str[:10]).date()
        dte = (exp_date - today).days

        if target_dte_min <= dte <= target_dte_max:
            target_expiration = exp_data
            logger.info(f"Found expiration: {exp_date_str} with {dte} DTE")
            break

    if not target_expiration:
        logger.warning(f"No expiration found within {target_dte_min}-{target_dte_max} DTE range")
        return None

    # Get strikes for this expiration (SpecificOptions array, not "Strikes")
    specific_options = target_expiration.get("SpecificOptions", [])

    if not specific_options:
        logger.error("No SpecificOptions in target expiration")
        return None

    # Find ATM strike (closest to current price)
    atm_strike_price = None
    min_diff = float('inf')

    # First pass: find closest strike price
    for option in specific_options:
        strike_price = option.get("Strike", 0)
        diff = abs(strike_price - underlying_price)
        if diff < min_diff:
            min_diff = diff
            atm_strike_price = strike_price

    if atm_strike_price is None:
        logger.error("Failed to find ATM strike")
        return None

    logger.info(f"ATM strike: {atm_strike_price} (underlying: {underlying_price})")

    # Second pass: find Call and Put UICs at ATM strike
    call_uic = None
    put_uic = None

    for option in specific_options:
        if option.get("Strike") == atm_strike_price:
            if option.get("PutCall") == "Call":
                call_uic = option.get("Uic")
            elif option.get("PutCall") == "Put":
                put_uic = option.get("Uic")

    if not call_uic or not put_uic:
        logger.error(f"Failed to find Call or Put UIC at strike {atm_strike_price}")
        return None

    return {
        "call": {
            "uic": call_uic,
            "strike": atm_strike_price,
            "expiry": target_expiration.get("Expiry"),
            "option_type": "Call"
        },
        "put": {
            "uic": put_uic,
            "strike": atm_strike_price,
            "expiry": target_expiration.get("Expiry"),
            "option_type": "Put"
        }
    }
```

---

#### E. Fix find_strangle_options() method

**Similar changes needed**:
1. Use `SpecificOptions` instead of `Strikes`
2. Match options by `Strike` and `PutCall` fields
3. Extract `Uic` from option objects

---

## 6. Testing Plan

### Phase 1: Test OptionRootId Retrieval
```bash
python -c "
from saxo_client import SaxoClient
import json

config = json.load(open('config.json'))
config['saxo_api']['environment'] = 'live'

client = SaxoClient(config)
client.authenticate()

# Test: Get SPY's OptionRootId
root_id = client.get_option_root_id(36590)
print(f'SPY OptionRootId: {root_id}')
"
```

**Expected Output**:
```
SPY OptionRootId: 309
```

---

### Phase 2: Test Options Chain Retrieval
```bash
python -c "
# ... (same setup as above)

root_id = client.get_option_root_id(36590)
chain = client.get_option_chain(root_id)

print(f'Number of expiries: {len(chain[\"OptionSpace\"])}')
print(f'First expiry: {chain[\"OptionSpace\"][0][\"Expiry\"]}')
"
```

**Expected Output**:
```
Number of expiries: 50
First expiry: 2024-01-19T00:00:00Z
```

---

### Phase 3: Test Full ATM Option Lookup
```bash
python main.py --live --dry-run
```

**Watch logs for**:
```
INFO | saxo_client | Found OptionRootId 309 for UIC 36590
INFO | saxo_client | Got option chain for OptionRootId 309
INFO | saxo_client | Found expiration: 2024-04-19T00:00:00Z with 92 DTE
INFO | saxo_client | ATM strike: 450.0 (underlying: 449.87)
INFO | strategy | Opening long straddle...
```

---

## 7. Summary of Issues

| Issue | Current State | Root Cause | Impact |
|-------|---------------|------------|--------|
| **Market Data NoAccess** | ✅ Workaround via external feed | No Saxo API subscription | Can trade but with 15-min delayed prices |
| **Options Chain 404** | ❌ **BLOCKING** | Wrong endpoint usage | Cannot retrieve option chains |
| **Missing OptionRootId** | ❌ **BLOCKING** | Skipped required step | Cannot access contractoptionspaces |
| **Wrong Response Parsing** | ❌ **BLOCKING** | Expects "Data" not "OptionSpace" | Crashes when parsing response |
| **Strike/UIC Extraction** | ❌ **BLOCKING** | Expects "Strikes" not "SpecificOptions" | Cannot find option contracts |

---

## 8. Recommended Actions

### Immediate (Code Fixes):
1. ✅ Implement `get_option_root_id()` method
2. ✅ Fix `get_option_chain()` to use OptionRootId in path
3. ✅ Fix `get_option_expirations()` to parse "OptionSpace"
4. ✅ Fix `find_atm_options()` to use "SpecificOptions"
5. ✅ Fix `find_strangle_options()` similarly
6. ✅ Test with `--live --dry-run`

### Long-term (Saxo Support):
1. Contact Saxo to enable US Equity market data API subscription
2. Request US Options market data API subscription
3. This will remove need for Yahoo Finance fallback
4. Get real-time prices instead of 15-min delayed

---

## 9. Key Documentation Links

- **Pricing Overview**: https://www.developer.saxo/openapi/learn/pricing
- **Options Chain Tutorial**: https://www.developer.saxo/openapi/learn/options-chain
- **Contract Option Spaces**: https://www.developer.saxo/openapi/referencedocs/ref/v1/instruments/contractoptionspaces
- **Order Placement**: https://www.developer.saxo/openapi/referencedocs/trade/v2/orders
- **InfoPrices Endpoint**: https://www.developer.saxo/openapi/referencedocs/trade/v1/infoprices
- **GitHub Samples (Options)**: https://saxobank.github.io/openapi-samples-js/orders/options/
- **Support Article - OptionRootId**: https://openapi.help.saxo/hc/en-us/articles/4417056831633

---

## Conclusion

**The bot CAN work in LIVE mode** once these code fixes are applied. The 404 errors are due to incorrect API usage, not account permissions. After fixing the code:

1. Options chains will load successfully ✅
2. Bot can find ATM straddles and strangles ✅
3. Prices will come from Yahoo Finance (delayed) ⚠️
4. Orders can be placed (in dry-run for testing) ✅

To get **real-time** market data, you must contact Saxo support separately.
