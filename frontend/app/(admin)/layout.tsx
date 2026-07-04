import { ReactNode } from 'react';
import { AppSidebar } from '@/components/layout/app-sidebar';

/**
 * 관리자 대시보드 셸 (다크 slate + 좌측 사이드바) — 기존 구조 유지.
 * 라우트 그룹 (admin) 하위 페이지 전부에 적용. URL 은 그룹명으로 바뀌지 않음.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen bg-slate-950 text-slate-50">
      <AppSidebar />
      <main className="flex-1 overflow-auto bg-slate-900">{children}</main>
    </div>
  );
}
