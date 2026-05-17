'use client';

import { useEffect, useState } from 'react';
import { useTradingStore } from '@/lib/store';
import { StatusBar } from '@/components/layout/status-bar';
import { StatCard } from '@/components/dashboard/stat-card';
import { TickerTable } from '@/components/dashboard/ticker-table';
import { RecentOrders } from '@/components/dashboard/recent-orders';
import { PriceChart } from '@/components/dashboard/price-chart';
import { Skeleton } from '@/components/ui/skeleton';

interface RiskStatus {
  daily_limit_breached?: boolean;
  new_entry_blocked?: boolean;
}

interface AccountBalance {
  total_value: number;
  available_cash: number;
  total_pnl: number;
  total_pnl_pct: number;
}

interface SystemInfo {
  mode?: string;
  position_count?: number;
  state?: string;
}

export default function Dashboard() {
  const { error } = useTradingStore();
  const [loading, setLoading] = useState(true);
  const [riskStatus, setRiskStatus] = useState<RiskStatus | null>(null);
  const [balance, setBalance] = useState<AccountBalance | null>(null);
  const [sysInfo, setSysInfo] = useState<SystemInfo | null>(null);

  // 초기 데이터 로드 (잔고 + 시스템 상태)
  useEffect(() => {
    let done = false;
    async function load() {
      const [balRes, sysRes] = await Promise.allSettled([
        fetch('/api/accounts/balance'),
        fetch('/api/status'),
      ]);
      if (balRes.status === 'fulfilled' && balRes.value.ok) {
        setBalance(await balRes.value.json());
      }
      if (sysRes.status === 'fulfilled' && sysRes.value.ok) {
        setSysInfo(await sysRes.value.json());
      }
      if (!done) setLoading(false);
    }
    load();
    const timer = setTimeout(() => { done = true; setLoading(false); }, 3000);
    return () => { done = true; clearTimeout(timer); };
  }, []);

  // 리스크 상태 폴링 (30초)
  useEffect(() => {
    async function fetchRisk() {
      try {
        const res = await fetch('/api/risk/status');
        if (res.ok) setRiskStatus(await res.json());
      } catch {
        // 리스크 API 미응답 시 배너 미표시
      }
    }
    fetchRisk();
    const interval = setInterval(fetchRisk, 30_000);
    return () => clearInterval(interval);
  }, []);

  // 잔고 주기적 갱신 (60초)
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch('/api/accounts/balance');
        if (res.ok) setBalance(await res.json());
      } catch { /* silent */ }
    }, 60_000);
    return () => clearInterval(interval);
  }, []);

  const riskAlert = riskStatus?.daily_limit_breached || riskStatus?.new_entry_blocked;
  const totalCapital = balance?.total_value ?? 0;
  const availableCash = balance?.available_cash ?? 0;
  const totalPnl = balance?.total_pnl ?? 0;
  const totalPnlPct = balance?.total_pnl_pct ?? 0;
  const modeLabel = sysInfo?.mode === 'live' ? 'LIVE' : sysInfo?.mode === 'simulation' ? 'SIM' : '—';
  const posCount = sysInfo?.position_count ?? 0;
  const isError = error !== null;

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      {/* 리스크 경고 배너 */}
      {riskAlert && (
        <div className="mb-4 flex items-center gap-3 rounded-lg border border-red-700 bg-red-900 bg-opacity-40 px-4 py-3">
          <span className="text-red-400">⚠️</span>
          <div>
            <span className="font-semibold text-red-300">리스크 한도 초과</span>
            {riskStatus?.new_entry_blocked && (
              <span className="ml-2 text-sm text-red-400">— 신규 진입 차단됨</span>
            )}
          </div>
          <a href="/monitor" className="ml-auto text-sm text-red-400 underline hover:text-red-300">
            상세 보기
          </a>
        </div>
      )}

      {/* 백엔드 연결 오류 배너 */}
      {isError && (
        <div className="mb-4 rounded-lg border border-yellow-700 bg-yellow-900 bg-opacity-30 px-4 py-3 text-sm text-yellow-300">
          백엔드에 연결되지 않았습니다. 실시간 데이터가 표시되지 않을 수 있습니다.
        </div>
      )}

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
        <div className="mb-8 grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
      ) : (
        <>
          <div className="mb-8 grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
            <StatCard
              title="총 자본"
              value={totalCapital > 0 ? `${totalCapital.toLocaleString()}원` : '—'}
              description={availableCash > 0 ? `가용 ${availableCash.toLocaleString()}원` : undefined}
              icon="💰"
              live
            />
            <StatCard
              title="총 P&L"
              value={balance ? `${totalPnl >= 0 ? '+' : ''}${totalPnl.toLocaleString()}원` : '—'}
              description={balance ? `${totalPnlPct >= 0 ? '+' : ''}${totalPnlPct.toFixed(2)}%` : undefined}
              color={totalPnl >= 0 ? 'success' : 'danger'}
              icon="📈"
              live
            />
            <StatCard
              title="보유 포지션"
              value={`${posCount}개`}
              icon="🤖"
            />
            <StatCard
              title="운영 모드"
              value={modeLabel}
              description={sysInfo?.state ?? undefined}
              color={modeLabel === 'LIVE' ? 'success' : 'default'}
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
