# ARGUS — Health Monitoring Agent

**Last Updated:** 2026-05-24 (Polish #2 IBKR-era rewrite + AUD2-H2 README sync)

Runs every 15 minutes (24/7) via systemd timer. Checks infrastructure health and sends Telegram/Email alerts on failure.

> **Branch:** This README reflects the IBKR-standalone branch state.
> The Saxo-era version (`token_keeper` + `saxo_token_cache.json`
> checks) lives on `main`. Auth-degradation (mid-session IBKR
> session loss) is NOT covered by ARGUS on this branch — that's
> the Polish #1 alert hooks fired from `bots/hydra/main.py`'s
> `ensure_connected()` gate. ARGUS only detects
> HYDRA-process-alive, not broker-session-alive.

---

## Health Checks

| # | Check | Threshold | Severity |
|---|-------|-----------|----------|
| 1 | HYDRA service running | `systemctl is-active hydra` | FAILURE |
| 2 | HYDRA state-file heartbeat freshness (market hours, non-holiday) | `last_heartbeat_at` < 5 min old | FAILURE |
| 3 | Circuit-breaker OPEN events in last 15 min | `orders` family OPEN = FAIL; any other family OPEN = WARN | FAILURE / WARNING |
| 4 | Disk space | < 85% used | WARNING |
| 5 | Memory usage | < 90% used | WARNING |
| 6 | Log staleness (market hours only, suppressed on holidays) | < 30 min since last HYDRA journal entry | FAILURE |
| 7 | State file JSON integrity | `json.load()` succeeds | FAILURE |
| 8 | GCS backup freshness (after 23:30 UTC) | Today's `hydra_state_YYYYMMDD.json` visible in `gs://calypso-backups/` | WARNING |

**Any FAILURE triggers a Telegram/Email alert. WARNINGs are logged only.**

### What replaced the Saxo-era checks

| Was (Saxo) | Now (IBKR) |
|---|---|
| Check 2: `token_keeper service running` | Check 2: `hydra_state.json` `last_heartbeat_at` freshness — bot writes the timestamp every ~10s status-interval; ARGUS fails if it's stale. Detects bot-process-alive-but-frozen. |
| Check 3: `saxo_token_cache.json` freshness | Check 3: `journalctl -u hydra` grep for `CircuitBreaker[ib.<family>] (CLOSED\|HALF_OPEN) → OPEN` events in the last 15 min. The `orders` family OPEN triggers an ARGUS FAIL; other families OPEN are WARN-level (the Polish #1 Telegram already sent a CRITICAL/HIGH for the trip itself). |
| (none — Saxo had no GCS backup check) | Check 8: confirm `db_backup.timer` actually shipped today's snapshot to GCS. Gated on 23:30 UTC (gives `db_backup.timer` at 23:00 + 30 min slack). |

Holiday-awareness: Checks 2 and 6 are suppressed when `shared.event_calendar.is_market_holiday()` returns True. This prevents 8+ false-positive ARGUS alerts/year on Thanksgiving, Christmas, etc.

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
