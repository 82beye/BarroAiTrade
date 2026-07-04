'use client';

import { useEffect } from 'react';
import { useWatchlistStore } from '@/lib/watchlist';

/**
 * 즐겨찾기 ★ 토글 버튼 (PRD §3.4)
 * 등록: 노란 ★ / 미등록: 회색 ☆. 낙관적 업데이트+실패 롤백은 스토어가 처리.
 */
export function WatchlistStar({
  symbol,
  size = 'md',
  className = '',
}: {
  symbol: string;
  size?: 'sm' | 'md';
  className?: string;
}) {
  const sym = (symbol ?? '').toUpperCase();
  const ensureLoaded = useWatchlistStore((s) => s.ensureLoaded);
  const toggle = useWatchlistStore((s) => s.toggle);
  const watched = useWatchlistStore((s) => s.symbols.has(sym));
  const pending = useWatchlistStore((s) => s.pending.has(sym));

  useEffect(() => {
    ensureLoaded();
  }, [ensureLoaded]);

  const dim = size === 'sm' ? 'text-base' : 'text-2xl';

  return (
    <button
      type="button"
      aria-label={watched ? '관심종목 해제' : '관심종목 등록'}
      aria-pressed={watched}
      title={watched ? '관심종목 해제' : '관심종목 등록'}
      disabled={pending || !sym}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        toggle(sym);
      }}
      className={`leading-none transition-colors disabled:opacity-50 ${dim} ${
        watched ? 'text-tima-active' : 'text-slate-500 hover:text-slate-300'
      } ${className}`}
    >
      {watched ? '★' : '☆'}
    </button>
  );
}
