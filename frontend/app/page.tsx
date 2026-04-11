'use client';

import { useEffect, useState } from 'react';
import { api, WebSocketClient } from '@/lib/api';
import { useTradingStore } from '@/lib/store';
import { StatCard } from '@/components/dashboard/stat-card';
import { TickerTable } from '@/components/dashboard/ticker-table';
import { RecentOrders } from '@/components/dashboard/recent-orders';
import { PriceChart } from '@/components/dashboard/price-chart';

export default function Dashboard() {
  const {
    isConnected,
    setConnected,
    error,
    setError,
    systemStatus,
    setSystemStatus,
    dispatchWSMessage,
  } = useTradingStore();

  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // API 상태 조회
    const fetchStatus = async () => {
      try {
        const response = await api.getStatus();
        setSystemStatus(response.data);
      } catch (err) {
        console.error('API 연결 실패:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchStatus();

    // WebSocket 연결
    const wsClient = new WebSocketClient();
    wsClient.connect()
      .then(() => {
        setConnected(true);

        // WebSocket 메시지 핸들링
        const messageHandler = (event: any) => {
          try {
            const message = JSON.parse(event.data);
            dispatchWSMessage(message);
          } catch (err) {
            console.error('WebSocket 메시지 파싱 실패:', err);
          }
        };

        wsClient.on('message', messageHandler);
      })
      .catch((err) => {
        setError('WebSocket 연결 실패');
        console.error(err);
      });

    return () => {
      wsClient.close();
    };
  }, [setConnected, setError, setSystemStatus, dispatchWSMessage]);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">대시보드</h1>
        <p className="mt-2 text-slate-400">
          AI 기반 멀티마켓 자동매매 플랫폼
        </p>
      </div>

      {/* Status Bar */}
      <div className="mb-6 flex flex-wrap items-center gap-4">
        <div
          className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium ${
            isConnected
              ? 'bg-green-900 text-green-200'
              : 'bg-red-900 text-red-200'
          }`}
        >
          <div
            className={`h-2 w-2 rounded-full ${
              isConnected ? 'bg-green-400' : 'bg-red-400'
            }`}
          ></div>
          {isConnected ? '연결됨' : '연결 끊김'}
        </div>

        {error && (
          <div className="inline-flex items-center gap-2 rounded-lg bg-red-900 px-4 py-2 text-sm font-medium text-red-200">
            ⚠️ {error}
          </div>
        )}

        {systemStatus && (
          <div className="text-sm text-slate-400">
            업타임: {Math.floor(systemStatus.uptime / 3600)}h
          </div>
        )}
      </div>

      {/* Stat Cards */}
      {loading ? (
        <div className="flex h-64 items-center justify-center">
          <p className="text-slate-400">로딩 중...</p>
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
              color={
                (systemStatus?.totalPnl || 0) >= 0 ? 'success' : 'danger'
              }
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
