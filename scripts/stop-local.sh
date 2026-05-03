#!/usr/bin/env bash
# BarroAiTrade 로컬 종료 스크립트
# launchd 서비스를 stop하여 프로세스 종료 (plist는 유지)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"

PLIST="$HOME/Library/LaunchAgents/com.barroaitrade.backend.plist"

if launchctl list | grep -q com.barroaitrade.backend; then
    launchctl stop com.barroaitrade.backend
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] BarroAiTrade 백엔드 종료 (launchctl stop)" >> "$LOG_DIR/launchd.log"
else
    # plist가 로드되지 않은 경우 로드 후 유지
    launchctl load "$PLIST"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 서비스 미실행 — plist 로드 완료 (다음 실행 대기)" >> "$LOG_DIR/launchd.log"
fi
