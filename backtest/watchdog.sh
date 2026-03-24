#!/bin/bash
# HYDRA Backtest Download Watchdog
# Monitors download progress and auto-restarts if stuck
# Run: bash backtest/watchdog.sh

CACHE_DIR="/Users/ddias/Desktop/CALYPSO/Git Repo/backtest/data/cache/options"
LOG_FILE="/tmp/backtest_download.log"
WATCHDOG_LOG="/tmp/watchdog.log"
THETA_JAR="/Users/ddias/Desktop/ThetaTerminal/ThetaTerminal.jar"
THETA_CREDS="/Users/ddias/Desktop/ThetaTerminal/creds.txt"
REPO_DIR="/Users/ddias/Desktop/CALYPSO/Git Repo"
TOTAL=966
STUCK_THRESHOLD=3600  # seconds — normal mode slow dates can take up to 15min each (3 retries × 5min timeout)
CHECK_INTERVAL=60     # check every 60 seconds

log() {
    echo "[$(date '+%H:%M:%S')] $1" | tee -a "$WATCHDOG_LOG"
}

get_file_count() {
    ls "$CACHE_DIR" 2>/dev/null | wc -l | tr -d ' '
}

is_theta_running() {
    ps aux | grep "ThetaTerminal.jar" | grep -v grep > /dev/null 2>&1
}

is_download_running() {
    ps aux | grep "backtest.run" | grep -v grep > /dev/null 2>&1
}

start_theta() {
    log "Starting ThetaTerminal with 8GB heap..."
    cd "$HOME/Desktop/ThetaTerminal" && java -Xms4G -Xmx8G -jar ThetaTerminal.jar --creds-file creds.txt > /tmp/thetaterminal.log 2>&1 &
    THETA_PID=$!
    log "ThetaTerminal PID: $THETA_PID — waiting 25s for boot..."
    sleep 25
    if curl -s "http://127.0.0.1:25510/v2/system/mdds/status" | grep -q "CONNECTED"; then
        log "ThetaTerminal CONNECTED ✓"
        return 0
    else
        log "ThetaTerminal failed to connect ✗"
        return 1
    fi
}

start_download() {
    log "Starting download process (2 workers)..."
    cd "$REPO_DIR" && python -m backtest.run --download >> "$LOG_FILE" 2>&1 &
    DL_PID=$!
    log "Download PID: $DL_PID"
}

kill_download() {
    local pid=$(ps aux | grep "backtest.run" | grep -v grep | awk '{print $2}')
    if [ -n "$pid" ]; then
        kill $pid 2>/dev/null
        log "Killed stuck download (PID $pid)"
        sleep 3
    fi
}

kill_theta() {
    local pid=$(ps aux | grep "ThetaTerminal.jar" | grep -v grep | awk '{print $2}')
    if [ -n "$pid" ]; then
        kill $pid 2>/dev/null
        log "Killed ThetaTerminal (PID $pid)"
        sleep 5
    fi
}

# ── Main watchdog loop ──────────────────────────────────────────────────────

log "========================================"
log "Watchdog started — stuck threshold: ${STUCK_THRESHOLD}s"
log "========================================"

# Start everything fresh if not running
if ! is_theta_running; then
    start_theta || { log "Cannot start ThetaTerminal. Exiting."; exit 1; }
fi
if ! is_download_running; then
    start_download
fi

LAST_COUNT=$(get_file_count)
LAST_PROGRESS_TIME=$(date +%s)
RESTART_COUNT=0

while true; do
    sleep $CHECK_INTERVAL

    CURRENT_COUNT=$(get_file_count)
    NOW=$(date +%s)
    DONE=$CURRENT_COUNT
    REMAINING=$((TOTAL - DONE))

    # Check if complete
    if [ "$DONE" -ge "$TOTAL" ]; then
        log "✅ Download COMPLETE — $DONE/$TOTAL files cached"
        break
    fi

    # Progress made?
    if [ "$CURRENT_COUNT" -gt "$LAST_COUNT" ]; then
        NEW=$((CURRENT_COUNT - LAST_COUNT))
        log "Progress: $DONE/$TOTAL files (+$NEW) — $REMAINING remaining"
        LAST_COUNT=$CURRENT_COUNT
        LAST_PROGRESS_TIME=$NOW
    else
        STUCK_FOR=$((NOW - LAST_PROGRESS_TIME))
        log "No progress for ${STUCK_FOR}s — $DONE/$TOTAL files (threshold: ${STUCK_THRESHOLD}s)"

        if [ "$STUCK_FOR" -ge "$STUCK_THRESHOLD" ]; then
            RESTART_COUNT=$((RESTART_COUNT + 1))
            log "⚠️  STUCK DETECTED — restart #$RESTART_COUNT"

            kill_download
            log "Restarting ThetaTerminal to clear stuck request..."
            kill_theta
            start_theta || { log "ThetaTerminal failed — retrying in 60s"; sleep 60; start_theta; }

            start_download
            LAST_PROGRESS_TIME=$(date +%s)
        fi
    fi

    # Also check if processes died unexpectedly
    if ! is_download_running && [ "$DONE" -lt "$TOTAL" ]; then
        log "⚠️  Download process died — restarting..."
        if ! is_theta_running; then
            log "ThetaTerminal also dead — restarting both..."
            start_theta
        fi
        start_download
        LAST_PROGRESS_TIME=$(date +%s)
    fi
done

log "Watchdog finished after $RESTART_COUNT restarts."
