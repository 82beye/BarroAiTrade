#!/usr/bin/env bash
# BarroAiTrade /monitor 대시보드 수동 종료 스크립트
# start-dashboard.sh 로 띄운 Next.js 프론트엔드(:3000) + ngrok 터널 종료.
# 사용: ./scripts/stop-dashboard.sh

set -euo pipefail

PORT=3000
GREEN="\033[0;32m"; YELLOW="\033[1;33m"; NC="\033[0m"
info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

# ── ngrok 종료 ──────────────────────────────────────────────────────
if pgrep -f "ngrok http" >/dev/null 2>&1; then
    pkill -f "ngrok http" && info "ngrok 종료"
else
    warn "ngrok 미실행"
fi

# ── 프론트엔드(:PORT) 종료 ──────────────────────────────────────────
FE_PIDS=$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [[ -n "$FE_PIDS" ]]; then
    # next dev 는 부모(npm/next) + 자식 워커 구조 — 부모 트리까지 정리
    for pid in $FE_PIDS; do
        kill "$pid" 2>/dev/null || true
    done
    sleep 1
    pkill -f "next dev" 2>/dev/null || true
    info "프론트엔드(:$PORT) 종료"
else
    warn "프론트엔드(:$PORT) 미실행"
fi

info "대시보드 종료 완료"
