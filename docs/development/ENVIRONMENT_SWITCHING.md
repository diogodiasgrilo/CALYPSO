# Environment Switching Guide

## Overview

Calypso now supports **two separate environments**:

1. **SIM (Simulation)** - Paper trading with external price feeds (Yahoo Finance)
2. **LIVE** - Real money trading with full market data access

Each environment has its own app credentials and tokens that are managed separately.

---

## Configuration Structure

Your `config.json` now has separate credentials for each environment:

```json
{
  "saxo_api": {
    "sim": {
      "app_key": "c9ad07d62c27484196b3a85189de68ce",
      "app_secret": "3c0e68cbda3e4494a6ad55abaa1fe5b4",
      "access_token": "...",
      "refresh_token": "...",
      "token_expiry": "..."
    },
    "live": {
      "app_key": "58fc7320f32b42d7ad52dd1387ea0b26",
      "app_secret": "9479b979aa9a40b3a89087c6f03d99aa",
      "access_token": "...",
      "refresh_token": "...",
      "token_expiry": "..."
    },
    "environment": "sim"
  }
}
```

---

## Usage

### Run in SIM Environment (Default)

```bash
# Paper trading with external price feeds
python main.py

# Paper trading in dry-run mode (no orders)
python main.py --dry-run

# Check status in SIM
python main.py --status
```

**What happens**:
- Uses SIM app credentials
- External price feed enabled (Yahoo Finance for SPY/VIX)
- No real money at risk
- Options chain returns 404 (SIM limitation)

---

### Run in LIVE Environment

```bash
# ⚠️ REAL MONEY TRADING ⚠️
python main.py --live

# Test with live data but no order execution
python main.py --live --dry-run

# Check status on live account
python main.py --status --live
```

**What happens**:
- Uses LIVE app credentials
- Full market data access (SPY, VIX, options chains)
- External price feed automatically **disabled**
- Options trading fully functional
- **Real orders with real money**

---

## Key Differences

| Feature | SIM Environment | LIVE Environment |
|---------|----------------|------------------|
| **Real Money** | No | Yes |
| **SPY Price Data** | External feed (Yahoo) | Direct from Saxo |
| **VIX Price Data** | External feed (Yahoo) | Direct from Saxo |
| **Options Chain** | ❌ 404 errors | ✅ Full access |
| **Order Execution** | Paper trading | Real execution |
| **Auth URL** | sim.logonvalidation.net | live.logonvalidation.net |
| **Account Key** | Demo account key | Live account key |

---

## First Time Setup

### For LIVE Environment

1. **You've already created the LIVE app** ✅
2. **Credentials are in config.json** ✅
3. **Authenticated successfully** ✅ (tokens saved)

**Next step**: Update the account key for live environment.

Your current `account_key` in config.json is from your demo account. When using `--live`, you need the account key from your dad's live account.

To get the live account key:
```bash
python main.py --status --live
```

Look for the account information in the API response. You may need to fetch it from the `/port/v1/accounts/me` endpoint.

---

## Read-Only Mode vs Dry-Run

### What is `--dry-run`?

When you use `--dry-run` flag:
- ✅ Fetches all real market data
- ✅ Runs full strategy logic
- ✅ Calculates what trades to make
- ❌ **Does NOT submit orders to Saxo**
- ✅ Logs what would have been done

**This is essentially "Read-Only" mode!**

### Usage Examples

```bash
# Test with live data, NO orders executed
python main.py --live --dry-run

# Test with specific sub-account, NO orders
python main.py --live --dry-run --account YOUR_SUB_ACCOUNT_KEY
```

**Perfect for**:
- Testing strategy logic with real market data
- Verifying options chain access works
- Checking account balances and positions
- Validating the full workflow before going live

---

## Safety Features

### Automatic Protection

1. **External feed disabled in LIVE** - Ensures you always use real market data
2. **Separate tokens** - SIM and LIVE tokens are stored separately
3. **Big warning** - `--live` flag shows a warning: ⚠️ WARNING: LIVE ENVIRONMENT ENABLED
4. **Dry-run safety** - `--dry-run` prevents order submission

### Recommended Testing Workflow

1. **Develop in SIM first**:
   ```bash
   python main.py --dry-run
   ```

2. **Test with live data (no execution)**:
   ```bash
   python main.py --live --dry-run
   ```

3. **Small live test**:
   ```bash
   # Manually set position_size = 1 in config
   python main.py --live
   ```

4. **Full deployment**:
   ```bash
   # Increase position_size after testing
   python main.py --live
   ```

---

## Troubleshooting

### "Invalid Account Key" Error in LIVE

This is expected! Your current account_key is from the demo account. You need to:

1. Fetch the live account key
2. Update `config.json`:
   ```json
   "account": {
     "account_key": "[LIVE_ACCOUNT_KEY_HERE]",
     "client_key": "[LIVE_CLIENT_KEY_HERE]"
   }
   ```

### Options Still Return 404 in LIVE

If you still get 404 errors on options in LIVE mode:
- Verify you authenticated with your dad's funded live account
- Check that the live account has options trading enabled
- Contact Saxo support to ensure options permissions are active

### Want to Switch Default Environment

Edit `config.json`:
```json
"environment": "live"  // Default to LIVE instead of SIM
```

Then you can use:
- `python main.py` → runs in LIVE
- `python main.py --sim` → would need to add this flag (not yet implemented)

---

## Account Management

### List All Available Accounts

```bash
# List accounts in SIM environment
python main.py --list-accounts

# List accounts in LIVE environment
python main.py --live --list-accounts
```

This shows:
- Account keys
- Account types
- Currency
- Current balance
- Which account is currently configured

### Select a Specific Account

```bash
# Use a specific account (overrides config.json)
python main.py --account HHzaFvDVAVCg3hi3QUvbNg==

# Use sub-account in LIVE mode
python main.py --live --account YOUR_SUB_ACCOUNT_KEY

# Test with live data on sub-account (safe)
python main.py --live --dry-run --account YOUR_SUB_ACCOUNT_KEY
```

**Use Case**: If you have multiple accounts/sub-accounts (like a small testing account separate from your main account), you can specify which one to use without editing config.json.

---

## Command Reference

```bash
# SIM Environment
python main.py                        # Paper trading
python main.py --dry-run              # Simulate, no orders
python main.py --status               # Check SIM status
python main.py --list-accounts        # List available SIM accounts

# LIVE Environment
python main.py --live                 # ⚠️ REAL MONEY
python main.py --live --dry-run       # Live data, no execution (RECOMMENDED for testing)
python main.py --status --live        # Check live status
python main.py --live --list-accounts # List available live accounts

# Account Selection
python main.py --account <KEY>        # Use specific account
python main.py --live --account <KEY> # Use specific live account

# Other Options
python main.py --interval 30          # Check every 30 seconds
python main.py --verbose              # Debug logging
python main.py --config prod.json     # Custom config file
```

---

## Next Steps

1. ✅ Test live authentication (Done!)
2. ⚠️ Get live account key
3. ⚠️ Update config.json with live account_key
4. ✅ Test options chain access in LIVE mode
5. ⚠️ Run `python main.py --live --dry-run` to verify full strategy

Once the account key is updated, your bot will have full access to options trading in the LIVE environment!
