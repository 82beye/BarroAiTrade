'use client';

/**
 * ai_swing 전략 활성화 현황 패널 (테마 보드 우측) — 2026-08-12 신규.
 *
 * 표시 전용. 주문·설정 변경 버튼은 두지 않는다 — 플래그 전환은 운영자가 `.env.local`
 * 에서 하고 데몬을 재기동한다(런북 §6·§8). 이 패널은 "지금 무엇이 켜져 있고 오늘
 * 진입이 가능한가"를 읽기만 한다.
 *
 * 값을 만들어 내지 않는다(§8): 백엔드가 강등한 블록은 사유(reason)를 그대로 노출하고,
 * 로더 status(ok/stale/partial/no_data)를 배지로 라벨링한다.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api, type AiSwingStatus } from '@/lib/api';

const POLL_MS = 15_000;

// ── 데이터 훅 (페이지에서 1회 호출 → dock/drawer 가 공유) ──
export function useAiSwingStatus() {
  const [data, setData] = useState<AiSwingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const res = await api.getAiSwingStatus();
        if (cancelled) return;
        setData(res.data ?? null);
        setFetchError(null);
      } catch (e) {
        if (cancelled) return;
        // 라우트가 없는 구(舊) 백엔드에서는 404 다 — 재기동 안내를 위해 구분한다.
        const code = (e as { response?: { status?: number } }).response?.status;
        setFetchError(code === 404 ? 'route_missing' : 'unreachable');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    timerRef.current = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  return { data, loading, fetchError };
}

// ── 표기 헬퍼 ──
function fmtNum(n?: number | null): string {
  return n === null || n === undefined ? '-' : n.toLocaleString('ko-KR');
}

function fmtTime(iso?: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** 로더 status → 사람이 읽는 배지. 원문 status 도 함께 남긴다. */
const UNIVERSE_LABEL: Record<string, string> = {
  ok: '당일 교집합',
  stale: '과거 파일',
  partial: '스캔 단독',
  no_data: '원본 없음',
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-2 py-0.5">
      <span className="shrink-0 text-[11px] text-tima-sub">{label}</span>
      <span className="min-w-0 truncate text-right text-[11px] font-medium text-tima-text">
        {children}
      </span>
    </div>
  );
}

