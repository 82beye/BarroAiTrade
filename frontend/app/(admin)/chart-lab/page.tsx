'use client';

/**
 * 차트 랩 — 보유 프로젝트들의 차트 구현을 버전별(v1~v10)로 한 곳에서 비교/열람.
 * 각 버전은 원본의 시각 스타일(색·레이아웃·시리즈 구성)을 보존해 포팅한 자립형 카드.
 * 딥링크: ?v=<id> 로 특정 버전 선택, ?view=all 로 전체 세로 나열.
 */

import { Suspense, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { CHART_VERSIONS, type ChartVersion } from '@/components/chart-lab/registry';

function LibBadge({ lib }: { lib: string }) {
  return (
    <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[10px] font-medium text-slate-300">
      {lib}
    </span>
  );
}

function VersionCard({ v }: { v: ChartVersion }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950 p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-9 items-center justify-center rounded-md bg-blue-500/20 font-mono text-xs font-bold text-blue-400">
            {v.key}
          </span>
          <h3 className="text-base font-semibold text-slate-100">{v.title}</h3>
          {v.live && (
            <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
              실데이터
            </span>
          )}
        </div>
        <LibBadge lib={v.lib} />
      </div>
      <div className="mb-1 text-xs text-slate-400">{v.oneLiner}</div>
      <div className="mb-4 font-mono text-[11px] text-slate-600">
        <span className="text-slate-500">{v.project}</span> · {v.file}
      </div>
      <div className="rounded-lg">
        <v.Component />
      </div>
    </div>
  );
}

function ChartLabInner() {
  const router = useRouter();
  const params = useSearchParams();
  const viewAll = params.get('view') === 'all';
  const vParam = Number(params.get('v'));
  const selected =
    CHART_VERSIONS.find((x) => x.id === vParam) ?? CHART_VERSIONS[0];

  const setQuery = useCallback(
    (next: { v?: number; view?: 'all' | 'single' }) => {
      const sp = new URLSearchParams(params.toString());
      if (next.view === 'all') {
        sp.set('view', 'all');
        sp.delete('v');
      } else if (next.view === 'single') {
        sp.delete('view');
        if (next.v != null) sp.set('v', String(next.v));
      } else if (next.v != null) {
        sp.set('v', String(next.v));
        sp.delete('view');
      }
      router.replace(`/chart-lab?${sp.toString()}`, { scroll: false });
    },
    [params, router],
  );

  return (
    <div className="min-h-screen bg-slate-900 p-8">
      {/* 헤더 */}
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-bold text-slate-50">📊 차트 랩</h1>
          <p className="mt-2 max-w-2xl text-slate-400">
            보유 프로젝트들의 트레이딩뷰/차트 구현을 버전별로 포팅해 한 페이지에서 비교·열람.
            각 카드는 원본의 색·레이아웃·시리즈 구성을 최대한 보존한 자립형 재현입니다.
          </p>
        </div>
        <div className="flex gap-1 rounded-lg bg-slate-800 p-1">
          <button
            onClick={() => setQuery({ view: 'single', v: selected.id })}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              !viewAll ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            단일 보기
          </button>
          <button
            onClick={() => setQuery({ view: 'all' })}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              viewAll ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            전체 보기
          </button>
        </div>
      </div>

      {/* 버전 목록 (탭/카드) */}
      <div className="mb-8 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {CHART_VERSIONS.map((v) => {
          const active = !viewAll && v.id === selected.id;
          return (
            <button
              key={v.id}
              onClick={() => setQuery({ view: 'single', v: v.id })}
              className={`rounded-lg border p-3 text-left transition-colors ${
                active
                  ? 'border-blue-500 bg-blue-500/10'
                  : 'border-slate-800 bg-slate-950 hover:border-slate-700'
              }`}
            >
              <div className="mb-1 flex items-center gap-2">
                <span className="font-mono text-xs font-bold text-blue-400">{v.key}</span>
                <span className="truncate text-sm font-semibold text-slate-100">{v.title}</span>
              </div>
              <div className="truncate text-[11px] text-slate-500">{v.project}</div>
              <div className="mt-1 truncate font-mono text-[10px] text-slate-600">{v.lib}</div>
            </button>
          );
        })}
      </div>

      {/* 본문 */}
      {viewAll ? (
        <div className="space-y-6">
          {CHART_VERSIONS.map((v) => (
            <VersionCard key={v.id} v={v} />
          ))}
        </div>
      ) : (
        <VersionCard v={selected} />
      )}
    </div>
  );
}

export default function ChartLabPage() {
  return (
    <Suspense fallback={<div className="p-8 text-slate-400">차트 랩 로딩 중...</div>}>
      <ChartLabInner />
    </Suspense>
  );
}
