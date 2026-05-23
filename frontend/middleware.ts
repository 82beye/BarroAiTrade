import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (!pathname.startsWith('/admin')) {
    return NextResponse.next();
  }

  // 쿠키에서 role 확인 (BAR-74b 이후 JWT 디코딩으로 교체)
  const role = request.cookies.get('user_role')?.value;
  if (!role) {
    // /login 페이지는 BAR-74b 이후 구현 예정 — 그 전까지는 홈으로 이동
    const homeUrl = request.nextUrl.clone();
    homeUrl.pathname = '/';
    homeUrl.searchParams.set('error', 'unauthorized');
    return NextResponse.redirect(homeUrl);
  }

  if (role !== 'admin') {
    const forbiddenUrl = request.nextUrl.clone();
    forbiddenUrl.pathname = '/';
    forbiddenUrl.searchParams.set('error', 'forbidden');
    return NextResponse.redirect(forbiddenUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/admin/:path*'],
};
