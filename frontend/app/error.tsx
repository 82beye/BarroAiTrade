'use client';

import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-900 p-8 text-center">
      <p className="text-5xl font-bold text-red-700">오류</p>
      <h1 className="mt-4 text-2xl font-bold text-slate-200">페이지를 불러오지 못했습니다</h1>
      <p className="mt-2 max-w-md text-slate-400">
        {error.message || '예기치 않은 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'}
      </p>
      <button
        onClick={reset}
        className="mt-8 rounded-lg bg-blue-600 px-6 py-3 text-sm font-medium text-white hover:bg-blue-700"
      >
        다시 시도
      </button>
    </div>
  );
}
