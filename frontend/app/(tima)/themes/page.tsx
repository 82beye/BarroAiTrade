'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Disclaimer } from '@/components/layout/disclaimer';
import { ThemeCardView } from '@/components/themes/theme-card';
import {
  api,
  type ThemeStockItem,
  type ThemeSnapshot,
  type ThemeMarketAggregateRaw,
} from '@/lib/api';

interface Theme {
  id: number;
  name: string;
  description: string;
}

const POLL_MS = 15_000;
const MIN_THEME_STOCKS = 2;

// 장중 3개 고정 스냅숏 시점 (PRD §3.2)
const SNAPSHOT_SLOTS = ['10:00', '12:30', '15:35'];

function calcThemeChangePct(stocks?: ThemeStockItem[]): number | null {
  const vals = (stocks ?? [])
    .map((s) => s.change_pct)
    .filter((v): v is number => v !== null && v !== undefined);
  if (vals.length === 0) return null;
  return vals.reduce((acc, v) => acc + v, 0) / vals.length;
}

// market_row_store 집계(거래대금/등락률 top-N 랭킹row 기준) — 오늘 실제 수급이
// 몰린 테마 순으로 카드 정렬에 쓴다. CSV 원천이라 필드가 전부 문자열로 온다.
export type ThemeMarketAggregate = {
  themeId: number;
  rankByValue: number | null;
  rankByChange: number | null;
  matchedCount: number;
  stockCount: number;
  sumValueTraded: number | null;
  avgChangePct: number | null;
  valueWeightedChangePct: number | null;
};

