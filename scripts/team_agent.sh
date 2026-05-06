#!/usr/bin/env bash
# scripts/team_agent.sh
#
# Team Agent 통합 wrapper — start / status / watch / kill / clean.
# Usage:
#   scripts/team_agent.sh start <BAR_ID> <stage> [extra opts...]
#   scripts/team_agent.sh status <BAR_ID> <stage>
#   scripts/team_agent.sh watch <BAR_ID> <stage> [--timeout=N]
#   scripts/team_agent.sh kill <BAR_ID> <stage>
#   scripts/team_agent.sh clean <BAR_ID> <stage>
#   scripts/team_agent.sh ls
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cmd="${1:-help}"
shift || true

case "$cmd" in
  start)
    exec "$SCRIPT_DIR/team_agent_tmux.sh" "$@"
    ;;
  status)
    exec "$SCRIPT_DIR/team_agent_status.sh" "$@"
    ;;
  watch)
    exec "$SCRIPT_DIR/team_agent_watch.sh" "$@"
    ;;
  kill)
    BAR_ID="${1:?BAR_ID required}"
    STAGE="${2:?stage required}"
    SESSION="team-${BAR_ID}-${STAGE}"
    tmux kill-session -t "$SESSION" 2>&1 || echo "[team-agent] no session: $SESSION"
    ;;
  clean)
    BAR_ID="${1:?BAR_ID required}"
    STAGE="${2:?stage required}"
    SESSION="team-${BAR_ID}-${STAGE}"
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    rm -rf "$ROOT/.claude/team-agent/sessions/$BAR_ID/$STAGE"
    echo "[team-agent] cleaned $BAR_ID/$STAGE"
    ;;
  ls)
    echo "## Active tmux team sessions"
    tmux list-sessions 2>/dev/null | grep '^team-' || echo "(none)"
    echo
    echo "## Saved session work_dirs"
    ls -1d "$ROOT"/.claude/team-agent/sessions/*/* 2>/dev/null || echo "(none)"
    ;;
  help|*)
    cat <<'EOF'
team_agent.sh — BarroAiTrade Team Agent 병렬 실행 wrapper

명령:
  start  <BAR_ID> <stage> [opts]   tmux 5 pane dispatch (기본: 자동 attach)
  status <BAR_ID> <stage>          1회 상태 출력
  watch  <BAR_ID> <stage>          5 pane 완료 대기 + COMBINED.md
  kill   <BAR_ID> <stage>          tmux 세션 종료
  clean  <BAR_ID> <stage>          세션 + 산출물 모두 삭제
  ls                               활성 세션 + 저장 산출물 목록
  help                             이 도움말

추가 옵션 (start 단계로 전달):
  --dry-run     tmux 안 띄우고 prompt 파일만 생성
  --no-attach   세션 생성하되 attach 안 함 (CI/스크립트)
  --roles=a,b   일부 역할만

stage: plan | design | do | analyze | report
roles 기본: architect, developer, qa, reviewer, security
EOF
    ;;
esac
