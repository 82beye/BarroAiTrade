'use client';

import { useEffect, useState } from 'react';
import { useTradingStore } from '@/lib/store';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

interface UniverseResponse {
  symbols: string[];
  count: number;
}

interface TickerMeta {
  symbol: string;
  name: string;
  price: number;
  change_pct: number;
  volume: number;
  high: number;
  low: number;
}

export function MarketTable() {
  const tickers = useTradingStore((state) => Array.from(state.tickers.values()));
  const [universe, setUniverse] = useState<string[]>([]);
  const [restMap, setRestMap] = useState<Record<string, TickerMeta>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadUniverse() {
      try {
        const res = await fetch('/api/market/universe');
        if (!res.ok) throw new Error(`${res.status}`);
        const data: UniverseResponse = await res.json();
        setUniverse(data.symbols);

        // 종목명 및 시세 일괄 조회 (최대 20개, 병렬)
        const slice = data.symbols.slice(0, 20);
        const results = await Promise.allSettled(
          slice.map((sym) =>
            fetch(`/api/market/ticker/${sym}`)
              .then((r) => (r.ok ? r.json() : null))
              .then((d): TickerMeta | null =>
                d
                  ? {
                      symbol: d.symbol,
                      name: d.name ?? d.symbol,
                      price: d.price ?? 0,
                      change_pct: d.change_pct ?? 0,
                      volume: d.volume ?? 0,
                      high: d.high ?? 0,
                      low: d.low ?? 0,
                    }
                  : null
              )
          )
        );
        const map: Record<string, TickerMeta> = {};
        results.forEach((r) => {
          if (r.status === 'fulfilled' && r.value) {
            map[r.value.symbol] = r.value;
          }
        });
        setRestMap(map);
      } catch (e) {
        setError(e instanceof Error ? e.message : '조회 실패');
      } finally {
        setLoading(false);
      }
    }
    loadUniverse();
  }, []);

  // universe 기반으로 rows 구성 (WS live data 우선, REST 폴백)
  const displaySymbols = universe.length > 0 ? universe.slice(0, 20) : [];
  const wsMap = new Map(tickers.map((t) => [t.symbol, t]));

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader>
        <CardTitle className="text-slate-200">마켓 현황</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-2">
            {[...Array(5)].map((_, i) => (
              <Skeleton key={i} className="h-10 w-full rounded" />
            ))}
          </div>
        ) : error ? (
          <div className="py-8 text-center text-sm text-slate-500">
            마켓 데이터를 불러올 수 없습니다 ({error})
          </div>
        ) : displaySymbols.length === 0 ? (
          <div className="py-8 text-center text-sm text-slate-500">
            모니터링 종목이 없습니다
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="pb-3 font-medium">종목</th>
                  <th className="pb-3 text-right font-medium">현재가</th>
                  <th className="pb-3 text-right font-medium">등락률</th>
                  <th className="pb-3 text-right font-medium">거래량</th>
                  <th className="pb-3 text-right font-medium">고가</th>
                  <th className="pb-3 text-right font-medium">저가</th>
                </tr>
              </thead>
              <tbody>
                {displaySymbols.map((symbol) => {
                  const live = wsMap.get(symbol);
                  const rest = restMap[symbol];
                  const price = live?.price ?? rest?.price;
                  const change = live?.change ?? rest?.change_pct;
                  const volume = live?.volume ?? rest?.volume;
                  const high = live?.high ?? rest?.high;
                  const low = live?.low ?? rest?.low;
                  const name = rest?.name ?? symbol;
                  const isRest = !live && rest;
                  return (
                    <tr
                      key={symbol}
                      className="border-b border-slate-800 last:border-0 hover:bg-slate-800 hover:bg-opacity-40"
                    >
                      <td className="py-3">
                        <div className="font-medium text-slate-200">{name}</div>
                        <div className="text-xs text-slate-500">{symbol}{isRest && <span className="ml-1 text-slate-600">(REST)</span>}</div>
                      </td>
                      <td className="py-3 text-right font-mono text-slate-200">
                        {price ? `${price.toLocaleString()}원` : '—'}
                      </td>
                      <td className={`py-3 text-right font-semibold ${change == null ? 'text-slate-500' : change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {change != null ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` : '—'}
                      </td>
                      <td className="py-3 text-right font-mono text-slate-400">
                        {volume ? `${(volume / 1000).toFixed(0)}천` : '—'}
                      </td>
                      <td className="py-3 text-right font-mono text-slate-400">
                        {high ? `${high.toLocaleString()}원` : '—'}
                      </td>
                      <td className="py-3 text-right font-mono text-slate-400">
                        {low ? `${low.toLocaleString()}원` : '—'}
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
  );
}
