'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Disclaimer } from '@/components/layout/disclaimer';
import {
  api,
  type InvestorsResponse,
  type InvestorFlow,
  type MarketIndex,
} from '@/lib/api';

const POLL_MS = 30_000;

function fmtSigned(n?: number | null): string {
  if (n === null || n === undefined) return '-';
  const s = Math.round(n).toLocaleString('ko-KR');
  return n > 0 ? `+${s}` : s;
}

function fmtNum(n?: number | null, digits = 2): string {
  return n === null || n === undefined
    ? '-'
    : n.toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function flowColor(n?: number | null): string {
  if (n === null || n === undefined) return 'text-tima-sub';
  return n > 0 ? 'text-tima-up' : n < 0 ? 'text-tima-down' : 'text-tima-text';
}

// 투자자별 매매동향 셀 (개인/외국인/기관 × 코스피/코스닥)
function FlowCell({ v }: { v?: number | null }) {
  return <span className={`font-mono ${flowColor(v)}`}>{fmtSigned(v)}</span>;
}

const INVESTOR_ROWS: { key: keyof InvestorFlow; label: string }[] = [
  { key: 'individual', label: '개인' },
  { key: 'foreign', label: '외국인' },
  { key: 'institution', label: '기관' },
];

export default function MarketOverviewPage() {
  const [investors, setInvestors] = useState<InvestorsResponse | null>(null);
  const [invStatus, setInvStatus] = useState<string>('');
  const [indices, setIndices] = useState<MarketIndex[]>([]);
  const [idxStatus, setIdxStatus] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    const [invRes, idxRes] = await Promise.allSettled([
      api.getInvestors(),
      api.getMarketIndices(),
    ]);

    if (invRes.status === 'fulfilled') {
      const d = invRes.value.data as InvestorsResponse;
      setInvStatus(d?.status ?? 'unsupported');
      setInvestors(d?.status === 'ok' ? d : null);
    } else {
      setInvStatus('unsupported');
      setInvestors(null);
    }

    if (idxRes.status === 'fulfilled') {
      const d = idxRes.value.data;
      setIdxStatus(d?.status ?? 'unsupported');
      setIndices(d?.status === 'ok' && Array.isArray(d.items) ? d.items : []);
    } else {
      setIdxStatus('unsupported');
      setIndices([]);
    }

    setLoading(false);
    setLastUpdated(new Date());
  }, []);

  useEffect(() => {
    load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(load, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load]);

  const invOk = invStatus === 'ok' && investors;
  const idxOk = idxStatus === 'ok' && indices.length > 0;

  return (
    <div className="p-3">
      <div className="mb-2 text-right text-[11px] text-tima-sub">
        {lastUpdated ? `${lastUpdated.toLocaleTimeString('ko-KR')} · 30초 갱신` : '30초 자동 갱신'}
      </div>

      {/* ① 매매동향 (억원) */}
      <section className="mb-4">
        <h2 className="mb-1.5 text-sm font-bold text-tima-teal">매매동향 (억원)</h2>
        <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
          {loading ? (
            <div className="py-10 text-center text-tima-sub">불러오는 중…</div>
          ) : invOk ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
                  <th className="p-2.5 text-left text-xs font-semibold">투자자</th>
                  <th className="p-2.5 text-right text-xs font-semibold">코스피</th>
                  <th className="p-2.5 text-right text-xs font-semibold">코스닥</th>
                </tr>
              </thead>
              <tbody>
                {INVESTOR_ROWS.map((r) => (
                  <tr key={r.key} className="border-b border-tima-line last:border-0">
                    <td className="p-2.5 font-semibold text-tima-text">{r.label}</td>
                    <td className="p-2.5 text-right">
                      <FlowCell v={investors!.kospi?.[r.key]} />
                    </td>
                    <td className="p-2.5 text-right">
                      <FlowCell v={investors!.kosdaq?.[r.key]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="py-10 text-center text-tima-sub">매매동향 데이터 미연동</div>
          )}
        </div>
      </section>

      {/* ② 지수 표 */}
      <section className="mb-4">
        <h2 className="mb-1.5 text-sm font-bold text-tima-teal">지수</h2>
        <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
          {loading ? (
            <div className="py-10 text-center text-tima-sub">불러오는 중…</div>
          ) : idxOk ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
                  <th className="p-2.5 text-left text-xs font-semibold">구분</th>
                  <th className="p-2.5 text-right text-xs font-semibold">지수</th>
                  <th className="p-2.5 text-right text-xs font-semibold">등락</th>
                  <th className="p-2.5 text-right text-xs font-semibold">등락률</th>
                </tr>
              </thead>
              <tbody>
                {indices.map((ix) => {
                  const up = (ix.change ?? 0) >= 0;
                  const dir = up ? 'text-tima-up' : 'text-tima-down';
                  return (
                    <tr key={ix.code} className="border-b border-tima-line last:border-0">
                      <td className="p-2.5 font-semibold text-tima-text">{ix.name}</td>
                      <td className={`p-2.5 text-right font-mono font-semibold ${dir}`}>
                        {fmtNum(ix.value)}
                      </td>
                      <td className={`p-2.5 text-right font-mono ${dir}`}>
                        {up ? '▲' : '▼'} {fmtNum(Math.abs(ix.change ?? 0))}
                      </td>
                      <td className={`p-2.5 text-right font-mono ${dir}`}>
                        {up ? '+' : ''}
                        {fmtNum(ix.change_pct)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="py-10 text-center text-tima-sub">지수 데이터 미연동</div>
          )}
        </div>
      </section>

      <Disclaimer />
    </div>
  );
}
