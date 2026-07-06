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
  volume: number;
}

interface PriceChartProps {
  defaultSymbol?: string;
  defaultTimeframe?: string;
  /** 전달 시 candlestick 위에 기준선(createPriceLine) 렌더. 미전달 시 심볼별 /api/chart/levels 자동 조회 */
  levels?: StrategyLevel[];
  /** 인라인(스크리너) 모드에서 심볼/주기 셀렉트 숨김 */
  hideControls?: boolean;
  /** 배경 테마. 티마 라이트 셸은 'light', 관리자 대시보드는 기본 'dark'(하위호환) */
  theme?: 'light' | 'dark';
  /** 차트 높이(px). 미전달 시 라이트 520 / 다크 400 (하위호환) */
  height?: number;
}

// ── 이동평균 5종 색 (PRD §4.4) ──
const MA_CONFIGS = [
  { period: 5, color: '#E91E8C', label: '5' },
  { period: 10, color: '#3090E0', label: '10' },
  { period: 20, color: '#E08040', label: '20' },
  { period: 60, color: '#38B068', label: '60' },
  { period: 120, color: '#94a3b8', label: '120' },
];

// ── 분봉 단위 (PRD §4.4: 1·3·5·10·15·30·60분, 기본 15분) ──
const MINUTE_UNITS = [
  { value: '1m', label: '1분' },
  { value: '3m', label: '3분' },
  { value: '5m', label: '5분' },
  { value: '10m', label: '10분' },
  { value: '15m', label: '15분' },
  { value: '30m', label: '30분' },
  { value: '1h', label: '60분' },
];

function isMinuteTf(tf: string): boolean {
  return MINUTE_UNITS.some((u) => u.value === tf);
}

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

