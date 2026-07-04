'use client';

import { useEffect, useRef, useState } from 'react';
import { api, type NewsItem, type MarketIndex } from '@/lib/api';

const POLL_MS = 60_000;
const ROLL_MS = 10_000;

function timeLabel(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

/**
 * 티마 셸 하단 티커 (PRD §2.1 하단 글로벌 티커) — 라이트.
 * ① 특징주 뉴스 1줄(베이지 배경 + 주황 [특징주] 라벨, 10초 롤링)
 * ② 지수 바(연보라 배경) — 미연동 시 "지수 미연동" 소문구.
 * 기존 news-ticker 의 폴링/롤링 로직 재사용.
 */
export function TimaTicker() {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [indices, setIndices] = useState<MarketIndex[]>([]);
  const [idx, setIdx] = useState(0);
  const [fade, setFade] = useState(true);
  const rollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      api
        .getRecentNews(30)
        .then((res) => {
          if (cancelled) return;
          const data = res.data;
          setNews(Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : []);
        })
        .catch(() => !cancelled && setNews([]));
      api
        .getMarketIndices()
        .then((res) => {
          if (cancelled) return;
          const data = res.data;
          if (data?.status === 'ok' && Array.isArray(data.items)) setIndices(data.items);
          else setIndices([]);
        })
        .catch(() => !cancelled && setIndices([]));
    };
    load();
    const poll = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(poll);
    };
  }, []);

  useEffect(() => {
    if (news.length <= 1) return;
    rollRef.current = setInterval(() => {
      setFade(false);
      setTimeout(() => {
        setIdx((i) => (i + 1) % news.length);
        setFade(true);
      }, 300);
    }, ROLL_MS);
    return () => {
      if (rollRef.current) clearInterval(rollRef.current);
    };
  }, [news.length]);

  useEffect(() => {
    if (idx >= news.length && news.length > 0) setIdx(0);
  }, [news.length, idx]);

  const current = news[Math.min(idx, Math.max(news.length - 1, 0))];

  return (
    <div>
      {/* ① 특징주 뉴스 1줄 (베이지) */}
      <a
        href={current?.url}
        target="_blank"
        rel="noopener noreferrer"
        className="flex h-8 items-center gap-2 overflow-hidden bg-tima-tickerNews px-3 text-xs"
      >
        <span className="shrink-0 font-bold text-tima-emph">[특징주]</span>
        {current ? (
          <span
            className={`flex min-w-0 items-center gap-2 transition-opacity duration-300 ${
              fade ? 'opacity-100' : 'opacity-0'
            }`}
          >
            <span className="shrink-0 font-mono text-tima-sub">{timeLabel(current.published_at)}</span>
            <span className="truncate text-tima-text">{current.title}</span>
          </span>
        ) : (
          <span className="text-tima-sub">특징주 뉴스 대기 중</span>
        )}
      </a>

      {/* ② 지수 바 (연보라) */}
      <div className="flex h-8 items-center gap-5 overflow-x-auto bg-tima-tickerIndex px-3 text-xs">
        {indices.length > 0 ? (
          indices.map((ix) => {
            const up = (ix.change_pct ?? 0) >= 0;
            return (
              <span key={ix.code} className="flex shrink-0 items-center gap-1.5">
                <span className="font-semibold text-tima-text">{ix.name}</span>
                <span className="font-mono text-tima-text">{fmtNum(ix.value)}</span>
                <span className={`font-mono ${up ? 'text-tima-up' : 'text-tima-down'}`}>
                  {up ? '▲' : '▼'} {fmtNum(Math.abs(ix.change))} ({up ? '+' : '-'}
                  {Math.abs(ix.change_pct ?? 0).toFixed(2)}%)
                </span>
              </span>
            );
          })
        ) : (
          <span className="text-tima-sub">지수 미연동</span>
        )}
      </div>
    </div>
  );
}
