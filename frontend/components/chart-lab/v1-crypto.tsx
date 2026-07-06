'use client';

/**
 * 차트 랩 v1 — 크립토 에어리어 차트
 * 원본: /Users/beye/Desktop/Workspace/nextjs-aibitgo/src/components/chart/crypto-chart.tsx
 * 라이브러리: lightweight-charts (Area) — 단일 종가 라인 + 그라디언트 필
 *
 * 원본은 v5 API(addSeries(AreaSeries, ...))를 쓰지만, 본 프로젝트는
 * lightweight-charts v4 이므로 addAreaSeries 로 동등 포팅. 색·투명도는 원본 보존:
 *   lineColor #10B981 / topColor rgba(16,185,129,.4) / bottomColor rgba(16,185,129,.05)
 * 데이터는 api.getOHLCV(1d,200) 종가, 실패 시 mock.
 */

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';

export function V1Crypto({ symbol = '005930' }: { symbol?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!containerRef.current) return;
    let chart: any = null;
    let handleResize: (() => void) | null = null;
    let disposed = false;

    (async () => {
      const lc = await import('lightweight-charts');
      if (disposed || !containerRef.current) return;
      chart = lc.createChart(containerRef.current, {
        layout: {
          background: { color: 'transparent' },
          textColor: '#9CA3AF',
        },
        grid: {
          vertLines: { color: '#1F2937' },
          horzLines: { color: '#1F2937' },
        },
        width: containerRef.current.clientWidth,
        height: 480,
        timeScale: { timeVisible: true, secondsVisible: false },
      });

      const areaSeries = chart.addAreaSeries({
        lineColor: '#10B981',
        topColor: 'rgba(16, 185, 129, 0.4)',
        bottomColor: 'rgba(16, 185, 129, 0.05)',
        lineWidth: 2,
      });

      let points = await fetchCloseSeries(symbol);
      if (points.length === 0) points = mockCloseSeries();
      areaSeries.setData(points);
      chart.timeScale().fitContent();
      setLoading(false);

      handleResize = () => {
        if (containerRef.current && chart) {
          chart.applyOptions({ width: containerRef.current.clientWidth });
        }
      };
      window.addEventListener('resize', handleResize);
    })();

    return () => {
      disposed = true;
      if (handleResize) window.removeEventListener('resize', handleResize);
      if (chart) chart.remove();
    };
  }, [symbol]);

  return (
    <div className="relative w-full">
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center text-sm text-slate-400">
          차트 로딩 중...
        </div>
      )}
      <div ref={containerRef} className="w-full" />
    </div>
  );
}

async function fetchCloseSeries(symbol: string): Promise<{ time: number; value: number }[]> {
  try {
    const res = await api.getOHLCV(symbol, '1d', 200);
    const raw: any[] = res.data?.data ?? [];
    return raw
      .map((it: any) => ({ time: it.timestamp as number, value: it.close as number }))
      .filter((p) => isFinite(p.value) && p.time > 0);
  } catch {
    return [];
  }
}

function mockCloseSeries(): { time: number; value: number }[] {
  const out: { time: number; value: number }[] = [];
  let base = 72000;
  const now = Math.floor(Date.now() / 1000);
  const day = 86400;
  for (let i = 90; i >= 0; i--) {
    base = base + (Math.random() - 0.5) * base * 0.02;
    out.push({ time: now - i * day, value: Math.round(base) });
  }
  return out;
}
