'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ReactNode } from 'react';

const adminNav = [
  { href: '/admin', label: '대시보드', icon: '🏛️' },
  { href: '/admin/users', label: '사용자 관리', icon: '👥' },
  { href: '/admin/audit', label: 'Audit 로그', icon: '📋' },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex h-full flex-col">
      {/* 어드민 헤더 */}
      <div className="flex items-center gap-6 border-b border-slate-700 bg-slate-900 px-6 py-3">
        <span className="text-sm font-semibold text-amber-400">🔐 어드민 백오피스</span>
        <nav className="flex gap-1">
          {adminNav.map(({ href, label, icon }) => {
            const isActive = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  isActive
                    ? 'bg-amber-500 bg-opacity-20 text-amber-400'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                }`}
              >
                <span className="mr-1">{icon}</span>
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
      <div className="flex-1 overflow-auto p-6">{children}</div>
    </div>
  );
}
