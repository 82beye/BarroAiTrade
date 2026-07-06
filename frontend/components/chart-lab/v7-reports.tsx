'use client';

/**
 * 차트 랩 v7 — 최근 30일 매매 추이 (리포트)
 * 원본: frontend/app/(admin)/reports/page.tsx (차트 부분만 추출)
 * 라이브러리: recharts (ComposedChart — Bar + Line, 이중 Y축)
 *
 * 원본 시각 스타일 보존:
 *   좌축 매매건수 파란 막대(#3b82f6, opacity .6), 우축 수익률 앰버 라인(#f59e0b) + dot,
 *   다크 그리드, 축 라벨(매매건수/수익률), 이중 Y축 구성.
 * 데이터는 정적 샘플(ChartPoint 형태 모사).
 */

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

// 정적 샘플: 일별 매매건수 + 수익률(%)
const DATA = (() => {
  const trades = [4, 6, 3, 8, 5, 2, 7, 9, 4, 6, 3, 5, 8, 6, 4, 7, 9, 5, 3, 6, 8, 4, 7, 5, 6, 9, 4, 7, 5, 8];
  const pnl = [0.8, 1.2, -0.6, 1.5, 0.4, -1.1, 2.0, 1.3, -0.4, 0.9, -0.7, 1.1, 1.6, 0.5, -0.3, 1.4, 2.1, 0.6, -0.5, 1.2, 1.8, -0.4, 1.5, 0.7, 1.0, 2.2, -0.3, 1.4, 0.8, 1.9];
  return trades.map((t, i) => {
    const dt = new Date(Date.UTC(2026, 5, 1) + i * 86400000);
    return {
      date: `${String(dt.getUTCMonth() + 1).padStart(2, '0')}-${String(dt.getUTCDate()).padStart(2, '0')}`,
      trades_count: t,
      pnl_pct: pnl[i],
    };
  });
})();

export function V7Reports() {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
      <h2 className="mb-4 text-base font-semibold text-slate-200">최근 30일 매매 추이</h2>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={DATA}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis dataKey="date" tick={{ fill: '#94a3b8', fontSize: 11 }} />
            <YAxis
              yAxisId="left"
              tick={{ fill: '#94a3b8', fontSize: 12 }}
              label={{ value: '매매건수', angle: -90, position: 'insideLeft', fill: '#94a3b8', fontSize: 11 }}
            />
            <YAxis
              yAxisId="right"
              orientation="right"
              tickFormatter={(v) => `${v.toFixed(1)}%`}
              tick={{ fill: '#94a3b8', fontSize: 12 }}
              label={{ value: '수익률', angle: 90, position: 'insideRight', fill: '#94a3b8', fontSize: 11 }}
            />
            <Tooltip
              contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', color: '#e2e8f0' }}
              formatter={(v: any, name: any) => {
                const val = typeof v === 'number' ? v : Number(v) || 0;
                return name === 'trades_count' ? [`${val}건`, '매매건수'] : [`${val.toFixed(2)}%`, '수익률'];
              }}
            />
            <Bar yAxisId="left" dataKey="trades_count" fill="#3b82f6" opacity={0.6} name="trades_count" />
            <Line yAxisId="right" type="monotone" dataKey="pnl_pct" stroke="#f59e0b" strokeWidth={2} dot={{ fill: '#f59e0b' }} name="pnl_pct" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
