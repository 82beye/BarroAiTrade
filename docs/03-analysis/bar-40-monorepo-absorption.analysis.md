---
tags: [analysis, feature/bar-40, status/in_progress, phase/0]
template: analysis
version: 1.0
---

# BAR-40 Gap Analysis Report

> **관련 문서**: [[../01-plan/features/bar-40-monorepo-absorption.plan|Plan]] | [[../02-design/features/bar-40-monorepo-absorption.design|Design]] | Report (pending)

- **Feature**: BAR-40 sub_repo(ai-trade) 모노레포 흡수
- **Phase**: 0 (기반 정비) — 첫 티켓
- **Match Rate**: **95%**
- **Date**: 2026-05-06
- **Status**: ✅ Above 90% — `/pdca report` 진행 권장
- **Branch**: `BAR-40-analyze-gap` (Do 결과는 PR #4 로 main 머지 완료, commit `9c49c9a`)

---

## 1. Analysis Overview

### 1.1 Purpose

BAR-40 의 **Plan §3 (FR-01~FR-07, NFR 4건, DoD 7건)** 과 **Design §3/§5/§8** 항목 단위로 실제 구현(현재 main HEAD `9c49c9a`) 의 일치도를 점검하고, 누락(Missing)·가산(Added)·보완(Patched) 사항을 분류해 다음 PDCA 단계(report vs iterate) 를 결정한다.

본 흡수 티켓의 핵심 제약은 **zero-modification mirror** 와 **import 격리** 였으며, 두 제약이 모두 만족되었는지가 합/부의 핵심 지표다.

### 1.2 Scope

- **Design 문서**: `docs/02-design/features/bar-40-monorepo-absorption.design.md` (352 lines)
- **Plan 문서**: `docs/01-plan/features/bar-40-monorepo-absorption.plan.md`
- **구현 경로**: `backend/legacy_scalping/**` (95 신규 파일, +30,075 LOC, ≈1.5MB), `Makefile`, `.gitignore`, `Dockerfile.backend`
- **검증 결과 출처**: PR #4 의 V1~V6 실증 결과 + 본 분석에서의 read-only 재확인
- **Analysis Date**: 2026-05-06

---

## 2. Phase Scores

### 2.1 Plan §3.1 Functional Requirements (FR-01 ~ FR-07)

| ID | 요구사항 | 구현 | 근거 |
|----|----------|:----:|------|
| FR-01 | rsync 미러 (`.git`/`.venv`/`__pycache__`/`data/ohlcv_cache/` 제외) | ✅ | 95 파일 / +30,075 LOC, OHLCV 미포함 |
| FR-02 | 모든 하위 디렉터리 `__init__.py` 보강 | ✅ | 13개 `__init__.py` 확인 (config/docs/scripts/monitoring/templates 포함) |
| FR-03 | `python -m backend.legacy_scalping.main` (DRY_RUN=1) 30초 이내 무에러 | ✅ | V1: exit 0, stderr 0줄 (top-of-module sys.exit) |
| FR-04 | `Makefile` `legacy-scalping` 타겟 | ✅ | `Makefile:11-14` (+ `PYTHON ?= python3` 보완) |
| FR-05 | `.gitignore` 갱신 (OHLCV·venv·pycache·log) | ✅ | `.gitignore:45-50` 5개 패턴 |
| FR-06 | requirements 충돌 검수 (충돌 시 분리) | ✅ | pandas 마이너 차이만 → 분리 미실행, Design §3.6 옵션 B 결정 기록 |
| FR-07 | namespace 충돌 회피 (`backend.scanner` vs `backend.legacy_scalping.scanner`) | ✅ | V4: main repo 에 `backend.scanner` 부재로 충돌 자체 부재 |

**Score: 7/7 = 100%**

### 2.2 Plan §3.2 Non-Functional Requirements

| 카테고리 | 기준 | 결과 | Status |
|----------|------|------|:------:|
| 성능 | dry-run ≤ 30s | DRY_RUN=1 시 즉시 sys.exit(0) (~0.5s) | ✅ |
| 호환성 | scanner/strategy/execution/monitoring 100% import | V2 통과 | ✅ |
| 안전성 | 외부 호출 0건 | V5 통과 (telegram/kiwoom/order grep 빈 출력) | ✅ |
| 리포지토리 | git 추가 ≤ 5MB | V6: ≈1.5MB | ✅ |

**Score: 4/4 = 100%**

### 2.3 Plan §4 Definition of Done

| 항목 | 결과 | Status |
|------|------|:------:|
| `backend/legacy_scalping/` 미러 | 95 파일 흡수 | ✅ |
| `python -m ... main` (DRY_RUN=1) 무에러 | V1 통과 | ✅ |
| `make legacy-scalping` 동작 | PR #4 검증 | ✅ |
| `python -c "from backend.legacy_scalping import scanner, strategy, execution, monitoring"` | V2 통과 | ✅ |
| OHLCV git 미포함 | V3: 0건 | ✅ |
| Docker `backend` 빌드 성공 | V7 미수행 — PR CI / 사용자 로컬 위임 | ⚠️ |
| PR 셀프 리뷰 + 머지 | PR #4 머지 (commit `9c49c9a`) | ✅ |

**Score: 6/7 = 86%** (V7 deferred — 위험도 낮음, BAR-41/43 시 자연스럽게 재검증)

### 2.4 Design §3 Implementation Spec

| Sub | 항목 | Status | Note |
|-----|------|:------:|------|
| §3.1 | rsync 흡수 명령 | ✅ + 🔄 | `frontend/`, `.claude/`, ` 2` suffix conflict, 빈 `backend/` 추가 exclude (구현 단계 발견) |
| §3.2 | `__init__.py` 일괄 보강 | ✅ | 13개 |
| §3.3 | `main.py` 최상단 5줄 dry-run 가드 | ✅ | `main.py:21-28` 정확히 설계대로 |
| §3.4 | Makefile 타겟 | ✅ + 🔄 | `PYTHON ?= python3` 추가 (system `python` 부재 환경 대응) |
| §3.5 | `.gitignore` 5개 패턴 | ✅ | 완전 일치 |
| §3.6 | requirements 정책 (1차 머지 → 충돌 시 분리) | ✅ | 충돌 0건 → 분리 미실행 → Option B 유지 결정 명문화 |
| §3.7 | import 격리 검증 스크립트 | ✅ | V2 통과 |

**Score: 7/7 = 100%** (🔄 보완은 설계 향상이지 결손 아님)

### 2.5 Design §5 Verification Scenarios (V1 ~ V8)

| # | 시나리오 | 결과 | Status |
|---|----------|------|:------:|
| V1 | dry-run 무에러 | exit 0, stderr 0줄 | ✅ |
| V2 | import 격리 | scanner/strategy/execution/monitoring OK | ✅ |
| V3 | OHLCV 미커밋 | `git ls-files data/` = 0 | ✅ |
| V4 | namespace 충돌 부재 | main repo 에 `backend.scanner` 자체가 없음 | ✅ |
| V5 | dry-run 외부 호출 0건 | telegram/kiwoom/order grep 빈 출력 | ✅ |
| V6 | repo 추가 용량 | ≈1.5MB ≤ 5MB | ✅ |
| V7 | Docker backend 빌드 | PR CI 위임, 로컬 미실행 | ⏳ |
| V8 | 기존 main repo 회귀 | `backend/tests/` 부재 → skip | ➖ N/A |

**Score: 6/7 effective = 86%** (V8 N/A 제외, V7 보류 1건)

### 2.6 Design §8 Implementation Checklist (D1 ~ D10)

| ID | 항목 | Status |
|----|------|:------:|
| D1 | 사전 점검 (`du -sh`, 파일 수) | ✅ |
| D2 | rsync 흡수 (§3.1) | ✅ |
| D3 | `__init__.py` 일괄 보강 (§3.2) | ✅ |
| D4 | `main.py` dry-run 가드 (§3.3) | ✅ |
| D5 | `.gitignore` 갱신 (§3.5) | ✅ |
| D6 | Makefile 타겟 (§3.4) | ✅ |
| D7 | V1~V8 검증 시나리오 | ⚠️ (V1~V6 ✅, V7 ⏳, V8 N/A) |
| D8 | requirements 충돌 검수 (§3.6) | ✅ |
| D9 | `Dockerfile.backend` 갱신 | ✅ (코멘트로 BAR-41/43 위임 명시) |
| D10 | PR 생성 | ✅ (PR #4 머지) |

**Score: 9.5/10 = 95%**

### 2.7 Overall Match Rate

```
Weight 분배: Plan(FR+NFR+DoD) 30% / Design §3 30% / Design §5 20% / Design §8 20%

Plan 평균: (100% + 100% + 86%) / 3 = 95.3%   → 28.6
Design §3:                              100%   → 30.0
Design §5:                               86%   → 17.2
Design §8:                               95%   → 19.0
─────────────────────────────────────────────────────
Overall                                          ≈ 94.8% → 반올림 95%
```

**Match Rate: 95% — Above 90% threshold.**

---

## 3. Missing Items

설계는 있으나 구현되지 않은 항목 (현 단계의 의도적 deferral 포함):

| # | 항목 | 출처 | 사유 / 처리 |
|---|------|------|-------------|
| M1 | Docker backend 빌드 검증(V7) | Design §5, Plan DoD | PR CI / 사용자 로컬 위임. 위험도 낮음 (Dockerfile.backend 동작 변경 없음). BAR-41/43 시 의존성 분리할 때 자연 재검증 |
| M2 | 회귀 테스트(V8) | Design §5 | `backend/tests/` 부재로 skip 결정. BAR-41 에서 `tests/legacy_scalping/` 시동 시 본격 도입 |
| M3 | `backend/legacy_scalping/requirements.txt` 별도 분리 | Design §3.6 옵션 B | requirements 충돌 0건 (pandas 마이너 차이) → §3.6 의 1차 시도(통합) 로 충분 → 의도적 미실행. 후속 BAR-41/43 추가 의존성 발생 시 분리 검토 |

**Missing 영향도**: 모두 *지연 검증* 또는 *상위 티켓 위임* 으로, 본 티켓의 흡수 동작 자체에는 영향 없음.

---

## 4. Additional Changes (가산 변경)

설계에 명시되지 않았으나 구현 단계에서 발견·추가된 보완:

| # | 변경 | 위치 | 사유 |
|---|------|------|------|
| A1 | rsync exclude 확장: `frontend/`, `.claude/`, ` 2` (공백+숫자) suffix conflict 디렉터리, 빈 `backend/` | rsync 명령 (§3.1 본문 유지, 실행 시 추가) | 실제 ai-trade 디렉터리에 macOS Finder 충돌 사본·BarroAiTrade 와 무관한 frontend·.claude 캐시가 잔존 → 흡수 오염 방지 |
| A2 | Makefile 에 `PYTHON ?= python3` 변수 도입 | `Makefile:9` | macOS 14+ 에서 system `python` 명령어 부재. 설계는 `python` 으로 표기되어 있었음 → 환경 비호환 회피 |
| A3 | `Dockerfile.backend:16-18` 코멘트로 BAR-41/43 위임 의도 명시 | `Dockerfile.backend` | legacy 의 추가 의존성(flask/matplotlib/reportlab) 은 본 티켓 범위 밖임을 코드 레벨에서 가시화. 동작 변경 없음 |
| A4 | `__init__.py` 를 `config/`, `docs/`, `scripts/`, `monitoring/templates/`, `scanner/agents/` 까지 모두 생성 | 13개 파일 | 설계는 "각 디렉터리에 빈 `__init__.py`" 로 일반화 — 구현은 ai-trade 의 모든 패키지 후보 디렉터리에 누락 없이 적용 (V2 import 격리 보장 강화) |
| A5 | requirements 옵션 B(분리) 미실행 결정 명문화 | 본 분석 §2.4 §3.M3 | Design §3.6 의 분기 결정을 retro 로 기록 |

**가산 영향도**: 모두 *방어적 보완* — 설계 정신과 충돌하지 않으며, 후속 티켓에서 design 본문 1.1 버전으로 반영 권장.

---

## 5. Risk Status (Design §6 대조)

| Risk | Detection | 발생 여부 |
|------|-----------|:---------:|
| import 충돌 | V4 실패 | 미발생 |
| OHLCV 144MB git 커밋 | V3 ≠ 0 | 미발생 |
| 환경변수 누락 | V1 시 KeyError | 미발생 (top-of-module sys.exit 으로 우회 성공) |
| requirements 충돌 | pip ResolutionImpossible | 미발생 (pandas 마이너 차이만) |
| dry-run 누수 (Layer 1 우회) | V5 출력 발견 | 미발생 |
| 하드코딩 경로 (`/Users/beye/`) | grep 검출 | 본 티켓 범위 밖 — BAR-40b 후속 검토 |

---

## 6. Convention & Architecture 준수

본 티켓은 *흡수 only* 라 BarroAiTrade 의 코딩 컨벤션을 legacy 코드에 강제하지 않는다 (zero-modification 원칙).

| 영역 | 평가 |
|------|------|
| Namespace (`backend.legacy_scalping.<sub>`) | ✅ 절대경로 강제 — 컨벤션 7.2 충족 |
| Folder 구조 (격리) | ✅ `backend/core/` 와 `backend/legacy_scalping/` 분리 |
| Naming | ➖ legacy 코드 그대로 (Plan 7.2 의 "수정 금지" 원칙) — BAR-41 어댑터 단계의 의무 |
| Import order / Type imports | ➖ Python 코드 — TS 컨벤션 비대상 |
| Env vars | ⚠️ KIWOOM_*/TELEGRAM_*/NOTION_* placeholder — BAR-42 위임 |

---

## 7. Conclusion

BAR-40 은 흡수 (mirror) 티켓의 본질에 부합하게 **설계의 7개 핵심 메커니즘 (rsync · `__init__.py` · DRY_RUN 가드 · Makefile · `.gitignore` · requirements 정책 · import 격리) 을 모두 구현**했다. V1~V6 시나리오는 실증되었고, V7 (Docker 빌드) 은 PR CI 로 위임, V8 (회귀 테스트) 은 `backend/tests/` 부재로 N/A 처리되었다. 설계 외 구현 단계 보완(A1~A5) 도 모두 *방어적 enhancement* 이며 설계 정신과 일치한다.

가장 중요한 두 제약 — **zero-modification mirror** (legacy 코드 의미 변경 0건, `main.py` 최상단 5줄 패치만 추가) 와 **import 격리** (`backend.legacy_scalping.<sub>` namespace) — 가 모두 충족되었기에, 후속 BAR-41 (어댑터), BAR-42 (Settings), BAR-43 (Logger), BAR-44 (베이스라인) 의 의존 기반이 안정적으로 마련되었다고 판단한다.

**Match Rate 95%** 는 90% 임계값을 명확히 상회하며, deferred 항목(V7/V8/requirements 분리) 은 모두 후속 티켓 또는 CI 단계에서 자연스럽게 재검증될 성격이다.

### 7.1 Recommended Next Step

→ **`/pdca report BAR-40`** 진행 (≥90% 도달, iterate 불요).

Report 단계에서 다음 사항을 다룰 것을 권장:

1. PR #4 머지 완료 사실, 그리고 후속 BAR-41/42/43/44 의 *블로킹 해제* 명시
2. Phase 0 의 첫 게이트 통과 — Master Plan v1 의 Phase 0 종료 게이트(베이스라인 리포트, BAR-44)까지의 잔여 거리 갱신
3. 본 분석 §4 의 가산 변경(A1~A5) 을 design v1.1 으로 retro 반영 (선택)
4. V7 (Docker 빌드) 검증 결과 첨부 (가능 시), 또는 BAR-41 시 통합 검증 약속
5. 설계 §6 위험 6건 중 *하드코딩 경로* 항목을 BAR-40b 후속 티켓으로 분리 등록

### 7.2 Iteration 미권장 사유

90% 미달이 아니므로 `/pdca iterate` 는 비용 대비 효익이 낮다. M1~M3 의 Missing 은 *결손* 이 아니라 *위임* 이며, A1~A5 의 가산은 모두 정합 — 자동 fix 대상이 아니다. 다음 티켓(BAR-41) 에서 자연스럽게 다뤄질 사안이다.

---

## 8. Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 gap 분석 — Match Rate 95% (Above 90%), report 권장 | beye |
