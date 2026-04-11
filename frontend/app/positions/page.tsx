'use client';

import { useTradingStore } from '@/lib/store';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatCard } from '@/components/dashboard/stat-card';

export default function PositionsPage() {
  const positions = useTradingStore((state) => state.positions);

  const totalPnl = positions.reduce((sum, pos) => sum + pos.pnl, 0);
  const totalQuantity = positions.reduce((sum, pos) => sum + pos.quantity, 0);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">포지션</h1>
        <p className="mt-2 text-slate-400">보유 포지션을 관리합니다</p>
      </div>

      {/* 요약 카드 */}
      <div className="mb-8 grid grid-cols-1 gap-6 md:grid-cols-3">
        <StatCard
          title="총 P&L"
          value={`$${totalPnl.toFixed(2)}`}
          color={totalPnl >= 0 ? 'success' : 'danger'}
          icon="💹"
        />
        <StatCard
          title="보유 포지션"
          value={positions.length}
          icon="📊"
        />
        <StatCard
          title="총 수량"
          value={totalQuantity}
          icon="📈"
        />
      </div>

      {/* 포지션 테이블 */}
      <Card className="border-slate-800 bg-slate-900">
        <CardHeader>
          <CardTitle>포지션 상세</CardTitle>
        </CardHeader>
        <CardContent>
          {positions.length === 0 ? (
            <p className="text-slate-400">보유 포지션이 없습니다</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>심볼</th>
                    <th>방향</th>
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
                      <td className="font-medium text-slate-50">
                        {position.symbol}
                      </td>
                      <td className="text-slate-300">
                        {position.side === 'LONG' ? '롱' : '숏'}
                      </td>
                      <td className="text-slate-300">{position.quantity}</td>
                      <td className="text-slate-300">
                        ${position.entryPrice.toFixed(2)}
                      </td>
                      <td className="text-slate-300">
                        ${position.currentPrice.toFixed(2)}
                      </td>
                      <td
                        className={
                          position.pnl >= 0
                            ? 'text-green-500 font-medium'
                            : 'text-red-500 font-medium'
                        }
                      >
                        ${position.pnl.toFixed(2)}
                      </td>
                      <td
                        className={
                          position.pnlPercent >= 0
                            ? 'text-green-500 font-medium'
                            : 'text-red-500 font-medium'
                        }
                      >
                        {position.pnlPercent >= 0 ? '+' : ''}
                        {position.pnlPercent.toFixed(2)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
