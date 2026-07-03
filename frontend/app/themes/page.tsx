'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Card } from '@/components/ui/card';
import { Disclaimer } from '@/components/layout/disclaimer';
import { api, type ThemeStockItem } from '@/lib/api';

interface Theme {
  id: number;
  name: string;
  description: string;
}

const POLL_MS = 30_000;

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

// 등락률 내림차순 (null 은 score 순으로 뒤쪽)
function sortStocks(a: ThemeStockItem, b: ThemeStockItem): number {
  const ca = a.change_pct;
  const cb = b.change_pct;
  const aNull = ca === null || ca === undefined;
  const bNull = cb === null || cb === undefined;
  if (!aNull && !bNull) return (cb as number) - (ca as number);
  if (aNull && bNull) return (b.score ?? 0) - (a.score ?? 0);
  return aNull ? 1 : -1;
}

// 당일 가격범위 박스플롯 (day_low~day_high 레인지, day_open~price 박스)
function BoxPlotBar({ s }: { s: ThemeStockItem }) {
  const { day_low, day_high, day_open, price } = s;
  if (
    day_low === null ||
    day_low === undefined ||
    day_high === null ||
    day_high === undefined ||
    day_high <= day_low
  ) {
    return null;
  }
  const range = day_high - day_low;
  const up = (s.change_pct ?? 0) >= 0;
  const boxColor = up ? '#D00010' : '#2060C0';

  let boxLeft = 0;
  let boxWidth = 0;
  if (
    day_open !== null &&
    day_open !== undefined &&
    price !== null &&
    price !== undefined
  ) {
    const lo = Math.min(day_open, price);
    const hi = Math.max(day_open, price);
    boxLeft = ((lo - day_low) / range) * 100;
    boxWidth = Math.max(((hi - lo) / range) * 100, 1.5);
  }
  const priceLeft =
    price !== null && price !== undefined ? ((price - day_low) / range) * 100 : null;

  return (
    <div className="relative mt-1 h-1.5 w-full rounded-full bg-slate-700">
      {boxWidth > 0 && (
        <div
          className="absolute top-0 h-1.5 rounded-full"
          style={{ left: `${boxLeft}%`, width: `${boxWidth}%`, backgroundColor: boxColor }}
        />
      )}
      {priceLeft !== null && (
        <div
          className="absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 bg-slate-100"
          style={{ left: `${Math.min(Math.max(priceLeft, 0), 100)}%` }}
        />
      )}
    </div>
  );
}

function ThemeCard({ theme, tick }: { theme: Theme; tick: number }) {
  const [stocks, setStocks] = useState<ThemeStockItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .getThemeStocks(theme.id)
      .then((res) => {
        if (!cancelled) setStocks(Array.isArray(res.data) ? res.data : []);
      })
      .catch(() => {
        if (!cancelled) setStocks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [theme.id, tick]);

  const sorted = useMemo(() => [...stocks].sort(sortStocks).slice(0, 5), [stocks]);

  const totalValue = useMemo(() => {
    const vals = stocks
      .map((s) => s.value_traded)
      .filter((v): v is number => v !== null && v !== undefined);
    return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) : null;
  }, [stocks]);

  return (
    <Card className="overflow-hidden border-slate-700 bg-slate-800">
      {/* 헤더 (teal) */}
      <div className="flex items-center justify-between bg-tima-teal px-4 py-2.5">
        <span className="font-bold text-black">{theme.name}</span>
        {totalValue !== null && (
          <span className="rounded-full bg-black/20 px-2 py-0.5 text-xs font-semibold text-black">
            {fmtNum(totalValue)}억
          </span>
        )}
      </div>

      {/* 이슈 1줄 */}
      {theme.description && (
        <p className="truncate px-4 pt-2 text-xs text-slate-400">{theme.description}</p>
      )}

      {/* 대표 종목 */}
      <div className="px-2 py-2">
        {sorted.length === 0 ? (
          <div className="px-2 py-4 text-center text-xs text-slate-500">종목 데이터 없음</div>
        ) : (
          sorted.map((s) => {
            const cp = s.change_pct;
            const hasCp = cp !== null && cp !== undefined;
            const up = (cp ?? 0) >= 0;
            const surge = hasCp && (cp as number) >= 8;
            return (
              <div
                key={s.symbol}
                className={`rounded px-2 py-1.5 ${surge ? 'bg-tima-surge' : ''}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span
                    className={`truncate text-sm font-medium ${
                      surge ? 'text-black' : 'text-slate-100'
                    }`}
                  >
                    {s.name ?? s.symbol}
                  </span>
                  <span
                    className={`shrink-0 font-mono text-sm ${
                      surge
                        ? 'text-black'
                        : !hasCp
                          ? 'text-slate-500'
                          : up
                            ? 'text-tima-up'
                            : 'text-tima-down'
                    }`}
                  >
                    {!hasCp ? '-' : `${up ? '↑' : '↓'} ${Math.abs(cp as number).toFixed(2)}%`}
                  </span>
                </div>
                <div
                  className={`flex items-center justify-between gap-2 text-xs ${
                    surge ? 'text-black/70' : 'text-slate-500'
                  }`}
                >
                  <span className="font-mono">{fmtNum(s.price)}</span>
                  <span className="font-mono">{fmtNum(s.value_traded)}억</span>
                </div>
                <BoxPlotBar s={s} />
              </div>
            );
          })
        )}
      </div>
    </Card>
  );
}

export default function ThemesPage() {
  const [themes, setThemes] = useState<Theme[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getThemes()
      .then((res) => {
        if (!cancelled) setThemes(Array.isArray(res.data) ? res.data : []);
      })
      .catch(() => {
        if (!cancelled) setThemes([]);
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
          setLastUpdated(new Date());
        }
      });

    intervalRef.current = setInterval(() => {
      setTick((t) => t + 1);
      setLastUpdated(new Date());
    }, POLL_MS);

    return () => {
      cancelled = true;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-slate-50">테마 보드</h1>
          <p className="mt-2 text-slate-400">시장 테마별 대표 종목을 실시간 추적합니다</p>
        </div>
        <div className="text-right text-xs text-slate-500">
          {lastUpdated && <div>마지막 갱신: {lastUpdated.toLocaleTimeString('ko-KR')}</div>}
          <div>30초 자동 갱신</div>
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-slate-400">테마 불러오는 중…</div>
      ) : themes.length === 0 ? (
        <Card className="border-slate-700 bg-slate-800">
          <div className="py-12 text-center text-slate-400">
            표시할 테마가 없습니다. 데이터 대기 중.
          </div>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {themes.map((t) => (
            <ThemeCard key={t.id} theme={t} tick={tick} />
          ))}
        </div>
      )}

      <Disclaimer />
    </div>
  );
}
