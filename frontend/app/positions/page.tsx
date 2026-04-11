'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface Position {
  id: string;
  symbol: string;
  quantity: number;
  entryPrice: number;
  currentPrice: number;
  pnl: number;
  pnlPercent: number;
}

export default function PositionsPage() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [totalPnl, setTotalPnl] = useState(0);

  useEffect(() => {
    const fetchPositions = async () => {
      try {
        // TODO: 실제 API 호출로 교체
        const mockPositions: Position[] = [
          {
            id: '1',
            symbol: 'AAPL',
            quantity: 100,
            entryPrice: 145.0,
            currentPrice: 150.25,
            pnl: 525,
            pnlPercent: 3.63,
          },
          {
            id: '2',
            symbol: 'MSFT',
            quantity: 50,
            entryPrice: 385.0,
            currentPrice: 380.5,
            pnl: -225,
            pnlPercent: -1.17,
          },
        ];

        setPositions(mockPositions);
        const totalPnl = mockPositions.reduce((sum, pos) => sum + pos.pnl, 0);
        setTotalPnl(totalPnl);
      } catch (error) {
        console.error('포지션 조회 실패:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchPositions();
  }, []);

  return (
    <div className="p-8">
      <h1 className="text-4xl font-bold mb-8">포지션</h1>

      {/* 요약 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="card">
          <h3 className="text-sm text-gray-400 mb-2">총 P&L</h3>
          <p className={`text-2xl font-bold ${totalPnl >= 0 ? 'text-success' : 'text-danger'}`}>
            ${totalPnl.toFixed(2)}
          </p>
        </div>

        <div className="card">
          <h3 className="text-sm text-gray-400 mb-2">보유 포지션</h3>
          <p className="text-2xl font-bold">{positions.length}</p>
        </div>

        <div className="card">
          <h3 className="text-sm text-gray-400 mb-2">총 수량</h3>
          <p className="text-2xl font-bold">
            {positions.reduce((sum, pos) => sum + pos.quantity, 0)}
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-400">로딩 중...</div>
        </div>
      ) : (
        <div className="card">
          {positions.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>심볼</th>
                    <th>수량</th>
                    <th>진입가</th>
                    <th>현재가</th>
                    <th>P&L</th>
                    <th>수익률</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((position) => (
                    <tr key={position.id}>
                      <td className="font-medium">{position.symbol}</td>
                      <td>{position.quantity}</td>
                      <td>${position.entryPrice.toFixed(2)}</td>
                      <td>${position.currentPrice.toFixed(2)}</td>
                      <td className={position.pnl >= 0 ? 'text-success' : 'text-danger'}>
                        ${position.pnl.toFixed(2)}
                      </td>
                      <td className={position.pnlPercent >= 0 ? 'text-success' : 'text-danger'}>
                        {position.pnlPercent >= 0 ? '+' : ''}{position.pnlPercent.toFixed(2)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-400">보유 포지션이 없습니다</p>
          )}
        </div>
      )}
    </div>
  );
}
