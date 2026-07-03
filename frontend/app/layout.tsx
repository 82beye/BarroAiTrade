import { ReactNode } from 'react';
import { AppSidebar } from '@/components/layout/app-sidebar';
import { RealtimeProvider } from '@/components/layout/realtime-provider';
import { NewsTicker } from '@/components/layout/news-ticker';
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
        <RealtimeProvider>
          <div className="flex h-screen">
            <AppSidebar />
            {/* 하단 고정 티커(뉴스+지수, 최대 2줄 ≈ 4rem) 가림 방지용 pb */}
            <main className="flex-1 overflow-auto bg-slate-900 pb-16">
              {children}
            </main>
          </div>
          <NewsTicker />
        </RealtimeProvider>
      </body>
    </html>
  );
}
