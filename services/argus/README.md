# ARGUS — Health Monitoring Agent

**Last Updated:** 2026-03-01

Runs every 15 minutes (24/7) via systemd timer. Checks infrastructure health and sends Telegram/Email alerts on failure.

---

## Health Checks

| # | Check | Threshold | Severity |
|---|-------|-----------|----------|
| 1 | HYDRA service running | `systemctl is-active hydra` | FAILURE |
| 2 | token_keeper service running | `systemctl is-active token_keeper` | FAILURE |
| 3 | Token cache freshness | < 25 minutes old | FAILURE |
| 4 | Disk space | < 85% used | WARNING |
| 5 | Memory usage | < 90% used | WARNING |
| 6 | Log staleness (market hours only) | < 30 min since last HYDRA log | FAILURE |
| 7 | State file JSON integrity | `json.load()` succeeds | FAILURE |

**Any FAILURE triggers a Telegram/Email alert. WARNINGs are logged only.**

---

## Files

| File | Purpose |
|------|---------|
| `services/argus/health_check.sh` | Main bash health check script |
| `services/argus/notify.py` | Python wrapper to send alerts via AlertService |
| `services/argus/__init__.py` | Package docstring |
| `deploy/argus.service` | systemd oneshot service |
| `deploy/argus.timer` | systemd timer (every 15 min) |

## Output

| Path | Format | Description |
|------|--------|-------------|
| `intel/argus/health_log.jsonl` | JSON Lines | One entry per check (PASS/FAIL + all metrics) |
| `intel/argus/incidents/` | Text files | Incident reports on failure |

---

## First-Time Deployment

```bash
# 1. Create intel directories on VM
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo -u calypso mkdir -p /opt/calypso/intel/argus/incidents"

# 2. Copy service + timer files to systemd
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo cp /opt/calypso/deploy/argus.service /opt/calypso/deploy/argus.timer /etc/systemd/system/"

# 3. Reload systemd and enable timer
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl daemon-reload && sudo systemctl enable argus.timer && sudo systemctl start argus.timer"

# 4. Verify timer is active
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-timers | grep argus"
```

## Commands

```bash
# Run manually (test)
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl start argus.service && sudo journalctl -u argus -n 20 --no-pager"

# View timer status
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl list-timers | grep argus"

# View recent health log
gcloud compute ssh calypso-bot --zone=us-east1-b --command="tail -5 /opt/calypso/intel/argus/health_log.jsonl | python3 -m json.tool"

# View incidents
gcloud compute ssh calypso-bot --zone=us-east1-b --command="ls -la /opt/calypso/intel/argus/incidents/"

# Disable timer
gcloud compute ssh calypso-bot --zone=us-east1-b --command="sudo systemctl stop argus.timer && sudo systemctl disable argus.timer"
```
