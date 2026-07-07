'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Disclaimer } from '@/components/layout/disclaimer';
import {
  api,
  type AccountBalance,
  type BalanceHistoryPoint,
  type RealizedPnlResponse,
} from '@/lib/api';

// ── 티마 계좌 화면 (라이트 셸 · 보유종목/실현손익/총괄) ──
// 키움 키 미연동 개발 환경에선 API 가 빈/에러 → 모든 뷰가 "미연동" 빈 상태로 강등.

type AccountTab = 'overview' | 'holdings' | 'pnl';

const TABS: { key: AccountTab; label: string }[] = [
  { key: 'holdings', label: '보유종목' },
  { key: 'pnl', label: '실현손익' },
  { key: 'overview', label: '총괄' },
];

const DAYS_OPTIONS = [7, 30, 90];
const POLL_MS = 30_000;

// 원화 (천단위 콤마)
function fmtKRW(n?: number | null): string {
  return n === null || n === undefined ? '-' : `${Math.round(n).toLocaleString('ko-KR')}원`;
}

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

// 축약(억/만) — 차트 축 라벨용
function fmtAxis(n: number): string {
  const a = Math.abs(n);
  if (a >= 1_0000_0000) return `${(n / 1_0000_0000).toFixed(1)}억`;
  if (a >= 1_0000) return `${Math.round(n / 1_0000).toLocaleString('ko-KR')}만`;
  return `${n}`;
}

// 손익 방향 색 (상승=빨강 tima-up / 하락=파랑 tima-down / 0=회색)
function pnlColor(v?: number | null): string {
  if (v === null || v === undefined || v === 0) return 'text-tima-sub';
  return v > 0 ? 'text-tima-up' : 'text-tima-down';
}

// +▲/-▼ 접두 (손익 원)
function SignedKRW({ v }: { v?: number | null }) {
  if (v === null || v === undefined) return <span className="text-tima-sub">-</span>;
  const up = v > 0;
  const flat = v === 0;
  return (
    <span className={`font-mono font-semibold ${pnlColor(v)}`}>
      {flat ? '' : up ? '+' : '-'}
      {Math.abs(Math.round(v)).toLocaleString('ko-KR')}원
    </span>
  );
}

// ▲/▼ 접두 (손익률 %)
function SignedPct({ v, className = '' }: { v?: number | null; className?: string }) {
  if (v === null || v === undefined) return <span className="text-tima-sub">-</span>;
  const up = v > 0;
  const flat = v === 0;
  return (
    <span className={`font-mono ${pnlColor(v)} ${className}`}>
      {flat ? '' : up ? '▲ ' : '▼ '}
      {Math.abs(v).toFixed(2)}%
    </span>
  );
}

const CHART_TEAL = '#10B8A8';
const AXIS_GREY = '#777777';
const GRID_LINE = '#E0E0E0';

const tooltipStyle = {
  backgroundColor: '#FFFFFF',
  border: '1px solid #E0E0E0',
  borderRadius: 8,
  color: '#1A1A1A',
  fontSize: 12,
};

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-sm text-tima-sub">
      {text}
    </div>
  );
}

