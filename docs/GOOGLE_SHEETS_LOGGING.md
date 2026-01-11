# Comprehensive Google Sheets Logging

**Date:** 2026-01-11
**Purpose:** Deep, intensive tracking of all strategy activities

---

## Overview

The Calypso bot now features **comprehensive Google Sheets logging** with 5 specialized worksheets that track every aspect of the delta neutral strategy:

1. **Trades** - Every single trade execution with full details
2. **Positions** - Real-time snapshot of all open positions
3. **Daily Summary** - End-of-day performance metrics
4. **Safety Events** - Fed filters, ITM warnings, emergency exits
5. **Greeks & Risk** - Delta, gamma, theta, vega tracking over time

---

## Worksheets Breakdown

### 1. **Trades** Worksheet (23 Columns)

Captures **every trade** with comprehensive details:

| Column | Description | Example |
|--------|-------------|---------|
| Timestamp | When trade occurred | 2026-01-11 14:30:00 |
| Action | Trade type | OPEN_LONG_STRADDLE, CLOSE_SHORT_STRANGLE, RECENTER, etc. |
| Reason | Why this trade | Entry, Roll, ITM Risk, Fed Filter, Emergency Exit |
| Strike | Strike price(s) | 450.0 or 440/460 (strangle) |
| Price | Execution price | 15.50 |
| Type | Option type | Straddle, Strangle, Call, Put |
| Expiry | Expiration date | 2026-04-17 |
| DTE | Days to expiration | 96 |
| Qty | Number of contracts | 1 |
| SPY Price | Underlying price at trade | 694.07 |
| VIX | VIX value at trade | 14.49 |
| Delta | Position delta | 0.0000 |
| Total Delta | Portfolio delta | -0.0523 |
| Gamma | Position gamma | 0.0150 |
| Theta | Position theta | -12.50 |
| Vega | Position vega | 45.23 |
| Premium | Premium received (shorts) | 3.50 |
| P&L | Trade P&L (USD) | -1550.00 |
| Realized P&L | Cumulative realized (USD) | 2450.00 |
| Unrealized P&L | Current unrealized (USD) | -350.00 |
| Currency | Base currency | USD |
| FX Rate | USD/EUR rate | 0.9200 |
| P&L (EUR) | Trade P&L converted | -1426.00 |

**Use Cases:**
- Track every entry, exit, roll, and recenter
- Analyze which trade reasons are most profitable
- Review historical trade decisions
- Calculate total premium collected
- Monitor P&L progression

---

### 2. **Positions** Worksheet (15 Columns)

**Real-time snapshot** of all open positions (updated on every strategy check):

| Column | Description | Example |
|--------|-------------|---------|
| Last Updated | Snapshot timestamp | 2026-01-11 14:35:00 |
| Position Type | Long/Short Call/Put | Long Call, Short Put, etc. |
| Strike | Strike price | 450.0 |
| Expiry | Expiration date | 2026-04-17 |
| DTE | Days to expiration | 96 |
| Quantity | Number of contracts | 1 |
| Entry Price | Price when opened | 7.50 |
| Current Price | Current market price | 8.20 |
| Delta | Position delta | 0.5200 |
| Gamma | Position gamma | 0.0075 |
| Theta | Position theta | -6.25 |
| Vega | Position vega | 22.50 |
| P&L | Position P&L (USD) | +70.00 |
| P&L (EUR) | Position P&L converted | +64.40 |
| Status | Position status | Active, Closing, etc. |

**Use Cases:**
- See all open positions at a glance
- Monitor current Greeks exposure
- Track unrealized P&L per position
- Identify which legs are profitable/losing
- Verify position hedging

---

### 3. **Daily Summary** Worksheet (20 Columns)

**End-of-day** performance snapshot (logged at market close or on demand):

