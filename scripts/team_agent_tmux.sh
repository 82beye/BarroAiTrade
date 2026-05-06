#!/usr/bin/env bash
# scripts/team_agent_tmux.sh
#
# CTO Lead orchestration — 5 팀 에이전트(architect/developer/qa/reviewer/security)
# 를 tmux 세션 안의 5 pane 에서 병렬로 동시 실행.
#
# Usage:
#   scripts/team_agent_tmux.sh <BAR_ID> <stage> [--dry-run] [--no-attach] [--roles=a,b,c]
#
# Stages: plan | design | do | analyze | report
#
# 결과:
#   .claude/team-agent/sessions/<BAR_ID>/<stage>/<role>.prompt.md  (입력)
#   .claude/team-agent/sessions/<BAR_ID>/<stage>/<role>.output.md  (출력)
#   .claude/team-agent/sessions/<BAR_ID>/<stage>/<role>.status     (exit code)
#   .claude/team-agent/sessions/<BAR_ID>/<stage>/COMBINED.md       (watch 종합)
#
# 주의:
#   - claude CLI 가 PATH 에 있어야 함 (Claude Code 설치 환경)
#   - 본 worktree 디렉터리 안에서 실행
#   - 기존 prompt 파일이 있으면 재사용 (수정 후 재실행 가능)
set -euo pipefail

# ─────────────── 인자 파싱 ───────────────
BAR_ID=""
STAGE=""
DRY_RUN=0
NO_ATTACH=0
ROLES_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-attach) NO_ATTACH=1; shift ;;
    --roles=*) ROLES_OVERRIDE="${1#*=}"; shift ;;
    -h|--help)
      sed -n '1,30p' "$0"; exit 0 ;;
    -*)
      echo "[team-agent] unknown option: $1" >&2; exit 2 ;;
    *)
      if [[ -z "$BAR_ID" ]]; then
        BAR_ID="$1"
      elif [[ -z "$STAGE" ]]; then
        STAGE="$1"
      else
        echo "[team-agent] unexpected arg: $1" >&2; exit 2
      fi
      shift ;;
  esac
done

if [[ -z "$BAR_ID" || -z "$STAGE" ]]; then
  echo "Usage: $0 <BAR_ID> <stage> [--dry-run] [--no-attach] [--roles=...]" >&2
  exit 2
fi

case "$STAGE" in
  plan|design|do|analyze|report) ;;
  *) echo "[team-agent] invalid stage: $STAGE (plan|design|do|analyze|report)" >&2; exit 2 ;;
esac

# ─────────────── 역할 셋 ───────────────
DEFAULT_ROLES=(architect developer qa reviewer security)
if [[ -n "$ROLES_OVERRIDE" ]]; then
  IFS=',' read -r -a ROLES <<< "$ROLES_OVERRIDE"
else
  ROLES=("${DEFAULT_ROLES[@]}")
fi

# ─────────────── 경로 ───────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SESSION="team-${BAR_ID}-${STAGE}"
WORK_DIR="$ROOT/.claude/team-agent/sessions/$BAR_ID/$STAGE"
TPL_DIR="$ROOT/.claude/team-agent/templates"

mkdir -p "$WORK_DIR"

# ─────────────── 프롬프트 준비 ───────────────
for role in "${ROLES[@]}"; do
  tpl="$TPL_DIR/$role.md"
  prompt_file="$WORK_DIR/${role}.prompt.md"

  if [[ ! -f "$tpl" ]]; then
    echo "[team-agent] missing template: $tpl" >&2
    exit 3
  fi

  if [[ ! -f "$prompt_file" ]]; then
    sed -e "s/{{BAR_ID}}/$BAR_ID/g" \
        -e "s/{{STAGE}}/$STAGE/g" \
        -e "s|{{ROOT}}|$ROOT|g" \
        "$tpl" > "$prompt_file"
  fi
done

# ─────────────── dry-run ───────────────
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] session=$SESSION"
  echo "[dry-run] work_dir=$WORK_DIR"
  echo "[dry-run] roles=${ROLES[*]}"
  for role in "${ROLES[@]}"; do
    echo "[dry-run] role=$role prompt=$WORK_DIR/${role}.prompt.md"
  done
  exit 0
fi

# ─────────────── 사전 검사 ───────────────
if ! command -v tmux >/dev/null 2>&1; then
  echo "[team-agent] tmux not found in PATH" >&2; exit 4
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "[team-agent] claude CLI not found in PATH" >&2; exit 4
fi

# ─────────────── tmux 세션 생성 ───────────────
tmux kill-session -t "$SESSION" 2>/dev/null || true
# detached 세션은 default-terminal-size 제한 — 충분히 크게 강제
tmux new-session -d -s "$SESSION" -n team -c "$ROOT" -x 320 -y 80

# 5 pane 분할 — tiled 레이아웃 매 분할마다 재적용 (no space 회피)
NUM=${#ROLES[@]}
for ((i=1; i<NUM; i++)); do
  tmux split-window -t "$SESSION:team" -c "$ROOT"
  tmux select-layout -t "$SESSION:team" tiled >/dev/null
done
tmux select-layout -t "$SESSION:team" tiled >/dev/null

# 각 pane 에 역할 디스패치
for i in "${!ROLES[@]}"; do
  role="${ROLES[$i]}"
  prompt_file="$WORK_DIR/${role}.prompt.md"
  output_file="$WORK_DIR/${role}.output.md"
  status_file="$WORK_DIR/${role}.status"
  pane="$SESSION:team.$i"

  # 각 pane 에 안내 + claude CLI 실행
  tmux send-keys -t "$pane" \
    "echo '=== team-agent: $role ($BAR_ID/$STAGE) ==='" Enter
  tmux send-keys -t "$pane" \
    "echo 'prompt: $prompt_file'" Enter
  tmux send-keys -t "$pane" \
    "echo 'output: $output_file'" Enter
  tmux send-keys -t "$pane" \
    "echo '--- claude --print 시작 ($(date +%H:%M:%S)) ---'" Enter
  tmux send-keys -t "$pane" \
    "{ claude --print --output-format text < '$prompt_file' > '$output_file' 2>&1; rc=\$?; echo \$rc > '$status_file'; echo \"=== done rc=\$rc ===\"; }" Enter
done

echo "[team-agent] session=$SESSION pane=$NUM started"
echo "[team-agent] work_dir=$WORK_DIR"
echo "[team-agent] roles: ${ROLES[*]}"
echo "[team-agent] outputs: $WORK_DIR/<role>.output.md"
echo
echo "다음 명령:"
echo "  - 모니터링:  scripts/team_agent_watch.sh $BAR_ID $STAGE"
echo "  - tmux 접속: tmux attach -t $SESSION"
echo "  - 종료:     tmux kill-session -t $SESSION"

if [[ "$NO_ATTACH" -eq 0 && -t 0 && -t 1 ]]; then
  exec tmux attach -t "$SESSION"
fi
