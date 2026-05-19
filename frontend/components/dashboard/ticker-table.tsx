'use client';

import { useEffect, useState } from 'react';
import { useTradingStore } from '@/lib/store';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

interface RestTicker {
  symbol: string;
  name?: string;
  price: number;
  volume: number;
  change_pct: number;
}

const TOP_SYMBOLS = ['005930', '000660', '035720', '035420', '051910'];

export function TickerTable() {
  const wsTickers = useTradingStore((state) => Array.from(state.tickers.values()));
  const [restTickers, setRestTickers] = useState<RestTicker[]>([]);
  const [loading, setLoading] = useState(true);

  // WS 데이터 없을 때 REST 폴백
  useEffect(() => {
    if (wsTickers.length > 0) {
      setLoading(false);
      return;
    }

    async function fetchRest() {
      try {
        const results = await Promise.allSettled(
          TOP_SYMBOLS.map((sym) =>
            fetch(`/api/market/ticker/${sym}`).then((r) => (r.ok ? r.json() : null))
          )
        );
        const data = results
          .filter((r): r is PromiseFulfilledResult<RestTicker> => r.status === 'fulfilled' && r.value !== null)
          .map((r) => r.value);
        setRestTickers(data);
      } catch {
        // 폴백도 실패 시 조용히 무시
      } finally {
        setLoading(false);
      }
    }

    fetchRest();
  }, [wsTickers.length]);

  const displayTickers = wsTickers.length > 0
    ? wsTickers.slice(0, 5).map((t) => ({
        symbol: t.symbol,
        name: undefined as string | undefined,
        price: t.price ?? 0,
        volume: t.volume ?? 0,
        change: t.change ?? 0,
      }))
    : restTickers.map((t) => ({
        symbol: t.symbol,
        name: t.name,
        price: t.price ?? 0,
        volume: t.volume ?? 0,
        change: t.change_pct ?? 0,
      }));

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-lg">실시간 시세</CardTitle>
        {wsTickers.length === 0 && !loading && (
          <span className="text-xs text-slate-500">REST 기준</span>
        )}
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-2">
            {[...Array(5)].map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-lg" />
            ))}
          </div>
        ) : displayTickers.length === 0 ? (
          <p className="text-slate-400">시세 데이터를 불러올 수 없습니다</p>
        ) : (
          <div className="space-y-2">
            {displayTickers.map((ticker) => (
              <div
                key={ticker.symbol}
                className="flex items-center justify-between rounded-lg bg-slate-800 p-3"
              >
                <div>
                  <p className="font-medium text-slate-50">{ticker.name ?? ticker.symbol}</p>
                  {ticker.name && (
                    <p className="text-xs text-slate-500">{ticker.symbol}</p>
                  )}
                  <p className="text-sm text-slate-400">
                    {ticker.price.toLocaleString()}원
                  </p>
                </div>
                <div className={`text-right ${ticker.change >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                  <p className="font-medium">
                    {ticker.change >= 0 ? '+' : ''}{ticker.change.toFixed(2)}%
                  </p>
                  <p className="text-sm text-slate-400">
                    {(ticker.volume / 1000).toFixed(0)}천주
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
