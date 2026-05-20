'use client';

import { useEffect, useState } from 'react';
import { useTradingStore } from '@/lib/store';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

interface Order {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  type: 'MARKET' | 'LIMIT';
  quantity: number;
  price: number;
  status: 'PENDING' | 'FILLED' | 'CANCELED' | 'REJECTED';
  timestamp: string;
}

const STATUS_COLORS: Record<string, string> = {
  FILLED: 'bg-green-900 text-green-200',
  PENDING: 'bg-yellow-900 text-yellow-200',
  CANCELED: 'bg-red-900 text-red-200',
  REJECTED: 'bg-red-900 text-red-200',
};

const STATUS_KO: Record<string, string> = {
  FILLED: '체결',
  PENDING: '대기',
  CANCELED: '취소',
  REJECTED: '거부',
};

export function RecentOrders() {
  const [orders, setOrders] = useState<Order[]>([]);
  const orderRefreshSignal = useTradingStore((state) => state.orderRefreshSignal);

  useEffect(() => {
    async function fetchOrders() {
      try {
        const res = await fetch('/api/trading/orders?limit=20');
        if (res.ok) {
          const data = await res.json();
          setOrders(data.orders ?? []);
        }
      } catch {
        // silent — 백엔드 미연결 시 빈 상태 유지
      }
    }
    fetchOrders();
    const interval = setInterval(fetchOrders, 30_000);
    return () => clearInterval(interval);
  }, [orderRefreshSignal]);

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader>
        <CardTitle className="text-lg">최근 주문</CardTitle>
      </CardHeader>
      <CardContent>
        {orders.length === 0 ? (
          <p className="text-slate-400">최근 주문이 없습니다</p>
        ) : (
          <div className="space-y-2">
            {orders.slice(0, 5).map((order) => (
              <div
                key={order.id}
                className="flex items-center justify-between rounded-lg bg-slate-800 p-3 text-sm"
              >
                <div>
                  <p className="font-medium text-slate-50">{order.symbol}</p>
                  <p className="text-slate-400">
                    {order.side === 'BUY' ? '매수' : '매도'} {order.quantity}주
                    {order.type === 'MARKET'
                      ? ' @ 시장가'
                      : ` @ ${order.price.toLocaleString()}원`}
                  </p>
                </div>
                <Badge
                  variant="secondary"
                  className={STATUS_COLORS[order.status] ?? 'bg-slate-700 text-slate-200'}
                >
                  {STATUS_KO[order.status] ?? order.status}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
