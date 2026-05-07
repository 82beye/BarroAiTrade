# BAR-74 / 75 / 76 / 77 / 78 / 79 — Phase 6 잔여 통합

## §0 분리 정책 (전체)
- 모두 a 트랙 (worktree mock + 인터페이스) — b 트랙 운영은 deferred

## BAR-74 어드민 백오피스
- backend/api/routes/admin.py + 8 tests (auth/users/audit/strategy toggle)
- BAR-74b: frontend `app/admin/`

## BAR-75 모바일 앱
- worktree skeleton (운영 RN/Expo 분리)
- 명세만

## BAR-76 해외주식
- StubUSStockGateway / StubHKStockGateway (paper)
- BAR-76b: IBKR / 영웅문 통합

## BAR-77 코인
- StubUpbitGateway (paper)
- BAR-77b: 실 API + 24h 운용

## BAR-78 회귀 자동화
- .github/workflows/regression.yml (모든 PR + main push)
- 베이스라인 ±5% 게이트

## BAR-79 SOR v2
- SORv2 split 라우팅 + 슬리피지 추정 (basis points)
- 12 tests
