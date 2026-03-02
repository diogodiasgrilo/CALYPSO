#!/usr/bin/env bash
# ARGUS — CALYPSO Health Monitor
#
# Runs every 15 minutes via systemd timer. Checks infrastructure health,
# appends result to health_log.jsonl, and calls notify.py on failure.
#
# Usage: /opt/calypso/services/argus/health_check.sh
# Requires: bash, systemctl

set -euo pipefail

# --- Configuration ---
CALYPSO_DIR="/opt/calypso"
VENV_PYTHON="${CALYPSO_DIR}/.venv/bin/python"
HEALTH_LOG="${CALYPSO_DIR}/intel/argus/health_log.jsonl"
INCIDENT_DIR="${CALYPSO_DIR}/intel/argus/incidents"
NOTIFY_SCRIPT="${CALYPSO_DIR}/services/argus/notify.py"
TOKEN_CACHE="${CALYPSO_DIR}/data/saxo_token_cache.json"
STATE_FILE="${CALYPSO_DIR}/data/hydra_state.json"

# Thresholds
TOKEN_MAX_AGE_MIN=25      # Token cache older than this = stale
DISK_WARN_PCT=85          # Disk usage above this = warning
MEMORY_WARN_PCT=90        # Memory usage above this = warning
LOG_STALE_MIN=30          # No HYDRA log in this many minutes during market = stale

# --- Ensure output directories exist ---
mkdir -p "$(dirname "${HEALTH_LOG}")" "${INCIDENT_DIR}"

# --- Timestamp ---
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
FAILURES=()
WARNINGS=()

# --- Helper: check if US market is roughly open (ET hours, weekday) ---
is_market_hours() {
    local hour_et
    # Get ET hour (handles DST via TZ)
    hour_et=$(TZ="America/New_York" date +"%H")
    local dow
    dow=$(date +"%u")  # 1=Mon, 7=Sun

    # Weekday and between 9:00 AM and 5:00 PM ET (generous window)
    if [[ "$dow" -le 5 ]] && [[ "$hour_et" -ge 9 ]] && [[ "$hour_et" -lt 17 ]]; then
        return 0
    fi
    return 1
}

# =========================================================================
# CHECK 1: HYDRA service
# =========================================================================
hydra_status="ok"
if ! systemctl is-active --quiet hydra 2>/dev/null; then
    hydra_status="down"
    FAILURES+=("HYDRA service is not running")
fi

# =========================================================================
# CHECK 2: token_keeper service
# =========================================================================
token_keeper_status="ok"
if ! systemctl is-active --quiet token_keeper 2>/dev/null; then
    token_keeper_status="down"
    FAILURES+=("token_keeper service is not running")
fi

# =========================================================================
# CHECK 3: Token cache freshness
# =========================================================================
token_cache_status="ok"
token_age_min="N/A"
if [[ -f "${TOKEN_CACHE}" ]]; then
    cache_mtime=$(stat -c %Y "${TOKEN_CACHE}" 2>/dev/null || stat -f %m "${TOKEN_CACHE}" 2>/dev/null || echo "0")
    now_epoch=$(date +%s)
    age_sec=$(( now_epoch - cache_mtime ))
    token_age_min=$(( age_sec / 60 ))

    if [[ "${token_age_min}" -gt "${TOKEN_MAX_AGE_MIN}" ]]; then
        token_cache_status="stale"
        FAILURES+=("Token cache is ${token_age_min}m old (max ${TOKEN_MAX_AGE_MIN}m)")
    fi
else
    token_cache_status="missing"
    FAILURES+=("Token cache file not found: ${TOKEN_CACHE}")
fi

# =========================================================================
# CHECK 4: Disk space
# =========================================================================
disk_status="ok"
disk_pct=$(df --output=pcent / 2>/dev/null | tail -1 | tr -d '% ' || echo "0")
if [[ "${disk_pct}" -gt "${DISK_WARN_PCT}" ]]; then
    disk_status="warning"
    WARNINGS+=("Disk usage at ${disk_pct}% (threshold ${DISK_WARN_PCT}%)")
fi

