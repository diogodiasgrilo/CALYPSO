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

## Bot Management (systemd)

### Start Bots
```bash
sudo systemctl start delta_neutral
sudo systemctl start iron_fly_0dte
sudo systemctl start rolling_put_diagonal

# Start ALL at once
sudo systemctl start delta_neutral iron_fly_0dte rolling_put_diagonal
```

### Stop Bots (Graceful)
```bash
sudo systemctl stop delta_neutral
sudo systemctl stop iron_fly_0dte
sudo systemctl stop rolling_put_diagonal

# Stop ALL at once
sudo systemctl stop delta_neutral iron_fly_0dte rolling_put_diagonal
```

### Restart Bots
```bash
sudo systemctl restart delta_neutral
sudo systemctl restart iron_fly_0dte
sudo systemctl restart rolling_put_diagonal

# Restart ALL at once
sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal
```

### Emergency Kill (Immediate Termination)
```bash
sudo systemctl kill -s SIGKILL delta_neutral
sudo systemctl kill -s SIGKILL iron_fly_0dte
sudo systemctl kill -s SIGKILL rolling_put_diagonal

# Kill ALL at once
sudo systemctl kill -s SIGKILL delta_neutral iron_fly_0dte rolling_put_diagonal
```

### Enable/Disable Auto-Start on Boot
```bash
# Enable (start automatically when VM reboots)
sudo systemctl enable delta_neutral
sudo systemctl enable iron_fly_0dte
sudo systemctl enable rolling_put_diagonal

# Disable (don't start on boot)
sudo systemctl disable delta_neutral
sudo systemctl disable iron_fly_0dte
sudo systemctl disable rolling_put_diagonal
```

---

## Check Bot Status

### Quick Status Script (All Bots)
```bash
/opt/calypso/scripts/bot_status.sh
```

### Individual Bot Status
```bash
sudo systemctl status delta_neutral
sudo systemctl status iron_fly_0dte
sudo systemctl status rolling_put_diagonal
```

---

## Live Logging

### Combined Monitor Log (All Bots)
```bash
tail -f /opt/calypso/logs/monitor.log
```

Shows key events from all bots: STARTED, HEARTBEAT, TRADE, ERROR, SHUTDOWN.

### Individual Bot Logs (Live)
```bash
sudo journalctl -u delta_neutral -f
sudo journalctl -u iron_fly_0dte -f
sudo journalctl -u rolling_put_diagonal -f
```

### View Recent Logs (Last N Lines)
```bash
sudo journalctl -u delta_neutral -n 100
sudo journalctl -u iron_fly_0dte -n 100
sudo journalctl -u rolling_put_diagonal -n 100
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
sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal
```

### One-Liner (Pull + Restart All)
```bash
cd /opt/calypso && sudo -u calypso git pull && sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal
```

---

## Log File Locations

| Bot | Log File |
|-----|----------|
| Delta Neutral | `/opt/calypso/logs/delta_neutral/bot.log` |
| Iron Fly 0DTE | `/opt/calypso/logs/iron_fly_0dte/bot.log` |
| Rolling Put Diagonal | `/opt/calypso/logs/rolling_put_diagonal/bot.log` |
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
| Start all bots | `sudo systemctl start delta_neutral iron_fly_0dte rolling_put_diagonal` |
| Stop all bots | `sudo systemctl stop delta_neutral iron_fly_0dte rolling_put_diagonal` |
| Restart all bots | `sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal` |
| Kill all bots (emergency) | `sudo systemctl kill -s SIGKILL delta_neutral iron_fly_0dte rolling_put_diagonal` |
| View combined logs | `tail -f /opt/calypso/logs/monitor.log` |
| View DN logs | `sudo journalctl -u delta_neutral -f` |
| Quick status | `/opt/calypso/scripts/bot_status.sh` |
| Pull & restart | `cd /opt/calypso && sudo -u calypso git pull && sudo systemctl restart delta_neutral iron_fly_0dte rolling_put_diagonal` |
| Check disk space | `df -h /` |
| Check memory | `free -h` |

---

**Last Updated:** 2026-01-23
