/**
 * API 클라이언트 - Backend와의 통신
 */
import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

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

  constructor(path: string = '/ws/realtime') {
    this.url = `${API_URL.replace('http', 'ws')}${path}`;
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url);

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
          this.attemptReconnect();
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
      setTimeout(() => this.connect(), delay);
    }
  }

  send(data: any): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    } else {
      console.warn('[WS] WebSocket not connected');
    }
  }

  on(event: 'message' | 'error' | 'close' | 'open', callback: (data: any) => void): void {
    if (!this.ws) return;
    this.ws.addEventListener(event, (e: any) => callback(e.data));
  }

  close(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
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
    apiClient.get(`/api/market/orderbook/${symbol}`),

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
