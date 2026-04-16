#!/usr/bin/env bash
# BarroAiTrade 로컬 실행 스크립트 (Docker 없이)
# launchd에서 직접 호출 — foreground로 실행하여 launchd가 프로세스 관리

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
LOG_DIR="$PROJECT_ROOT/logs"

mkdir -p "$LOG_DIR"

# 환경변수 로드
if [[ -f "$PROJECT_ROOT/.env.local" ]]; then
    set -a
    source "$PROJECT_ROOT/.env.local"
    set +a
fi

# venv 활성화
source "$VENV/bin/activate"

cd "$PROJECT_ROOT"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] BarroAiTrade 백엔드 시작 (mode=${TRADING_MODE:-simulation})" >> "$LOG_DIR/launchd.log"

# Backend (FastAPI) — foreground 실행 (launchd가 프로세스 수명 관리)
exec python -m uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info
