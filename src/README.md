# src/ - Core Application Code

This directory contains all the core Python modules that make up the Calypso trading bot.

## Files

### Main Application
- **`main.py`** - Entry point for the bot. Run this to start trading.
  ```bash
  python src/main.py --live --dry-run
  ```

### Strategy Implementation
- **`strategy.py`** - Implements the Delta Neutral strategy:
  - Long straddle entry/exit
  - Short strangle management
  - 5-point recentering
  - Roll management
  - Fed meeting filters
  - ITM prevention
  - Emergency exits

### API Client
- **`saxo_client.py`** - Saxo Bank OpenAPI client:
  - OAuth2 authentication
  - REST API calls
  - WebSocket streaming
  - Circuit breaker pattern
  - Token persistence

### Services
- **`logger_service.py`** - Logging infrastructure:
  - File logging
  - Google Sheets integration
  - Excel integration
  - Trade tracking
  - Currency conversion

- **`external_price_feed.py`** - Yahoo Finance fallback:
  - SPY price feeds
  - VIX price feeds
  - Used when Saxo feeds unavailable

## Module Structure

```
src/
├── main.py                 # Entry point
├── strategy.py             # Strategy logic
├── saxo_client.py          # API client
├── logger_service.py       # Logging
└── external_price_feed.py  # External feeds
```

## Usage

Run from the project root:

```bash
# Dry-run with live data
python src/main.py --live --dry-run

# Live trading
python src/main.py --live

# Check status
python src/main.py --status
```
