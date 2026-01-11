# config/ - Configuration Files

This directory contains all configuration files and credentials for the bot.

## Files

### Configuration
- **`config.json`** - Main configuration file (**NEVER COMMIT**)
  - Saxo API credentials (SIM and LIVE)
  - Strategy parameters
  - Logging settings
  - Account keys

- **`config.example.json`** - Template configuration
  - Use this as a starting point
  - Copy to `config.json` and fill in your credentials

### Credentials
- **`google_credentials.json`** - Google Sheets API credentials (**NEVER COMMIT**)
  - Optional: Only needed if using Google Sheets logging

## Setup

1. Copy the example config:
   ```bash
   cp config/config.example.json config/config.json
   ```

2. Edit `config/config.json` with your Saxo credentials:
   ```json
   {
     "saxo_api": {
       "live": {
         "app_key": "your_app_key_here",
         "app_secret": "your_app_secret_here"
       }
     }
   }
   ```

3. (Optional) Add Google Sheets credentials if needed

## Security

⚠️ **NEVER commit config.json or google_credentials.json to git!**

These files contain sensitive API keys and secrets. They are automatically ignored by `.gitignore`.

## Configuration Sections

### Strategy Parameters
- `max_vix_entry`: 18.0 - Only enter when VIX < 18
- `long_straddle_min_dte`: 90 - Minimum days to expiration for longs
- `long_straddle_max_dte`: 120 - Maximum days to expiration for longs
- `recenter_threshold_points`: 5.0 - Recenter if price moves 5+ points
- `exit_dte_min`: 30 - Exit when longs reach 30-60 DTE
- `exit_dte_max`: 60
- `fed_blackout_days`: 2 - Don't enter 2 days before FOMC
- `emergency_exit_percent`: 5.0 - Hard exit on 5%+ move

### Circuit Breaker
- `max_consecutive_errors`: 3 - Halt after 3 errors
- `max_disconnection_seconds`: 60 - Halt if disconnected 60s
- `cooldown_minutes`: 15 - Cooldown period

### Logging
- `log_file`: "logs/bot_log.txt"
- `log_level`: "INFO"
- `console_output`: true
