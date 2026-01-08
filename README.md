# Delta Neutral Trading Bot

A fully automated Python trading bot that implements a delta-neutral options strategy on SPY using the Saxo Bank OpenAPI.

---

## 🎯 Quick Start (Simple Overview)

### What Does This Bot Do?

This bot automatically trades SPY options using a "delta neutral" strategy to profit from volatility while staying market-neutral:

1. **Buys protection** - Long straddles (Call + Put at the same strike)
2. **Generates income** - Sells weekly strangles (Call + Put further out)
3. **Manages risk** - Automatically recenters when the market moves too far
4. **Repeats weekly** - Rolls income positions every week

### How to Run It

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Edit config.json with your API keys
# (See detailed setup below)

# 3. Test in simulation mode
python main.py --dry-run

# 4. Run live (when ready)
python main.py
```

---

## 📚 Detailed Overview

### The Strategy Explained

This bot implements the **Delta Neutral Strategy with 5-Point Recentering** popularized in options trading communities:

#### Phase 1: Entry (VIX Filter)
- **When**: Only when VIX < 18 (low volatility environment)
- **What**: Buy 1 ATM Call + 1 ATM Put on SPY with 90-120 days to expiration
- **Why**: This creates a "long straddle" that profits from big moves in either direction

#### Phase 2: Income Generation
- **What**: Sell weekly Call + Put at 1.5-2x the expected weekly move
- **Why**: Collect premium while the market stays range-bound
- **Result**: Offset the cost of the long straddle over time

#### Phase 3: The "5-Point Recenter" Rule
- **Trigger**: SPY moves 5+ points from your initial strike
- **Action**:
  1. Close the current long straddle
  2. Open a new ATM long straddle (same expiration)
  3. Close and reset the weekly shorts
- **Why**: Maintains delta neutrality and locks in profits

#### Phase 4: Weekly Management
- **Rolling**: Every Thursday/Friday, close the weekly shorts and open new ones
- **Exit**: When 30-60 DTE remains on the long straddle, close everything and start over

### Why This Strategy Works

1. **Market Neutral** - Makes money whether markets go up or down (as long as they move)
2. **Income Generation** - Weekly premium collection offsets the cost of protection
3. **Automatic Risk Management** - Recentering keeps exposure balanced
4. **Time Decay Control** - Long options decay slower than short options

---

## 🏗️ Architecture & Code Structure

### Module Breakdown

```
Calypso/
├── main.py                 # Entry point - orchestrates everything
├── saxo_client.py          # API communication layer
├── strategy.py             # Trading logic and decision-making
├── logger_service.py       # Trade logging and monitoring
├── config.json             # Your credentials and settings
└── requirements.txt        # Python dependencies
```

### How the Modules Connect

```
┌─────────────────────────────────────────────────────────────┐
│                          main.py                            │
│                    (The Orchestrator)                       │
│  - Loads config                                             │
│  - Initializes all components                               │
│  - Runs the main loop                                       │
│  - Handles shutdown                                         │
└────────┬───────────────────────────────────┬────────────────┘
         │                                   │
         │ Creates                           │ Creates
         ▼                                   ▼
┌──────────────────────┐          ┌─────────────────────────┐
│   saxo_client.py     │          │   logger_service.py     │
│   (API Interface)    │          │   (Trade Recorder)      │
│                      │          │                         │
│ - OAuth2 auth        │          │ - Local file logs       │
│ - REST API calls     │          │ - Google Sheets         │
│ - WebSocket stream   │          │ - Microsoft Excel       │
│ - Order execution    │          │ - Status reports        │
│ - Circuit breaker    │          │                         │
└──────────┬───────────┘          └─────────────────────────┘
           │                                   ▲
           │ Used by                           │
           │                                   │ Logs to
           ▼                                   │
