'use client';

import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useTradingStore } from '@/lib/store';

interface Signal {
  symbol: string;
  name?: string;
  signal_type: string;
  score: number;
  timestamp: string;
}

interface ScanResult {
  signals: Signal[];
}

interface TickerMeta {
  symbol: string;
  name: string;
  price: number;
  change_pct: number;
}

const SIGNAL_BADGE: Record<string, { label: string; className: string }> = {
  blue_line: { label: '블루라인', className: 'bg-blue-600 text-white' },
  f_zone: { label: 'F존', className: 'bg-purple-600 text-white' },
  buy: { label: '매수', className: 'bg-green-600 text-white' },
  sell: { label: '매도', className: 'bg-red-600 text-white' },
};

export default function WatchlistPage() {
  const tickers = useTradingStore((state) => state.tickers);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [tickermap, setTickermap] = useState<Record<string, TickerMeta>>({});
  const [signalmap, setSignalmap] = useState<Record<string, Signal>>({});
  const [loading, setLoading] = useState(true);
  const [addInput, setAddInput] = useState('');
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWatchlist = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/watchlist');
      if (!res.ok) throw new Error(`${res.status}`);
      const data: { symbols: string[] } = await res.json();
      setSymbols(data.symbols);

      if (data.symbols.length === 0) return;

      // 종목 시세(이름·가격·등락률) + 신호 병렬 조회
      const [tickerResults, scanRes] = await Promise.all([
        Promise.allSettled(
          data.symbols.map((sym) =>
            fetch(`/api/market/ticker/${sym}`)
              .then((r) => (r.ok ? r.json() : null))
              .then((d): TickerMeta | null =>
                d
                  ? { symbol: d.symbol, name: d.name ?? d.symbol, price: d.price ?? 0, change_pct: d.change_pct ?? 0 }
                  : null
              )
          )
        ),
        fetch(`/api/signals/scan?symbols=${encodeURIComponent(data.symbols.join(','))}&market_type=stock`)
          .then((r) => (r.ok ? r.json() : { signals: [] }))
          .catch((): ScanResult => ({ signals: [] })),
      ]);

      const tm: Record<string, TickerMeta> = {};
      tickerResults.forEach((r) => {
        if (r.status === 'fulfilled' && r.value) tm[r.value.symbol] = r.value;
      });
      setTickermap(tm);

      const sm: Record<string, Signal> = {};
      (scanRes as ScanResult).signals.forEach((sig) => { sm[sig.symbol] = sig; });
      setSignalmap(sm);
    } catch (e) {
      setError(e instanceof Error ? e.message : '조회 실패');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchWatchlist(); }, [fetchWatchlist]);

  async function handleAdd() {
    const sym = addInput.trim().toUpperCase();
    if (!sym) return;
    setAdding(true);
    try {
      const res = await fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      setAddInput('');
      await fetchWatchlist();
    } catch (e) {
      setError(e instanceof Error ? e.message : '추가 실패');
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(symbol: string) {
    try {
      await fetch(`/api/watchlist/${symbol}`, { method: 'DELETE' });
      await fetchWatchlist();
    } catch {
      setError('제거 실패');
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">감시 종목</h1>
        <p className="mt-2 text-slate-400">실시간 모니터링 중인 KOSPI/KOSDAQ 종목 목록</p>
      </div>

      {/* 종목 추가 */}
      <Card className="mb-6 border-slate-700 bg-slate-800">
        <CardContent className="pt-4">
          <div className="flex gap-3">
            <Input
              value={addInput}
              onChange={(e) => setAddInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
              placeholder="종목코드 입력 (예: 005930)"
              className="max-w-xs border-slate-600 bg-slate-700 text-slate-200 placeholder-slate-500"
            />
            <Button
              onClick={handleAdd}
              disabled={adding || !addInput.trim()}
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50"
            >
              {adding ? '추가 중...' : '추가'}
            </Button>
            <Button
              variant="outline"
              onClick={fetchWatchlist}
              disabled={loading}
              className="border-slate-600 text-slate-300 hover:bg-slate-700"
            >
              새로고침
            </Button>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="mb-4 rounded-lg border border-red-700 bg-red-900 bg-opacity-30 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* 종목 테이블 */}
      <Card className="border-slate-700 bg-slate-800">
        <CardHeader>
          <CardTitle className="text-slate-200">
            종목 목록 <span className="ml-2 text-sm font-normal text-slate-400">({symbols.length}개)</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-2">
              {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-12 w-full rounded" />)}
            </div>
          ) : symbols.length === 0 ? (
            <div className="py-12 text-center text-slate-500">
              감시 종목이 없습니다. 위에서 종목코드를 추가하세요.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-left text-slate-400">
                    <th className="pb-3 font-medium">종목</th>
                    <th className="pb-3 text-right font-medium">현재가</th>
                    <th className="pb-3 text-right font-medium">등락률</th>
                    <th className="pb-3 text-center font-medium">신호</th>
                    <th className="pb-3 text-right font-medium">점수</th>
                    <th className="pb-3 text-right font-medium"></th>
                  </tr>
                </thead>
                <tbody>
                  {symbols.map((sym) => {
                    const live = tickers.get(sym);
                    const rest = tickermap[sym];
                    const name = rest?.name ?? sym;
                    const price = live?.price ?? rest?.price;
                    const change = live?.change ?? rest?.change_pct;
                    const sig = signalmap[sym];
                    const badge = sig ? SIGNAL_BADGE[sig.signal_type] : null;
                    return (
                      <tr key={sym} className="border-b border-slate-700 last:border-0 hover:bg-slate-700 hover:bg-opacity-30">
                        <td className="py-3">
                          <div className="font-medium text-slate-200">{name}</div>
                          <div className="text-xs text-slate-500">{sym}</div>
                        </td>
                        <td className="py-3 text-right font-mono text-slate-200">
                          {price ? `${price.toLocaleString()}원` : '—'}
                        </td>
                        <td className={`py-3 text-right font-semibold ${change == null ? 'text-slate-500' : change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {change != null ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` : '—'}
                        </td>
                        <td className="py-3 text-center">
                          {badge ? (
                            <span className={`inline-flex rounded px-2 py-0.5 text-xs font-semibold ${badge.className}`}>
                              {badge.label}
                            </span>
                          ) : (
                            <span className="text-xs text-slate-600">—</span>
                          )}
                        </td>
                        <td className="py-3 text-right">
                          {sig ? (
                            <span className={`font-semibold ${sig.score >= 7 ? 'text-green-400' : 'text-yellow-400'}`}>
                              {sig.score.toFixed(1)}
                            </span>
                          ) : (
                            <span className="text-slate-600">—</span>
                          )}
                        </td>
                        <td className="py-3 text-right">
                          <button
                            onClick={() => handleRemove(sym)}
                            className="text-xs text-slate-500 hover:text-red-400"
                          >
                            제거
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
