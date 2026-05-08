---
tags: [index, features]
---

# 피처 인덱스

> 전체 피처 목록, PDCA 진행 현황, 산출물 링크

---

## 마스터 실행 계획

| 문서 | 범위 | 상태 |
|---|---|:---:|
| [[../01-plan/MASTER-EXECUTION-PLAN-v2\|마스터 실행 계획 v2]] | Phase 0~6 / BAR-40~79 (40 티켓) | 🟢 Active |
| [[../01-plan/MASTER-EXECUTION-PLAN-v1\|마스터 실행 계획 v1]] | (supersede by v2) | 📦 보존 |

---

## Phase 0 — 기반 정비 ✅ 완료 (2026-05-06)

| BAR | Title | Plan | Design | Analysis | Report | Match | 상태 |
|-----|-------|:---:|:---:|:---:|:---:|:---:|:---:|
| **BAR-40** | sub_repo 모노레포 흡수 | [[../01-plan/features/bar-40-monorepo-absorption.plan\|P]] | [[../02-design/features/bar-40-monorepo-absorption.design\|D]] | [[../03-analysis/bar-40-monorepo-absorption.analysis\|A]] | [[../04-report/bar-40-monorepo-absorption.report\|R]] | 95% | ✅ |
| **BAR-41** | 모델 호환 어댑터 | [[../01-plan/features/bar-41-model-adapter.plan\|P]] | [[../02-design/features/bar-41-model-adapter.design\|D]] | [[../03-analysis/bar-41-model-adapter.analysis\|A]] | [[../04-report/bar-41-model-adapter.report\|R]] | 96% | ✅ |
| **BAR-42** | 통합 환경변수 스키마 | [[../01-plan/features/bar-42-config-settings.plan\|P]] | [[../02-design/features/bar-42-config-settings.design\|D]] | [[../03-analysis/bar-42-config-settings.analysis\|A]] | [[../04-report/bar-42-config-settings.report\|R]] | 98% | ✅ |
| **BAR-43** | 표준 로깅·메트릭 통일 | [[../01-plan/features/bar-43-monitoring-unify.plan\|P]] | [[../02-design/features/bar-43-monitoring-unify.design\|D]] | [[../03-analysis/bar-43-monitoring-unify.analysis\|A]] | [[../04-report/bar-43-monitoring-unify.report\|R]] | 97% | ✅ |
| **BAR-44** | 회귀 베이스라인 (종료 게이트) | [[../01-plan/features/bar-44-baseline.plan\|P]] | [[../02-design/features/bar-44-baseline.design\|D]] | [[../03-analysis/bar-44-baseline.analysis\|A]] | [[../04-report/bar-44-baseline.report\|R]] | 96% | ✅ |

→ **회고**: [[../04-report/PHASE-0-summary]] | **베이스라인**: [[../04-report/PHASE-0-baseline-2026-05]]

## Phase 1 — 전략 엔진 통합 ⏳ 진입 예정

| BAR | Title | 의존 | 상태 |
|-----|-------|------|------|
| BAR-45 | Strategy v2 추상 + AnalysisContext | Phase 0 ✅ | 🔓 진입 가능 |
| BAR-46 | F존 v2 리팩터 | BAR-45 | ⏳ |
| BAR-47 | SF존 별도 클래스 분리 | BAR-45 | ⏳ |
| BAR-48 | 골드존 전략 신규 포팅 | BAR-45 | ⏳ |
| BAR-49 | 38스윙 전략 신규 포팅 | BAR-45 | ⏳ |
| BAR-50 | ScalpingConsensusStrategy | BAR-45 | ⏳ |

> v1 의 BAR-51 은 v2 에서 BAR-79 (백테스터 v2 확장, Phase 6) 로 재할당.

## Phase 2~6 — 마스터 플랜 v2 참조

