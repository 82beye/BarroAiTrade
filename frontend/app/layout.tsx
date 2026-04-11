import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'BarroAiTrade',
  description: 'AI 기반 멀티마켓 자동매매 플랫폼',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="bg-dark text-white">
        <div className="flex h-screen">
          {/* Sidebar */}
          <aside className="w-64 bg-gray-900 border-r border-gray-700">
            <div className="p-6">
              <h1 className="text-2xl font-bold text-primary">BarroAiTrade</h1>
              <p className="text-sm text-gray-400 mt-1">v0.1.0</p>
            </div>
            <nav className="mt-8 px-4 space-y-2">
              <a
                href="/"
                className="block px-4 py-2 rounded-lg bg-primary bg-opacity-20 text-primary"
              >
                🏠 대시보드
              </a>
              <a
                href="/trading"
                className="block px-4 py-2 rounded-lg text-gray-400 hover:bg-gray-800"
              >
                📊 트레이딩
              </a>
              <a
                href="/positions"
                className="block px-4 py-2 rounded-lg text-gray-400 hover:bg-gray-800"
              >
                📈 포지션
              </a>
              <a
                href="/markets"
                className="block px-4 py-2 rounded-lg text-gray-400 hover:bg-gray-800"
              >
                💹 마켓
              </a>
            </nav>
          </aside>

          {/* Main Content */}
          <main className="flex-1 overflow-auto">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
