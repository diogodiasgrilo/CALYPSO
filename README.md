# CALYPSO - Automated Options Trading Platform

Multi-strategy options trading platform using Saxo Bank API, running on Google Cloud VM.

**Repository:** https://github.com/diogodiasgrilo/CALYPSO

---

## Trading Strategies

### 1. Delta Neutral (SPY)
**Long Straddle + Weekly Short Strangles** with 5-point recentering:
- Buy ATM long straddle (90-120 DTE) when VIX < 18
- Sell weekly short strangles at 1.5-2x expected move
- Recenter if SPY moves 5+ points from initial strike
- Roll shorts on Friday or if challenged
- Exit when longs reach 30-60 DTE

### 2. Iron Fly 0DTE (S&P 500)
**0DTE Iron Butterfly** with opening range filter:
- Monitor opening range (9:30-10:00 AM ET)
- Enter at 10:00 AM if VIX < 20 and price within range
- Sell ATM iron butterfly with wings at expected move
- Take profit at $75 per contract
- Stop loss when price touches wing strikes
- Max hold time: 60 minutes

### 3. Rolling Put Diagonal (QQQ)
**Bill Belt's Rolling Put Diagonal** strategy:
- Buy long put (14 DTE, -0.33 delta)
- Sell short put (1 DTE, ATM)
- Roll short put daily for income
- Roll long put when approaching expiry

---

## Project Structure

```
calypso/
├── bots/
│   ├── delta_neutral/           # SPY strategy
│   │   ├── main.py
│   │   ├── strategy.py          # Core trading logic (~8000 lines)
│   │   ├── models/              # Data models (extracted)
│   │   │   ├── states.py        # PositionType, StrategyState enums
│   │   │   ├── positions.py     # Option/Straddle/Strangle dataclasses
│   │   │   └── metrics.py       # Performance tracking
│   │   ├── safety/              # Safety documentation
│   │   │   └── __init__.py      # Safety architecture docs
│   │   └── config/config.json
│   ├── iron_fly_0dte/           # S&P 500 0DTE strategy
│   │   ├── main.py
│   │   ├── strategy.py
│   │   └── config/config.json
│   └── rolling_put_diagonal/    # QQQ strategy
│       ├── main.py
│       ├── strategy.py
│       └── config/config.json
├── shared/                      # Shared infrastructure
│   ├── saxo_client.py          # Saxo Bank API client
│   ├── logger_service.py       # Logging + Google Sheets
│   ├── market_hours.py         # Market hours + holidays
│   ├── token_coordinator.py    # Multi-bot token sharing
│   └── config_loader.py        # Config management
├── scripts/                     # Utility scripts
├── logs/                        # Log files (per bot)
├── data/                        # Persistent metrics
└── docs/                        # Documentation
```

---

## Quick Start (Local Development)

### 1. Install Dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure
```bash
# Copy example config for each bot you want to run
cp bots/delta_neutral/config/config.example.json bots/delta_neutral/config/config.json
# Edit with your Saxo API credentials
```

### 3. Run
```bash
# Dry run (simulation - no real trades)
python -m bots.delta_neutral.main --live --dry-run

# Live trading (real money)
python -m bots.delta_neutral.main --live

# Check status only
python -m bots.delta_neutral.main --status
```

---

## GCP VM Deployment

All 3 bots run as systemd services on a single GCP VM (`calypso-bot`, zone `us-east1-b`).

### SSH Access
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b
```

### Bot Management
```bash
# Start/stop/restart individual bots
sudo systemctl start delta_neutral
sudo systemctl stop iron_fly_0dte
sudo systemctl restart rolling_put_diagonal

# Start/stop ALL bots
sudo systemctl start delta_neutral iron_fly_0dte rolling_put_diagonal
sudo systemctl stop delta_neutral iron_fly_0dte rolling_put_diagonal

# Emergency kill (immediate)
sudo systemctl kill -s SIGKILL delta_neutral
```

### View Logs
```bash
# Combined monitor log (all bots)
tail -f /opt/calypso/logs/monitor.log

# Individual bot logs
sudo journalctl -u delta_neutral -f
sudo journalctl -u iron_fly_0dte -f
sudo journalctl -u rolling_put_diagonal -f

