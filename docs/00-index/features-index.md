---
tags: [index, features]
---

# 피처 인덱스

> 전체 피처 목록, PDCA 진행 현황, 산출물 링크

---

## 전체 피처 현황

| 피처 ID | 제목 | Plan | Design | Analysis | Report | 상태 |
|---------|------|------|--------|----------|--------|------|
| BAR-17 | 실시간 대시보드 | [[../01-plan/features/bar-17-dashboard.plan\|Plan]] | [[../02-design/features/bar-17-dashboard.design\|Design]] | [[../03-analysis/bar-17-dashboard.analysis\|Analysis]] | [[../04-report/bar-17-dashboard.report\|Report]] | ✅ 완료 |

---

## 피처 상세

### BAR-17 — 실시간 트레이딩 대시보드

- **설명**: Next.js 15 기반 실시간 트레이딩 대시보드 구현 (WebSocket + 차트 + 컴포넌트 시스템)
- **PDCA 상태**: 완료 (Check ≥ 90%)
- **관련 문서**:
  - [[../01-plan/features/bar-17-dashboard.plan|Plan 문서]]
  - [[../02-design/features/bar-17-dashboard.design|Design 문서]]
  - [[../03-analysis/bar-17-dashboard.analysis|Analysis 문서]]
  - [[../04-report/bar-17-dashboard.report|Report 문서]]

---

## 신규 피처 추가 가이드

새로운 피처가 시작될 때 아래 순서로 산출물을 생성합니다:

1. `01-plan/features/{feature-id}.plan.md` 생성
2. `02-design/features/{feature-id}.design.md` 생성
3. `03-analysis/{feature-id}.analysis.md` 생성 (구현 후)
4. `04-report/{feature-id}.report.md` 생성 (GAP ≥ 90% 달성 후)
5. 이 인덱스 테이블에 행 추가

---

*[[README|← 홈으로]] | 최종 업데이트: 2026-04-11*
