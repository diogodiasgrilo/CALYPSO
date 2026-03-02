# CALYPSO VM Command Reference

**Last Updated:** 2026-03-02
**VM Name:** `calypso-bot` | **Zone:** `us-east1-b` | **Project:** `calypso-trading-bot`
**Calypso Path:** `/opt/calypso` | **User:** `calypso`

---

## Current Service Status (as of 2026-03-02)

| Service | Type | Status |
|---------|------|--------|
| **HYDRA** | Long-running trading bot | **LIVE** (only active bot) |
| **Token Keeper** | Long-running token refresh | **RUNNING** (24/7) |
| **ARGUS** | Oneshot timer (every 15 min) | **ACTIVE** |
| **APOLLO** | Oneshot timer (8:30 AM ET weekdays) | **ACTIVE** |
| **HERMES** | Oneshot timer (5:00 PM ET weekdays) | **ACTIVE** |
| **CLIO** | Oneshot timer (Saturday 9:00 AM ET) | **ACTIVE** |
| Iron Fly | Long-running bot | STOPPED |
| Delta Neutral | Long-running bot | STOPPED |
| MEIC | Long-running bot | STOPPED |
| Rolling Put Diagonal | Long-running bot | STOPPED |

---

## EMERGENCY: Copy-Paste One-Liners (from VS Code terminal)

### Stop HYDRA (graceful)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
```

### Stop EVERYTHING (HYDRA + Token Keeper — token expires in ~20 min!)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra token_keeper"
```

### Check HYDRA status
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra"
```

### Check ALL services and agent timers
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra token_keeper argus.timer apollo.timer hermes.timer clio.timer"
```

### HYDRA logs (last 50 lines)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 50 --no-pager"
```

### HYDRA logs (live follow — Ctrl+C to exit, does NOT stop bot)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -f"
```

---

## SSH Access

```bash
# Interactive session (stays open for multiple commands — useful for running several things)
gcloud compute ssh calypso-bot --zone=us-east1-b

# Single command (runs and exits — most common usage from VS Code)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="<command>"
```

---

## HYDRA Trading Bot

HYDRA is the only active trading bot. It runs continuously during market hours.

### Start / Stop / Restart
```bash
# Start (token_keeper should already be running)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"

# Stop (graceful — waits up to 60s for cleanup)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"

# Restart (e.g., after config change or code deploy)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"
```

### Status and Logs
```bash
# Status
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra"

# Recent logs (last 50 lines)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 50 --no-pager"

# Today's logs
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since today --no-pager"

# Live logs (Ctrl+C to exit — does NOT stop the bot)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -f"

# Search for errors
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since today --no-pager | grep -E 'ERROR|WARNING|CRITICAL'"
```

### Config (on VM only — gitignored)
```bash
# View HYDRA config
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/hydra/config/config.json"

# Edit HYDRA config (interactive — opens nano editor)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso nano /opt/calypso/bots/hydra/config/config.json"
# After editing, restart: sudo systemctl restart hydra
```

### State and Data Files
```bash
# HYDRA state (today's entries, positions, P&L)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/hydra_state.json"

# Cumulative metrics (all-time stats)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/hydra_metrics.json"

# Position registry (what HYDRA currently owns)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/position_registry.json"
```

**IMPORTANT:** Never use `kill` or `pkill` — HYDRA has `Restart=always` with `RestartSec=30`, so killing it will auto-restart in 30 seconds. Always use `systemctl stop`.

---

## Token Keeper

Keeps Saxo OAuth tokens fresh 24/7. Must always be running — tokens expire every 20 minutes.

```bash
# Status
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper"

# Logs (last 20 lines)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -n 20 --no-pager"

# Restart (rarely needed)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart token_keeper"

# Token cache freshness
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/data/saxo_token_cache.json"
```

---

## AI Agent Timers (ARGUS, APOLLO, HERMES, CLIO)

Agents are **systemd oneshot services** triggered by timers. Each run starts a fresh Python process — no persistent state to manage.

| Agent | Timer Schedule | What It Does |
|-------|---------------|--------------|
| **ARGUS** | Every 15 min, 24/7 | Health checks (HYDRA, token, disk, memory) |
| **APOLLO** | 8:30 AM ET, Mon-Fri | Morning market briefing + risk level |
| **HERMES** | 5:00 PM ET, Mon-Fri | Daily execution quality report |
| **CLIO** | Saturday 9:00 AM ET | Weekly strategy analysis + git commit |

### Check Timer Status (when will they fire next?)
```bash
# All agent timers
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-timers argus.timer apollo.timer hermes.timer clio.timer --no-pager"

# Individual timer
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status apollo.timer"
```

### View Agent Logs (last run output)
```bash
# ARGUS (health monitor)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u argus -n 30 --no-pager"

# APOLLO (morning scout)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u apollo -n 50 --no-pager"

# HERMES (daily analyst)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hermes -n 50 --no-pager"

# CLIO (weekly analyst)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u clio -n 50 --no-pager"
```