// 일봉 → 주봉/월봉 리샘플 (백엔드는 1d까지만 제공 — PRD §4.4 월/주 주기)
function resample(daily: OHLCVData[], unit: 'week' | 'month'): OHLCVData[] {
  const out: OHLCVData[] = [];
  let key = '';
  for (const c of daily) {
    const d = new Date(c.time * 1000);
    const k =
      unit === 'month'
        ? `${d.getFullYear()}-${d.getMonth()}`
        : `${d.getFullYear()}-${Math.floor((d.getTime() / 86400000 + 4) / 7)}`; // epoch 기준 주(목요일 앵커)
    if (k !== key) {
      key = k;
      out.push({ ...c });
    } else {
      const last = out[out.length - 1];
      last.high = Math.max(last.high, c.high);
      last.low = Math.min(last.low, c.low);
      last.close = c.close;
      last.volume += c.volume;
    }
  }
  return out;
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

// 거래량 히스토그램 색 (티마: 보라/회색 계열 반투명 — PRD §4.4)
const VOL_UP = 'rgba(156, 124, 216, 0.55)';
const VOL_DOWN = 'rgba(148, 163, 184, 0.45)';

export function PriceChart({
  defaultSymbol = '005930',
  defaultTimeframe = '1h',
  levels,
  hideControls = false,
  theme = 'dark',
  height,
}: PriceChartProps) {
  const isLight = theme === 'light';
  const chartHeight = height ?? (isLight ? 520 : 400);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const volSeriesRef = useRef<any>(null);
  const lcRef = useRef<any>(null); // lightweight-charts 모듈 (LineStyle 등)
  const maSeriesRef = useRef<Record<number, any>>({});
  const priceLinesRef = useRef<any[]>([]);
  // autoscaleInfoProvider 가 읽는 현재 기준선 가격 목록 (렌더 무관 최신값)
  const levelPricesRef = useRef<number[]>([]);
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  // 라이트 분봉 토글: 마지막 선택한 분 단위 기억(기본 15분)
  const [minuteUnit, setMinuteUnit] = useState(
    isMinuteTf(defaultTimeframe) ? defaultTimeframe : '15m',
  );
  const [minuteOpen, setMinuteOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [autoLevels, setAutoLevels] = useState<StrategyLevel[]>([]);
  const [fallbackNote, setFallbackNote] = useState<string | null>(null);

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
        height: chartHeight,
        layout: {
          background: { color: isLight ? '#FFFFFF' : '#0f172a' },
          textColor: isLight ? '#333333' : '#94a3b8',
        },
        grid: {
          vertLines: { color: isLight ? '#EEEEEE' : '#1e293b' },
          horzLines: { color: isLight ? '#EEEEEE' : '#1e293b' },
        },
        crosshair: { mode: 0 },
        timeScale: {
          borderColor: isLight ? '#DDDDDD' : '#334155',
          timeVisible: true,
        },
        rightPriceScale: {
          borderColor: isLight ? '#DDDDDD' : '#334155',
        },
      });

      const series = chart.addCandlestickSeries({
        upColor: '#D00010',
        downColor: '#2060C0',
        borderUpColor: '#D00010',
        borderDownColor: '#2060C0',
        wickUpColor: '#D00010',
        wickDownColor: '#2060C0',
        // 전략 기준선(createPriceLine)은 기본 오토스케일에 포함되지 않아
        // 캔들 범위 밖이면 안 보임 → 근접(±30% 밴드) 기준선을 스케일에 포함 (PRD §4.3)
        autoscaleInfoProvider: (original: () => any) => {
          const res = original();
          if (!res?.priceRange) return res;
          let { minValue, maxValue } = res.priceRange;
          const band = 0.3 * Math.max(maxValue - minValue, maxValue * 0.05);
          for (const p of levelPricesRef.current) {
            if (p >= minValue - band && p <= maxValue + band) {
              minValue = Math.min(minValue, p);
              maxValue = Math.max(maxValue, p);
            }
          }
          return { ...res, priceRange: { minValue, maxValue } };
        },
      });

      // 거래량 히스토그램 (하단 20% 별도 priceScale — PRD §4.4)
      const volSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
        lastValueVisible: false,
        priceLineVisible: false,
      });
      chart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
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
      volSeriesRef.current = volSeries;

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
      volSeriesRef.current = null;
      maSeriesRef.current = {};
      priceLinesRef.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 데이터 로드 + 이동평균 계산
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      // 주봉/월봉은 일봉을 받아 클라이언트 리샘플
      const isResampled = timeframe === '1w' || timeframe === '1M';
      const fetchTf = isResampled ? '1d' : timeframe;
      const fetchLimit = isResampled ? 600 : 200;

      const fetchCandles = async (tf: string, lim: number): Promise<OHLCVData[]> => {
        try {
          const response = await api.getOHLCV(symbol, tf, lim);
          const raw: any[] = response.data?.data ?? [];
          return raw.map((item: any) => ({
            time: item.timestamp,
            open: item.open,
            high: item.high,
            low: item.low,
            close: item.close,
            volume: item.volume ?? 0,
          }));
        } catch {
          return [];
        }
      };

      let candles = await fetchCandles(fetchTf, fetchLimit);

      // 분/시간봉 미가용(게이트웨이 없음) 시 일봉 캐시로 정직 폴백 —
      // mock 랜덤 캔들은 전략 기준선과 스케일이 어긋나 오해 소지 (기준선 우선)
      let note: string | null = null;
      if (candles.length === 0 && fetchTf !== '1d') {
        candles = await fetchCandles('1d', 200);
        if (candles.length > 0) note = '분봉 미연동 — 일봉 표시';
      }
      setFallbackNote(note);

      if (candles.length === 0) {
        candles = generateMockOHLCV(symbol, fetchLimit);
      }
      if (isResampled) {
        candles = resample(candles, timeframe === '1M' ? 'month' : 'week');
      }

      if (seriesRef.current) {
        seriesRef.current.setData(candles);
        // 거래량 히스토그램 갱신 (상승 보라/하락 회색)
        if (volSeriesRef.current) {
          volSeriesRef.current.setData(
            candles.map((c) => ({
              time: c.time,
              value: c.volume,
              color: c.close >= c.open ? VOL_UP : VOL_DOWN,
            })),
          );
        }
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

    const prices: number[] = [];
    (effectiveLevels ?? []).forEach((lv) => {
      if (typeof lv.price !== 'number' || !isFinite(lv.price)) return;
      prices.push(lv.price);
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
    // autoscaleInfoProvider 가 새 기준선을 반영하도록 오토스케일 재계산 유도
    levelPricesRef.current = prices;
    try {
      series.priceScale().applyOptions({ autoScale: true });
    } catch {
      /* noop */
    }
  }, [effectiveLevels, loading]);

  const selectCls = isLight
    ? 'border-tima-line bg-white text-sm text-tima-text'
    : 'border-slate-700 bg-slate-800 text-sm text-slate-50';

  // ── MA 범례 (컴팩트 한 줄: 색점+숫자) ──
  const maLegend = (
    <div className="flex flex-nowrap items-center gap-x-2 whitespace-nowrap">
      {MA_CONFIGS.map(({ period, color, label }) => (
        <span
          key={period}
          className={`flex items-center gap-1 text-[11px] font-medium ${
            isLight ? 'text-tima-sub' : 'text-slate-400'
          }`}
        >
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: color }}
          />
          {label}
        </span>
      ))}
      {fallbackNote && (
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
            isLight ? 'bg-tima-bg text-tima-sub' : 'bg-slate-800 text-slate-400'
          }`}
        >
          {fallbackNote}
        </span>
      )}
    </div>
  );

  // ── 라이트 주기 토글 (월 주 일 분 + 분 드롭다운 — PRD §4.4) ──
  const periodMode: 'month' | 'week' | 'day' | 'minute' =
    timeframe === '1M'
      ? 'month'
      : timeframe === '1w'
        ? 'week'
        : timeframe === '1d'
          ? 'day'
          : 'minute';

  const lightPeriodToggle = (
    <div className="flex items-center gap-1.5">
      <div className="flex items-center gap-1">
        {(
          [
            { key: 'month', label: '월', tf: '1M' },
            { key: 'week', label: '주', tf: '1w' },
            { key: 'day', label: '일', tf: '1d' },
            { key: 'minute', label: '분', tf: minuteUnit },
          ] as const
        ).map(({ key, label, tf }) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              setMinuteOpen(false);
              setTimeframe(tf);
            }}
            className={`min-w-[26px] rounded-md px-2 py-1 text-[13px] font-semibold transition-colors ${
              periodMode === key
                ? 'bg-tima-teal text-white'
                : 'text-tima-sub hover:text-tima-text'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* 분 단위 드롭다운 (체크마크) */}
      {periodMode === 'minute' && (
        <div className="relative">
          <button
            type="button"
            onClick={() => setMinuteOpen((v) => !v)}
            className="flex items-center gap-1 rounded-md border border-tima-line bg-white px-2 py-1 text-[13px] font-medium text-tima-text"
          >
            {MINUTE_UNITS.find((u) => u.value === minuteUnit)?.label ?? '15분'}
            <span className="text-[10px] text-tima-sub">▾</span>
          </button>
          {minuteOpen && (
            <>
              <div
                className="fixed inset-0 z-20"
                onClick={() => setMinuteOpen(false)}
                aria-hidden
              />
              <div className="absolute left-0 top-full z-30 mt-1 w-24 overflow-hidden rounded-md border border-tima-line bg-white shadow-lg">
                {MINUTE_UNITS.map((u) => {
                  const active = u.value === minuteUnit;
                  return (
                    <button
                      key={u.value}
                      type="button"
                      onClick={() => {
                        setMinuteUnit(u.value);
                        setTimeframe(u.value);
                        setMinuteOpen(false);
                      }}
                      className={`flex w-full items-center justify-between px-2.5 py-1.5 text-left text-[13px] ${
                        active
                          ? 'font-semibold text-tima-teal'
                          : 'text-tima-text hover:bg-tima-bg'
                      }`}
                    >
                      {u.label}
                      {active && <span className="text-tima-teal">✓</span>}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );

  return (
    <Card className={isLight ? 'border-tima-line bg-white' : 'border-slate-800 bg-slate-900'}>
      {isLight ? (
        /* 라이트: 주기 토글 + 이평 범례 단일 행 (종목은 페이지 컨텍스트가 결정 — 셀렉트 없음) */
        <CardHeader className="flex flex-row flex-nowrap items-center justify-between gap-x-3 overflow-x-auto pb-2">
          <div className="shrink-0">{lightPeriodToggle}</div>
          <div className="flex shrink-0 items-center">{maLegend}</div>
        </CardHeader>
      ) : (
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <div className="flex flex-col gap-2">
            <CardTitle className="text-lg">가격 차트</CardTitle>
            {maLegend}
          </div>
          {!hideControls && (
            <div className="flex gap-2">
              <Select
                name="symbol"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className={`w-28 ${selectCls}`}
              >
                {!['005930', '000660', '035720', '051910', '035420'].includes(symbol) && (
                  <option value={symbol}>{symbol}</option>
                )}
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
                className={`w-20 ${selectCls}`}
              >
                <option value="1m">1분</option>
                <option value="5m">5분</option>
                <option value="15m">15분</option>
                <option value="1h">1시간</option>
                <option value="1d">일봉</option>
                <option value="1w">주봉</option>
                <option value="1M">월봉</option>
              </Select>
            </div>
          )}
        </CardHeader>
      )}
      <CardContent>
        <div ref={chartContainerRef} className="relative w-full" style={{ minHeight: chartHeight }}>
          {loading && (
            <div
              className={`absolute inset-0 z-10 flex items-center justify-center ${
                isLight ? 'bg-white/60' : 'bg-slate-900/50'
              }`}
            >
              <p className={`text-sm ${isLight ? 'text-tima-sub' : 'text-slate-400'}`}>차트 로딩 중...</p>
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
      volume: Math.round(50000 + Math.random() * 450000),
    });

    basePrice = close;
  }

  return data;
}
