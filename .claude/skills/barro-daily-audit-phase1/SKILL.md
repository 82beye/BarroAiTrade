---
name: barro-daily-audit-phase1
description: BarroAiTrade BAR-OPS-09 Phase 1 - Daily 운영 audit 자동화 도구 3종(scripts/_daily_evening_pipeline.py, _strategy_perf_track.py, _loss_drill_down.py) + pytest 14건 + 보고서를 worktree .claude/worktrees/strange-jackson-3c740a에 일괄 적용하고 검증·커밋·푸시한다. PRD 명세된 6개 파일(scripts 3종 + backend/tests/test_daily_pipeline.py + docs/04-report/features/2026-05-22-daily-pipeline.md + .gitignore 4줄 추가)을 그대로 생성. 사용자가 "BAR-OPS-09 Phase 1 적용", "Phase 1 푸시", "daily 운영 audit 자동화", "daily-pipeline 스킬", "kt00009 ledger 도구", "전략 ledger 만들기", "손실 drill-down", "Daily audit Phase 1", "0001-feat-BAR-OPS-09-Daily-audit-3-Phase-1.patch 적용", "BAR-OPS-09 worktree에 패치 적용" 같은 표현을 쓸 때 즉시 트리거. PRD 문서를 통째로 첨부하면서 "스킬로 실행", "스킬로 진행", "이 PRD 적용해줘" 라고 해도 이 스킬을 호출.
---

# BarroAiTrade BAR-OPS-09 Phase 1 적용 스킬

PRD `Daily 운영 audit 자동화 도구 (Phase 1)` 에 정의된 6개 파일을 BAR-OPS-09 worktree 에 일괄 적용하고 검증·커밋·푸시한다. 원격 세션에서 신규 14건 + 회귀 154건 통과까지 끝낸 코드가 `references/files/` 에 그대로 들어 있으므로, 본 스킬의 역할은 그 본문을 정확히 옮기고 검증한 뒤 사용자 승인을 받아 커밋·푸시하는 것이다.

## 트리거 조건

다음 중 하나에 해당하면 즉시 이 스킬로 진행한다.

- "BAR-OPS-09 Phase 1 적용해줘", "Phase 1 푸시해줘", "Daily 운영 audit 자동화 적용"
- "0001-feat-BAR-OPS-09-Daily-audit-3-Phase-1.patch" 라는 패치 파일을 가리키며 적용 요청
- PRD 본문(§0~§10) 전체를 첨부하면서 "이거 스킬로 진행" 또는 "스킬 실행"
- "scripts/_daily_evening_pipeline.py", "_strategy_perf_track.py", "_loss_drill_down.py" 중 하나 이상을 만들어 달라는 요청 + BAR-OPS-09 컨텍스트

## 적용할 6개 파일

| references/files/ | worktree 대상 경로 | 종류 |
|---|---|---|
| `01-daily-evening-pipeline.py` | `scripts/_daily_evening_pipeline.py` | 신규 |
| `02-strategy-perf-track.py` | `scripts/_strategy_perf_track.py` | 신규 |
| `03-loss-drill-down.py` | `scripts/_loss_drill_down.py` | 신규 |
| `04-test-daily-pipeline.py` | `backend/tests/test_daily_pipeline.py` | 신규 |
| `05-report.md` | `docs/04-report/features/2026-05-22-daily-pipeline.md` | 신규 |
| `06-gitignore.diff` | `.gitignore` | 수정 (4줄 추가) |

## 작업 흐름 (5 단계)

### Step 1 — Worktree 검증

작업 루트는 `worktree`. 사용자가 `BarroAiTrade` 메인 디렉터리에 있더라도 모든 git/pytest/적용 명령은 worktree에서 실행한다.

```bash
WORKTREE=/Users/beye/workspace/BarroAiTrade/.claude/worktrees/strange-jackson-3c740a
cd "$WORKTREE"
git status
git branch --show-current   # → BAR-OPS-09
git log --oneline -5
```

확인 사항:
- 현재 브랜치가 `BAR-OPS-09`
- 6개 대상 파일이 **존재하지 않음** (이미 적용된 상태면 사용자에게 보고 후 중단)
- modified/untracked 가 PRD 와 무관한 잔재(`docs/.bkit-memory.json`, `docs/.pdca-status.json`, `docs/.pdca-snapshots/`, `analysis/imports/*` 등)면 그대로 두고 진행 — 커밋에서 제외함
- 충돌하는 변경(scripts/_daily_evening_pipeline.py 가 이미 다른 내용으로 존재 등)이 있으면 사용자에게 확인 후 중단

### Step 2 — 6개 파일 적용

`references/files/` 의 각 파일을 `Read` 로 읽어 worktree 의 대응 경로에 `Write` 로 그대로 작성한다. 6번(`.gitignore`)만 `Edit` 로 추가 라인 삽입.

병렬 처리 가능 — 6개 파일 사이에 의존성 없음.

- `01-daily-evening-pipeline.py` → `$WORKTREE/scripts/_daily_evening_pipeline.py` (Write)
- `02-strategy-perf-track.py` → `$WORKTREE/scripts/_strategy_perf_track.py` (Write)
- `03-loss-drill-down.py` → `$WORKTREE/scripts/_loss_drill_down.py` (Write)
- `04-test-daily-pipeline.py` → `$WORKTREE/backend/tests/test_daily_pipeline.py` (Write)
- `05-report.md` → `$WORKTREE/docs/04-report/features/2026-05-22-daily-pipeline.md` (Write)
- `06-gitignore.diff` → 가이드대로 `$WORKTREE/.gitignore` 에 4줄 삽입 (Edit)

### Step 3 — 검증

