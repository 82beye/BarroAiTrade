# BAR-17 실시간 대시보드 Design Document

> **Summary**: Next.js 15 + ShadCN UI + Lightweight Charts 기반 실시간 트레이딩 대시보드 상세 설계
>
> **Project**: BarroAiTrade Frontend
> **Version**: 0.1.0
> **Author**: CTO Agent
> **Date**: 2026-04-11
> **Status**: Draft
> **Planning Doc**: [bar-17-dashboard.plan.md](../../01-plan/features/bar-17-dashboard.plan.md)

---

## 1. Overview

### 1.1 Design Goals

- Next.js 15 + React 19 기반으로 프레임워크 업그레이드
- ShadCN UI 컴포넌트 시스템으로 일관된 UI 구축
- WebSocket → Zustand → UI 실시간 데이터 파이프라인 구현
- Lightweight Charts로 트레이딩 전문 차트 제공
- Mock 데이터를 실제 API/WebSocket으로 전��

### 1.2 Design Principles

- **컴포넌트 분리**: 페이지(Page) → 레이아웃(Layout) → 위젯(Widget) → UI(ShadCN) 계층
- **실시간 우선**: WebSocket 데이터를 Zustand 스토어에 집중, 컴포넌트는 선택적 구독
- **점진적 마이그레이션**: 기존 코드 보존하며 단계적 업그레이드

---

## 2. Architecture

### 2.1 Component Diagram

```
┌──────────────────────────────────────────────────────────┐
│                    Next.js 15 App Router                  │
├──────────┬───────────────────────────────────────────────┤
│ Sidebar  │  Page Content                                 │
│ (Layout) │  ┌─────────────────────────────────────────┐  │
│          │  │ Dashboard Page                           │  │
│  [Nav]   │  │  ┌──────────┐ ┌────���─────┐ ┌────────┐  │  │
│  - Home  │  │  │ StatCard │ │ StatCard │ │StatCard│  │  │
│  - Trade │  │  ├──────────┴─┴──────────┴─┴────────┤  │  │
│  - Pos   │  │  │ PriceChart (Lightweight Charts)   │  │  │
│  - Mkt   │  │  ├──────────────────┬─────���──────────┤  │  │
│          │  │  │ TickerTable      │ RecentOrders   │  │  │
│          │  │  └────��─────────────┴─────────────��──┘  │  │
│          │  └──────────────────���──────────────────────┘  │
└──────────┴───────────────────────────────────────────────┘
```

### 2.2 Data Flow

```
Backend FastAPI
  │
  ├── REST API (/api/*) ──────────────────────┐
  │                                            ▼
  │                                     TanStack Query
  │                                     (캐싱/리페칭)
  │                                            │
  └── WebSocket (/ws/realtime) ──┐             │
                                  ▼             ▼
                            useWebSocket   API Response
                                  │             │
                                  ▼             ▼
                            ┌─────────────────────┐
                            │   Zustand Store      │
                            │  - tickers (Map)     │
                            │  - orders (Array)    │
                            │  - balance           │
                            │  - positions         │
                            │  - isConnected       │
                            └─────────┬───────────��
                                      │
                                      ▼
                            React Components
                            (선택적 구독 via selector)
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| Dashboard Page | Zustand Store, useWebSocket | 실시간 데이터 소비 |
| PriceChart | Zustand (tickers), API (OHLCV) | 차트 렌더링 |
| TickerTable | Zustand (tickers) | 실시간 시세 테이블 |
| TradingForm | API (placeOrder), Zustand (orders) | 주문 제출 |
| useWebSocket | WebSocketClient, Zustand Store | WS 메시지 → Store 디스패치 |

---

## 3. Data Model

### 3.1 Entity Definition

```typescript
// ── 시세 (Ticker) ─────────────────────────────────
interface Ticker {
  symbol: string;
  price: number;
  high: number;
  low: number;
  volume: number;
  change: number;        // 추가: 변화율 (%)
  timestamp: string;
}

