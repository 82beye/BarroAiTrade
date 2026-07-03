'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent } from '@/components/ui/card';
import { Disclaimer } from '@/components/layout/disclaimer';
import { PriceChart } from '@/components/dashboard/price-chart';
import { strategyStyle, STRATEGY_FILTERS } from '@/lib/strategy';
import { api, type AlertItem } from '@/lib/api';

const POLL_MS = 30_000;

function dateGroupLabel(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const weekday = ['일', '월', '화', '수', '목', '금', '토'][d.getDay()];
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 (${weekday})`;
}

function dateKey(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

function timeLabel(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export default function AlertsPage() {
  const [filter, setFilter] = useState<string>('all'); // 'all' | strategy key
  const [items, setItems] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [selected, setSelected] = useState<AlertItem | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAlerts = useCallback(async (strategy: string) => {
    try {
      const res = await api.getAlertsHistory(strategy === 'all' ? undefined : strategy, 100);
      const data = res.data;
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
      setLastUpdated(new Date());
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    setSelected(null);
    fetchAlerts(filter);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => fetchAlerts(filter), POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [filter, fetchAlerts]);

  // 날짜 그룹핑 (occurred_at 내림차순)
  const groups = useMemo(() => {
    const sorted = [...items].sort(
      (a, b) => new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime(),
    );
    const map = new Map<string, { label: string; items: AlertItem[] }>();
    for (const it of sorted) {
      const k = dateKey(it.occurred_at);
      if (!map.has(k)) map.set(k, { label: dateGroupLabel(it.occurred_at), items: [] });
      map.get(k)!.items.push(it);
    }
    return Array.from(map.values());
  }, [items]);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-slate-50">알림내역</h1>
          <p className="mt-2 text-slate-400">전략 시그널 도달 이벤트 로그</p>
        </div>
        <div className="text-right text-xs text-slate-500">
          {lastUpdated && <div>마지막 갱신: {lastUpdated.toLocaleTimeString('ko-KR')}</div>}
          <div>30초 자동 갱신</div>
        </div>
      </div>

      {/* 필터 탭 (활성 밑줄 빨강 — PRD §4.5) */}
      <div className="mb-6 flex flex-wrap gap-1 border-b border-slate-800">
        {[{ key: 'all', label: '전체', color: '#94a3b8' }, ...STRATEGY_FILTERS].map((f) => {
          const on = f.key === filter;
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-semibold transition-colors ${
                on
                  ? 'border-tima-up text-slate-50'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {/* 알림 리스트 */}
      {loading ? (
        <Card className="border-slate-700 bg-slate-800">
          <CardContent className="py-12 text-center text-slate-400">불러오는 중…</CardContent>
        </Card>
      ) : groups.length === 0 ? (
        <Card className="border-slate-700 bg-slate-800">
          <CardContent className="py-12 text-center text-slate-400">
            알림 이벤트가 없습니다. 운영 데몬 기록 대기 중.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {groups.map((g) => (
            <div key={g.label}>
              <h2 className="mb-2 text-sm font-semibold text-slate-400">{g.label}</h2>
              <Card className="border-slate-700 bg-slate-800">
                <CardContent className="p-0">
                  {g.items.map((it, i) => {
                    const st = strategyStyle(it.strategy);
                    const active = selected?.id === it.id;
                    return (
                      <div
                        key={`${it.id}-${i}`}
                        className={`flex items-center gap-3 border-b border-slate-700 px-4 py-3 last:border-0 hover:bg-slate-700/40 ${
                          active ? 'bg-slate-700/50' : ''
                        }`}
                      >
                        <span
                          className="h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ backgroundColor: st.color }}
                          title={st.label}
                        />
                        {/* 메시지 클릭 → 인라인 차트 토글 */}
                        <button
                          onClick={() => setSelected(active ? null : it)}
                          className="flex-1 text-left text-sm text-slate-200"
                        >
                          {it.message}
                          {it.name && (
                            <span className="ml-1 text-xs text-slate-500">
                              {it.name} ({it.symbol})
                            </span>
                          )}
                        </button>
                        <Link
                          href={`/stocks/${it.symbol}`}
                          className="shrink-0 text-xs text-slate-500 hover:text-tima-teal"
                          title="종목 상세"
                        >
                          상세→
                        </Link>
                        <span className="shrink-0 font-mono text-xs text-slate-500">
                          {timeLabel(it.occurred_at)}
                        </span>
                      </div>
                    );
                  })}
                </CardContent>
              </Card>

              {/* 인라인 차트 (알림→차트 최단 경로, PRD §6.6) */}
              {selected && g.items.some((it) => it.id === selected.id) && (
                <div className="mt-3">
                  <div className="mb-2 text-sm text-slate-300">
                    <span className="font-semibold text-slate-100">
                      {selected.name ?? selected.symbol}
                    </span>{' '}
                    <span className="text-slate-500">({selected.symbol})</span> 기준선 차트
                  </div>
                  <PriceChart
                    key={selected.symbol}
                    defaultSymbol={selected.symbol}
                    defaultTimeframe="15m"
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <Disclaimer />
    </div>
  );
}
