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

interface AuditEntry {
  id: number;
  event_type: string;
  symbol: string | null;
  created_at: string;
}

type ApiStatus = 'loading' | 'ok' | 'unauthorized' | 'forbidden' | 'unavailable' | 'error';

function ServiceUnavailableBanner() {
  return (
    <div className="mb-6 flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3">
      <span className="text-lg">🔧</span>
      <div>
        <p className="text-sm font-medium text-amber-400">서비스 점검 중</p>
        <p className="text-xs text-slate-400">
          인증 서비스가 초기화되지 않았습니다. 관리자에게 문의하세요. (BAR-74b 배포 후 활성화)
        </p>
      </div>
    </div>
  );
}

async function fetchAdmin<T>(path: string): Promise<{ data: T | null; status: ApiStatus }> {
  try {
    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    const res = await fetch(path, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (res.status === 401) return { data: null, status: 'unauthorized' };
    if (res.status === 403) return { data: null, status: 'forbidden' };
    if (res.status === 503) return { data: null, status: 'unavailable' };
    if (!res.ok) return { data: null, status: 'error' };
    return { data: await res.json(), status: 'ok' };
  } catch {
    return { data: null, status: 'error' };
  }
}

export default function AdminDashboard() {
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [userStatus, setUserStatus] = useState<ApiStatus>('loading');
  const [auditStatus, setAuditStatus] = useState<ApiStatus>('loading');

  useEffect(() => {
    fetchAdmin<UserSummary[]>('/api/admin/users').then(({ data, status }) => {
      setUserStatus(status);
      if (data) setUsers(data);
    });
    fetchAdmin<AuditEntry[]>('/api/admin/audit/recent?limit=10').then(({ data, status }) => {
      setAuditStatus(status);
      if (data) setAudit(data);
    });
  }, []);

  const serviceUnavailable = userStatus === 'unavailable' || auditStatus === 'unavailable';

  if (userStatus === 'unauthorized' || auditStatus === 'unauthorized') {
    if (typeof window !== 'undefined') window.location.href = '/?error=unauthorized';
    return null;
  }

  if (userStatus === 'forbidden' || auditStatus === 'forbidden') {
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
      <h1 className="text-xl font-bold text-slate-200">어드민 대시보드</h1>

      {serviceUnavailable && <ServiceUnavailableBanner />}

      <div className="grid gap-4 md:grid-cols-2">
        {/* 사용자 요약 */}
        <Card className="border-slate-700 bg-slate-800">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-slate-300">사용자</CardTitle>
              <Link href="/admin/users" className="text-xs text-blue-400 hover:underline">
                전체 보기 →
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            {userStatus === 'loading' ? (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-8 animate-pulse rounded bg-slate-700" />
                ))}
              </div>
            ) : users.length === 0 ? (
              <p className="text-sm text-slate-500">
                {serviceUnavailable ? '서비스 점검 중 — 데이터 없음' : '등록된 사용자 없음'}
              </p>
            ) : (
              <ul className="space-y-2">
                {users.slice(0, 5).map((u) => (
                  <li key={u.user_id} className="flex items-center justify-between text-sm">
                    <span className="text-slate-300">{u.user_id}</span>
                    <div className="flex items-center gap-2">
                      <Badge variant={u.role === 'admin' ? 'destructive' : 'secondary'} className="text-xs">
                        {u.role}
                      </Badge>
                      <span className="text-xs text-slate-500">{u.api_calls} calls</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* 최근 Audit */}
        <Card className="border-slate-700 bg-slate-800">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-slate-300">최근 Audit 로그</CardTitle>
              <Link href="/admin/audit" className="text-xs text-blue-400 hover:underline">
                전체 보기 →
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            {auditStatus === 'loading' ? (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-8 animate-pulse rounded bg-slate-700" />
                ))}
              </div>
            ) : audit.length === 0 ? (
              <p className="text-sm text-slate-500">
                {serviceUnavailable ? '서비스 점검 중 — 데이터 없음' : '로그 없음'}
              </p>
            ) : (
              <ul className="space-y-2">
                {audit.map((e) => (
                  <li key={e.id} className="flex items-center justify-between text-xs">
                    <span className="font-mono text-slate-300">{e.event_type}</span>
                    <span className="text-slate-500">
                      {e.symbol ? `${e.symbol} · ` : ''}
                      {new Date(e.created_at).toLocaleTimeString('ko-KR')}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