| Column | Description | Example |
|--------|-------------|---------|
| Date | Trading date | 2026-01-11 |
| Strategy State | Bot state | FullPosition, Recentering, etc. |
| SPY Open | Opening price | 693.50 |
| SPY Close | Closing price | 694.80 |
| SPY Range | Day's range | 2.10 |
| VIX Avg | Average VIX | 14.80 |
| VIX High | Highest VIX | 15.50 |
| Total Delta | End-of-day delta | -0.0234 |
| Total Gamma | End-of-day gamma | 0.0320 |
| Total Theta | End-of-day theta | -28.50 |
| Daily P&L | Profit/loss for day (USD) | +245.00 |
| Realized P&L | Cumulative realized (USD) | 2695.00 |
| Unrealized P&L | Current unrealized (USD) | -180.00 |
| Premium Collected | Cumulative premium (USD) | 8750.00 |
| Trades Count | Number of trades today | 3 |
| Recenter Count | Cumulative recenters | 2 |
| Roll Count | Cumulative rolls | 8 |
| Cumulative P&L | Total P&L since start (USD) | 2515.00 |
| P&L (EUR) | Daily P&L converted | +225.40 |
| Notes | Manual notes | "VIX spike mid-day, rolled shorts early" |

**Use Cases:**
- Track daily performance trends
- Calculate win rate (days profitable vs not)
- Monitor strategy effectiveness
- Identify best/worst trading days
- Correlate performance with SPY/VIX movements

---

### 4. **Safety Events** Worksheet (12 Columns)

Logs **all safety triggers** (Fed filters, ITM risks, emergency exits):

| Column | Description | Example |
|--------|-------------|---------|
| Timestamp | When event occurred | 2026-01-11 10:15:00 |
| Event Type | Type of safety event | Fed Meeting Filter, ITM Risk, Emergency Exit, etc. |
| Severity | Event severity | INFO, WARNING, CRITICAL |
| SPY Price | Current underlying price | 694.80 |
| Initial Strike | Strategy initial strike | 690.00 |
| Distance (%) | Move from initial strike | 0.70% |
| VIX | Current VIX | 16.20 |
| Action Taken | What bot did | Entry Blocked, Roll Shorts, Close All |
| Short Call Strike | Call strike if relevant | 705.0 |
| Short Put Strike | Put strike if relevant | 675.0 |
| Description | Event details | "SPY within 2% of short call strike 705, rolled to next week" |
| Result | Outcome | Success, Failed, Pending |

**Use Cases:**
- Review all Fed meeting blackouts
- Track how often ITM prevention triggers
- Analyze emergency exit conditions
- Verify safety systems working correctly
- Calculate impact of safety filters on P&L

---

### 5. **Greeks & Risk** Worksheet (15 Columns)

**Continuous tracking** of portfolio Greeks (logged every strategy check):

| Column | Description | Example |
|--------|-------------|---------|
| Timestamp | Snapshot time | 2026-01-11 14:40:00 |
| SPY Price | Underlying price | 694.50 |
| VIX | VIX value | 14.60 |
| Long Delta | Delta from long positions | 0.0000 |
| Short Delta | Delta from short positions | -0.0523 |
| Total Delta | Net delta | -0.0523 |
| Long Gamma | Gamma from longs | 0.0280 |
| Short Gamma | Gamma from shorts | -0.0120 |
| Total Gamma | Net gamma | 0.0160 |
| Long Theta | Theta from longs | -18.50 |
| Short Theta | Theta from shorts | +6.25 |
| Total Theta | Net theta | -12.25 |
| Long Vega | Vega from longs | 85.40 |
| Short Vega | Vega from shorts | -32.10 |
| Total Vega | Net vega | 53.30 |

**Use Cases:**
- Monitor delta neutrality over time
- Track theta decay
- Analyze gamma exposure
- Identify vega risk from VIX changes
- Create charts showing Greeks evolution

---

## Setup Instructions

### 1. Create Google Cloud Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create new project (e.g., "Calypso Trading Bot")
3. Enable **Google Sheets API**:
   - Navigate to "APIs & Services" > "Library"
   - Search for "Google Sheets API"
   - Click "Enable"
