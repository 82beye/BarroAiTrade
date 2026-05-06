# BarroAiTrade — Knowledge Base

> AI 기반 트레이딩 플랫폼 프로젝트 산출물 저장소

이 Vault는 BarroAiTrade 프로젝트의 모든 PDCA 산출물을 Obsidian에서 탐색 가능한 지식 베이스로 관리합니다.

---

## 빠른 탐색

| 구분 | 링크 | 설명 |
|------|------|------|
| 🎯 **마스터 실행 계획 v2** | [[01-plan/MASTER-EXECUTION-PLAN-v2]] | Phase 0~6 / BAR-40~79 (40 티켓, 🟢 Active) |
| 📦 마스터 실행 계획 v1 | [[01-plan/MASTER-EXECUTION-PLAN-v1]] | (supersede by v2, 보존) |
| 🎉 **Phase 0 종합 회고** | [[04-report/PHASE-0-summary]] | 5 BAR / 27 PR / 평균 96.4% (2026-05-06) |
| 📊 Phase 0 베이스라인 | [[04-report/PHASE-0-baseline-2026-05]] | 4 전략 ±5% 회귀 임계값 |
| 피처 인덱스 | [[00-index/features-index]] | 전체 피처 목록 및 현황 |
| PDCA 대시보드 | [[00-index/status-dashboard]] | 단계별 산출물 현황 |
| 배포 정보 | [[deployment]] | 인프라 & 배포 가이드 |
| Paperclip Board | [[05-paperclip/issue-board]] | Paperclip 이슈 현황 |
| WBS | [[05-paperclip/wbs]] | 전체 구현 계획 + 스케줄 |

---

## PDCA 산출물 구조

```
docs/
├── 00-index/          # 인덱스 & 대시보드
├── 01-plan/           # Plan 산출물
│   └── features/
├── 02-design/         # Design 산출물
│   └── features/
├── 03-analysis/       # Analysis(Check) 산출물
├── 04-report/         # Report(Act) 산출물
└── 05-paperclip/      # Paperclip 연동 (Issue Board, WBS)
```

---

## 피처별 산출물

### Phase 0 (기반 정비) — ✅ 완료 (2026-05-06)

| BAR | Title | Plan | Design | Analysis | Report | Match |
|-----|-------|:---:|:---:|:---:|:---:|:---:|
| **BAR-40** | sub_repo 모노레포 흡수 | [[01-plan/features/bar-40-monorepo-absorption.plan\|P]] | [[02-design/features/bar-40-monorepo-absorption.design\|D]] | [[03-analysis/bar-40-monorepo-absorption.analysis\|A]] | [[04-report/bar-40-monorepo-absorption.report\|R]] | 95% |
| **BAR-41** | 모델 호환 어댑터 | [[01-plan/features/bar-41-model-adapter.plan\|P]] | [[02-design/features/bar-41-model-adapter.design\|D]] | [[03-analysis/bar-41-model-adapter.analysis\|A]] | [[04-report/bar-41-model-adapter.report\|R]] | 96% |
| **BAR-42** | 통합 환경변수 스키마 | [[01-plan/features/bar-42-config-settings.plan\|P]] | [[02-design/features/bar-42-config-settings.design\|D]] | [[03-analysis/bar-42-config-settings.analysis\|A]] | [[04-report/bar-42-config-settings.report\|R]] | 98% |
| **BAR-43** | 표준 로깅·메트릭 통일 | [[01-plan/features/bar-43-monitoring-unify.plan\|P]] | [[02-design/features/bar-43-monitoring-unify.design\|D]] | [[03-analysis/bar-43-monitoring-unify.analysis\|A]] | [[04-report/bar-43-monitoring-unify.report\|R]] | 97% |
| **BAR-44** | 회귀 베이스라인 (종료 게이트) | [[01-plan/features/bar-44-baseline.plan\|P]] | [[02-design/features/bar-44-baseline.design\|D]] | [[03-analysis/bar-44-baseline.analysis\|A]] | [[04-report/bar-44-baseline.report\|R]] | 96% |

→ **Phase 0 종합 회고**: [[04-report/PHASE-0-summary]] (5 BAR / 27 PR / 42 테스트 / 평균 96.4%)

### Phase 1 (전략 엔진 통합) — ⏳ 진입 예정

BAR-45~50 (Strategy v2, F존/SF존/골드존/38스윙, ScalpingConsensus). 마스터 플랜 v2 §2 참조.

### 이전 피처

| BAR | Title | Plan | Design | Analysis | Report | 상태 |
|-----|-------|:---:|:---:|:---:|:---:|:---:|
| BAR-17 | 실시간 대시보드 | [[01-plan/features/bar-17-dashboard.plan\|P]] | [[02-design/features/bar-17-dashboard.design\|D]] | [[03-analysis/bar-17-dashboard.analysis\|A]] | [[04-report/bar-17-dashboard.report\|R]] | ✅ |
| BAR-23 | 프론트엔드 페이지 | [[01-plan/features/bar-23-frontend-pages.plan\|P]] | [[02-design/features/bar-23-frontend-pages.design\|D]] | [[03-analysis/bar-23-frontend-pages.analysis\|A]] | [[04-report/bar-23-frontend-pages.report\|R]] | ✅ |
| BAR-28 | 한국 주식 시장 분석 | [[01-plan/features/bar-28-korean-stocks.plan\|P]] | — | — | — | ✅ |
| BAR-29 | 백테스팅 검증 리포트 | [[01-plan/features/bar-29-backtest-validation.plan\|P]] | — | — | — | ✅ |

---

## 태그 구조

- `#plan` — Plan 산출물
- `#design` — Design 산출물
- `#analysis` — Gap/Check 분석
- `#report` — PDCA 완료 보고서
- `#paperclip` — Paperclip 연동 문서
- `#feature/bar-17` ~ `#feature/bar-44` — 피처별 모든 문서
- `#phase/0` ~ `#phase/6` — Phase 별 분류
- `#area/repo`, `#area/strategy`, `#area/security` 등 — 영역 분류
- `#status/done` — 완료된 산출물
- `#status/in_progress` — 진행 중인 산출물
- `#milestone/phase-0-종료` — Phase 0 종료 마일스톤 (BAR-44 report)

---

## Paperclip 연동

- **Paperclip UI**: [http://127.0.0.1:3100](http://127.0.0.1:3100)
- **회사**: BarroQuant (이슈 접두어: BAR)
- **프로젝트**: BarroAiTrade
- Issue Board: [[05-paperclip/issue-board]]
- WBS 계획: [[05-paperclip/wbs]]

---

*BarroAiTrade PDCA Knowledge Base — 마지막 업데이트: 2026-05-06 (Phase 0 종료, 27 PR / 42 테스트 / 평균 96.4%)*
