'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

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

  useEffect(() => {
    const fetchMarkets = async () => {
      try {
        // TODO: 실제 API 호출로 교체
        setMarkets([
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
        ]);
      } catch (error) {
        console.error('마켓 데이터 조회 실패:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchMarkets();
  }, []);

  return (
    <div className="p-8">
      <h1 className="text-4xl font-bold mb-8">마켓 데이터</h1>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-400">로딩 중...</div>
        </div>
      ) : (
        <div className="card">
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
                    <td className="font-medium">{market.symbol}</td>
                    <td>${market.price.toFixed(2)}</td>
                    <td className={market.change >= 0 ? 'text-success' : 'text-danger'}>
                      {market.change >= 0 ? '+' : ''}{market.change.toFixed(2)}%
                    </td>
                    <td>{(market.volume / 1000000).toFixed(1)}M</td>
                    <td>${market.high.toFixed(2)}</td>
                    <td>${market.low.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
