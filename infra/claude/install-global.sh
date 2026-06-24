#!/usr/bin/env bash
# [개발머신 셋업] 프로젝트 에이전트 정의를 전역(~/.claude/agents)에 설치.
#   controller 가 ~/.claude/agents/barrotrade-*.md(전역)를 참조 → 전역 등록용.
#   프로젝트 스킬(.claude/skills/barrotrade)·프로젝트 에이전트(.claude/agents)는 git pull 로 자동.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
GLOBAL="$HOME/.claude/agents"
mkdir -p "$GLOBAL"
cp "$REPO/.claude/agents/"*.md "$GLOBAL/"
echo "전역 설치 완료: $GLOBAL ($(ls "$GLOBAL"/*.md | wc -l | tr -d ' ') agents)"
echo "프로젝트 스킬은 .claude/skills/barrotrade (git pull 시 자동 적용)."
