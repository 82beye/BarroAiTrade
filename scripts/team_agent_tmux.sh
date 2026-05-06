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

# pane runner 스크립트 — WORK_DIR 만 PWD 로 추론, ROLE 만 인자로 (명령 짧게 → 터미널 wrap 회피)
RUNNER="$WORK_DIR/_runner.sh"
cat > "$RUNNER" <<'RUNNER_EOF'
#!/usr/bin/env bash
# team-agent pane runner — single role
# 호출: ROLE=<role> bash _runner.sh
# WORK_DIR=PWD, BAR_ID/STAGE 는 path 에서 추론
set -uo pipefail
ROLE="${ROLE:?ROLE env required}"
WORK_DIR="$(pwd)"
STAGE="$(basename "$WORK_DIR")"
BAR_ID="$(basename "$(dirname "$WORK_DIR")")"

prompt="$WORK_DIR/${ROLE}.prompt.md"
output="$WORK_DIR/${ROLE}.output.md"
status="$WORK_DIR/${ROLE}.status"

echo "=== team-agent: $ROLE ($BAR_ID/$STAGE) ==="
echo "prompt: $prompt"
echo "--- claude --print 시작 ($(date '+%H:%M:%S')) ---"
cat "$prompt" | claude --print --output-format text > "$output" 2>&1
rc=$?
echo "$rc" > "$status"
echo "=== done rc=$rc ==="
RUNNER_EOF
chmod +x "$RUNNER"

# 각 pane 에 cd + ROLE=... bash _runner.sh 두 줄로 분리 (line wrap 회피)
for i in "${!ROLES[@]}"; do
  role="${ROLES[$i]}"
  pane="$SESSION:team.$i"
  tmux send-keys -t "$pane" "cd '$WORK_DIR'" Enter
  tmux send-keys -t "$pane" "ROLE=$role bash _runner.sh" Enter
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
