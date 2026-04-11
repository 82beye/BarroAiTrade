'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import { useTradingStore } from '@/lib/store';

export default function TradingPage() {
  const { orders, addOrder } = useTradingStore();
  const [formData, setFormData] = useState({
    symbol: 'AAPL',
    side: 'BUY' as const,
    quantity: 1,
    price: 150,
  });
  const [loading, setLoading] = useState(false);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: name === 'quantity' || name === 'price' ? parseFloat(value) : value,
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const response = await api.placeOrder(formData);
      const order = response.data;

      addOrder({
        id: order.order_id,
        symbol: order.symbol,
        side: order.side,
        quantity: order.quantity,
        price: order.price,
        status: order.status,
        timestamp: new Date().toISOString(),
      });

      // 폼 초기화
      setFormData({
        symbol: 'AAPL',
        side: 'BUY',
        quantity: 1,
        price: 150,
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
    <div className="p-8">
      <h1 className="text-4xl font-bold mb-8">트레이딩</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* 주문 폼 */}
        <div className="lg:col-span-1">
          <div className="card">
            <h2 className="text-xl font-bold mb-6">신규 주문</h2>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-2">심볼</label>
                <input
                  type="text"
                  name="symbol"
                  value={formData.symbol}
                  onChange={handleInputChange}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded-lg text-white focus:border-primary outline-none"
                  placeholder="AAPL"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">방향</label>
                <select
                  name="side"
                  value={formData.side}
                  onChange={handleInputChange}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded-lg text-white focus:border-primary outline-none"
                >
                  <option value="BUY">매수 (BUY)</option>
                  <option value="SELL">매도 (SELL)</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">수량</label>
                <input
                  type="number"
                  name="quantity"
                  value={formData.quantity}
                  onChange={handleInputChange}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded-lg text-white focus:border-primary outline-none"
                  min="0.01"
                  step="0.01"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">가격</label>
                <input
                  type="number"
                  name="price"
                  value={formData.price}
                  onChange={handleInputChange}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded-lg text-white focus:border-primary outline-none"
                  min="0.01"
                  step="0.01"
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full button-primary disabled:opacity-50"
              >
                {loading ? '실행 중...' : '주문 실행'}
              </button>
            </form>
          </div>
        </div>

        {/* 주문 목록 */}
        <div className="lg:col-span-2">
          <div className="card">
            <h2 className="text-xl font-bold mb-6">최근 주문</h2>
            {orders.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>심볼</th>
                      <th>방향</th>
                      <th>수량</th>
                      <th>가격</th>
                      <th>상태</th>
                      <th>시간</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((order) => (
                      <tr key={order.id}>
                        <td className="font-medium">{order.symbol}</td>
                        <td className={order.side === 'BUY' ? 'text-success' : 'text-danger'}>
                          {order.side === 'BUY' ? '매수' : '매도'}
                        </td>
                        <td>{order.quantity}</td>
                        <td>${order.price}</td>
                        <td>
                          <span className={`px-2 py-1 rounded text-xs font-medium ${
                            order.status === 'FILLED'
                              ? 'bg-green-900 text-green-200'
                              : order.status === 'PENDING'
                              ? 'bg-yellow-900 text-yellow-200'
                              : 'bg-red-900 text-red-200'
                          }`}>
                            {order.status}
                          </span>
                        </td>
                        <td className="text-gray-400 text-sm">
                          {new Date(order.timestamp).toLocaleTimeString('ko-KR')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-gray-400">최근 주문 없음</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