function toNum(v: string | undefined): number | null {
  if (v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function parseThemeAggregates(
  rows: ThemeMarketAggregateRaw[],
): Map<number, ThemeMarketAggregate> {
  const map = new Map<number, ThemeMarketAggregate>();
  for (const row of rows) {
    const themeId = toNum(row.theme_id);
    if (themeId === null) continue;
    map.set(themeId, {
      themeId,
      rankByValue: toNum(row.rank_by_value),
      rankByChange: toNum(row.rank_by_change),
      matchedCount: toNum(row.matched_count) ?? 0,
      stockCount: toNum(row.stock_count) ?? 0,
      sumValueTraded: toNum(row.sum_value_traded),
      avgChangePct: toNum(row.avg_change_pct),
      valueWeightedChangePct: toNum(row.value_weighted_change_pct),
    });
  }
  return map;
}

function currentKstDate(): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

type ThemeCardItem = {
  key: string;
  id?: number | string | null;
  name: string;
  description: string;
  stocks: ThemeStockItem[];
  themeChangePct: number | null;
  marketAggregate: ThemeMarketAggregate | null;
};

function mergeIndividualStocks(groups: ThemeStockItem[][]): ThemeStockItem[] {
  const bySymbol = new Map<string, ThemeStockItem>();
  for (const stocks of groups) {
    for (const stock of stocks) {
      const prev = bySymbol.get(stock.symbol);
      if (!prev || (stock.score ?? 0) > (prev.score ?? 0)) {
        bySymbol.set(stock.symbol, stock);
      }
    }
  }
  return Array.from(bySymbol.values()).sort((a, b) => {
    const av = a.change_pct;
    const bv = b.change_pct;
    if (av !== null && av !== undefined && bv !== null && bv !== undefined && av !== bv) {
      return bv - av;
    }
    if (av !== null && av !== undefined) return -1;
    if (bv !== null && bv !== undefined) return 1;
    return (b.score ?? 0) - (a.score ?? 0);
  });
}

function sortThemeItems(items: ThemeCardItem[]): ThemeCardItem[] {
  return [...items].sort((a, b) => {
    // 1순위: market_row_store 집계의 거래대금 랭킹(rank_by_value, 낮을수록 상위) —
    // 오늘 실제 수급이 몰린 테마부터. 두 테마 다 집계 커버(랭킹 top-N 매칭)일 때만 적용.
    const ar = a.marketAggregate?.rankByValue ?? null;
    const br = b.marketAggregate?.rankByValue ?? null;
    if (ar !== null && br !== null && ar !== br) return ar - br;
    if (ar !== null && br === null) return -1;
    if (ar === null && br !== null) return 1;
    // 2순위(집계 미커버 테마끼리): 카드 종목 등락률 평균 — 기존 로직 유지.
    const av = a.themeChangePct;
    const bv = b.themeChangePct;
    if (av !== null && bv !== null && av !== bv) return bv - av;
    if (av !== null && bv === null) return -1;
    if (av === null && bv !== null) return 1;
    return a.name.localeCompare(b.name, 'ko-KR');
  });
}

function buildThemeCardItems(
  themes: Theme[],
  themeStocks: Record<number, ThemeStockItem[] | undefined>,
  aggregates: Map<number, ThemeMarketAggregate>,
): ThemeCardItem[] {
  const themeItems: ThemeCardItem[] = [];
  const individualGroups: ThemeStockItem[][] = [];

  for (const theme of themes) {
    const stocks = themeStocks[theme.id];
    if (stocks !== undefined && stocks.length > 0 && stocks.length < MIN_THEME_STOCKS) {
      individualGroups.push(stocks);
      continue;
    }
    themeItems.push({
      key: `theme-${theme.id}`,
      id: theme.id,
      name: theme.name,
      description: theme.description,
      stocks: stocks ?? [],
      themeChangePct: calcThemeChangePct(stocks),
      marketAggregate: aggregates.get(theme.id) ?? null,
    });
  }

  const individualStocks = mergeIndividualStocks(individualGroups);
  if (individualStocks.length > 0) {
    themeItems.push({
      key: 'individual',
      id: null,
      name: '개별',
      description: '단일 종목 이슈',
      stocks: individualStocks,
      themeChangePct: calcThemeChangePct(individualStocks),
      marketAggregate: null,
    });
  }

  return sortThemeItems(themeItems);
}

// 타임라인 스냅숏 모달 (PRD §3.2)
function TimelineModal({ onClose }: { onClose: () => void }) {
  const [slot, setSlot] = useState(SNAPSHOT_SLOTS[SNAPSHOT_SLOTS.length - 1]);
  const [snapshot, setSnapshot] = useState<ThemeSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [noData, setNoData] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setNoData(false);
    api
      .getThemeSnapshots(undefined, slot)
      .then((res) => {
        if (cancelled) return;
        const data = res.data;
        if (!data || data.status === 'no_data' || !Array.isArray(data.themes)) {
          setSnapshot(null);
          setNoData(true);
        } else {
          setSnapshot(data as ThemeSnapshot);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSnapshot(null);
          setNoData(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slot]);

  const capturedLabel = snapshot?.captured_at
    ? new Date(snapshot.captured_at).toLocaleTimeString('ko-KR', {
        hour: '2-digit',
        minute: '2-digit',
      })
    : slot;
  const rankedSnapshotThemes = useMemo(
    () => {
      const themeList = (snapshot?.themes ?? []).map((theme) => ({
        id: Number(theme.id),
        name: theme.name,
        description: theme.description ?? '',
      }));
      const stocksByTheme = Object.fromEntries(
        (snapshot?.themes ?? []).map((theme) => [Number(theme.id), theme.stocks ?? []]),
      ) as Record<number, ThemeStockItem[]>;
      // 스냅숏(과거 동결)은 오늘자 실시간 집계 랭킹과 무관 — 빈 맵으로 기존
      // change_pct 기반 정렬 유지.
      return buildThemeCardItems(themeList, stocksByTheme, new Map());
    },
    [snapshot],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/50 p-3"
      onClick={onClose}
    >
      <div
        className="mx-auto flex h-full w-full max-w-[430px] flex-col overflow-hidden rounded-xl border border-tima-line bg-tima-bg"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between border-b border-tima-line bg-white px-4 py-3">
          <h2 className="flex items-center gap-2 text-base font-bold text-tima-text">
            타임라인
          </h2>
          <button
            onClick={onClose}
            className="rounded p-1.5 text-tima-sub hover:bg-tima-bg hover:text-tima-text"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>

        {/* slot 탭 (활성 황색 — PRD §3.2) */}
        <div className="flex items-center gap-2 border-b border-tima-line bg-white px-4 py-2.5">
          {SNAPSHOT_SLOTS.map((s) => {
            const on = s === slot;
            return (
              <button
                key={s}
                onClick={() => setSlot(s)}
                className={`rounded-full px-4 py-1.5 text-sm font-semibold transition-colors ${
                  on
                    ? 'bg-tima-active text-black'
                    : 'bg-tima-bg text-tima-sub hover:bg-tima-line'
                }`}
              >
                {s}
              </button>
            );
          })}
          {snapshot && (
            <span className="ml-auto self-center text-[11px] text-tima-sub">
              동결 {capturedLabel}
            </span>
          )}
        </div>

        {/* 본문 */}
        <div className="flex-1 overflow-y-auto p-3">
          {loading ? (
            <div className="py-12 text-center text-tima-sub">스냅숏 불러오는 중…</div>
          ) : noData || !snapshot ? (
            <div className="py-12 text-center text-tima-sub">해당 시각 스냅숏이 없습니다.</div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              {rankedSnapshotThemes.map((t) => (
                <ThemeCardView
                  key={t.key}
                  id={t.id}
                  name={t.name}
                  description={t.description}
                  stocks={t.stocks}
                  capturedAt={capturedLabel}
                  themeChangePct={t.themeChangePct}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ThemesPage() {
  const [themes, setThemes] = useState<Theme[]>([]);
  const [themeStocks, setThemeStocks] = useState<Record<number, ThemeStockItem[] | undefined>>({});
  const [aggregates, setAggregates] = useState<Map<number, ThemeMarketAggregate>>(new Map());
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [showTimeline, setShowTimeline] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadingRef = useRef(false);

  const rankedThemes = useMemo(
    () => buildThemeCardItems(themes, themeStocks, aggregates),
    [themeStocks, themes, aggregates],
  );

  useEffect(() => {
    let cancelled = false;

    const loadThemeBoard = async () => {
      if (loadingRef.current) return;
      loadingRef.current = true;
      try {
        const themeRes = await api.getThemes();
        if (cancelled) return;
        const themeList: Theme[] = Array.isArray(themeRes.data) ? themeRes.data : [];
        const themeIds = new Set(themeList.map((theme) => String(theme.id)));
        setThemes(themeList);
        setThemeStocks((prev) =>
          Object.fromEntries(
            Object.entries(prev).filter(([themeId]) => themeIds.has(themeId)),
          ),
        );
        setLoading(false);
        setLastUpdated(new Date());

        // 정렬 기준(market_row_store 집계) — 실패해도 카드 표시 자체는 막지 않고
        // 기존 change_pct 기반 정렬로 조용히 강등(날조 금지, 화면 렌더 최우선).
        try {
          const aggRes = await api.getThemeMarketAggregates(200);
          if (!cancelled) {
            const isCurrentTradeDate = aggRes.data?.meta?.trade_date === currentKstDate();
            const rows: ThemeMarketAggregateRaw[] =
              isCurrentTradeDate && Array.isArray(aggRes.data?.aggregates)
              ? aggRes.data.aggregates
              : [];
            setAggregates(parseThemeAggregates(rows));
          }
        } catch {
          if (!cancelled) setAggregates(new Map());
        }

        await Promise.all(
          themeList.map(async (theme) => {
            try {
              const stockRes = await api.getThemeStocks(theme.id);
              if (!cancelled) {
                setThemeStocks((prev) => ({
                  ...prev,
                  [theme.id]: Array.isArray(stockRes.data) ? stockRes.data : [],
                }));
              }
            } catch {
              if (!cancelled) {
                setThemeStocks((prev) => ({ ...prev, [theme.id]: [] }));
              }
            }
          }),
        );
      } catch {
        if (!cancelled) {
          setThemes([]);
          setThemeStocks({});
        }
      } finally {
        loadingRef.current = false;
        if (!cancelled) {
          setLoading(false);
          setLastUpdated(new Date());
        }
      }
    };

    loadThemeBoard();

    intervalRef.current = setInterval(() => {
      loadThemeBoard();
    }, POLL_MS);

    return () => {
      cancelled = true;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  return (
    <div className="p-3">
      <div className="mb-2 flex items-center justify-between">
        <button
          onClick={() => setShowTimeline(true)}
          className="flex items-center gap-1 rounded-full border border-tima-line bg-white px-3 py-1 text-xs font-semibold text-tima-text"
        >
          🕐 타임라인
        </button>
        <span className="text-[11px] text-tima-sub">
          {lastUpdated ? `${lastUpdated.toLocaleTimeString('ko-KR')} · 15초 갱신` : '15초 자동 갱신'}
        </span>
      </div>

      {loading ? (
        <div className="py-12 text-center text-tima-sub">테마 불러오는 중…</div>
      ) : themes.length === 0 ? (
        <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
          표시할 테마가 없습니다. 데이터 대기 중.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {rankedThemes.map((item) => (
            <ThemeCardView
              key={item.key}
              id={item.id}
              name={item.name}
              description={item.description}
              stocks={item.stocks}
              themeChangePct={item.themeChangePct}
            />
          ))}
        </div>
      )}

      <Disclaimer />

      {showTimeline && <TimelineModal onClose={() => setShowTimeline(false)} />}
    </div>
  );
}
