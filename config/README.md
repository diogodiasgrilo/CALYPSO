# config/ - Configuration Files

This directory contains shared configuration templates. Each bot has its own `config/` directory with bot-specific settings.

## Directory Structure

```
config/
├── README.md                    # This file
└── (shared templates if any)

bots/delta_neutral/config/
├── config.json                  # Active config (NEVER COMMIT)
├── config.example.json          # Template
└── google_credentials.json      # Google API creds (NEVER COMMIT)

bots/iron_fly_0dte/config/
├── config.json                  # Active config (NEVER COMMIT)
├── config.example.json          # Template
└── google_credentials.json      # Google API creds (NEVER COMMIT)
```

## Setup

### Delta Neutral Bot

```bash
cd bots/delta_neutral/config
cp config.example.json config.json
# Edit config.json with your credentials
```

### Iron Fly Bot

```bash
cd bots/iron_fly_0dte/config
cp config.example.json config.json
# Edit config.json with your credentials
```

## Configuration Reference

### Saxo API Section (Both Bots)

```json
{
  "saxo_api": {
    "environment": "sim",           // "sim" or "live"
    "sim": {
      "app_key": "YOUR_SIM_KEY",
      "app_secret": "YOUR_SIM_SECRET"
    },
    "live": {
      "app_key": "YOUR_LIVE_KEY",
      "app_secret": "YOUR_LIVE_SECRET"
    }
  }
}
```

### Google Sheets Section

```json
{
  "google_sheets": {
    "enabled": true,
    "credentials_file": "config/google_credentials.json",
    "spreadsheet_name": "Your_Spreadsheet_Name",
    "strategy_type": "delta_neutral",    // or "iron_fly"
    "include_opening_range": false       // true for iron_fly only
  }
}
```

**Strategy Types:**
- `delta_neutral` - Creates worksheets for theta tracking, straddle/strangle P&L
- `iron_fly` - Creates worksheets for 0DTE metrics, opening range tracking

### Delta Neutral Strategy Parameters

```json
{
  "strategy": {
    "max_vix_entry": 18.0,              // Only enter when VIX < 18
    "long_straddle_min_dte": 90,        // Min DTE for long straddle
    "long_straddle_max_dte": 120,       // Max DTE for long straddle
    "recenter_threshold_points": 5.0,   // Recenter on 5-point move
    "exit_dte_min": 30,                 // Exit window start
    "exit_dte_max": 60,                 // Exit window end
    "fed_blackout_days": 2,             // Days before FOMC to avoid
    "emergency_exit_percent": 5.0       // Hard exit threshold
  }
}
```

### Iron Fly Strategy Parameters

```json
{
  "strategy": {
    "underlying_symbol": "SPX",         // SPX for index options
    "entry_time_est": "10:00",          // Enter at 10:00 AM EST
    "opening_range_minutes": 30,        // Track 9:30-10:00 range
    "max_vix_entry": 20.0,              // Only enter when VIX < 20
    "vix_spike_threshold_percent": 5.0, // Abort on VIX spike
    "profit_target_per_contract": 75.0, // Target $75/contract
    "max_hold_minutes": 60,             // Exit after 60 min
    "stop_loss_type": "wing_touch"      // Exit on wing breach
  }
}
```

### Circuit Breaker (Both Bots)

```json
{
  "circuit_breaker": {
    "max_consecutive_errors": 3,        // Errors before halt
    "max_disconnection_seconds": 60,    // Disconnection tolerance
    "cooldown_minutes": 15              // Cooldown period
  }
}
```

## Security

⚠️ **NEVER commit these files to git:**
- `config.json` - Contains API keys
- `google_credentials.json` - Contains Google API credentials

These files are automatically ignored by `.gitignore`.

## GCP Deployment

When running on GCP, credentials are loaded from Secret Manager:
- Saxo API credentials from `saxo-api-credentials`
- Google Sheets credentials from `google-sheets-credentials`

Bot-specific settings (strategy parameters, spreadsheet names) still come from each bot's `config.json`.
