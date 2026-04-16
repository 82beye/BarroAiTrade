#!/usr/bin/env bash
# Stability monitoring script for 48-hour test
# Run this continuously to monitor system health

set -euo pipefail

PROJECT_ROOT="/Users/beye82/Workspace/BarroAiTrade"
LOG_DIR="$PROJECT_ROOT/logs"
MONITOR_LOG="$LOG_DIR/monitor.log"

# Ensure log file exists
mkdir -p "$LOG_DIR"
touch "$MONITOR_LOG"

INTERVAL=30  # Check every 30 seconds
MAX_MEMORY_MB=2048  # Alert if memory exceeds 2GB

echo "Monitoring started at $(date '+%Y-%m-%d %H:%M:%S')" >> "$MONITOR_LOG"

while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

    # Check if backend process is running
    if ! pgrep -f "uvicorn backend.main" > /dev/null; then
        echo "[$TIMESTAMP] ❌ CRITICAL: Backend process not running!" >> "$MONITOR_LOG"
        break
    fi

    # Get memory usage
    PID=$(pgrep -f "uvicorn backend.main" | head -1)
    MEMORY_KB=$(ps -p "$PID" -o rss= 2>/dev/null || echo "0")
    MEMORY_MB=$((MEMORY_KB / 1024))

    # Check for issues
    if [ "$MEMORY_MB" -gt "$MAX_MEMORY_MB" ]; then
        echo "[$TIMESTAMP] ⚠️  WARNING: High memory usage: ${MEMORY_MB}MB (PID: $PID)" >> "$MONITOR_LOG"
    else
        echo "[$TIMESTAMP] ✓ OK: Process running (PID: $PID, Memory: ${MEMORY_MB}MB)" >> "$MONITOR_LOG"
    fi

    # Check API availability
    if ! curl -s http://127.0.0.1:8000/ > /dev/null 2>&1; then
        echo "[$TIMESTAMP] ⚠️  WARNING: API not responding" >> "$MONITOR_LOG"
    fi

    sleep "$INTERVAL"
done

echo "Monitoring stopped at $(date '+%Y-%m-%d %H:%M:%S')" >> "$MONITOR_LOG"
