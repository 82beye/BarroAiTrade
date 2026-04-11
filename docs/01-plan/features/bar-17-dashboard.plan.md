# BAR-17 실시간 대시보드 Planning Document

> **Summary**: Next.js 15 기반 실시간 트레이딩 대시보드 구현 (WebSocket + 차트 + 컴포넌트 시스템)
>
> **Project**: BarroAiTrade Frontend
> **Version**: 0.1.0
> **Author**: CTO Agent
> **Date**: 2026-04-11
> **Status**: Draft

---

## 1. Overview

### 1.1 Purpose

현재 BarroAiTrade 프론트엔드는 Next.js 14 + Mock 데이터 기반의 프로토타입 상태이다.
BAR-17 이슈는 이를 Next.js 15 기반의 **실시간 대시보드**로 업그레이드하여,
백엔드 WebSocket/REST API와 연동된 프로덕션 수준의 트레이딩 UI를 구현하는 것을 목표로 한다.

### 1.2 Background

- 프론트엔드: Next.js 14, React 18, 4개 페이지 존재 (Dashboard, Trading, Markets, Positions)
- 백엔드: FastAPI + WebSocket (`/ws/realtime`, `/api/status` 만 활성)
- Markets/Positions 페이지: Mock 데이터 사용 (TODO 주석)
- `components/` 디렉토리: 비어 있음 (재사용 컴포넌트 없음)
- WebSocket 클라이언트: 기본 구현 존재 (reconnect 포함)
- Zustand 스토어: Ticker, Order, Balance 타입 정의됨

### 1.3 Related Documents

- Issue: Paperclip BAR-17
- Backend: `/backend/main.py` (FastAPI 진입점)
- Existing Frontend: `/frontend/` (Next.js 14 프로토타입)

---

## 2. Scope

### 2.1 In Scope

- [ ] Next.js 14 → 15 + React 19 업그레이드
- [ ] ShadCN UI 컴포넌트 시스템 도입
- [ ] 실시간 대시보드 (WebSocket 기반 시세, 포지션, PnL)
- [ ] 차트 라이브러리 통합 (가격 차트, PnL 차트)
- [ ] Mock 데이터 → 실제 API 연동 전환
- [ ] 반응형 레이아웃 (모바일/태블릿/데스크톱)
- [ ] 에러/로딩 상태 개선

### 2.2 Out of Scope

- 백엔드 API 개발 (별도 이슈)
- 사용자 인증/인가
- 전략 관리 UI (별도 feature)
- 모바일 앱 (PWA 수준의 반응형만)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | Next.js 15 + React 19 마이그레이션 | High | Pending |
| FR-02 | ShadCN UI 컴포넌트 시스템 셋업 | High | Pending |
| FR-03 | 실시간 시세 대시보드 (WebSocket → Ticker 표시) | High | Pending |
| FR-04 | 실시간 포지션 PnL 업데이트 | High | Pending |
| FR-05 | 가격 차트 (OHLCV 캔들스틱 또는 라인 차트) | High | Pending |
| FR-06 | 주문 폼 리팩토링 (ShadCN Form + 유효성 검증) | Medium | Pending |
| FR-07 | 마켓 데이터 페이지 — 실제 API 연동 | Medium | Pending |
| FR-08 | 포지션 페이지 — 실제 API 연동 | Medium | Pending |
| FR-09 | 사이드바 네비게이션 개선 (Next.js Link + active 상태) | Medium | Pending |
| FR-10 | WebSocket 연결 상태 indicator + auto-reconnect UI 피드백 | Low | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| Performance | WebSocket 메시지 → UI 반영 < 100ms | Chrome DevTools Performance |
| Performance | 초기 로딩 (LCP) < 2s | Lighthouse |
| Responsiveness | 768px ~ 1920px 해상도 지원 | 브라우저 리사이즈 테스트 |
| Accessibility | 키보드 네비게이션, 색상 대비 | ShadCN 기본 지원 활용 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] Next.js 15 + React 19 정상 빌드
- [ ] 모든 페이지 실시간 데이터 표시 (Mock 제거)
- [ ] ShadCN 기반 컴포넌트로 UI 통일
- [ ] WebSocket 연결/해제/재연결 정상 동작
- [ ] 차트 렌더링 정상 동작
- [ ] 반응형 레이아웃 동작 확인

### 4.2 Quality Criteria

- [ ] TypeScript strict 모드 에러 없음
- [ ] `next build` 성공
- [ ] Zero lint errors

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Next.js 15 breaking changes (App Router 변경) | High | Medium | 공식 마이그레이션 가이드 따름, 단계적 업그레이드 |
| React 19 호환성 이슈 (zustand, axios 등) | Medium | Medium | 의존성 호환성 사전 확인 후 업그레이드 |
| 백엔드 REST API 미구현 (routes 주석 처리됨) | High | High | API 미구현 엔드포인트는 MSW 또는 fallback mock 사용 |
| WebSocket 메시지 대량 수신 시 렌더링 성능 | Medium | Medium | throttle/debounce 적용, React.memo 활용 |
| 차트 라이브러리 번들 사이즈 | Low | Medium | dynamic import (next/dynamic) lazy loading |

