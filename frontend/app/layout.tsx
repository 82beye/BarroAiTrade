import { ReactNode } from 'react';
import { AppSidebar } from '@/components/layout/app-sidebar';
import './globals.css';

export const metadata = {
  title: 'BarroAiTrade',
  description: 'AI 기반 멀티마켓 자동매매 플랫폼',
};

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
