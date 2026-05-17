'use client';

import { useRealtimeConnection } from '@/hooks/useRealtimeConnection';

export function RealtimeProvider({ children }: { children: React.ReactNode }) {
  useRealtimeConnection();
  return <>{children}</>;
}
