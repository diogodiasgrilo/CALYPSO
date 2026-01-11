# Google Sheets Logging - Quick Start Guide

**5-Minute Setup Guide for Comprehensive Logging**

---

## What You're Getting

📊 **5 Worksheets** that track everything:
1. **Trades** (23 columns) - Every trade with full details
2. **Positions** (15 columns) - Real-time position snapshot
3. **Daily Summary** (20 columns) - Daily performance metrics
4. **Safety Events** (12 columns) - Fed filters, ITM warnings, emergency exits
5. **Greeks & Risk** (15 columns) - Delta, gamma, theta, vega tracking

**Total:** 85 columns of intensive strategy tracking

---

## Setup Steps

### Step 1: Install Dependencies (Already Done ✅)

```bash
pip install gspread google-auth google-auth-oauthlib google-auth-httplib2
```

### Step 2: Create Google Service Account (5 minutes)

1. **Go to:** https://console.cloud.google.com/
2. **Create project:** "Calypso Trading Bot"
3. **Enable APIs:**
   - Search "Google Sheets API" → Enable
   - Search "Google Drive API" → Enable
4. **Create Service Account:**
   - Go to: IAM & Admin → Service Accounts
   - Click "Create Service Account"
   - Name: `calypso-bot-logger`
   - Click "Create and Continue"
   - Skip role selection (click "Continue")
   - Click "Done"
5. **Download Credentials:**
   - Click on the service account you just created
   - Go to "Keys" tab
   - Click "Add Key" → "Create new key" → JSON
   - Click "Create"
   - Save the downloaded file as: `config/google_credentials.json`

### Step 3: Configure Bot

Edit `config/config.json`:

```json
{
  "google_sheets": {
    "enabled": true,
    "credentials_file": "config/google_credentials.json",
    "spreadsheet_name": "Calypso_Bot_Log"
  }
}
```

### Step 4: Run Bot (First Time)

```bash
python -m src.main --live --dry-run
```

**Bot will automatically:**
- ✅ Create spreadsheet "Calypso_Bot_Log"
- ✅ Create 5 worksheets with headers
- ✅ Start logging to console/file (Google Sheets will show permission error until Step 5)

### Step 5: Share Spreadsheet with Bot

1. **Open the spreadsheet:** Go to Google Sheets and find "Calypso_Bot_Log"
2. **Click "Share"** button (top right)
3. **Add service account email:**
   - Open `config/google_credentials.json`
   - Find the `"client_email"` field (looks like: `calypso-bot-logger@project-id.iam.gserviceaccount.com`)
   - Copy that email
   - Paste into "Share" dialog in Google Sheets
4. **Set permissions:** Change to "Editor"
5. **Click "Done"**

### Step 6: Restart Bot

```bash
# Stop current bot (Ctrl+C)
# Restart
python -m src.main --live --dry-run
```

**Now you'll see:**
```
All Google Sheets worksheets initialized successfully
Trade logged to Google Sheets: OPEN_LONG_STRADDLE
```

**Done! 🎉** Check your spreadsheet - data is flowing!

---

## What Gets Logged Automatically

### ✅ Every Trade
- OPEN_LONG_STRADDLE, CLOSE_LONG_STRADDLE
- OPEN_SHORT_STRANGLE, CLOSE_SHORT_STRANGLE
- RECENTER, EXIT_ALL
- **Worksheet:** Trades
- **Frequency:** On each trade execution

### ✅ Greeks Every 60 Seconds
- Delta, gamma, theta, vega
- Long positions, short positions, totals
- **Worksheet:** Greeks & Risk
- **Frequency:** Every strategy check (60s)

### ✅ Position Snapshots
- All open positions with full details
- Real-time P&L per position
- **Worksheet:** Positions
- **Frequency:** When positions change

### ✅ Safety Events
- Fed meeting blackouts
- ITM risk warnings
- Emergency exits
- Circuit breaker activations
- **Worksheet:** Safety Events
- **Frequency:** When safety conditions trigger

### ✅ Daily Summaries
- End-of-day performance
- Daily P&L, premium collected
- Trade count, recenter/roll counts
- **Worksheet:** Daily Summary
- **Frequency:** Market close (or manual)

