'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Disclaimer } from '@/components/layout/disclaimer';
import { PriceChart } from '@/components/dashboard/price-chart';
import { WatchlistStar } from '@/components/watchlist/watchlist-star';
import { api, type ScreenerResponse, type ScreenerItem } from '@/lib/api';

// ── F존 5 서브탭 (PRD §4.1) ──
// F존포착 = f_zone 전체, F존+ = f_zone 중 score≥8 클라 필터(원앱 기준 미공개 → 시스템 기준 표기)
interface TimaStrategyTab {
  key: string;
  label: string;
  strategy: string; // 백엔드 스크리너 키
  minScore?: number; // 클라 필터
  subtitle?: string;
}

const TIMA_TABS: TimaStrategyTab[] = [
  { key: 'f_capture', label: 'F존포착', strategy: 'f_zone' },
  { key: 'f_plus', label: 'F존+', strategy: 'f_zone', minScore: 8, subtitle: '점수 8+ (시스템 기준)' },
  { key: 'sf', label: 'SF존', strategy: 'sf_zone' },
  { key: 'gold', label: '골드존', strategy: 'gold_zone' },
  { key: 'swing', label: '38스윙', strategy: 'swing_38' },
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

// 도달 시각 HH:MM:SS (SF존 등)
function fmtReached(s?: string | null): string | null {
  if (!s) return null;
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

const POLL_MS = 10_000;

export default function SignalsPage() {
  const [activeKey, setActiveKey] = useState<string>(TIMA_TABS[0].key);
  const [data, setData] = useState<ScreenerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [selected, setSelected] = useState<ScreenerItem | null>(null);

  // 개발용 수동 심볼 스캔
  const [showManual, setShowManual] = useState(false);
  const [symbolInput, setSymbolInput] = useState('005930,035720,000660');
  const [manualSymbols, setManualSymbols] = useState<string>('');

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const activeTab = useMemo(
    () => TIMA_TABS.find((t) => t.key === activeKey) ?? TIMA_TABS[0],
    [activeKey],
  );
  const isSF = activeTab.strategy === 'sf_zone';

  const fetchScreener = useCallback(async (strategy: string, symbols: string) => {
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
  }, []);

  // 탭/수동심볼 변경 시 즉시 조회 + 10초 폴링
  useEffect(() => {
    setSelected(null);
    fetchScreener(activeTab.strategy, manualSymbols);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => fetchScreener(activeTab.strategy, manualSymbols), POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [activeTab.strategy, manualSymbols, fetchScreener]);

  // score 필터 (F존+)
  const items = useMemo(() => {
    const raw = data?.items ?? [];
    if (activeTab.minScore === undefined) return raw;
    return raw.filter((it) => (it.score ?? 0) >= activeTab.minScore!);
  }, [data, activeTab.minScore]);

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
    <div className="p-3">
      {/* 전략 서브탭 5종 (활성 황색 배경 + 검정 — PRD §4.1) */}
      <div className="mb-2 flex flex-wrap gap-1.5">
        {TIMA_TABS.map((t) => {
          const on = t.key === activeKey;
          return (
            <button
              key={t.key}
              onClick={() => setActiveKey(t.key)}
              className={`rounded-full px-3.5 py-1.5 text-sm font-semibold transition-colors ${
                on
                  ? 'bg-tima-active text-black'
                  : 'border border-tima-line bg-white text-tima-sub hover:bg-tima-bg'
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] text-tima-sub">
          {activeTab.subtitle ?? ' '}
        </span>
        <span className="text-right text-[11px] text-tima-sub">
          {lastUpdated
            ? `${lastUpdated.toLocaleTimeString('ko-KR')} · ${loading ? '갱신 중…' : '10초 갱신'}`
            : '10초 자동 갱신'}
        </span>
      </div>

      {/* 스크리너 테이블 (2단 셀, 숫자 우측정렬 — PRD §6.3) */}
      {isOk ? (
        <div className="overflow-hidden rounded-lg border border-tima-line bg-white">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-tima-line bg-tima-bg/60 text-tima-sub">
                  <th className="min-w-[110px] whitespace-nowrap p-2.5 text-left text-xs font-semibold">
                    종목명
                  </th>
                  <th className="whitespace-nowrap p-2.5 text-right text-xs font-semibold">현재가·등락</th>
                  <th className="whitespace-nowrap p-2.5 text-right text-xs font-semibold">
                    {isSF ? '대금(억)/시총(천억)' : '대금·시총(억)'}
                  </th>
                  {levelLabels.map((lb) => (
                    <th key={lb} className="whitespace-nowrap p-2.5 text-right text-xs font-semibold">
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
                  const hasCp = it.change_pct !== null && it.change_pct !== undefined;
                  const dirColor = !hasCp ? 'text-tima-sub' : up ? 'text-tima-up' : 'text-tima-down';
                  // SF존: +29% 이상 상한가 직행 → 빨간 배경 배지
                  const surgeBadge = isSF && hasCp && (it.change_pct as number) >= 29;
                  // 시총: SF존은 천억 단위(반올림)
                  const capDisplay = isSF
                    ? it.market_cap === null || it.market_cap === undefined
                      ? '-'
                      : Math.round(it.market_cap / 1000).toLocaleString('ko-KR')
                    : fmtNum(it.market_cap);
                  return (
                    <tr
                      key={`${it.symbol}-${i}`}
                      onClick={() => setSelected(it)}
                      className={`cursor-pointer border-b border-tima-line last:border-0 hover:bg-tima-bg/50 ${
                        rowActive ? 'bg-tima-bg/70' : ''
                      }`}
                    >
                      <td className="p-2.5">
                        <div className="flex items-start gap-1.5">
                          <WatchlistStar symbol={it.symbol} size="sm" className="mt-0.5" />
                          <div>
                            <Link
                              href={`/stocks/${it.symbol}`}
                              onClick={(e) => e.stopPropagation()}
                              className="whitespace-nowrap font-bold text-tima-text hover:text-tima-teal hover:underline"
                            >
                              {it.name ?? it.symbol}
                            </Link>
                            <div className="whitespace-nowrap text-[10px] text-tima-sub">
                              {detected ?? it.symbol}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="p-2.5 text-right">
                        <div className={`font-mono font-semibold ${dirColor}`}>{fmtNum(it.price)}</div>
                        {surgeBadge ? (
                          <span className="mt-0.5 inline-block rounded bg-tima-up px-1.5 py-0.5 font-mono text-[11px] font-bold text-white">
                            ↑{Math.abs(it.change_pct as number).toFixed(2)}%
                          </span>
                        ) : (
                          <div className={`font-mono text-[11px] ${dirColor}`}>
                            {!hasCp ? '-' : `${up ? '↑' : '↓'} ${Math.abs(it.change_pct as number).toFixed(2)}%`}
                          </div>
                        )}
                      </td>
                      <td className="p-2.5 text-right">
                        <div className="font-mono text-tima-text">{fmtNum(it.value_traded)}</div>
                        <div className="font-mono text-[11px] text-tima-sub">{capDisplay}</div>
                      </td>
                      {levelLabels.map((lb) => {
                        const lv = (it.levels ?? []).find((x) => x.label === lb);
                        if (!lv) {
                          return (
                            <td key={lb} className="p-2.5 text-right text-tima-line">
                              -
                            </td>
                          );
                        }
                        const dOffset =
                          lv.d_offset === null || lv.d_offset === undefined
                            ? null
                            : `(D+${lv.d_offset})`;
                        const reached = fmtReached(lv.reached_at);
                        return (
                          <td key={lb} className="p-2.5 text-right">
                            <div
                              className={`inline-flex flex-col items-end rounded px-2 py-1 ${
                                lv.active ? 'border border-tima-emph bg-tima-emph/5' : ''
                              }`}
                            >
                              <span className="font-mono font-semibold text-tima-text">
                                {fmtNum(lv.price)}
                              </span>
                              {dOffset && <span className="text-[10px] text-tima-sub">{dOffset}</span>}
                              {reached && (
                                <span className="font-mono text-[10px] text-tima-sub">{reached}</span>
                              )}
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
        </div>
      ) : (
        <div className="rounded-lg border border-tima-line bg-white py-12 text-center text-tima-sub">
          {loading ? '불러오는 중…' : '포착 종목이 없습니다. 데몬 시그널 대기 중.'}
        </div>
      )}

      {/* 인라인 차트 (행 클릭 시 — 알림→차트 최단 경로) */}
      {selected && (
        <div className="mt-4">
          <div className="mb-2 text-sm text-tima-text">
            <span className="font-bold">{selected.name ?? selected.symbol}</span>{' '}
            <span className="text-tima-sub">({selected.symbol})</span> 기준선 차트
          </div>
          <PriceChart
            key={selected.symbol}
            defaultSymbol={selected.symbol}
            defaultTimeframe="15m"
            levels={selected.levels ?? []}
            theme="light"
          />
        </div>
      )}

      {/* 개발용 수동 심볼 스캔 (접이식) */}
      <div className="mt-4">
        <button
          onClick={() => setShowManual((v) => !v)}
          className="text-xs text-tima-sub hover:text-tima-text"
        >
          {showManual ? '▾' : '▸'} 수동 심볼 스캔 (개발용)
        </button>
        {showManual && (
          <div className="mt-2 rounded-lg border border-tima-line bg-white p-3">
            <p className="mb-2 text-sm font-semibold text-tima-text">
              심볼 지정 스캔 — 현재 전략({activeTab.strategy})으로 조회
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                value={symbolInput}
                onChange={(e) => setSymbolInput(e.target.value)}
                placeholder="종목코드 (쉼표 구분, 예: 005930,035720)"
                className="flex-1 rounded-lg border border-tima-line bg-white px-3 py-2 text-sm text-tima-text placeholder-tima-sub focus:border-tima-active focus:outline-none"
                onKeyDown={(e) => e.key === 'Enter' && handleManualScan()}
              />
              <Button onClick={handleManualScan} className="bg-tima-teal px-5 text-white hover:bg-tima-teal/90">
                조회
              </Button>
              {manualSymbols && (
                <Button
                  onClick={() => {
                    setManualSymbols('');
                    setSymbolInput('005930,035720,000660');
                  }}
                  className="bg-tima-bg px-4 text-tima-text hover:bg-tima-line"
                >
                  해제
                </Button>
              )}
            </div>
            <p className="mt-2 text-xs text-tima-sub">
              symbols 파라미터로 동일 스크리너 API를 호출합니다. 해제 시 데몬 포착 종목으로 복귀.
            </p>
          </div>
        )}
      </div>

      <Disclaimer />
    </div>
  );
}