// ── OHLCV (차트 데이터) ────────────────────────────
interface OHLCV {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// ── 주문 (Order) ──────────────────────────────────
interface Order {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  type: 'MARKET' | 'LIMIT';  // 추가: 주문 유형
  quantity: number;
  price: number;
  status: 'PENDING' | 'FILLED' | 'CANCELED' | 'REJECTED';
  timestamp: string;
}

// ── 포지션 (Position) ──��──────────────────────────
interface Position {
  id: string;
  symbol: string;
  side: 'LONG' | 'SHORT';    // ��가: 포지션 방향
  quantity: number;
  entryPrice: number;
  currentPrice: number;
  pnl: number;
  pnlPercent: number;
  updatedAt: string;          // 추가: 실시간 업데이트 시각
}

// ── 잔고 (Balance) ───────���────────────────────────
interface Balance {
  currency: string;
  free: number;
  locked: number;
  total: number;
}

// ── 시스템 상태 ───────────────────────────────────
interface SystemStatus {
  uptime: number;
  connectedMarkets: MarketType[];
  activeStrategies: number;
  totalCapital: number;
  totalPnl: number;           // 추가: 총 PnL
  timestamp: string;
}

// ── WebSocket 메시지 ──────────────────────────────
type WSMessageType = 'ticker' | 'order' | 'position' | 'balance' | 'status';

interface WSMessage {
  type: WSMessageType;
  data: Ticker | Order | Position | Balance | SystemStatus;
  timestamp: string;
}
```

---

## 4. API Specification

### 4.1 REST Endpoints (Backend FastAPI)

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| GET | `/api/status` | 시스템 상태 조회 | **구현됨** |
| GET | `/api/market/ohlcv` | OHLCV 차트 데이터 | 미���현 |
| GET | `/api/market/ticker/:symbol` | 종목 시세 | 미구현 |
| GET | `/api/market/orderbook/:symbol` | 호가창 | 미구현 |
| GET | `/api/market/universe` | 전종목 목록 | 미구현 |
| GET | `/api/accounts/balance` | 잔고 조회 | 미구현 |
| POST | `/api/trading/order` | 주문 실행 | 미구현 |
| DELETE | `/api/trading/order/:id` | 주문 취소 | 미구현 |
| GET | `/api/positions` | 포지션 목록 | 미구현 |

### 4.2 WebSocket Protocol

| Path | Description | Status |
|------|-------------|--------|
| `/ws/realtime` | 실시간 데이터 스트림 | **구현됨** |

**수신 메시지 형��:**
```json
{
  "type": "ticker",
  "data": {
    "symbol": "AAPL",
    "price": 150.25,
    "change": 2.5,
    "volume": 50000000,
    "high": 151.5,
    "low": 149.0,
    "timestamp": "2026-04-11T10:00:00Z"
  }
}
```

### 4.3 API 미구현 대응 전략

백엔드 REST 라우터가 아직 주석 처리 상태이므로, 프론트엔드에서 다음과 같이 대응:

| 전략 | 적용 대상 | 방법 |
|------|-----------|------|
| **Fallback Mock** | 미구현 API 엔드포인트 | API 클라이언트에서 catch → 로컬 mock 데이��� 반환 |
| **WebSocket 우선** | ticker, position 실시간 데이터 | WS 데이터가 있으면 REST 생략 |
| **Graceful Degradation** | 전체 | API 실패 시 "데이터 없음" 표시 (crash 방지) |

---

## 5. UI/UX Design

### 5.1 Dashboard Page Layout

```
┌────────────────────────────────────────────────────────────┐
│ [StatusBar]  연결: ● 연결됨  |  업타임: 24h  |  시간: HH:MM │
├─────────────────────────────────��──────────────────────────┤
│                                                            │
│  ┌──────────┐ ┌──────────┐ ┌��─────────┐ ┌───��──────┐     │
│  │ 총 자본  │ │ 총 PnL   │ │ 활성전략 │ │ 연결마켓 │     │
│  │ $50,000  │ │ +$1,250  │ │    3     │ │    2     │     │
│  └─────���────┘ └──────────┘ ��──────────┘ └────���─────┘     │
│                                                            │
│  ┌────────────────────────────────────────────────────┐   │
│  │            PriceChart (캔들스틱)                     │   │
│  │  Symbol: [AAPL ▼]  Timeframe: [1H ▼]              │   │
│  │  ┌──────────────────────────────────────────────┐  │   │
│  │  │ ╱╲    ╱╲                                     │  │   ���
│  │  │╱  ╲  ╱  ╲    ╱���                             │  │   │
│  │  │    ╲╱    ╲  ╱  ╲                             │  │   │
│  │  │           ╲╱    ╲                            │  │   │
│  │  └──────────────────────────────────────────────┘  │   │
│  └─────────���──────────────────────────────────────────┘   │
│                                                            │
│  ┌���───────────────────────┐ ┌───���────────────────────┐    │
│  │  실시간 시세 (Ticker)   │ │  최근 주문 (Orders)    ���    │
│  │  AAPL  $150.25  +2.5%  │ │  AAPL BUY 100 FILLED  │    │
│  │  MSFT  $380.50  -1.2%  │ │  MSFT SELL 50 PENDING │    │
│  │  GOOGL $140.75  +0.8%  │ │                        ���    │
│  └─────────────────────���──┘ └───��────────────────────┘    │
└───────��──────────────────────────────────��─────────────────┘
```

### 5.2 Trading Page Layout

```
┌────────────────────────────────────────────────────────────┐
│  트레이딩                                                   │
├──────────────┬─────────────────────────────────────────────┤
│  주문 폼     │  차트 + 호가창                               │
│  ┌────────┐  │  ┌─────────────────────────────────────┐    │
│  │Symbol  │  │  │  PriceChart (확대)                   ���    │
│  │[AAPL▼] │  │  │                                     │    │
│  │Side    │  │  └─────────────────────────────────────┘    │
│  │[BUY▼]  │  │  ┌─────────────────────────────────────┐    │
│  │Type    │  │  │  주문 목록 (테이블)                   │    │
│  │[LIMIT▼]│  │  │  ID | Symbol | Side | Qty | Status  │    │
│  │Qty     │  │  └────────────���────────────────────────┘    │
│  │[___]   │  │                                             │
│  │Price   │  │                                             │
│  │[___]   │  │                                             │
│  │[실행]  │  │                                             │
│  └────────┘  ��                                             │
└──────────────┴─────────��──────────────────────────��────────┘
```

### 5.3 Component List

| Component | Location | Responsibility |
|-----------|----------|----------------|
| **Layout** | | |
| `AppSidebar` | `components/layout/app-sidebar.tsx` | ShadCN Sidebar 기반 네비게이션 |
| `StatusBar` | `components/layout/status-bar.tsx` | 연결 상태, 시스템 정보 표시 |
| **Dashboard Widgets** | | |
| `StatCard` | `components/dashboard/stat-card.tsx` | 핵심 지표 카드 (총자본, PnL 등) |
| `PriceChart` | `components/dashboard/price-chart.tsx` | Lightweight Charts 캔들스틱/라인 |
| `TickerTable` | `components/dashboard/ticker-table.tsx` | 실시간 시세 테이블 |
| `RecentOrders` | `components/dashboard/recent-orders.tsx` | 최근 주문 목록 |
| **Trading** | | |
| `OrderForm` | `components/trading/order-form.tsx` | 주문 폼 (react-hook-form + zod) |
| `OrderTable` | `components/trading/order-table.tsx` | 주문 내역 테이블 |
| **Positions** | | |
| `PositionSummary` | `components/positions/position-summary.tsx` | 포지션 요약 카드들 |
| `PositionTable` | `components/positions/position-table.tsx` | 포지션 상세 테이블 |
| **Markets** | | |
| `MarketTable` | `components/markets/market-table.tsx` | 전종목 시세 테이블 |
| **ShadCN UI** | | |
| `Button, Card, Table, Badge, Select, Input, Form, Sheet, Skeleton, Sonner` | `components/ui/` | ShadCN 기본 컴포넌트 |

---

## 6. Zustand Store 재설계

### 6.1 Store 구조

```typescript
interface TradingStore {
  // 시세 (WebSocket 실시간)
  tickers: Map<string, Ticker>;
  updateTicker: (ticker: Ticker) => void;

