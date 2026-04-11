'use client';

import { useTradingStore } from '@/lib/store';

export function StatusBar() {
  const isConnected = useTradingStore((state) => state.isConnected);
  const error = useTradingStore((state) => state.error);
  const systemStatus = useTradingStore((state) => state.systemStatus);

  return (
    <div className="flex flex-wrap items-center gap-4">
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
        />
        {isConnected ? '연결됨' : '연결 끊김'}
      </div>

      {error && (
        <div className="inline-flex items-center gap-2 rounded-lg bg-red-900 px-4 py-2 text-sm font-medium text-red-200">
          {error}
        </div>
      )}

      {systemStatus && (
        <div className="text-sm text-slate-400">
          업타임: {Math.floor(systemStatus.uptime / 3600)}h
        </div>
      )}
    </div>
  );
}
