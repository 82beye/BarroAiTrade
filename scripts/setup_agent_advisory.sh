#!/usr/bin/env bash
# setup_agent_advisory.sh — 운영 머신 1회 부트스트랩 (git pull 직후 실행).
#
# 멱등·비파괴: 런타임 디렉터리 생성 + 의존성/env 점검 + mock 스모크 + 활성화 절차 안내.
# 자동 설치/주문 변경 절대 없음. Hermes 설치·launchd 등록·게이트 활성은 "명령만 출력"(HITL).
#
# 사용: bash scripts/setup_agent_advisory.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-./venv/bin/python}"; [ -x "$PY" ] || PY="python3"

echo "▶ BarroAiTrade 에이전트 자문(advisory) 부트스트랩"
echo "  ROOT=$ROOT  PY=$PY"

# 1) 런타임 디렉터리 (writer/capture 도 auto-mkdir 하지만 선생성)
mkdir -p data logs/decisions workspace/_intraday
echo "✓ 디렉터리: data/ · logs/decisions/ · workspace/_intraday/"

# 2) 의존성 점검
if $PY -c "import backend.core.risk.agent_advisory" 2>/dev/null; then
  echo "✓ advisory 모듈 import OK (소비자 리더 = 코드 반영됨, default-OFF)"
else
  echo "✗ advisory 모듈 import 실패 — git pull / PYTHONPATH 확인"; exit 1
fi
if command -v claude >/dev/null 2>&1; then
  echo "✓ claude CLI 존재 → --backend claude-cli 가능"
else
  echo "⚠ claude CLI 없음 → --backend mock 만 가능(claude-cli 쓰려면 Claude Code 설치)"
fi
if command -v hermes >/dev/null 2>&1; then
  echo "✓ hermes 존재(선택 런타임)"
else
  echo "ⓘ hermes 미설치(선택) — https://hermes-agent.org. 없으면 cron/launchd 로 writer 스케줄."
fi

# 3) env 점검(경고만 — 비밀값은 커밋 금지)
[ -n "${ANTHROPIC_API_KEY:-}" ] && echo "✓ ANTHROPIC_API_KEY set" || echo "⚠ ANTHROPIC_API_KEY 미설정(claude-cli 에 필요할 수 있음)"
if [ -n "${TELEGRAM_BOT_TOKEN:-}${BARRO_TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "✓ Telegram env 일부 set"
else
  echo "ⓘ Telegram env 미설정 — --telegram 실시간 표시 비활성"
fi

# 4) mock 스모크 (라이브 무영향)
echo "▶ writer mock 스모크(1회):"
$PY scripts/agent_advisory_writer.py --once --backend mock || true

cat <<'NEXT'

────────────────────────────────────────────────────────
다음 단계 (활성은 전부 HITL — 기본은 라이브 무영향):
  [Phase1 표시 ] writer 루프 가동(데몬 무영향, advisory.json·텔레그램만):
       make advisory-writer            # claude-cli 백엔드, 표시 전용
     또는 launchd: infra/com.barro.advisory-writer.plist.example
       (User/경로 수정 → launchctl load -w ~/Library/LaunchAgents/...)
  [Phase2 shadow] 데몬 리더는 이미 코드에 있음(default-OFF) — advisory.json 만 쌓이며 측정.
  [Phase3 게이트] data/policy.json 에  "agent_advisory_enabled": true  설정 (★HITL).
       롤백: 해당 키 false → 즉시 무력화(데몬 byte-identical).
────────────────────────────────────────────────────────
NEXT
echo "✓ 부트스트랩 완료 — 라이브 매매 무영향 상태(게이트 OFF)."