# Quick status check
/opt/calypso/scripts/bot_status.sh
```

### Deploy Updates
```bash
cd /opt/calypso
sudo -u calypso git pull
sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal
```

---

## Log Files

All timestamps are in **Eastern Time (ET)** to match NYSE trading hours.

| Log | Location | Description |
|-----|----------|-------------|
| Combined Monitor | `logs/monitor.log` | Key events from all bots |
| Delta Neutral | `logs/delta_neutral/bot.log` | Full logs for DN bot |
| Iron Fly 0DTE | `logs/iron_fly_0dte/bot.log` | Full logs for IF bot |
| Rolling Put Diagonal | `logs/rolling_put_diagonal/bot.log` | Full logs for RPD bot |

---

## Documentation

- **[VM Commands Reference](docs/VM_COMMANDS.md)** - Complete VM command reference
- **[Google Sheets Setup](docs/GOOGLE_SHEETS.md)** - Trade logging setup
- **[Deployment Guide](docs/DEPLOYMENT.md)** - GCP deployment instructions
- **[Delta Neutral Edge Cases](docs/DELTA_NEUTRAL_EDGE_CASES.md)** - Risk analysis (44 edge cases)
- **[Iron Fly Edge Cases](docs/IRON_FLY_EDGE_CASES.md)** - Risk analysis (63 edge cases)
- **[Iron Fly Code Audit](docs/IRON_FLY_CODE_AUDIT.md)** - Pre-LIVE comprehensive code review
- **[Configuration Reference](config/README.md)** - Config file reference

---

## Features

**Shared Infrastructure:**
- WebSocket real-time price streaming from Saxo
- Circuit breaker for error handling
- Token persistence & auto-refresh
- Multi-bot token coordination
- External price feed fallback (Yahoo Finance)
- Multi-currency support (USD/EUR conversion)
- Google Sheets logging with strategy-specific dashboards
- US market holiday detection (all NYSE holidays)
- Intelligent sleep during market closures

**Safety Features (All Bots):**
- Circuit breaker (halts trading after consecutive failures)
- Action cooldowns (prevents rapid retry loops)
- Fed meeting blackout periods (2 days before FOMC)
- ITM prevention for short options
- Emergency exit on large moves (5%+)
- VIX-based entry filtering
- Position recovery on restart

**Delta Neutral Advanced Safety (44 edge cases covered):**
- Progressive order retry (0% → 5% → 10% slippage → MARKET)
- Partial fill fallback handlers (6 emergency scenarios)
- Emergency position handlers (close naked shorts, protect straddle)
- Orphaned order tracking (blocks trading until resolved)
- ITM risk detection (0.3% threshold, 30s checks) with emergency roll
- Auto-sync with Saxo before all emergency actions
- Critical intervention flag (halts trading until human review)
- Flash crash velocity detection (MKT-002 - 2%+ in 5 min)
- Position reconciliation (POS-003 - hourly Saxo sync)
- Token refresh on 401 (CONN-004) and rate limiting (CONN-006)
- Half-day closure detection (TIME-003)
- Market open delay for quote stability (TIME-005)
- Invalid quote detection (DATA-004 - Bid=0/Ask=0)
- See [Edge Cases Doc](docs/DELTA_NEUTRAL_EDGE_CASES.md) for full analysis
- See `bots/delta_neutral/safety/__init__.py` for implementation docs

**Iron Fly 0DTE Advanced Safety (63 edge cases covered):**
- Entry order: Longs first (Long Call → Long Put → Short Call → Short Put)
- Entry retries: 3 attempts with 15-second delays; auto-unwind filled legs on failure
- Stop losses: Software-based via 2-second polling (NOT broker-side stops)
- Wing breach tolerance: $0.10 buffer to avoid floating-point issues
- Circuit breaker: 5 consecutive failures or 5-of-10 sliding window triggers halt
- Daily circuit breaker escalation: 3 opens = daily halt
- Stop loss retry escalation: 5 retries per leg with extreme spread warning
- Position recovery on crash with metadata persistence
- Multiple iron fly detection and auto-selection
- Multi-bot token coordination (WebSocket 401 fix)
- See [Edge Cases Doc](docs/IRON_FLY_EDGE_CASES.md) for full analysis
- See [Code Audit](docs/IRON_FLY_CODE_AUDIT.md) for pre-LIVE review

---

## Requirements

- Python 3.8+
- Saxo Bank account with API access
- Required market data subscriptions:
  - NYSE (AMEX and ARCA), Bats - Level 1
  - CBOE Indices - Level 1
  - OPRA Options Data

---

## Security

**Never commit these files:**
- `bots/*/config/config.json` - Contains API keys
- `config/google_credentials.json` - Google API credentials
- `logs/` - May contain sensitive data

These are automatically ignored by `.gitignore`.

---

## Disclaimer

This software trades with real money. Use at your own risk. Past performance does not guarantee future results. Always test thoroughly with `--dry-run` before live trading.

---

**Version:** 3.2.0
**Last Updated:** 2026-01-23
