'use client';

import { useEffect, useRef, useState } from 'react';
import { Disclaimer } from '@/components/layout/disclaimer';
import { ThemeCardView } from '@/components/themes/theme-card';
import { api, type ThemeStockItem, type ThemeSnapshot } from '@/lib/api';

interface Theme {
  id: number;
  name: string;
  description: string;
}

const POLL_MS = 15_000;

// 장중 3개 고정 스냅숏 시점 (PRD §3.2)
const SNAPSHOT_SLOTS = ['10:00', '12:30', '15:35'];

// 라이브 테마 카드 (자체 종목 조회 + 폴링)
function LiveThemeCard({ theme, tick }: { theme: Theme; tick: number }) {
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

  return <ThemeCardView name={theme.name} description={theme.description} stocks={stocks} />;
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
              {snapshot.themes.map((t) => (
                <ThemeCardView
                  key={t.id}
                  name={t.name}
                  description={t.description}
                  stocks={t.stocks ?? []}
                  capturedAt={capturedLabel}
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
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const [showTimeline, setShowTimeline] = useState(false);
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
          {themes.map((t) => (
            <LiveThemeCard key={t.id} theme={t} tick={tick} />
          ))}
        </div>
      )}

      <Disclaimer />

      {showTimeline && <TimelineModal onClose={() => setShowTimeline(false)} />}
    </div>
  );
}
