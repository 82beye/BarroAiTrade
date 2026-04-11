'use client';

import { useTradingStore } from '@/lib/store';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export function PositionTable() {
  const positions = useTradingStore((state) => state.positions);

  return (
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
                    <td className="font-medium text-slate-50">{position.symbol}</td>
                    <td className="text-slate-300">{position.side === 'LONG' ? '롱' : '숏'}</td>
                    <td className="text-slate-300">{position.quantity}</td>
                    <td className="text-slate-300">${position.entryPrice.toFixed(2)}</td>
                    <td className="text-slate-300">${position.currentPrice.toFixed(2)}</td>
                    <td className={position.pnl >= 0 ? 'font-medium text-green-500' : 'font-medium text-red-500'}>
                      ${position.pnl.toFixed(2)}
                    </td>
                    <td className={position.pnlPercent >= 0 ? 'font-medium text-green-500' : 'font-medium text-red-500'}>
                      {position.pnlPercent >= 0 ? '+' : ''}{position.pnlPercent.toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
