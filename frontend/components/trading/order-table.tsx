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

export function OrderTable() {
  const [orders, setOrders] = useState<Order[]>([]);
  const orderRefreshSignal = useTradingStore((state) => state.orderRefreshSignal);

  useEffect(() => {
    async function fetchOrders() {
      try {
        const res = await fetch('/api/trading/orders?limit=50');
        if (res.ok) {
          const data = await res.json();
          setOrders(data.orders ?? []);
        }
      } catch {
        // silent
      }
    }
    fetchOrders();
    const interval = setInterval(fetchOrders, 30_000);
    return () => clearInterval(interval);
  }, [orderRefreshSignal]);

  return (
    <Card className="border-slate-800 bg-slate-900">
      <CardHeader>
        <CardTitle>주문 내역</CardTitle>
      </CardHeader>
      <CardContent>
        {orders.length === 0 ? (
          <p className="text-slate-400">주문 내역이 없습니다</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 text-left text-slate-400">
                  <th className="pb-3 font-medium">시간</th>
                  <th className="pb-3 font-medium">심볼</th>
                  <th className="pb-3 font-medium">방향</th>
                  <th className="pb-3 font-medium">유형</th>
                  <th className="pb-3 text-right font-medium">수량</th>
                  <th className="pb-3 text-right font-medium">가격</th>
                  <th className="pb-3 font-medium">상태</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.id} className="border-b border-slate-800 last:border-0 hover:bg-slate-800 hover:bg-opacity-40">
                    <td className="py-3 font-mono text-xs text-slate-400">
                      {new Date(order.timestamp).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}
                    </td>
                    <td className="py-3 font-semibold text-slate-200">{order.symbol}</td>
                    <td className="py-3">
                      <span className={`font-semibold ${order.side === 'BUY' ? 'text-blue-400' : 'text-orange-400'}`}>
                        {order.side === 'BUY' ? '매수' : '매도'}
                      </span>
                    </td>
                    <td className="py-3 text-slate-400">{order.type === 'LIMIT' ? '지정가' : '시장가'}</td>
                    <td className="py-3 text-right font-mono text-slate-300">{order.quantity}주</td>
                    <td className="py-3 text-right font-mono text-slate-300">
                      {order.type === 'MARKET' ? (
                        <span className="text-slate-500">시장가</span>
                      ) : (
                        `${order.price.toLocaleString()}원`
                      )}
                    </td>
                    <td className="py-3">
                      <Badge
                        variant="secondary"
                        className={STATUS_COLORS[order.status] ?? 'bg-slate-700 text-slate-200'}
                      >
                        {STATUS_KO[order.status] ?? order.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
