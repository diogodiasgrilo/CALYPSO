#!/bin/bash
# HYDRA Backtest Download Monitor
# Run: bash backtest/monitor_download.sh

CACHE_DIR="/Users/ddias/Desktop/CALYPSO/Git Repo/backtest/data/cache/options"
LOG_FILE="/tmp/backtest_download.log"
TOTAL=966
START_TIME=$(date +%s)
START_COUNT=$(ls "$CACHE_DIR" 2>/dev/null | wc -l | tr -d ' ')

while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START_TIME))
    DONE=$(ls "$CACHE_DIR" 2>/dev/null | wc -l | tr -d ' ')
    REMAINING=$((TOTAL - DONE))
    DOWNLOADED=$((DONE - START_COUNT))

    # Rate and ETA
    if [ "$DOWNLOADED" -gt 0 ] && [ "$ELAPSED" -gt 0 ]; then
        SECS_PER_FILE=$(echo "scale=1; $ELAPSED / $DOWNLOADED" | bc)
        ETA_SECS=$(echo "$REMAINING * $SECS_PER_FILE / 1" | bc)
        ETA_MIN=$((ETA_SECS / 60))
        ETA_HR=$((ETA_MIN / 60))
        ETA_MIN_REM=$((ETA_MIN % 60))
        RATE=$(echo "scale=1; 60 / $SECS_PER_FILE" | bc)
        if [ "$ETA_HR" -gt 0 ]; then
            ETA_STR="${ETA_HR}h ${ETA_MIN_REM}m"
        else
            ETA_STR="${ETA_MIN_REM}m"
        fi
    else
        ETA_STR="calculating..."
        RATE="..."
        SECS_PER_FILE="..."
    fi

    # Progress bar
    PCT=$((DONE * 100 / TOTAL))
    BARS=$((DONE * 40 / TOTAL))
    BAR=$(printf '█%.0s' $(seq 1 $BARS 2>/dev/null))
    EMPTY=$(printf '░%.0s' $(seq 1 $((40 - BARS)) 2>/dev/null))

    # Elapsed
    EL_MIN=$((ELAPSED / 60))
    EL_SEC=$((ELAPSED % 60))

    # Build set of dates that are already done (completed ✓, failed ✗, or no data)
    DONE_DATES=$(grep -E "✓$|✗$|no data" "$LOG_FILE" 2>/dev/null | grep -oE "[0-9]{4}-[0-9]{2}-[0-9]{2}" | sort -u)

    # Active workers — dates currently being downloaded but NOT yet in done set
    ACTIVE_RAW=$(grep "Downloading SPXW chain" "$LOG_FILE" 2>/dev/null | grep -oE "[0-9]{4}-[0-9]{2}-[0-9]{2}" | sort -u)
    ACTIVE=""
    while IFS= read -r d; do
        [ -z "$d" ] && continue
        echo "$DONE_DATES" | grep -qx "$d" || ACTIVE="${ACTIVE}${d}"$'\n'
    done <<< "$ACTIVE_RAW"
    ACTIVE=$(echo "$ACTIVE" | grep -v '^$' | tail -4)

    # Last 4 completed
    COMPLETED=$(grep " ✓$" "$LOG_FILE" 2>/dev/null | tail -4 | sed 's/.*\] //' | sed 's/ ✓//')

    clear
    echo "╔══════════════════════════════════════════════╗"
    echo "║    HYDRA Backtest — ThetaData Download       ║"
    echo "╠══════════════════════════════════════════════╣"
    printf "║  [%-40s] %3d%%  ║\n" "${BAR}${EMPTY}" "$PCT"
    echo "╠══════════════════════════════════════════════╣"
    printf "║  Done:     %4s / %4s  (%4s remaining)    ║\n" "$DONE" "$TOTAL" "$REMAINING"
    printf "║  Rate:     %-30s  ║\n" "${RATE}/min  (${SECS_PER_FILE}s per file)"
    printf "║  Elapsed:  %-4s  ETA: %-20s  ║\n" "${EL_MIN}m ${EL_SEC}s" "$ETA_STR"
    echo "╠══════════════════════════════════════════════╣"
    echo "║  Downloading now:                            ║"
    if [ -n "$ACTIVE" ]; then
        echo "$ACTIVE" | while IFS= read -r line; do
            printf "║    ⬇  %-38s  ║\n" "$line"
        done
    else
        printf "║    %-44s  ║\n" "(waiting for workers...)"
    fi
    echo "╠══════════════════════════════════════════════╣"
    echo "║  Last completed:                             ║"
    if [ -n "$COMPLETED" ]; then
        echo "$COMPLETED" | while IFS= read -r line; do
            printf "║    ✓  %-38s  ║\n" "$line"
        done
    else
        printf "║    %-44s  ║\n" "(none yet)"
    fi
    echo "╚══════════════════════════════════════════════╝"
    printf "  Updated: %s   (Ctrl+C to exit)\n" "$(date '+%H:%M:%S')"

    sleep 5
done
