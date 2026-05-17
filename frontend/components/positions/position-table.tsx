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
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="pb-3 font-medium">심볼</th>
                  <th className="pb-3 font-medium">방향</th>
                  <th className="pb-3 text-right font-medium">수량</th>
                  <th className="pb-3 text-right font-medium">진입가</th>
                  <th className="pb-3 text-right font-medium">현재가</th>
                  <th className="pb-3 text-right font-medium">P&L</th>
                  <th className="pb-3 text-right font-medium">수익률</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((position) => (
                  <tr key={position.id} className="border-b border-slate-800 last:border-0 hover:bg-slate-800 hover:bg-opacity-40">
                    <td className="py-3 font-semibold text-slate-200">{position.symbol}</td>
                    <td className="py-3">
                      <span className={`rounded px-2 py-0.5 text-xs font-semibold ${
                        position.side === 'LONG'
                          ? 'bg-blue-900 text-blue-200'
                          : 'bg-orange-900 text-orange-200'
                      }`}>
                        {position.side === 'LONG' ? '롱' : '숏'}
                      </span>
                    </td>
                    <td className="py-3 text-right font-mono text-slate-300">{position.quantity}주</td>
                    <td className="py-3 text-right font-mono text-slate-300">{position.entryPrice.toLocaleString()}원</td>
                    <td className="py-3 text-right font-mono text-slate-300">{position.currentPrice.toLocaleString()}원</td>
                    <td className={`py-3 text-right font-semibold ${position.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {position.pnl >= 0 ? '+' : ''}{position.pnl.toLocaleString()}원
                    </td>
                    <td className={`py-3 text-right font-semibold ${position.pnlPercent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
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
