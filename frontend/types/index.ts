/**
 * 공통 타입 정의
 */

export type MarketType = 'STOCK' | 'CRYPTO' | 'FUTURES';

export interface OHLCV {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OrderBook {
  symbol: string;
  bids: Array<[number, number]>; // [price, volume]
  asks: Array<[number, number]>;
  timestamp: string;
}

export interface SystemStatus {
  uptime: number;
  connectedMarkets: MarketType[];
  activeStrategies: number;
  totalCapital: number;
  timestamp: string;
}
