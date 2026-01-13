# Calypso - Delta Neutral Trading Bot

Automated options trading bot implementing a delta-neutral income strategy on SPY using the Saxo Bank OpenAPI.

## 🎯 Strategy

**Long Straddle + Weekly Short Strangles** with 5-point recentering:

1. Buy ATM long straddle (90-120 DTE) when VIX < 18
2. Sell weekly short strangles at 1.5-2x expected move
3. Recenter if SPY moves 5+ points from initial strike
4. Roll shorts on Friday or if challenged
5. Exit when longs reach 30-60 DTE

**Safety Mechanisms:**
- Fed meeting blackout filter (2 days before FOMC)
- ITM prevention (never let shorts expire ITM)
- Emergency exit on 5%+ moves

## 📁 Project Structure

\`\`\`
Calypso/
├── src/              # Core application code
│   ├── main.py                 # Entry point
│   ├── strategy.py             # Strategy logic
│   ├── saxo_client.py          # API client
│   ├── logger_service.py       # Logging
│   └── external_price_feed.py  # Yahoo Finance fallback
│
├── config/           # Configuration & credentials
│   ├── config.json             # Main config (gitignored)
│   ├── config.example.json     # Config template
│   └── google_credentials.json # Google Sheets (optional)
│
├── docs/             # Documentation
│   ├── QUICK_START.md          # Quick start guide
│   ├── strategy/               # Strategy docs
│   └── development/            # Dev history
│
├── scripts/          # Utility scripts
│   ├── search/                 # Instrument search
│   └── tests/                  # Test scripts
│
├── logs/             # Log files
│   └── bot_log.txt             # Main log
│
├── requirements.txt  # Python dependencies
└── README.md         # This file
\`\`\`

## 🚀 Quick Start

### 1. Install Dependencies

\`\`\`bash
pip install -r requirements.txt
\`\`\`

### 2. Configure

\`\`\`bash
cp config/config.example.json config/config.json
# Edit config/config.json with your Saxo API credentials
\`\`\`

### 3. Run

\`\`\`bash
# Dry-run with live data (no orders placed)
python src/main.py --live --dry-run

# Live trading
python src/main.py --live

# Check status
python src/main.py --status
\`\`\`

## 📖 Documentation

- **[Quick Start Guide](docs/QUICK_START.md)** - Detailed setup instructions
- **[Google Sheets Logging](docs/GOOGLE_SHEETS_QUICK_START.md)** - Enable comprehensive trade logging
- **[Strategy Documentation](docs/strategy/)** - How the strategy works
- **[Development Documentation](docs/development/)** - Implementation details & changes
- **[Configuration Guide](config/README.md)** - Configuration reference
- **[Scripts Guide](scripts/README.md)** - Utility scripts

## 🔐 Security

⚠️ **Never commit these files:**
- \`config/config.json\` - Contains API keys
- \`config/google_credentials.json\` - Google API credentials
- \`logs/\` - May contain sensitive data

These are automatically ignored by \`.gitignore\`.

## 📊 Features

- ✅ Delta-neutral options strategy
- ✅ Automatic recentering on price moves
- ✅ VIX-based entry filtering
- ✅ Fed meeting blackout periods
- ✅ ITM prevention for short options
- ✅ Emergency exit on large moves
- ✅ WebSocket real-time streaming
- ✅ Circuit breaker for error handling
- ✅ Token persistence & auto-refresh
- ✅ External price feed fallback
- ✅ Multi-currency support (USD/EUR)
- ✅ Trade logging (file, Google Sheets, Excel)

## 🛠️ Requirements

- Python 3.8+
- Saxo Bank account with API access
- Required subscriptions:
  - NYSE (AMEX and ARCA), Bats - Level 1
  - CBOE Indices - Level 1
  - OPRA Options Data

## ⚙️ Configuration

Key strategy parameters in \`config/config.json\`:

\`\`\`json
{
  "strategy": {
    "max_vix_entry": 18.0,
    "long_straddle_min_dte": 90,
    "long_straddle_max_dte": 120,
    "recenter_threshold_points": 5.0,
    "exit_dte_min": 30,
    "exit_dte_max": 60,
    "fed_blackout_days": 2,
    "emergency_exit_percent": 5.0
  }
}
\`\`\`

## 🔍 Testing

Use test scripts to verify connectivity:

\`\`\`bash
python scripts/tests/test_spy_price.py
python scripts/tests/test_live_with_external_feed.py
\`\`\`

## 📝 Logging

Logs are written to \`logs/bot_log.txt\` with INFO level by default.

View recent activity:
\`\`\`bash
tail -f logs/bot_log.txt
\`\`\`

## 🐛 Troubleshooting

1. **Authentication fails:** Check token_expiry in config/config.json
2. **Price feeds return NoAccess:** Verify subscriptions are active
3. **Circuit breaker triggered:** Review logs/bot_log.txt
4. **Options not found:** Use scripts/search/ to find correct UICs

## ⚠️ Disclaimer

This bot trades with real money. Use at your own risk. Past performance does not guarantee future results. Always test thoroughly with --dry-run before live trading.

---

**Current Version:** 1.0.0  
**Last Updated:** 2026-01-11
