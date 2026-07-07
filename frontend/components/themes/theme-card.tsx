'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import type { ThemeStockItem } from '@/lib/api';
import { DAILY_LIMIT_CHANGE_PCT } from '@/lib/change-percent';

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

function fmtEok(n?: number | null): string {
  return n === null || n === undefined
    ? '-'
    : n.toLocaleString('ko-KR', { maximumFractionDigits: 0 });
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

const CHANGE_RANGE_CENTER_PCT = 50;

function isFiniteNumber(value?: number | null): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

function calcPrevClose(price?: number | null, changePct?: number | null): number | null {
  if (!isFiniteNumber(price) || !isFiniteNumber(changePct) || price <= 0 || changePct <= -100) {
    return null;
  }
  return price / (1 + changePct / 100);
}

function calcChangePctFromBase(price?: number | null, basePrice?: number | null): number | null {
  if (!isFiniteNumber(price) || !isFiniteNumber(basePrice) || basePrice <= 0) {
    return null;
  }
  return ((price - basePrice) / basePrice) * 100;
}

function calcStockChangePctRange(stock: ThemeStockItem) {
  const currentPct = stock.change_pct;
  const prevClose = calcPrevClose(stock.price, currentPct);
  const highPct = calcChangePctFromBase(stock.day_high, prevClose);
  const lowPct = calcChangePctFromBase(stock.day_low, prevClose);
  const values = [currentPct, highPct, lowPct].filter(isFiniteNumber);
  const maxPct = values.length > 0 ? Math.max(...values) : 0;
  const minPct = values.length > 0 ? Math.min(...values) : 0;

  return {
    maxUpPct: Math.max(0, maxPct),
    maxDownAbsPct: Math.max(0, Math.abs(Math.min(0, minPct))),
  };
}

function clampTrackPct(value: number): number {
  return Math.min(100, Math.max(0, value));
}

function toCenteredTrackPct(changePct: number): number {
  return clampTrackPct(
    CHANGE_RANGE_CENTER_PCT + (changePct / DAILY_LIMIT_CHANGE_PCT) * CHANGE_RANGE_CENTER_PCT,
  );
}

function calcCenteredBarGeometry(changePct: number) {
  const endPct = toCenteredTrackPct(changePct);
  return {
    leftPct: Math.min(CHANGE_RANGE_CENTER_PCT, endPct),
    widthPct: Math.abs(endPct - CHANGE_RANGE_CENTER_PCT),
    endPct,
  };
}

// 전체 트랙을 -30%~+30%로 두고 중앙 0% 기준선에서 실제 등락률만큼 좌우로 표시한다.
// 수평 실선은 0%부터 해당 종목의 일중 최저/최고 등락률까지의 범위를 표시한다.
export function ChangePctRangeBar({
  changePct,
  maxUpPct,
  maxDownAbsPct,
}: {
  changePct?: number | null;
  /** 해당 종목의 일중 최고 등락률 */
  maxUpPct: number;
  /** 해당 종목의 일중 최저 등락률 절댓값 */
  maxDownAbsPct: number;
}) {
  if (changePct === null || changePct === undefined) {
    return (
      <div className="relative mt-1 h-1.5 w-full rounded-full bg-black/10">
        <div className="absolute left-1/2 top-1/2 h-3 w-0.5 -translate-x-1/2 -translate-y-1/2 bg-tima-text" />
      </div>
    );
  }

  const up = changePct >= 0;
  const { leftPct, widthPct, endPct } = calcCenteredBarGeometry(changePct);
  const maxUpLinePct = maxUpPct > 0 ? toCenteredTrackPct(maxUpPct) : null;
  const maxDownLinePct = maxDownAbsPct > 0 ? toCenteredTrackPct(-maxDownAbsPct) : null;
  const maxUpLineWidthPct =
    maxUpLinePct !== null ? Math.max(0, maxUpLinePct - CHANGE_RANGE_CENTER_PCT) : 0;
  const maxDownLineWidthPct =
    maxDownLinePct !== null ? Math.max(0, CHANGE_RANGE_CENTER_PCT - maxDownLinePct) : 0;
  const upColor = '#D00010';
  const downColor = '#2060C0';
  const color = up ? upColor : downColor;

  return (
    <div
      className="relative mt-1 h-1.5 w-full rounded-full bg-black/10"
      data-change-pct={changePct}
      data-range-min-pct={-DAILY_LIMIT_CHANGE_PCT}
      data-range-zero-line-pct={CHANGE_RANGE_CENTER_PCT.toFixed(2)}
      data-range-max-pct={DAILY_LIMIT_CHANGE_PCT}
      data-bar-left-pct={leftPct.toFixed(2)}
      data-bar-width-pct={widthPct.toFixed(2)}
      data-value-end-pct={endPct.toFixed(2)}
      data-max-up-line-pct={maxUpLinePct?.toFixed(2) ?? ''}
      data-max-up-line-width-pct={maxUpLineWidthPct.toFixed(2)}
      data-max-down-line-pct={maxDownLinePct?.toFixed(2) ?? ''}
      data-max-down-line-width-pct={maxDownLineWidthPct.toFixed(2)}
      aria-label={`-30% 0% +30% 기준 ${changePct.toFixed(2)}%, 최고 상승률 ${maxUpPct.toFixed(2)}%, 최저 하락률 -${maxDownAbsPct.toFixed(2)}%`}
    >
      <div
        className="absolute top-0 z-10 h-1.5 rounded-full"
        style={{ left: `${leftPct}%`, width: `${widthPct}%`, backgroundColor: color }}
      />
      {maxDownLinePct !== null && (
        <div
          className="absolute top-1/2 z-20 h-px -translate-y-1/2"
          style={{
            left: `${maxDownLinePct}%`,
            width: `${maxDownLineWidthPct}%`,
            backgroundColor: downColor,
          }}
        />
      )}
      <div className="absolute left-1/2 top-1/2 z-30 h-3 w-0.5 -translate-x-1/2 -translate-y-1/2 bg-tima-text" />
      {maxUpLinePct !== null && (
        <div
          className="absolute top-1/2 z-20 h-px -translate-y-1/2"
          style={{
            left: `${CHANGE_RANGE_CENTER_PCT}%`,
            width: `${maxUpLineWidthPct}%`,
            backgroundColor: upColor,
          }}
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
      <div className="bg-tima-teal px-2.5 py-2">
        <div className="flex min-w-0 items-center gap-1">
          {id !== null && id !== undefined ? (
            <Link
              href={`/themes/${id}`}
              className="min-w-0 flex-1 truncate text-sm font-bold leading-none text-black hover:underline"
            >
              {name}
            </Link>
          ) : (
            <span className="min-w-0 flex-1 truncate text-sm font-bold leading-none text-black">
              {name}
            </span>
          )}
          {effectiveThemeChangePct !== null && (
            <span
              className={`shrink-0 rounded-full bg-white px-1 py-0.5 font-mono text-[10px] font-bold leading-none shadow-sm ${
                effectiveThemeChangePct >= 0 ? 'text-tima-up' : 'text-tima-down'
              }`}
            >
              {fmtSignedPct(effectiveThemeChangePct)}
            </span>
          )}
          {totalValue !== null && (
            <span className="shrink-0 rounded-full bg-white px-1 py-0.5 text-[10px] font-bold leading-none text-tima-teal shadow-sm">
              {fmtEok(totalValue)}억
            </span>
          )}
          {capturedAt && (
            <span className="shrink-0 rounded bg-white/30 px-1 py-0.5 text-[10px] font-medium leading-none text-black/70">
              {capturedAt}
            </span>
          )}
        </div>
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
            const limitUp = hasCp && (cp as number) >= DAILY_LIMIT_CHANGE_PCT - 1;
            const limitDown = hasCp && (cp as number) <= -DAILY_LIMIT_CHANGE_PCT + 1;
            const dirColor = !hasCp ? 'text-tima-sub' : up ? 'text-tima-up' : 'text-tima-down';
            const changeRange = calcStockChangePctRange(s);
            // 체결시각(HH:MM) — 백엔드가 제공 시에만 노출(타입 미보장, 방어 접근)
            const tradedAt = (s as { traded_at?: string | null }).traded_at ?? null;
            return (
              <Link
                key={s.symbol}
                href={`/stocks/${s.symbol}`}
                className={`relative block rounded px-1.5 py-1.5 transition-colors ${
                  limitUp ? 'bg-tima-surge' : limitDown ? 'bg-tima-down/10' : 'hover:bg-tima-bg/60'
                }`}
              >
                {/* 상한가/하한가 근접 종목 플래그 */}
                {(limitUp || limitDown) && (
                  <span
                    aria-hidden
                    className={`absolute left-0 top-0 h-0 w-0 rounded-tl border-r-[8px] border-t-[8px] border-r-transparent ${
                      limitUp ? 'border-t-tima-up' : 'border-t-tima-down'
                    }`}
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
                      : `${fmtEok(s.value_traded)}억`}
                  </span>
                </div>
                {/* 3줄: 테마 내 최고 상승률/최저 하락률 기준 막대 */}
                <ChangePctRangeBar
                  changePct={cp}
                  maxUpPct={changeRange.maxUpPct}
                  maxDownAbsPct={changeRange.maxDownAbsPct}
                />
              </Link>
            );
          })
        )}
      </div>
    </div>
  );
}
