'use client';

import { useEffect, useState } from 'react';
import { useTradingStore } from '@/lib/store';
import { StatusBar } from '@/components/layout/status-bar';
import { StatCard } from '@/components/dashboard/stat-card';
import { TickerTable } from '@/components/dashboard/ticker-table';
import { RecentOrders } from '@/components/dashboard/recent-orders';
import { PriceChart } from '@/components/dashboard/price-chart';
import { Skeleton } from '@/components/ui/skeleton';
import { useRealtimeConnection } from '@/hooks/useRealtimeConnection';

export default function Dashboard() {
  const { systemStatus } = useTradingStore();
  const [loading, setLoading] = useState(true);

  useRealtimeConnection();

  useEffect(() => {
    const timer = setTimeout(() => setLoading(false), 1000);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">대시보드</h1>
        <p className="mt-2 text-slate-400">AI 기반 멀티마켓 자동매매 플랫폼</p>
      </div>

      {/* Status Bar */}
      <div className="mb-6">
        <StatusBar />
      </div>

      {/* Stat Cards */}
      {loading ? (
        <div className="mb-8 grid grid-cols-1 gap-6 md:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
      ) : (
        <>
          <div className="mb-8 grid grid-cols-1 gap-6 md:grid-cols-4">
            <StatCard
              title="총 자본"
              value={`$${(systemStatus?.totalCapital || 0).toLocaleString()}`}
              icon="💰"
            />
            <StatCard
              title="총 P&L"
              value={`$${(systemStatus?.totalPnl || 0).toFixed(2)}`}
              color={(systemStatus?.totalPnl || 0) >= 0 ? 'success' : 'danger'}
              icon="📈"
            />
            <StatCard
              title="활성 전략"
              value={systemStatus?.activeStrategies || 0}
              icon="🤖"
            />
            <StatCard
              title="연결 마켓"
              value={systemStatus?.connectedMarkets.length || 0}
              icon="🌐"
            />
          </div>

          {/* Price Chart */}
          <div className="mb-6">
            <PriceChart />
          </div>

          {/* Content Grid */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <TickerTable />
            </div>
            <div>
              <RecentOrders />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
