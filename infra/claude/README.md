# BarroAiTrade — 에이전트 모드 / 스킬 (개발머신 셋업)

운영머신과 동일한 멀티에이전트 환경을 **개발머신**에서 사용하기 위한 셋업.
정의는 git 으로 전달(.gitignore negation 으로 `.claude/agents`·`.claude/skills` 트래킹),
런타임 상태(jobs/sessions/worktrees 등)는 계속 ignore.

## 1) 프로젝트 에이전트·스킬 — git 자동
`git pull` 시 다음이 그대로 따라옴:
- `.claude/agents/` — 12 에이전트 (barrotrade-* 11 + daytrading-quant)
- `.claude/skills/barrotrade/` — SKILL.md + config 8
→ 프로젝트에서 바로 `/barrotrade <mode>` 스킬·서브에이전트 사용 가능.

## 2) 전역 설치(선택) — ~/.claude/agents
controller 가 전역 경로(`~/.claude/agents/barrotrade-*.md`)를 참조하므로 전역 등록하려면:
```
bash infra/claude/install-global.sh
```

## 안전
전 에이전트 ★실거래 송출 금지·mock(mockapi)·HITL★. compliance.json 이 order endpoint 차단.

## 현재 갭
barrotrade `cycle` 전체엔 17 에이전트 필요(현재 11). 누락: data-preprocessor·
fundamental-specialist·rag-analyst 등 → `analyze`/`debate`/`quick`/`reflect` 모드 우선.
