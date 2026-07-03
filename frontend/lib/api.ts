/**
 * API 클라이언트 - Backend와의 통신
 */
import axios from 'axios';

// 브라우저에서는 상대경로(Next.js rewrite 프록시), SSR/외부 지정 시 절대경로
const API_URL =
  typeof window !== 'undefined'
    ? ''
    : process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const apiClient = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// WebSocket 클라이언트
export class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private manuallyClosed = false;
  // listeners persist across reconnects
  private storedListeners: Map<string, Array<(data: any) => void>> = new Map();

  constructor(path: string = '/ws/realtime') {
    if (typeof window !== 'undefined' && !API_URL) {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      this.url = `${proto}//${window.location.host}${path}`;
    } else {
      this.url = `${(API_URL || 'http://localhost:8000').replace('http', 'ws')}${path}`;
    }
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.manuallyClosed = false;
        this.ws = new WebSocket(this.url);

        // Re-attach all stored listeners onto the new ws instance
        this.storedListeners.forEach((callbacks, event) => {
          callbacks.forEach((cb) => {
            this.ws!.addEventListener(event, (e: Event) => cb((e as MessageEvent).data));
          });
        });

        this.ws.onopen = () => {
          console.log('[WS] Connected');
          this.reconnectAttempts = 0;
          resolve();
        };

        this.ws.onerror = (error) => {
          console.error('[WS] Error:', error);
          reject(error);
        };

        this.ws.onclose = () => {
          console.log('[WS] Disconnected');
          if (!this.manuallyClosed) this.attemptReconnect();
        };
      } catch (error) {
        reject(error);
      }
    });
  }

  private attemptReconnect() {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++;
      const delay = Math.pow(2, this.reconnectAttempts) * 1000;
      console.log(`[WS] Reconnecting in ${delay}ms...`);
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect().catch(() => {
          // reconnect failed; next attempt scheduled by onclose
        });
      }, delay);
    }
  }

  send(data: unknown): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    } else {
      console.warn('[WS] WebSocket not connected');
    }
  }

  on(event: 'message' | 'error' | 'close' | 'open', callback: (data: any) => void): void {
    if (!this.storedListeners.has(event)) this.storedListeners.set(event, []);
    this.storedListeners.get(event)!.push(callback);
    // also attach to current ws if already open
    if (this.ws) {
      this.ws.addEventListener(event, (e: Event) => callback((e as MessageEvent).data));
    }
  }

  close(): void {
    this.manuallyClosed = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.storedListeners.clear();
  }
}

// API 메서드
export const api = {
  // 상태 조회
  getStatus: () => apiClient.get('/api/status'),

  // 시장 데이터
  getOHLCV: (symbol: string, timeframe: string, limit?: number) =>
    apiClient.get(`/api/market/ohlcv`, { params: { symbol, timeframe, limit } }),

  getTicker: (symbol: string) =>
    apiClient.get(`/api/market/ticker/${symbol}`),

  getOrderBook: (symbol: string) =>
    apiClient.get(`/api/market/order-book/${symbol}`),

  // 계좌 정보
  getBalance: () => apiClient.get('/api/accounts/balance'),

  // 주문
  placeOrder: (order: any) => apiClient.post('/api/trading/order', order),

  cancelOrder: (orderId: string) =>
    apiClient.delete(`/api/trading/order/${orderId}`),

  getOrderStatus: (orderId: string) =>
    apiClient.get(`/api/trading/order/${orderId}`),

  // 포지션
  getPositions: () => apiClient.get('/api/positions'),

  // 시장 전종목
  getUniverse: () => apiClient.get('/api/market/universe'),

  // ── 티마(TIMA) 스크리너 / 테마 / 차트 기준선 ──
  getScreenerStrategies: () => apiClient.get('/api/screener/strategies'),

  getScreener: (strategy: string, symbols?: string, limit?: number) =>
    apiClient.get(`/api/screener/${strategy}`, { params: { symbols, limit } }),

  getChartLevels: (symbol: string) =>
    apiClient.get('/api/chart/levels', { params: { symbol } }),

  getThemes: () => apiClient.get('/api/themes'),

  getThemeStocks: (id: number | string) =>
    apiClient.get(`/api/themes/${id}/stocks`),

  // ── 티마 P1 — 알림센터 / 스냅숏 / 종목상세 / 티커 ──
  getAlertsHistory: (strategy?: string, limit?: number) =>
    apiClient.get('/api/alerts/history', { params: { strategy, limit } }),

  getAlertSettings: () => apiClient.get('/api/alerts/settings'),

  updateAlertSettings: (patch: Partial<AlertSettings>) =>
    apiClient.put('/api/alerts/settings', patch),

  getThemeSnapshots: (date?: string, slot?: string) =>
    apiClient.get('/api/themes/snapshots', { params: { date, slot } }),

  getStockThemes: (symbol: string) =>
    apiClient.get(`/api/stocks/${symbol}/themes`),

  getMarketIndices: () => apiClient.get('/api/market/indices'),

  getRecentNews: (limit?: number) =>
    apiClient.get('/api/news/recent', { params: { limit } }),

  getCalendarBySymbol: (symbol: string) =>
    apiClient.get(`/api/calendar/symbol/${symbol}`),

  // 기간 지정 마켓일정 (일/주/월 뷰 공용)
  getCalendar: (start: string, end: string) =>
    apiClient.get('/api/calendar', { params: { start, end } }),

  // ── 티마 P2 — 통합검색 / NXT 애프터마켓 / 호가 ──
  search: (q: string, limit = 10) =>
    apiClient.get('/api/search', { params: { q, limit } }),

  getNxt: (filter: 'value' | 'gainers' | 'losers' = 'value', limit = 30) =>
    apiClient.get('/api/market/nxt', { params: { filter, limit } }),
};

