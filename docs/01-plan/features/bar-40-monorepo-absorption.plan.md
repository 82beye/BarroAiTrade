---
tags: [plan, feature/bar-40, status/in_progress, phase/0, area/repo]
template: plan
version: 1.0
---

# BAR-40 sub_repo(ai-trade) 모노레포 흡수 Plan

> **Project**: BarroAiTrade
> **Feature**: BAR-40
> **Phase**: 0 (기반 정비) — 첫 티켓
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v1#Phase 0 — 기반 정비 (Week 1–2, 5 티켓: BAR-40~44)]]
> **Author**: beye
> **Date**: 2026-05-06
> **Status**: In Progress
> **Gate**: BAR-41 / BAR-42 / BAR-43 / BAR-44 의 선결 의존 — Phase 0 종료 게이트(베이스라인 리포트)까지 블로킹

---

## 1. Overview

### 1.1 Purpose

`/Users/beye/workspace/ai-trade` 의 16K 줄 자산(main.py 2,412줄, scanner 2,344줄, execution 2,831줄, scalping_team 9에이전트, OHLCV 캐시 144MB)을 BarroAiTrade main 레포 안 `backend/legacy_scalping/` 으로 **동작 변경 없이** 흡수한다. 이로써 두 시스템의 표준 모델·로깅·메트릭을 공유할 토대가 만들어진다.

### 1.2 Background

- 마스터 실행 계획 v1 의 Phase 0 첫 티켓.
- 현재 ai-trade 는 별도 venv·별도 cron 으로 운용되어 BarroAiTrade 의 `Strategy`/`MarketGateway`/`RiskEngine` 표준에 통합되지 못함.
- **이번 단계의 핵심 제약**: ai-trade 의 `main.py`(2,412줄)·`kiwoom_api.py`(1,569줄) 내부 분해는 *하지 않는다*. 흡수만. 분해는 Phase 1 종료 후 별도 티켓.

### 1.3 Related Documents

