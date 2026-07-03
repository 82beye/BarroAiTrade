'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select } from '@/components/ui/select';
import { api, type StrategyLevel } from '@/lib/api';

interface OHLCVData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface PriceChartProps {
  defaultSymbol?: string;
  defaultTimeframe?: string;
  /** 전달 시 candlestick 위에 기준선(createPriceLine) 렌더. 미전달 시 심볼별 /api/chart/levels 자동 조회 */
  levels?: StrategyLevel[];
  /** 인라인(스크리너) 모드에서 심볼/주기 셀렉트 숨김 */
  hideControls?: boolean;
}

// ── 이동평균 5종 색 (PRD §4.4) ──
const MA_CONFIGS = [
  { period: 5, color: '#E91E8C', label: '5' },
  { period: 10, color: '#3090E0', label: '10' },
  { period: 20, color: '#E08040', label: '20' },
  { period: 60, color: '#38B068', label: '60' },
  { period: 120, color: '#94a3b8', label: '120' },
];

// ── 전략 기준선 색 매핑 (PRD §4.3 / §6.8) ──
function levelColor(label: string): string {
  const u = (label || '').toUpperCase();
  if (u === 'SF') return '#5820B8';
  if (u === 'B1') return '#38B068';
  if (u === 'B2') return '#3090E0';
  if (u === 'B3') return '#7B40C8';
  if (u.startsWith('G')) return '#E0A000';
  if (u.startsWith('J')) return '#C81880';
  return '#94a3b8';
}

function sma(candles: OHLCVData[], period: number): { time: number; value: number }[] {
  const out: { time: number; value: number }[] = [];
  for (let i = period - 1; i < candles.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += candles[j].close;
    out.push({ time: candles[i].time, value: sum / period });
  }
  return out;
}