  // 주문
  orders: Order[];
  addOrder: (order: Order) => void;
  updateOrder: (orderId: string, update: Partial<Order>) => void;

  // 포지션 (추가)
  positions: Position[];
  setPositions: (positions: Position[]) => void;
  updatePosition: (position: Position) => void;

  // 잔고
  balance: Balance | null;
  setBalance: (balance: Balance) => void;

  // 시스템 상태 (추가)
  systemStatus: SystemStatus | null;
  setSystemStatus: (status: SystemStatus) => void;

  // 연결 상태
  isConnected: boolean;
  setConnected: (connected: boolean) => void;

  // 에러
  error: string | null;
  setError: (error: string | null) => void;
}
```

### 6.2 WebSocket → Store 디스패치

```typescript
// useWebSocket 훅에서 메시지 타입별 Store 업데이트
function dispatchWSMessage(message: WSMessage, store: TradingStore) {
  switch (message.type) {
    case 'ticker':
      store.updateTicker(message.data as Ticker);
      break;
    case 'order':
      store.updateOrder((message.data as Order).id, message.data as Order);
      break;
    case 'position':
      store.updatePosition(message.data as Position);
      break;
    case 'balance':
      store.setBalance(message.data as Balance);
      break;
    case 'status':
      store.setSystemStatus(message.data as SystemStatus);
      break;
  }
}
```

---

## 7. Error Handling

### 7.1 Error Strategy

| 상황 | 처리 방법 | UI 표현 |
|------|-----------|---------|
| API 호출 실패 | catch → Sonner toast | 토스트 알림 |
| WebSocket 연결 끊김 | auto-reconnect + StatusBar 표시 | 빨간 indicator |
| WebSocket 재연결 성공 | StatusBar 복구 | 초록 indicator |
| 주문 실패 | toast error + 폼 유지 | 에러 토스트 |
| 데이터 로딩 중 | Skeleton 컴포넌트 | 스켈레톤 UI |

---

## 8. Security Considerations

- [x] XSS 방지: React 기본 이스케이핑 + ShadCN 컴포넌트 사용
- [ ] WebSocket origin 검증 (백엔드 CORS 설정 확인)
- [x] 환경변수 관리: `.env.local` (gitignore), `.env.example` (공개)
- [ ] Rate Limiting: 주문 API 호출 빈도 제한 (프론트엔드 debounce)

---

## 9. Clean Architecture (Dynamic Level)

### 9.1 Layer Assignment

| Layer | Location | Components |
|-------|----------|------------|
| **Presentation** | `app/`, `components/` | Pages, Widgets, UI Components |
| **Application** | `hooks/` | useWebSocket, useRealtime (비즈니스 로직 훅) |
| **Domain** | `types/`, `lib/store.ts` | 타입 정의, Zustand Store |
| **Infrastructure** | `lib/api.ts` | axios 클라이언트, WebSocket 클라이언트 |

### 9.2 Import Rules

| From | Can Import | Cannot Import |
|------|-----------|---------------|
| `app/` Pages | `components/`, `hooks/`, `lib/store` | `lib/api` 직접 호출 지양 |
| `components/` | `hooks/`, `types/`, `lib/store` | `lib/api` 직접 호출 지양 |
| `hooks/` | `lib/api`, `lib/store`, `types/` | `components/`, `app/` |
| `lib/api` | `types/` | 나머지 전부 |

---

## 10. Coding Convention

### 10.1 Naming

| Target | Rule | Example |
|--------|------|---------|
| Components | PascalCase | `StatCard`, `PriceChart` |
| Hooks | camelCase (use 접두어) | `useWebSocket`, `useRealtime` |
| Files (component) | kebab-case.tsx | `stat-card.tsx`, `price-chart.tsx` |
| Files (utility) | kebab-case.ts | `api.ts`, `store.ts` |
| Folders | kebab-case | `components/dashboard/` |
| Types/Interfaces | PascalCase | `Ticker`, `WSMessage` |

### 10.2 Import Order

```typescript
// 1. React / Next.js
import { useState, useEffect } from 'react';
import Link from 'next/link';

// 2. Third-party
import { useQuery } from '@tanstack/react-query';

// 3. Internal (absolute @/)
import { Card } from '@/components/ui/card';
import { useTradingStore } from '@/lib/store';

// 4. Relative
import { StatCard } from './stat-card';

// 5. Types
import type { Ticker } from '@/types';
```

---

## 11. Implementation Order

### Phase 1: 프레임워크 업그레이드 (FR-01)

```
1. [ ] next@15, react@19, react-dom@19 업그레이드
2. [ ] @types/react@19, @types/react-dom@19 업그레��드
3. [ ] next.config.js → next.config.ts 변환 (선택)
4. [ ] 기존 페이지 빌드 확인 (breaking changes 해결)
```

### Phase 2: ShadCN UI 셋업 (FR-02)

```
1. [ ] npx shadcn@latest init (Tailwind 설정 확���)
2. [ ] 기본 컴포넌트 추가: button, card, table, badge, select, input, form, skeleton, sonner
3. [ ] globals.css 테마 커스터마이징 (다크 테마)
```

### Phase 3: 레이아웃 리팩토링 (FR-09)

```
1. [ ] components/layout/app-sidebar.tsx 생성 (ShadCN Sheet/Nav)
2. [ ] components/layout/status-bar.tsx 생성
3. [ ] app/layout.tsx 리팩토링 (Link, active 상태, ShadCN)
```

### Phase 4: Zustand Store 확장

```
1. [ ] types/index.ts 업데이트 (Position, WSMessage 등)
2. [ ] lib/store.ts 확장 (positions, systemStatus 추가)
3. [ ] hooks/useWebSocket.ts 리팩토링 (WSMessage 디스패치)
```

### Phase 5: 대시보드 위젯 (FR-03, FR-04)

```
1. [ ] components/dashboard/stat-card.tsx
2. [ ] components/dashboard/ticker-table.tsx
3. [ ] components/dashboard/recent-orders.tsx
4. [ ] app/page.tsx 리팩토링 (위젯 조합)
```

### Phase 6: 차트 통합 (FR-05)

```
1. [ ] lightweight-charts 패���지 설치
2. [ ] components/dashboard/price-chart.tsx (dynamic import)
3. [ ] 차트 심볼/타임프레임 셀렉터
```

### Phase 7: 페이지별 API 연동 (FR-06, FR-07, FR-08)

```
1. [ ] components/trading/order-form.tsx (react-hook-form + zod)
2. [ ] components/trading/order-table.tsx
3. [ ] app/trading/page.tsx 리팩토링
4. [ ] components/markets/market-table.tsx + API 연동
5. [ ] app/markets/page.tsx 리팩토링
6. [ ] components/positions/position-summary.tsx
7. [ ] components/positions/position-table.tsx
8. [ ] app/positions/page.tsx 리팩토링
```

### Phase 8: 마무리 (FR-10)

```
1. [ ] WebSocket 연결 상태 UI 피드백 완성
2. [ ] Skeleton 로딩 상태 적��
3. [ ] 반응형 레이아웃 점검
4. [ ] next build 확인
```

---

## 12. Package Dependencies

### 신규 설치

```bash
# Next.js 15 + React 19
npm install next@15 react@19 react-dom@19
npm install -D @types/react@19 @types/react-dom@19

# ShadCN UI (init 후 개별 추가)
npx shadcn@latest init

# TanStack Query (선택 - 미구현 API 대비)
npm install @tanstack/react-query

# Chart
npm install lightweight-charts

# Form
npm install react-hook-form zod @hookform/resolvers

# Toast (ShadCN sonner)
npx shadcn@latest add sonner
```

### 제거 대상

없음 (기존 의존성 모두 유지)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-11 | Initial draft | CTO Agent |
