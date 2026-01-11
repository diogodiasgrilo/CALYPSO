# docs/ - Documentation

This directory contains all documentation for the Calypso trading bot.

## Directory Structure

```
docs/
├── README.md                        # This file (main docs entry point)
├── QUICK_START.md                   # Quick start guide
├── GOOGLE_SHEETS_QUICK_START.md     # Google Sheets setup guide
├── GOOGLE_SHEETS_LOGGING.md         # Complete Google Sheets logging reference
├── strategy/                        # Strategy documentation
│   ├── SAXO_API_ANALYSIS.md
│   ├── PRICING_ANALYSIS.md
│   ├── SUBSCRIPTION_ANALYSIS.md
│   └── SAFETY_FEATURES_ADDED.md
└── development/                     # Development history & implementation
    ├── CHANGES_LOG.md
    ├── SESSION_SUMMARY.md
    ├── FIXES_APPLIED.md
    ├── ENVIRONMENT_SWITCHING.md
    ├── MARKET_DATA_ACCESS.md
    └── GOOGLE_SHEETS_IMPLEMENTATION.md  # Technical implementation details
```

## Quick Reference

### Getting Started
- **[QUICK_START.md](QUICK_START.md)** - Start here! Quick setup guide
- **[GOOGLE_SHEETS_QUICK_START.md](GOOGLE_SHEETS_QUICK_START.md)** - **NEW!** Quick 5-minute Google Sheets setup
- **[GOOGLE_SHEETS_LOGGING.md](GOOGLE_SHEETS_LOGGING.md)** - Complete reference for 5-worksheet logging system

### Strategy Documentation (`docs/strategy/`)

- **[SAXO_API_ANALYSIS.md](strategy/SAXO_API_ANALYSIS.md)** - How options chain API works
- **[PRICING_ANALYSIS.md](strategy/PRICING_ANALYSIS.md)** - Price feed analysis
- **[SUBSCRIPTION_ANALYSIS.md](strategy/SUBSCRIPTION_ANALYSIS.md)** - Required market data subscriptions
- **[SAFETY_FEATURES_ADDED.md](strategy/SAFETY_FEATURES_ADDED.md)** - Fed filter, ITM prevention, emergency exits

### Development History (`docs/development/`)

- **[CHANGES_LOG.md](development/CHANGES_LOG.md)** - Detailed changelog of all fixes
- **[SESSION_SUMMARY.md](development/SESSION_SUMMARY.md)** - Session summaries
- **[FIXES_APPLIED.md](development/FIXES_APPLIED.md)** - Bug fixes applied
- **[ENVIRONMENT_SWITCHING.md](development/ENVIRONMENT_SWITCHING.md)** - SIM vs LIVE switching
- **[MARKET_DATA_ACCESS.md](development/MARKET_DATA_ACCESS.md)** - Market data setup
- **[GOOGLE_SHEETS_IMPLEMENTATION.md](development/GOOGLE_SHEETS_IMPLEMENTATION.md)** - Technical implementation details for Google Sheets logging

## Strategy Overview

The bot implements a **Delta Neutral Income Strategy**:

1. ✅ **Entry:** Long ATM straddle (90-120 DTE) when VIX < 18
2. ✅ **Income:** Sell weekly strangles at 1.5-2x expected move
3. ✅ **Recentering:** Recenter longs if price moves 5+ points (keep same expiry)
4. ✅ **Rolling:** Roll shorts on Thursday/Friday or if challenged
5. ✅ **Exit:** Close all positions when longs reach 30-60 DTE
6. ✅ **Safety Filters:**
   - Fed meeting blackout (2 days before FOMC)
   - ITM prevention (never let shorts expire ITM)
   - Emergency exit (5%+ move from entry)
