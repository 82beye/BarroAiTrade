'use client';

import { useEffect, useState, useCallback } from 'react';

const API = '/api';
const POLL_MS = 10_000;
const LOG_POLL_MS = 10_000;

// ── Types ──────────────────────────────────────────────────────────────────
interface RiskStatus {
  current_exposure_pct: number;
  daily_pnl_pct: number;
  position_count: number;
  daily_limit_breached: boolean;
  new_entry_blocked: boolean;
  limits: { daily_loss_limit_pct: number; max_concurrent_positions: number };
  status: string;
}

interface Position {
  symbol: string;
  name?: string;
  quantity: number;
  avg_price?: number;
  cur_price?: number;
  pnl_rate?: number;
  strategy?: string;
  tranche?: string;
}

interface AuditRow {
  ts: string;
  action: string;
  side: string;
  symbol: string;
  qty: string;
  price: string;
  blocked: string;
  reason?: string;
  strategy?: string;
}

interface Signal {
  symbol: string;
  name?: string;
  strategy?: string;
  score?: number;
  direction?: string;
  flu_rate?: number;
  cur_price?: number;
  timestamp?: string;
  ts?: string;
}

interface LogFileStatus {
  key: string;
  label: string;
  file: string;
  exists: boolean;
  healthy: boolean;
  size_bytes: number;
  last_modified: number;
  age_sec: number;
  last_line: string;
}

type ServerState = 'ok' | 'error' | 'loading';

// ── Fetch helpers ──────────────────────────────────────────────────────────
async function fetchJson<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${API}${path}`, { cache: 'no-store' });
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}

// ── Sub-components ─────────────────────────────────────────────────────────
function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-semibold ${
        ok ? 'bg-emerald-900 text-emerald-300' : 'bg-red-900 text-red-300'
      }`}
    >
      {label}
    </span>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-400">
        {title}
      </h2>
      {children}
    </div>
  );
}

