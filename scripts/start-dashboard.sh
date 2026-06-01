#!/usr/bin/env bash
# BarroAiTrade /monitor 대시보드 수동 기동 스크립트
# 구성: Next.js 프론트엔드(:3000) + ngrok 터널(고정 도메인)
# launchd 미등록 영역 — 재부팅/장애 후 이 스크립트로 수동 기동.
# 사용: ./scripts/start-dashboard.sh
#
# 백엔드(:8000)·텔레그램 봇은 launchd가 관리(RunAtLoad=재부팅 시 자동, 월~금 08:20 재기동):
#   수동 기동:  launchctl kickstart gui/$(id -u)/com.barroaitrade.backend

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
LOG_DIR="$PROJECT_ROOT/logs"
NGROK_DOMAIN="myspace-wagon-elephant.ngrok-free.dev"
PORT=3000

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

# ── 2) ngrok 터널 (고정 도메인 → :PORT) ─────────────────────────────
if pgrep -f "ngrok http" >/dev/null 2>&1; then
    info "ngrok 이미 실행 중 — 건너뜀"
else
    command -v ngrok >/dev/null 2>&1 || error "ngrok 미설치 (/usr/local/bin/ngrok 등)"
    info "ngrok 터널 기동 ($NGROK_DOMAIN → :$PORT)..."
    nohup ngrok http --url="https://$NGROK_DOMAIN" "$PORT" </dev/null >>"$LOG_DIR/ngrok.log" 2>&1 &
    disown
    # 에이전트 API(:4040)로 터널 등록 대기 (최대 ~15s)
    for _ in $(seq 1 15); do
        curl -s -m 3 http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -q public_url && break
        sleep 1
    done
fi

# ── 3) 검증 ─────────────────────────────────────────────────────────
sleep 1
LCODE=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/monitor" 2>/dev/null || echo "000")
PCODE=$(curl -s -m 15 -H "ngrok-skip-browser-warning: 1" -o /dev/null -w "%{http_code}" \
        "https://$NGROK_DOMAIN/monitor" 2>/dev/null || echo "000")

echo
info "로컬   /monitor  → HTTP $LCODE"
info "공개   /monitor  → HTTP $PCODE   (https://$NGROK_DOMAIN/monitor)"
if [[ "$PCODE" == "200" ]]; then
    info "✅ 대시보드 가동 완료"
else
    warn "공개 URL이 200이 아님 — logs/frontend.log, logs/ngrok.log 확인"
fi
