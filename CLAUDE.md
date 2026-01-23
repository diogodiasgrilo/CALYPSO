# CALYPSO Trading Bot Infrastructure

## CRITICAL: Bot Control Warning

**NEVER use `kill` or `pkill` to stop bots!** All services have `Restart=always` with `RestartSec=30`. Killing a bot will cause it to auto-restart in 30 seconds. **ALWAYS use `systemctl stop`** to properly stop a bot.

---

## Project Overview

CALYPSO is a monorepo containing multiple automated options trading bots that trade SPX 0DTE options via Saxo Bank's OpenAPI. All bots run on a Google Cloud VM and use:
- **Saxo Bank OpenAPI** for order execution and market data
- **Google Secret Manager** for credentials (never in config files)
- **Google Sheets** for trade logging and dashboards
- **WebSocket streaming** for real-time price data

### Codebase Structure
```
bots/
  iron_fly_0dte/      # Doc Severson's Iron Fly strategy
  delta_neutral/      # Brian's Delta Neutral strategy
  rolling_put_diagonal/  # Bill Belt's Rolling Put Diagonal strategy

shared/               # Shared modules used by all bots
  saxo_client.py      # Saxo Bank API client (orders, positions, streaming)
  logger_service.py   # Trade logging (Google Sheets, local files)
  config_loader.py    # Config loading with Secret Manager integration
  market_hours.py     # Market hours, holidays, early close detection
  event_calendar.py   # FOMC/economic calendar for trading blackouts
  secret_manager.py   # Google Secret Manager integration
  token_coordinator.py # OAuth token refresh coordination
  external_price_feed.py # Yahoo Finance fallback for VIX
  technical_indicators.py # TA calculations
```

---

## VM Details

- **VM Name:** `calypso-bot`
- **Zone:** `us-east1-b`
- **Project:** `calypso-trading-bot`
- **Calypso Path:** `/opt/calypso`
- **Calypso User:** `calypso`

---

## Trading Bots (3 Total)

| Bot | Service Name | Strategy | Config Path |
|-----|--------------|----------|-------------|
| Iron Fly | `iron_fly_0dte.service` | Doc Severson's 0DTE Iron Butterfly | `bots/iron_fly_0dte/config/config.json` |
| Delta Neutral | `delta_neutral.service` | Brian's Delta Neutral | `bots/delta_neutral_0dte/config/config.json` |
| Rolling Put Diagonal | `rolling_put_diagonal.service` | Bill Belt's Rolling Put Diagonal | `bots/rolling_put_diagonal/config/config.json` |

All bots have: `Restart=always`, `RestartSec=30`, `StartLimitInterval=600`, `StartLimitBurst=5`

### Iron Fly Bot Details
- **Entry:** 10:00 AM EST (after 30-min opening range)
- **Exit:** Wing touch (stop loss) or $75 profit target
- **Max hold:** 60 minutes (11:00 AM rule)
- **Filters:** VIX < 20, no FOMC days, price in opening range
- **Edge cases:** 63 analyzed, all resolved (see `docs/IRON_FLY_EDGE_CASES.md`)
- **Code audit:** Comprehensive review completed (see `docs/IRON_FLY_CODE_AUDIT.md`)

#### Iron Fly Safety Features
- **Entry order:** Longs first (Long Call → Long Put → Short Call → Short Put) - safer on partial fills
- **Entry retries:** 3 attempts with 15-second delays; auto-unwind filled legs on failure
- **Stop losses:** Software-based via 2-second polling (NOT broker-side stops)
- **Wing breach tolerance:** $0.10 buffer to avoid floating-point issues
- **Circuit breaker:** 5 consecutive failures or 5-of-10 sliding window triggers halt

#### Iron Fly Typical P&L (1 contract, ~30pt wings)
| Scenario | P&L | Notes |
|----------|-----|-------|
| Max profit (expires at ATM) | ~$1,500 | Rare - would hold to 4:00 PM |
| Profit target hit | +$75 | Target exit |
| Stop loss (wing touch) | -$250 to -$350 | Typical stop-out |
| Max loss (circuit breaker) | -$400 | Safety cap |

### Bot Isolation
Iron Fly (SPX/SPXW) and Delta Neutral (SPY) are mostly independent:
- Different underlying instruments (UIC 4913/128 vs SPY UICs)
- Separate systemd processes
- Separate config files, logs, and state files
- No shared position data

**Shared Resources:**
- `token_coordinator.py` manages OAuth token refresh across all bots via file-based locking
- When one bot refreshes the token, others pick up the fresh token from the shared cache
- WebSocket connections refresh tokens before connecting to avoid 401 errors (CONN-008 fix)

