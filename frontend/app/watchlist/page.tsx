'use client';

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

interface WatchlistItem {
  code: string;
  name: string;
  price: number;
  blueLineDot: number;
  watermelon: boolean;
  score: number;
  updatedAt: string;
}

type FilterType = 'all' | 'blueline' | 'watermelon';

// Mock data
const MOCK_WATCHLIST: WatchlistItem[] = [
  {
    code: 'AAPL',
    name: 'Apple Inc.',
    price: 185.42,
    blueLineDot: 92,
    watermelon: true,
    score: 95,
    updatedAt: '2026-04-11T15:30:00Z',
  },
  {
    code: 'MSFT',
    name: 'Microsoft Corp.',
    price: 420.15,
    blueLineDot: 88,
    watermelon: false,
    score: 87,
    updatedAt: '2026-04-11T15:29:45Z',
  },
  {
    code: 'GOOGL',
    name: 'Alphabet Inc.',
    price: 142.65,
    blueLineDot: 75,
    watermelon: true,
    score: 82,
    updatedAt: '2026-04-11T15:28:30Z',
  },
  {
    code: 'AMZN',
    name: 'Amazon.com Inc.',
    price: 186.78,
    blueLineDot: 65,
    watermelon: false,
    score: 71,
    updatedAt: '2026-04-11T15:27:15Z',
  },
  {
    code: 'NVDA',
    name: 'NVIDIA Corp.',
    price: 892.30,
    blueLineDot: 95,
    watermelon: true,
    score: 98,
    updatedAt: '2026-04-11T15:30:00Z',
  },
];

export default function WatchlistPage() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [filter, setFilter] = useState<FilterType>('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [isLoading, setIsLoading] = useState(true);

  // 초기 데이터 로드
  useEffect(() => {
    const loadWatchlist = async () => {
      try {
        // TODO: API 엔드포인트 (현재 Mock data)
        // const response = await fetch('/api/watchlist');
        // const data = await response.json();
        setWatchlist(MOCK_WATCHLIST);
      } catch (error) {
        console.error('Failed to load watchlist:', error);
        setWatchlist(MOCK_WATCHLIST); // Mock fallback
      } finally {
        setIsLoading(false);
      }
    };

    loadWatchlist();
  }, []);

  // 필터링된 데이터
  const filteredWatchlist = watchlist.filter((item) => {
    // 필터 적용
    if (filter === 'blueline' && item.blueLineDot < 80) return false;
    if (filter === 'watermelon' && !item.watermelon) return false;

    // 검색어 적용
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      return item.code.toLowerCase().includes(term) || item.name.toLowerCase().includes(term);
    }

    return true;
  });

  const handleRefresh = () => {
    setIsLoading(true);
    setTimeout(() => {
      setWatchlist(MOCK_WATCHLIST);
      setIsLoading(false);
    }, 500);
  };

  return (
    <div className="flex-1 space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">감시 종목</h1>
          <p className="text-sm text-muted-foreground mt-1">
            파란점선 신호 및 수박 신호로 추적 중인 종목 목록
          </p>
        </div>
        <Button onClick={handleRefresh} disabled={isLoading}>
          {isLoading ? '로딩 중...' : '새로고침'}
        </Button>
      </div>

      {/* Filter Bar */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex gap-4 items-center flex-wrap">
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as FilterType)}
              className="px-3 py-2 border border-input rounded-md text-sm bg-slate-900 text-slate-50 w-40"
            >
              <option value="all">전체 보기</option>
              <option value="blueline">파란점선 근접</option>
              <option value="watermelon">수박신호</option>
            </select>

            <Input
              placeholder="종목코드 또는 종목명 검색..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-80"
            />

            <div className="text-sm text-slate-400">
              결과: <strong>{filteredWatchlist.length}</strong>개
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Watchlist Table */}
      <Card>
        <CardHeader>
          <CardTitle>종목 목록</CardTitle>
          <CardDescription>실시간 업데이트 (WebSocket 연동 예정)</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="text-center py-8">로딩 중...</div>
          ) : filteredWatchlist.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              해당하는 종목이 없습니다.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-700">
                  <tr>
                    <th className="text-left px-4 py-3 font-semibold">종목코드</th>
                    <th className="text-left px-4 py-3 font-semibold">종목명</th>
                    <th className="text-right px-4 py-3 font-semibold">현재가</th>
                    <th className="text-center px-4 py-3 font-semibold">파란점선</th>
                    <th className="text-center px-4 py-3 font-semibold">수박신호</th>
                    <th className="text-right px-4 py-3 font-semibold">점수</th>
                    <th className="text-right px-4 py-3 font-semibold text-xs">마지막 업데이트</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredWatchlist.map((item) => (
                    <tr key={item.code} className="border-b border-slate-800 hover:bg-slate-800/30">
                      <td className="px-4 py-3 font-semibold">{item.code}</td>
                      <td className="px-4 py-3">{item.name}</td>
                      <td className="text-right px-4 py-3">${item.price.toFixed(2)}</td>
                      <td className="text-center px-4 py-3">
                        <div className="flex items-center justify-center gap-2">
                          <div className="relative w-8 h-1 bg-blue-900 rounded">
                            <div
                              className="absolute h-1 bg-blue-500 rounded transition-all"
                              style={{ width: `${item.blueLineDot}%` }}
                            />
                          </div>
                          <span className="text-xs">{item.blueLineDot}%</span>
                        </div>
                      </td>
                      <td className="text-center px-4 py-3">
                        {item.watermelon ? (
                          <Badge className="bg-green-600 hover:bg-green-700">활성</Badge>
                        ) : (
                          <span className="text-xs text-slate-500">-</span>
                        )}
                      </td>
                      <td className="text-right px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          <div className="relative w-12 h-2 bg-slate-700 rounded-full">
                            <div
                              className="absolute h-2 bg-indigo-500 rounded-full transition-all"
                              style={{ width: `${item.score}%` }}
                            />
                          </div>
                          <span className="text-sm font-semibold w-8 text-right">
                            {item.score}
                          </span>
                        </div>
                      </td>
                      <td className="text-right px-4 py-3 text-xs text-slate-500">
                        {new Date(item.updatedAt).toLocaleTimeString('ko-KR')}
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
