'use client';

import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';

// ── Types ──────────────────────────────────────────────────────────────────
interface Theme {
  id: number;
  name: string;
  description: string;
}

interface ThemeStock {
  symbol: string;
  score: number;
  theme_id: number;
  theme_name: string | null;
}

interface CalendarEvent {
  id: number;
  event_type: string;
  symbol: string | null;
  event_date: string;
  title: string;
  source: string;
}

interface NewsItem {
  id: number;
  source: string;
  source_id: string;
  title: string;
  url: string;
  published_at: string;
  tags: string[];
}

type Tab = 'themes' | 'calendar' | 'news';

const EVENT_TYPE_COLORS: Record<string, string> = {
  earnings: 'bg-blue-900 text-blue-300',
  dividend: 'bg-green-900 text-green-300',
  split: 'bg-purple-900 text-purple-300',
  holiday: 'bg-slate-700 text-slate-300',
  macro: 'bg-orange-900 text-orange-300',
};

function dateRange(days: number) {
  const today = new Date();
  const start = new Date(today);
  start.setDate(today.getDate() - 1);
  const end = new Date(today);
  end.setDate(today.getDate() + days);
  return {
    start: start.toISOString().split('T')[0],
    end: end.toISOString().split('T')[0],
  };
}

// ── Sub-components ─────────────────────────────────────────────────────────
function ThemesTab() {
  const [themes, setThemes] = useState<Theme[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [stocks, setStocks] = useState<Record<number, ThemeStock[]>>({});
  const [loadingStocks, setLoadingStocks] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/themes')
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setThemes)
      .catch(() => setError('테마 데이터를 불러올 수 없습니다'))
      .finally(() => setLoading(false));
  }, []);

  async function toggleTheme(id: number) {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    if (stocks[id]) return;
    setLoadingStocks(id);
    try {
      const r = await fetch(`/api/themes/${id}/stocks`);
      if (r.ok) {
        const data = await r.json();
        setStocks((prev) => ({ ...prev, [id]: data }));
      }
    } finally {
      setLoadingStocks(null);
    }
  }

  if (loading) return <div className="space-y-2">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-12 w-full rounded-lg" />)}</div>;
  if (error) return <p className="text-sm text-slate-500">{error}</p>;
  if (themes.length === 0) return <p className="text-sm text-slate-500">테마 데이터가 없습니다</p>;

  return (
    <div className="space-y-2">
      {themes.map((theme) => (
        <div key={theme.id} className="rounded-lg border border-slate-700 bg-slate-800">
          <button
            onClick={() => toggleTheme(theme.id)}
            className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-slate-700 hover:bg-opacity-50"
          >
            <div>
              <span className="font-medium text-slate-200">{theme.name}</span>
              {theme.description && (
                <span className="ml-3 text-sm text-slate-500">{theme.description}</span>
              )}
            </div>
            <span className="text-slate-500">{expanded === theme.id ? '▲' : '▼'}</span>
          </button>

          {expanded === theme.id && (
            <div className="border-t border-slate-700 px-4 py-3">
              {loadingStocks === theme.id ? (
                <p className="text-sm text-slate-500">종목 로딩 중...</p>
              ) : (stocks[theme.id] ?? []).length === 0 ? (
                <p className="text-sm text-slate-500">관련 종목 없음</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {stocks[theme.id].map((s) => (
                    <div
                      key={s.symbol}
                      className="flex items-center gap-1.5 rounded-lg border border-slate-600 bg-slate-900 px-3 py-1.5"
                    >
                      <span className="font-mono text-sm font-semibold text-slate-200">{s.symbol}</span>
                      <span className="text-xs text-slate-500">점수 {s.score.toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function CalendarTab() {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);

  const load = useCallback(async (d: number) => {
    setLoading(true);
    const { start, end } = dateRange(d);
    try {
      const r = await fetch(`/api/calendar?start=${start}&end=${end}`);
      if (r.ok) setEvents(await r.json());
      else setEvents([]);
    } catch {
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(days); }, [days, load]);

  const grouped = events.reduce<Record<string, CalendarEvent[]>>((acc, e) => {
    (acc[e.event_date] ??= []).push(e);
    return acc;
  }, {});
  const sortedDates = Object.keys(grouped).sort();

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <span className="text-sm text-slate-400">기간:</span>
        {[7, 14, 30].map((d) => (
          <button
            key={d}
            onClick={() => setDays(d)}
            className={`rounded px-3 py-1 text-sm ${days === d ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
          >
            {d}일
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-2">{[...Array(4)].map((_, i) => <Skeleton key={i} className="h-16 w-full rounded-lg" />)}</div>
      ) : sortedDates.length === 0 ? (
        <p className="text-sm text-slate-500">해당 기간 이벤트가 없습니다</p>
      ) : (
        <div className="space-y-4">
          {sortedDates.map((date) => {
            const d = new Date(date);
            const isToday = date === new Date().toISOString().split('T')[0];
            return (
              <div key={date}>
                <div className={`mb-2 flex items-center gap-2 text-sm font-semibold ${isToday ? 'text-blue-400' : 'text-slate-400'}`}>
                  <span>{d.toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' })}</span>
                  {isToday && <span className="rounded bg-blue-600 px-1.5 py-0.5 text-xs text-white">오늘</span>}
                </div>
                <div className="space-y-1.5">
                  {grouped[date].map((ev) => (
                    <div key={ev.id} className="flex items-start gap-3 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2">
                      <span className={`mt-0.5 rounded px-1.5 py-0.5 text-xs font-semibold ${EVENT_TYPE_COLORS[ev.event_type] ?? 'bg-slate-700 text-slate-300'}`}>
                        {ev.event_type}
                      </span>
                      <div className="flex-1">
                        <p className="text-sm text-slate-200">{ev.title}</p>
                        {ev.symbol && <p className="mt-0.5 font-mono text-xs text-slate-500">{ev.symbol}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function NewsTab() {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTag, setActiveTag] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/news/recent?limit=50')
      .then((r) => (r.ok ? r.json() : []))
      .then(setNews)
      .catch(() => setNews([]))
      .finally(() => setLoading(false));
  }, []);

  const allTags = Array.from(new Set(news.flatMap((n) => n.tags))).slice(0, 20);
  const filtered = activeTag ? news.filter((n) => n.tags.includes(activeTag)) : news;

  return (
    <div>
      {allTags.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          <button
            onClick={() => setActiveTag(null)}
            className={`rounded px-2.5 py-1 text-xs ${!activeTag ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
          >
            전체
          </button>
          {allTags.map((tag) => (
            <button
              key={tag}
              onClick={() => setActiveTag(tag === activeTag ? null : tag)}
              className={`rounded px-2.5 py-1 text-xs ${activeTag === tag ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="space-y-2">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-14 w-full rounded-lg" />)}</div>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-slate-500">뉴스가 없습니다</p>
      ) : (
        <div className="space-y-2">
          {filtered.map((item) => (
            <a
              key={item.id}
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block rounded-lg border border-slate-700 bg-slate-800 px-4 py-3 hover:border-slate-500 hover:bg-slate-700 hover:bg-opacity-60"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <p className="text-sm font-medium text-slate-200 leading-snug">{item.title}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    <span className="text-xs text-slate-500">{item.source}</span>
                    <span className="text-xs text-slate-600">
                      {new Date(item.published_at).toLocaleDateString('ko-KR', {
                        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                      })}
                    </span>
                    {item.tags.slice(0, 3).map((tag) => (
                      <Badge key={tag} variant="secondary" className="bg-slate-700 text-xs text-slate-400">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </div>
                <span className="mt-0.5 text-slate-600">↗</span>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────
export default function MarketInfoPage() {
  const [tab, setTab] = useState<Tab>('themes');

  const tabs: { key: Tab; label: string }[] = [
    { key: 'themes', label: '테마주' },
    { key: 'calendar', label: '경제 캘린더' },
    { key: 'news', label: '최근 뉴스' },
  ];

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">시장 정보</h1>
        <p className="mt-2 text-slate-400">테마주 · 경제 이벤트 · 최신 뉴스</p>
      </div>

      {/* Tabs */}
      <div className="mb-6 flex gap-1 rounded-lg border border-slate-700 bg-slate-800 p-1 w-fit">
        {tabs.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-md px-5 py-2 text-sm font-medium transition-colors ${
              tab === key
                ? 'bg-blue-600 text-white'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <Card className="border-slate-700 bg-slate-800">
        <CardContent className="pt-4">
          {tab === 'themes' && <ThemesTab />}
          {tab === 'calendar' && <CalendarTab />}
          {tab === 'news' && <NewsTab />}
        </CardContent>
      </Card>
    </div>
  );
}
