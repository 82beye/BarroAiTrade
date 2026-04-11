'use client';

import { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

interface DailyReport {
  date: string;
  totalPnL: number;
  pnlRatio: number;
  maxDrawdown: number;
  tradeCount: number;
  winRate: number;
}

interface TradeRecord {
  id: string;
  timestamp: string;
  symbol: string;
  orderType: 'BUY' | 'SELL';
  quantity: number;
  entryPrice: number;
  exitPrice?: number;
  pnl?: number;
  pnlRatio?: number;
}

interface ChartDataPoint {
  date: string;
  pnlRatio: number;
}

// Mock data
const MOCK_REPORTS: DailyReport[] = [
  {
    date: '2026-04-08',
    totalPnL: 1250,
    pnlRatio: 2.15,
    maxDrawdown: -890,
    tradeCount: 12,
    winRate: 75,
  },
  {
    date: '2026-04-09',
    totalPnL: 850,
    pnlRatio: 1.45,
    maxDrawdown: -650,
    tradeCount: 8,
    winRate: 62.5,
  },
  {
    date: '2026-04-10',
    totalPnL: 2100,
    pnlRatio: 3.65,
    maxDrawdown: -1200,
    tradeCount: 15,
    winRate: 80,
  },
  {
    date: '2026-04-11',
    totalPnL: 1800,
    pnlRatio: 3.10,
    maxDrawdown: -950,
    tradeCount: 10,
    winRate: 70,
  },
];

const MOCK_TRADES: TradeRecord[] = [
  {
    id: '1',
    timestamp: '2026-04-11T14:05:00Z',
    symbol: 'AAPL',
    orderType: 'BUY',
    quantity: 10,
    entryPrice: 185.2,
    exitPrice: 186.5,
    pnl: 130,
    pnlRatio: 0.7,
  },
  {
    id: '2',
    timestamp: '2026-04-11T13:20:00Z',
    symbol: 'MSFT',
    orderType: 'BUY',
    quantity: 5,
    entryPrice: 420.1,
    exitPrice: 422.8,
    pnl: 135,
    pnlRatio: 0.64,
  },
  {
    id: '3',
    timestamp: '2026-04-11T12:45:00Z',
    symbol: 'GOOGL',
    orderType: 'SELL',
    quantity: 8,
    entryPrice: 142.9,
    exitPrice: 141.5,
    pnl: 112,
    pnlRatio: 0.98,
  },
  {
    id: '4',
    timestamp: '2026-04-11T11:30:00Z',
    symbol: 'NVDA',
    orderType: 'BUY',
    quantity: 3,
    entryPrice: 892.1,
    exitPrice: 885.5,
    pnl: -198,
    pnlRatio: -0.74,
  },
];

export default function ReportsPage() {
  const [selectedDate, setSelectedDate] = useState('2026-04-11');
  const [currentReport, setCurrentReport] = useState<DailyReport | null>(null);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [chartData, setChartData] = useState<ChartDataPoint[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [sortBy, setSortBy] = useState<'time' | 'pnl'>('time');

  // 초기 데이터 로드
  useEffect(() => {
    const loadReports = async () => {
      try {
        setIsLoading(true);
        // TODO: API 엔드포인트 (현재 Mock data)
        // const response = await fetch(`/api/reports?date=${selectedDate}`);
        // const data = await response.json();

        const report = MOCK_REPORTS.find((r) => r.date === selectedDate) || MOCK_REPORTS[MOCK_REPORTS.length - 1];
        setCurrentReport(report);

        // Mock trades
        setTrades(MOCK_TRADES);

        // Chart data
        setChartData(MOCK_REPORTS.map((r) => ({
          date: r.date.split('-')[2],
          pnlRatio: r.pnlRatio,
        })));
      } catch (error) {
        console.error('Failed to load reports:', error);
        setCurrentReport(MOCK_REPORTS[MOCK_REPORTS.length - 1]);
        setTrades(MOCK_TRADES);
        setChartData(MOCK_REPORTS.map((r) => ({
          date: r.date.split('-')[2],
          pnlRatio: r.pnlRatio,
        })));
      } finally {
        setIsLoading(false);
      }
    };

    loadReports();
  }, [selectedDate]);

  const sortedTrades = [...trades].sort((a, b) => {
    if (sortBy === 'time') {
      return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
    } else {
      return (b.pnlRatio || 0) - (a.pnlRatio || 0);
    }
  });

  return (
    <div className="flex-1 space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">리포트</h1>
        <p className="text-sm text-muted-foreground mt-1">
          일일 손익 분석 및 매매 내역 조회
        </p>
      </div>

      {/* Date Selector */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex gap-4 items-center">
            <label className="text-sm font-medium">날짜 선택:</label>
            <input
              type="date"
              value={selectedDate}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="px-3 py-2 border border-input rounded-md text-sm bg-slate-900 text-slate-50"
            />
            <select
              defaultValue="daily"
              className="px-3 py-2 border border-input rounded-md text-sm bg-slate-900 text-slate-50 w-32"
            >
              <option value="daily">일일</option>
              <option value="weekly">주간</option>
              <option value="monthly">월간</option>
            </select>
          </div>
        </CardContent>
      </Card>

      {/* Summary Cards */}
      {currentReport && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {/* Total PnL */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">일일 수익</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                ${currentReport.totalPnL > 0 ? '+' : ''}{currentReport.totalPnL}
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {currentReport.date} 기준
              </p>
            </CardContent>
          </Card>

          {/* PnL Ratio */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">수익률</CardTitle>
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${currentReport.pnlRatio > 0 ? 'text-green-600' : 'text-red-600'}`}>
                {currentReport.pnlRatio > 0 ? '+' : ''}{currentReport.pnlRatio.toFixed(2)}%
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {currentReport.tradeCount} 건 거래
              </p>
            </CardContent>
          </Card>

          {/* Max Drawdown */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">최대낙폭</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">
                ${currentReport.maxDrawdown}
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                승률: {currentReport.winRate}%
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* PnL Chart */}
      <Card>
        <CardHeader>
          <CardTitle>손익률 추이</CardTitle>
          <CardDescription>최근 4일간의 수익률 변화</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="w-full h-80">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis label={{ value: '수익률 (%)', angle: -90, position: 'insideLeft' }} />
                <Tooltip
                  formatter={(value) => `${typeof value === 'number' ? value.toFixed(2) : value}%`}
                  labelFormatter={(label) => `${label}일`}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="pnlRatio"
                  stroke="#6366f1"
                  name="수익률"
                  strokeWidth={2}
                  dot={{ fill: '#6366f1' }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* Trade History Table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>매매 내역</CardTitle>
              <CardDescription>
                {selectedDate} 거래 기록 ({sortedTrades.length}건)
              </CardDescription>
            </div>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as 'time' | 'pnl')}
              className="px-3 py-2 border border-input rounded-md text-sm bg-slate-900 text-slate-50 w-32"
            >
              <option value="time">시간순</option>
              <option value="pnl">수익순</option>
            </select>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="text-center py-8">로딩 중...</div>
          ) : sortedTrades.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              거래 기록이 없습니다.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-700">
                  <tr>
                    <th className="text-left px-4 py-3 font-semibold">시간</th>
                    <th className="text-left px-4 py-3 font-semibold">종목</th>
                    <th className="text-left px-4 py-3 font-semibold">주문타입</th>
                    <th className="text-right px-4 py-3 font-semibold">수량</th>
                    <th className="text-right px-4 py-3 font-semibold">진입가</th>
                    <th className="text-right px-4 py-3 font-semibold">청산가</th>
                    <th className="text-right px-4 py-3 font-semibold">수익금액</th>
                    <th className="text-right px-4 py-3 font-semibold">수익률</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedTrades.map((trade) => (
                    <tr key={trade.id} className="border-b border-slate-800 hover:bg-slate-800/30">
                      <td className="px-4 py-3 text-sm">
                        {new Date(trade.timestamp).toLocaleTimeString('ko-KR', {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </td>
                      <td className="px-4 py-3 font-semibold">{trade.symbol}</td>
                      <td className="px-4 py-3">
                        <Badge variant={trade.orderType === 'BUY' ? 'default' : 'secondary'}>
                          {trade.orderType === 'BUY' ? '매수' : '매도'}
                        </Badge>
                      </td>
                      <td className="text-right px-4 py-3">{trade.quantity}</td>
                      <td className="text-right px-4 py-3">${trade.entryPrice.toFixed(2)}</td>
                      <td className="text-right px-4 py-3">
                        ${trade.exitPrice?.toFixed(2) || '-'}
                      </td>
                      <td className={`text-right px-4 py-3 font-semibold ${(trade.pnl ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}`}>
                        ${trade.pnl ? (trade.pnl > 0 ? '+' : '') + trade.pnl.toFixed(0) : '-'}
                      </td>
                      <td className={`text-right px-4 py-3 font-semibold ${(trade.pnlRatio ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {trade.pnlRatio ? (trade.pnlRatio > 0 ? '+' : '') + trade.pnlRatio.toFixed(2) : '-'}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
