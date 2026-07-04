import { ReactNode } from 'react';
import { RealtimeProvider } from '@/components/layout/realtime-provider';
import './globals.css';

export const metadata = {
  title: 'BARRO',
  description: 'AI 기반 멀티마켓 자동매매 플랫폼',
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
      <body>
        <RealtimeProvider>{children}</RealtimeProvider>
      </body>
    </html>
  );
}
