import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-900 p-8 text-center">
      <p className="text-6xl font-bold text-slate-700">404</p>
      <h1 className="mt-4 text-2xl font-bold text-slate-200">페이지를 찾을 수 없습니다</h1>
      <p className="mt-2 text-slate-400">요청하신 페이지가 존재하지 않거나 이동되었습니다.</p>
      <Link
        href="/"
        className="mt-8 rounded-lg bg-blue-600 px-6 py-3 text-sm font-medium text-white hover:bg-blue-700"
      >
        대시보드로 돌아가기
      </Link>
    </div>
  );
}