export function PriceChart({
  defaultSymbol = '005930',
  defaultTimeframe = '1h',
  levels,
  hideControls = false,
}: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const lcRef = useRef<any>(null); // lightweight-charts 모듈 (LineStyle 등)
  const maSeriesRef = useRef<Record<number, any>>({});
  const priceLinesRef = useRef<any[]>([]);
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  const [loading, setLoading] = useState(true);
  const [autoLevels, setAutoLevels] = useState<StrategyLevel[]>([]);

  // 외부에서 defaultSymbol 변경(스크리너 행 클릭) 시 내부 심볼 동기화
  useEffect(() => {
    setSymbol(defaultSymbol);
  }, [defaultSymbol]);

  // 실제 렌더에 사용할 기준선: prop 우선, 없으면 자동 조회분
  const effectiveLevels = levels ?? autoLevels;

  // 차트 초기화
  useEffect(() => {
    if (!chartContainerRef.current) return;

    let chart: any = null;
    let handleResize: (() => void) | null = null;

    const initChart = async () => {
      const lc = await import('lightweight-charts');
      lcRef.current = lc;
      const { createChart } = lc;

      chart = createChart(chartContainerRef.current!, {
        width: chartContainerRef.current!.clientWidth,
        height: 400,
        layout: {
          background: { color: '#0f172a' },
          textColor: '#94a3b8',
        },
        grid: {
          vertLines: { color: '#1e293b' },
          horzLines: { color: '#1e293b' },
        },
        crosshair: { mode: 0 },
        timeScale: {
          borderColor: '#334155',
          timeVisible: true,
        },
        rightPriceScale: {
          borderColor: '#334155',
        },
      });

      const series = chart.addCandlestickSeries({
        upColor: '#D00010',
        downColor: '#2060C0',
        borderUpColor: '#D00010',
        borderDownColor: '#2060C0',
        wickUpColor: '#D00010',
        wickDownColor: '#2060C0',
      });

      // 이동평균 5종 LineSeries
      MA_CONFIGS.forEach(({ period, color }) => {
        maSeriesRef.current[period] = chart.addLineSeries({
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
      });

      chartRef.current = chart;
      seriesRef.current = series;

      handleResize = () => {
        if (chartContainerRef.current && chart) {
          chart.applyOptions({ width: chartContainerRef.current.clientWidth });
        }
      };
      window.addEventListener('resize', handleResize);

      await loadData();
    };

    initChart();

    return () => {
      if (handleResize) window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      seriesRef.current = null;
      maSeriesRef.current = {};
      priceLinesRef.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 데이터 로드 + 이동평균 계산
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      let candles: OHLCVData[] = [];
      try {
        const response = await api.getOHLCV(symbol, timeframe, 200);
        const raw: any[] = response.data?.data ?? [];
        candles = raw.map((item: any) => ({
          time: item.timestamp,
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
        }));
      } catch {
        candles = [];
      }

      if (candles.length === 0) {
        candles = generateMockOHLCV(symbol, 200);
      }

      if (seriesRef.current) {
        seriesRef.current.setData(candles);
        // 이동평균 갱신
        MA_CONFIGS.forEach(({ period }) => {
          const s = maSeriesRef.current[period];
          if (s) s.setData(candles.length >= period ? sma(candles, period) : []);
        });
      }
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe]);

  // 심볼/타임프레임 변경 시 데이터 재로드
  useEffect(() => {
    if (seriesRef.current) {
      loadData();
    }
  }, [loadData]);

  // levels prop 미전달 시 심볼 변경마다 자동 조회
  useEffect(() => {
    if (levels !== undefined) return; // 외부 제어 모드
    let cancelled = false;
    api
      .getChartLevels(symbol)
      .then((res) => {
        if (!cancelled) setAutoLevels(res.data?.levels ?? []);
      })
      .catch(() => {
        if (!cancelled) setAutoLevels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, levels]);

  // 기준선 오버레이 적용 (levels 변경 시 기존 제거 후 재생성)
  useEffect(() => {
    const series = seriesRef.current;
    const lc = lcRef.current;
    if (!series || !lc) return;

    priceLinesRef.current.forEach((pl) => {
      try {
        series.removePriceLine(pl);
      } catch {
        /* noop */
      }
    });
    priceLinesRef.current = [];

    (effectiveLevels ?? []).forEach((lv) => {
      if (typeof lv.price !== 'number' || !isFinite(lv.price)) return;
      const pl = series.createPriceLine({
        price: lv.price,
        color: levelColor(lv.label),
        lineWidth: lv.active ? 2 : 1,
        lineStyle: lc.LineStyle.Dashed,
        axisLabelVisible: true,
        title: lv.label,
      });
      priceLinesRef.current.push(pl);
    });
  }, [effectiveLevels, loading]);

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div className="flex flex-col gap-2">
          <CardTitle className="text-lg">가격 차트</CardTitle>
          {/* 이동평균 범례 (PRD §4.4) */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {MA_CONFIGS.map(({ period, color, label }) => (
              <span key={period} className="flex items-center gap-1 text-xs text-slate-400">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: color }}
                />
                MA{label}
              </span>
            ))}
          </div>
        </div>
        {!hideControls && (
          <div className="flex gap-2">
            <Select
              name="symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="w-28 border-slate-700 bg-slate-800 text-sm text-slate-50"
            >
              <option value="005930">삼성전자</option>
              <option value="000660">SK하이닉스</option>
              <option value="035720">카카오</option>
              <option value="051910">LG화학</option>
              <option value="035420">NAVER</option>
            </Select>
            <Select
              name="timeframe"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="w-20 border-slate-700 bg-slate-800 text-sm text-slate-50"
            >
              <option value="1m">1분</option>
              <option value="5m">5분</option>
              <option value="15m">15분</option>
              <option value="1h">1시간</option>
              <option value="1d">일봉</option>
            </Select>
          </div>
        )}
      </CardHeader>
      <CardContent>
        <div ref={chartContainerRef} className="relative w-full">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-900/50">
              <p className="text-sm text-slate-400">차트 로딩 중...</p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// Mock OHLCV 데이터 생성 (API 미응답 대응 — 한국 주식 가격 기준)
function generateMockOHLCV(symbol: string, count: number): OHLCVData[] {
  const SEED: Record<string, number> = {
    '005930': 72000,
    '000660': 185000,
    '035720': 55000,
    '051910': 320000,
    '035420': 195000,
  };
  let basePrice = SEED[symbol] ?? 70000;
  const data: OHLCVData[] = [];
  const now = new Date();

  for (let i = count; i >= 0; i--) {
    const time = new Date(now.getTime() - i * 3600000);
    const volatility = basePrice * 0.005;
    const open = basePrice + (Math.random() - 0.5) * volatility * 2;
    const close = open + (Math.random() - 0.5) * volatility;
    const high = Math.max(open, close) + Math.random() * volatility * 0.5;
    const low = Math.min(open, close) - Math.random() * volatility * 0.5;

    data.push({
      time: Math.floor(time.getTime() / 1000),
      open: Math.round(open),
      high: Math.round(high),
      low: Math.round(low),
      close: Math.round(close),
    });

    basePrice = close;
  }

  return data;
}
