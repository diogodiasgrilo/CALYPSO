"""
ARGUS — Health Monitoring Agent for CALYPSO

Runs every 15 minutes (24/7) via systemd timer. Checks infrastructure health
and sends Telegram/Email alerts on failure via AlertService.

Checks:
    - HYDRA service status (systemctl is-active)
    - token_keeper service status
    - Token cache freshness (< 25 minutes old)
    - Disk space (< 85% used)
    - Memory usage (< 90% used)
    - Log staleness during market hours (< 30 min since last HYDRA log)
    - State file JSON integrity (hydra_state.json)

Files:
    services/argus/health_check.sh    Main bash health check script
    services/argus/notify.py          Python wrapper to send alerts via AlertService
    deploy/argus.service              systemd oneshot service
    deploy/argus.timer                systemd timer (every 15 min)

Output:
    intel/argus/health_log.jsonl      JSON Lines health log (one entry per check)
    intel/argus/incidents/            Incident reports on failure

Last Updated: 2026-03-01
"""