function Dot({ state }: { state: ServerState }) {
  const color =
    state === 'ok' ? 'bg-emerald-400' : state === 'error' ? 'bg-red-400' : 'bg-yellow-400';
  return (
    <span className="relative flex h-2.5 w-2.5">
      <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${color}`} />
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${color}`} />
    </span>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────
export default function MonitorPage() {
  const [risk, setRisk] = useState<RiskStatus | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [logs, setLogs] = useState<LogFileStatus[]>([]);
  const [serverState, setServerState] = useState<ServerState>('loading');
  const [lastUpdated, setLastUpdated] = useState<string>('');

  const refresh = useCallback(async () => {
    const [riskData, posData, auditData, sigData] = await Promise.all([
      fetchJson<RiskStatus>('/risk/status'),
      fetchJson<{ positions: Position[] }>('/positions'),
      fetchJson<{ log: AuditRow[] }>('/risk/audit'),
      fetchJson<{ signals: Signal[] }>('/signals/recent'),
    ]);

    if (!riskData) {
      setServerState('error');
    } else {
      setServerState('ok');
      setRisk(riskData);
    }

    setPositions(posData?.positions ?? []);
    setAudit((auditData?.log ?? []).slice(-10).reverse());
    setSignals(sigData?.signals ?? []);
    setLastUpdated(new Date().toLocaleTimeString('ko-KR'));
  }, []);

  const refreshLogs = useCallback(async () => {
    const logsData = await fetchJson<{ logs: LogFileStatus[] }>('/logs/status');
    setLogs(logsData?.logs ?? []);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    refreshLogs();
    const id = setInterval(refreshLogs, LOG_POLL_MS);
    return () => clearInterval(id);
  }, [refreshLogs]);

  const pnlColor =
    (risk?.daily_pnl_pct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400';

  return (
    <div className="min-h-screen bg-slate-900 p-6 text-slate-100">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Dot state={serverState} />
          <h1 className="text-2xl font-bold">BarroAiTrade 모니터</h1>
          <Badge ok={serverState === 'ok'} label={serverState === 'ok' ? '서버 정상' : '서버 오류'} />
          {risk?.new_entry_blocked && (
            <Badge ok={false} label="매수 차단" />
          )}
          {risk?.daily_limit_breached && (
            <Badge ok={false} label="일일 손실한도 초과" />
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500">
            {lastUpdated ? `${lastUpdated} 갱신 (${POLL_MS / 1000}s / 로그 ${LOG_POLL_MS / 1000}s)` : '로딩 중...'}
          </span>
          <button
            onClick={refresh}
            className="rounded-lg bg-slate-700 px-3 py-1.5 text-xs font-medium hover:bg-slate-600"
          >
            새로고침
          </button>
        </div>
      </div>

      {/* Risk Stats */}
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          {
            label: '일일 손익',
            value: risk ? `${risk.daily_pnl_pct >= 0 ? '+' : ''}${risk.daily_pnl_pct.toFixed(2)}%` : '—',
            cls: pnlColor,
          },
          {
            label: '노출도',
            value: risk ? `${(risk.current_exposure_pct * 100).toFixed(1)}%` : '—',
            cls: 'text-slate-100',
          },
          {
            label: '보유 종목',
            value: risk ? `${risk.position_count}/${risk.limits.max_concurrent_positions}` : '—',
            cls: 'text-slate-100',
          },
          {
            label: '손실 한도',
            value: risk ? `${(risk.limits.daily_loss_limit_pct * 100).toFixed(1)}%` : '—',
            cls: 'text-slate-400',
          },
        ].map(({ label, value, cls }) => (
          <div key={label} className="rounded-xl border border-slate-700 bg-slate-800 p-4">
            <p className="text-xs text-slate-400">{label}</p>
            <p className={`mt-1 text-2xl font-bold tabular-nums ${cls}`}>{value}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Positions */}
        <Card title={`보유 포지션 (${positions.length})`}>
          {positions.length === 0 ? (
            <p className="text-sm text-slate-500">포지션 없음</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th className="pb-2">종목</th>
                  <th className="pb-2">전략</th>
                  <th className="pb-2 text-right">수량</th>
                  <th className="pb-2 text-right">손익</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const pnl = p.pnl_rate ?? 0;
                  return (
                    <tr key={p.symbol} className="border-t border-slate-700">
                      <td className="py-1.5 font-mono text-xs">
                        {p.symbol}
                        {p.name && <span className="ml-1 text-slate-400">{p.name}</span>}
                      </td>
                      <td className="py-1.5 text-xs">
                        {p.strategy && (
                          <span className="rounded bg-slate-700 px-1.5 py-0.5 text-sky-400">{p.strategy}</span>
                        )}
                        {p.tranche && (
                          <span className="ml-1 text-slate-500">{p.tranche}</span>
                        )}
                      </td>
                      <td className="py-1.5 text-right tabular-nums">{p.quantity}</td>
                      <td
                        className={`py-1.5 text-right tabular-nums font-semibold ${
                          pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                        }`}
                      >
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>

        {/* Audit Log */}
        <Card title="최근 주문 (audit)">
          {audit.length === 0 ? (
            <p className="text-sm text-slate-500">주문 내역 없음</p>
          ) : (
            <div className="space-y-1.5">
              {audit.map((row, i) => {
                const isBlocked = row.blocked === '1';
                const isDryRun = row.action === 'DRY_RUN';
                const actionColor = isBlocked
                  ? 'text-red-400'
                  : isDryRun
                  ? 'text-yellow-400'
                  : 'text-emerald-400';
                return (
                  <div key={i} className="rounded border border-slate-700 bg-slate-900 px-2 py-1">
                    <div className="flex items-center gap-2 text-xs">
                      <span className={`font-semibold ${actionColor}`}>{row.action}</span>
                      <span className="text-slate-300">
                        {row.side} {row.symbol} {row.qty}주
                      </span>
                      {row.strategy && (
                        <span className="rounded bg-slate-700 px-1 py-0.5 text-sky-400">{row.strategy}</span>
                      )}
                      <span className="ml-auto text-slate-500">
                        {row.ts?.slice(11, 16)}
                      </span>
                    </div>
                    {isBlocked && row.reason && (
                      <p className="mt-0.5 truncate text-xs text-red-400">{row.reason}</p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        {/* Signals */}
        <Card title="최근 시그널">
          {signals.length === 0 ? (
            <p className="text-sm text-slate-500">시그널 없음</p>
          ) : (
            <div className="space-y-1.5">
              {signals.map((s, i) => {
                const ts = s.timestamp || s.ts;
                const timeFmt = ts
                  ? new Date(ts).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                  : '';
                const fluColor = (s.flu_rate ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400';
                return (
                  <div
                    key={i}
                    className="rounded border border-slate-700 bg-slate-900 px-2 py-2 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-slate-300">{s.symbol}</span>
                        {s.name && (
                          <span className="font-semibold text-slate-100">{s.name}</span>
                        )}
                        {s.direction && (
                          <span className={`font-bold ${s.direction === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                            {s.direction}
                          </span>
                        )}
                      </div>
                      {timeFmt && (
                        <span className="text-slate-500">{timeFmt}</span>
                      )}
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-slate-400">
                      {s.strategy && (
                        <span className="rounded bg-slate-800 px-1 py-0.5 text-slate-400">
                          {s.strategy}
                        </span>
                      )}
                      {s.score != null && (
                        <span className="font-semibold text-sky-400">
                          score {s.score.toFixed(3)}
                        </span>
                      )}
                      {s.flu_rate != null && (
                        <span className={`font-medium ${fluColor}`}>
                          {s.flu_rate >= 0 ? '+' : ''}{s.flu_rate.toFixed(2)}%
                        </span>
                      )}
                      {s.cur_price != null && (
                        <span className="ml-auto tabular-nums text-slate-300">
                          {s.cur_price.toLocaleString()}원
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>

      {/* Log status + 긴급 대응 */}
      <div className="mt-6 rounded-xl border border-slate-700 bg-slate-800 p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-slate-400">
          긴급 대응
        </h2>
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
          {logs.length === 0
            ? ['매수 로그', '평가 로그', '청산 로그', '서버 로그', '리포트 로그'].map((l) => (
                <div key={l} className="animate-pulse rounded border border-slate-700 bg-slate-900 px-3 py-3">
                  <div className="h-2 w-16 rounded bg-slate-700" />
                </div>
              ))
            : logs.map((log) => {
                const ageFmt =
                  log.age_sec < 60
                    ? `${log.age_sec}초 전`
                    : log.age_sec < 3600
                    ? `${Math.floor(log.age_sec / 60)}분 전`
                    : log.age_sec < 86400
                    ? `${Math.floor(log.age_sec / 3600)}시간 전`
                    : `${Math.floor(log.age_sec / 86400)}일 전`;

                return (
                  <div
                    key={log.key}
                    className={`rounded border px-3 py-2.5 ${
                      !log.exists
                        ? 'border-slate-700 bg-slate-900'
                        : log.healthy
                        ? 'border-emerald-800 bg-emerald-950'
                        : 'border-red-800 bg-red-950'
                    }`}
                  >
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`inline-block h-2 w-2 rounded-full ${
                          !log.exists
                            ? 'bg-slate-600'
                            : log.healthy
                            ? 'bg-emerald-400'
                            : 'bg-red-400'
                        }`}
                      />
                      <p
                        className={`text-xs font-semibold ${
                          !log.exists
                            ? 'text-slate-500'
                            : log.healthy
                            ? 'text-emerald-300'
                            : 'text-red-300'
                        }`}
                      >
                        {log.label}
                      </p>
                    </div>
                    <p className="mt-1 font-mono text-xs text-slate-500">{log.file}</p>
                    {log.exists ? (
                      <>
                        <p
                          className={`mt-1 text-xs font-medium ${
                            log.healthy ? 'text-emerald-400' : 'text-red-400'
                          }`}
                        >
                          {ageFmt}
                        </p>
                        <p className="mt-1 truncate text-xs text-slate-500" title={log.last_line}>
                          {log.last_line || '(내용 없음)'}
                        </p>
                      </>
                    ) : (
                      <p className="mt-1 text-xs text-slate-600">파일 없음</p>
                    )}
                  </div>
                );
              })}
        </div>
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2">
          <p className="text-xs font-medium text-red-300">긴급 중지</p>
          <p className="mt-0.5 font-mono text-xs text-red-500">
            launchctl bootout gui/501/com.barroaitrade.backend
          </p>
        </div>
      </div>
    </div>
  );
}
