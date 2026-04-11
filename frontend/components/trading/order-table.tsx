'use client';

import { useTradingStore } from '@/lib/store';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

export function OrderTable() {
  const orders = useTradingStore((state) => state.orders);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'FILLED':
        return 'bg-green-900 text-green-200';
      case 'PENDING':
        return 'bg-yellow-900 text-yellow-200';
      case 'CANCELED':
      case 'REJECTED':
        return 'bg-red-900 text-red-200';
      default:
        return 'bg-slate-700 text-slate-200';
    }
  };

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
            <table className="table">
              <thead>
                <tr>
                  <th>심볼</th>
                  <th>방향</th>
                  <th>유형</th>
                  <th>수량</th>
                  <th>가격</th>
                  <th>상태</th>
                  <th>시간</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.id}>
                    <td className="font-medium text-slate-50">{order.symbol}</td>
                    <td
                      className={
                        order.side === 'BUY'
                          ? 'text-green-500'
                          : 'text-red-500'
                      }
                    >
                      {order.side === 'BUY' ? '매수' : '매도'}
                    </td>
                    <td className="text-slate-300">{order.type}</td>
                    <td className="text-slate-300">{order.quantity}</td>
                    <td className="text-slate-300">${order.price.toFixed(2)}</td>
                    <td>
                      <Badge
                        variant="secondary"
                        className={getStatusColor(order.status)}
                      >
                        {order.status}
                      </Badge>
                    </td>
                    <td className="text-sm text-slate-400">
                      {new Date(order.timestamp).toLocaleTimeString('ko-KR')}
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
