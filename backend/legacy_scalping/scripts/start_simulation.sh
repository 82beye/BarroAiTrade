#!/bin/bash
# =============================================================================
# ai-trade 시뮬레이션 시작 스크립트
# nohup 백그라운드 실행, PID lock 기반 중복 방지
# =============================================================================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_LOCK="/tmp/ai-trade-simulation.pid"
LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/ai-trade-$(date +%Y-%m-%d).log"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

# 이미 실행 중인지 확인
if [ -f "$PID_LOCK" ]; then
    OLD_PID=$(cat "$PID_LOCK" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "이미 실행 중입니다 (PID: $OLD_PID)"
        echo "  PID lock: $PID_LOCK"
        echo "  중지: scripts/stop_simulation.sh"
        exit 0
    fi
    # stale lock file 제거
    rm -f "$PID_LOCK"
fi

# nohup 백그라운드 실행
cd "$PROJECT_DIR"
nohup python3 main.py --mode simulation >> "$LOG_FILE" 2>&1 &
BGPID=$!

# 프로세스 시작 확인 (1초 대기)
sleep 1
if kill -0 "$BGPID" 2>/dev/null; then
    echo "시뮬레이션 시작 (PID: $BGPID)"
    echo "  로그: $LOG_FILE"
    echo "  중지: scripts/stop_simulation.sh"
    echo "  상태: scripts/status.sh"
else
    echo "시작 실패 - 로그를 확인하세요:"
    tail -20 "$LOG_FILE"
    exit 1
fi
