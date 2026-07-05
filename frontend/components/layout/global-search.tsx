'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, type SearchResult } from '@/lib/api';

const DEBOUNCE_MS = 300;

/**
 * 통합검색 (PRD §3.4) — 종목·테마 단일 입력.
 * 사이드바 상단에 배치. 300ms 디바운스로 /api/search 호출, 자동완성 드롭다운.
 * collapsed: 아이콘 버튼 → 오른쪽 오버레이 패널 / expanded: 인라인 입력 + 하단 드롭다운.
 */
export function GlobalSearch({ collapsed = false }: { collapsed?: boolean }) {
  const router = useRouter();
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  // 디바운스 검색
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

  // 외부 클릭 / ESC 닫기
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

  // 패널 열릴 때 포커스
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  function go(r: SearchResult) {
    setOpen(false);
    setQ('');
    setResults([]);
    if (r.type === 'stock') router.push(`/stocks/${r.symbol}`);
    else router.push('/themes');
  }

  const list = (
    <div className="max-h-72 overflow-y-auto">
      {loading && q.trim() ? (
        <div className="px-3 py-3 text-xs text-slate-500">검색 중…</div>
      ) : searched && results.length === 0 ? (
        <div className="px-3 py-3 text-xs text-slate-500">검색 결과 없음</div>
      ) : (
        results.map((r) => (
          <button
            key={r.type === 'stock' ? `s-${r.symbol}` : `t-${r.id}`}
            onClick={() => go(r)}
            className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-slate-700/60"
          >
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                r.type === 'stock'
                  ? 'bg-tima-teal/20 text-tima-teal'
                  : 'bg-tima-select/20 text-tima-select'
              }`}
            >
              {r.type === 'stock' ? '종목' : '테마'}
            </span>
            <span className="truncate text-sm text-slate-200">{r.name}</span>
            {r.type === 'stock' && (
              <span className="ml-auto shrink-0 font-mono text-xs text-slate-500">
                {r.symbol}
              </span>
            )}
          </button>
        ))
      )}
    </div>
  );

  const input = (
    <input
      ref={inputRef}
      type="text"
      value={q}
      onChange={(e) => setQ(e.target.value)}
      onFocus={() => setOpen(true)}
      placeholder="종목, 테마명을 입력하세요"
      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:border-tima-teal focus:outline-none"
    />
  );

  // ── collapsed: 아이콘 버튼 + 오버레이 패널 ──
  if (collapsed) {
    return (
      <div ref={wrapRef} className="relative">
        <button
          onClick={() => setOpen((v) => !v)}
          title="통합검색"
          className="flex w-full items-center justify-center rounded-lg py-2.5 text-lg text-slate-400 hover:bg-slate-800 hover:text-slate-200"
        >
          🔍
        </button>
        {open && (
          <div className="absolute left-full top-0 z-50 ml-2 w-72 rounded-lg border border-slate-700 bg-slate-800 p-2 shadow-xl">
            {input}
            <div className="mt-1">{list}</div>
          </div>
        )}
      </div>
    );
  }

  // ── expanded: 인라인 입력 + 하단 드롭다운 ──
  return (
    <div ref={wrapRef} className="relative">
      {input}
      {open && q.trim() && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 rounded-lg border border-slate-700 bg-slate-800 shadow-xl">
          {list}
        </div>
      )}
    </div>
  );
}
