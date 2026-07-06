/**
 * 감시(관심) 종목 공유 스토어 — 즐겨찾기 ★ 토글 (PRD §3.4, FR-T-07)
 *
 * 백엔드 계약 (backend/api/routes/watchlist.py):
 *   GET    /api/watchlist          → { symbols: string[], count }
 *   POST   /api/watchlist  {symbol} → { symbol, added, count }
 *   DELETE /api/watchlist/:symbol   → { symbol, removed, count }
 *
 * 여러 화면(종목 상세 헤더·스크리너 행)이 한 상태를 공유하도록 전역 스토어로 관리.
 * 토글은 낙관적 업데이트 후 실패 시 롤백한다.
 */
import { create } from 'zustand';

interface WatchlistState {
  symbols: Set<string>;
  loaded: boolean;
  loading: boolean;
  pending: Set<string>; // 요청 진행 중 심볼(버튼 비활성)
  ensureLoaded: () => Promise<void>;
  isWatched: (symbol: string) => boolean;
  toggle: (symbol: string) => Promise<boolean>; // 성공 여부 반환
}

export const useWatchlistStore = create<WatchlistState>((set, get) => ({
  symbols: new Set<string>(),
  loaded: false,
  loading: false,
  pending: new Set<string>(),

  ensureLoaded: async () => {
    if (get().loaded || get().loading) return;
    set({ loading: true });
    try {
      const res = await fetch('/api/watchlist');
      if (res.ok) {
        const data: { symbols?: string[] } = await res.json();
        set({ symbols: new Set((data.symbols ?? []).map((s) => s.toUpperCase())), loaded: true });
      }
    } catch {
      /* 미로드 유지 — 이후 호출에서 재시도 가능 */
    } finally {
      set({ loading: false });
    }
  },

  isWatched: (symbol) => get().symbols.has(symbol.toUpperCase()),

  toggle: async (symbolRaw) => {
    const symbol = symbolRaw.toUpperCase();
    if (!symbol || get().pending.has(symbol)) return false;
    const currently = get().symbols.has(symbol);

    // 낙관적 업데이트
    set((s) => {
      const next = new Set(s.symbols);
      if (currently) next.delete(symbol);
      else next.add(symbol);
      const pending = new Set(s.pending);
      pending.add(symbol);
      return { symbols: next, pending };
    });

    try {
      const res = currently
        ? await fetch(`/api/watchlist/${symbol}`, { method: 'DELETE' })
        : await fetch('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol }),
          });
      if (!res.ok) throw new Error(String(res.status));
      return true;
    } catch {
      // 실패 롤백
      set((s) => {
        const next = new Set(s.symbols);
        if (currently) next.add(symbol);
        else next.delete(symbol);
        return { symbols: next };
      });
      return false;
    } finally {
      set((s) => {
        const pending = new Set(s.pending);
        pending.delete(symbol);
        return { pending };
      });
    }
  },
}));
