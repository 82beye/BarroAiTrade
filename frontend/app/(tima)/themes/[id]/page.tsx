'use client';

import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { Disclaimer } from '@/components/layout/disclaimer';
import { WatchlistStar } from '@/components/watchlist/watchlist-star';
import { api, type ThemeStockItem, type NewsItem } from '@/lib/api';

interface Theme {
  id: number;
  name: string;
  description?: string | null;
}

const POLL_MS = 15_000;

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

// 등락률 → 전일대비(변화금액) 역산 (ThemeStockItem 은 change_pct 만 제공)
function prevChange(price?: number | null, cp?: number | null): number | null {
  if (price === null || price === undefined || cp === null || cp === undefined) return null;
  const prev = price / (1 + cp / 100);
  return price - prev;
}

// 등락률 내림차순 (null 뒤로)
function sortByChange(a: ThemeStockItem, b: ThemeStockItem): number {
  const ca = a.change_pct;
  const cb = b.change_pct;
  const aNull = ca === null || ca === undefined;
  const bNull = cb === null || cb === undefined;
  if (!aNull && !bNull) return (cb as number) - (ca as number);
  if (aNull && bNull) return (b.score ?? 0) - (a.score ?? 0);
  return aNull ? 1 : -1;
}

export default function ThemeDetailPage() {
  const params = useParams();
  const id = Array.isArray(params.id) ? params.id[0] : (params.id ?? '');

  const [theme, setTheme] = useState<Theme | null>(null);
  const [stocks, setStocks] = useState<ThemeStockItem[]>([]);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [issuesOpen, setIssuesOpen] = useState(false);

  // 테마 메타 (전체 목록에서 id 매칭)
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    api
      .getThemes()
      .then((res) => {
        if (cancelled) return;
        const list: Theme[] = Array.isArray(res.data) ? res.data : [];
        const found = list.find((t) => String(t.id) === String(id));
        setTheme(found ?? { id: Number(id), name: `테마 ${id}`, description: null });
      })
      .catch(() => {
        if (!cancelled) setTheme({ id: Number(id), name: `테마 ${id}`, description: null });
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // 종목 (폴링)
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const load = () =>
      api
        .getThemeStocks(id)
        .then((res) => {
          if (!cancelled) setStocks(Array.isArray(res.data) ? res.data : []);
        })
        .catch(() => {
          if (!cancelled) setStocks([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [id]);

  // 관련 뉴스 (테마명 매칭 — 최근이슈)
  useEffect(() => {
    let cancelled = false;
    api
      .getRecentNews(50)
      .then((res) => {
        if (cancelled) return;
        const data = res.data;
        setNews(Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : []);
      })
      .catch(() => {
        if (!cancelled) setNews([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sorted = useMemo(() => [...stocks].sort(sortByChange), [stocks]);

  const themeIssues = useMemo(() => {
    const name = theme?.name;
    if (!name) return [];
    const needle = name.toLowerCase();
    return news
      .filter((n) => `${n.title ?? ''} ${(n.tags ?? []).join(' ')}`.toLowerCase().includes(needle))
      .slice(0, 12);
  }, [news, theme?.name]);

  return (
    <div className="p-3">
      {/* 헤더 (< + 테마명) */}
      <div className="mb-3 flex items-center gap-2">
        <button
          onClick={() => history.back()}
          aria-label="뒤로"
          className="shrink-0 text-lg text-tima-text"
        >
          ‹
        </button>
        <h1 className="truncate text-lg font-bold text-tima-text">{theme?.name ?? '테마'}</h1>
      </div>

      {/* 테마개요 (보라 좌측 바) */}
      {theme?.description && (
        <div className="mb-4 rounded-lg border border-tima-line bg-white p-3">
          <h2 className="mb-1.5 text-sm font-bold text-tima-teal">테마개요</h2>
          <p className="border-l-2 border-strategyLine-sf pl-3 text-sm leading-relaxed text-tima-text">
            {theme.description}
          </p>
        </div>
      )}

      {/* 최근이슈 (더보기) */}
      {themeIssues.length > 0 && (
        <div className="mb-4 rounded-lg border border-tima-line bg-white p-3">
          <h2 className="mb-2 text-sm font-bold text-tima-teal">최근이슈</h2>
          <div className="space-y-2">
            {(issuesOpen ? themeIssues : themeIssues.slice(0, 3)).map((n) => (
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
          {themeIssues.length > 3 && (
            <button
              onClick={() => setIssuesOpen((v) => !v)}
              className="mt-2 text-xs font-semibold text-tima-teal hover:underline"
            >
              {issuesOpen ? '접기' : `더보기 (${themeIssues.length - 3})`}
            </button>
          )}
        </div>
      )}

      {/* 종목 테이블 */}
      {loading ? (
        <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
          불러오는 중…
        </div>
      ) : sorted.length === 0 ? (
        <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
          테마 종목 데이터가 없습니다.
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
                  <th className="min-w-[100px] whitespace-nowrap p-2.5 text-left text-xs font-semibold">
                    종목명
                  </th>
                  <th className="whitespace-nowrap p-2.5 text-right text-xs font-semibold">현재가</th>
                  <th className="whitespace-nowrap p-2.5 text-right text-xs font-semibold">전일대비</th>
                  <th className="whitespace-nowrap p-2.5 text-right text-xs font-semibold">등락률</th>
                  <th className="whitespace-nowrap p-2.5 text-right text-xs font-semibold">대금(억)</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((s) => {
                  const cp = s.change_pct;
                  const hasCp = cp !== null && cp !== undefined;
                  const up = (cp ?? 0) >= 0;
                  const dirColor = !hasCp ? 'text-tima-sub' : up ? 'text-tima-up' : 'text-tima-down';
                  const chg = prevChange(s.price, cp);
                  return (
                    <tr
                      key={s.symbol}
                      className="border-b border-tima-line last:border-0 hover:bg-tima-bg/50"
                    >
                      <td className="p-2.5">
                        <div className="flex items-center gap-1.5">
                          <WatchlistStar symbol={s.symbol} size="sm" />
                          <Link
                            href={`/stocks/${s.symbol}`}
                            className="whitespace-nowrap font-bold text-tima-text hover:text-tima-teal hover:underline"
                          >
                            {s.name ?? s.symbol}
                          </Link>
                        </div>
                      </td>
                      <td className={`p-2.5 text-right font-mono font-semibold ${dirColor}`}>
                        {fmtNum(s.price)}
                      </td>
                      <td className={`p-2.5 text-right font-mono ${dirColor}`}>
                        {chg === null
                          ? '-'
                          : `${up ? '▲' : '▼'} ${fmtNum(Math.round(Math.abs(chg)))}`}
                      </td>
                      <td className={`p-2.5 text-right font-mono ${dirColor}`}>
                        {!hasCp ? '-' : `${up ? '+' : '-'}${Math.abs(cp as number).toFixed(2)}%`}
                      </td>
                      <td className="p-2.5 text-right font-mono text-tima-text">
                        {fmtNum(s.value_traded)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Disclaimer />
    </div>
  );
}
