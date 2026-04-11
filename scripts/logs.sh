#!/usr/bin/env bash
# BarroAiTrade 로그 확인 스크립트
# 사용: ./scripts/logs.sh [backend|frontend|all]

set -euo pipefail

SERVICE="${1:-all}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_ROOT"

case "$SERVICE" in
    backend)
        docker-compose logs -f backend
        ;;
    frontend)
        docker-compose logs -f frontend
        ;;
    all|*)
        docker-compose logs -f backend frontend
        ;;
esac
