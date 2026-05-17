'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import { useTradingStore } from '@/lib/store';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';

export function OrderForm() {
  const { addOrder, tickers } = useTradingStore();
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [formData, setFormData] = useState({
    symbol: '005930',
    side: 'BUY',
    type: 'LIMIT',
    quantity: 1,
    price: 70000,
  });

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>
  ) => {
    const { name, value } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]:
        name === 'quantity' ? parseInt(value, 10) || 1
        : name === 'price' ? parseFloat(value)
        : value,
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const response = await api.placeOrder({
        symbol: formData.symbol,
        side: formData.side.toLowerCase(),
        order_type: formData.type.toLowerCase(),
        quantity: formData.quantity,
        price: formData.type === 'LIMIT' ? formData.price : undefined,
        strategy_id: 'manual',
      });
      const order = response.data;

      addOrder({
        id: order.order_id || Date.now().toString(),
        symbol: order.symbol,
        side: (order.side?.toUpperCase() ?? formData.side) as 'BUY' | 'SELL',
        type: formData.type as 'MARKET' | 'LIMIT',
        quantity: order.filled_quantity ?? formData.quantity,
        price: order.avg_price ?? formData.price,
        status: order.status === 'pending' ? 'PENDING'
               : order.status === 'filled' ? 'FILLED'
               : order.status === 'cancelled' ? 'CANCELED'
               : 'PENDING',
        timestamp: order.timestamp ?? new Date().toISOString(),
      });

      setStatus({ type: 'success', message: `${formData.symbol} 주문이 실행되었습니다.` });
      setTimeout(() => setStatus(null), 4000);
    } catch (error) {
      setStatus({ type: 'error', message: error instanceof Error ? error.message : '주문 실패' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader>
        <CardTitle>신규 주문</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-2 block text-sm font-medium text-slate-300">
              심볼
            </label>
            <Input
              name="symbol"
              value={formData.symbol}
              onChange={handleChange}
              className="border-slate-700 bg-slate-800 text-slate-50"
              placeholder="종목코드 (예: 005930)"
            />
            {(() => {
              const live = tickers.get(formData.symbol.toUpperCase());
              if (!live) return null;
              return (
                <p className="mt-1 text-xs text-slate-400">
                  현재가{' '}
                  <span className="font-semibold text-slate-200">
                    {live.price.toLocaleString()}원
                  </span>
                  <span className={`ml-2 ${live.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {live.change >= 0 ? '+' : ''}{live.change.toFixed(2)}%
                  </span>
                </p>
              );
            })()}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-300">
                방향
              </label>
              <Select
                name="side"
                value={formData.side}
                onChange={handleChange}
                className="border-slate-700 bg-slate-800 text-slate-50"
              >
                <option value="BUY">매수 (BUY)</option>
                <option value="SELL">매도 (SELL)</option>
              </Select>
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-slate-300">
                유형
              </label>
              <Select
                name="type"
                value={formData.type}
                onChange={handleChange}
                className="border-slate-700 bg-slate-800 text-slate-50"
              >
                <option value="MARKET">시장가 (MARKET)</option>
                <option value="LIMIT">지정가 (LIMIT)</option>
              </Select>
            </div>
          </div>

          <div>
            <label className="mb-2 block text-sm font-medium text-slate-300">
              수량
            </label>
            <Input
              type="number"
              name="quantity"
              value={formData.quantity}
              onChange={handleChange}
              className="border-slate-700 bg-slate-800 text-slate-50"
              min="1"
              step="1"
            />
          </div>

          {formData.type === 'LIMIT' && (
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-300">
                가격 (원)
              </label>
              <Input
                type="number"
                name="price"
                value={formData.price}
                onChange={handleChange}
                className="border-slate-700 bg-slate-800 text-slate-50"
                min="1"
                step="1"
              />
            </div>
          )}

          {status && (
            <div className={`rounded-lg border px-3 py-2 text-sm ${
              status.type === 'success'
                ? 'border-green-700 bg-green-900 bg-opacity-30 text-green-300'
                : 'border-red-700 bg-red-900 bg-opacity-30 text-red-300'
            }`}>
              {status.message}
            </div>
          )}

          <Button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? '실행 중...' : '주문 실행'}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
