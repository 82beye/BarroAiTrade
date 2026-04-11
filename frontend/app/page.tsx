'use client';

import { useEffect, useState } from 'react';
import { api, WebSocketClient } from '@/lib/api';
import { useTradingStore } from '@/lib/store';

export default function Dashboard() {
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const { isConnected, setConnected, error, setError } = useTradingStore();

  useEffect(() => {
    // API 상태 조회
    const fetchStatus = async () => {
      try {
        const response = await api.getStatus();
        setStatus(response.data);
      } catch (err) {
        setError('API 연결 실패');
        console.error(err);
      } finally {
        setLoading(false);
      }
    };

    fetchStatus();

    // WebSocket 연결
    const wsClient = new WebSocketClient();
    wsClient.connect()
      .then(() => setConnected(true))
      .catch((err) => {
        setError('WebSocket 연결 실패');
        console.error(err);
      });

    return () => {
      wsClient.close();
    };
  }, [setConnected, setError]);

  return (
    <div className="p-8">
      {/* 헤더 */}
      <div className="mb-8">
        <h1 className="text-4xl font-bold mb-2">대시보드</h1>
        <p className="text-gray-400">AI 기반 멀티마켓 자동매매 플랫폼</p>
      </div>

      {/* 상태 표시 */}
      <div className="mb-6 flex items-center gap-4">
        <div className={`flex items-center gap-2 px-4 py-2 rounded-lg ${
          isConnected ? 'bg-green-900 text-green-200' : 'bg-red-900 text-red-200'
        }`}>
          <div className={`w-2 h-2 rounded-full ${
            isConnected ? 'bg-green-400' : 'bg-red-400'
          }`}></div>
          {isConnected ? '연결됨' : '연결 끊김'}
        </div>
        {error && (
          <div className="px-4 py-2 rounded-lg bg-red-900 text-red-200">
            ⚠️ {error}
          </div>
        )}
      </div>

      {/* 로딩 상태 */}
      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-400">로딩 중...</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
          {/* 핵심 지표 */}
          <div className="card">
            <h3 className="text-sm text-gray-400 mb-2">총 자본</h3>
            <p className="text-2xl font-bold">
              ${status?.total_capital?.toLocaleString() || '0'}
            </p>
          </div>

          <div className="card">
            <h3 className="text-sm text-gray-400 mb-2">활성 전략</h3>
            <p className="text-2xl font-bold text-primary">
              {status?.active_strategies || 0}
            </p>
          </div>

          <div className="card">
            <h3 className="text-sm text-gray-400 mb-2">연결된 마켓</h3>
            <p className="text-2xl font-bold text-success">
              {status?.connected_markets?.length || 0}
            </p>
          </div>

          <div className="card">
            <h3 className="text-sm text-gray-400 mb-2">가동 시간</h3>
            <p className="text-2xl font-bold">
              {status?.uptime ? `${Math.floor(status.uptime / 3600)}h` : '0h'}
            </p>
          </div>
        </div>
      )}

      {/* 최근 활동 */}
      <div className="card">
        <h2 className="text-xl font-bold mb-4">최근 활동</h2>
        <p className="text-gray-400">아직 활동 기록이 없습니다</p>
      </div>
    </div>
  );
}
