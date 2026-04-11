---
tags: [design, feature/bar-23, status/in_progress]
---

# BAR-23 Frontend: Watchlist + Reports + Settings 페이지 Design Document

> **관련 Plan**: [[../01-plan/features/bar-23-frontend-pages.plan|Plan]]

> **Summary**: 3개 페이지의 상세 레이아웃, 컴포넌트 구조, API 스펙
>
> **Project**: BarroAiTrade Frontend
> **Feature**: BAR-23
> **Author**: Frontend Engineer Agent
> **Date**: 2026-04-11
> **Status**: In Progress

---

## 1. Page Structure & Layouts

### 1.1 Watchlist Page (`app/watchlist/page.tsx`)

#### Layout
```
┌─────────────────────────────────────────────────────┐
│ Header: "감시 종목" + 새로고침 버튼                    │
├─────────────────────────────────────────────────────┤
│ Filter Bar:                                         │
│  [파란점선 근접 ◄► ][수박신호 ◄► ][전체 보기 ◄► ]     │
│  검색창: 종목코드/종목명                               │
├─────────────────────────────────────────────────────┤
│                                                     │
│ WatchlistTable:                                     │
│ ┌──────────────────────────────────────────────┐   │
│ │ 종목코드 │ 종목명 │ 파란점선 │ 수박신호 │ 점수 │ 현재가 │   │
│ ├──────────────────────────────────────────────┤   │
│ │ AAPL   │ Apple │  ●●●  │    ✓    │ 95 │ $185 │   │
│ │ MSFT   │ MS    │  ●●●  │        │ 87 │ $420 │   │
│ └──────────────────────────────────────────────┘   │
│ (자동 갱신: WebSocket 또는 polling)                   │
│                                                     │
└─────────────────────────────────────────────────────┘
```

#### Components
| Component | Purpose | Dependencies |
|-----------|---------|:-------------:|
| **WatchlistPage** | 페이지 최상위 | Zustand store |
| **WatchlistFilter** | 필터 UI (드롭다운) | ShadCN Select |
| **WatchlistTable** | 데이터 테이블 | ShadCN Table |
| **WatchlistRow** | 행 렌더링 | 재사용 가능 |

#### Data Structure
```typescript
interface Watchlist {
  code: string;           // 종목코드 (e.g., "AAPL")
  name: string;           // 종목명
  price: number;          // 현재가
  blueLineDot: number;    // 파란점선 신호 강도 (0-100)
  watermelon: boolean;    // 수박신호 활성화 여부
  score: number;          // 종합 점수 (0-100)
  updatedAt: timestamp;   // 마지막 업데이트
}
```

#### Filter Logic
- **파란점선 근접**: `blueLineDot > 80`인 종목만 표시
- **수박신호**: `watermelon === true`인 종목만 표시
- **전체 보기**: 필터 없음

#### API Integration (Mock fallback)
- **GET /api/watchlist** → Mock data if failed
- **Real-time updates**: WebSocket `watchlist:update`

---

### 1.2 Reports Page (`app/reports/page.tsx`)

#### Layout
```
┌─────────────────────────────────────────────────────┐
│ Header: "리포트" + 날짜 선택기                         │
│ [◄ 2026-04-10 ►] 단위: [일일 ▼] [주간 ▼] [월간 ▼]    │
├─────────────────────────────────────────────────────┤
│                                                     │
│ Summary Cards:                                      │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│ │ 일일 수익 │ │ 수익률   │ │ 최대낙폭 │             │
│ │ +$1,250  │ │ +2.15%   │ │ -$890   │             │
│ └──────────┘ └──────────┘ └──────────┘             │
│                                                     │
│ PnL Chart (Recharts LineChart):                    │
│ ┌─────────────────────────────────────┐            │
│ │    손익률 추이                          │            │
│ │    3%  ╱╲╱╲                          │            │
│ │    0%──┴──┴─────                     │            │
│ │   -1%      └──                       │            │
│ └─────────────────────────────────────┘            │
│                                                     │
│ Trade History Table:                                │
│ ┌──────────────────────────────────────┐           │
│ │ 시간 │ 종목 │ 주문타입 │ 수량 │ 진입가 │ 청산가 │ 수익률 │           │
│ ├──────────────────────────────────────┤           │
│ │ 14:05 │ AAPL │  매수  │ 10 │ 185.2 │ 186.5 │ +0.7% │           │
│ └──────────────────────────────────────┘           │
│                                                     │
└─────────────────────────────────────────────────────┘
```

