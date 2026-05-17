'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

interface Signal {
  symbol: string;
  name?: string;
  price: number;
  signal_type: string;
  score: number;
  reason: string;
  timestamp: string;
}

interface ScanResult {
  market_type: string;
  scanned_count: number;
  signal_count: number;
  signals: Signal[];
}

const SIGNAL_LABELS: Record<string, { label: string; color: string }> = {
  blue_line: { label: '블루라인 (돌파)', color: 'bg-blue-500' },
  f_zone: { label: 'F존 (모멘텀)', color: 'bg-purple-500' },
  buy: { label: '매수', color: 'bg-green-600' },
  sell: { label: '매도', color: 'bg-red-600' },
};

function getSignalMeta(type: string) {
  return SIGNAL_LABELS[type] ?? { label: type, color: 'bg-slate-600' };
}

export default function SignalsPage() {
  const [symbolInput, setSymbolInput] = useState('005930,035720,000660');
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleScan() {
    setScanning(true);
    setError(null);
    setResult(null);

    try {
      const symbols = symbolInput.trim();
      const res = await fetch(`/api/signals/scan?symbols=${encodeURIComponent(symbols)}&market_type=stock`);
      if (!res.ok) throw new Error(`서버 오류: ${res.status}`);
      const data: ScanResult = await res.json();
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : '스캔 실패');
    } finally {
      setScanning(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-50">신호 스캐너</h1>
        <p className="mt-2 text-slate-400">관심 종목의 매매 신호를 실시간 분석합니다</p>
      </div>

      {/* 입력 영역 */}
      <Card className="mb-6 border-slate-700 bg-slate-800">
        <CardHeader>
          <CardTitle className="text-slate-200">종목 스캔</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-3">
            <input
              type="text"
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value)}
              placeholder="종목코드 (쉼표 구분, 예: 005930,035720)"
              className="flex-1 rounded-lg border border-slate-600 bg-slate-700 px-4 py-2 text-slate-200 placeholder-slate-500 focus:border-blue-500 focus:outline-none"
              onKeyDown={(e) => e.key === 'Enter' && handleScan()}
            />
            <Button
              onClick={handleScan}
              disabled={scanning || !symbolInput.trim()}
              className="bg-blue-600 px-6 hover:bg-blue-700 disabled:opacity-50"
            >
              {scanning ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                  스캔 중...
                </span>
              ) : (
                '스캔'
              )}
            </Button>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            예시 종목: 005930 (삼성전자), 035720 (카카오), 000660 (SK하이닉스)
          </p>
        </CardContent>
      </Card>

      {/* 오류 */}
      {error && (
        <div className="mb-4 rounded-lg border border-red-700 bg-red-900 bg-opacity-30 px-4 py-3 text-red-300">
          {error}
        </div>
      )}

      {/* 결과 요약 */}
      {result && (
        <>
          <div className="mb-4 flex flex-wrap gap-4">
            <div className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2">
              <span className="text-sm text-slate-400">스캔 종목</span>
              <span className="ml-2 font-bold text-slate-200">{result.scanned_count}개</span>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2">
              <span className="text-sm text-slate-400">신호 발생</span>
              <span className={`ml-2 font-bold ${result.signal_count > 0 ? 'text-green-400' : 'text-slate-400'}`}>
                {result.signal_count}개
              </span>
            </div>
          </div>

          {/* 신호 테이블 */}
          {result.signals.length === 0 ? (
            <Card className="border-slate-700 bg-slate-800">
              <CardContent className="py-12 text-center text-slate-400">
                신호가 없습니다. 다른 종목을 시도해 보세요.
              </CardContent>
            </Card>
          ) : (
            <Card className="border-slate-700 bg-slate-800">
              <CardHeader>
                <CardTitle className="text-slate-200">신호 목록</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-700 text-slate-400">
                        <th className="pb-3 text-left font-medium">종목</th>
                        <th className="pb-3 text-left font-medium">신호</th>
                        <th className="pb-3 text-right font-medium">현재가</th>
                        <th className="pb-3 text-right font-medium">점수</th>
                        <th className="pb-3 text-left font-medium">사유</th>
                        <th className="pb-3 text-right font-medium">시각</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.signals.map((sig, i) => {
                        const meta = getSignalMeta(sig.signal_type);
                        return (
                          <tr
                            key={i}
                            className="border-b border-slate-700 last:border-0 hover:bg-slate-700 hover:bg-opacity-30"
                          >
                            <td className="py-3">
                              <div className="font-medium text-slate-200">{sig.name ?? sig.symbol}</div>
                              <div className="text-xs text-slate-500">{sig.symbol}</div>
                            </td>
                            <td className="py-3">
                              <span className={`inline-flex rounded px-2 py-0.5 text-xs font-semibold text-white ${meta.color}`}>
                                {meta.label}
                              </span>
                            </td>
                            <td className="py-3 text-right font-mono text-slate-200">
                              {sig.price.toLocaleString()}원
                            </td>
                            <td className="py-3 text-right">
                              <span className={`font-semibold ${sig.score >= 7 ? 'text-green-400' : 'text-yellow-400'}`}>
                                {sig.score.toFixed(1)}
                              </span>
                            </td>
                            <td className="max-w-xs py-3 text-slate-400">{sig.reason}</td>
                            <td className="py-3 text-right font-mono text-xs text-slate-500">
                              {new Date(sig.timestamp).toLocaleTimeString('ko-KR')}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
