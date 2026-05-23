'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import Link from 'next/link';

interface AuditEntry {
  id: number;
  event_type: string;
  symbol: string | null;
  created_at: string;
}

type ApiStatus = 'loading' | 'ok' | 'unauthorized' | 'forbidden' | 'unavailable' | 'error';

const EVENT_TYPE_COLORS: Record<string, string> = {
  ORDER_PLACED: 'bg-green-500/20 text-green-400 border-green-500/30',
  ORDER_CANCELLED: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  LOGIN: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  LOGOUT: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
  CONFIG_CHANGED: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  ERROR: 'bg-red-500/20 text-red-400 border-red-500/30',
};

function EventBadge({ type }: { type: string }) {
  const cls = EVENT_TYPE_COLORS[type] ?? 'bg-slate-500/20 text-slate-400 border-slate-500/30';
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-mono ${cls}`}>
      {type}
    </span>
  );
}

export default function AdminAuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [status, setStatus] = useState<ApiStatus>('loading');
  const [limit, setLimit] = useState(100);

  useEffect(() => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    fetch(`/api/admin/audit/recent?limit=${limit}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(async (res) => {
        if (res.status === 401) { setStatus('unauthorized'); return; }
        if (res.status === 403) { setStatus('forbidden'); return; }
        if (res.status === 503) { setStatus('unavailable'); return; }
        if (!res.ok) { setStatus('error'); return; }
        setEntries(await res.json());
        setStatus('ok');
      })
      .catch(() => setStatus('error'));
  }, [limit]);

  if (status === 'unauthorized') {
    if (typeof window !== 'undefined') window.location.href = '/?error=unauthorized';
    return null;
  }

  if (status === 'forbidden') {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <p className="text-4xl">🚫</p>
        <h2 className="mt-4 text-xl font-bold text-slate-200">접근 권한 없음</h2>
        <p className="mt-2 text-sm text-slate-400">admin 권한이 필요합니다.</p>
        <Link href="/" className="mt-6 rounded-lg bg-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-600">
          홈으로
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-slate-200">Audit 로그</h1>
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 focus:outline-none"
        >
          <option value={50}>최근 50건</option>
          <option value={100}>최근 100건</option>
          <option value={200}>최근 200건</option>
          <option value={500}>최근 500건</option>
          <option value={1000}>최근 1000건</option>
        </select>
      </div>

      {status === 'unavailable' && (
        <div className="flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3">
          <span className="text-lg">🔧</span>
          <div>
            <p className="text-sm font-medium text-amber-400">서비스 점검 중</p>
            <p className="text-xs text-slate-400">
              인증 서비스가 초기화되지 않았습니다. 관리자에게 문의하세요.
            </p>
          </div>
        </div>
      )}

      <Card className="border-slate-700 bg-slate-800">
        <CardHeader>
          <CardTitle className="text-sm font-medium text-slate-300">
            {status === 'ok' ? `${entries.length}건` : '이벤트 로그'}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {status === 'loading' ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-9 animate-pulse rounded bg-slate-700" />
              ))}
            </div>
          ) : entries.length === 0 ? (
            <p className="py-12 text-center text-sm text-slate-500">
              {status === 'unavailable' ? '서비스 점검 중 — 나중에 다시 시도하세요' : '로그 없음'}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-700 bg-slate-900/50">
                    <th className="px-4 py-2 text-left font-medium text-slate-400">ID</th>
                    <th className="px-4 py-2 text-left font-medium text-slate-400">이벤트 유형</th>
                    <th className="px-4 py-2 text-left font-medium text-slate-400">종목</th>
                    <th className="px-4 py-2 text-right font-medium text-slate-400">시각</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/50">
                  {entries.map((e) => (
                    <tr key={e.id} className="hover:bg-slate-700/30">
                      <td className="px-4 py-2 font-mono text-slate-500">{e.id}</td>
                      <td className="px-4 py-2">
                        <EventBadge type={e.event_type} />
                      </td>
                      <td className="px-4 py-2 text-slate-400">{e.symbol ?? '—'}</td>
                      <td className="px-4 py-2 text-right text-slate-400">
                        {new Date(e.created_at).toLocaleString('ko-KR')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
