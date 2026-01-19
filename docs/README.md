# docs/ - Documentation

This directory contains all documentation for the Calypso trading bot platform.

## Directory Structure

```
docs/
├── README.md                        # This file (main docs entry point)
├── QUICK_START.md                   # Quick start guide
├── GOOGLE_SHEETS_QUICK_START.md     # Google Sheets setup guide
├── GOOGLE_SHEETS_LOGGING.md         # Complete Google Sheets logging reference
├── VM_OPERATIONS.md                 # GCP VM management
├── strategy/                        # Strategy documentation
│   ├── SAXO_API_ANALYSIS.md
│   ├── PRICING_ANALYSIS.md
│   ├── SUBSCRIPTION_ANALYSIS.md
│   └── SAFETY_FEATURES_ADDED.md
├── deployment/                      # Cloud deployment guides
│   └── GCP_MIGRATION.md             # Google Cloud Platform deployment
└── development/                     # Development history & implementation
    ├── CHANGES_LOG.md
    ├── SESSION_SUMMARY.md
    ├── FIXES_APPLIED.md
    ├── ENVIRONMENT_SWITCHING.md
    ├── MARKET_DATA_ACCESS.md
    └── GOOGLE_SHEETS_IMPLEMENTATION.md
```

## Quick Reference

### Getting Started
- **[QUICK_START.md](QUICK_START.md)** - Start here! Quick setup guide
- **[GOOGLE_SHEETS_QUICK_START.md](GOOGLE_SHEETS_QUICK_START.md)** - Quick 5-minute Google Sheets setup
- **[GOOGLE_SHEETS_LOGGING.md](GOOGLE_SHEETS_LOGGING.md)** - Complete reference for logging system
- **[VM_OPERATIONS.md](VM_OPERATIONS.md)** - GCP VM management commands

### Strategy Documentation (`docs/strategy/`)

- **[SAXO_API_ANALYSIS.md](strategy/SAXO_API_ANALYSIS.md)** - How options chain API works
- **[PRICING_ANALYSIS.md](strategy/PRICING_ANALYSIS.md)** - Price feed analysis
- **[SUBSCRIPTION_ANALYSIS.md](strategy/SUBSCRIPTION_ANALYSIS.md)** - Required market data subscriptions
- **[SAFETY_FEATURES_ADDED.md](strategy/SAFETY_FEATURES_ADDED.md)** - Fed filter, ITM prevention, emergency exits

### Cloud Deployment (`docs/deployment/`)

- **[GCP_MIGRATION.md](deployment/GCP_MIGRATION.md)** - Complete guide for deploying to Google Cloud Platform (~$15/month)

### Development History (`docs/development/`)

- **[CHANGES_LOG.md](development/CHANGES_LOG.md)** - Detailed changelog of all fixes
- **[SESSION_SUMMARY.md](development/SESSION_SUMMARY.md)** - Session summaries
- **[GOOGLE_SHEETS_IMPLEMENTATION.md](development/GOOGLE_SHEETS_IMPLEMENTATION.md)** - Technical implementation details

---

## Strategy Overview

### Strategy 1: Delta Neutral (Brian's Strategy) - SPY

**Long Straddle + Weekly Short Strangles** with automatic recentering:

| Phase | Action | Details |
|-------|--------|---------|
| **Entry** | Long ATM straddle | 90-120 DTE when VIX < 18 |
| **Income** | Sell weekly strangles | 1.5-2x expected move from ATM |
| **Recenter** | Move longs if price moves | 5+ point move triggers recenter |
| **Roll** | Roll shorts weekly | Friday or when challenged |
| **Exit** | Close all positions | When longs reach 30-60 DTE |

**Safety Filters:**
- Fed meeting blackout (2 days before FOMC)
- ITM prevention (never let shorts expire ITM)
- Emergency exit (5%+ move from entry)
- VIX-based entry filtering

**Google Sheets Worksheets:**
- Trades, Positions, Daily Summary, Performance Metrics, Account Summary, Bot Logs

---

### Strategy 2: 0DTE Iron Fly (Doc Severson's Strategy) - SPX

**0DTE Iron Butterfly** with opening range filter:

| Phase | Action | Details |
|-------|--------|---------|
| **Pre-Market** | Track opening range | 9:30-10:00 AM EST high/low |
| **Filter** | Check conditions | VIX < 20, price within range |
| **Entry** | Sell ATM iron fly | At 10:00 AM if conditions met |
| **Monitor** | Watch wings | Exit if price touches wing |
| **Exit** | Take profit/stop | $75 target or wing touch |

**Entry Conditions:**
- VIX < 20 at entry time
- VIX didn't spike 5%+ during opening range
- Price stayed within opening range (no trend day)

**Exit Conditions:**
- Profit target: $50-$100 per contract
- Stop loss: Price touches either wing strike
- Time stop: Max 60 minutes hold time (average is 18 min)

**Google Sheets Worksheets:**
- Trades, Positions, Daily Summary, Performance Metrics, Account Summary, Opening Range, Bot Logs

---

## Shared Features

Both bots share common infrastructure:

### Market Hours & Holidays
- Automatic detection of US market hours (9:30 AM - 4:00 PM ET)
- Recognition of all NYSE/NASDAQ holidays:
  - New Year's Day, MLK Day, Presidents' Day, Good Friday
  - Memorial Day, Juneteenth, Independence Day, Labor Day
  - Thanksgiving, Christmas
- Intelligent sleep during market closures

### WebSocket Streaming
- Real-time price updates via Saxo WebSocket
- Automatic reconnection on disconnect
- Circuit breaker for error handling

### Token Management
- Multi-bot token sharing via coordinator
- Automatic token refresh
- Support for SIM and LIVE environments

### Google Sheets Logging
- Strategy-specific worksheet formats
- Looker dashboard integration
- Daily/weekly/cumulative metrics tracking

---

## Requirements

- Python 3.8+
- Saxo Bank account with API access
- Required market data subscriptions:
  - NYSE (AMEX and ARCA), Bats - Level 1
  - CBOE Indices - Level 1
  - OPRA Options Data

## Support

For issues or questions:
- Check the documentation in this folder first
- Review the strategy-specific config.example.json files
- Check logs in the `logs/` directory
