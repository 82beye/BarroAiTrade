---
tags: [plan, feature/bar-23, status/in_progress]
---

# BAR-23 Frontend: Watchlist + Reports + Settings 페이지 Planning Document

> **관련 문서**: [[bar-23-frontend-pages.design|Design]] | Analysis | Report

> **Summary**: BAR-17에서 미구현된 3개 페이지(Watchlist, Reports, Settings) 추가 구현 및 완성
>
> **Project**: BarroAiTrade Frontend
> **Feature**: BAR-23
> **Author**: Frontend Engineer Agent
> **Date**: 2026-04-11
> **Status**: In Progress

---

## 1. Overview

### 1.1 Purpose

BAR-17 대시보드 프로젝트는 Next.js 15 기반 실시간 트레이딩 UI를 완성했으나,
3개 주요 페이지가 미구현 상태였다:
- **Watchlist** (감시 종목): 종목 필터링 및 파란점선/수박 신호 추적
- **Reports** (리포트): 일일 손익 분석 및 매매 내역 조회
- **Settings** (설정): 리스크 파라미터 및 매매 모드 관리

BAR-23은 이 3개 페이지를 완성하여 프로젝트를 마무리하는 것이 목표이다.

### 1.2 Background

- **Current State**: BAR-17 완료 (Match Rate ~90%, 10 FRs 구현)
  - Next.js 15, React 19, ShadCN UI, Zustand store 구축 완료
  - 대시보드, 트레이딩, 마켓, 포지션 4개 페이지 구현 완료
  - WebSocket 실시간 데이터 파이프라인 확립
  
- **Missing Pages**: BAR-17 계획서에서 제외된 3개 페이지
- **Dependencies**: BAR-21 (REST API Routes) — Mock fallback 사용 가능

---

## 2. Scope

### 2.1 In Scope

- [ ] Watchlist 페이지 구현 (`app/watchlist/page.tsx`)
  - 파란점선(BlueLineDot) 근접 종목 필터
  - 수박신호(Watermelon) 신호 필터
  - 종목별 점수/상태 표시
  - 자동 갱신 (WebSocket 또는 polling)

- [ ] Reports 페이지 구현 (`app/reports/page.tsx`)
  - 날짜 범위 선택기 (달력)
  - 일일 손익률 차트 (Recharts LineChart)
  - 매매 내역 테이블
  - 필터 및 정렬 기능

- [ ] Settings 페이지 구현 (`app/settings/page.tsx`)
  - 리스크 파라미터 폼 (손절, 익절, 일일한도, 최대종목수)
  - 매매 모드 선택 (Simulation/Live)
  - 텔레그램 알림 설정
  - API 연동 (PUT /api/config, PUT /api/risk/limits)

- [ ] 사이드바 네비게이션 업데이트
  - 3개 새 페이지 링크 추가

### 2.2 Out of Scope

- 백엔드 REST API 개발 (BAR-21, 별도 이슈)
- 사용자 인증/인가
- 모바일 앱 (PWA 수준의 반응형만)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Page | Requirement | Priority |
|----|------|-------------|----------|
| FR-11 | Watchlist | 파란점선 근접 종목 필터 | High |
| FR-12 | Watchlist | 수박신호 신호 필터 | High |
| FR-13 | Watchlist | 실시간 자동 갱신 (WebSocket/polling) | High |
| FR-14 | Reports | 날짜 선택기 (DatePicker) | High |
| FR-15 | Reports | 손익률 차트 (LineChart) | High |
| FR-16 | Reports | 매매 내역 테이블 | High |
| FR-17 | Settings | 리스크 파라미터 폼 | High |
| FR-18 | Settings | 매매 모드 선택 (Simulation/Live) | Medium |
| FR-19 | Settings | 텔레그램 알림 설정 | Medium |
| FR-20 | Settings | 설정 저장 (API 호출) | High |
| FR-21 | Navigation | 사이드바 3개 링크 추가 | Medium |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement |
|----------|----------|-------------|
| Performance | 페이지 로딩 < 2s | Lighthouse LCP |
| Responsiveness | 768px ~ 1920px 해상도 지원 | 브라우저 테스트 |
| API Integration | Mock fallback with try/catch | 백엔드 미구현 시 안정성 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] 3개 페이지 정상 렌더링
- [ ] 사이드바 네비게이션 링크 추가
- [ ] 설정 저장 동작 확인
- [ ] `next build` 성공
- [ ] 모든 페이지 모바일 반응형 확인

### 4.2 Quality Criteria

- [ ] TypeScript strict 모드 에러 없음
- [ ] Zero lint errors
- [ ] 컴포넌트 재사용성 검증

---

## 5. Implementation Order

1. **Design 문서 작성** — 3개 페이지 레이아웃 및 컴포넌트 정의
2. **Watchlist 페이지** — 데이터 구조 + 필터 로직 + UI
3. **Reports 페이지** — DatePicker + 차트 + 테이블
4. **Settings 페이지** — 폼 구현 + API 연동
5. **Navigation 업데이트** — 사이드바 링크 추가
6. **통합 테스트** — `next build` + 모든 페이지 동작 확인

---

## 6. Architecture Considerations

### 6.1 Component Structure

```
components/
  watchlist/
    WatchlistTable.tsx        # 메인 테이블
    WatchlistFilter.tsx       # 필터 UI
  reports/
    ReportsDatePicker.tsx     # 날짜 선택
    ReportsPnLChart.tsx       # 손익 차트
    ReportsTradeTable.tsx     # 매매 내역
  settings/
    SettingsRiskForm.tsx      # 리스크 파라미터
    SettingsModeSelect.tsx    # 매매 모드
    SettingsNotification.tsx  # 알림 설정
  shared/
    FormInput.tsx             # 공통 폼 입력
    FormSelect.tsx            # 공통 선택박스
```

### 6.2 Data Flow

```
WebSocket/REST API
    ↓
Zustand Store (watchlist, reports, settings)
    ↓
Hooks (useWatchlist, useReports, useSettings)
    ↓
Page Components
    ↓
UI Components (ShadCN)
```

### 6.3 Key Dependencies

- **ShadCN Form**: react-hook-form + zod for Settings page
- **Recharts**: For PnL chart visualization
- **date-fns**: Date utilities for Reports
- **Zustand**: Store for cross-page data sharing

---

## 7. Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| REST API 미구현 (BAR-21) | High | Mock data with try/catch fallback |
| 차트 라이브러리 성능 | Medium | Dynamic import, 캐싱 활용 |
| 폼 유효성 검증 복잡도 | Low | react-hook-form + zod 사용 |
| 페이지 간 상태 공유 | Medium | Zustand store 중앙화 |

---

## 8. Next Steps

1. [ ] Design 문서 작성
2. [ ] 팀 리뷰
3. 구현 시작 (Design 승인 후)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-11 | Initial plan | Frontend Engineer |
