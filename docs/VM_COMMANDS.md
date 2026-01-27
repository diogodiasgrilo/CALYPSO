# Calypso VM Command Reference

Complete command reference for managing the Calypso trading bots on the GCP VM.

**VM:** `calypso-bot` | **Zone:** `us-east1-b` | **Location:** `/opt/calypso`

---

## SSH Access

```bash
# Interactive session (stays open for multiple commands)
gcloud compute ssh calypso-bot --zone=us-east1-b

# Single command (runs and exits)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="<command>"
```

---

## Token Keeper Service

The Token Keeper service keeps OAuth tokens fresh 24/7. It should always be running.

```bash
# Status
sudo systemctl status token_keeper

# View logs
sudo journalctl -u token_keeper -f

# Restart (rarely needed)
sudo systemctl restart token_keeper
```

---

## Bot Management (systemd)

### Start Bots
```bash
sudo systemctl start delta_neutral
sudo systemctl start iron_fly_0dte
sudo systemctl start rolling_put_diagonal
sudo systemctl start meic

# Start ALL at once (token_keeper should already be running)
sudo systemctl start delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

### Stop Bots (Graceful)
```bash
sudo systemctl stop delta_neutral
sudo systemctl stop iron_fly_0dte
sudo systemctl stop rolling_put_diagonal
sudo systemctl stop meic

# Stop ALL trading bots (token_keeper keeps running to preserve auth)
sudo systemctl stop delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

### Restart Bots
```bash
sudo systemctl restart delta_neutral
sudo systemctl restart iron_fly_0dte
sudo systemctl restart rolling_put_diagonal
sudo systemctl restart meic

# Restart ALL at once
sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

### Emergency Kill (Immediate Termination)
```bash
sudo systemctl kill -s SIGKILL delta_neutral
sudo systemctl kill -s SIGKILL iron_fly_0dte
sudo systemctl kill -s SIGKILL rolling_put_diagonal
sudo systemctl kill -s SIGKILL meic

# Kill ALL trading bots at once
sudo systemctl kill -s SIGKILL delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

### Enable/Disable Auto-Start on Boot
```bash
# Enable (start automatically when VM reboots)
sudo systemctl enable token_keeper  # Should always be enabled
sudo systemctl enable delta_neutral
sudo systemctl enable iron_fly_0dte
sudo systemctl enable rolling_put_diagonal
sudo systemctl enable meic

# Disable (don't start on boot)
sudo systemctl disable delta_neutral
sudo systemctl disable iron_fly_0dte
sudo systemctl disable rolling_put_diagonal
sudo systemctl disable meic
# WARNING: Don't disable token_keeper unless you want tokens to expire!
```

---

## Check Bot Status

### Quick Status Script (All Bots)
```bash
/opt/calypso/scripts/bot_status.sh
```

### Individual Service Status
```bash
sudo systemctl status token_keeper
sudo systemctl status delta_neutral
sudo systemctl status iron_fly_0dte
sudo systemctl status rolling_put_diagonal
sudo systemctl status meic
```

---

## Live Logging

### Combined Monitor Log (All Bots)
```bash
tail -f /opt/calypso/logs/monitor.log
```

Shows key events from all bots: STARTED, HEARTBEAT, TRADE, ERROR, SHUTDOWN.

### Individual Service Logs (Live)
```bash
sudo journalctl -u token_keeper -f
sudo journalctl -u delta_neutral -f
sudo journalctl -u iron_fly_0dte -f
sudo journalctl -u rolling_put_diagonal -f
sudo journalctl -u meic -f
```

### View Recent Logs (Last N Lines)
```bash
sudo journalctl -u token_keeper -n 100
sudo journalctl -u delta_neutral -n 100
sudo journalctl -u iron_fly_0dte -n 100
sudo journalctl -u rolling_put_diagonal -n 100
sudo journalctl -u meic -n 100
```

### Search Logs for Errors
```bash
sudo journalctl -u delta_neutral | grep -E "ERROR|WARNING"
```

**Note:** Press `Ctrl+C` to exit live log view. This does NOT stop the bot.

---

## Pull Changes & Restart

### Full Update Procedure
```bash
cd /opt/calypso
sudo -u calypso git pull
# Clear Python cache to ensure new code runs
find bots shared services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic
# Token keeper rarely needs restart (only if token_keeper code changed)
```

### One-Liner (Pull + Clear Cache + Restart All)
```bash
cd /opt/calypso && sudo -u calypso git pull && find bots shared services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null && sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic
```

---

## Log File Locations

| Service | Log File |
|---------|----------|
| Token Keeper | `journalctl -u token_keeper` (systemd only) |
| Delta Neutral | `/opt/calypso/logs/delta_neutral/bot.log` |
| Iron Fly 0DTE | `/opt/calypso/logs/iron_fly_0dte/bot.log` |
| Rolling Put Diagonal | `/opt/calypso/logs/rolling_put_diagonal/bot.log` |
| MEIC | `/opt/calypso/logs/meic/bot.log` |
| Combined Monitor | `/opt/calypso/logs/monitor.log` |

All timestamps are in **Eastern Time (ET)**.

---

## VM Troubleshooting

### Check VM Status
```bash
gcloud compute instances describe calypso-bot --zone=us-east1-b --format="value(status)"
```

### Start VM if Stopped
```bash
gcloud compute instances start calypso-bot --zone=us-east1-b
```

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

---

## Quick Reference Table

| Action | Command |
|--------|---------|
| SSH into VM | `gcloud compute ssh calypso-bot --zone=us-east1-b` |
| Token keeper status | `sudo systemctl status token_keeper` |
| Token keeper logs | `sudo journalctl -u token_keeper -f` |
| Start all bots | `sudo systemctl start delta_neutral iron_fly_0dte rolling_put_diagonal meic` |
| Stop all bots | `sudo systemctl stop delta_neutral iron_fly_0dte rolling_put_diagonal meic` |
| Restart all bots | `sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic` |
| Kill all bots (emergency) | `sudo systemctl kill -s SIGKILL delta_neutral iron_fly_0dte rolling_put_diagonal meic` |
| View combined logs | `tail -f /opt/calypso/logs/monitor.log` |
| View DN logs | `sudo journalctl -u delta_neutral -f` |
| Quick status | `/opt/calypso/scripts/bot_status.sh` |
| Pull & restart | `cd /opt/calypso && sudo -u calypso git pull && find bots shared services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null && sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal meic` |
| Check disk space | `df -h /` |
| Check memory | `free -h` |

---

**Last Updated:** 2026-01-27
