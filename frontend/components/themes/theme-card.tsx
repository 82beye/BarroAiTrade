'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import { Card } from '@/components/ui/card';
import type { ThemeStockItem } from '@/lib/api';

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

// 등락률 내림차순 (null 은 score 순으로 뒤쪽)
export function sortStocks(a: ThemeStockItem, b: ThemeStockItem): number {
  const ca = a.change_pct;
  const cb = b.change_pct;
  const aNull = ca === null || ca === undefined;
  const bNull = cb === null || cb === undefined;
  if (!aNull && !bNull) return (cb as number) - (ca as number);
  if (aNull && bNull) return (b.score ?? 0) - (a.score ?? 0);
  return aNull ? 1 : -1;
}

// 당일 가격범위 박스플롯 (day_low~day_high 레인지, day_open~price 박스)
export function BoxPlotBar({ s }: { s: ThemeStockItem }) {
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

interface ThemeCardViewProps {
  name: string;
  description?: string | null;
  stocks: ThemeStockItem[];
  /** 스냅숏 모달에서 동결 시각 표기 (HH:MM) */
  capturedAt?: string | null;
}

/**
 * 프레젠테이셔널 테마 카드 (PRD §3.1) — 라이브 보드·타임라인 스냅숏 공용.
 * teal 헤더 + 대금 배지 + 이슈 1줄 + 대표종목 4~5행(등락률순, 박스플롯 바).
 * 종목명은 /stocks/[symbol] 상세 페이지로 연결 (PRD §3.3).
 */
export function ThemeCardView({ name, description, stocks, capturedAt }: ThemeCardViewProps) {
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
        <span className="font-bold text-black">{name}</span>
        <div className="flex items-center gap-2">
          {capturedAt && (
            <span className="rounded-full bg-black/10 px-2 py-0.5 text-[10px] font-medium text-black/70">
              {capturedAt}
            </span>
          )}
          {totalValue !== null && (
            <span className="rounded-full bg-black/20 px-2 py-0.5 text-xs font-semibold text-black">
              {fmtNum(totalValue)}억
            </span>
          )}
        </div>
      </div>

      {/* 이슈 1줄 */}
      {description && (
        <p className="truncate px-4 pt-2 text-xs text-slate-400">{description}</p>
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
              <Link
                key={s.symbol}
                href={`/stocks/${s.symbol}`}
                className={`block rounded px-2 py-1.5 transition-colors hover:bg-slate-700/40 ${
                  surge ? 'bg-tima-surge hover:bg-tima-surge/90' : ''
                }`}
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
              </Link>
            );
          })
        )}
      </div>
    </Card>
  );
}