---

## 6. Architecture Considerations

### 6.1 Project Level Selection

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| **Starter** | Simple structure | Static sites, portfolios | |
| **Dynamic** | Feature-based modules, BaaS integration | Web apps with backend, SaaS MVPs | **V** |
| **Enterprise** | Strict layer separation, DI, microservices | High-traffic systems | |

### 6.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| Framework | Next.js 14 / Next.js 15 | **Next.js 15** | BAR-17 요구사항, React 19 서버 컴포넌트 활용 |
| State Management | Context / Zustand / Redux | **Zustand** | 이미 도입됨, 경량, WebSocket 실시간 업데이트에 적합 |
| API Client | fetch / axios / react-query | **axios + TanStack Query** | axios 기존 사용 + 캐싱/리페칭 자동화 |
| UI Components | Tailwind only / ShadCN / Radix | **ShadCN UI** | 접근성 내장, Tailwind 호환, 빠른 개발 |
| Chart | Chart.js / Recharts / Lightweight Charts | **Lightweight Charts** | 트레이딩 전용, 캔들스틱 지원, 경량 |
| Styling | Tailwind CSS | **Tailwind CSS v3** | 기존 사용, ShadCN 호환 |
| Form | native / react-hook-form | **react-hook-form + zod** | ShadCN Form 통합, 스키마 기반 유효성 검증 |

### 6.3 Clean Architecture Approach

```
Selected Level: Dynamic

Folder Structure (Target):
┌─────────────────────────────────────────────────────┐
│ frontend/                                           │
│   app/                    # Next.js 15 App Router   │
│     (dashboard)/          # 대시보드 route group     │
│     trading/              # 트레이딩 페이지           │
│     markets/              # 마켓 데이터              │
│     positions/            # 포지션 관리              │
│     layout.tsx            # 루트 레이아웃            │
│   components/                                       │
│     ui/                   # ShadCN UI 컴포넌트       │
│     dashboard/            # 대시보드 전용 컴포넌트    │
│     trading/              # 트레이딩 전용 컴포넌트    │
│     layout/               # 레이아웃 (Sidebar, Nav)  │
│   hooks/                  # 커스텀 훅                │
│     useWebSocket.ts       # WebSocket 훅 (개선)     │
│     useRealtime.ts        # 실시간 데이터 구독 훅    │
│   lib/                    # 유틸리티                 │
│     api.ts                # API 클라이언트           │
│     store.ts              # Zustand 스토어           │
│     utils.ts              # 공통 유틸               │
│   types/                  # 타입 정의               │
│     index.ts              # 공통 타입               │
└─────────────────────────────────────────────────────┘
```

---

## 7. Convention Prerequisites

### 7.1 Existing Project Conventions

- [ ] `CLAUDE.md` has coding conventions section
- [x] TypeScript configuration (`tsconfig.json`)
- [x] Tailwind CSS configuration (`tailwind.config.js`)
- [ ] ESLint configuration (`.eslintrc.*`) — 없음
- [ ] Prettier configuration (`.prettierrc`) — 없음

### 7.2 Conventions to Define/Verify

| Category | Current State | To Define | Priority |
|----------|---------------|-----------|:--------:|
| **Naming** | 혼재 (camelCase + kebab-case) | 파일: kebab-case, 컴포넌트: PascalCase | High |
| **Folder structure** | 기본 구조만 | Feature 기반 components/ 구조 | High |
| **Import order** | 없음 | React → Next → 3rd party → local | Medium |
| **Error handling** | console.error + alert | Toast 기반 에러 표시 | Medium |

### 7.3 Environment Variables Needed

| Variable | Purpose | Scope | To Be Created |
|----------|---------|-------|:-------------:|
| `NEXT_PUBLIC_API_URL` | Backend API 엔드포인트 | Client | 있음 (.env.example) |
| `NEXT_PUBLIC_WS_URL` | WebSocket 엔드포인트 (분리 시) | Client | V |

### 7.4 Pipeline Integration

| Phase | Status | Document Location |
|-------|:------:|-------------------|
| Phase 1 (Schema) | - | `docs/01-plan/schema.md` |
| Phase 2 (Convention) | - | `docs/01-plan/conventions.md` |

---

## 8. Implementation Order (Preview)

구현 순서 제안 (Design 문서에서 상세화):

1. **Next.js 15 마이그레이션** — 프레임워크 업그레이드 + 빌드 확인
2. **ShadCN UI 셋업** — 컴포넌트 시스템 기반 구축
3. **레이아웃 리팩토링** — Sidebar → ShadCN 기반, Next.js Link 적용
4. **대시보드 실시간 위젯** — WebSocket → Zustand → UI 파이프라인
5. **차트 통합** — Lightweight Charts 캔들스틱/라인 차트
6. **페이지별 API 연동** — Markets, Positions, Trading 순차 연동
7. **에러/로딩 상태 개선** — Skeleton, Toast, 연결 상태 indicator

---

## 9. Next Steps

1. [ ] Design 문서 작성 (`bar-17-dashboard.design.md`)
2. [ ] 팀 리뷰 및 승인
3. [ ] 구현 시작

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-11 | Initial draft | CTO Agent |
