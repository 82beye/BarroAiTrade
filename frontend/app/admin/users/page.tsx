'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import Link from 'next/link';

interface UserSummary {
  user_id: string;
  role: string;
  api_calls: number;
}

type ApiStatus = 'loading' | 'ok' | 'unauthorized' | 'forbidden' | 'unavailable' | 'error';

export default function AdminUsersPage() {
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [status, setStatus] = useState<ApiStatus>('loading');

  useEffect(() => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    fetch('/api/admin/users', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(async (res) => {
        if (res.status === 401) { setStatus('unauthorized'); return; }
        if (res.status === 403) { setStatus('forbidden'); return; }
        if (res.status === 503) { setStatus('unavailable'); return; }
        if (!res.ok) { setStatus('error'); return; }
        setUsers(await res.json());
        setStatus('ok');
      })
      .catch(() => setStatus('error'));
  }, []);

  if (status === 'unauthorized') {
    if (typeof window !== 'undefined') window.location.href = '/login?next=/admin/users';
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
      <h1 className="text-xl font-bold text-slate-200">사용자 관리</h1>

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
            등록 사용자 {status === 'ok' ? `(${users.length}명)` : ''}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {status === 'loading' ? (
            <div className="space-y-3">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="h-10 animate-pulse rounded bg-slate-700" />
              ))}
            </div>
          ) : users.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-500">
              {status === 'unavailable' ? '서비스 점검 중 — 나중에 다시 시도하세요' : '등록된 사용자 없음'}
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="pb-2 text-left text-xs font-medium text-slate-400">사용자 ID</th>
                  <th className="pb-2 text-left text-xs font-medium text-slate-400">역할</th>
                  <th className="pb-2 text-right text-xs font-medium text-slate-400">API 호출</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {users.map((u) => (
                  <tr key={u.user_id}>
                    <td className="py-2 font-mono text-slate-200">{u.user_id}</td>
                    <td className="py-2">
                      <Badge
                        variant={u.role === 'admin' ? 'destructive' : 'secondary'}
                        className="text-xs"
                      >
                        {u.role}
                      </Badge>
                    </td>
                    <td className="py-2 text-right text-slate-400">{u.api_calls.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
