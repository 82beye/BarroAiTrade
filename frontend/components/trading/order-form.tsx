'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import { useTradingStore } from '@/lib/store';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';

export function OrderForm() {
  const { addOrder } = useTradingStore();
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({
    symbol: 'AAPL',
    side: 'BUY',
    type: 'LIMIT',
    quantity: 1,
    price: 150,
  });

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>
  ) => {
    const { name, value } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]:
        name === 'quantity' || name === 'price' ? parseFloat(value) : value,
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const response = await api.placeOrder(formData);
      const order = response.data;

      addOrder({
        id: order.order_id || Date.now().toString(),
        symbol: order.symbol,
        side: order.side,
        type: order.type,
        quantity: order.quantity,
        price: order.price,
        status: order.status || 'PENDING',
        timestamp: new Date().toISOString(),
      });

      alert('주문이 실행되었습니다');
    } catch (error) {
      console.error('주문 실패:', error);
      alert('주문 실패');
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
              placeholder="AAPL"
            />
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
              min="0.01"
              step="0.01"
            />
          </div>

          <div>
            <label className="mb-2 block text-sm font-medium text-slate-300">
              가격
            </label>
            <Input
              type="number"
              name="price"
              value={formData.price}
              onChange={handleChange}
              className="border-slate-700 bg-slate-800 text-slate-50"
              min="0.01"
              step="0.01"
            />
          </div>

          <Button
            type="submit"
            disabled={loading}
            className="w-full button-primary"
          >
            {loading ? '실행 중...' : '주문 실행'}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
