'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Disclaimer } from '@/components/layout/disclaimer';
import { api, type CalendarEvent } from '@/lib/api';

type ViewMode = 'day' | 'week' | 'month';

// ── 카테고리 매핑 (PRD §3.5: 테마 빨강 / 개별 주황 / 정책 초록) ──
interface CategoryStyle {
  label: string;
  dot: string; // 원형 뱃지 배경색
  chip: string; // 월간 파스텔 칩 클래스
}

function categoryStyle(eventType: string): CategoryStyle {
  const t = (eventType || '').toLowerCase();
  if (t === 'theme' || t === 'earnings')
    return { label: '테마', dot: '#D00010', chip: 'bg-rose-100 text-rose-700' };
  if (t === 'individual' || t === 'dividend' || t === 'split')
    return { label: '개별', dot: '#E08040', chip: 'bg-amber-100 text-amber-700' };
  if (t === 'policy' || t === 'macro' || t === 'holiday')
    return { label: '정책', dot: '#38B068', chip: 'bg-emerald-100 text-emerald-700' };
  return { label: eventType || '기타', dot: '#94a3b8', chip: 'bg-slate-200 text-slate-600' };
}

// ── 로컬 날짜 헬퍼 (toISOString UTC 오차 회피) ──
function ymd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

function startOfWeek(d: Date): Date {
  // 일요일 시작
  return addDays(d, -d.getDay());
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

function CategoryBadge({ ev }: { ev: CalendarEvent }) {
  const c = categoryStyle(ev.event_type);
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold text-white"
      style={{ backgroundColor: c.dot }}
    >
      {c.label}
    </span>
  );
}

