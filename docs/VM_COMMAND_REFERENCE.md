# CALYPSO VM Command Reference

**Last Updated:** 2026-01-29
**VM Name:** `calypso-bot`
**Zone:** `us-east1-b`
**Project:** `calypso-trading-bot`
**Calypso Path:** `/opt/calypso`
**Calypso User:** `calypso`

---

## Quick Reference Table

| Bot | Service Name |
|-----|--------------|
| Delta Neutral | `delta_neutral.service` |
| Iron Fly 0DTE | `iron_fly_0dte.service` |
| Rolling Put Diagonal | `rolling_put_diagonal.service` |
| MEIC | `meic.service` |
| Token Keeper | `token_keeper.service` |

---

## SSH Access

### From Local Machine
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b
```

### Run Command Remotely (without SSH session)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="YOUR_COMMAND_HERE"
```

---

## Bot Management (systemd)

### Start Bots
```bash
# Start Token Keeper first (ensures fresh tokens)
sudo systemctl start token_keeper

# Start individual bots
sudo systemctl start delta_neutral
sudo systemctl start iron_fly_0dte
sudo systemctl start rolling_put_diagonal
sudo systemctl start meic

# Start ALL trading bots at once
sudo systemctl start delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

### Stop Bots (Graceful Shutdown)
```bash
# Stop individual bots
sudo systemctl stop delta_neutral
sudo systemctl stop iron_fly_0dte
sudo systemctl stop rolling_put_diagonal
sudo systemctl stop meic

# Stop ALL trading bots (Token Keeper keeps running)
sudo systemctl stop delta_neutral iron_fly_0dte rolling_put_diagonal meic

# Stop EVERYTHING including Token Keeper (token will expire in ~20 min!)
sudo systemctl stop delta_neutral iron_fly_0dte rolling_put_diagonal meic token_keeper
```

### Restart Bots
```bash
# Restart individual bots
sudo systemctl restart delta_neutral
sudo systemctl restart iron_fly_0dte
sudo systemctl restart rolling_put_diagonal
sudo systemctl restart meic

# Restart ALL trading bots
sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic

# Restart Token Keeper (rarely needed)
sudo systemctl restart token_keeper
```

### Emergency Kill (Immediate Termination)
```bash
# Kill individual bots
sudo systemctl kill -s SIGKILL delta_neutral
sudo systemctl kill -s SIGKILL iron_fly_0dte
sudo systemctl kill -s SIGKILL rolling_put_diagonal
sudo systemctl kill -s SIGKILL meic

# Kill ALL bots at once (emergency)
sudo systemctl kill -s SIGKILL delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

**WARNING:** All services have `Restart=always` with `RestartSec=30`. Killing will cause auto-restart in 30 seconds. Use `systemctl stop` for permanent stop.

---

## Check Bot Status

### Quick Status Snapshot (All Bots)
```bash
/opt/calypso/scripts/bot_status.sh
```

### Check Individual Bot Status
```bash
sudo systemctl status token_keeper
sudo systemctl status delta_neutral
sudo systemctl status iron_fly_0dte
sudo systemctl status rolling_put_diagonal
sudo systemctl status meic
```

### Check All Services at Once
```bash
sudo systemctl status token_keeper delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

### List Running Calypso Services
```bash
sudo systemctl list-units --type=service | grep -E '(iron|delta|rolling|meic|token_keeper)'
```

---

## Live Logging

### Combined Monitor Log (All Bots)
```bash
tail -f /opt/calypso/logs/monitor.log
```

### Individual Bot Logs (Live - Follow Mode)
```bash
sudo journalctl -u token_keeper -f
sudo journalctl -u delta_neutral -f
sudo journalctl -u iron_fly_0dte -f
sudo journalctl -u rolling_put_diagonal -f
sudo journalctl -u meic -f
```

### View Recent Logs (Last N Lines)
```bash
# Last 50 lines
sudo journalctl -u delta_neutral -n 50 --no-pager
sudo journalctl -u iron_fly_0dte -n 50 --no-pager
sudo journalctl -u rolling_put_diagonal -n 50 --no-pager
sudo journalctl -u meic -n 50 --no-pager
sudo journalctl -u token_keeper -n 50 --no-pager

# Last 100 lines
sudo journalctl -u delta_neutral -n 100 --no-pager
```

### View Today's Logs
```bash
sudo journalctl -u delta_neutral --since today --no-pager
sudo journalctl -u iron_fly_0dte --since today --no-pager
sudo journalctl -u token_keeper --since today --no-pager
```

### View Logs from Specific Time
```bash
# Last hour
sudo journalctl -u delta_neutral --since "1 hour ago" --no-pager

# Specific time range
sudo journalctl -u delta_neutral --since "2026-01-29 09:30:00" --until "2026-01-29 10:00:00" --no-pager
```

---

## Pull Changes & Restart

### Full Update Procedure (with Cache Clear)
```bash
cd /opt/calypso
sudo -u calypso git pull
find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

### One-Liner for Pull + Cache Clear + Restart All
```bash
cd /opt/calypso && sudo -u calypso git pull && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null && sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

### Remote One-Liner (from local machine)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"
```

