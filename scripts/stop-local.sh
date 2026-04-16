#!/usr/bin/env bash
# BarroAiTrade 로컬 종료 스크립트
# launchd 서비스를 unload하여 KeepAlive 프로세스 종료

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"

PLIST="$HOME/Library/LaunchAgents/com.barroaitrade.backend.plist"

if launchctl list | grep -q com.barroaitrade.backend; then
    launchctl unload "$PLIST"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] BarroAiTrade 백엔드 종료 (launchctl unload)" >> "$LOG_DIR/launchd.log"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 서비스 미실행 상태" >> "$LOG_DIR/launchd.log"
fi