#### Components
| Component | Purpose |
|-----------|---------|
| **ReportsPage** | 페이지 최상위 |
| **ReportsDatePicker** | 날짜 선택 (react-day-picker) |
| **ReportsSummary** | 요약 카드 (StatCard ×3) |
| **ReportsPnLChart** | Recharts LineChart |
| **ReportsTradeTable** | 매매 내역 테이블 |

#### Data Structure
```typescript
interface DailyReport {
  date: string;           // "2026-04-11"
  totalPnL: number;       // 총 수익 ($)
  pnlRatio: number;       // 수익률 (%)
  maxDrawdown: number;    // 최대낙폭 ($)
  tradeCount: number;     // 매매 건수
  winRate: number;        // 승률 (%)
}

interface TradeRecord {
  id: string;
  timestamp: string;      // "2026-04-11T14:05:00Z"
  symbol: string;         // "AAPL"
  orderType: 'BUY' | 'SELL';
  quantity: number;
  entryPrice: number;
  exitPrice?: number;
  pnl?: number;           // 수익금액
  pnlRatio?: number;      // 수익률 (%)
}
```

#### API Integration (Mock fallback)
- **GET /api/reports?date=YYYY-MM-DD** → Daily report
- **GET /api/trades?startDate=...&endDate=...** → Trade history

---

### 1.3 Settings Page (`app/settings/page.tsx`)

#### Layout
```
┌─────────────────────────────────────────────────────┐
│ Header: "설정"                                       │
├─────────────────────────────────────────────────────┤
│                                                     │
│ Section 1: 리스크 파라미터                            │
│ ┌─────────────────────────────────────┐             │
│ │ 손절(Stop Loss)      [5.0 %]        │             │
│ │ 익절(Take Profit)    [10.0 %]       │             │
│ │ 일일 한도(Daily Limit) [$5,000]     │             │
│ │ 최대 종목수          [5]             │             │
│ └─────────────────────────────────────┘             │
│                                                     │
│ Section 2: 매매 모드                                │
│ ┌─────────────────────────────────────┐             │
│ │ ◯ Simulation (테스트)                │             │
│ │ ◉ Live (실시간)                      │             │
│ └─────────────────────────────────────┘             │
│                                                     │
│ Section 3: 알림 설정                                │
│ ┌─────────────────────────────────────┐             │
│ │ 텔레그램 알림 활성화  [Toggle ON]     │             │
│ │ 텔레그램 Chat ID     [1234567890]    │             │
│ │ 알림 종류: ☑ 주문 ☑ 청산 ☑ 손절  │             │
│ └─────────────────────────────────────┘             │
│                                                     │
│ [저장하기] [초기화]                                 │
│                                                     │
└─────────────────────────────────────────────────────┘
```

#### Components
| Component | Purpose |
|-----------|---------|
| **SettingsPage** | 페이지 최상위 |
| **SettingsRiskForm** | 리스크 파라미터 (ShadCN Form) |
| **SettingsModeSelect** | 매매 모드 선택 |
| **SettingsNotification** | 알림 설정 |
| **SettingsActions** | 저장/초기화 버튼 |

#### Data Structure
```typescript
interface Settings {
  // Risk Parameters
  stopLoss: number;           // (%) 기본값: 5.0
  takeProfit: number;         // (%) 기본값: 10.0
  dailyLimit: number;         // ($) 기본값: 5000
  maxSymbols: number;         // 기본값: 5

  // Mode
  mode: 'SIMULATION' | 'LIVE'; // 기본값: SIMULATION

  // Notification
  telegramEnabled: boolean;
  telegramChatId?: string;
  notifyOnOrder: boolean;
  notifyOnClose: boolean;
  notifyOnStopLoss: boolean;
}
```

#### Form Validation (Zod Schema)
```typescript
const SettingsSchema = z.object({
  stopLoss: z.number().min(0.1).max(50),
  takeProfit: z.number().min(0.1).max(100),
  dailyLimit: z.number().min(100).max(1000000),
  maxSymbols: z.number().min(1).max(20),
  mode: z.enum(['SIMULATION', 'LIVE']),
  telegramChatId: z.string().optional(),
});
```

#### API Integration
- **PUT /api/config** → Save settings
- **PUT /api/risk/limits** → Save risk parameters
- **GET /api/settings** → Load current settings (Mock fallback)

---

## 2. Component Architecture

### 2.1 Reusable UI Components

