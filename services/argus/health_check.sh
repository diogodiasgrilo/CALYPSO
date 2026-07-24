#!/usr/bin/env bash
#
# ARGUS — HYDRA-on-IBKR Health Monitor (Polish Item 2, 2026-05-24)
# ================================================================
#
# Runs every 15 minutes via systemd timer. Checks infrastructure
# health, appends result to health_log.jsonl, and calls notify.py
# on failure.
#
# IBKR-era checks (replaced Saxo-era token_keeper + saxo_token_cache):
#   1. HYDRA process is running
#   2. HYDRA state-file heartbeat — `last_heartbeat_at` < 5 min old
#      during regular market hours
#   3. Circuit-breaker state — `orders` family OPEN in last 15 min = FAIL,
#      any other family OPEN = WARN
#   4. Disk usage <= 85%
#   5. Memory usage <= 90%
#   6. Log staleness (market hours only) — last journal entry < 30 min
#   7. State file JSON integrity
#   8. GCS backup freshness (after 23:30 UTC: yesterday's backup
#      must be visible in gs://calypso-backups/)
#
# Auth-degradation (mid-session session loss) detection lives in
# bots/hydra/main.py's ensure_connected() gate + the Polish Item 1
# Telegram alerts. ARGUS detects HYDRA-process-alive, NOT
# broker-session-alive.
#
# Usage: /opt/calypso/services/argus/health_check.sh
# Requires: bash, systemctl, python3 (the venv at /opt/calypso/.venv —
#           used for JSON parsing in Check 2 + Check 7 and broker-log
#           timestamp windowing in Check 3), gsutil (Check 8 only;
#           gracefully degrades if absent).
# AUD2-L3: prior comment listed `jq (preferred) or python3 (fallback)`
# but the script always uses Python (no jq calls anywhere).

set -uo pipefail

# --- Configuration ---
CALYPSO_DIR="/opt/calypso"
VENV_PYTHON="${CALYPSO_DIR}/.venv/bin/python"
HEALTH_LOG="${CALYPSO_DIR}/intel/argus/health_log.jsonl"
INCIDENT_DIR="${CALYPSO_DIR}/intel/argus/incidents"
NOTIFY_SCRIPT="${CALYPSO_DIR}/services/argus/notify.py"
STATE_FILE="${CALYPSO_DIR}/data/hydra_state.json"
# I-H1: per-variant state files. Whichever of B/C holds the live paper seat
# (B since the 2026-07-24 swap; was C before) previously had ZERO ARGUS
# coverage — a crash/freeze/stale-state there reported PASS. CHECK 9 below
# watches both B and C regardless of which is currently live.
VARIANT_B_STATE_FILE="${CALYPSO_DIR}/data/variant_b/hydra_state.json"
VARIANT_C_STATE_FILE="${CALYPSO_DIR}/data/variant_c/hydra_state.json"
GCS_BACKUPS="gs://calypso-backups"
# Circuit breakers live in the calypso-broker process (P5a/commit 13776d6), not
# in hydra, and the broker logs to a rotating FILE (not journald). Check 3 scans
# this file for the breaker-OPEN line. Path matches services/broker/main.py:54
# (CALYPSO_BROKER_LOG default) + entry_window_watch.py:48.
BROKER_LOG="${CALYPSO_DIR}/logs/broker/broker.log"

# Thresholds
STATE_HEARTBEAT_MAX_AGE_MIN=5   # State-file `last_heartbeat_at` older than this during market = FAIL
DISK_WARN_PCT=85
MEMORY_WARN_PCT=90
LOG_STALE_MIN=30
BREAKER_OPEN_FAIL_FAMILIES=("orders")  # OPEN on these = FAIL; any other family OPEN = WARN

# --- Ensure output directories exist ---
mkdir -p "$(dirname "${HEALTH_LOG}")" "${INCIDENT_DIR}"

# --- Timestamp ---
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
FAILURES=()
WARNINGS=()

# ─── Helper: regular session window (broad) ──────────────────────────────
# Returns 0 (true) on weekdays between 9 AM and 5 PM ET. The state-file
# heartbeat check + log-staleness check rely on this. Generous window
# accounts for pre-market data fetching + post-market settlement work.
is_market_hours() {
    local hour_et
    hour_et=$(TZ="America/New_York" date +"%H")
    local dow
    dow=$(date +"%u")
    if [[ "$dow" -le 5 ]] && [[ "$hour_et" -ge 9 ]] && [[ "$hour_et" -lt 17 ]]; then
        return 0
    fi
    return 1
}

