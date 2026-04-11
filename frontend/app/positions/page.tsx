'use client';

import { PositionSummary } from '@/components/positions/position-summary';
import { PositionTable } from '@/components/positions/position-table';

export default function PositionsPage() {
  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">포지션</h1>
        <p className="mt-2 text-slate-400">보유 포지션을 관리합니다</p>
      </div>

      <div className="mb-8">
        <PositionSummary />
      </div>

      <PositionTable />
    </div>
  );
}
