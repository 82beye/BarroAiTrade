'use client';

/**
 * 차트 랩 v5 — 백테스트 Equity Curve + 결과 요약
 * 원본: /Users/beye/Desktop/Workspace/nextjs-aibitgo/src/components/trade/grid/grid-backtest-panel.tsx
 * 라이브러리: recharts (AreaChart) — 차트 부분 + 결과 스탯 카드만 추출
 *
 * 원본 시각 스타일 보존:
 *   6개 결과 스탯 그리드(총수익률/MDD/Sharpe/거래수/승률/최종자산),
 *   Equity Curve 에어리어(수익 녹/손실 적 그라디언트), 초기자본 기준 점선.
 * 데이터는 정적 샘플(BacktestResult 형태 모사).
 */

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

const INITIAL = 1000;

// 정적 샘플 백테스트 결과
const RESULT = (() => {
  const curve: { timestamp: number; equity: number; pnl: number }[] = [];
  let equity = INITIAL;
  const start = Date.UTC(2026, 4, 1);
  const seq = [0.4, -0.3, 0.8, 1.2, -0.6, 0.9, 1.4, -0.4, 0.7, 1.1, 0.5, -0.8, 1.3, 0.6, 1.0, 1.5, -0.5, 0.8, 1.2, 0.9, -0.3, 1.1, 1.4, 0.7, 1.6, -0.4, 1.0, 1.3, 0.8, 1.7];
  seq.forEach((d, i) => {
    equity = equity * (1 + d / 100);
    curve.push({
      timestamp: start + i * 86400000,
      equity: Math.round(equity * 100) / 100,
      pnl: Math.round((equity - INITIAL) * 100) / 100,
    });
  });
  const finalEquity = curve[curve.length - 1].equity;
  return {
    totalReturn: ((finalEquity - INITIAL) / INITIAL) * 100,
    maxDrawdown: -4.3,
    sharpeRatio: 1.62,
    totalTrades: 47,
    winRate: 61.7,
    finalEquity,
    equityCurve: curve,
  };
})();

const POS = RESULT.totalReturn >= 0;

function StatCard({ label, value, color, icon }: { label: string; value: string; color?: string; icon: string }) {
  return (
    <div className="rounded-lg bg-slate-800/60 p-3">
      <div className="mb-1 flex items-center gap-1.5 text-xs text-slate-400">
        <span>{icon}</span>
        {label}
      </div>
      <p className={`text-sm font-bold ${color ?? 'text-slate-100'}`}>{value}</p>
    </div>
  );
}

function EquityTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const dp = payload[0].payload;
  const pos = dp.pnl >= 0;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/95 p-2.5 shadow-lg backdrop-blur-sm">
      <p className="mb-1 text-xs text-slate-400">{new Date(dp.timestamp).toLocaleDateString('ko-KR')}</p>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">자산</span>
        <span className="text-xs font-bold text-slate-100">${dp.equity.toLocaleString()}</span>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">PnL</span>
        <span className={`text-xs font-bold ${pos ? 'text-green-500' : 'text-red-500'}`}>
          {pos ? '+' : ''}${dp.pnl.toFixed(2)}
        </span>
      </div>
    </div>
  );
}

export function V5Backtest() {
  const grad = 'v5EquityGradient';
  return (
    <div className="space-y-4">
      {/* 결과 요약 */}
      <div className="rounded-lg border border-slate-800 bg-slate-950 p-5">
        <div className="mb-3 flex items-center justify-between">
          <span className="flex items-center gap-2 text-base font-semibold text-slate-100">✅ 백테스트 결과</span>
          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${POS ? 'bg-green-500 text-white' : 'bg-red-500 text-white'}`}>
            {POS ? '+' : ''}{RESULT.totalReturn.toFixed(2)}%
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <StatCard icon="📈" label="총 수익률" value={`${POS ? '+' : ''}${RESULT.totalReturn.toFixed(2)}%`} color={POS ? 'text-green-500' : 'text-red-500'} />
          <StatCard icon="📉" label="최대 낙폭" value={`${RESULT.maxDrawdown.toFixed(2)}%`} color="text-red-500" />
          <StatCard icon="📊" label="Sharpe" value={RESULT.sharpeRatio.toFixed(2)} color="text-green-500" />
          <StatCard icon="🎯" label="총 거래 수" value={`${RESULT.totalTrades}`} />
          <StatCard icon="🎯" label="승률" value={`${RESULT.winRate.toFixed(1)}%`} color={RESULT.winRate >= 50 ? 'text-green-500' : 'text-red-500'} />
          <StatCard icon="💰" label="최종 자산" value={`$${RESULT.finalEquity.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} color="text-green-500" />
        </div>
      </div>

      {/* Equity Curve */}
      <div className="rounded-lg border border-slate-800 bg-slate-950 p-5">
        <div className="mb-3 flex items-center gap-2 text-base font-semibold text-slate-100">📊 Equity Curve</div>
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={RESULT.equityCurve} margin={{ top: 5, right: 5, left: -15, bottom: 0 }}>
            <defs>
              <linearGradient id={grad} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={POS ? '#22c55e' : '#ef4444'} stopOpacity={0.25} />
                <stop offset="100%" stopColor={POS ? '#22c55e' : '#ef4444'} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
            <XAxis
              dataKey="timestamp"
              tickFormatter={(v: number) => {
                const d = new Date(v);
                return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
              }}
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
            <RechartsTooltip content={<EquityTooltip />} />
            <ReferenceLine y={INITIAL} stroke="#64748b" strokeDasharray="4 4" strokeOpacity={0.5} />
            <Area type="monotone" dataKey="equity" stroke={POS ? '#22c55e' : '#ef4444'} strokeWidth={2} fill={`url(#${grad})`} animationDuration={500} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