# ─── Helper: core trading session (tight) ────────────────────────────────
# Returns 0 (true) weekdays 9:30 AM–4:00 PM ET — the window during which HYDRA
# writes `last_heartbeat_at` every ~10s. The heartbeat-freshness FAIL (Check 2)
# gates on THIS, not is_market_hours(): after the 4 PM close HYDRA sleeps in
# 15-min cycles and stops updating the heartbeat BY DESIGN, so the broad 9–5
# is_market_hours window produced a daily FALSE FAIL in the 4–5 PM ET hour
# (state-file froze at 16:00, ARGUS still demanded a <5m heartbeat until 17:00).
is_trading_session() {
    local dow hhmm
    dow=$(date +"%u")
    # %H%M as a base-10 int (10# avoids octal parse of the leading zero).
    hhmm=$((10#$(TZ="America/New_York" date +"%H%M")))
    # Start at 9:40, not 9:30: HYDRA detects the open via a 60s pre-market
    # recheck loop and doesn't write its first market-hours heartbeat until
    # ~9:31; an ARGUS tick landing in that 9:30–9:31 ramp-up read the stale
    # OVERNIGHT heartbeat and false-FAILed (observed 2026-06-02 09:31). A 10-min
    # post-open grace clears the ramp-up; Check 1 (process) + Check 6 (log
    # staleness) still cover liveness in that window. End at 16:00 (the close,
    # after which HYDRA stops the tight heartbeat by design).
    if [[ "$dow" -le 5 ]] && [[ "$hhmm" -ge 940 ]] && [[ "$hhmm" -lt 1600 ]]; then
        return 0
    fi
    return 1
}

# ─── Helper: US market holiday (suppress log-staleness on holidays) ─────
# Calls into shared.event_calendar for an authoritative answer. If the
# Python module is missing or the call fails, defaults to "not a
# holiday" — which is the conservative answer (we'd rather have ARGUS
# fire a recoverable false positive on a holiday than suppress a real
# stale-log incident on a regular day).
is_market_holiday() {
    if [[ ! -x "${VENV_PYTHON}" ]]; then
        return 1
    fi
    "${VENV_PYTHON}" -c "
import sys
sys.path.insert(0, '${CALYPSO_DIR}')
try:
    # 2026-06-19 fix: shared.event_calendar NEVER exported is_market_holiday, so
    # this import always raised -> except -> exit 1 ('not a holiday'). ARGUS thus
    # treated EVERY market holiday (e.g. Juneteenth, a Friday) as a trading day and
    # spammed stale-heartbeat FAILs every 15 min (the bots are idle on a closed
    # market and stop writing last_heartbeat_at). Use the AUTHORITATIVE holiday
    # calendar in shared.market_hours (get_holiday_name — the same source the bots
    # use to decide the market is closed). Defaults to now-ET.
    from shared.market_hours import get_holiday_name
    sys.exit(0 if get_holiday_name() else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# ─── Helper: per-variant health (process + heartbeat + state + log) ──────
# I-H1: ARGUS originally watched ONLY variant A (the inline CHECKs 1/2/6/7
# below). Whichever of B/C holds the live paper seat had NO watchdog, so a
# crash/freeze/stale-state there went unobserved (compounded by the alerts
# gap on that variant). This function adds process + heartbeat-freshness + state JSON
# integrity + log-staleness for an arbitrary variant unit, appending to the
# global FAILURES/WARNINGS arrays prefixed with the unit name. A's proven
# inline checks are left untouched (this is purely additive). The
# heartbeat-missing case is intentionally SILENT (no WARN) to avoid a recurring
# false positive if a variant doesn't populate the field — log-staleness +
# process liveness still catch a frozen variant either way.
check_variant_health() {
    local unit="$1"
    local vstate="$2"

    # Process liveness. If the variant is down, skip the downstream state/log
    # checks (they'd be redundant noise) — the process FAIL is the signal.
    if ! systemctl is-active --quiet "${unit}" 2>/dev/null; then
        FAILURES+=("${unit} service is not running")
        return
    fi

    # Heartbeat freshness — only during the tight trading session + non-holiday,
    # mirroring CHECK 2's gating. FAIL only if the field exists and is stale.
    if is_trading_session && ! is_market_holiday; then
        if [[ -f "${vstate}" ]]; then
            local v_hb
            v_hb=$("${VENV_PYTHON}" -c "
import json, sys
try:
    with open('${vstate}') as f:
        s = json.load(f)
    v = s.get('last_heartbeat_at') or s.get('daily_state', {}).get('last_heartbeat_at')
    print(v or '')
except Exception:
    print('')
" 2>/dev/null)
            if [[ -n "${v_hb}" ]]; then
                local v_hb_epoch
                v_hb_epoch=$("${VENV_PYTHON}" -c "
from datetime import datetime
try:
    s = '${v_hb}'.replace('Z', '+00:00')
    print(int(datetime.fromisoformat(s).timestamp()))
except Exception:
    print(0)
" 2>/dev/null)
                if [[ "${v_hb_epoch}" -gt 0 ]]; then
                    local v_age_min=$(( ( $(date +%s) - v_hb_epoch ) / 60 ))
                    if [[ "${v_age_min}" -gt "${STATE_HEARTBEAT_MAX_AGE_MIN}" ]]; then
                        FAILURES+=("${unit} state-file heartbeat is ${v_age_min}m old (max ${STATE_HEARTBEAT_MAX_AGE_MIN}m)")
                    fi
                fi
            fi
        else
            WARNINGS+=("${unit} state file not found: ${vstate}")
        fi
    fi

    # State-file JSON integrity (any time the file exists).
    if [[ -f "${vstate}" ]]; then
        if ! "${VENV_PYTHON}" -c "import json; json.load(open('${vstate}'))" 2>/dev/null; then
            FAILURES+=("${unit} state file is not valid JSON: ${vstate}")
        fi
    fi

    # Log staleness — broad market-hours window + non-holiday, mirroring CHECK 6.
    if is_market_hours && ! is_market_holiday; then
        local v_log_epoch
        v_log_epoch=$(journalctl -u "${unit}" -n 1 --no-pager -o short-unix 2>/dev/null | awk '{print int($1)}' || echo "0")
        if [[ "${v_log_epoch}" -gt 0 ]]; then
            local v_log_age_min=$(( ( $(date +%s) - v_log_epoch ) / 60 ))
            if [[ "${v_log_age_min}" -gt "${LOG_STALE_MIN}" ]]; then
                FAILURES+=("${unit} log stale: last entry ${v_log_age_min}m ago (max ${LOG_STALE_MIN}m)")
            fi
        fi
    fi
}

# =========================================================================
# CHECK 1: HYDRA process is running
# =========================================================================
hydra_status="ok"
if ! systemctl is-active --quiet hydra 2>/dev/null; then
    hydra_status="down"
    FAILURES+=("hydra service is not running")
fi

# =========================================================================
# CHECK 2: HYDRA state-file heartbeat freshness (REPLACES Saxo token_keeper).
# Polish Item 2: HYDRA writes `last_heartbeat_at` into hydra_state.json
# every status-interval (~10s) tick during market hours. ARGUS reads
# the field; >= STATE_HEARTBEAT_MAX_AGE_MIN minutes during regular
# market hours = FAIL.
#
# NOTE: Auth-degradation (mid-session session loss) is NOT detected
# here; it's the responsibility of bots/hydra/main.py's
# ensure_connected() gate + the API_ERROR Telegram alert. ARGUS only
# detects HYDRA-process-alive, not broker-session-alive.
# =========================================================================
heartbeat_status="ok"
heartbeat_age_min="N/A"
# Gate on the TIGHT 9:30–16:00 session: HYDRA only writes the ~10s heartbeat
# during the open session; post-close it sleeps 15m and stops (by design), so
# enforcing the <5m heartbeat in the 16:00–17:00 hour is a guaranteed false FAIL.
if is_trading_session && ! is_market_holiday; then
    if [[ -f "${STATE_FILE}" ]]; then
        # Pull last_heartbeat_at via Python (atomic state-file reader)
        last_hb=$("${VENV_PYTHON}" -c "
import json, sys
try:
    with open('${STATE_FILE}') as f:
        s = json.load(f)
    v = s.get('last_heartbeat_at') or s.get('daily_state', {}).get('last_heartbeat_at')
    print(v or '')
except Exception:
    print('')
" 2>/dev/null)
        if [[ -n "${last_hb}" ]]; then
            # last_hb is an ISO 8601 string (UTC). Convert to epoch.
            last_hb_epoch=$("${VENV_PYTHON}" -c "
from datetime import datetime
try:
    s = '${last_hb}'.replace('Z', '+00:00')
    print(int(datetime.fromisoformat(s).timestamp()))
except Exception:
    print(0)
" 2>/dev/null)
            if [[ "${last_hb_epoch}" -gt 0 ]]; then
                now_epoch=$(date +%s)
                age_sec=$(( now_epoch - last_hb_epoch ))
                heartbeat_age_min=$(( age_sec / 60 ))
                if [[ "${heartbeat_age_min}" -gt "${STATE_HEARTBEAT_MAX_AGE_MIN}" ]]; then
                    heartbeat_status="stale"
                    FAILURES+=("HYDRA state-file heartbeat is ${heartbeat_age_min}m old (max ${STATE_HEARTBEAT_MAX_AGE_MIN}m)")
                fi
            else
                heartbeat_status="unparseable"
                WARNINGS+=("HYDRA state-file last_heartbeat_at could not be parsed")
            fi
        else
            heartbeat_status="missing"
            # Field not in state file — likely the bot hasn't ticked
            # since the field was added in Polish Item 2. WARN, don't FAIL.
            WARNINGS+=("HYDRA state-file has no last_heartbeat_at field")
        fi
    else
        heartbeat_status="no_state"
        WARNINGS+=("HYDRA state file not found (may be normal pre-9:30)")
    fi
fi

# =========================================================================
# CHECK 3: Circuit-breaker OPEN events in last 15 minutes (NEW IBKR-era)
# Polish Item 2: scan the broker log for the canonical breaker-OPEN log line
# from shared/ib_retry.py:137 — "CircuitBreaker[ib.<family>] <state> → OPEN — <reason>".
# Any match on a BREAKER_OPEN_FAIL_FAMILIES family = FAIL. Any other
# family OPEN = WARN.
#
# SOURCE: the five ib.* breakers live in the calypso-broker process's
# IBClient (shared/ib_client.py:560), NOT in hydra — the strategies use a
# BrokerClient with circuit_breakers={}. The broker writes to a rotating
# FILE (BROKER_LOG), not journald, so we scan that file (+ its most recent
# rotation) and window by the ET timestamp the broker stamps on each line
# ("YYYY-MM-DD HH:MM:SS,mmm | LEVEL | calypso-broker | ..."). Fails CLOSED:
# any parse/read error yields NO suppression of a real OPEN — we simply emit
# whatever lines we can positively match within the window.
# =========================================================================
breaker_status="ok"
breaker_opens_today="0"
# Extract breaker-OPEN lines from the last 15 minutes out of the broker's
# ET-stamped rotating log. Done in Python so we can parse the timestamp prefix
# (journalctl's --since is unavailable for a flat file).
breaker_log=$("${VENV_PYTHON}" -c "
import os, re, sys
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
    et = ZoneInfo('America/New_York')
except Exception:
    et = None
now = datetime.now(et) if et else datetime.now()
cutoff = now - timedelta(minutes=15)
line_re = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
# ib_retry logs the prior state as the LOWERCASE enum value (CircuitState.value
# = "closed"/"half_open"); match case-insensitively so the orders-breaker trip
# is actually detected (the safety-critical FAIL path).
open_re = re.compile(r'CircuitBreaker\[ib\.[a-z_]+\] (?:closed|half_open) . OPEN', re.IGNORECASE)
out = []
for path in ('${BROKER_LOG}', '${BROKER_LOG}.1'):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for ln in fh:
                if not open_re.search(ln):
                    continue
                m = line_re.match(ln)
                if not m:
                    # No parseable timestamp — keep it (fail closed: don't drop a real OPEN)
                    out.append(ln.rstrip('\n'))
                    continue
                ts = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
                if et:
                    ts = ts.replace(tzinfo=et)
                if ts >= cutoff:
                    out.append(ln.rstrip('\n'))
    except FileNotFoundError:
        continue
    except Exception:
        continue
print('\n'.join(out))
" 2>/dev/null)
if [[ -n "${breaker_log}" ]]; then
    breaker_opens_today=$(echo "${breaker_log}" | wc -l | tr -d ' ')
    # Check if any of our FAIL families are in the matched lines.
    fail_match=""
    for family in "${BREAKER_OPEN_FAIL_FAMILIES[@]}"; do
        if echo "${breaker_log}" | grep -q "CircuitBreaker\[ib\.${family}\]"; then
            fail_match="${family}"
            break
        fi
    done
    if [[ -n "${fail_match}" ]]; then
        breaker_status="fail_family_open"
        FAILURES+=("Circuit breaker '${fail_match}' tripped OPEN in last 15min (see RUNBOOKS.md RB-2)")
    else
        breaker_status="non_fail_family_open"
        WARNINGS+=("Circuit breaker OPEN events in last 15min: ${breaker_opens_today} (non-fail families)")
    fi
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
# CHECK 6: Log staleness — market hours only, suppressed on holidays
# =========================================================================
log_status="ok"
log_age_min="N/A"
if is_market_hours && ! is_market_holiday; then
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
    WARNINGS+=("State file not found (may be normal outside trading)")
fi

# =========================================================================
# CHECK 8: GCS backup freshness (Polish Item 4)
# After 23:30 UTC (gives db_backup.timer at 23:00 + 30min slack for
# gsutil cp), today's backup should be visible. Before 23:30 UTC we
# check that YESTERDAY's backup exists (must have completed by now).
# =========================================================================
backup_status="ok"
current_utc_hour=$(date -u +%H)
current_utc_minute=$(date -u +%M)
expected_date=""
# Decide which date's backup we expect
if [[ "${current_utc_hour}" -gt 23 ]] || \
   ( [[ "${current_utc_hour}" -eq 23 ]] && [[ "${current_utc_minute}" -ge 30 ]] ); then
    # 23:30+ UTC — today's backup should be in. Check for it.
    expected_date=$(date -u +%Y%m%d)
else
    # Before 23:30 UTC — yesterday's backup must exist.
    expected_date=$(date -u -d "yesterday" +%Y%m%d 2>/dev/null || \
                    date -u -v-1d +%Y%m%d 2>/dev/null || \
                    echo "")
fi
if [[ -n "${expected_date}" ]] && command -v gsutil >/dev/null 2>&1; then
    if gsutil -q stat "${GCS_BACKUPS}/hydra_state_${expected_date}.json" 2>/dev/null; then
        backup_status="ok"
    else
        backup_status="missing"
        WARNINGS+=("GCS backup missing: hydra_state_${expected_date}.json (run db_backup.service?)")
    fi
elif ! command -v gsutil >/dev/null 2>&1; then
    backup_status="no_gsutil"
    # Not a failure — gsutil may not be available on dev machines
fi

# =========================================================================
# CHECK 9: Variant coverage (I-H1) — whichever of B/C holds the LIVE
# real-paper-order seat (B since 2026-07-24; was C before) MUST be watched;
# both are watched regardless so a future swap needs no code change here.
# Uses check_variant_health() (process + heartbeat + state integrity + log
# staleness). Only runs if the unit is installed on this host (a single-bot
# host without the variant units shouldn't false-FAIL).
# =========================================================================
variant_c_status="ok"
variant_b_status="ok"
if systemctl list-unit-files hydra_variant_c.service >/dev/null 2>&1; then
    pre_c=${#FAILURES[@]}
    check_variant_health "hydra_variant_c" "${VARIANT_C_STATE_FILE}"
    [[ ${#FAILURES[@]} -gt ${pre_c} ]] && variant_c_status="fail"
else
    variant_c_status="not_installed"
fi
if systemctl list-unit-files hydra_variant_b.service >/dev/null 2>&1; then
    pre_b=${#FAILURES[@]}
    check_variant_health "hydra_variant_b" "${VARIANT_B_STATE_FILE}"
    [[ ${#FAILURES[@]} -gt ${pre_b} ]] && variant_b_status="fail"
else
    variant_b_status="not_installed"
fi

# =========================================================================
# BUILD RESULT
# =========================================================================
overall="PASS"
if [[ ${#FAILURES[@]} -gt 0 ]]; then
    overall="FAIL"
fi

# Build JSON log entry — replaced Saxo-era keys (token_keeper,
# token_cache, token_age_min) with IBKR-era keys (heartbeat,
# heartbeat_age_min, breaker, breaker_opens_today, backup).
log_entry=$(cat <<JSONEOF
{"timestamp":"${TIMESTAMP}","status":"${overall}","hydra":"${hydra_status}","heartbeat":"${heartbeat_status}","heartbeat_age_min":"${heartbeat_age_min}","breaker":"${breaker_status}","breaker_opens_today":"${breaker_opens_today}","disk_pct":"${disk_pct}","disk":"${disk_status}","memory_pct":"${memory_pct}","memory":"${memory_status}","log":"${log_status}","log_age_min":"${log_age_min}","state_file":"${state_status}","backup":"${backup_status}","variant_c":"${variant_c_status}","variant_b":"${variant_b_status}","failures":${#FAILURES[@]},"warnings":${#WARNINGS[@]}}
JSONEOF
)

# Append to health log
echo "${log_entry}" >> "${HEALTH_LOG}"

# =========================================================================
# ON FAILURE: write incident report + send alert
# =========================================================================
if [[ "${overall}" == "FAIL" ]]; then
    failure_msg="ARGUS Health Check FAILED at ${TIMESTAMP}"
    for f in "${FAILURES[@]}"; do
        failure_msg="${failure_msg}\n- ${f}"
    done
    for w in "${WARNINGS[@]}"; do
        failure_msg="${failure_msg}\n- [warn] ${w}"
    done
    incident_file="${INCIDENT_DIR}/incident_$(date -u +%Y%m%d_%H%M%S).txt"
    echo -e "${failure_msg}" > "${incident_file}"
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
