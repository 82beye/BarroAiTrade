'use client';

import { ReactNode, useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { TimaSearch } from '@/components/tima/tima-search';
import { TimaTicker } from '@/components/tima/tima-ticker';

// ── 하단 5탭 (PRD §2.1) ──
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

// 하단 5탭: 마켓중심 · 계좌 · 마켓일정 · F존 · 시장종합 (알림은 헤더 🔔 로 이동)
// NXT 탭은 애프터마켓 게이트웨이 미연동으로 임시 비활성 — 계좌 탭으로 대체.
const TABS: Tab[] = [
  { href: '/themes', label: '마켓중심', icon: <Icon d="M12 3a9 9 0 109 9h-9V3z M12 3v9h9a9 9 0 00-9-9z" /> },
  // { href: '/nxt', label: 'NXT', icon: <Icon d="M6 20V4l12 16V4" /> },  // TODO: NXT 게이트웨이 연동 후 복원
  { href: '/account', label: '계좌', icon: <Icon d="M3 6h18v12H3z M3 10h18 M7 15h4" /> },
  { href: '/calendar', label: '마켓일정', icon: <Icon d="M8 2v3M16 2v3M3.5 9h17M4 5h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1z" /> },
  { href: '/signals', label: 'F존', icon: <Icon d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" /> },
  { href: '/market-overview', label: '시장종합', icon: <Icon d="M4 19V5m0 14h16M8 15l3-4 3 2 4-6" /> },
];

// 햄버거 드로어 항목 (PRD §2.1)
const DRAWER_LINKS = [
  { href: '/watchlist', label: '나의관심', icon: '★' },
  { href: '/alerts', label: '알림내역', icon: '🔔' },
  { href: '/settings', label: '환경설정', icon: '⚙️' },
  { href: '/', label: '관리자 대시보드', icon: '🖥️' },
];

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

function nowLabel(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}-${dd}(${WEEKDAYS[d.getDay()]}) ${hh}:${mi}`;
}

/**
 * 티마 모바일 앱 셸 (PRD §2.1) — 라이트 민트그레이.
 * 상단 고정(BARRO 로고·시각·검색·햄버거) + 콘텐츠 스크롤 + 하단 고정(티커·지수·5탭바).
 * 데스크톱에서는 중앙 폰 프레임(max-w-430)으로, 양옆은 어두운 여백.
 */
export function TimaShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [drawer, setDrawer] = useState(false);
  const [clock, setClock] = useState('');

  // 시각 1분 갱신 (SSR 불일치 회피 위해 마운트 후 세팅)
  useEffect(() => {
    setClock(nowLabel());
    const t = setInterval(() => setClock(nowLabel()), 60_000);
    return () => clearInterval(t);
  }, []);

  // 라우트 이동 시 드로어 닫기
  useEffect(() => {
    setDrawer(false);
  }, [pathname]);

  return (
    <div className="min-h-screen bg-neutral-900">
      <div className="relative mx-auto flex h-screen w-full max-w-[430px] flex-col overflow-hidden bg-tima-bg text-tima-text shadow-2xl">
        {/* ── 상단 고정 셸 ── */}
        <header className="shrink-0 border-b border-black/5 bg-tima-bg px-3 pb-2 pt-2">
          <div className="flex items-center justify-between">
            <div className="flex items-baseline gap-2">
              <Link href="/themes" className="text-xl font-black tracking-tight text-tima-brand">
                BarroTrade
              </Link>
              <span className="font-mono text-xs text-tima-sub">{clock}</span>
            </div>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <TimaSearch />
            {/* 알림내역 (5탭에서 이동 — PRD §2.1) */}
            <Link
              href="/alerts"
              aria-label="알림내역"
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-black/10 bg-white ${
                pathname.startsWith('/alerts') ? 'text-tima-select' : 'text-tima-text'
              }`}
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 00-4-5.66V5a2 2 0 10-4 0v.34A6 6 0 006 11v3.2a2 2 0 01-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
                />
              </svg>
            </Link>
            <button
              onClick={() => setDrawer(true)}
              aria-label="메뉴"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-black/10 bg-white text-tima-text"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
          </div>
        </header>

        {/* ── 콘텐츠 스크롤 영역 ── */}
        <main className="flex-1 overflow-y-auto">{children}</main>

        {/* ── 하단 고정 (티커·지수·5탭바) ── */}
        <div className="shrink-0">
          <TimaTicker />
          <nav className="grid grid-cols-5 border-t border-black/10 bg-tima-tabbar">
            {TABS.map((t) => {
              const active = pathname === t.href || pathname.startsWith(t.href + '/');
              return (
                <Link
                  key={t.href}
                  href={t.href}
                  className={`flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium transition-colors ${
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

        {/* ── 햄버거 드로어 ── */}
        {drawer && (
          <div className="absolute inset-0 z-50" onClick={() => setDrawer(false)}>
            <div className="absolute inset-0 bg-black/40" />
            <div
              className="absolute right-0 top-0 flex h-full w-64 flex-col bg-white shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between border-b border-tima-line px-4 py-3">
                <span className="text-lg font-black text-tima-brand">BarroTrade</span>
                <button
                  onClick={() => setDrawer(false)}
                  aria-label="닫기"
                  className="text-tima-sub hover:text-tima-text"
                >
                  ✕
                </button>
              </div>
              <nav className="flex-1 py-2">
                {DRAWER_LINKS.map((l) => (
                  <Link
                    key={l.href + l.label}
                    href={l.href}
                    className="flex items-center gap-3 px-4 py-3 text-sm text-tima-text hover:bg-tima-bg"
                  >
                    <span className="w-5 text-center">{l.icon}</span>
                    {l.label}
                  </Link>
                ))}
              </nav>
              <div className="border-t border-tima-line px-4 py-3">
                <button
                  disabled
                  className="flex w-full items-center gap-3 text-sm text-tima-sub opacity-50"
                >
                  <span className="w-5 text-center">⎋</span> 로그아웃
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
