'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent } from '@/components/ui/card';
import { Disclaimer } from '@/components/layout/disclaimer';
import { api, type NxtItem } from '@/lib/api';

type NxtFilter = 'value' | 'gainers' | 'losers';

const FILTERS: { key: NxtFilter; label: string }[] = [
  { key: 'value', label: '거래대금' },
  { key: 'gainers', label: '상승률' },
  { key: 'losers', label: '하락률' },
];

const POLL_MS = 60_000;

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

function PctCell({ v }: { v?: number | null }) {
  if (v === null || v === undefined) return <span className="text-slate-500">-</span>;
  const up = v >= 0;
  return (
    <span className={`font-mono ${up ? 'text-tima-up' : 'text-tima-down'}`}>
      {up ? '↑' : '↓'} {Math.abs(v).toFixed(2)}%
    </span>
  );
}

export default function NxtPage() {
  const [filter, setFilter] = useState<NxtFilter>('value');
  const [items, setItems] = useState<NxtItem[]>([]);
  const [status, setStatus] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (f: NxtFilter) => {
    try {
      const res = await api.getNxt(f, 30);
      const data = res.data;
      setStatus(data?.status ?? 'unsupported');
      setItems(data?.status === 'ok' && Array.isArray(data.items) ? data.items : []);
    } catch {
      setStatus('unsupported');
      setItems([]);
    } finally {
      setLoading(false);
      setLastUpdated(new Date());
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    load(filter);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => load(filter), POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [filter, load]);

  const isOk = status === 'ok' && items.length > 0;

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-slate-50">NXT 애프터마켓</h1>
          <p className="mt-2 text-slate-400">대체거래소(넥스트레이드) 시간외 시세</p>
        </div>
        <div className="text-right text-xs text-slate-500">
          {lastUpdated && <div>마지막 갱신: {lastUpdated.toLocaleTimeString('ko-KR')}</div>}
          <div>{loading ? '갱신 중…' : '60초 자동 갱신'}</div>
        </div>
      </div>

      {/* 필터 pill (활성 tima.active 황색 — PRD §5) */}
      <div className="mb-6 flex flex-wrap gap-2">
        {FILTERS.map((f) => {
          const on = f.key === filter;
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded-full px-5 py-2 text-sm font-semibold transition-colors ${
                on ? 'bg-tima-active text-black' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
              }`}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {isOk ? (
        <Card className="border-slate-700 bg-slate-800">
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-slate-400">
                    <th className="p-3 text-left font-medium">종목명</th>
                    <th className="p-3 text-right font-medium">NXT현재가</th>
                    <th className="p-3 text-right font-medium">종가대비</th>
                    <th className="p-3 text-right font-medium">당일종가</th>
                    <th className="p-3 text-right font-medium">등락률</th>
                    <th className="p-3 text-right font-medium">Aft(억)</th>
                    <th className="p-3 text-right font-medium">누적(억)</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => (
                    <tr
                      key={`${it.symbol}-${i}`}
                      className="border-b border-slate-700 last:border-0 hover:bg-slate-700/40"
                    >
                      <td className="p-3">
                        <Link
                          href={`/stocks/${it.symbol}`}
                          className="font-medium text-slate-100 hover:text-tima-teal hover:underline"
                        >
                          {it.name ?? it.symbol}
                        </Link>
                        <div className="font-mono text-xs text-slate-500">{it.symbol}</div>
                      </td>
                      <td className="p-3 text-right font-mono text-slate-100">
                        {fmtNum(it.nxt_price)}
                      </td>
                      <td className="p-3 text-right">
                        <PctCell v={it.vs_close_pct} />
                      </td>
                      <td className="p-3 text-right font-mono text-slate-300">
                        {fmtNum(it.day_close)}
                      </td>
                      <td className="p-3 text-right">
                        <PctCell v={it.day_change_pct} />
                      </td>
                      <td className="p-3 text-right font-mono text-slate-300">
                        {fmtNum(it.aft_value)}
                      </td>
                      <td className="p-3 text-right font-mono text-slate-400">
                        {fmtNum(it.cum_value)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="border-slate-700 bg-slate-800">
          <CardContent className="py-12 text-center text-slate-400">
            {loading
              ? '불러오는 중…'
              : 'NXT 애프터마켓 데이터 미연동 (게이트웨이 확장 필요)'}
          </CardContent>
        </Card>
      )}

      {/* NXT 전용 면책 (PRD §5) */}
      <p className="mt-6 text-xs leading-relaxed text-slate-500">
        NXT(넥스트레이드) 시세는 대체거래소 애프터마켓 데이터로, 정규장 시세와 체결·호가 기준이
        다를 수 있습니다. 표시된 수치는 참고용이며 지연·오류가 있을 수 있습니다.
      </p>
      <Disclaimer />
    </div>
  );
}