4. Enable **Google Drive API**:
   - Search for "Google Drive API"
   - Click "Enable"
5. Create Service Account:
   - Navigate to "IAM & Admin" > "Service Accounts"
   - Click "Create Service Account"
   - Name: `calypso-bot-logger`
   - Role: None needed (we'll grant access directly to spreadsheet)
6. Create JSON Key:
   - Click on the service account
   - Go to "Keys" tab
   - "Add Key" > "Create new key" > JSON
   - Download the JSON file
7. Save the JSON file as `config/google_credentials.json`

### 2. Share Spreadsheet with Service Account

The bot will automatically create a spreadsheet named `Calypso_Bot_Log` (or your configured name), but you need to share it:

1. Run the bot once (it will create the spreadsheet)
2. Open the spreadsheet in Google Sheets
3. Click "Share"
4. Add the service account email (found in `google_credentials.json` as `client_email`)
   - Example: `calypso-bot-logger@project-id.iam.gserviceaccount.com`
5. Grant "Editor" permissions
6. Click "Done"

### 3. Configure Bot

Edit `config/config.json`:

```json
{
  "google_sheets": {
    "enabled": true,
    "credentials_file": "config/google_credentials.json",
    "spreadsheet_name": "Calypso_Bot_Log",
    "worksheet_name": "Trades"
  }
}
```

**Note:** `worksheet_name` is ignored (legacy field) - all 5 worksheets are created automatically.

---

## What Gets Logged Automatically

### Every Trade Execution
- `log_trade()` logs to **Trades** worksheet with full details
- Includes: timestamp, action, reason, strike, price, Greeks, P&L, currency conversion

### Every Strategy Check (60-second loop)
- `log_greeks_snapshot()` logs to **Greeks & Risk** worksheet
- Tracks delta, gamma, theta, vega evolution over time

### Daily at Market Close
- `log_daily_summary()` logs to **Daily Summary** worksheet
- Summarizes day's performance, trades, Greeks

### Position Updates
- `log_position_snapshot()` logs to **Positions** worksheet
- Real-time snapshot of all open positions (updated every strategy check)

### Safety Events
- `log_safety_event()` logs to **Safety Events** worksheet
- Triggered by:
  - Fed meeting filter blocking entry
  - ITM risk detected on shorts
  - Emergency exit condition (5%+ move)
  - Circuit breaker activation

---

## Enhanced TradeRecord Fields

The `log_trade()` method now accepts **comprehensive optional parameters**:

```python
self.trade_logger.log_trade(
    action="OPEN_LONG_STRADDLE",
    strike=call_option["strike"],
    price=call_price + put_price,
    delta=0.0,
    pnl=0.0,
    saxo_client=self.client,  # For currency conversion
    # NEW COMPREHENSIVE FIELDS:
    underlying_price=self.current_underlying_price,
    vix=self.current_vix,
    option_type="Straddle",
    expiry_date=call_option["expiry"],
    dte=call_option["dte"],
    quantity=self.position_size,
    premium_received=None,  # Or premium if selling
    total_delta=self.get_total_delta(),
    realized_pnl=self.metrics.realized_pnl,
    unrealized_pnl=self.metrics.unrealized_pnl,
    trade_reason="Entry",  # Or "Roll", "ITM Risk", etc.
    greeks={
        "gamma": 0.0150,
        "theta": -12.50,
        "vega": 45.23
    }
)
```

**All fields are optional** - if not provided, they'll show "N/A" in the spreadsheet.

---

## Logging Safety Events

Example of logging a Fed meeting filter block:

```python
self.trade_logger.log_safety_event({
    "event_type": "Fed Meeting Filter",
    "severity": "WARNING",
    "spy_price": self.current_underlying_price,
    "initial_strike": self.initial_straddle_strike,
    "distance_pct": 0.0,
    "vix": self.current_vix,
    "action_taken": "Entry Blocked",
    "short_call_strike": "N/A",
    "short_put_strike": "N/A",
    "description": f"FOMC meeting in 1 day(s) - entry blocked",
    "result": "Success"
})
```

Example of logging ITM risk:

```python
self.trade_logger.log_safety_event({
    "event_type": "ITM Risk Detected",
    "severity": "CRITICAL",
    "spy_price": price,
    "initial_strike": self.initial_straddle_strike,
    "distance_pct": abs((price - call_strike) / call_strike * 100),
    "vix": self.current_vix,
    "action_taken": "Roll Shorts",
    "short_call_strike": call_strike,
    "short_put_strike": put_strike,
    "description": f"SPY ${price:.2f} within 2% of short call ${call_strike:.2f}",
    "result": "Success" if rolled else "Failed"
})
```

---

## Logging Position Snapshots

The bot can log current positions to the **Positions** worksheet:

```python
# Build position list
positions = []

if self.long_straddle and self.long_straddle.call:
    positions.append({
        "type": "Long Call",
        "strike": self.long_straddle.call.strike,
        "expiry": self.long_straddle.call.expiry,
        "dte": self._calculate_dte(self.long_straddle.call.expiry),
        "quantity": self.long_straddle.call.quantity,
        "entry_price": self.long_straddle.call.entry_price,
        "current_price": self.long_straddle.call.current_price,
        "delta": self.long_straddle.call.delta,
        "gamma": 0.0150,  # If available from Saxo
        "theta": -8.25,
        "vega": 42.50,
        "pnl": (self.long_straddle.call.current_price -
                self.long_straddle.call.entry_price) * 100,
        "pnl_eur": pnl * exchange_rate if exchange_rate else 0,
        "status": "Active"
    })

# Log to Google Sheets
self.trade_logger.log_position_snapshot(positions)
```

---

## Logging Daily Summaries

At market close or on demand:

```python
summary = {
    "date": datetime.now().strftime("%Y-%m-%d"),
    "state": self.state.value,
    "spy_open": daily_spy_open,
    "spy_close": self.current_underlying_price,
    "spy_range": daily_high - daily_low,
    "vix_avg": daily_vix_avg,
    "vix_high": daily_vix_high,
    "total_delta": self.get_total_delta(),
    "total_gamma": self.get_total_gamma(),
    "total_theta": self.get_total_theta(),
    "daily_pnl": today_realized_pnl,
    "realized_pnl": self.metrics.realized_pnl,
    "unrealized_pnl": self.metrics.unrealized_pnl,
    "premium_collected": self.metrics.premium_collected,
    "trades_count": today_trade_count,
    "recenter_count": self.metrics.recenter_count,
    "roll_count": self.metrics.roll_count,
    "cumulative_pnl": self.metrics.total_pnl,
    "pnl_eur": today_realized_pnl * exchange_rate if exchange_rate else 0,
    "notes": ""  # Manual notes if needed
}

self.trade_logger.log_daily_summary(summary)
```

---

## Logging Greeks Snapshots

Every strategy check (or on demand):

```python
greeks = {
    "spy_price": self.current_underlying_price,
    "vix": self.current_vix,
    "long_delta": long_straddle_delta,
    "short_delta": short_strangle_delta,
    "total_delta": self.get_total_delta(),
    "long_gamma": long_gamma,
    "short_gamma": short_gamma,
    "total_gamma": long_gamma + short_gamma,
    "long_theta": long_theta,
    "short_theta": short_theta,
    "total_theta": long_theta + short_theta,
    "long_vega": long_vega,
    "short_vega": short_vega,
    "total_vega": long_vega + short_vega
}

self.trade_logger.log_greeks_snapshot(greeks)
```

---

## Data Analysis Use Cases

### 1. Performance Analysis
- Track cumulative P&L over time
- Calculate Sharpe ratio from daily returns
- Identify best/worst performing periods
- Correlate P&L with VIX levels

### 2. Risk Management
- Monitor delta deviation from neutral
- Track maximum gamma exposure
- Analyze theta decay vs premium collected
- Identify vega exposure during VIX spikes

### 3. Strategy Optimization
- Compare performance with/without Fed filter
- Analyze profitability of recenters vs holds
- Measure impact of early vs late rolls
- Calculate average time in trade before exit

### 4. Trade Review
- Review every entry/exit decision
- Analyze why certain trades were losers
- Identify patterns in profitable trades
- Validate strategy rules are being followed

### 5. Safety System Validation
- Verify Fed filter blocks all pre-FOMC entries
- Confirm ITM prevention triggers correctly
- Track emergency exit effectiveness
- Monitor circuit breaker activations

---

## Spreadsheet Formulas & Charts

### Suggested Google Sheets Formulas

**Cumulative P&L Chart** (Trades sheet):
```
=SUM($R$2:R2)  // In column U, drag down
```

**Win Rate** (Daily Summary):
```
=COUNTIF(K:K, ">0") / COUNTA(K:K)  // Positive days / total days
```

**Average Daily P&L**:
```
=AVERAGE(K:K)  // Column K = Daily P&L
```

**Max Drawdown**:
```
=MIN(U:U) - MAX(U:U)  // Where U = cumulative P&L
```

### Suggested Charts

1. **Cumulative P&L Over Time**
   - X-axis: Date (from Trades)
   - Y-axis: Cumulative P&L
   - Type: Line chart

2. **Delta Over Time**
   - X-axis: Timestamp (from Greeks & Risk)
   - Y-axis: Total Delta
   - Type: Line chart with +/- 0.10 target bands

3. **VIX vs Daily P&L**
   - X-axis: Date
   - Y-axis: VIX Avg (left), Daily P&L (right)
   - Type: Combo chart

4. **Greeks Dashboard**
   - 4 separate charts: Delta, Gamma, Theta, Vega
   - All showing evolution over time
   - Type: Line charts

5. **Safety Events Frequency**
   - Count of each Event Type
   - Type: Bar chart

---

## Performance Considerations

- **API Rate Limits**: Google Sheets API has quotas (60 writes/minute per user, 300 writes/minute per project)
- **Batch Writes**: The bot writes immediately on each event (not batched)
- **Large Datasets**: Greeks & Risk sheet will grow fastest (every 60s). Consider archiving old data monthly.
- **Network Latency**: Writes are asynchronous (won't block trading)

---

## Troubleshooting

### "gspread library not installed"
```bash
pip install gspread google-auth google-auth-oauthlib google-auth-httplib2
```

### "Google credentials file not found"
- Ensure `config/google_credentials.json` exists
- Check path in `config/config.json`

### "Permission denied" when writing
- Verify service account email has Editor access to spreadsheet
- Check that spreadsheet exists and is shared

### Spreadsheet not created automatically
- Bot only creates spreadsheet if it doesn't exist
- If creation fails, create manually and share with service account

### Data not appearing in sheets
- Check bot logs for Google Sheets errors
- Verify `enabled: true` in config
- Ensure internet connectivity

---

## Security Notes

⚠️ **NEVER commit `config/google_credentials.json` to git!**

The `.gitignore` is already configured to exclude:
```
config/google_credentials.json
config/*credentials*.json
```

---

## Summary

The comprehensive Google Sheets logging provides:

✅ **23-column detailed trade log** with every execution
✅ **Real-time position tracking** with full Greeks
✅ **Daily performance summaries** for analysis
✅ **Safety event logging** (Fed, ITM, emergency)
✅ **Continuous Greeks monitoring** (delta, gamma, theta, vega)
✅ **Automatic currency conversion** (USD to EUR)
✅ **Full audit trail** of all bot decisions
✅ **Data ready for charts and analysis**

Your delta neutral strategy is now **fully transparent** with every decision, trade, and risk metric logged in real-time to Google Sheets!
