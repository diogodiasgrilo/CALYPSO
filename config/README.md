# Configuration Reference

Each bot has its own configuration file in `bots/{bot_name}/config/config.json`.

---

## Directory Structure

```
bots/
├── delta_neutral/config/
│   ├── config.json              # Active config (NEVER COMMIT)
│   └── config.example.json      # Template
├── iron_fly_0dte/config/
│   ├── config.json
│   └── config.example.json
└── rolling_put_diagonal/config/
    ├── config.json
    └── config.example.json

config/
└── google_credentials.json      # Shared Google API creds (NEVER COMMIT)
```

---

## Setup

```bash
# For each bot you want to run:
cd bots/delta_neutral/config
cp config.example.json config.json
# Edit config.json with your credentials
```

---

## Common Configuration Sections

### Saxo API (All Bots)

```json
{
  "saxo_api": {
    "environment": "live",
    "sim": {
      "app_key": "YOUR_SIM_KEY",
      "app_secret": "YOUR_SIM_SECRET",
      "access_token": "",
      "refresh_token": "",
      "token_expiry": ""
    },
    "live": {
      "app_key": "YOUR_LIVE_KEY",
      "app_secret": "YOUR_LIVE_SECRET",
      "access_token": "",
      "refresh_token": "",
      "token_expiry": ""
    },
    "base_url_sim": "https://gateway.saxobank.com/sim/openapi",
    "base_url_live": "https://gateway.saxobank.com/openapi"
  }
}
```

### Google Sheets (All Bots)

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

### Circuit Breaker (All Bots)

```json
{
  "circuit_breaker": {
    "max_consecutive_errors": 5,
    "max_disconnection_seconds": 60,
    "cooldown_minutes": 15,
    "auto_reset_if_safe": true
  }
}
```

### Currency Conversion (All Bots)

```json
{
  "currency": {
    "base_currency": "USD",
    "account_currency": "EUR",
    "eur_usd_uic": 21,
    "enabled": true,
    "cache_rate_seconds": 300
  }
}
```

---

## Bot-Specific Configuration

### Delta Neutral Strategy

```json
{
  "strategy": {
    "underlying_symbol": "SPY",
    "underlying_uic": 36590,
    "vix_symbol": "VIX",
    "vix_uic": 10606,
    "max_vix_entry": 18.0,
    "vix_defensive_threshold": 25.0,
    "long_straddle_min_dte": 90,
    "long_straddle_max_dte": 120,
    "recenter_threshold_points": 5.0,
    "weekly_target_return_percent": 1.0,
    "exit_dte_min": 30,
    "exit_dte_max": 60,
    "roll_days": ["Friday"],
    "max_bid_ask_spread_percent": 15,
    "order_timeout_seconds": 60,
    "position_size": 1,
    "fed_blackout_days": 2,
    "emergency_exit_percent": 5.0
  }
}
```

### Iron Fly 0DTE Strategy

```json
{
  "strategy": {
    "underlying_symbol": "US500.I",
    "underlying_uic": 4913,
    "vix_symbol": "VIX",
    "vix_uic": 10606,
    "entry_time_est": "10:00",
    "opening_range_minutes": 30,
    "max_vix_entry": 20.0,
    "vix_spike_threshold_percent": 5.0,
    "profit_target_per_contract": 75.0,
    "max_hold_minutes": 60,
    "stop_loss_type": "wing_touch",
    "position_size": 1
  }
}
```

### Rolling Put Diagonal Strategy

```json
{
  "strategy": {
    "underlying_symbol": "QQQ",
    "underlying_uic": 4328771,
    "long_put_dte": 14,
    "long_put_delta": -0.33,
    "short_put_dte": 1,
    "short_put_delta": -0.50,
    "position_size": 1
  }
}
```

---

## Security

**NEVER commit these files to git:**
- `bots/*/config/config.json` - Contains API keys
- `config/google_credentials.json` - Google API credentials

These are automatically ignored by `.gitignore`.

---

## GCP Deployment

On GCP, sensitive credentials are loaded from Secret Manager:
- `calypso-saxo-credentials` - Saxo API tokens
- `calypso-google-sheets-credentials` - Google Sheets credentials

Bot-specific settings (strategy parameters) still come from each bot's `config.json`.

---

**Last Updated:** 2026-01-23
