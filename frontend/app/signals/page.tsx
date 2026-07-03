'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Disclaimer } from '@/components/layout/disclaimer';
import { PriceChart } from '@/components/dashboard/price-chart';
import {
  api,
  type ScreenerResponse,
  type ScreenerItem,
  type StrategyMeta,
} from '@/lib/api';

const DEFAULT_STRATEGIES: StrategyMeta[] = [
  { key: 'f_zone', label: 'F존' },
  { key: 'sf_zone', label: 'SF존' },
  { key: 'gold_zone', label: '골드존' },
  { key: 'swing_38', label: '38스윙' },
];

const LEVEL_ORDER = ['SF', 'B1', 'B2', 'B3', 'G1', 'G2', 'G3', 'J1', 'J2', 'J3'];

function levelRank(label: string): number {
  const i = LEVEL_ORDER.indexOf((label || '').toUpperCase());
  return i < 0 ? 99 : i;
}

function fmtDetected(s?: string | null): string | null {
  if (!s) return null;
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return `${String(d.getMonth() + 1).padStart(2, '0')}월 ${String(d.getDate()).padStart(2, '0')}일`;
}

function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

const POLL_MS = 30_000;

export default function SignalsPage() {
  const [strategies, setStrategies] = useState<StrategyMeta[]>(DEFAULT_STRATEGIES);
  const [active, setActive] = useState<string>(DEFAULT_STRATEGIES[0].key);
  const [data, setData] = useState<ScreenerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [selected, setSelected] = useState<ScreenerItem | null>(null);

  // 개발용 수동 심볼 스캔
  const [showManual, setShowManual] = useState(false);
  const [symbolInput, setSymbolInput] = useState('005930,035720,000660');
  const [manualSymbols, setManualSymbols] = useState<string>('');

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 전략 목록 로드
  useEffect(() => {
    let cancelled = false;
    api
      .getScreenerStrategies()
      .then((res) => {
        const list: StrategyMeta[] = Array.isArray(res.data) ? res.data : [];
        if (!cancelled && list.length > 0) {
          setStrategies(list);
          setActive((prev) => (list.some((s) => s.key === prev) ? prev : list[0].key));
        }
      })
      .catch(() => {
        /* 기본 목록 유지 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const fetchScreener = useCallback(
    async (strategy: string, symbols: string) => {
      setLoading(true);
      try {
        const res = await api.getScreener(strategy, symbols || undefined);
        setData(res.data ?? null);
      } catch {
        setData(null);
      } finally {
        setLoading(false);
        setLastUpdated(new Date());
      }
    },
    [],
  );

  // 전략/수동심볼 변경 시 즉시 조회 + 30초 폴링
  useEffect(() => {
    setSelected(null);
    fetchScreener(active, manualSymbols);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => fetchScreener(active, manualSymbols), POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [active, manualSymbols, fetchScreener]);

  const items = data?.items ?? [];
  const isOk = data?.status === 'ok' && items.length > 0;

  // 기준가 컬럼 라벨 집합 (전략별 동적)
  const levelLabels = useMemo(() => {
    const set = new Set<string>();
    items.forEach((it) => (it.levels ?? []).forEach((lv) => set.add(lv.label)));
    return Array.from(set).sort((a, b) => levelRank(a) - levelRank(b));
  }, [items]);

  function handleManualScan() {
    setManualSymbols(symbolInput.trim());
  }

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-slate-50">전략 스크리너</h1>
          <p className="mt-2 text-slate-400">전략별 포착(시그널) 종목을 실시간 추적합니다</p>
        </div>
        <div className="text-right text-xs text-slate-500">
          {lastUpdated && <div>마지막 갱신: {lastUpdated.toLocaleTimeString('ko-KR')}</div>}
          <div>{loading ? '갱신 중…' : '30초 자동 갱신'}</div>
        </div>
      </div>

      {/* 전략 서브탭 */}
      <div className="mb-6 flex flex-wrap gap-2">
        {strategies.map((s) => {
          const on = s.key === active;
          return (
            <button
              key={s.key}
              onClick={() => setActive(s.key)}
              className={`rounded-full px-5 py-2 text-sm font-semibold transition-colors ${
                on
                  ? 'bg-tima-active text-black'
                  : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
              }`}
            >
              {s.label}
            </button>
          );
        })}
      </div>

      {/* 스크리너 테이블 */}
      {isOk ? (
        <Card className="border-slate-700 bg-slate-800">
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-slate-400">
                    <th className="p-3 text-left font-medium">종목</th>
                    <th className="p-3 text-right font-medium">현재가·등락</th>
                    <th className="p-3 text-right font-medium">대금(억)·시총(억)</th>
                    <th className="p-3 text-right font-medium">점수</th>
                    {levelLabels.map((lb) => (
                      <th key={lb} className="p-3 text-right font-medium">
                        {lb}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => {
                    const up = (it.change_pct ?? 0) >= 0;
                    const detected = fmtDetected(it.detected_at);
                    const rowActive = selected?.symbol === it.symbol;
                    return (
                      <tr
                        key={`${it.symbol}-${i}`}
                        onClick={() => setSelected(it)}
                        className={`cursor-pointer border-b border-slate-700 last:border-0 hover:bg-slate-700/40 ${
                          rowActive ? 'bg-slate-700/50' : ''
                        }`}
                      >
                        <td className="p-3">
                          <div className="font-medium text-slate-100">{it.name ?? it.symbol}</div>
                          <div className="text-xs text-slate-500">
                            {it.symbol}
                            {detected && <span className="ml-1">· {detected}</span>}
                          </div>
                        </td>
                        <td className="p-3 text-right">
                          <div className="font-mono text-slate-100">{fmtNum(it.price)}</div>
                          <div
                            className={`font-mono text-xs ${
                              it.change_pct === null || it.change_pct === undefined
                                ? 'text-slate-500'
                                : up
                                  ? 'text-tima-up'
                                  : 'text-tima-down'
                            }`}
                          >
                            {it.change_pct === null || it.change_pct === undefined
                              ? '-'
                              : `${up ? '↑' : '↓'} ${Math.abs(it.change_pct).toFixed(2)}%`}
                          </div>
                        </td>
                        <td className="p-3 text-right">
                          <div className="font-mono text-slate-200">{fmtNum(it.value_traded)}</div>
                          <div className="font-mono text-xs text-slate-500">
                            {fmtNum(it.market_cap)}
                          </div>
                        </td>
                        <td className="p-3 text-right">
                          <span
                            className={`font-semibold ${
                              it.score >= 7 ? 'text-emerald-400' : 'text-amber-400'
                            }`}
                          >
                            {it.score?.toFixed?.(1) ?? it.score}
                          </span>
                        </td>
                        {levelLabels.map((lb) => {
                          const lv = (it.levels ?? []).find((x) => x.label === lb);
                          if (!lv) {
                            return (
                              <td key={lb} className="p-3 text-right text-slate-600">
                                -
                              </td>
                            );
                          }
                          return (
                            <td key={lb} className="p-3 text-right">
                              <div
                                className={`inline-flex flex-col rounded px-2 py-1 ${
                                  lv.active ? 'border border-tima-emph' : ''
                                }`}
                              >
                                <span className="font-mono text-slate-100">{fmtNum(lv.price)}</span>
                                <span className="text-[10px] text-slate-500">{lv.kind}</span>
                              </div>
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="border-slate-700 bg-slate-800">
          <CardContent className="py-12 text-center text-slate-400">
            {loading ? '불러오는 중…' : '포착 종목이 없습니다. 데몬 시그널 대기 중.'}
          </CardContent>
        </Card>
      )}

      {/* 인라인 차트 (행 클릭 시 — 알림→차트 최단 경로) */}
      {selected && (
        <div className="mt-6">
          <div className="mb-2 text-sm text-slate-300">
            <span className="font-semibold text-slate-100">{selected.name ?? selected.symbol}</span>{' '}
            <span className="text-slate-500">({selected.symbol})</span> 기준선 차트
          </div>
          <PriceChart
            key={selected.symbol}
            defaultSymbol={selected.symbol}
            defaultTimeframe="15m"
            levels={selected.levels ?? []}
          />
        </div>
      )}

      {/* 개발용 수동 심볼 스캔 (접이식) */}
      <div className="mt-6">
        <button
          onClick={() => setShowManual((v) => !v)}
          className="text-sm text-slate-400 hover:text-slate-200"
        >
          {showManual ? '▾' : '▸'} 수동 심볼 스캔 (개발용)
        </button>
        {showManual && (
          <Card className="mt-2 border-slate-700 bg-slate-800">
            <CardHeader>
              <CardTitle className="text-sm text-slate-200">
                심볼 지정 스캔 — 현재 전략({active})으로 조회
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex gap-3">
                <input
                  type="text"
                  value={symbolInput}
                  onChange={(e) => setSymbolInput(e.target.value)}
                  placeholder="종목코드 (쉼표 구분, 예: 005930,035720)"
                  className="flex-1 rounded-lg border border-slate-600 bg-slate-700 px-4 py-2 text-slate-200 placeholder-slate-500 focus:border-tima-active focus:outline-none"
                  onKeyDown={(e) => e.key === 'Enter' && handleManualScan()}
                />
                <Button onClick={handleManualScan} className="bg-slate-600 px-6 hover:bg-slate-500">
                  조회
                </Button>
                {manualSymbols && (
                  <Button
                    onClick={() => {
                      setManualSymbols('');
                      setSymbolInput('005930,035720,000660');
                    }}
                    className="bg-slate-700 px-4 hover:bg-slate-600"
                  >
                    해제
                  </Button>
                )}
              </div>
              <p className="mt-2 text-xs text-slate-500">
                symbols 파라미터로 동일 스크리너 API를 호출합니다. 해제 시 데몬 포착 종목으로 복귀.
              </p>
            </CardContent>
          </Card>
        )}
      </div>

      <Disclaimer />
    </div>
  );
}