export default function CalendarPage() {
  const [view, setView] = useState<ViewMode>('day');
  const [anchor, setAnchor] = useState<Date>(() => new Date());
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const todayStr = ymd(new Date());

  // 뷰 범위 계산
  const range = useMemo(() => {
    if (view === 'day') return { start: ymd(anchor), end: ymd(anchor) };
    if (view === 'week') {
      const s = startOfWeek(anchor);
      return { start: ymd(s), end: ymd(addDays(s, 6)) };
    }
    // month: 앞뒤 주말 그리드 포함 범위
    const first = startOfMonth(anchor);
    const gridStart = startOfWeek(first);
    const last = endOfMonth(anchor);
    const gridEnd = addDays(startOfWeek(last), 6);
    return { start: ymd(gridStart), end: ymd(gridEnd) };
  }, [view, anchor]);

  const load = useCallback(async (start: string, end: string) => {
    setLoading(true);
    try {
      const res = await api.getCalendar(start, end);
      const data = res.data;
      setEvents(
        Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [],
      );
    } catch {
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(range.start, range.end);
  }, [range.start, range.end, load]);

  // 날짜별 그룹
  const byDate = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const e of events) {
      const k = (e.event_date || '').slice(0, 10);
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(e);
    }
    return map;
  }, [events]);

  // 네비게이션 단위 이동
  function nav(dir: -1 | 1) {
    if (view === 'day') setAnchor((d) => addDays(d, dir));
    else if (view === 'week') setAnchor((d) => addDays(d, dir * 7));
    else setAnchor((d) => new Date(d.getFullYear(), d.getMonth() + dir, 1));
  }

  const navLabel = useMemo(() => {
    if (view === 'day')
      return `${anchor.getFullYear()}.${anchor.getMonth() + 1}.${anchor.getDate()} (${WEEKDAYS[anchor.getDay()]})`;
    if (view === 'week') {
      const s = startOfWeek(anchor);
      const e = addDays(s, 6);
      return `${s.getMonth() + 1}.${s.getDate()} ~ ${e.getMonth() + 1}.${e.getDate()}`;
    }
    return `${anchor.getFullYear()}.${anchor.getMonth() + 1}`;
  }, [view, anchor]);

  // 당일 이벤트 수 (일 뷰 기준일 / 그 외 오늘)
  const dayCount = useMemo(() => {
    const key = view === 'day' ? ymd(anchor) : todayStr;
    return byDate.get(key)?.length ?? 0;
  }, [byDate, view, anchor, todayStr]);

  return (
    <div className="p-3">
      {/* 상단 컨트롤 */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span className="flex h-6 min-w-6 items-center justify-center rounded-full bg-tima-up px-1.5 text-xs font-bold text-white">
            {dayCount}
          </span>
          <button
            onClick={() => nav(-1)}
            className="rounded-md border border-tima-line bg-white px-2 py-1 text-tima-sub hover:bg-tima-bg"
            aria-label="이전"
          >
            ‹
          </button>
          <button
            onClick={() => setAnchor(new Date())}
            className="min-w-[7rem] rounded-md border border-tima-line bg-white px-2 py-1 text-center text-sm font-semibold text-tima-text hover:bg-tima-bg"
            title="오늘로"
          >
            {navLabel}
          </button>
          <button
            onClick={() => nav(1)}
            className="rounded-md border border-tima-line bg-white px-2 py-1 text-tima-sub hover:bg-tima-bg"
            aria-label="다음"
          >
            ›
          </button>
        </div>

        {/* 일/주/월 뷰 토글 (활성 tima.teal) */}
        <div className="flex gap-1 rounded-md border border-tima-line bg-white p-0.5">
          {(
            [
              { key: 'day', label: '일' },
              { key: 'week', label: '주' },
              { key: 'month', label: '월' },
            ] as { key: ViewMode; label: string }[]
          ).map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setView(key)}
              className={`rounded px-3 py-1 text-sm font-semibold transition-colors ${
                view === key ? 'bg-tima-teal text-white' : 'text-tima-sub hover:text-tima-text'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 본문 */}
      {loading ? (
        <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
          불러오는 중…
        </div>
      ) : view === 'day' ? (
        <DayView events={byDate.get(ymd(anchor)) ?? []} />
      ) : view === 'week' ? (
        <WeekView start={startOfWeek(anchor)} byDate={byDate} todayStr={todayStr} />
      ) : (
        <MonthView
          anchor={anchor}
          byDate={byDate}
          todayStr={todayStr}
          onPickDay={(d) => {
            setAnchor(d);
            setView('day');
          }}
        />
      )}

      <p className="mt-4 text-[11px] leading-relaxed text-tima-sub">
        자료를 참고하여 당사 내부적으로 가공되었으므로 오류가 있을 수 있습니다. 해당 정보는 투자
        권유나 종목 추천이 아닙니다. 모든 투자의 책임은 투자자 본인에게 있습니다.
      </p>
      <Disclaimer />
    </div>
  );
}

// ── 일 뷰 ──
function DayView({ events }: { events: CalendarEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
        최근 특별한 일정이 없습니다.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {events.map((ev) => (
        <div
          key={ev.id}
          className="flex items-start gap-3 rounded-xl border border-tima-line bg-white px-3 py-3 shadow-sm"
        >
          <CategoryBadge ev={ev} />
          <div className="flex-1">
            <p className="text-sm text-tima-text">{ev.title}</p>
            <div className="mt-1 flex items-center gap-2 text-xs text-tima-sub">
              {ev.source && <span>{ev.source}</span>}
              {ev.symbol && (
                <Link
                  href={`/stocks/${ev.symbol}`}
                  className="font-mono text-tima-teal hover:underline"
                >
                  {ev.symbol} →
                </Link>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── 주 뷰 (7일 컬럼) ──
function WeekView({
  start,
  byDate,
  todayStr,
}: {
  start: Date;
  byDate: Map<string, CalendarEvent[]>;
  todayStr: string;
}) {
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {days.map((d) => {
        const key = ymd(d);
        const evs = byDate.get(key) ?? [];
        const isToday = key === todayStr;
        return (
          <div
            key={key}
            className={`rounded-lg border bg-white p-3 ${
              isToday ? 'border-tima-up ring-1 ring-tima-up' : 'border-tima-line'
            }`}
          >
            <div
              className={`mb-2 flex items-baseline justify-between ${
                d.getDay() === 0 || d.getDay() === 6 ? 'text-tima-sub' : 'text-tima-text'
              }`}
            >
              <span className="text-sm font-semibold">
                {d.getMonth() + 1}.{d.getDate()} ({WEEKDAYS[d.getDay()]})
              </span>
              {evs.length > 0 && <span className="text-xs text-tima-sub">{evs.length}</span>}
            </div>
            {evs.length === 0 ? (
              <p className="text-xs text-tima-line">-</p>
            ) : (
              <div className="space-y-1.5">
                {evs.map((ev) => {
                  const c = categoryStyle(ev.event_type);
                  return (
                    <div key={ev.id} className="flex items-start gap-1.5">
                      <span
                        className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full"
                        style={{ backgroundColor: c.dot }}
                      />
                      <span className="line-clamp-2 text-xs text-tima-text">{ev.title}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── 월 뷰 (요일 그리드 달력) ──
function MonthView({
  anchor,
  byDate,
  todayStr,
  onPickDay,
}: {
  anchor: Date;
  byDate: Map<string, CalendarEvent[]>;
  todayStr: string;
  onPickDay: (d: Date) => void;
}) {
  const [showWeekend, setShowWeekend] = useState(true);
  const first = startOfMonth(anchor);
  const gridStart = startOfWeek(first);
  const cells = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
  const month = anchor.getMonth();

  return (
    <div className="rounded-lg border border-tima-line bg-white p-2">
      {/* 주말 표시 토글 */}
      <div className="mb-2 flex justify-end">
        <button
          onClick={() => setShowWeekend((v) => !v)}
          className={`rounded-full px-2.5 py-1 text-xs font-medium ${
            showWeekend ? 'bg-emerald-100 text-emerald-700' : 'bg-tima-bg text-tima-sub'
          }`}
        >
          주말 {showWeekend ? 'ON' : 'OFF'}
        </button>
      </div>

      {/* 요일 헤더 */}
      <div className="mb-1 grid grid-cols-7 gap-1 text-center text-xs font-semibold text-tima-sub">
        {WEEKDAYS.map((w, i) => (
          <div key={w} className={i === 0 ? 'text-tima-up' : i === 6 ? 'text-tima-down' : ''}>
            {w}
          </div>
        ))}
      </div>

      {/* 날짜 셀 */}
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d) => {
          const key = ymd(d);
          const evs = byDate.get(key) ?? [];
          const inMonth = d.getMonth() === month;
          const isToday = key === todayStr;
          const isWeekend = d.getDay() === 0 || d.getDay() === 6;
          const dimWeekend = isWeekend && !showWeekend;
          return (
            <button
              key={key}
              onClick={() => onPickDay(d)}
              className={`flex min-h-[4.5rem] flex-col rounded border p-1 text-left transition-colors ${
                isToday ? 'border-tima-up ring-1 ring-tima-up' : 'border-tima-line'
              } ${inMonth ? 'bg-white hover:bg-tima-bg/50' : 'bg-tima-bg/40'} ${
                dimWeekend ? 'opacity-40' : ''
              }`}
            >
              <span
                className={`mb-0.5 text-xs font-semibold ${
                  isToday
                    ? 'text-tima-up'
                    : !inMonth
                      ? 'text-tima-line'
                      : d.getDay() === 0
                        ? 'text-tima-up'
                        : d.getDay() === 6
                          ? 'text-tima-down'
                          : 'text-tima-text'
                }`}
              >
                {d.getDate()}
              </span>
              <div className="space-y-0.5">
                {evs.slice(0, 3).map((ev) => {
                  const c = categoryStyle(ev.event_type);
                  return (
                    <div
                      key={ev.id}
                      className={`truncate rounded px-1 py-0.5 text-[9px] font-medium ${c.chip}`}
                      title={ev.title}
                    >
                      {ev.title}
                    </div>
                  );
                })}
                {evs.length > 3 && (
                  <div className="px-1 text-[9px] text-tima-sub">+{evs.length - 3}</div>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
