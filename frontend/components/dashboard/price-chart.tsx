'use client';

import { useEffect, useRef, useState } from 'react';
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
  defaultSymbol = 'AAPL',
  defaultTimeframe = '1H',
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
  const loadData = async () => {
    setLoading(true);
    try {
      const response = await api.getOHLCV(symbol, timeframe, 100);
      const data: OHLCVData[] = response.data.map((item: any) => ({
        time: item.timestamp,
        open: item.open,
        high: item.high,
        low: item.low,
        close: item.close,
      }));

      if (seriesRef.current && data.length > 0) {
        seriesRef.current.setData(data);
      }
    } catch (err) {
      // API 미구현 시 mock 데이터 사용
      const mockData = generateMockOHLCV(symbol, 100);
      if (seriesRef.current) {
        seriesRef.current.setData(mockData);
      }
    } finally {
      setLoading(false);
    }
  };

  // 심볼/타임프레임 변경 시 데이터 재로드
  useEffect(() => {
    if (seriesRef.current) {
      loadData();
    }
  }, [symbol, timeframe]);

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-lg">가격 차트</CardTitle>
        <div className="flex gap-2">
          <Select
            name="symbol"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="w-24 border-slate-700 bg-slate-800 text-sm text-slate-50"
          >
            <option value="AAPL">AAPL</option>
            <option value="MSFT">MSFT</option>
            <option value="GOOGL">GOOGL</option>
            <option value="TSLA">TSLA</option>
          </Select>
          <Select
            name="timeframe"
            value={timeframe}
            onChange={(e) => setTimeframe(e.target.value)}
            className="w-20 border-slate-700 bg-slate-800 text-sm text-slate-50"
          >
            <option value="5m">5분</option>
            <option value="15m">15분</option>
            <option value="1H">1시간</option>
            <option value="4H">4시간</option>
            <option value="1D">일봉</option>
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

// Mock OHLCV 데이터 생성 (API 미구현 대응)
function generateMockOHLCV(symbol: string, count: number) {
  const data = [];
  let basePrice = symbol === 'AAPL' ? 150 : symbol === 'MSFT' ? 380 : 140;
  const now = new Date();

  for (let i = count; i >= 0; i--) {
    const time = new Date(now.getTime() - i * 3600000);
    const open = basePrice + (Math.random() - 0.5) * 4;
    const close = open + (Math.random() - 0.5) * 3;
    const high = Math.max(open, close) + Math.random() * 2;
    const low = Math.min(open, close) - Math.random() * 2;

    data.push({
      time: Math.floor(time.getTime() / 1000),
      open: parseFloat(open.toFixed(2)),
      high: parseFloat(high.toFixed(2)),
      low: parseFloat(low.toFixed(2)),
      close: parseFloat(close.toFixed(2)),
    });

    basePrice = close;
  }

  return data;
}
