'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select } from '@/components/ui/select';
import { api } from '@/lib/api';

interface OHLCVData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface PriceChartProps {
  defaultSymbol?: string;
  defaultTimeframe?: string;
}

export function PriceChart({
  defaultSymbol = '005930',
  defaultTimeframe = '1h',
}: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  const [loading, setLoading] = useState(true);

  // 차트 초기화
  useEffect(() => {
    if (!chartContainerRef.current) return;

    let chart: any = null;
    let series: any = null;

    const initChart = async () => {
      const { createChart } = await import('lightweight-charts');

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
        crosshair: {
          mode: 0,
        },
        timeScale: {
          borderColor: '#334155',
          timeVisible: true,
        },
        rightPriceScale: {
          borderColor: '#334155',
        },
      });

      series = chart.addCandlestickSeries({
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderUpColor: '#22c55e',
        borderDownColor: '#ef4444',
        wickUpColor: '#22c55e',
        wickDownColor: '#ef4444',
      });

      chartRef.current = chart;
      seriesRef.current = series;

      // 리사이즈 처리
      const handleResize = () => {
        if (chartContainerRef.current && chart) {
          chart.applyOptions({
            width: chartContainerRef.current.clientWidth,
          });
        }
      };

      window.addEventListener('resize', handleResize);

      // 데이터 로드
      await loadData();

      return () => {
        window.removeEventListener('resize', handleResize);
        chart.remove();
      };
    };

    initChart();

    return () => {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, []);

  // 데이터 로드
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.getOHLCV(symbol, timeframe, 100);
      const candles: any[] = response.data?.data ?? [];
      const data: OHLCVData[] = candles.map((item: any) => ({
        time: item.timestamp,
        open: item.open,
        high: item.high,
        low: item.low,
        close: item.close,
      }));

      if (seriesRef.current && data.length > 0) {
        seriesRef.current.setData(data);
      } else {
        seriesRef.current?.setData(generateMockOHLCV(symbol, 100));
      }
    } catch {
      seriesRef.current?.setData(generateMockOHLCV(symbol, 100));
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

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-lg">가격 차트</CardTitle>
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
      </CardHeader>
      <CardContent>
        <div ref={chartContainerRef} className="relative w-full">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-slate-900/50">
              <p className="text-sm text-slate-400">차트 로딩 중...</p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// Mock OHLCV 데이터 생성 (API 미응답 대응 — 한국 주식 가격 기준)
function generateMockOHLCV(symbol: string, count: number) {
  const SEED: Record<string, number> = {
    '005930': 72000,  // 삼성전자
    '000660': 185000, // SK하이닉스
    '035720': 55000,  // 카카오
    '051910': 320000, // LG화학
    '035420': 195000, // NAVER
  };
  let basePrice = SEED[symbol] ?? 70000;
  const data = [];
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
