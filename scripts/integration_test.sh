#!/usr/bin/env bash
# BarroAiTrade E2E 통합 테스트
# 실행: ./scripts/integration_test.sh
# 전제: docker-compose 서비스가 기동 중이어야 함

set -euo pipefail

BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"

GREEN="\033[0;32m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

PASS=0
FAIL=0

pass() { echo -e "${GREEN}[PASS]${NC} $*"; ((PASS++)); }
fail() { echo -e "${RED}[FAIL]${NC} $*"; ((FAIL++)); }
info() { echo -e "${YELLOW}[INFO]${NC} $*"; }

# ── 백엔드 테스트 ──────────────────────────────────────────────────────────

info "=== 백엔드 API 테스트 ==="

# 헬스체크
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/api/status" || echo "000")
if [[ "$STATUS" == "200" ]]; then
    pass "GET /api/status → $STATUS"
else
    fail "GET /api/status → $STATUS (200 기대)"
fi

# API 문서
DOCS=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/docs" || echo "000")
if [[ "$DOCS" == "200" ]]; then
    pass "GET /docs → $DOCS"
else
    fail "GET /docs → $DOCS"
fi

# 시스템 상태 응답 구조 확인
BODY=$(curl -s "$BACKEND_URL/api/status" 2>/dev/null || echo "{}")
if echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'state' in d and 'mode' in d" 2>/dev/null; then
    pass "시스템 상태 응답 구조 정상 (state, mode 포함)"
else
    fail "시스템 상태 응답 구조 이상: $BODY"
fi

# ── 프론트엔드 테스트 ──────────────────────────────────────────────────────

info ""
info "=== 프론트엔드 테스트 ==="

FE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$FRONTEND_URL" || echo "000")
if [[ "$FE_STATUS" == "200" ]]; then
    pass "GET / → $FE_STATUS"
else
    fail "GET / → $FE_STATUS (200 기대)"
fi

# ── WebSocket 테스트 ──────────────────────────────────────────────────────

info ""
info "=== WebSocket 테스트 ==="

if command -v wscat &>/dev/null; then
    WS_RESULT=$(timeout 3 wscat -c "ws://localhost:8000/ws/realtime" --execute '{"type":"ping"}' 2>&1 || true)
    if echo "$WS_RESULT" | grep -q "pong"; then
        pass "WebSocket ping/pong 정상"
    else
        fail "WebSocket ping/pong 실패"
    fi
else
    info "wscat 미설치 — WebSocket 테스트 스킵 (npm install -g wscat)"
fi

# ── 결과 요약 ──────────────────────────────────────────────────────────────

echo ""
echo "=============================="
echo "  결과: ${PASS} 통과 / ${FAIL} 실패"
echo "=============================="

[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
