'use client';

/**
 * 차트 랩 v2 — 그리드 주문선 캔들 차트
 * 원본: /Users/beye/Desktop/Workspace/nextjs-aibitgo/src/components/trade/grid/grid-order-chart.tsx
 * 라이브러리: lightweight-charts (Candlestick + createPriceLine)
 *
 * 원본 시각 스타일 보존:
 *   배경 #1a1a2e / 캔들 up #26a69a down #ef5350 / 그리드 #1e222d
 *   Long 주문선 초록 점선, Short 주문선 빨강 점선, 진입가 골드 실선(#FFD700)
 * 원본은 Spider Web 주문 템플릿을 받지만, 여기선 마지막 종가 기준으로
 * 상/하 그리드 주문선을 파생 샘플로 재현. 데이터는 실 OHLCV(1d) → 실패 시 mock.
 */

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';

interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export function V2GridOrder({ symbol = '005930' }: { symbol?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!containerRef.current) return;
    let chart: any = null;
    let ro: ResizeObserver | null = null;
    let disposed = false;

    (async () => {
      const lc = await import('lightweight-charts');
      if (disposed || !containerRef.current) return;
      chart = lc.createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 400,
        layout: {
          background: { color: '#1a1a2e' },
          textColor: '#d1d4dc',
        },
        grid: {
          vertLines: { color: '#1e222d' },
          horzLines: { color: '#1e222d' },
        },
        crosshair: { mode: 0 },
        timeScale: { borderColor: '#2B2B43', timeVisible: true, secondsVisible: false },
        rightPriceScale: { borderColor: '#2B2B43' },
      });

      const candleSeries = chart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
      });

      let candles = await fetchCandles(symbol);
      if (candles.length === 0) candles = mockCandles();
      candleSeries.setData(candles);

      // 마지막 종가 기준 그리드 주문선 파생 (원본 Spider Web 주문선 재현)
      const last = candles[candles.length - 1]?.close ?? 70000;
      for (let step = 1; step <= 4; step++) {
        const longPrice = last * (1 - step * 0.02); // 하단 매수 그리드
        const shortPrice = last * (1 + step * 0.02); // 상단 매도 그리드
        candleSeries.createPriceLine({
          price: Math.round(longPrice),
          color: '#26a69a',
          lineWidth: step === 1 ? 2 : 1,
          lineStyle: lc.LineStyle.Dashed,
          axisLabelVisible: true,
          title: `L${step}`,
        });
        candleSeries.createPriceLine({
          price: Math.round(shortPrice),
          color: '#ef5350',
          lineWidth: step === 1 ? 2 : 1,
          lineStyle: lc.LineStyle.Dashed,
          axisLabelVisible: true,
          title: `S${step}`,
        });
      }
      // 진입가 (골드 실선)
      candleSeries.createPriceLine({
        price: Math.round(last),
        color: '#FFD700',
        lineWidth: 2,
        lineStyle: lc.LineStyle.Solid,
        axisLabelVisible: true,
        title: '진입가',
      });

      chart.timeScale().fitContent();
      setLoading(false);

      ro = new ResizeObserver((entries) => {
        for (const e of entries) chart?.applyOptions({ width: e.contentRect.width });
      });
      ro.observe(containerRef.current);
    })();

    return () => {
      disposed = true;
      if (ro) ro.disconnect();
      if (chart) chart.remove();
    };
  }, [symbol]);

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          📉 Spider Web 주문 차트
        </h3>
        <span className="text-xs text-slate-500">일봉 | 그리드 주문선 파생</span>
      </div>
      <div className="relative w-full overflow-hidden rounded-lg border border-slate-700">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#1a1a2e] text-sm text-slate-400">
            차트 로딩 중...
          </div>
        )}
        <div ref={containerRef} className="w-full" style={{ height: 400 }} />
      </div>
      {/* 원본 범례 재현 */}
      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs text-slate-400">
        <div className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-4" style={{ borderTop: '2px dashed #26a69a' }} />
          Long 주문
        </div>
        <div className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-4" style={{ borderTop: '2px dashed #ef5350' }} />
          Short 주문
        </div>
        <div className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-4 bg-[#FFD700]" />
          진입가
        </div>
      </div>
    </div>
  );
}

async function fetchCandles(symbol: string): Promise<Candle[]> {
  try {
    const res = await api.getOHLCV(symbol, '1d', 150);
    const raw: any[] = res.data?.data ?? [];
    return raw
      .map((it: any) => ({
        time: it.timestamp as number,
        open: it.open,
        high: it.high,
        low: it.low,
        close: it.close,
      }))
      .filter((c) => isFinite(c.close) && c.time > 0);
  } catch {
    return [];
  }
}

function mockCandles(): Candle[] {
  const out: Candle[] = [];
  let base = 72000;
  const now = Math.floor(Date.now() / 1000);
  const day = 86400;
  for (let i = 100; i >= 0; i--) {
    const vol = base * 0.02;
    const open = base + (Math.random() - 0.5) * vol;
    const close = open + (Math.random() - 0.5) * vol;
    const high = Math.max(open, close) + Math.random() * vol * 0.5;
    const low = Math.min(open, close) - Math.random() * vol * 0.5;
    out.push({
      time: now - i * day,
      open: Math.round(open),
      high: Math.round(high),
      low: Math.round(low),
      close: Math.round(close),
    });
    base = close;
  }
  return out;
}
