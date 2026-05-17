'use client';

import { useTradingStore } from '@/lib/store';
import { StatCard } from '@/components/dashboard/stat-card';

export function PositionSummary() {
  const positions = useTradingStore((state) => state.positions);

  const totalPnl = positions.reduce((sum, pos) => sum + pos.pnl, 0);
  const totalQuantity = positions.reduce((sum, pos) => sum + pos.quantity, 0);

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
      <StatCard
        title="총 P&L"
        value={`${totalPnl >= 0 ? '+' : ''}${totalPnl.toLocaleString()}원`}
        color={totalPnl >= 0 ? 'success' : 'danger'}
        icon="💹"
      />
      <StatCard title="보유 포지션" value={`${positions.length}개`} icon="📊" />
      <StatCard title="총 수량" value={`${totalQuantity.toLocaleString()}주`} icon="📈" />
    </div>
  );
}
