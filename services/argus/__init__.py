"""
ARGUS — Health Monitoring Agent for CALYPSO

Runs every 15 minutes (24/7) via systemd timer. Checks infrastructure health
and sends Telegram/Email alerts on failure via AlertService.

Checks (IBKR-era; the old Saxo token_keeper + token-cache checks are gone):
    - HYDRA process is running (systemctl is-active)
    - HYDRA state-file heartbeat freshness (hydra_state.json last_heartbeat_at
      < 5 min old during market hours; detects alive-but-frozen) — replaces
      the old token_keeper check
    - Circuit-breaker OPEN events in the last 15 min (scans the broker's
      rotating log file for CircuitBreaker[ib.<family>] → OPEN; an
      orders-family OPEN => FAIL, any other family => WARN) — IBKR-era
    - Disk space (< 85% used)
    - Memory usage (< 90% used)
    - Log staleness during market hours (< 30 min since last HYDRA log)
    - State file JSON integrity (hydra_state.json)
    - GCS backup freshness (today's/yesterday's hydra_state snapshot visible
      in gs://calypso-backups/)

Files:
    services/argus/health_check.sh    Main bash health check script
    services/argus/notify.py          Python wrapper to send alerts via AlertService
    deploy/argus.service              systemd oneshot service
    deploy/argus.timer                systemd timer (every 15 min)

Output:
    intel/argus/health_log.jsonl      JSON Lines health log (one entry per check)
    intel/argus/incidents/            Incident reports on failure

Last Updated: 2026-05-31
"""