# =========================================================================
# CHECK 5: Memory usage
# =========================================================================
memory_status="ok"
memory_pct=$(free | awk '/Mem:/ {printf "%.0f", $3/$2*100}' 2>/dev/null || echo "0")
if [[ "${memory_pct}" -gt "${MEMORY_WARN_PCT}" ]]; then
    memory_status="warning"
    WARNINGS+=("Memory usage at ${memory_pct}% (threshold ${MEMORY_WARN_PCT}%)")
fi

# =========================================================================
# CHECK 6: Log staleness (market hours only)
# =========================================================================
log_status="ok"
log_age_min="N/A"
if is_market_hours; then
    # Get timestamp of last HYDRA log line
    last_log_epoch=$(journalctl -u hydra -n 1 --no-pager -o short-unix 2>/dev/null | awk '{print int($1)}' || echo "0")
    if [[ "${last_log_epoch}" -gt 0 ]]; then
        now_epoch=$(date +%s)
        log_age_sec=$(( now_epoch - last_log_epoch ))
        log_age_min=$(( log_age_sec / 60 ))

        if [[ "${log_age_min}" -gt "${LOG_STALE_MIN}" ]]; then
            log_status="stale"
            FAILURES+=("HYDRA log stale: last entry ${log_age_min}m ago (max ${LOG_STALE_MIN}m)")
        fi
    else
        log_status="no_logs"
        WARNINGS+=("No HYDRA journal logs found")
    fi
fi

# =========================================================================
# CHECK 7: State file JSON integrity
# =========================================================================
state_status="ok"
if [[ -f "${STATE_FILE}" ]]; then
    if ! "${VENV_PYTHON}" -c "import json; json.load(open('${STATE_FILE}'))" 2>/dev/null; then
        state_status="corrupt"
        FAILURES+=("State file is not valid JSON: ${STATE_FILE}")
    fi
else
    state_status="missing"
    # Not a failure — state file may not exist if no trading today
    WARNINGS+=("State file not found (may be normal outside trading)")
fi

# =========================================================================
# BUILD RESULT
# =========================================================================
overall="PASS"
if [[ ${#FAILURES[@]} -gt 0 ]]; then
    overall="FAIL"
fi

# Build JSON log entry
log_entry=$(cat <<JSONEOF
{"timestamp":"${TIMESTAMP}","status":"${overall}","hydra":"${hydra_status}","token_keeper":"${token_keeper_status}","token_cache":"${token_cache_status}","token_age_min":"${token_age_min}","disk_pct":"${disk_pct}","disk":"${disk_status}","memory_pct":"${memory_pct}","memory":"${memory_status}","log":"${log_status}","log_age_min":"${log_age_min}","state_file":"${state_status}","failures":${#FAILURES[@]},"warnings":${#WARNINGS[@]}}
JSONEOF
)

# Append to health log
echo "${log_entry}" >> "${HEALTH_LOG}"

# =========================================================================
# ON FAILURE: write incident report + send alert
# =========================================================================
if [[ "${overall}" == "FAIL" ]]; then
    # Build failure message
    failure_msg="ARGUS Health Check FAILED at ${TIMESTAMP}"
    for f in "${FAILURES[@]}"; do
        failure_msg="${failure_msg}\n- ${f}"
    done
    for w in "${WARNINGS[@]}"; do
        failure_msg="${failure_msg}\n- [warn] ${w}"
    done

    # Write incident report
    incident_file="${INCIDENT_DIR}/incident_$(date -u +%Y%m%d_%H%M%S).txt"
    echo -e "${failure_msg}" > "${incident_file}"

    # Send alert via Python notifier
    if [[ -x "${VENV_PYTHON}" ]] && [[ -f "${NOTIFY_SCRIPT}" ]]; then
        echo -e "${failure_msg}" | "${VENV_PYTHON}" "${NOTIFY_SCRIPT}" 2>&1 || true
    else
        echo "WARNING: Cannot send alert — notify.py or venv python not found" >&2
    fi

    echo "ARGUS: FAIL (${#FAILURES[@]} failures, ${#WARNINGS[@]} warnings)"
    exit 1
fi

echo "ARGUS: PASS (${#WARNINGS[@]} warnings)"
exit 0
