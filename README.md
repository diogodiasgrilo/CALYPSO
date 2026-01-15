# CALYPSO - Trading Bot Platform

Automated options trading platform implementing multiple strategies using the Saxo Bank OpenAPI.

## Trading Strategies

### 1. Delta Neutral Strategy (Brian's Strategy)
**Long Straddle + Weekly Short Strangles** with 5-point recentering on SPY:
- Buy ATM long straddle (90-120 DTE) when VIX < 18
- Sell weekly short strangles at 1.5-2x expected move
- Recenter if SPY moves 5+ points from initial strike
- Roll shorts on Friday or if challenged
- Exit when longs reach 30-60 DTE

### 2. 0DTE Iron Fly Strategy (Doc Severson's Strategy)
**0DTE Iron Butterfly** with opening range filter on SPX:
- Monitor opening range (9:30-10:00 AM EST)
- Enter at 10:00 AM if VIX < 20 and price within range
- Sell ATM iron butterfly with wings at expected move
- Take profit at $50-$100 per contract
- Stop loss when price touches wing strikes
- Average hold time: 18 minutes

## Project Structure

```
calypso/
├── shared/                          # Shared infrastructure
│   ├── saxo_client.py              # Saxo Bank API client
│   ├── logger_service.py           # Google Sheets + logging
│   ├── config_loader.py            # Config management
│   ├── market_hours.py             # Market hours utilities
│   ├── secret_manager.py           # GCP secrets
│   └── external_price_feed.py      # Yahoo Finance fallback
│
├── bots/
│   ├── delta_neutral/              # Brian's Strategy
│   │   ├── main.py                 # Entry point
│   │   ├── strategy.py             # Strategy logic
│   │   └── config/                 # Bot-specific config
│   │
│   └── iron_fly_0dte/              # Doc Severson's Strategy
│       ├── main.py                 # Entry point
│       ├── strategy.py             # Strategy logic
│       └── config/                 # Bot-specific config
│
├── deploy/                          # Deployment files
│   ├── delta_neutral.service       # Systemd for bot 1
│   ├── iron_fly_0dte.service       # Systemd for bot 2
│   └── setup_vm.sh                 # GCP VM setup
│
├── docs/                            # Documentation
├── scripts/                         # Utility scripts
├── data/                            # Persistent data
├── logs/                            # Log files
├── requirements.txt                 # Dependencies
└── README.md
```

## Quick Start

### 1. Install Dependencies

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
# Delta Neutral Bot
cp bots/delta_neutral/config/config.example.json bots/delta_neutral/config/config.json
# Edit with your Saxo API credentials

# Iron Fly Bot
cp bots/iron_fly_0dte/config/config.example.json bots/iron_fly_0dte/config/config.json
# Edit with your Saxo API credentials
```

### 3. Run

```bash
# Delta Neutral Bot
python -m bots.delta_neutral.main --dry-run        # Simulation mode
python -m bots.delta_neutral.main --live           # Live trading
python -m bots.delta_neutral.main --status         # Check status

# Iron Fly Bot
python -m bots.iron_fly_0dte.main --dry-run        # Simulation mode
python -m bots.iron_fly_0dte.main --live           # Live trading
python -m bots.iron_fly_0dte.main --calibrate 25   # Manual expected move
```

## GCP VM Deployment

Both bots can run as separate systemd services on the same VM:

```bash
# Install services
sudo cp deploy/delta_neutral.service /etc/systemd/system/
sudo cp deploy/iron_fly_0dte.service /etc/systemd/system/
sudo systemctl daemon-reload

# Start Delta Neutral
sudo systemctl enable delta_neutral
sudo systemctl start delta_neutral

# Start Iron Fly
sudo systemctl enable iron_fly_0dte
sudo systemctl start iron_fly_0dte

# View logs
sudo journalctl -u delta_neutral -f
sudo journalctl -u iron_fly_0dte -f
```

## Documentation

- **[Quick Start Guide](docs/QUICK_START.md)** - Detailed setup instructions
- **[Google Sheets Logging](docs/GOOGLE_SHEETS_QUICK_START.md)** - Trade logging setup
- **[VM Operations](docs/VM_OPERATIONS.md)** - GCP VM management
- **[Configuration Guide](config/README.md)** - Configuration reference

## Security

**Never commit these files:**
- `bots/*/config/config.json` - Contains API keys
- `config/google_credentials.json` - Google API credentials
- `logs/` - May contain sensitive data

These are automatically ignored by `.gitignore`.

## Features

**Shared Infrastructure:**
- WebSocket real-time price streaming
- Circuit breaker for error handling
- Token persistence & auto-refresh
- External price feed fallback (Yahoo Finance)
- Multi-currency support (USD/EUR)
- Google Sheets logging for Looker dashboards

**Delta Neutral Bot:**
- VIX-based entry filtering
- Automatic recentering on price moves
- Fed meeting blackout periods
- ITM prevention for short options
- Emergency exit on large moves

**Iron Fly Bot:**
- Opening range filter (trend day detection)
- VIX level and spike filters
- Calibration mode for manual expected move
- Fast exit on wing breach (stop loss)
- Time-based exit

## Requirements

- Python 3.8+
- Saxo Bank account with API access
- Required subscriptions:
  - NYSE (AMEX and ARCA), Bats - Level 1
  - CBOE Indices - Level 1
  - OPRA Options Data

## Disclaimer

This software trades with real money. Use at your own risk. Past performance does not guarantee future results. Always test thoroughly with `--dry-run` before live trading.

---

**Version:** 2.0.0
**Last Updated:** 2025-01-15
