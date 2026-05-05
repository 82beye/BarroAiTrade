---
tags: [design, feature/bar-40, status/in_progress, phase/0, area/repo]
template: design
version: 1.0
---

# BAR-40 sub_repo(ai-trade) 모노레포 흡수 Design Document

> **관련 문서**: [[../../01-plan/features/bar-40-monorepo-absorption.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v1|Master Plan v1]]
>
> **Summary**: ai-trade 16K줄 자산을 `backend/legacy_scalping/` 으로 동작 변경 없이 흡수하기 위한 상세 설계 — 흡수 절차·dry-run 차단·import 격리·requirements 정책·검증 시나리오
>
> **Project**: BarroAiTrade
> **Feature**: BAR-40
> **Phase**: 0 (기반 정비)
> **Author**: beye
> **Date**: 2026-05-06
> **Status**: Draft
> **Planning Doc**: [bar-40-monorepo-absorption.plan.md](../../01-plan/features/bar-40-monorepo-absorption.plan.md)

---

## 1. Overview

### 1.1 Design Goals

- ai-trade 의 *코드 변경 없이* (단순 미러) 동작을 보존
- main repo 의 `backend.*` 트리와 **import 충돌 0건** 보장
- `python -m backend.legacy_scalping.main --dry-run` 무에러 30초 이내 종료
- OHLCV 캐시 144MB 가 git 에 들어오지 않도록 사전 차단
- 후속 BAR-41/42/43 가 의존할 수 있는 *안정된 모듈 진입점* 제공

### 1.2 Design Principles

- **Zero-modification mirror**: legacy 코드는 *읽기 전용*. 패치는 어댑터(BAR-41) 또는 환경변수로만 처리
- **Namespace-first**: 모든 import 는 `backend.legacy_scalping.<sub>` 절대 경로 강제
- **Side-effect-free import**: 모듈 import 시 외부 호출(API, 주문, Telegram, 파일 IO) 발생 금지
- **YAGNI on requirements**: 단일 requirements 머지를 먼저 시도, 충돌 시 분리

---

## 2. Architecture

### 2.1 Before / After 디렉터리 구조

```
Before (분리 운용)
─────────────────────────────────────────
/Users/beye/workspace/
├── BarroAiTrade/                           ← 메인 레포
│   └── backend/
│       ├── core/{strategy,gateway,...}/
│       └── models/, api/, db/
└── ai-trade/                               ← 별도 레포 (별도 venv·cron)
    ├── main.py                             (2,412 LOC)
    ├── scanner/                            (8 files, 2,344 LOC)
    ├── strategy/
    │   ├── scalping_team/                  (9 agents)
    │   ├── strategy_team/                  (6 agents)
    │   └── verification_team/              (956 LOC)
    ├── execution/
    │   └── kiwoom_api.py                   (1,569 LOC)
    ├── monitoring/                         (3,772 LOC)
    └── data/ohlcv_cache/                   (144 MB, 2,967 종목)


After (흡수 후)
─────────────────────────────────────────
BarroAiTrade/
└── backend/
    ├── core/                               ← 표준 시스템 (변경 없음)
    │   ├── strategy/, gateway/, scanner/, ...
    ├── models/, api/, db/                  ← 변경 없음
    └── legacy_scalping/                    ← 신규 (ai-trade 미러)
        ├── __init__.py
        ├── main.py                         (--dry-run flag 추가)
        ├── scanner/, strategy/, execution/, monitoring/
        ├── seoheefather_strategy.py
        ├── data/ohlcv_cache/               ← .gitignore (커밋 안 함)
        └── requirements-legacy.txt         ← 충돌 시 분리 (조건부)
```

### 2.2 Import 흐름 (격리 모델)

```
[main repo 코드]
  └─ from backend.core.strategy import Strategy        ✅ 표준 경로
  └─ from backend.legacy_scalping.scanner import ...   ✅ 명시적 legacy 진입

[legacy_scalping 내부]
  └─ from backend.legacy_scalping.scanner import ...   ✅ 절대 경로
  └─ from .scanner import ...                          ❌ 금지 (상대 import 비허용)
  └─ from scanner import ...                           ❌ 금지 (sys.path 오염)
```

