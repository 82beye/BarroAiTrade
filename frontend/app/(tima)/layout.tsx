import { ReactNode } from 'react';
import { TimaShell } from '@/components/tima/tima-shell';

/**
 * 티마 벤치마크 셸 (라이트 모바일 앱) — PRD §2.1.
 * (tima) 그룹 하위 페이지(themes·signals·alerts·calendar·nxt·stocks) 전부에 적용.
 * URL 은 그룹명으로 바뀌지 않아 기존 경로 유지.
 */
export default function TimaLayout({ children }: { children: ReactNode }) {
  return <TimaShell>{children}</TimaShell>;
}
