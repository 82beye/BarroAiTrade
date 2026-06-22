#!/usr/bin/env bash
# setup_hermes_ops.sh — 운영 머신 Hermes Agent + advisory 파이프라인 셋업 (멱등·비파괴).
#
# 역할 분리(설계):
#   · 결정적 critical 파이프라인(advisory writer: verdict+market-context+LLM 오버레이)
#     → launchd 로 안정 가동(Hermes 버전 비의존).
#   · Hermes Agent → LLM 프로바이더 + 멀티채널 게이트웨이(Telegram) + 대화형 ops 에이전트
#     + 영속 메모리(~/.hermes). 운영자가 "오늘 시장 어때?" 같은 질의를 채팅으로.
#
# 안전: curl|bash 설치·launchctl load 는 자동 실행하지 않는다. 기본은 점검 + 명령 안내.
#   실제 수행은 명시 플래그(--install-hermes / --install-launchd)로 opt-in.
#   라이브 매매 게이트는 건드리지 않음(전부 default-OFF, 활성은 policy.json HITL).
#
# 사용:
#   bash scripts/setup_hermes_ops.sh                 # 점검 + 안내(아무것도 설치 안 함)
#   bash scripts/setup_hermes_ops.sh --install-hermes --install-launchd --install-skill
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-./venv/bin/python}"; [ -x "$PY" ] || PY="python3"
HERMES_INSTALL_URL="https://hermes-agent.nousresearch.com/install.sh"
PLIST_SRC="infra/com.barro.advisory-writer.plist.example"
SKILL_SRC="infra/hermes/skills/barro-market-brief"
ENV_SRC="infra/hermes/ops.env.example"

DO_HERMES=0; DO_LAUNCHD=0; DO_SKILL=0
for a in "$@"; do case "$a" in
  --install-hermes)  DO_HERMES=1 ;;
  --install-launchd) DO_LAUNCHD=1 ;;
  --install-skill)   DO_SKILL=1 ;;
  -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "알 수 없는 인자: $a (--help)"; exit 2 ;;
esac; done

say(){ printf '%s\n' "$*"; }
hr(){ printf '%s\n' "────────────────────────────────────────────────────────"; }

hr; say "▶ BarroAiTrade × Hermes 운영 셋업   ROOT=$ROOT  PY=$PY"; hr

# ── 1) 사전 점검 ──────────────────────────────────────────────────────────────
say "[1] 사전 점검"
command -v git >/dev/null  && say "  ✓ git" || { say "  ✗ git 필요"; exit 1; }
[ -x ./venv/bin/python ]   && say "  ✓ venv (./venv)" || say "  ⚠ venv 없음 → python3 사용. (운영은 venv 권장)"
command -v claude >/dev/null && say "  ✓ claude CLI (writer LLM 백엔드)" || say "  ⚠ claude CLI 없음 → writer 는 --backend mock 만 / market-llm 비활성"
[ -n "${ANTHROPIC_API_KEY:-}" ] && say "  ✓ ANTHROPIC_API_KEY" || say "  ⚠ ANTHROPIC_API_KEY 미설정(LLM 경로 필요 시)"
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then say "  ✓ Telegram env"; else say "  ⓘ Telegram env 미설정 → 표시 비활성(채팅 게이트웨이도 후속 설정 필요)"; fi

# ── 2) advisory 파이프라인 부트스트랩(결정적, 라이브 무영향) ──────────────────
say ""; say "[2] advisory 파이프라인 부트스트랩 (디렉터리/의존성/theme_map 점검 + mock 스모크)"
if [ -x scripts/setup_agent_advisory.sh ]; then
  PYTHON="$PY" bash scripts/setup_agent_advisory.sh | sed 's/^/    /'
else
  say "  ⚠ scripts/setup_agent_advisory.sh 없음 — 구버전? git pull 확인"
fi

# ── 3) Hermes Agent ──────────────────────────────────────────────────────────
say ""; say "[3] Hermes Agent"
if command -v hermes >/dev/null 2>&1; then
  say "  ✓ hermes 설치됨 → 진단:"; hermes doctor 2>&1 | sed 's/^/    /' || say "    (hermes doctor 실패 — 버전 확인)"
else
  if [ "$DO_HERMES" = 1 ]; then
    say "  ▶ Hermes 설치(opt-in): curl -fsSL $HERMES_INSTALL_URL | bash"
    curl -fsSL "$HERMES_INSTALL_URL" | bash || { say "  ✗ 설치 실패 — 수동 설치 후 재실행"; }
    say "  → 셸 재로딩 필요: source ~/.zshrc (또는 ~/.bashrc)"
  else
    say "  ⓘ 미설치. 설치하려면: curl -fsSL $HERMES_INSTALL_URL | bash  (또는 본 스크립트에 --install-hermes)"
    say "    설치 후: source ~/.zshrc → hermes setup (LLM 프로바이더) → hermes gateway setup/start (Telegram)"
  fi
fi

# ── 4) Hermes 스킬(시장 브리핑) 설치 ─────────────────────────────────────────
say ""; say "[4] Hermes 스킬: barro-market-brief (advisory.json → 시장 브리핑 대화)"
HSKILL_DIR="$HOME/.hermes/skills/barro-market-brief"
if [ -d "$SKILL_SRC" ]; then
  if [ "$DO_SKILL" = 1 ]; then
    mkdir -p "$HOME/.hermes/skills"
    cp -R "$SKILL_SRC" "$HOME/.hermes/skills/" && say "  ✓ 설치: $HSKILL_DIR"
    say "    (Hermes 가 ~/.hermes/skills/ 를 로드. 스킬 본문은 운영 advisory.json 경로를 참조)"
  else
    say "  ⓘ 미설치. 설치하려면 --install-skill (→ $HSKILL_DIR 로 복사)"
  fi
else
  say "  ⚠ $SKILL_SRC 없음 — git pull 확인"
fi

# ── 5) launchd: advisory writer 상시 가동 ────────────────────────────────────
say ""; say "[5] launchd: advisory writer (verdict+market-context 생산 + Telegram 표시)"
PLIST_DST="$HOME/Library/LaunchAgents/com.barro.advisory-writer.plist"
if [ "$DO_LAUNCHD" = 1 ]; then
  if [ -f "$PLIST_SRC" ]; then
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s#/Users/USERNAME/workspace/BarroAiTrade#$ROOT#g; s#USERNAME#$(whoami)#g" "$PLIST_SRC" > "$PLIST_DST"
    say "  ✓ plist 생성: $PLIST_DST  (경로 자동 치환)"
    say "    LLM 시장국면까지 켜려면 plist ProgramArguments 에 <string>--market-llm</string> 추가."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load -w "$PLIST_DST" && say "  ✓ launchctl load 완료 (writer 가동)" || say "  ✗ load 실패 — 경로/권한 확인"
  else
    say "  ⚠ $PLIST_SRC 없음"
  fi
else
  say "  ⓘ 미설치. 설치하려면 --install-launchd. 수동: cp $PLIST_SRC ~/Library/LaunchAgents/... → 경로 수정 → launchctl load -w ..."
  say "    또는 포그라운드 확인: make advisory-writer  (LLM 시장국면: make advisory-writer ARGS=--market-llm)"
fi

# ── 6) 요약 ──────────────────────────────────────────────────────────────────
hr; say "✓ 셋업 점검 완료 — 라이브 매매 게이트는 전부 default-OFF(무영향)."
say "  다음: 운영 프로세스·HITL 활성 절차 → docs/operations/hermes-ops-runbook.md"; hr
