# Calypso Trading Bot - Looker Studio Dashboard Guide

This guide walks you through setting up a comprehensive Looker Studio dashboard to visualize your Calypso SPY options trading strategy performance.

## Overview

**IMPORTANT**: This dashboard tracks **ONLY the SPY delta-neutral strategy**, not your entire Saxo account. All P&L, positions, and metrics are filtered to show only SPY options trades managed by the Calypso bot.

The dashboard displays:
- **SPY Strategy P&L** (USD and EUR) - only SPY options
- **Live bot activity logs**
- **SPY option positions with theta decay**
- **Daily/Weekly/Monthly/Yearly strategy performance**
- **Safety events (rolls, recenters, alerts)**
- **Strategy summary (delta, theta, position values)**

## Prerequisites

1. Google account with access to [Looker Studio](https://lookerstudio.google.com)
2. Calypso bot running with Google Sheets logging enabled
3. Access to the `Calypso_Bot_Log` Google Spreadsheet

## Google Sheets Data Structure

The bot logs to **7 worksheets** (all SPY strategy-specific):

| Worksheet | Purpose | Update Frequency |
|-----------|---------|-----------------|
| **Trades** | Every SPY trade execution with full details | Per trade |
| **Positions** | Real-time snapshot of SPY option positions | Every 5 min |
| **Daily Summary** | Daily SPY strategy performance metrics | End of day |
| **Safety Events** | Rolls, recenters, alerts | Per event |
| **Bot Logs** | Live activity stream | Continuous |
| **Performance Metrics** | SPY strategy KPIs | Hourly |
| **Account Summary** | SPY strategy values and Greeks | Every 15 min |

## Dashboard Setup

### Step 1: Open Looker Studio

1. Go to [lookerstudio.google.com](https://lookerstudio.google.com)
2. Click **"Create"** → **"Report"**
3. Name it: `Calypso Trading Dashboard`

### Step 2: Connect Google Sheets Data Source

1. Click **"Add data"** in the toolbar
2. Select **"Google Sheets"**
3. Find and select `Calypso_Bot_Log`
4. Add each worksheet as a separate data source:
   - Trades
   - Positions
   - Daily Summary
   - Safety Events
   - Bot Logs
   - Performance Metrics
   - Account Summary

### Step 3: Create Dashboard Pages

---

## Page 1: Executive Overview (For Investors)

This page shows high-level performance metrics at a glance.

### Scorecards Row (Top)

Add 6 **Scorecard** components:

| Scorecard | Data Source | Metric | Format |
|-----------|-------------|--------|--------|
| Total P&L (USD) | Performance Metrics | `Total P&L ($)` | Currency |
| Total P&L (EUR) | Performance Metrics | `Total P&L (EUR)` | Currency |
| Today's P&L | Account Summary | `Day P&L ($)` | Currency |
| Win Rate | Performance Metrics | `Win Rate (%)` | Percent |
| Total Trades | Performance Metrics | `Trade Count` | Number |
| Max Drawdown | Performance Metrics | `Max Drawdown (%)` | Percent |

**Styling:**
- Background: Dark (for positive), Red (for negative) using conditional formatting
- Font: Large, bold numbers

### P&L Time Series Chart

1. Add a **Time series** chart
2. Data Source: `Daily Summary`
3. Dimension: `Date`
4. Metric: `Cumulative P&L ($)`
5. Style:
   - Line color: Green for positive trend
   - Add trend line
   - Show data labels

### Monthly Performance Bar Chart

1. Add a **Bar chart**
2. Data Source: `Daily Summary`
3. Dimension: `Date` (group by Month)
4. Metric: `Daily P&L ($)` (SUM)
5. Style:
   - Bars colored by value (green positive, red negative)

### Current Positions Table

1. Add a **Table**
2. Data Source: `Positions`
3. Columns:
   - Type
   - Strike
   - Expiry
   - Days to Expiry
   - P&L ($)
   - P&L (EUR)
   - Theta/Day ($)
   - Weekly Theta ($)

---

## Page 2: Live Trading Activity

Real-time view of bot operations.

### Bot Logs Panel

1. Add a **Table** with scrolling enabled
2. Data Source: `Bot Logs`
3. Columns:
   - Timestamp
   - Level
   - Component
   - Message
   - SPY Price
   - VIX
4. Sort by: Timestamp (Descending)
5. Rows per page: 50
6. Style:
   - Conditional row coloring:
     - ERROR = Red background
     - WARNING = Yellow background
     - INFO = Default

### Recent Trades Table

1. Add a **Table**
2. Data Source: `Trades`
3. Columns:
   - Timestamp
   - Action
   - Type
   - Strike
   - Premium ($)
   - P&L ($)
   - Notes
4. Sort by: Timestamp (Descending)
5. Limit: 20 rows

### Safety Events Table

1. Add a **Table**
2. Data Source: `Safety Events`
3. Columns:
   - Timestamp
   - Event
   - SPY Price
   - VIX
   - Description
   - Result
4. Style: Conditional coloring by event type

---

## Page 3: Performance Analytics

Detailed performance breakdown.

### Theta Analysis Section

**Theta Income Chart:**
1. Add a **Bar chart**
2. Data Source: `Positions`
3. Dimension: `Type`
4. Metric: `Weekly Theta ($)` (SUM)
5. Filter: Only SHORT positions

**Net Theta Calculation:**
- Create a calculated field: `Net Theta = Short Theta - Long Theta Cost`
- Display as scorecard

### Performance by Period

Add a **Table** with performance metrics:

| Period | P&L ($) | P&L (%) | Win Rate | Trades |
|--------|---------|---------|----------|--------|
| Today | | | | |
| This Week | | | | |
| This Month | | | | |
| This Year | | | | |
| All Time | | | | |

Use `Performance Metrics` data source, filtered by Period.

### Risk Metrics

**Scorecards:**
- Max Drawdown ($)
- Max Drawdown (%)
- Sharpe Ratio
- Best Trade ($)
- Worst Trade ($)

---

## Page 4: Account & Risk

Saxo account details and risk monitoring.

### SPY Strategy Summary Cards

1. Data Source: `Account Summary`
2. Metrics (all SPY strategy-specific):
   - Strategy Unrealized P&L ($)
   - Strategy Unrealized P&L (EUR)
   - Long Straddle Value ($)
   - Short Strangle Value ($)
   - Net Strategy Value ($)
   - Strategy Margin Used ($)

### Total Delta Gauge

1. Add a **Gauge** chart
2. Metric: `Total Delta`
3. Range: -0.5 to +0.5
4. Color zones:
   - Green: -0.1 to +0.1 (delta neutral)
   - Yellow: -0.3 to -0.1, +0.1 to +0.3
   - Red: outside -0.3 to +0.3

### Strategy Value Over Time

1. Add a **Time series** chart
2. Data Source: `Account Summary`
3. Dimension: `Timestamp`
4. Metrics:
   - Net Strategy Value ($)
   - Long Straddle Value ($)
   - Short Strangle Value ($)

### Position Structure Table

1. Data Source: `Account Summary`
2. Columns:
   - Long Call Strike
   - Long Put Strike
   - Short Call Strike
   - Short Put Strike
   - Total Delta
   - Total Theta ($)

---

## Page 5: Greeks & Strategy

Option Greeks and strategy details.

### Current Delta Gauge

1. Add a **Scorecard**
2. Calculate total delta from Positions
3. Target: 0 (delta neutral)
4. Show deviation

### Position Greeks Table

1. Data Source: `Positions`
2. Columns:
   - Type
   - Strike
   - Delta
   - Gamma
   - Theta
   - Vega (if available)

### Strategy Structure Visualization

Create a visual showing:
- Long Straddle position (strikes, expiry)
- Short Strangle position (strikes, expiry)
- Current SPY price vs strikes

---

## Styling Guidelines

### Color Scheme

| Element | Color |
|---------|-------|
| Positive P&L | `#00C853` (Green) |
| Negative P&L | `#FF1744` (Red) |
| Headers | `#1A237E` (Dark Blue) |
| Background | `#FAFAFA` (Light Gray) |
| Accent | `#FFC107` (Amber) |

### Fonts

- Headers: Roboto Bold, 18px
- Values: Roboto, 24px
- Labels: Roboto Light, 12px

### Logo

Add the Calypso logo in the header (if available).

---

## Auto-Refresh Settings

1. Click **File** → **Report settings**
2. Enable **"Data freshness"**
3. Set refresh interval:
   - For real-time pages: 1 minute
   - For summary pages: 15 minutes

---

## Sharing with Investors

### View-Only Access

1. Click **Share** in Looker Studio
2. Add investor email addresses
3. Set permission to **"Viewer"**
4. Enable **"Link sharing"** for easy access

### Embedding

To embed in a website:
1. Click **File** → **Embed report**
2. Copy the iframe code
3. Paste into your investor portal

### Scheduled Email Reports

1. Click **File** → **Schedule email delivery**
2. Set frequency (Daily, Weekly)
3. Add recipient emails
4. Choose PDF or link format

---

## Calculated Fields Reference

Create these calculated fields in Looker Studio for advanced metrics:

### Win Rate
```
COUNT_IF(P&L ($) > 0) / COUNT(*) * 100
```

### Average Trade P&L
```
SUM(P&L ($)) / COUNT(*)
```

### Net Theta (Daily)
```
SUM_IF(Theta/Day ($), Type CONTAINS "Short") - ABS(SUM_IF(Theta/Day ($), Type CONTAINS "Long"))
```

### Monthly Return %
```
SUM(Daily P&L ($)) / FIRST(Starting Capital) * 100
```

---

## Troubleshooting

### Data Not Updating

1. Check that the bot is running and logging to Google Sheets
2. Verify Google Sheets connection in Looker Studio
3. Click **"Refresh data"** in the toolbar
4. Check data source credentials haven't expired

### Missing Worksheets

If new worksheets don't appear:
1. In Looker Studio, go to **Resource** → **Manage added data sources**
2. Click **"Edit"** on the Google Sheets connection
3. Select the missing worksheets
4. Click **"Reconnect"**

### Performance Issues

For large datasets:
1. Use date range filters to limit data
2. Create aggregated views in Google Sheets
3. Use Looker Studio's data blending sparingly

---

## Sample Dashboard Screenshots

*Add screenshots of your completed dashboard here for reference*

---

## Support

For issues with:
- **Looker Studio**: [Google Support](https://support.google.com/looker-studio)
- **Calypso Bot**: Check the bot logs or raise an issue on GitHub
- **Data Quality**: Verify Google Sheets data directly

---

## Changelog

| Date | Change |
|------|--------|
| 2026-01-12 | Initial dashboard documentation |
