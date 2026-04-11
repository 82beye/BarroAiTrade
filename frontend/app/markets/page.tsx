'use client';

import { useEffect, useState } from 'react';
import { useTradingStore } from '@/lib/store';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface MarketData {
  symbol: string;
  price: number;
  change: number;
  volume: number;
  high: number;
  low: number;
}

export default function MarketsPage() {
  const [markets, setMarkets] = useState<MarketData[]>([]);
  const [loading, setLoading] = useState(true);
  const tickers = useTradingStore((state) => Array.from(state.tickers.values()));

  useEffect(() => {
    // Mock 데이터로 초기화 (실제 API 연동 대기 중)
    const mockMarkets: MarketData[] = [
      {
        symbol: 'AAPL',
        price: 150.25,
        change: 2.5,
        volume: 50000000,
        high: 151.5,
        low: 149.0,
      },
      {
        symbol: 'MSFT',
        price: 380.5,
        change: -1.2,
        volume: 20000000,
        high: 385.0,
        low: 378.5,
      },
      {
        symbol: 'GOOGL',
        price: 140.75,
        change: 0.8,
        volume: 15000000,
        high: 142.0,
        low: 139.5,
      },
    ];

    // WebSocket 데이터가 있으면 병합
    const merged = mockMarkets.map((market) => {
      const wsData = tickers.find((t) => t.symbol === market.symbol);
      return wsData
        ? {
            ...market,
            price: wsData.price,
            change: wsData.change,
            volume: wsData.volume,
            high: wsData.high,
            low: wsData.low,
          }
        : market;
    });

    setMarkets(merged);
    setLoading(false);
  }, [tickers]);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">마켓 데이터</h1>
        <p className="mt-2 text-slate-400">전종목 시세를 확인합니다</p>
      </div>

      {loading ? (
        <div className="flex h-64 items-center justify-center">
          <p className="text-slate-400">로딩 중...</p>
        </div>
      ) : (
        <Card className="border-slate-800 bg-slate-900">
          <CardHeader>
            <CardTitle>마켓 현황</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>심볼</th>
                    <th>현재가</th>
                    <th>변화율</th>
                    <th>거래량</th>
                    <th>고가</th>
                    <th>저가</th>
                  </tr>
                </thead>
                <tbody>
                  {markets.map((market) => (
                    <tr key={market.symbol}>
                      <td className="font-medium text-slate-50">
                        {market.symbol}
                      </td>
                      <td className="text-slate-300">
                        ${market.price.toFixed(2)}
                      </td>
                      <td
                        className={
                          market.change >= 0
                            ? 'text-green-500'
                            : 'text-red-500'
                        }
                      >
                        {market.change >= 0 ? '+' : ''}
                        {market.change.toFixed(2)}%
                      </td>
                      <td className="text-slate-300">
                        {(market.volume / 1000000).toFixed(1)}M
                      </td>
                      <td className="text-slate-300">
                        ${market.high.toFixed(2)}
                      </td>
                      <td className="text-slate-300">
                        ${market.low.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
