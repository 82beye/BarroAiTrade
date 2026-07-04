'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
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
    <div className="p-3">
      {/* 필터 pill (활성 tima.active 황색 — PRD §5) */}
      <div className="mb-2 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => {
          const on = f.key === filter;
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded-full px-4 py-1.5 text-sm font-semibold transition-colors ${
                on
                  ? 'bg-tima-active text-black'
                  : 'border border-tima-line bg-white text-tima-sub hover:bg-tima-bg'
              }`}
            >
              {f.label}
            </button>
          );
        })}
      </div>
      <div className="mb-2 text-right text-[11px] text-tima-sub">
        {lastUpdated
          ? `${lastUpdated.toLocaleTimeString('ko-KR')} · ${loading ? '갱신 중…' : '60초 갱신'}`
          : '60초 자동 갱신'}
      </div>

      {isOk ? (
        <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
                  <th className="p-2.5 text-left text-xs font-semibold">종목명</th>
                  <th className="p-2.5 text-right text-xs font-semibold">NXT현재가</th>
                  <th className="p-2.5 text-right text-xs font-semibold">종가대비</th>
                  <th className="p-2.5 text-right text-xs font-semibold">등락률</th>
                  <th className="p-2.5 text-right text-xs font-semibold">Aft·누적(억)</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, i) => (
                  <tr
                    key={`${it.symbol}-${i}`}
                    className="border-b border-tima-line last:border-0 hover:bg-tima-bg/50"
                  >
                    <td className="p-2.5">
                      <Link
                        href={`/stocks/${it.symbol}`}
                        className="font-bold text-tima-text hover:text-tima-teal hover:underline"
                      >
                        {it.name ?? it.symbol}
                      </Link>
                      <div className="font-mono text-[10px] text-tima-sub">{it.symbol}</div>
                    </td>
                    <td className="p-2.5 text-right font-mono font-semibold text-tima-text">
                      {fmtNum(it.nxt_price)}
                    </td>
                    <td className="p-2.5 text-right">
                      <PctCell v={it.vs_close_pct} />
                    </td>
                    <td className="p-2.5 text-right">
                      <PctCell v={it.day_change_pct} />
                      <div className="font-mono text-[10px] text-tima-sub">
                        종가 {fmtNum(it.day_close)}
                      </div>
                    </td>
                    <td className="p-2.5 text-right">
                      <div className="font-mono text-tima-text">{fmtNum(it.aft_value)}</div>
                      <div className="font-mono text-[10px] text-tima-sub">
                        {fmtNum(it.cum_value)}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
          {loading ? '불러오는 중…' : 'NXT 애프터마켓 데이터 미연동 (게이트웨이 확장 필요)'}
        </div>
      )}

      {/* NXT 전용 면책 (PRD §5) */}
      <p className="mt-4 text-[11px] leading-relaxed text-tima-sub">
        NXT(넥스트레이드) 시세는 대체거래소 애프터마켓 데이터로, 정규장 시세와 체결·호가 기준이
        다를 수 있습니다. 표시된 수치는 참고용이며 지연·오류가 있을 수 있습니다.
      </p>
      <Disclaimer />
    </div>
  );
}