```
components/
  shared/
    FormInput.tsx          # Text input wrapper (ShadCN)
    FormSelect.tsx         # Select wrapper (ShadCN)
    FormCheckbox.tsx       # Checkbox wrapper
    StatCard.tsx           # Summary stat display
    PageHeader.tsx         # Page title + actions
```

### 2.2 Page-Specific Components

```
components/
  watchlist/
    WatchlistPage.tsx      # Main page container
    WatchlistFilter.tsx    # Filter controls
    WatchlistTable.tsx     # Data table
  
  reports/
    ReportsPage.tsx        # Main page container
    ReportsDatePicker.tsx  # DatePicker wrapper
    ReportsSummary.tsx     # 3 stat cards
    ReportsPnLChart.tsx    # LineChart visualization
    ReportsTradeTable.tsx  # Trade history
  
  settings/
    SettingsPage.tsx       # Main page container
    SettingsRiskForm.tsx   # Risk parameter form
    SettingsModeRadio.tsx  # Mode selection
    SettingsNotify.tsx     # Notification settings
    SettingsActions.tsx    # Save/Reset buttons
```

---

## 3. Styling & Theming

### 3.1 Color Palette (Existing ShadCN)
- **Primary**: Indigo-600 (BAR-17)
- **Success**: Green-600 (profit)
- **Danger**: Red-600 (loss/stop-loss)
- **Neutral**: Gray-500 (neutral)

### 3.2 Responsive Breakpoints (Tailwind)
- **Mobile**: < 640px (sm:)
- **Tablet**: 640px ~ 1024px (md:, lg:)
- **Desktop**: > 1024px (xl:)

---

## 4. Implementation Dependencies

### 4.1 Already Installed
- ✅ Next.js 15, React 19
- ✅ ShadCN UI (Button, Card, Input, Select, etc.)
- ✅ Zustand
- ✅ Tailwind CSS
- ✅ react-hook-form
- ✅ zod

### 4.2 To Install
- `recharts` — Line chart for PnL visualization
- `react-day-picker` — DatePicker component (or use ShadCN Popover)

---

## 5. Integration Points

### 5.1 Zustand Store Updates
```typescript
// Add to existing store (lib/store.ts)
interface WatchlistState {
  watchlist: Watchlist[];
  setWatchlist: (data: Watchlist[]) => void;
  setFilter: (filter: FilterType) => void;
}

interface ReportsState {
  reports: DailyReport[];
  trades: TradeRecord[];
  setReports: (data: DailyReport[]) => void;
  setTrades: (data: TradeRecord[]) => void;
}

interface SettingsState {
  settings: Settings;
  updateSettings: (data: Partial<Settings>) => void;
  saveSettings: () => Promise<void>;
}
```

### 5.2 API Integration Layer
```typescript
// lib/api.ts additions
export const watchlistAPI = {
  fetch: async (filter?: string) => {},
};

export const reportsAPI = {
  fetch: async (date: string) => {},
  trades: async (startDate: string, endDate: string) => {},
};

export const settingsAPI = {
  fetch: async () => {},
  save: async (settings: Settings) => {},
};
```

---

## 6. Implementation Order

### Phase 1: Foundation
1. Install missing dependencies (recharts, react-day-picker)
2. Update Zustand store with new state slices
3. Create shared components (FormInput, StatCard, etc.)

### Phase 2: Watchlist Page
1. Create `app/watchlist/page.tsx` layout
2. Build WatchlistFilter component
3. Build WatchlistTable component
4. Integrate with Zustand store
5. Add WebSocket auto-update

### Phase 3: Reports Page
1. Create `app/reports/page.tsx` layout
2. Build ReportsDatePicker component
3. Build ReportsSummary (StatCards)
4. Build ReportsPnLChart (Recharts LineChart)
5. Build ReportsTradeTable component
6. API integration (with mock fallback)

### Phase 4: Settings Page
1. Create `app/settings/page.tsx` layout
2. Build SettingsRiskForm with react-hook-form + zod
3. Build SettingsModeRadio component
4. Build SettingsNotify component
5. API integration (save settings)

### Phase 5: Navigation & Polish
1. Update Sidebar/Navigation with 3 new links
2. Test all pages responsiveness
3. Verify `next build` success
4. Final QA

---

## 7. Success Criteria (Design)

- [ ] All 3 pages layout designed
- [ ] Component hierarchy finalized
- [ ] Zustand store structure defined
- [ ] API contracts defined (with mock fallback)
- [ ] Dependencies listed
- [ ] Responsive design confirmed

---

## Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | 2026-04-11 | Frontend Engineer | Initial design |
