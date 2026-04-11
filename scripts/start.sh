#!/usr/bin/env bash
# BarroAiTrade 시스템 시작 스크립트
# 사용: ./scripts/start.sh [simulation|live]

set -euo pipefail

MODE="${1:-simulation}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 색상 출력
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# .env.local 확인
if [[ ! -f "$PROJECT_ROOT/.env.local" ]]; then
    warn ".env.local 파일이 없습니다. .env.example을 복사하여 설정하세요."
    warn "  cp .env.example .env.local"
fi

# 라이브 모드 경고
if [[ "$MODE" == "live" ]]; then
    warn "============================================"
    warn " 실거래 모드로 시작합니다!"
    warn " 실제 자금이 사용됩니다."
    warn "============================================"
    read -rp "계속하려면 'yes'를 입력하세요: " confirm
    [[ "$confirm" == "yes" ]] || error "취소되었습니다."
fi

info "BarroAiTrade 시작 중... (모드: $MODE)"
cd "$PROJECT_ROOT"

export TRADING_MODE="$MODE"

# Docker 이미지 빌드 및 실행
info "Docker Compose 실행..."
docker-compose up -d --build

info "서비스 상태 확인..."
sleep 5
docker-compose ps

info ""
info "시스템이 시작되었습니다:"
info "  - 대시보드:  http://localhost:3000"
info "  - API:       http://localhost:8000"
info "  - Grafana:   http://localhost:3001  (admin/barro1234)"
info "  - Prometheus: http://localhost:9090"
info ""
info "로그 보기: ./scripts/logs.sh"
info "중지하기:  ./scripts/stop.sh"
