#!/usr/bin/env bash
# BarroAiTrade 시스템 중지 스크립트

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GREEN="\033[0;32m"
NC="\033[0m"
info() { echo -e "${GREEN}[INFO]${NC} $*"; }

info "BarroAiTrade 중지 중..."
cd "$PROJECT_ROOT"

docker-compose down

info "시스템이 중지되었습니다."
