# VM Commands Reference

Quick reference for managing all CALYPSO services on the Google Cloud VM. Copy-paste these commands directly into your terminal.

**VM:** `calypso-bot` | **Zone:** `us-east1-b` | **Path:** `/opt/calypso` | **User:** `calypso`

> **CRITICAL:** Never use `kill` or `pkill` to stop bots. They have `Restart=always` and will auto-restart in 30 seconds. Always use `systemctl stop`.

---

## Table of Contents

- [HYDRA (Active Trading Bot)](#hydra-active-trading-bot)
- [Token Keeper](#token-keeper)
- [Dashboard](#dashboard)
- [Agents (APOLLO, HERMES, HOMER, CLIO)](#agents)
- [Stopped Bots (Iron Fly, Delta Neutral, MEIC, Rolling Put)](#stopped-bots)
- [Deploy Code Changes](#deploy-code-changes)
- [Deploy Dashboard Frontend](#deploy-dashboard-frontend)
- [View Logs](#view-logs)
- [VM System Commands](#vm-system-commands)
- [Emergency Commands](#emergency-commands)

---

## HYDRA (Active Trading Bot)

HYDRA is the only active trading bot. It trades SPX 0DTE iron condors.

**Check if HYDRA is running:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra --no-pager"
```

**Start HYDRA:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"
```

**Stop HYDRA:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
```

**Restart HYDRA** (use after deploying code changes):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"
```

**View HYDRA logs** (last 50 lines):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 50 --no-pager"
```

**Follow HYDRA logs live:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -f"
```

**View today's HYDRA logs only:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra --since today --no-pager"
```

**View HYDRA config (read-only):**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/bots/hydra/config/config.json"
```

**View HYDRA state file:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/hydra_state.json"
```

**View HYDRA cumulative metrics:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/hydra_metrics.json"
```

---

## Token Keeper

Keeps Saxo OAuth tokens fresh 24/7. Must always be running — if it stops, tokens expire in ~20 minutes and all bots lose API access.

**Check status:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status token_keeper --no-pager"
```

**Start:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start token_keeper"
```

**Stop** (WARNING: tokens will expire in ~20 minutes):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop token_keeper"
```

**Restart:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart token_keeper"
```

**View logs:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u token_keeper -n 50 --no-pager"
```

---

## Dashboard

Read-only monitoring dashboard for HYDRA. Runs on port 8001 (backend) behind nginx on port 8080.

**Check status:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status dashboard --no-pager"
```

**Start:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start dashboard"
```

**Stop:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop dashboard"
```

**Restart:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart dashboard"
```

**View logs:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u dashboard -n 50 --no-pager"
```

---

## Agents

Agents run on systemd timers (scheduled, not always-on). They run once at their scheduled time, then exit.

| Agent | Timer | Schedule | What it does |
|-------|-------|----------|-------------|
| APOLLO | `apollo.timer` | 8:30 AM ET weekdays | Pre-market briefing |
| HERMES | `hermes.timer` | 7:00 PM ET weekdays | Daily execution report |
| HOMER | `homer.timer` | 7:30 PM ET weekdays | Updates trading journal |
| CLIO | `clio.timer` | Saturday 9:00 AM ET | Weekly strategy analysis |

**Check all agent timers:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-timers --no-pager | grep -E '(apollo|hermes|homer|clio)'"
```

**Run an agent manually** (example: HOMER):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m services.homer.main'"
```

**Run HOMER in dry-run mode** (parse + collect but don't write):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m services.homer.main --dry-run'"
```

**View agent logs** (example: HERMES):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hermes -n 50 --no-pager"
```

**Check last agent run results:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -lt /opt/calypso/intel/hermes/ | head -5"
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -lt /opt/calypso/intel/apollo/ | head -5"
```

---

## Stopped Bots

These bots are stopped and not actively trading. They can be started if needed.

| Bot | Service Name | Strategy |
|-----|-------------|----------|
| Iron Fly | `iron_fly_0dte` | Doc Severson's 0DTE Iron Butterfly |
| Delta Neutral | `delta_neutral` | Brian's Delta Neutral |
| MEIC | `meic` | Tammy Chambless's MEIC (replaced by HYDRA) |
| Rolling Put Diagonal | `rolling_put_diagonal` | Bill Belt's Rolling Put Diagonal |

**Check status of all bots:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status iron_fly_0dte delta_neutral meic rolling_put_diagonal --no-pager"
```

**Start a stopped bot** (example: MEIC):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start meic"
```

**Stop a bot:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop meic"
```

---

## Deploy Code Changes

After making code changes locally, follow these steps to deploy to the VM:

**Step 1: Commit and push locally:**
```bash
git add -A
git commit -m "your message"
git push
```

**Step 2: Pull on VM and clear Python cache:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && git pull && find bots shared services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; echo Cache cleared'"
```

**Step 3: Restart the bot to apply changes:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"
```

**Step 4: Verify it started correctly:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra --no-pager"
```

> **Why clear cache?** Python caches compiled bytecode in `__pycache__` directories. Stale cache can cause bots to run old code even after `git pull`.

---

## Deploy Dashboard Frontend

Dashboard frontend changes require a local build before deploying.

**Step 1: Build locally:**
```bash
cd dashboard/frontend && npm run build
```

**Step 2: Upload to VM:**
```bash
gcloud compute scp --recurse dashboard/frontend/dist/ calypso-bot:/tmp/dashboard-dist --zone=us-east1-b
```

**Step 3: Install on VM:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp -r /tmp/dashboard-dist/* /opt/calypso/dashboard/frontend/dist/ && sudo chown -R calypso:calypso /opt/calypso/dashboard/frontend/dist/ && rm -rf /tmp/dashboard-dist && echo 'Dashboard frontend deployed'"
```

**Step 4: Restart dashboard:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart dashboard"
```

---

## View Logs

**HYDRA bot log file** (alternative to journalctl):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="tail -100 /opt/calypso/logs/hydra/bot.log"
```

**Cloud Function alert logs** (Telegram/Email delivery):
```bash
gcloud functions logs read process-trading-alert --region=us-east1 --project=calypso-trading-bot --limit=50
```

**Check dead letter queue** (failed alerts):
```bash
gcloud pubsub subscriptions pull calypso-alerts-dlq-sub --project=calypso-trading-bot --limit=10 --auto-ack
```

---

## VM System Commands

**SSH into the VM interactively:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b
```

**Check disk usage:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="df -h"
```

**Check memory usage:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="free -h"
```

**Check running Python processes:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ps aux | grep python"
```

**List all CALYPSO services:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-units --type=service | grep -E '(iron|delta|rolling|meic|hydra|token_keeper|dashboard)'"
```

**List data files:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/data/"
```

---

## Emergency Commands

**Stop HYDRA immediately** (if something goes wrong during market hours):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"
```

**Stop ALL trading bots** (token keeper keeps running to preserve auth):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte delta_neutral rolling_put_diagonal meic hydra"
```

**Stop EVERYTHING including token keeper** (tokens will expire in ~20 min):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop iron_fly_0dte delta_neutral rolling_put_diagonal meic hydra token_keeper"
```

**Check if HYDRA has active positions** (before stopping during market hours):
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="cat /opt/calypso/data/hydra_state.json | python3 -c \"import json,sys; s=json.load(sys.stdin); print('Active entries:', len([e for e in s.get('entries',[]) if not all([e.get('call_side_stopped') or e.get('call_side_expired') or e.get('call_side_skipped'), e.get('put_side_stopped') or e.get('put_side_expired') or e.get('put_side_skipped')])]))\""
```

**Run a diagnostic script on the VM:**
```bash
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python scripts/SCRIPT_NAME.py'"
```

---

## Quick Reference Card

| Action | Command |
|--------|---------|
| Is HYDRA running? | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl status hydra --no-pager"` |
| Start HYDRA | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start hydra"` |
| Stop HYDRA | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra"` |
| Restart HYDRA | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl restart hydra"` |
| HYDRA logs (last 50) | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -n 50 --no-pager"` |
| HYDRA logs (live) | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo journalctl -u hydra -f"` |
| Deploy code | Pull → clear cache → restart (see Deploy section above) |
| Stop everything | `gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop hydra token_keeper"` |
