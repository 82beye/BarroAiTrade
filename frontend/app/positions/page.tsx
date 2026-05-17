'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTradingStore } from '@/lib/store';
import { PositionSummary } from '@/components/positions/position-summary';
import { PositionTable } from '@/components/positions/position-table';
import { Skeleton } from '@/components/ui/skeleton';

const POLL_MS = 30_000;

export default function PositionsPage() {
  const { setPositions } = useTradingStore();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadPositions = useCallback(async () => {
    try {
      const res = await fetch('/api/positions');
      if (!res.ok) throw new Error(`${res.status}`);
      const json = await res.json();
      const raw: any[] = json.positions ?? [];
      setPositions(
        raw.map((p) => ({
          id: p.symbol,
          symbol: p.symbol,
          side: 'LONG' as const,
          quantity: p.quantity ?? 0,
          entryPrice: p.avg_price ?? 0,
          currentPrice: p.cur_price ?? p.avg_price ?? 0,
          pnl: Math.round(((p.cur_price ?? p.avg_price ?? 0) - (p.avg_price ?? 0)) * (p.quantity ?? 0)),
          pnlPercent: p.pnl_rate ?? 0,
          updatedAt: new Date().toISOString(),
        }))
      );
    } catch {
      setError('포지션 데이터를 불러오지 못했습니다. 백엔드 연결을 확인하세요.');
    } finally {
      setLoading(false);
    }
  }, [setPositions]);

  useEffect(() => {
    loadPositions();
    const id = setInterval(loadPositions, POLL_MS);
    return () => clearInterval(id);
  }, [loadPositions]);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">포지션</h1>
        <p className="mt-2 text-slate-400">보유 포지션을 관리합니다</p>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-700 bg-red-900 bg-opacity-30 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
            {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
          </div>
          <Skeleton className="h-48 w-full rounded-lg" />
        </div>
      ) : (
        <>
          <div className="mb-8">
            <PositionSummary />
          </div>
          <PositionTable />
        </>
      )}
    </div>
  );
}
