#!/usr/bin/env bash
# scripts/team_agent_watch.sh
#
# 5 pane 의 status 파일이 모두 생성되면 결과를 COMBINED.md 로 집약.
# Usage: scripts/team_agent_watch.sh <BAR_ID> <stage> [--timeout=1200]
set -euo pipefail

BAR_ID="${1:?BAR_ID required}"
STAGE="${2:?stage required}"
shift 2

TIMEOUT=1200
INTERVAL=5
while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout=*) TIMEOUT="${1#*=}"; shift ;;
    --interval=*) INTERVAL="${1#*=}"; shift ;;
    *) echo "unknown: $1" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="$ROOT/.claude/team-agent/sessions/$BAR_ID/$STAGE"

if [[ ! -d "$WORK_DIR" ]]; then
  echo "[watch] no work_dir: $WORK_DIR" >&2; exit 3
fi

ROLES=()
shopt -s nullglob
for prompt in "$WORK_DIR"/*.prompt.md; do
  role="$(basename "$prompt" .prompt.md)"
  ROLES+=("$role")
done
shopt -u nullglob

if [[ ${#ROLES[@]} -eq 0 ]]; then
  echo "[watch] no prompts in $WORK_DIR" >&2; exit 3
fi

echo "[watch] roles: ${ROLES[*]}"

start=$(date +%s)
while :; do
  done=0; errors=0
  for role in "${ROLES[@]}"; do
    if [[ -f "$WORK_DIR/${role}.status" ]]; then
      done=$((done + 1))
      rc="$(cat "$WORK_DIR/${role}.status" 2>/dev/null || echo "?")"
      [[ "$rc" != "0" ]] && errors=$((errors + 1))
    fi
  done
  ts=$(date +%H:%M:%S)
  echo "[$ts] $done/${#ROLES[@]} complete (errors=$errors)"
  if [[ $done -eq ${#ROLES[@]} ]]; then break; fi

  elapsed=$(( $(date +%s) - start ))
  if [[ $elapsed -gt $TIMEOUT ]]; then
    echo "[watch] timeout ${TIMEOUT}s elapsed" >&2
    exit 5
  fi
  sleep "$INTERVAL"
done

# 종합 산출
combined="$WORK_DIR/COMBINED.md"
{
  echo "# Team Agent Combined Output"
  echo
  echo "- BAR: $BAR_ID"
  echo "- Stage: $STAGE"
  echo "- Generated: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "- Roles: ${ROLES[*]}"
  echo
  for role in "${ROLES[@]}"; do
    rc="$(cat "$WORK_DIR/${role}.status" 2>/dev/null || echo "?")"
    echo
    echo "---"
    echo
    echo "## $role (exit=$rc)"
    echo
    if [[ -f "$WORK_DIR/${role}.output.md" ]]; then
      cat "$WORK_DIR/${role}.output.md"
    else
      echo "_(no output)_"
    fi
  done
} > "$combined"

echo "[watch] combined: $combined"
echo "[watch] errors: $errors"
exit "$errors"
