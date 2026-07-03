'use client';

import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { Card, CardContent } from '@/components/ui/card';
import { Disclaimer } from '@/components/layout/disclaimer';
import { PriceChart } from '@/components/dashboard/price-chart';
import { api, type StockTheme, type NewsItem } from '@/lib/api';

interface Ticker {
  symbol: string;
  name?: string | null;
  price?: number | null;
  change_pct?: number | null;
}

interface CalendarEvent {
  id: number | string;
  event_type: string;
  symbol?: string | null;
  event_date: string;
  title: string;
  source?: string;
}

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

export default function StockDetailPage() {
  const params = useParams();
  const symbol = Array.isArray(params.symbol) ? params.symbol[0] : (params.symbol ?? '');

  const [ticker, setTicker] = useState<Ticker | null>(null);
  const [themes, setThemes] = useState<StockTheme[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [news, setNews] = useState<NewsItem[]>([]);

  // 시세
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    api
      .getTicker(symbol)
      .then((res) => {
        if (!cancelled) setTicker(res.data ?? null);
      })
      .catch(() => {
        if (!cancelled) setTicker(null);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  // 관련 테마
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    api
      .getStockThemes(symbol)
      .then((res) => {
        if (!cancelled) setThemes(Array.isArray(res.data?.themes) ? res.data.themes : []);
      })
      .catch(() => {
        if (!cancelled) setThemes([]);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  // 관련 일정
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    api
      .getCalendarBySymbol(symbol)
      .then((res) => {
        if (cancelled) return;
        const data = res.data;
        setEvents(Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : []);
      })
      .catch(() => {
        if (!cancelled) setEvents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  // 관련 뉴스 (tags/title 에 심볼·종목명 매칭)
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    api
      .getRecentNews(50)
      .then((res) => {
        if (!cancelled) {
          const data = res.data;
          setNews(Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : []);
        }
      })
      .catch(() => {
        if (!cancelled) setNews([]);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const name = ticker?.name ?? symbol;
  const cp = ticker?.change_pct;
  const hasCp = cp !== null && cp !== undefined;
  const up = (cp ?? 0) >= 0;

  const relatedNews = useMemo(() => {
    const needles = [symbol, ticker?.name].filter(Boolean).map((s) => String(s).toLowerCase());
    if (needles.length === 0) return [];
    return news
      .filter((n) => {
        const hay = `${n.title ?? ''} ${(n.tags ?? []).join(' ')}`.toLowerCase();
        return needles.some((nd) => hay.includes(nd));
      })
      .slice(0, 8);
  }, [news, symbol, ticker?.name]);

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      {/* 헤더 */}
      <div className="mb-6">
        <div className="flex flex-wrap items-baseline gap-3">
          <h1 className="text-3xl font-bold text-slate-50">{name}</h1>
          <span className="font-mono text-sm text-slate-500">{symbol}</span>
        </div>
        <div className="mt-2 flex items-baseline gap-3">
          <span className="font-mono text-2xl font-bold text-slate-100">
            {fmtNum(ticker?.price)}
          </span>
          <span
            className={`font-mono text-sm ${
              !hasCp ? 'text-slate-500' : up ? 'text-tima-up' : 'text-tima-down'
            }`}
          >
            {!hasCp ? '-' : `${up ? '↑' : '↓'} ${Math.abs(cp as number).toFixed(2)}%`}
          </span>
        </div>
      </div>

      {/* 관련테마 칩 (가로 스크롤 — PRD §3.3) */}
      {themes.length > 0 && (
        <div className="mb-6 flex gap-2 overflow-x-auto pb-1">
          {themes.map((t) => (
            <Link
              key={t.id}
              href="/themes"
              title={t.description ?? undefined}
              className="shrink-0 rounded-full border border-tima-teal/60 px-3 py-1 text-sm text-tima-teal transition-colors hover:bg-tima-teal/10"
            >
              {t.name}
              {t.score !== null && t.score !== undefined && (
                <span className="ml-1 text-xs text-slate-500">{t.score.toFixed(1)}</span>
              )}
            </Link>
          ))}
        </div>
      )}

      {/* 기준선 차트 (심볼별 자동 조회) */}
      <div className="mb-6">
        <PriceChart key={symbol} defaultSymbol={symbol} defaultTimeframe="15m" />
      </div>

      {/* 관련 일정 */}
      <Card className="mb-6 border-slate-700 bg-slate-800">
        <CardContent className="pt-4">
          <h2 className="mb-3 text-sm font-semibold text-slate-300">관련 일정</h2>
          {events.length === 0 ? (
            <p className="text-sm text-slate-500">최근 특별한 일정이 없습니다.</p>
          ) : (
            <div className="space-y-2">
              {events.map((ev) => (
                <div
                  key={ev.id}
                  className="flex items-start gap-3 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2"
                >
                  <span className="mt-0.5 rounded bg-slate-700 px-1.5 py-0.5 text-xs font-semibold text-slate-300">
                    {ev.event_type}
                  </span>
                  <div className="flex-1">
                    <p className="text-sm text-slate-200">{ev.title}</p>
                    <p className="mt-0.5 font-mono text-xs text-slate-500">{ev.event_date}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 관련 뉴스 (매칭 결과 있을 때만) */}
      {relatedNews.length > 0 && (
        <Card className="mb-6 border-slate-700 bg-slate-800">
          <CardContent className="pt-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-300">관련 뉴스</h2>
            <div className="space-y-2">
              {relatedNews.map((n) => (
                <a
                  key={n.id}
                  href={n.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 hover:border-slate-500 hover:bg-slate-800"
                >
                  <p className="text-sm text-slate-200">{n.title}</p>
                  <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                    <span>{n.source}</span>
                    <span>
                      {new Date(n.published_at).toLocaleDateString('ko-KR', {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                  </div>
                </a>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <Disclaimer />
    </div>
  );
}
