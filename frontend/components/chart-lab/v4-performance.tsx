'use client';

/**
 * 차트 랩 v4 — 누적 수익률 에어리어 차트
 * 원본: /Users/beye/Desktop/Workspace/nextjs-aibitgo/src/components/trade/grid/grid-performance-chart.tsx
 * 라이브러리: recharts (AreaChart)
 *
 * 원본 시각 스타일 보존:
 *   수익 구간 녹색(#22c55e)/손실 구간 빨강(#ef4444) 그라디언트, 상단 요약 PnL,
 *   시간축 토글(1H/4H/1D/1W), 호버 커스텀 툴팁, y=0 기준 점선.
 * 데이터는 정적 샘플(PerformanceDataPoint 형태 모사).
 */

import { useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts';

type Timeframe = '1h' | '4h' | '1d' | '1w';
const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: '1h', label: '1H' },
  { value: '4h', label: '4H' },
  { value: '1d', label: '1D' },
  { value: '1w', label: '1W' },
];

const INITIAL_CAPITAL = 1000;

// 정적 샘플: 30개 포인트 누적 PnL (약 +8% 우상향)
const SAMPLE = (() => {
  const out: { timestamp: number; pnl: number; equity: number }[] = [];
  let equity = INITIAL_CAPITAL;
  const start = Date.UTC(2026, 5, 1);
  const seq = [1, 2, -1, 3, 2, -2, 4, 3, 1, -1, 2, 3, 4, -2, 3, 5, 2, -1, 3, 4, 2, 1, -2, 3, 4, 5, 3, 2, 4, 6];
  seq.forEach((d, i) => {
    equity += d * 4;
    out.push({
      timestamp: start + i * 86400000,
      pnl: Math.round((equity - INITIAL_CAPITAL) * 100) / 100,
      equity: Math.round(equity * 100) / 100,
    });
  });
  return out;
})();

function fmtDate(ts: number, tf: Timeframe): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  if (tf === '1h' || tf === '4h') return `${p(d.getHours())}:${p(d.getMinutes())}`;
  return `${p(d.getMonth() + 1)}/${p(d.getDate())}`;
}

function ChartTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const dp = payload[0].payload;
  const pnlPct = (dp.pnl / INITIAL_CAPITAL) * 100;
  const pos = dp.pnl >= 0;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/95 p-3 shadow-lg backdrop-blur-sm">
      <p className="mb-1.5 text-xs text-slate-400">
        {new Date(dp.timestamp).toLocaleDateString('ko-KR')}
      </p>
      <div className="space-y-1">
        <Row label="PnL" val={`${pos ? '+' : ''}$${dp.pnl.toFixed(2)}`} cls={pos ? 'text-green-500' : 'text-red-500'} />
        <Row label="수익률" val={`${pos ? '+' : ''}${pnlPct.toFixed(2)}%`} cls={pos ? 'text-green-500' : 'text-red-500'} />
        <Row label="자산" val={`$${dp.equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}`} cls="text-slate-200" />
      </div>
    </div>
  );
}
function Row({ label, val, cls }: { label: string; val: string; cls: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={`text-sm font-semibold ${cls}`}>{val}</span>
    </div>
  );
}

export function V4Performance() {
  const [tf, setTf] = useState<Timeframe>('1d');
  const summary = useMemo(() => {
    const latest = SAMPLE[SAMPLE.length - 1];
    return {
      pnl: latest.pnl,
      pct: (latest.pnl / INITIAL_CAPITAL) * 100,
      positive: latest.pnl >= 0,
    };
  }, []);
  const grad = 'v4PnlGradient';

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950 p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 text-base font-semibold text-slate-100">
            {summary.positive ? '📈' : '📉'} 수익률 차트
          </span>
          <span className={`text-lg font-bold ${summary.positive ? 'text-green-500' : 'text-red-500'}`}>
            {summary.positive ? '+' : ''}${summary.pnl.toFixed(2)}
            <span className="ml-1 text-xs font-medium">
              ({summary.positive ? '+' : ''}{summary.pct.toFixed(2)}%)
            </span>
          </span>
        </div>
        <div className="flex gap-1">
          {TIMEFRAMES.map((o) => (
            <button
              key={o.value}
              onClick={() => setTf(o.value)}
              className={`h-7 rounded px-2.5 text-xs font-medium transition-colors ${
                tf === o.value ? 'bg-slate-100 text-slate-900' : 'text-slate-400 hover:bg-slate-800'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={SAMPLE} margin={{ top: 5, right: 5, left: -15, bottom: 0 }}>
          <defs>
            <linearGradient id={grad} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22c55e" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#22c55e" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={(v: number) => fmtDate(v, tf)}
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            axisLine={false}
            tickLine={false}
          />
          <RechartsTooltip content={<ChartTooltip />} />
          <ReferenceLine y={0} stroke="#64748b" strokeDasharray="4 4" strokeOpacity={0.5} />
          <Area type="monotone" dataKey="pnl" stroke="#22c55e" strokeWidth={2} fill={`url(#${grad})`} animationDuration={500} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
