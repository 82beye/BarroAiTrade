/**
 * Zustand 전역 상태 관리
 * Design: bar-17-dashboard
 */
import { create } from 'zustand';

// ── Types ──────────────────────────────────────────────────────────────
export interface Ticker {
  symbol: string;
  price: number;
  high: number;
  low: number;
  volume: number;
  change: number;
  timestamp: string;
}

export interface Order {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  type: 'MARKET' | 'LIMIT';
  quantity: number;
  price: number;
  status: 'PENDING' | 'FILLED' | 'CANCELED' | 'REJECTED';
  timestamp: string;
}

export interface Position {
  id: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  quantity: number;
  entryPrice: number;
  currentPrice: number;
  pnl: number;
  pnlPercent: number;
  updatedAt: string;
}

export interface Balance {
  currency: string;
  free: number;
  locked: number;
  total: number;
}

export interface SystemStatus {
  uptime: number;
  connectedMarkets: string[];
  activeStrategies: number;
  totalCapital: number;
  totalPnl: number;
  timestamp: string;
}

export interface WSMessage {
  type: 'ticker' | 'order' | 'position' | 'balance' | 'status';
  data: Ticker | Order | Position | Balance | SystemStatus;
  timestamp: string;
}

// ── Store Interface ────────────────────────────────────────────────────────
interface TradingStore {
  // 시장 데이터 (WebSocket 실시간)
  tickers: Map<string, Ticker>;
  updateTicker: (ticker: Ticker) => void;
  getTicker: (symbol: string) => Ticker | undefined;

  // 주문
  orders: Order[];
  addOrder: (order: Order) => void;
  updateOrder: (orderId: string, update: Partial<Order>) => void;
  getOrders: (symbol?: string) => Order[];

  // 포지션
  positions: Position[];
  setPositions: (positions: Position[]) => void;
  updatePosition: (position: Position) => void;
  getPositions: (symbol?: string) => Position[];

  // 잔고
  balance: Balance | null;
  setBalance: (balance: Balance) => void;

  // 시스템 상태
  systemStatus: SystemStatus | null;
  setSystemStatus: (status: SystemStatus) => void;

  // 연결 상태
  isConnected: boolean;
  setConnected: (connected: boolean) => void;

  // 에러
  error: string | null;
  setError: (error: string | null) => void;

  // WebSocket 메시지 디스패칭
  dispatchWSMessage: (message: WSMessage) => void;
}

// ── Store Creation ─────────────────────────────────────────────────────────
export const useTradingStore = create<TradingStore>((set, get) => ({
  // 시세
  tickers: new Map(),
  updateTicker: (ticker) =>
    set((state) => {
      const newTickers = new Map(state.tickers);
      newTickers.set(ticker.symbol, ticker);
      return { tickers: newTickers };
    }),
  getTicker: (symbol) => get().tickers.get(symbol),

  // 주문
  orders: [],
  addOrder: (order) =>
    set((state) => ({
      orders: [...state.orders, order],
    })),
  updateOrder: (orderId, update) =>
    set((state) => ({
      orders: state.orders.map((o) =>
        o.id === orderId ? { ...o, ...update } : o
      ),
    })),
  getOrders: (symbol) =>
    symbol ? get().orders.filter((o) => o.symbol === symbol) : get().orders,

  // 포지션
  positions: [],
  setPositions: (positions) => set({ positions }),
  updatePosition: (position) =>
    set((state) => {
      const exists = state.positions.find((p) => p.id === position.id);
      if (exists) {
        return {
          positions: state.positions.map((p) =>
            p.id === position.id ? position : p
          ),
        };
      }
      return {
        positions: [...state.positions, position],
      };
    }),
  getPositions: (symbol) =>
    symbol
      ? get().positions.filter((p) => p.symbol === symbol)
      : get().positions,

  // 잔고
  balance: null,
  setBalance: (balance) => set({ balance }),

  // 시스템 상태
  systemStatus: null,
  setSystemStatus: (status) => set({ systemStatus: status }),

  // 연결 상태
  isConnected: false,
  setConnected: (connected) => set({ isConnected: connected }),

  // 에러
  error: null,
  setError: (error) => set({ error }),

  // WebSocket 메시지 디스패치
  dispatchWSMessage: (message) => {
    switch (message.type) {
      case 'ticker':
        get().updateTicker(message.data as Ticker);
        break;
      case 'order':
        get().updateOrder((message.data as Order).id, message.data as Order);
        break;
      case 'position':
        get().updatePosition(message.data as Position);
        break;
      case 'balance':
        get().setBalance(message.data as Balance);
        break;
      case 'status':
        get().setSystemStatus(message.data as SystemStatus);
        break;
    }
  },
}));
