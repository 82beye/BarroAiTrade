'use client';

import { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Area, AreaChart, BarChart, Bar, ComposedChart,
} from 'recharts';

interface BalancePoint {
  date: string;
  cash: number;
  eval_total: number;
  total: number;
  position_count: number;
}

interface Holding {
  symbol: string;
  name: string;
  qty: number;
  avg_buy_price: number;
  cur_price: number;
  eval_amount: number;
  pnl: number;
  pnl_rate: number;
}

interface RealtimeBalance {
  total_value: number;
  available_cash: number;
  invested_value: number;
  eval_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  holdings: Holding[];
  position_count: number;
  timestamp: string;
}

interface PnLPoint {
  date: string;
  pnl: number;
  commission: number;
  tax: number;
  net_pnl: number;
}

interface PnLData {
  points: PnLPoint[];
  summary: {
    total_pnl: number;
    total_commission: number;
    total_tax: number;
    trading_days: number;
  };
}

export default function BalancePage() {
  const [historyData, setHistoryData] = useState<BalancePoint[]>([]);
  const [realtime, setRealtime] = useState<RealtimeBalance | null>(null);
  const [pnlData, setPnlData] = useState<PnLData | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'overview' | 'holdings' | 'pnl'>('overview');

  useEffect(() => {
    const ctrl = new AbortController();
    const { signal } = ctrl;
    setLoading(true);

    (async () => {
      try {
        const [histRes, balRes, pnlRes] = await Promise.all([
          fetch(`/api/reports/balance-history?days=${days}`, { signal }),
          fetch('/api/accounts/balance', { signal }),
          fetch(`/api/reports/realized-pnl?days=${days}`, { signal }),
        ]);
        if (histRes.ok) setHistoryData((await histRes.json()).points ?? []);
        if (balRes.ok) setRealtime(await balRes.json());
        if (pnlRes.ok) setPnlData(await pnlRes.json());
      } catch (err) {
        if ((err as Error).name !== 'AbortError') console.error(err);
      } finally {
        setLoading(false);
      }
    })();

    return () => ctrl.abort();
  }, [days]);

  const formatKRW = (v: number) => {
    if (Math.abs(v) >= 1_0000_0000) return `${(v / 1_0000_0000).toFixed(1)}`;
    if (Math.abs(v) >= 1_0000) return `${(v / 1_0000).toFixed(0)}`;
    return v.toLocaleString();
  };

  const latest = historyData.length > 0 ? historyData[historyData.length - 1] : null;
  const first = historyData.length > 0 ? historyData[0] : null;
  const change = latest && first ? latest.total - first.total : 0;
  const changePct = first && first.total > 0 ? (change / first.total) * 100 : 0;

  // Cumulative P&L for chart
  const cumulativePnl = pnlData?.points.reduce<{ date: string; daily: number; cumulative: number }[]>(
    (acc, p) => {
      const prev = acc.length > 0 ? acc[acc.length - 1].cumulative : 0;
      acc.push({ date: p.date, daily: p.net_pnl, cumulative: prev + p.net_pnl });
      return acc;
    }, []
  ) ?? [];

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">잔고 추이</h1>
        <p className="mt-2 text-slate-400">
          실시간 계좌 잔고 + 자산 변동 추이
          {realtime && (
            <span className="ml-2 text-xs text-slate-500">
              ({new Date(realtime.timestamp).toLocaleTimeString('ko-KR')} 기준)
            </span>
          )}
        </p>
      </div>

      {/* Period selector + Tab */}
      <div className="mb-6 flex items-center justify-between">
        <div className="flex gap-2">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                days === d
                  ? 'bg-blue-600 text-white'
                  : 'border border-slate-600 bg-slate-800 text-slate-300 hover:bg-slate-700'
              }`}
            >
              {d}일
            </button>
          ))}
        </div>
        <div className="flex gap-1 rounded-lg bg-slate-800 p-1">
          {([['overview', '총괄'], ['holdings', '보유종목'], ['pnl', '실현손익']] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                tab === key ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Real-time summary cards */}
      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-5">
        <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
          <p className="text-xs font-medium uppercase text-slate-400">총 자산</p>
          <p className="mt-1 text-2xl font-bold text-slate-50">
            {realtime ? `${realtime.total_value.toLocaleString()}원` : latest ? `${latest.total.toLocaleString()}원` : '—'}
          </p>
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
          <p className="text-xs font-medium uppercase text-slate-400">예수금</p>
          <p className="mt-1 text-2xl font-bold text-emerald-400">
            {realtime ? `${realtime.available_cash.toLocaleString()}원` : latest ? `${latest.cash.toLocaleString()}원` : '—'}
          </p>
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
          <p className="text-xs font-medium uppercase text-slate-400">평가금</p>
          <p className="mt-1 text-2xl font-bold text-sky-400">
            {realtime ? `${realtime.eval_value.toLocaleString()}원` : latest ? `${latest.eval_total.toLocaleString()}원` : '—'}
          </p>
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
          <p className="text-xs font-medium uppercase text-slate-400">평가손익</p>
          <p className={`mt-1 text-2xl font-bold ${(realtime?.total_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {realtime
              ? `${realtime.total_pnl >= 0 ? '+' : ''}${realtime.total_pnl.toLocaleString()}원`
              : '—'}
          </p>
          {realtime && (
            <p className={`text-xs ${realtime.total_pnl_pct >= 0 ? 'text-emerald-500' : 'text-red-500'}`}>
              {realtime.total_pnl_pct >= 0 ? '+' : ''}{realtime.total_pnl_pct.toFixed(2)}%
            </p>
          )}
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
          <p className="text-xs font-medium uppercase text-slate-400">기간 변동</p>
          <p className={`mt-1 text-2xl font-bold ${change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {historyData.length > 0
              ? `${change >= 0 ? '+' : ''}${change.toLocaleString()}원`
              : '—'}
          </p>
          {historyData.length > 0 && (
            <p className={`text-xs ${changePct >= 0 ? 'text-emerald-500' : 'text-red-500'}`}>
              {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
            </p>
          )}
        </div>
      </div>

      {/* Tab content */}
      {tab === 'overview' && (
        <>
          {/* Asset trend chart */}
          <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">
              총 자산 추이
            </h2>
            {loading ? (
              <div className="flex h-80 items-center justify-center text-slate-500">로딩 중...</div>
            ) : historyData.length === 0 ? (
              <div className="flex h-80 items-center justify-center text-slate-500">
                데이터가 없습니다. 장중 데몬 실행 시 자동 기록됩니다.
              </div>
            ) : (
              <div className="h-80 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={historyData}>
                    <defs>
                      <linearGradient id="totalGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(v) => v.slice(5)}
                      tick={{ fill: '#94a3b8', fontSize: 11 }}
                    />
                    <YAxis
                      tickFormatter={formatKRW}
                      tick={{ fill: '#94a3b8', fontSize: 11 }}
                      domain={['dataMin - 1000000', 'dataMax + 1000000']}
                    />
                    <Tooltip
                      contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', color: '#e2e8f0' }}
                      formatter={(v: number, name: string) => {
                        const label = name === 'total' ? '총 자산' : name === 'cash' ? '예수금' : '평가금';
                        return [`${v.toLocaleString()}원`, label];
                      }}
                      labelFormatter={(label) => `${label}`}
                    />
                    <Area
                      type="monotone"
                      dataKey="total"
                      stroke="#6366f1"
                      strokeWidth={2}
                      fill="url(#totalGrad)"
                      dot={{ fill: '#6366f1', r: 4 }}
                      name="total"
                    />
                    <Line
                      type="monotone"
                      dataKey="cash"
                      stroke="#10b981"
                      strokeWidth={1.5}
                      strokeDasharray="5 5"
                      dot={false}
                      name="cash"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* Daily balance table */}
          {historyData.length > 0 && (
            <div className="mt-6 rounded-xl border border-slate-700 bg-slate-800 p-4">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">
                일별 잔고
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-700 text-left text-slate-400">
                      <th className="pb-3 font-medium">날짜</th>
                      <th className="pb-3 text-right font-medium">총 자산</th>
                      <th className="pb-3 text-right font-medium">예수금</th>
                      <th className="pb-3 text-right font-medium">평가금</th>
                      <th className="pb-3 text-right font-medium">보유종목</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...historyData].reverse().map((p, i) => (
                      <tr key={i} className="border-b border-slate-800 last:border-0 hover:bg-slate-700 hover:bg-opacity-30">
                        <td className="py-2.5 font-mono text-xs text-slate-300">{p.date}</td>
                        <td className="py-2.5 text-right font-mono font-semibold text-slate-200">
                          {p.total.toLocaleString()}원
                        </td>
                        <td className="py-2.5 text-right font-mono text-emerald-400">
                          {p.cash.toLocaleString()}원
                        </td>
                        <td className="py-2.5 text-right font-mono text-sky-400">
                          {p.eval_total.toLocaleString()}원
                        </td>
                        <td className="py-2.5 text-right text-slate-400">{p.position_count}종목</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {tab === 'holdings' && (
        <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">
            보유 종목 ({realtime?.position_count ?? 0}종목)
          </h2>
          {!realtime || realtime.holdings.length === 0 ? (
            <div className="py-12 text-center text-slate-500">보유 종목이 없습니다.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-left text-slate-400">
                    <th className="pb-3 font-medium">종목</th>
                    <th className="pb-3 text-right font-medium">수량</th>
                    <th className="pb-3 text-right font-medium">매입가</th>
                    <th className="pb-3 text-right font-medium">현재가</th>
                    <th className="pb-3 text-right font-medium">평가금액</th>
                    <th className="pb-3 text-right font-medium">손익</th>
                    <th className="pb-3 text-right font-medium">수익률</th>
                  </tr>
                </thead>
                <tbody>
                  {realtime.holdings
                    .sort((a, b) => Math.abs(b.eval_amount) - Math.abs(a.eval_amount))
                    .map((h, i) => (
                    <tr key={i} className="border-b border-slate-800 last:border-0 hover:bg-slate-700 hover:bg-opacity-30">
                      <td className="py-2.5">
                        <div className="font-semibold text-slate-200">{h.name}</div>
                        <div className="text-xs text-slate-500">{h.symbol}</div>
                      </td>
                      <td className="py-2.5 text-right font-mono text-slate-300">{h.qty}주</td>
                      <td className="py-2.5 text-right font-mono text-slate-300">
                        {h.avg_buy_price.toLocaleString()}원
                      </td>
                      <td className="py-2.5 text-right font-mono text-slate-200">
                        {h.cur_price.toLocaleString()}원
                      </td>
                      <td className="py-2.5 text-right font-mono text-slate-200">
                        {h.eval_amount.toLocaleString()}원
                      </td>
                      <td className={`py-2.5 text-right font-mono ${h.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {h.pnl >= 0 ? '+' : ''}{h.pnl.toLocaleString()}원
                      </td>
                      <td className={`py-2.5 text-right font-mono font-semibold ${h.pnl_rate >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {h.pnl_rate >= 0 ? '+' : ''}{h.pnl_rate.toFixed(2)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'pnl' && (
        <>
          {/* P&L summary */}
          {pnlData && (
            <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-4">
              <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
                <p className="text-xs font-medium uppercase text-slate-400">총 실현손익</p>
                <p className={`mt-1 text-2xl font-bold ${pnlData.summary.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {pnlData.summary.total_pnl >= 0 ? '+' : ''}{pnlData.summary.total_pnl.toLocaleString()}원
                </p>
              </div>
              <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
                <p className="text-xs font-medium uppercase text-slate-400">수수료 합계</p>
                <p className="mt-1 text-2xl font-bold text-orange-400">
                  {pnlData.summary.total_commission.toLocaleString()}원
                </p>
              </div>
              <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
                <p className="text-xs font-medium uppercase text-slate-400">세금 합계</p>
                <p className="mt-1 text-2xl font-bold text-orange-400">
                  {pnlData.summary.total_tax.toLocaleString()}원
                </p>
              </div>
              <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
                <p className="text-xs font-medium uppercase text-slate-400">매매일수</p>
                <p className="mt-1 text-2xl font-bold text-slate-200">
                  {pnlData.summary.trading_days}일
                </p>
              </div>
            </div>
          )}

          {/* Cumulative P&L chart */}
          {cumulativePnl.length > 0 && (
            <div className="mb-6 rounded-xl border border-slate-700 bg-slate-800 p-4">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">
                실현손익 추이 (누적)
              </h2>
              <div className="h-72 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={cumulativePnl}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(v) => v.slice(5)}
                      tick={{ fill: '#94a3b8', fontSize: 11 }}
                    />
                    <YAxis
                      yAxisId="left"
                      tickFormatter={formatKRW}
                      tick={{ fill: '#94a3b8', fontSize: 11 }}
                    />
                    <YAxis
                      yAxisId="right"
                      orientation="right"
                      tickFormatter={formatKRW}
                      tick={{ fill: '#94a3b8', fontSize: 11 }}
                    />
                    <Tooltip
                      contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', color: '#e2e8f0' }}
                      formatter={(v: number, name: string) => {
                        const label = name === 'daily' ? '일별 손익' : '누적 손익';
                        return [`${v.toLocaleString()}원`, label];
                      }}
                      labelFormatter={(label) => `${label}`}
                    />
                    <Bar yAxisId="left" dataKey="daily" name="daily" fill="#6366f1" opacity={0.6} />
                    <Line
                      yAxisId="right"
                      type="monotone"
                      dataKey="cumulative"
                      name="cumulative"
                      stroke="#f59e0b"
                      strokeWidth={2}
                      dot={{ fill: '#f59e0b', r: 3 }}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Daily P&L table */}
          {pnlData && pnlData.points.length > 0 && (
            <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
              <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">
                일자별 실현손익
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-700 text-left text-slate-400">
                      <th className="pb-3 font-medium">날짜</th>
                      <th className="pb-3 text-right font-medium">실현손익</th>
                      <th className="pb-3 text-right font-medium">수수료</th>
                      <th className="pb-3 text-right font-medium">세금</th>
                      <th className="pb-3 text-right font-medium">순손익</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...pnlData.points].reverse().map((p, i) => (
                      <tr key={i} className="border-b border-slate-800 last:border-0 hover:bg-slate-700 hover:bg-opacity-30">
                        <td className="py-2.5 font-mono text-xs text-slate-300">{p.date}</td>
                        <td className={`py-2.5 text-right font-mono ${p.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {p.pnl >= 0 ? '+' : ''}{p.pnl.toLocaleString()}원
                        </td>
                        <td className="py-2.5 text-right font-mono text-orange-400">
                          {p.commission.toLocaleString()}원
                        </td>
                        <td className="py-2.5 text-right font-mono text-orange-400">
                          {p.tax.toLocaleString()}원
                        </td>
                        <td className={`py-2.5 text-right font-mono font-semibold ${p.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {p.net_pnl >= 0 ? '+' : ''}{p.net_pnl.toLocaleString()}원
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {!pnlData && !loading && (
            <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
              <div className="py-12 text-center text-slate-500">
                실현손익 데이터를 가져올 수 없습니다. 백엔드 연결을 확인하세요.
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
