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
};