### Pull + Restart Specific Bot
```bash
cd /opt/calypso && sudo -u calypso git pull && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null && sudo systemctl restart delta_neutral
```

**IMPORTANT:** Always clear Python cache (`__pycache__`) after pulling changes. Stale bytecode can cause bots to run old code.

---

## Enable/Disable Auto-Start on Boot

### Enable (Start Automatically on VM Reboot)
```bash
sudo systemctl enable token_keeper
sudo systemctl enable delta_neutral
sudo systemctl enable iron_fly_0dte
sudo systemctl enable rolling_put_diagonal
sudo systemctl enable meic
```

### Disable (Don't Start on Boot)
```bash
sudo systemctl disable delta_neutral
sudo systemctl disable iron_fly_0dte
sudo systemctl disable rolling_put_diagonal
sudo systemctl disable meic
# Note: Usually keep token_keeper enabled
```

---

## Log File Locations

| Bot | Log File |
|-----|----------|
| Token Keeper | journalctl only (no file log) |
| Delta Neutral | `/opt/calypso/logs/delta_neutral/bot.log` |
| Iron Fly 0DTE | `/opt/calypso/logs/iron_fly_0dte/bot.log` |
| Rolling Put Diagonal | `/opt/calypso/logs/rolling_put_diagonal/bot.log` |
| MEIC | `/opt/calypso/logs/meic/bot.log` |
| Combined Monitor | `/opt/calypso/logs/monitor.log` |

### View File Logs Directly
```bash
tail -f /opt/calypso/logs/delta_neutral/bot.log
tail -f /opt/calypso/logs/iron_fly_0dte/bot.log
tail -100 /opt/calypso/logs/delta_neutral/bot.log
```

---

## Data & Config Files

### Position Data
```bash
cat /opt/calypso/data/iron_fly_position.json
cat /opt/calypso/data/delta_neutral_state.json
cat /opt/calypso/data/position_registry.json
```

### Token Cache
```bash
cat /opt/calypso/data/saxo_token_cache.json
```

### View Config Files
```bash
cat /opt/calypso/bots/delta_neutral/config/config.json
cat /opt/calypso/bots/iron_fly_0dte/config/config.json
cat /opt/calypso/bots/rolling_put_diagonal/config/config.json
cat /opt/calypso/bots/meic/config/config.json
```

### Edit Config Files
```bash
sudo -u calypso nano /opt/calypso/bots/delta_neutral/config/config.json
# After editing, restart the bot:
sudo systemctl restart delta_neutral
```

---

## System Diagnostics

### Check Disk Space
```bash
df -h /
```

### Check Memory Usage
```bash
free -h
```

### View Running Python Processes
```bash
ps aux | grep python
```

### Check CPU/Memory per Process
```bash
top -b -n 1 | grep python
```

---

## Run Scripts on VM

### General Pattern
```bash
sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/SCRIPT_NAME.py'
```

### Common Scripts
```bash
# Preview what bot would do (most useful)
sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/preview_live_entry.py'

# Test API connectivity
sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/test_rest_api.py'

# Check short strikes
sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/check_short_strikes.py'
```

### Run from Local Machine
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/preview_live_entry.py'"
```

---

## Alert System (Cloud Functions)

### View Alert Logs
```bash
gcloud functions logs read process-trading-alert --region=us-east1 --project=calypso-trading-bot --limit=50
```

### Check Dead Letter Queue
```bash
gcloud pubsub subscriptions pull calypso-alerts-dlq-sub --project=calypso-trading-bot --limit=10 --auto-ack
```

---

## Common One-Liners (From Local Machine)

### Emergency Stop All Bots
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop delta_neutral iron_fly_0dte rolling_put_diagonal meic"
```

### Check All Bot Status
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper delta_neutral iron_fly_0dte rolling_put_diagonal meic"
```

### Pull + Restart + Verify
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'" && \
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic" && \
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status delta_neutral iron_fly_0dte rolling_put_diagonal meic"
```

### View Delta Neutral Logs (Last 50)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u delta_neutral -n 50 --no-pager"
```

### Follow Delta Neutral Logs Live
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u delta_neutral -f"
```

---

## Important Notes

1. **All timestamps are in Eastern Time (ET)** to match NYSE trading hours
2. **Never use `kill` or `pkill`** - bots auto-restart in 30 seconds due to `Restart=always`
3. **Always clear cache after git pull** - stale `__pycache__` can cause old code to run
4. **Token Keeper should run 24/7** - keeps Saxo OAuth tokens fresh even when bots are stopped
5. **Config files are gitignored** - edit directly on VM, not in local repo
6. **Run scripts as `calypso` user** - required for Secret Manager access

---

## Service File Locations

```bash
# View service configurations
cat /etc/systemd/system/token_keeper.service
cat /etc/systemd/system/delta_neutral.service
cat /etc/systemd/system/iron_fly_0dte.service
cat /etc/systemd/system/rolling_put_diagonal.service
cat /etc/systemd/system/meic.service
```

### After Modifying Service Files
```bash
sudo systemctl daemon-reload
sudo systemctl restart SERVICE_NAME
```
