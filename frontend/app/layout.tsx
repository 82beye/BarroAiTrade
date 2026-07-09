import { ReactNode } from 'react';
import type { Metadata, Viewport } from 'next';
import { RealtimeProvider } from '@/components/layout/realtime-provider';
import { InAppNavigationGuard } from '@/components/layout/in-app-navigation-guard';
import './globals.css';

export const metadata: Metadata = {
  title: 'BarroTrade',
  description: 'AI 기반 멀티마켓 자동매매 플랫폼',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    title: 'BarroTrade',
    statusBarStyle: 'black-translucent',
  },
  formatDetection: {
    telephone: false,
  },
  icons: {
    icon: '/icon.svg',
  },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#0f172a',
};

/**
 * 루트 레이아웃 — html/body/전역 provider 만 담당.
 * 실제 셸(관리자 사이드바 / 티마 모바일 앱)은 라우트 그룹 레이아웃에서 구성:
 *   app/(admin)/layout.tsx — 다크 사이드바 셸
 *   app/(tima)/layout.tsx  — 라이트 모바일 앱 셸
 * 라우트 그룹은 URL 을 바꾸지 않으므로 기존 경로 전부 유지된다.
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <link rel="manifest" href="/manifest.json" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-title" content="BarroTrade" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="mobile-web-app-capable" content="yes" />
        <link rel="icon" href="/icon.svg" />
      </head>
      <body>
        <InAppNavigationGuard />
        <RealtimeProvider>{children}</RealtimeProvider>
      </body>
    </html>
  );
}
