'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, type SearchResult } from '@/lib/api';

const DEBOUNCE_MS = 300;

/**
 * 티마 셸 통합검색 (PRD §3.4) — 라이트 스타일.
 * global-search 와 동일한 /api/search 로직(300ms 디바운스 + 종목/테마 자동완성)을
 * 재사용하되 민트그레이 배경의 모바일 앱 셸에 맞춘 밝은 톤으로 렌더.
 */
export function TimaSearch() {
  const router = useRouter();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    const term = q.trim();
    if (term.length === 0) {
      setResults([]);
      setSearched(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const res = await api.search(term, 10);
        const data = res.data;
        setResults(Array.isArray(data?.results) ? data.results : []);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
        setSearched(true);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  function go(r: SearchResult) {
    setOpen(false);
    setQ('');
    setResults([]);
    if (r.type === 'stock') router.push(`/stocks/${r.symbol}`);
    else router.push('/themes');
  }

  return (
    <div ref={wrapRef} className="relative flex-1">
      <div className="flex items-center gap-2 rounded-full border border-black/10 bg-white px-3 py-1.5">
        <svg className="h-4 w-4 shrink-0 text-tima-sub" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
        </svg>
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => setOpen(true)}
          placeholder="종목, 테마명을 입력하세요"
          className="w-full bg-transparent text-sm text-tima-text placeholder-tima-sub focus:outline-none"
        />
      </div>

      {open && q.trim() && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 overflow-hidden rounded-xl border border-tima-line bg-white shadow-lg">
          <div className="max-h-72 overflow-y-auto">
            {loading ? (
              <div className="px-3 py-3 text-xs text-tima-sub">검색 중…</div>
            ) : searched && results.length === 0 ? (
              <div className="px-3 py-3 text-xs text-tima-sub">검색 결과 없음</div>
            ) : (
              results.map((r) => (
                <button
                  key={r.type === 'stock' ? `s-${r.symbol}` : `t-${r.id}`}
                  onClick={() => go(r)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-tima-bg/60"
                >
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold text-white ${
                      r.type === 'stock' ? 'bg-tima-teal' : 'bg-tima-select'
                    }`}
                  >
                    {r.type === 'stock' ? '종목' : '테마'}
                  </span>
                  <span className="truncate text-sm text-tima-text">{r.name}</span>
                  {r.type === 'stock' && (
                    <span className="ml-auto shrink-0 font-mono text-xs text-tima-sub">{r.symbol}</span>
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
