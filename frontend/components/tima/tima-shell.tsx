'use client';

import { ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { TimaTicker } from '@/components/tima/tima-ticker';

// ── 하단 탭 ──
interface Tab {
  href: string;
  label: string;
  icon: ReactNode;
}

function Icon({ d, fill = false }: { d: string; fill?: boolean }) {
  return (
    <svg
      className="h-5 w-5"
      fill={fill ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth={1.8}
      viewBox="0 0 24 24"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

// 하단 탭: 마켓중심만 노출(2026-07-29 사용자 지시).
// 계좌·마켓일정·F존·시장종합은 구 관리자 대시보드(/ · 이전 페이지)에서 접근한다.
const TABS: Tab[] = [
  { href: '/themes', label: '마켓중심', icon: <Icon d="M12 3a9 9 0 109 9h-9V3z M12 3v9h9a9 9 0 00-9-9z" /> },
];

/**
 * 티마 모바일 앱 셸 — 라이트 민트그레이.
 * 상단 1단(플랫폼명 BarroTrade — 구 관리자 대시보드 '/' 로 이동) + 콘텐츠 스크롤 + 하단(티커·마켓중심 탭).
 * 데스크톱에서는 중앙 폰 프레임(max-w-430)으로, 양옆은 어두운 여백.
 */
export function TimaShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const showBottomTicker = !pathname.startsWith('/stocks/');

  return (
    <div className="tima-viewport bg-neutral-900">
      <div className="tima-viewport-frame relative mx-auto flex w-full max-w-[430px] flex-col overflow-hidden bg-tima-bg text-tima-text shadow-2xl">
        {/* ── 상단 고정 셸 (1단: 플랫폼명만) ── */}
        <header
          className="shrink-0 border-b border-black/5 bg-tima-bg px-3 pb-2"
          style={{ paddingTop: 'calc(0.5rem + env(safe-area-inset-top, 0px))' }}
        >
          <div className="flex items-center">
            {/* 프로젝트 플랫폼명 — 클릭 시 이전 페이지(구 관리자 대시보드)로 이동 */}
            <Link href="/" className="text-xl font-black tracking-tight text-tima-brand">
              BarroTrade
            </Link>
          </div>
        </header>

        {/* ── 콘텐츠 스크롤 영역 ── */}
        <main className="flex-1 overflow-y-auto">{children}</main>

        {/* ── 하단 고정 (티커·마켓중심 탭) ── */}
        <div className="shrink-0">
          {showBottomTicker && <TimaTicker />}
          <nav
            className="grid grid-cols-1 border-t border-black/10 bg-tima-tabbar"
            style={{ paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}
          >
            {TABS.map((t) => {
              const active = pathname === t.href || pathname.startsWith(t.href + '/');
              return (
                <Link
                  key={t.href}
                  href={t.href}
                  className={`flex flex-col items-center gap-0.5 pb-1 pt-2 text-[11px] font-medium transition-colors ${
                    active ? 'text-tima-teal' : 'text-tima-sub'
                  }`}
                >
                  {t.icon}
                  <span>{t.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
      </div>
    </div>
  );
}
