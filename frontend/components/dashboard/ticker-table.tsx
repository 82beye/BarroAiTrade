'use client';

import { useTradingStore, Ticker } from '@/lib/store';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export function TickerTable() {
  const tickers = useTradingStore((state) => Array.from(state.tickers.values()));

  if (tickers.length === 0) {
    return (
      <Card className="border-slate-800 bg-slate-900">
        <CardHeader>
          <CardTitle className="text-lg">실시간 시세</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-slate-400">실시간 데이터를 기다리는 중...</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader>
        <CardTitle className="text-lg">실시간 시세</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {tickers.slice(0, 5).map((ticker) => (
            <div
              key={ticker.symbol}
              className="flex items-center justify-between rounded-lg bg-slate-800 p-3"
            >
              <div>
                <p className="font-medium text-slate-50">{ticker.symbol}</p>
                <p className="text-sm text-slate-400">
                  ${ticker.price.toFixed(2)}
                </p>
              </div>
              <div
                className={`text-right ${
                  ticker.change >= 0 ? 'text-green-500' : 'text-red-500'
                }`}
              >
                <p className="font-medium">
                  {ticker.change >= 0 ? '+' : ''}
                  {ticker.change.toFixed(2)}%
                </p>
                <p className="text-sm">
                  Vol: {(ticker.volume / 1000000).toFixed(1)}M
                </p>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
