'use client';

import { OrderForm } from '@/components/trading/order-form';
import { OrderTable } from '@/components/trading/order-table';

export default function TradingPage() {
  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">트레이딩</h1>
        <p className="mt-2 text-slate-400">주문을 실행하고 관리합니다</p>
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        {/* 주문 폼 */}
        <div className="lg:col-span-1">
          <OrderForm />
        </div>

        {/* 주문 목록 */}
        <div className="lg:col-span-2">
          <OrderTable />
        </div>
      </div>
    </div>
  );
}