// ── 티마 공용 타입 ──
export interface StrategyLevel {
  label: string; // SF, B1, B2, B3, G1..G3, J1..J3
  price: number;
  kind: 'support' | 'target' | 'anchor';
  active: boolean;
  reached_at?: string | null; // 도달 시각 (SF존 등) — 하위호환
  d_offset?: number | null; // 포착일 기준 상대일 (D+N) — 하위호환
}

export interface ScreenerItem {
  symbol: string;
  name?: string | null;
  detected_at?: string | null;
  price: number;
  change_pct?: number | null;
  value_traded?: number | null; // 억원
  market_cap?: number | null; // 억원
  score: number;
  reason?: string;
  levels: StrategyLevel[];
}

export interface ScreenerResponse {
  strategy: string;
  generated_at?: string;
  count: number;
  status: string;
  disclaimer?: string;
  items: ScreenerItem[];
}

export interface StrategyMeta {
  key: string;
  label: string;
}

export interface ThemeStockItem {
  symbol: string;
  score: number;
  theme_id: number;
  theme_name?: string | null;
  name?: string | null;
  price?: number | null;
  change_pct?: number | null;
  day_open?: number | null;
  day_high?: number | null;
  day_low?: number | null;
  value_traded?: number | null; // 억원
}

// ── 티마 P1 타입 ──
export interface AlertItem {
  id: number | string;
  strategy: string; // f_zone / sf_zone / gold_zone / swing_38
  symbol: string;
  name?: string | null;
  message: string;
  level_label?: string | null;
  occurred_at: string;
}

export interface AlertHistoryResponse {
  items: AlertItem[];
  count: number;
  status: string;
}

export interface AlertSettings {
  f_zone: boolean;
  sf_zone: boolean;
  gold_zone: boolean;
  swing_38: boolean;
}

export interface SnapshotTheme {
  id: number;
  name: string;
  description?: string | null;
  stocks: ThemeStockItem[];
}

export interface ThemeSnapshot {
  date: string;
  slot: string;
  captured_at?: string | null;
  themes: SnapshotTheme[];
}

export interface ThemeSnapshotSlotList {
  date: string;
  slots: string[];
  status?: string;
}

export interface StockTheme {
  id: number;
  name: string;
  description?: string | null;
  score?: number | null;
}

export interface StockThemesResponse {
  symbol: string;
  themes: StockTheme[];
}

export interface MarketIndex {
  code: string;
  name: string;
  value: number;
  change: number;
  change_pct: number;
}

export interface MarketIndicesResponse {
  items: MarketIndex[];
  status: string;
}

export interface NewsItem {
  id: number | string;
  source: string;
  source_id?: string;
  title: string;
  url: string;
  published_at: string;
  tags?: string[];
}

// ── 티마 P2 타입 ──
export interface CalendarEvent {
  id: number | string;
  event_type: string; // theme / individual / policy / …
  symbol?: string | null;
  event_date: string; // YYYY-MM-DD
  title: string;
  source?: string;
}

export type SearchResult =
  | { type: 'stock'; symbol: string; name: string }
  | { type: 'theme'; id: number | string; name: string };

export interface SearchResponse {
  query: string;
  results: SearchResult[];
}

export interface NxtItem {
  symbol: string;
  name?: string | null;
  nxt_price?: number | null; // NXT 현재가
  vs_close_pct?: number | null; // 종가 대비 %
  day_close?: number | null; // 당일 종가
  day_change_pct?: number | null; // 당일 등락률
  aft_value?: number | null; // 애프터 거래대금 (억)
  cum_value?: number | null; // 누적 거래대금 (억)
}

export interface NxtResponse {
  items: NxtItem[];
  status: 'ok' | 'unsupported' | 'not_ready' | string;
}