[[../01-plan/MASTER-EXECUTION-PLAN-v2#2. v2 BAR 매트릭스 (v1 + 변경 + 신규)]]

---

## 이전 피처 (Phase 0 이전)

| BAR | Title | Plan | Design | Analysis | Report | 상태 |
|-----|-------|:---:|:---:|:---:|:---:|:---:|
| BAR-17 | 실시간 대시보드 | [[../01-plan/features/bar-17-dashboard.plan\|P]] | [[../02-design/features/bar-17-dashboard.design\|D]] | [[../03-analysis/bar-17-dashboard.analysis\|A]] | [[../04-report/bar-17-dashboard.report\|R]] | ✅ |
| BAR-23 | 프론트엔드 페이지 | [[../01-plan/features/bar-23-frontend-pages.plan\|P]] | [[../02-design/features/bar-23-frontend-pages.design\|D]] | [[../03-analysis/bar-23-frontend-pages.analysis\|A]] | [[../04-report/bar-23-frontend-pages.report\|R]] | ✅ |
| BAR-28 | 한국 주식 시장 분석 | [[../01-plan/features/bar-28-korean-stocks.plan\|P]] | — | — | — | ✅ |
| BAR-29 | 백테스팅 검증 리포트 | [[../01-plan/features/bar-29-backtest-validation.plan\|P]] | — | — | — | ✅ |

---

## 분석 자산 (Plan 입력)

- [[../01-plan/analysis/BarroAiTrade_고도화_계획|BarroAiTrade × ai-trade 통합 고도화 계획]] — 마스터 플랜 v1/v2 의 *원본 입력 문서*
- [[../01-plan/analysis/Backtest-Validation-Report|백테스팅 전략 검증 리포트]] (BAR-29)
- [[../01-plan/analysis/Backtest-Performance-Metrics|성과 지표 데이터 (CSV)]]
- [[../01-plan/analysis/KR-Market-Analysis-2026Q2|한국 주식 시장 분석 2026Q2]] (BAR-28)
- [[../01-plan/analysis/System-Parameters-Initial|시스템 파라미터 초기값]]

---

## 신규 피처 추가 가이드

새 피처는 PDCA 5 PR 패턴으로 진행:

1. `/pdca plan BAR-XX` → `01-plan/features/bar-XX-{slug}.plan.md`
2. `/pdca design BAR-XX` → `02-design/features/bar-XX-{slug}.design.md`
3. `/pdca do BAR-XX` (구현 + 테스트)
4. `/pdca analyze BAR-XX` → `03-analysis/bar-XX-{slug}.analysis.md` (gap-detector)
5. `/pdca report BAR-XX` → `04-report/bar-XX-{slug}.report.md` (Match ≥ 90%)
6. 이 인덱스 + `_index.md` 갱신

---

## OPS 트랙 (운영 자동화 26 BAR)

> Master Plan v2 (BAR-40~79) 완료 후 추가된 운영 자동화 + 자기학습 트랙.

→ [[ops-track-index|OPS 작업 순서 인덱스]] (BAR-OPS-01~35, 26 BAR)
→ [[system-flow|시스템 흐름도]] (Mermaid 9 다이어그램)
→ [[../05-paperclip/runbook-ops|운영 시작 RUNBOOK]]
→ [[../05-paperclip/security-rotation|보안 회전 가이드]]

| 영역 | 진행 |
|------|------|
| 인증·기본 (OPS-01~07) | ✅ 완료 |
| 시뮬·전략 (OPS-08~09) | ✅ 완료 |
| 키움 자체 OpenAPI (OPS-10~12) | ✅ 완료 |
| 영속·정책·게이트 (OPS-13~17) | ✅ 완료 |
| End-to-End + 매도 (OPS-18~20) | ✅ 완료 |
| Telegram (OPS-21~25) | ✅ 완료 |
| Confirm 패턴 (OPS-26~27) | ✅ 완료 |
| 학습 루프 (OPS-28~32) | ✅ 완료 |
| 미체결 + 정확도 (OPS-33~35) | ✅ 완료 |

**누적**: 키움 API 11 TR-ID / Telegram 19 명령 / 회귀 830 passed, 0 fail

---

*[[../README|← 홈으로]] | 최종 업데이트: 2026-05-08 (OPS-35 완료, 운영 가능)*
