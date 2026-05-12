'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

interface NavLink {
  href: string;
  label: string;
  icon: string;
}

const navLinks: NavLink[] = [
  { href: '/', label: '대시보드', icon: '🏠' },
  { href: '/trading', label: '트레이딩', icon: '📊' },
  { href: '/positions', label: '포지션', icon: '📈' },
  { href: '/markets', label: '마켓', icon: '💹' },
  { href: '/watchlist', label: '감시 종목', icon: '👁️' },
  { href: '/reports', label: '리포트', icon: '📋' },
  { href: '/settings', label: '설정', icon: '⚙️' },
  { href: '/monitor', label: '모니터', icon: '🖥️' },
];

export function AppSidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className={`flex flex-col border-r border-slate-800 bg-slate-950 transition-all duration-200 ${
        collapsed ? 'w-14' : 'w-64'
      }`}
    >
      {/* Header + toggle */}
      <div className="flex items-center justify-between p-4">
        {!collapsed && (
          <div>
            <h1 className="text-2xl font-bold text-blue-500">BarroAiTrade</h1>
            <p className="mt-1 text-sm text-slate-400">v0.1.0</p>
          </div>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          title={collapsed ? '메뉴 펼치기' : '메뉴 접기'}
        >
          <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {collapsed ? (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            ) : (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7M18 19l-7-7 7-7" />
            )}
          </svg>
        </button>
      </div>

      {/* Nav */}
      <nav className={`mt-4 flex-1 space-y-1 ${collapsed ? 'px-1.5' : 'px-4'}`}>
        {navLinks.map(({ href, label, icon }) => {
          const isActive = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={`flex items-center rounded-lg text-sm font-medium transition-colors ${
                collapsed ? 'justify-center px-0 py-2.5' : 'px-4 py-2'
              } ${
                isActive
                  ? 'bg-blue-500 bg-opacity-20 text-blue-500'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              <span className={collapsed ? 'text-lg' : ''}>{icon}</span>
              {!collapsed && <span className="ml-2">{label}</span>}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
