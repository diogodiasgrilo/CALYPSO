# Google Sheets Logging Setup

Comprehensive trade logging to Google Sheets for all 3 bots.

Each bot has its own dedicated spreadsheet with strategy-specific worksheets.

---

## Quick Setup (5 minutes)

### Step 1: Create Google Service Account

1. Go to: https://console.cloud.google.com/
2. Create/select project: "Calypso Trading Bot"
3. Enable APIs:
   - Search "Google Sheets API" → Enable
   - Search "Google Drive API" → Enable
4. Create Service Account:
   - Go to: IAM & Admin → Service Accounts
   - Click "Create Service Account"
   - Name: `calypso-bot-logger`
   - Click "Create and Continue" → Skip roles → "Done"
5. Download Credentials:
   - Click on the service account
   - Go to "Keys" tab
   - Click "Add Key" → "Create new key" → JSON
   - Save as: `config/google_credentials.json`

### Step 2: Configure Bot

Each bot's config file (`bots/{bot_name}/config/config.json`):

```json
{
  "google_sheets": {
    "enabled": true,
    "credentials_file": "config/google_credentials.json",
    "spreadsheet_name": "Calypso_Delta_Neutral_Live_Data"
  }
}
```

**Spreadsheet names by bot:**
- Delta Neutral: `Calypso_Delta_Neutral_Live_Data`
- Iron Fly 0DTE: `Calypso_Iron_Fly_Live_Data`
- Rolling Put Diagonal: `Calypso_Rolling_Put_Diagonal_Live_Data`

### Step 3: Run Bot (First Time)

```bash
python -m bots.delta_neutral.main --live --dry-run
```

Bot automatically creates the spreadsheet with all worksheets.

### Step 4: Share Spreadsheet

1. Open the created spreadsheet in Google Sheets
2. Click "Share" (top right)
3. Add service account email (from `google_credentials.json` → `client_email`)
4. Set to "Editor"
5. Click "Done"

### Step 5: Restart Bot

```bash
sudo systemctl restart delta_neutral
```

Done! Data is now flowing to Google Sheets.

---

## Worksheets Created

Each bot creates these worksheets:

| Worksheet | Purpose | Update Frequency |
|-----------|---------|------------------|
| **Trades** | Every trade with full details | On each trade |
| **Positions** | Current open positions | On position change |
| **Daily Summary** | Daily P&L, theta, metrics | End of day |
| **Performance Metrics** | Running performance stats | Every 15 min |
| **Account Summary** | Account balance, buying power | Every 15 min |
| **Bot Logs** | Bot activity log | Hourly |

**Additional worksheets by strategy:**
- **Iron Fly 0DTE:** Opening Range (9:30-10:00 AM tracking)
- **Delta Neutral:** Greeks & Risk (delta, theta, vega tracking)

---

## Verify It's Working

### Check Bot Output
```
TradeLoggerService initialized
  - Local logging: ENABLED
  - Google Sheets: ENABLED  ← This line confirms it
  - Currency Conversion: ENABLED (USD -> EUR)
```

### Check Spreadsheet
1. Open the spreadsheet in Google Sheets
2. Verify tabs at bottom (Trades, Positions, etc.)
3. Data should appear as bot runs

---

## Troubleshooting

### "Permission denied"
→ Share spreadsheet with service account email (Step 4)

### "Spreadsheet not found"
→ Let bot create it first, then share

### "gspread library not installed"
→ Run: `pip install gspread google-auth`

### "Credentials file not found"
→ Check `config/google_credentials.json` exists

---

## GCP Deployment

On the VM, credentials are stored in GCP Secret Manager:

```bash
# Credentials are automatically loaded from:
# - Secret: calypso-google-sheets-credentials
```

No need to manually copy credential files to the VM.

---

## Data Analysis

### Looker Studio Dashboard

1. Go to https://lookerstudio.google.com
2. Create new report
3. Add data source → Google Sheets
4. Select your Calypso spreadsheet
5. Build charts:
   - Cumulative P&L over time
   - Daily performance bar chart
   - Position allocation pie chart
   - Greeks time series

### Google Sheets Formulas

Useful formulas for your spreadsheet:

```
# Cumulative P&L (add column to Trades sheet)
=SUMIF(A$2:A2, "<="&A2, R$2:R2)

# Win rate
=COUNTIF(R:R, ">0") / COUNTA(R:R)

# Average trade P&L
=AVERAGE(R:R)

# Max drawdown (requires helper columns)
=MIN(CumulativePnL) - MAX(CumulativePnL)
```

---

**Last Updated:** 2026-01-23
