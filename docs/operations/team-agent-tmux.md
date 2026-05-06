# Team Agent Tmux 병렬 실행기

## 개요

BarroAiTrade PDCA 단계마다 5 팀 에이전트(architect / developer / qa / reviewer / security) 를 tmux 세션 안의 5 pane 에서 **병렬로 동시 실행**한다. 각 pane 은 별도 `claude --print` CLI 를 띄워 독립 컨텍스트로 작동하며, 결과는 파일로 수집되어 CTO Lead 가 통합 검토.

## 디렉터리 구조

```
scripts/
  team_agent_tmux.sh      # 진입점 (tmux 세션 생성 + 5 pane dispatch)
  team_agent_watch.sh     # 5 pane 완료 대기 + COMBINED.md 생성
  team_agent_status.sh    # 1회 상태 출력 (loop 없음)

.claude/team-agent/
  templates/              # 5 역할 프롬프트 템플릿
    architect.md
    developer.md
    qa.md
    reviewer.md
    security.md
  sessions/               # BAR/단계별 산출물
    <BAR_ID>/
      <stage>/
        <role>.prompt.md  # 역할별 입력 (template + 컨텍스트)
        <role>.output.md  # claude CLI 출력
        <role>.status     # exit code (0 = OK)
        COMBINED.md       # watch 종합본
```

## 사용법

### 1. 5 pane 병렬 dispatch

```bash
# BAR-56 design 단계, 5 역할 모두
scripts/team_agent_tmux.sh BAR-56 design

# 자동 attach 없이 (CI/script 모드)
scripts/team_agent_tmux.sh BAR-56 do --no-attach

# 일부 역할만
scripts/team_agent_tmux.sh BAR-56 analyze --roles=qa,reviewer

# dry-run (tmux 안 띄움, 프롬프트 파일만 생성)
scripts/team_agent_tmux.sh BAR-56 design --dry-run
```

### 2. 진척 모니터링 / 결과 수집

```bash
# 1회 상태
scripts/team_agent_status.sh BAR-56 design

# 모든 pane 완료까지 대기 + COMBINED.md 생성
scripts/team_agent_watch.sh BAR-56 design
scripts/team_agent_watch.sh BAR-56 design --timeout=600
```

### 3. 세션 관리

```bash
# 접속
tmux attach -t team-BAR-56-design

# 종료
tmux kill-session -t team-BAR-56-design
```

## 워크플로 (CTO Lead 관점)

```
1. Plan PR 머지
2. /pdca next 또는 다음 단계 BAR 결정
3. scripts/team_agent_tmux.sh <BAR> design --no-attach
4. scripts/team_agent_watch.sh <BAR> design  (완료까지 대기)
5. .claude/team-agent/sessions/<BAR>/design/COMBINED.md 검토
6. CTO Lead 가 종합 → design 문서 commit & PR
7. 단계 진행 (do/analyze/report)
```

## 프롬프트 커스터마이징

기본 템플릿(`{{BAR_ID}}` / `{{STAGE}}` / `{{ROOT}}` placeholder) 외 추가 컨텍스트 주입은:

1. `team_agent_tmux.sh` 가 첫 실행 시 `<role>.prompt.md` 생성.
2. tmux dispatch 전에 prompt 파일을 직접 편집해 추가 지시 삽입 가능.
3. 두 번째 실행부터는 기존 prompt 재사용 (재생성 X).

새 prompt 강제 생성: `<role>.prompt.md` 삭제 후 재실행.

## 종료 코드

| 코드 | 의미 |
|:---:|------|
| 0 | 모두 성공 |
| 2 | 인자 오류 |
| 3 | template/디렉터리 누락 |
| 4 | tmux/claude CLI 미설치 |
| 5 | watch 타임아웃 |
| N | watch 가 발견한 에러 pane 수 |

## 주의 사항

- **자격증명**: prompt 파일에 API key / 비밀번호 직접 입력 금지. `.env` 또는 mcp 사용.
- **PR/머지**: 본 도구는 산출물 생성까지. 실 PR 생성·머지는 CTO Lead 또는 개별 Agent 호출이 책임.
- **회귀**: developer pane 이 코드 수정 시 다른 pane 와 race 가능 — 동일 파일 수정은 한 pane 으로 한정 권장.
- **로그 보존**: `.claude/team-agent/sessions/` 는 git 추적 X (개별 .gitignore). PR 산출물은 CTO Lead 가 `docs/` 로 옮긴 뒤 commit.

## 향후 확장

- `--inject=<file>` 옵션: 외부 컨텍스트 prompt 합성
- `team_agent_promote.sh`: COMBINED.md → docs/02-design 자동 promote
- 결과 webhook (Slack 등) — Phase 6 BAR-73 알림 IaC 와 연계