**금지 규칙**: legacy 측에서 `from backend.core.*` 또는 `from backend.models.*` 를 직접 import 해서는 안 된다 — 이는 어댑터(BAR-41)의 책임. 본 티켓에서는 *완전 격리*.

### 2.3 dry-run 차단 메커니즘

dry-run 시 외부 호출(키움 API/OAuth/주문/Telegram/Notion)이 절대 발생해서는 안 된다. 다음 두 층의 가드를 둔다:

```
Layer 1: 진입점 가드 (main.py top-of-module)
─────────────────────────────────────────
import os
if os.environ.get("DRY_RUN") == "1":
    print("[BAR-40] DRY_RUN: import-only mode — skipping main()")
    sys.exit(0)

Layer 2: 외부 호출 함수 가드 (BAR-40 단계에선 추가하지 않음)
─────────────────────────────────────────
※ Layer 2 는 본 티켓 범위 밖. main 진입점에서 즉시 sys.exit(0)
  하므로 Layer 1 만으로 충분. 더 세밀한 차단은 BAR-41 어댑터에서.
```

**핵심 결정**: `--dry-run` CLI flag 가 아닌 **환경변수 `DRY_RUN=1`** 사용.
- 이유 1: ai-trade 의 argparse 진입점을 *수정하지 않기* 위해 (zero-modification 원칙)
- 이유 2: Makefile / Docker / CI 에서 환경변수가 더 일관됨
- 이유 3: 향후 import-time 가드 확장이 쉬움

CLI 호출 형식:
```
DRY_RUN=1 python -m backend.legacy_scalping.main
```

---

## 3. Implementation Spec

### 3.1 흡수 명령 (rsync)

```bash
rsync -av \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='data/ohlcv_cache/' \
  --exclude='*.log' \
  --exclude='.DS_Store' \
  /Users/beye/workspace/ai-trade/ \
  /Users/beye/workspace/BarroAiTrade/backend/legacy_scalping/
```

**사전 점검**:
```bash
du -sh /Users/beye/workspace/ai-trade/data/ohlcv_cache/   # 144MB 예상
find /Users/beye/workspace/ai-trade -name "*.py" | wc -l  # 파일 수
```

### 3.2 `__init__.py` 보강 정책

**규칙**: 각 디렉터리에 빈 `__init__.py`. **명시적 re-export 는 BAR-41 까지 보류** (지금 단계에서는 namespace 만 확보).

```bash
find backend/legacy_scalping -type d ! -path '*/data/*' \
  -exec sh -c '[ ! -f "$1/__init__.py" ] && touch "$1/__init__.py"' _ {} \;
```

### 3.3 `main.py` 진입점 가드 (단일 패치)

ai-trade 의 `main.py` 최상단에 **단 한 줄짜리** 패치:

```python
# Patch BAR-40: dry-run early exit (BarroAiTrade integration)
import os, sys
if os.environ.get("DRY_RUN") == "1":
    sys.exit(0)
# --- 이하 기존 ai-trade main.py 본문은 변경 없음 ---
```

**대안 검토**: `main.py` 를 *전혀* 수정하지 않고 `backend/legacy_scalping/__main__.py` 를 별도로 두는 방식.

| 옵션 | 장점 | 단점 | 채택 |
|---|---|---|---|
| A. main.py 최상단 패치 (5줄) | dry-run 보장 단순, `python -m backend.legacy_scalping.main` 동작 | zero-modification 원칙 *부분* 위반 | ⭐ 채택 |
| B. `__main__.py` 별도 신규 | main.py 무수정 | dry-run 차단을 wrapper 가 담당 → 누락 위험 | 보류 |

→ **A 채택**. zero-modification 은 "동작 의미 변화 없음" 으로 재정의 (외부 호출 시 import-only 종료는 *흡수 단계의 안전장치*이지 동작 변화가 아님).

### 3.4 Makefile 타겟

