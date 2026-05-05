#!/bin/bash
# =============================================================================
# ai-trade 시뮬레이션 중지 스크립트
# SIGTERM → 5초 대기 → SIGKILL
# =============================================================================

PID_LOCK="/tmp/ai-trade-simulation.pid"

if [ ! -f "$PID_LOCK" ]; then
    echo "실행 중인 프로세스 없음 (PID lock 없음)"
    exit 0
fi

PID=$(cat "$PID_LOCK" 2>/dev/null)
if [ -z "$PID" ]; then
    echo "PID lock 파일이 비어있음, 삭제합니다"
    rm -f "$PID_LOCK"
    exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
    echo "프로세스(PID: $PID) 이미 종료됨, lock 파일 정리"
    rm -f "$PID_LOCK"
    exit 0
fi

# SIGTERM (graceful 종료)
echo "SIGTERM 전송 (PID: $PID)..."
kill -TERM "$PID"

# 5초간 종료 대기
for i in $(seq 1 5); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "정상 종료됨 (${i}초)"
        rm -f "$PID_LOCK"
        exit 0
    fi
    sleep 1
done

# SIGKILL (강제 종료)
echo "5초 내 미종료 → SIGKILL 전송..."
kill -9 "$PID"
sleep 1

if ! kill -0 "$PID" 2>/dev/null; then
    echo "강제 종료됨"
else
    echo "종료 실패 - 수동 확인 필요 (PID: $PID)"
fi

rm -f "$PID_LOCK"