┌─────────────────────────────────────────────┴───────────────┐
│                      strategy.py                            │
│                  (Trading Brain)                            │
│                                                             │
│ - Checks VIX conditions                                     │
│ - Finds ATM options                                         │
│ - Places straddle/strangle orders                          │
│ - Monitors for 5-point moves                               │
│ - Executes recentering                                      │
│ - Manages rolling and exits                                │
└─────────────────────────────────────────────────────────────┘
```

### Detailed Module Functions

#### 1. **main.py** - The Entry Point

**What it does:**
- Parses command-line arguments (`--dry-run`, `--status`, etc.)
- Loads and validates configuration
- Initializes the SaxoClient, Strategy, and Logger
- Runs the main loop that checks conditions every N seconds
- Handles graceful shutdown (CTRL+C)

**Key Functions:**
- `load_config()` - Reads config.json
- `validate_config()` - Checks for required credentials
- `run_bot()` - Main trading loop
- `show_status()` - Display current positions without trading

**Main Loop Flow:**
```python
while not shutdown_requested:
    1. Check circuit breaker (is trading safe?)
    2. Run strategy check (strategy.run_strategy_check())
    3. Log status periodically
    4. Sleep for check_interval seconds
    5. Repeat
```

---

#### 2. **saxo_client.py** - The API Layer

**What it does:**
- Handles ALL communication with Saxo Bank's API
- Manages authentication tokens
- Places orders and retrieves market data
- Streams real-time prices via WebSocket
- Implements circuit breaker safety pattern

**Key Classes:**

##### `SaxoClient`
The main client that talks to Saxo's API.

**Authentication Methods:**
- `authenticate()` - Main auth entry point
- `_oauth_authorization_flow()` - Opens browser for user login
- `_exchange_code_for_token()` - Converts auth code to access token
- `_refresh_access_token()` - Renews expired tokens

**Market Data Methods:**
- `get_quote(uic)` - Get current price for an instrument
- `get_option_chain(underlying_uic)` - Get all available options
- `find_atm_options()` - Find Call/Put at current market price
- `find_strangle_options()` - Find OTM options for income
- `get_vix_price()` - Check volatility level
- `check_bid_ask_spread()` - Slippage protection

**Trading Methods:**
- `place_order()` - Single order execution
- `place_multi_leg_order()` - Straddle/Strangle execution
- `get_positions()` - See what you currently own
- `close_position()` - Exit a position
- `cancel_order()` - Cancel pending orders

**Streaming Methods:**
- `start_price_streaming()` - Real-time price updates via WebSocket
- `stop_price_streaming()` - Clean shutdown

**Circuit Breaker:**
- Tracks consecutive errors
- Opens circuit after 3 errors OR 60s disconnection
- Blocks all trading for 15 minutes cooldown
- Automatically closes when safe

---

#### 3. **strategy.py** - The Trading Brain

**What it does:**
- Implements the complete delta neutral strategy logic
- Makes all trading decisions
- Tracks positions and P&L
- Executes the 5-point recentering rule

**Key Classes:**

##### `DeltaNeutralStrategy`
The core strategy implementation.

**State Machine:**
The strategy operates in different states:
- `IDLE` - No positions, waiting to enter
- `WAITING_VIX` - Market conditions not met (VIX too high)
- `LONG_STRADDLE_ACTIVE` - Long straddle entered, need shorts
- `FULL_POSITION` - Complete position with longs + shorts
- `RECENTERING` - Executing 5-point recenter
- `ROLLING_SHORTS` - Rolling weekly income positions
- `EXITING` - Closing everything

**Key Methods:**

**Market Data:**
- `update_market_data()` - Refresh SPY and VIX prices
- `handle_price_update()` - Process WebSocket updates
- `check_vix_entry_condition()` - Is VIX < 18?

**Position Entry:**
- `enter_long_straddle()` - Buy ATM Call + Put (90-120 DTE)
- `enter_short_strangle()` - Sell OTM Call + Put (weekly)
- `close_long_straddle()` - Exit long positions
- `close_short_strangle()` - Exit short positions

**The 5-Point Recenter:**
- `_check_recenter_condition()` - Monitor for 5+ point move
- `execute_recenter()` - Full recenter sequence:
  1. Close shorts
  2. Close long straddle
  3. Open new ATM long straddle
  4. Open new shorts

**Management:**
- `should_roll_shorts()` - Check if Thursday/Friday OR challenged
- `roll_weekly_shorts()` - Close old shorts, open new ones
- `should_exit_trade()` - Check if 30-60 DTE on longs
- `exit_all_positions()` - Close everything

**Main Strategy Loop:**
- `run_strategy_check()` - Called every minute by main.py
  - This is the "brain" that decides what to do based on current state

**Data Structures:**
- `StraddlePosition` - Tracks the long straddle (call + put)
- `StranglePosition` - Tracks the short strangle (call + put)
- `StrategyMetrics` - Running P&L, recenter count, etc.

---

#### 4. **logger_service.py** - The Recorder

**What it does:**
- Logs every trade to multiple destinations
- Records system events and errors
- Provides status reporting
- Runs asynchronously (doesn't slow down trading)

**Key Classes:**

##### `TradeLoggerService`
Main logging orchestrator.

**Logging Destinations:**
1. **Local Files** (Always enabled)
   - `bot_log.txt` - All events, errors, status
   - `bot_log_trades.json` - Trade history in JSON

2. **Google Sheets** (Optional)
   - Real-time spreadsheet updates
   - Every trade instantly logged
   - Easy to view from mobile

3. **Microsoft Excel** (Optional)
   - SharePoint/OneDrive integration
   - Enterprise-friendly

**Key Methods:**
- `log_trade()` - Record a trade execution
- `log_event()` - General system event
- `log_error()` - Error with stack trace
- `log_status()` - Pretty-printed strategy status

**Trade Record Format:**
```
[Timestamp, Action, Strike, Price, Delta, P&L]

