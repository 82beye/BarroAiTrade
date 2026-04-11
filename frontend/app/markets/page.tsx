'use client';

import { MarketTable } from '@/components/markets/market-table';

export default function MarketsPage() {
  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">마켓 데이터</h1>
        <p className="mt-2 text-slate-400">전종목 시세를 확인합니다</p>
      </div>
      <MarketTable />
    </div>
  );
}