- 마스터 플랜: [[../MASTER-EXECUTION-PLAN-v1]]
- 시장분석/베이스라인 후속: BAR-44
- 어댑터 후속: BAR-41
- Settings 후속: BAR-42
- Logger 후속: BAR-43

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/legacy_scalping/**` 디렉터리에 ai-trade 전체 미러 (`main.py`, `scanner/`, `strategy/`, `execution/`, `monitoring/`, `seoheefather_strategy.py` 포함)
- [ ] 모든 디렉터리 `__init__.py` 보강 (Python 패키지화)
- [ ] import 경로 정리 — 절대 경로 `backend.legacy_scalping.<sub>` 강제, 기존 ai-trade 내 상대 import 가 깨지지 않도록 검증
- [ ] `Makefile` 에 `legacy-scalping` 타겟 추가 (`python -m backend.legacy_scalping.main --dry-run`)
- [ ] `--dry-run` flag 추가 (cron/실주문 없이 모듈 import + 초기화 + 즉시 종료)
- [ ] `.gitignore` 에 `backend/legacy_scalping/data/ohlcv_cache/` 등록 (144MB 회피)
- [ ] requirements 머지 — `ai-trade/requirements.txt` 와 main repo 의 의존성 버전 충돌 검수
- [ ] CI 그린 확인 (Dockerfile.backend 빌드 + 단순 import smoke)

### 2.2 Out of Scope

- ❌ ai-trade `main.py`(2,412줄) 모듈 분해
- ❌ `kiwoom_api.py`(1,569줄) BarroAiTrade `KiwoomGateway` 와 통합
- ❌ 모델 호환 어댑터 작성 — **BAR-41** 의 책임
- ❌ Settings 통합 — **BAR-42** 의 책임
- ❌ Logger·Prometheus 통일 — **BAR-43** 의 책임
- ❌ scalping_team 의 `Strategy v2` 인터페이스 흡수 — Phase 1 BAR-50 의 책임
- ❌ ai-trade 의 OHLCV 캐시 144MB git 커밋 (.gitignore 처리)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `cp -r ai-trade backend/legacy_scalping` 미러 (`.git`, `.venv`, `__pycache__`, `data/ohlcv_cache/` 제외) | High | Pending |
| FR-02 | `backend/legacy_scalping/__init__.py` + 모든 하위 패키지 `__init__.py` 추가 | High | Pending |
| FR-03 | `python -m backend.legacy_scalping.main --dry-run` 30초 이내 무에러 종료 | High | Pending |
| FR-04 | `Makefile` `legacy-scalping` 타겟 (`make legacy-scalping` 으로 dry-run 실행) | Medium | Pending |
| FR-05 | `.gitignore` 갱신 (OHLCV 캐시 + venv + __pycache__) | High | Pending |
| FR-06 | requirements 충돌 검수 — 충돌 발생 시 `pyproject.toml` extras 또는 `requirements-legacy.txt` 분리 | High | Pending |
| FR-07 | namespace 충돌 회피 — main repo 의 `backend.scanner` vs `backend.legacy_scalping.scanner` 가 동시 import 가능해야 함 | High | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 성능 | dry-run 종료 ≤ 30s | `time make legacy-scalping` |
| 호환성 | ai-trade 기존 모듈 100% import 가능 | `python -c "from backend.legacy_scalping import scanner, strategy, execution, monitoring"` |
| 안전성 | dry-run 시 외부 호출(키움 API/주문/Telegram 송신) 0건 | `--dry-run` flag 가 모든 외부 부수효과 차단 |
| 리포지토리 무게 | git 추가 용량 ≤ 5MB | `git diff --stat` 확인 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] `backend/legacy_scalping/` 디렉터리 생성 및 ai-trade 미러 완료
- [ ] `python -m backend.legacy_scalping.main --dry-run` 무에러 종료
- [ ] `make legacy-scalping` 타겟 동작
- [ ] `python -c "from backend.legacy_scalping import scanner, strategy, execution, monitoring; print('ok')"` 통과
- [ ] OHLCV 캐시(144MB) git 미포함 확인 (`git ls-files backend/legacy_scalping/data/ | wc -l == 0`)
- [ ] Docker `backend` 이미지 빌드 성공 (Dockerfile.backend 변경 시)
- [ ] PR 본인 셀프 리뷰 + 머지

### 4.2 Quality Criteria

- [ ] 새 코드 line 수 ≤ 100줄 (대부분은 cp + __init__.py 보강)
- [ ] dry-run 시 stderr 0줄
- [ ] requirements 충돌 0건 (또는 분리 strategy 적용 후)

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| import 충돌 (예: 동일 모듈명 `scanner`) | Medium | High | `backend.legacy_scalping.<sub>` namespace 강제. `__init__.py` 의 명시적 re-export 만 허용 |
| OHLCV 캐시 144MB git 커밋 | High (repo 비대화) | Medium | `.gitignore` 사전 등록, `git status` 로 untracked 확인 후 add |
| ai-trade 의 환경변수 누락 (KIWOOM_*, TELEGRAM_*, NOTION_*) | Medium | Medium | BAR-42 의존 — 본 티켓에서는 placeholder 만 표시, 실제 운용은 BAR-42 |
| requirements 버전 충돌 (예: pandas / httpx) | Medium | Medium | 충돌 발견 시 `requirements-legacy.txt` 분리 + Dockerfile.backend 에서 `pip install -r` 분리 |
| dry-run 모드가 외부 호출을 차단하지 못함 | High (실주문 위험) | Low | `--dry-run` flag → `os.environ["DRY_RUN"]="1"` 설정 → ai-trade 의 모든 외부 호출 진입 시 early return. 차단 누락 발견 시 PR 차단 |
| ai-trade 의 절대 경로 의존 (e.g., `/Users/beye/...`) | Medium | Medium | grep 으로 하드코딩 경로 색출, `pathlib.Path(__file__).parent` 로 변경 |

---

## 6. Architecture Considerations

### 6.1 Project Level

- **Enterprise** (이미 BarroAiTrade 는 Enterprise 레벨로 운영)

### 6.2 흡수 전략

```
Before:
/Users/beye/workspace/ai-trade/        (별도 레포, 별도 venv)
└── main.py, scanner/, strategy/, execution/, monitoring/

After:
/Users/beye/workspace/BarroAiTrade/
└── backend/
    ├── core/                          (기존 — 표준 시스템)
    │   ├── strategy/, gateway/, scanner/, ...
    └── legacy_scalping/                (신규 — ai-trade 미러)
        ├── __init__.py
        ├── main.py
        ├── scanner/, strategy/, execution/, monitoring/
        └── seoheefather_strategy.py
```

### 6.3 Key Decisions

| Decision | Selected | Rationale |
|---|---|---|
| 흡수 방식 | `cp -r` 복사 (git mv 아님) | ai-trade 는 별도 레포라서 git mv 불가. ai-trade 자체 git history 는 이번 단계에서 보존하지 않음 (필요 시 별도 BAR 티켓에서 git subtree merge 검토) |
| Namespace | `backend.legacy_scalping` | main repo 의 `backend` 트리 안에 격리 |
| dry-run 차단 메커니즘 | `DRY_RUN=1` 환경변수 + 핵심 진입점 가드 | ai-trade 코드 변경 최소화 |
| requirements | 1차 머지 시도 → 충돌 시 분리 | YAGNI: 먼저 단일 requirements 가능한지 확인 |

---

## 7. Convention Prerequisites

### 7.1 기존 프로젝트 컨벤션

- ✅ `docs/01-plan/features/{bar-XX}-{slug}.plan.md` (BAR-17, 23, 28, 29 선례)
- ✅ Pydantic v2 + asyncio 표준
- ❌ `CLAUDE.md` 부재 — Phase 0 종료 후 작성 권장 (별도 BAR 티켓 후보)
- ❌ `tests/` 디렉터리 부재 — BAR-41 에서 `tests/legacy_scalping/` 시동

### 7.2 본 티켓에서 정의할 컨벤션

| 항목 | 결정 |
|---|---|
| legacy 모듈 import path | 항상 `from backend.legacy_scalping.<sub> import ...` (절대) |
| legacy 모듈 수정 정책 | **수정 금지** (단순 흡수). 패치 필요 시 어댑터(BAR-41)에서 처리 |
| OHLCV 캐시 위치 | `backend/legacy_scalping/data/ohlcv_cache/` (gitignored) — Phase 0 종료 후 BAR-44 시 활용 |
| dry-run 환경변수 | `DRY_RUN=1` |

---

## 8. 작업 단계 (Implementation Outline)

> 본 plan 승인 후 BAR-40 design 문서에서 상세화. 여기는 개략적 단계.

1. **사전 점검**: `du -sh /Users/beye/workspace/ai-trade/data/` 로 캐시 크기 확인, `find ai-trade -type f -name "*.py" | wc -l` 로 파일 수 확인
2. **미러**: `rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='data/ohlcv_cache/' ai-trade/ BarroAiTrade/backend/legacy_scalping/`
3. **`__init__.py` 보강**: 모든 디렉터리에 빈 `__init__.py` (또는 명시적 re-export)
4. **dry-run flag 주입**: `main.py` 진입점에서 `DRY_RUN=1` 시 즉시 import-only 종료
5. **import 경로 검증**: `python -m backend.legacy_scalping.main --dry-run` 실행 → 첫 실패 fix → 반복
6. **`.gitignore` 갱신**: `backend/legacy_scalping/data/`, `*.pyc`, `__pycache__`, `.venv/`
7. **`Makefile` 타겟**: `legacy-scalping: \n\tDRY_RUN=1 python -m backend.legacy_scalping.main --dry-run`
8. **requirements 검수**: `pip install -r ai-trade/requirements.txt` 충돌 여부 확인 → 충돌 시 `requirements-legacy.txt` 분리
9. **PR 생성**: `BAR-40-monorepo-absorption` 브랜치 → `docs(do): BAR-40 sub_repo 모노레포 흡수`

---

## 9. Next Steps

1. [ ] Design 문서 작성 (`/pdca design BAR-40`) — `docs/02-design/features/bar-40-monorepo-absorption.design.md`
2. [ ] 본인 리뷰 + 승인
3. [ ] Do 단계 진입 (`/pdca do BAR-40`) — 위 작업 단계 실행
4. [ ] BAR-41 plan 문서 작성 (어댑터)

---

## 10. 비고: BAR-51 번호 충돌

마스터 플랜 v1 작성 시점(2026-05-06) 직전에 main 에 **다른 BAR-51 (서비스 복구 모니터링 — 하트비트/오케스트레이터, commit `bb85bcf`)** 이 머지된 상태가 발견됨. 마스터 플랜의 BAR-51(백테스터 v2 확장)은 번호 재할당이 필요. 본 BAR-40 작업 자체에는 영향 없으나, Phase 1 plan 작성 시 BAR-51 → 다른 미사용 번호(예: BAR-79)로 변경할 것. 마스터 플랜 v2 발행 시 일괄 정정 권장.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 plan — Phase 0 첫 티켓 | beye |