Example:
2024-01-15 10:30:00, OPEN_LONG_STRADDLE, 450, 15.50, 0.00, 0.00
2024-01-15 10:35:00, OPEN_SHORT_STRANGLE, 440/460, 2.50, -0.05, 250.00
2024-01-18 14:20:00, RECENTER, 455, 455.00, 0.02, 450.00
```

---

## ⚙️ Configuration Setup (CRITICAL!)

### Step-by-Step Setup Guide

#### 1. Saxo Bank API Credentials

You need a Saxo Bank developer account to use this bot.

**Get Your Credentials:**

1. **Create a Saxo Developer Account:**
   - Go to https://www.developer.saxo/
   - Sign up for a developer account
   - Verify your email

2. **Create an Application:**
   - Log in to the Developer Portal
   - Click "Create New App"
   - Choose "Open API" application type
   - Set redirect URI to: `http://localhost:8080/callback`
   - Note your **App Key** and **App Secret**

3. **Get Account Keys:**
   - In the developer portal, go to your account section
   - Note your **Account Key** (like: `Ab12Cd34Ef56`)
   - Note your **Client Key** (like: `Ab12Cd34Ef56`)

4. **Update config.json:**
   ```json
   {
     "saxo_api": {
       "app_key": "YOUR_APP_KEY_HERE",
       "app_secret": "YOUR_APP_SECRET_HERE",
       "environment": "sim"
     },
     "account": {
       "account_key": "YOUR_ACCOUNT_KEY_HERE",
       "client_key": "YOUR_CLIENT_KEY_HERE"
     }
   }
   ```

5. **First Run Authentication:**
   - The first time you run the bot, it will:
     1. Open your browser for login
     2. You'll authorize the app
     3. It will automatically exchange the code for tokens
     4. Tokens are used automatically for future runs

