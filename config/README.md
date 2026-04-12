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
├── rolling_put_diagonal/config/
│   ├── config.json
│   └── config.example.json
├── meic/config/                 # Legacy (replaced by HYDRA)
│   ├── config.json
│   └── config.example.json
└── hydra/config/                # LIVE — HYDRA v1.6.0
    ├── config.json
    └── config.example.json

config/
└── google_credentials.json      # Shared Google API creds (NEVER COMMIT)
```

**Note:** The Token Keeper service uses the same Saxo credentials as the trading bots.
It loads configuration from `bots/iron_fly_0dte/config/config.json` by default.

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

### MEIC (Multiple Entry Iron Condors) Strategy — STOPPED

> **Note:** MEIC has been replaced by HYDRA (v1.6.0) as of 2026-02-28. Config remains for reference.

```json
{
  "strategy": {
    "underlying_symbol": "SPXW",
    "underlying_uic": 4913,
    "entry_times_et": ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30"],
    "target_delta": 10,
    "spread_width": 50,
    "max_vix_entry": 25.0,
    "stop_loss_credit_multiplier": 1.0,
    "position_size": 1
  }
}
```

### HYDRA (Trend Following MEIC) Strategy — LIVE (v1.6.0)

```json
{
  "strategy": {
    "strategy_type": "hydra",
    "underlying_symbol": "SPXW",
    "underlying_uic": 4913,
    "spx_index_uic": 4913,
    "entry_times_et": ["10:05", "10:35", "11:05", "11:35", "12:05"],
    "contracts_per_entry": 1,
    "max_vix_entry": 25.0,
    "stop_commission_buffer": 0.15,

    "call_starting_otm_multiplier": 3.5,
    "put_starting_otm_multiplier": 4.0,
    "call_min_spread_width": 25,
    "put_min_spread_width": 25,
    "max_spread_width": 110,
    "spread_vix_multiplier": 6.0,

    "min_call_credit": 2.00,
    "min_put_credit": 2.75,
    "call_credit_floor": 0.75,
    "put_credit_floor": 2.00,
    "call_stop_buffer": 0.35,
    "put_stop_buffer": 1.55,

    "early_close_enabled": false,
    "early_close_roc_threshold": 0.03
  }
}
```

**Key HYDRA config params (v1.22.3, 2026-04-12):**
| Key | Default | Description |
|-----|---------|-------------|
| `entry_times_et` | ["10:15", "10:45", "11:15"] | 3 base entries at 30-min intervals (E6 upday at 14:00 if enabled) |
| `call_starting_otm_multiplier` | 3.5 | MKT-024: Starting OTM distance for calls (× VIX-adjusted delta) |
| `put_starting_otm_multiplier` | 4.0 | MKT-024: Starting OTM distance for puts (wider due to put skew) |
| `call_min_spread_width` | 25 | MKT-027: Call spread floor (points) |
| `put_min_spread_width` | 25 | MKT-027: Put spread floor (points) |
| `max_spread_width` | 110 | MKT-027: Spread cap for margin (5 × 110pt × $100 = $55,000) |
| `spread_vix_multiplier` | 6.0 | MKT-027: VIX-scaled formula: `round(VIX × 6.0 / 5) × 5`, floor 25, cap 110 |
| `min_call_credit` | 2.00 | MKT-011: Call credit gate ($2.00 per spread, fallback $0.75) |
| `min_put_credit` | 2.75 | MKT-011: Put credit gate ($2.75 per spread, fallback $2.00) |
| `call_credit_floor` | 0.75 | MKT-029: Graduated fallback floor for call credit |
| `put_credit_floor` | 2.00 | MKT-029: Graduated fallback floor for put credit |
| `call_stop_buffer` | 0.35 | Asymmetric stop buffer for calls ($0.35 per contract, × 100 in config) |
| `put_stop_buffer` | 1.55 | Asymmetric stop buffer for puts ($1.55 per contract, × 100 in config) |
| `early_close_enabled` | false | MKT-018: Intentionally disabled (hold-to-expiry outperforms on 1-min data) |
| `early_close_roc_threshold` | 0.03 | MKT-018: ROC threshold (only used when MKT-018 enabled) |

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

**Last Updated:** 2026-03-03