### View Agent Reports (saved output)
```bash
# ARGUS health log (last 5 entries)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="tail -5 /opt/calypso/intel/argus/health_log.jsonl"

# APOLLO morning briefing (today)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/intel/apollo/\$(TZ=America/New_York date +%Y-%m-%d).md"

# HERMES daily report (today)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/intel/hermes/\$(TZ=America/New_York date +%Y-%m-%d).md"

# CLIO weekly reports (list all)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/intel/clio/"

# ARGUS incidents (failures only)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/intel/argus/incidents/"
```

### Manually Trigger an Agent (run it now)
```bash
# Run ARGUS now
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start argus.service && sudo journalctl -u argus -n 30 --no-pager"

# Run APOLLO now
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start apollo.service && sudo journalctl -u apollo -n 50 --no-pager"

# Run HERMES now
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hermes.service && sudo journalctl -u hermes -n 50 --no-pager"

# Run CLIO now
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start clio.service && sudo journalctl -u clio -n 100 --no-pager"
```

### Enable / Disable Agent Timers
```bash
# Disable an agent (stops future runs, doesn't affect current run)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop apollo.timer && sudo systemctl disable apollo.timer"

# Re-enable an agent
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl enable apollo.timer && sudo systemctl start apollo.timer"
```

---

## Deploy Code Changes

### Standard Deploy (pull + cache clear — agents pick up changes automatically)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Done'"
```

### Deploy + Restart HYDRA (if HYDRA code changed)
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'" && \
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra && echo 'HYDRA restarted'"
```

**Note:** Agent timers (ARGUS, APOLLO, HERMES, CLIO) do NOT need restart after deploy — each run starts a fresh Python process that loads the latest code.

---

## Other Bots (Currently STOPPED)

These bots are inactive but their service files remain on the VM.

```bash
# Start a stopped bot
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start iron_fly_0dte"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start delta_neutral"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start meic"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start rolling_put_diagonal"

# Check status of all bots
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra token_keeper iron_fly_0dte delta_neutral meic rolling_put_diagonal"
```

---

## System Diagnostics

```bash
# Disk space
gcloud compute ssh calypso-bot --zone=us-east1-b --command="df -h /"

# Memory usage
gcloud compute ssh calypso-bot --zone=us-east1-b --command="free -h"

# Running Python processes
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep python"

# VM status (if can't SSH)
gcloud compute instances describe calypso-bot --zone=us-east1-b --format="value(status)"

# Start VM if stopped
gcloud compute instances start calypso-bot --zone=us-east1-b
```

---

## Alert System

```bash
# Cloud Function logs (Telegram/Email delivery)
gcloud functions logs read process-trading-alert --region=us-east1 --project=calypso-trading-bot --limit=20

# Dead letter queue (failed alerts)
gcloud pubsub subscriptions pull calypso-alerts-dlq-sub --project=calypso-trading-bot --limit=10 --auto-ack
```

---

## Run Scripts on VM

```bash
# General pattern
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/SCRIPT_NAME.py'"

# Preview what HYDRA would do right now
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/preview_live_entry.py'"

# Test API connectivity
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/test_rest_api.py'"
```

---

## Quick Reference Table

| Action | Command (from VS Code terminal) |
|--------|---------------------------------|
| **Stop HYDRA** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"` |
| **Start HYDRA** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"` |
| **Restart HYDRA** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"` |
| **HYDRA status** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra"` |
| **HYDRA logs (50 lines)** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 50 --no-pager"` |
| **HYDRA logs (live)** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -f"` |
| **Token keeper status** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper"` |
| **All timer status** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-timers --no-pager"` |
| **Agent logs (APOLLO)** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u apollo -n 50 --no-pager"` |
| **Agent logs (HERMES)** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hermes -n 50 --no-pager"` |
| **Agent logs (ARGUS)** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u argus -n 30 --no-pager"` |
| **Agent logs (CLIO)** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u clio -n 100 --no-pager"` |
| **Deploy (pull + cache)** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Done'"` |
| **Disk space** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="df -h /"` |
| **Memory** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="free -h"` |
| **Stop EVERYTHING** | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra token_keeper"` |

---

## Important Reminders

1. **Never use `kill` or `pkill`** — services auto-restart in 30 seconds. Always use `systemctl stop`.
2. **Token Keeper must run 24/7** — without it, Saxo tokens expire in ~20 minutes and require manual re-auth.
3. **Agents don't need restart** — they're oneshot services triggered by timers. `git pull` is enough.
4. **HYDRA needs restart** after code changes to `bots/hydra/` or `shared/` — it's a long-running process.
5. **Config files are gitignored** — edit directly on VM with `nano`, then restart the affected service.
6. **Always clear `__pycache__`** after `git pull` — stale bytecode can run old code.
7. **All log timestamps are ET** (Eastern Time) — matching NYSE trading hours.
