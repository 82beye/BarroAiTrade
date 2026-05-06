#!/usr/bin/env bash
# scripts/team_agent_status.sh
#
# 한 번만 상태 출력 (loop 없음).
# Usage: scripts/team_agent_status.sh <BAR_ID> <stage>
set -euo pipefail

BAR_ID="${1:?BAR_ID required}"
STAGE="${2:?stage required}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="$ROOT/.claude/team-agent/sessions/$BAR_ID/$STAGE"

if [[ ! -d "$WORK_DIR" ]]; then
  echo "no work_dir: $WORK_DIR" >&2; exit 3
fi

shopt -s nullglob
prompts=("$WORK_DIR"/*.prompt.md)
shopt -u nullglob

if [[ ${#prompts[@]} -eq 0 ]]; then
  echo "no prompts in $WORK_DIR" >&2; exit 3
fi

printf "%-12s  %-8s  %-8s  %s\n" "ROLE" "STATUS" "EXIT" "OUTPUT_BYTES"
printf "%-12s  %-8s  %-8s  %s\n" "----" "------" "----" "------------"
for prompt in "${prompts[@]}"; do
  role="$(basename "$prompt" .prompt.md)"
  status_file="$WORK_DIR/${role}.status"
  output_file="$WORK_DIR/${role}.output.md"

  if [[ -f "$status_file" ]]; then
    rc="$(cat "$status_file")"
    state="DONE"
  else
    rc="-"
    state="RUNNING"
  fi

  if [[ -f "$output_file" ]]; then
    bytes="$(wc -c < "$output_file" | tr -d ' ')"
  else
    bytes="0"
  fi

  printf "%-12s  %-8s  %-8s  %s\n" "$role" "$state" "$rc" "$bytes"
done