```makefile
.PHONY: legacy-scalping
legacy-scalping: ## BAR-40 dry-run smoke test
	@echo "[BAR-40] Running legacy_scalping dry-run..."
	@DRY_RUN=1 python -m backend.legacy_scalping.main
	@echo "[BAR-40] dry-run OK"
```

기존 `Makefile` 부재 시 신규 생성. 존재 시 타겟만 추가.

### 3.5 `.gitignore` 갱신

`backend/legacy_scalping/` 에 한정한 패턴을 root `.gitignore` 에 추가:

```gitignore
# BAR-40: legacy_scalping (ai-trade 흡수)
backend/legacy_scalping/data/ohlcv_cache/
backend/legacy_scalping/.venv/
backend/legacy_scalping/**/__pycache__/
backend/legacy_scalping/**/*.pyc
backend/legacy_scalping/**/*.log
```

`git status` 로 144MB 가 untracked 인지 확인 → 만약 들어왔다면 `git rm --cached -r` 후 재커밋.

### 3.6 requirements 정책

**1차 시도** (단일 머지):

```bash
# main repo 의 requirements.txt 와 ai-trade 의 requirements.txt 비교
diff <(sort BarroAiTrade/backend/requirements.txt) \
     <(sort ai-trade/requirements.txt)
```

**충돌 판정 기준**:
- 동일 패키지 다른 메이저 버전 (e.g., `pandas==2.x` vs `pandas==1.x`)
- 동일 패키지 동일 메이저, 다른 마이너 → 더 높은 버전 채택
- 한쪽에만 있는 패키지 → 추가

**2차 시도** (충돌 발견 시):

```
backend/legacy_scalping/requirements-legacy.txt   ← ai-trade 만의 의존성
backend/requirements.txt                            ← main repo 표준
```

`Dockerfile.backend` 에서 두 파일 모두 설치:
```dockerfile
RUN pip install -r backend/requirements.txt
RUN pip install -r backend/legacy_scalping/requirements-legacy.txt
```

### 3.7 import 격리 검증 스크립트

```bash
# tests/legacy_scalping/test_smoke_import.py (BAR-41 에서 정식 테스트화)
# 본 티켓에서는 임시 검증 명령으로 수행
python -c "
import backend.legacy_scalping
import backend.legacy_scalping.scanner
import backend.legacy_scalping.strategy
import backend.legacy_scalping.execution
import backend.legacy_scalping.monitoring
print('all legacy modules importable')
"
```

만약 ImportError 발생 시 절대 경로 미수정 모듈을 색출:
```bash
grep -rn "^from \(scanner\|strategy\|execution\|monitoring\)" backend/legacy_scalping/
grep -rn "^import \(scanner\|strategy\|execution\|monitoring\)" backend/legacy_scalping/
```

→ *발견 시 본 티켓에서는 namespace re-export 를 `__init__.py` 에 추가하는 최소 패치만 수행*. 정식 import 정리는 BAR-41 의 일부.

---

## 4. Component Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                     backend/                                      │
│ ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│ │   core/      │  │   models/        │  │  legacy_scalping/    │  │
│ │  (표준)      │  │   (표준)         │  │  (BAR-40 신규)       │  │
│ │              │  │                  │  │                      │  │
│ │ Strategy     │  │ EntrySignal      │  │ main.py (patched)    │  │
│ │ MarketGateway│  │ Position         │  │ scanner/             │  │
│ │ RiskEngine   │  │ Order            │  │ strategy/            │  │
│ └──────────────┘  └──────────────────┘  │  ├ scalping_team/    │  │
│        ▲                  ▲              │  ├ strategy_team/    │  │
│        │                  │              │  └ verification_team/│  │
│        │ (BAR-41 이후)    │              │ execution/           │  │
│        └─── adapter ──────┴──────────────┤ monitoring/          │  │
│                                          │ seoheefather_*.py    │  │
│                                          └──────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

본 티켓 단계: `legacy_scalping/` 박스 *생성만*. adapter (점선) 은 BAR-41.

---

## 5. Verification Scenarios

