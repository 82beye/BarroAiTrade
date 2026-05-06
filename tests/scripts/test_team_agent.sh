#!/usr/bin/env bash
# tests/scripts/test_team_agent.sh
#
# Bash 단위 검증 — team_agent_*.sh 의 dry-run / 인자 / 산출물 경로.
# Run: bash tests/scripts/test_team_agent.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$SCRIPT_DIR"

PASS=0
FAIL=0
FAILED_NAMES=()

assert() {
  local name="$1"; shift
  if "$@"; then
    PASS=$((PASS+1))
    echo "  ✓ $name"
  else
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("$name")
    echo "  ✗ $name" >&2
  fi
}

# 1. 구문 체크
echo "## 1. Syntax"
assert "team_agent.sh syntax"           bash -n scripts/team_agent.sh
assert "team_agent_tmux.sh syntax"      bash -n scripts/team_agent_tmux.sh
assert "team_agent_watch.sh syntax"     bash -n scripts/team_agent_watch.sh
assert "team_agent_status.sh syntax"    bash -n scripts/team_agent_status.sh

# 2. 인자 검증
echo "## 2. Arg validation"
assert "missing BAR_ID exits non-zero"  bash -c 'scripts/team_agent_tmux.sh 2>/dev/null; [[ $? -ne 0 ]]'
assert "invalid stage rejected"         bash -c 'scripts/team_agent_tmux.sh BAR-X invalid_stage --dry-run 2>/dev/null; [[ $? -eq 2 ]]'

# 3. dry-run 산출물
echo "## 3. Dry-run"
TEST_BAR="BAR-TEST-9999"
rm -rf ".claude/team-agent/sessions/$TEST_BAR"
scripts/team_agent_tmux.sh "$TEST_BAR" design --dry-run >/dev/null

WORK="$SCRIPT_DIR/.claude/team-agent/sessions/$TEST_BAR/design"
for role in architect developer qa reviewer security; do
  assert "dry-run created $role.prompt.md"  test -f "$WORK/${role}.prompt.md"
  assert "$role prompt has BAR_ID"          grep -q "BAR-TEST-9999" "$WORK/${role}.prompt.md"
done

# 4. status (no panes yet)
echo "## 4. Status (no panes)"
assert "status output contains role"    bash -c 'scripts/team_agent_status.sh "'"$TEST_BAR"'" design 2>&1 | grep -q architect'
assert "status shows RUNNING"           bash -c 'scripts/team_agent_status.sh "'"$TEST_BAR"'" design 2>&1 | grep -q RUNNING'

# 5. wrapper
echo "## 5. Wrapper"
assert "team_agent.sh help works"       bash -c 'scripts/team_agent.sh help 2>&1 | grep -q "Team Agent"'
assert "team_agent.sh ls works"         bash -c 'scripts/team_agent.sh ls 2>&1 | grep -q "Active"'

# 6. 역할 override
echo "## 6. Roles override"
TEST_BAR2="BAR-TEST-7777"
rm -rf ".claude/team-agent/sessions/$TEST_BAR2"
scripts/team_agent_tmux.sh "$TEST_BAR2" design --dry-run --roles=qa,reviewer >/dev/null
WORK2="$SCRIPT_DIR/.claude/team-agent/sessions/$TEST_BAR2/design"
assert "roles override creates qa"          test -f "$WORK2/qa.prompt.md"
assert "roles override creates reviewer"    test -f "$WORK2/reviewer.prompt.md"
assert "roles override skips architect"     bash -c '! test -f "'"$WORK2/architect.prompt.md"'"'

# 7. clean
echo "## 7. Clean"
scripts/team_agent.sh clean "$TEST_BAR" design >/dev/null
scripts/team_agent.sh clean "$TEST_BAR2" design >/dev/null
assert "clean removes work_dir"             bash -c '! test -d "'"$WORK"'"'

echo
echo "─────────────────────────────────"
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [[ $FAIL -gt 0 ]]; then
  echo "Failed:"
  printf "  - %s\n" "${FAILED_NAMES[@]}"
  exit 1
fi