export default function AccountPage() {
  const [balance, setBalance] = useState<AccountBalance | null>(null);
  const [history, setHistory] = useState<BalanceHistoryPoint[]>([]);
  const [pnl, setPnl] = useState<RealizedPnlResponse | null>(null);
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState<AccountTab>('holdings');
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (d: number) => {
    const [balR, histR, pnlR] = await Promise.allSettled([
      api.getBalance(),
      api.getBalanceHistory(d),
      api.getRealizedPnl(d),
    ]);
    setBalance(balR.status === 'fulfilled' ? (balR.value.data ?? null) : null);
    setHistory(
      histR.status === 'fulfilled' && Array.isArray(histR.value.data?.points)
        ? histR.value.data.points
        : [],
    );
    setPnl(pnlR.status === 'fulfilled' ? (pnlR.value.data ?? null) : null);
    setLoading(false);
    setLastUpdated(new Date());
  }, []);

  useEffect(() => {
    setLoading(true);
    load(days);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => load(days), POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [days, load]);

  const holdings = useMemo(
    () =>
      (balance?.holdings ?? [])
        .slice()
        .sort((a, b) => Math.abs(b.eval_amount) - Math.abs(a.eval_amount)),
    [balance],
  );

  // 실현손익 누적 (막대차트용)
  const pnlSeries = useMemo(() => {
    const pts = pnl?.points ?? [];
    let acc = 0;
    return pts.map((p) => {
      acc += p.net_pnl;
      return { date: p.date, net: p.net_pnl, cum: acc };
    });
  }, [pnl]);

  const hasBalance = !!balance && (balance.total_value > 0 || (balance.holdings?.length ?? 0) > 0);

  return (
    <div className="p-3">
      {/* 요약 카드 */}
      {hasBalance ? (
        <div className="rounded-xl border border-tima-line bg-white p-4">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-tima-sub">
            총 평가자산
          </p>
          <p className="mt-0.5 font-mono text-2xl font-bold text-tima-text">
            {fmtKRW(balance!.total_value)}
          </p>
          <div className="mt-1 flex items-baseline gap-2">
            <SignedKRW v={balance!.total_pnl} />
            <SignedPct v={balance!.total_pnl_pct} className="text-sm font-semibold" />
          </div>

          <div className="mt-3 grid grid-cols-3 gap-2 border-t border-tima-line pt-3">
            <div>
              <p className="text-[10px] text-tima-sub">예수금</p>
              <p className="mt-0.5 font-mono text-sm font-semibold text-tima-text">
                {fmtKRW(balance!.available_cash)}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-tima-sub">총매입</p>
              <p className="mt-0.5 font-mono text-sm font-semibold text-tima-text">
                {fmtKRW(balance!.invested_value)}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-tima-sub">평가금</p>
              <p className="mt-0.5 font-mono text-sm font-semibold text-tima-text">
                {fmtKRW(balance!.eval_value)}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <EmptyState
          text={loading ? '불러오는 중…' : '계좌 데이터 미연동 (장중·키 연결 시 표시)'}
        />
      )}

      {/* 탭 pill (활성 tima-select 분홍) */}
      <div className="mb-2 mt-3 flex gap-1.5">
        {TABS.map((t) => {
          const on = t.key === tab;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex-1 rounded-full px-3 py-1.5 text-sm font-semibold transition-colors ${
                on
                  ? 'bg-tima-select text-white'
                  : 'border border-tima-line bg-white text-tima-sub hover:bg-tima-bg'
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {/* ── 총괄 ── */}
      {tab === 'overview' && (
        <>
          {/* days 셀렉터 */}
          <div className="mb-2 flex items-center justify-between">
            <div className="flex gap-1">
              {DAYS_OPTIONS.map((d) => (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
                    days === d
                      ? 'bg-tima-teal text-white'
                      : 'border border-tima-line bg-white text-tima-sub hover:bg-tima-bg'
                  }`}
                >
                  {d}일
                </button>
              ))}
            </div>
            <span className="text-[11px] text-tima-sub">자산추이</span>
          </div>

          {history.length > 0 ? (
            <div className="rounded-lg border border-tima-line bg-white p-3">
              <div className="h-52 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={history} margin={{ top: 4, right: 6, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="acctTotalGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={CHART_TEAL} stopOpacity={0.3} />
                        <stop offset="95%" stopColor={CHART_TEAL} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke={GRID_LINE} />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(v: string) => (v?.length >= 5 ? v.slice(5) : v)}
                      tick={{ fill: AXIS_GREY, fontSize: 10 }}
                      tickLine={false}
                      axisLine={{ stroke: GRID_LINE }}
                    />
                    <YAxis
                      width={44}
                      tickFormatter={fmtAxis}
                      tick={{ fill: AXIS_GREY, fontSize: 10 }}
                      tickLine={false}
                      axisLine={false}
                      domain={['dataMin', 'dataMax']}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v: any) => [fmtKRW(Number(v)), '총자산']}
                      labelFormatter={(l) => `${l}`}
                    />
                    <Area
                      type="monotone"
                      dataKey="total"
                      stroke={CHART_TEAL}
                      strokeWidth={2}
                      fill="url(#acctTotalGrad)"
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <EmptyState
              text={loading ? '불러오는 중…' : '자산추이 데이터 미연동 (장중 데몬 기록 시 표시)'}
            />
          )}

          {/* 손익 요약 */}
          {pnl?.summary && (
            <div className="mt-3 grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-tima-line bg-white p-3">
                <p className="text-[10px] text-tima-sub">기간 실현손익</p>
                <p className="mt-0.5 text-base">
                  <SignedKRW v={pnl.summary.total_pnl} />
                </p>
              </div>
              <div className="rounded-lg border border-tima-line bg-white p-3">
                <p className="text-[10px] text-tima-sub">매매일수</p>
                <p className="mt-0.5 font-mono text-base font-semibold text-tima-text">
                  {pnl.summary.trading_days}일
                </p>
              </div>
            </div>
          )}
        </>
      )}

      {/* ── 보유종목 ── */}
      {tab === 'holdings' && (
        <>
          {holdings.length > 0 ? (
            <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
                      <th className="p-2.5 text-left text-xs font-semibold">종목명</th>
                      <th className="p-2.5 text-right text-xs font-semibold">수량·평단</th>
                      <th className="p-2.5 text-right text-xs font-semibold">현재가·평가</th>
                      <th className="p-2.5 text-right text-xs font-semibold">손익률</th>
                    </tr>
                  </thead>
                  <tbody>
                    {holdings.map((h, i) => (
                      <tr
                        key={`${h.symbol}-${i}`}
                        className="border-b border-tima-line last:border-0 hover:bg-tima-bg/50"
                      >
                        <td className="p-2.5">
                          <Link
                            href={`/stocks/${h.symbol}`}
                            className="font-bold text-tima-text hover:text-tima-teal hover:underline"
                          >
                            {h.name ?? h.symbol}
                          </Link>
                          <div className="font-mono text-[10px] text-tima-sub">{h.symbol}</div>
                        </td>
                        <td className="p-2.5 text-right">
                          <div className="font-mono text-tima-text">{fmtNum(h.qty)}주</div>
                          <div className="font-mono text-[10px] text-tima-sub">
                            {fmtNum(h.avg_buy_price)}
                          </div>
                        </td>
                        <td className="p-2.5 text-right">
                          <div className="font-mono font-semibold text-tima-text">
                            {fmtNum(h.cur_price)}
                          </div>
                          <div className="font-mono text-[10px] text-tima-sub">
                            {fmtNum(Math.round(h.eval_amount))}
                          </div>
                        </td>
                        <td className="p-2.5 text-right">
                          <div>
                            <SignedPct v={h.pnl_rate} className="text-sm font-semibold" />
                          </div>
                          <div className="text-[10px]">
                            <SignedKRW v={h.pnl} />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <EmptyState
              text={
                loading
                  ? '불러오는 중…'
                  : hasBalance
                    ? '보유 종목 없음'
                    : '계좌 데이터 미연동 (장중·키 연결 시 표시)'
              }
            />
          )}
        </>
      )}

      {/* ── 실현손익 ── */}
      {tab === 'pnl' && (
        <>
          {/* days 셀렉터 */}
          <div className="mb-2 flex items-center justify-between">
            <div className="flex gap-1">
              {DAYS_OPTIONS.map((d) => (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
                    days === d
                      ? 'bg-tima-teal text-white'
                      : 'border border-tima-line bg-white text-tima-sub hover:bg-tima-bg'
                  }`}
                >
                  {d}일
                </button>
              ))}
            </div>
            {pnl?.summary && (
              <span className="text-[11px]">
                순손익 <SignedKRW v={pnl.summary.total_pnl} />
              </span>
            )}
          </div>

          {pnlSeries.length > 0 ? (
            <>
              {/* 일별 순손익 막대 */}
              <div className="rounded-lg border border-tima-line bg-white p-3">
                <div className="h-44 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={pnlSeries} margin={{ top: 4, right: 6, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={GRID_LINE} />
                      <XAxis
                        dataKey="date"
                        tickFormatter={(v: string) => (v?.length >= 5 ? v.slice(5) : v)}
                        tick={{ fill: AXIS_GREY, fontSize: 10 }}
                        tickLine={false}
                        axisLine={{ stroke: GRID_LINE }}
                      />
                      <YAxis
                        width={44}
                        tickFormatter={fmtAxis}
                        tick={{ fill: AXIS_GREY, fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                      />
                      <Tooltip
                        contentStyle={tooltipStyle}
                        formatter={(v: any, name: any) => [
                          fmtKRW(Number(v)),
                          name === 'cum' ? '누적' : '순손익',
                        ]}
                        labelFormatter={(l) => `${l}`}
                        cursor={{ fill: 'rgba(0,0,0,0.04)' }}
                      />
                      <Bar dataKey="net" fill={CHART_TEAL} radius={[2, 2, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* 일자별 실현손익 표 */}
              <div className="mt-3 overflow-hidden rounded-lg border border-tima-line bg-white">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
                        <th className="p-2.5 text-left text-xs font-semibold">날짜</th>
                        <th className="p-2.5 text-right text-xs font-semibold">실현손익</th>
                        <th className="p-2.5 text-right text-xs font-semibold">수수료·세금</th>
                        <th className="p-2.5 text-right text-xs font-semibold">순손익</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(pnl?.points ?? [])
                        .slice()
                        .reverse()
                        .map((p, i) => (
                          <tr
                            key={`${p.date}-${i}`}
                            className="border-b border-tima-line last:border-0 hover:bg-tima-bg/50"
                          >
                            <td className="p-2.5 font-mono text-xs text-tima-text">{p.date}</td>
                            <td className="p-2.5 text-right text-xs">
                              <SignedKRW v={p.pnl} />
                            </td>
                            <td className="p-2.5 text-right font-mono text-[11px] text-tima-sub">
                              {fmtNum(Math.round(p.commission))}·{fmtNum(Math.round(p.tax))}
                            </td>
                            <td className="p-2.5 text-right text-xs">
                              <SignedKRW v={p.net_pnl} />
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          ) : (
            <EmptyState
              text={loading ? '불러오는 중…' : '실현손익 데이터 미연동 (매매 발생 시 표시)'}
            />
          )}
        </>
      )}

      {/* 갱신 시각 */}
      <div className="mt-3 text-right text-[11px] text-tima-sub">
        {lastUpdated
          ? `${lastUpdated.toLocaleTimeString('ko-KR')} · ${loading ? '갱신 중…' : '30초 갱신'}`
          : '30초 자동 갱신'}
      </div>

      <Disclaimer />
    </div>
  );
}
