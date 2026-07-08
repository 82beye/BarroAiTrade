'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { PriceChart } from '@/components/dashboard/price-chart';
import { categoryStyle } from '@/lib/event-category';
import { toInAppHref } from '@/lib/in-app-link';
import { WatchlistStar } from '@/components/watchlist/watchlist-star';
import {
  api,
  type StockTheme,
  type NewsItem,
  type FundamentalResponse,
  type OrderBookRef,
  type OrderBookTick,
  type BrokersResponse,
  type ProgramItem,
  type StrategyLevel,
} from '@/lib/api';

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

  const [tab, setTab] = useState<DetailTab>('chart');
  const [ticker, setTicker] = useState<Ticker | null>(null);
  const [themes, setThemes] = useState<StockTheme[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [fundamental, setFundamental] = useState<FundamentalResponse | null>(null);

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

  // 펀더멘탈 (시총·유통비율 — null 숨김)
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    api
      .getFundamental(symbol)
      .then((res) => {
        if (cancelled) return;
        const d = res.data as FundamentalResponse;
        setFundamental(d?.status === 'ok' ? d : null);
      })
      .catch(() => {
        if (!cancelled) setFundamental(null);
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
    { key: 'chart', label: '차트' },
    { key: 'orderbook', label: '호가' },
    { key: 'info', label: '정보' },
  ];

  return (
    <div className="p-3">
      {/* 뒤로 + 서브탭 (차트 | 호가 | 정보 — 활성 tima.select 분홍, PRD §3.3) */}
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
          {/* 펀더멘탈 라인 (시가총액·유통비율 — PRD §3.3, null 숨김) */}
          {fundamental &&
            (fundamental.market_cap != null || fundamental.float_ratio != null) && (
              <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 rounded-lg border border-tima-line bg-white px-3 py-2 text-sm">
                {fundamental.market_cap != null && (
                  <span className="text-tima-sub">
                    시가총액{' '}
                    <span className="font-mono font-semibold text-tima-text">
                      {fmtNum(fundamental.market_cap)}억
                    </span>
                  </span>
                )}
                {fundamental.float_ratio != null && (
                  <span className="text-tima-sub">
                    유통비율{' '}
                    <span className="font-mono font-semibold text-tima-text">
                      {fundamental.float_ratio.toFixed(2)}%
                    </span>
                  </span>
                )}
                {fundamental.per != null && (
                  <span className="text-tima-sub">
                    PER{' '}
                    <span className="font-mono font-semibold text-tima-text">
                      {fundamental.per.toFixed(2)}
                    </span>
                  </span>
                )}
                {fundamental.pbr != null && (
                  <span className="text-tima-sub">
                    PBR{' '}
                    <span className="font-mono font-semibold text-tima-text">
                      {fundamental.pbr.toFixed(2)}
                    </span>
                  </span>
                )}
              </div>
            )}

          {/* 관련테마 칩 (아웃라인, 가로 스크롤 — PRD §3.3) */}
          {themes.length > 0 && (
            <div className="mb-4">
              <span className="mb-1.5 block text-sm font-bold text-tima-teal">관련테마</span>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {themes.map((t) => (
                  <Link
                    key={t.id}
                    href={`/themes/${t.id}`}
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
                  <Link
                    key={n.id}
                    href={toInAppHref(n.url)}
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
                  </Link>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* ── 차트 탭 ── */}
      {tab === 'chart' && (
        <div className="mb-6">
          <PriceChart key={symbol} defaultSymbol={symbol} defaultTimeframe="15m" theme="light" height={390} />
        </div>
      )}

      {/* ── 호가 탭 (사다리 + 참조가 + 최근체결 + 4서브탭 — PRD §4.6) ── */}
      {tab === 'orderbook' && (
        <div className="mb-6">
          <OrderBookPanel symbol={symbol} currentPrice={ticker?.price ?? null} />
        </div>
      )}

    </div>
  );
}

// ── 호가 패널 (PRD §4.6) — 상단: 최근체결·사다리·참조가 / 하단: 4서브탭 ──
type Level = [number, number]; // [price, qty]
type ObSubTab = 'chart' | 'program' | 'brokers' | 'levels';

const OB_SUBTABS: { key: ObSubTab; label: string }[] = [
  { key: 'chart', label: '차트' },
  { key: 'program', label: '프로그램' },
  { key: 'brokers', label: '거래원' },
  { key: 'levels', label: '참고값' },
];

// 참고값 기준선 색 (PRD §4.3 — price-chart 와 동일 규칙)
function levelBadgeColor(label: string): string {
  const u = (label || '').toUpperCase();
  if (u === 'SF') return '#5820B8';
  if (u === 'B1') return '#38B068';
  if (u === 'B2') return '#3090E0';
  if (u === 'B3') return '#7B40C8';
  if (u.startsWith('G')) return '#E0A000';
  if (u.startsWith('J')) return '#C81880';
  return '#94a3b8';
}

function OrderBookPanel({
  symbol,
  currentPrice,
}: {
  symbol: string;
  currentPrice: number | null;
}) {
  const [asks, setAsks] = useState<Level[]>([]);
  const [bids, setBids] = useState<Level[]>([]);
  const [ref, setRef] = useState<OrderBookRef | null>(null);
  const [strength, setStrength] = useState<number | null>(null);
  const [ticks, setTicks] = useState<OrderBookTick[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [ok, setOk] = useState(false);
  const [sub, setSub] = useState<ObSubTab>('chart');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.getOrderBook(symbol);
      const data = res.data;
      const a = Array.isArray(data?.asks) ? (data.asks as Level[]) : [];
      const b = Array.isArray(data?.bids) ? (data.bids as Level[]) : [];
      setAsks(a);
      setBids(b);
      setRef(data?.ref ?? null);
      setStrength(data?.strength ?? null);
      setTicks(Array.isArray(data?.ticks) ? data.ticks : []);
      setOk(a.length > 0 || b.length > 0);
    } catch {
      setAsks([]);
      setBids([]);
      setRef(null);
      setStrength(null);
      setTicks([]);
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

  const maxQty = useMemo(() => {
    const all = [...asks, ...bids].map(([, q]) => q ?? 0);
    return all.length > 0 ? Math.max(...all, 1) : 1;
  }, [asks, bids]);

  const asksDesc = useMemo(() => [...asks].sort((x, y) => y[0] - x[0]), [asks]);
  const bidsDesc = useMemo(() => [...bids].sort((x, y) => y[0] - x[0]), [bids]);

  function pct(price: number): string | null {
    if (currentPrice === null || currentPrice === undefined || currentPrice === 0) return null;
    const p = ((price - currentPrice) / currentPrice) * 100;
    return `${p >= 0 ? '+' : ''}${p.toFixed(2)}%`;
  }

  return (
    <div className="space-y-3">
      {/* 상단: 최근체결(좌) · 사다리(중) · 참조가(우) */}
      {!loaded ? (
        <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
          불러오는 중…
        </div>
      ) : !ok ? (
        <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
          호가 데이터를 불러올 수 없습니다 (장중·게이트웨이 연결 시 표시).
        </div>
      ) : (
        <div className="flex gap-2">
          {/* 최근 체결 (ticks) */}
          <div className="w-16 shrink-0">
            <div className="mb-1 text-center text-[10px] font-semibold text-tima-sub">체결</div>
            <div className="overflow-hidden rounded border border-tima-line bg-white">
              {ticks.length === 0 ? (
                <div className="py-4 text-center text-[10px] text-tima-line">-</div>
              ) : (
                ticks.slice(0, 12).map((t, i) => (
                  <div
                    key={i}
                    className="border-b border-tima-line/60 px-1 py-0.5 text-right last:border-0"
                  >
                    <div className="font-mono text-[10px] text-tima-text">{fmtNum(t.price)}</div>
                    <div className="font-mono text-[9px] text-tima-sub">{fmtNum(t.qty)}</div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* 호가 사다리 */}
          <div className="flex-1 overflow-hidden rounded-lg border border-tima-line bg-white">
            <div>
              {asksDesc.map(([price, qty], i) => (
                <ObRow key={`a-${i}`} price={price} qty={qty} maxQty={maxQty} side="ask" pct={pct(price)} />
              ))}
            </div>
            <div className="flex items-center justify-center border-y border-tima-line bg-tima-bg py-1.5">
              <span className="font-mono text-base font-bold text-tima-text">{fmtNum(currentPrice)}</span>
            </div>
            <div>
              {bidsDesc.map(([price, qty], i) => (
                <ObRow key={`b-${i}`} price={price} qty={qty} maxQty={maxQty} side="bid" pct={pct(price)} />
              ))}
            </div>
          </div>

          {/* 참조가 패널 (기준가/시가/…/체결강도 — null 행 숨김) */}
          <RefPanel refData={ref} strength={strength} />
        </div>
      )}

      {/* 하단 서브탭 (밑줄 탭, 활성 teal) */}
      <div className="flex border-b border-tima-line">
        {OB_SUBTABS.map((t) => {
          const on = t.key === sub;
          return (
            <button
              key={t.key}
              onClick={() => setSub(t.key)}
              className={`flex-1 border-b-2 py-2 text-sm font-semibold transition-colors ${
                on
                  ? 'border-tima-teal text-tima-teal'
                  : 'border-transparent text-tima-sub hover:text-tima-text'
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div>
        {sub === 'chart' && (
          <PriceChart
            key={`ob-${symbol}`}
            defaultSymbol={symbol}
            defaultTimeframe="15m"
            hideControls
            theme="light"
            height={360}
          />
        )}
        {sub === 'program' && <ProgramTab symbol={symbol} />}
        {sub === 'brokers' && <BrokersTab symbol={symbol} />}
        {sub === 'levels' && <LevelsTab symbol={symbol} />}
      </div>
    </div>
  );
}

// 참조가 패널 (PRD §4.6)
function RefPanel({ refData, strength }: { refData: OrderBookRef | null; strength: number | null }) {
  const rows: { label: string; value?: number | null; color?: string }[] = refData
    ? [
        { label: '기준가', value: refData.base_price },
        { label: '시가', value: refData.open },
        { label: '고가', value: refData.high, color: 'text-tima-up' },
        { label: '저가', value: refData.low, color: 'text-tima-down' },
        { label: '상한가', value: refData.upper_limit, color: 'text-tima-up' },
        { label: '하한가', value: refData.lower_limit, color: 'text-tima-down' },
      ]
    : [];
  const visible = rows.filter((r) => r.value != null);
  const hasVI = refData && (refData.vi_up_expected != null || refData.vi_down_expected != null);

  if (visible.length === 0 && !hasVI && strength == null) {
    return (
      <div className="w-24 shrink-0">
        <div className="rounded border border-tima-line bg-white px-2 py-3 text-center text-[10px] text-tima-sub">
          참조가 미연동
        </div>
      </div>
    );
  }

  return (
    <div className="w-24 shrink-0 space-y-2">
      {visible.length > 0 && (
        <div className="overflow-hidden rounded border border-tima-line bg-white">
          {visible.map((r) => (
            <div
              key={r.label}
              className="flex items-center justify-between border-b border-tima-line/60 px-1.5 py-1 last:border-0"
            >
              {/* 레퍼런스 호가창(참고가 패널)은 값이 먼저, 라벨이 뒤 — 상승VI/하락VI
                  블록(라벨 먼저)과는 순서가 반대라 여기만 별도 배치 */}
              <span className={`font-mono text-[11px] font-semibold ${r.color ?? 'text-tima-text'}`}>
                {fmtNum(r.value)}
              </span>
              <span className="text-[10px] text-tima-sub">{r.label}</span>
            </div>
          ))}
        </div>
      )}

      {hasVI && (
        <div className="overflow-hidden rounded border border-tima-line bg-white">
          <div className="border-b border-tima-line/60 bg-tima-bg/60 px-1.5 py-0.5 text-center text-[9px] font-semibold text-tima-sub">
            정적 VI 예상
          </div>
          {refData!.vi_up_expected != null && (
            <div className="flex items-center justify-between px-1.5 py-1">
              <span className="text-[10px] text-tima-sub">상승VI</span>
              <span className="font-mono text-[11px] font-semibold text-tima-up">
                {fmtNum(refData!.vi_up_expected)}
              </span>
            </div>
          )}
          {refData!.vi_down_expected != null && (
            <div className="flex items-center justify-between border-t border-tima-line/60 px-1.5 py-1">
              <span className="text-[10px] text-tima-sub">하락VI</span>
              <span className="font-mono text-[11px] font-semibold text-tima-down">
                {fmtNum(refData!.vi_down_expected)}
              </span>
            </div>
          )}
        </div>
      )}

      {strength != null && (
        <div className="flex items-center justify-between rounded border border-tima-line bg-white px-1.5 py-1">
          <span className="text-[10px] text-tima-sub">체결강도</span>
          <span
            className={`font-mono text-[11px] font-semibold ${strength >= 100 ? 'text-tima-up' : 'text-tima-down'}`}
          >
            {strength.toFixed(2)}
          </span>
        </div>
      )}
    </div>
  );
}

function ObRow({
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
  const barColor = isAsk ? 'bg-tima-down/15' : 'bg-tima-up/15';
  const qtyColor = isAsk ? 'text-tima-down' : 'text-tima-up';
  return (
    <div className="relative flex items-center border-b border-tima-line last:border-0 px-2 py-1">
      <div
        className={`absolute inset-y-0 ${isAsk ? 'left-0' : 'right-0'} ${barColor}`}
        style={{ width: `${w}%` }}
      />
      <div className="relative z-10 flex w-full items-center justify-between text-xs">
        {isAsk ? (
          <>
            <span className={`font-mono ${qtyColor}`}>{fmtNum(qty)}</span>
            <span className="flex items-baseline gap-1.5">
              <span className="font-mono text-tima-text">{fmtNum(price)}</span>
              {pct && <span className="w-12 text-right font-mono text-[10px] text-tima-sub">{pct}</span>}
            </span>
          </>
        ) : (
          <>
            <span className="flex items-baseline gap-1.5">
              <span className="font-mono text-tima-text">{fmtNum(price)}</span>
              {pct && <span className="w-12 text-right font-mono text-[10px] text-tima-sub">{pct}</span>}
            </span>
            <span className={`font-mono ${qtyColor}`}>{fmtNum(qty)}</span>
          </>
        )}
      </div>
    </div>
  );
}

// ── 프로그램 서브탭 (시간별/일별 토글 — PRD §4.6) ──
function ProgramTab({ symbol }: { symbol: string }) {
  const [mode, setMode] = useState<'time' | 'daily'>('time');
  const [items, setItems] = useState<ProgramItem[]>([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getProgram(symbol, mode)
      .then((res) => {
        if (cancelled) return;
        const d = res.data;
        setStatus(d?.status ?? 'unsupported');
        setItems(d?.status === 'ok' && Array.isArray(d.items) ? d.items : []);
      })
      .catch(() => {
        if (!cancelled) {
          setStatus('unsupported');
          setItems([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, mode]);

  const isOk = status === 'ok' && items.length > 0;

  return (
    <div>
      <div className="mb-2 flex gap-1.5">
        {(['time', 'daily'] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`rounded-full px-3.5 py-1 text-xs font-semibold transition-colors ${
              m === mode
                ? 'bg-tima-active text-black'
                : 'border border-tima-line bg-white text-tima-sub'
            }`}
          >
            {m === 'time' ? '시간별' : '일별'}
          </button>
        ))}
      </div>
      {loading ? (
        <div className="rounded-lg border border-tima-line bg-white py-10 text-center text-tima-sub">
          불러오는 중…
        </div>
      ) : isOk ? (
        <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
                  <th className="p-2 text-left text-xs font-semibold">{mode === 'time' ? '시간' : '일자'}</th>
                  <th className="p-2 text-right text-xs font-semibold">가격</th>
                  <th className="p-2 text-right text-xs font-semibold">거래량</th>
                  <th className="p-2 text-right text-xs font-semibold">순매수</th>
                  <th className="p-2 text-right text-xs font-semibold">증감</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, i) => (
                  <tr key={i} className="border-b border-tima-line last:border-0">
                    <td className="p-2 font-mono text-xs text-tima-text">{it.time_or_date}</td>
                    <td className="p-2 text-right font-mono text-tima-text">{fmtNum(it.price)}</td>
                    <td className="p-2 text-right font-mono text-tima-sub">{fmtNum(it.volume)}</td>
                    <td className={`p-2 text-right font-mono ${netColor(it.net_buy)}`}>
                      {fmtSigned(it.net_buy)}
                    </td>
                    <td className={`p-2 text-right font-mono ${netColor(it.net_buy_delta)}`}>
                      {fmtSigned(it.net_buy_delta)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-tima-line bg-white py-10 text-center text-tima-sub">
          프로그램 데이터 미연동
        </div>
      )}
    </div>
  );
}

// ── 거래원 서브탭 (매도/매수 상위 5 대칭 — PRD §4.6) ──
function BrokersTab({ symbol }: { symbol: string }) {
  const [data, setData] = useState<BrokersResponse | null>(null);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getBrokers(symbol)
      .then((res) => {
        if (cancelled) return;
        const d = res.data as BrokersResponse;
        setStatus(d?.status ?? 'unsupported');
        setData(d?.status === 'ok' ? d : null);
      })
      .catch(() => {
        if (!cancelled) {
          setStatus('unsupported');
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (loading) {
    return (
      <div className="rounded-lg border border-tima-line bg-white py-10 text-center text-tima-sub">
        불러오는 중…
      </div>
    );
  }
  if (status !== 'ok' || !data) {
    return (
      <div className="rounded-lg border border-tima-line bg-white py-10 text-center text-tima-sub">
        거래원 데이터 미연동
      </div>
    );
  }

  const rows = Math.max(data.sell?.length ?? 0, data.buy?.length ?? 0, 5);
  return (
    <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
            <th className="p-2 text-left text-xs font-semibold text-tima-down">매도상위</th>
            <th className="p-2 text-right text-xs font-semibold text-tima-down">수량</th>
            <th className="p-2 text-left text-xs font-semibold text-tima-up">매수상위</th>
            <th className="p-2 text-right text-xs font-semibold text-tima-up">수량</th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, i) => {
            const s = data.sell?.[i];
            const b = data.buy?.[i];
            return (
              <tr key={i} className="border-b border-tima-line last:border-0">
                <td className="p-2 text-xs text-tima-text">{s?.name ?? '-'}</td>
                <td className="p-2 text-right font-mono text-xs text-tima-down">
                  {s ? fmtNum(s.qty) : '-'}
                </td>
                <td className="p-2 text-xs text-tima-text">{b?.name ?? '-'}</td>
                <td className="p-2 text-right font-mono text-xs text-tima-up">
                  {b ? fmtNum(b.qty) : '-'}
                </td>
              </tr>
            );
          })}
        </tbody>
        {(data.foreign_sell != null || data.foreign_buy != null) && (
          <tfoot>
            <tr className="border-t border-tima-line bg-tima-bg/40">
              <td className="p-2 text-xs font-semibold text-tima-sub">외국계합</td>
              <td className="p-2 text-right font-mono text-xs text-tima-down">
                {fmtNum(data.foreign_sell)}
              </td>
              <td className="p-2" />
              <td className="p-2 text-right font-mono text-xs text-tima-up">
                {fmtNum(data.foreign_buy)}
              </td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

// ── 참고값 서브탭 (기준선 리스트 — 원형 뱃지 + 가격, PRD §4.6) ──
function LevelsTab({ symbol }: { symbol: string }) {
  const [levels, setLevels] = useState<StrategyLevel[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getChartLevels(symbol)
      .then((res) => {
        if (cancelled) return;
        const d = res.data;
        const lv = Array.isArray(d?.levels) ? d.levels : Array.isArray(d) ? d : [];
        setLevels(lv);
      })
      .catch(() => {
        if (!cancelled) setLevels([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (loading) {
    return (
      <div className="rounded-lg border border-tima-line bg-white py-10 text-center text-tima-sub">
        불러오는 중…
      </div>
    );
  }
  if (levels.length === 0) {
    return (
      <div className="rounded-lg border border-tima-line bg-white py-10 text-center text-tima-sub">
        기준가 데이터가 없습니다.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
      {levels.map((lv, i) => (
        <div
          key={`${lv.label}-${i}`}
          className="flex items-center justify-between border-b border-tima-line px-3 py-2 last:border-0"
        >
          <div className="flex items-center gap-2">
            <span
              className="flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-bold text-white"
              style={{ backgroundColor: levelBadgeColor(lv.label) }}
            >
              {lv.label}
            </span>
            {lv.active && (
              <span className="rounded border border-tima-emph px-1.5 py-0.5 text-[10px] font-semibold text-tima-emph">
                활성
              </span>
            )}
          </div>
          <span className="font-mono text-sm font-semibold text-tima-text">{fmtNum(lv.price)}</span>
        </div>
      ))}
    </div>
  );
}

// 순매수 부호 색/포맷
function netColor(n?: number | null): string {
  if (n === null || n === undefined) return 'text-tima-sub';
  return n > 0 ? 'text-tima-up' : n < 0 ? 'text-tima-down' : 'text-tima-text';
}

function fmtSigned(n?: number | null): string {
  if (n === null || n === undefined) return '-';
  const s = Math.abs(n).toLocaleString('ko-KR');
  return n > 0 ? `+${s}` : n < 0 ? `-${s}` : s;
}