| # | 시나리오 | 명령 | 기대 결과 |
|---|---|---|---|
| V1 | dry-run 무에러 | `make legacy-scalping` | exit 0, stderr 0줄 |
| V2 | import 격리 | `python -c "import backend.legacy_scalping.scanner; import backend.legacy_scalping.strategy"` | 무에러 |
| V3 | OHLCV 캐시 미커밋 | `git ls-files backend/legacy_scalping/data/ \| wc -l` | `0` |
| V4 | namespace 충돌 부재 | `python -c "import backend.scanner; import backend.legacy_scalping.scanner; print(backend.scanner is backend.legacy_scalping.scanner)"` | `False` (다른 모듈) |
| V5 | dry-run 외부 호출 0건 | `DRY_RUN=1 python -m backend.legacy_scalping.main 2>&1 \| grep -i "telegram\|kiwoom\|order"` | 빈 출력 |
| V6 | repo 추가 용량 | `git diff main --stat` | 합계 ≤ 5MB (LOC 만) |
| V7 | Docker backend 빌드 | `docker compose build backend` | success |
| V8 | 기존 main repo 동작 | `pytest backend/tests/` (있다면) | 변동 없음 |

---

## 6. Risk Mitigation Detail

| Risk (from Plan) | Detection | Action |
|---|---|---|
| import 충돌 | V4 시나리오 실패 | namespace re-export 누락 모듈에 `__init__.py` 패치 |
| OHLCV 144MB git 커밋 | V3 시나리오 ≠ 0 | `git rm --cached -r backend/legacy_scalping/data/`, `.gitignore` 갱신 |
| 환경변수 누락 | V1 dry-run 시 `KeyError`/`AttributeError` | dry-run 진입점 가드(§3.3)가 sys.exit(0) 으로 우회 — 정식 운용은 BAR-42 의존 |
| requirements 충돌 | `pip install` 시 ResolutionImpossible | §3.6 의 2차 시도 (분리) 적용 |
| dry-run 누수 (Layer 1 우회) | V5 시나리오 출력 발견 | 누수 모듈 색출 후 `main.py` import 단계 재정렬 |
| 하드코딩 경로 | grep `/Users/beye/` 검출 | `pathlib.Path(__file__).parent` 로 변경 — 단, BAR-40 의 zero-modification 원칙에 위배 시 **별도 BAR 티켓 (예: BAR-40b)** 으로 분리하고 본 티켓은 placeholder 환경변수로 회피 |

---

## 7. Out-of-Scope (재확인)

본 design 에서 *설계하지 않는* 항목 (의도적 후속 분리):

| 항목 | 책임 티켓 |
|---|---|
| 모델 호환 어댑터 (dict 시그널 ↔ EntrySignal) | BAR-41 |
| 통합 환경변수 스키마 (KIWOOM_*, TELEGRAM_*) | BAR-42 |
| 표준 logger / Prometheus 통일 | BAR-43 |
| 회귀 백테스트 베이스라인 측정 | BAR-44 |
| ai-trade `main.py` 분해 | Phase 1 종료 후 별도 BAR |
| `kiwoom_api.py` ↔ `KiwoomGateway` 통합 | Phase 1 종료 후 별도 BAR |

---

## 8. Implementation Checklist (Do phase 가이드)

> 본 design 승인 후 `/pdca do BAR-40` 으로 아래 체크리스트 실행.

- [ ] D1 — 사전 점검 (`du -sh`, 파일 수)
- [ ] D2 — `rsync` 흡수 (§3.1 명령)
- [ ] D3 — `__init__.py` 일괄 보강 (§3.2)
- [ ] D4 — `main.py` 최상단 dry-run 가드 패치 (§3.3)
- [ ] D5 — `.gitignore` 갱신 (§3.5)
- [ ] D6 — `Makefile` `legacy-scalping` 타겟 추가 (§3.4)
- [ ] D7 — V1~V8 검증 시나리오 실행 (§5)
- [ ] D8 — requirements 충돌 검수 (§3.6) — 충돌 시 분리
- [ ] D9 — `Dockerfile.backend` 갱신 (필요 시)
- [ ] D10 — PR 생성 (`docs(BAR-40): do — sub_repo 흡수 구현`)

---

## 9. Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 design — Plan 1.0 의 §8 작업단계 상세화 | beye |