---

## Verify It's Working

### Check 1: Bot Console Output

You should see:
```
TradeLoggerService initialized
  - Local logging: ENABLED
  - Google Sheets: ENABLED  ← This line!
  - Microsoft Excel: DISABLED
  - Currency Conversion: ENABLED (USD -> EUR)
```

### Check 2: Google Sheets

1. Open "Calypso_Bot_Log" spreadsheet
2. You should see 5 tabs at bottom:
   - Trades
   - Positions
   - Daily Summary
   - Safety Events
   - Greeks & Risk
3. Headers should be formatted (bold)
4. Data should appear as bot trades

### Check 3: No Errors

If you see:
```
ERROR | gspread library not installed
```
→ Run: `pip install gspread google-auth`

If you see:
```
ERROR | Google credentials file not found
```
→ Check that `config/google_credentials.json` exists

If you see:
```
ERROR | Permission denied
```
→ Make sure you shared spreadsheet with service account email (Step 5)

---

## Spreadsheet Structure

### Trades Worksheet
```
| Timestamp | Action | Reason | Strike | Price | Type | Expiry | DTE | ... (23 cols total) |
|-----------|--------|--------|--------|-------|------|--------|-----|---------------------|
| 2026-01-11 14:30 | OPEN_LONG_STRADDLE | Entry | 450 | 15.50 | Straddle | 2026-04-17 | 96 | ... |
```

### Positions Worksheet
```
| Last Updated | Position Type | Strike | Expiry | DTE | Qty | Entry | Current | Delta | ... |
|--------------|---------------|--------|--------|-----|-----|-------|---------|-------|-----|
| 2026-01-11 14:35 | Long Call | 450 | 2026-04-17 | 96 | 1 | 7.50 | 8.20 | 0.52 | ... |
```

### Greeks & Risk Worksheet
```
| Timestamp | SPY | VIX | Long Delta | Short Delta | Total Delta | ... |
|-----------|-----|-----|------------|-------------|-------------|-----|
| 2026-01-11 14:40 | 694.50 | 14.60 | 0.0000 | -0.0523 | -0.0523 | ... |
```

---

## Next Steps

### 1. Run Bot and Monitor

Watch data populate in real-time:
```bash
python -m src.main --live --dry-run
```

Open spreadsheet and watch:
- **Trades** sheet fills with each execution
- **Greeks & Risk** updates every 60 seconds
- **Positions** updates on position changes

### 2. Analyze Data

Create charts in Google Sheets:
- **Cumulative P&L:** Trades sheet, column R (P&L) → Running total
- **Delta Over Time:** Greeks sheet, column F (Total Delta) → Line chart
- **Daily Performance:** Daily Summary sheet → Bar chart

### 3. Read Full Documentation

For advanced usage and all features:
- **[docs/GOOGLE_SHEETS_LOGGING.md](docs/GOOGLE_SHEETS_LOGGING.md)** - Complete guide (700 lines)
- **[GOOGLE_SHEETS_IMPLEMENTATION.md](GOOGLE_SHEETS_IMPLEMENTATION.md)** - Implementation details

---

## Troubleshooting

### "Permission denied when accessing spreadsheet"
**Solution:** Make sure you shared spreadsheet with service account email (Step 5)

### "Spreadsheet not found"
**Solution:** Let bot create it on first run, then share with service account

### Data not appearing
**Solution:** Check bot logs for errors, verify `enabled: true` in config

### Rate limit errors
**Solution:** Google Sheets API allows 60 writes/min - bot writes are within limits

---

## Summary

**Setup Time:** ~5 minutes
**Worksheets:** 5 specialized sheets
**Total Columns:** 85 data points
**Coverage:** Every trade, position, safety event, Greek value
**Frequency:** Real-time (60s for Greeks, instant for trades)

**Your delta neutral strategy is now comprehensively tracked with intensive, deep logging!** 🚀

For questions or issues, see full documentation in [docs/GOOGLE_SHEETS_LOGGING.md](docs/GOOGLE_SHEETS_LOGGING.md).
