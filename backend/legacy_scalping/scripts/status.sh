#!/bin/bash
# =============================================================================
# ai-trade 시뮬레이션 상태 확인
# =============================================================================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_LOCK="/tmp/ai-trade-simulation.pid"
LOG_DIR="${PROJECT_DIR}/logs"
TODAY_LOG="${LOG_DIR}/ai-trade-$(date +%Y-%m-%d).log"

echo "=== ai-trade 시뮬레이션 상태 ==="
echo ""

# 실행 상태 확인
if [ -f "$PID_LOCK" ]; then
    PID=$(cat "$PID_LOCK" 2>/dev/null)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        # 실행 시간 계산
        ELAPSED=$(ps -o etime= -p "$PID" 2>/dev/null | xargs)
        echo "상태: 실행 중"
        echo "PID:  $PID"
        echo "실행: $ELAPSED"
        echo ""
    else
        echo "상태: 중지됨 (stale PID lock)"
        echo ""
    fi
else
    echo "상태: 중지됨"
    echo ""
fi

# 오늘 로그
if [ -f "$TODAY_LOG" ]; then
    SIZE=$(du -h "$TODAY_LOG" | cut -f1)
    echo "--- 오늘 로그 ($SIZE) ---"
    tail -10 "$TODAY_LOG"
    echo ""
else
    echo "오늘 로그 없음"
fi
