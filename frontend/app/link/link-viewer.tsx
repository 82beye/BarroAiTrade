'use client';

import { useMemo } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

function parseHttpUrl(raw: string | null): URL | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    return url;
  } catch {
    return null;
  }
}

export function LinkViewer() {
  const router = useRouter();
  const params = useSearchParams();
  const url = useMemo(() => parseHttpUrl(params.get('url')), [params]);

  return (
    <div className="flex h-screen flex-col bg-white text-tima-text">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-tima-line bg-tima-bg px-3">
        <button
          onClick={() => router.back()}
          aria-label="이전"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-black/10 bg-white text-lg text-tima-text"
        >
          ‹
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold text-tima-text">원문</div>
          <div className="truncate text-[11px] text-tima-sub">{url?.hostname ?? '링크 없음'}</div>
        </div>
      </header>

      {url ? (
        <iframe
          title="원문"
          src={url.toString()}
          className="min-h-0 flex-1 border-0"
          referrerPolicy="no-referrer-when-downgrade"
          sandbox="allow-forms allow-same-origin allow-scripts"
        />
      ) : (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-tima-sub">
          열 수 없는 링크입니다.
        </div>
      )}
    </div>
  );
}

