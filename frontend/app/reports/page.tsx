'use client';

import { useState, useEffect, useCallback } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';

interface DailySummary {
  trades_count: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  pnl: number;
  pnl_pct: number;
}

interface TradeRecord {
  symbol: string;
  side: 'buy' | 'sell';
  entry_price: number;
  exit_price?: number;
  pnl?: number;
  entry_time: string;
  exit_time?: string;
}

interface DailyReport {
  date: string;
  summary: DailySummary;
  trades: TradeRecord[];
}

interface ChartPoint {
  date: string;
  pnl_pct: number;
}

function today() {
  return new Date().toISOString().split('T')[0];
}

export default function ReportsPage() {
  const [selectedDate, setSelectedDate] = useState(today());
  const [report, setReport] = useState<DailyReport | null>(null);
  const [chartData, setChartData] = useState<ChartPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<'time' | 'pnl'>('time');

  const fetchReport = useCallback(async (date: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/reports/daily?date_str=${date}`);
      if (!res.ok) throw new Error(`${res.status}`);
      const data: DailyReport = await res.json();
      setReport(data);

      // 최근 7일 차트 데이터 병렬 조회
      const days = Array.from({ length: 7 }, (_, i) => {
        const d = new Date(date);
        d.setDate(d.getDate() - i);
        return d.toISOString().split('T')[0];
      }).reverse();

      const results = await Promise.allSettled(
        days.map((d) =>
          fetch(`/api/reports/daily?date_str=${d}`)
            .then((r) => (r.ok ? r.json() : null))
            .then((d2): ChartPoint | null =>
              d2 ? { date: d2.date?.slice(5), pnl_pct: d2.summary?.pnl_pct ?? 0 } : null
            )
        )
      );
      setChartData(
        results
          .filter((r): r is PromiseFulfilledResult<ChartPoint> => r.status === 'fulfilled' && r.value !== null)
          .map((r) => r.value)
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : '조회 실패');
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchReport(selectedDate); }, [selectedDate, fetchReport]);

  const sortedTrades = report
    ? [...report.trades].sort((a, b) => {
        if (sortBy === 'time') return new Date(b.entry_time).getTime() - new Date(a.entry_time).getTime();
        return (b.pnl ?? 0) - (a.pnl ?? 0);
      })
    : [];

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">리포트</h1>
        <p className="mt-2 text-slate-400">일일 손익 분석 및 매매 내역 조회</p>
      </div>

      {/* 날짜 선택 */}
      <Card className="mb-6 border-slate-700 bg-slate-800">
        <CardContent className="pt-4">
          <div className="flex items-center gap-4">
            <label className="text-sm font-medium text-slate-300">날짜 선택:</label>
            <input
              type="date"
              value={selectedDate}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-slate-200"
            />
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="mb-4 rounded-lg border border-red-700 bg-red-900 bg-opacity-30 px-4 py-3 text-sm text-red-300">
          {error} — 해당 날짜에 리포트가 없거나 백엔드가 응답하지 않습니다.
        </div>
      )}

      {/* 요약 카드 */}
      {loading ? (
        <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
      ) : report ? (
        <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
          <Card className="border-slate-700 bg-slate-800">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-slate-400">일일 수익</CardTitle>
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${report.summary.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {report.summary.pnl >= 0 ? '+' : ''}{report.summary.pnl.toLocaleString()}원
              </div>
              <p className="mt-1 text-xs text-slate-500">{report.date} 기준</p>
            </CardContent>
          </Card>

          <Card className="border-slate-700 bg-slate-800">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-slate-400">수익률</CardTitle>
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${report.summary.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {report.summary.pnl_pct >= 0 ? '+' : ''}{report.summary.pnl_pct.toFixed(2)}%
              </div>
              <p className="mt-1 text-xs text-slate-500">
                {report.summary.trades_count}건 · 승률 {report.summary.win_rate.toFixed(1)}%
              </p>
            </CardContent>
          </Card>

          <Card className="border-slate-700 bg-slate-800">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-slate-400">승/패</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-slate-200">
                <span className="text-green-400">{report.summary.win_count}승</span>
                {' / '}
                <span className="text-red-400">{report.summary.loss_count}패</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">총 {report.summary.trades_count}건</p>
            </CardContent>
          </Card>
        </div>
      ) : null}

      {/* 수익률 차트 */}
      {chartData.length > 0 && (
        <Card className="mb-6 border-slate-700 bg-slate-800">
          <CardHeader>
            <CardTitle className="text-slate-200">최근 7일 수익률 추이</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis dataKey="date" tick={{ fill: '#94a3b8', fontSize: 12 }} />
                  <YAxis
                    tickFormatter={(v) => `${v.toFixed(1)}%`}
                    tick={{ fill: '#94a3b8', fontSize: 12 }}
                  />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', color: '#e2e8f0' }}
                    formatter={(v) => [typeof v === 'number' ? `${v.toFixed(2)}%` : '—', '수익률']}
                  />
                  <Line
                    type="monotone"
                    dataKey="pnl_pct"
                    stroke="#6366f1"
                    strokeWidth={2}
                    dot={{ fill: '#6366f1' }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 매매 내역 */}
      <Card className="border-slate-700 bg-slate-800">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-slate-200">매매 내역</CardTitle>
              <CardDescription className="text-slate-500">
                {selectedDate} 거래 기록 ({sortedTrades.length}건)
              </CardDescription>
            </div>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as 'time' | 'pnl')}
              className="rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-slate-200"
            >
              <option value="time">시간순</option>
              <option value="pnl">수익순</option>
            </select>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-2">
              {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-12 w-full rounded" />)}
            </div>
          ) : sortedTrades.length === 0 ? (
            <div className="py-12 text-center text-slate-500">
              {error ? '백엔드 연결 후 거래 내역이 표시됩니다.' : '해당 날짜의 거래 내역이 없습니다.'}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-left text-slate-400">
                    <th className="pb-3 font-medium">시간</th>
                    <th className="pb-3 font-medium">종목</th>
                    <th className="pb-3 font-medium">방향</th>
                    <th className="pb-3 text-right font-medium">진입가</th>
                    <th className="pb-3 text-right font-medium">청산가</th>
                    <th className="pb-3 text-right font-medium">수익금</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedTrades.map((trade, i) => (
                    <tr key={i} className="border-b border-slate-800 last:border-0 hover:bg-slate-700 hover:bg-opacity-30">
                      <td className="py-3 font-mono text-xs text-slate-400">
                        {new Date(trade.entry_time).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}
                      </td>
                      <td className="py-3 font-semibold text-slate-200">{trade.symbol}</td>
                      <td className="py-3">
                        <Badge className={trade.side === 'buy' ? 'bg-blue-600 text-white' : 'bg-orange-600 text-white'}>
                          {trade.side === 'buy' ? '매수' : '매도'}
                        </Badge>
                      </td>
                      <td className="py-3 text-right font-mono text-slate-300">
                        {trade.entry_price.toLocaleString()}원
                      </td>
                      <td className="py-3 text-right font-mono text-slate-300">
                        {trade.exit_price ? `${trade.exit_price.toLocaleString()}원` : '—'}
                      </td>
                      <td className={`py-3 text-right font-semibold ${(trade.pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {trade.pnl != null
                          ? `${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toLocaleString()}원`
                          : '—'}
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
