#!/bin/bash
# bot_status.sh - Quick status overview of all Calypso trading bots
#
# Usage: ./scripts/bot_status.sh
#
# Shows:
#   - Service status (running/stopped)
#   - Memory usage
#   - Last log entry from each bot

echo ""
echo "=========================================="
echo "  CALYPSO BOT STATUS - $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "=========================================="
echo ""

# Define bots
BOTS=("delta_neutral" "iron_fly_0dte" "rolling_put_diagonal")
BOT_NAMES=("Delta Neutral" "Iron Fly 0DTE" "Rolling Put Diagonal")

# Check each bot
for i in "${!BOTS[@]}"; do
    BOT="${BOTS[$i]}"
    NAME="${BOT_NAMES[$i]}"

    # Get service status
    STATUS=$(systemctl is-active "$BOT" 2>/dev/null)

    if [ "$STATUS" = "active" ]; then
        STATUS_ICON="✅"
        STATUS_TEXT="RUNNING"

        # Get memory usage
        PID=$(systemctl show "$BOT" --property=MainPID --value 2>/dev/null)
        if [ -n "$PID" ] && [ "$PID" != "0" ]; then
            MEM=$(ps -o rss= -p "$PID" 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')
        else
            MEM="N/A"
        fi

        # Get last log line
        LAST_LOG=$(journalctl -u "$BOT" -n 1 --no-pager -o cat 2>/dev/null | tail -1)
        if [ -z "$LAST_LOG" ]; then
            LAST_LOG="No recent logs"
        fi
    else
        STATUS_ICON="❌"
        STATUS_TEXT="STOPPED"
        MEM="N/A"
        LAST_LOG="Service not running"
    fi

    printf "%-20s %s %-8s | Mem: %-10s\n" "$NAME" "$STATUS_ICON" "$STATUS_TEXT" "$MEM"
    printf "  └─ %s\n" "${LAST_LOG:0:80}"
    echo ""
done

# Show monitor log tail if exists
if [ -f "/opt/calypso/logs/monitor.log" ]; then
    echo "------------------------------------------"
    echo "  RECENT MONITOR LOG (last 5 entries)"
    echo "------------------------------------------"
    tail -5 /opt/calypso/logs/monitor.log 2>/dev/null || echo "  (empty)"
fi

echo ""
echo "=========================================="
echo "  Commands:"
echo "    Live monitor:  tail -f /opt/calypso/logs/monitor.log"
echo "    Bot logs:      journalctl -u <bot_name> -f"
echo "    Restart bot:   sudo systemctl restart <bot_name>"
echo "=========================================="
echo ""