---

## Quick Reference Commands

### Emergency Stop (All Bots)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte delta_neutral rolling_put_diagonal"
```

### Stop Individual Bots
```bash
# Iron Fly
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte"

# Delta Neutral
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop delta_neutral"

# Rolling Put Diagonal
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop rolling_put_diagonal"
```

### Start Bots
```bash
# All bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start iron_fly_0dte delta_neutral rolling_put_diagonal"

# Individual
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start iron_fly_0dte"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start delta_neutral"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start rolling_put_diagonal"
```

### Restart Bots
```bash
# All bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte delta_neutral rolling_put_diagonal"

# Individual
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart delta_neutral"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart rolling_put_diagonal"
```

### Check Status
```bash
# All bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status iron_fly_0dte delta_neutral rolling_put_diagonal"

# List running services
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-units --type=service | grep -E '(iron|delta|rolling)'"
```

### View Logs
```bash
# Recent logs (50 lines)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u iron_fly_0dte -n 50 --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u delta_neutral -n 50 --no-pager"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u rolling_put_diagonal -n 50 --no-pager"

# Follow logs (live)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u iron_fly_0dte -f"

# Today's logs
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u iron_fly_0dte --since today --no-pager"
```

---

## Deployment Workflow

### Push Local Changes to VM

1. **Commit and push locally:**
```bash
git add -A
git commit -m "your message"
git push
```

2. **Pull on VM (must use calypso user):**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull'"
```

3. **Restart bots to apply changes:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte delta_neutral rolling_put_diagonal"
```

4. **Verify status:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status iron_fly_0dte delta_neutral rolling_put_diagonal"
```

---

## VM System Commands

```bash
# SSH connect interactively
gcloud compute ssh calypso-bot --zone=us-east1-b

# Disk usage
gcloud compute ssh calypso-bot --zone=us-east1-b --command="df -h"

# Memory usage
gcloud compute ssh calypso-bot --zone=us-east1-b --command="free -h"

# Running Python processes
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep python"

# List log directories
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/logs/"

# List data directory
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/data/"
```

---

## Config Files

**IMPORTANT:** Config files are in `.gitignore` and must be edited directly on the VM. They are NOT synced via git. Local config files are for development only - production configs live on the VM.

To edit VM configs, use `nano` or `vim` via SSH:
```bash
# Edit Iron Fly config on VM
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso nano /opt/calypso/bots/iron_fly_0dte/config/config.json"

# After editing, restart the bot to apply changes
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart iron_fly_0dte"
```

```bash
# View VM config
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/iron_fly_0dte/config/config.json"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/delta_neutral_0dte/config/config.json"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/rolling_put_diagonal/config/config.json"

# View systemd service files
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/iron_fly_0dte.service"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/delta_neutral.service"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /etc/systemd/system/rolling_put_diagonal.service"
```

---

## Google Secret Manager

```bash
# List secrets
gcloud secrets list --project=calypso-trading-bot

# View a secret
gcloud secrets versions access latest --secret=SECRET_NAME --project=calypso-trading-bot
```

---

## Key Saxo Bank Symbols

| Symbol | UIC | Description |
|--------|-----|-------------|
| US500.I | 4913 | S&P 500 CFD (for SPX price tracking) |
| SPXW:xcbf | 128 | SPX Weekly options (0DTE) |
| VIX.I | 10606 | VIX spot price |
| VIX:xcbf | 117 | VIX options |

---

## Important Notes

1. **Git on VM:** Must run as `calypso` user: `sudo -u calypso bash -c 'cd /opt/calypso && git pull'`
2. **Service names use underscores:** `iron_fly_0dte`, `delta_neutral`, `rolling_put_diagonal`
3. **Log locations:** `/opt/calypso/logs/{iron_fly_0dte,delta_neutral_0dte,rolling_put_diagonal}/bot.log`
4. **Position data:** `/opt/calypso/data/iron_fly_position.json`
5. **Config files are gitignored:** Real credentials come from Secret Manager
6. **All API calls are direct:** No caching for order status or positions (always fresh from Saxo)
7. **Iron Fly bot:** Running in LIVE mode (as of 2026-01-23)
8. **Delta Neutral bot:** Running in LIVE mode
9. **Rolling Put Diagonal bot:** Still in dry-run mode

---

## Documentation

- `docs/IRON_FLY_EDGE_CASES.md` - 63 edge cases analyzed for Iron Fly bot
- `docs/IRON_FLY_CODE_AUDIT.md` - Comprehensive pre-LIVE code audit (2026-01-23)
- `.claude/settings.local.json` - Full command reference (also readable)
