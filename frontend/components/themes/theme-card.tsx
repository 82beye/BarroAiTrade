'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import type { ThemeStockItem } from '@/lib/api';

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

function fmtSignedPct(n: number): string {
  const sign = n > 0 ? '+' : n < 0 ? '-' : '';
  return `${sign}${Math.abs(n).toFixed(2)}%`;
}

function calcThemeChangePct(stocks: ThemeStockItem[]): number | null {
  const vals = stocks
    .map((s) => s.change_pct)
    .filter((v): v is number => v !== null && v !== undefined);
  if (vals.length === 0) return null;
  return vals.reduce((acc, v) => acc + v, 0) / vals.length;
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
  if (day_open !== null && day_open !== undefined && price !== null && price !== undefined) {
    const lo = Math.min(day_open, price);
    const hi = Math.max(day_open, price);
    boxLeft = ((lo - day_low) / range) * 100;
    boxWidth = Math.max(((hi - lo) / range) * 100, 1.5);
  }
  const priceLeft =
    price !== null && price !== undefined ? ((price - day_low) / range) * 100 : null;

  return (
    <div className="relative mt-1 h-1.5 w-full rounded-full bg-black/10">
      {boxWidth > 0 && (
        <div
          className="absolute top-0 h-1.5 rounded-full"
          style={{ left: `${boxLeft}%`, width: `${boxWidth}%`, backgroundColor: boxColor }}
        />
      )}
      {priceLeft !== null && (
        <div
          className="absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 bg-tima-text"
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
  /** 전달 시 헤더 테마명이 /themes/[id] 상세로 링크 */
  id?: number | string | null;
  /** 테마 구성종목 기준 평균 등락률. 미전달 시 stocks 에서 계산. */
  themeChangePct?: number | null;
}

/**
 * 프레젠테이셔널 테마 카드 (PRD §3.1) — 라이브 보드·타임라인 스냅숏 공용.
 * teal 헤더 + 대금 배지 + 이슈 1줄 + 대표종목 4~5행(등락률순, 박스플롯 바).
 * 라이트 모바일 셸 기준 흰 카드 + 검정 텍스트 + 등락 빨강/파랑.
 */
export function ThemeCardView({
  name,
  description,
  stocks,
  capturedAt,
  id,
  themeChangePct,
}: ThemeCardViewProps) {
  const sorted = useMemo(() => [...stocks].sort(sortStocks).slice(0, 5), [stocks]);
  const effectiveThemeChangePct = useMemo(
    () => themeChangePct ?? calcThemeChangePct(stocks),
    [stocks, themeChangePct],
  );

  const totalValue = useMemo(() => {
    const vals = stocks
      .map((s) => s.value_traded)
      .filter((v): v is number => v !== null && v !== undefined);
    return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) : null;
  }, [stocks]);

  return (
    <div className="overflow-hidden rounded-lg border border-tima-line bg-white shadow-sm">
      {/* 헤더 (teal) — id 전달 시 테마 상세로 링크 */}
      <div className="bg-tima-teal px-3 py-2">
        <div className="flex min-w-0 items-center justify-between gap-2">
          {id !== null && id !== undefined ? (
            <Link href={`/themes/${id}`} className="min-w-0 flex-1 truncate font-bold text-black hover:underline">
              {name}
            </Link>
          ) : (
            <span className="min-w-0 flex-1 truncate font-bold text-black">{name}</span>
          )}
          {capturedAt && (
            <span className="shrink-0 rounded bg-white/30 px-1.5 py-0.5 text-[10px] font-medium text-black/70">
              {capturedAt}
            </span>
          )}
        </div>
        {(effectiveThemeChangePct !== null || totalValue !== null) && (
          <div className="mt-1 flex items-center gap-1">
          {effectiveThemeChangePct !== null && (
            <span
              className={`shrink-0 rounded-full bg-white px-1.5 py-0.5 font-mono text-[11px] font-bold shadow-sm ${
                effectiveThemeChangePct >= 0 ? 'text-tima-up' : 'text-tima-down'
              }`}
            >
              {fmtSignedPct(effectiveThemeChangePct)}
            </span>
          )}
          {totalValue !== null && (
            <span className="shrink-0 rounded-full bg-white px-1.5 py-0.5 text-[11px] font-bold text-tima-teal shadow-sm">
              {fmtNum(totalValue)}억
            </span>
          )}
          </div>
        )}
      </div>

      {/* 이슈 1줄 */}
      {description && (
        <p className="truncate px-3 pt-1.5 text-[11px] text-tima-sub">{description}</p>
      )}

      {/* 대표 종목 */}
      <div className="px-1.5 py-1.5">
        {sorted.length === 0 ? (
          <div className="px-2 py-4 text-center text-xs text-tima-sub">종목 데이터 없음</div>
        ) : (
          sorted.map((s) => {
            const cp = s.change_pct;
            const hasCp = cp !== null && cp !== undefined;
            const up = (cp ?? 0) >= 0;
            const surge = hasCp && (cp as number) >= 8;
            const dirColor = !hasCp ? 'text-tima-sub' : up ? 'text-tima-up' : 'text-tima-down';
            // 체결시각(HH:MM) — 백엔드가 제공 시에만 노출(타입 미보장, 방어 접근)
            const tradedAt = (s as { traded_at?: string | null }).traded_at ?? null;
            return (
              <Link
                key={s.symbol}
                href={`/stocks/${s.symbol}`}
                className={`relative block rounded px-1.5 py-1.5 transition-colors ${
                  surge ? 'bg-tima-surge' : 'hover:bg-tima-bg/60'
                }`}
              >
                {/* 급등(≥8%) 좌상단 빨간 삼각 플래그 */}
                {surge && (
                  <span
                    aria-hidden
                    className="absolute left-0 top-0 h-0 w-0 rounded-tl border-r-[8px] border-t-[8px] border-r-transparent border-t-tima-up"
                  />
                )}
                {/* 1줄: 종목명(좌) / 등락률%(우, 크게·볼드) */}
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-bold text-tima-text">
                    {s.name ?? s.symbol}
                  </span>
                  <span className={`shrink-0 font-mono text-sm font-bold ${dirColor}`}>
                    {!hasCp ? '-' : `${up ? '↑' : '↓'} ${Math.abs(cp as number).toFixed(2)}%`}
                  </span>
                </div>
                {/* 2줄: 현재가(좌) · 체결시각(중) · 거래대금 억(우) */}
                <div className="flex items-center justify-between gap-2 text-[11px]">
                  <span className={`font-mono ${dirColor}`}>{fmtNum(s.price)}</span>
                  {tradedAt && <span className="font-mono text-tima-sub">{tradedAt}</span>}
                  <span className="font-mono text-tima-sub">
                    {s.value_traded === null || s.value_traded === undefined
                      ? '-'
                      : `${fmtNum(s.value_traded)}억`}
                  </span>
                </div>
                {/* 3줄: 박스플롯 바 */}
                <BoxPlotBar s={s} />
              </Link>
            );
          })
        )}
      </div>
    </div>
  );
}
