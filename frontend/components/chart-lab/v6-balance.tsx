'use client';

/**
 * 차트 랩 v6 — 총 자산 추이 (잔고)
 * 원본: frontend/app/(admin)/balance/page.tsx (차트 부분만 추출)
 * 라이브러리: recharts (AreaChart + Line)
 *
 * 원본 시각 스타일 보존:
 *   인디고(#6366f1) 총자산 에어리어 + dot, 초록(#10b981) 예수금 점선 라인,
 *   다크 그리드(#334155), 축·툴팁 slate 톤, 원화 축약 포맷.
 * 데이터는 정적 샘플(BalancePoint 형태 모사).
 */

import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

// 정적 샘플: 일별 총자산/예수금 (원)
const DATA = (() => {
  const out: { date: string; total: number; cash: number }[] = [];
  let total = 50_000_000;
  let cash = 12_000_000;
  const seq = [0.2, 0.5, -0.3, 0.8, 1.1, -0.4, 0.6, 0.9, 1.3, -0.5, 0.7, 1.0, 0.4, -0.6, 0.8, 1.2, 0.5, -0.3, 0.9, 1.1, 0.6, -0.2, 1.0, 1.4, 0.7, -0.4, 0.9, 1.2, 0.8, 1.5];
  seq.forEach((d, i) => {
    total = total * (1 + d / 100);
    cash = cash + (Math.round((d - 0.5) * 300000));
    const dt = new Date(Date.UTC(2026, 5, 1) + i * 86400000);
    out.push({
      date: `2026-${String(dt.getUTCMonth() + 1).padStart(2, '0')}-${String(dt.getUTCDate()).padStart(2, '0')}`,
      total: Math.round(total),
      cash: Math.max(0, Math.round(cash)),
    });
  });
  return out;
})();

const formatKRW = (v: number) => {
  if (Math.abs(v) >= 1_0000_0000) return `${(v / 1_0000_0000).toFixed(1)}억`;
  if (Math.abs(v) >= 1_0000) return `${(v / 1_0000).toFixed(0)}만`;
  return v.toLocaleString();
};

export function V6Balance() {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-400">총 자산 추이</h2>
      <div className="h-80 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={DATA}>
            <defs>
              <linearGradient id="v6TotalGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis dataKey="date" tickFormatter={(v) => v.slice(5)} tick={{ fill: '#94a3b8', fontSize: 11 }} />
            <YAxis
              tickFormatter={formatKRW}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
              domain={['dataMin - 1000000', 'dataMax + 1000000']}
            />
            <Tooltip
              contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', color: '#e2e8f0' }}
              formatter={(v: any, name: any) => {
                const label = name === 'total' ? '총 자산' : '예수금';
                const val = typeof v === 'number' ? v : Number(v) || 0;
                return [`${val.toLocaleString()}원`, label];
              }}
            />
            <Area type="monotone" dataKey="total" stroke="#6366f1" strokeWidth={2} fill="url(#v6TotalGrad)" dot={{ fill: '#6366f1', r: 3 }} name="total" />
            <Line type="monotone" dataKey="cash" stroke="#10b981" strokeWidth={1.5} strokeDasharray="5 5" dot={false} name="cash" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
