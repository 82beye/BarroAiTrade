'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { Disclaimer } from '@/components/layout/disclaimer';
import { PriceChart } from '@/components/dashboard/price-chart';
import { categoryStyle } from '@/lib/event-category';
import { WatchlistStar } from '@/components/watchlist/watchlist-star';
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

type DetailTab = 'info' | 'chart' | 'orderbook';

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

export default function StockDetailPage() {
  const params = useParams();
  const symbol = Array.isArray(params.symbol) ? params.symbol[0] : (params.symbol ?? '');

  const [tab, setTab] = useState<DetailTab>('info');
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

  const TABS: { key: DetailTab; label: string }[] = [
    { key: 'info', label: '정보' },
    { key: 'chart', label: '차트' },
    { key: 'orderbook', label: '호가' },
  ];

  return (
    <div className="p-3">
      {/* 뒤로 + 서브탭 (정보 | 차트 | 호가 — 활성 tima.select 분홍, PRD §3.3) */}
      <div className="mb-3 flex items-center gap-2">
        <button
          onClick={() => history.back()}
          aria-label="뒤로"
          className="shrink-0 text-lg text-tima-text"
        >
          ‹
        </button>
        <div className="flex flex-1 gap-1.5">
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex-1 rounded-full border py-1.5 text-sm font-semibold transition-colors ${
                tab === key
                  ? 'border-tima-select bg-tima-select text-white'
                  : 'border-tima-line bg-white text-tima-sub'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 종목 헤더 */}
      <div className="mb-4 rounded-lg border border-tima-line bg-white px-3 py-2.5">
        <div className="flex items-center gap-2">
          {symbol && <WatchlistStar symbol={symbol} size="sm" />}
          <span className="text-lg font-bold text-tima-text">{name}</span>
          <span className="font-mono text-xs text-tima-sub">{symbol}</span>
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span
            className={`font-mono text-2xl font-bold ${
              !hasCp ? 'text-tima-text' : up ? 'text-tima-up' : 'text-tima-down'
            }`}
          >
            {fmtNum(ticker?.price)}
          </span>
          <span
            className={`font-mono text-sm font-semibold ${
              !hasCp ? 'text-tima-sub' : up ? 'text-tima-up' : 'text-tima-down'
            }`}
          >
            {!hasCp ? '-' : `${up ? '▲' : '▼'} ${Math.abs(cp as number).toFixed(2)}%`}
          </span>
        </div>
      </div>

      {/* ── 정보 탭: 관련테마 칩 + 일정 + 뉴스 ── */}
      {tab === 'info' && (
        <>
          {/* 관련테마 칩 (아웃라인, 가로 스크롤 — PRD §3.3) */}
          {themes.length > 0 && (
            <div className="mb-4">
              <span className="mb-1.5 block text-sm font-bold text-tima-teal">관련테마</span>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {themes.map((t) => (
                  <Link
                    key={t.id}
                    href="/themes"
                    title={t.description ?? undefined}
                    className="shrink-0 rounded-full border border-tima-teal/60 bg-white px-3 py-1 text-sm text-tima-teal transition-colors hover:bg-tima-teal/10"
                  >
                    {t.name}
                    {t.score !== null && t.score !== undefined && (
                      <span className="ml-1 text-xs text-tima-sub">{t.score.toFixed(1)}</span>
                    )}
                  </Link>
                ))}
              </div>
            </div>
          )}

          <div className="mb-4 rounded-lg border border-tima-line bg-white p-3">
            <h2 className="mb-2 text-sm font-bold text-tima-teal">관련 일정</h2>
            {events.length === 0 ? (
              <p className="text-sm text-tima-sub">최근 특별한 일정이 없습니다.</p>
            ) : (
              <div className="space-y-2">
                {events.map((ev) => (
                  <div
                    key={ev.id}
                    className="flex items-start gap-3 rounded-lg border border-tima-line bg-tima-bg/40 px-3 py-2"
                  >
                    <span
                      className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
                      style={{ backgroundColor: categoryStyle(ev.event_type).dot }}
                    >
                      {categoryStyle(ev.event_type).label}
                    </span>
                    <div className="flex-1">
                      <p className="text-sm text-tima-text">{ev.title}</p>
                      <p className="mt-0.5 font-mono text-xs text-tima-sub">{ev.event_date}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {relatedNews.length > 0 && (
            <div className="mb-4 rounded-lg border border-tima-line bg-white p-3">
              <h2 className="mb-2 text-sm font-bold text-tima-teal">관련 뉴스</h2>
              <div className="space-y-2">
                {relatedNews.map((n) => (
                  <a
                    key={n.id}
                    href={n.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block rounded-lg border border-tima-line bg-tima-bg/40 px-3 py-2 hover:border-tima-teal/50"
                  >
                    <p className="text-sm text-tima-text">{n.title}</p>
                    <div className="mt-1 flex items-center gap-2 text-xs text-tima-sub">
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
            </div>
          )}
        </>
      )}

      {/* ── 차트 탭 ── */}
      {tab === 'chart' && (
        <div className="mb-6">
          <PriceChart key={symbol} defaultSymbol={symbol} defaultTimeframe="15m" theme="light" />
        </div>
      )}

      {/* ── 호가 탭 ── */}
      {tab === 'orderbook' && (
        <div className="mb-6">
          <OrderBook symbol={symbol} currentPrice={ticker?.price ?? null} />
        </div>
      )}

      <Disclaimer />
    </div>
  );
}

// ── 호가 사다리 (PRD §4.6) — 매도(위, 파랑) / 매수(아래, 빨강), 5초 폴링 ──
type Level = [number, number]; // [price, qty]

function OrderBook({ symbol, currentPrice }: { symbol: string; currentPrice: number | null }) {
  const [asks, setAsks] = useState<Level[]>([]);
  const [bids, setBids] = useState<Level[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [ok, setOk] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.getOrderBook(symbol);
      const data = res.data;
      const a = Array.isArray(data?.asks) ? (data.asks as Level[]) : [];
      const b = Array.isArray(data?.bids) ? (data.bids as Level[]) : [];
      setAsks(a);
      setBids(b);
      setOk(a.length > 0 || b.length > 0);
    } catch {
      setAsks([]);
      setBids([]);
      setOk(false);
    } finally {
      setLoaded(true);
    }
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    setLoaded(false);
    load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(load, 5_000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [symbol, load]);

  // 최대 잔량 (바 스케일)
  const maxQty = useMemo(() => {
    const all = [...asks, ...bids].map(([, q]) => q ?? 0);
    return all.length > 0 ? Math.max(...all, 1) : 1;
  }, [asks, bids]);

  const askSum = useMemo(() => asks.reduce((s, [, q]) => s + (q ?? 0), 0), [asks]);
  const bidSum = useMemo(() => bids.reduce((s, [, q]) => s + (q ?? 0), 0), [bids]);

  // 매도: 높은 가격이 위 (내림차순), 매수: 높은 가격이 위 (내림차순)
  const asksDesc = useMemo(() => [...asks].sort((x, y) => y[0] - x[0]), [asks]);
  const bidsDesc = useMemo(() => [...bids].sort((x, y) => y[0] - x[0]), [bids]);

  function pct(price: number): string | null {
    if (currentPrice === null || currentPrice === undefined || currentPrice === 0) return null;
    const p = ((price - currentPrice) / currentPrice) * 100;
    return `${p >= 0 ? '+' : ''}${p.toFixed(2)}%`;
  }

  if (!loaded) {
    return (
      <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
        불러오는 중…
      </div>
    );
  }

  if (!ok) {
    return (
      <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
        호가 데이터를 불러올 수 없습니다 (장중·게이트웨이 연결 시 표시).
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
      {/* 매도호가 (위, 파랑) */}
      <div>
        {asksDesc.map(([price, qty], i) => (
          <Row key={`a-${i}`} price={price} qty={qty} maxQty={maxQty} side="ask" pct={pct(price)} />
        ))}
      </div>

      {/* 현재가 강조 */}
      <div className="flex items-center justify-center border-y border-tima-line bg-tima-bg py-2">
        <span className="font-mono text-lg font-bold text-tima-text">{fmtNum(currentPrice)}</span>
        <span className="ml-2 text-xs text-tima-sub">현재가</span>
      </div>

      {/* 매수호가 (아래, 빨강) */}
      <div>
        {bidsDesc.map(([price, qty], i) => (
          <Row key={`b-${i}`} price={price} qty={qty} maxQty={maxQty} side="bid" pct={pct(price)} />
        ))}
      </div>

      {/* 잔량 합계 */}
      <div className="flex items-center justify-between border-t border-tima-line px-4 py-2 text-xs">
        <span className="text-tima-down">매도합 {fmtNum(askSum)}</span>
        <span className="text-tima-up">매수합 {fmtNum(bidSum)}</span>
      </div>
    </div>
  );
}

function Row({
  price,
  qty,
  maxQty,
  side,
  pct,
}: {
  price: number;
  qty: number;
  maxQty: number;
  side: 'ask' | 'bid';
  pct: string | null;
}) {
  const w = Math.max(((qty ?? 0) / maxQty) * 100, 0);
  const isAsk = side === 'ask';
  // 매도 파랑 / 매수 빨강
  const barColor = isAsk ? 'bg-tima-down/15' : 'bg-tima-up/15';
  const qtyColor = isAsk ? 'text-tima-down' : 'text-tima-up';
  return (
    <div className="relative flex items-center border-b border-tima-line last:border-0 px-4 py-1.5">
      {/* 잔량 바 (매도 왼쪽 / 매수 오른쪽 정렬) */}
      <div
        className={`absolute inset-y-0 ${isAsk ? 'left-0' : 'right-0'} ${barColor}`}
        style={{ width: `${w}%` }}
      />
      <div className="relative z-10 flex w-full items-center justify-between text-sm">
        {isAsk ? (
          <>
            <span className={`font-mono ${qtyColor}`}>{fmtNum(qty)}</span>
            <span className="flex items-baseline gap-2">
              <span className="font-mono text-tima-text">{fmtNum(price)}</span>
              {pct && <span className="w-14 text-right font-mono text-xs text-tima-sub">{pct}</span>}
            </span>
          </>
        ) : (
          <>
            <span className="flex items-baseline gap-2">
              <span className="font-mono text-tima-text">{fmtNum(price)}</span>
              {pct && <span className="w-14 text-right font-mono text-xs text-tima-sub">{pct}</span>}
            </span>
            <span className={`font-mono ${qtyColor}`}>{fmtNum(qty)}</span>
          </>
        )}
      </div>
    </div>
  );
}
