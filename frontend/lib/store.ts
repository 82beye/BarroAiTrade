/**
 * Zustand 전역 상태 관리
 */
import { create } from 'zustand';

export interface Ticker {
  symbol: string;
  price: number;
  high: number;
  low: number;
  volume: number;
  timestamp: string;
}

export interface Order {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  status: 'PENDING' | 'FILLED' | 'CANCELED' | 'REJECTED';
  timestamp: string;
}

export interface Balance {
  currency: string;
  free: number;
  locked: number;
  total: number;
}

interface TradingStore {
  // 시장 데이터
  tickers: Map<string, Ticker>;
  updateTicker: (ticker: Ticker) => void;

  // 포지션 및 주문
  orders: Order[];
  addOrder: (order: Order) => void;
  updateOrder: (orderId: string, update: Partial<Order>) => void;

  // 계좌
  balance: Balance | null;
  setBalance: (balance: Balance) => void;

  // 연결 상태
  isConnected: boolean;
  setConnected: (connected: boolean) => void;

  // 에러
  error: string | null;
  setError: (error: string | null) => void;
}

export const useTradingStore = create<TradingStore>((set) => ({
  tickers: new Map(),
  updateTicker: (ticker) =>
    set((state) => {
      const newTickers = new Map(state.tickers);
      newTickers.set(ticker.symbol, ticker);
      return { tickers: newTickers };
    }),

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

  balance: null,
  setBalance: (balance) => set({ balance }),

  isConnected: false,
  setConnected: (connected) => set({ isConnected: connected }),

  error: null,
  setError: (error) => set({ error }),
}));
