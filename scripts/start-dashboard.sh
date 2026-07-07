#!/usr/bin/env bash
# BarroAiTrade /monitor 대시보드 수동 기동 스크립트
# 구성: Next.js 프론트엔드(:3000) + Cloudflare Tunnel(trycloudflare)
# launchd 미등록 영역 — 재부팅/장애 후 이 스크립트로 수동 기동.
# 사용: ./scripts/start-dashboard.sh
#
# 백엔드(:8000)·텔레그램 봇은 launchd가 관리(RunAtLoad=재부팅 시 자동, 월~금 08:20 재기동):
#   수동 기동:  launchctl kickstart gui/$(id -u)/com.barroaitrade.backend

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
LOG_DIR="$PROJECT_ROOT/logs"
PORT=3000
CLOUDFLARED_LOG="$LOG_DIR/cloudflared.log"

GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"

# ── 1) 프론트엔드 (Next.js dev, :PORT) ──────────────────────────────
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    info "프론트엔드 이미 실행 중 (:$PORT) — 건너뜀"
else
    [[ -x "$FRONTEND_DIR/node_modules/.bin/next" ]] || error "next 미설치 — 'cd frontend && npm install' 필요"
    info "프론트엔드 기동 (next dev, :$PORT)..."
    ( cd "$FRONTEND_DIR" && nohup ./node_modules/.bin/next dev </dev/null >>"$LOG_DIR/frontend.log" 2>&1 & disown ) || true
    # :PORT LISTEN 대기 (최대 ~40s)
    for _ in $(seq 1 20); do
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
        sleep 2
    done
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 \
        && info "프론트엔드 LISTEN 확인 (:$PORT)" \
        || warn "프론트엔드가 아직 :$PORT 바인딩 안 됨 — logs/frontend.log 확인"
fi

# ── 2) Cloudflare Tunnel (trycloudflare → :PORT) ────────────────────
if pgrep -f "cloudflared tunnel --url http://localhost:$PORT" >/dev/null 2>&1; then
    info "Cloudflare Tunnel 이미 실행 중 — 건너뜀"
else
    command -v cloudflared >/dev/null 2>&1 || error "cloudflared 미설치 (/usr/local/bin/cloudflared 등)"
    info "Cloudflare Tunnel 기동 (trycloudflare → :$PORT)..."
    nohup cloudflared tunnel --url "http://localhost:$PORT" </dev/null >>"$CLOUDFLARED_LOG" 2>&1 &
    disown
    # quick tunnel URL 로그 출력 대기 (최대 ~30s)
    for _ in $(seq 1 30); do
        grep -qE "https://[-a-z0-9]+\\.trycloudflare\\.com" "$CLOUDFLARED_LOG" 2>/dev/null && break
        sleep 1
    done
fi

# ── 3) 검증 ─────────────────────────────────────────────────────────
sleep 1
LCODE=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/monitor" 2>/dev/null || echo "000")
PUBLIC_URL=$(grep -Eo "https://[-a-z0-9]+\\.trycloudflare\\.com" "$CLOUDFLARED_LOG" 2>/dev/null | tail -1 || true)
if [[ -n "$PUBLIC_URL" ]]; then
    PCODE=$(curl -s -m 15 -o /dev/null -w "%{http_code}" "$PUBLIC_URL/monitor" 2>/dev/null || echo "000")
else
    PCODE="000"
fi

echo
info "로컬   /monitor  → HTTP $LCODE"
info "공개   /monitor  → HTTP $PCODE   (${PUBLIC_URL:-Cloudflare URL 미확인}/monitor)"
if [[ "$PCODE" == "200" ]]; then
    info "✅ 대시보드 가동 완료"
else
    warn "공개 URL이 200이 아님 — logs/frontend.log, logs/cloudflared.log 확인"
fi