```bash
cd "$WORKTREE"

# Phase 1 단위·스모크 테스트 — 목표: 14 passed
pytest backend/tests/test_daily_pipeline.py -q

# 회귀 — 목표: 영향 없음 (원격 검증: 154 passed / 5 skipped)
pytest backend/tests/strategy/ backend/tests/risk/ -q
```

테스트 실패 시 `references/pitfalls.md` 참조 — 가장 흔한 원인 4가지(`markdown_report` 전이 import, float/Decimal 혼용, legacy import 경로, `scripts/__init__.py` 부재) 점검.

### Step 4 — 커밋 (사용자 승인 필수)

PRD §10 의 커밋 메시지를 사용하고, **6개 대상 파일만** stage. 잔재 파일은 절대 함께 커밋하지 않는다.

```bash
cd "$WORKTREE"
git add scripts/_daily_evening_pipeline.py \
        scripts/_strategy_perf_track.py \
        scripts/_loss_drill_down.py \
        backend/tests/test_daily_pipeline.py \
        docs/04-report/features/2026-05-22-daily-pipeline.md \
        .gitignore

git commit -m "$(cat <<'EOF'
feat(BAR-OPS-09): Daily 운영 audit 자동화 도구 3종 (Phase 1)

매일 저녁 운영 결과를 audit 하고 손실 종목을 진단해 전략 파라미터를 다음
영업일에 반영하는 일별 사이클의 1단계. kt00009 정확 체결가 기반 종목별
net 계산과 손실 원인 진단을 자동화한다.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

커밋 직전 stage 내용을 사용자에게 보여주고 승인 받는다. `--no-verify` 같은 우회 옵션은 사용하지 않는다 — 사용자가 명시적으로 요청하지 않은 한.

### Step 5 — 푸시 (사용자 승인 필수)

```bash
cd "$WORKTREE"
git push origin BAR-OPS-09
```

푸시 결과(remote sha, 변경 통계)를 사용자에게 보고.

## 핵심 사실 (반드시 숙지)

세부는 `references/code-facts.md`. 요약:

- `kt00009` 체결 조회 = `backend/legacy_scalping/execution/kiwoom_api.py` 의 `KiwoomRestAPI.get_order_executions` (`backend.legacy_scalping...` 가 아닌 `sys.path` 추가 후 `from execution.kiwoom_api import KiwoomRestAPI`)
- 전략 귀속 1순위 = `backend/core/journal/active_positions.py` 의 `ActivePositionStore(path).load_all()` → `ActivePosition.strategy`
- `IntradaySimulator` exit plan = `backend/core/backtester/intraday_simulator.py` 의 `_exit_plan_for_strategy` / `_scaled_exit_plan` / `_sfzone_atr_exit_plan`
- `data/` 는 `.gitignore` 됨 (런타임 파일)
- `scripts/__init__.py` 가 존재해야 `from scripts._daily_evening_pipeline import ...` 가능 — worktree 에 이미 존재 확인 완료

## 함정 (반드시 회피)

세부는 `references/pitfalls.md`. 요약:

1. **`markdown_report` import 금지** — `backend/core/journal/markdown_report.py` 는 `kiwoom_native_rank` → `httpx` 를 전이 import. `_fmt_signed` 같은 헬퍼는 `_strategy_perf_track.py` 안에 직접 인라인 (이미 `02-strategy-perf-track.py` 가 그렇게 작성됨).
2. **float/Decimal 혼용 금지** — `OHLCV.high/low/close/open` 은 **float**, net·entry_price 는 **Decimal**. 캔들 값 사용 전 반드시 `Decimal(str(c.high))` 변환.
3. **legacy KiwoomRestAPI 경로** — `backend.legacy_scalping...` 패키지 import 가 아님. `sys.path.insert(0, str(backend/legacy_scalping))` 후 `from execution.kiwoom_api import KiwoomRestAPI` (이미 `01-daily-evening-pipeline.py` 의 `fetch_executions_live` 가 그렇게 작성됨).
4. **`scripts/__init__.py` 필수** — pytest 가 `from scripts._daily_evening_pipeline import` 함. worktree 에 이미 존재.

## 검증 / 수용 기준

세부는 `references/verify.md`. 요약:

1. `pytest backend/tests/test_daily_pipeline.py -q` → **14 passed**
2. `pytest backend/tests/strategy/ backend/tests/risk/ -q` → 영향 없음 (Phase 1 은 기존 코드 미변경)
3. 커밋에 무관한 잔재(`docs/.bkit-memory.json` 등) 포함 안 됨
4. 라이브 `kt00009` 호출은 키움 자격증명·네트워크 필요 — M4 에서 별도 확인 (스킬 범위 외)

## 적용 후 — Phase 2~7 후속

매일 저녁 zip 전달 → 도구 실행 → 손실 drill-down → 전략 fix → 시뮬 검증 → commit 반복. 자세한 로드맵은 `references/roadmap.md`. Phase 2 부터는 `references/code-facts.md` 의 전략별 파라미터 위치(이미 검증된 경로) 를 참조해 별도 PRD 로 진행.

## 사용자 보고 패턴

각 단계 종료 시 짧게 1줄로 보고:

```
[Step 1] worktree 검증 완료 — BAR-OPS-09 브랜치, 적용 대상 6개 모두 부재
[Step 2] 6개 파일 적용 완료 (scripts/ 3, backend/tests/ 1, docs/ 1, .gitignore 수정)
[Step 3] pytest 14 passed / 회귀 ___ passed, ___ skipped
[Step 4] 커밋 진행할까요? — stage 내역: <6개 파일>
[Step 5] 푸시 진행할까요? — origin/BAR-OPS-09
```

커밋·푸시는 사용자 승인 후 진행 (Skill 자동 진행 금지).
