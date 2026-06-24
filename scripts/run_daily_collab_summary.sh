#!/usr/bin/env bash
# run_daily_collab_summary.sh — 일일 협업 요약 생성 cron/launchd 래퍼
#
# 당일 에이전트 협업 결정(data/agent_room) + 코드/설정 수정(git·policy.json)을
# docs/operations/daily-collab/<date>.md 로 집계한다. read-only — 거래/주문 없음.
#
# cron 예 (월-금 16:20 KST):
#   20 16 * * 1-5 cd /Users/USERNAME/workspace/BarroAiTrade && \
#     bash scripts/run_daily_collab_summary.sh >> logs/daily_collab_summary.log 2>&1
#
# 인자는 _daily_collab_summary.py 로 전달(예: --date 2026-06-24). 없으면 오늘(KST).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 9
ROOT="$(pwd)"

# .env.local 로딩(있으면) — BARRO_DATA_DIR 등 운영 환경변수
if [ -f ./.env.local ]; then
  set -a; . ./.env.local; set +a
fi

# 중복 실행 방지(파일 PID 잠금)
LOCK="/tmp/barro-daily-collab.lock"
if [ -f "$LOCK" ]; then
  PID="$(cat "$LOCK" 2>/dev/null || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    echo "[collab-summary] 이미 실행 중 PID=$PID — skip"; exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# venv fallback chain (.venv → venv → python3)
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="./venv/bin/python"; [ -x "$PY" ] || PY="python3"

echo "[collab-summary] $(date '+%F %T %Z') start (root=$ROOT, py=$PY)"
"$PY" scripts/_daily_collab_summary.py "$@"
rc=$?
echo "[collab-summary] $(date '+%F %T %Z') done rc=$rc"
exit $rc