**Important Notes:**
- **ALWAYS USE SIMULATION FIRST** (`"environment": "sim"`)
- The simulation account gives you virtual money to test with
- Never commit config.json to git (it's in .gitignore)
- Tokens expire - the bot will auto-refresh them

---

#### 2. Understanding UICs (Unique Instrument Codes)

Saxo uses "UICs" instead of ticker symbols. You need the correct UICs for your account.

**Finding UICs:**

Use the Saxo API to search:

```python
# Quick script to find UICs
from saxo_client import SaxoClient
import json

with open('config.json') as f:
    config = json.load(f)

client = SaxoClient(config)
client.authenticate()

# Search for SPY
spy = client.search_instrument("SPY", "Etf")
print(f"SPY UIC: {spy['Identifier']}")

# Search for VIX
vix = client.search_instrument("VIX", "CfdOnIndex")
print(f"VIX UIC: {vix['Identifier']}")
```

**Update config.json:**
```json
{
  "strategy": {
    "underlying_symbol": "SPY",
    "underlying_uic": 211,  // Your actual SPY UIC
    "vix_symbol": "VIX",
    "vix_uic": 19217  // Your actual VIX UIC
  }
}
```

**Common UICs (Verify these in your environment!):**
- SPY (ETF): Usually 211
- VIX (Index): Usually 19217
- QQQ (ETF): Usually 226
- IWM (ETF): Usually 258

---

#### 3. Strategy Parameters (Fine-Tuning)

These control how the bot trades. Default values are based on the video strategy.

```json
{
  "strategy": {
    // VIX Filter - only enter when VIX is below this
    "max_vix_entry": 18.0,

    // Long Straddle Expiration (90-120 days out)
    "long_straddle_min_dte": 90,
    "long_straddle_max_dte": 120,

    // Recenter Trigger (5 points from initial strike)
    "recenter_threshold_points": 5.0,

    // Short Strangle Distance (1.5-2x expected move)
    "weekly_strangle_multiplier_min": 1.5,
    "weekly_strangle_multiplier_max": 2.0,

    // Exit Timing (close everything at 30-60 DTE)
    "exit_dte_min": 30,
    "exit_dte_max": 60,

    // Roll Days (when to roll weekly shorts)
    "roll_days": ["Thursday", "Friday"],

    // Slippage Protection
    "max_bid_ask_spread_percent": 0.5,

    // Position Size (contracts)
    "position_size": 1
  }
}
```

**Tuning Tips:**
- **Lower VIX threshold (16)** = More conservative entry
- **Higher VIX threshold (20)** = More opportunities, more risk
- **Smaller recenter (3 points)** = More frequent recentering
- **Larger recenter (7 points)** = Let winners run longer
- **Higher multiplier (2.5x)** = Safer shorts, less premium
- **Lower multiplier (1.2x)** = More premium, more risk

---

#### 4. Circuit Breaker Settings (Safety)

Protects you from API issues and cascading failures.

```json
{
  "circuit_breaker": {
    // Stop trading after 3 consecutive API errors
    "max_consecutive_errors": 3,

    // Stop trading if disconnected for 60 seconds
    "max_disconnection_seconds": 60,

    // How long to wait before retrying (15 minutes)
    "cooldown_minutes": 15
  }
}
```

**What triggers the circuit breaker:**
- 3 failed API calls in a row
- WebSocket disconnection for 60+ seconds
- Authentication failures

**When it opens:**
- All trading stops immediately
- Bot continues running but won't place orders
- After cooldown, automatically retries

---

#### 5. Google Sheets Integration (Optional)

Log trades to a Google Spreadsheet for easy monitoring.

**Setup Steps:**

1. **Create a Google Cloud Project:**
   - Go to https://console.cloud.google.com/
   - Create a new project: "Trading Bot"

2. **Enable APIs:**
   - Enable "Google Sheets API"
   - Enable "Google Drive API"

3. **Create Service Account:**
   - Go to "IAM & Admin" → "Service Accounts"
   - Click "Create Service Account"
   - Name: "trading-bot"
   - Grant role: "Editor"
   - Click "Create Key" → JSON
   - Download the JSON file

4. **Save Credentials:**
   - Rename downloaded file to `google_credentials.json`
   - Put it in your Calypso folder (same directory as main.py)

5. **Share Spreadsheet:**
   - Create a Google Sheet named "Trading_Bot_Log"
   - Open the `google_credentials.json` file
   - Find the `client_email` field (looks like: `trading-bot@project.iam.gserviceaccount.com`)
   - Share your spreadsheet with that email address (Editor access)

6. **Update config.json:**
   ```json
   {
     "google_sheets": {
       "enabled": true,
       "credentials_file": "google_credentials.json",
       "spreadsheet_name": "Trading_Bot_Log",
       "worksheet_name": "Trades"
     }
   }
   ```

7. **Test it:**
   ```bash
   python main.py --dry-run
   # Check your Google Sheet - you should see trades logged
   ```

---

#### 6. Microsoft Excel/SharePoint Integration (Optional)

For enterprise users who prefer Microsoft ecosystem.

**Setup Steps:**

1. **Azure AD App Registration:**
   - Go to https://portal.azure.com/
   - Navigate to "Azure Active Directory"
   - Click "App registrations" → "New registration"
   - Name: "Trading Bot"
   - Supported account types: "Single tenant"
   - Register

2. **Configure App:**
   - Go to "API permissions"
   - Add "Microsoft Graph" → "Sites.ReadWrite.All"
   - Add "Microsoft Graph" → "Files.ReadWrite.All"
   - Grant admin consent

3. **Get Credentials:**
   - Go to "Certificates & secrets"
   - Create new client secret
   - Copy the **Value** (this is your client_secret)
   - Copy the **Application (client) ID** (this is your client_id)
   - Copy your **Directory (tenant) ID** (this is your tenant_id)

4. **Create Excel Workbook:**
   - Create Excel file: "Trading_Bot_Log.xlsx"
   - Add worksheet: "Trades"
   - Upload to SharePoint or OneDrive
   - Note the site URL

5. **Update config.json:**
   ```json
   {
     "microsoft_sheets": {
       "enabled": true,
       "client_id": "your-client-id",
       "client_secret": "your-client-secret",
       "tenant_id": "your-tenant-id",
       "site_url": "https://yourtenant.sharepoint.com/sites/yoursite",
       "workbook_name": "Trading_Bot_Log.xlsx",
       "worksheet_name": "Trades"
     }
   }
   ```

---

#### 7. Logging Settings

Control what gets logged and where.

```json
{
  "logging": {
    // Log file name
    "log_file": "bot_log.txt",

    // Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
    "log_level": "INFO",

    // Also print to console?
    "console_output": true
  }
}
```

**Log Levels:**
- **DEBUG** - Everything (very verbose, use for troubleshooting)
- **INFO** - Normal operations (recommended)
- **WARNING** - Issues that aren't errors
- **ERROR** - Errors that don't stop the bot
- **CRITICAL** - Fatal errors

**Log Files Created:**
- `bot_log.txt` - All events, timestamped
- `bot_log_trades.json` - Trade history in JSON format

---

## 🚀 Running the Bot

### Command Line Options

```bash
# Show help
python main.py --help

# Run in dry-run mode (no real trades)
python main.py --dry-run

# Check current status without trading
python main.py --status

# Use custom config file
python main.py --config production.json

# Change check interval (default: 60 seconds)
python main.py --interval 30

# Verbose output (debug mode)
python main.py --verbose

# Combine options
python main.py --config prod.json --interval 30 --verbose
```

### First Time Setup Checklist

1. ✅ Install Python 3.8+ (check with `python --version`)
2. ✅ Install dependencies: `pip install -r requirements.txt`
3. ✅ Copy `config.example.json` to `config.json`
4. ✅ Add your Saxo API credentials to config.json
5. ✅ Verify UICs for SPY and VIX
6. ✅ Set `"environment": "sim"` for paper trading
7. ✅ (Optional) Setup Google Sheets
8. ✅ Test authentication: `python main.py --status`
9. ✅ Test dry-run: `python main.py --dry-run`
10. ✅ Monitor logs in `bot_log.txt`

### Testing Before Live Trading

**Phase 1: Status Check**
```bash
python main.py --status
```
- Verifies authentication works
- Shows current VIX level
- Displays any existing positions

**Phase 2: Dry Run**
```bash
python main.py --dry-run
```
- Simulates trading decisions without placing orders
- Logs what it would do
- Safe to run anytime

**Phase 3: Paper Trading**
```bash
# Make sure config.json has:
# "environment": "sim"

python main.py
```
- Uses Saxo's simulation environment
- Places real orders in paper account
- Full functionality with fake money

**Phase 4: Live Trading** (Only when confident!)
```bash
# Change config.json to:
# "environment": "live"

python main.py
```
- Real money, real trades
- Start with position_size: 1
- Monitor closely for first week

---

## 📊 Monitoring the Bot

### Real-Time Monitoring

**Console Output:**
```
2024-01-15 10:30:00 | INFO     | strategy           | VIX entry condition MET: 16.50 < 18
2024-01-15 10:30:05 | INFO     | strategy           | Long straddle entered: Strike 450, Expiry 2024-04-19
2024-01-15 10:30:12 | INFO     | strategy           | Short strangle entered: Put 440 / Call 460
2024-01-15 10:30:12 | INFO     | main               | ACTION: Entered long straddle and short strangle
```

**Status Reports** (Every 5 minutes):
```
============================================================
STRATEGY STATUS
============================================================
  State: FullPosition
  SPY Price: $452.35
  VIX: 16.20
  Initial Strike: $450.00
  Distance from Strike: $2.35
  Long Straddle: Active
  Short Strangle: Active
  Total Delta: 0.0523
  Total P&L: $450.00
  Realized P&L: $150.00
  Unrealized P&L: $300.00
  Premium Collected: $250.00
  Recenter Count: 0
  Roll Count: 2
============================================================
```

### Log Files

**bot_log.txt** - Timestamped events:
```
2024-01-15 10:30:00 | INFO  | main | Trading bot starting...
2024-01-15 10:30:02 | INFO  | saxo | Authentication successful
2024-01-15 10:30:05 | INFO  | strategy | Checking VIX condition...
```

**bot_log_trades.json** - Trade history:
```json
[
  {
    "timestamp": "2024-01-15T10:30:00",
    "action": "OPEN_LONG_STRADDLE",
    "strike": 450,
    "price": 15.50,
    "delta": 0.00,
    "pnl": 0.00
  },
  {
    "timestamp": "2024-01-15T10:35:00",
    "action": "OPEN_SHORT_STRANGLE",
    "strike": "440/460",
    "price": 2.50,
    "delta": -0.05,
    "pnl": 250.00
  }
]
```

### Google Sheets Dashboard

If enabled, your spreadsheet shows:

| Timestamp | Action | Strike | Price | Delta | P&L |
|-----------|--------|--------|-------|-------|-----|
| 2024-01-15 10:30:00 | OPEN_LONG_STRADDLE | 450 | 15.50 | 0.00 | 0.00 |
| 2024-01-15 10:35:00 | OPEN_SHORT_STRANGLE | 440/460 | 2.50 | -0.05 | 250.00 |
| 2024-01-18 14:20:00 | RECENTER | 455 | 455.00 | 0.02 | 450.00 |

---

## 🔧 Troubleshooting

### Common Issues

#### 1. Authentication Fails

**Error:** `Failed to authenticate. Please check your credentials.`

**Solution:**
- Verify app_key and app_secret are correct
- Check that redirect_uri is exactly: `http://localhost:8080/callback`
- Make sure port 8080 is not blocked
- Try deleting tokens and re-authenticating

#### 2. Can't Find Options

**Error:** `Failed to find ATM options for straddle`

**Solution:**
- Verify UICs are correct for your account
- Check that options are available for SPY
- Ensure you have options trading permissions
- Try different DTE range

#### 3. Bid-Ask Spread Too Wide

**Warning:** `Bid-ask spread 1.2% exceeds threshold 0.5%`

**Solution:**
- Market is illiquid (happens pre-market or low volume)
- Wait for market hours (9:30 AM - 4:00 PM ET)
- Increase `max_bid_ask_spread_percent` in config (risky)
- Try different strikes

#### 4. Circuit Breaker Opens

**Error:** `CIRCUIT BREAKER OPENED! Trading halted for 15 minutes.`

**Solution:**
- This is NORMAL during API issues
- Bot will automatically retry after cooldown
- Check `bot_log.txt` for the cause
- If persistent, check Saxo API status

#### 5. VIX Condition Not Met

**Info:** `VIX entry condition NOT met: 19.50 >= 18`

**Solution:**
- This is normal market behavior
- Bot waits for VIX < 18 before entering
- You can lower the threshold in config (higher risk)
- Be patient - good entries require patience

#### 6. Import Errors

**Error:** `ModuleNotFoundError: No module named 'gspread'`

**Solution:**
```bash
pip install -r requirements.txt
```

If still fails:
```bash
pip install --upgrade pip
pip install -r requirements.txt --force-reinstall
```

#### 7. WebSocket Disconnects

**Warning:** `WebSocket closed: 1006 - Connection lost`

**Solution:**
- This is normal - bot reconnects automatically
- If frequent, check your internet connection
- Circuit breaker protects you during disconnections
- No action needed unless circuit opens

---

## ⚠️ Risk Warnings

### Before You Start

1. **This is REAL money** - Even paper trading teaches bad habits if you don't treat it seriously
2. **Options can expire worthless** - You can lose 100% of premium paid
3. **Naked shorts are dangerous** - But this strategy uses defined risk (long straddle protects you)
4. **Past performance ≠ future results** - Backtest thoroughly
5. **Automation can fail** - Always monitor your bot
6. **API can go down** - Have manual access to close positions
7. **Recentering costs money** - Each recenter has transaction costs

### Position Sizing

Start small:
- **Week 1**: 1 contract, dry-run mode
- **Week 2**: 1 contract, paper trading
- **Week 3**: 1 contract, live trading
- **Month 2**: Scale to 2-3 contracts
- **Month 3+**: Scale based on comfort and results

**Never risk more than:**
- 2% of account per trade
- 10% of account total in options
- Money you can't afford to lose

### Known Limitations

1. **No slippage modeling** - Dry-run assumes fills at mid-price
2. **No commission calculation** - Factor in ~$0.65/contract
3. **No margin requirements** - Ensure you have required capital
4. **No dividend handling** - SPY dividends can affect options
5. **No earnings avoidance** - Strategy doesn't avoid earnings dates
6. **No holiday calendar** - Doesn't handle market holidays
7. **Internet dependent** - Requires stable connection

---

## 🎓 Learning Resources

### Understanding the Strategy

- **Original Video**: https://youtu.be/4bsSnxvfwHY?si=jnlkm0B_YhH8OJaZ
- **Delta Neutral Strategies**: TastyTrade.com
- **Options Greeks**: Khan Academy - Options
- **VIX Explained**: CBOE VIX Whitepaper

### Saxo Bank API

- **Developer Portal**: https://www.developer.saxo/
- **API Documentation**: https://www.developer.saxo/openapi/learn
- **API Reference**: https://www.developer.saxo/openapi/referencedocs

### Python & Trading

- **Python for Finance**: "Python for Finance" by Yves Hilpisch
- **Algorithmic Trading**: "Algorithmic Trading" by Ernest Chan
- **Options Pricing**: "Options, Futures, and Other Derivatives" by John Hull

---

## 📝 Customization Ideas

### Easy Customizations

1. **Change Underlying**
   - Modify UICs in config.json
   - Test with QQQ, IWM, or other ETFs

2. **Adjust Timing**
   - Change `long_straddle_min_dte` and `max_dte`
   - Modify `exit_dte_min` and `max_dte`

3. **Different Roll Days**
   - Change `roll_days` to ["Monday", "Wednesday"]
   - Roll based on DTE instead of day

4. **Multiple Contracts**
   - Increase `position_size` to 2 or 3
   - Consider scaling in/out

### Advanced Customizations

1. **Add Greeks Tracking**
   - Calculate real-time delta, gamma, theta
   - Adjust positions based on Greeks

2. **Dynamic VIX Threshold**
   - Use VIX percentile instead of absolute level
   - Calculate historical VIX average

3. **Machine Learning**
   - Predict optimal entry times
   - Optimize recenter threshold

4. **Multiple Underlyings**
   - Run multiple strategies in parallel
   - Spread risk across SPY, QQQ, IWM

5. **Conditional Orders**
   - Set profit targets
   - Implement stop losses

---

## 🆘 Support & Community

### Getting Help

1. **Check Logs First**
   - Review `bot_log.txt` for errors
   - Check trade history in `bot_log_trades.json`

2. **Common Issues**
   - See Troubleshooting section above

3. **Saxo Support**
   - Developer Portal: https://www.developer.saxo/
   - Support email: OpenAPISupport@saxobank.com

4. **Code Issues**
   - Check each module's docstrings
   - Add `--verbose` flag for detailed logs
   - Use `--dry-run` to test safely

### Development

```bash
# Run tests (if you add them)
pytest tests/

# Format code
black *.py

# Type checking
mypy *.py

# Linting
flake8 *.py
```

---

## 📄 License & Disclaimer

### Disclaimer

**THIS SOFTWARE IS FOR EDUCATIONAL PURPOSES ONLY.**

- The authors are NOT financial advisors
- This is NOT investment advice
- You trade at YOUR OWN RISK
- Past performance does not guarantee future results
- Options trading involves substantial risk of loss
- Only trade with money you can afford to lose

**The authors and contributors:**
- Make NO guarantees of profitability
- Are NOT responsible for your trading losses
- Do NOT provide financial, legal, or tax advice
- Recommend consulting with licensed professionals

**By using this software, you agree:**
- You are solely responsible for your trading decisions
- You understand the risks of automated trading
- You will test thoroughly before live trading
- You will monitor your positions actively
- You accept full liability for any losses

### License

MIT License - Free to use, modify, and distribute with attribution.

---

## 🎯 Quick Reference Card

### File Purposes
- **main.py**: Runs the bot
- **saxo_client.py**: Talks to Saxo API
- **strategy.py**: Makes trading decisions
- **logger_service.py**: Records everything
- **config.json**: Your credentials & settings

### Essential Commands
```bash
pip install -r requirements.txt    # Install
python main.py --status            # Check status
python main.py --dry-run           # Test mode
python main.py                     # Run live
```

### Must-Edit in config.json
```json
{
  "saxo_api": {
    "app_key": "YOUR_KEY",
    "app_secret": "YOUR_SECRET"
  },
  "account": {
    "account_key": "YOUR_ACCOUNT",
    "client_key": "YOUR_CLIENT"
  }
}
```

### Strategy Logic
1. VIX < 18? → Buy long straddle (90-120 DTE)
2. Sell weekly shorts (1.5-2x expected move)
3. SPY moves 5+ points? → Recenter
4. Thursday/Friday? → Roll shorts
5. 30-60 DTE on longs? → Exit all

---

**Good luck with your trading! Remember: Start small, test thoroughly, and never risk more than you can afford to lose. 📈**
