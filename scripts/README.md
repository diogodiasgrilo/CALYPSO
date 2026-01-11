# scripts/ - Utility and Test Scripts

This directory contains helper scripts for development, testing, and instrument discovery.

## Directory Structure

```
scripts/
├── search/     # Instrument search utilities
└── tests/      # Test and validation scripts
```

## Search Scripts (`scripts/search/`)

Tools for finding UICs (Unique Instrument Codes) in the Saxo API:

- **`find_spy_uic.py`** - Find the UIC for SPY ETF
- **`find_vix.py`** - Find the UIC for VIX index
- **`find_strategy_uics.py`** - Find all required UICs for the strategy
- **`search_instruments.py`** - General instrument search tool
- **`search_sp500_alternatives.py`** - Find S&P 500 related instruments

### Usage Example
```bash
python scripts/search/find_spy_uic.py
python scripts/search/find_vix.py
```

## Test Scripts (`scripts/tests/`)

Scripts for testing API connectivity and data retrieval:

- **`test_spy_price.py`** - Test SPY price fetching
- **`test_spy_quote.py`** - Test SPY quote retrieval
- **`test_spy_asset_types.py`** - Test different asset type queries
- **`test_live_with_external_feed.py`** - Test external Yahoo Finance fallback

### Usage Example
```bash
python scripts/tests/test_spy_price.py
python scripts/tests/test_live_with_external_feed.py
```

## When to Use These Scripts

### Search Scripts
- Setting up a new instrument or strategy
- Verifying UIC codes before adding to config
- Exploring available instruments on Saxo
- Finding alternative tickers

### Test Scripts
- Troubleshooting price feed issues
- Verifying API connectivity
- Testing authentication
- Validating external feed fallback
- During development of new features

## Note

These scripts are for **development and testing only**. They are not part of the main trading bot and should not be run during live trading.

All scripts assume they're being run from the project root directory.
