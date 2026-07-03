'use client';

import { useEffect, useRef, useState } from 'react';
import { api, type NewsItem, type MarketIndex } from '@/lib/api';

const POLL_MS = 60_000; // 데이터 재조회
const ROLL_MS = 10_000; // 뉴스 롤링 교체

function timeLabel(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

export function NewsTicker() {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [indices, setIndices] = useState<MarketIndex[]>([]);
  const [idx, setIdx] = useState(0);
  const [fade, setFade] = useState(true);
  const rollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 데이터 폴링
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
        .catch(() => {
          if (!cancelled) setNews([]);
        });

      api
        .getMarketIndices()
        .then((res) => {
          if (cancelled) return;
          const data = res.data;
          if (data?.status === 'ok' && Array.isArray(data.items)) setIndices(data.items);
          else setIndices([]);
        })
        .catch(() => {
          if (!cancelled) setIndices([]);
        });
    };

    load();
    const poll = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(poll);
    };
  }, []);

  // 뉴스 롤링 (CSS 페이드 트랜지션)
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

  // 인덱스 안전 클램프
  useEffect(() => {
    if (idx >= news.length && news.length > 0) setIdx(0);
  }, [news.length, idx]);

  if (news.length === 0 && indices.length === 0) return null;

  const current = news[Math.min(idx, Math.max(news.length - 1, 0))];

  return (
    <div className="fixed inset-x-0 bottom-0 z-40">
      {/* ① 뉴스 1줄 */}
      {current && (
        <a
          href={current.url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex h-8 items-center gap-2 overflow-hidden border-t border-slate-700 bg-slate-800 px-4 text-xs hover:bg-slate-700"
        >
          <span className="shrink-0 rounded bg-tima-emph/90 px-1.5 py-0.5 font-semibold text-white">
            특징주
          </span>
          <span
            className={`flex min-w-0 items-center gap-2 transition-opacity duration-300 ${
              fade ? 'opacity-100' : 'opacity-0'
            }`}
          >
            <span className="shrink-0 font-mono text-slate-500">
              {timeLabel(current.published_at)}
            </span>
            <span className="truncate text-slate-200">{current.title}</span>
          </span>
        </a>
      )}

      {/* ② 지수 바 (status ok & items 있을 때만) */}
      {indices.length > 0 && (
        <div className="flex h-8 items-center gap-6 overflow-x-auto border-t border-slate-700 bg-slate-900 px-4 text-xs">
          {indices.map((ix) => {
            const up = (ix.change_pct ?? 0) >= 0;
            return (
              <span key={ix.code} className="flex shrink-0 items-center gap-1.5">
                <span className="text-slate-400">{ix.name}</span>
                <span className="font-mono text-slate-100">{fmtNum(ix.value)}</span>
                <span className={`font-mono ${up ? 'text-tima-up' : 'text-tima-down'}`}>
                  {up ? '▲' : '▼'} {fmtNum(Math.abs(ix.change))} (
                  {up ? '+' : '-'}
                  {Math.abs(ix.change_pct).toFixed(2)}%)
                </span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