function Section({ title, right, children }: {
  title: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-tima-line px-3 py-2 first:border-t-0">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-bold text-tima-text">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function Notice({ tone, title, body }: {
  tone: 'warn' | 'info' | 'danger';
  title: string;
  body?: React.ReactNode;
}) {
  const cls =
    tone === 'danger'
      ? 'border-tima-up/40 bg-tima-up/10 text-tima-up'
      : tone === 'warn'
        ? 'border-tima-emph/50 bg-tima-emph/10 text-tima-emph'
        : 'border-tima-line bg-tima-bg text-tima-sub';
  return (
    <div className={`rounded border px-2 py-1.5 text-[11px] ${cls}`}>
      <div className="font-bold">{title}</div>
      {body && <div className="mt-0.5 break-words font-mono text-[10px] opacity-90">{body}</div>}
    </div>
  );
}

// ── 본체 (프레젠테이셔널) ──
export function AiSwingPanelBody({
  data,
  loading,
  fetchError,
}: {
  data: AiSwingStatus | null;
  loading: boolean;
  fetchError: string | null;
}) {
  if (loading && !data && !fetchError) {
    return <div className="px-3 py-8 text-center text-xs text-tima-sub">현황 불러오는 중…</div>;
  }

  if (fetchError) {
    return (
      <div className="px-3 py-3">
        <Notice
          tone="warn"
          title={fetchError === 'route_missing' ? 'API 미배포' : '백엔드 응답 없음'}
          body={
            fetchError === 'route_missing'
              ? 'GET /api/ai-swing/status 없음 — 이 브랜치 배포 후 백엔드 재기동 필요'
              : '/api/ai-swing/status 연결 실패'
          }
        />
      </div>
    );
  }

  if (!data) {
    return <div className="px-3 py-8 text-center text-xs text-tima-sub">데이터 없음</div>;
  }

  if (data.status === 'disabled') {
    return (
      <div className="px-3 py-3">
        <Notice
          tone="info"
          title="대시보드 표시 비활성 (기본 OFF)"
          body={
            <>
              .env.local 에 BARRO_AI_SWING_DASHBOARD_ENABLED=1 추가 후 백엔드 재기동
              {data.reason ? ` · ${data.reason}` : ''}
            </>
          }
        />
      </div>
    );
  }

  const gates = data.gates ?? [];
  const config = data.config;
  const universe = data.universe;
  const positions = data.positions;
  const shadow = data.shadow;
  const entryReady = data.entry_ready;
  const mismatch = data.config_mismatch ?? [];
  const closedGates = gates.filter((g) => !g.ok);

  return (
    <div className="divide-y-0">
      {/* 설정 강등 (.env.local 을 못 읽음) */}
      {data.status === 'no_data' && (
        <div className="px-3 pt-3">
          <Notice
            tone="warn"
            title="설정 파일을 읽지 못했습니다"
            body={data.config_source?.reason || data.reason || 'unknown'}
          />
        </div>
      )}

      {/* 오늘 진입 가능 여부 — 가장 먼저 본다 */}
      <Section
        title="오늘 실진입"
        right={
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
              entryReady?.ok && data.entry_active
                ? 'bg-tima-teal text-black'
                : 'bg-tima-line text-tima-sub'
            }`}
          >
            {!data.entry_active ? '차단' : entryReady?.ok ? '가능' : '대기'}
          </span>
        }
      >
        {!data.entry_active ? (
          <Notice
            tone="info"
            title={closedGates.length > 0 ? `게이트 ${closedGates.length}개 닫힘` : '실주문 마스터 OFF'}
            body={
              closedGates.length > 0
                ? closedGates.map((g) => g.env).join(', ')
                : 'LIVE_TRADING_ENABLED=false'
            }
          />
        ) : entryReady?.ok ? (
          <Notice tone="info" title="원본 신선도 통과 — 신호 발생 시 진입" />
        ) : (
          <Notice tone="warn" title="당일 원본 대기" body={entryReady?.reason || '-'} />
        )}
      </Section>

      {/* 5중 게이트 */}
      <Section
        title="진입 게이트"
        right={
          <span className="font-mono text-[10px] text-tima-sub">
            {gates.filter((g) => g.ok).length}/{gates.length}
          </span>
        }
      >
        <ul className="space-y-0.5">
          {gates.map((g) => (
            <li key={g.id} className="flex items-baseline justify-between gap-2">
              <span className="flex min-w-0 items-baseline gap-1">
                <span className={`shrink-0 text-[11px] ${g.ok ? 'text-tima-teal' : 'text-tima-up'}`}>
                  {g.ok ? '✓' : '✗'}
                </span>
                <span className="truncate text-[11px] text-tima-text">{g.label}</span>
              </span>
              <span
                className="shrink-0 font-mono text-[10px] text-tima-sub"
                title={g.env}
              >
                {g.value}
              </span>
            </li>
          ))}
          {gates.length === 0 && <li className="text-[11px] text-tima-sub">게이트 정보 없음</li>}
        </ul>
      </Section>

      {/* 캡·모드 */}
      {config && (
        <Section title="운영 캡">
          <Row label="예산 비중">{(config.budget_ratio * 100).toFixed(0)}%</Row>
          <Row label="동시 보유">{config.max_positions}종목</Row>
          <Row label="원본 신선도">{config.max_age_h}시간 이내</Row>
          <Row label="과거 파일 진입">{config.allow_stale ? '허용' : '금지'}</Row>
          <Row label="실주문">
            <span className={config.live_trading ? 'text-tima-up' : 'text-tima-sub'}>
              {config.live_trading ? 'ON' : 'OFF'}
            </span>
            {' · '}
            <span className={config.broker_mode === 'real' ? 'text-tima-up' : 'text-tima-teal'}>
              {config.broker_mode === 'real'
                ? '실계좌'
                : config.broker_mode === 'mock'
                  ? '모의'
                  : '미확인'}
            </span>
          </Row>
        </Section>
      )}

      {/* 오늘 유니버스 (단테 스캔 ∩ 예측) */}
      <Section
        title="오늘 유니버스"
        right={
          universe && (
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                universe.status === 'ok' ? 'bg-tima-teal text-black' : 'bg-tima-line text-tima-sub'
              }`}
            >
              {UNIVERSE_LABEL[universe.status] ?? universe.status}
            </span>
          )
        }
      >
        {!universe || universe.status === 'no_data' ? (
          <Notice tone="info" title="유니버스 없음" body={universe?.reason || '-'} />
        ) : (
          <>
            <div className="mb-1 flex items-baseline justify-between font-mono text-[10px] text-tima-sub">
              <span>
                scan {universe.scan_count ?? 0} ∩ pred {universe.pred_count ?? 0} →{' '}
                <span className="font-bold text-tima-text">{universe.intersect_count ?? 0}</span>
              </span>
              <span>
                {universe.scan_date || '-'}
                {universe.pred_date && universe.pred_date !== universe.scan_date
                  ? ` / ${universe.pred_date}`
                  : ''}
              </span>
            </div>
            {universe.reason && (
              <p className="mb-1 break-words font-mono text-[10px] text-tima-sub">
                {universe.reason}
              </p>
            )}
            {universe.items.length === 0 ? (
              <div className="py-3 text-center text-[11px] text-tima-sub">선정 종목 없음</div>
            ) : (
              <ul className="space-y-1">
                {universe.items.map((it) => (
                  <li key={it.symbol} className="rounded bg-tima-bg/70 px-2 py-1.5">
                    <Link href={`/stocks/${it.symbol}`} className="block hover:underline">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-[12px] font-bold text-tima-text">
                          <span className="mr-1 font-mono text-[10px] text-tima-sub">
                            {it.rank_combined}
                          </span>
                          {it.name || it.symbol}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-tima-sub">
                          {it.symbol}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-baseline justify-between gap-2 font-mono text-[10px] text-tima-sub">
                        <span>
                          scan {it.scan_score.toFixed(1)} · pred {it.pred_score.toFixed(1)}
                        </span>
                        <span>거래량 {it.volume_ratio.toFixed(1)}x</span>
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1">
                        {it.consensus_level && (
                          <span className="rounded bg-tima-teal/20 px-1 py-px text-[10px] font-medium text-tima-text">
                            {it.consensus_level}
                          </span>
                        )}
                        <span className="rounded bg-black/5 px-1 py-px font-mono text-[10px] text-tima-sub">
                          conf {it.confidence.toFixed(2)}
                        </span>
                        {it.watermelon_signal && (
                          <span className="rounded bg-tima-surge px-1 py-px text-[10px] font-medium text-tima-text">
                            수박
                          </span>
                        )}
                        {it.blue_line_status && (
                          <span className="rounded bg-black/5 px-1 py-px text-[10px] text-tima-sub">
                            {it.blue_line_status}
                          </span>
                        )}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {universe.truncated && (
              <p className="mt-1 text-[10px] text-tima-sub">상위 일부만 표시</p>
            )}
          </>
        )}
      </Section>

      {/* 보유 */}
      <Section
        title="ai_swing 보유"
        right={
          <span className="font-mono text-[10px] text-tima-sub">
            {positions?.status === 'ok' ? `${positions.items.length}종목` : '-'}
          </span>
        }
      >
        {positions?.status !== 'ok' ? (
          <Notice tone="info" title="장부 없음" body={positions?.reason || '-'} />
        ) : positions.items.length === 0 ? (
          <div className="py-2 text-center text-[11px] text-tima-sub">
            보유 없음
            {typeof positions.total_positions === 'number' && positions.total_positions > 0 && (
              <span className="ml-1 text-tima-sub">
                (전체 {positions.total_positions}종목 중 ai_swing 0)
              </span>
            )}
          </div>
        ) : (
          <ul className="space-y-1">
            {positions.items.map((p) => (
              <li key={p.symbol} className="rounded bg-tima-bg/70 px-2 py-1.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[12px] font-bold text-tima-text">{p.name}</span>
                  <span className="shrink-0 font-mono text-[10px] text-tima-sub">{p.symbol}</span>
                </div>
                <div className="mt-0.5 flex items-baseline justify-between font-mono text-[10px] text-tima-sub">
                  <span>진입 {fmtNum(p.entry_price)}</span>
                  <span>
                    {p.filled_qty}/{p.total_recommended_qty}주 · SL {p.sl_pct}%
                  </span>
                </div>
                <div className="font-mono text-[10px] text-tima-sub">{fmtTime(p.entry_time)}</div>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* shadow 관측 (런북 §4·§6 — 실주문 전환 게이트 진척도) */}
      <Section
        title="shadow 관측"
        right={
          <span className="font-mono text-[10px] text-tima-sub">
            {data.shadow_history_days ?? 0}/5일
          </span>
        }
      >
        {shadow?.status !== 'ok' ? (
          <Notice
            tone="info"
            title={shadow?.reason === 'shadow_never_run' ? '관측 데몬 미실행' : '관측 결과 없음'}
            body={
              shadow?.reason === 'shadow_never_run'
                ? 'scripts/ai_swing_daemon.py 실행 시 생성'
                : shadow?.reason || '-'
            }
          />
        ) : (
          <>
            <Row label="판정">{shadow.evaluated ?? 0}종목</Row>
            <Row label="진입 신호">
              <span className={(shadow.signal_count ?? 0) > 0 ? 'text-tima-up' : ''}>
                {shadow.signal_count ?? 0}건
              </span>
            </Row>
            <Row label="제외">{shadow.skipped_count ?? 0}건</Row>
            <Row label="관측 시각">{fmtTime(shadow.as_of)}</Row>
            {shadow.signals.length > 0 && (
              <ul className="mt-1 space-y-1">
                {shadow.signals.map((s) => (
                  <li key={s.symbol} className="rounded bg-tima-surge/40 px-2 py-1.5">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-[12px] font-bold text-tima-text">
                        {s.name || s.symbol}
                      </span>
                      <span className="shrink-0 font-mono text-[10px] text-tima-sub">
                        {s.symbol}
                      </span>
                    </div>
                    <div className="font-mono text-[10px] text-tima-sub">
                      진입 {fmtNum(s.entry_price)} · SL {fmtNum(s.sl_price)} · TP1{' '}
                      {fmtNum(s.tp1_price)}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Section>

      {/* 설정 출처 · 프로세스 env 불일치 */}
      <Section title="설정 출처">
        <Row label="파일">{data.config_source?.path || '-'}</Row>
        <Row label="수정 시각">{fmtTime(data.config_source?.as_of)}</Row>
        {mismatch.length > 0 && (
          <div className="mt-1">
            <Notice
              tone="warn"
              title={`백엔드 프로세스 env 불일치 ${mismatch.length}건`}
              body={
                <>
                  백엔드는 기동 시점 값을 들고 있습니다. 위 표시값은 cron 이 매 실행마다
                  소싱하는 .env.local(다음 데몬 실행에 적용) 기준입니다.
                  {mismatch.map((m) => (
                    <div key={m.env}>
                      {m.env}: {m.env_local} ← 프로세스 {m.process}
                    </div>
                  ))}
                </>
              }
            />
          </div>
        )}
      </Section>

      <p className="px-3 pb-3 pt-1 text-[10px] text-tima-sub">
        표시 전용 · 15초 갱신 · 갱신 {fmtTime(data.as_of)}
      </p>
    </div>
  );
}

// ── 헤더(공용) ──
function PanelHeader({ data, onClose }: { data: AiSwingStatus | null; onClose?: () => void }) {
  const badge =
    data?.status === 'disabled'
      ? { text: '표시 OFF', cls: 'bg-white/70 text-tima-sub' }
      : data?.entry_active
        ? data.entry_ready?.ok
          ? { text: '실진입 ON', cls: 'bg-white text-tima-up' }
          : { text: '대기', cls: 'bg-white text-tima-text' }
        : { text: 'OFF', cls: 'bg-white/70 text-tima-sub' };

  return (
    <div className="flex items-center gap-2 bg-tima-teal px-3 py-2">
      <span className="min-w-0 flex-1 truncate text-sm font-bold leading-none text-black">
        ai_swing 활성화 현황
      </span>
      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold shadow-sm ${badge.cls}`}>
        {badge.text}
      </span>
      {onClose && (
        <button
          onClick={onClose}
          aria-label="닫기"
          className="shrink-0 rounded p-0.5 text-black/70 hover:text-black"
        >
          ✕
        </button>
      )}
    </div>
  );
}

/* ── 도크 배치 상수 (여기가 단일 진실원천) ───────────────────────────────────
 * 폰 프레임을 왼쪽으로 밀고 도크를 그 오른쪽에 나란히 둔다. 프레임을 옮기는 일은
 * `TimaShell` 이 바깥 컨테이너의 padding-right 로 도크 자리를 비워서 한다 —
 * 프레임의 `mx-auto` 가 남은 폭 안에서 가운데 정렬되므로 결과적으로 왼쪽으로 밀린다.
 *
 *   프레임 F=430 · 간격 G=16 · 도크 D=320  →  최소 요구 폭 F+G+D = 766px
 *   TimaShell padding-right = D + G           = 336px   (min-[800px]:pr-[336px])
 *   도크 left               = 50% + (F-D+G)/2 = 50% + 63px
 *   ⇒ 좌우 여백 = V/2 - 383 로 좌우 대칭. 800px 에서 양옆 17px 확보.
 *
 * ★ transform(translateX)으로 프레임을 밀지 않는다 — 조상에 transform 이 생기면
 *   position:fixed 인 이 도크의 기준이 뷰포트가 아니라 그 조상이 되어 배치가 깨진다.
 * ★ 아래 320px/63px 과 TimaShell 의 336px 은 함께 움직인다. 하나만 바꾸지 말 것.
 *   (backend/tests 가 아닌 육안 검증 대상 — 폭 800/1000/1792px 에서 확인한다)
 * ─────────────────────────────────────────────────────────────────────────── */

/**
 * 데스크톱 도크 — 프레임 오른쪽에 나란히 고정. 800px 미만에서는 자리가 물리적으로
 * 안 나오므로(766px 필요) 숨기고 `AiSwingDrawer` 로 연다(모바일 레이아웃 무영향).
 */
export function AiSwingDock(props: {
  data: AiSwingStatus | null;
  loading: boolean;
  fetchError: string | null;
}) {
  return (
    <aside
      className="fixed bottom-4 top-4 z-40 hidden w-[320px] flex-col overflow-hidden rounded-xl border border-tima-line bg-white shadow-2xl min-[800px]:flex"
      style={{ left: 'calc(50% + 63px)' }}
      aria-label="ai_swing 활성화 현황"
    >
      <PanelHeader data={props.data} />
      <div className="flex-1 overflow-y-auto">
        <AiSwingPanelBody {...props} />
      </div>
    </aside>
  );
}

/** 모바일·태블릿 — 우측 슬라이드 드로어. */
export function AiSwingDrawer({
  open,
  onClose,
  ...props
}: {
  open: boolean;
  onClose: () => void;
  data: AiSwingStatus | null;
  loading: boolean;
  fetchError: string | null;
}) {
  const close = useCallback(() => onClose(), [onClose]);
  if (!open) return null;
  return (
    <div className="absolute inset-0 z-50 min-[800px]:hidden" onClick={close}>
      <div className="absolute inset-0 bg-black/40" />
      <div
        className="absolute right-0 top-0 flex h-full w-[85%] max-w-[340px] flex-col bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <PanelHeader data={props.data} onClose={close} />
        <div className="flex-1 overflow-y-auto">
          <AiSwingPanelBody {...props} />
        </div>
      </div>
    </div>
  );
}

/** 드로어 열기 버튼 — 테마 보드 상단바용. 800px 이상에서는 도크가 상시 보이므로 숨긴다. */
export function AiSwingDrawerButton({
  onClick,
  active,
}: {
  onClick: () => void;
  active: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-semibold min-[800px]:hidden ${
        active
          ? 'border-tima-teal bg-tima-teal text-black'
          : 'border-tima-line bg-white text-tima-text'
      }`}
    >
      🤖 ai_swing
    </button>
  );
}
