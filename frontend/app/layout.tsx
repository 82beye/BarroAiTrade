'use client';

import type { Metadata } from 'next';
import { ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import './globals.css';

// export const metadata: Metadata = {
//   title: 'BarroAiTrade',
//   description: 'AI 기반 멀티마켓 자동매매 플랫폼',
// };

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
];

function AppSidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 border-r border-slate-800 bg-slate-950">
      <div className="p-6">
        <h1 className="text-2xl font-bold text-blue-500">BarroAiTrade</h1>
        <p className="mt-1 text-sm text-slate-400">v0.1.0</p>
      </div>

      <nav className="mt-8 space-y-2 px-4">
        {navLinks.map(({ href, label, icon }) => {
          const isActive = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`block rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-blue-500 bg-opacity-20 text-blue-500'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              {icon} {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}

export default function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="bg-slate-950 text-slate-50">
        <div className="flex h-screen">
          <AppSidebar />
          <main className="flex-1 overflow-auto bg-slate-900">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
